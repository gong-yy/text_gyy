"""Task 5：纠正案例持久化与内部 Agent 后台学习 —— 只建新规则、绝不覆盖、失败不影响订单。"""
import pytest

from app.models import CorrectionCase, History, MemoryRule
from app.normalize import normalize_value
from app.services.correction_agent import ask_internal_model, process_case
from conftest import (create_mock_eportal_order, create_rule, headers,
                      start_edit_session)


def save_with_error_description(client, field: str, final: str, description: str,
                                original: str = "13%", customer: str = "Acme"):
    """建单 → 换会话 → 保存（含错误说明，选择「不记忆」由 Agent 决定是否沉淀）。"""
    order = create_mock_eportal_order(client, customer=customer, fields={field: original})
    start_edit_session(client, order)
    return client.post("/api/eportal/orders/current/save", json={
        "expected_version": order["version"],
        "changes": {field: final},
        "items": None, "attachments": None,
        "error_descriptions": {field: description},
        "memory_choices": {field: "none"},
        "feedback_choices": {},
    })


def valid_result(correct_value: str) -> dict:
    return {"error_type": "tax_mapping", "normalized_original_value": "13",
            "correct_value": correct_value, "rule_type": "permanent", "summary": "税务映射"}


def latest_case(db):
    return db.query(CorrectionCase).order_by(CorrectionCase.id.desc()).first()


def find_rule(db, customer: str, field: str, original: str) -> MemoryRule | None:
    candidates = db.query(MemoryRule).filter(MemoryRule.field_name == field).all()
    for r in candidates:
        if (normalize_value(r.customer_name) == normalize_value(customer)
                and normalize_value(r.old_value) == normalize_value(original)):
            return r
    return None


def test_valid_agent_result_creates_new_long_term_rule_after_successful_writeback(client, session, monkeypatch):
    monkeypatch.setattr("app.services.correction_agent.ask_internal_model", lambda payload: {
        "error_type": "tax_mapping", "normalized_original_value": "13",
        "correct_value": "CN_VAT13", "rule_type": "permanent", "summary": "税务映射",
    })
    saved = save_with_error_description(client, "Tax Structure", "CN_VAT13", "13% 应为 VAT13")
    assert saved.status_code == 200
    case = latest_case(session)
    assert case is not None
    assert case.state == "pending"  # 未配置模型端点 → 保持待处理，不自动调度
    assert case.original_value == "13%" and case.final_value == "CN_VAT13"
    assert case.operator_name == "张销售"
    process_case(session, case.id)
    assert case.state == "processed"
    rule = find_rule(session, "Acme", "Tax Structure", "13%")
    assert rule is not None and rule.new_value == "CN_VAT13"
    assert rule.source == "agent" and rule.status == "enabled"
    # history 记录：案例创建 + Agent 规则创建
    types = {h.op_type for h in session.query(History).all()}
    assert {"correction_case_created", "agent_rule_created"} <= types


def test_agent_conflict_never_overwrites_existing_long_term_rule(client, session, monkeypatch):
    create_rule(client, "Acme", "Tax Structure", "13%", "OLD")
    case = CorrectionCase(order_ref="SO-X", version=1, customer_name="Acme", field_name="Tax Structure",
                          original_value="13%", memory_value=None, final_value="NEW",
                          description="客户要求改新值", operator_id="sales1", operator_name="张销售",
                          state="pending")
    session.add(case)
    session.commit()
    monkeypatch.setattr("app.services.correction_agent.ask_internal_model",
                        lambda _: valid_result("NEW"))
    process_case(session, case.id)
    assert find_rule(session, "Acme", "Tax Structure", "13%").new_value == "OLD"  # 绝不覆盖
    assert session.get(CorrectionCase, case.id).state == "conflict"


def test_agent_rejects_mismatched_or_non_permanent_results(client, session, monkeypatch):
    save_with_error_description(client, "Tax Structure", "CN_VAT13", "应为 CN VAT")
    case = latest_case(session)
    # correct_value 与人工最终值不一致 → 失败，不建规则
    monkeypatch.setattr("app.services.correction_agent.ask_internal_model",
                        lambda _: valid_result("OTHER"))
    process_case(session, case.id)
    assert case.state == "failed" and "不一致" in case.error
    # rule_type 非 permanent → 失败
    case2_src = save_with_error_description(client, "签约公司", "上海", "固定上海")
    assert case2_src.status_code == 200
    case2 = latest_case(session)
    monkeypatch.setattr("app.services.correction_agent.ask_internal_model",
                        lambda _: {**valid_result("上海"), "rule_type": "once"})
    process_case(session, case2.id)
    assert case2.state == "failed" and "permanent" in case2.error
    assert find_rule(session, "Acme", "Tax Structure", "13%") is None


def test_agent_failure_does_not_affect_saved_order(client, session, monkeypatch):
    def boom(payload):
        raise RuntimeError("模型服务不可用")
    monkeypatch.setattr("app.services.correction_agent.ask_internal_model", boom)
    saved = save_with_error_description(client, "Tax Structure", "CN_VAT13", "13% 应为 VAT13")
    assert saved.status_code == 200  # 订单已成功保存，不受 Agent 失败影响
    form = None
    order_id = saved.json()["order"]["order_id"]
    detail = client.get(f"/api/mock/eportal/orders/{order_id}").json()
    assert detail["fields"]["Tax Structure"]["value"] == "CN_VAT13"
    case = latest_case(session)
    process_case(session, case.id)
    assert case.state == "failed"
    types = {h.op_type for h in session.query(History).all()}
    assert "agent_failed" in types


def test_agent_calls_lm_studio_rest_v1_and_reads_structured_content(monkeypatch):
    """LM Studio REST v1 以 system_prompt/input 收参，结果位于 content。"""
    captured = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"content": '{"error_type":"tax_mapping","normalized_original_value":"13",'
                               '"correct_value":"CN_VAT13","rule_type":"permanent","summary":"税务映射"}'}

    def fake_post(url, *, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return Response()

    monkeypatch.setattr("app.services.correction_agent.settings.agent_endpoint", "http://10.106.4.46:1234/api/v1/chat")
    monkeypatch.setattr("app.services.correction_agent.settings.agent_model", "qwen/qwen3.5-35b-a3b")
    monkeypatch.setattr("app.services.correction_agent.httpx.post", fake_post)

    result = ask_internal_model({"field_name": "Tax Structure", "original_value": "13%"})

    assert captured["url"] == "http://10.106.4.46:1234/api/v1/chat"
    assert captured["json"]["model"] == "qwen/qwen3.5-35b-a3b"
    assert "system_prompt" in captured["json"]
    assert '"field_name": "Tax Structure"' in captured["json"]["input"]
    assert result["correct_value"] == "CN_VAT13"
