"""mock ePortal 查询/演示接口（仅供 /eportal 演示页与验收演示使用；真实环境为外部系统）。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..adapters.eportal import (
    FAULT,
    _CALC_FIELDS,
    _default_item_schema,
    _ensure_ids,
    _schema_entry,
    issue_mock_ticket,
)
from ..db import get_db
from ..models import EportalOrder
from ..schemas import FaultRequest, MockOrderCreateRequest
from ..util import utcnow

router = APIRouter(prefix="/api/mock/eportal", tags=["mock-eportal"])


@router.post("/orders")
def create_order(body: MockOrderCreateRequest, db: Session = Depends(get_db)):
    """创建预订单（演示/测试入口）：canonical COSTING SHEET 全量模板 + 传入值覆盖。"""
    from ..eportal_schema import default_attachments, default_item_schema, merge_order_fields

    schema_fields = merge_order_fields({})
    for name, spec in dict(body.fields or {}).items():
        if isinstance(spec, dict) and "value" in spec:  # 完整 schema 条目覆盖
            entry = dict(spec)
            entry.setdefault("editable", True)
            entry.setdefault("required", False)
            entry.setdefault("type", "text")
            entry.setdefault("options", [])
            entry.setdefault("group", "其他")
            entry.setdefault("label", name)
        else:  # 标量：通用文本条目
            entry = _schema_entry(spec)
        if name in _CALC_FIELDS:  # 金额类计算字段强制只读
            entry["editable"] = False
            if entry.get("group", "基本信息") == "基本信息":
                entry["group"] = "计算字段"
        schema_fields[name] = entry
    items = _ensure_ids(body.items or [], "line_id", "L")
    n = (db.query(EportalOrder.id).count() or 0) + 1
    row = EportalOrder(
        form_id=f"SO-{n:04d}",
        customer_name=body.customer_name,
        fields=schema_fields,
        items=items,
        item_schema=body.item_schema if body.item_schema is not None else default_item_schema(),
        attachments=_ensure_ids(
            body.attachments if body.attachments is not None else default_attachments(), "id", "A"),
        auto_modified=dict(body.auto_modified or {}),
        version=max(1, body.version),
        status="draft",
    )
    db.add(row)
    db.commit()
    return {
        "order_id": row.form_id, "form_id": row.form_id, "version": row.version,
        "customer_name": row.customer_name, "status": row.status,
    }


@router.post("/orders/{form_id}/ticket")
def issue_ticket(form_id: str, body: dict, db: Session = Depends(get_db)):
    """演示【修改】按钮：ePortal 签发一次性 ticket（绑定操作人/订单/当前版本）。"""
    row = db.query(EportalOrder).filter(EportalOrder.form_id == form_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"订单不存在：{form_id}")
    ticket = issue_mock_ticket(form_id, str(body.get("user_id", "sales1")),
                               str(body.get("user_name", "张销售")), version=row.version)
    return {"ticket": ticket, "order_id": row.form_id, "version": row.version}


@router.get("/orders")
def list_orders(db: Session = Depends(get_db)):
    rows = db.query(EportalOrder).order_by(EportalOrder.id.desc()).limit(200).all()
    return [
        {
            "form_id": r.form_id,
            "order_id": r.form_id,
            "customer_name": r.customer_name,
            "fields": r.fields,
            "items": r.items,
            "item_schema": r.item_schema,
            "attachments": r.attachments,
            "auto_modified": r.auto_modified,
            "version": r.version,
            "status": r.status,
            "updated_at": r.updated_at.isoformat(sep=" ") if r.updated_at else None,
        }
        for r in rows
    ]


@router.get("/orders/{form_id}")
def get_order(form_id: str, db: Session = Depends(get_db)):
    row = db.query(EportalOrder).filter(EportalOrder.form_id == form_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"表单不存在：{form_id}")
    return {
        "form_id": row.form_id,
        "order_id": row.form_id,
        "customer_name": row.customer_name,
        "fields": row.fields,
        "items": row.items,
        "item_schema": row.item_schema,
        "attachments": row.attachments,
        "auto_modified": row.auto_modified,
        "version": row.version,
        "status": row.status,
    }


@router.post("/orders/{form_id}/submit")
def submit_order(form_id: str, db: Session = Depends(get_db)):
    """业务员在 ePortal 核对后提交（恒定人工终审，闭环完成）。"""
    row = db.query(EportalOrder).filter(EportalOrder.form_id == form_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"表单不存在：{form_id}")
    row.status = "submitted"
    db.commit()
    return {"ok": True, "form_id": row.form_id, "status": row.status}


@router.post("/fault")
def set_fault(body: FaultRequest):
    """注入回写故障：接下来 N 次更新返回失败（演示「回写失败自动重试→手动重发」）。"""
    FAULT["update_fail_times"] = max(0, int(body.update_fail_times))
    return {"update_fail_times": FAULT["update_fail_times"]}
