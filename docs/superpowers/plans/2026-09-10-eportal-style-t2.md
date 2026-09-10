# ePortal-Style T2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace T2's compact editor with a COSTING SHEET - T page that follows the supplied ePortal form layout and submits through T's existing writeback flow.

**Architecture:** Keep T as the data and save boundary. Recreate the ePortal page's presentation locally using its table layout and visual conventions, while field, product-row, and attachment edits occur in T-owned dialogs. The only bottom action is `同步并提交`, which invokes the existing save endpoint and displays only a success/failure dialog.

**Tech Stack:** FastAPI, SQLAlchemy, vanilla HTML/CSS/JavaScript, pytest.

**Spec:** Conversation-approved design, based on `新建 文本文档 (2).txt`.

## Global Constraints

- The heading must be exactly `COSTING SHEET - T`.
- Do not render the original ePortal actions: 新建, 另存, 取消, 保存, 删除, 导出EXCEL, 历史, 转到T系统.
- Render only `同步并提交` in the bottom action area.
- Initial page load must not display historical sync state.
- Submit outcome must display only `提交成功` or `提交失败`.
- Reuse no ePortal API calls, login controls, or ePortal user data from the supplied source.

---

### Task 1: Expose T order detail required by the replicated form

**Files:**
- Modify: `app/services/order.py`
- Test: `tests/test_order_detail.py`

**Interfaces:**
- Produces `order_detail()` fields `items` and `attachments` from `Order.payload`.

- [ ] Write a failing API/detail test asserting an intake order returns `items` and `attachments` as arrays.
- [ ] Run `pytest tests/test_order_detail.py -q` and confirm failure because the keys are absent.
- [ ] Add `items` and `attachments` to `order_detail()` with empty-array defaults.
- [ ] Re-run the test and confirm it passes.

### Task 2: Build COSTING SHEET - T presentation and dialogs

**Files:**
- Modify: `app/static/t2.html`
- Test: `tests/test_t2_page.py`

**Interfaces:**
- Consumes `GET /api/orders/{order_id}` detail payload.
- Produces `collectChanges()` for scalar dialog edits and `renderProducts()`/`renderAttachments()` for display.

- [ ] Write failing page-contract tests for the exact title, ePortal-style table classes, dialog editor, product/attachment sections, absent legacy action labels, and `同步并提交`.
- [ ] Run `pytest tests/test_t2_page.py -q` and confirm failure.
- [ ] Replace the compact grid with the supplied source's black-table, dotted-input, yellow-required-field visual language; use a local modal editor for scalar fields and product rows.
- [ ] Render attachment slots and product rows from the T detail payload; do not invoke the ePortal source's AJAX endpoints.
- [ ] Re-run page-contract tests and confirm they pass.

### Task 3: Submit outcome behavior

**Files:**
- Modify: `app/static/t2.html`
- Test: `tests/test_t2_page.py`

**Interfaces:**
- Consumes `POST /api/orders/{order_id}/save` result `{status}`.
- Produces a native/local result dialog containing exactly `提交成功` or `提交失败`.

- [ ] Write a failing page-contract test for `同步并提交` and two result messages.
- [ ] Run the focused test and confirm failure.
- [ ] Wire the button to submit collected scalar changes, then show success only for `status === 'synced'`; show failure for all other results and request errors.
- [ ] Re-run the focused test and confirm it passes.

### Task 4: Regression verification

**Files:**
- Test: `tests/test_acceptance.py`, `tests/test_t2_page.py`

- [ ] Run `pytest -q --basetemp .pytest-tmp-t2`.
- [ ] Confirm all tests pass and inspect the diff to ensure no unrelated changes were introduced.
