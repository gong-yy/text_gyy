"""Task 2：T 的服务器端编辑会话 —— ticket 换 HttpOnly 会话 cookie，操作人只在服务端。"""
from app.adapters.eportal import issue_mock_ticket
from conftest import create_mock_eportal_order, start_edit_session


def test_ticket_creates_http_only_edit_session_without_exposing_operator_in_url(client):
    ticket = issue_mock_ticket("SO-1", "sales1", "张销售", version=1)
    response = client.post("/api/eportal/session", json={"ticket": ticket})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    detail = client.get("/api/eportal/orders/current")
    assert detail.status_code == 200
    assert detail.json()["operator"]["id"] == "sales1"
    assert detail.json()["operator"]["name"] == "张销售"


def test_edit_session_rejects_missing_or_invalid_cookie(client):
    assert client.get("/api/eportal/orders/current").status_code == 401
    client.cookies.set("t_edit_session", "forged-session-id")
    assert client.get("/api/eportal/orders/current").status_code == 401
    client.cookies.clear()  # 避免伪造 cookie 污染共享 client 的后续用例


def test_each_ticket_yields_independent_session(client):
    order = create_mock_eportal_order(client, fields={"tax_structure": "13%"})
    first = start_edit_session(client, order, user=("sales1", "张销售"))
    second = start_edit_session(client, order, user=("sales2", "李销售"))
    assert first["operator"] == {"id": "sales1", "name": "张销售"}
    assert second["operator"] == {"id": "sales2", "name": "李销售"}
    detail = client.get("/api/eportal/orders/current")
    assert detail.json()["operator"]["id"] == "sales2"  # 最新会话生效
    assert detail.json()["order"]["order_id"] == order["order_id"]


def test_session_loads_order_only_through_eportal_adapter(client):
    """会话只绑定换票时的订单；数据一律经 ePortal 适配器加载。"""
    order = create_mock_eportal_order(client, customer="Acme", fields={"tax_structure": "13%"})
    start_edit_session(client, order)
    detail = client.get("/api/eportal/orders/current").json()
    assert detail["order"]["customer_name"] == "Acme"
    assert detail["order"]["version"] == order["version"]
    assert detail["order"]["fields"]["tax_structure"]["value"] == "13%"
