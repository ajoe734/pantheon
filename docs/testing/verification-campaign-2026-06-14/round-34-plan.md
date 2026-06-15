# Round 34 — ZeroDivisionError audit (metric denominators)

**Date:** 2026-06-15
**Depth/breadth step:** Computed metrics (rates, shares, averages, utilization)
divide by counts/totals; a zero denominator raises `ZeroDivisionError` → 500.

## Hypotheses

- H1: every division in a reachable request path guards a zero denominator.

## Method

1. AST-scan service code for `Div`/`FloorDiv` with a non-constant denominator;
   exclude `pathlib.Path` `/` joins (false positives).
2. For each numeric site, check for a guard (`max(x,1)`, `if values else …`,
   `not in (None, 0)`, entry early-return).
3. Trace the reachable (HTTP-path) ones; classify internal helpers separately.

## Pass criteria

- H1: reachable divisions guarded; any confirmed reachable zero-denominator
  fixed via the dev workflow. Unconfirmed internal helpers documented.
