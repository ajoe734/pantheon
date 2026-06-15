# Round 31 — Naive/aware datetime mixing audit

**Date:** 2026-06-15
**Depth/breadth step:** `datetime.utcnow()` returns a **naive** datetime;
comparing or sorting it against a **timezone-aware** datetime raises `TypeError`
→ 500. This round hunts that mixing across the fleet.

## Hypotheses

- H1: no `utcnow()` result is compared/sorted against an aware datetime.

## Method

1. Grep all `utcnow()` usages in non-test service code.
2. Classify each: serialization (`.isoformat()`/`.timestamp()` — safe) vs a
   datetime assigned to a variable later compared/sorted (risky).
3. Inspect each risky site; reproduce any aware/naive comparison.

## Pass criteria

- H1: no aware/naive mixing; any `TypeError`-producing site fixed via the dev
  workflow with a regression test.
