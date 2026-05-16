# SRC-001 Evidence

Task: SourceRecord schema + ingest API
Owner: Codex
Reviewer: Claude

## Delivered Scope

- Added canonical `SourceRecord` JSON schema at `services/source_ingestion/source_record.schema.json`.
- Exposed schema readback through `GET /api/source-ingest/schemas/source-record`.
- Added `POST /api/source-ingest/source-records` for inline SourceRecord ingest using the existing governed source-ingest lifecycle.
- Kept `/api/source-ingest/jobs` behavior on the same ingest path, so connector validation, evidence normalization, persistence, watermarks, DLQ/audit handling, and search refresh notification remain shared.
- Added SourceRecord list/readback contract coverage for persisted schema-conformant records and mismatch rejection.

## Task-Owned Files

- `services/source_ingestion/main.py`
- `services/source_ingestion/source_record.schema.json`
- `services/source_ingestion/test_src001_source_record_contract.py`
- `support/evidence/SRC-001/README.md`
- `support/evidence/SRC-001/review-claude.md`

## Verification

```text
python3 -m py_compile services/source_ingestion/main.py services/source_ingestion/connectors/base.py services/source_ingestion/test_src001_source_record_contract.py
passed

python3 -m pytest services/source_ingestion/test_src001_source_record_contract.py -q
3 passed in 10.87s

python3 -m pytest services/source_ingestion/test_service.py::test_trigger_success_persists_run_and_watermark_for_replay -q
1 passed in 5.44s

python3 -m json.tool services/source_ingestion/source_record.schema.json >/dev/null
passed

git diff --check -- services/source_ingestion/main.py
passed
```

## Worktree Boundary

The worktree already contains unrelated dirty files from adjacent orchestrator
tasks. SRC-001-owned changes are limited to the files listed above.
