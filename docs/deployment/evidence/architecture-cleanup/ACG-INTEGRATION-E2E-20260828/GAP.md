# ACG-INTEGRATION-E2E-20260828 Local Integration GAP

- Task: `ACG-INTEGRATION-E2E-20260828`
- Owner / reviewer: `Claude` / `Codex`
- Pantheon head: `56b9ded77ddbd89011578b60f417ef8f31643f2d`
- execute-plans head: `7409bb4192768737535ec480bdf351a15630dc89`
- Outcome: **local cleanup integration rejected; two owning paths require correction**

Ownership reassigned after a supervisor lost-lease recovery (Claude now owns,
Codex now reviews). The findings below were independently re-verified by the
new owner against the same clean `origin/dev` head (`56b9ded77`, unchanged)
without adding any verifier script: `read_store.py` (124 lines) still defines
neither `_merge_market_persona_fleet`, `ServiceBackedReadAdapter`, nor
`CanonicalSnapshotAdapter`, all three callers still import them, the
cross-loop drill fixture still ends in `:mfa` with no tenant segment, and both
repositories' `dev-compatibility-manifest.json` files still disagree exactly
as recorded in GAP-02.

This requeue is intentionally docs-only. It records reproducible local
caller/test migration failures and canonical FE/BFF handoff-record drift. It
does not make hosted, operator-live, or security acceptance an active blocker,
does not create a repair task, and does not add a verifier or synthetic record.

## Confirmed green evidence

| Surface | Exact result |
|---|---|
| Python static compile plus route/current-twelve contracts | `49 passed` after `compileall` |
| Source Ingestion full suite | `845 passed, 2 optional integration smokes skipped`; the earlier concurrent-run failure was re-run successfully and is retracted |
| Runtime Manager full suite | `274 passed, 3 subtests passed` |
| Frontend exact-head suite | typecheck passed; lint `0 errors`; unit `193 files / 1964 tests`; import graph `5 passed`; strict-live build passed |
| v1.13 generated contract chain | `PANTHEON_CONTRACT_ROOT=<this worktree> npm run test:contract`: drift check aligned `49 schemas / 157 routes / 75 hashes`; `7 passed` |
| Compose/deploy contract suite | Compose config, shell syntax, and Python compile passed; `337 passed, 1 optional live-VACUUM skip` |

These green results do not override the two failures below.

## GAP-01 — deleted read-store callers remain in BFF tests

The full `services/control-plane/bff` suite stops during collection with three
imports of symbols intentionally removed from the 124-line pure-projection
`read_store.py`:

| Test caller | Removed symbol |
|---|---|
| `test_pathreon_market_persona_fleet_contract.py` | `_merge_market_persona_fleet` |
| `test_read_store_loop_sentinel.py` | `ServiceBackedReadAdapter` |
| `tests/test_bff_approvals_surface_contract.py` | `CanonicalSnapshotAdapter` |

Exact result: `3 errors during collection` after `1019.52s`.

Classification: **caller/test migration incomplete**. The correction is to
move the assertions to the existing canonical owners or delete obsolete tests;
the removed compatibility store must not be restored.

A second caller-fixture mismatch is independently reproducible in
`test_loop_auto_bff004_cross_loop_drill.py`: its bearer fixture ends in `:mfa`
without a concrete tenant, so the loop-health route correctly returns HTTP 403
with `precondition_failed=tenant_scope` before the intended historical-fixture
assertion runs. Classification: **test auth fixture not migrated to tenant
scope**, not a runtime loop-health failure.

## GAP-02 — canonical FE/BFF compatibility manifests disagree

The current v1.13 generation handoff is internally sound:

- Pantheon `backend-generation-input.v1_13.json` and execute-plans
  `frontend-generation-output.v1_13.json` both bind contract commit
  `6ad99d2e5abe4f31c9f48892ae7f44bf3bbab980`;
- bundle-index and OpenAPI SHA-256 values match;
- the exact-head contract drift test passes.

The canonical dev compatibility records do not describe that same chain:

| Record | Family / status | Contract and runtime identity |
|---|---|---|
| Pantheon `docs/contracts/agora/dev-compatibility-manifest.json` | `agora.v1.13` / `accepted` | contract `9e909de182f9f2379d23e8e6b81eefec29ffbce7`; older backend/frontend runtime pair |
| execute-plans `docs/contracts/agora/dev-compatibility-manifest.json` | `agora.v1.1` / `pending` | backend contract `7ab267adc9f88519149ae01a874764d8fd8c1108`; frontend runtime and generated-from are all-zero placeholders |

The reviewed blobs at the exact heads are:

- Pantheon generation input: `db4e7512a2f0d818f5962d126adb7206336f34e7`;
- Pantheon dev compatibility manifest: `39559b0527a65f01a3cb42c702443ae3918ed26f`;
- execute-plans generation output: `c319517017f3fb2f2de847ca97eb01c00595d86f`;
- execute-plans dev compatibility manifest: `e95433b0b1323bd1564647a61a0b756519fc3bb8`.

Classification: **canonical handoff record drift**. This evidence does not
claim an API/schema incompatibility—the generated v1.13 payload is aligned. It
shows that the two canonical dev manifests cannot jointly identify one accepted
FE/BFF contract pair and must be reconciled without adding a parallel manifest
or compatibility facade.

## Required local recheck

1. Migrate/remove the three stale test callers and make the drill fixture tenant-scoped; the full BFF suite must collect and pass.
2. Reconcile both repositories' canonical dev compatibility manifests to the same v1.13 generation chain and real FE/BFF identities; rerun the exact-head contract drift suite.
3. Preserve the deleted read-store boundary: no legacy facade, fallback owner, verifier script, or alternate truth record.

No Source Ingestion repair is requested. No hosted, operator-live, or security
task is requested by this report.
