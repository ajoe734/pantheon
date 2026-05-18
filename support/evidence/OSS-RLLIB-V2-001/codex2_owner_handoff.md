# Codex2 Owner Handoff: OSS-RLLIB-V2-001

Owner: Codex2
Reviewer: Claude
Date: 2026-05-18
Status: Ready for assigned reviewer

## Scope Confirmed

- `TWSETradingEnv` remains a gymnasium-compatible 5-instrument TWSE OHLCV environment with discrete hold/long/short actions.
- `production_ppo_run.py` keeps the CPU-only upstream Ray/RLlib PPO path when dependencies are available and the dependency-light fallback for local CI.
- Production-sized runs (`num_iters >= 100`) still fail closed when trained policy reward does not improve over the random baseline.
- Fallback-generated admission packets remain valid but `can_proceed=false` until upstream RLlib PPO backend evidence is present.
- No GPU, broker session, order route, capital binding, registry write, or deployment-stage change is introduced.

## Current-Owner Changes

- Hardened `_train_with_rllib()` so package imports can resolve `TWSETradingEnv-v1` through the package-relative module path before falling back to direct script execution imports.
- Updated admission packet generator metadata from the previous owner label to `Codex2 / OSS-RLLIB-V2-001`.
- Preserved the prior Codex review findings as historical review evidence from the earlier owner/reviewer assignment.

## Verification

```bash
python3 -m pytest services/research/rllib/test_production_ppo_run.py -q
# 21 passed in 3.76s

python3 -c "from services.research.rllib.production_ppo_run import run_production; r=run_production(num_iters=1, eval_episodes=1, created_at='2026-05-18T00:00:00Z'); print(r['backend_kind'], r['cpu_only'], r['status'])"
# dependency_light_fallback True completed

python3 services/research/rllib/registry_admission_packet.py --output /tmp/oss-rllib-v2-codex2-admission.json --created-at 2026-05-18T00:00:00Z
jq '{generated_by, can_proceed, missing_evidence, backend_kind: .candidate_artifact.backend_kind}' /tmp/oss-rllib-v2-codex2-admission.json
# generated_by=Codex2 / OSS-RLLIB-V2-001, can_proceed=false,
# missing_evidence=[upstream_rllib_ppo_backend_confirmed],
# backend_kind=dependency_light_fallback

python3 -m pytest scripts/test_ai_status.py services/research/rllib/test_production_ppo_run.py -q
# 66 passed in 21.40s

python3 -m pytest -q
# terminated after about 4 minutes with no output; repo-level verification remains a known slow/hung check
```
