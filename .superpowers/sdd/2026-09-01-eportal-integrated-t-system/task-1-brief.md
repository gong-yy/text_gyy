# Task 1 brief — ePortal gateway contract

Read `docs/superpowers/plans/2026-09-01-eportal-integrated-t-system.md`, Task 1, before editing. It is the requirements authority for this task.

Implement only Task 1. Modify `app/config.py`, `config.ini`, and `app/adapters/eportal.py`; add `tests/test_eportal_gateway.py`.

Required interfaces:

- `EPortalAdapter.exchange_ticket(ticket: str) -> EditContext`
- `EPortalAdapter.get_order_for_edit(order_id: str, operator_id: str) -> dict`
- `EPortalAdapter.update_order_for_edit(order_id: str, operator: dict, expected_version: int, changes: dict) -> dict`

Add server-side ePortal service-token configuration. Add an immutable `EditContext(order_id, version, user_id, user_name)`. The HTTP adapter must use the documented internal endpoint contract and map HTTP 409 to an `EPortalConflictError`. Implement deterministic mock ticket issue/exchange support with expiry and single-use state, sufficient for the planned test. Do not implement web routes, server sessions, T2 UI, Agent work, or unrelated refactors.

Follow TDD: first add `test_mock_ticket_is_single_use_and_returns_operator_context`, run it and verify the intended failure, then implement the smallest production change and run the focused test plus relevant existing tests. There is no Git repository: do not commit, reset, clean, or create a worktree. Do not spawn subagents.

Write your detailed report to `.superpowers/sdd/2026-09-01-eportal-integrated-t-system/task-1-report.md`: modified files; exact test commands/output; test-first failure evidence; design decisions; concerns. Return only status, one-line test summary, and concerns.
