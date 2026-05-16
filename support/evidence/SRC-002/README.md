# SRC-002 Evidence

Task: paper ingest adapter skeleton
Owner: Codex
Reviewer: Claude

## Delivered Scope

- Added `OpenAlexPaperIngestAdapter` for governed academic-paper ingestion.
- Exposed bounded OpenAlex connector/fetch policy through the source-ingest provider catalog.
- Added pure OpenAlex work payload to `SourceRecord` normalization with DOI, event/available time, authors, venue, abstract/body, access/license, and governance metadata.
- Added execution-route rejection for paper adapter metadata so paper evidence cannot target Lean, broker, runtime, or order-routing paths.
- Documented the paper adapter skeleton in `docs/deployment/source-connector-framework.md`.

## Task-Owned Files

- `services/source_ingestion/connectors/paper.py`
- `services/source_ingestion/tests/test_paper_ingest_adapter.py`
- `docs/deployment/source-connector-framework.md`
- `services/source_ingestion/connectors/__init__.py`
- `services/source_ingestion/connectors/examples.py`

Note: `services/source_ingestion/connectors/__init__.py` and
`services/source_ingestion/connectors/examples.py` also contain pre-existing
repo allowlist provider changes from adjacent SRC-003 work. SRC-002-owned
hunks are the `OpenAlexPaperIngestAdapter` export and provider-catalog swap.

## Verification

```text
python3 -m py_compile services/source_ingestion/connectors/paper.py services/source_ingestion/connectors/examples.py services/source_ingestion/connectors/__init__.py
passed

python3 -m pytest -q services/source_ingestion/tests/test_paper_ingest_adapter.py
3 passed in 2.09s

python3 -m pytest -q services/source_ingestion/tests/test_connector_framework.py services/source_ingestion/test_service.py::test_registry_exposes_connector_status_policy_and_provider_examples
4 passed in 7.89s

python3 -m pytest -q services/source_ingestion
73 passed in 94.09s (0:01:34)
```
