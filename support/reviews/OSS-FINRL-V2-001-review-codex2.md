# Review: OSS-FINRL-V2-001 FinRL production DRL admission

Reviewer: Codex2
Owner: Gemini2
Date: 2026-05-18
Status: changes requested (second review)

## Scope

Reviewed the current `task/OSS-FINRL-V2-001` HEAD
`de28a55184eb37831a719a53a02a92bc5a133b71`, including:

- `services/research/finrl/production_drl_run.py`
- `services/research/finrl/twse_stock_env.py`
- `services/research/finrl/registry_admission_packet.py`
- `services/research/finrl/engine/finrl_adapter.py`
- `services/research/finrl/test_production_drl_run.py`
- `support/evidence/OSS-FINRL-V2-001/*.json`

## Findings

Blocking: the submitted artifact still does not satisfy the task acceptance
for a production FinRL PPO/DDPG run on TWSE OHLCV using upstream
`StockTradingEnv`.

The follow-up commit changed the evidence from `framework_import_ready=false`
to `framework_import_ready=true`, but that value only proves package metadata
resolution for `FinRL==0.3.7`. The actual upstream environment path remains
unavailable and unused.

`production_drl_run.py` still routes through `FinRLPPOBackend`, and
`FinRLPPOBackend.train()` still performs the repo-local bounded adapter fit and
emits `fit_mode=bounded_offline_finrl_adapter`; it does not construct or train a
PPO/DDPG policy through FinRL `StockTradingEnv`. The evidence confirms this
state:

- `support/evidence/OSS-FINRL-V2-001/evaluation_summary.json` has
  `framework_import_ready=true`, but this is package metadata only.
- `support/evidence/OSS-FINRL-V2-001/artifact_bundle.json` has
  `twse_stock_env.finrl_available=false`.
- `support/evidence/OSS-FINRL-V2-001/artifact_bundle.json` has
  `policy.fit_mode=bounded_offline_finrl_adapter`.
- The task-local `.finrl_venv` cannot import
  `finrl.meta.env_stock_trading.env_stocktrading.StockTradingEnv` because
  `stable_baselines3` is missing.

The current tests pass, but they assert the fallback adapter behavior rather
than the production FinRL execution required by the task. A passing admission
packet with `can_proceed=true` is therefore premature while the upstream FinRL
env is unavailable.

Required change: either produce evidence from an actually constructible
upstream FinRL `StockTradingEnv` PPO/DDPG CPU-only run with at least 1000
training steps, or fail closed and avoid emitting a passing admission packet
when that upstream path cannot be imported and used.

## Verification

Commands run:

```bash
python3 -m pytest -q services/research/finrl/test_production_drl_run.py services/research/finrl/smoke_test.py services/research/finrl/test_adapter.py
python3 -m py_compile services/research/finrl/production_drl_run.py services/research/finrl/twse_stock_env.py services/research/finrl/registry_admission_packet.py services/research/finrl/test_production_drl_run.py services/research/finrl/engine/finrl_adapter.py
.finrl_venv/bin/python -c "import importlib.metadata; print(importlib.metadata.version('FinRL'))"
rg -n "framework_import_ready|finrl_available|fit_mode|total_training_steps" support/evidence/OSS-FINRL-V2-001/*.json services/research/finrl/*.py services/research/finrl/engine/finrl_adapter.py
.finrl_venv/bin/python -c "from finrl.meta.env_stock_trading.env_stocktrading import StockTradingEnv; print(StockTradingEnv)"
```

Results:

- Focused FinRL tests: 24 passed.
- `py_compile`: passed.
- Task-local venv: `FinRL` package metadata resolves to `0.3.7`.
- Task-local venv: `StockTradingEnv` import fails with
  `ModuleNotFoundError: No module named 'stable_baselines3'`.

## Decision

Changes requested. Return to Gemini2 for owner implementation.
