# OSS-FINRL-001 Acceptance Packet and Dependency Map

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `OSS-FINRL-001-SIDECAR-ACCEPTANCE`
**Helper parent:** `OSS-FINRL-001`
**Parent owner:** `Codex`
**Parent reviewer:** `Gemini2`
**Parent status:** `review`
**Prepared by:** `Codex`
**Reviewer:** `Claude`
**Date:** `2026-05-17`
**Status:** `review_approved`
**Review outcome:** Claude approved this sidecar packet on
`2026-05-17T03:37:45Z`.

> Scope constraint: support artifact only. This packet summarizes acceptance
> criteria, dependency boundaries, reviewer checks, and current evidence for
> `OSS-FINRL-001`. It does not modify L1 canonical truth, core contracts,
> registry/governance implementation, or runtime behavior.

## 1. Executive Summary

`OSS-FINRL-001` is the current FinRL adapter skeleton task for bounded DQN/PPO
mini-training on governed historical OHLCV records. The parent implementation is
already in `review` and reports local plus Docker verification in `ai-status.json`.

The current repo surface is centered on `services/research/finrl/adapter.py` as
the public `train(strategy_spec_ref, backend)` entrypoint and
`services/research/finrl/engine/finrl_adapter.py` as the bounded offline policy
fit engine. Outputs are ExperimentRun-shaped and reference a `model_artifact`
registry entry while retaining the deferred/offline boundary:

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
| `ai-status.json` | Durable task state, owner/reviewer routing, parent acceptance criteria, and parent owner verification summary |
| `.orchestrator/task-briefs/oss_finrl_001_sidecar_acceptance.md` | Confirms this helper is support-only and must not mutate canonical truth |
| `services/research/finrl/adapter.py` | Public `train(strategy_spec_ref, backend)` entrypoint and backend selector |
| `services/research/finrl/engine/finrl_adapter.py` | Governed dataset validation, stub/PPO/DQN bounded fits, artifact bundle, registry entry, and candidate packet generation |
| `services/research/finrl/smoke_test.py` | Deterministic 60-step smoke coverage for stub, DQN, and PPO backends |
| `services/research/finrl/test_adapter.py` | Unit coverage for validation, gate behavior, worker artifact persistence, registry metadata, and governance boundary |
| `services/research/finrl/worker.py` | Env-gated worker entrypoint that writes artifact, registry, and candidate JSON packets |
| `services/research/finrl/config.py` | Backend alias normalization for `stub`, `finrl_ppo`, and `finrl_dqn` |
| `services/research/finrl/requirements.txt` | Service-local upstream/version pins; includes `finrl==0.3.7` |
| `services/research/finrl/Dockerfile` | CPU-only container surface using `python:3.11-slim` |
| `services/research/finrl/contract.md` | Local interface contract for the parent adapter skeleton |

## 3. Parent Acceptance Checklist

| Parent criterion | Current packet read | Reviewer check |
|---|---|---|
| `adapter.py` exposes `train(strategy_spec_ref, backend)` returning an ExperimentRun dict including `model_artifact_ref` | PASS: `train()` returns `run_id`, `backend`, `model_artifact_ref`, `artifact_type`, `metrics`, and `status` | Confirm response shape for all supported backends and reject any missing `model_artifact_ref` |
| Smoke trains on 60 days deterministic synthetic OHLCV for <= 1k steps and asserts positive Sharpe | PASS: `smoke_test.py` builds two instruments across 60 indexed observations and asserts `sharpe > 0`, `mean_reward_proxy > 0`, and `num_steps <= 1000` for stub/DQN/PPO | Confirm smoke remains deterministic enough for CI and that the date labels are used as ordering labels, not calendar validation |
| Smoke produces model artifact registered as `artifact_type=model_artifact` | PASS: public `train()` returns `artifact_type = model_artifact`; engine registry entry also uses `artifact_type = model_artifact` | Confirm reviewer can trace `model_artifact_ref` to `registry_entry["registry_id"]` |
| Dockerfile CPU-only, no NVIDIA image path | PASS: Dockerfile uses `python:3.11-slim`; no CUDA/NVIDIA base image is present | Optionally verify with `rg -n "cuda|nvidia" services/research/finrl/Dockerfile services/research/finrl/requirements.txt` |
| `requirements.txt` pins FinRL explicitly | PASS: `services/research/finrl/requirements.txt` contains `finrl==0.3.7` | Confirm Docker/package resolution stays service-local and does not move pins into shared requirements |

## 4. Repo-Current Behavior Map

| Surface | Behavior observed from code | Acceptance relevance |
|---|---|---|
| Public entrypoint | `train(strategy_spec_ref, backend="finrl_ppo")` converts strategy spec records into the governed dataset envelope and dispatches to PPO, DQN, or stub backend | Directly satisfies the adapter interface criterion |
| Backend selection | Aliases include `finrl`, `ppo`, `finrl_ppo`, `dqn`, `finrl_dqn`, `stub`, and `stub_finrl` | Review should test all accepted aliases if broadening beyond parent proof |
| Dataset validation | Requires non-empty OHLCV records, numeric `open/high/low/close/volume`, at least 2 instruments, and enough periods per instrument | Keeps the skeleton from accepting malformed OHLCV input silently |
| PPO path | `FinRLPPOBackend` performs bounded offline policy fit with `bounded_epochs <= 12` and returns `backend = finrl_ppo` | Satisfies PPO mini-training shape without claiming live execution |
| DQN path | `FinRLDQNBackend` performs bounded offline value update with `bounded_epochs <= 10` and returns `backend = finrl_dqn` | Satisfies DQN mini-training shape without claiming live execution |
| Stub path | `StubFinRLBackend` provides deterministic offline CI-safe behavior | Provides fast smoke path and fallback verification |
| Artifact bundle | Includes dataset checksum, environment schema, policy payload, evaluation summary, governance block, and registry hints | Supports reviewer traceability from run result to artifact envelope |
| Registry entry | Emits `artifact_type = model_artifact`, `artifact_state = draft`, storage ref, checksum, lineage, and evaluation summary | Matches parent artifact acceptance while preserving draft/offline state |
| Candidate packet | Requests only `candidate` projection with `deployment_stage = none` and `gate_state = closed` | Keeps review wording away from paper/canary/live promotion |
| Worker | Requires `PANTHEON_FINRL_PREP_ENABLED=1`; writes artifact bundle, registry entry, and candidate packet JSON files | Provides container/runtime handoff surface while staying non-default |

## 5. Dependency Map

### 5.1 External Package Dependencies

| Package | Pin or range | Source | Notes |
|---|---:|---|---|
| `finrl` | `==0.3.7` | `services/research/finrl/requirements.txt` | Upstream framework pin for the FinRL service-local container |
| `gymnasium` | `==1.2.3` | `services/research/finrl/requirements.txt` | Local RL environment dependency |
| `matplotlib` | `==3.10.9` | `services/research/finrl/requirements.txt` | Local plotting/runtime dependency inherited by this lane |
| `numpy` | `==2.4.5` | `services/research/finrl/requirements.txt` | Numeric dependency |
| `pandas` | `==3.0.3` | `services/research/finrl/requirements.txt` | Dataframe dependency |
| `pytest` | `==9.0.3` | `services/research/finrl/requirements.txt` | Container smoke test runner |

### 5.2 Internal Dependencies and Boundaries

| Dependency | Relationship | Boundary |
|---|---|---|
| `services/research/finrl/adapter.py` | Public adapter facade | May be reviewed for parent acceptance; this sidecar does not edit it |
| `services/research/finrl/engine/finrl_adapter.py` | Core bounded offline training and artifact generation | Produces draft research artifact envelopes only |
| `services/research/finrl/smoke_test.py` | Parent acceptance smoke | Runs stub/DQN/PPO path with synthetic OHLCV |
| `services/research/finrl/test_adapter.py` | Regression tests | Covers validation, artifact, worker, and governance shape |
| `services/research/finrl/Dockerfile` | CPU-only container | Must remain independent from other research adapter requirements |
| `services/learning/rl/RL_PATH_APPROVAL_GATE.md` | RL activation gate | Not modified here; current implementation language preserves closed gate semantics |
| Registry/governance runtime code | Downstream consumers | Not touched by this sidecar; parent output is a packet shape, not a registry write |

### 5.3 Task-Level Dependencies

| Task | Relationship | Current read |
|---|---|---|
| `OSS-FINRL-001` | Parent implementation task | `review`; owner `Codex`, reviewer `Gemini2` |
| `OSS-FINRL-001-SIDECAR-ACCEPTANCE` | This helper task | `review_approved`; support-only artifact approved by Claude |
| Other research adapters | Sibling services | No dependency declared in `ai-status.json`; sidecar must not mutate sibling adapters |

## 6. Verification Snapshot

Parent owner evidence recorded in `ai-status.json` reports:

- `py_compile` passed for `adapter.py`, `engine/finrl_adapter.py`, `config.py`, `worker.py`, `smoke_test.py`, and `test_adapter.py`
- `pytest -q services/research/finrl/smoke_test.py services/research/finrl/test_adapter.py` reported `20 passed`
- direct smoke execution passed for stub, DQN, and PPO
- worker execution passed for `PANTHEON_FINRL_BACKEND=stub`, `finrl_dqn`, and `finrl_ppo`
- Docker build/run passed with `python:3.11-slim` CPU-only surface and no CUDA/NVIDIA/stable-baselines/torch package path reported by the parent owner
- `git diff --check -- services/research/finrl` passed

Sidecar-local checks run by Codex on `2026-05-17`:

| Command | Result |
|---|---|
| `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/research/finrl/adapter.py services/research/finrl/engine/finrl_adapter.py services/research/finrl/config.py services/research/finrl/worker.py services/research/finrl/smoke_test.py services/research/finrl/test_adapter.py` | PASS |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q services/research/finrl/smoke_test.py services/research/finrl/test_adapter.py` | PASS: `20 passed in 4.02s` |
| `rg -n "cuda|nvidia|stable-baselines|torch" services/research/finrl/Dockerfile services/research/finrl/requirements.txt` | PASS: no matches |
| `git diff --check -- support/sidecars/OSS-FINRL-001/OSS-FINRL-001-SIDECAR-ACCEPTANCE.md` | PASS |

## 7. Reviewer Cautions

| Caution | Why it matters |
|---|---|
| This is not RL gate approval | The packet supports review of an adapter skeleton; it does not reopen production RL training or promote FinRL beyond the task evidence |
| Parent is in review, not done | Reviewer should treat parent owner proof as reported evidence until parent review approves it |
| `smoke_test.py` date strings are ordering labels | The 60 observations use synthetic `2026-05-XX` labels beyond calendar month length; this is acceptable for ordering-only smoke but should not be described as validated calendar data |
| Worker gate and smoke entrypoint differ | Worker is env-gated by `PANTHEON_FINRL_PREP_ENABLED`; smoke test is CI-facing and calls `train()` directly |
| Package import readiness is metadata-based in the bounded skeleton | PPO/DQN paths record whether package metadata resolves, then run local bounded policy math; review should not overstate this as full upstream FinRL training API coverage |
| Artifact output is a packet shape, not a live registry write | `model_artifact_ref` points to the generated registry id; no registry service or governance write path is opened by this task |

## 8. Scope Boundary - Reject These Interpretations

| Interpretation to reject | Reason |
|---|---|
| The sidecar updates canonical FinRL truth | It only adds this support packet under `support/sidecars/` |
| `OSS-FINRL-001` proves canary/live readiness | It proves bounded offline adapter skeleton behavior only |
| `model_artifact` output means approved registry artifact | Current artifact state remains `draft`; candidate projection is offline review only |
| Docker smoke success authorizes shared dependency changes | FinRL dependencies remain service-local in `services/research/finrl/requirements.txt` |
| Parent review should be decided from this packet alone | Reviewer still needs to inspect parent implementation and verification evidence |

## 9. Review Outcome and Closeout

Claude reviewed and approved this sidecar packet on `2026-05-17T03:37:45Z`.
The approved scope remains support-only under
`support/sidecars/OSS-FINRL-001/`; no canonical truth, runtime, registry, or
governance implementation files are modified by this sidecar.

Owner closeout verification reran on `2026-05-17`:

| Command | Result |
|---|---|
| `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/research/finrl/adapter.py services/research/finrl/engine/finrl_adapter.py services/research/finrl/config.py services/research/finrl/worker.py services/research/finrl/smoke_test.py services/research/finrl/test_adapter.py` | PASS |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q services/research/finrl/smoke_test.py services/research/finrl/test_adapter.py` | PASS |
| `rg -n "cuda|nvidia|stable-baselines|torch" services/research/finrl/Dockerfile services/research/finrl/requirements.txt` | PASS: no matches |
| `git diff --check -- support/sidecars/OSS-FINRL-001/OSS-FINRL-001-SIDECAR-ACCEPTANCE.md` | PASS |

Parent decision remains separate: `Gemini2` remains the reviewer for
`OSS-FINRL-001`; this sidecar closeout does not approve or absorb parent
implementation changes.

Retained reviewer stance:

1. approve the sidecar only if the support artifact remains scoped to
   `support/sidecars/OSS-FINRL-001/`
2. confirm the checklist maps to current parent files without modifying
   canonical truth
3. keep the parent decision separate: `Gemini2` remains the reviewer for
   `OSS-FINRL-001`
4. reject wording that turns this acceptance packet into production RL
   activation, registry promotion, or live trading readiness

---
*Generated by Codex as a sidecar `acceptance_packet` helper for
`OSS-FINRL-001`. This file is a support artifact and does not modify canonical
truth.*
