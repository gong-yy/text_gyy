import httpx

from conftest import get_order, headers, intake


def test_successful_creation_reports_final_result_to_zhimou(client, monkeypatch):
    from app.config import settings
    import app.services.zhimou_callback as callback

    monkeypatch.setattr(settings, "zhimou_callback_url", "https://callback.example/t_sys_back.php")
    captured = {}

    def fake_post(url, *, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return httpx.Response(200, json={"success": True, "message": "回调处理成功"})

    monkeypatch.setattr(callback.httpx, "post", fake_post)

    created = intake(client, "Acme", {"Customer ID": "C-1"}, task_id="T000001")

    assert captured["url"] == "https://callback.example/t_sys_back.php"
    assert captured["json"] == {
        "task_id": "T000001",
        "success": True,
        "msg": "ePortal 创单成功",
        "eportal_id": created["form_id"],
    }
    assert captured["headers"]["Content-Type"] == "application/json; charset=UTF-8"
    assert get_order(client, created["order_id"])["zhimou_callback"]["status"] == "succeeded"


def test_failed_creation_reports_failure_to_zhimou(client, monkeypatch, session):
    from app.adapters.eportal import EPortalError
    from app.config import settings
    import app.services.order as order_service
    import app.services.zhimou_callback as callback
    from app.models import Order

    class OfflineAdapter:
        def create_order(self, *args, **kwargs):
            raise EPortalError("ePortal offline")

    monkeypatch.setattr(order_service, "get_adapter", lambda: OfflineAdapter())
    monkeypatch.setattr(settings, "zhimou_callback_url", "https://callback.example/t_sys_back.php")
    captured = {}

    def fake_post(url, *, json, headers, timeout):
        captured.update(url=url, json=json)
        return httpx.Response(200, json={"success": True})

    monkeypatch.setattr(callback.httpx, "post", fake_post)

    response = client.post(
        "/api/intake",
        json={"customer_name": "Acme", "fields": {}, "task_id": "T000002"},
    )

    assert response.status_code == 502
    assert captured["json"] == {
        "task_id": "T000002",
        "success": False,
        "msg": "ePortal offline",
    }
    order = session.query(Order).one()
    assert order.status == "create_failed"
    assert order.payload["zhimou_callback"]["status"] == "succeeded"


def test_rejected_creation_reports_failure_to_zhimou(client, monkeypatch):
    from app.adapters.eportal import CreateResult
    from app.config import settings
    import app.services.order as order_service
    import app.services.zhimou_callback as callback

    class RejectingAdapter:
        def create_order(self, *args, **kwargs):
            return CreateResult(form_id="", version=1, accepted=False, response={"code": 0, "msg": "PO 校验失败"})

    monkeypatch.setattr(order_service, "get_adapter", lambda: RejectingAdapter())
    monkeypatch.setattr(settings, "zhimou_callback_url", "https://callback.example/t_sys_back.php")
    captured = {}

    def fake_post(url, *, json, headers, timeout):
        captured["json"] = json
        return httpx.Response(200, json={"success": True})

    monkeypatch.setattr(callback.httpx, "post", fake_post)
    response = client.post(
        "/api/intake",
        json={"customer_name": "Acme", "fields": {}, "task_id": "T000004"},
    )

    assert response.status_code == 200
    assert response.json() == {"code": 0, "msg": "PO 校验失败"}
    assert captured["json"] == {
        "task_id": "T000004",
        "success": False,
        "msg": "PO 校验失败",
    }


def test_failed_callback_can_be_retried_without_recreating_eportal_order(client, monkeypatch):
    from app.config import settings
    import app.services.zhimou_callback as callback

    monkeypatch.setattr(settings, "zhimou_callback_url", "https://callback.example/t_sys_back.php")
    attempts = []

    def offline_post(*args, **kwargs):
        attempts.append(kwargs["json"])
        raise httpx.ConnectError("offline", request=httpx.Request("POST", args[0]))

    monkeypatch.setattr(callback.httpx, "post", offline_post)
    created = intake(client, "Acme", {"Customer ID": "C-1"}, task_id="T000003")
    assert get_order(client, created["order_id"])["zhimou_callback"]["status"] == "failed"

    def success_post(*args, **kwargs):
        attempts.append(kwargs["json"])
        return httpx.Response(200, json={"success": True})

    monkeypatch.setattr(callback.httpx, "post", success_post)
    response = client.post(
        f"/api/orders/{created['order_id']}/zhimou-callback/resend",
        headers=headers("sales1"),
    )

    assert response.status_code == 200
    assert response.json()["callback"]["status"] == "succeeded"
    assert len(attempts) == 2


def test_multipart_intake_forwards_zhimou_attachment_to_eportal(client, monkeypatch):
    """智眸上传的原始文件应以同名 multipart 字段转交给 ePortal 建单接口。"""
    import json

    import httpx
    from app.adapters.eportal import HttpEPortalAdapter
    from app.config import settings
    import app.services.zhimou_callback as callback

    monkeypatch.setattr(settings, "eportal_mode", "http")
    monkeypatch.setattr(settings, "eportal_base_url", "http://eportal.example")
    monkeypatch.setattr(settings, "eportal_create_path", "/ae.php/api/entry")
    monkeypatch.setattr(callback.httpx, "post", lambda *args, **kwargs: httpx.Response(200, json={"success": True}))
    import app.services.order as order_service
    monkeypatch.setattr(order_service, "get_adapter", HttpEPortalAdapter)
    captured = {}

    def fake_post(url, *, files=None, json=None, headers, timeout):
        if json is not None:
            return httpx.Response(200, json={"success": True})
        captured.update(url=url, files=files, headers=headers, timeout=timeout)
        return httpx.Response(200, json={"code": "1", "id": "EP-1001"})

    import app.adapters.eportal as eportal
    monkeypatch.setattr(eportal.httpx, "post", fake_post)
    response = client.post(
        "/api/intake",
        data={"data": json.dumps({"customer_name": "Acme", "task_id": "T-attachment"})},
        files={"contract": ("PO-1001.pdf", b"%PDF-test-content", "application/pdf")},
        headers=headers("zhimou"),
    )

    assert response.status_code == 200, response.text
    assert captured["url"] == "http://eportal.example/ae.php/api/entry"
    assert dict(captured["files"])["contract"] == ("PO-1001.pdf", b"%PDF-test-content", "application/pdf")


def test_multipart_intake_forwards_intellisight_id_to_eportal(client, monkeypatch):
    """智眸的 intellisight_id 必须原样进入 ePortal 建单 data。"""
    import json

    import httpx
    from app.adapters.eportal import HttpEPortalAdapter
    from app.config import settings
    import app.services.order as order_service
    import app.services.zhimou_callback as callback
    import app.adapters.eportal as eportal

    monkeypatch.setattr(settings, "eportal_base_url", "http://eportal.example")
    monkeypatch.setattr(settings, "eportal_create_path", "/ae.php/api/entry")
    monkeypatch.setattr(order_service, "get_adapter", HttpEPortalAdapter)
    captured = {}

    def fake_post(url, *, files=None, json=None, headers, timeout):
        if json is not None:
            return httpx.Response(200, json={"success": True})
        captured["data"] = json_module.loads(dict(files)["data"][1])
        return httpx.Response(200, json={"code": "1", "id": "EP-1003"})

    json_module = json
    monkeypatch.setattr(eportal.httpx, "post", fake_post)
    monkeypatch.setattr(callback.httpx, "post", fake_post)
    response = client.post(
        "/api/intake",
        data={"data": json.dumps({"customer_name": "TradeDesk", "intellisight_id": "T001532"})},
        files={"trace": ("trace.txt", b"demo", "text/plain")},
        headers=headers("zhimou"),
    )

    assert response.status_code == 200, response.text
    assert captured["data"]["intellisight_id"] == "T001532"


def test_multipart_intake_stores_attachment_with_its_original_filename(client, monkeypatch, session, tmp_path):
    """T 应在转发前留存附件，保留原文件名并将位置记入订单。"""
    import json

    import httpx
    from app.adapters.eportal import HttpEPortalAdapter
    from app.config import settings
    from app.models import Order
    import app.services.order as order_service
    import app.services.zhimou_callback as callback

    monkeypatch.setattr(settings, "attachment_storage_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "eportal_base_url", "http://eportal.example")
    monkeypatch.setattr(order_service, "get_adapter", HttpEPortalAdapter)
    monkeypatch.setattr(callback.httpx, "post", lambda *args, **kwargs: httpx.Response(200, json={"success": True}))
    import app.adapters.eportal as eportal
    monkeypatch.setattr(eportal.httpx, "post", lambda *args, **kwargs: httpx.Response(200, json={"code": "1", "id": "EP-1002"}))

    response = client.post(
        "/api/intake",
        data={"data": json.dumps({"customer_name": "Acme", "task_id": "T-store"})},
        files={"contract": ("PO-1002.pdf", b"stored-content", "application/pdf")},
        headers=headers("zhimou"),
    )

    assert response.status_code == 200, response.text
    order = session.query(Order).one()
    attachment = order.payload["attachments"][0]
    assert attachment["filename"] == "PO-1002.pdf"
    assert attachment["path"].replace("\\", "/").endswith("T-store/PO-1002.pdf")
    assert (tmp_path / "uploads" / "T-store" / "PO-1002.pdf").read_bytes() == b"stored-content"
