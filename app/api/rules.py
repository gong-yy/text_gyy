"""记忆库管理接口（仅管理员）：规则列表/新建/启停/删除、命中明细、操作历史。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..history import OP_LABELS, log
from ..models import History, HitLog, MemoryRule, User
from ..schemas import RuleCreateRequest, RuleStatusRequest
from .deps import require_admin

router = APIRouter(prefix="/api/rules", tags=["rules"])


def _rule_payload(r: MemoryRule) -> dict:
    return {
        "id": r.id,
        "customer_name": r.customer_name,
        "field_name": r.field_name,
        "old_value": r.old_value,
        "new_value": r.new_value,
        "rule_type": r.rule_type,
        "status": r.status,
        "effective_count": r.effective_count,
        "hit_count": r.hit_count,
        "last_hit_time": r.last_hit_time.isoformat(sep=" ") if r.last_hit_time else None,
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat(sep=" ") if r.created_at else None,
        "updated_by": r.updated_by,
        "updated_at": r.updated_at.isoformat(sep=" ") if r.updated_at else None,
        "source": r.source,
    }


def _get_rule(db: Session, rule_id: int) -> MemoryRule:
    rule = db.get(MemoryRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"规则不存在：{rule_id}")
    return rule


@router.get("")
def list_rules(
    customer: str | None = Query(None),
    field: str | None = Query(None),
    status: str | None = Query(None),
    rule_type: str | None = Query(None),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(MemoryRule)
    if customer:
        q = q.filter(MemoryRule.customer_name.contains(customer))
    if field:
        q = q.filter(MemoryRule.field_name.contains(field))
    if status:
        q = q.filter(MemoryRule.status == status)
    if rule_type:
        q = q.filter(MemoryRule.rule_type == rule_type)
    rows = q.order_by(MemoryRule.updated_at.desc(), MemoryRule.id.desc()).limit(500).all()
    return [_rule_payload(r) for r in rows]


@router.post("")
def create_rule(body: RuleCreateRequest, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    if body.rule_type not in ("permanent", "once"):
        raise HTTPException(status_code=422, detail="rule_type 须为 permanent / once")
    rule = MemoryRule(
        customer_name=body.customer_name.strip(),
        field_name=body.field_name.strip(),
        old_value=body.old_value,
        new_value=body.new_value,
        rule_type=body.rule_type,
        status="enabled",
        effective_count=body.effective_count if body.rule_type == "once" else 0,
        source="explicit_config",
        created_by=user.username,
        updated_by=user.username,
    )
    db.add(rule)
    db.flush()
    log(db, "rule_create", user, rule=rule, before=None, after=rule.new_value,
        remark=f"管理员预置规则：{rule.customer_name} / {rule.field_name}：{rule.old_value} → {rule.new_value}")
    db.commit()
    return _rule_payload(rule)


@router.patch("/{rule_id}/status")
def change_status(rule_id: int, body: RuleStatusRequest, user: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    if body.status not in ("enabled", "disabled"):
        raise HTTPException(status_code=422, detail="status 须为 enabled / disabled")
    rule = _get_rule(db, rule_id)
    old = rule.status
    rule.status = body.status
    rule.updated_by = user.username
    log(db, "rule_toggle", user, rule=rule, before=old, after=body.status)
    db.commit()
    return _rule_payload(rule)


@router.delete("/{rule_id}")
def delete_rule(rule_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rule = _get_rule(db, rule_id)
    payload = _rule_payload(rule)
    log(db, "rule_delete", user, rule=None, field=rule.field_name,
        before=f"{rule.old_value} → {rule.new_value}", after=None,
        remark=f"删除规则 #{rule.id}（{rule.customer_name} / {rule.field_name}）")
    db.delete(rule)
    db.commit()
    return {"deleted": True, "rule": payload}


@router.get("/{rule_id}/hits")
def rule_hits(rule_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db),
              limit: int = Query(100, le=500)):
    _get_rule(db, rule_id)
    rows = (
        db.query(HitLog)
        .filter(HitLog.rule_id == rule_id)
        .order_by(HitLog.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": h.id, "rule_id": h.rule_id, "order_id": h.order_id, "form_id": h.form_id,
            "hit": h.hit, "field_name": h.field_name,
            "value_before": h.value_before, "value_after": h.value_after,
            "hit_time": h.hit_time.isoformat(sep=" ") if h.hit_time else None,
        }
        for h in rows
    ]


@router.get("/{rule_id}/history")
def rule_history(rule_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db),
                 limit: int = Query(100, le=500)):
    _get_rule(db, rule_id)
    rows = (
        db.query(History)
        .filter(History.rule_id == rule_id)
        .order_by(History.id.desc())
        .limit(limit)
        .all()
    )
    return [_history_payload(h) for h in rows]


def _history_payload(h: History) -> dict:
    return {
        "id": h.id,
        "operator_id": h.operator_id,
        "operator_name": h.operator_name,
        "op_time": h.op_time.isoformat(sep=" ") if h.op_time else None,
        "op_type": h.op_type,
        "op_type_label": OP_LABELS.get(h.op_type, h.op_type),
        "order_id": h.order_id,
        "form_id": h.form_id,
        "field_name": h.field_name,
        "value_before": h.value_before,
        "value_after": h.value_after,
        "rule_id": h.rule_id,
        "remark": h.remark,
    }


# 全量操作历史查询（独立 /api/history 前缀，避免与 /{rule_id} 冲突）
history_router = APIRouter(prefix="/api/history", tags=["history"])


@history_router.get("")
def query_history(
    order_id: int | None = Query(None),
    rule_id: int | None = Query(None),
    op_type: str | None = Query(None),
    field: str | None = Query(None),
    form_id: str | None = Query(None),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    limit: int = Query(200, le=1000),
):
    q = db.query(History)
    if order_id is not None:
        q = q.filter(History.order_id == order_id)
    if rule_id is not None:
        q = q.filter(History.rule_id == rule_id)
    if op_type:
        q = q.filter(History.op_type == op_type)
    if field:
        q = q.filter(History.field_name.contains(field))
    if form_id:
        q = q.filter(History.form_id == form_id)
    rows = q.order_by(History.id.desc()).limit(limit).all()
    return [_history_payload(h) for h in rows]
