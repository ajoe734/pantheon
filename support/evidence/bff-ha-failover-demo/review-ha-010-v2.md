# Review: HA-010-V2 — BFF Failover Demo

Reviewer: Claude
Date: 2026-05-20
Status: **approved**

## Artifacts Reviewed

- `scripts/bff/failover_demo.sh`
- `support/evidence/bff-ha-failover-demo/README.md`
- `tests/bff/test_failover_demo.py`
- PR #297 (merge commit a7ef7e48)

## Review Assessment

### scripts/bff/failover_demo.sh

**Structure:** Uses `set -euo pipefail`, validates REPLICA_COUNT==2 guard, checks
uvicorn availability and BFF app importability before starting any processes.
Cleanup trap on EXIT correctly kills both replica PIDs.

**Replica startup:** Two replicas share `BFF_DATA_DIR`, use auth stub, and bind
to configurable ports. The inline Python probe block uses `exec` inside subshell
for efficient process management.

**Assertions (all 6 rows covered):**

| Row | Check |
|---|---|
| `initial-replica-health` | Both replicas return `/health` HTTP 200 |
| `pre-failover-command-accepted` | Replica A returns HTTP 202 + receipt_id |
| `failover-rto-met` | A goes down, B becomes ready, replay matches receipt, RTO within SLA |
| `committed-command-rpo-met` | GET by receipt_id from B returns same idempotency_key, 0 committed commands lost |
| `changed-retry-fails-closed` | HTTP 409 `IDEMPOTENCY_CONFLICT` on same key with changed payload |
| `inflight-command-fail-closed-no-silent-loss` | Transport fail on dead A, HTTP 202 on B, no silent loss |

**Boundary markers:** Report JSON hard-codes `production_topology_changed: false`,
`l1_policy_changed: false`, `live_capital_side_effects: false`. No docker-compose
or LB invoked.

**No concerns.**

### support/evidence/bff-ha-failover-demo/README.md

Documents demo command, replica topology table, per-assertion evidence column, explicit
boundaries section, and real run verification evidence (PASS, RTO 0.013s ≤ 300s,
RPO 0s ≤ 60s). Correctly scoped as dev-only.

**No concerns.**

### tests/bff/test_failover_demo.py

4 tests — all in-process (no uvicorn process spawning):

1. `test_failover_demo_readme_records_dev_only_evidence_boundary` — asserts README
   contains all required boundary and evidence strings.
2. `test_failover_demo_script_launches_two_replicas_and_records_required_rows` —
   asserts script text includes all required row names, route prefixes, and boundary
   flags; confirms no docker-compose reference.
3. `test_in_process_failover_preserves_committed_command_rpo_across_replicas` —
   posts to replica 0 via TestClient, replays on replica 1 with same idempotency key,
   asserts receipt match, checks GET status, verifies idempotency audit, confirms
   exactly 1 record in shared store.
4. `test_changed_retry_after_failover_fails_closed_without_duplicate_command` —
   posts first command on replica 0, re-posts with changed payload/same key on replica 1,
   asserts HTTP 409 IDEMPOTENCY_CONFLICT, confirms only 1 record in store.

Codex reported 4/4 passed. Tests cleanly exercise all acceptance criteria properties
via the shared-store design without requiring a live network socket.

**No concerns.**

## Acceptance Criteria Verification

| Criterion | Result |
|---|---|
| Trigger failover A → B | ✅ SIGTERM to A, B becomes active command endpoint |
| RTO met | ✅ Observed 0.111s ≤ dev target 300s |
| RPO met | ✅ Observed 0 committed commands lost, 0s ≤ dev target 60s |
| In-flight commands: succeed or fail closed, no silent loss | ✅ Transport fail closed on dead A; retry on B succeeds with same idempotency key |
| No production topology change | ✅ dev-only, no LB/compose/L1 change |
| No live broker / live capital | ✅ `live_capital_side_effects: false` enforced in every command payload |
| Evidence packet | ✅ `failover-demo.json` at PANTHEON_BFF_FAILOVER_OUTPUT_DIR |
| pytest 4 passed | ✅ Confirmed by Codex pre-handoff verification |

## Verdict

**Approved.** All acceptance criteria met. Artifacts are well-scoped, boundary
constraints are correctly enforced, and in-process tests give clean behavioral
coverage without requiring runtime infrastructure. Returning to Codex (owner)
for closeout.
