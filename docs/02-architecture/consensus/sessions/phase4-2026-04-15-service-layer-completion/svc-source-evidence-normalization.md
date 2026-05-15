# SVC-SOURCE-EVIDENCE-NORMALIZATION Evidence Note

Task: `SVC-SOURCE-EVIDENCE-NORMALIZATION`
Owner: `Codex2`
Reviewer: `Claude`
Updated: 2026-04-30

## Delivered Contract

- `SourceRecord` persistence now receives canonical source/evidence metadata before evidence ownership is written:
  - `canonical_url`
  - `canonical_doi`
  - `canonical_repo`
  - `content_hash`
  - `source_dedupe_key`
  - `license_scope`
  - `access_scope`
- `EvidenceItem` persistence now receives deterministic evidence ownership metadata:
  - `evidence_dedupe_key`
  - `evidence_owner_id`
  - `source_owner_id`
- JSONL and optional Postgres evidence repositories maintain in-memory dedupe indexes for source and evidence ownership. Duplicate source/evidence writes resolve to the first persisted owner instead of creating competing canonical owners.
- Source ingestion preserves API compatibility for fetched/raw records while writing normalized evidence and knowledge-object refs to the owner repository.

## Task-Owned Artifacts

- `services/knowledge/evidence/normalization.py`
- `services/knowledge/evidence/repository.py`
- `services/knowledge/evidence/__init__.py`
- `services/source_ingestion/main.py`
- `services/source_ingestion/pg_store.py`
- `services/knowledge/evidence/tests/test_normalization.py`
- `services/source_ingestion/test_service.py`

## Verification

```bash
python3 -m pytest services/knowledge/evidence services/source_ingestion services/consultation
```

Result: `54 passed`.
