"""内部 Agent 后台学习：纠正案例 → 内部大模型结构化归纳 → 自动创建新长期规则。

硬约束：
- Agent 只在 ePortal 回写成功后后台处理，失败不影响已保存订单；
- 只能新建规则，绝不自动覆盖既有长期规则（同键已有启用长期规则 → conflict）；
- 模型只输出结构化 JSON（error_type / normalized_original_value / correct_value /
  rule_type / summary），且 correct_value 必须与人工最终值完全一致才采纳。
"""
import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..history import log
from ..models import CorrectionCase, MemoryRule
from ..normalize import normalize_value
from ..util import utcnow

REQUIRED_KEYS = ("error_type", "normalized_original_value", "correct_value", "rule_type", "summary")


class AgentError(Exception):
    pass


def ask_internal_model(payload: dict) -> dict:
    """调用内部模型端点，返回严格 JSON。未配置端点即失败（案例保持可重试）。"""
    if not settings.agent_endpoint:
        raise AgentError("内部模型端点未配置（config [agent] endpoint）")
    headers = {"Content-Type": "application/json"}
    if settings.agent_api_key:
        headers["X-Api-Key"] = settings.agent_api_key
    resp = httpx.post(settings.agent_endpoint, json=payload, headers=headers, timeout=settings.agent_timeout)
    if resp.status_code >= 400:
        raise AgentError(f"内部模型返回 HTTP {resp.status_code}")
    try:
        result = resp.json()
    except Exception as exc:
        raise AgentError(f"内部模型响应不是有效 JSON：{exc}")
    if not isinstance(result, dict):
        raise AgentError("内部模型响应须为 JSON 对象")
    return result


def _validate_result(result: dict, case: CorrectionCase) -> None:
    missing = [k for k in REQUIRED_KEYS if not result.get(k)]
    if missing:
        raise AgentError(f"模型结果缺少必填字段：{'、'.join(missing)}")
    if result["rule_type"] != "permanent":
        raise AgentError(f"rule_type 须为 permanent（收到：{result['rule_type']}），Agent 只建长期规则")
    if result["correct_value"] != case.final_value:
        raise AgentError(f"模型 correct_value（{result['correct_value']}）与人工最终值（{case.final_value}）不一致，不采纳")


def find_same_key_rule(db: Session, customer: str, field: str, normalized_original: str) -> MemoryRule | None:
    """同键（客户+字段+原值，标准化后）且启用的长期规则。"""
    candidates = (
        db.query(MemoryRule)
        .filter(MemoryRule.status == "enabled", MemoryRule.rule_type == "permanent",
                MemoryRule.field_name == field)
        .all()
    )
    target = normalize_value(normalized_original)
    for rule in candidates:
        if normalize_value(rule.customer_name) == normalize_value(customer) and normalize_value(rule.old_value) == target:
            return rule
    return None


def process_case(db: Session, case_id: int, http_client=None) -> CorrectionCase:
    """处理一条纠正案例（幂等：仅 pending 会被处理）。"""
    case = db.get(CorrectionCase, case_id)
    if case is None:
        raise ValueError(f"纠正案例不存在：{case_id}")
    if case.state != "pending":
        return case

    payload = {
        "customer_name": case.customer_name,
        "field_name": case.field_name,
        "original_value": case.original_value,
        "memory_value": case.memory_value,
        "final_value": case.final_value,
        "description": case.description,
        "order_id": case.order_ref,
        "operator": {"id": case.operator_id, "name": case.operator_name},
    }
    try:
        result = ask_internal_model(payload)
        _validate_result(result, case)
    except Exception as exc:  # Agent 调用/解析失败不影响已保存订单，案例记失败可重试
        case.state = "failed"
        case.error = str(exc)
        case.processed_at = utcnow()
        log(db, "agent_failed", None, field=case.field_name, before=case.original_value,
            after=case.final_value, remark=f"纠正案例 #{case.id} 处理失败：{exc}（可后台重试）",
            operator_id="agent", operator_name="内部Agent", order_ref=case.order_ref)
        db.commit()
        return case

    existing = find_same_key_rule(db, case.customer_name, case.field_name, result["normalized_original_value"])
    if existing is not None:
        case.state = "conflict"
        case.agent_summary = result["summary"]
        case.agent_result = result
        case.rule_id = existing.id
        case.error = f"同键长期规则 #{existing.id} 已存在（{existing.old_value} → {existing.new_value}），Agent 不覆盖"
        case.processed_at = utcnow()
        log(db, "agent_conflict", None, rule=existing, field=case.field_name,
            before=case.original_value, after=case.final_value,
            remark=f"纠正案例 #{case.id} 与既有规则 #{existing.id} 冲突，仅记录不覆盖",
            operator_id="agent", operator_name="内部Agent", order_ref=case.order_ref)
        db.commit()
        return case

    rule = MemoryRule(
        customer_name=case.customer_name,
        field_name=case.field_name,
        old_value=result["normalized_original_value"],
        new_value=result["correct_value"],
        rule_type="permanent",
        status="enabled",
        effective_count=0,
        source="agent",
        created_by="agent",
        updated_by="agent",
    )
    db.add(rule)
    db.flush()
    case.state = "processed"
    case.agent_summary = result["summary"]
    case.agent_result = result
    case.error = None
    case.rule_id = rule.id
    case.processed_at = utcnow()
    log(db, "agent_rule_created", None, rule=rule, field=case.field_name,
        before=case.original_value, after=rule.new_value,
        remark=f"Agent 依据纠正案例 #{case.id} 创建长期规则：{result['summary']}",
        operator_id="agent", operator_name="内部Agent", order_ref=case.order_ref)
    db.commit()
    return case
