# BFF-CONSOL-026 Sidecar: BFF and Frontend Handoff Packet

Task ID: BFF-CONSOL-026-SIDECAR-BFF-HANDOFF
Parent Task: BFF-CONSOL-026 - CI route diff fail-hard mode
Helper Kind: bff_handoff_packet
Prepared by: Codex2
Reviewer: Codex
Date: 2026-05-13
Mutates canonical truth: false

## Purpose

This packet gives the BFF consolidation owner and reviewer a support-only
handoff for the BFF-CONSOL-026 fail-hard route diff gate. It summarizes the
current BFF query gap, the route-change operator journey, and frontend manifest
handoff expectations.

This artifact does not change L1 canonical truth, core contracts, runtime code,
registry code, governance implementation, or the route manifest snapshots.

## Parent State Observed

| Area | Observation |
|---|---|
| Parent lifecycle | `BFF-CONSOL-026` is archived as `done`; Codex approved the fail-hard route diff gate. |
| Approved behavior | `scripts/bff_route_diff.py` defaults to `fail-hard`; CI runs `python3 scripts/bff_route_diff.py --check-baseline`. |
| Baseline lock | `docs/bff/contract_snapshots/route-diff-baseline.json` locks the current fail-hard failure surface. |
| Cutover note | `docs/bff/contract_snapshots/route-diff-fail-hard-cutover.md` states that backend and frontend route manifest changes must land together or mark the unmatched route non-blocking. |
| Parent evidence | `support/evidence/BFF-CONSOL-026-closeout.md` records final verification and the parent review file. |

## BFF Query Gap Matrix

The current checked-in diff baseline compares:

- Backend manifest: `services/control-plane/bff/contract_snapshots/backend_routes_manifest.json`
- Frontend manifest: `services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json`
- Baseline lock: `docs/bff/contract_snapshots/route-diff-baseline.json`

Summary from the fail-hard route diff:

| Metric | Value |
|---|---:|
| Backend routes in snapshot | 371 |
| Frontend routes in snapshot | 178 |
| Total fail-hard failures | 209 |
| Backend active routes missing frontend manifest coverage | 209 |
| Frontend active routes missing backend coverage | 0 |
| Naming/family mismatches | 0 |
| Duplicate backend keys | 0 |
| Duplicate frontend keys | 0 |
| Warnings | 0 |

The 209 failures are all grandfathered backend-only active routes already locked
by the baseline. They are not a new BFF-CONSOL-026 regression by themselves.
They are the known consolidation backlog that prevents silent growth of
unmatched backend or frontend surfaces after the fail-hard cutover.

### Gap Counts by Route Family

| Family | Count | Example keys |
|---|---:|---|
| `api-v1` | 95 | `GET /api/v1/approval-decisions`, `GET /api/v1/artifacts`, `GET /api/v1/bindings` |
| `operator` | 35 | `GET /api/v1/operator/alerts`, `GET /api/v1/operator/commands/{param}`, `GET /api/v1/operator/degraded-control-guidance` |
| `governance-runtime-risk-audit` | 18 | `GET /bff/audit`, `GET /bff/approvals/{param}`, `DELETE /bff/confirm-tokens/{param}` |
| `health` | 12 | `GET /bff/capabilities`, `GET /bff/feature-flags`, `GET /bff/readyz` |
| `agora-core` | 8 | `GET /bff/agora/ask/sessions`, `GET /bff/agora/evaluation-runs`, `GET /bff/agora/inbox` |
| `evolution-experiment-jobs-events` | 8 | `GET /bff/artifacts`, `GET /bff/research-experiments`, `PATCH /bff/artifacts/{param}` |
| `execute-plans-cutover-smoke` | 7 | `GET /bff/v5/control-room`, `GET /bff/v5/loop-runs/{param}`, `POST /bff/v5/sentinel/findings/{param}/status` |
| `capital-ranking-rebalance` | 5 | `GET /bff/ranking-formulas`, `PATCH /bff/rebalances/{param}`, `POST /bff/ranking-formulas` |
| `v5-interventions` | 5 | `GET /bff/v5/interventions/{param}`, `POST /bff/v5/interventions/{param}/claim`, `POST /bff/v5/interventions/{param}/decide` |
| `mcp-final` | 4 | `GET /bff/mcp-servers`, `GET /bff/mcp-tools/{param}` |
| `session-auth-me` | 4 | `PATCH /bff/me/locale`, `POST /bff/auth/refresh`, `POST /bff/logout` |
| `sse-substrate` | 4 | `GET /api/v1/incidents/stream`, `GET /api/v1/kill-switch/updates`, `GET /api/v1/runtime/{param}/events/stream` |
| `agora-extended` | 3 | `GET /bff/agora/persona-lab/runs`, `GET /bff/channels`, `GET /bff/channels/{param}` |
| `final-contract` | 1 | `POST /bff/actions/{param}/{param}/{param}` |

### Live Worktree Caveat

The live route extractor currently sees two additional dirty-worktree routes not
present in the checked-in backend snapshot:

- `GET /bff/research-analyses`
- `GET /bff/research-analyses/{param}`

`python3 scripts/bff_route_manifest_backend.py --dump` reports 373 live routes
and classifies both routes as `family: unknown`. This matches the parent review
note that `python3 scripts/bff_route_manifest_backend.py --check` fails because
of unrelated dirty `services/control-plane/bff/main.py` changes.

Parent owners should not absorb this sidecar as approval for those routes. A
future task should either:

- refresh the backend snapshot and frontend manifest together, with a decided
  route family and frontend ownership; or
- mark the routes as explicitly non-blocking if they are deferred,
  superseded, mock-only, or otherwise outside the active frontend surface.

## Operator Journey for Route Changes

BFF-CONSOL-026 changes the delivery journey for route work. It is a CI and
review gate, not an operator runtime behavior change.

### Backend route owner adds or changes a route

```text
Backend owner adds FastAPI route
  -> Run backend manifest extractor
  -> If the route is active frontend surface, add/update frontend manifest row
  -> If not active frontend surface, mark the route non-blocking with rationale
  -> Run python3 scripts/bff_route_diff.py --check-baseline
  -> If the failure surface changes, update route-diff-baseline.json in the same reviewed task
  -> Reviewer checks that the baseline delta is intentional and task-scoped
```

### Frontend route owner adds or changes a call

```text
Frontend owner adds route use to execute-plans surface
  -> Add/update execute_plans_bff_routes.json row
  -> Confirm matching backend method/path or set covered_by for an alias
  -> If no backend exists, mark mock_only/deferred/superseded with task_id and reason
  -> Run python3 scripts/bff_route_diff.py --check-baseline
  -> Do not merge while frontend_missing_backend has an unaccounted active row
```

### Reviewer journey

```text
Reviewer opens route diff output
  -> Confirm frontend_missing_backend remains zero unless intentionally added
  -> Confirm backend_missing_frontend changes are either paired with frontend rows or non-blocking status
  -> Confirm family mismatches remain zero or are explicitly corrected
  -> Confirm baseline updates contain only task-owned route changes
```

## Frontend Handoff Materials

Frontend owners should treat the route manifest as a deliberate contract index,
not as generated noise. Every active frontend BFF call should have:

- `method`
- normalized `path`
- `family`
- `status`
- optional `task_id`
- optional `covered_by`
- optional `reason`

Use these non-blocking statuses only when the route is deliberately not required
to have a counterpart:

- `deferred`
- `deferred_with_task`
- `deprecated`
- `mock_only`
- `mock_only_dev`
- `superseded`
- `superseded_with_reason`

For alias rows, use `covered_by` to point at the backend route that implements
the frontend-visible shape. Do not duplicate active rows with different family
names; the fail-hard diff treats family mismatches as failures.

### Recommended frontend review checklist

| Check | Expected result |
|---|---|
| New frontend call has backend counterpart | `frontend_missing_backend` remains zero. |
| New backend active route has frontend row | `backend_missing_frontend` does not grow. |
| Deferred/mock route is intentional | Row has non-blocking status, `task_id`, and a concise `reason`. |
| Alias route is intentional | Row has `covered_by` pointing to the implemented backend route. |
| Family names match | `naming_mismatches` remains zero. |
| Baseline update is scoped | `route-diff-baseline.json` changes only for the task-owned route set. |

## Parent Absorption Notes

- This packet is advisory support for the BFF consolidation record and final
  acceptance packet.
- It should not be promoted into canonical truth by copy/paste. If any item
  needs canonical status, open or attach a parent-owned task that edits the
  proper canonical artifact.
- The parent owner can link this packet when explaining the fail-hard gate,
  route backlog size, and frontend manifest responsibilities.
- The two dirty `research-analyses` routes should remain outside the
  BFF-CONSOL-026 approval scope unless a later parent task explicitly absorbs
  and verifies them.

## Verification for This Sidecar

Commands and focused checks used while preparing this support packet:

```bash
jq '.tasks[] | select(.id=="BFF-CONSOL-026-SIDECAR-BFF-HANDOFF")' ai-status.json
sed -n '1,220p' .orchestrator/reviews/BFF-CONSOL-026-review-codex.md
jq '.' ai-task-archive/tasks/BFF-CONSOL-026.json
sed -n '1,240p' support/evidence/BFF-CONSOL-026-closeout.md
python3 scripts/bff_route_diff.py --dump | jq '{summary, backend_missing_frontend_by_family:(.failures.backend_missing_frontend | group_by(.family) | map({family:.[0].family,count:length,examples:.[0:5]|map(.key)})), frontend_missing_backend:.failures.frontend_missing_backend, naming_mismatches:.failures.naming_mismatches}'
python3 scripts/bff_route_manifest_backend.py --dump | jq '{metadata, research_routes:(.entries | map(select(.path|test("research-analyses"))))}'
git diff --check -- support/sidecars/BFF-CONSOL-026/BFF-CONSOL-026-SIDECAR-BFF-HANDOFF.md
python3 scripts/bff_route_diff.py --check-baseline
python3 -m pytest scripts/test_bff_route_diff.py -q
git status --short
```

Observed results:

- Sidecar task status was `in_progress`, owner `Codex2`, reviewer `Codex`.
- Parent `BFF-CONSOL-026` was archived as `done` with Codex approval.
- Route diff summary was fail-hard baseline status `fail` with 209 locked
  backend-only failures, 0 frontend-only failures, 0 naming mismatches, and 0
  warnings.
- Live backend extractor saw 373 routes because of unrelated dirty
  `research-analyses` additions; the checked-in backend snapshot remains 371
  routes.
- Handoff packet whitespace check passed.
- `python3 scripts/bff_route_diff.py --check-baseline` reported the baseline
  matches the current fail-hard surface and locks 209 grandfathered backend-only
  routes.
- `python3 -m pytest scripts/test_bff_route_diff.py -q` passed: `7 passed`.
- No canonical truth, runtime implementation, registry implementation, or
  governance implementation was modified by this sidecar.
