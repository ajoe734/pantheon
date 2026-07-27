# L12-DIST-001 transactional distillation evidence

Owner: Codex2
Reviewer: Codex
Status: owner implementation and acceptance proof ready; independent review pending

This cut scanned through authoritative task-state journal sequence 2976. The
canonical row at that boundary reports `in_progress`, owner `Codex2`, reviewer
`Codex`; later journal events are outside this owner cut.

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
  either a stale token or an already-expired token is rejected rather than
  silently applied.
- **Truthful Registry delivery.** A job is acknowledged only after a terminal
  Registry readback. Write failure parks a durable retry with backoff and
  dead-letters after the attempt budget, and the controller tick raises with
  stage `registry_sync` instead of recording success.
- **Immutable artifacts.** A Registry entry in `approved` or `retired` state is
  never rewritten. StrategySpec registration uses the Registry's atomic
  create-if-absent path, and terminal readback (not the write response) decides
  the outcome, so an approval landing after the controller's probe but before
  its POST still blocks mutation.
- **Committed replay input.** Versioned jobs always load their source snapshot
  from the transactionally committed version ledger. The caller-provided
  source map remains only as a compatibility path for legacy unversioned jobs.

## Defects this work found and fixed

All seven were found or reproduced through acceptance regressions.

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

4. **An expired worker could still commit a terminal result.** Terminal
   updates checked the lease token and `leased` status but not
   `lease_expires_at`. Before another worker reclaimed the job, an already
   expired owner could still write `done`, `failed`, `skipped`, or a terminal
   dead letter. Every claimed terminal transition now fails closed at the
   expiry boundary as well as on token mismatch.

5. **An intervening revision changed replay materialization identity.** The
   first version of a source used an unversioned evidence bundle while it was
   the only version, then switched to a digest-keyed bundle if a later
   revision arrived before outage replay. The same job could therefore create
   a second seed identity. A versioned job now always derives its evidence
   item, bundle, and seed lineage from its admission-time source digest.

6. **The Registry immutability probe had a TOCTOU overwrite window.** The
   controller first read the stable Registry id and then issued a
   non-conditional POST. If another actor created and approved that id between
   the two requests, the POST replaced the approved entry with a draft. The
   StrategySpec facade now calls the Registry's atomic `register_if_absent`
   operation; the controller's POST receives the existing entry unchanged and
   its terminal readback marks the job skipped as immutable. Both an in-memory
   race and the real HTTP Registry service reproduce approval after the GET but
   before the controller POST.

7. **A caller map could replace a committed version's source payload.** When
   the ledger held v1 and v2 for one source id, `run_pending()` preferred the
   caller's source-id map. If that map contained v2, the v1 job was distilled
   from v2 content even though its job digest and lineage named v1. Versioned
   jobs now always use `source_for_job(job)`; a two-version regression proves
   each delivered source digest equals the digest of the snapshot processed.

## Acceptance evidence

All proofs live in
`services/source_ingestion/tests/test_l12_dist_001_transactional_distillation.py`,
one class per acceptance criterion.

| Acceptance | Result | Evidence |
|---|---|---|
| Committed normalized SourceRecord transactionally enqueues one versioned job | Pass | `TestVersionedAdmission` — duplicate admission yields one job and one version row; the job carries its own committed snapshot; a contradicting payload under the same identity rolls back the whole admission |
| Concurrent workers claim with lease, revised content handled by digest | Pass | `TestConcurrentWorkers` — two spawned OS processes claim disjoint jobs, and a barrier-synchronized two-process run distills 40 sources to 40 distinct seeds with both workers demonstrably contending; stale and expired lease terminal transitions are rejected; revised content is a distinct version while an identical re-crawl is not; v1 and v2 jobs ignore a caller map pointing only at v2 and each process its own committed snapshot |
| Registry failure records controller failure and durable retry or DLQ | Pass | `TestRegistryFailureIsTruthful` — the tick raises at stage `registry_sync`, records no success, parks a durable retry, dead-letters with a redrivable payload after the attempt budget, replays to `done` on recovery, and preserves the first job's seed identity when a revision arrives during the outage |
| Approved immutable artifacts remain unchanged | Pass | `TestApprovedArtifactsAreImmutable` — an approved entry is never written, approval landing after the initial probe but before the atomic create is returned unchanged, approval after a new write but before readback still blocks completion, and an accepted seed draft is not overwritten |
| Crash before or after Registry write replays to one terminal draft | Pass | `TestCrashReplay` — a crash before the write replays after lease expiry to one draft; a lost acknowledgement after a landed write replays by readback without a second Registry entry; repeated ticks stay idempotent |

## Proof required by the task packet

| Proof | Result | How |
|---|---|---|
| Real source-to-registry service test | Pass | `TestRealSourceToRegistryService` serves the real `services/registry` FastAPI app over real HTTP on a real port and drives a full controller tick through it. Source lineage (`source_id`, `source_digest`, `source_event_version`) is read back over HTTP from the registered entry, and a second real-service proof creates and approves the stable id after the controller GET but before its POST and verifies approval survives. No HTTP mock is used in this section. |
| Two-worker and revised-content test | Pass | Genuinely independent OS processes via `multiprocessing` `spawn`, synchronized by a shared barrier so both processes contend on the same ledger and seed store. A separate v1/v2 regression supplies only v2 in the caller map and verifies both job digests still match their processed committed snapshots. Thread coverage is retained only as `TestThreadRegressions` and is explicitly not the concurrency acceptance proof. |
| Registry outage and replay | Pass | Both a simulated outage (`TestRegistryFailureIsTruthful`) and a real one against a port with nothing listening, then recovery against the real service (`test_real_service_outage_then_recovery_replays_once`). The intervening-revision regression proves the original job reuses the same digest-keyed bundle and seed identity. |
| Approved-artifact immutability | Pass | `TestApprovedArtifactsAreImmutable`, including the exact approve-after-GET-before-POST race, plus the same race through the real Registry HTTP service and a direct facade regression proving same-id registration cannot overwrite approval. |

## Validation

Commands rerun in this task worktree with the checkout-scoped
`.venv-pantheon/bin/python3` after merging `origin/dev`
`7f545a33bf41e5682dc67f50333c84b42f09d17e` at local merge
`a1aca8d522227e364bbafaf2bc789f319f93d566`:

- `pytest services/source_ingestion/tests/test_l12_dist_001_transactional_distillation.py services/source_ingestion/tests/test_distillation_worker.py services/source_ingestion/tests/test_distillation_controller.py` — 65 passed.
- `pytest services/source_ingestion` — 757 passed with 2 skipped.
- `pytest services/registry services/research/strategy_spec` — 230 passed
  (including the direct same-id approved StrategySpec regression).
- Multiprocess seed test repeated 3× for stability — passed each time.
- ProductEvidence schema, companion checksum, ten-rule evidence validator,
  commit-trailer check, and `git diff --check` — passed on the manifest
  follow-up that names this receipt.
- Closeout truth replay — failed closed only on the expected open independent
  review risk and missing reviewer verdict; no owner-evidence gap remained.
- Negative control: the same two-process scenario with the JSONL lease stubbed
  out lost 1–2 of 40 seeds on every run, confirming the proof is not vacuous.

## Composition boundary

This task owns the distillation worker, its controller's Registry sink, the
narrow StrategySpec facade change from overwriting register to atomic
create-if-absent, and the shared JSONL registry store's read-modify-write
safety. It does not change seed schema or materializer API, Registry lifecycle
transitions, or the source ingestion controller surface owned by
`L12-SRC-001`, the allowed overlap task.

This packet does not claim hosted deployment, a real external crawl, live
broker or capital authority, or any approval-gate change.
