# ePortal Ticket Order Loading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load an ePortal order by its actual `id` through `ae.php/api/ticket`, and display its values plus supported ePortal dropdown choices in T2.

**Architecture:** A same-origin T API will validate the ePortal `id`, fetch and normalize the ePortal ticket JSON, and return a T2-oriented presentation model. T2 will use that API when `id` is present in its URL and render the fetched values; it will retain local-order and `intellisight_id` compatibility. Fixed option catalogs, extracted from the supplied ePortal source, will select the correct editor control without browser-to-ePortal requests.

**Tech Stack:** FastAPI, httpx, pytest, vanilla JavaScript.

**Spec:** `docs/superpowers/specs/2026-09-11-eportal-ticket-order-load-design.md`

## Global Constraints

- Use ePortal's actual identifier name `id`; do not invent an `order_id` query parameter for this integration.
- Fetch ePortal only from the T backend; do not expose an ePortal token or call ePortal from T2 JavaScript.
- This phase is read/display only for remote ePortal orders; do not make remote save claims without an ePortal write contract.
- Preserve the existing local-order and `intellisight_id` T2 flows.

---

### Task 1: Configure and normalize ePortal ticket orders

**Files:**
- Modify: `app/config.py`
- Modify: `app/adapters/eportal.py`
- Create: `tests/test_eportal_ticket_order.py`

**Interfaces:**
- Consumes: `settings.eportal_base_url` and ePortal JSON from `/ae.php/api/ticket?id={id}`.
- Produces: `HttpEPortalAdapter.get_ticket_order(eportal_id: int) -> dict` returning a normalized model with `id`, `fields`, `items`, `attachments`, and `item_schema`.

- [ ] **Step 1: Write failing tests for a successful ticket-order read and invalid IDs**

```python
def test_http_adapter_gets_ticket_order_and_normalizes_values(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: httpx.Response(200, json={
        "id": 679, "customer_name": "TradeDesk", "tax_structure": "10",
        "products": [{"product_id": "P-1", "biz_category": "BAU_BIZ"}],
        "att1": {"id": 1, "name": "contract.pdf"},
    }))
    result = HttpEPortalAdapter().get_ticket_order(679)
    assert result["id"] == 679
    assert next(field for field in result["fields"] if field["field_name"] == "tax_structure")["value"] == "10"
    assert result["items"][0]["product_id"] == "P-1"
    assert result["attachments"][0]["name"] == "contract.pdf"

def test_http_adapter_rejects_a_mismatched_ticket_order_id(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: httpx.Response(200, json={"id": 680}))
    with pytest.raises(EPortalError, match="ID 不一致"):
        HttpEPortalAdapter().get_ticket_order(679)
```

- [ ] **Step 2: Run the new test file and verify it fails because `get_ticket_order` does not exist**

Run: `pytest tests/test_eportal_ticket_order.py -v`

Expected: failure naming the missing `get_ticket_order` method.

- [ ] **Step 3: Add the ticket path setting and minimal adapter implementation**

```python
# config.py default eportal settings
"ticket_order_path": "/ae.php/api/ticket?id={id}",

# eportal.py
def get_ticket_order(self, eportal_id: int) -> dict:
    if eportal_id <= 0:
        raise EPortalError("ePortal id 必须为正整数")
    response = self._request(
        "get",
        settings.eportal_base_url + settings.eportal_ticket_order_path.format(id=eportal_id),
        timeout=15,
    )
    payload = self._check(response)
    if int(payload.get("id") or 0) != eportal_id:
        raise EPortalError("ePortal 返回订单 ID 不一致")
    return normalize_ticket_order(payload)
```

Implement `normalize_ticket_order` in `app/adapters/eportal.py`: exclude ePortal metadata from `fields`, transform `products` to `items`, transform non-null `att1` through `att4` to attachment entries, and attach static field/product schemas with the ePortal-derived option catalogs.

- [ ] **Step 4: Run the new adapter tests and verify they pass**

Run: `pytest tests/test_eportal_ticket_order.py -v`

Expected: all tests pass.

### Task 2: Expose a same-origin read-only T API

**Files:**
- Modify: `app/api/eportal_session.py`
- Modify: `tests/test_eportal_ticket_order.py`

**Interfaces:**
- Consumes: `HttpEPortalAdapter.get_ticket_order(eportal_id)`.
- Produces: `GET /api/eportal/ticket-orders/{id}` returning the normalized model.

- [ ] **Step 1: Write a failing route test**

```python
def test_ticket_order_route_returns_adapter_model(monkeypatch, client):
    expected = {"id": 679, "fields": [], "items": [], "attachments": [], "item_schema": {}}
    monkeypatch.setattr("app.api.eportal_session.get_adapter", lambda: StubAdapter(expected))
    response = client.get("/api/eportal/ticket-orders/679")
    assert response.status_code == 200
    assert response.json() == expected
```

- [ ] **Step 2: Run the route test and verify it fails with 404**

Run: `pytest tests/test_eportal_ticket_order.py::test_ticket_order_route_returns_adapter_model -v`

Expected: assertion failure because the route is not registered.

- [ ] **Step 3: Add the route without a user-session dependency**

```python
@router.get("/ticket-orders/{eportal_id}")
def ticket_order(eportal_id: int = Path(..., ge=1)):
    try:
        return get_adapter().get_ticket_order(eportal_id)
    except EPortalError as exc:
        raise HTTPException(status_code=502, detail="无法读取 ePortal 订单") from exc
```

The route must keep the upstream body out of the returned error and must not accept an arbitrary upstream URL.

- [ ] **Step 4: Run the route test and verify it passes**

Run: `pytest tests/test_eportal_ticket_order.py::test_ticket_order_route_returns_adapter_model -v`

Expected: pass with a 200 response.

### Task 3: Load remote ePortal `id` orders in T2

**Files:**
- Modify: `app/static/t2.html`
- Modify: `tests/test_t2_page.py`

**Interfaces:**
- Consumes: `GET /api/eportal/ticket-orders/{id}`.
- Produces: T2 remote read mode for `/t2?id=679`.

- [ ] **Step 1: Write a failing T2 page contract test**

```python
def test_t2_page_uses_eportal_id_to_load_ticket_order(client):
    page = client.get("/t2").text
    assert 'params.get("id")' in page
    assert '/api/eportal/ticket-orders/${eportalId}' in page
    assert "远端订单当前为只读展示" in page
```

- [ ] **Step 2: Run the contract test and verify it fails**

Run: `pytest tests/test_t2_page.py::test_t2_page_uses_eportal_id_to_load_ticket_order -v`

Expected: failure because the page currently has no ePortal `id` loading path.

- [ ] **Step 3: Add remote load mode while preserving legacy paths**

```javascript
const eportalId = params.get("id");
async function load() {
  if (eportalId) {
    order = await API.fetch(`/api/eportal/ticket-orders/${encodeURIComponent(eportalId)}`);
    remoteReadOnly = true;
  } else {
    order = await API.fetch(`/api/orders/${orderId}`);
  }
  render();
}
```

Display the returned `id` as the ePortal context. Disable the submit action and show `远端订单当前为只读展示，等待 ePortal 提供保存接口。` in remote mode.

- [ ] **Step 4: Run the T2 contract test and the existing T2 page test file**

Run: `pytest tests/test_t2_page.py -v`

Expected: all tests pass after updating legacy assertions only where the new `id` path is additive.

### Task 4: Render supported ePortal dropdowns

**Files:**
- Modify: `app/static/t2.html`
- Modify: `tests/test_t2_page.py`

**Interfaces:**
- Consumes: normalized field and product schemas with `type`, `options`, and `editable` properties.
- Produces: select-based edit controls for supported header and product fields.

- [ ] **Step 1: Write failing page-contract tests for select rendering**

```python
def test_t2_page_renders_schema_select_options(client):
    page = client.get("/t2").text
    assert 'f.type==="select"' in page
    assert 'f.options.map' in page
    assert 'productColumn.options' in page
```

- [ ] **Step 2: Run the contract test and verify it fails**

Run: `pytest tests/test_t2_page.py::test_t2_page_renders_schema_select_options -v`

Expected: failure because field and product editors render text inputs only.

- [ ] **Step 3: Render selects from schema options**

Update the field dialog to create a `<select>` when `f.type === "select"`, retaining textarea/date behavior for other fields. Update the product cell editor similarly using the current column schema. On a `biz_category` change, calculate Node ID options from the schema mapping before rendering the Node ID editor.

- [ ] **Step 4: Run T2 tests and verify the dropdown contract passes**

Run: `pytest tests/test_t2_page.py -v`

Expected: all tests pass.

### Task 5: Verify the integration without live ePortal writes

**Files:**
- Modify: `README.md`
- Test: `tests/test_eportal_ticket_order.py`

- [ ] **Step 1: Add a failing test for a non-JSON upstream response**

```python
def test_ticket_order_route_hides_non_json_upstream_response(monkeypatch, client):
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: httpx.Response(502, text="upstream internals"))
    response = client.get("/api/eportal/ticket-orders/679")
    assert response.status_code == 502
    assert "upstream internals" not in response.text
```

- [ ] **Step 2: Run the test and verify it fails for the expected unhandled response path**

Run: `pytest tests/test_eportal_ticket_order.py::test_ticket_order_route_hides_non_json_upstream_response -v`

Expected: failure until the adapter and route translate the upstream failure.

- [ ] **Step 3: Implement the smallest error translation needed for the test**

Catch the adapter's upstream exception at the route boundary and return exactly the controlled Chinese error defined in Task 2.

- [ ] **Step 4: Document the read-only ePortal `id` URL and save limitation**

Add README text stating that `/t2?id=<ePortal id>` reads from `GET /ae.php/api/ticket?id=<id>`, and that remote save stays disabled until ePortal supplies a write contract.

- [ ] **Step 5: Run the focused and full test suites**

Run: `pytest tests/test_eportal_ticket_order.py tests/test_t2_page.py -v`

Run: `pytest -q`

Expected: both commands pass; no live ePortal write is performed.
