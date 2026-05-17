# OSS-FINRL-V2-001 Acceptance Packet and Dependency Map

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `OSS-FINRL-V2-001-SIDECAR-ACCEPTANCE`
**Helper parent:** `OSS-FINRL-V2-001`
**Parent owner:** `Gemini2`
**Parent reviewer:** `Codex2`
**Parent status:** `todo`
**Prepared by:** `Gemini2`
**Date:** `2026-05-17`

> Scope constraint: support artifact only. This packet summarizes acceptance
> criteria, dependency boundaries, reviewer checks, and current evidence for
> `OSS-FINRL-V2-001`. It does not modify L1 canonical truth, core contracts,
> registry/governance implementation, or runtime behavior.

## 1. Executive Summary

`OSS-FINRL-V2-001` upgrades the FinRL DRL adapter from skeleton to production scale.
It trains PPO/DDPG models on TWSE OHLCV datasets and submits registry admission
packets for approved experiment artifacts.

- `artifact_type = model_artifact`
- `artifact_state = draft`
- `deployment_summary.current_stage = none`
- `governance.gate_state = closed`
- `direct_live_influence = false`
- `allowed_next_action = offline_registry_review_only`

This sidecar does not approve the parent implementation. It packages the review
surface so the assigned reviewer can verify acceptance without treating the
support packet as canonical promotion or RL gate activation.

## 2. Parent Acceptance Checklist

| Parent criterion | Current packet read | Reviewer check |
|---|---|---|
| `production_drl_run.py` exposes `run_production()` returning evaluation_summary | TBD: Implementation in progress | Confirm PPO/DDPG training loop is CPU-only and produces model artifact |
| Trains on real TWSE OHLCV dataset | TBD | Confirm data path from MGMT-QLIB-001 manifest |
| Produces model artifact registered as `artifact_type=model_artifact` | TBD | Confirm artifacts registered with checksum and lineage |
| Dockerfile CPU-only, no NVIDIA image path | TBD | Confirm CPU-only surface |
| `requirements.txt` pins FinRL explicitly | TBD | Confirm FinRL package pinning |

## 3. Scope Boundary - Reject These Interpretations

| Interpretation to reject | Reason |
|---|---|
| The sidecar updates canonical FinRL truth | It only adds this support packet under `support/sidecars/` |
| `OSS-FINRL-V2-001` proves canary/live readiness | It proves production-scale DRL adapter behavior only |
| `model_artifact` output means approved registry artifact | Current artifact state remains `draft` |
| Docker smoke success authorizes shared dependency changes | FinRL dependencies remain service-local |
