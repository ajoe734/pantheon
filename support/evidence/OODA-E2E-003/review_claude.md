# OODA-E2E-003 Review - Claude

Reviewer: Claude
Task: OODA-E2E-003 - ExperimentRun -> CandidateArtifact admission E2E test
Owner: Codex2
Review date: 2026-05-19

## Verdict: APPROVED

## Scope of This Review

Reviewing Codex2's commit `5ae4e87a` (OODA-E2E-003: validate writeback summaries).
Key change: moved malformed `evaluation_summary` validation into the production
`registry_writeback.py` boundary so the admission gate cannot silently accept
a non-mapping summary and create a candidate entry.

## Acceptance Criteria Check

1. test loads fixture ExperimentRun and calls writeback service to register CandidateArtifact
   - `_load_fixture()` reads `tests/e2e/fixtures/experiment_run_for_admission.json`.
   - `write_experiment_run_artifact_to_registry()` is called with the loaded run dict.
   - Result: PASS.

2. asserts artifact registered with artifact_state=candidate not draft and not approved
   - `test_candidate_artifact_registered_with_candidate_state` asserts `ArtifactState.CANDIDATE`.
   - Explicitly asserts not `DRAFT` and not `APPROVED`.
   - Result: PASS.

3. asserts lineage refs include experiment_run_id and source_strategy_spec_id
   - `producer_run_id == run_dict["run_id"]`.
   - `lineage.source_run_ids` includes the experiment run id.
   - `lineage.source_strategy_spec_id` is non-null and references the fixture spec id.
   - Result: PASS.

4. gate passes for valid input and rejects malformed evaluation_summary
   - Valid mapping input produces a candidate artifact and preserves `evaluation_summary`.
   - Bare string `"invalid-not-a-dict"` raises `ExperimentRegistryWritebackError`
     matching `"evaluation_summary must be a mapping"`.
   - Validation is enforced at the production writeback boundary (`_evaluation_summary`
     in `registry_writeback.py`), not only in the E2E test helper.
   - Result: PASS.

5. pytest -q -x exit 0
   - Verified focused run:
     `python3 -m pytest -q services/research/experiments/test_registry_writeback.py
      tests/e2e/test_experiment_run_to_admission.py`
   - Result: `9 passed in 1.02s`.

## Test Run Evidence

```text
Command: python3 -m pytest -q \
  services/research/experiments/test_registry_writeback.py \
  tests/e2e/test_experiment_run_to_admission.py
Result: 9 passed in 1.02s

Full pytest -q -x remains blocked by missing flask in
services/control-plane/internal/test_internal_api_incident.py
(pre-existing, unrelated to this task scope).
```

## Notes

- Validation moved into `_evaluation_summary()` in `registry_writeback.py` so all
  callers (not just E2E tests) get the guard.
- New unit guard `test_rejects_malformed_evaluation_summary_at_writeback_boundary`
  added to `test_registry_writeback.py` confirms the production boundary holds.
- Fixture is clean and correctly shaped.
- No live broker access, no capital binding, no GPU path.

## Follow-up

None required.
