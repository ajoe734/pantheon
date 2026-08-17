# L12-CURRENT-SOURCE-READBACK-20260817 durable terminal readback evidence

Owner: Claude
Reviewer: Antigravity
Status: implementation complete; awaiting independent review

## Outcome

`source.durable_terminal_readback` was failing because of two bugs in the
deployed E2E harness itself
(`tests/integration/l12/test_current_research_loops_deployed_e2e.py`), not in
`services/source_ingestion`. Direct reproduction against both an in-process
FastAPI `TestClient` and the live, already-running `pantheon` compose
deployment (`source-ingest` on `127.0.0.1:18097`) confirmed the production
write and read path (`main.py`, `configured.py`, `scheduler.py`) already
persists and serves a `static_records`-configured connector's job trigger
correctly end to end.

The two harness defects:

1. `GET /api/source-ingest/source-records/{source_id}` returns the
   established `{"source_record": {...}}` envelope, matching every other
   source-ingest read endpoint and `tests/e2e/test_source_to_strategy_spec.py`.
   The E2E poll's accept-predicate compared `source_id` against the
   un-unwrapped top-level dict, so it could never match and the boundary
   always timed out, masking the fact that the record already existed.
2. Once (1) was fixed, a second defect surfaced: the connector payload's
   `metadata.body` was a fixed string, identical across every run. Source
   evidence is content-addressed
   (`services/knowledge/evidence/normalization.py`) by `content_hash` when no
   `canonical_doi`/`repo`/`url` is present, so on the shared, long-lived dev
   compose deployment this suite normally runs against, a second run's fixed
   body text collided with the first run's `SourceRecord` and
   `_persist_source_evidence_refs` correctly attached the new evidence to the
   pre-existing dedupe owner instead of the freshly generated `source_id`.
   The caller-declared `source_id` then never resolved, even though the
   ingest run itself completed successfully.

Neither `scheduler.run_once` idempotency nor configured fetch batch
persistence was defective; both were ruled out by inspecting the completed
run object (`raw_count == normalized_count == 1`, `status == completed`) and
the connector fetch state (`successful_attempts == attempts`, `failed_attempts
== 0`).

## Fix

- Added `DeployedResearchHarness._source_record(view)`, matching the existing
  `_entry(view)` unwrap helper already used for the strategy-spec registry
  boundary, and used it both in the poll accept-predicate and to extract the
  flat `SourceRecord` used by `_source_digest`.
- Made `metadata.body` include `self.run_token` so each run's content hash
  (and therefore dedupe key) is unique, matching the uniqueness already
  applied to `content_ref` and `trace_id`.
- The POST/PUT trigger call sequence (connector configure, schedule, job
  trigger) is unchanged.
- No changes to `services/source_ingestion/main.py`,
  `services/source_ingestion/configured.py`,
  `services/source_ingestion/scheduler.py`, or
  `services/knowledge/evidence/normalization.py`. The content-addressed
  dedupe design is intentional product behavior for genuinely duplicate
  content and is left in place.

## Acceptance evidence

| Acceptance | Result | Evidence |
|---|---|---|
| Job trigger synchronously produces a SourceRecord retrievable at its own source_id | Pass | in-process reproduction + live-compose run, both 200 with the exact posted source_id |
| `source.durable_terminal_readback` passes against the default pantheon compose project | Pass | live-compose test run; `successful_boundaries` includes `source.durable_terminal_readback` |
| Root cause documented (scheduler idempotency / fetch persistence / store identity mismatch) | Pass | store identity mismatch confirmed; the other two candidates ruled out with direct evidence |
| No change to the test's client-facing trigger sequence unless proven wrong | Pass | trigger POST/PUT calls unchanged; only read-side unwrap and one content value changed |

## Validation

- In-process `TestClient` reproduction (jsonl evidence backend): connector
  configure 201, schedule 200, job trigger 201 (`run.status=completed`),
  readback 200 with the exact posted `source_id`.
- Live deployed compose stack, before fix: `source.durable_terminal_readback`
  timed out (envelope mismatch).
- Live deployed compose stack, envelope fix only: `source.durable_terminal_readback`
  timed out with a genuine 404 despite `normalized_count=1` on the completed
  run (dedupe collision against a prior run).
- Live deployed compose stack, both fixes: `successful_boundaries` includes
  `source.owner_compose_identity`, `source.anti_preseed_readback`,
  `source.connector_command`, `source.scheduled_trigger`,
  `source.job_trigger`, `source.durable_terminal_readback`. `first_failure`
  moved on to the later, unrelated `source.authority_actual_state` boundary,
  which times out only because this specific shared 16-hour-uptime compose
  deployment has accumulated 248 connectors and a 1.18MB controller readback
  payload that exceeds the harness's fixed 32768-byte HTTP read buffer. That
  boundary is not named in this task's acceptance criteria and is unrelated
  to source-ingestion write/read persistence; a follow-up task should widen
  `DeployedResearchHarness._http_json`'s read buffer or reset the shared
  environment's accumulated connector state.
- `python3 -c "import ast; ast.parse(...)"` on the modified test file: exit 0.
- `test_deployed_suite_has_no_fixture_or_product_store_shortcut` (the file's
  own AST guard): 1 passed.

## Composition boundary

This task owns the deployed E2E harness's source-ingestion readback boundary
only. It does not change `services/source_ingestion` runtime behavior,
`docker-compose.yml` topology, or the later loop boundaries
(`source.authority_actual_state` onward). The newly exposed
`source.authority_actual_state` read-buffer limitation is recorded above as a
follow-on finding, not claimed as fixed by this task.
