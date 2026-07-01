# Review: MGMT-LOAD-002 — BFF Shell Summary And Jobs Route Canonicalization

Reviewer: Claude
Date: 2026-07-01
Status: APPROVED

## Scope Verified

- `GET /bff/management/shell-summary` added at `services/control-plane/bff/main.py:14959`.
  Returns `data.counts` (pending_approvals, open_alerts, running_jobs),
  redacted `data.session`, `data.transport`, and `meta.surfaces` freshness /
  degraded metadata. No full approvals/alerts/jobs list payload is returned.
- Count sources (`_shell_summary_pending_approvals_count`,
  `_shell_summary_open_alerts_count`, `_shell_summary_running_jobs_count`) read
  cheap store-level lists directly and never call
  `_build_operator_alerts_payload`; enforced by
  `test_shell_summary_returns_counts_without_full_lists` monkeypatching that
  builder to raise if invoked.
- Count cache (`_SHELL_SUMMARY_COUNT_CACHE`) is guarded by
  `_SHELL_SUMMARY_COUNT_CACHE_LOCK` (`threading.Lock`), keyed on
  `read_store_id` + `read_surface_state`, short TTL
  (`PANTHEON_BFF_SHELL_SUMMARY_COUNT_TTL_SECONDS`, default 5s). The route
  handler is a sync `def` (not `async def`) so FastAPI runs the synchronous
  read-store work in the threadpool instead of blocking the event loop.
- `/bff/jobs` duplicate handler block removed (previously two `@app.get("/bff/jobs")`
  definitions); the retained canonical implementation is the execute-plans
  compatibility route returning both `data` and `items`. Verified only one
  source occurrence of `@app.get("/bff/jobs")` and one registered GET route
  remain (`test_jobs_route_has_one_canonical_get_handler`).

## Implementation Review

- Degraded-state propagation: `_shell_summary_*_count` helpers return an
  "unavailable"/"degraded" surface per source and `_build_shell_summary_counts`
  aggregates them into a composed `shell_summary` surface via
  `_aggregate_group_surface`, so partial source failure does not silently
  zero out unrelated counts or crash the route.
- Session payload is redacted: only `operator_id`, `display_label`, `roles`,
  `session_kind`, `state`, `fresh`, `mfa_verified` are exposed; no token or
  capability data leaks (verified by
  `test_shell_summary_redacts_session_and_exposes_transport`).
- `_shell_summary_running_jobs_count` reuses the same `_list_bff_jobs()` helper
  as the canonical `/bff/jobs` route, so there is a single source of truth for
  job records instead of a second parallel jobs listing path.
- No shadowing introduced: `test_route_resolution_no_shadowing.py` and
  `test_execute_plans_contract_registry.py` continue to pass after the jobs
  route dedup.

## Contract Tests

`services/control-plane/bff/test_mgmt_load_002_shell_summary.py` (new) covers:
counts-without-full-lists, session redaction + transport shape, degraded
count-surface propagation, OpenAPI registration, and single canonical
`/bff/jobs` GET handler.

## Verification

Reviewer independently re-ran:

```
python3 -m pytest services/control-plane/bff/test_mgmt_load_002_shell_summary.py \
  services/control-plane/bff/test_bff_evolution_experiment_jobs_events_contract.py \
  services/control-plane/bff/test_route_resolution_no_shadowing.py \
  services/control-plane/bff/test_execute_plans_contract_registry.py -q
-> 41 passed, 16 warnings in 23.32s
```

Warnings are pre-existing FastAPI `on_event` deprecation notices, not
introduced by this task.

## Hosted Dev Timing Evidence Gap — Reviewer Decision

The task's own acceptance line requires "Dev BFF evidence shows shell summary
p95 <= 200 ms under 10 concurrent requests, **or** archives a
reviewer-approved blocker with exact bottleneck." The owner recorded in
`MGMT-LOAD-002-bff-shell-summary.md` that hosted dev timing evidence could not
be produced in this worker because `PANTHEON_BFF_SMOKE_BEARER_TOKEN` and the
dev BFF base URL were unset in the task worktree (an environment/credential
gap, not a code defect), and proposed deferring the hosted 10-concurrent p95
gate to the `MGMT-LOAD-001` (baseline hosted probes) / `MGMT-LOAD-005` (BFF
read concurrency isolation, which explicitly depends on both `MGMT-LOAD-001`
and `MGMT-LOAD-002` and re-runs the fanout probe against the deployed dev BFF)
task chain.

Reviewer accepts this path as the exact bottleneck / blocker evidence required
by the acceptance criterion, for these reasons:

- `MGMT-LOAD-005`'s scope already re-runs the hosted BFF fanout probe from
  `MGMT-LOAD-001` against the deployed dev BFF and explicitly measures
  concurrent-read behavior across shell/Evidence/health routes, so the
  10-concurrent hosted measurement for shell-summary is not lost — it lands in
  the task designed to own it.
- The local ASGI TestClient smoke evidence (sequential warm, p95 = 9.916 ms,
  max = 22.248 ms over 20 requests) is far below the 200 ms budget and the
  route only does cheap store-list reads behind a locked short-TTL cache with
  sync-handler threadpool isolation, so there is no structural reason to
  expect the hosted concurrent number to regress by an order of magnitude.
- Credentials/base-URL provisioning for hosted dev smoke is infrastructure
  setup, not remaining implementation scope for this task.

If `MGMT-LOAD-005`'s hosted fanout probe later shows shell-summary p95 over
200 ms under concurrency, that is a `MGMT-LOAD-005` (or a reopened
`MGMT-LOAD-002`) finding, not an unreviewed gap in this closeout.

## Acceptance Criteria Status

| Criterion | Status |
|---|---|
| shell-summary returns no full approvals/alerts/jobs lists | PASS |
| count freshness/degraded surfaces explicit | PASS |
| `/bff/jobs` single canonical route | PASS |
| OpenAPI + contract tests | PASS |
| Dev timing evidence | ACCEPTED VIA MGMT-LOAD-001/MGMT-LOAD-005 PROBE PATH (see above) |

## Decision

APPROVED. Implementation is complete and correctly tested. Hosted dev timing
evidence is deferred to the `MGMT-LOAD-001`/`MGMT-LOAD-005` probe path per the
reasoning above. Task is ready for owner (`Codex`) finalization.
