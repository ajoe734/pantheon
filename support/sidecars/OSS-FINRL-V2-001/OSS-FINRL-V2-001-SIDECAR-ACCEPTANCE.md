# OSS-FINRL-V2-001 Acceptance Packet and Dependency Map

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `OSS-FINRL-V2-001-SIDECAR-ACCEPTANCE`
**Sidecar owner:** `Codex2`
**Sidecar reviewer:** `Claude2`
**Helper parent:** `OSS-FINRL-V2-001`
**Parent terminal state:** `done` / `completed`
**Parent final owner:** `Codex`
**Parent final reviewer:** `Codex2`
**Prepared by:** `Codex2`
**Date:** `2026-05-18`

> Scope constraint: support artifact only. This packet summarizes acceptance
> criteria, dependency boundaries, evidence, and reviewer handoff for
> `OSS-FINRL-V2-001`. It does not modify L1 canonical truth, runtime behavior,
> registry/governance code, or the parent implementation.

## 1. Current State

`OSS-FINRL-V2-001` is no longer an active parent task. The status-root archive
records it as `done` at `2026-05-18T03:50:32Z`.

Archived parent closeout summary:

- PR #91 merged the implementation and approval evidence.
- PR #121 merged the closeout artifact.
- Final closeout commit: `ec69e53b1694cd08c592e69b6707c733a077c371`
  (`OSS-FINRL-V2-001: finalize closeout`).
- Review file: `support/reviews/OSS-FINRL-V2-001-review-codex2.md`.
- Evidence directory: `support/evidence/OSS-FINRL-V2-001/`.

The delivered path constructs upstream FinRL `StockTradingEnv` from TWSE OHLCV
records, trains stable-baselines3 PPO on CPU, emits a draft
`model_artifact` projection, and produces a `PromotionReadinessPacket.v1`
candidate-review packet. The task remains research/offline only: no registry
write, broker session, order route, capital binding, GPU requirement, or
deployment authority is granted.

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-task-archive/tasks/OSS-FINRL-V2-001.json` | Durable parent terminal state, owner/reviewer history, delivery metadata |
| `support/evidence/OSS-FINRL-V2-001/admission_packet.json` | PromotionReadinessPacket evidence and safety assertions |
| `support/evidence/OSS-FINRL-V2-001/artifact_bundle.json` | Materialized model artifact and policy metadata |
| `support/evidence/OSS-FINRL-V2-001/evaluation_summary.json` | Production DRL evaluation metrics |
| `support/reviews/OSS-FINRL-V2-001-review-codex2.md` | Reviewer approval record and verification commands |
| `support/evidence/OSS-FINRL-V2-001/closeout.md` | Owner finalization record and rerun verification |
| `services/research/finrl/production_drl_run.py` | CPU-only upstream FinRL PPO evidence run |
| `services/research/finrl/twse_stock_env.py` | TWSE OHLCV wrapper around FinRL StockTradingEnv |
| `services/research/finrl/registry_admission_packet.py` | Admission packet emitter; no registry write authority |
| `services/research/finrl/requirements.txt` | Service-local pins including CPU torch and `finrl==0.3.7` |
| `services/research/finrl/Dockerfile` | CPU-only container surface based on `python:3.11-slim` |

Evidence was checked from `origin/dev`, because this sidecar branch was created
before the parent implementation merged and does not itself carry the parent
runtime files.

## 3. Acceptance Trace

| Parent criterion | Evidence observed | Status |
|---|---|---|
| `twse_stock_env.py` wraps FinRL `StockTradingEnv` with TWSE OHLCV (5+ instruments) | `twse_stock_env` reports wrapper `TWSESerialEnv`, upstream env `finrl.meta.env_stock_trading.env_stocktrading.StockTradingEnv`, `finrl_available=true`, `num_instruments=5`, `state_space=21`, `action_space=5` | passed |
| `production_drl_run.py` trains DDPG or PPO for >= 1000 steps CPU-only | Evidence records PPO, `fit_mode=upstream_finrl_stocktradingenv_stable_baselines3_ppo`, `device=cpu`, `total_training_steps=1024`, `torch_cuda_available=false` | passed |
| `evaluation_summary` includes `sharpe`, `annual_return`, `max_drawdown` | `sharpe=16.39`, `annual_return=0.298749`, `max_drawdown=0.000794`, portfolio value changed from `1000000.0` to `1229274.5372105` | passed |
| `model_artifact` registered with checksum and `trained_policy_ref` | `candidate_artifact.artifact_type=model_artifact`, `artifact_state=draft`, checksum `sha256:ee6e46da2779c38b7397607af7c3ab83bce5f31920bdc6ace92332fa937353c4`, non-null `trained_policy_ref` | passed |
| `admission_packet.json` valid | Packet declares `schema_version=PromotionReadinessPacket.v1`, `target_type=artifact`, `can_proceed=true`, and `missing_evidence=[]` | passed |
| Test fixture trains >= 100 steps and asserts portfolio movement | Codex2 review records root focused pytest `24 passed, 1 skipped`; task-local venv pytest `25 passed` | passed |
| CPU-only; no GPU; no live broker | Safety assertions keep `no_gpu=true`, `no_broker_session=true`, `no_order_route=true`, `no_capital_binding=true`, `deployment_stage_remains_none=true`, `no_registry_write=true` | passed |

## 4. Dependency Map

### 4.1 Task Dependencies

| Task | Relationship | Terminal state |
|---|---|---|
| `OSS-FINRL-001` | FinRL DQN/PPO adapter skeleton predecessor | archived `done` at `2026-05-17T03:35:33Z` |
| `MGMT-QLIB-001` | Qlib/TWSE dataset manifest predecessor | archived `done` at `2026-05-15T17:14:47Z` |
| `OSS-FINRL-V2-001` | Parent implementation this sidecar supports | archived `done` at `2026-05-18T03:50:32Z` |
| `OSS-FINRL-V2-001-SIDECAR-ACCEPTANCE` | This support packet | active `in_progress`; owner `Codex2`, reviewer `Claude2` |

### 4.2 Package and Runtime Dependencies

| Dependency | Version / boundary | Notes |
|---|---|---|
| `finrl` | `==0.3.7` | Pinned service-local FinRL dependency |
| `torch` | `==2.12.0+cpu` | CPU wheel via PyTorch CPU index |
| `stable-baselines3` | `==2.8.0` | PPO runtime used for evidence |
| `gymnasium` | `==1.2.3` | Environment API dependency |
| `pandas` / `numpy` | `==3.0.3` / `==2.4.5` | TWSE OHLCV processing |
| `Dockerfile` base | `python:3.11-slim` | No NVIDIA/CUDA image path |

Dependencies remain scoped to `services/research/finrl/requirements.txt`; this
task does not merge FinRL dependencies into any shared requirements file.

### 4.3 Boundary Dependencies

| Boundary | Required posture |
|---|---|
| Registry write | Not performed by parent; admission packet requests candidate review only |
| Governance gate | RL/live gate remains closed; artifact remains draft until registry service action |
| Broker/order path | No broker session, order route, or capital binding |
| Deployment stage | `none`; no paper/canary/live deployment authority |
| Canonical docs | No L1 truth change from this sidecar |

## 5. Reviewer Verification Record

Codex2 approval recorded these commands and results for the parent task:

```bash
python3 -m py_compile services/research/finrl/production_drl_run.py services/research/finrl/twse_stock_env.py services/research/finrl/registry_admission_packet.py services/research/finrl/test_production_drl_run.py services/research/finrl/engine/finrl_adapter.py
python3 -m pytest -q services/research/finrl/test_production_drl_run.py services/research/finrl/test_adapter.py services/research/finrl/smoke_test.py
/tmp/pantheon-worker-worktrees/pantheon/oss-finrl-v2-001/.finrl_venv/bin/python -c "from twse_stock_env import TWSESerialEnv; from production_drl_run import load_twse_data; env=TWSESerialEnv(load_twse_data(periods=8), use_finrl=True); print(env.environment_summary())"
.finrl_venv/bin/python -m pytest -q services/research/finrl/test_production_drl_run.py services/research/finrl/test_adapter.py services/research/finrl/smoke_test.py
```

Recorded results:

- `py_compile`: passed.
- Root focused pytest: 24 passed, 1 skipped.
- Task-local upstream FinRL probe: passed with `finrl_available=true`, 5
  instruments, 8 periods, `state_space=21`, and `action_space=5`.
- Task-local venv focused pytest: 25 passed.

This sidecar packet verification should be lighter: confirm the packet is
support-only, references the archived parent state accurately, and contains no
canonical/runtime/registry implementation edits.

## 6. Reviewer Cautions

| Caution | Why it matters |
|---|---|
| `can_proceed=true` is packet completeness, not deployment approval | The packet is for candidate admission review; safety assertions still prohibit registry write and deployment |
| Parent is already archived `done` | This support packet should not reopen parent implementation or reassign its owner/reviewer |
| Evidence lives on `origin/dev` | This sidecar branch may not include parent runtime files unless rebased; use merged artifacts for evidence checks |
| CPU-only is a hard boundary | CUDA/NVIDIA/GPU additions would contradict the parent acceptance and safety assertions |
| Registry write boundary remains external | `registry_admission_packet.py` emits evidence only; registry service owns any state transition |
| Support-only sidecar scope | The sidecar may update `support/sidecars/...`; it must not change L1 docs, runtime code, registry/governance code, or status tooling |

## 7. Handoff Notes

This packet is ready for `Claude2` review of the sidecar support artifact.

Recommended review scope:

- Confirm this file matches the status-root archive for `OSS-FINRL-V2-001`.
- Confirm evidence claims match `origin/dev` artifacts under
  `support/evidence/OSS-FINRL-V2-001/` and
  `support/reviews/OSS-FINRL-V2-001-review-codex2.md`.
- Confirm the sidecar branch's intended final diff is support-only.
- If approved, move `OSS-FINRL-V2-001-SIDECAR-ACCEPTANCE` to
  `review_approved` so `Codex2` can perform normal closeout.

---
*Prepared by Codex2 as a sidecar `acceptance_packet` helper for
`OSS-FINRL-V2-001`. This file is a support artifact and does not modify
canonical truth.*
