import httpx
import pytest

from app.adapters import eportal
from app.adapters.eportal import EPortalError, HttpEPortalAdapter


class StubAdapter:
    def __init__(self, result):
        self.result = result

    def get_ticket_order(self, eportal_id):
        return self.result


def test_http_adapter_gets_ticket_order_and_normalizes_values(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={
            "id": 679,
            "customer_name": "TradeDesk",
            "tax_structure": "13.00",
            "customer_payment_term": "30D",
            "products": [{"product_id": "P-1", "biz_category": "BAU_BIZ", "currency": "CNY"}],
            "att1": {"id": 1, "name": "contract.pdf"},
        })

    monkeypatch.setattr(eportal.httpx, "get", get)
    monkeypatch.setattr(eportal.settings, "eportal_base_url", "http://10.106.4.173")
    monkeypatch.setattr(eportal.settings, "eportal_ticket_order_path", "/ae.php/api/ticket?id={id}", raising=False)

    result = HttpEPortalAdapter().get_ticket_order(679)

    assert calls == [("http://10.106.4.173/ae.php/api/ticket?id=679", {
        "headers": {"Content-Type": "application/json"}, "timeout": 15,
    })]
    assert result["id"] == 679
    assert next(field for field in result["fields"] if field["field_name"] == "tax_structure") == {
        "field_name": "tax_structure", "label": "Tax Structure", "value": "13.00", "type": "select",
        "editable": True, "required": True,
        "options": [{"value": "3.00", "label": "CN_VAT3"}, {"value": "6.00", "label": "CN_VAT6"},
                    {"value": "9.00", "label": "CN_VAT9"}, {"value": "13.00", "label": "CN_VAT13"},
                    {"value": "16.00", "label": "CN_VAT16"}, {"value": "0", "label": "CN_VAT0"}],
    }
    assert result["items"][0]["product_id"] == "P-1"
    assert result["item_schema"]["currency"]["type"] == "select"
    assert result["attachments"] == [{"id": 1, "slot_name": "合同/报价单/ePO", "filename": "contract.pdf", "name": "contract.pdf"}]


def test_ticket_order_uses_business_labels_for_the_eportal_edit_layout():
    result = eportal.normalize_ticket_order({
        "id": 679,
        "sales_person": "wendy.chen",
        "customer_name": "TradeDesk",
        "customer_address": "Shanghai",
        "user_name": "Kiko Zhao",
    })
    fields = {field["field_name"]: field for field in result["fields"]}

    assert fields["sales_person"]["label"] == "Sales Person"
    assert fields["customer_name"]["label"] == "Customer Name"
    assert fields["customer_address"]["label"] == "Customer Delivery Address"
    assert fields["user_name"]["label"] == "End User Name (To receive the item)"


def test_normalize_ticket_order_preserves_every_eportal_product_row_in_order():
    """新源码的 products 数组有多行时，T2 必须保留每行独立的字段数据。"""
    result = eportal.normalize_ticket_order({
        "id": 46913,
        "products": [
            {"product_id": "PC-001", "description": "Switch", "qty": "2", "pass": 1},
            {"product_id": "PC-002", "description": "Power cable", "qty": "8", "pass": 0},
            {"product_id": "PC-003", "description": "Stack cable", "qty": "2", "pass": 2},
        ],
    })

    assert [(row["product_id"], row["description"], row["qty"], row["pass"]) for row in result["items"]] == [
        ("PC-001", "Switch", "2", 1),
        ("PC-002", "Power cable", "8", 0),
        ("PC-003", "Stack cable", "2", 2),
    ]


def test_normalize_ticket_order_reads_string_and_numeric_attachments():
    """真实 ePortal ticket 报文：att1~att4 为文件名文本，att_1~att_4 为文件 ID。"""
    result = eportal.normalize_ticket_order({
        "id": 679,
        "customer_name": "TradeDesk",
        "location": "beijing",
        "att1": "1572_合同.pdf",
        "att2": "1572_JFORM.xlsx",
        "att4": "1572_JFORM_APPROVAL.xlsx",
        "att_1": 171770,
        "att_2": 171771,
        "att_3": 0,
        "att_4": 171771,
    })
    assert result["attachments"] == [
        {"id": 171770, "slot_name": "合同/报价单/ePO", "filename": "1572_合同.pdf", "name": "1572_合同.pdf"},
        {"id": 171771, "slot_name": "J-FORM", "filename": "1572_JFORM.xlsx", "name": "1572_JFORM.xlsx"},
        {"id": 171771, "slot_name": "J-FORM (Approval)", "filename": "1572_JFORM_APPROVAL.xlsx", "name": "1572_JFORM_APPROVAL.xlsx"},
    ]


def test_http_adapter_rejects_mismatched_or_invalid_ticket_order_ids(monkeypatch):
    monkeypatch.setattr(eportal.httpx, "get", lambda *args, **kwargs: httpx.Response(200, json={"id": 680}))
    monkeypatch.setattr(eportal.settings, "eportal_ticket_order_path", "/ae.php/api/ticket?id={id}", raising=False)

    with pytest.raises(EPortalError, match="ID 不一致"):
        HttpEPortalAdapter().get_ticket_order(679)
    with pytest.raises(EPortalError, match="正整数"):
        HttpEPortalAdapter().get_ticket_order(0)


def test_http_adapter_hides_invalid_ticket_order_response(monkeypatch):
    monkeypatch.setattr(eportal.httpx, "get", lambda *args, **kwargs: httpx.Response(502, text="upstream internals"))
    monkeypatch.setattr(eportal.settings, "eportal_ticket_order_path", "/ae.php/api/ticket?id={id}", raising=False)

    with pytest.raises(EPortalError, match="服务错误 HTTP 502"):
        HttpEPortalAdapter().get_ticket_order(679)


def test_ticket_order_route_returns_adapter_model(monkeypatch, client):
    expected = {"id": 679, "fields": [], "items": [], "attachments": [], "item_schema": {}}
    monkeypatch.setattr("app.api.eportal_session.get_adapter", lambda: StubAdapter(expected))

    response = client.get("/api/eportal/ticket-orders/679")

    assert response.status_code == 200
    assert response.json() == expected


def test_ticket_order_route_hides_upstream_error(monkeypatch, client):
    class FailingAdapter:
        def get_ticket_order(self, eportal_id):
            raise EPortalError("upstream internals")

    monkeypatch.setattr("app.api.eportal_session.get_adapter", lambda: FailingAdapter())

    response = client.get("/api/eportal/ticket-orders/679")

    assert response.status_code == 502
    assert "upstream internals" not in response.text
    assert response.json()["detail"] == "无法读取 ePortal 订单"
