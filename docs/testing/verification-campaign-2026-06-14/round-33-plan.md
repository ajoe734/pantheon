# Round 33 — Fleet-wide aware/naive sort-key audit

**Date:** 2026-06-15
**Depth/breadth step:** F17/F18 fixed the BFF. Round 33 extends the audit to the
**whole fleet** — does any other service have a `... or datetime.min` (naive
floor) sort key that can mix with aware datetimes and 500?

## Hypotheses

- H1: no non-BFF service sorts with a naive `datetime.min` floor over values
  that can be tz-aware.

## Method

1. Grep all services (ex-BFF) for `or datetime.min`/`or datetime.max` sort keys.
2. For each, determine whether the sorted value can be tz-aware (e.g. parsed
   from `"...Z"`).
3. Fix any genuine case with the F17/F18 pattern + regression test.

## Pass criteria

- H1: fleet-wide clean, or each case fixed via the dev workflow.
