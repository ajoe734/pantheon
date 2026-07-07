# PPL-ALLOC-006 - Promotion And Allocation Workbench

Owner: Claude
Reviewer: Codex
Depends on: `PPL-ALLOC-003`, `PPL-ALLOC-004`
Type: frontend implementation task

## Problem

`Promotion & Allocation` exists as a unified entry, but it is still mostly a
tab wrapper around old ranking/rebalance components. Operators need one
workbench that explains paper promotion, real ranking, quarterly target
weights, rebalance approval, and emergency actions.

## Scope

- Expand `/management/promotion-allocation` into:
  - `Paper candidates`;
  - `Real ranking`;
  - `Quarterly capital`;
  - `Emergency actions`.
- Paper candidates show stage, paper ledger, sample sufficiency, score,
  recommendation, evidence, and submit-review action.
- Real ranking shows canary/live stage, current weight, target weight,
  proposed delta, cap reason, and approval state.
- Quarterly capital shows rebalance proposals with simulation/constraint
  status and links to `/management/rebalance/:id`.
- Emergency actions show containment recommendations and links to Human Inbox /
  governance detail.
- Keep recommendation submit and rebalance proposal creation honest about
  write-disabled/local-only state.

## Acceptance

- Tests prove old links route to the correct tab and no duplicate standalone
  workflow page is exposed in nav.
- Workbench rows distinguish recommendation, review, approval, and applied
  states.
- Real ranking cannot display a capital increase as applied before approval.
- Rebalance proposal creation returns or links to a review/detail item.

## Validation

```sh
git status -sb
npm test -- src/management/pages/oversight/PromotionAllocation.test.tsx
npm test -- src/management/pages/oversight/RankingRecommendationPages.test.tsx
npm test -- src/management/pages/oversight/HumanGateDetail.test.tsx
npm run lint
npm run build
git diff --check
```
