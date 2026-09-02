"""canonical COSTING SHEET 全量 schema：表头字段 / 产品行列 / 附件槽 / 智眸拆分。"""
import pytest

from app.adapters.eportal import EPortalError, get_adapter, issue_mock_ticket
from app.eportal_schema import (ATTACHMENT_SLOTS, HEADER_FIELDS, ITEM_COLUMNS,
                                default_item_schema, merge_order_fields,
                                split_zhimou_items)
from conftest import (create_mock_eportal_order, eportal_form, intake,
                      start_edit_session)


def test_canonical_schema_shape():
    assert len(HEADER_FIELDS) == 31
    assert len(ITEM_COLUMNS) == 19
    assert len(ATTACHMENT_SLOTS) == 4
    # 计算列只读
    readonly = {c for c, _l, _t, _r, editable, _o in ITEM_COLUMNS if not editable}
    assert {"unit_cost", "total_cost", "total_price", "tax_payable", "gp", "gp_percent"} <= readonly
    # 必填附件槽
    assert {s["name"] for s in ATTACHMENT_SLOTS if s["required"]} == {"合同/报价单/ePO", "J-FORM", "J-FORM (Approval)"}


def test_split_zhimou_items_multi_row():
    scalars, items = split_zhimou_items({
        "customer_name": "TradeDesk",
        "description_list": "ThinkStation P3;ThinkPad X1;ThinkCentre M9",
        "qty_list": "1;2;3",
        "unit_price_list": "100.00;200;300.5",
    })
    assert scalars == {"customer_name": "TradeDesk"}
    assert [i["description"] for i in items] == ["ThinkStation P3", "ThinkPad X1", "ThinkCentre M9"]
    assert [i["qty"] for i in items] == ["1", "2", "3"]
    assert [i["unit_price"] for i in items] == ["100.00", "200", "300.5"]
    assert all("line_id" not in i for i in items)


def test_merge_order_fields_preserves_canonical_metadata():
    fields = merge_order_fields({"Tax Structure": "13%", "自定义字段": "x"})
    assert fields["Tax Structure"]["type"] == "select"
    assert fields["Tax Structure"]["options"] == ["CN VAT13", "CN VAT0", "CN VAT6", "Others"]
    assert fields["Tax Structure"]["value"] == "13%"
    assert fields["Sales Person"]["value"] == "" and fields["Sales Person"]["required"] is True
    assert fields["SO"]["editable"] is False
    assert fields["产品含税总金额"]["editable"] is False
    assert fields["自定义字段"]["editable"] is True and fields["自定义字段"]["group"] == "其他"


def test_intake_order_carries_full_costing_sheet(client):
    r = intake(client, "TradeDesk", {
        "Customer Name": "TradeDesk",
        "签约公司": "港宽科技（上海）有限公司北京分公司",
        "Tax Structure": "13%",
        "Customer Payment Term": "30D",
        "description_list": "ThinkStation P3 Tower",
        "qty_list": "3",
        "unit_price_list": "29901.62",
    })
    assert r["status"] == "created"
    form = eportal_form(client, r["form_id"])
    # 全量表单：智眸没提取的字段以空值出现，等人工/记忆补全
    assert form["fields"]["Sales Person"] == ""
    assert form["fields"]["Customer ID"] == ""
    assert form["fields"]["Tax Structure"] == "13%"
    assert form["fields"]["签约公司"] == "港宽科技（上海）有限公司北京分公司"
    assert form["fields"]["SO"] == "" and "SO" in form["fields"]
    # 产品行结构化 + 列 schema
    assert form["items"][0]["description"] == "ThinkStation P3 Tower"
    assert form["items"][0]["qty"] == "3"
    assert form["item_schema"]["biz_category"]["options"] == ["BAU_BIZ", "NEWBIZ"]
    assert form["item_schema"]["unit_cost"]["editable"] is False
    # 命名附件槽
    atts = {a["name"]: a for a in form["attachments"]}
    assert set(atts) == {"合同/报价单/ePO", "J-FORM", "J-FORM (Approval)", "GCF"}
    assert atts["合同/报价单/ePO"]["required"] is True
    assert atts["GCF"]["removable"] is True


def test_save_rejects_deleting_required_attachment_slot(client):
    order = create_mock_eportal_order(client, fields={"Tax Structure": "13%"})
    start_edit_session(client, order)
    resp = client.post("/api/eportal/orders/current/save", json={
        "expected_version": order["version"], "changes": {},
        "items": None, "attachments": [{"id": "gcf", "name": "GCF", "removable": True, "required": False, "uploaded": False}],
        "error_descriptions": {},
    })
    assert resp.status_code == 422
    assert "合同/报价单/ePO" in resp.json()["detail"]


def test_mock_rejects_editing_readonly_item_column(client):
    order = create_mock_eportal_order(
        client, fields={"Tax Structure": "13%"},
        items=[{"line_id": "L-1", "description": "ThinkPad T14", "qty": "1",
                "unit_price": "13217.7", "gp_percent": "12.5"}],
    )
    adapter = get_adapter()
    with pytest.raises(EPortalError):
        adapter.update_order_for_edit(
            order["order_id"], {"id": "sales1", "name": "张销售"}, 1, {},
            items=[{"line_id": "L-1", "description": "ThinkPad T14", "qty": "1",
                    "unit_price": "13217.7", "gp_percent": "30"}])  # 改 GP% 被拒


def test_save_typed_item_columns_via_session(client):
    order = create_mock_eportal_order(
        client, fields={"Tax Structure": "13%"},
        items=[{"line_id": "L-1", "description": "ThinkPad T14", "qty": "1", "unit_price": "13217.7"}],
    )
    start_edit_session(client, order)
    resp = client.post("/api/eportal/orders/current/save", json={
        "expected_version": 1, "changes": {},
        "items": [{"line_id": "L-1", "description": "ThinkPad T14", "qty": "2",
                   "unit_price": "13217.7", "biz_category": "BAU_BIZ"}],
        "attachments": None, "error_descriptions": {},
    })
    assert resp.status_code == 200, resp.text
    form = eportal_form(client, order["order_id"])
    assert form["items"][0]["qty"] == "2"
    assert form["items"][0]["biz_category"] == "BAU_BIZ"
    assert form["items"][0]["gp_percent"] in ("", None)
