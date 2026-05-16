# EX-002-RB Review: Claude

Date: 2026-05-16
Reviewer: Claude
Owner: Codex
Task: EX-002-RB - Loader metadata migration promotion_state -> artifact_state + deployment_stage (rebaseline)

## Decision: APPROVED

## Scope Verified

All four acceptance criteria are met:

1. PromotionGate.build_execution_projection() emits artifact_state + deployment_stage + promotion_state (compat) - verified in gate.py lines 170-179 and test_gate_projection_emits_canonical_artifact_state_and_deployment_stage.
2. ArtifactLoader._validate_metadata() requires artifact_state=approved for canonical split metadata - verified in artifact_loader.py lines 285-296 and six regression tests in TestEX002RBLoaderMetadataMigration.
3. Legacy promotion_state-only metadata remains loadable - verified by test_loader_falls_back_to_legacy_promotion_state.
4. All test suites pass: 18 loader tests, smoke passed, 4 gate tests, 69 registry tests.

## Key Implementation Findings

### PromotionGate (gate.py)

- Lifecycle-to-artifact-state mapping: candidate->candidate, paper->approved, live->approved, retired->retired.
- Lifecycle-to-deployment-stage mapping: candidate->none, paper->paper, live->live, retired->frozen.
- promotion_state is preserved alongside new fields for transition compat.
- Live state guard: raises PromotionError if rollback is absent (line 198-199).

### ArtifactLoader (artifact_loader.py)

- Double guard logic is correct and exhaustive:
  - First check (line 287-291): artifact_state not in (None, "approved") rejects non-approved states (e.g. "candidate") regardless of deployment_stage presence.
  - Second check (line 292-296): has_canonical_stage and artifact_state != "approved" catches deployment_stage present but artifact_state=None (missing).
- Fallback (line 298): deployment_stage = metadata.get("deployment_stage") or metadata.get("promotion_state"). Correct precedence.
- Legacy path: artifact_state=None + deployment_stage=None passes both guards and falls back to promotion_state.

### Schema (promoted_artifact_metadata.schema.json)

- artifact_state and deployment_stage declared as optional properties with correct enums.
- allOf condition correctly migrated to deployment_stage=="live" (not promotion_state).
- additionalProperties: true preserves backward compat for existing metadata.

### Prior Review Gap (from Codex reopening)

The prior gap - loader accepting artifact_state=candidate + deployment_stage=paper - is fully closed. Three specific regression tests confirm:
- test_loader_rejects_split_metadata_without_approved_artifact_state (deployment_stage present, artifact_state missing)
- test_loader_rejects_unapproved_artifact_state_for_executable_stage (artifact_state=candidate + deployment_stage=paper)
- test_loader_rejects_legacy_stage_when_artifact_state_is_not_approved (artifact_state=candidate + promotion_state=paper)

## No Blocking Findings

The contract.md is updated correctly. Deferred items (algorithm-level LEAN run, artifact deserialization) are documented and scoped out of this task.
