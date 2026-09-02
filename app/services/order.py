"""订单服务：智眸接入 → T1 匹配 → 建单 → T2 保存（记忆确认/负反馈）→ 回写重试；
以及 ePortal 编辑会话驱动的版本化保存（Task 3+）。
"""
import re
import time

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..adapters.eportal import EPortalConflictError, EPortalError, get_adapter
from ..config import settings
from ..history import log
from ..lock import is_locked_by_other, lock_holder, release
from ..models import CorrectionCase, HitLog, MemoryRule, Order, User
from ..util import utcnow
from .memory import apply_memory


class BizError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(message)


def intake(
    db: Session,
    *,
    customer_name: str,
    fields: dict,
    task_id: str | None = None,
    meta: dict | None = None,
) -> Order:
    """② 智眸流转 → T1 匹配 → ③ 建单送 ePortal。"""
    if not (customer_name or "").strip():
        raise BizError(400, "缺少客户名（记忆锚定键必需）")
    from ..eportal_schema import HEADER_FIELDS, split_zhimou_items

    scalar_fields, items = split_zhimou_items(fields or {})  # 智眸产品行列 → 结构化产品行
    all_fields = {meta[0]: "" for meta in HEADER_FIELDS}  # canonical 全量表单都过 T1（空值填充依赖此语义）
    all_fields.update(scalar_fields)
    order = Order(
        zhimou_task_id=task_id,
        customer_name=customer_name.strip(),
        status="pending_create",
        payload={
            "fields": {f: {"value": "" if v is None else str(v), "source": "zhimou"} for f, v in all_fields.items()},
            "original": {},
            "applied_memory": [],
            "items": items,
            "meta": {**(meta or {}), **{k: v for k, v in (fields or {}).items()
                                        if k in ("product_amount", "product_revenue")}},
        },
    )
    db.add(order)
    db.flush()
    apply_memory(db, order)  # 命中改 / 未命中保持；hit_log 命中与未命中均记录
    flag_modified(order, "payload")  # flush 后原地改 JSON，需显式标记变更才能持久化
    auto_modified = {a["field"]: {"rule_id": a["rule_id"]} for a in order.payload["applied_memory"]}
    try:
        res = get_adapter().create_order(
            db,
            order.customer_name,
            {f: o["value"] for f, o in order.payload["fields"].items()},
            auto_modified,
            items=items or None,
        )
        order.form_id = res.form_id
        order.version = res.version
        order.status = "created"
    except EPortalError as exc:
        order.status = "create_failed"  # 建单失败：留待处理列表，可 T2 修改后重提
        order.last_error = str(exc)
    db.commit()
    return order


def _field_template(db: Session, customer_name: str) -> dict:
    """按客户合并模板：客户专属配置覆盖 '*' 默认。"""
    from ..models import TemplateField

    rows = db.query(TemplateField).filter(TemplateField.customer_name.in_(["*", customer_name])).all()
    merged: dict[str, dict] = {}
    for r in sorted(rows, key=lambda x: (x.customer_name != customer_name, x.display_order)):
        merged[r.field_name] = {
            "field_name": r.field_name,
            "label": r.label or r.field_name,
            "display_order": r.display_order,
            "visible": r.visible,
            "editable": r.editable,
        }
    return merged


def order_detail(db: Session, order: Order) -> dict:
    fields = order.payload.get("fields", {})
    template = _field_template(db, order.customer_name)
    ordered: list[dict] = []
    seen = set()
    for name, t in sorted(template.items(), key=lambda kv: kv[1]["display_order"]):
        if name in fields:
            ordered.append({**t, "value": fields[name]["value"], "source": fields[name].get("source", "zhimou"),
                            "rule_id": fields[name].get("rule_id")})
            seen.add(name)
    for name, obj in fields.items():  # 模板外字段追加在末尾（可见可改）
        if name not in seen:
            ordered.append({"field_name": name, "label": name, "display_order": 9999,
                            "visible": True, "editable": True,
                            "value": obj["value"], "source": obj.get("source", "zhimou"),
                            "rule_id": obj.get("rule_id")})
    return {
        "order_id": order.id,
        "zhimou_task_id": order.zhimou_task_id,
        "customer_name": order.customer_name,
        "form_id": order.form_id,
        "version": order.version,
        "status": order.status,
        "last_error": order.last_error,
        "fields": ordered,
        "original": order.payload.get("original", {}),
        "applied_memory": order.payload.get("applied_memory", []),
        "pending_writeback": order.pending_writeback,
        "lock": {"locked_by": order.locked_by, "holder": lock_holder(order)},
        "created_at": order.created_at.isoformat(sep=" ") if order.created_at else None,
        "updated_at": order.updated_at.isoformat(sep=" ") if order.updated_at else None,
    }


def save_changes(
    db: Session,
    order: Order,
    operator: User,
    changes: dict,
    memory_choices: dict | None = None,
    feedback_choices: dict | None = None,
) -> dict:
    """⑤ T2 保存：逐字段记忆确认 / 负反馈，然后 ⑥ 同步回写 ePortal。"""
    if is_locked_by_other(order, operator.username):
        raise BizError(423, f"当前正被 {lock_holder(order)} 编辑")

    fields = order.payload.get("fields", {})
    original = order.payload.get("original", {})
    applied_map = {a["field"]: a for a in order.payload.get("applied_memory", [])}
    memory_choices = memory_choices or {}
    feedback_choices = feedback_choices or {}

    changed: list[tuple[str, str, str]] = []
    for field, new_value in (changes or {}).items():
        if field not in fields:
            raise BizError(400, f"未知字段：{field}")
        new_value = "" if new_value is None else str(new_value)
        old_value = fields[field]["value"]
        if old_value != new_value:
            changed.append((field, old_value, new_value))
    if not changed:
        return {"status": order.status, "changed": [], "form_id": order.form_id, "message": "无修改内容"}

    results = []
    for field, old_value, new_value in changed:
        fields[field]["value"] = new_value
        fields[field]["source"] = "human"
        fields[field].pop("rule_id", None)
        log(db, "manual_modify", operator, order=order, field=field, before=old_value, after=new_value)

        rule_entry = applied_map.get(field)
        rule = db.get(MemoryRule, rule_entry["rule_id"]) if rule_entry else None
        handled = False
        if rule and rule.status == "enabled" and rule.rule_type == "permanent":
            # 负反馈：已生效的长期记忆再次被人工改写 → 用户选择「覆盖」或「仅本次」
            fb = feedback_choices.get(field)
            if fb not in ("override", "once"):
                raise BizError(
                    422,
                    f"字段「{field}」此前由长期记忆自动修改，本次被人工改写：请选择「覆盖原规则」或「仅本次单次修改」",
                )
            if fb == "override":
                rule.new_value = new_value
                rule.updated_by = operator.username
                rule.updated_at = utcnow()
                log(db, "rule_override", operator, order=order, rule=rule, field=field,
                    before=old_value, after=new_value, remark="覆盖原规则：后续订单按新值匹配")
            else:
                log(db, "once_modify", operator, order=order, rule=rule, field=field,
                    before=old_value, after=new_value, remark="原规则保持不变，本次修改仅对当前订单生效")
            handled = True

        if not handled:
            choice = memory_choices.get(field, "permanent")  # 默认记为长期规则
            if choice not in ("permanent", "once", "none"):
                raise BizError(422, f"字段「{field}」记忆选项无效：{choice}")
            anchor_old = original.get(field, old_value)  # 记忆锚定用智眸原值
            if choice == "permanent":
                r = MemoryRule(
                    customer_name=order.customer_name,
                    field_name=field,
                    old_value=anchor_old,
                    new_value=new_value,
                    rule_type="permanent",
                    status="enabled",
                    effective_count=0,
                    source="human_diff",
                    created_by=operator.username,
                    updated_by=operator.username,
                )
                db.add(r)
                db.flush()
                log(db, "memory_record", operator, order=order, rule=r, field=field,
                    before=old_value, after=new_value, remark="人工修改沉淀为长期记忆，一次生效")
            elif choice == "once":
                r = MemoryRule(
                    customer_name=order.customer_name,
                    field_name=field,
                    old_value=anchor_old,
                    new_value=new_value,
                    rule_type="once",
                    status="enabled",
                    effective_count=1,
                    source="once_modify",
                    created_by=operator.username,
                    updated_by=operator.username,
                )
                db.add(r)
                db.flush()
                # 仅对当前订单生效：命中消费一次后自动失效，不污染长期记忆
                r.hit_count = 1
                r.effective_count = 0
                r.status = "disabled"
                r.last_hit_time = utcnow()
                db.add(HitLog(rule_id=r.id, order_id=order.id, form_id=order.form_id, hit=True,
                              field_name=field, value_before=old_value, value_after=new_value, hit_time=utcnow()))
                log(db, "once_modify", operator, order=order, rule=r, field=field,
                    before=old_value, after=new_value, remark="仅本次单次修改：规则命中消费一次后自动失效")
            # choice == "none"：不产生任何记忆规则

        results.append({"field": field, "before": old_value, "after": new_value})

    order.version += 1
    order.pending_writeback = {"fields": {f: n for f, _, n in changed}, "version": order.version}
    flag_modified(order, "payload")
    db.commit()

    ok = _writeback(db, order)
    if ok:
        release(db, order, operator)  # 保存回写成功即释放锁
    db.commit()
    if ok:
        message = "修改已同步回写 ePortal，可返回 ePortal 核对并提交订单"
    else:
        message = "回写 ePortal 失败（已自动重试），修改已保留在 T 系统，可点击「重新同步」手动重发"
    return {"status": order.status, "changed": results, "form_id": order.form_id, "message": message}


def _writeback(db: Session, order: Order) -> bool:
    """⑥ 同步回写：失败自动重试 N 次；建单失败单则修改后重提（重新建单）。"""
    pw = order.pending_writeback
    if not pw:
        return True
    if not order.form_id:
        try:
            res = get_adapter().create_order(
                db,
                order.customer_name,
                {f: o["value"] for f, o in order.payload["fields"].items()},
                {},
            )
            order.form_id = res.form_id
            order.version = res.version
            order.status = "created"
            order.pending_writeback = None
            order.last_error = None
            return True
        except EPortalError as exc:
            order.status = "create_failed"
            order.last_error = str(exc)
            return False
    last_error: Exception | None = None
    for _ in range(settings.max_retries + 1):
        try:
            get_adapter().update_form(db, order.form_id, pw["fields"], pw["version"])
            order.status = "synced"
            order.pending_writeback = None
            order.last_error = None
            return True
        except EPortalError as exc:
            last_error = exc
            if settings.retry_backoff > 0:
                time.sleep(settings.retry_backoff)
    order.status = "sync_failed"
    order.last_error = str(last_error)
    return False


def resend(db: Session, order: Order) -> dict:
    """回写失败后手动重发；T2 数据本地留存不丢失。"""
    if not order.pending_writeback:
        if order.status == "create_failed":
            order.pending_writeback = {
                "fields": {f: o["value"] for f, o in order.payload["fields"].items()},
                "version": order.version,
            }
        else:
            raise BizError(400, "当前订单状态无需重发")
    ok = _writeback(db, order)
    db.commit()
    if ok:
        message = "同步成功，可返回 ePortal 核对并提交订单"
    else:
        message = f"同步仍失败：{order.last_error}"
    return {"status": order.status, "ok": ok, "form_id": order.form_id, "message": message}


# ---------- ePortal 编辑会话驱动的版本化保存（Task 3+） ----------

_BOOL_TRUE = {"true", "1", "是", "y", "yes"}


def _validate_schema_changes(fields: dict, changes: dict) -> None:
    """按 ePortal 字段 schema 校验标量变更：存在性 / 只读 / 类型 / 选项 / 必填。"""
    for name, value in changes.items():
        meta = fields.get(name)
        if not isinstance(meta, dict):
            raise BizError(422, f"未知字段：{name}")
        if not meta.get("editable", False):
            raise BizError(422, f"字段「{name}」为 ePortal 计算字段/只读字段，不允许修改（total_price 等计算字段由 ePortal 计算）")
        ftype = meta.get("type", "text")
        if ftype == "select":
            # 下拉仅为录入提示，不限制取值：不同订单税率/条款各不相同，
            # 智眸原值（如 13%）或业务员手填值可能不在预设 options 内，最终由 ePortal 业务校验兜底
            pass
        elif ftype == "boolean":
            if not isinstance(value, bool) and str(value).strip().lower() not in _BOOL_TRUE | {"false", "0", "否", ""}:
                raise BizError(422, f"字段「{name}」须为布尔值")
        elif ftype == "date" and str(value).strip() and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(value).strip()):
            raise BizError(422, f"字段「{name}」日期格式须为 YYYY-MM-DD")
    for name, meta in fields.items():  # 必填：不允许把已有值清空（草稿允许留空待人工补全）
        if meta.get("required") and meta.get("editable", False) and name in changes \
                and (changes[name] is None or str(changes[name]) == ""):
            raise BizError(422, f"必填字段「{name}」不能清空")


def _plan_memory_decisions(db: Session, order: dict, changed: list[tuple[str, str, str]],
                           memory_choices: dict, feedback_choices: dict) -> list[dict]:
    """回写前确定每个变更字段的记忆决策（长期/单次/不记忆；负反馈 覆盖/单次），缺选择即拒绝。"""
    auto_map = order.get("auto_modified") or {}
    decisions = []
    for name, before, after in changed:
        ann = auto_map.get(name)
        rule = db.get(MemoryRule, ann.get("rule_id")) if isinstance(ann, dict) and ann.get("rule_id") else None
        if rule is not None and rule.status == "enabled" and rule.rule_type == "permanent":
            fb = feedback_choices.get(name)
            if fb not in ("override", "once"):
                raise BizError(422, f"字段「{name}」此前由长期记忆自动修改，本次被人工改写：请选择「覆盖原规则」或「仅本次单次修改」")
            decisions.append({"field": name, "before": before, "after": after, "rule": rule, "mode": fb})
        else:
            choice = memory_choices.get(name, "permanent")  # 默认记为长期规则
            if choice not in ("permanent", "once", "none"):
                raise BizError(422, f"字段「{name}」记忆选项无效：{choice}")
            decisions.append({"field": name, "before": before, "after": after, "rule": None, "mode": choice})
    return decisions


def _apply_memory_decisions(db: Session, customer: str, order_ref: str, operator: dict,
                            decisions: list[dict]) -> None:
    """回写成功后落地记忆：history 留痕 + 规则新建/覆盖/单次消费。"""
    op = lambda **kw: dict(operator_id=operator.get("id"), operator_name=operator.get("name"), order_ref=order_ref, **kw)
    for d in decisions:
        name, before, after = d["field"], d["before"], d["after"]
        log(db, "manual_modify", None, field=name, before=before, after=after, **op())
        rule = d["rule"]
        if rule is not None:  # 负反馈
            if d["mode"] == "override":
                rule.new_value = after
                rule.updated_by = operator.get("id") or ""
                rule.updated_at = utcnow()
                log(db, "rule_override", None, rule=rule, field=name, before=before, after=after,
                    remark="覆盖原规则：后续订单按新值匹配", **op())
            else:
                log(db, "once_modify", None, rule=rule, field=name, before=before, after=after,
                    remark="原规则保持不变，本次修改仅对当前订单生效", **op())
            continue
        anchor = before  # 未命中记忆时，修改前的值即智眸原值
        mode = d["mode"]
        if mode == "permanent":
            r = MemoryRule(customer_name=customer, field_name=name, old_value=anchor, new_value=after,
                           rule_type="permanent", status="enabled", effective_count=0,
                           source="human_diff", created_by=operator.get("id") or "",
                           updated_by=operator.get("id") or "")
            db.add(r)
            db.flush()
            log(db, "memory_record", None, rule=r, field=name, before=before, after=after,
                remark="人工修改沉淀为长期记忆，一次生效", **op())
        elif mode == "once":
            r = MemoryRule(customer_name=customer, field_name=name, old_value=anchor, new_value=after,
                           rule_type="once", status="enabled", effective_count=1,
                           source="once_modify", created_by=operator.get("id") or "",
                           updated_by=operator.get("id") or "")
            db.add(r)
            db.flush()
            r.hit_count = 1
            r.effective_count = 0
            r.status = "disabled"  # 仅当前订单生效：命中消费一次后自动失效
            r.last_hit_time = utcnow()
            db.add(HitLog(rule_id=r.id, order_id=None, form_id=order_ref, hit=True, field_name=name,
                          value_before=before, value_after=after, hit_time=utcnow()))
            log(db, "once_modify", None, rule=r, field=name, before=before, after=after,
                remark="仅本次单次修改：规则命中消费一次后自动失效", **op())
        # mode == "none"：不产生任何记忆规则


def save_eportal_changes(db: Session, background_tasks, session, body) -> dict:
    """ePortal 编辑会话保存：schema 校验 → 版本化回写（409 不覆盖）→ 留痕/记忆/纠正案例。"""
    order = get_adapter().get_order_for_edit(session.order_id, session.user_id)
    fields = order.get("fields") or {}
    changes = {k: ("" if v is None else v) for k, v in (body.changes or {}).items()}

    _validate_schema_changes(fields, changes)
    if body.items is not None and (not isinstance(body.items, list) or any(not isinstance(r, dict) for r in body.items)):
        raise BizError(422, "items 须为对象数组（产品行）")
    if body.attachments is not None and (not isinstance(body.attachments, list) or any(not isinstance(r, dict) for r in body.attachments)):
        raise BizError(422, "attachments 须为对象数组")
    if body.attachments is not None:  # 必填附件槽不能删除
        current = {a.get("id"): a for a in (order.get("attachments") or [])}
        kept_ids = {a.get("id") for a in body.attachments}
        missing = [cur.get("name") or cid for cid, cur in current.items()
                   if cur.get("required") and cid not in kept_ids]
        if missing:
            raise BizError(422, f"必填附件不能删除：{'、'.join(missing)}")

    changed = [(n, fields[n].get("value"), v) for n, v in changes.items() if fields[n].get("value") != v]
    decisions = _plan_memory_decisions(db, order, changed, body.memory_choices or {}, body.feedback_choices or {})

    operator = {"id": session.user_id, "name": session.user_name}
    try:
        updated = get_adapter().update_order_for_edit(
            session.order_id, operator, body.expected_version,
            {n: v for n, _, v in changed}, body.items, body.attachments)
    except EPortalConflictError as exc:
        log(db, "writeback_conflict", None, order_ref=session.order_id,
            remark=str(exc), operator_id=session.user_id, operator_name=session.user_name)
        db.commit()
        raise BizError(409, str(exc))
    except EPortalError as exc:
        raise BizError(502, f"回写 ePortal 失败：{exc}")

    _apply_memory_decisions(db, order.get("customer_name") or "", session.order_id, operator, decisions)
    cases = _create_correction_cases(db, background_tasks, session, order, updated, decisions, body.error_descriptions or {})
    db.commit()
    return {
        "status": "saved",
        "order": updated,
        "changed": [{"field": n, "before": b, "after": a} for n, b, a in changed],
        "cases": cases,
        "message": "修改已同步回写 ePortal，可返回 ePortal 核对并提交订单",
    }


def _create_correction_cases(db: Session, background_tasks, session, order: dict, updated: dict,
                             decisions: list[dict], descriptions: dict) -> list[dict]:
    """每个非空错误说明建一条待处理纠正案例（Task 5 实现 Agent 处理）。"""
    auto_map = order.get("auto_modified") or {}
    by_rule = {}
    for field_name, ann in auto_map.items():
        if isinstance(ann, dict) and ann.get("rule_id"):
            by_rule[field_name] = db.get(MemoryRule, ann["rule_id"])
    cases = []
    for field_name, desc in (descriptions or {}).items():
        text = ("" if desc is None else str(desc)).strip()
        if not text:
            continue
        decision = next((d for d in decisions if d["field"] == field_name), None)
        rule = by_rule.get(field_name)
        case = CorrectionCase(
            order_ref=session.order_id,
            version=updated.get("version", order.get("version")),
            customer_name=order.get("customer_name") or "",
            field_name=field_name,
            original_value=(rule.old_value if rule else (decision["before"] if decision else "")),
            memory_value=(rule.new_value if rule else None),
            final_value=(decision["after"] if decision else (order.get("fields", {}).get(field_name, {}) or {}).get("value", "")),
            description=text,
            operator_id=session.user_id,
            operator_name=session.user_name,
            state="pending",
        )
        db.add(case)
        db.flush()
        log(db, "correction_case_created", None, rule=rule, field=field_name,
            before=case.original_value, after=case.final_value,
            remark=f"纠正案例 #{case.id}：{text[:120]}",
            operator_id=session.user_id, operator_name=session.user_name, order_ref=session.order_id)
        cases.append({"id": case.id, "field": field_name, "state": case.state})
        if settings.agent_endpoint and background_tasks is not None:
            background_tasks.add_task(_process_case_task, case.id)
    return cases


def _process_case_task(case_id: int) -> None:
    from ..db import SessionLocal
    from .correction_agent import process_case

    db = SessionLocal()
    try:
        process_case(db, case_id)
    finally:
        db.close()
