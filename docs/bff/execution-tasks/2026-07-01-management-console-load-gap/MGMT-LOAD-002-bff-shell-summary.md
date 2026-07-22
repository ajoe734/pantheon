# MGMT-LOAD-002 - BFF Shell Summary And Jobs Route Canonicalization

Owner: Claude2
Reviewer: Codex
Parent: `MGMT-GAP-010`
Depends on: `MGMT-GAP-003`

## Problem

The management shell currently fetches full approvals, alerts, jobs, current
user, and health state during first mount. Badge counts should not require full
list payloads or expensive alert aggregation. The BFF source also has duplicate
`/bff/jobs` route definitions, which makes startup behavior harder to reason
about.

## Scope

- Add `GET /bff/management/shell-summary` with session identity, transport
  health, and cheap counts for pending approvals, open alerts, and running jobs.
- Make count freshness and degraded count sources explicit in `meta.surfaces`.
- Avoid calling the full alert/list builders solely to compute badge counts.
- Consolidate duplicate `/bff/jobs` route definitions into one canonical route.
- Add OpenAPI/schema and contract tests for summary success, degraded counts,
  redaction, and jobs route behavior.

## Acceptance

- `/bff/management/shell-summary` returns no full approvals, alerts, or jobs
  list payloads.
- Summary count tests cover success and degraded source states.
- `/bff/jobs` has one canonical implementation and one contract test source of
  truth.
- Dev BFF evidence shows shell summary p95 <= 200 ms under 10 concurrent
  requests, or archives a reviewer-approved blocker with exact bottleneck.

## 2026-07-01 Implementation Evidence

Task branch: `task/MGMT-LOAD-002`

Implemented in BFF:

- Added `GET /bff/management/shell-summary` with `data.counts`,
  redacted `data.session`, `data.transport`, and explicit
  `meta.surfaces` freshness/degraded state.
- Count sources avoid `_build_operator_alerts_payload`; alert badge count uses
  cheap incident, governance review, approval, and kill-switch count checks.
- Shell summary count reads use a short TTL cache guarded for concurrent first
  load, and the route is a sync FastAPI handler so synchronous read-store work
  runs outside the event loop.
- Removed the earlier duplicate `/bff/jobs` handler block. The remaining
  canonical jobs route is the execute-plans compatibility implementation that
  returns both `data` and `items`.

Contract coverage:

- `services/control-plane/bff/test_mgmt_load_002_shell_summary.py`
  verifies summary shape, no full approvals/alerts/jobs list payloads,
  redacted session data, degraded count surfaces, OpenAPI registration, and a
  single source-level `@app.get("/bff/jobs")`.
- Existing jobs contract coverage remains in
  `services/control-plane/bff/test_bff_evolution_experiment_jobs_events_contract.py`.

Validation run:

```text
python3 -m pytest services/control-plane/bff/test_mgmt_load_002_shell_summary.py services/control-plane/bff/test_bff_evolution_experiment_jobs_events_contract.py services/control-plane/bff/test_route_resolution_no_shadowing.py services/control-plane/bff/test_execute_plans_contract_registry.py -q
41 passed, 16 warnings in 24.01s

git diff --check
pass
```

Local smoke, not hosted evidence:

```json
{
  "route": "GET /bff/management/shell-summary",
  "environment": "local TestClient ASGI sequential warm",
  "count": 20,
  "all_status_200": true,
  "p95_ms": 9.916,
  "max_ms": 22.248,
  "surface_statuses": ["unavailable"]
}
```

Hosted dev timing evidence was not produced in this worker because
`PANTHEON_BFF_SMOKE_BEARER_TOKEN` and dev BFF base URL environment variables
were unset in the task worktree. The hosted 10-concurrent p95 gate remains a
review/closeout requirement through the MGMT-LOAD-001/MGMT-LOAD-005 probe path
after this branch is deployed to the dev BFF.
