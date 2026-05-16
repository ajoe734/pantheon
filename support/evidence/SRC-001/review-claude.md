# SRC-001 Review — Claude

Task: SourceRecord schema + ingest API
Reviewer: Claude
Owner: Codex
Date: 2026-05-16

## Review Decision

**APPROVED** — all scope items delivered, tests pass, governance boundary correctly enforced.

## Scope Verified

- `source_record.schema.json`: JSON Schema Draft 7. Required fields (`source_id`, `connector_id`, `source_type`, `title`, `content_ref`, `status`, `metadata`, `trace_id`, `created_at`) match `SourceRecordBody`. `source_type` enum exactly matches `SourceType` Python enum (10 values). `status` enum exactly matches `SourceRecordStatus` (5 values). `additionalProperties: false` at top level enforces strict boundary. Schema `description` correctly labels this "research evidence only; not a runtime execution route."
- `GET /api/source-ingest/schemas/source-record`: clean readback of schema file, no transformation.
- `POST /api/source-ingest/source-records`: delegates to shared `_run_ingest_request` after non-empty records guard; reuses existing governed lifecycle (connector validation, evidence normalization, persistence, watermarks, DLQ/audit, search refresh). Mismatch rejection (record `source_type` != connector `source_type`) confirmed by test.
- `GET /api/source-ingest/source-records/{source_id}`: readback returns schema-conformant record with `status=normalized`, `source_dedupe_key`, `content_hash`.
- `test_src001_source_record_contract.py`: three contract tests covering schema structure, full ingest lifecycle with readback (including optional jsonschema validation), and rejection cases.

## Verification Run

```
python3 -m py_compile services/source_ingestion/main.py services/source_ingestion/connectors/base.py services/source_ingestion/test_src001_source_record_contract.py
→ PASSED

python3 -m pytest services/source_ingestion/test_src001_source_record_contract.py -q
→ 3 passed in 9.02s

python3 -m pytest services/source_ingestion/test_service.py::test_trigger_success_persists_run_and_watermark_for_replay -q
→ 1 passed in 14.94s

python3 -m json.tool services/source_ingestion/source_record.schema.json
→ PASSED
```

## Notes

No blockers. The facade pattern (SourceRecordIngestRequest delegating to TriggerIngestJobRequest) is clean and keeps the new endpoint bounded. The `SourceRecordBody.to_domain()` omits `created_at`, which is correct — the domain SourceRecord sets its own creation timestamp.
