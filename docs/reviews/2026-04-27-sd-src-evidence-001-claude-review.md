# SD-SRC-EVIDENCE-001 Claude Review

Task: `SD-SRC-EVIDENCE-001` - Upgrade governed source evidence search slice
Owner: Codex
Reviewer: Claude
Status decision: APPROVE

## Scope Reviewed

The first governed `SD-03` slice that introduces:

- `services/source_ingestion/` for `SourceConnector`, `SourceRecord`, and a
  validated `IngestRun` lifecycle.
- `services/knowledge/evidence/` for `EvidenceItem`, `EvidenceBundle`,
  `KnowledgeObject`, an in-memory repository, and a bundle builder.
- `services/search/` for governed pre-ranking filters, deterministic keyword
  retrieval, and cited `EvidenceBundle` results.
- `integrations/openclaw/search_gateway.py` for an OpenClaw-facing scoped
  search that returns cited evidence-bundle refs only.
- JSON contracts under `docs/contracts/` for source connector, evidence
  bundle, knowledge object, and search request shapes.
- BFF RW-02 internals routed through `SearchGateway`, with replayable
  `meta.governed_evidence` exposed alongside the existing `data` shape.

Per the materialization packet
(`docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md`), this
task owns the first SD-03 governed source/evidence/search slice only.
Durable storage, vector retrieval, full ingestion scheduling, and broader
SD-01/SD-09 lineage closure remain explicitly out of scope.

## Acceptance Verification

| Acceptance target | Evidence | Result |
|---|---|---|
| Source connectors carry governed identity (type, provider, license, mode, auth, status) | `SourceConnector` enforces required fields and normalizes enum values at `services/source_ingestion/connectors/base.py:101-139`; covered by `test_connector_requires_source_type_provider_and_license_scope` | PASS |
| Ingest lifecycle validates transitions and emits events | `IngestRun.transition` enforces `_ALLOWED_TRANSITIONS` and emits `IngestRunQueued/Started/SourceNormalizingStarted/EvidenceIndexingStarted/IngestRunCompleted` (`services/source_ingestion/connectors/base.py:222-289`); covered by `test_ingest_run_lifecycle_emits_start_and_completion_events` and `test_ingest_run_rejects_invalid_transition` | PASS |
| Evidence bundles refuse rejected sources and require provenance | `EvidenceBundleBuilder.build_bundle` blocks rejected sources, requires both source records and evidence items, propagates citation refs / trace refs / access scope, and registers everything in the repository (`services/knowledge/evidence/bundle_builder.py:14-82`); covered by `test_bundle_builder_persists_citation_refs_and_trace_refs` and `test_rejected_source_cannot_be_used_in_bundle` | PASS |
| Knowledge objects link back to source / evidence item / evidence bundle | `KnowledgeObject` requires all three IDs (`services/knowledge/evidence/models.py:178-232`); repository rejects orphan IDs (`services/knowledge/evidence/repository.py:41-50`); covered by `test_knowledge_object_links_back_to_evidence_bundle` | PASS |
| Search applies ACL / license / environment / source-type / persona / workspace filters before ranking | `SearchGateway.search` filters `requested_source_types`, calls `SearchAccessContext.permits` (license, access scope, environment, persona, workspace), and only then runs the `KeywordRetriever` (`services/search/gateway.py:74-105`; `services/search/filters.py:60-97`); covered by `test_search_returns_cited_evidence_bundle_refs` (rejects the restricted `ko-private` knowledge object before ranking) | PASS |
| Search results expose evidence-bundle refs, citations, matched item refs, and rejected counts | `RetrievalResult` carries `evidence_bundle_id`, `matched_items[]`, `citations`, `filters_applied`, and `rejected_items_count`, enforced by `SearchGateway.search` looking up the bundle/evidence item per match (`services/search/gateway.py:106-142`); same shape exposed via `OpenClawSearchGateway.search` (`integrations/openclaw/search_gateway.py:20-60`) | PASS |
| OpenClaw adapter rejects unscoped persona/workspace requests before retrieval | `OpenClawSearchGateway.search` calls `context.require_persona_workspace()` which raises `SearchPolicyError` on missing scope (`integrations/openclaw/search_gateway.py:28-30`; `services/search/filters.py:73-75`); covered by `test_openclaw_search_requires_persona_and_workspace_scope` | PASS |
| OpenClaw response carries cited evidence, not raw blobs | Adapter returns `evidence_bundle_id`, `citations`, `matched_items`, `answer_context`, and `relevance_score`; never returns `raw_payload`; covered by `test_openclaw_search_returns_evidence_not_raw_undocumented_blob` | PASS |
| BFF RW-02 consumes governed truth and preserves response `data` shape | `ReadSurfaceStore.list_research_search_results` builds an in-memory `EvidenceBundleBuilder`, projects through `SearchGateway`, stores `_last_governed_search_refs`, and replays them via `meta.governed_evidence` only - the per-item `data` shape (`result_id`, `match_type`, `title`, `excerpt`, `linked_ticket_id`, `relevance_score`, `links`) is unchanged (`services/control-plane/bff/read_store.py:7280-7491`); main.py adds `meta["governed_evidence"]` only when refs exist (`services/control-plane/bff/main.py:7717-7722`); covered by `test_rw02_search_contract_returns_ranked_projection_and_index_adapter_meta` | PASS |
| Contract schemas validate model payloads | JSON Schemas under `docs/contracts/{source_connector,evidence_bundle,knowledge_object,search_request}.schema.json` validated against `to_dict()` outputs in `services/search/tests/test_contracts.py::test_sd03_contract_schemas_accept_model_payloads` | PASS |
| Targeted suite passes | `PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages python3 -m pytest -p no:cacheprovider services/source_ingestion/tests services/knowledge/evidence/tests services/search/tests services/control-plane/bff/test_rw02_search_contract.py services/control-plane/bff/test_kw03_evidence_refs_contract.py -q` -> `18 passed in 3.03s` (rerun during review) | PASS |

## Boundary And Scope

The implementation stays within the SD-SRC-EVIDENCE-001 boundary set by the
materialization packet:

- governance of source / evidence / search lives in dedicated services rather
  than being smeared into BFF read-model code
- BFF RW-02 still owns its dataset and only borrows the `SearchGateway` to
  filter and rank already-eligible documents; the existing `data` item shape
  and `meta.surfaces` / `meta.index_adapter` keys are unchanged
- `meta.governed_evidence` is replayable: every key is the existing
  `result_id`, and every value points to a recreated `evbundle-rw02-<result_id>`
  with citation labels and matched-item refs that BFF replay can verify
- OpenClaw integration is a one-way governed adapter that returns evidence
  refs only; it does not let OpenClaw write source/evidence truth back into
  Pantheon
- the slice is explicitly in-memory; no durable index, no vector store, and no
  scheduler are introduced or claimed
- no SD-01 / SD-09 lineage trace closure is asserted, no live / canary
  execution claim is made, and no research-activation promotion happens

## Observations (Non-Blocking)

Notes for follow-up tasks; none block the slice:

- `ReadSurfaceStore._build_research_search_repository` rebuilds the
  `EvidenceBundleBuilder` on every call to `list_research_search_results`.
  That is fine for a first slice and avoids stale repository state, but a
  later durable backing store will want to memoize bundles by
  `result_id` / source watermark to avoid rebuilding on every search.
- `KeywordRetriever` reads `metadata["search_text"]` and
  `metadata["relevance_score"]` to preserve BFF-side scoring during the
  RW-02 cutover. When the durable index lands, those fallbacks should move
  out of the retriever and into the index adapter.
- `EvidenceBundle.confidence` is taken as `min(item.confidence)` unless an
  explicit override is supplied. This is conservative but means any single
  low-confidence evidence item drags the bundle down; once `SD-RECON-001`
  defines reconciliation evidence semantics, the bundle confidence policy may
  need to be revisited.
- `SearchAccessContext.permits` treats an empty `environment_scope` on a
  knowledge object as "all environments allowed". This is acceptable for the
  first slice, but the durable knowledge-object writer should always populate
  the scope rather than relying on the default.

## Decision

Approve `SD-SRC-EVIDENCE-001`.

The governed source / evidence / search slice meets the Source / Evidence /
Search acceptance shape in
`docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md`: source
connectors produce evidence bundles with provenance and trace refs, search
results point back to governed evidence rather than raw blobs, and BFF RW-02
now consumes this truth through `SearchGateway` while preserving the existing
response `data` shape. Downstream work (`SD-RECON-001`,
`EP5-002-PACKET-PREP-001`, `CROSS-REPO-SD-VERIFY-001`) can now consume cited
`evidence_bundle_id` refs without inventing shadow payloads.

## Verification Reproduction

```text
PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages \
  python3 -m pytest -p no:cacheprovider \
    services/source_ingestion/tests \
    services/knowledge/evidence/tests \
    services/search/tests \
    services/control-plane/bff/test_rw02_search_contract.py \
    services/control-plane/bff/test_kw03_evidence_refs_contract.py -q
..................                                                       [100%]
18 passed in 3.03s
```

## Handoff Back To Owner

Task returns to `Codex` for finalization to `done` per the standard
review_approved -> done lifecycle.
