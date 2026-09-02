"""ground_truth 实测：D:/GK/T/data/ground_truth 的智眸提取数据 → intake → 记忆匹配 → T2 闭环。

数据缺失时跳过（CI 环境无该目录也能通过其余测试）。
"""
import csv
import os
from pathlib import Path

import pytest

from app.eportal_schema import zhimou_row_to_intake
from conftest import (create_mock_eportal_order, create_rule, eportal_form,
                      headers, intake, start_edit_session)

GROUND_TRUTH_DIR = Path(os.environ.get("T_SYSTEM_GROUND_TRUTH_DIR", "D:/GK/T/data/ground_truth"))


def load_ground_truth() -> list[tuple[str, dict]]:
    rows = []
    for path in sorted(GROUND_TRUTH_DIR.glob("*.csv")):
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if any((v or "").strip() for v in row.values()):
                    rows.append((path.name, row))
    return rows


pytestmark = pytest.mark.skipif(
    not GROUND_TRUTH_DIR.exists() or not list(GROUND_TRUTH_DIR.glob("*.csv")),
    reason=f"ground_truth 数据不存在：{GROUND_TRUTH_DIR}",
)


def test_zhimou_row_mapping_shape():
    rows = load_ground_truth()
    assert rows, "ground_truth 目录存在但无有效数据"
    name, row = rows[0]
    payload = zhimou_row_to_intake(row)
    assert payload["customer_name"], "客户名（记忆锚定键）不能为空"
    assert payload["fields"]["Tax Structure"] in ("13%", "0%")
    assert payload["fields"]["Quotation Ref / PO No"]
    assert "description_list" in payload["fields"]  # 产品行列由 intake 拆分
    assert "task_id" in payload["meta"]  # 元数据不入表单
    # 智眸普遍缺失的字段不出现在映射结果里（等人工/记忆补全）
    for absent in ("Sales Person", "Customer ID", "Date"):
        assert absent not in payload["fields"]


def test_bulk_intake_all_ground_truth_orders(client):
    rows = load_ground_truth()
    customers = set()
    for name, row in rows:
        payload = zhimou_row_to_intake(row)
        r = intake(client, payload["customer_name"], payload["fields"], task_id=payload["meta"].get("task_id"))
        assert r["status"] == "created", f"{name}: {r}"
        form = eportal_form(client, r["form_id"])
        customers.add(payload["customer_name"])
        # 智眸原值保持（未命中记忆）
        assert form["fields"]["Tax Structure"] == row["tax_structure"].strip()
        assert form["fields"]["Quotation Ref / PO No"] == row["quotation_ref_po_no"].strip()
        assert form["fields"]["Sales Person"] == ""  # 智眸缺失字段 → 空值待人工
        # 产品行以结构列（qty/unit_price）为准拆分；描述文本内的分号不拆行
        qty_parts = [p for p in row["qty_list"].split(";") if p.strip()] if row["qty_list"] else []
        price_parts = [p for p in row["unit_price_list"].split(";") if p.strip()] if row["unit_price_list"] else []
        desc_parts = [p for p in row["description_list"].split(";") if p.strip()] if row["description_list"] else []
        expect_rows = max(len(qty_parts), len(price_parts), 1) if (qty_parts or price_parts or desc_parts) else 0
        assert len(form["items"]) == expect_rows
        if form["items"]:
            assert form["items"][0]["description"].startswith(desc_parts[0][:30])
        # 命中与未命中均记录：每张单至少一条 hit_log（Tax Structure 查询）
        assert r["applied_memory"] == []
    assert len(customers) >= 3, f"应覆盖多客户，实际：{customers}"


def test_memory_hit_for_configured_customer(client):
    """为某真实客户配置税率规则后，其订单自动修改并标注。"""
    rows = load_ground_truth()
    target = next((row for _n, row in rows if row["tax_structure"].strip() == "13%"
                   and (row["customer_name"] or "").strip()), None)
    assert target, "找不到 13% 样本"
    customer = target["customer_name"].strip()
    create_rule(client, customer, "Tax Structure", "13%", "CN VAT13")
    payload = zhimou_row_to_intake(target)
    r = intake(client, payload["customer_name"], payload["fields"])
    assert r["applied_memory"], "应命中税率规则"
    assert r["applied_memory"][0]["applied_value"] == "CN VAT13"
    form = eportal_form(client, r["form_id"])
    assert form["fields"]["Tax Structure"] == "CN VAT13"
    assert form["auto_modified"]["Tax Structure"]["rule_id"] == r["applied_memory"][0]["rule_id"]


def test_ground_truth_t2_loop_fills_human_field_and_learns(client):
    """真实数据闭环：T2 补 Sales Person（空值锚定记忆）→ 第二张同客户新单自动填充。"""
    rows = load_ground_truth()
    target = next((row for _n, row in rows if (row["customer_name"] or "").strip()), None)
    payload = zhimou_row_to_intake(target)
    customer = payload["customer_name"]
    r1 = intake(client, customer, payload["fields"])
    assert r1["status"] == "created"
    # 业务员从 ePortal 进入 T2（ticket 换会话）
    ticket = issue_ticket(client, r1["form_id"])
    resp = client.post("/api/eportal/session", json={"ticket": ticket})
    assert resp.status_code == 200
    saved = client.post("/api/eportal/orders/current/save", json={
        "expected_version": 1,
        "changes": {"Sales Person": "Louis Lu"},
        "items": None, "attachments": None,
        "error_descriptions": {"Sales Person": "该客户销售固定为 Louis Lu"},
        "memory_choices": {"Sales Person": "permanent"},  # 空值 → 长期规则（空值填充）
        "feedback_choices": {},
    })
    assert saved.status_code == 200, saved.text
    form = eportal_form(client, r1["form_id"])
    assert form["fields"]["Sales Person"] == "Louis Lu"
    # 第二张同客户新单：T1 空值命中，Sales Person 自动填充，无需再进 T2
    r2 = intake(client, customer, payload["fields"])
    assert r2["applied_memory"], "空值填充规则应命中"
    assert any(a["field"] == "Sales Person" and a["applied_value"] == "Louis Lu"
               for a in r2["applied_memory"])
    form2 = eportal_form(client, r2["form_id"])
    assert form2["fields"]["Sales Person"] == "Louis Lu"


def test_tax_variance_rows_both_intake(client):
    """同一 PO 的 13%/0% 两个版本（智眸提取差异）均正常建单，各自保持提取值。"""
    rows = load_ground_truth()
    by_po = {}
    for _n, row in rows:
        po = row["quotation_ref_po_no"].strip()
        by_po.setdefault(po, []).append(row["tax_structure"].strip())
    variance = {po: taxes for po, taxes in by_po.items() if len(set(taxes)) > 1}
    if not variance:
        pytest.skip("数据中无同 PO 多税率样本")
    po, taxes = next(iter(variance.items()))
    for tax in taxes:
        row = next(r for _n, r in rows if r["quotation_ref_po_no"].strip() == po and r["tax_structure"].strip() == tax)
        payload = zhimou_row_to_intake(row)
        r = intake(client, payload["customer_name"], payload["fields"])
        assert r["status"] == "created"
        assert eportal_form(client, r["form_id"])["fields"]["Tax Structure"] == tax


def issue_ticket(client, order_id: str) -> str:
    resp = client.post(f"/api/mock/eportal/orders/{order_id}/ticket",
                       json={"user_id": "sales1", "user_name": "张销售"})
    assert resp.status_code == 200, resp.text
    return resp.json()["ticket"]
