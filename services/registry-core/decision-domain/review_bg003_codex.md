# BG-003 Review — Codex

Status: changes requested

## Findings

1. `examples/five_stage_chain.json` does not validate against the published schemas, so the core acceptance claim is currently false.
   RegimeState example uses `regime_classification` / `regime_features` and omits required `regime_class`, `window_start`, and `window_end` (`examples/five_stage_chain.json:37-57` vs `regime_state.schema.json:7-20`, `40-84`).
   UniverseSelection example uses `selected_instruments` instead of `selected_universe`, omits required `exclusion_reasons`, and includes `feature_dataset` in `input_refs` even though that `ref_type` is not allowed by the schema (`examples/five_stage_chain.json:101-143` vs `universe_selection.schema.json:7-19`, `57-140`).
   SignalInference example signal entries use `instrument_id`, `signal_value`, and `signal_strength`, but the schema requires `symbol`, `direction`, `magnitude`, and `confidence` (`examples/five_stage_chain.json:191-255` vs `signal_inference.schema.json:69-124`).
   AllocationDecision example uses `allocation_decision_id`, `signal_inference_id`, `weight`, and `notional_target`, while the schema requires `allocation_id`, `signal_ref`, `target_weight`, and `target_value` (`examples/five_stage_chain.json:281-366` vs `allocation_decision.schema.json:7-18`, `54-145`).
   RiskAdjudication example uses `risk_adjudication_id`, `allocation_decision_id`, `risk_outcome`, and `risk_checks`, while the schema requires `adjudication_id`, `allocation_ref`, `risk_assessment`, and `verdict` (`examples/five_stage_chain.json:381-495` vs `risk_adjudication.schema.json:7-18`, `38-166`).
   I reproduced this with `jsonschema.Draft7Validator`; all five stages fail their own schema.

2. `services/registry-core/decision-domain/README.md` documents the same old field names as the invalid example instead of the actual schema contract.
   The README still advertises `regime_classification`, `selected_instruments`, `allocation_decision_id`, `signal_inference_id`, `risk_adjudication_id`, and `risk_outcome` as required fields (`README.md:44`, `69`, `93`, `113-145`), but those are not the canonical schema keys.
   That leaves the task with two incompatible sources of truth: the schema files and the human-facing Decision Layer Object Map.

3. The attached validation evidence does not actually prove the failing acceptance criteria.
   `validate_schemas.py` only checks draft-07 headers and common-field presence; it never validates `examples/five_stage_chain.json` against the schemas (`validate_schemas.py:28-43`, `77-124`).
   `scripts/validate_bg003.py` likewise does not validate the example payloads end-to-end, and it already reports FAIL on criterion 9 and criterion 10 (`scripts/validate_bg003.py:109-170`, `206-209`).
   Given the task brief claims "All 12 acceptance criteria met", the acceptance evidence is currently not defensible.

## Required fixes before re-handoff

1. Choose one contract shape and align all three surfaces to it: schema files, `README.md`, and `examples/five_stage_chain.json`.
2. Add a real end-to-end validator that loads each stage example and validates it against its corresponding Draft-07 schema.
3. Re-run validation and re-handoff only after the example chain passes and the README reflects the same field names as the schemas.
