"""T1 记忆匹配引擎 + 兑换记忆库服务。

匹配键 = 客户名 + 字段 + 原值（匹配前做标准化：去空格/大小写/全半角/百分号小数归一）。
命中 → 按记忆自动替换；未命中 → 保持智眸原值；无论命中与否均继续流转建单。
同一键多条启用规则 → 取最近更新的一条。每次查询写 hit_log（命中与未命中均记录）。
"""
from sqlalchemy.orm import Session

from ..history import log
from ..models import HitLog, MemoryRule, Order
from ..normalize import normalize_value
from ..util import utcnow


def find_rule(db: Session, customer_name: str, field_name: str, value: str) -> MemoryRule | None:
    """查一条启用规则：键匹配（标准化后相等），多规则取最近更新。"""
    candidates = (
        db.query(MemoryRule)
        .filter(MemoryRule.status == "enabled", MemoryRule.field_name == field_name)
        .all()
    )
    matched = [
        r
        for r in candidates
        if normalize_value(r.customer_name) == normalize_value(customer_name)
        and normalize_value(r.old_value) == normalize_value(value)
        and (r.rule_type == "permanent" or (r.effective_count or 0) > 0)
    ]
    if not matched:
        return None
    matched.sort(key=lambda r: (r.updated_at or r.created_at, r.id), reverse=True)
    return matched[0]


def apply_memory(db: Session, order: Order) -> list[dict]:
    """T1：对订单每个字段查记忆并自动替换。返回命中归因列表。"""
    fields = order.payload.setdefault("fields", {})
    original = order.payload.setdefault("original", {})
    applied: list[dict] = order.payload.setdefault("applied_memory", [])

    for field, obj in fields.items():
        current = obj.get("value", "")
        original.setdefault(field, current)  # 智眸提取原值留档（记忆锚定用）
        rule = find_rule(db, order.customer_name, field, current)
        hit = rule is not None
        db.add(
            HitLog(
                rule_id=rule.id if rule else None,
                order_id=order.id,
                form_id=order.form_id,
                hit=hit,
                field_name=field,
                value_before=current,
                value_after=rule.new_value if rule else None,
                hit_time=utcnow(),
            )
        )
        if rule:
            obj["value"] = rule.new_value
            obj["source"] = "memory"
            obj["rule_id"] = rule.id
            applied.append(
                {
                    "field": field,
                    "rule_id": rule.id,
                    "original_value": current,
                    "applied_value": rule.new_value,
                }
            )
            rule.hit_count = (rule.hit_count or 0) + 1
            rule.last_hit_time = utcnow()
            if rule.rule_type == "once":
                rule.effective_count = (rule.effective_count or 0) - 1
                if rule.effective_count <= 0:
                    rule.status = "disabled"  # 单次规则命中消费一次后自动失效
                    log(
                        db,
                        "rule_toggle",
                        None,
                        rule=rule,
                        field=field,
                        before="enabled",
                        after="disabled",
                        remark=f"单次规则命中消费后自动失效（订单 {order.id}）",
                    )
    db.flush()
    return applied
