# L12-REC-001 reconciliation durability evidence

Status: independent review approved by `Codex2`; PR #4150 merged to `dev` as
`917d281ae10b0426dae0667cf452b2d64263777f`; product closeout is packaged.

This packet proves the repository implementation and focused product tests for
durable, tenant-scoped reconciliation windows. It does not claim that the
hosted environment has already enabled token authentication, does not mutate a
runtime or emergency-control state, and does not replace the later
`L12-MANIFEST-001` / hosted verification tasks.

The machine-readable receipt is in [`evidence.json`](evidence.json), with its
digest in [`evidence.sha256`](evidence.sha256). The schema-governed product
closeout manifest is in [`closeout/evidence.json`](closeout/evidence.json),
with its digest in
[`closeout/evidence.sha256`](closeout/evidence.sha256).

## Delivered authority

- Task: `L12-REC-001`
- Owner: `Codex`
- Reviewer: `Codex2`
- Branch base: `1827cce2e9d6c31f7b57ec4400f3c5d1b3bede29`
  (`origin/dev` at dispatch)
- Owner-store anchor:
  `9948a1da6be9969df7ce7c052ac64c5214189360`
- Worker/API anchor:
  `e175b29c71b73caa10eb1b2e35c46a4f3bc168f7`
- Rejected-acceptance regression anchor:
  `23959c7ae2816067fa2a605397cd936f7e2e8eeb`
- Consumer recovery repair anchor:
  `f4d9a84d5c79b1669f260f25fd428054e65d45d2`
- Approved review head:
  `ba5090aa558455aabd2afe39a30cf22be077b32e`

The independent reviewer approved all repaired acceptance gaps at
`2026-07-26T11:05:17Z`. Reviewer validation reran the four rejected
regressions, the 98-test reconciliation suite, the 26-test affected
persistence/lifecycle suite, Python compilation, evidence checksum and JSON
assertions, `git diff --check`, and all six required PR checks.

Postgres mode now owns evaluations, alert handoffs, ReconciliationRecords,
DriftReports, work claims, and worker checkpoints. JSON mode implements the
same logical authorities with strict reads, transaction-scoped file locks,
atomic replace, file fsync, and directory fsync. Tenant identity participates
in the storage key, so equal external IDs from two tenants cannot overwrite
one another. Tenant-aware JSON and Postgres legacy/raw-key fallback also
verifies the record's exact tenant before returning it; this guard covers
evaluations, alert handoffs, reconciliation records, drift reports, and worker
states.

## Lease and recovery proof

Scheduler windows are deterministic over tenant plus configured observation
interval. The API atomically claims a window before dependency reads or side
effects. A concurrent worker receives `deferred`; a completed window returns a
durable receipt; failed and expired work may be reclaimed; an old lease token
cannot complete a reclaimed window.

The scheduler sends the same `tick_id` and `window_id` on timeout retry. The
endpoint records `duration_seconds`, `sla_seconds`, `within_sla`, and
`sla_status`; scheduler startup rejects a timeout smaller than its configured
SLA.

Telemetry consumption uses one tenant/event claim before DriftReport creation
or incident classification. A replay may return the already accepted report
for compatibility, but it does not repeat the incident side effect. Consumer
pending/DLQ/completed state is checkpointed around delivery attempts and
guarded by a cross-process lease. Its checkpoint and release operations compare
the exact token acquired by that process against the canonical token while
holding the state-file lock; a matching checkpoint renews the lease. After
expiry and successor acquisition, the stale process cannot save or release,
even when the successor reuses the same worker ID. Pending, DLQ, and completed
identity is tenant plus event ID, so two tenants with the same external event
ID both enqueue and deliver. Corrupt state returns unhealthy without a source
fetch and without overwriting the corrupt bytes. Existing incident listener
replay remains fail-closed and idempotent.

## Tenant and incident proof

When `RECONCILIATION_DRIFT_AUTH_MODE=token`, every reconciliation API request
requires a constant-time-checked bearer token and `X-Tenant-Id`. Telemetry,
correlation-envelope, scheduled-summary, and incident tenant identity must
match the authenticated tenant. Reads filter by tenant, and cross-tenant
lookups return no foreign record.

The hardening suite writes the same external DriftReport ID for `tenant-a` and
`tenant-b`, then reads one isolated record per tenant. It also seeds each
legacy/raw-key record type with a tenant-A record and proves that a tenant-B
lookup returns no record in either JSON or Postgres mode. Its incident case
preserves `tenant_id`, `incident_id`, `source_event_id`, binding/runtime
identity, and telemetry event IDs. A tenant-B request carrying tenant-A
incident identity is rejected with HTTP 403.

## Validation

```text
python3 -m py_compile \
  services/reconciliation-drift/store.py \
  services/reconciliation-drift/main.py \
  services/reconciliation-drift/consumer.py \
  services/reconciliation-drift/scheduler_worker.py \
  services/reconciliation-drift/incident_listener.py
exit 0

pytest -q services/reconciliation-drift/tests
98 passed, 1 warning in 30.45s

pytest -q \
  services/foundation/tests/test_control_plane_postgres_owner_stores.py \
  services/foundation/tests/test_persistence_posture.py \
  services/trade_journey/test_canonical_paper_lifecycle_integration.py
26 passed in 4.60s

ruff check --select F,E9 \
  services/reconciliation-drift/store.py \
  services/reconciliation-drift/main.py \
  services/reconciliation-drift/consumer.py \
  services/reconciliation-drift/scheduler_worker.py \
  services/reconciliation-drift/incident_listener.py \
  services/reconciliation-drift/tests/test_l12_rec_001_hardening.py \
  services/reconciliation-drift/tests/test_reconciliation_drift_store.py
All checks passed
```

The single pytest warning is Starlette's installed TestClient/httpx
deprecation notice; it is not a reconciliation failure.

## Acceptance mapping

| Acceptance | Evidence |
| --- | --- |
| Measured configurable SLA | deterministic clock test asserts 0.25 seconds against 1.0-second SLA; timeout/SLA config is validated |
| No duplicate logical window | two scheduler requests execute one dependency read/evaluation; two consumer requests build/classify one report/incident |
| Durable tenant records/reports/state | JSON/Postgres owner-store tests, equal-ID tenant isolation, persisted worker checkpoint, and exact-tenant legacy/raw lookup across all five getters |
| Consumer takeover recovery | successor token/checkpoint survive stale save and release attempts, including reuse of the same worker ID |
| Tenant-scoped consumer delivery | equal external event IDs for tenant A and tenant B both enqueue, deliver, and retain separate completed receipts |
| Corruption fail-closed | corrupt consumer/work-claim sources remain byte-identical and return errors |
| Restart/retry/DLQ recovery | stable scheduler timeout retry, expired/failed claim recovery, consumer and incident-listener replay regression tests |

All task acceptance criteria are satisfied at repository/product-test level.
Hosted activation and current dev deployment proof remain deliberately
unclaimed.
