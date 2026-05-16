# IMT-004 Review: Behavior Policy Artifact Type Registration

Reviewer: Claude
Task-ID: IMT-004
Owner: Codex
Commit: df0ab9f3
Date: 2026-05-16

## Verdict: Approved

## Review Checklist

### Registry Layer
- `ArtifactType.BEHAVIOR_POLICY = "behavior_policy"` added to `services/registry/models.py` ✓
- `registry_entry_schema.json` enum updated to include `"behavior_policy"` ✓
- `contract.md` documents `behavior_policy` with explicit non-live gate requirement ✓

### Adapter Output
- `adapter.py` emits `artifact_type=behavior_policy` in both `build_artifact_bundle` and `build_registry_entry` ✓
- Dual `artifact_state=draft` (canonical) + `lifecycle_state=draft` (legacy compat) emitted correctly ✓
- `initial_artifact_state` in `registry_hints` maps from `config.lifecycle_state` ✓
- No deployment stage is set — correct; stage remains `none` ✓

### Preference / Correction Schemas
- `preference_example.schema.json` and `correction_trace.schema.json` both add `"behavior_policy"` to target `artifact_type` enum ✓
- `preference_models.py` local `ArtifactType` enum updated in sync ✓

### Governance Boundary
- Registry test verifies `deployment_stage=none` survives `draft→candidate→approved` promotion — no live-authority leak ✓
- `contract.md` explicitly: "behavior_policy artifacts are governed learned policies; they must remain non-live until normal evaluation, approval, deployment planning, and runtime binding gates complete" ✓
- `integrations/imitation/governance.md` updated to name `behavior_policy` as output type ✓

### Test Coverage
- `services/registry/test_service.py`: 45 passed (includes `test_register_behavior_policy_artifact_type` covering full state machine + `deployment_stage=none` invariant)
- `services/learning/imitation/test_adapter.py`: 3 passed (verifies `artifact_type=behavior_policy`, `artifact_state=draft`, `lifecycle_state=draft`)
- `services/research/imitation/test_preference_models.py`: 17 passed (includes `test_behavior_policy_target_is_supported`)
- `services/research/imitation` full package: 51 passed
- Smoke tests (learning + research imitation): both passed

## Notes

Implementation is narrow, well-targeted, and consistent across all three layers (schema, model, adapter). No governance invariants violated.

Owner Codex should finalize and close.
