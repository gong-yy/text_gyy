"""操作日志（history）：所有人工修改与规则操作全量留痕。

operator 可传 T 内部 User，或直接给 operator_id/operator_name（ePortal 操作人）；
order_ref 用于记录 ePortal 订单 ID（form_id 列）。
"""
from sqlalchemy.orm import Session

from .models import Order, User
from .util import utcnow

OP_LABELS = {
    "manual_modify": "人工修改",
    "rule_override": "规则覆盖",
    "rule_toggle": "规则启停",
    "once_modify": "单次修改",
    "rule_delete": "规则删除",
    "memory_record": "记忆记入",
    "rule_create": "规则新建",
    "writeback_conflict": "回写冲突",
    "correction_case_created": "案例创建",
    "agent_rule_created": "Agent规则创建",
    "agent_failed": "Agent失败",
    "agent_conflict": "Agent冲突",
}


def log(
    db: Session,
    op_type: str,
    operator: User | None,
    *,
    order: Order | None = None,
    rule=None,
    field: str | None = None,
    before: str | None = None,
    after: str | None = None,
    remark: str | None = None,
    operator_id: str | None = None,
    operator_name: str | None = None,
    order_ref: str | None = None,
) -> None:
    from .models import History

    if operator is not None:
        operator_id = operator_id or operator.username
        operator_name = operator_name or (operator.display_name or operator.username)
    db.add(
        History(
            operator_id=operator_id or "system",
            operator_name=operator_name or "系统",
            op_time=utcnow(),
            op_type=op_type,
            order_id=order.id if order else None,
            form_id=order_ref or (order.form_id if order else None),
            field_name=field,
            value_before=str(before) if before is not None else None,
            value_after=str(after) if after is not None else None,
            rule_id=rule.id if rule else None,
            remark=remark,
        )
    )
