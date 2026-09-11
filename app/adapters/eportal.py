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
from pathlib import Path
from threading import Lock

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..logging_config import audit
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
    "exchange_rate", "quotation_ref", "ratifier_mail", "applicant_mail", "sales_bundling", "tax_structure",
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
_LEGACY_PRODUCT_FIELDS = (
    "node_id", "biz_category", "product_id", "PN", "description", "qty", "currency",
    "unit_cost", "price", "unit_price", "total_cost", "total_price", "tax_pyable", "GP",
    "GP_percent", "supplier", "inventory_type", "warehouse", "dropship", "remarks", "notes",
)

_TICKET_FIELD_SPECS = {
    "buyer_1": {"label": "Requester", "type": "select", "options": [
        ("", ""), ("anniean.chen", "Anniean Chen"), ("di.wu", "Candice Wu"),
        ("hzeng", "Helena Zeng"), ("jasmine.wu", "Wu Si Min Jasmine"),
        ("jfeng", "Feng Jun Cici"), ("lisa.li", "Lisa Li"), ("suki.li", "Suki Li"),
        ("yuanfeng.li", "Sophia Li"), ("zhang.jing", "Zhang Jing"), ("fern.wang", "Fern Wang"),
    ]},
    "tax_structure": {"label": "Tax Structure", "type": "select", "required": True, "options": [
        ("3.00", "CN_VAT3"), ("6.00", "CN_VAT6"), ("9.00", "CN_VAT9"),
        ("10", "CN_VAT10"), ("13.00", "CN_VAT13"), ("16.00", "CN_VAT16"), ("0", "CN_VAT0"),
    ]},
    "customer_payment_term": {"label": "Customer Payment Term", "type": "select", "required": True, "options": [
        (value, value) for value in (
            "", "0D", "0D_ForCall", "10_DEP_15D", "10_DEP_30D", "10_DEP_40_7D_50_UAT", "10D",
            "14D", "14D_PDC", "15D", "15D_PDC", "1D", "120D", "135D", "20_DEP_30D",
            "20_DEP_70_0D_10_UAT", "20_DEP_80_30D", "20_DEP_80_7D", "20D", "20D_PDC", "21D",
            "21D_PDC", "23D", "25D", "26D", "30_DEP_30D", "30_DEP_50_7D_20_UAT",
            "30_DEP_60_7D_10_UAT", "30_DEP_COD", "30D", "30D_PDC", "3D", "40D", "42D", "45D",
            "45D_PDC", "4D", "50_DEP_0D", "50_DEP_15D", "50_DEP_30D", "50_DEP_7D", "50D", "55D",
            "5D", "5D_10DEP_30DRECEIVED", "5D_30DEP_15D", "60D", "60D_PDC", "65D", "65D_PDC",
            "70_DEP_15D", "70D", "75D", "7D", "7D_35RECEIVED_30DUAT", "7D_PDC", "90D", "90D_PDC",
            "B2B", "CBD", "COD",
        )
    ]},
    "location": {"label": "签约公司", "type": "select", "required": True,
                 "options": [("", ""), ("beijing", "北京"), ("shanghai", "上海"), ("guangzhou", "广州")]},
    "date": {"label": "Date", "type": "date", "required": True},
    "delivery_date": {"label": "Estimated Delivery Date To Customer", "type": "date", "required": True},
}
_TICKET_PRODUCT_SPECS = {
    "node_id": {"label": "Node ID", "type": "select", "required": True, "options_by_biz_category": {
        "BAU_BIZ": ["DPG-JCSH", "DPG-JCBJ", "DPG-JCGZ", "ESG-JCSH", "ESG-JCBJ", "ESG-JCGZ"],
        "NEW_BIZ": ["BDS-JCSH", "BDS-JCBJ", "BDS-JCGZ"],
    }},
    "biz_category": {"label": "BiZ Category", "type": "select", "required": True,
                     "options": [("", ""), ("BAU_BIZ", "BAU_BIZ"), ("NEW_BIZ", "NEW_BIZ"), ("Services", "Services")]},
    "currency": {"label": "Cost Currency", "type": "select", "options": [("", ""), ("CNY", "CNY"), ("USD", "USD"), ("MYR", "MYR"), ("SGD", "SGD")]},
    "price": {"label": "Price Currency", "type": "select", "options": [("", ""), ("CNY", "CNY"), ("USD", "USD"), ("MYR", "MYR"), ("SGD", "SGD")]},
    "tax_pyable": {"label": "Tax Payable", "type": "select", "options": [("13.00", "13.00"), ("9.00", "9.00"), ("6.00", "6.00"), ("3.00", "3.00"), ("0", "0")]},
    "dropship": {"label": "Dropship", "type": "select", "options": [("", ""), ("Y", "Y"), ("N", "N")]},
}
_TICKET_ATTACHMENT_SLOTS = {
    "att1": "合同/报价单/ePO", "att2": "J-FORM", "att4": "J-FORM (Approval)", "att3": "GCF",
}
_TICKET_METADATA_KEYS = {
    "id", "products", "attachments", "files", "att1", "att2", "att3", "att4", "att_1", "att_2", "att_3", "att_4",
    "create_time", "last_mod", "applicant_id", "applicant_mail", "mail_id", "mail_state", "resubmit_time", "rpa_time",
    "rpa_return_time", "rpa_otp_time", "otp_return_time", "ratify_time", "submit_time", "submitter", "submitter_mail",
}


def _option_dicts(values: list[tuple[str, str]]) -> list[dict]:
    return [{"value": value, "label": label} for value, label in values]


def normalize_ticket_order(payload: dict) -> dict:
    """Convert ePortal's flat ticket JSON into the model consumed by T2."""
    fields = []
    for name, value in payload.items():
        if name in _TICKET_METADATA_KEYS or isinstance(value, (dict, list)):
            continue
        spec = _TICKET_FIELD_SPECS.get(name, {})
        fields.append({
            "field_name": name,
            "label": spec.get("label", name),
            "value": "" if value is None else str(value),
            "type": spec.get("type", "text"),
            "editable": not bool(spec.get("readonly", False)),
            "required": bool(spec.get("required", False)),
            "options": _option_dicts(spec.get("options", [])),
        })
    item_schema = {}
    for name, spec in _TICKET_PRODUCT_SPECS.items():
        entry = {"label": spec["label"], "type": spec["type"], "editable": True,
                 "required": bool(spec.get("required", False)), "options": _option_dicts(spec.get("options", []))}
        if "options_by_biz_category" in spec:
            entry["options_by_biz_category"] = spec["options_by_biz_category"]
        item_schema[name] = entry
    for item in payload.get("products") or []:
        for name in item:
            item_schema.setdefault(name, {"label": name, "type": "text", "editable": name not in _CALC_FIELDS,
                                          "required": False, "options": []})
    attachments = []
    for key, slot_name in _TICKET_ATTACHMENT_SLOTS.items():
        attachment = payload.get(key)
        if isinstance(attachment, dict) and attachment:
            name = str(attachment.get("name") or attachment.get("filename") or "")
            attachments.append({"id": attachment.get("id") or key, "slot_name": slot_name,
                                "filename": name, "name": name})
    return {
        "id": int(payload["id"]), "form_id": str(payload["id"]), "version": int(payload.get("last_mod") or 0),
        "fields": fields, "items": [dict(item) for item in (payload.get("products") or [])],
        "attachments": attachments, "item_schema": item_schema,
    }


class EPortalError(Exception):
    pass


class EPortalConflictError(EPortalError):
    """ePortal rejected a versioned update because the order has changed."""


def _legacy_products(items: list | None, *, tax_structure: str = "", location: str = "") -> list:
    """Build ePortal's legacy products array without changing source values."""
    rows = []
    for index, item in enumerate(items or [], start=1):
        values = {key: value for key, value in dict(item or {}).items() if key not in {"line_id"}}
        for source, target in _CANONICAL_PRODUCT_TO_LEGACY.items():
            if source in values:
                values[target] = values.pop(source)
        row = {key: "" for key in _LEGACY_PRODUCT_FIELDS}
        row.update({key: "" if value is None else value for key, value in values.items()})
        row["node_id"] = row["node_id"] or str(index)
        row["biz_category"] = row["biz_category"] or "BAU_BIZ"
        row["currency"] = row["currency"] or "CNY"
        row["price"] = row["price"] or "CNY"
        row["tax_pyable"] = row["tax_pyable"] or tax_structure
        row["warehouse"] = row["warehouse"] or location
        row["dropship"] = row["dropship"] or "N"
        rows.append(row)
    return rows


def legacy_create_payload(
    customer_name: str,
    fields: dict,
    items: list | None,
    *,
    intellisight_id: str | None = None,
) -> dict:
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
    payload["stage"] = payload["stage"] or "0"
    payload["date"] = payload["date"] or datetime.now().date().isoformat()
    payload["exchange_rate"] = payload["exchange_rate"] or "1"
    payload["sales_bundling"] = payload["sales_bundling"] or "Product only"
    payload["service_amount"] = payload["service_amount"] or "0"
    payload["service_revenue"] = payload["service_revenue"] or "0"
    payload["total_gp"] = payload["total_gp"] or payload["product_gp"]
    if intellisight_id:
        payload["intellisight_id"] = str(intellisight_id)
    payload["products"] = _legacy_products(
        items, tax_structure=str(payload["tax_structure"] or ""), location=str(payload["location"] or "")
    )
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
    accepted: bool = True
    response: dict | None = None


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
    def get_ticket_order(self, eportal_id: int) -> dict:
        """Fetch a complete ePortal order by the ePortal page's actual `id`."""

    @abstractmethod
    def update_order_for_edit(
        self, order_id: str, operator: dict, expected_version: int, changes: dict,
        items: list | None = None, attachments: list | None = None,
    ) -> dict:
        """Apply versioned edits to ePortal and return its complete updated order."""

    @abstractmethod
    def create_order(self, db: Session, customer_name: str, fields: dict, auto_modified: dict,
                     items: list | None = None, intellisight_id: str | None = None,
                     attachments: list[dict] | None = None) -> CreateResult:
        """建单：生成预订单草稿，返回表单 ID。"""

    @abstractmethod
    def update_form(self, db: Session, form_id: str, changed_fields: dict, version: int,
                    items: list | None = None, intellisight_id: str | None = None) -> dict:
        """回写：携带智眸订单号同步修改后字段；失败抛 EPortalError。"""

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

    def get_ticket_order(self, eportal_id: int) -> dict:
        raise EPortalError("本地 mock ePortal 不支持按远端 id 读取订单")

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
                     items: list | None = None, attachments: list | None = None,
                     intellisight_id: str | None = None) -> CreateResult:
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

    def update_form(self, db: Session, form_id: str, changed_fields: dict, version: int,
                    items: list | None = None, intellisight_id: str | None = None) -> dict:
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
        if items is not None:
            row.items = items
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

    def get_ticket_order(self, eportal_id: int) -> dict:
        if eportal_id <= 0:
            raise EPortalError("ePortal id 必须为正整数")
        url = settings.eportal_base_url + settings.eportal_ticket_order_path.format(id=eportal_id)
        try:
            payload = self._check(httpx.get(url, headers=self._headers(), timeout=15))
        except httpx.RequestError as exc:
            raise EPortalError("无法连接 ePortal") from exc
        except ValueError as exc:
            raise EPortalError("ePortal 返回内容不是有效 JSON") from exc
        try:
            returned_id = int(payload.get("id"))
        except (TypeError, ValueError) as exc:
            raise EPortalError("ePortal 返回内容缺少有效 ID") from exc
        if returned_id != eportal_id:
            raise EPortalError("ePortal 返回订单 ID 不一致")
        return normalize_ticket_order(payload)

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
                     items: list | None = None, intellisight_id: str | None = None,
                     attachments: list[dict] | None = None) -> CreateResult:
        payload = legacy_create_payload(customer_name, fields, items, intellisight_id=intellisight_id)
        multipart_files = {"data": (None, json.dumps(payload, ensure_ascii=False), "application/json")}
        if attachments:
            # list preserves repeated multipart field names used by some 智眸 uploads.
            multipart_files = list(multipart_files.items()) + [
                (
                    str(attachment["field_name"]),
                    (
                    str(attachment["filename"]),
                    Path(str(attachment["path"])).read_bytes(),
                        str(attachment.get("content_type") or "application/octet-stream"),
                    ),
                )
                for attachment in attachments
            ]
        try:
            audit("eportal_create_requested", intellisight_id=intellisight_id or "",
                  attachment_count=len(attachments or []), item_count=len(items or []))
            resp = httpx.post(
                settings.eportal_base_url + settings.eportal_create_path,
                files=multipart_files,
                headers=self._headers(json_content=False),
                timeout=15,
            )
            data = self._check(resp)
            audit("eportal_create_responded", intellisight_id=intellisight_id or "",
                  http_status=resp.status_code, response_json=json.dumps(data, ensure_ascii=False))
        except httpx.RequestError as exc:
            audit("eportal_create_transport_error", intellisight_id=intellisight_id or "", reason=str(exc))
            raise EPortalError(f"ePortal 连接失败：{exc}") from exc
        except ValueError as exc:
            audit("eportal_create_invalid_response", intellisight_id=intellisight_id or "", reason=str(exc))
            raise EPortalError("ePortal 返回内容不是有效 JSON") from exc
        accepted = str(data.get("code")) == "1"
        return CreateResult(
            form_id=str(data.get("id") or data.get("form_id") or data.get("order_id") or ""),
            version=int(data.get("version", 1)),
            accepted=accepted,
            response=data,
        )

    def update_form(self, db: Session, form_id: str, changed_fields: dict, version: int,
                    items: list | None = None, intellisight_id: str | None = None) -> dict:
        """复用 entry 接口回写完整订单，并携带智眸订单号供 ePortal 定位。"""
        payload = legacy_create_payload("", changed_fields, items, intellisight_id=intellisight_id)
        payload["id"] = form_id
        try:
            audit("eportal_writeback_requested", intellisight_id=intellisight_id or "", form_id=form_id,
                  version=version, item_count=len(items or []))
            resp = httpx.post(
                settings.eportal_base_url + settings.eportal_create_path,
                files={"data": (None, json.dumps(payload, ensure_ascii=False), "application/json")},
                headers=self._headers(json_content=False),
                timeout=15,
            )
            data = self._check(resp)
            audit("eportal_writeback_responded", intellisight_id=intellisight_id or "", form_id=form_id,
                  http_status=resp.status_code, response_json=json.dumps(data, ensure_ascii=False))
        except httpx.RequestError as exc:
            audit("eportal_writeback_transport_error", intellisight_id=intellisight_id or "", form_id=form_id,
                  reason=str(exc))
            raise EPortalError(f"ePortal 连接失败：{exc}") from exc
        except ValueError as exc:
            audit("eportal_writeback_invalid_response", intellisight_id=intellisight_id or "", form_id=form_id,
                  reason=str(exc))
            raise EPortalError("ePortal 返回内容不是有效 JSON") from exc
        if str(data.get("code")) != "1":
            audit("eportal_writeback_rejected", intellisight_id=intellisight_id or "", form_id=form_id,
                  code=data.get("code"), message=data.get("msg") or "")
            raise EPortalError(f"ePortal 回写失败：{data.get('msg') or data}")
        audit("eportal_writeback_accepted", intellisight_id=intellisight_id or "", form_id=form_id,
              code=data.get("code"), eportal_id=data.get("id") or "")
        return data

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
