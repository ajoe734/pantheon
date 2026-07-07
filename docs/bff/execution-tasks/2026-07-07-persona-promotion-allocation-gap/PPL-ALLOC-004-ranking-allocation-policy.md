# PPL-ALLOC-004 - Ranking Allocation Policy And Rebalance Proposal Contract

Owner: Gemini
Reviewer: Claude2
Depends on: `PPL-ALLOC-001`, `PPL-ALLOC-003`
Type: BFF/policy implementation task

## Problem

The ranking model can recommend promotion, but real capital changes need a
stage-aware target-weight policy and an auditable rebalance proposal contract.
Without this, operators can see rankings but cannot safely update real capital
weights.

## Scope

- Implement stage-aware recommendation mapping:
  - paper high score -> paper-to-canary review;
  - canary high score -> canary-to-live review;
  - live high score -> allocation increase/retain review;
  - hard risk failure -> containment recommendation.
- Implement target-weight calculation from the gap spec formula.
- Enforce eligibility exclusions, caps, and smoothing.
- Add rebalance proposal create/read payloads with:
  - ranking snapshot id;
  - current weights;
  - target weights;
  - deltas;
  - cap reasons;
  - evidence refs;
  - simulation and constraints;
  - rollback target.
- Ensure recommendation submit and target-weight calculation do not apply live
  capital.

## Acceptance

- Tests cover paper, canary, live, excluded, capped, and emergency-risk rows.
- Rebalance proposal lines include persona id, stage, scope, current weight,
  target weight, delta, cap reason, and evidence.
- Increasing live capital requires a human approval reference before apply.
- Emergency reduction can bypass quarterly timing but cannot promote or
  increase allocation.

## Validation

```sh
git status -sb
python3 -m pytest services/control-plane/bff/tests/test_bff_persona_allocation_policy.py -q
python3 -m pytest services/control-plane/bff/tests/test_bff_rebalance_proposals.py -q
git diff --check
```
