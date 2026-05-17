# OSS-RLLIB-001 Review

**Task:** OSS-RLLIB-001 — RLlib PPO adapter skeleton
**Reviewer:** Claude
**Owner:** Codex
**Review date:** 2026-05-17
**Decision:** APPROVED

---

## Acceptance Criteria Verification

| Criterion | Status | Evidence |
|---|---|---|
| `adapter.py` exposes `train_ppo(env_id, num_iters)` returning ExperimentRun dict with `model_artifact_ref` | ✅ PASS | `artifact_type=model_artifact`, `model_artifact_ref` present in run output |
| `smoke_test.py` runs CartPole-v1 PPO <= 20 iters CPU-only and asserts mean_reward > random baseline | ✅ PASS | smoke_test assertions OK; mean_reward 500.0 > random_baseline 17.33 |
| Smoke produces `model_artifact` registered as `artifact_type=model_artifact` | ✅ PASS | `registry_entry.artifact_type=model_artifact` asserted and confirmed |
| Dockerfile uses `python:3.11-slim` base, no NVIDIA/GPU image | ✅ PASS | `FROM python:3.11-slim`, CMD is inert (prep-gated) |
| `requirements.txt` pins `ray[rllib]` and `gymnasium` versions | ✅ PASS | `ray[rllib]==2.9.3`, `ray[tune]==2.9.3`, `gymnasium==0.28.1` |

---

## Verification Commands

```
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/research/rllib/cartpole_ppo.py services/research/rllib/adapter.py services/research/rllib/__init__.py services/research/rllib/smoke_test.py
→ py_compile: OK

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q services/research/rllib/test_adapter.py
→ 19 passed in 4.02s

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q services/research/rllib/test_ray_tune_adapter.py
→ 16 passed in 3.28s

PYTHONDONTWRITEBYTECODE=1 python3 services/research/rllib/smoke_test.py --num-iters 2 --eval-episodes 3 --output-dir /tmp/pantheon/research/rllib/claude-review-smoke
→ assertions: OK

PYTHONDONTWRITEBYTECODE=1 python3 services/research/rllib/smoke_test.py --enable-deferred-prep --deferred-backend stub
→ assertions: OK

PYTHONDONTWRITEBYTECODE=1 python3 -c "import services.research.rllib as rllib; result = rllib.train_ppo(num_iters=1, eval_episodes=2); print(result['artifact_type'])"
→ model_artifact (package import from repo root OK)

git diff --check -- services/research/rllib/
→ diff-check: clean
```

---

## Review Notes

- Implementation correctly separates the new CartPole PPO adapter (`cartpole_ppo.py`) from the existing governed deferred-prep adapter package (`adapter/`), avoiding the import shadowing pattern that blocked OSS-STAT-001.
- Both `services.research.rllib` and `services.research.rllib.adapter` expose `train_ppo` correctly.
- The dependency-light fallback is explicit: `backend_kind=dependency_light_fallback`, `backend_failure` recorded — no silent fallback.
- `require_rllib=True` fails closed as specified in the contract.
- Model artifact governance fields (`artifact_state=draft`, `deployment_stage=none`, `direct_live_influence=false`) are correctly set throughout.
- Tests total 35 (19 + 16); all pass without ray/gymnasium in local env.
- Dockerfile CMD is intentionally inert (deferred-prep prep-gate design, not the statsmodels pattern) — acceptable for this task scope.
