# Experiment Contracts

`ExperimentTask` and `ExperimentRun` are the schema-backed contracts for the
research orchestrator path from `StrategySpec` to experiment output.

The contracts intentionally stop at the research plane:

- `ExperimentTask` binds one `strategy_id` plus one `strategy_spec_version` to
  pinned `dataset_version_id` and `code_version` inputs.
- `ExperimentRun` preserves the task, strategy, StrategySpec version, dataset,
  code, input manifest, output manifest, backend, and trace refs needed for
  later CandidateArtifact packaging.
- The models validate lifecycle invariants, but they do not launch adapters,
  approve artifacts, create deployment plans, or route to execution runtimes.

`ExperimentRun` repeats `dataset_version_id` and `code_version` from the task so
downstream registry writeback can verify lineage without joining against mutable
task state.

## Registry Writeback

`registry_writeback.py` owns the controlled `ExperimentRun -> RegistryEntry`
mapping for completed research runs. It may create only `draft` or `candidate`
artifact registry entries, keeps `deployment_stage=none`, sets
`producer_run_id` to the ExperimentRun id, and preserves lineage back to the run,
StrategySpec ref, and dataset refs. Approval and deployment-stage writes remain
outside the research orchestrator boundary.
