"""Task 4：T2 页面契约 —— ticket 启动（URL 不带 token/身份）、错误说明控件、动态 schema 渲染。"""


def test_t2_page_uses_ticket_bootstrap_and_has_error_description_control(client):
    page = client.get("/t2").text
    assert "/api/eportal/session" in page
    assert "error_descriptions" in page
    assert 'params.get("token")' not in page
    assert 'params.get("ticket")' in page
    # 保存后确认弹窗：是否返回 ePortal
    assert "是否返回 ePortal" in page
    # 下拉支持自定义值
    assert "其他（自定义）" in page


def test_t2_page_renders_by_declared_field_types(client):
    page = client.get("/t2").text
    # 文本/日期/布尔/下拉/只读 控件类型
    assert 'type="date"' in page or "type=\"date\"" in page or "input_date" in page
    assert "checkbox" in page
    assert "<select" in page
    assert "readonly" in page
    # 产品行与附件区
    assert "items" in page
    assert "attachments" in page
    # 不渲染独立登录控件 / Agent 输出
    assert "登录" not in page.split("</script>")[0] or "no-login" in page
