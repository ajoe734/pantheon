# Foundation / Source Evidence / Consultation Execution Task Packet

Date: 2026-04-28
Owner of packet: Codex
Dispatch target: supervisor ready-dispatch and auto workers

## Current State Versus Blueprint Gap

The system has the first materialized slices in place, but the blueprint still
expects these capabilities to be systemic rather than pilot-only.

| Area | Current implementation state | Blueprint gap to close now |
|---|---|---|
| Foundation envelope and primitives | `SD-FND-002` adopted the shared foundation envelope on two pilot paths: BFF operator commands and runtime-manager kill-switch. `SD-FND-003` added shared outbox, DLQ, schema registry, and audited idempotent replay primitives. | Adoption is not yet broad across governance, promotion, deployment, read/write surfaces, and service boundaries. Runtime crash-recovery semantics still need hardening beyond the first kill-switch path. |
| Source / evidence / search | `SD-SRC-EVIDENCE-001` introduced governed source connectors, evidence bundles, knowledge objects, search filters, OpenClaw search gateway, and BFF RW-02 governed evidence metadata. | Store and index are still first-slice/in-memory oriented. Durable source records, evidence bundles, search index replay, ingestion scheduling, and source watermarks must become service-owned durable behavior. |
| Consultation / red-team | `SD-CONSULT-001` introduced a consultation service with request, transcript, memo, immutable publication, gate handoff, evidence refs, and JSONL audit. | Lifecycle tables are loaded in memory at service start, actor fidelity is synthetic on some events, and BFF/runtime-manager do not yet consume this service as the authoritative consultation boundary. |

## Execution Order

1. Close foundation adoption and crash-recovery hardening first. Later source,
   evidence, search, and consultation persistence should reuse the foundation
   primitives rather than create shadow ledgers.
2. Land durable source/evidence/search storage and replay before scheduling and
   richer index adapters.
3. Land durable consultation persistence and actor fidelity before wiring
   higher-level BFF/runtime workflows to the consultation service.
4. Keep EP5 live/canary activation out of this packet. These tasks may improve
   readiness evidence, but they must not call a live broker or reopen a human
   approval gate.

## Materialized Tasks

### SD-FND-004 - Foundation adoption inventory and service rollout

- Owner: Codex2
- Reviewer: Claude
- Depends on: `SD-FND-002`, `SD-FND-003`
- Phase: SD Residual / Foundations
- Scope:
  - Inventory all command, governance, promotion, deployment, evidence, and
    consultation service paths that still bypass `TraceContext`,
    `CommandEnvelope`, `IdempotencyRecord`, `AuditAction`, outbox, DLQ, or schema
    registry primitives.
  - Adopt the foundation envelope on at least one additional non-pilot path with
    tests. Prefer a governance/promotion/deployment path if it can be changed
    without broad product risk.
  - Produce a follow-on adoption matrix for remaining paths, including owner,
    risk, and required test evidence.
- Acceptance:
  - A checked-in adoption matrix lists pilot-complete, newly-adopted, deferred,
    and intentionally-excluded paths.
  - At least one additional path emits foundation trace, idempotency, policy or
    audit evidence through shared primitives.
  - Targeted tests prove success, replay/idempotency, and rejection/error
    envelope behavior for the newly adopted path.

### SD-FND-005 - Crash recovery hardening for foundation-backed commands

- Owner: Claude
- Reviewer: Codex
- Depends on: `SD-FND-002`, `SD-FND-003`
- Phase: SD Residual / Foundations
- Scope:
  - Harden runtime-manager kill-switch crash recovery around durable write
    ordering, idempotency ledger replay, corrupt-state quarantine, and
    audit/outbox recovery.
  - Add at least one crash-window regression test for the persist-order concern
    documented in `SD-FND-002` review.
  - Generalize the recovery pattern enough that later service adopters can reuse
    it without copying runtime-manager-specific code.
- Acceptance:
  - Tests prove no duplicate side effect after crash/replay on the kill-switch
    command path.
  - Corrupt or partial durable state is quarantined or recovered with an audit
    trail instead of crashing startup.
  - Foundation replay/idempotency primitives are used directly, not recreated in
    runtime-manager local code.

### SD-SRC-EVIDENCE-002 - Durable source, evidence, and search store

- Owner: Copilot
- Reviewer: Codex
- Depends on: `SD-SRC-EVIDENCE-001`, `SD-FND-003`
- Phase: SD Residual / Source Evidence Search
- Scope:
  - Replace first-slice in-memory repository assumptions with service-owned
    durable JSONL or equivalent local durable stores for source records,
    evidence items, evidence bundles, knowledge objects, and search index state.
  - Preserve existing contracts and BFF RW-02 response shape.
  - Add replay tests that rebuild the repository and search index from durable
    records.
- Acceptance:
  - Service restart/reload preserves source, evidence bundle, knowledge object,
    and search result refs.
  - Search results still expose cited evidence-bundle refs and never raw blobs.
  - BFF RW-02 `meta.governed_evidence` remains stable before and after durable
    replay.

### SD-SRC-EVIDENCE-003 - Ingestion scheduler, watermarks, and index adapter

- Owner: Gemini
- Reviewer: Codex
- Depends on: `SD-SRC-EVIDENCE-002`
- Phase: SD Residual / Source Evidence Search
- Scope:
  - Add governed ingestion scheduling with source watermarks, retry/DLQ behavior,
    and replayable ingest-run state.
  - Move durable index scoring inputs behind an adapter so keyword retrieval no
    longer depends on BFF-only metadata fallbacks.
  - Keep vector retrieval optional and interface-gated; do not introduce an
    ungoverned vector store.
- Acceptance:
  - A scheduled ingest run can resume from a persisted watermark after restart.
  - Failed ingestion records go through shared DLQ/audit paths.
  - Search uses the index adapter boundary and preserves the current governed
    result contract.

### SD-CONSULT-002 - Durable consultation persistence and actor fidelity

- Owner: Claude2
- Reviewer: Codex
- Depends on: `SD-CONSULT-001`, `SD-FND-003`
- Phase: SD Residual / Consultation
- Scope:
  - Replace full-table in-memory lifecycle loading with append/replay or
    equivalent durable service-owned persistence for requests, participants,
    evidence attachments, transcripts, memos, publications, handoffs, and audit.
  - Capture initiating actor identity where `SD-CONSULT-001` currently emits a
    synthetic service actor.
  - Reuse foundation audit/outbox/replay primitives where they fit.
- Acceptance:
  - Service restart/reload preserves a full consultation lifecycle and immutable
    memo publication history.
  - Participant assignment and gate handoff audit records preserve the initiating
    actor and service actor separately where applicable.
  - Replay tests prove handoff evidence refs and audit refs remain stable.

### SD-CONSULT-003 - BFF/runtime consultation adoption

- Owner: Gemini
- Reviewer: Claude
- Depends on: `SD-CONSULT-002`, `SD-FND-002`
- Phase: SD Residual / Consultation
- Scope:
  - Wire BFF and runtime-facing consultation workflows to the consultation
    service as the authoritative boundary.
  - Remove or quarantine shadow consultation lifecycle assumptions outside the
    service.
  - Preserve existing UI/read-model response shapes unless a contract migration
    is explicitly documented.
- Acceptance:
  - BFF consultation reads/writes use service-owned records rather than local
    shadow state.
  - Runtime/governance handoff references point to consultation service handoff
    IDs, evidence refs, and audit refs.
  - Targeted BFF/runtime tests prove compatibility and no response-shape
    regression.

## Dispatch Notes

- The first ready wave should include `SD-FND-004`, `SD-FND-005`,
  `SD-SRC-EVIDENCE-002`, and `SD-CONSULT-002`.
- `SD-SRC-EVIDENCE-003` and `SD-CONSULT-003` are intentionally dependency-gated
  and should dispatch automatically after their durable-persistence parents are
  approved and finalized.
- Owners and reviewers are intentionally distinct to preserve the existing
  lifecycle rule: owner implements, reviewer approves, owner finalizes.
