# SD-SRC-EVIDENCE-001 Review Packet (Sidecar)

**Parent Task**: `SD-SRC-EVIDENCE-001` - Upgrade governed source evidence search slice
**Parent Owner**: Codex
**Parent Reviewer**: Claude (parent already approved and archived; current sidecar reviewer is Codex2)
**Parent Status**: done, archived at 2026-04-27T14:39:08Z
**Sidecar Task**: `SD-SRC-EVIDENCE-001-SIDECAR-REVIEW`
**Sidecar Owner**: Codex
**Sidecar Reviewer**: Codex2
**Helper Kind**: `review_packet`
**Generated**: 2026-04-28T00:42:00Z
**Mutates canonical**: no

> Support artifact only. This packet does not modify L1 canonical truth, core
> contract truth, runtime / registry / governance implementation, or the parent
> task record. It consolidates the already-approved source / evidence / search
> trail for Codex2 review routing and downstream handoff.

## 1. Executive Summary

`SD-SRC-EVIDENCE-001` is already finalized to `done` and archived. The parent
landed the first governed SD-03 source / evidence / search slice: governed
source connector primitives, validated ingest lifecycle events, evidence bundle
and knowledge-object models, a pre-ranking `SearchGateway`, an OpenClaw-facing
adapter that returns cited evidence refs, JSON contract schemas, and BFF RW-02
search metadata under `meta.governed_evidence`.

This sidecar is retrospective review support. It should help Codex2 confirm the
evidence trail and support-only boundary; it should not reopen the parent task
or expand it into durable/vector storage, full ingestion scheduling, lineage
reconciliation, EP5 live/canary proof, or cross-repo verification.

## 2. Evidence Sources

| Source | Reviewer use |
|---|---|
| `ai-task-archive/tasks/SD-SRC-EVIDENCE-001.json` | Parent terminal record: `done`, commit `dd12ce5`, Claude approval notes, targeted suite result |
| `docs/reviews/2026-04-27-sd-src-evidence-001-codex-handoff.md` | Parent owner handoff and acceptance mapping |
| `docs/reviews/2026-04-27-sd-src-evidence-001-claude-review.md` | Parent reviewer approval, detailed line-level acceptance review, and non-blocking observations |
| `support/sidecars/SD-SRC-EVIDENCE-001/SD-SRC-EVIDENCE-001-SIDECAR-ACCEPTANCE.md` | Acceptance / dependency packet for the same parent |
| `docs/reviews/2026-04-27-sd-src-evidence-001-sidecar-acceptance-claude-review.md` | Review approval for the acceptance sidecar; notes support-only boundary |
| `docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md` | Defines SD residual source / evidence / search acceptance boundary |
| `services/source_ingestion/connectors/base.py` | `SourceConnector`, `SourceRecord`, and `IngestRun` lifecycle implementation |
| `services/knowledge/evidence/{models.py,bundle_builder.py,repository.py}` | Evidence bundle, evidence item, knowledge object, and repository invariants |
| `services/search/{filters.py,gateway.py}` | Search request / access filtering and governed retrieval response |
| `integrations/openclaw/search_gateway.py` | OpenClaw-facing adapter boundary |
| `services/control-plane/bff/{read_store.py,main.py}` | RW-02 governed evidence projection and response metadata |
| Targeted tests under source ingestion, evidence, search, and BFF RW/KW contracts | Repo-current non-regression evidence |

## 3. Parent Acceptance Coverage

| Acceptance target | Evidence | Review read |
|---|---|---|
| Source connectors carry governed identity | `SourceConnector` requires connector id, provider, license scope, source type, auth type, status, and at least one supported mode (`services/source_ingestion/connectors/base.py:101-139`) | PASS |
| Source records and ingest lifecycle are explicit | `SourceRecord` normalizes source id, connector id, source type, content ref, status, metadata, and trace id (`services/source_ingestion/connectors/base.py:142-179`); `IngestRun` enforces allowed transitions and emits lifecycle events (`services/source_ingestion/connectors/base.py:207-300`) | PASS |
| Evidence bundles reject invalid provenance | `EvidenceBundleBuilder.build_bundle()` requires source records and evidence items, rejects rejected sources, verifies evidence items reference bundled sources, gathers trace refs / access scope / license scope, and persists through the repository (`services/knowledge/evidence/bundle_builder.py:20-82`) | PASS |
| Evidence models require replayable refs | `EvidenceItem` requires source id, content ref, citation label, confidence, access scope, and trace refs; `EvidenceBundle` requires source ids, evidence item ids, citation refs, license scope, access scope, creator, and trace refs (`services/knowledge/evidence/models.py:44-135`) | PASS |
| Knowledge objects link back to governed evidence | `KnowledgeObject` requires source id, evidence item id, and evidence bundle id (`services/knowledge/evidence/models.py:178-232`); repository rejects unknown bundle or item refs (`services/knowledge/evidence/repository.py:41-50`) | PASS |
| Search filters before ranking | `SearchGateway.search()` filters source type, access context, and citation availability before keyword retrieval, then records `pre_ranking_filter` in metadata (`services/search/gateway.py:74-105`); `SearchAccessContext.permits()` covers environment, license, access, persona, and workspace scope (`services/search/filters.py:77-97`) | PASS |
| Search results expose governed evidence refs | `RetrievalResult` carries `evidence_bundle_id`, `matched_items`, `citations`, `filters_applied`, rejected count, relevance, and created time (`services/search/gateway.py:19-44`, `services/search/gateway.py:105-142`) | PASS |
| OpenClaw adapter is scoped and evidence-only | Adapter requires persona/workspace scope before retrieval and returns evidence bundle id, citations, matched items, answer context, relevance, rejected count, and filters; it does not return raw payload blobs (`integrations/openclaw/search_gateway.py:20-60`) | PASS |
| BFF RW-02 consumes governed evidence while preserving data shape | Read store builds an in-memory evidence repository from eligible RW-02 documents, routes through `SearchGateway`, stores `_last_governed_search_refs`, and returns the existing projected item keys (`services/control-plane/bff/read_store.py:7283-7484`); route adds `meta.governed_evidence` only when refs exist (`services/control-plane/bff/main.py:8033-8073`) | PASS |
| Contract schemas and route contracts are covered | Targeted suite includes source ingestion, evidence, search, contract schema validation, RW-02 response shape, and KW-03 evidence refs | PASS |

## 4. Verification

Fresh command run from repo root for this review sidecar:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider \
  services/source_ingestion/tests \
  services/knowledge/evidence/tests \
  services/search/tests \
  services/control-plane/bff/test_rw02_search_contract.py \
  services/control-plane/bff/test_kw03_evidence_refs_contract.py \
  -q
..................                                                       [100%]
18 passed in 3.42s
```

Interpretation:

- The parent archive records closeout at commit `dd12ce5` with `18 passed in
  3.82s`.
- The parent Claude review reran the same targeted suite and recorded `18
  passed in 3.03s`.
- The acceptance sidecar recorded a later targeted rerun with `18 passed in
  3.06s`.
- The repo-current targeted files still report `18 passed`. This is a
  non-regression signal for the same source / evidence / search and BFF contract
  surface, not an expansion of the parent acceptance claim.

## 5. Review Focus Areas For Codex2

| Focus area | What to confirm | Expected disposition |
|---|---|---|
| Retrospective routing | Parent reviewer was Claude; this helper is routed to Codex2 as a support sidecar | Treat this packet as review support only, not a parent re-review requirement |
| Governed evidence boundary | Source, evidence, knowledge object, and search refs are explicit and replayable | Approve if the packet preserves the first-slice boundary |
| BFF compatibility | RW-02 response `data` shape remains stable; governed refs live in metadata | Approve if `meta.governed_evidence` remains additive |
| OpenClaw boundary | Adapter returns cited Pantheon evidence refs and requires persona/workspace scope | Approve if no OpenClaw write-back or raw blob path is claimed |
| Downstream split | Durable/vector index, ingestion scheduler, SD-RECON-001, EP5, and cross-repo verification remain separate | Do not ask this sidecar to absorb downstream closure |
| Test evidence | Current targeted suite remains at 18 passing tests | Treat as repo-current non-regression evidence |

## 6. Non-Blocking Observations

The parent Claude review recorded follow-up observations that remain
non-blocking for this sidecar:

| Observation | Disposition |
|---|---|
| BFF RW-02 rebuilds the in-memory evidence repository on every search call | Acceptable for the first slice; a later durable backing store can memoize by `result_id` / source watermark |
| `KeywordRetriever` reads BFF metadata to preserve existing scoring behavior | Later durable index work should move those fallbacks into the index adapter |
| Bundle confidence defaults to the minimum item confidence | Conservative for first slice; `SD-RECON-001` may refine confidence semantics |
| Empty `environment_scope` on a knowledge object means all environments allowed | Acceptable for first slice; durable writers should populate scope explicitly |

## 7. Reviewer Guardrails

Reject any review interpretation that:

- treats this sidecar as canonical SD-03 architecture truth or a replacement for
  L1 policy
- reopens the already-archived `SD-SRC-EVIDENCE-001` parent without a new
  follow-up task
- requires a durable evidence store, vector DB, ingestion scheduler, or
  production connector implementation in this helper
- treats `meta.governed_evidence` as a breaking change to RW-02 `data` item
  shape
- promotes this first governed slice into full source-to-runtime lineage
  closure, EP5 live/canary proof, research activation, or cross-repo proof
- allows OpenClaw to write source/evidence truth back into Pantheon through this
  adapter
- edits L1 docs, core contracts, runtime registry, governance code, frontend
  source, or LEAN bridge files from this helper slice

## 8. Handoff To Codex2

This sidecar is ready for review.

Recommended reviewer decision:

1. Approve this sidecar if the packet accurately consolidates the already-done
   parent evidence and remains support-only.
2. Use the parent archive, Codex handoff, Claude review, acceptance sidecar, and
   fresh 18-test targeted rerun as the evidence trail.
3. Keep durable/vector evidence storage, ingestion scheduling,
   `SD-RECON-001`, `EP5-002-PACKET-PREP-001`, and
   `CROSS-REPO-SD-VERIFY-001` responsible for their own downstream proof and
   integration scope.

Suggested review summary if approved:

```text
Review packet approved. The sidecar accurately consolidates the archived
SD-SRC-EVIDENCE-001 governed source/evidence/search evidence, current 18-test
targeted verification, downstream boundaries, and support-only guardrails. No
canonical truth edited.
```

---
Generated by Codex as a sidecar `review_packet` helper for
`SD-SRC-EVIDENCE-001`.
