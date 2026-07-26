# L12-SRC-001 source due-state reconciliation evidence

Owner: Codex2
Reviewer: Codex
Status: cross-process repair complete; independent re-review required

## Outcome

The source controller now exposes two fail-closed modes:

- `reconcile_only` continuously admits Persona requirements and converges
  connector/schedule actual state without calling the provider scheduler.
- `reconcile_and_pull` is the separately governed execution mode. It requires
  a finite 1–24 tick budget and remains behind the existing connector adapter
  allowlist, exact-host egress policy, connector rate policy, and scheduler
  concurrency cap.

The reconcile-only readback rejects a tick if `source_record_count`,
`dlq_count`, or `frontier_backlog` changes. That makes “schedule intent was
reconciled” distinguishable from “external provider data was pulled.”

JSONL mutation paths now refresh durable state while holding sidecar
`flock(LOCK_EX)` leases. Persona reconciliation also holds one process-shared
transaction lock across requirement admission, connector convergence, and
schedule convergence. Frontier enqueue and claim are atomic against the latest
shared log, so only the worker that claims the durable frontier may create an
ingest run or SourceRecord.

## Acceptance evidence

| Acceptance | Result | Evidence |
|---|---|---|
| Automatic Persona requirement to connector/schedule desired state | Pass | `test_run_controller_tick_reconcile_only_never_executes_provider`; existing create/repair API tests |
| Supervised schedules and bounded allowlisted provider pull | Pass | finite provider-mode config guard; API concurrency cap; [bounded-live-proof.json](bounded-live-proof.json) |
| Duplicate tick and two-worker safety | Pass | `test_two_process_workers_create_one_connector_schedule_run_and_source_record` spawns two independent processes/modules against one data directory and asserts one connector-config record, one schedule record, one frontier id, one ingest-run id, and one SourceRecord |
| Restart recovery and connector failure isolation | Pass | persisted missed-tick tests, stale frontier recovery, and four-outcome provider matrix |
| Truthful SourceHealth | Pass | `metadata.last_outcome` reports `success`, `policy_denial`, `credential_unavailable`, or `provider_failure` with stable typed category/code |

## Validation

- Genuine two-process acceptance plus same-process regressions: 3 passed.
- Focused controller/reconciler/scheduler suite: 125 passed.
- Full `services/source_ingestion` suite: 730 passed, 2 skipped.
- Bounded real-provider drill: exact host `openapi.twse.com.tw`, TWSE only,
  `max_records=3`, timeout 10 seconds, connector concurrency 1; three normalized
  `tw_price_daily` SourceRecords returned.

The earlier thread-only tests remain as same-process regression coverage and
are explicitly named `*_threads_*`; they are not the two-worker acceptance
proof.

## Composition boundary

This task owns `services/source_ingestion` behavior and its evidence packet.
Root deployment/profile wiring is intentionally left to `L12-DIST-001`, the
allowed overlap task. This packet does not claim hosted deployment, continuous
external crawl, TPEx proof, or live broker/capital authority.
