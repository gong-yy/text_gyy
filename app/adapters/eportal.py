"""ePortal 适配器：mock（本地演示闭环）/ http（对接真实 ePortal REST）。

真实契约要点（提示词第四节）：
- 建单：T → ePortal，生成预订单草稿，响应返回表单 ID；
- 回写：T2 → ePortal，按表单 ID 同步修改后数据，幂等键 = 表单ID + 版本号；
- 入口：ePortal 订单页【修改】按钮 → T2（携带表单ID/订单ID + 身份令牌），在 mock 演示页实现。
"""
import json
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import EportalOrder, EportalWriteLog
from ..util import utcnow

# 演示故障注入：>0 时接下来 N 次 mock 回写返回失败（验收 9 / 回写失败重试演示）
FAULT = {"update_fail_times": 0}

# 金额类计算列/字段默认只读：由 ePortal 计算与校验，T 不得覆盖
_CALC_FIELDS = {"unit_cost", "total_cost", "total_price", "tax_payable", "gp", "gp_percent",
                "line_total", "amount", "total", "no"}
_LEGACY_CALC_PRODUCT_FIELDS = _CALC_FIELDS | {"tax_pyable"}
_LEGACY_CREATE_FIELDS = (
    "so", "so1", "date", "term", "buyer", "prior", "sf_no", "stage", "location", "original",
    "presales", "ratifier", "salesman", "applicant", "user_name", "buyer_boss", "buyer_mail",
    "customer_id", "applicant_id", "sales_person", "user_contact", "customer_name", "delivery_date",
    "exchange_rate", "quotation_ref", "ratifier_mail", "applicant_mail", "sales_bundling",
    "buyer_boss_mail", "customer_address", "es_salesman_code", "customer_payment_term",
    "total_gp", "product_gp", "service_gp", "total_amount", "total_revenue", "product_amount",
    "product_revenue", "service_amount", "service_revenue",
)
_CANONICAL_TO_LEGACY = {
    "Customer ID": "customer_id",
    "Sales Person": "sales_person",
    "Presales": "presales",
    "Quotation Ref / PO No": "quotation_ref",
    "Date": "date",
    "Exchange Rate (for foreign currency)": "exchange_rate",
    "Customer Delivery Address": "customer_address",
    "End User Name": "user_name",
    "End User Contact": "user_contact",
    "Estimated Delivery Date To Customer": "delivery_date",
    "Tax Structure": "tax_structure",
    "Customer Payment Term": "customer_payment_term",
    "产品含税总金额": "product_amount",
    "服务含税总金额": "service_amount",
    "合同含税总金额": "total_amount",
    "产品不含税总金额": "product_revenue",
    "服务不含税总金额": "service_revenue",
    "合同不含税总金额": "total_revenue",
    "产品GP%": "product_gp",
    "服务GP%": "service_gp",
    "合同总GP%": "total_gp",
    "Sales Bundling": "sales_bundling",
    "SF No.": "sf_no",
}
_CANONICAL_PRODUCT_TO_LEGACY = {
    "product_part_no": "product_id",
    "vendor_part_no": "PN",
    "cost_currency": "currency",
    "price_currency": "price",
}


class EPortalError(Exception):
    pass


class EPortalConflictError(EPortalError):
    """ePortal rejected a versioned update because the order has changed."""


def _legacy_products(items: list | None) -> list:
    """Build ePortal's legacy products array without changing source values."""
    rows = []
    for item in items or []:
        row = {key: value for key, value in dict(item or {}).items() if key not in {"line_id"}}
        for source, target in _CANONICAL_PRODUCT_TO_LEGACY.items():
            if source in row:
                row[target] = row.pop(source)
        rows.append(row)
    return rows


def legacy_create_payload(customer_name: str, fields: dict, items: list | None) -> dict:
    """Create the historical ePortal form payload carried by multipart field `data`."""
    values = dict(fields or {})
    payload = {key: "" for key in _LEGACY_CREATE_FIELDS}
    for source, target in _CANONICAL_TO_LEGACY.items():
        if source in values:
            payload[target] = values[source]
    for key in _LEGACY_CREATE_FIELDS:
        if key in values:
            payload[key] = values[key]
    payload["customer_name"] = customer_name or payload["customer_name"]
    payload["products"] = _legacy_products(items)
    return payload


def _schema_entry(value, editable: bool = True, required: bool = False, type_: str = "text",
                  options: list | None = None, group: str = "基本信息") -> dict:
    """schema 化字段条目：{value, type, editable, required, options, group}。"""
    return {
        "value": value,
        "type": type_,
        "editable": editable,
        "required": required,
        "options": list(options or []),
        "group": group,
    }


def _default_item_schema(items: list) -> dict:
    """从产品行列推导编辑 schema：金额类计算列只读，其余可编辑。"""
    columns: dict = {}
    for item in items or []:
        for key in item:
            if key not in columns:
                columns[key] = {"editable": key not in _CALC_FIELDS, "label": key}
    return columns


def _ensure_ids(rows: list, id_key: str, prefix: str) -> list:
    """为产品行/附件补稳定 ID。"""
    out = []
    for row in rows or []:
        row = dict(row or {})
        row.setdefault(id_key, f"{prefix}-{secrets.token_hex(4)}")
        out.append(row)
    return out


@dataclass
class CreateResult:
    form_id: str
    version: int


@dataclass(frozen=True)
class EditContext:
    order_id: str
    version: int
    user_id: str
    user_name: str


@dataclass
class _MockTicket:
    context: EditContext
    expires_at: datetime
    consumed: bool = False


_MOCK_TICKETS: dict[str, _MockTicket] = {}
_MOCK_TICKET_SEQUENCE = 0
_MOCK_TICKETS_LOCK = Lock()


def _cleanup_expired_mock_tickets(now: datetime) -> None:
    for ticket, entry in list(_MOCK_TICKETS.items()):
        if entry.expires_at <= now:
            del _MOCK_TICKETS[ticket]


def issue_mock_ticket(
    order_id: str,
    user_id: str,
    user_name: str,
    *,
    version: int,
    expires_in_seconds: int = 300,
) -> str:
    """Create a deterministic, one-time mock ePortal ticket for tests and demos."""
    global _MOCK_TICKET_SEQUENCE
    with _MOCK_TICKETS_LOCK:
        now = utcnow()
        _cleanup_expired_mock_tickets(now)
        _MOCK_TICKET_SEQUENCE += 1
        ticket = f"mock-ticket-{_MOCK_TICKET_SEQUENCE:06d}"
        _MOCK_TICKETS[ticket] = _MockTicket(
            context=EditContext(order_id, version, user_id, user_name),
            expires_at=now + timedelta(seconds=expires_in_seconds),
        )
    return ticket


class EPortalAdapter(ABC):
    @abstractmethod
    def exchange_ticket(self, ticket: str) -> EditContext:
        """Exchange an opaque, short-lived ePortal ticket for edit context."""

    @abstractmethod
    def get_order_for_edit(self, order_id: str, operator_id: str) -> dict:
        """Fetch the complete editable ePortal order for its authorized operator."""

    @abstractmethod
    def update_order_for_edit(
        self, order_id: str, operator: dict, expected_version: int, changes: dict,
        items: list | None = None, attachments: list | None = None,
    ) -> dict:
        """Apply versioned edits to ePortal and return its complete updated order."""

    @abstractmethod
    def create_order(self, db: Session, customer_name: str, fields: dict, auto_modified: dict,
                     items: list | None = None) -> CreateResult:
        """建单：生成预订单草稿，返回表单 ID。"""

    @abstractmethod
    def update_form(self, db: Session, form_id: str, changed_fields: dict, version: int) -> dict:
        """回写：按表单 ID 同步修改后字段（幂等键 表单ID+版本号）。失败抛 EPortalError。"""

    @abstractmethod
    def get_form(self, db: Session, form_id: str) -> dict:
        """读取预订单当前数据。"""


class MockEPortalAdapter(EPortalAdapter):
    """本地模拟 ePortal：数据存 t_system.db 的 eportal_* 表，供 /eportal 演示页展示。"""

    def exchange_ticket(self, ticket: str) -> EditContext:
        with _MOCK_TICKETS_LOCK:
            now = utcnow()
            _cleanup_expired_mock_tickets(now)
            entry = _MOCK_TICKETS.get(ticket)
            if entry is None or entry.consumed:
                raise EPortalError("ePortal ticket 无效、已使用或已过期")
            entry.consumed = True
            return entry.context

    # ---- schema 化订单拉取 / 版本化回写（T2 编辑会话专用） ----

    @staticmethod
    def _order_dict(row: EportalOrder) -> dict:
        item_schema = row.item_schema or {}
        items = row.items or []
        if not item_schema and items:  # 兼容旧数据：从产品行列推导 schema
            item_schema = _default_item_schema(items)
        return {
            "order_id": row.form_id,
            "form_id": row.form_id,
            "version": row.version,
            "customer_name": row.customer_name,
            "status": row.status,
            "fields": row.fields or {},
            "items": items,
            "item_schema": item_schema,
            "items_editable": any(col.get("editable") for col in item_schema.values()),
            "attachments": row.attachments or [],
            "auto_modified": row.auto_modified or {},
        }

    def get_order_for_edit(self, order_id: str, operator_id: str | None = None) -> dict:
        from ..db import SessionLocal

        db = SessionLocal()
        try:
            row = db.query(EportalOrder).filter(EportalOrder.form_id == order_id).one_or_none()
            if row is None:
                raise EPortalError(f"订单不存在：{order_id}")
            db.refresh(row)
            return self._order_dict(row)
        finally:
            db.close()

    def update_order_for_edit(
        self, order_id: str, operator: dict, expected_version: int, changes: dict,
        items: list | None = None, attachments: list | None = None,
    ) -> dict:
        from ..db import SessionLocal

        db = SessionLocal()
        try:
            row = db.query(EportalOrder).filter(EportalOrder.form_id == order_id).one_or_none()
            if row is None:
                raise EPortalError(f"订单不存在：{order_id}")
            if (row.version or 1) != expected_version:
                raise EPortalConflictError(
                    f"订单已被他人更新（ePortal 当前版本 {row.version}），请刷新后重新进入修改页")
            fields = dict(row.fields or {})
            for name, value in (changes or {}).items():
                entry = fields.get(name)
                if not isinstance(entry, dict):
                    raise EPortalError(f"字段不存在：{name}")
                if not entry.get("editable", False):
                    raise EPortalError(f"字段 {name} 为计算字段/只读字段，ePortal 拒绝覆盖")
                entry = dict(entry)
                entry["value"] = value
                fields[name] = entry
            row.fields = fields
            if items is not None:
                items = _ensure_ids(items, "line_id", "L")
                old_rows = {r.get("line_id"): r for r in (row.items or [])}  # 计算列只读保护
                schema_cols = row.item_schema or {}
                for nr in items:
                    lid = nr.get("line_id")
                    if lid in old_rows:
                        for col, meta in schema_cols.items():
                            if meta.get("editable") is False:
                                if col in nr and str(nr[col] or "") != str(old_rows[lid].get(col, "") or ""):
                                    raise EPortalError(f"产品行计算列 {col} 由 ePortal 计算，拒绝覆盖")
                                nr[col] = old_rows[lid].get(col, "")  # 未提交/同值 → ePortal 保留
                    else:
                        for col, meta in schema_cols.items():
                            if meta.get("editable") is False:
                                if nr.get(col):
                                    raise EPortalError(f"新产品行不允许填写计算列 {col}")
                                nr[col] = ""
                row.items = items
            if attachments is not None:
                row.attachments = _ensure_ids(attachments, "id", "A")
            row.version = (row.version or 1) + 1
            db.add(EportalWriteLog(form_id=order_id, version=row.version,
                                   payload={"changes": changes or {}, "operator": operator or {}}))
            db.commit()
        finally:
            db.close()
        return self.get_order_for_edit(order_id, (operator or {}).get("id"))

    # ---- 旧版兼容链路（intake 建单 / 回写重试演示） ----

    def create_order(self, db: Session, customer_name: str, fields: dict, auto_modified: dict,
                     items: list | None = None, attachments: list | None = None) -> CreateResult:
        from ..eportal_schema import default_attachments, default_item_schema, merge_order_fields

        n = (db.query(EportalOrder.id).count() or 0) + 1
        form_id = f"EP-{utcnow():%Y%m%d}-{n:04d}"
        normalized_items = _ensure_ids(items or [], "line_id", "L")
        row = EportalOrder(
            form_id=form_id,
            customer_name=customer_name,
            fields=merge_order_fields(fields),  # canonical COSTING SHEET 全量模板 + 传入值
            items=normalized_items,
            item_schema=default_item_schema(),
            attachments=_ensure_ids(attachments if attachments is not None else default_attachments(), "id", "A"),
            auto_modified=dict(auto_modified or {}),
            version=1,
            status="draft",
        )
        db.add(row)
        db.flush()
        return CreateResult(form_id=form_id, version=1)

    def update_form(self, db: Session, form_id: str, changed_fields: dict, version: int) -> dict:
        replay = (
            db.query(EportalWriteLog)
            .filter(EportalWriteLog.form_id == form_id, EportalWriteLog.version == version)
            .one_or_none()
        )
        if replay:  # 幂等重放：同键已成功写过，直接返回成功
            return {"ok": True, "version": version, "replayed": True}
        if FAULT["update_fail_times"] > 0:
            FAULT["update_fail_times"] -= 1
            raise EPortalError("模拟 ePortal 更新故障（故障注入）")
        row = db.query(EportalOrder).filter(EportalOrder.form_id == form_id).one_or_none()
        if row is None:
            raise EPortalError(f"表单不存在：{form_id}")
        fields = dict(row.fields or {})
        fields.update(changed_fields)
        row.fields = fields
        row.version = max(row.version or 1, version)
        db.add(EportalWriteLog(form_id=form_id, version=version, payload=dict(changed_fields)))
        db.flush()
        return {"ok": True, "version": version}

    def get_form(self, db: Session, form_id: str) -> dict:
        row = db.query(EportalOrder).filter(EportalOrder.form_id == form_id).one_or_none()
        if row is None:
            raise EPortalError(f"表单不存在：{form_id}")
        return {
            "form_id": row.form_id,
            "customer_name": row.customer_name,
            "fields": row.fields,
            "auto_modified": row.auto_modified,
            "version": row.version,
            "status": row.status,
        }


class HttpEPortalAdapter(EPortalAdapter):
    """真实 ePortal REST 对接（契约细节落地后按实际报文调整）。"""

    def _headers(self, *, json_content: bool = True) -> dict:
        h = {"Content-Type": "application/json"} if json_content else {}
        if settings.eportal_service_token:
            h["Authorization"] = f"Bearer {settings.eportal_service_token}"
        if settings.eportal_api_key:
            h["X-Api-Key"] = settings.eportal_api_key
        return h

    def _check(self, resp: httpx.Response) -> dict:
        if resp.status_code == 409:
            raise EPortalConflictError(f"ePortal version conflict: {resp.text[:200]}")
        if resp.status_code >= 500:
            raise EPortalError(f"ePortal 服务错误 HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise EPortalError(f"ePortal 拒绝请求 HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def exchange_ticket(self, ticket: str) -> EditContext:
        resp = httpx.post(
            settings.eportal_base_url + settings.eportal_ticket_exchange_path,
            json={"ticket": ticket}, headers=self._headers(), timeout=15,
        )
        data = self._check(resp)
        user = data["user"]
        return EditContext(
            order_id=str(data["order_id"]), version=int(data["version"]),
            user_id=str(user["id"]), user_name=str(user["name"]),
        )

    def get_order_for_edit(self, order_id: str, operator_id: str) -> dict:
        url = settings.eportal_base_url + settings.eportal_order_for_edit_path.format(order_id=order_id)
        headers = {**self._headers(), "X-EPortal-Operator-Id": operator_id}
        return self._check(httpx.get(url, headers=headers, timeout=15))

    def update_order_for_edit(
        self, order_id: str, operator: dict, expected_version: int, changes: dict
    ) -> dict:
        url = settings.eportal_base_url + settings.eportal_order_update_for_edit_path.format(order_id=order_id)
        return self._check(httpx.patch(
            url,
            json={"operator": operator, "expected_version": expected_version, "changes": changes},
            headers=self._headers(), timeout=15,
        ))

    def create_order(self, db: Session, customer_name: str, fields: dict, auto_modified: dict,
                     items: list | None = None) -> CreateResult:
        payload = legacy_create_payload(customer_name, fields, items)
        resp = httpx.post(
            settings.eportal_base_url + settings.eportal_create_path,
            files={"data": (None, json.dumps(payload, ensure_ascii=False), "application/json")},
            headers=self._headers(json_content=False),
            timeout=15,
        )
        data = self._check(resp)
        return CreateResult(form_id=str(data["form_id"]), version=int(data.get("version", 1)))

    def update_form(self, db: Session, form_id: str, changed_fields: dict, version: int) -> dict:
        url = settings.eportal_base_url + settings.eportal_update_path.format(form_id=form_id)
        resp = httpx.put(
            url,
            json={"fields": changed_fields, "version": version},  # 幂等键：表单ID+版本号
            headers=self._headers(),
            timeout=15,
        )
        return self._check(resp)

    def get_form(self, db: Session, form_id: str) -> dict:
        url = settings.eportal_base_url + settings.eportal_get_path.format(form_id=form_id)
        resp = httpx.get(url, headers=self._headers(), timeout=15)
        return self._check(resp)


_adapter: EPortalAdapter | None = None


def get_adapter() -> EPortalAdapter:
    global _adapter
    if _adapter is None:
        _adapter = HttpEPortalAdapter() if settings.eportal_mode == "http" else MockEPortalAdapter()
    return _adapter
