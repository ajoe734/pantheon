# PPLG-004 - Paper Eligibility And Unified Competition Ranking Engine

Priority: P0

Area: Evaluation, ranking, promotion recommendation

Depends on: `PPLG-001`

## Goal

Implement the evaluation engine that determines paper eligibility, computes
promotion score, ranks paper challengers, canary challengers, and live
incumbents within the same cohorts, and creates recommendations without
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
- Implement unified cohort ranking by market, strategy family, frequency, risk
  budget, and capital pool type.
- Include `competition_track` values for `paper_challenger`,
  `canary_challenger`, `live_incumbent`, `watchlist_incumbent`, and
  `risk_off_excluded`.
- Compare challenger scores against live incumbent scores and emit
  `challenger_delta_score` and replacement-risk reasons.
- Emit recommendation packets only, not approvals.

## Acceptance Criteria

- Ineligible personas are not queued for promotion.
- Eligible personas expose score, score components, gates, cohort percentile,
  tie-breakers, and evidence refs.
- Ranking snapshots include paper challengers, canary challengers, and live
  incumbents in the same cohort result.
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
