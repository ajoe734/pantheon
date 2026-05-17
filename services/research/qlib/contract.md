# Qlib Rolling OOS Contract

Task: OSS-QLIB-002

This module adds a research-only rolling-window out-of-sample path on top of
the governed Qlib dataset manifest evidence. It does not write registry truth,
open broker sessions, route orders, bind capital, or touch LEAN/runtime state.

## Entry Points

- `services/research/qlib/rolling_pipeline.py`
  - `run(strategy_spec_ref, window_days, **kwargs) -> dict`
  - Returns a schema-valid `ExperimentRun` payload.
  - `metadata.producer_run_id` is the ExperimentRun `run_id`.
  - `metadata.lineage` carries `strategy_spec_artifact_id`,
    `source_strategy_spec_id`, `dataset_manifest_id`, `dataset_manifest_ref`,
    and `source_dataset_refs`.
  - `metadata.evaluation_summary.oos_metrics` carries the OOS metrics and is
    linked back to the StrategySpec artifact id.

- `services/research/qlib/oos_eval.py`
  - `evaluate(experiment_run) -> dict`
  - Returns an OOS metrics dict with `sharpe`, `sortino`, `max_dd`, and `ic`.
  - `sharpe`, `sortino`, and `max_dd` use one cross-sectional average return
    per OOS date; `ic` uses observation-level predictions versus realized
    returns.

## Default Evidence Inputs

The default rolling run reads:

- dataset manifest: `support/evidence/MGMT-QLIB-001/dataset_manifest.json`
- StrategySpec packet: `support/evidence/MGMT-QLIB-002/strategy_spec_packet.json`
- smoke dataset: `services/research/qlib/examples/smoke_dataset.json`

The pipeline validates that the requested `strategy_spec_ref` matches the
dataset and manifest lineage before it builds OOS observations.

## ExperimentRun Placement

`ExperimentRun` rejects unknown top-level fields by schema, so the task-specific
writeback material lives in `metadata`:

- `metadata.producer_run_id`
- `metadata.lineage`
- `metadata.oos_observations`
- `metadata.oos_metrics`
- `metadata.evaluation_summary`

This keeps the output compatible with `services/research/experiments/models.py`
while preserving the producer and lineage fields needed by registry writeback.

## Smoke Command

```bash
PYTHONDONTWRITEBYTECODE=1 python3 services/research/qlib/rolling_pipeline.py \
  qlib-tw-cross-sectional-alpha-spec-v1 \
  --window-days 252 \
  --label-horizon-days 5
```

The printed `ExperimentRun` includes
`metadata.evaluation_summary.strategy_spec_artifact_id =
"qlib-tw-cross-sectional-alpha-spec-v1"` and keeps
`deployment_stage=none`.
