# First-Week Observation Window

Status: operational runbook for broker production-live activation
Source: 2026-05-19 blueprint supplement Part B5

This runbook defines the evidence packet consumed by
`services/broker/live_activation/first_week_observation.py`. The builder creates
a deterministic 13-check report for the first seven days after broker
production-live activation. It does not record approvals, mutate runtime state,
connect to the broker, or enable live flags.

## Required Packet

The report packet must include:

- `live_activation_ref`, `deployment_plan_ref`, `runtime_binding_id`, and
  `capital_pool_id`
- `observation_window.live_started_at` and
  `observation_window.observed_through_at` covering at least seven days
- seven `daily_checkins` entries, one for each day 1-7, each complete or
  recorded with an evidence reference
- runtime and broker health evidence
- telemetry heartbeat and PnL snapshot counts for the window
- audit retention and audit replay evidence
- risk, exposure, liquidity, execution-quality, and drift threshold evidence
- kill-switch, safe-mode, rollback, and no-go evidence
- explicit zero active incidents, zero operator blockers, and no active hard-fail
  conditions

## Thirteen Checks

| ID | Check | Fail-closed condition |
| --- | --- | --- |
| `first_week_b5_01` | Live activation identity is traceable. | Missing activation, deployment plan, runtime binding, or capital binding evidence. |
| `first_week_b5_02` | First-week observation window covers seven days. | Missing/invalid timestamps or a window shorter than seven days. |
| `first_week_b5_03` | Daily operator check-ins are complete for days 1-7. | Missing day or incomplete day status. |
| `first_week_b5_04` | Runtime binding and broker session stayed healthy. | Missing evidence or unhealthy runtime/broker session. |
| `first_week_b5_05` | Telemetry stream stayed available with required snapshots. | Missing telemetry path, fewer than seven heartbeats, or fewer than seven PnL snapshots. |
| `first_week_b5_06` | Audit retention and replay path stayed available. | Missing audit retention, replay, or audit event evidence. |
| `first_week_b5_07` | PnL and drawdown stayed within risk thresholds. | PnL or drawdown breach. |
| `first_week_b5_08` | Exposure and liquidity stayed within policy thresholds. | Exposure or liquidity breach. |
| `first_week_b5_09` | Slippage, fills, and order rejections stayed within thresholds. | Slippage, fill, or order-rejection breach. |
| `first_week_b5_10` | Short-term drift is clear or cooldown governance is satisfied. | Drift is present without governance evidence or without the required cooldown. |
| `first_week_b5_11` | Kill-switch and safe-mode paths remain reachable. | Missing kill-switch or safe-mode evidence. |
| `first_week_b5_12` | Rollback/frozen transition path remains ready. | Missing rollback target or frozen/rollback transition evidence. |
| `first_week_b5_13` | No-go, incident, and hard-fail conditions are clear. | Open incident, open operator blocker, missing no-go evidence, or active hard-fail condition. |

## Decision Tree

The builder returns one of four decisions:

| Decision | Meaning |
| --- | --- |
| `continue_live` | All 13 checks are ready and the first-week report is complete. |
| `freeze_or_safe_mode` | A hard-fail, telemetry, kill-switch, safe-mode, runtime, or broker health blocker is present. |
| `escalate_to_risk_owner` | Risk threshold, drift, execution quality, incident, or no-go evidence blocks continuation. |
| `hold_live_change` | Evidence is missing or incomplete but no immediate freeze/safe-mode category was detected. |

Only `continue_live` sets `can_continue_live=true`. Every other decision is
fail-closed and requires operator/risk-owner follow-up outside this builder.

## Local Verification

Focused validation:

```bash
pytest -q tests/broker/test_first_week_observation.py
python3 -m py_compile services/broker/live_activation/first_week_observation.py tests/broker/test_first_week_observation.py
```
