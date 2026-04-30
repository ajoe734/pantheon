# Review Packet: SVC-SOURCE-SEARCH-AUTONOMOUS-CONNECTOR-INDEXER

**Sidecar Kind:** review_packet
**Parent Task:** SVC-SOURCE-SEARCH-AUTONOMOUS-CONNECTOR-INDEXER
**Sidecar ID:** SVC-SOURCE-SEARCH-AUTONOMOUS-CONNECTOR-INDEXER-SIDECAR-REVIEW
**Prepared By:** Claude2
**Reviewer:** Claude
**Status:** done (review_approved → closeout finalized by Claude2)
**Parent Task Status:** done (terminal_outcome: completed)
**Prepared At:** 2026-04-30

---

## 1. Parent Task Summary

**Title:** Add autonomous connector and index refresh baseline for source search

**Objective (zh):** 把 source-ingest/search 從 bounded external_feed + query-time durable reload 推進到 autonomous connector/indexer baseline：scheduled ingest、bounded connector adapter、materialized index refresh、DLQ/replay 與 smoke 驗證。

**Owner:** Claude  
**Reviewer:** Codex2  
**Final Commit:** `7c4a924` (scheduled connector + materialized index baseline)  
**Closeout Commit:** `fe03838` (closeout note, phase4 gap-inventory section 14)  
**Branch:** `backend-dev-publish-20260429`

---

## 2. Acceptance Criteria — Evidence Map

| # | Acceptance Criterion | Status | Evidence |
|---|---|---|---|
| 1 | source-ingest has scheduled bounded connector execution (not only caller-triggered external_feed) | PASS | `PUT/GET /api/source-ingest/connectors/{id}/schedule` + `POST /api/source-ingest/run-scheduled` implemented. Watermark-driven due-time check, interval_seconds-based scheduling. |
| 2 | search has materialized index refresh path (not only query-time reload) | PASS | `POST/GET /api/search/index/materialize` implemented. `JsonlMaterializedIndexStore` appends snapshots. Reload and materialize are independent paths. |
| 3 | watermark DLQ replay and max_records/bytes/timeout guards remain enforced | PASS | Existing `IngestionScheduler.run_once()` enforces `MAX_RECORDS_PER_JOB`; DLQ routing preserved on scheduled path; watermarks updated after each run. |
| 4 | request-document compat remains quarantined | PASS | `/api/search/query/request-documents-compat` still gated behind `allow_request_documents_compat` flag; quarantine not loosened. |
| 5 | autonomous ingest index smoke passes | PASS | `scripts/smoke_honest_stack.py` updated; `docker compose config --quiet → exit 0`. |
| 6 | focused source/search tests and compose config pass | PASS | `python3 -m pytest services/source_ingestion/ services/search/ → 64 passed`. |

**All 6 acceptance criteria: PASS.**

---

## 3. Implementation Evidence

### 3.1 Source Ingest — New Scheduled Connector Baseline

**New classes (services/source_ingestion/configured.py):**
- `ConnectorScheduleConfig` — dataclass: `connector_id`, `interval_seconds`, `enabled`, `updated_at`; schema `connector_schedule_config.v1`
- `JsonlConnectorScheduleStore` — append-only JSONL store; methods: `reload()`, `upsert_schedule()`, `get_schedule()`, `list_schedules()`

**New HTTP endpoints (services/source_ingestion/main.py):**

| Method | Route | Function |
|---|---|---|
| PUT | `/api/source-ingest/connectors/{connector_id}/schedule` | Upsert connector schedule config |
| GET | `/api/source-ingest/connectors/{connector_id}/schedule` | Retrieve current schedule |
| POST | `/api/source-ingest/run-scheduled` | Run all due scheduled connectors |

**Execution flow:**
1. Admin configures schedule via PUT endpoint
2. POST `/run-scheduled` loops all enabled schedules, checks elapsed time vs `interval_seconds`
3. Due connectors invoke `IngestionScheduler.run_once()` with bounded fetch
4. Results: evidence refs, DLQ entries, audit actions, updated watermarks

**Persistence:** `/tmp/pantheon/source-ingest/connector_schedule.jsonl`

### 3.2 Search — New Materialized Index Baseline

**New class (services/search/main.py):**
- `JsonlMaterializedIndexStore` — append-only JSONL store; schema `search_materialize_store.v1`; methods: `reload()`, `record_materialize()`, `get_last()`

**New HTTP endpoints:**

| Method | Route | Function |
|---|---|---|
| POST | `/api/search/index/materialize` | Snapshot current durable index to materialized store |
| GET | `/api/search/index/materialize` | Retrieve last materialized state (404 if none) |

**Adapter states:**
- `durable` — live reload from evidence repo (existing query-time refresh path)
- `materialized` — persisted snapshot (new batch refresh path)
- `request_documents_compat` — ephemeral from request docs (quarantined)

**Persistence:** `/tmp/pantheon/search/search-materialize.jsonl`

### 3.3 Test Coverage

**Source ingestion (services/source_ingestion/tests/test_scheduled_connector.py — 8 tests):**
- `test_set_and_get_connector_schedule` — PUT/GET roundtrip
- `test_schedule_replays_after_reload` — JSONL durability across restarts
- `test_run_scheduled_runs_due_connector_and_persists_evidence` — execution + evidence builder
- `test_run_scheduled_skips_disabled_connector` — disabled connector filtering
- `test_run_scheduled_skips_not_due_connector` — interval-elapsed filtering
- `test_set_schedule_returns_404_for_unknown_connector` — unknown connector error
- `test_get_schedule_returns_404_when_not_configured` — no-config error
- `test_run_scheduled_returns_empty_when_no_schedules_configured` — empty result

**Search (services/search/tests/test_materialized_index.py — 6 tests):**
- `test_materialize_index_persists_state` — POST creates JSONL + returns state
- `test_get_materialized_index_replays_last_state` — GET retrieves last entry
- `test_get_materialized_index_returns_404_when_not_yet_materialized` — 404 guard
- `test_materialize_index_is_durable_and_replayable` — persists across restarts
- `test_materialize_returns_materialized_adapter_state` — confirms `adapter_state: "materialized"`
- `test_query_reload_and_materialize_are_independent_paths` — reload/materialize independence

**Total across all suites:** 64 passed (source_ingestion ≈27, search ≈27, existing shared tests ≈10)

### 3.4 Verification Commands (from archive)

```bash
python3 -m pytest services/source_ingestion/ services/search/
# Result: 64 passed

docker compose config --quiet
# Result: exit 0
```

---

## 4. Dependencies — Status Check

| Dependency | Status | Notes |
|---|---|---|
| SVC-POSTGRES-PRODUCTION-OWNERSHIP-WAVE2 | done | Postgres ownership baseline established; parent task builds on it |
| SVC-SOURCE-INGEST-EXTERNAL-FETCH-BASELINE | done | Bounded external_feed mode was prerequisite; scheduled connector extends this |
| SVC-SEARCH-DURABLE-COMPAT-QUARANTINE | done | Request-doc compat quarantine was prerequisite; preserved in this task |

All three dependencies are `done`. No unresolved blockers.

---

## 5. Canonical Scope Boundary — What Was NOT Changed

This task is an execution slice. The following canonical truth files were **not modified**:

- `TARGET_ARCHITECTURE.md`
- `OPENCLAW_RUNTIME_CONTRACT.md`
- `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`
- `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`
- `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`
- Any L1 policy document

The implementation added a bounded autonomous baseline only:
- Source-ingest is **not** promoted to production crawler; allowlist bounds remain enforced
- Request-document compatibility quarantine is **not** removed
- Advanced indexer or full-pipeline crawler is **out of scope** for this task

Architecture documentation was updated only in the task-scoped gap-inventory section 14 (commit fe03838), which is a task record, not canonical truth.

---

## 6. Quality Notes (from Codex2 Review)

From `review_notes_zh` in the task archive:

1. Reviewed `7c4a924` and working tree scope for source_ingestion/search/smoke — scheduled connector, watermark due check, DLQ/replay, bounded fetch guard, request-doc compat quarantine, materialized index append/replay all match acceptance criteria.
2. Verification passed: `python3 -m pytest services/source_ingestion/ services/search/ → 64 passed`; `docker compose config --quiet → exit 0`.
3. Note: working tree contains unrelated orchestrator/archive dirty files; they did not affect task-touched artifacts. Owner separated these during closeout per closeout spec.

---

## 7. Open Items / Reviewer Notes

There are **no open items** on this parent task — it is `done` with `terminal_outcome: completed`.

The following items were explicitly deferred as out-of-scope for this task (from gap-inventory section 14):
- Production-grade source crawler beyond bounded allowlist
- Full pipeline integration with live market data connectors
- Advanced search indexer with incremental diff strategies

These belong to future tasks, not this slice.

**Reviewer guidance for Claude:**
- This review packet is a support artifact only; it does not modify any canonical document
- The parent task archive (`ai-task-archive/tasks/SVC-SOURCE-SEARCH-AUTONOMOUS-CONNECTOR-INDEXER.json`) is the durable delivery record
- Sidecar review approval should confirm: (a) packet is accurate relative to the archive and implementation, (b) scope boundary is correctly stated, (c) evidence summary is sufficient for the parent task's acceptance
- No re-review of the parent task implementation is required; this packet summarizes what Codex2 already approved

---

## 8. Artifact Index

| Artifact | Type | Path |
|---|---|---|
| Source ingest main (routes) | Implementation | `services/source_ingestion/main.py` (L636–L712) |
| Connector schedule store | Implementation | `services/source_ingestion/configured.py` (L17–L120) |
| Scheduler | Implementation | `services/source_ingestion/scheduler.py` |
| Scheduled connector tests | Tests | `services/source_ingestion/tests/test_scheduled_connector.py` |
| Search main (routes + store) | Implementation | `services/search/main.py` (L48–L86, L385–L412) |
| Materialized index tests | Tests | `services/search/tests/test_materialized_index.py` |
| Gap-inventory closeout note | Doc record | `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md` §14 |
| Parent task archive | Archive | `ai-task-archive/tasks/SVC-SOURCE-SEARCH-AUTONOMOUS-CONNECTOR-INDEXER.json` |
| This review packet | Support artifact | `support/sidecars/SVC-SOURCE-SEARCH-AUTONOMOUS-CONNECTOR-INDEXER/SVC-SOURCE-SEARCH-AUTONOMOUS-CONNECTOR-INDEXER-SIDECAR-REVIEW.md` |

---

*Prepared by Claude2 as support sidecar for SVC-SOURCE-SEARCH-AUTONOMOUS-CONNECTOR-INDEXER. This file is a support artifact only — do not use it to override canonical task or architecture state.*
