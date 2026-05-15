# SD-SRC-EVIDENCE-001 Sidecar Acceptance Packet

Status: support-only packet ready for reviewer handoff
Task: `SD-SRC-EVIDENCE-001-SIDECAR-ACCEPTANCE`
Parent task: `SD-SRC-EVIDENCE-001`
Helper kind: `acceptance_packet`
Owner: Codex
Reviewer: Codex2
Prepared: 2026-04-27

## Boundary

This packet supports review and parent-owner absorption for
`SD-SRC-EVIDENCE-001`. It does not edit L1 canonical truth, core governance
runtime, registry truth, or deployment policy. Its only artifact is this support
handoff document.

## Source Context

The source maturity assessment marked `SD-03` as Low-Medium because research
ingest, memory, and evidence references existed, but the governed
`SourceConnector` / `EvidenceBundle` / `KnowledgeObject` / `SearchGateway`
plane was still partial.

The materializable task packet scoped `SD-SRC-EVIDENCE-001` to the first
governed SD-03 source/evidence/search slice, with acceptance focused on:

- source connectors producing evidence bundles with provenance and trace refs
- search results pointing to governed evidence rather than raw undocumented
  blobs
- BFF routes consuming this truth instead of inventing shadow payloads

The parent handoff reports this slice as ready for `Codex2` review with
targeted verification of 18 passing tests.

## Acceptance Checklist

| Gate | Acceptance signal | Evidence refs | Sidecar disposition |
|---|---|---|---|
| Source connector contract exists | `SourceConnector` requires connector id, source type, provider, license scope, supported mode, auth type, and status. | `services/source_ingestion/connectors/base.py`, `docs/contracts/source_connector.schema.json`, `services/source_ingestion/tests/test_ingest_run.py` | Satisfied for first in-memory slice. |
| Governed ingest lifecycle exists | `IngestRun` validates lifecycle transitions and emits lifecycle events from queued/fetching through completed, failed, or rejected terminal states. | `services/source_ingestion/connectors/base.py`, `services/source_ingestion/ingest_manager.py`, `services/source_ingestion/tests/test_ingest_run.py` | Satisfied for deterministic lifecycle coverage. |
| Evidence bundle rejects bad provenance | Bundles require source records, evidence items, citation refs, license scope, access scope, trace refs, and reject rejected sources. | `services/knowledge/evidence/bundle_builder.py`, `services/knowledge/evidence/models.py`, `services/knowledge/evidence/tests/test_bundle.py`, `docs/contracts/evidence_bundle.schema.json` | Satisfied for repository-backed bundle creation. |
| Knowledge objects preserve back references | `KnowledgeObject` links `source_id`, `evidence_item_id`, and `evidence_bundle_id`. | `services/knowledge/evidence/models.py`, `services/knowledge/evidence/repository.py`, `docs/contracts/knowledge_object.schema.json` | Satisfied for first search-indexable evidence object. |
| Search filters before ranking | `SearchGateway` applies source type, environment, ACL, workspace/persona, and license filters before keyword ranking. | `services/search/filters.py`, `services/search/gateway.py`, `services/search/tests/test_governed_search.py`, `docs/contracts/search_request.schema.json` | Satisfied for governed pre-ranking filters. |
| Search returns evidence refs, not raw blobs | Retrieval results expose `evidence_bundle_id`, citations, matched evidence items, rejected count, and filters applied. | `services/search/gateway.py`, `integrations/openclaw/search_gateway.py`, `services/search/tests/test_governed_search.py` | Satisfied for Pantheon and OpenClaw-facing search. |
| OpenClaw scope is explicit | OpenClaw search rejects unscoped persona/workspace requests before retrieval. | `integrations/openclaw/search_gateway.py`, `services/search/tests/test_governed_search.py` | Satisfied for adapter boundary. |
| BFF consumes governed truth | RW-02 search builds an in-memory evidence repository, projects through `SearchGateway`, keeps the existing `data` item shape, and adds `meta.governed_evidence`. | `services/control-plane/bff/read_store.py`, `services/control-plane/bff/main.py`, `services/control-plane/bff/test_rw02_search_contract.py` | Satisfied while preserving response compatibility. |
| Contract schemas validate model payloads | Schema tests validate model output for source connector, evidence bundle, knowledge object, and search request contracts. | `services/search/tests/test_contracts.py`, `docs/contracts/*.schema.json` | Satisfied for first JSON contract set. |

## Dependency Map

| Dependency / consumer | Direction | Current state | Review note |
|---|---|---|---|
| Parent task `SD-SRC-EVIDENCE-001` | Parent delivery consumes this packet only if useful. | Parent is already in `review`; sidecar does not gate parent completion unless reviewer/owner choose to absorb it. | Codex2 should review this sidecar as support material, not as canonical approval for the parent implementation. |
| SD materialization source packet | Upstream scope source. | Scope and acceptance shape came from `docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md`. | No source-packet edits made. |
| SD maturity assessment | Upstream gap source. | Original `SD-03` gap was full governed source/evidence/search plane being partial. | This packet records only acceptance mapping for the implemented first slice. |
| BFF RW-02 search | Downstream consumer. | Now receives governed search refs via read-store projection while preserving existing response `data`. | Reviewer should confirm no compatibility regression beyond added `meta.governed_evidence`. |
| OpenClaw adapter | Downstream consumer. | Adapter exposes cited evidence bundle refs and rejects unscoped requests. | This is a scoped adapter proof, not a full OpenClaw research-memory replacement. |
| Future durable/vector evidence store | Future dependency, not in this slice. | Current implementation is deliberately in-memory. | Do not require durable/vector DB to accept this first governed slice. |
| Single-truth lineage and registry | Adjacent SD dependency. | Evidence refs are replayable IDs, but source-to-runtime-to-telemetry lineage remains parent/adjacent work. | Do not promote broader SD-01/SD-09 closure from this task alone. |

## Non-Goals And Residual Risks

- This is not a durable evidence store, vector DB, full ingestion scheduler, or
  production connector implementation.
- This does not complete all `SD-03` future-state architecture; it establishes
  the first governed, contract-tested slice.
- This does not change L1 truth or claim full-system completion.
- BFF integration is in-memory and seeded from the RW-02 read-model dataset; a
  later owner still needs to decide when to back it with durable indexed
  evidence.

## Verification Packet

Parent handoff reports:

```bash
python3 -m pytest \
  services/source_ingestion/tests \
  services/knowledge/evidence/tests \
  services/search/tests \
  services/control-plane/bff/test_rw02_search_contract.py \
  services/control-plane/bff/test_kw03_evidence_refs_contract.py \
  -q
```

Reported result: `18 passed in 8.07s`.

The same handoff also reports:

```bash
python3 -m compileall -q \
  services/source_ingestion \
  services/knowledge \
  services/search \
  integrations/openclaw/search_gateway.py
```

Reported result: passed.

Sidecar rerun:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider \
  services/source_ingestion/tests \
  services/knowledge/evidence/tests \
  services/search/tests \
  services/control-plane/bff/test_rw02_search_contract.py \
  services/control-plane/bff/test_kw03_evidence_refs_contract.py \
  -q
```

Sidecar result: `18 passed in 3.06s`.

## Reviewer Handoff

Recommended reviewer checks for Codex2:

1. Confirm this support packet stayed within sidecar scope and did not mutate
   canonical truth.
2. Confirm the acceptance checklist is faithful to the parent handoff and source
   materialization packet.
3. Confirm no checklist item overclaims durable storage, vector retrieval,
   source-to-runtime lineage closure, or complete `SD-03` closure.
4. If approved, move `SD-SRC-EVIDENCE-001-SIDECAR-ACCEPTANCE` to
   `review_approved`; parent owner can decide whether to absorb this packet into
   the main review/handoff trail.
