# E2E-R5 — Capital-pool / binding financial-safety invariants

**Round:** E2E-R5 of the e2e business-flow verification campaign
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r5-capital-integrity
**Business flow:** capital-pool → persona-capital-binding → runtime-binding.

## Plan

Gate the financial-safety invariants of the capital→binding chain:
1. Single active binding per pool (runtime-manager RUN-001).
2. Every active binding's `capital_pool_id` resolves to a listed, active pool.
3. Backing pools have `single_runtime_enforced == True`.
4. Backing pools have a positive budget.

## Verification program

`scripts/verify_e2e_capital_integrity.py` (+ unit test), wired into
`run-acceptance.sh` full mode as `e2e-capital-integrity-verifier`. FAILs on any
invariant violation.

## Live result (dev, 2026-06-15)

```
capital integrity over 16 active bindings / 15 pools:
  RUN-001 over-allocated pools : 0      <- invariant HOLDS
  single_runtime_enforced off  : 0      <- invariant HOLDS
  non-positive budget          : 0      <- invariant HOLDS
  dangling backing pools       : 1      <- VIOLATION
FAIL: binding rb-bf09c882… -> pool pool-devloop-l0-001 (unlisted)
```

## Finding

The core financial-safety invariants **hold**: no pool is over-allocated
(single active binding per pool), every backing pool enforces `single_runtime`,
and all budgets are positive (10000.0). This is a good-news round — the
allocation safety properties are intact.

The single violation is the same `devloop-l0-001` chain flagged in E2E-R1/R3:
binding `rb-bf09c882…` references `pool-devloop-l0-001` which is not in the
capital-pools listing. A rescue/placeholder binding whose pool was never
materialized — a data-provenance gap, not an over-allocation.

## Disposition

- **Shipped (code/CI):** the financial-safety invariant verifier + logic test +
  CI gate, so over-allocation / disabled enforcement / dangling-or-inactive
  backing pools are caught going forward (currently FAILs only on the known
  devloop dangling pool).
- **Flagged (upstream build):** retire the orphan `devloop-l0-001` binding or
  materialize its capital pool (same upstream gap as R1/R3).

## Next round

E2E-R6: intervention / approval state-machine transition enforcement (v5
interventions cannot skip claim → decide → escalate → two-man-sign), or deepen
R5 with per-binding capital-allocation vs realized-exposure reconciliation.
