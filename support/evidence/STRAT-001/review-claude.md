# STRAT-001 Review — Claude

Task: StrategySpec schema / model
Owner: Codex
Reviewer: Claude
Date: 2026-05-16

## Verdict: Approved

No blocking findings.

## Files Reviewed

- `services/research/strategy_spec/models.py` (new)
- `services/research/strategy_spec/test_models.py` (new)
- `services/control-plane/specs/strategy_spec.schema.json` (modified)
- `services/research/strategy_spec/__init__.py` (modified)
- `services/research/strategy_spec/normalizer.py` (modified)
- `services/research/strategy_spec/README.md` (modified)
- `support/evidence/STRAT-001/README.md`

## Verification

```
python3 -m py_compile services/research/strategy_spec/models.py \
    services/research/strategy_spec/normalizer.py \
    services/research/strategy_spec/__init__.py
# -> passed

python3 -m pytest services/research/strategy_spec -q
# -> 18 passed (10 at submission time; 8 additional from STRAT-003/STRAT-004 built on this foundation)

# Live constraint checks:
# - live/canary/paper + approval_required=false -> StrategySpecValidationError (correct)
# - additionalProperties disallowed at root -> StrategySpecValidationError (correct)
```

## Findings

### Schema (strategy_spec.schema.json)

- `minLength: 1` applied to `strategy_id`, `title`, `hypothesis`, `objective`, and all sub-object string fields. Correct.
- `additionalProperties: false` on root and all sub-objects (`market_scope`, `data_dependencies[]`, `execution_profile`, `evaluation_plan`, `governance`, `provenance`, `evidence_refs[]`, `code_refs[]`). Correct.
- `lifecycle_state` enum covers all expected states (draft/candidate/review/approved/archived/rejected/superseded). Optional at schema level; semantics enforced at model layer.
- `source_record` added to `data_dependencies[].kind` enum. Correctly aligns with the `StrategyDataDependencyKind.SOURCE_RECORD` enum value.
- `execution_mode_hint` enum (research/paper/canary/live) correctly listed as optional in schema; governance constraint is enforced at model layer via `validate_strategy_spec()`.

### Domain Model (models.py)

- All dataclasses are `frozen=True`. Correct for a read-oriented model with no execution authority.
- `validate_strategy_spec_payload` delegates to `Draft7Validator` against the canonical JSON schema path. Schema path resolution uses `Path(__file__).resolve().parents[2] / "control-plane" / "specs"` which is correct for the repo layout.
- `validate_strategy_spec(spec)` provides semantic layer on top:
  - **Governance gate**: `paper/canary/live execution_mode_hint requires governance.approval_required=true` — confirmed working by live test above. Critical constraint correctly placed.
  - **Non-manual provenance gate**: `source_refs` required for non-manual `source_kind`. Correct.
  - **Duplicate detection**: data_dependencies, evidence_refs, code_refs all deduplicated by key tuple. Correct.
  - `code_refs[].line_end >= line_start` cross-field constraint enforced. Correct.
- `_coerce_enum` normalizes enum inputs to their `.value` string, so model fields always hold plain strings. This means `validate_strategy_spec` correctly compares `execution_mode_hint in {StrategyExecutionModeHint.PAPER.value, ...}` (string set comparison). No type mismatch.
- `StrategySpec.from_dict` defaults `validate_schema=True`; `validate_schema=False` bypass is available for internal callers that already trust their payloads. Acceptable design.
- `StrategySpec.__post_init__` calls `validate_strategy_spec(self)` — correct belt-and-suspenders after sub-object construction.
- Round-trip test (`spec.to_dict() == payload`) is a strong contract guarantee.

### Normalizer Compatibility (normalizer.py)

- `validate_strategy_spec` now delegates to `validate_strategy_spec_payload` from `models.py` (line 202), routing through the same canonical JSON schema. Compatible with existing RS-002 output shape. No regression.

### __init__.py

- All models, enums, and helpers are re-exported via `__all__`. Lineage helpers (`StrategySpecLineageRefs`, `attach_lineage_refs_to_strategy_spec_payload`, `build_strategy_spec_lineage_refs`) also included, pre-wiring for STRAT-004.

## No Blocking Issues

All required acceptance criteria for STRAT-001 are met:
- Schema-backed domain model present
- Canonical schema strengthened (lifecycle/source_record/canary/non-empty constraints)
- Model helpers exported
- RS-002 normalizer output compatible via shared schema validation
- Tests pass (7 model-specific + existing normalizer tests)
- Governance constraint enforced at model layer
