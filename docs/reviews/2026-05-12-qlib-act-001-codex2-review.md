# QLIB-ACT-001 Codex2 Review

Reviewer: Codex2
Date: 2026-05-12
Disposition: approved

## Scope Reviewed

- `.orchestrator/task-briefs/qlib_act_001.md`
- `services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md`
- `integrations/qlib/activation_packet.md`
- `services/learning/qlib/ACTIVATION_CRITERIA.md`
- `services/research/qlib/adapter/qlib_adapter.py`
- Task-scoped commit `f4b32a7b`

## Verification Commands

```bash
git show --stat --oneline --decorate --no-renames f4b32a7b
git show --name-only --format=fuller f4b32a7b
git diff --check f4b32a7b^..f4b32a7b -- services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md integrations/qlib/activation_packet.md
python3 -m unittest discover -s services/research/qlib -p 'test_*.py'
python3 services/research/qlib/smoke_test.py
```

## Review Notes

- The StrategySpec satisfies the QLIB-ACT-001 acceptance scope: problem statement, TWSE + TPEx universe, >=50 instrument / >=2 year daily OHLCV floor, 5-day forward-return label, 5-day primary horizon, 13 OHLCV-derived features, LightGBM baseline configuration, IC/Sharpe gate targets, and explicit supervised-vs-RL/TRL rationale are all present.
- The candidate registry artifact ID `qlib-tw-cross-sectional-alpha-spec-v1` is issued in the StrategySpec and referenced in the activation packet for QLIB-ACT-002 and QLIB-ACT-003 lineage.
- The documents preserve the required safety posture: `artifact_state=draft`, `deployment_summary.current_stage=none`, no production registry write, no order route, and downstream governed dataset / LightGBM activation evidence still pending.
- The Qlib activation criteria and adapter constants agree with the documented numerical floor: >=50 instruments, >=2.0 years, >=504 daily periods, and daily frequency for v1 activation.
- Task commit `f4b32a7b` is scoped to the two declared artifacts.

## Acceptance Check

| Criterion | Result |
|---|---|
| StrategySpec problem statement | Pass |
| Universe | Pass |
| Label | Pass |
| Horizon | Pass |
| Evaluation metrics | Pass |
| Why supervised LightGBM vs RL/TRL | Pass |
| RS-003 gate evidence attached or referenced | Pass |
| Candidate registry artifact ID issued | Pass |
| No production registry write before review approval | Pass |
| `artifact_state=draft` and `deployment_summary.current_stage=none` preserved | Pass |

## Closeout Notes

- Owner closeout should update any "pending review" / "pending handoff" wording if the artifacts are published after this approval, while preserving `artifact_state=draft` and `deployment_summary.current_stage=none`.
- QLIB-ACT-002 and QLIB-ACT-003 remain responsible for governed dataset proof, real/stub activation run evidence, and registry admission packet evidence.

## Disposition

Approved. Move `QLIB-ACT-001` to `review_approved` and return it to owner `Claude` for final closeout.
