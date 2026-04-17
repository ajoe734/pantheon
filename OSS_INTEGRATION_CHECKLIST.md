# OSS Integration Checklist

Last updated: 2026-04-17
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
| `OpenClaw` | upstream repo/runtime | `governed` | Source pin remains locked to `openclaw/openclaw` tag `v2026.4.7` / commit `5050017543011b61df67744ebc6368d889c25a95`, the runtime artifact remains pinned to `ghcr.io/openclaw/openclaw:2026.4.7`, the runnable Pantheon-side adapter now lives under `integrations/openclaw/adapter/`, `docker-compose.yml` now exposes an `openclaw-gateway` runtime dependency path (profile `openclaw`), and `bash scripts/openclaw-gateway-adapter-smoke.sh` has now passed a real upstream gateway smoke on 2026-04-16 for `pantheon.ingest`, `pantheon.review`, `pantheon.retrain`, and `pantheon.deploy`. |
| `DSPy` | Python package/framework | `governed` | v2.4.5 pinned; runnable adapter lives in `services/learning/dspy/`; canonical evidence is now in `integrations/dspy/{integration,governance,smoke_test}.md`; smoke test and unit coverage passed on 2026-04-15. |
| `TRL` | Python package/framework | `smoke-tested` | `trl>=0.8.0,<0.10.0` pinned in `services/learning/trl/requirements.txt`; governed preference-pair adapter (`GovernedPreferencePairAdapter`, `StubDPOBackend`, `TRLDPOBackend`, `run_trl_dpo_workflow`) in `services/learning/trl/adapter/`; smoke test passes (16 unit tests + smoke assertions OK, 2026-04-17); registry output uses canonical `artifact_state=draft` + `deployment_summary.current_stage=none`; evidence in `integrations/trl/{integration,governance,smoke_test}.md`; task: OSS-NEXT-002 (Claude); TRL artifacts are non-executable governed models (`draft` → `candidate` → `approved`), not `paper/live` execution states; production activation blocked on runtime data gates: ≥200 FB-002 events, ≥100 preference pairs, active LP-002 imitation baseline, and a ready downstream consumer; see `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §2` |
| `Qlib` | Python package/framework | `smoke-tested` | activation criteria documented in `services/learning/qlib/ACTIVATION_CRITERIA.md`; `pyqlib==0.9.6` pinned in `services/research/qlib/requirements.txt`; governed data-handler adapter built in `services/research/qlib/adapter/` (`GovernedQlibDataAdapter`, `StubLightGBMBackend`, `QlibLightGBMBackend`, `run_qlib_workflow`); smoke test passes (13 unit tests + smoke assertions OK, 2026-04-17); registry output uses canonical `artifact_state=draft` + `deployment_summary.current_stage=none`; evidence in `integrations/qlib/{integration,governance,smoke_test}.md`; task: OSS-NEXT-001 (Claude); next: production activation requires ≥50 instruments, 2+ years data, and RS-003 replication gate pass per ACTIVATION_CRITERIA §1 |
| `FinRL` | upstream repo/package | `criteria-defined` | deferred until RL path is justified; entry criteria documented in `services/learning/rl/PATH_DEFINITION.md` §1 and the formal approval checkpoint now lives in `services/learning/rl/RL_PATH_APPROVAL_GATE.md`; `finrl==0.3.6` already pinned in `services/research/finrl/requirements.txt` and FinRL Dockerfile exists; activation owner: Copilot; next: pass the RL approval gate, then build the governed single-agent policy-output adapter; see `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §3` |
| `RLlib` | Python package/framework | `version-pinned` | deferred until RL path is approved; entry criteria and full workflow documented in `services/learning/rl/PATH_DEFINITION.md`, and the required approval checkpoint now lives in `services/learning/rl/RL_PATH_APPROVAL_GATE.md`; `ray[rllib]>=2.9.0,<3.0.0` is pinned in `services/research/rllib/requirements.txt` alongside Ray Tune; Dockerfile stub exists; no governed adapter yet; activation owner: Copilot; next: pass the RL approval gate, then prove the governed training/eval loop; see `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §4` |
| `Ray Tune` | Python package/framework | `version-pinned` | `ray[tune]>=2.9.0,<3.0.0` pinned in `services/research/rllib/requirements.txt` alongside RLlib; the same `services/learning/rl/RL_PATH_APPROVAL_GATE.md` must be passed before governed search outputs or smoke integration work begins; activation owner: Copilot (bundles with RLlib path); next: open the RL implementation lane via the approval gate, then define governed search outputs and smoke-test integration with the selected RL path |
| `vectorbt` | Python package/framework | `version-pinned` | task materialization completed in `services/research/vectorbt/ACTIVATION_CRITERIA.md`; `vectorbt==0.26.2` pinned in `services/research/vectorbt/requirements.txt`; first approved scope is rapid strategy backtesting and vectorized portfolio simulation for Research Plane prototyping; canonical integration baseline in `integrations/vectorbt/integration.md`; activation owner: Codex; next: implement the governed input adapter, stub/real backend split, smoke test, and governance evidence pack before claiming `smoke-tested` |
| `statsmodels` | Python package/framework | `version-pinned` | task materialization completed in `services/research/statsmodels/ACTIVATION_CRITERIA.md`; `statsmodels==0.14.2` pinned in `services/research/statsmodels/requirements.txt`; first approved scope is econometrics and regime research (cointegration, VAR/VECM, Markov-switching diagnostics); canonical integration baseline in `integrations/statsmodels/integration.md`; activation owner: Codex2; next: implement the governed input adapter, stub/real backend split, smoke test, and governance evidence pack before claiming `smoke-tested` |
| `QuantLib` | Python package/framework | `version-pinned` | task materialization completed in `services/research/quantlib/ACTIVATION_CRITERIA.md`; `QuantLib-Python==1.18` pinned in `services/research/quantlib/requirements.txt`; first approved scope is derivatives pricing and risk analytics (options pricing via Black-Scholes/Heston, Greeks, yield curve construction, fixed income analytics); canonical integration baseline in `integrations/quantlib/integration.md`; activation owner: Claude; next: implement the governed input adapter, stub/real backend split, smoke test, and governance evidence pack before claiming `smoke-tested` |
| `imitation` | Python package/framework | `governed` | v1.0.1 pinned; runnable BC adapter lives in `services/learning/imitation/`; canonical evidence is now in `integrations/imitation/{integration,governance,smoke_test}.md`; smoke test and unit coverage passed on 2026-04-15. |
| `MLflow` | backend/service/package | `governed` | v3.10.1 pinned; runnable registry↔experiment adapter lives in `services/registry/experiments/`; canonical evidence is now in `integrations/mlflow/{integration,governance,smoke_test}.md`; smoke test and unit coverage passed on 2026-04-15. |
| `W&B` | backend/service/package | `criteria-defined` | activation criteria documented in `services/registry/experiments/WANDB_ACTIVATION.md`; W&B is an optional alternative backend to MLflow requiring stable MLflow integration first (≥30 days operational history), explicit operator need, adapter generalization beyond the current MLflow-first `RegistryExperimentAdapter`, and canonical `artifact_state` / `deployment_stage` support; no SDK pin yet; `EXPERIMENT_BACKEND` env-var selector now present in `services/registry/experiments/config.py` (default `"mlflow"`, `"wandb"` not wired yet); activation owner: Qwen; next: generalize adapter surface for configurable backends, pin `wandb>=0.16.0`, implement W&B backend, and prove metadata equivalence — after MLflow 30-day history gate is met; see `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §5` |

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

1. `OpenClaw`
2. `DSPy`
3. `MLflow or W&B`
4. `Qlib`
5. `imitation`
6. `TRL`
7. `FinRL / RLlib / Tune`

Why this order:

- `OpenClaw` affects orchestration semantics everywhere
- `DSPy` is the first intended persona optimization path already on the active board
- experiment/registry backend should exist before learning integrations fan out
- RL stack should stay last until governance and registry paths are stable

## Working Rule

When adding a new task for any named OSS component, include these acceptance points unless there is a good reason not to:

- upstream source selected
- version pinned
- dependency or repo path added
- local adapter boundary defined
- smoke test described or implemented
