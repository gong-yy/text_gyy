"""Task 3：结构化订单数据与版本化保存 —— 只读计算字段拒绝、409 冲突不覆盖、操作人留痕。"""
from conftest import (create_mock_eportal_order, eportal_form, headers,
                      start_edit_session, valid_save_payload)


def order_with_schema(client, version=7):
    return create_mock_eportal_order(
        client, customer="Acme",
        fields={
            "tax_structure": {"value": "13%", "type": "text", "editable": True,
                              "required": False, "options": [], "group": "税务信息"},
            "total_price": {"value": "100.00", "type": "text", "editable": False,
                            "required": False, "options": [], "group": "计算字段"},
            "payment_term": {"value": "30D", "type": "select", "editable": True,
                             "required": True, "options": ["30D", "45D"], "group": "付款信息"},
        },
        version=version,
    )


def test_save_sends_only_editable_fields_and_keeps_eportal_calculated_fields_readonly(client):
    order = order_with_schema(client)
    start_edit_session(client, order)
    response = client.post("/api/eportal/orders/current/save", json={
        "expected_version": 7,
        "changes": {"tax_structure": "CN_VAT13", "total_price": "999"},
        "items": [], "attachments": [], "error_descriptions": {},
    })
    assert response.status_code == 422
    assert "total_price" in response.json()["detail"]


def test_stale_expected_version_returns_409_without_overwriting_order(client):
    order = order_with_schema(client, version=8)
    start_edit_session(client, order)
    response = client.post("/api/eportal/orders/current/save", json=valid_save_payload(version=7))
    assert response.status_code == 409
    assert "刷新" in response.json()["detail"]
    # ePortal 的新版本未被 T 覆盖
    form = eportal_form(client, order["order_id"])
    assert form["version"] == 8
    assert form["fields"]["tax_structure"] == "13%"


def test_save_rejects_unknown_field(client):
    order = order_with_schema(client)
    start_edit_session(client, order)
    response = client.post("/api/eportal/orders/current/save", json={
        "expected_version": 7, "changes": {"no_such_field": "x"},
        "items": None, "attachments": None, "error_descriptions": {},
    })
    assert response.status_code == 422
    assert "no_such_field" in response.json()["detail"]


def test_save_allows_custom_select_value_but_not_clearing_required(client):
    """下拉允许自定义值（不同订单税率/条款不同，智眸原值可不在 options 内）；必填不允许清空。"""
    order = order_with_schema(client)
    start_edit_session(client, order)
    # 自定义下拉值（60D 不在预设选项）→ 放行
    resp = client.post("/api/eportal/orders/current/save", json={
        "expected_version": 7, "changes": {"payment_term": "60D"},
        "items": None, "attachments": None, "error_descriptions": {},
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["order"]["fields"]["payment_term"]["value"] == "60D"
    # 必填字段清空 → 422
    resp = client.post("/api/eportal/orders/current/save", json={
        "expected_version": 8, "changes": {"payment_term": ""},
        "items": None, "attachments": None, "error_descriptions": {},
    })
    assert resp.status_code == 422
    assert "payment_term" in resp.json()["detail"]


def test_successful_save_updates_eportal_and_logs_eportal_operator(client):
    order = order_with_schema(client)
    start_edit_session(client, order)  # sales1 / 张销售
    response = client.post("/api/eportal/orders/current/save", json={
        "expected_version": 7, "changes": {"tax_structure": "CN_VAT13"},
        "items": None, "attachments": None, "error_descriptions": {},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "saved"
    assert body["order"]["version"] == 8  # ePortal 返回的新版本成为当前版本
    assert body["order"]["fields"]["tax_structure"]["value"] == "CN_VAT13"
    form = eportal_form(client, order["order_id"])
    assert form["fields"]["tax_structure"] == "CN_VAT13"
    assert form["version"] == 8
    # history 记录 ePortal 操作人
    hist = client.get(f"/api/history?form_id={order['order_id']}", headers=headers("admin")).json()
    assert isinstance(hist, list), hist
    manual = [h for h in hist if h["op_type"] == "manual_modify"]
    assert manual and manual[0]["operator_name"] == "张销售"
    assert manual[0]["operator_id"] == "sales1"
    assert manual[0]["value_before"] == "13%" and manual[0]["value_after"] == "CN_VAT13"


def test_save_supports_structured_items_and_attachments(client):
    order = create_mock_eportal_order(
        client, customer="Acme", fields={"tax_structure": "13%"},
        items=[{"line_id": "L-1", "part_no": "P-001", "qty": 10, "total_price": "50.00"}],
        attachments=[{"id": "A-1", "name": "PO.pdf", "removable": True}],
    )
    start_edit_session(client, order)
    response = client.post("/api/eportal/orders/current/save", json={
        "expected_version": 1,
        "changes": {},
        # 只改可编辑列（qty）；total_price 为 ePortal 计算列，不提交则保留
        "items": [{"line_id": "L-1", "part_no": "P-001", "qty": 12}],
        "attachments": [],  # 删除全部附件
        "error_descriptions": {},
    })
    assert response.status_code == 200, response.text
    form = eportal_form(client, order["order_id"])
    assert form["items"][0]["qty"] == 12
    assert form["items"][0]["total_price"] == "50.00"  # 计算列由 ePortal 保留
    assert form["attachments"] == []


def test_save_rejects_malformed_items(client):
    order = order_with_schema(client)
    start_edit_session(client, order)
    response = client.post("/api/eportal/orders/current/save", json={
        "expected_version": 7, "changes": {}, "items": ["not-a-dict"],
        "attachments": None, "error_descriptions": {},
    })
    assert response.status_code == 422
