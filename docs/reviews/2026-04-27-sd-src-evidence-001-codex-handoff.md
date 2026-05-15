# SD-SRC-EVIDENCE-001 Codex Handoff

Status: ready for Codex2 review
Task: `SD-SRC-EVIDENCE-001`
Owner: Codex
Reviewer: Codex2
Date: 2026-04-27

## Scope Delivered

- Added first governed SD-03 source/evidence/search slice:
  - `services/source_ingestion/` for `SourceConnector`, `SourceRecord`, and validated `IngestRun` lifecycle events.
  - `services/knowledge/evidence/` for `EvidenceItem`, `EvidenceBundle`, `KnowledgeObject`, repository, and bundle builder.
  - `services/search/` for pre-ranking governed search filters, keyword retrieval, and cited `EvidenceBundle` results.
  - `integrations/openclaw/search_gateway.py` for OpenClaw-facing scoped search that returns cited evidence bundle refs.
- Added JSON contract schemas under `docs/contracts/`:
  - `source_connector.schema.json`
  - `evidence_bundle.schema.json`
  - `knowledge_object.schema.json`
  - `search_request.schema.json`
- Updated RW-02 BFF search internals so `ReadSurfaceStore.list_research_search_results()` builds a governed in-memory evidence repository and searches via `SearchGateway`.
- Preserved the existing RW-02 `data` item shape while adding `meta.governed_evidence` with replayable `evidence_bundle_id`, citations, and matched item refs.

## Acceptance Mapping

- Source connectors produce governed provenance:
  - `SourceConnector` requires source type, provider, license scope, supported mode, auth type, and status.
  - `IngestRun` validates `queued -> fetching -> normalizing -> indexing -> completed` and emits lifecycle events.
- Evidence bundles are governed and replayable:
  - `EvidenceBundleBuilder` rejects rejected sources, requires evidence items, preserves citation refs, trace refs, license scope, and access scope.
  - `KnowledgeObject` links back to `source_id`, `evidence_item_id`, and `evidence_bundle_id`.
- Search results point to governed evidence:
  - `SearchGateway` filters ACL/license/environment/source type before ranking.
  - `RetrievalResult` exposes `evidence_bundle_id`, citations, matched item refs, and rejected item count.
  - OpenClaw adapter rejects unscoped persona/workspace requests.
- BFF consumes governed truth:
  - RW-02 search now projects from `SearchGateway` output and exposes `meta.governed_evidence` instead of only shadow search documents.

## Verification

```bash
python3 -m pytest \
  services/source_ingestion/tests \
  services/knowledge/evidence/tests \
  services/search/tests \
  services/control-plane/bff/test_rw02_search_contract.py \
  services/control-plane/bff/test_kw03_evidence_refs_contract.py \
  -q
```

Result: `18 passed in 8.07s`

Additional syntax check:

```bash
python3 -m compileall -q services/source_ingestion services/knowledge services/search integrations/openclaw/search_gateway.py
```

Result: passed.

## Notes For Review

- This is deliberately an in-memory first slice, not a full vector DB or durable evidence store.
- Existing RW-02 response `data` shape is preserved; the new governed evidence refs are added under response metadata.
- `services/control-plane/bff/main.py` already had unrelated dirty changes before this task. The SD-SRC-EVIDENCE-001 change in that file is limited to attaching `meta.governed_evidence` after RW-02 search.
