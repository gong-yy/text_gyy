from conftest import headers, intake


def test_eportal_can_find_local_order_by_intellisight_id_without_person_login(client):
    created = intake(client, "Acme", {"Customer ID": "C-1"}, task_id="T001533")

    response = client.get("/api/orders/lookup?intellisight_id=T001533")

    assert response.status_code == 200
    assert response.json()["order_id"] == created["order_id"]
    assert response.json()["intellisight_id"] == "T001533"

    locked = client.post(f"/api/orders/{created['order_id']}/lock", json={})
    assert locked.status_code == 200
