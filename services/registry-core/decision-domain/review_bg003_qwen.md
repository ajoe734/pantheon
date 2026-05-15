# BG-003 Review: Formalize decision-front objects and adjudication boundaries

**Reviewer:** Qwen (second review round — re-assigned after Claude capacity failure)
**Date:** 2026-04-13
**Verdict:** APPROVED

---

## Review Context

This is a second review round. The previous Codex review (`review_bg003_codex.md`) identified schema/example misalignment and README drift. Codex subsequently fixed all reported issues. The previous Qwen review (`review_bg003_qwen.md`) approved the corrected artifacts. I have re-verified all artifacts from scratch.

## Acceptance Criteria Verification

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | 5 JSON Schema draft-07 files produced and validated | ✅ PASS | All 5 schemas pass `validate_schemas.py` Draft-07 validation |
| 2 | All schemas contain required common fields | ✅ PASS | `strategy_id`, `artifact_id`, `version`, `evaluated_at`, `input_refs`, `output_refs`, `decision_reasoning`, `model_ref` present and required in all 5 |
| 3 | Five-stage chain example validates end-to-end | ✅ PASS | `five_stage_chain.json` validates against all 5 schemas with correct cross-references |
| 4 | Chain links to ApprovalDecision back-half gate | ✅ PASS | `RiskAdjudication.output_refs` enum includes `approval_decision`; chain example links to `approval-20260413-001` |
| 5 | BG-000 market scope + BG-001 master refs referenced | ✅ PASS | `market_scope` objects in 4/5 schemas; `security_master_ref`/`contract_master_ref` in UniverseSelection, SignalInference; `input_refs` include all BG-001 ref types |
| 6 | Research vs decision provenance distinguished | ✅ PASS | `model_ref` descriptions explicitly state "Distinguishes first-class decision provenance from raw research input/artifacts" |

## Additional Checks Performed

1. **Legacy field name audit**: Searched all `.json` files for old field names (`regime_classification`, `selected_instruments`, `allocation_decision_id`, `signal_inference_id`, `risk_adjudication_id`, `risk_outcome`). None found in current artifacts — only present in Codex's historical review notes.
2. **README alignment**: README uses canonical field names from the schemas; no legacy terms remain.
3. **`validate_schemas.py` coverage**: The validator now performs full Draft-07 instance validation against schemas for single-stage examples AND the five-stage chain, plus cross-stage reference closure checks and single-stage/chain mirroring checks.
4. **`validate_bg003.py`**: All 12 acceptance criteria pass (18/18 sub-checks PASS, 0 FAIL).

## Schema Assessment

### RegimeState
- `regime_class` enum comprehensive; covers standard regime taxonomy
- Conditional rule: `confidence ≤ 0.3 → regime_transition/mixed` is a good uncertainty guardrail
- `feature_snapshot` structured sub-object enables replay without raw feature dumps
- Lifecycle `state` + conditional `superseded_by` requirement is correct

### UniverseSelection
- `regime_ref` properly links upstream RegimeState
- `exclusion_reasons` with enumerated reasons — critical for audit/replay
- Empty-universe reasoning requirement is good policy enforcement
- `eligibility_flags` enum covers liquidity, history, regime, risk-budget, data-quality, and trading-hours dimensions

### SignalInference
- `direction` enum (`long`, `short`, `neutral`, `exit`) aligns with execution plane vocabulary
- `universe_ref` constrains signals to eligible instruments
- `inference_stats` provides batch-level summary
- `market_scope` inheritance with BG-001 refs for replay scope

### AllocationDecision
- Clear semantic boundary from ApprovalDecision (calculation vs governance)
- Preserves both original signal direction and allocated direction for audit
- `portfolio_summary` at aggregate level — useful for risk consumers
- `constraints_applied` enum documents active policy constraints

### RiskAdjudication
- Per-check `status` enum (`pass`/`warn`/`fail`/`skipped`) — comprehensive
- `stress_test_results` separate from policy checks — good separation of concerns
- Conditional validation: `approved_with_conditions → conditions.minItems ≥ 1`; `rejected → decision_reasoning.minLength ≥ 10`
- `output_refs` links to `approval_decision`, `deployment_plan`, `runtime_binding`, `evolution_decision` — properly closes front-half to back-half chain

## Chain Integrity

The five-stage chain example demonstrates:
1. Correct ID propagation through all stages
2. `output_refs` / cross-stage linkage consistency
3. Timestamp progression (09:30:00 → 09:30:05 → 09:30:10 → 09:30:15 → 09:30:20)
4. Consistent `strategy_id`, `persona_id`, `capital_pool_id` across all stages
5. RiskAdjudication output_refs correctly links to `approval-20260413-001`

## Non-blocking Notes

- `regime_scores` in the example sums to >1.0 (2.40 total). The schema doesn't enforce sum-to-1. This appears to be raw scores rather than normalized probabilities, which is acceptable but could benefit from a clarifying description update if downstream consumers expect probabilities.
- `SignalInference.model_ref` is required while `RiskAdjudication.model_ref` is optional. Reasonable since risk checks may be purely rule-based.
- The `regime_class` enum mixes orthogonal dimensions (e.g., `risk_on`/`risk_off` are directional, `trending_up`/`mean_reverting` are behavioral, `high_volatility`/`low_volatility` are volatility regimes). This is a design-level observation, not a schema correctness issue.

## Conclusion

All acceptance criteria pass. Schemas are well-structured, examples validate end-to-end, the provenance chain is correct, and the front-half to back-half linkage is properly established. No blocking issues found.

**Approving BG-003.**
