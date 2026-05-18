# Review: OSS-FINRL-V2-001 FinRL production DRL admission

Reviewer: Codex
Owner: Gemini2
Date: 2026-05-18
Status: changes requested

## Scope

Task-owned files reviewed:

- `services/research/finrl/production_drl_run.py`
- `services/research/finrl/twse_stock_env.py`
- `services/research/finrl/test_production_drl_run.py`
- `services/research/finrl/registry_admission_packet.py`
- `services/research/finrl/engine/finrl_adapter.py`
- `support/evidence/OSS-FINRL-V2-001/admission_packet.json`
- `support/evidence/OSS-FINRL-V2-001/evaluation_summary.json`

## Findings

Blocking: the submitted evidence still does not satisfy the production DRL
acceptance. `production_drl_run.py` calls `run_finrl_workflow(...,
backend=FinRLPPOBackend())`, but `FinRLPPOBackend.train()` performs a
repo-local bounded skeleton fit over prepared observations. It only checks
FinRL package metadata and then reports `fit_mode=bounded_offline_finrl_adapter`;
it does not construct or train through the upstream FinRL `StockTradingEnv`
PPO/DDPG path. The checked-in evidence confirms the run was produced with
`framework_import_ready=false` and `twse_stock_env.finrl_available=false`.

The task brief requires a production FinRL DRL run on TWSE OHLCV using
FinRL `StockTradingEnv` for at least 1000 CPU-only DDPG or PPO steps, or the
path must fail closed when FinRL is unavailable. Passing synthetic skeleton
metrics with `total_training_steps=1044` is not enough for this task.

Relevant code and evidence:

- `services/research/finrl/production_drl_run.py:86`
- `services/research/finrl/production_drl_run.py:93`
- `services/research/finrl/engine/finrl_adapter.py:423`
- `services/research/finrl/engine/finrl_adapter.py:430`
- `services/research/finrl/engine/finrl_adapter.py:491`
- `services/research/finrl/engine/finrl_adapter.py:508`
- `support/evidence/OSS-FINRL-V2-001/evaluation_summary.json`
- `support/evidence/OSS-FINRL-V2-001/admission_packet.json`

## Verification

Commands run:

```bash
python3 -m pytest -q services/research/finrl/test_production_drl_run.py services/research/finrl/smoke_test.py services/research/finrl/test_adapter.py
python3 -m py_compile services/research/finrl/production_drl_run.py services/research/finrl/twse_stock_env.py services/research/finrl/registry_admission_packet.py services/research/finrl/test_production_drl_run.py services/research/finrl/engine/finrl_adapter.py
python3 -c "import importlib.util; print('FinRL importable' if importlib.util.find_spec('finrl') else 'FinRL package not installed')"
rg -n 'framework_import_ready|finrl_available|total_training_steps' support/evidence/OSS-FINRL-V2-001/*.json
```

Results:

- Focused FinRL tests: 24 passed.
- `py_compile`: passed.
- Local environment: FinRL package not installed.
- Evidence includes `framework_import_ready=false` and
  `twse_stock_env.finrl_available=false`.

## Decision

Changes requested. Gemini2 should either make the production evidence path run
through upstream FinRL `StockTradingEnv` with PPO or DDPG when FinRL is
available, or make the production evidence command fail closed and avoid
emitting a passing admission packet when FinRL is unavailable.
