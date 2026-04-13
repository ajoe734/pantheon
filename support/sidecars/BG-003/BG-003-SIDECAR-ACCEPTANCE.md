# BG-003 Acceptance Packet (Sidecar)

**Parent Task**: `BG-003` — Formalize decision-front objects and adjudication boundaries
**Parent Owner**: Codex
**Parent Reviewer**: Qwen
**Parent Status**: `in_progress`
**Sidecar Owner**: Qwen
**Sidecar Reviewer**: Codex
**Helper Kind**: `acceptance_packet`
**Generated**: 2026-04-13T02:36:14Z

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime/registry/governance implementations.

Shared-truth sources used in this packet:
- `ai-status.json` — task registry and agent state
- `current-work.md` — derived sprint snapshot
- `ai-activity-log.jsonl` — activity history
- `docs/02-architecture/consensus/phase2/` — planning session materials

---

## 1. Dependency Map

### 1.1 Parent Dependencies

`BG-003` depends on `PLAN-002` (done). The planning session materialized the blueprint-gap convergence wave that produced this task.

### 1.2 What BG-003 Was Supposed to Deliver

**Gap statement (GAP-03)**: The back half of the decision chain is already formalized (`ApprovalDecision`, `DeploymentPlan`, `RuntimeBinding`, `EvolutionDecision`), but the **first half of the five-stage decision chain** lacks first-class auditable objects. Scoped grep searches under `services/**`, `integrations/**`, and `support/**` returned zero hits for all five target object names. This is a confirmed real P0 gap. (`gap-response-matrix.md §GAP-03`; `Pantheon_Blueprint_Gap_Review_v1.md §GAP-03`)

The parent task title is "Formalize decision-front objects and adjudication boundaries." Per the planning session (`qwen-readout.md §Slice 2`; `execution-materialization.md`; `planning-session.json:243-254`), BG-003 must deliver:

| Deliverable | Specification |
|---|---|
| 5 `.schema.json` files | `RegimeState`, `UniverseSelection`, `SignalInference`, `AllocationDecision`, `RiskAdjudication` |
| Target location | `services/registry-core/decision-domain/` |
| Field requirements (per schema) | `strategy_id`, `artifact_id`, `version`, `evaluated_at`, `input_refs[]` (upstream dataset/experiment refs), `output_refs[]` (downstream allocation/deployment refs), and `decision_reasoning` or `model_ref` for provenance |
| Acceptance criteria | Each schema validates against JSON Schema draft-07; ≥1 strategy family can produce a full five-stage chain `RegimeState → UniverseSelection → SignalInference → AllocationDecision → RiskAdjudication` using example payloads; the chain links to the existing `ApprovalDecision` downstream |
| Cross-cutting requirement | Schemas must consume BG-000 vocabulary (market scope policy) and reference BG-001 objects (SecurityMaster/ContractMaster IDs) where applicable |
| Explicit documentation | Make explicit which current logic remains research input versus first-class decision provenance |

### 1.3 Five-Stage Decision Chain Context

The five-stage decision chain is the front half of Pantheon's full decision provenance pipeline:

```
RegimeState → UniverseSelection → SignalInference → AllocationDecision → RiskAdjudication
                                                                        ↓
                                                       ApprovalDecision (existing, back half)
                                                       DeploymentPlan (existing, back half)
                                                       RuntimeBinding (existing, back half)
                                                       EvolutionDecision (existing, back half)
```

Each stage is an auditable provenance object. Together they allow replay (BG-005) to attribute failures to the correct stage — regime misclassification, universe selection error, signal inference flaw, allocation miscalculation, or risk adjudication failure — rather than treating everything before deployment as opaque research logic. (`starter-draft.md`; `consensus-packet.md`; `Pantheon_Blueprint_Gap_Review_v1.md §GAP-03`)

### 1.4 Existing Back-Half Schemas (Reference)

The following governance schemas already exist and represent the "back half" that BG-003's front-half schemas must link to:

| Schema | Path |
|---|---|
| `ApprovalDecision` | `services/control-plane/governance/approval_decision.schema.json` |
| `EvolutionDecision` | `services/control-plane/governance/evolution_decision.schema.json` |

These serve as reference patterns for schema structure (JSON Schema draft-07, conditional `allOf` validation, lifecycle state enums, evidence reference patterns, actor role/identity fields).

### 1.5 Downstream Consumers Waiting On BG-003

| Consumer | Task ID | Phase | Owner | Why BG-003 matters |
|---|---|---|---|---|
| Define golden replay scenario and acceptance runbook | `BG-005` | Blueprint Gap P0 | Codex | **Hard dependency.** BG-005 cannot construct a meaningful replay scenario without decision-front object refs. The replay must pin `RegimeState`, `UniverseSelection`, `SignalInference`, `AllocationDecision`, and `RiskAdjudication` IDs to attribute failure provenance. |
| Publish memory layer design note | `BG-004` | Blueprint Gap P2 | Claude | Memory retrieval may need to index decision-front objects for persona-specific replay context. |
| Publish product-facing glossary | `BG-007` | Blueprint Gap P2 | Codex | The glossary must include decision-front object names and stage vocabulary. |

### 1.6 Parallel P0 Tasks

`BG-003` is parallelizable with `BG-000` (market scope) and `BG-001` (data plane objects). All three are Wave 0 / P0 foundations. `BG-003` should consume BG-000 vocabulary and BG-001 object refs once they exist, but schema drafting can proceed in parallel with explicit placeholder refs that are resolved during integration. (`execution-materialization.md`; `consensus-packet.md`)

### 1.7 Readiness Verdict On Dependencies

**`BG-003` is dependency-complete for schema drafting.** `PLAN-002` is done. The planning session has locked the object boundaries and acceptance criteria. Schema drafting can begin immediately; integration with BG-000 vocabulary and BG-001 object refs should be resolved once those tasks produce their artifacts.

---

## 2. Acceptance Checklist for Parent Task (`BG-003`)

The parent task acceptance criteria derived from the planning session (`qwen-readout.md §Slice 2`; `planning-session.json:243-254`; `gap-response-matrix.md §GAP-03`):

| # | Criterion | Verification Method | Status |
|---|---|---|---|
| 1 | `regime_state_schema_exists` | `services/registry-core/decision-domain/regime_state.schema.json` exists and validates as JSON Schema draft-07 | ⏳ Pending — parent task `in_progress`, no artifacts yet |
| 2 | `universe_selection_schema_exists` | `services/registry-core/decision-domain/universe_selection.schema.json` exists and validates as JSON Schema draft-07 | ⏳ Pending |
| 3 | `signal_inference_schema_exists` | `services/registry-core/decision-domain/signal_inference.schema.json` exists and validates as JSON Schema draft-07 | ⏳ Pending |
| 4 | `allocation_decision_schema_exists` | `services/registry-core/decision-domain/allocation_decision.schema.json` exists and validates as JSON Schema draft-07 | ⏳ Pending |
| 5 | `risk_adjudication_schema_exists` | `services/registry-core/decision-domain/risk_adjudication.schema.json` exists and validates as JSON Schema draft-07 | ⏳ Pending |
| 6 | `common_fields_present` | Each schema contains `strategy_id`, `artifact_id`, `version`, `evaluated_at`, `input_refs[]`, `output_refs[]`, and `decision_reasoning` or `model_ref` | ⏳ Pending |
| 7 | `five_stage_chain_completable` | ≥1 strategy family can produce a complete chain `RegimeState → UniverseSelection → SignalInference → AllocationDecision → RiskAdjudication` using example payloads | ⏳ Pending |
| 8 | `chain_links_to_approval_decision` | The five-stage chain links to the existing `ApprovalDecision` schema downstream (e.g., `output_refs[]` from `RiskAdjudication` references `ApprovalDecision.target_id` or equivalent linkage field) | ⏳ Pending |
| 9 | `bg000_vocabulary_consumed` | Schemas reference BG-000 market scope policy vocabulary (e.g., market identifiers, instrument classes) rather than inventing free-form enums | ⏳ Pending — depends on BG-000 artifact availability |
| 10 | `bg001_object_refs_used` | Schemas reference BG-001 objects (`SecurityMaster`, `ContractMaster`, `DatasetVersion` IDs) where applicable | ⏳ Pending — depends on BG-001 artifact availability |
| 11 | `research_vs_provenance_documented` | The task makes explicit which current logic remains research input versus first-class decision provenance | ⏳ Pending |
| 12 | `no_canonical_modification` | No L1 canonical truth, core contract, or runtime/registry/governance implementation files were modified by this sidecar | ✅ Verified (this sidecar creates support artifacts only) |

---

## 3. Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| **BG-003 artifacts are empty** — `ai-status.json` records `artifacts: []` for the parent task | BG-005 (golden replay) is P0-blocked; the critical path to production sign-off cannot proceed | This acceptance packet provides the checklist template for the parent owner to verify against once artifacts are produced |
| **BG-000 / BG-001 not yet available** — market scope policy and data plane objects may not exist when BG-003 drafts schemas | Schema fields may use placeholder enums that later conflict with BG-000/BG-001 truth | Parent owner should use extensible string types for market/instrument fields initially, then tighten to BG-000/BG-001 refs during integration pass |
| **Five-stage chain complexity** — ensuring example payloads flow through all five stages with valid cross-references | Chain validation may fail on integration if stage outputs don't match next stage inputs | Parent owner should produce a single example payload set that exercises the full chain end-to-end before declaring acceptance |
| **Duplication with existing back-half schemas** — `AllocationDecision` (front half) could overlap conceptually with `ApprovalDecision` (back half) | Naming confusion or semantic drift between stages | Front-half `AllocationDecision` is a *capital allocation calculation*; back-half `ApprovalDecision` is a *governance approval gate*. The parent owner should document this distinction explicitly in schema descriptions |
| **Parent owner capacity** — Codex (BG-003 owner) has experienced rate-limiting (429 errors) in recent dispatches | Task progress may stall, further delaying BG-005 | This acceptance packet is ready for use as soon as the parent produces artifacts; no additional preparation needed |

---

## 4. Execution Wave Readiness

BG-003 is on the **critical path to production sign-off**:

```
BG-000 ─┐
        ├→ BG-005 (golden replay / production sign-off gate)
BG-001 ─┤
        │
BG-003 ─┘
```

BG-003's deliverables directly enable:

1. **BG-005 (golden replay)** — The replay scenario must pin real decision-front object refs alongside dataset/master versions. Without BG-003, replay has no decision provenance to verify.
2. **BG-007 (product glossary)** — The glossary must include decision-front object names and stage vocabulary for operator-facing documentation.
3. **BG-004 (memory layer)** — Memory retrieval may index decision-front objects for persona-specific context and institutional memory write-back.

**Recommended sequencing:** BG-003 should complete in parallel with BG-000 and BG-001, then integrate vocabulary/refs before BG-005 begins. The five-stage chain example payloads should be validated before BG-005 starts drafting its runbook.

---

## 5. Artifacts Inventory

| Artifact | Path | Purpose |
|---|---|---|
| This acceptance packet | `support/sidecars/BG-003/BG-003-SIDECAR-ACCEPTANCE.md` | Acceptance checklist + dependency map |
| Existing ApprovalDecision schema | `services/control-plane/governance/approval_decision.schema.json` | Reference pattern for back-half governance schema |
| Existing EvolutionDecision schema | `services/control-plane/governance/evolution_decision.schema.json` | Reference pattern for back-half evolution schema |
| GAP-03 gap statement | `docs/02-architecture/consensus/phase2/gap-response-matrix.md` | Gap definition, repo evidence, and closure decision |
| Slice 2 deliverable spec | `docs/02-architecture/consensus/phase2/qwen-readout.md` | Detailed 5-schema requirements (field names, location, acceptance) |
| Execution materialization | `docs/02-architecture/consensus/phase2/execution-materialization.md` | Wave ordering and BG-003 role in P0 wave |
| Consensus packet | `docs/02-architecture/consensus/phase2/consensus-packet.md` | Delivery wave ordering and BG-003 critical path position |
| Review round 01 | `docs/02-architecture/consensus/phase2/review-round-01.md` | Reviewer consensus on BG-003 object boundaries |
| Starter draft | `docs/02-architecture/consensus/phase2/starter-draft.md` | Wave 0 rationale: formalize five-stage chain for replay failure attribution |
| Planning session task | `docs/02-architecture/consensus/phase2/planning-session.json` | Machine-readable BG-003 task definition |
| Blueprint gap review | `Pantheon_Blueprint_Gap_Review_v1.md` | Original GAP-03 assessment (§376-449) |

---

## 6. Handoff Note to Reviewer (Codex)

Codex, this packet establishes the acceptance framework for the parent task `BG-003`. Current state:

- Parent `BG-003` is `in_progress` (owner: Codex, reviewer: Qwen) with `artifacts: []` — the five schemas have not yet been produced
- This sidecar is a **preparatory packet** — it provides the checklist, dependency map, and risk assessment that the parent owner and reviewer should use once artifacts exist
- No canonical truth files were modified by this sidecar

**What this packet provides:**

1. ✅ Complete dependency map — PLAN-002 (done), parallel with BG-000/BG-001, blocks BG-005
2. ✅ 12-item acceptance checklist derived from planning session requirements
3. ✅ Five risk items identified with mitigations
4. ✅ Existing back-half schemas cataloged as reference patterns
5. ✅ Downstream consumer impact analysis (BG-005, BG-007, BG-004)
6. ✅ Artifacts inventory — all planning session materials cited with paths

**Recommended next step**: review and approve this sidecar packet. Once the parent owner (Codex) produces the five schema artifacts, this checklist becomes the verification instrument for formal acceptance. The parent reviewer (Qwen) should use this packet alongside the parent's schema artifacts to validate all 12 criteria before moving BG-003 to `review_approved`.

---

*Generated by Qwen as a sidecar `acceptance_packet` helper for `BG-003`. This file is a support artifact and does not modify canonical truth.*
