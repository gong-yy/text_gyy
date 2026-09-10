"""T2 页面契约：本地 order_id 启动、T 系统登录态与 ePortal 同步重发。"""


def test_t2_page_uses_local_order_bootstrap(client):
    page = client.get("/t2").text
    assert "/api/orders/${orderId}" in page
    assert "/api/orders/lookup" in page
    assert "/lock" in page
    assert "/save" in page
    assert "ticket" not in page
    assert 'params.get("token")' not in page
    assert 'get("order_id")' in page
    assert 'get("intellisight_id")' in page
    assert 'get("form_id")' not in page
    # 进入时不展示历史回写状态；提交后仅弹出成功/失败结果。
    assert "statusBadge(order.status)" not in page
    assert "重新同步" not in page
    assert 'alert(result.status === "synced" ? "提交成功" : "提交失败")' in page


def test_t2_page_uses_t_system_login_and_renders_local_fields(client):
    page = client.get("/t2").text
    assert "/static/common.js" in page
    assert "API.user" in page
    assert "data-field" in page
    assert "readonly" in page
    # 当前 T2 以 ePortal 表单号展示订单上下文；旧版“本地订单编号”文案已移除。
    assert "ePortal 表单" in page
