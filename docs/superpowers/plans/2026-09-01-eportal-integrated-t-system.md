# ePortal 集成 T 系统实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 将 T 系统改造成由 ePortal ticket 驱动的订单修改与内部 Agent 学习服务，同时保留智眸 intake 和记忆匹配能力。

**Architecture:** 新增 ePortal gateway adapter，负责换取 ticket、拉取订单和版本化回写；T 用服务器端编辑会话绑定 ePortal 操作人与订单。订单数据从扁平字段扩展为可编辑 schema、产品行和附件；保存成功后异步处理错误说明，并根据内部 Agent 的有效结构化结果自动新增而不覆盖长期规则。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Pydantic 2、httpx、pytest、原生 HTML/JavaScript。

**Spec:** `docs/superpowers/specs/2026-09-01-eportal-integrated-t-system-design.md`

## Global Constraints

- T 不维护 ePortal 业务员密码或独立业务员登录页。
- 浏览器 URL 仅传递短时一次性 opaque ticket，不传用户信息、订单内容或长期令牌。
- ePortal 是订单真源；T 不写计算字段，ePortal 负责最终业务校验与金额重算。
- 保存回写必须带 `expected_version`；HTTP 409 不覆盖 ePortal 的新版本。
- Agent 只在 ePortal 回写成功后后台处理；只能新建规则，不能自动覆盖既有长期规则。
- 每个生产行为先添加一个会失败的自动化测试，并观察预期失败。
- 当前工作目录没有 Git 元数据；完成后报告文件变更与测试结果，不执行提交。

---

### Task 1: 配置与 ePortal gateway 契约

**Files:**
- Modify: `app/config.py`
- Modify: `config.ini`
- Modify: `app/adapters/eportal.py`
- Test: `tests/test_eportal_gateway.py`

**Interfaces:**
- Produces `EPortalAdapter.exchange_ticket(ticket: str) -> EditContext`。
- Produces `EPortalAdapter.get_order_for_edit(order_id: str, operator_id: str) -> dict`。
- Produces `EPortalAdapter.update_order_for_edit(order_id: str, operator: dict, expected_version: int, changes: dict) -> dict`。

- [x] **Step 1: Write the failing mock-ticket test**

```python
def test_mock_ticket_is_single_use_and_returns_operator_context(client):
    ticket = issue_mock_ticket("SO-1", "sales1", "张销售", version=3)
    first = client.post("/api/eportal/session", json={"ticket": ticket})
    assert first.status_code == 200
    assert first.json()["operator"] == {"id": "sales1", "name": "张销售"}
    assert client.post("/api/eportal/session", json={"ticket": ticket}).status_code == 401
```

- [x] **Step 2: Run test to verify it fails**

Run: `C:\Users\GYY\anaconda3\python.exe -m pytest tests/test_eportal_gateway.py::test_mock_ticket_is_single_use_and_returns_operator_context -q`

Expected: FAIL because `/api/eportal/session` and ticket exchange do not exist.

- [x] **Step 3: Implement the minimal gateway and config**

Add ePortal ticket exchange/detail/update paths and a server-side `service_token` configuration value. Add immutable `EditContext(order_id, version, user_id, user_name)` and implement mock ticket storage with expiry/consumed state. Extend `HttpEPortalAdapter` to call the three internal ePortal APIs with the server-side service credential, translating HTTP 409 to `EPortalConflictError`.

- [x] **Step 4: Run gateway tests**

Run: `C:\Users\GYY\anaconda3\python.exe -m pytest tests/test_eportal_gateway.py -q`

Expected: PASS.

### Task 2: T 的服务器端编辑会话与 T2 启动接口

**Files:**
- Create: `app/edit_session.py`
- Create: `app/api/eportal_session.py`
- Modify: `app/main.py`
- Modify: `app/api/orders.py`
- Test: `tests/test_eportal_session.py`

**Interfaces:**
- Consumes `EPortalAdapter.exchange_ticket` and `get_order_for_edit` from Task 1.
- Produces `POST /api/eportal/session` that exchanges a ticket and sets `t_edit_session` HttpOnly cookie.
- Produces `get_edit_session(request) -> EditSession` dependency.

- [x] **Step 1: Write the failing session-isolation test**

```python
def test_ticket_creates_http_only_edit_session_without_exposing_operator_in_url(client):
    ticket = issue_mock_ticket("SO-1", "sales1", "张销售", version=1)
    response = client.post("/api/eportal/session", json={"ticket": ticket})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    detail = client.get("/api/eportal/orders/current")
    assert detail.json()["operator"]["id"] == "sales1"
```

- [x] **Step 2: Run test to verify it fails**

Run: `C:\Users\GYY\anaconda3\python.exe -m pytest tests/test_eportal_session.py::test_ticket_creates_http_only_edit_session_without_exposing_operator_in_url -q`

Expected: FAIL because the cookie-backed session endpoint does not exist.

- [x] **Step 3: Implement the minimal session flow**

Create an in-memory session store keyed by `secrets.token_urlsafe(32)`, with expiry no later than ticket expiry. Exchange the ticket once, persist the resulting context server-side, set a `httponly=True`, `samesite="lax"` cookie, and expose a current-order endpoint that loads data only through the ePortal adapter. Replace T2’s raw `token` query handling with ticket exchange.

- [x] **Step 4: Run session tests**

Run: `C:\Users\GYY\anaconda3\python.exe -m pytest tests/test_eportal_session.py -q`

Expected: PASS.

### Task 3: 结构化订单数据与版本化保存

**Files:**
- Modify: `app/schemas.py`
- Modify: `app/services/order.py`
- Modify: `app/api/orders.py`
- Modify: `app/adapters/eportal.py`
- Test: `tests/test_order_editing.py`

**Interfaces:**
- Consumes `EditSession` from Task 2.
- Produces `POST /api/eportal/orders/current/save` accepting `expected_version`, scalar `changes`, `items`, `attachments`, `error_descriptions`.
- Produces 409 response with ePortal conflict message when version has changed.

- [x] **Step 1: Write the failing editable/readonly test**

```python
def test_save_sends_only_editable_fields_and_keeps_eportal_calculated_fields_readonly(client):
    start_edit_session(client, order_with_schema())
    response = client.post("/api/eportal/orders/current/save", json={
        "expected_version": 7,
        "changes": {"tax_structure": "CN_VAT13", "total_price": "999"},
        "items": [], "attachments": [], "error_descriptions": {},
    })
    assert response.status_code == 422
    assert "total_price" in response.json()["detail"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `C:\Users\GYY\anaconda3\python.exe -m pytest tests/test_order_editing.py::test_save_sends_only_editable_fields_and_keeps_eportal_calculated_fields_readonly -q`

Expected: FAIL because current save endpoint accepts arbitrary local fields.

- [x] **Step 3: Implement schema validation and gateway save**

Represent ePortal field schema as `{value, type, editable, required, options, group}`. Validate all submitted field names, reject readonly fields, validate select options and required values, preserve product lines/attachments as structured JSON, and call `update_order_for_edit` using the edit-session operator and expected version. Log ePortal user identity in history and return the complete updated order response. Translate adapter conflict to `HTTPException(409)` without applying local overwrite.

- [x] **Step 4: Write the failing conflict test**

```python
def test_stale_expected_version_returns_409_without_overwriting_order(client):
    start_edit_session(client, order_with_schema(version=8))
    response = client.post("/api/eportal/orders/current/save", json=valid_save_payload(version=7))
    assert response.status_code == 409
    assert "刷新" in response.json()["detail"]
```

- [x] **Step 5: Run focused edit tests**

Run: `C:\Users\GYY\anaconda3\python.exe -m pytest tests/test_order_editing.py -q`

Expected: PASS.

### Task 4: T2 动态编辑界面

**Files:**
- Modify: `app/static/t2.html`
- Modify: `app/static/common.js`
- Test: `tests/test_t2_page.py`

**Interfaces:**
- Consumes `GET /api/eportal/orders/current` and save endpoint from Tasks 2–3.
- Produces ticket-only page bootstrap and payload containing `changes`, `items`, `attachments`, `error_descriptions`.

- [x] **Step 1: Write the failing page-contract test**

```python
def test_t2_page_uses_ticket_bootstrap_and_has_error_description_control(client):
    page = client.get("/t2").text
    assert "/api/eportal/session" in page
    assert "error_descriptions" in page
    assert "params.get(\"token\")" not in page
```

- [x] **Step 2: Run test to verify it fails**

Run: `C:\Users\GYY\anaconda3\python.exe -m pytest tests/test_t2_page.py::test_t2_page_uses_ticket_bootstrap_and_has_error_description_control -q`

Expected: FAIL because T2 currently consumes a raw token and has no error-description field.

- [x] **Step 3: Implement dynamic controls**

On first load read only `ticket`, exchange it, then load the current ePortal order. Render fields by declared type: text, date, boolean, select and readonly. Render editable product lines and permitted attachment actions. Under each changed editable scalar field render an error-description textarea, and send only non-empty descriptions in the save payload. Do not render independent T login controls or agent output.

- [x] **Step 4: Run T2 tests**

Run: `C:\Users\GYY\anaconda3\python.exe -m pytest tests/test_t2_page.py -q`

Expected: PASS.

### Task 5: 纠正案例持久化与内部 Agent 后台学习

**Files:**
- Modify: `app/models.py`
- Modify: `app/history.py`
- Create: `app/services/correction_agent.py`
- Modify: `app/services/order.py`
- Modify: `app/config.py`
- Modify: `sql/schema.mysql.sql`
- Test: `tests/test_correction_agent.py`

**Interfaces:**
- Produces `CorrectionCase` with state `pending|processed|failed|conflict`.
- Produces `process_case(db, case_id, client) -> CorrectionCase`.
- Consumes internal model endpoint config; model response schema is `error_type`, `normalized_original_value`, `correct_value`, `rule_type`, `summary`.

- [x] **Step 1: Write the failing successful-learning test**

```python
def test_valid_agent_result_creates_new_long_term_rule_after_successful_writeback(client, monkeypatch):
    monkeypatch.setattr("app.services.correction_agent.ask_internal_model", lambda payload: {
        "error_type": "tax_mapping", "normalized_original_value": "13",
        "correct_value": "CN_VAT13", "rule_type": "permanent", "summary": "税务映射",
    })
    saved = save_with_error_description(client, "Tax Structure", "CN_VAT13", "13% 应为 VAT13")
    assert saved.status_code == 200
    case = latest_case()
    process_case(session, case.id)
    assert find_rule("Acme", "Tax Structure", "13%").new_value == "CN_VAT13"
```

- [x] **Step 2: Run test to verify it fails**

Run: `C:\Users\GYY\anaconda3\python.exe -m pytest tests/test_correction_agent.py::test_valid_agent_result_creates_new_long_term_rule_after_successful_writeback -q`

Expected: FAIL because correction cases and the processor do not exist.

- [x] **Step 3: Implement case model and minimal processor**

Add `correction_case` table/model with raw correction context, agent summary, state, error, timestamps and resulting rule ID. After successful ePortal update, create one pending case per non-empty error description and schedule a FastAPI background task. Post only needed field-level context to the configured internal model endpoint. Parse strict JSON, require `rule_type == "permanent"`, require exact equality of model `correct_value` and user final value, and create a new permanent rule only when no enabled permanent rule has the same normalized key. Write `correction_case_created`, `agent_rule_created`, `agent_failed`, and `agent_conflict` history entries.

- [x] **Step 4: Write the failing non-overwrite test**

```python
def test_agent_conflict_never_overwrites_existing_long_term_rule(client, monkeypatch):
    create_rule(client, "Acme", "Tax Structure", "13%", "OLD")
    case = create_pending_case(final_value="NEW")
    monkeypatch.setattr("app.services.correction_agent.ask_internal_model", lambda _: valid_result("NEW"))
    process_case(session, case.id)
    assert find_rule("Acme", "Tax Structure", "13%").new_value == "OLD"
    assert reload_case(case.id).status == "conflict"
```

- [x] **Step 5: Run Agent tests**

Run: `C:\Users\GYY\anaconda3\python.exe -m pytest tests/test_correction_agent.py -q`

Expected: PASS.

### Task 6: 迁移 mock 演示、文档与全量验证

**Files:**
- Modify: `app/api/mock_eportal.py`
- Modify: `app/static/eportal.html`
- Modify: `README.md`
- Modify: `tests/conftest.py`
- Modify: `tests/test_acceptance.py`
- Test: `tests/`

**Interfaces:**
- Mock ePortal supports ticket issue, schema-rich order details and version-aware update behavior.
- README documents real ePortal internal API contract and internal-model config variables.

- [x] **Step 1: Write the failing end-to-end test**

```python
def test_eportal_ticket_to_t2_save_preserves_operator_and_learns_from_error_description(client):
    order = create_mock_eportal_order(client)
    ticket = issue_mock_ticket(order["order_id"], "sales1", "张销售", order["version"])
    begin_ticket_session(client, ticket)
    saved = client.post("/api/eportal/orders/current/save", json=valid_save_payload_with_note())
    assert saved.status_code == 200
    assert history_for_order(order["order_id"])[0]["operator_name"] == "张销售"
    assert latest_case().status in {"pending", "processed"}
```

- [x] **Step 2: Run test to verify it fails**

Run: `C:\Users\GYY\anaconda3\python.exe -m pytest tests/test_acceptance.py::test_eportal_ticket_to_t2_save_preserves_operator_and_learns_from_error_description -q`

Expected: FAIL until mock ePortal and integration wiring are complete.

- [x] **Step 3: Implement mock compatibility and update documentation**

Make the demonstration ePortal issue tickets instead of placing tokens in its T2 URL. Implement mock schema/order detail/update endpoints with calculated fields marked readonly and deterministic version increments. Preserve old intake/memory scenarios through the mock adapter. Document required ePortal endpoints, ticket semantics, service credential environment variables and internal-model request/response contract.

- [x] **Step 4: Run full verification**

Run: `C:\Users\GYY\anaconda3\python.exe -m pytest tests -q`

Expected: PASS with all existing and new tests.

- [x] **Step 5: Inspect generated SQLite schema**

Run: `C:\Users\GYY\anaconda3\python.exe -c "from app.main import init_db; init_db(); print('schema initialized')"`

Expected: `schema initialized`; `correction_case` exists without startup errors.
