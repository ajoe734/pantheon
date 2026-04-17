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
| `OpenClaw` | upstream repo/runtime | `governed` | Source pin remains locked to `openclaw/openclaw` tag `v2026.4.7` / commit `5050017543011b61df67744ebc6368d889c25a95`, the runtime artifact remains pinned to `ghcr.io/openclaw/openclaw:2026.4.7`, the runnable Pantheon-side adapter now lives under `integrations/openclaw/adapter/`, `docker-compose.yml` now exposes an `openclaw-gateway` runtime dependency path (profile `openclaw`), `bash scripts/openclaw-smoke-test.sh` revalidated the baseline pin on 2026-04-17, and `bash scripts/openclaw-gateway-adapter-smoke.sh` passed a real upstream gateway smoke on 2026-04-17 for `pantheon.ingest`, `pantheon.review`, `pantheon.retrain`, and `pantheon.deploy`. |
| `DSPy` | Python package/framework | `governed` | v2.4.5 pinned; runnable adapter lives in `services/learning/dspy/`; canonical evidence is now in `integrations/dspy/{integration,governance,smoke_test}.md`; smoke test and unit coverage were refreshed on 2026-04-17. |
| `TRL` | Python package/framework | `smoke-tested` | `trl>=0.8.0,<0.10.0` pinned in `services/learning/trl/requirements.txt`; governed preference-pair adapter (`GovernedPreferencePairAdapter`, `StubDPOBackend`, `TRLDPOBackend`, `run_trl_dpo_workflow`) in `services/learning/trl/adapter/`; smoke test passes (16 unit tests + smoke assertions OK, 2026-04-17); registry output uses canonical `artifact_state=draft` + `deployment_summary.current_stage=none`; evidence in `integrations/trl/{integration,governance,smoke_test}.md`; task: OSS-NEXT-002 (Claude); TRL artifacts are non-executable governed models (`draft` → `candidate` → `approved`), not `paper/live` execution states; production activation blocked on runtime data gates: ≥200 FB-002 events, ≥100 preference pairs, active LP-002 imitation baseline, and a ready downstream consumer; see `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §2` |
| `Qlib` | Python package/framework | `smoke-tested` | activation criteria documented in `services/learning/qlib/ACTIVATION_CRITERIA.md`; `pyqlib==0.9.6` pinned in `services/research/qlib/requirements.txt`; governed data-handler adapter built in `services/research/qlib/adapter/` (`GovernedQlibDataAdapter`, `StubLightGBMBackend`, `QlibLightGBMBackend`, `run_qlib_workflow`); smoke test passes (13 unit tests + smoke assertions OK, 2026-04-17); registry output uses canonical `artifact_state=draft` + `deployment_summary.current_stage=none`; evidence in `integrations/qlib/{integration,governance,smoke_test}.md`; task: OSS-NEXT-001 (Claude); next: production activation requires ≥50 instruments, 2+ years data, and RS-003 replication gate pass per ACTIVATION_CRITERIA §1 |
| `FinRL` | upstream repo/package | `criteria-defined` | the accepted 2026-04-17 Phase 6 decision explicitly **defers RL for the current wave**; entry criteria remain in `services/learning/rl/PATH_DEFINITION.md` §1 and the formal checkpoint is `services/learning/rl/RL_PATH_APPROVAL_GATE.md`; `finrl==0.3.6` already pinned in `services/research/finrl/requirements.txt` and FinRL Dockerfile exists; activation owner: Copilot; next: wait for Qlib `artifact_state=approved` + **3 months** stable evaluation evidence, then reopen the RL gate and build the governed **single-agent** policy-output adapter as the first RL lane; see `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §3` |
| `RLlib` | Python package/framework | `version-pinned` | the accepted 2026-04-17 Phase 6 decision explicitly **defers RL for the current wave**; entry criteria and full workflow remain documented in `services/learning/rl/PATH_DEFINITION.md`, and the required approval checkpoint is `services/learning/rl/RL_PATH_APPROVAL_GATE.md`; `ray[rllib]>=2.9.0,<3.0.0` is pinned in `services/research/rllib/requirements.txt` alongside Ray Tune; Dockerfile stub exists; no governed adapter yet; activation owner: Copilot; next: stay deferred behind the RL gate and follow the FinRL first-lane proof before opening the governed RLlib train/eval loop; see `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §4` |
| `Ray Tune` | Python package/framework | `version-pinned` | `ray[tune]>=2.9.0,<3.0.0` pinned in `services/research/rllib/requirements.txt` alongside RLlib; the accepted 2026-04-17 decision keeps RL closed for the current wave, so no governed search-output or smoke integration work may begin yet; activation owner: Copilot (bundles with RLlib path); next: remain deferred until the RL gate reopens, after the FinRL first-lane proof justifies the broader RLlib + Tune path |
| `vectorbt` | Python package/framework | `smoke-tested` | `vectorbt==0.26.2` pinned in `services/research/vectorbt/requirements.txt`; `GovernedVectorbtInputAdapter`, `StubVectorbtBackend`, and `VectorbtBackend` implemented in `services/research/vectorbt/adapter/vectorbt_adapter.py`; smoke test passes (MA-crossover backtest, 2 instruments, 35 bars each) and 26 unit tests pass via `services/research/vectorbt/test_adapter.py`; governed artifact envelope confirmed (`artifact_family=vectorbt_backtest`, `artifact_state=draft`, `direct_live_influence=false`, `lean_consumption=scoring_only_not_direct_action`); CI-safe stub backend is default; real backend gated behind `PANTHEON_VECTORBT_BACKEND=real`; OSS-IMPL-003 delivered 2026-04-17; next: complete Gate 2 evidence pack (`integrations/vectorbt/{governance,smoke_test}.md`) to claim `governed` |
| `statsmodels` | Python package/framework | `smoke-tested` | v0.14.2 pinned; `GovernedStatsmodelsInputAdapter`, `StubStatsmodelsBackend`, and `StatsmodelsBackend` implemented in `services/research/statsmodels/adapter/statsmodels_adapter.py`; smoke test passes (all three analysis paths: cointegration, VAR/VECM, Markov-switching) and 20 unit tests pass via `services/research/statsmodels/test_adapter.py`; governed artifact envelope confirmed (`artifact_family=regime_report`, `artifact_state=draft`, `direct_live_influence=false`, `lean_consumption=research_only_not_direct_action`); CI-safe stub backend is default; real backend gated behind `PANTHEON_STATSMODELS_BACKEND=real`; OSS-IMPL-001 delivered 2026-04-17; next: complete Gate 2 evidence pack (`integrations/statsmodels/{governance,smoke_test}.md`) to claim `governed` |
| `QuantLib` | Python package/framework | `smoke-tested` | `QuantLib-Python==1.18` pinned in `services/research/quantlib/requirements.txt`; governed adapter implemented in `services/research/quantlib/adapter/` (`GovernedQuantLibInputAdapter`, `StubQuantLibBackend`, `QuantLibBackend`, `run_quantlib_workflow`); smoke test passes via `python3 services/research/quantlib/smoke_test.py` and 17 unit tests pass via `python3 -m pytest services/research/quantlib/test_adapter.py -q` on 2026-04-17; governed artifact envelope confirmed (`artifact_family=pricing_report`, `artifact_state=draft`, `direct_live_influence=false`, `lean_consumption=research_only_not_direct_action`); first approved scope remains derivatives pricing and fixed-income risk analytics; next: complete Gate 2 evidence pack (`integrations/quantlib/{governance,smoke_test}.md`) to claim `governed` |
| `imitation` | Python package/framework | `governed` | v1.0.1 pinned; runnable BC adapter lives in `services/learning/imitation/`; canonical evidence is now in `integrations/imitation/{integration,governance,smoke_test}.md`; smoke test and unit coverage were refreshed on 2026-04-17. |
| `MLflow` | backend/service/package | `governed` | v3.10.1 pinned; runnable registry↔experiment adapter lives in `services/registry/experiments/`; canonical evidence is now in `integrations/mlflow/{integration,governance,smoke_test}.md`; smoke test and unit coverage were refreshed on 2026-04-17. |
| `W&B` | backend/service/package | `criteria-defined` | **OSS-NEXT-004 (2026-04-17): formally deferred for current wave** — all six entry criteria unmet: MLflow operational history <2 days (gate requires ≥30), no operator preference on file, `RegistryExperimentAdapter` not generalized, canonical `artifact_state`/`deployment_stage` migration not landed, no SDK pin, network readiness unverified; re-entry gate documented in `services/registry/experiments/WANDB_ACTIVATION.md §7`; earliest eligible reopen: 2026-05-15 (MLflow history gate); activation owner: Qwen; `EXPERIMENT_BACKEND` env-var selector present in `services/registry/experiments/config.py` (default `"mlflow"`, W&B not in `_SUPPORTED_BACKENDS`); see `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §5` |

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
