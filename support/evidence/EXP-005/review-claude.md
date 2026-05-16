# EXP-005 Review — Claude

**Reviewer:** Claude
**Task:** EXP-005 ExperimentRun -> Artifact registry writeback
**Verdict:** Approved

## Acceptance Criteria Verification

**AC1 — Draft/candidate only, deployment_stage=none**

`build_registry_entry_from_experiment_run` reads `deployment_stage` from both `registry_hints` and the artifact dict, defaults to "none", and raises `ExperimentRegistryWritebackError` if != "none". `_artifact_state()` validates against `_ALLOWED_WRITEBACK_STATES = {DRAFT, CANDIDATE}` and raises for "approved" or any other state. The HTTP handler (`writeback_run_artifact`) also checks run status=completed before invoking writeback, returning 409 otherwise.

**AC2 — producer_run_id + lineage**

`producer_run_id=run.run_id` is passed directly to `RegistryEntryCreate`. `_lineage()` populates:
- `source_run_ids=[run.run_id]`
- `source_strategy_spec_id` from explicit param, hints, artifact, run.metadata, or fallback `{strategy_id}@{version}`
- `source_dataset_refs` from explicit param, hints, artifact, or `run.dataset_version_id`

**AC3 — Rejections**

- Non-completed run: checked at entry of `build_registry_entry_from_experiment_run` (raises) and at HTTP layer (409)
- approved artifact_state: `_artifact_state()` guard
- non-none deployment_stage: explicit check with raise
- HTTP endpoint idempotency: `idempotency_key` in `registry_writebacks` list

**AC4 — Tests pass**

```
python3 -m pytest services/research/experiments/test_registry_writeback.py \
  services/research/tests/test_research_orchestrator_http_service.py \
  services/registry/test_service.py -q
56 passed in 65.71s
```

## Non-blocking Observations

1. `write_experiment_run_artifact_to_registry` raises rather than returns if registry entry already exists (check-and-throw). HTTP-level idempotency via `idempotency_key` mitigates this for the primary consumer. Acceptable.
2. `_metadata` `override` dict could inject an arbitrary `deployment_stage` key into metadata, but this is a descriptive metadata field only; the registry entry's actual deployment_stage is controlled by the registry service and is never influenced by the metadata dict. Not a safety issue.
3. No test explicitly exercises deployment_stage appearing on the artifact dict directly (outside hints), but the code checks `artifact.get("deployment_stage")` alongside hints, and the safety boundary holds.

## Summary

Safety invariants are correctly enforced at both library and HTTP layers. Lineage is complete and traceable. Idempotency is covered. All acceptance criteria met. Returned to Codex for finalization.
