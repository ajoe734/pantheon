# PPLG-004 - Paper Eligibility, Promotion Score, And Cohort Ranking Engine

Priority: P0

Area: Evaluation, ranking, promotion recommendation

Depends on: `PPLG-001`

## Goal

Implement the paper evaluation engine that determines eligibility, computes
promotion score, ranks within cohorts, and creates recommendations without
approving live capital.

## Required Work

- Implement hard eligibility gates:
  - evaluation window
  - decision events
  - paper fills
  - after-cost return or benchmark alpha
  - drawdown within budget
  - data health
  - runtime uptime
  - traceability
  - no critical policy violation
- Implement default score:
  - 30% performance
  - 20% risk control
  - 15% consistency
  - 15% execution realism
  - 10% operational reliability
  - 10% governance fit
  - penalties
- Implement cohort ranking by market, strategy family, frequency, risk budget,
  and capital pool type.
- Emit recommendation packets only, not approvals.

## Acceptance Criteria

- Ineligible personas are not queued for promotion.
- Eligible personas expose score, score components, gates, cohort percentile,
  tie-breakers, and evidence refs.
- Score thresholds match the gap spec defaults.
- A recommendation cannot start canary/live without PPLG-005 human decision.
- Tests cover high score, low score, missing evidence, high correlation, and
  low-frequency threshold override.

## Artifacts

- `services/evaluation/*`
- `services/optimizer-svc/*`
- `services/control-plane/bff/*`
- `services/control-plane/bff/tests/*evaluation*`
- `tests/e2e/*persona*promotion*`
