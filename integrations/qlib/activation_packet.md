# Qlib Production Activation Packet

Last updated: 2026-04-30
Owner: SVC-QLIB-ACTIVATION-READY-ADAPTER (Codex)
Reviewer: Claude2
Status: activation-ready behind explicit offline gates; production remains data-gated

## 1. Purpose

This packet is the reviewable activation surface for the `Qlib` row after the
adapter moved from smoke-only packaging to an activation-ready offline worker.

It does two things:

1. records the current activation truth in one place
2. defines the exact evidence bundle required before the first governed
   production LightGBM alpha run may start

This packet does **not** claim that Qlib is production-activated today. It
formalizes that the repo-local adapter, worker, artifact handoff, and gateway
offline execution path are ready, while production remains blocked on RS-003,
governed market-data, and StrategySpec evidence gates.

## 2. Current Disposition

Current checklist row status remains `smoke-tested` because the checklist has no
separate `activation-ready` state and production activation is not open.

Repo-local truth as of 2026-04-30:

- the governed Qlib adapter exists at `services/research/qlib/adapter/qlib_adapter.py`
- the offline pre-activation preflight scaffold exists at `services/research/qlib/preflight.py`
- `validate_activation_ready_dataset()` enforces the >=50 instrument, >=2 year,
  >=504 daily-period production data floors before training when
  `enforce_activation_ready=True`
- `persist_qlib_run_artifacts()` writes `artifact_bundle.json`, `registry_entry.json`,
  `candidate_packet.json`, and `manifest.json` without writing registry truth
- `services/research/qlib/worker.py` is fail-closed unless
  `PANTHEON_QLIB_ACTIVATION_READY_ENABLED=1` is set, and it requires explicit
  `QLIB_BACKEND=stub|real`
- selecting `QLIB_BACKEND=real` runs `QlibLightGBMBackend` or returns the explicit
  `Install services/research/qlib/requirements.txt first` error
- `services/research-worker-gateway` can execute the Qlib worker only under
  `PANTHEON_OFFLINE_GATE_ENABLED=true`; production/paper/canary/live remain
  rejected
- the default smoke path still passes via `python3 services/research/qlib/smoke_test.py`
- unit coverage still passes via
  `python3 -m unittest discover -s services/research/qlib -p 'test_*.py'`
- production activation is still blocked on entry gates from
  `services/learning/qlib/ACTIVATION_CRITERIA.md §1`

The gate is therefore cleared only in the truthful sense:

- all repo-local safety gates are closed by default
- the first governed LightGBM activation-ready handoff is now prepared
- the actual production LightGBM run must wait until the upstream strategy and
  governed dataset gates are proven

## 3. Activation Gate Read

| Activation criterion | Current read | Evidence | Gap to close |
|---|---|---|---|
| RS-003 baseline StrategySpec candidate exists in registry | blocked | `services/learning/qlib/ACTIVATION_CRITERIA.md §1.1` and `§3.2` require a replication-gate-passed `candidate` artifact before Qlib training; this repo snapshot contains the RS-003 gate implementation but no task-local evidence naming the target candidate registry artifact for this activation | attach the governed StrategySpec / candidate artifact ID, the target strategy family, and the RS-003 pass evidence bundle |
| Governed dataset of ≥50 instruments with ≥2 years OHLCV history is available | blocked | `services/learning/qlib/ACTIVATION_CRITERIA.md §1.3` sets the threshold; `services/research/qlib/examples/equity_dataset_sample.json` is only a smoke sample (`dataset:equity-universe-top10-2024-daily`) and does not prove the production bar | attach a governed dataset manifest with universe size, date window, frequency, and dataset refs for the target run |
| Supervised alpha framing is documented for the target strategy | blocked | the gate doc defines the correct problem shape, and the sample dataset uses `strategy_id: equity-cross-sectional-alpha`, but no task-local packet yet cites the concrete governed StrategySpec that binds the target alpha statement, label definition, and universe | cite the target StrategySpec version and summarize why LightGBM supervised ranking/prediction is still the right fit |
| No upstream dependency conflicts | satisfied | `services/research/qlib/requirements.txt`, `integrations/qlib/integration.md`, and the passing smoke/unit baselines show the pinned package path is compatible with the current governed research stack | keep this revalidated when dependency pins change |

## 3.1 Offline Preflight

`services/research/qlib/preflight.py` provides a repo-local readiness report for
the three production activation blockers above:

1. RS-003 candidate registry ref and pass evidence
2. governed dataset manifest with >=50 instruments, >=2 years of OHLCV history,
   allowed frequency, and lineage refs
3. concrete StrategySpec binding with supervised label/target framing

The preflight is deliberately non-writing and fail-closed. Missing probes return
`activation_allowed=false`; they do not query or update registry/governance, and
they do not execute `QlibLightGBMBackend` or the production LightGBM path.

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
   - non-writing `candidate_packet` requesting only `draft -> candidate`
   - artifact manifest with checksum and paths for the persisted handoff files

The governed output target remains unchanged:

- workflow entrypoint: `run_qlib_workflow()`
- artifact family: `qlib_alpha`
- registry artifact type: `model_artifact`
- initial registry state: `artifact_state=draft`
- deployment stage: `deployment_summary.current_stage=none`
- lifecycle for Qlib alpha artifacts: `draft` → `candidate` → `approved` → `retired`

## 5. Verification Snapshot

Revalidated in this session on 2026-04-30:

1. `python3 services/research/qlib/smoke_test.py`
   - Result: passed
   - Dataset: governed sample dataset from `services/research/qlib/examples/equity_dataset_sample.json`
   - Output confirms `artifact_state=draft`, `deployment_stage=none`, and
     governed storage under `research/qlib/`
2. `python3 -m unittest discover -s services/research/qlib -p 'test_*.py'`
   - Result: 28 tests passed, including preflight, activation-ready data floors,
     candidate packet, persistence, explicit backend error, and fail-closed worker checks
3. `pytest -q services/research-worker-gateway/tests/test_research_worker_gateway_qlib_activation.py`
   - Result: 2 tests passed
   - Closed gate: Qlib offline dispatch is rejected
   - Open gate: Qlib worker runs with explicit env gate, enforces data floors,
     persists handoff artifacts, and leaves production activation disabled
4. `python3 -m pytest -q services/research-worker-gateway/tests/test_research_worker_gateway_qlib_activation.py services/research-worker-gateway/tests/test_research_worker_gateway_gate_dispatch.py`
   - Result: 11 tests passed
   - Confirms closed-gate rejection, open-gate offline subprocess execution,
     stdout/stderr/exit-code persistence, capability gate metadata, and
     paper/canary/live fail-closed behavior
5. `python3 -m pytest -q services/research-worker-gateway/tests/test_research_worker_gateway_rejection_policy.py services/research-worker-gateway/tests/test_research_worker_gateway_http_service.py`
   - Result: 9 tests passed
   - Confirms the pre-existing rejection and HTTP contract still fail closed

These checks prove the adapter is activation-ready for offline gated research
handoff and governance-safe. They do not satisfy the production activation
thresholds by themselves.

## 6. Disposition

`Qlib` should remain `smoke-tested` in `OSS_INTEGRATION_CHECKLIST.md`.

The truthful next action is:

1. cite the exact RS-003 candidate artifact for the target alpha lane
2. attach the governed ≥50-instrument, ≥2-year OHLCV dataset manifest
3. bind the target StrategySpec and supervised label definition to that run
4. execute the first governed LightGBM activation through `QlibLightGBMBackend`
5. submit the resulting `qlib_alpha` artifact for registry admission

Until those five items exist, the row is activation-ready behind offline gates
but still blocked from production use.
