# EVOCHAIN-004 — Freeze / Rollback Canonical Store

Status: owner closeout evidence complete; pending task PR merge

Owner: Codex
Reviewer: Claude
Branch: `task/EVOCHAIN-004`
Merge target: `dev`

## Delivered Contract

The governance service now owns two separate canonical datasets:

| Dataset | Dev/local store | Postgres owner table | Read API |
|---|---|---|---|
| Freeze orders | `$GOVERNANCE_DATA_DIR/freeze_orders.json` | `governance.freeze_orders` | `GET /api/governance/freeze-orders[/{id}]` |
| Rollback request/outcome records | `$GOVERNANCE_DATA_DIR/rollbacks.json` | `governance.rollbacks` | `GET /api/governance/rollbacks[/{id}]` |

List APIs return `200 []` for a healthy empty store. Freeze orders support
`status` and `scope` filters. Rollbacks support `runtime_id`, `action_type`,
and `status` filters. Detail reads return `404` for unknown IDs.

The persistence builder follows `GOVERNANCE_STORE_BACKEND=json|postgres`.
JSON is the dev/local recovery path; enforced staging/production posture uses
the governance-owned Postgres tables through `PostgresJsonOwnerStore`.

## BFF Read Semantics

`ServiceBackedReadAdapter` registers canonical datasets `freeze_orders` and
`all_rollbacks`. Both call the governance HTTP service first, using the
already-deployed `PANTHEON_GOVERNANCE_APPROVAL_API_URL` as the primary explicit
service-discovery key and `PANTHEON_GOVERNANCE_SERVICE_URL` as an explicit
compatibility fallback. They intentionally ignore the legacy
`PANTHEON_GOVERNANCE_API_URL`, which can point at the evolution service. Direct
backend-owned store files are a secondary service-store path; the BFF-local
snapshot is last-resort fallback only.

Truth rules:

- healthy `200 []` -> `source: service_client`, surface `status: ok`
- healthy populated list -> canonical records, filters and ordering preserved
- 404/null/non-list payload -> not available; never reported as healthy empty
- service failure + strict mode -> `source: missing`, `status: unavailable`
- service failure + fallback enabled -> `source: local_snapshot`, degraded

This makes the Evolution Journal `freeze_orders` and `rollbacks` source
surfaces `ok` even when the canonical stores contain zero records. When the
other journal dependencies are also healthy, the composed journal surface is
`ok` rather than permanently degraded by these two datasets.

## Ownership Boundary

FreezeOrder remains a governance quarantine object. The rollback dataset is a
canonical request/outcome audit read model; it does not grant governance the
right to mutate runtime state. The Rollback Controller authors immutable
rollback requests, and Runtime Manager remains the exclusive writer of
RuntimeBinding, position ownership, and telemetry cutover.

New rollback records should use the canonical action types `replace`,
`pause_then_replace`, or `liquidate_then_replace`. Existing seed records are
passed through for compatibility.

## Changed Scope

- `.orchestrator/task-briefs/evochain_004.md`
- `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`
- `services/governance/record_store.py`
- `services/governance/main.py`
- `services/governance/contract.md`
- `services/governance/test_freeze_rollback_store.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/tests/test_evochain_004_freeze_rollback_store.py`
- this task artifact

Anchor commits:

- `d7ee20512` — governance owner-store/read API layer
- `f455cbb8f` — BFF service-client/surface-truth layer
- `7fe37853e` — refreshed reviewer approval handoff state

## Owner Closeout Recheck

Claude approved implementation commit `e764a0fc1` after the original 50-test
suite passed. During owner finalization, a branch audit found that the new BFF
clients could select the legacy `PANTHEON_GOVERNANCE_API_URL` before the
explicit governance URL. Deployed service-family configuration may point that
legacy alias at evolution. The closeout correction removes the legacy alias
from both new datasets and adds conflict regression coverage.

Claude's refreshed review approved corrected commit `ef679632a`; the approval
handoff was anchored at `7fe37853e`. Owner closeout then merged current
`origin/dev` (`0e8c06603`) in `dc9385344` and reran the complete focused suite
before publication.

## Verification

Executed after composing the task branch with the latest `origin/dev`:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  services/governance/test_freeze_rollback_store.py \
  services/governance/test_governance_api.py \
  services/control-plane/bff/tests/test_evochain_004_freeze_rollback_store.py \
  services/control-plane/bff/test_read_store_service_clients.py \
  services/control-plane/bff/test_read_store_bootstrap_snapshot.py \
  services/control-plane/bff/tests/test_bff_b3_evolution_journal.py \
  services/control-plane/bff/test_evolution_center_contract.py
```

Reviewer-handoff result at `e764a0fc1`: `50 passed, 12 warnings in 66.43s`.

Owner closeout correction result before latest-`dev` composition:
`51 passed, 12 warnings in 28.43s`. Warnings are the existing FastAPI
`on_event` deprecation notices from BFF startup/shutdown registration.

Final owner revalidation after merging `origin/dev` at `4e410f2cf`:
`51 passed, 12 warnings in 26.43s`.

Final owner closeout verification after merging `origin/dev` at `0e8c06603`
in `dc9385344`: `52 passed, 12 warnings in 146.20s`. The additional passing
case comes from newer `dev` coverage in the selected journal test module; the
warning class remains the existing FastAPI `on_event` deprecation.

Additional checks:

- `git diff --check origin/dev...HEAD` — passed
- final branch reconciliation — `origin/dev` at `0e8c06603` merged cleanly in
  `dc9385344` before final owner verification

## Residual Risks / Follow-up

- `ReadSurfaceStore.get_rollbacks(runtime_id)` and
  `get_rollbacks_by_incident(incident_id)` still serve their legacy
  per-runtime/per-incident datasets. This task's accepted scope is the EV-04
  `list_all_rollbacks` / Evolution Journal path. Owner: control-plane
  composition review with `EVOCHAIN-005`; expiry: 2026-07-20.
- The existing `time_range` argument on `list_all_rollbacks` remains a v1
  deferred no-op. Owner: evolution read-contract follow-up; expiry: when a
  server-side time-window contract is approved.
- Hosted dev curl/deployment proof is intentionally deferred to
  `EVOCHAIN-011`, after `EVOCHAIN-005` adds governed write APIs and records can
  be produced end-to-end.
