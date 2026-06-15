# Round 32 — Generalize F17: all aware/naive sort-key mixing

**Date:** 2026-06-15
**Depth/breadth step:** Round 31 fixed one `_parse_rfc3339(x) or datetime.min`
sort key (F17). This round asks: how many siblings of that exact pattern exist,
and are they all latent `TypeError` 500s?

## Hypotheses

- H1: every `_parse_rfc3339(...) or datetime.min` sort key in `read_store.py`
  has the same aware/naive mixing bug and must be normalized.
- H2: a blanket "make `_parse_rfc3339` always naive" fix is unsafe (it would
  break aware comparison sites, e.g. the F12 audit `from_ts` filter).

## Method

1. Grep all `or datetime.min` (naive floor) sort keys in `read_store.py`.
2. Confirm the global-normalize option is unsafe by tracing a comparison caller
   (`list_governance_audit_events` `from_ts`, fed by main.py's aware
   `_parse_rfc3339`).
3. Wrap each sort-key expression with `.replace(tzinfo=None)` (the F17 fix),
   leaving comparison sites untouched.
4. Add a static guard that every `or datetime.min` floor is tz-normalized.

## Pass criteria

- H1: all sibling sort keys normalized; tests green.
- H2: comparison sites unchanged (no regression to F12's audit filter).
