# PPL-GOV-003 - BFF Recommendation Submit Bridge

Owner: Gemini2
Reviewer: Codex
Depends on: PPL-GOV-001
Type: BFF implementation task

## Purpose

Turn a PM-12 recommendation row into a persisted governance review instead of a
local-only frontend inbox id.

## Scope

- Add a BFF write route or adapter for quarterly ranking recommendation submit.
- Validate quarter, recommendation id, recommendation action id, persona id, and
  stage target.
- Reuse `QuarterlyRankingRecommendationSubmit` command semantics where possible.
- Create or return a promotion-review / human-inbox item.
- Return links for review detail and decision endpoint.
- Preserve `liveCapitalMutation=false`.

## Acceptance

- Submit requires operator, approver, or admin role.
- Invalid recommendation id returns 404 or validation error with clear
  precondition.
- Valid submit returns 202/200 with review id and human inbox id.
- Repeated idempotency key does not create duplicate reviews.
- Stage-aware target is explicit:
  `paper_running -> canary_candidate`,
  `canary_running -> live_candidate`,
  `live_running -> live_rebalance_review`.
- Tests prove the route does not change live capital, stage, or broker state.

## Validation

```sh
python3 -m pytest services/control-plane/bff/tests/test_bff_promotion_reviews.py
python3 -m pytest services/control-plane/bff/tests/test_bff_b5_humangate_commands.py
python3 -m pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py
git diff --check
```
