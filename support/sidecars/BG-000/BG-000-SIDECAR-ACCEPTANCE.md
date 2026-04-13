# BG-000 Acceptance Packet (Sidecar)

**Parent Task**: `BG-000` — Canonicalize market scope, instrument policy, and source-class matrix
**Parent Owner**: Qwen
**Parent Reviewer**: Codex
**Parent Status**: `in_progress`
**Sidecar Owner**: Codex
**Sidecar Reviewer**: Claude
**Helper Kind**: `acceptance_packet`
**Generated**: 2026-04-13T07:08:04Z
**Last Updated**: 2026-04-13T07:14:16Z

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime/registry/governance implementations.

Finalize refresh note (2026-04-13):

- The task-scoped brief for this resumed pass marks `BG-000-SIDECAR-ACCEPTANCE` as reviewer-approved and waiting for Codex owner-close.
- Durable `ai-status.json` still shows the sidecar as `in_progress`, so this finalize pass is expected to restore the approval state before moving the sidecar to `done`.
- The parent-task cleanup items captured below remain unchanged: this refresh does not add new acceptance claims or modify any canonical BG-000 policy file.

Shared-truth sources used in this packet:
- `AI_COLLABORATION_GUIDE.md` — collaboration and status-lifecycle rules
- `.orchestrator/task-briefs/bg_000_sidecar_acceptance.md` — task-scoped sidecar brief
- `.orchestrator/task-briefs/bg_000.md` — parent-task brief and recent activity
- `ai-status.json` — durable task registry for parent/sidecar state
- `docs/02-architecture/consensus/phase2/planning-session.json` — machine-readable task origin
- `docs/02-architecture/consensus/phase2/execution-materialization.md` — explicit BG-000 deliverable contract
- `docs/02-architecture/consensus/phase2/consensus-packet.md` — human-approved provider brief
- `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md` — upstream question set for GAP-00
- `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` — published market/instrument policy
- `DATA_SOURCE_SCOPE_MATRIX.md` — published source-class matrix
- `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md` — published identity/truth-model policy

---

## 1. Dependency Map

### 1.1 Parent Dependencies

`BG-000` depends on `PLAN-002` (done). The phase2 planning session then materialized BG-000 as a Wave 0 / P0 foundation task whose job is to publish the market/data truth package before BG-001, BG-003, and BG-005 consume it.

### 1.2 What BG-000 Was Supposed to Deliver

Per `execution-materialization.md`, BG-000 must publish three support-free canonical policy docs and answer the upstream market-data scope questions needed to unblock the Data Plane.

| Deliverable | Planned requirement | Evidence in repo |
|---|---|---|
| Market scope / instrument policy | Publish `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` and define market universe, instrument scope, and per-market stage eligibility | `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` exists (222 lines) with sections for market universe, per-market instruments, lifecycle, calendar, and venue policy |
| Source-class matrix | Publish `DATA_SOURCE_SCOPE_MATRIX.md` and formalize source-class boundaries | `DATA_SOURCE_SCOPE_MATRIX.md` exists (136 lines) with 6 source classes, per-market mapping, and Data Plane ingest rules |
| Symbol/contract truth model | Publish `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md` and define identity/linkage policy | `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md` exists (242 lines) with SecurityMaster/ContractMaster policy, continuous-series rule, and validation rules |
| Human-approved provider input | Use the BG-000 provider shortlist approved in planning | `consensus-packet.md` contains the approved provider brief, but no standalone `PRIMARY_DATA_PROVIDER_SHORTLIST.md` exists in the repo |
| Answers to the 10 market-data questions from the upstream plan | Close the GAP-00 question set in `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md` section 12 | The three docs answer most questions directly; one question (`DatasetVersion` existence / timing) is still only implied via BG-001 handoff, not answered explicitly in the BG-000 docs |

### 1.3 Downstream Consumers Waiting On BG-000

| Consumer | Task ID | Phase | Owner | Why BG-000 matters |
|---|---|---|---|---|
| Data Plane object formalization | `BG-001` | Blueprint Gap P0 | Claude | `BG-001` must consume BG-000 market and instrument vocabulary instead of inventing free-form market enums or symbol semantics |
| Decision-front provenance schemas | `BG-003` | Blueprint Gap P0 | Qwen | `BG-003` example payloads must use BG-000 market vocabulary and avoid bypassing the source-policy package |
| Golden replay acceptance gate | `BG-005` | Blueprint Gap P0 | Codex | `BG-005` depends on BG-000 to define which markets/instruments/data classes are legitimate replay inputs |
| Product/operator glossary | `BG-007` | Blueprint Gap P2 | Codex | Product-facing terminology should inherit BG-000's market/instrument vocabulary |

### 1.4 Current Parent-State Snapshot

The parent artifact bundle exists, but the machine state is not fully reconciled:

| Observation | Evidence | Impact |
|---|---|---|
| Durable parent status is still `in_progress` | `ai-status.json` task entry for `BG-000` | Parent is not ready for formal `done` closure yet |
| Recent activity already records a `review_approved` event | `.orchestrator/task-briefs/bg_000.md` recent activity on 2026-04-13T05:53:04Z | Owner-close drift exists between activity history and durable state |
| Parent `artifacts` array is still empty | `ai-status.json` task entry for `BG-000` | Machine-readable artifact discovery is incomplete |
| Parent `next` summary is stale | It claims 270/208/257 lines, says all 10 questions are answered in `DATA_SOURCE_SCOPE_MATRIX.md` section 6, and says ready for review | Actual file lengths are 222/136/242, and section 6 in `DATA_SOURCE_SCOPE_MATRIX.md` is only version history |

### 1.5 Readiness Verdict

**BG-000 is substantively close on content, but not yet owner-close clean.** The three intended documents exist and are structurally coherent. The remaining work is status/evidence reconciliation:

1. record the three published docs in the parent task's `artifacts`
2. reconcile the parent status with the already-recorded review activity
3. fix the stale parent summary so it matches the actual artifact evidence

---

## 2. Acceptance Checklist for Parent Task (`BG-000`)

### 2.1 Coverage of the 10 Upstream Market-Data Questions

These questions come from `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md` section 12.

| # | Question | Evidence | Status |
|---|---|---|---|
| 1 | Are US / TW / crypto formally v1 primary markets? | `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` section 1 defines `US`, `TW`, `CRYPTO` as the Pantheon v1 market universe | Verified |
| 2 | Which spot/cash instruments are required per market? | `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` section 2.1-2.3 lists required equities, ETFs, spot pairs, and local venue distinctions | Verified |
| 3 | Which derivatives are required per market? | `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` section 2.1-2.3 defines required futures/options/perpetuals and deferred derivative scope | Verified |
| 4 | Which instruments are research-only versus execution-targeted? | Execution-target columns in `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` distinguish `research-only`, `paper -> canary`, and `paper -> canary -> live` | Verified |
| 5 | Which data sources belong to official / broker-aligned / research-grade / specialized analytics classes? | `DATA_SOURCE_SCOPE_MATRIX.md` sections 1-3 define 6 source classes and map each market's data needs to those classes | Verified |
| 6 | Who is the truth owner for SymbolMaster / ContractMaster? | `DATA_SOURCE_SCOPE_MATRIX.md` source-class rules and `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md` sections 1-4 establish Data Plane ownership and contract-level truth | Verified |
| 7 | Does `DatasetVersion` already exist, and if not when is it added? | The BG-000 docs do not answer this explicitly; it is deferred implicitly to BG-001 / replay follow-on work | Needs explicit owner note |
| 8 | Can replay reconstruct historical options-chain / futures-contract state? | `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` flags options-chain replay as a blocker, and `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md` section 4 requires replay to resolve actual historical contracts | Verified as policy requirement |
| 9 | Is multi-market timezone / calendar discipline formalized? | `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` section 5 formalizes US/TW session rules and crypto 24/7 cutoffs/funding windows | Verified |
| 10 | Which markets advance to paper / canary / live? | `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` sections 1-2 define per-market and per-instrument stage eligibility | Verified |

### 2.2 Package-Level Acceptance Checks

| # | Criterion | Verification Method | Status |
|---|---|---|---|
| 1 | `three_required_docs_exist` | All three planned files exist at repo root | Verified |
| 2 | `cross_references_exist` | The three docs reference each other and the upstream market-data plan | Verified |
| 3 | `downstream_model_paths_exist` | Referenced Data Plane model paths under `services/data-plane/models/` and `services/data-plane/schemas/` exist | Verified |
| 4 | `source_class_boundary_is_vendor_agnostic` | `DATA_SOURCE_SCOPE_MATRIX.md` section 4 states vendor selection is operational, not schema truth | Verified |
| 5 | `provider_input_is_traceable` | The human-approved provider brief exists in `consensus-packet.md`, not as a standalone repo artifact | Partial |
| 6 | `parent_status_summary_matches_artifacts` | Parent task summary still cites stale line counts and a wrong section reference | Needs owner reconciliation |
| 7 | `parent_artifacts_are_recorded_in_state` | Parent `artifacts` array remains empty in `ai-status.json` | Needs owner reconciliation |
| 8 | `review_gate_state_is_reconciled` | Brief history shows `review_approved`, but durable status is still `in_progress` | Needs owner reconciliation |

---

## 3. Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Parent state drift (`review_approved` activity vs `in_progress` durable status) | Owner may think the task is still waiting for review when the reviewer gate already happened | Parent owner should reconcile the status before attempting `done` |
| Parent `next` summary is factually stale | Reviewers and downstream automation may rely on wrong file lengths and the incorrect `DATA_SOURCE_SCOPE_MATRIX.md` section reference | Replace the stale summary with evidence that matches the published docs |
| Parent `artifacts` list is empty | Machine-readable discovery does not point consumers to the three published docs | Parent owner should register the three artifact paths before closure |
| `DatasetVersion` answer is not explicit in BG-000 docs | The upstream question set is not literally closed inside the BG-000 package | Parent owner should add a short closure note that BG-000 defines the boundary and BG-001 owns `DatasetVersion` formalization |
| Provider shortlist is only embedded in planning consensus, not materialized as a standalone artifact | Reviewers can trace the approved input, but the repo lacks a dedicated provider-shortlist file if someone expects one | Accept the consensus packet as the authoritative provider input for now, or materialize a support note later if the parent owner wants a local pointer |
| Self-checklists inside the BG-000 docs still contain open boxes | Readers may mistake cross-task follow-on items for missing BG-000 content | Treat unchecked items about schema/tests/BG-001 validation as downstream work, not as evidence that the policy package is absent |

---

## 4. Artifacts Inventory

| Artifact | Path | Purpose |
|---|---|---|
| Primary market/instrument policy | `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md` | Defines v1 market universe, instrument scope, lifecycle, calendar, and stage eligibility |
| Primary source-class matrix | `DATA_SOURCE_SCOPE_MATRIX.md` | Defines source classes, per-market data-need mapping, and canonical ingest/store rules |
| Primary symbol/contract policy | `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md` | Defines identity, canonical symbol policy, derivative linkage, and replay rule |
| Planning source task definition | `docs/02-architecture/consensus/phase2/planning-session.json` | Machine-readable origin for BG-000 |
| Execution contract | `docs/02-architecture/consensus/phase2/execution-materialization.md` | Explicit BG-000 deliverable wording |
| Human-approved provider brief | `docs/02-architecture/consensus/phase2/consensus-packet.md` | Approved provider shortlist and market-by-market source guidance |
| Upstream question set | `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md` | Defines the 10 questions and acceptance expectations that BG-000 must answer |
| Parent task brief | `.orchestrator/task-briefs/bg_000.md` | Parent state, recent activity, and review-history context |
| This acceptance packet | `support/sidecars/BG-000/BG-000-SIDECAR-ACCEPTANCE.md` | Support checklist, dependency map, and owner-close notes |

---

## 5. Handoff Note to Reviewer (Claude)

Claude, this packet confirms that BG-000 already has the intended three-document policy bundle in place, but it also surfaces the owner-close cleanup still needed before anyone should pretend the parent is fully closed.

Key takeaways:

1. The three planned BG-000 docs exist and are mutually coherent enough for downstream consumers.
2. The package answers 9 of the 10 upstream market-data questions directly; the missing explicit answer is the `DatasetVersion` existence/timing question, which is currently only implied as BG-001 follow-on scope.
3. The durable parent state is not reconciled: `review_approved` activity exists in the task brief, but `ai-status.json` still says `in_progress`, the `artifacts` array is empty, and the `next` summary is stale.
4. The human-approved provider shortlist is traceable in `consensus-packet.md`, but there is no standalone `PRIMARY_DATA_PROVIDER_SHORTLIST.md`.
5. No canonical truth files or runtime/registry/governance implementation files were modified by this sidecar.

**Recommended next step**: approve this sidecar packet if it is sufficient as reviewer support. The parent owner should then decide whether to absorb the cleanup notes into the parent closeout path before moving `BG-000` toward final closure.

---

## 6. Codex Owner Finalize Refresh (2026-04-13)

This resumed dispatch is an owner-close pass for the sidecar itself, not a reopening of BG-000 content review.

Current finalize context:

- `.orchestrator/task-briefs/bg_000_sidecar_acceptance.md` records the sidecar as `review_approved`, owned by `Codex`, reviewed by `Claude`, and waiting for owner closure.
- The packet's substantive verdict is unchanged: the three BG-000 policy docs exist, are coherent enough for downstream use, and still leave four parent reconciliation items plus the explicit `DatasetVersion` closure note for the parent owner.
- This support artifact remains within sidecar-only scope. No L1 canonical truth, runtime, registry, governance, or parent-task implementation file was modified during this refresh.

Finalize intent:

- Close `BG-000-SIDECAR-ACCEPTANCE` as `done` after restoring the approval-state drift in the status system.
- Leave the parent BG-000 cleanup decisions with the parent owner; this packet remains support evidence only.

---

*Generated by Codex as a sidecar `acceptance_packet` helper for `BG-000`. This file is a support artifact and does not modify canonical truth.*
