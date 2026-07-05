# PPL-GOV-001 - Gap Spec And Current-State Guard

Owner: Codex
Reviewer: Claude
Depends on: none
Type: audit and source-of-truth task

## Purpose

Prevent the fleet from implementing another partial recommendation surface
without a complete promotion-governance loop.

## Scope

- Lock the gap spec under
  `docs/04/pantheon_persona_promotion_governance_gap_2026-07-05/`.
- Confirm current BFF PM-12 ranking/recommendation routes.
- Confirm existing frontend Persona League / Quarterly Ranking submit behavior.
- Confirm whether promotion-review BFF management routes exist.
- Record the required management locations for recommendation, queue, and
  approval.

## Acceptance

- Gap spec clearly states current implemented behavior and missing closed-loop
  behavior.
- Execution packet has dependencies, owners, reviewers, and production
  acceptance.
- The spec explicitly says recommendation submit and approval do not directly
  mutate live capital.
- No source behavior changes are made by this task.

## Validation

```sh
git status -sb
rg -n "quarterly-ranking|promotion-reviews|QuarterlyRankingRecommendationSubmit" services/control-plane/bff src docs
git diff --check
```
