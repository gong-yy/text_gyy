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
            "tax_structure": "10",
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
        "field_name": "tax_structure", "label": "Tax Structure", "value": "10", "type": "select",
        "editable": True, "required": True,
        "options": [{"value": "3.00", "label": "CN_VAT3"}, {"value": "6.00", "label": "CN_VAT6"},
                    {"value": "9.00", "label": "CN_VAT9"}, {"value": "10", "label": "CN_VAT10"},
                    {"value": "13.00", "label": "CN_VAT13"}, {"value": "16.00", "label": "CN_VAT16"},
                    {"value": "0", "label": "CN_VAT0"}],
    }
    assert result["items"][0]["product_id"] == "P-1"
    assert result["item_schema"]["currency"]["type"] == "select"
    assert result["attachments"] == [{"id": 1, "slot_name": "合同/报价单/ePO", "filename": "contract.pdf", "name": "contract.pdf"}]


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
