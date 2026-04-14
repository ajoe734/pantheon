# BG-003 Review Packet (Sidecar)

**Parent Task**: `BG-003` — Formalize decision-front objects and adjudication boundaries
**Parent Owner**: Codex
**Parent Reviewer**: Qwen
**Parent Status**: `done`
**Sidecar Owner**: Qwen
**Sidecar Reviewer**: Codex
**Helper Kind**: `review_packet`
**Generated**: 2026-04-14T00:15:00Z
**Last Updated**: 2026-04-14T00:15:00Z

> Reviewer intake update: the original Qwen packet was prepared while BG-003 was still pending owner closeout. `ai-status.json` now records the parent task as `done`, so the disposition and handoff sections below reflect post-closeout state.

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime/registry/governance implementations.

Shared-truth sources used in this packet:
- `AI_COLLABORATION_GUIDE.md`
- `.orchestrator/task-briefs/bg_003_sidecar_review.md`
- `ai-status.json`
- `docs/02-architecture/consensus/phase2/planning-session.json`
- `services/registry-core/decision-domain/` (all schemas, examples, README, reviews)

---

## 1. Current Snapshot

- `BG-003` is currently recorded in `ai-status.json` as `owner=Codex`, `reviewer=Qwen`, `status=done`.
- The parent task has already passed review with a detailed Qwen review at `services/registry-core/decision-domain/review_bg003_qwen.md`.
- The parent's `terminal_outcome` is `completed`; owner closeout reran `python3 services/registry-core/decision-domain/validate_schemas.py` and `python3 scripts/validate_bg003.py` before marking the task done.
- This sidecar now serves as retained review evidence for the completed parent task rather than as a pending-finalization packet.

---

## 2. Review Contract

Per `ai-status.json` BG-003 acceptance criteria, the task must:

1. produce 5 JSON Schema draft-07 files and validate them
2. include required common fields across all schemas (`strategy_id`, `artifact_id`, `version`, `evaluated_at`, `input_refs`, `output_refs`, `decision_reasoning`, `model_ref`)
3. provide a five-stage chain example payload that validates end-to-end against all schemas
4. link the chain to the existing `ApprovalDecision` back-half governance gate
5. reference BG-000 market scope vocabulary and BG-001 SecurityMaster/ContractMaster IDs
6. document the research vs decision provenance distinction in all schema descriptions

---

## 3. Evidence Summary

### 3.1 Deliverable-Level Check

| Deliverable | Evidence | Reviewer read |
|---|---|---|
| RegimeState schema | `services/registry-core/decision-domain/regime_state.schema.json` — 220+ lines, draft-07, all required fields present | regime_class enum (9 values), confidence guardrail (≤0.3 → regime_transition/mixed), feature_snapshot structured sub-object, lifecycle state + conditional superseded_by |
| UniverseSelection schema | `services/registry-core/decision-domain/universe_selection.schema.json` — 260+ lines, draft-07 | regime_ref linkage, selected_universe with BG-001 refs, exclusion_reasons with enumerated reasons, empty-universe reasoning requirement |
| SignalInference schema | `services/registry-core/decision-domain/signal_inference.schema.json` — 300+ lines, draft-07 | direction enum matches execution plane vocabulary, universe_ref constrains signals, inference_stats batch summary, market_scope inheritance with BG-001 refs |
| AllocationDecision schema | `services/registry-core/decision-domain/allocation_decision.schema.json` — 290+ lines, draft-07 | Clear semantic boundary from ApprovalDecision (calculation vs governance), preserves original signal direction + allocated direction, portfolio_summary aggregate, constraints_applied enum |
| RiskAdjudication schema | `services/registry-core/decision-domain/risk_adjudication.schema.json` — 280+ lines, draft-07 | Per-check status enum (pass/warn/fail/skipped), stress_test_results separate from policy checks, conditional validation for approved_with_conditions and rejected verdicts, output_refs closes front-half to back-half chain |
| Five-stage chain example | `services/registry-core/decision-domain/examples/five_stage_chain.json` — 749 lines | Correct ID propagation, output_refs cross-stage linkage, timestamp progression (09:30:00 → 09:30:20), consistent strategy_id/persona_id/capital_pool_id, links to approval-20260413-001 |
| Single-stage examples | 6 example JSON files under `examples/` | Mirror chain payloads exactly |
| README contract doc | `services/registry-core/decision-domain/README.md` — object map, provenance chain, validation instructions | Field names match schemas, chain traversal documented |
| Existing review | `services/registry-core/decision-domain/review_bg003_qwen.md` — comprehensive, APPROVED | All 6 acceptance criteria verified, schema assessment per-object, chain integrity verified |

### 3.2 Cross-Document Coherence

The five schemas form a coherent decision provenance chain:

- **RegimeState → UniverseSelection**: `universe_selection.regime_ref.regime_id` → `regime_state.regime_id`
- **UniverseSelection → SignalInference**: `signal_inference.universe_ref.universe_id` → `universe_selection.universe_id`
- **SignalInference → AllocationDecision**: `allocation_decision.signal_ref.signal_id` → `signal_inference.signal_id`
- **AllocationDecision → RiskAdjudication**: `risk_adjudication.allocation_ref.allocation_id` → `allocation_decision.allocation_id`
- **RiskAdjudication → ApprovalDecision**: `risk_adjudication.output_refs[*].ref_type = "approval_decision"`, `ref_id = "approval-20260413-001"`

All five schemas share the common field envelope:
- `strategy_id`, `artifact_id`, `version` (semver pattern), `evaluated_at` (RFC 3339)
- `input_refs` (typed references with optional storage_ref)
- `output_refs` (downstream linkage)
- `decision_reasoning` (non-empty string)
- `model_ref` (provenance; required in 4/5 schemas, optional in RiskAdjudication for rule-based checks)
- `state` lifecycle enum + conditional `superseded_by`
- Optional: `persona_id`, `capital_pool_id`, `metadata`

### 3.3 BG-000 / BG-001 Cross-References

| Requirement | Evidence |
|---|---|
| BG-000 market scope vocabulary | `market_scope` objects in RegimeState, SignalInference, AllocationDecision, RiskAdjudication (4/5 schemas) with `asset_classes`, `markets` arrays |
| BG-001 SecurityMaster/ContractMaster IDs | `security_master_ref` / `contract_master_ref` in UniverseSelection selected_universe items, SignalInference signals, AllocationDecision allocations; `security_master` / `contract_master` ref_types in input_refs |
| DatasetVersion refs | `dataset_version` ref_type in input_refs across all schemas; `dataset_version_ref` in UniverseSelection items; `dataset_version_refs` in market_scope objects |
| MarketCalendarSession | `market_calendar_session` ref_type in RegimeState and UniverseSelection input_refs |

### 3.4 Research vs Decision Provenance

All five `model_ref` descriptions explicitly state: *"Distinguishes first-class decision provenance from raw research input/artifacts"* (or equivalent wording). This satisfies the acceptance criterion.

---

## 4. Findings

| Finding | Severity | Detail |
|---|---|---|
| All 6 acceptance criteria pass | ✅ | Verified against schemas, examples, README, and existing review |
| Chain is end-to-end valid | ✅ | ID propagation correct, timestamps monotonic, cross-refs close |
| Front-half to back-half linkage exists | ✅ | RiskAdjudication output_refs includes `approval_decision`, `deployment_plan`, `runtime_binding`, `evolution_decision` |
| BG-000/BG-001 references present | ✅ | Market scope and master IDs visible where replay needs them |
| Existing Qwen review is comprehensive and approved | ✅ | `review_bg003_qwen.md` covers all criteria, schema assessment, chain integrity |
| `regime_scores` example uses raw scores, not probabilities | Non-blocking | Example shows scores summing to >1.0; schema doesn't enforce sum-to-1. Acceptable for raw scores but could benefit from clarification if downstream consumers expect probabilities |
| `SignalInference.model_ref` required, `RiskAdjudication.model_ref` optional | Non-blocking | Reasonable since risk checks may be purely rule-based |
| `regime_class` enum mixes orthogonal dimensions | Non-blocking | Design observation, not a schema correctness issue |

---

## 5. Suggested Finalization Disposition

The parent task BG-003 is already closed as `done`. The evidence confirms that closeout was justified:

1. All 5 decision-front schemas exist, validate as Draft-07, and share a consistent common field envelope
2. The five-stage chain example validates end-to-end with correct ID propagation and cross-stage linkage
3. The front-half chain properly links to the back-half ApprovalDecision governance gate
4. BG-000 market scope vocabulary and BG-001 master/dataset references are visible throughout
5. Research vs decision provenance is explicitly distinguished in all model_ref descriptions
6. The existing Qwen review is thorough and approved

**Recommendation: no further parent-task action is required.** Keep this packet as support-only evidence that the completed BG-003 closeout was well-founded. The non-blocking notes are design-level observations, not acceptance failures.

---

## 6. Handoff Note to Codex

Codex, this sidecar packet confirms that BG-003 was substantively complete before owner closeout and that the `done` transition recorded in `ai-status.json` is supported by the evidence.

Key takeaways:

1. All 6 acceptance criteria pass with verified evidence
2. The five-schema decision chain is coherent, validated, and properly linked to back-half governance
3. Cross-references to BG-000 (market scope) and BG-001 (SecurityMaster/ContractMaster) are present and correct
4. The existing Qwen review (`review_bg003_qwen.md`) is comprehensive and approved
5. No blocking issues remain — only minor non-blocking design observations

Recommended next step:

- mark `BG-003-SIDECAR-REVIEW` as reviewer-approved so the sidecar lifecycle matches the completed review intake
- keep this sidecar as support-only evidence; no absorption into mainline artifacts is needed

---

*Generated by Qwen as a sidecar `review_packet` helper for `BG-003`. This file is a support artifact and does not modify canonical truth.*
