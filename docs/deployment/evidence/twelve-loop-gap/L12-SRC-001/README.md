# L12-SRC-001 source due-state reconciliation evidence

Owner: Codex2
Reviewer: Codex
Status: implementation complete; independent review required

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

## Acceptance evidence

| Acceptance | Result | Evidence |
|---|---|---|
| Automatic Persona requirement to connector/schedule desired state | Pass | `test_run_controller_tick_reconcile_only_never_executes_provider`; existing create/repair API tests |
| Supervised schedules and bounded allowlisted provider pull | Pass | finite provider-mode config guard; API concurrency cap; [bounded-live-proof.json](bounded-live-proof.json) |
| Duplicate tick and two-worker safety | Pass | concurrent reconcile and scheduled-run tests prove one connector, one schedule, one run, and one SourceRecord |
| Restart recovery and connector failure isolation | Pass | persisted missed-tick tests, stale frontier recovery, and four-outcome provider matrix |
| Truthful SourceHealth | Pass | `metadata.last_outcome` reports `success`, `policy_denial`, `credential_unavailable`, or `provider_failure` with stable typed category/code |

## Validation

- Focused controller/reconciler/scheduler suite: 124 passed.
- Full `services/source_ingestion` suite: 729 passed, 2 skipped.
- Bounded real-provider drill: exact host `openapi.twse.com.tw`, TWSE only,
  `max_records=3`, timeout 10 seconds, connector concurrency 1; three normalized
  `tw_price_daily` SourceRecords returned.

## Composition boundary

This task owns `services/source_ingestion` behavior and its evidence packet.
Root deployment/profile wiring is intentionally left to `L12-DIST-001`, the
allowed overlap task. This packet does not claim hosted deployment, continuous
external crawl, TPEx proof, or live broker/capital authority.
