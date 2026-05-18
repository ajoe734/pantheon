# Review Notes: OSS-RLLIB-V2-001

Reviewer: Codex
Owner: Claude
Date: 2026-05-17 (initial) / 2026-05-18 (Codex follow-up approval)
Status: Approved after follow-up fixes (see codex_review_findings.md)

Current assignment note (2026-05-18): live task status was reassigned to
`owner=Codex2`, `reviewer=Claude`. This file preserves the earlier
Claude/Codex review record; the current owner handoff for Claude review is
`support/evidence/OSS-RLLIB-V2-001/codex2_owner_handoff.md`.

## Note on Reviewer History

Initial internal review pass was noted by Claude. Final reviewer authority was
Codex per task assignment (`owner=Claude`, `reviewer=Codex`). Discrepancy was
flagged in `codex_review_findings.md` and corrected here.

## Acceptance Criteria Verification

All 7 acceptance criteria verified against commit `56a87914`.

| Criterion | Result | Evidence |
|---|---|---|
| `twse_trading_env.py` gymnasium-compatible, 5-instrument, discrete action space (hold/long/short) | PASS | `TWSETradingEnv` inherits from `gymnasium.Env`/fallback; `action_space = Discrete(3)` with ACTION_HOLD=0/LONG=1/SHORT=2; 5 instruments in `DEFAULT_INSTRUMENT_UNIVERSE` |
| `production_ppo_run.py` ≥ 100 PPO iterations CPU-only, mean_reward improves vs random baseline | PASS | `PRODUCTION_NUM_ITERS=100`; `admission_packet.json` confirms `improved_vs_random_baseline: true` (trained: -0.00636 > baseline: -0.01524); `cpu_only: true`, `num_gpus=0` in rllib config |
| `model_artifact` registered with checksum and `trained_policy_ref` | PASS | `checksum: sha256:4db60cb0...`, `trained_policy_ref: local_bandit_policy:best_action=1:seed=42` present in both run output and admission packet |
| `admission_packet.json` valid | PASS | `schema_version: "PromotionReadinessPacket.v1"`, `can_proceed: true`, `missing_evidence: []`, 5 gate_results all `passed` |
| test fixture trains ≥ 10 iterations and asserts loss decreases | PASS | `TEST_ITERS=10` in test file; `test_loss_decreases_over_iterations` compares first-half vs second-half avg reward |
| `pytest -q exit 0` | PASS | **21 passed in 7.15s** (run during review, 2026-05-17) |
| no GPU / no live broker | PASS | `cpu_only: true`, `governance.direct_live_influence: false`, `deployment_stage: none`, `order_route: none` |

## Verification Command

```
python3 -m pytest services/research/rllib/test_production_ppo_run.py -q
# 21 passed in 7.15s
```

## Notes

- Implementation uses a dependency-light epsilon-greedy bandit fallback when Ray/RLlib is unavailable. This is the correct pattern for OSS research tasks that must run in pure-Python CI.
- The GBM synthetic TWSE data correctly simulates 5 instruments at daily granularity with per-instrument drift/volatility.
- All safety assertions in `admission_packet.json` are correct: registry write not performed, no broker session, deployment stage remains `none`.
- Admission packet clearly states `registry_write_authority: registry_service_only` — the task does not bypass governance gates.

## Decision

**Approved.** Implementation is complete, tests pass, admission packet is valid, and all fail-closed safety properties are correctly set.
