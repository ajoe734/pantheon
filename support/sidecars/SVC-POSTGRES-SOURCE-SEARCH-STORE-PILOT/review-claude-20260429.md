# Review: SVC-POSTGRES-SOURCE-SEARCH-STORE-PILOT-SIDECAR-ACCEPTANCE

**Reviewer:** Claude
**Date:** 2026-04-29
**Decision:** APPROVED

## Scope Check

The packet is a support-only artifact. It does not touch any L1 canonical docs, service runtime
code, compose defaults, registry behavior, or database ownership policy. Scope constraint in section
1 is respected throughout.

## Accuracy Verification

**Section 4 store class names:** Verified against codebase.
- `JsonlIngestScheduleStore` — `services/source_ingestion/scheduler.py:86` ✅
- `JsonlConfiguredConnectorStore` — `services/source_ingestion/configured.py:35` ✅
- `JsonlEvidenceRepository` — `services/knowledge/evidence/repository.py:87` (imported and used in
  source_ingestion/main.py) ✅
- `JsonlSearchIndexStore` — `services/search/index_store.py:77` ✅

**Env var names:** Packet uses placeholder names with an explicit "or equivalent service-specific
name" qualifier (AC-2). The parent's actual implementation uses `SOURCE_INGEST_EVIDENCE_BACKEND`,
`SEARCH_INDEX_STORE_BACKEND`, and `SEARCH_EVIDENCE_BACKEND`. The qualifier covers these.

**Ownership enforcement:** `PostgresReadOnlyEvidenceRepository` (search/pg_store.py:45) overrides
all write methods to raise `EvidenceValidationError("...source-ingest is the write owner")`.
`PostgresSearchIndexStore` writes only to `search_svc.search_index_snapshots`. Write boundary is
enforced at the class level — not just documented.

## Minor Note

Section 2 "Parent Task Truth" shows a historical snapshot (owner=Claude, reviewer=Codex2) from
when the packet was authored. The parent task state has since evolved (owner=Codex2,
reviewer=Copilot). This is expected for a support artifact prepared mid-flight and does not affect
the packet's usefulness. Future sidecar packets may note "state at time of authoring" explicitly.

## Checklist

| Area | Pass |
|---|---|
| Scope — no canonical/runtime edits | ✅ |
| Dependency map is accurate | ✅ |
| Store boundary in section 4 is accurate | ✅ |
| AC-1 through AC-5 are coherent with implementation | ✅ |
| Guardrails are non-canonical recommendations only | ✅ |
| Verification commands are focused and safe | ✅ |
| Reviewer checklist for Codex2 is usable | ✅ |

## Disposition

Approved as acceptance/dependency support material. Parent owner (Codex2) should decide whether
to absorb this packet into the implementation handoff or reference it during the parent review.
