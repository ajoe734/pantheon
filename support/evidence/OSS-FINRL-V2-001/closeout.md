# OSS-FINRL-V2-001 Closeout

Owner: Codex
Reviewer: Codex2
Date: 2026-05-18
Status: finalization record

## Scope Confirmed

This closeout finalizes the FinRL production DRL admission work after Codex2
approval. The delivered runtime path constructs upstream FinRL
`StockTradingEnv` from deterministic TWSE OHLCV records, trains
stable-baselines3 PPO on CPU, and emits registry admission evidence only.

No registry write, broker session, order route, capital binding, GPU
requirement, paper deployment, canary deployment, or live deployment authority
is granted by this task.

## Durable Artifacts

- `services/research/finrl/production_drl_run.py`
- `services/research/finrl/twse_stock_env.py`
- `services/research/finrl/registry_admission_packet.py`
- `services/research/finrl/engine/finrl_adapter.py`
- `services/research/finrl/test_production_drl_run.py`
- `services/research/finrl/requirements.txt`
- `support/evidence/OSS-FINRL-V2-001/admission_packet.json`
- `support/evidence/OSS-FINRL-V2-001/artifact_bundle.json`
- `support/evidence/OSS-FINRL-V2-001/candidate_packet.json`
- `support/evidence/OSS-FINRL-V2-001/evaluation_summary.json`
- `support/evidence/OSS-FINRL-V2-001/registry_entry.json`
- `support/reviews/OSS-FINRL-V2-001-review-codex2.md`

## Acceptance Evidence

- `evaluation_summary.json` records `algorithm=ppo`, `device=cpu`,
  `stock_trading_env_used=true`, and `total_training_steps=1024`.
- The same summary records `sharpe=16.39`, `annual_return=0.298749`, and
  `max_drawdown=0.000794`.
- `admission_packet.json` validates as a
  `PromotionReadinessPacket.v1` candidate-review packet with `can_proceed=true`.
- Safety assertions remain fail-closed: no registry write, no broker session,
  no order route, no capital binding, no GPU, and deployment stage remains
  `none`.
- PR #91 merged into `dev` with Branch CI passing after the task branch was
  updated from latest `dev`.

## Verification Rerun

Commands rerun during owner finalization:

```bash
python3 -m py_compile services/research/finrl/production_drl_run.py services/research/finrl/twse_stock_env.py services/research/finrl/registry_admission_packet.py services/research/finrl/test_production_drl_run.py services/research/finrl/engine/finrl_adapter.py
python3 -m pytest -q services/research/finrl/test_production_drl_run.py services/research/finrl/test_adapter.py services/research/finrl/smoke_test.py
/tmp/pantheon-worker-worktrees/pantheon/oss-finrl-v2-001/.finrl_venv/bin/python -c "from twse_stock_env import TWSESerialEnv; from production_drl_run import load_twse_data; env=TWSESerialEnv(load_twse_data(periods=8), use_finrl=True); print(env.environment_summary())"
.finrl_venv/bin/python -m pytest -q services/research/finrl/test_production_drl_run.py services/research/finrl/test_adapter.py services/research/finrl/smoke_test.py
```

Results:

- `py_compile`: passed.
- Root focused pytest: 24 passed, 1 skipped.
- Task-local upstream FinRL probe: passed with `finrl_available=True`,
  5 instruments, 8 periods, `state_space=21`, and `action_space=5`.
- Task-local venv focused pytest: 25 passed.

## Closeout Notes

The task branch originally ended on Codex2's approval commit. This owner
closeout record provides the final Codex-owned task commit required before
running `scripts/ai-status.sh done` against the status root.
