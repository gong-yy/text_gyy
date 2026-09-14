"""T2 COSTING SHEET 需要的订单详情数据。"""
from conftest import get_order, headers, intake, lock


def test_order_detail_exposes_product_rows_and_attachments_for_t2(client):
    created = intake(
        client,
        "Acme",
        {"Customer Name": "Acme"},
    )

    detail = get_order(client, created["order_id"])

    assert detail["items"] == []
    assert detail["attachments"] == []


def test_order_detail_maps_source_attachment_slots_for_t2(client):
    """ePortal 源码里的 att1~att4 必须显示到 T2 的固定附件行。"""
    created = intake(client, "Acme", {
        "Customer Name": "Acme",
        "att1": {"name": "contract.pdf", "size": 38, "path": "/uploads/contract.pdf", "type": "application/pdf"},
        "att_2": {"name": "j-form.xlsx", "size": 7, "path": "/uploads/j-form.xlsx"},
    })

    attachments = get_order(client, created["order_id"])["attachments"]

    assert {item["field_name"] for item in attachments} == {"合同/报价单/ePO", "J-FORM"}
    assert {item["filename"] for item in attachments} == {"contract.pdf", "j-form.xlsx"}


def test_order_detail_maps_json_encoded_source_attachment_slots_for_t2(client):
    """部分 ePortal 回调会把 att1 序列化为字符串，T2 仍须识别。"""
    import json

    created = intake(client, "Acme", {
        "Customer Name": "Acme",
        "att1": json.dumps({"name": "contract.pdf", "size": 38, "path": "/uploads/contract.pdf"}),
    })

    attachments = get_order(client, created["order_id"])["attachments"]
    assert attachments[0]["slot_name"] == "合同/报价单/ePO"
    assert attachments[0]["filename"] == "contract.pdf"


def test_order_detail_preserves_eportal_attachment_slot_order_and_other_files(client):
    """源码中 att4 是 Approval、att3 是 GCF，files 是“其他附件”。"""
    created = intake(client, "Acme", {
        "Customer Name": "Acme",
        "att3": {"name": "gcf.pdf", "size": 3},
        "att4": {"name": "approval.xlsx", "size": 4},
        "files": '[{"name":"other.pdf","size":5,"path":"/uploads/other.pdf"}]',
    })

    attachments = get_order(client, created["order_id"])["attachments"]
    slot_by_file = {item["filename"]: item["slot_name"] for item in attachments}
    assert slot_by_file == {
        "gcf.pdf": "GCF",
        "approval.xlsx": "J-FORM (Approval)",
        "other.pdf": "其他附件",
    }


def test_t2_save_persists_product_rows(client):
    created = intake(client, "Acme", {"Customer Name": "Acme"})
    assert lock(client, created["order_id"], "sales1").status_code == 200

    response = client.post(
        f"/api/orders/{created['order_id']}/save",
        json={"changes": {}, "items": [{"product_id": "P-1", "qty": "2"}]},
        headers=headers("sales1"),
    )

    assert response.status_code == 200, response.text
    assert get_order(client, created["order_id"])["items"] == [{"product_id": "P-1", "qty": "2"}]


def test_t2_product_import_reads_source_excel_columns_a_b_c(client):
    """与 ePortal 源码一致：跳过首行标题，A/B/C 追加产品号、描述、Vendor Part No。"""
    from io import BytesIO
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Product Part No", "Description", "Vendor Part No"])
    sheet.append(["PC-001", "Switch", "V-001"])
    sheet.append(["PC-002", "Power Cable", "V-002"])
    content = BytesIO()
    workbook.save(content)

    response = client.post(
        "/api/orders/products/import",
        files={"file": ("products.xlsx", content.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers("sales1"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["items"] == [
        {"product_id": "PC-001", "description": "Switch", "PN": "V-001"},
        {"product_id": "PC-002", "description": "Power Cable", "PN": "V-002"},
    ]


def test_t2_save_keeps_optional_reason_and_save_mode_in_history(client, session):
    """每次人工字段修改都留存前后值、原因（可空）和单次/长期方式。"""
    from app.models import History

    created = intake(client, "Acme", {"Sales Person": "Old Name"})
    assert lock(client, created["order_id"], "sales1").status_code == 200

    response = client.post(
        f"/api/orders/{created['order_id']}/save",
        json={
            "changes": {"Sales Person": "New Name"},
            "memory_choices": {"Sales Person": "once"},
            "change_reasons": {"Sales Person": "客户已确认新的销售负责人"},
            "feedback_choices": {},
        },
        headers=headers("sales1"),
    )

    assert response.status_code == 200, response.text
    record = session.query(History).filter_by(order_id=created["order_id"], op_type="manual_modify").one()
    assert record.field_name == "Sales Person"
    assert record.value_before == "Old Name"
    assert record.value_after == "New Name"
    assert record.remark == "转换规则：客户已确认新的销售负责人；保存方式：单次"


def test_order_detail_returns_latest_conversion_rule_for_same_customer_and_field(client):
    """再次编辑时，只回填该客户该字段最近一次保存的转换规则。"""
    created = intake(client, "Acme", {"Sales Person": "Old Name"})
    assert lock(client, created["order_id"], "sales1").status_code == 200
    saved = client.post(
        f"/api/orders/{created['order_id']}/save",
        json={
            "changes": {"Sales Person": "New Name"},
            "memory_choices": {"Sales Person": "once"},
            "change_reasons": {"Sales Person": "英文姓名统一使用全名"},
            "feedback_choices": {},
        },
        headers=headers("sales1"),
    )
    assert saved.status_code == 200, saved.text

    detail = get_order(client, created["order_id"])

    assert detail["conversion_rules"] == {"Sales Person": "英文姓名统一使用全名"}


def test_order_detail_exposes_prior_select_values_for_the_same_field(client):
    """已保存的下拉值必须作为同一字段的后续可选项，且不串到别的字段。"""
    created = intake(client, "Acme", {"Tax Structure": "13.00", "签约公司": "shanghai"})
    assert lock(client, created["order_id"], "sales1").status_code == 200
    saved = client.post(
        f"/api/orders/{created['order_id']}/save",
        json={
            "changes": {"Tax Structure": "Special VAT", "签约公司": "beijing"},
            "memory_choices": {"Tax Structure": "once", "签约公司": "once"},
            "change_reasons": {},
            "feedback_choices": {},
        },
        headers=headers("sales1"),
    )
    assert saved.status_code == 200, saved.text

    detail = get_order(client, created["order_id"])

    assert detail["select_options"] == {
        "Tax Structure": ["Special VAT"],
        "签约公司": ["beijing"],
    }


def test_order_detail_exposes_prior_cost_currency_values(client):
    """产品行 Cost (Currency) 的历史选择也须回到下一次编辑所用的数据中。"""
    created = intake(client, "Acme", {"Customer Name": "Acme"})
    assert lock(client, created["order_id"], "sales1").status_code == 200
    saved = client.post(
        f"/api/orders/{created['order_id']}/save",
        json={
            "changes": {},
            "items": [{"currency": "JPY"}],
            "memory_choices": {"items.0.currency": "once"},
            "change_reasons": {},
            "feedback_choices": {},
        },
        headers=headers("sales1"),
    )
    assert saved.status_code == 200, saved.text

    detail = get_order(client, created["order_id"])

    assert detail["item_select_options"] == {"currency": ["JPY"]}
