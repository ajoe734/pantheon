# Promotion Gate (REG-002)

This directory is the canonical home for the promotion gate implementation.

Current contents:

- `gate.py`: lifecycle transition and metadata checks
- `cli.py`: CLI entrypoint for promoting a registry entry JSON document
- `test_gate.py`: service-local unit coverage for transition and projection behavior
- `smoke_test_gate.py`: end-to-end local smoke test for candidate/paper/live flow

Legacy compatibility files remain at repo root:

- `gate.py`
- `cli.py`

Those wrappers exist to avoid breaking older commands while task-board artifacts converge on the
service-local path.

## Scope

Canonical target semantics after `REG-004`:

- artifact-state transitions inside the registry
- candidate readiness checks
- approved-state governance checks
- retirement checks
- handoff into deployment planning, not direct paper/live lifecycle mutation

Current implementation note:

- `gate.py` and its tests still implement the earlier legacy model with `lifecycle_state` /
  `promotion_state`
- this README now documents the canonical target contract
- follow-on tasks `GOV-001`, `DEP-001`, and execution-side metadata migration will bring the code path into full alignment

## Canonical Artifact-State Rules

Allowed registry transitions now follow `artifact_state`, not deployment stage:

- `draft -> candidate`
- `candidate -> approved`
- `draft|candidate|approved -> retired`

Direct skips such as `candidate -> live` are category errors under the new model because `live`
is a deployment stage, not a registry state.

## Deployment Stage Is Separate

`paper`, `canary`, `live`, and `frozen` now belong to deployment/runtime semantics.

Canonical flow:

1. registry admits an artifact as `candidate`
2. governance advances the artifact to `approved`
3. `DeploymentPlan` chooses `paper`, `canary`, `live`, or `frozen`
4. `RuntimeBinding` records what is actually running

Consequences:

- an artifact may be `approved` while deployment stage is still `none`
- rollback rebinds runtime to another `approved` artifact; it is not an artifact-state transition
- `paper -> live` is no longer described as a registry promotion path

## Promotion Requirements

Canonical requirements are now split between artifact governance and deployment planning.

### Candidate

- `replication_success` must be truthy
- `lineage` must include at least one governed source reference:
  `source_run_ids`, `source_strategy_spec_id`, or `source_dataset_refs`
- legacy `lineage.source_run_id` is normalized to `lineage.source_run_ids`

### Approved

- the artifact must satisfy candidate requirements first
- `evaluation_summary` must be an object when the artifact is execution-capable
- `evaluation_summary.risk_review_passed` must be truthy for execution-capable artifacts
- `approval_decision_id` is the target canonical approval record once `GOV-001` lands
- `lineage` must still contain at least one source reference
- `rollback_target` should identify the default prior approved artifact for deployment rollback
- loader compatibility and pool/runtime compatibility must be available before any deployment plan is approved

### Deployment-stage gates after approval

After an artifact is `approved`, stage-specific rules move to deployment policy and runtime control:

- `paper` entry conditions come from `PAPER_CANARY_LIVE_POLICY.md` section 5
- `paper -> canary` thresholds come from `PAPER_CANARY_LIVE_POLICY.md` section 6
- `canary -> live` thresholds come from `PAPER_CANARY_LIVE_POLICY.md` section 7
- `frozen` is controlled by deployment / incident / evolution policy, not registry lifecycle

### Legacy live compatibility

While `gate.py` still runs the legacy `paper/live` promotion model, the old live checks remain a
compatibility constraint for the existing implementation:

- `approver` must be present
- `lineage` must still contain at least one source reference
- rollback metadata must exist in one of these forms:
  - canonical legacy shape: `metadata.rollback.target_registry_id` + `metadata.rollback.target_version`
  - temporary compatibility fallback: `metadata.rollback_target_registry_id` + top-level `rollback_target`
- rollback targets cannot point back to the same `registry_id` or `version`

Those rules should migrate into the `approved` + deployment-plan path rather than remain encoded as
`candidate -> paper -> live` registry state changes.

## Execution Projection

Canonical target projection after `REG-004`:

- registry contributes `artifact_state`
- deployment/runtime read model contributes `deployment_stage`
- loader must require `artifact_state=approved` plus an exact deployment-stage match for the
  runtime mode

The Object Store keys remain:

- `openclaw/registry/{strategy_id}/{version}/metadata.json`
- `openclaw/registry/{strategy_id}/{version}/artifact.bin`

Canonical target metadata should include:

- `registry_id`
- `strategy_id`
- `version`
- `artifact_type`
- `artifact_state`
- `deployment_stage`
- `checksum`
- `lineage`
- `created_at`
- `approved_at` when the artifact entered `approved`
- `approval_decision_id` once `GOV-001` lands
- `approver` only as a compatibility hint while approval is not yet first-class
- `rollback` for approved artifacts that may be deployed or rebound

Current implementation compatibility:

- `build_execution_projection()` in `gate.py` still emits legacy `promotion_state`
- current loader behavior still checks `promotion_state=paper|live`
- until those consumers migrate, treat `promotion_state` as a legacy alias and do not use it as
  canonical registry truth in new contracts

## CLI

Current executable CLI behavior still follows the legacy `gate.py` target states.

Promote an entry without overwriting the source file:

```bash
python3 services/registry/promotion/cli.py \
  --entry-file /tmp/registry-entry.json \
  --to paper \
  --approver "risk-committee"
```

Overwrite the registry entry in place:

```bash
python3 services/registry/promotion/cli.py \
  --entry-file /tmp/registry-entry.json \
  --to live \
  --approver "risk-committee" \
  --inplace
```

Compatibility warning:

- the current CLI implementation still accepts legacy targets implemented in `gate.py`
- the canonical contract target is narrowing toward artifact-state transitions only
- once the code path migrates, the registry-side CLI should advance `draft -> candidate -> approved` only
- stage changes should move into a future deployment-plan CLI or service boundary rather than stay in the registry promotion CLI

It does not replace:

- registry storage
- execution loader checks
- deployment planning / runtime binding
- experiment backend lineage

Those are defined in:

- `services/registry/contract.md`
- `services/registry/lineage/contract.md`
- `services/execution/artifact-loader/contract.md`
