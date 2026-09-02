# SDD ledger — plan: docs/superpowers/plans/2026-09-01-eportal-integrated-t-system.md

Workspace ruling: `D:\GK\T\data\t-system` is not a Git repository, so no isolated Git worktree, commits, Git diff packages, or workspace deletion are possible. Work proceeds in place; reports and review artifacts remain in this workspace. Cost if wrong: changes cannot be reverted through Git history.

## Preflight interface scan

| Tasks | Shared file/interface | Finding | Ruling |
| --- | --- | --- | --- |
| 1 → 2 | `EPortalAdapter.exchange_ticket`, ticket mock behavior | Task 1 produces the ticket contract that Task 2 consumes. | Consistent; Task 2 must use only Task 1 adapter methods. |
| 1 → 3 | `EPortalAdapter.get_order_for_edit` / `update_order_for_edit` | Task 1 names the adapter methods; Task 3 supplies request validation and uses them. | Consistent; version conflicts are translated by the adapter. |
| 2 → 3 | `EditSession` dependency | Task 2 creates the server-side session and Task 3 uses it for operator identity. | Consistent; no legacy token dependency may be retained in the new ePortal edit endpoints. |
| 3 → 4 | ePortal current-order and save endpoints | Task 4 renders and submits the Task 3 payload. | Consistent; UI supports schema fields first; complex mock fields may remain read-only until mock schema allows edits. |
| 3 → 5 | successful versioned save | Task 5 must run only after Task 3’s ePortal save succeeds. | Consistent; failed/409 save cannot create correction cases. |
| 4 → 5 | `error_descriptions` payload | Task 4 creates the field inputs; Task 5 persists/processes them. | Consistent; only non-empty descriptions are sent. |
| 1–5 → 6 | mock adapter, docs, existing acceptance tests | Task 6 integrates all preceding work. | Consistent; preserve legacy mock intake tests while adding ticket flow. |

Ruling: ePortal’s real internal API is unavailable in this workspace, so Task 1 implements the documented HTTP contract and a deterministic mock equivalent. Cost if wrong: endpoint field names may need a thin adapter adjustment when ePortal publishes its final contract.

