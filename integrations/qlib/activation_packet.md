# Qlib Production Activation Packet

Last updated: 2026-04-24
Owner: APP-003-QLIB-ACTIVATION-001 (Codex2)
Reviewer: Codex
Status: prepared for first governed LightGBM alpha activation; data-gated

## 1. Purpose

This packet is the reviewable activation surface for the `Qlib` row after the
adapter and smoke baseline landed under `OSS-NEXT-001`.

It does two things:

1. records the current activation truth in one place
2. defines the exact evidence bundle required before the first governed
   production LightGBM alpha run may start

This packet does **not** claim that Qlib is production-activated today. It
formalizes that the remaining blockers are RS-003 and governed market-data
gates, not missing repo-local adapter code.

## 2. Current Disposition

Current row status remains `smoke-tested`.

Repo-local truth as of 2026-04-24:

- the governed Qlib adapter exists at `services/research/qlib/adapter/qlib_adapter.py`
- the default smoke path still passes via `python3 services/research/qlib/smoke_test.py`
- unit coverage still passes via
  `python3 -m unittest discover -s services/research/qlib -p 'test_*.py'`
- production activation is still blocked on entry gates from
  `services/learning/qlib/ACTIVATION_CRITERIA.md §1`

The gate is therefore cleared only in the truthful sense:

- all repo-local code gates are closed
- the first governed LightGBM activation packet is now prepared
- the actual production LightGBM run must wait until the upstream strategy and
  governed dataset gates are proven

## 3. Activation Gate Read

| Activation criterion | Current read | Evidence | Gap to close |
|---|---|---|---|
| RS-003 baseline StrategySpec candidate exists in registry | blocked | `services/learning/qlib/ACTIVATION_CRITERIA.md §1.1` and `§3.2` require a replication-gate-passed `candidate` artifact before Qlib training; this repo snapshot contains the RS-003 gate implementation but no task-local evidence naming the target candidate registry artifact for this activation | attach the governed StrategySpec / candidate artifact ID, the target strategy family, and the RS-003 pass evidence bundle |
| Governed dataset of ≥50 instruments with ≥2 years OHLCV history is available | blocked | `services/learning/qlib/ACTIVATION_CRITERIA.md §1.3` sets the threshold; `services/research/qlib/examples/equity_dataset_sample.json` is only a smoke sample (`dataset:equity-universe-top10-2024-daily`) and does not prove the production bar | attach a governed dataset manifest with universe size, date window, frequency, and dataset refs for the target run |
| Supervised alpha framing is documented for the target strategy | blocked | the gate doc defines the correct problem shape, and the sample dataset uses `strategy_id: equity-cross-sectional-alpha`, but no task-local packet yet cites the concrete governed StrategySpec that binds the target alpha statement, label definition, and universe | cite the target StrategySpec version and summarize why LightGBM supervised ranking/prediction is still the right fit |
| No upstream dependency conflicts | satisfied | `services/research/qlib/requirements.txt`, `integrations/qlib/integration.md`, and the passing smoke/unit baselines show the pinned package path is compatible with the current governed research stack | keep this revalidated when dependency pins change |

## 4. First Governed LightGBM Activation Bundle

Before the first production Qlib run starts, the owner should attach all of the
following evidence to the execution/review lane:

1. RS-003 candidate proof
   - candidate registry ID
   - strategy family / problem statement
   - replication-gate pass timestamp or evidence ref
2. governed dataset proof
   - dataset ref(s)
   - instrument count
   - history window
   - frequency and market scope
3. target-supervision proof
   - label definition
   - why supervised alpha is appropriate for this target
   - why RL / TRL are not the correct first lane
4. LightGBM run bundle
   - backend used (`QlibLightGBMBackend`)
   - config version and key hyperparameters
   - artifact checksum and storage path
   - holdout metrics / backtest summary
5. registry admission packet
   - canonical `artifact_state=draft`
   - `deployment_summary.current_stage=none`
   - lineage refs back to source dataset and source strategy spec

The governed output target remains unchanged:

- workflow entrypoint: `run_qlib_workflow()`
- artifact family: `qlib_alpha`
- registry artifact type: `model_artifact`
- initial registry state: `artifact_state=draft`
- deployment stage: `deployment_summary.current_stage=none`
- lifecycle for Qlib alpha artifacts: `draft` → `candidate` → `approved` → `retired`

## 5. Verification Snapshot

Revalidated in this session on 2026-04-24:

1. `python3 services/research/qlib/smoke_test.py`
   - Result: passed
   - Dataset: governed sample dataset from `services/research/qlib/examples/equity_dataset_sample.json`
   - Output confirms `artifact_state=draft`, `deployment_stage=none`, and
     governed storage under `research/qlib/`
2. `python3 -m unittest discover -s services/research/qlib -p 'test_*.py'`
   - Result: 14 tests passed

These checks prove the adapter is still runnable and governance-safe. They do
not satisfy the production activation thresholds by themselves.

## 6. Disposition

`Qlib` should remain `smoke-tested` in `OSS_INTEGRATION_CHECKLIST.md`.

The truthful next action is:

1. cite the exact RS-003 candidate artifact for the target alpha lane
2. attach the governed ≥50-instrument, ≥2-year OHLCV dataset manifest
3. bind the target StrategySpec and supervised label definition to that run
4. execute the first governed LightGBM activation through `QlibLightGBMBackend`
5. submit the resulting `qlib_alpha` artifact for registry admission

Until those five items exist, the row is prepared for activation but still
blocked from production use.
