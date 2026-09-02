# Task 1 review — ePortal gateway contract

## Verdict

- **Spec:** ❌ **Not approved**
- **Quality:** **Rejected**

The core adapter and configuration surface is largely aligned with the Task 1 contract: `EditContext` is frozen; the three required adapter methods exist; the documented exchange/read/update paths and a server-side bearer token setting are present; and HTTP 409 is translated to `EPortalConflictError`. Existing acceptance coverage remains green (17 passed).

However, the mock ticket implementation does not guarantee the required single-use behavior under concurrent exchanges. The required new test is also red and never directly exercises the Task 1 gateway. The latter is chiefly a plan/task-boundary contradiction (detailed below), but leaving the Task 1 test suite red means Task 1 cannot be accepted as a quality-complete deliverable.

## Findings

### P1 — mock ticket exchange is not atomic, so a ticket can be consumed more than once

**File:** `app/adapters/eportal.py`, `MockEPortalAdapter.exchange_ticket`

The method reads `_MOCK_TICKETS[ticket]`, checks `entry.consumed`, and only then assigns `entry.consumed = True`, with no lock or atomic compare-and-set. Two server threads can both observe `consumed == False` before either writes it, then both receive the same edit context. That violates the design/spec requirement that an ePortal ticket be usable exactly once. The report's statement that the mock “atomically marks” the ticket consumed is not supported by this implementation.

**Required remediation:** protect lookup/validation/consume as one critical section (for example, with a module-level lock), then return the captured immutable context after the lock is released. Add a concurrency-oriented test or otherwise directly test the one-time exchange contract.

### P2 — the only new gateway test is red and tests a Task 2 route rather than Task 1 behavior

**File:** `tests/test_eportal_gateway.py`

The test invokes `POST /api/eportal/session`, which does not exist yet, so the current focused suite fails with `404 Not Found`. It consequently does not call `MockEPortalAdapter.exchange_ticket`, nor does it verify expiry, immutable context, HTTP request formation, or 409 mapping. Task 1 therefore has no passing automated coverage for the production behavior it introduces.

**Required remediation:** move this endpoint test to Task 2 (or mark it as a Task 2 expectation) and add direct Task 1 adapter tests: successful exchange/context, second exchange rejected, expired ticket rejected, the HTTP paths/headers/payloads, and 409 translation.

### P3 — expired mock tickets are retained indefinitely

**File:** `app/adapters/eportal.py`

Expired and consumed entries remain in the module-global `_MOCK_TICKETS` dictionary. Long-running local demonstrations or tests that issue many tickets will monotonically retain them. This is not an immediate functional or security break in the mock implementation, but bounded cleanup would make the test/demo facility safer and more maintainable.

## Plan inconsistency (not a Task 1 implementation defect)

Task 1 Step 1/4 prescribes `tests/test_eportal_gateway.py::test_mock_ticket_is_single_use_and_returns_operator_context` and says it should pass after Task 1. That test requires `/api/eportal/session`. The Task 1 brief simultaneously says “Do not implement web routes,” and the plan explicitly assigns production of that endpoint to Task 2. A Task 1-only implementation cannot make this endpoint test pass without violating its scope. The observed `404` is therefore the expected result of respecting the task boundary, not evidence that the gateway interface itself is missing.

The plan should assign the endpoint test to Task 2 and give Task 1 direct adapter-level tests instead. This does not remove P1 (atomic single use) or P2's test-quality gap.

## Evidence reviewed

- Requirements: `task-1-brief.md`; implementation report: `task-1-report.md`; plan: `docs/superpowers/plans/2026-09-01-eportal-integrated-t-system.md`; design spec: `docs/superpowers/specs/2026-09-01-eportal-integrated-t-system-design.md`.
- Current files: `app/adapters/eportal.py`, `app/config.py`, `config.ini`, and `tests/test_eportal_gateway.py`.
- `C:\\Users\\GYY\\anaconda3\\python.exe -m pytest tests\\test_eportal_gateway.py -q` → **1 failed** (`404` at `/api/eportal/session`), plus environment/cache warnings.
- `C:\\Users\\GYY\\anaconda3\\python.exe -m pytest tests\\test_acceptance.py -q` → **17 passed**, 2 warnings.
