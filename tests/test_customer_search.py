"""T2 客户搜索：复用 ePortal 客户资料并回填 Costing Sheet 字段。"""
from conftest import headers


def test_customer_search_returns_matching_eportal_customer(client):
    created = client.post("/api/mock/eportal/orders", json={
        "customer_name": "Acme Shanghai",
        "fields": {
            "customer_id": "AC-001",
            "customer_address": "Shanghai",
            "customer_payment_term": "30D",
            "user_name": "Kiko",
        },
    }).json()

    response = client.get("/api/orders/customers/search?q=AC-001", headers=headers("sales1"))

    assert response.status_code == 200, response.text
    assert response.json()["items"] == [{
        "customer_name": "Acme Shanghai", "customer_id": "AC-001",
        "customer_address": "Shanghai", "customer_payment_term": "30D", "user_name": "Kiko",
    }]


def test_customer_search_turns_an_invalid_eportal_response_into_a_readable_gateway_error(client, monkeypatch):
    import httpx
    import app.adapters.eportal as eportal

    monkeypatch.setattr(eportal.settings, "eportal_mode", "http")
    monkeypatch.setattr(eportal.settings, "eportal_service_username", "t-service")
    monkeypatch.setattr(eportal.settings, "eportal_service_password", "secret")
    monkeypatch.setattr(
        eportal.httpx, "post",
        lambda *a, **k: httpx.Response(200, json={"code": 0}, headers={"Set-Cookie": "PHPSESSID=abc; path=/"}),
    )
    monkeypatch.setattr(eportal.httpx, "get", lambda *a, **k: httpx.Response(200, text="not-json"))

    response = client.get("/api/orders/customers/search?q=Acme", headers=headers("sales1"))

    assert response.status_code == 502
    assert "客户查询返回格式无法识别" in response.json()["detail"]


def test_customer_search_uses_the_eportal_w_query_parameter(monkeypatch):
    """客户 ID 与名称都须经 ePortal 的 api/searchCustomer?w 搜索。"""
    import httpx
    import app.adapters.eportal as eportal

    monkeypatch.setattr(eportal.settings, "eportal_mode", "http")
    monkeypatch.setattr(eportal.settings, "eportal_base_url", "https://eportal.example")
    monkeypatch.setattr(eportal.settings, "eportal_service_username", "t-service")
    monkeypatch.setattr(eportal.settings, "eportal_service_password", "secret")
    calls = []

    def post(url, **kwargs):
        calls.append(("post", url, kwargs))
        assert url == "https://eportal.example/ae.php/api/login"
        assert kwargs["json"] == {"name": "t-service", "password": "secret"}
        return httpx.Response(200, json={"code": 0, "msg": "ok"}, headers={"Set-Cookie": "PHPSESSID=sess1; path=/"})

    def get(url, **kwargs):
        calls.append(("get", url, kwargs))
        assert url == "https://eportal.example/ae.php/api/searchCustomer"
        assert kwargs["params"] == {"w": "Acme"}
        assert kwargs["headers"]["Cookie"] == "PHPSESSID=sess1"
        return httpx.Response(200, json=[{"customer_name": "Acme", "company_id": "AC-1", "customer_address": "Shanghai", "phone_id": "13800138000"}])

    monkeypatch.setattr(eportal.httpx, "post", post)
    monkeypatch.setattr(eportal.httpx, "get", get)

    result = eportal.search_customers(None, "Acme")
    assert result[0]["customer_id"] == "AC-1"
    assert result[0]["customer_name"] == "Acme"
    assert result[0]["user_contact"] == "13800138000"
    assert [c[0] for c in calls] == ["post", "get"]


def test_customer_search_works_without_service_account_by_sending_id_or_name_as_w(monkeypatch):
    """公开 searchCustomer 不应因未配置服务账号而在发请求前失败。"""
    import httpx
    import app.adapters.eportal as eportal

    monkeypatch.setattr(eportal.settings, "eportal_mode", "http")
    monkeypatch.setattr(eportal.settings, "eportal_base_url", "https://eportal.example")
    monkeypatch.setattr(eportal.settings, "eportal_service_username", "")
    monkeypatch.setattr(eportal.settings, "eportal_service_password", "")
    requested = []

    def post(*_args, **_kwargs):
        raise AssertionError("公开客户搜索不应先请求登录接口")

    def get(url, **kwargs):
        requested.append((url, kwargs))
        return httpx.Response(200, json=[{
            "company_id": "1201B", "customer_name": "测试客户", "customer_address": "上海"
        }])

    monkeypatch.setattr(eportal.httpx, "post", post)
    monkeypatch.setattr(eportal.httpx, "get", get)

    assert eportal.search_customers(None, "1201B")[0]["customer_id"] == "1201B"
    assert eportal.search_customers(None, "测试")[0]["customer_name"] == "测试客户"
    assert [call[1]["params"] for call in requested] == [{"w": "1201B"}, {"w": "测试"}]


def test_customer_search_parses_the_php_array_response_seen_in_eportal(monkeypatch):
    """真实接口直接打印 PHP array 时，T 仍应提取客户 ID、名称和地址。"""
    import httpx
    import app.adapters.eportal as eportal

    monkeypatch.setattr(eportal.settings, "eportal_mode", "http")
    monkeypatch.setattr(eportal.HttpEPortalAdapter, "_web_login", lambda self: "sess1")
    monkeypatch.setattr(
        eportal.httpx, "get",
        lambda *a, **k: httpx.Response(200, text='''array(1) {\n  [0] => array(4) {\n    ["cid"] => int(1)\n    ["company_id"] => string(5) "1201B"\n    ["customer_name"] => string(5) "测试客户"\n    ["customer_address"] => string(8) "上海"\n  }\n}'''),
    )

    assert eportal.search_customers(None, "测试客户") == [{
        "customer_name": "测试客户", "customer_id": "1201B", "customer_address": "上海",
        "customer_payment_term": "", "user_name": "", "user_contact": "",
    }]


def test_protected_eportal_request_reauthenticates_once_when_eportal_requires_identity(monkeypatch):
    """受保护接口的旧 token 被拒绝时，服务端重登并以新 token 重试一次。"""
    import httpx
    import app.adapters.eportal as eportal

    monkeypatch.setattr(eportal.settings, "eportal_mode", "http")
    monkeypatch.setattr(eportal.settings, "eportal_base_url", "https://eportal.example")
    monkeypatch.setattr(eportal.settings, "eportal_service_username", "t-service")
    monkeypatch.setattr(eportal.settings, "eportal_service_password", "secret")
    eportal.HttpEPortalAdapter._auth_cache = None
    issued = iter(["expired-token", "fresh-token"])
    seen_tokens = []

    def post(url, **kwargs):
        return httpx.Response(200, json={"code": 1, "token": next(issued)})

    def get(url, **kwargs):
        seen_tokens.append(kwargs["headers"]["user_token"])
        if len(seen_tokens) == 1:
            return httpx.Response(200, json={"code": 0, "msg": "需要身份验证"})
        return httpx.Response(200, json={"items": [{"descr": "Acme", "company_id": "AC-1"}]})

    monkeypatch.setattr(eportal.httpx, "post", post)
    monkeypatch.setattr(eportal.httpx, "get", get)

    response = eportal.HttpEPortalAdapter()._request("get", "https://eportal.example/api/protected")
    assert response.json()["items"][0]["company_id"] == "AC-1"
    assert seen_tokens == ["expired-token", "fresh-token"]


def test_protected_request_reauthenticates_on_identity_required_message(monkeypatch):
    """无验证客户搜索是例外；其余接口仍在认证失效时重登。"""
    import httpx
    import app.adapters.eportal as eportal

    monkeypatch.setattr(eportal.settings, "eportal_mode", "http")
    monkeypatch.setattr(eportal.settings, "eportal_base_url", "https://eportal.example")
    monkeypatch.setattr(eportal.settings, "eportal_service_username", "t-service")
    monkeypatch.setattr(eportal.settings, "eportal_service_password", "secret")
    eportal.HttpEPortalAdapter._auth_cache = None
    issued = iter(["expired-token", "fresh-token"])
    request_tokens = []

    def post(url, **kwargs):
        return httpx.Response(200, json={"code": 1, "token": next(issued)})

    def get(url, **kwargs):
        request_tokens.append(kwargs["headers"]["user_token"])
        if len(request_tokens) == 1:
            return httpx.Response(200, json={"code": 0, "msg": "需要身份验证"})
        return httpx.Response(200, json={"items": [{"descr": "Acme", "company_id": "AC-1"}]})

    monkeypatch.setattr(eportal.httpx, "post", post)
    monkeypatch.setattr(eportal.httpx, "get", get)

    response = eportal.HttpEPortalAdapter()._request("get", "https://eportal.example/api/protected")
    assert response.json()["items"][0]["company_id"] == "AC-1"
    assert request_tokens == ["expired-token", "fresh-token"]


def test_t2_common_script_does_not_rebuild_form_controls_on_a_timer():
    """The page owns its radio controls; a timer must not replace their DOM nodes."""
    from pathlib import Path

    script = (Path(__file__).parents[1] / "app" / "static" / "common.js").read_text(encoding="utf-8")
    assert "setInterval(installT2Extras" not in script
