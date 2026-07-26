# L12-DIST-001 transactional distillation evidence

Owner: Claude
Reviewer: Antigravity
Status: owner implementation and acceptance proof ready; independent review pending

## Outcome

Source-to-draft distillation is now a versioned, transactional, replayable
event pipeline instead of a polling JSONL scan.

- **Versioned admission.** `source_version_digest` derives a content identity
  from the normalized SourceRecord while deliberately ignoring run-local
  correlation fields (`ingest_run_id`, `source_ingest_run_id`, `trace_id`).
  Re-ingesting unchanged content resolves to the version already committed;
  revised content becomes a distinct event with its own job.
- **Transactional outbox/inbox.** `DistillationJobQueue` commits the source
  snapshot and its outbox event in one `BEGIN IMMEDIATE` SQLite transaction,
  with a foreign key from the outbox to the committed version. The inbox,
  outbox, and dead-letter tables move together on every terminal update.
- **Leased claims.** Claims are fenced by a unique lease token and a lease
  epoch. An expired lease is recovered back to `pending`; a completion carrying
  a stale token is rejected rather than silently applied.
- **Truthful Registry delivery.** A job is acknowledged only after a terminal
  Registry readback. Write failure parks a durable retry with backoff and
  dead-letters after the attempt budget, and the controller tick raises with
  stage `registry_sync` instead of recording success.
- **Immutable artifacts.** A Registry entry in `approved` or `retired` state is
  never rewritten, and the terminal readback (not the write response) decides
  the outcome, so an approval landing mid-write still blocks mutation.

## Defects this work found and fixed

Both were found by writing the acceptance proof, not by inspection.

1. **Registry sync fell outside the seed's evidence lineage.** The worker
   resolved a version key for the evidence item, but the controller
   re-synthesized its own item from the raw digest. On a source's *first*
   version the two disagreed, so `StrategySpecConversionService` rejected the
   item as outside the StrategySpecSeed lineage and every job entered
   `retry_wait`. The worker now hands the Registry sink a `RegistrySyncRequest`
   carrying the exact evidence item and bundle the seed was materialized from.

2. **Identical content re-ingested in a later run failed admission.** The
   digest ignores run-local fields by design, but admission compared the full
   committed snapshot byte for byte. A second ingest run of unchanged content
   therefore raised `source version identity collision` instead of resolving to
   the committed version. Versioned rows now compare on the digest projection;
   legacy non-versioned rows still require byte equality.

3. **Concurrent workers lost seed drafts.** `JsonlRegistryStore.upsert` and
   `delete` rewrote the whole file from an unlocked read, so two workers writing
   different seeds could drop one another's records. Both mutations now hold the
   existing sidecar `flock` lease across the read and the overwrite.

   Measured with the lease removed, two processes distilling 40 sources
   persisted 39, 39, and 38 of 40 seeds across three runs. With the lease all
   40 persist on every run.

## Acceptance evidence

All proofs live in
`services/source_ingestion/tests/test_l12_dist_001_transactional_distillation.py`,
one class per acceptance criterion.

| Acceptance | Result | Evidence |
|---|---|---|
| Committed normalized SourceRecord transactionally enqueues one versioned job | Pass | `TestVersionedAdmission` — duplicate admission yields one job and one version row; the job carries its own committed snapshot; a contradicting payload under the same identity rolls back the whole admission |
| Concurrent workers claim with lease, revised content handled by digest | Pass | `TestConcurrentWorkers` — two spawned OS processes claim disjoint jobs, and a barrier-synchronized two-process run distills 40 sources to 40 distinct seeds with both workers demonstrably contending; stale lease completion is rejected; revised content is a distinct version while an identical re-crawl is not |
| Registry failure records controller failure and durable retry or DLQ | Pass | `TestRegistryFailureIsTruthful` — the tick raises at stage `registry_sync`, records no success, parks a durable retry, dead-letters with a redrivable payload after the attempt budget, and replays to `done` on recovery |
| Approved immutable artifacts remain unchanged | Pass | `TestApprovedArtifactsAreImmutable` — an approved entry is never written, approval landing between probe and readback still blocks mutation, and an accepted seed draft is not overwritten |
| Crash before or after Registry write replays to one terminal draft | Pass | `TestCrashReplay` — a crash before the write replays after lease expiry to one draft; a lost acknowledgement after a landed write replays by readback without a second Registry entry; repeated ticks stay idempotent |

## Proof required by the task packet

| Proof | Result | How |
|---|---|---|
| Real source-to-registry service test | Pass | `TestRealSourceToRegistryService` serves the real `services/registry` FastAPI app over real HTTP on a real port and drives a full controller tick through it. Source lineage (`source_id`, `source_digest`, `source_event_version`) is read back over HTTP from the registered entry. No HTTP mock is used in this section. |
| Two-worker and revised-content test | Pass | Genuinely independent OS processes via `multiprocessing` `spawn`, synchronized by a shared barrier so both processes contend on the same ledger and seed store. Thread coverage is retained only as `TestThreadRegressions` and is explicitly not the concurrency acceptance proof. |
| Registry outage and replay | Pass | Both a simulated outage (`TestRegistryFailureIsTruthful`) and a real one against a port with nothing listening, then recovery against the real service (`test_real_service_outage_then_recovery_replays_once`). |
| Approved-artifact immutability | Pass | `TestApprovedArtifactsAreImmutable`, including the approve-between-probe-and-write race. |

## Validation

Commands run in this task worktree with `/home/lupin/pantheon/.venv/bin/python3`:

- `pytest services/source_ingestion/tests/test_l12_dist_001_transactional_distillation.py` — 22 passed.
- `pytest services/source_ingestion` — 752 passed, 2 skipped.
- `pytest services/registry services/research/strategy_spec` — 229 passed
  (regression cover for the shared `JsonlRegistryStore` change and the
  conversion path).
- Multiprocess seed test repeated 3× for stability — passed each time.
- Negative control: the same two-process scenario with the JSONL lease stubbed
  out lost 1–2 of 40 seeds on every run, confirming the proof is not vacuous.

## Composition boundary

This task owns the distillation worker, its controller's Registry sink, and the
shared JSONL registry store's read-modify-write safety. It does not change seed
materialization semantics, the Registry HTTP contract, or the source ingestion
controller surface owned by `L12-SRC-001`, the allowed overlap task.

This packet does not claim hosted deployment, a real external crawl, live
broker or capital authority, or any approval-gate change.
