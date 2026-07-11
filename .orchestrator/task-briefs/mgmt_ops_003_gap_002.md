# Task Brief: MGMT-OPS-003-GAP-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Runtime binding and telemetry truth
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex2
- Next: REQUEST_CHANGES: reconciliation evidence does not account for every hosted gap. Hosted capture reports 19 runtimes, 14 telemetry runtimes, and 19 unresolved holdings, but reconciliation-snapshot/report contains only 8 runtime bindings and 8 records; README claim that the report preserves all 19 holdings is unsupported. Capture all 19 driving runtime rows (or provide an explicit, auditable 19-holding-to-runtime mapping), reconcile every one, preserve unresolved incident/quarantine reasons, and rerun twice to show one append-only audit entry with a replayed=true report. Reviewer verification: 53 passed; deployed e3d3d88487 is ancestor of dev.

## Summary
修復或隔離 dev runtime 的 persona、broker、ledger、capital scope 與 telemetry 缺口，不得靠隱藏資料改善指標。
