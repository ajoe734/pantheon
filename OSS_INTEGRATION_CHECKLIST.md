# OSS Integration Checklist

Last updated: 2026-04-29
Status: execution checklist for upstream OSS components referenced by the OpenClaw target architecture

## Purpose

This file turns named upstream OSS components into a concrete integration checklist.

For each component, we track whether we have:

1. selected the upstream project
2. pinned a version or commit
3. added the dependency or repo integration path
4. built a local adapter
5. defined governed I/O boundaries
6. run a smoke test

Do not treat a component as integrated just because we wrote contracts around it.

## Checklist Status Codes

- `not-started`
- `source-selected`
- `version-pinned`
- `dependency-added`
- `adapter-started`
- `criteria-defined` (deferred framework with explicit entry criteria documented)
- `smoke-tested`
- `governed`

## Component Inventory

| Component | Upstream Type | Current Status | What still needs to happen |
|---|---|---|---|
| `OpenClaw` | upstream repo/runtime | `governed` | Source pin remains locked to `openclaw/openclaw` tag `v2026.4.7` / commit `5050017543011b61df67744ebc6368d889c25a95`, the runtime artifact remains pinned to `ghcr.io/openclaw/openclaw:2026.4.7`, the runnable Pantheon-side adapter now lives under `integrations/openclaw/adapter/`, `docker-compose.yml` exposes an `openclaw-gateway` runtime dependency path (profile `openclaw`), and the fail-closed repo-authoritative runtime-adoption scaffold is landed. `bash scripts/openclaw-smoke-test.sh` revalidated the baseline pin on 2026-04-17, and `bash scripts/openclaw-gateway-adapter-smoke.sh` passed a real upstream gateway smoke on 2026-04-17 for `pantheon.ingest`, `pantheon.review`, `pantheon.retrain`, and `pantheon.deploy`. This is not broker-session, paper/canary/live, execution-kernel, or capital-binding activation. |
| `DSPy` | Python package/framework | `governed` | v2.4.5 pinned; runnable adapter lives in `services/learning/dspy/`; canonical evidence is now in `integrations/dspy/{integration,governance,smoke_test}.md`; smoke test and unit coverage were refreshed on 2026-04-17. |
| `TRL` | Python package/framework | `smoke-tested` | `trl>=0.8.0,<0.10.0` pinned in `services/learning/trl/requirements.txt`; governed preference-pair adapter (`GovernedPreferencePairAdapter`, `StubDPOBackend`, `TRLDPOBackend`, `run_trl_dpo_workflow`) in `services/learning/trl/adapter/`; non-writing pre-activation preflight scaffold exists in `services/learning/trl/preflight.py`; activation evidence harness exists in `services/learning/trl/activation_smoke.py` and produced task evidence under `support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/`: 240 governed FB-002 bounded events, 240 preference pairs, 3 strategy families, all approve/edit/reject actions, evaluator packet, registry entry, candidate packet, checksum-bearing artifact bundle, and explicit real-backend dependency/config evidence (`ModuleNotFoundError: No module named 'trl'`, `silent_stub_fallback=false`); registry output uses canonical `artifact_state=draft` + `deployment_summary.current_stage=none`; evidence in `integrations/trl/{integration,governance,smoke_test,activation_packet}.md`; task: OSS-NEXT-002 (Claude); follow-up `P2-TRL-RUNTIME-DATA-ACTIVATION-001` is reviewer-approved for bounded FB-002 runtime-data activation evidence and real DPO install/config evidence; TRL artifacts are non-executable governed models (`draft` → `candidate` → `approved`), not `paper/live` execution states; see `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §2` |
| `Qlib` | Python package/framework | `smoke-tested` | activation criteria documented in `services/learning/qlib/ACTIVATION_CRITERIA.md`; `pyqlib==0.9.6` pinned in `services/research/qlib/requirements.txt`; governed data-handler adapter built in `services/research/qlib/adapter/` (`GovernedQlibDataAdapter`, `StubLightGBMBackend`, `QlibLightGBMBackend`, `validate_activation_ready_dataset`, `persist_qlib_run_artifacts`, `run_qlib_workflow`); production-data packet helpers (`validate_production_dataset_proof`, `build_production_activation_packet`, `production_activation_smoke.py`) now require provider, entitlement/license, freshness, PIT, durable storage, rate-limit/audit, and no-order-route proof before producing a candidate handoff; activation-ready offline worker is explicit-gated behind `PANTHEON_QLIB_ACTIVATION_READY_ENABLED=1`, requires explicit `QLIB_BACKEND=stub|real`, returns an explicit install error for unavailable real Qlib backend, enforces production data floors before training when requested, persists checksum-bearing artifact/registry/candidate handoff files, and never writes registry truth or routes directly to orders; gateway Qlib offline dispatch is additionally gated by `PANTHEON_OFFLINE_GATE_ENABLED=true`; smoke/unit tests pass (32 unit tests + smoke assertions OK, revalidated 2026-05-01); gateway activation/dispatch and rejection/http tests remain the activation-ready baseline from 2026-04-30; registry output uses canonical `artifact_state=draft` + `deployment_summary.current_stage=none` and candidate packet requests only `draft -> candidate`; evidence in `integrations/qlib/{integration,governance,smoke_test,activation_packet}.md`; task: `P2-QLIB-PROD-DATA-ACTIVATION-001` produced the governed production-data proof contract and real/stub-selectable backend smoke without order routing; row remains `smoke-tested` until registry review admits a real production data packet |
| `FinRL` | upstream repo/package | `smoke-tested` | `finrl==0.3.6` is pinned in `services/research/finrl/requirements.txt`; FinRL Dockerfile, adapter, worker, examples, deferred-prep smoke path, and activation evidence harness (`services/research/finrl/activation_smoke.py`) exist under `services/research/finrl`; activation smoke ran on 2026-05-01 via `python3 services/research/finrl/activation_smoke.py --enable-activation-ready --backend real`: real `finrl_ppo` backend recorded explicit `ModuleNotFoundError: No module named 'finrl'` (`silent_stub_fallback=false`), stub fallback produced checksum-bearing artifact bundle, evaluator packet, registry entry, and candidate packet; evidence in `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/`; `deployment_stage=none`, `gate_state=closed`, no broker session, no order routing, no paper/canary/live promotion, no capital binding; evidence produced for task `P2-RL-UPSTREAM-RUNTIME-SMOKE-001` |
| `RLlib` | Python package/framework | `smoke-tested` | `ray[rllib]>=2.9.0,<3.0.0` is pinned in `services/research/rllib/requirements.txt` alongside Ray Tune; `services/research/rllib` contains train/eval adapter, worker, sample dataset, deferred-prep smoke path, and activation evidence harness (`services/research/rllib/activation_smoke.py`); activation smoke ran on 2026-05-01 via `python3 services/research/rllib/activation_smoke.py --enable-activation-ready --backend real`: real `rllib_ppo` backend recorded explicit `ModuleNotFoundError: No module named 'ray'` (`silent_stub_fallback=false`), stub fallback produced train/eval rollout evidence with 18 train steps / 9 eval steps, flattened_observation_dim=72, joint_action_cardinality=125, checksum-bearing artifact bundle, evaluator packet, registry entry, and candidate packet; evidence in `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/`; `deployment_stage=none`, `gate_state=closed`, no registry-writing production train/eval path or order-capable route open by default; evidence produced for task `P2-RL-UPSTREAM-RUNTIME-SMOKE-001` |
| `Ray Tune` | Python package/framework | `smoke-tested` | `ray[tune]>=2.9.0,<3.0.0` pinned in `services/research/rllib/requirements.txt` alongside RLlib; `services/research/rllib` contains search-output adapter, worker, deferred-prep smoke path, and activation evidence harness (`services/research/rllib/ray_tune_activation_smoke.py`); activation smoke ran on 2026-05-01 via `python3 services/research/rllib/ray_tune_activation_smoke.py --enable-activation-ready --backend real`: real `ray_tune_search` backend recorded explicit `ModuleNotFoundError: No module named 'ray'` (`silent_stub_fallback=false`), stub fallback produced 8-trial hyperparameter search evidence with top-3 candidates, best_trial validation_sharpe_proxy=1.037, checksum-bearing optimizer_result bundle, evaluator packet, registry entry, and candidate packet; evidence in `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/`; `deployment_stage=none`, `gate_state=closed`, research-only optimizer_result output; evidence produced for task `P2-RL-UPSTREAM-RUNTIME-SMOKE-001` |
| `vectorbt` | Python package/framework | `governed` | `vectorbt==0.26.2` pinned in `services/research/vectorbt/requirements.txt`; `GovernedVectorbtInputAdapter`, `StubVectorbtBackend`, and `VectorbtBackend` implemented in `services/research/vectorbt/adapter/vectorbt_adapter.py`; smoke test revalidated on 2026-04-18 via `python3 services/research/vectorbt/smoke_test.py` (MA-crossover backtest, 2 instruments, 35 bars each) and unit coverage revalidated on 2026-04-18 via `python3 -m pytest services/research/vectorbt/test_adapter.py -q` => `28 passed, 5 subtests passed`; governed artifact envelope confirmed (`artifact_family=vectorbt_backtest`, `artifact_state=draft`, `direct_live_influence=false`, `lean_consumption=scoring_only_not_direct_action`); CI-safe stub backend is default; real backend remains gated behind `PANTHEON_VECTORBT_BACKEND=real`; canonical evidence pack now lives in `integrations/vectorbt/{integration,governance,smoke_test}.md` |
| `statsmodels` | Python package/framework | `governed` | `statsmodels==0.14.2` pinned in `services/research/statsmodels/requirements.txt`; `GovernedStatsmodelsInputAdapter`, `StubStatsmodelsBackend`, and `StatsmodelsBackend` implemented in `services/research/statsmodels/adapter/statsmodels_adapter.py`; smoke test revalidated on 2026-04-18 via `python3 services/research/statsmodels/smoke_test.py` (cointegration, VAR/VECM, Markov-switching all present) and unit coverage revalidated on 2026-04-18 via `python3 -m pytest services/research/statsmodels/test_adapter.py -q` => `20 passed`; governed artifact envelope confirmed (`artifact_family=regime_report`, `artifact_state=draft`, `direct_live_influence=false`, `lean_consumption=research_only_not_direct_action`); CI-safe stub backend remains the default; canonical evidence pack now lives in `integrations/statsmodels/{integration,governance,smoke_test}.md` |
| `QuantLib` | Python package/framework | `governed` | `QuantLib-Python==1.18` pinned in `services/research/quantlib/requirements.txt`; governed adapter implemented in `services/research/quantlib/adapter/` (`GovernedQuantLibInputAdapter`, `StubQuantLibBackend`, `QuantLibBackend`, `run_quantlib_workflow`); worker entrypoint and governed sample dataset now live in `services/research/quantlib/{worker.py,examples/pricing_dataset_sample.json}`; stub smoke revalidated on 2026-04-21 via `python3 services/research/quantlib/smoke_test.py`, default-workspace unit coverage revalidated on 2026-04-21 via `python3 -m pytest services/research/quantlib/test_adapter.py -q` => `17 passed, 1 skipped`, and worker fallback execution revalidated on 2026-04-21 via `python3 services/research/quantlib/worker.py`; recorded real-backend rerun from 2026-04-17 also passed (`18 passed` plus real smoke success); governed artifact envelope confirmed (`artifact_family=pricing_report`, `artifact_state=draft`, `direct_live_influence=false`, `lean_consumption=research_only_not_direct_action`); first approved scope remains derivatives pricing and fixed-income risk analytics; canonical evidence pack now lives in `integrations/quantlib/{integration,governance,smoke_test}.md` |
| `imitation` | Python package/framework | `governed` | v1.0.1 pinned; runnable BC adapter lives in `services/learning/imitation/`; canonical evidence is now in `integrations/imitation/{integration,governance,smoke_test}.md`; smoke test and unit coverage were refreshed on 2026-04-17. |
| `MLflow` | backend/service/package | `governed` | v3.10.1 pinned; runnable registry↔experiment adapter lives in `services/registry/experiments/`; canonical evidence is now in `integrations/mlflow/{integration,governance,smoke_test}.md`; smoke test and unit coverage were refreshed on 2026-04-17. |
| `W&B` | backend/service/package | `activation-gated` | Offline local-store scaffold is landed with `EXPERIMENT_BACKEND=wandb` selectable behind `PANTHEON_ENABLE_WANDB_OFFLINE_STORE=1` or the legacy deferred-prep flag; SDK-backed online sync now lives in `services/registry/experiments/adapter.py` as `WandbOnlineBackend`, with `wandb>=0.16.0,<1.0` pinned in `services/registry/experiments/requirements.txt`, `PANTHEON_WANDB_ONLINE_SYNC_ENABLED=1` required, and `smoke_test.py --backend wandb-online` uploading metrics/artifact bundles plus W&B API readback when a test project/API key are provided. Local workspace evidence on 2026-05-01 is a structured skip because the SDK and `WANDB_API_KEY` were absent; unit tests use a fake SDK to prove metadata/readback shape without persisting secrets. This is a non-ordering experiment backend path only; broker/order/capital paths remain out of scope and research-worker-gateway keeps W&B non-dispatchable. |

## Required Evidence Per Component

Each upstream integration should eventually produce these repo-local artifacts:

1. `integration.md`
   - selected upstream project
   - pinned version/commit
   - packaging/runtime notes

2. `adapter/`
   - local code that maps upstream inputs/outputs into governed repo contracts

3. `smoke_test.md` or executable smoke test
   - minimal proof the integration path works

4. `governance.md`
   - how promotion, permissions, and rollback apply to that upstream component

## Immediate Priorities

Priority order for real upstream integration work:

1. `OpenClaw` runtime-adoption closeout
2. `Qlib` production data/model activation posture
3. `TRL` production data/model activation posture
4. `FinRL`, `RLlib`, `Ray Tune` — all three advanced to `smoke-tested` in `P2-RL-UPSTREAM-RUNTIME-SMOKE-001` (2026-05-01); real backend dependency evidence recorded; RL gate remains closed
5. `W&B` credentialed online sync rerun in a test W&B project/API-key environment for `P2-WANDB-ONLINE-SYNC-001`

Why this order:

- `OpenClaw` already has a governed baseline, but repo-authoritative runtime adoption still blocks the final runtime story
- `Qlib` and `TRL` are the two smoke-tested but not yet production-activated baselines, so they are the truthful OSS activation tail
- `DSPy`, `imitation`, and `MLflow` are already governed and therefore no longer belong at the front of the remaining activation queue
- RL stack stays research-only and non-ordering, but its bounded runtime smoke is now active instead of being indefinitely deferred

## Pre-Activation Operator Surface

The allowed pre-activation integration wiring is exposed through a read-only BFF aggregate:

- `GET /api/v1/operator/research/oss-preactivation`
- sources: research orchestrator capabilities/runs, policy-learning capabilities/jobs, research-worker gateway capabilities/jobs, and OpenClaw gateway-adapter capabilities/upstream status
- allowed scope: capability and rejection metadata only
- explicit non-scope: Qlib/TRL/RL/W&B direct production registry promotion, paper/canary/live execution, governance writes, broker sessions, order routing, and capital binding

## P2 Production Data Activation Posture

Current task evidence for `P2-OSS-ACTIVATE-001` lives in
`services/learning/OSS_ACTIVATION_NOTES.md`.

That packet is the current cross-component activation read:

- production research data is allowed only after durable storage, entitlement,
  license/PIT, rate-limit, freshness, and audit posture are complete
- Qlib and TRL remain `smoke-tested`, but follow-ups
  `P2-QLIB-PROD-DATA-ACTIVATION-001` and
  `P2-TRL-RUNTIME-DATA-ACTIVATION-001` are now active to finish governed
  production-data/runtime-data activation evidence without order routing
- OpenClaw may request governed search context through `SearchGateway` controls
  but receives evidence/citation refs only
- OSS/source/search paths are not authorized to route directly to broker, Lean,
  paper/canary/live deployment, capital binding, or order-capable execution

## Working Rule

When adding a new task for any named OSS component, include these acceptance points unless there is a good reason not to:

- upstream source selected
- version pinned
- dependency or repo path added
- local adapter boundary defined
- smoke test described or implemented
