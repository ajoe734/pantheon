# Review: OSS-FINRL-V2-001 FinRL production DRL admission

Reviewer: Codex2
Owner: Gemini2
Date: 2026-05-18
Status: approved

## Scope

Reviewed the current `task/OSS-FINRL-V2-001` HEAD
`746770a72e3b5418dacdc2d3cfdff7ef9d6d6880`, including:

- `services/research/finrl/production_drl_run.py`
- `services/research/finrl/twse_stock_env.py`
- `services/research/finrl/registry_admission_packet.py`
- `services/research/finrl/engine/finrl_adapter.py`
- `services/research/finrl/test_production_drl_run.py`
- `services/research/finrl/requirements.txt`
- `support/evidence/OSS-FINRL-V2-001/*.json`

## Findings

No blocking findings remain.

Refresh note: the post-approval HEAD only changes
`support/evidence/OSS-FINRL-V2-001/admission_packet.json` to record
`finalization_owner=Gemini2`. The packet still validates as a bounded
PromotionReadinessPacket candidate-review artifact, keeps the model artifact in
`draft`, requests only `draft_to_candidate`, and preserves the fail-closed
assertions for registry write, broker session, order route, capital binding,
GPU, and deployment stage.

The prior blocker was resolved: the production evidence path now constructs the
upstream FinRL `StockTradingEnv` from the installed `FinRL==0.3.7`
distribution and trains stable-baselines3 PPO on CPU. The task-local venv probe
confirmed `TWSESerialEnv(..., use_finrl=True)` reports `finrl_available=true`
with 5 instruments, 8 periods, `state_space=21`, and `action_space=5`.

The checked-in evidence now satisfies the task acceptance:

- `support/evidence/OSS-FINRL-V2-001/evaluation_summary.json` reports
  `total_training_steps=1024`, `stock_trading_env_used=true`, `device=cpu`,
  `fit_mode=upstream_finrl_stocktradingenv_stable_baselines3_ppo`,
  `sharpe=16.39`, `annual_return=0.298749`, and `max_drawdown=0.000794`.
- `support/evidence/OSS-FINRL-V2-001/artifact_bundle.json` records the policy
  backend as `finrl_stocktradingenv_sb3_ppo`, framework version `0.3.7`,
  stable-baselines3 version `2.8.0`, CPU torch version `2.12.0+cpu`, and
  `run_materialized=true`.
- `support/evidence/OSS-FINRL-V2-001/admission_packet.json` validates the
  PromotionReadinessPacket shape, carries a checksum-prefixed
  `model_artifact_ref`, keeps registry write authority outside this task, and
  preserves broker/order/capital/deployment fail-closed assertions.
- The tests include the 1000-step production threshold and skip the production
  training path in root CI only when the optional upstream runtime is missing.

## Verification

Commands run:

```bash
python3 -m py_compile services/research/finrl/production_drl_run.py services/research/finrl/twse_stock_env.py services/research/finrl/registry_admission_packet.py services/research/finrl/test_production_drl_run.py services/research/finrl/engine/finrl_adapter.py
python3 -m pytest -q services/research/finrl/test_production_drl_run.py services/research/finrl/test_adapter.py services/research/finrl/smoke_test.py
/tmp/pantheon-worker-worktrees/pantheon/oss-finrl-v2-001/.finrl_venv/bin/python -c "from twse_stock_env import TWSESerialEnv; from production_drl_run import load_twse_data; env=TWSESerialEnv(load_twse_data(periods=8), use_finrl=True); print(env.environment_summary())"
.finrl_venv/bin/python -m pytest -q services/research/finrl/test_production_drl_run.py services/research/finrl/test_adapter.py services/research/finrl/smoke_test.py
python3 - <<'PY'
import json
import sys
sys.path.insert(0, 'services/research/finrl')
from registry_admission_packet import validate_admission_packet
with open('support/evidence/OSS-FINRL-V2-001/admission_packet.json', encoding='utf-8') as f:
    packet = json.load(f)
print(validate_admission_packet(packet))
PY
```

Results:

- `py_compile`: passed.
- Root pytest: 24 passed, 1 skipped.
- Task-local venv upstream env probe: passed with `finrl_available=true`.
- Task-local venv pytest: 25 passed.
- Admission packet validator: passed (`[]`).

## Decision

Approved. Return to Gemini2 for owner closeout and task finalization.
