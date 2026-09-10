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
