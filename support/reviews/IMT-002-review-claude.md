# IMT-002 Review — Claude

**Reviewer:** Claude
**Task:** IMT-002 — PreferenceExample / CorrectionTrace schema
**Commit reviewed:** 908914b6
**Date:** 2026-05-16
**Outcome:** APPROVED

## Scope Confirmed

Validation-only schemas for preference-learning data. No registry writes, no runtime authority, no capital binding. Scope boundary is clean.

## Schema Review

### preference_example.schema.json

- Correct Draft-7 header (`$schema`, `$id`, `title`)
- `actor_role` restricted to `operator` / `approver` — human-only signal enforced at schema level
- `promotion_state` restricted to `candidate` / `paper` — live/canary states excluded
- `governedTrainingTarget` anyOf correctly requires at least one of: `registry_id`, `(artifact_version + artifact_type)`, or `(lineage_ref + artifact_type)`
- `allOf` if-then branches correctly enforce non-null artifact constraints per action:
  - `approve` → `chosen_artifact` must be non-null `artifactSnapshot`
  - `reject` → `rejected_artifact` must be non-null `artifactSnapshot`
  - `edit` → both artifacts non-null + `correction_trace_id` required
- `additionalProperties: false` enforced at root and `governedTrainingTarget` — no unknown fields permitted

### correction_trace.schema.json

- Same Draft-7 header and governance target definition as above (consistent)
- `operations` has `minItems: 1` — non-noop enforced at schema level
- `correctionOperation` allows `replace / append / remove / annotate` — adequate set for correction semantics
- `additionalProperties: false` at root, target, and operation level

## Python Model Review

- Both dataclasses are `frozen=True` — immutable after construction
- `__post_init__` validates all fields and calls `validate_*_payload(self.to_dict())` — schema validation is always exercised on construction
- Governance invariants duplicated at model level as defense-in-depth:
  - approve/reject/edit pair semantics
  - `target.strategy_id == self.strategy_id` consistency check
  - `before_artifact != after_artifact` via stable JSON comparison
  - `operations` non-empty check
- `_stable_json()` uses `sort_keys=True` for deterministic artifact comparison — correct
- `from_dict()` / `to_dict()` round-trip symmetry confirmed by tests
- Enum coercion via `_coerce_enum()` gives friendly validation error messages
- `validate_preference_example_against_correction_trace()` covers all six cross-object lineage fields

## Test Coverage

16 focused tests in `test_preference_models.py`:

| Test | Coverage |
|---|---|
| Draft-7 schema self-validation | Both schemas valid |
| Round-trip through model and schema | PreferenceExample, CorrectionTrace |
| approve requires chosen_artifact | Enforced |
| reject requires rejected_artifact | Enforced |
| edit requires correction_trace_id | Enforced |
| System actor_role rejected | Enforced |
| Live promotion_state rejected | Enforced |
| target.strategy_id mismatch rejected | Enforced |
| Empty operations rejected | Enforced |
| Noop before/after artifact rejected | Enforced |
| lineage_ref + artifact_type target | Accepted |
| Target missing governed linkage | Rejected |
| Cross-validation matching edit trace | Returns no errors |
| Cross-validation lineage mismatch | Reports correct error |
| Unknown top-level field rejected | Enforced |

40/40 full imitation package tests pass without regression.

## Verification

```
python3 -m py_compile services/research/imitation/preference_models.py ✅
python3 -m json.tool preference_example.schema.json ✅
python3 -m json.tool correction_trace.schema.json ✅
pytest services/research/imitation/test_preference_models.py -q → 16 passed ✅
pytest services/research/imitation -q → 40 passed ✅
```

## Verdict

No issues found. Governance invariants are correctly enforced at both schema and model level. Scope boundary is clean. All verification passes. Returning to Codex for finalization.
