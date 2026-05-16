# SRC-002 Review — Claude

Task: paper ingest adapter skeleton
Owner: Codex
Reviewer: Claude
Date: 2026-05-16

## Verdict: Approved

## Scope Verified

- `OpenAlexPaperIngestAdapter` dataclass is frozen, bounded, and validated in `__post_init__`: `max_records`, `max_bytes`, `timeout_seconds`, and `api_url`/`allowed_url_prefixes` are all range-checked on construction.
- `_assert_no_execution_routes` guards both connector metadata and per-work metadata against route keys and forbidden execution values (`lean`, `broker`, `runtime`, `execution`, `live`, etc.).
- `source_record_from_openalex_work` preserves all required normalization fields: DOI, authors (via `authorships` and direct `authors`), publication date, event_time, available_time, venue, open_access, concepts/keywords, abstract/body, body_hash, governance, access_scope, allowed_use.
- `_abstract_from_inverted_index` correctly reconstructs plain-text abstract from OpenAlex inverted-index format.
- `_normalize_doi` handles doi: prefix, doi.org/dx.doi.org URLs, and bare DOI strings.
- `governance` block hard-sets `direct_execution_allowed: False`, `broker_consumption: not_direct_action`, `lean_consumption: research_only_not_direct_action`, `openclaw_consumption: governed_search_only`.
- Provider catalog in `examples.py` swapped to `OpenAlexPaperIngestAdapter` for the `example-openalex-feed` entry; export in `__init__.py` adds `OpenAlexPaperIngestAdapter`.
- `docs/deployment/source-connector-framework.md` updated with paper adapter skeleton note.

## Verification Run

```
python3 -m py_compile services/source_ingestion/connectors/paper.py services/source_ingestion/connectors/examples.py services/source_ingestion/connectors/__init__.py
→ OK

python3 -m pytest -q services/source_ingestion/tests/test_paper_ingest_adapter.py
→ 3 passed

python3 -m pytest -q services/source_ingestion/tests/test_connector_framework.py services/source_ingestion/test_service.py::test_registry_exposes_connector_status_policy_and_provider_examples
→ 4 passed

python3 -m pytest -q services/source_ingestion
→ 73 passed
```

## Notes

- SRC-003 repo allowlist hunks in `__init__.py` and `examples.py` are pre-existing and outside SRC-002 scope; correctly noted.
- No execution route surface; paper evidence cannot reach Lean/broker/runtime paths.
- Returning to Codex for finalization.
