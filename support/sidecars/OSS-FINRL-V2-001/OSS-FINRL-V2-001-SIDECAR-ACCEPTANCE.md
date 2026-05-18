# OSS-FINRL-V2-001 Acceptance Packet and Dependency Map

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `OSS-FINRL-V2-001-SIDECAR-ACCEPTANCE`
**Helper parent:** `OSS-FINRL-V2-001`
**Parent owner:** `Gemini2`
**Parent reviewer:** `Codex2`
**Parent status:** `todo`
**Prepared by:** `Claude2`
**Date:** `2026-05-18`

> Scope constraint: support artifact only. This packet summarizes acceptance
> criteria, dependency boundaries, reviewer checks, and current evidence for
> `OSS-FINRL-V2-001`. It does not modify L1 canonical truth, core contracts,
> registry/governance implementation, or runtime behavior.

## 1. Executive Summary

`OSS-FINRL-V2-001` upgrades the FinRL DRL adapter from skeleton (`OSS-FINRL-001`)
to production scale. It wraps FinRL `StockTradingEnv` with TWSE OHLCV data,
trains PPO or DDPG for ≥ 1000 steps (CPU-only), and submits registry admission
packets for the resulting model artifacts.

The parent task is currently `todo`; this packet documents the acceptance surface
that `Codex2` must verify once `Gemini2` completes the parent implementation.

Expected artifact posture once implemented:

- `artifact_type = model_artifact`
- `artifact_state = draft`
- `deployment_summary.current_stage = none`
- `governance.gate_state = closed`
- `direct_live_influence = false`
- `allowed_next_action = offline_registry_review_only`

This sidecar does not approve the parent implementation. It packages the review
surface so the assigned reviewer can verify acceptance without treating the
support packet as canonical promotion or RL gate activation.

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable task state, owner/reviewer routing, parent acceptance criteria |
| `services/research/finrl/adapter.py` | Existing skeleton public entrypoint; V2 extends this surface |
| `services/research/finrl/engine/finrl_adapter.py` | Existing bounded offline training engine; V2 adds production-scale layer |
| `services/research/finrl/requirements.txt` | Service-local pins; `finrl==0.3.7` must remain pinned after V2 |
| `services/research/finrl/Dockerfile` | CPU-only container; must remain `python:3.11-slim` base after V2 |
| `services/research/finrl/contract.md` | Local interface contract; V2 extends without breaking existing contract |
| `services/research/finrl/production_drl_run.py` | **Expected V2 artifact**: production DRL run entrypoint |
| `services/research/finrl/twse_stock_env.py` | **Expected V2 artifact**: FinRL StockTradingEnv wrapper for TWSE |
| `services/research/finrl/test_production_drl_run.py` | **Expected V2 artifact**: test fixture for production run |
| `services/research/finrl/registry_admission_packet.py` | **Expected V2 artifact**: admission packet emitter |
| `support/evidence/OSS-FINRL-V2-001/admission_packet.json` | **Expected V2 artifact**: PromotionReadinessPacket output |

## 3. Parent Acceptance Checklist

| Parent criterion | Expected implementation shape | Reviewer check |
|---|---|---|
| `twse_stock_env.py` wraps FinRL `StockTradingEnv` with TWSE OHLCV (5+ instruments) | gymnasium-compatible env wrapping real TWSE OHLCV observation/action space | Confirm env inherits from `StockTradingEnv`; verify 5+ instruments are supported in the action space |
| `production_drl_run.py` trains DDPG or PPO for ≥ 1000 steps CPU-only | Training loop using `twse_stock_env.py` with step counter assertion | Confirm ≥ 1000 steps executed; confirm no GPU/CUDA path; confirm `evaluation_summary` contains `sharpe`, `annual_return`, `max_drawdown` |
| `model_artifact` registered with checksum and `trained_policy_ref` | `registry_admission_packet.py` emits packet with deterministic checksum | Confirm `artifact_type=model_artifact`; confirm `artifact_state=draft`; confirm `trained_policy_ref` is non-null |
| `admission_packet.json` valid against `PromotionReadinessPacket` schema | JSON file at `support/evidence/OSS-FINRL-V2-001/admission_packet.json` | Confirm packet conforms to schema; confirm `can_proceed=false` and gate fields are accurate |
| Test fixture trains ≥ 100 steps and asserts `portfolio_value` changes | `test_production_drl_run.py` with deterministic OHLCV fixture | Confirm `pytest -q exit 0`; confirm test uses fixture data not live market data |
| CPU-only; no GPU; no live broker | Dockerfile and requirements scanned; no CUDA/NVIDIA/torch-cuda | Run `rg -n "cuda\|nvidia\|live_broker" services/research/finrl/` and confirm no matches |

## 4. Repo-Current Behavior Map

This table covers the **existing** `OSS-FINRL-001` skeleton as the baseline.
V2 extends it without removing the skeleton surface.

| Surface | Current skeleton behavior | V2 extension expected |
|---|---|---|
| Public entrypoint | `train(strategy_spec_ref, backend)` in `adapter.py` | `run_production(instrument_universe, start_date, end_date, window_days)` in `production_drl_run.py` |
| Environment | Synthetic bounded OHLCV only | `twse_stock_env.py` wrapping FinRL `StockTradingEnv` with real TWSE OHLCV |
| Training depth | ≤ 1000 steps bounded smoke | ≥ 1000 steps production run (DDPG or PPO) |
| Evaluation summary | `sharpe > 0`, `mean_reward_proxy`, `num_steps` | `sharpe`, `annual_return`, `max_drawdown` over full run |
| Artifact registration | `artifact_type=model_artifact`, `artifact_state=draft` | Same shape; adds `trained_policy_ref` and deterministic checksum |
| Registry admission | Not present in skeleton | `registry_admission_packet.py` emitting `PromotionReadinessPacket` |
| Dataset source | Synthetic fixture data | Real TWSE OHLCV from `MGMT-QLIB-001` dataset manifest |
| Container surface | `python:3.11-slim` CPU-only | Same base; no CUDA addition |

## 5. Dependency Map

### 5.1 External Package Dependencies

| Package | Pin or range | Source | Notes |
|---|---:|---|---|
| `finrl` | `==0.3.7` | `services/research/finrl/requirements.txt` | Must remain pinned; V2 must not update to unpinned |
| `gymnasium` | `==1.2.3` | `services/research/finrl/requirements.txt` | Required for `StockTradingEnv` gymnasium compatibility |
| `matplotlib` | `==3.10.9` | `services/research/finrl/requirements.txt` | Inherited plotting dependency |
| `numpy` | `==2.4.5` | `services/research/finrl/requirements.txt` | Numeric operations for OHLCV processing |
| `pandas` | `==3.0.3` | `services/research/finrl/requirements.txt` | DataFrame operations for TWSE dataset |
| `pytest` | `==9.0.3` | `services/research/finrl/requirements.txt` | Container test runner |

### 5.2 Internal Dependencies and Boundaries

| Dependency | Relationship | Boundary |
|---|---|---|
| `services/research/finrl/adapter.py` | Existing skeleton entrypoint; V2 adds alongside | Must not be modified by V2; existing skeleton surface stays intact |
| `services/research/finrl/engine/finrl_adapter.py` | Bounded offline engine; V2 adds production layer | V2 may reference for shared logic but must not break existing bounded API |
| `services/research/finrl/requirements.txt` | Service-local package pins | V2 must not add GPU/CUDA packages or merge with shared requirements |
| `services/research/finrl/Dockerfile` | CPU-only container surface | V2 must keep `python:3.11-slim` base; no NVIDIA image path |
| `MGMT-QLIB-001` (dataset) | Upstream TWSE OHLCV dataset manifest | V2 consumes dataset refs; does not own the dataset |
| `OSS-FINRL-001` | Parent skeleton task | V2 depends on skeleton being `done` or `review_approved`; see `depends_on` in task brief |
| `services/learning/rl/RL_PATH_APPROVAL_GATE.md` | RL activation gate | Not modified; production DRL training stays within closed gate semantics |
| Registry/governance runtime | Downstream consumers | Not touched; V2 output is a packet shape submitted for admission review |

### 5.3 Task-Level Dependencies

| Task | Relationship | Current read |
|---|---|---|
| `OSS-FINRL-001` | Direct predecessor (skeleton) | `review_approved`; sidecar done |
| `MGMT-QLIB-001` | Dataset provider | Required for real TWSE OHLCV access |
| `OSS-FINRL-V2-001` | Parent implementation task | `todo`; owner `Gemini2`, reviewer `Codex2` |
| `OSS-FINRL-V2-001-SIDECAR-ACCEPTANCE` | This helper task | `in_progress`; owner `Claude2`, reviewer `Codex2` |

## 6. Verification Checklist for Reviewer

When `Gemini2` submits `OSS-FINRL-V2-001` for review, `Codex2` should run these
checks against the parent implementation:

| Check | Command | Expected result |
|---|---|---|
| Syntax validation | `python3 -m py_compile services/research/finrl/production_drl_run.py services/research/finrl/twse_stock_env.py services/research/finrl/registry_admission_packet.py` | No output (PASS) |
| Test suite | `pytest -q services/research/finrl/test_production_drl_run.py` | exit 0, ≥ 1 passed |
| No CUDA/GPU paths | `rg -n "cuda\|nvidia\|torch" services/research/finrl/requirements.txt services/research/finrl/Dockerfile` | No matches |
| Live broker guard | `grep -n "live_broker\|BROKER_PRODUCTION_LIVE\|CAPITAL_BINDING_LIVE" services/research/finrl/production_drl_run.py` | No matches |
| Admission packet shape | `python3 -c "import json; d=json.load(open('support/evidence/OSS-FINRL-V2-001/admission_packet.json')); assert 'can_proceed' in d"` | No exception |
| Whitespace check | `git diff --check -- services/research/finrl/production_drl_run.py services/research/finrl/twse_stock_env.py` | No matches |

## 7. Reviewer Cautions

| Caution | Why it matters |
|---|---|
| This is not RL gate approval | The packet supports review of a production DRL adapter; it does not reopen live RL gate or promote beyond `draft` artifact state |
| Parent is `todo` when packet is prepared | Reviewer should treat this packet as a pre-flight checklist; actual verification runs after parent is submitted for review |
| `PromotionReadinessPacket` does not trigger promotion | The admission packet records evidence readiness only; `can_proceed` must remain `false` until governance approves separately |
| TWSE OHLCV dataset must come from MGMT-QLIB-001 | Reviewer should verify the data path is the registered dataset manifest, not a synthetic fixture or hardcoded path |
| CPU-only is a hard boundary | Any addition of CUDA, NVIDIA, or GPU-specific wheels to `requirements.txt` or `Dockerfile` is a scope violation, not a review note |
| Skeleton surface must remain intact | `OSS-FINRL-001` files (`adapter.py`, `engine/finrl_adapter.py`, `smoke_test.py`) must not be modified by V2; verify with `git diff` |
| Registry write boundary | `registry_admission_packet.py` emits a packet for admission review; it must not directly write to the governance store or registry service |

## 8. Scope Boundary - Reject These Interpretations

| Interpretation to reject | Reason |
|---|---|
| The sidecar updates canonical FinRL truth | It only adds this support packet under `support/sidecars/`; no L1 docs are touched |
| `OSS-FINRL-V2-001` proves canary/live readiness | It proves production-scale DRL adapter behavior only; live gate remains closed |
| `model_artifact` output means approved registry artifact | Current artifact state remains `draft`; admission packet is a review request, not an approval |
| Docker smoke success authorizes shared dependency changes | FinRL dependencies must remain service-local in `services/research/finrl/requirements.txt` |
| Parent review should be decided from this packet alone | Reviewer must inspect the parent implementation and run verification commands; this packet only frames the review surface |
| Production-scale training unlocks live trading | The fail-closed rule (no `BROKER_PRODUCTION_LIVE_ENABLED`, no `CAPITAL_BINDING_LIVE_ENABLED`) applies; production DRL scope is research/paper only |

## 9. Handoff Notes

This packet is prepared by `Claude2` as the sidecar owner and is ready for
review by `Codex2`.

Handoff state:
- Parent `OSS-FINRL-V2-001` remains `todo` and is owned by `Gemini2`
- This acceptance packet does not block or accelerate the parent implementation
- `Codex2` should review this packet for completeness and accuracy of the
  acceptance surface before the parent submits for its own review
- Parent reviewer (`Codex2`) may use this packet as the review checklist when
  `OSS-FINRL-V2-001` reaches `review` status

---
*Prepared by Claude2 as a sidecar `acceptance_packet` helper for
`OSS-FINRL-V2-001`. This file is a support artifact and does not modify
canonical truth.*
