# P2-RL-UPSTREAM-RUNTIME-SMOKE-001 Evidence Bundle

Task: FinRL RLlib Ray Tune governed runtime activation smoke
Phase: P2 Wave 8 External Activation
Owner: Claude
Reviewer: Codex2
Generated: 2026-05-01

## Summary

This directory contains activation smoke evidence for three RL frameworks:
- **FinRL** (finrl_ppo backend, PPO policy training)
- **RLlib** (rllib_ppo backend, bounded train/eval with rollouts)
- **Ray Tune** (ray_tune_search backend, hyperparameter search over RLlib)

All three frameworks attempted the real upstream backend. Each recorded an explicit
`dependency_or_config_error` (No module named 'finrl'/'ray') with `silent_stub_fallback=false`.
Stub-backed handoff artifacts were produced to complete the evidence bundle.

## Governance Boundary

All three frameworks confirm:
- `deployment_stage: none`
- `gate_state: closed`
- `order_routing_enabled: false`
- `broker_session_enabled: false`
- `paper_canary_live_promotion: false`
- `capital_binding: false`
- `direct_governance_write: false`

## Evidence Files

| File | Description |
|---|---|
| `activation_evidence_summary.json` | Combined acceptance gate results for all three frameworks |
| `manifest.json` | Full artifact index with checksums |
| `finrl_real_backend_attempt.json` | FinRL real backend failure (ModuleNotFoundError: finrl) |
| `finrl_artifact_bundle.json` | FinRL stub-backed artifact bundle (draft, offline-only) |
| `finrl_registry_entry.json` | FinRL draft registry entry |
| `finrl_candidate_packet.json` | FinRL candidate packet (offline review only) |
| `finrl_evaluator_packet.json` | FinRL EV-001 advisory packet |
| `finrl_dataset_evidence.json` | FinRL OHLCV dataset evidence (3 instruments, 30 periods) |
| `rllib_real_backend_attempt.json` | RLlib real backend failure (ModuleNotFoundError: ray) |
| `rllib_artifact_bundle.json` | RLlib stub-backed artifact bundle (draft, offline-only) |
| `rllib_registry_entry.json` | RLlib draft registry entry |
| `rllib_candidate_packet.json` | RLlib candidate packet (offline review only) |
| `rllib_evaluator_packet.json` | RLlib EV-001 advisory packet |
| `rllib_dataset_evidence.json` | RLlib OHLCV dataset evidence with train/eval rollout shapes |
| `ray_tune_real_backend_attempt.json` | Ray Tune real backend failure (ModuleNotFoundError: ray) |
| `ray_tune_artifact_bundle.json` | Ray Tune stub-backed optimizer_result bundle |
| `ray_tune_registry_entry.json` | Ray Tune draft registry entry |
| `ray_tune_candidate_packet.json` | Ray Tune candidate packet (offline review only) |
| `ray_tune_evaluator_packet.json` | Ray Tune EV-001 advisory packet |
| `ray_tune_dataset_evidence.json` | Ray Tune dataset evidence with search trial summary |

## Verification Commands

```bash
python3 services/research/finrl/activation_smoke.py --enable-activation-ready --backend real
python3 services/research/rllib/activation_smoke.py --enable-activation-ready --backend real
python3 services/research/rllib/ray_tune_activation_smoke.py --enable-activation-ready --backend real
```

## Acceptance Criteria Status

1. ✓ FinRL/RLlib/Ray Tune enabled paths run bounded governed smoke or produce explicit upstream dependency/config evidence
2. ✓ Reward environment, dataset, and artifact schemas enforced with persisted checksums and evaluator packet
3. ✓ Outputs remain research artifacts only — no broker session, order routing, paper/canary/live promotion, or capital binding
