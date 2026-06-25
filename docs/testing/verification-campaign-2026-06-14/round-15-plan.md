# Round 15 — Pagination correctness & cursor robustness

**Date:** 2026-06-15
**Depth/breadth step:** Verifies that paginated reads are **complete and
non-duplicating** under a small page size, and that cursor/limit parameters are
robustly validated (no 500 on malformed cursors).

## Hypotheses

- H1: paginating a collection at `page_size=2` yields the **same set** as a
  single large fetch — no missing items, no duplicates, no infinite loop.
- H2: malformed pagination params (`page_size=abc/-1/0/huge/2.5`, garbage/huge
  `page_token`, bad date filters) return a clean 4xx — never 500.

## Method

1. For every param-free GET list endpoint with >3 items, walk the cursor at
   `page_size=2`; compare the union of pages to the full fetch.
2. Fuzz pagination/limit/date query params on a real paginator (`/bff/audit`)
   and a `limit`-based endpoint.

## Pass criteria

- H1: union == full set, zero duplicates, bounded page count, for every
  paginated endpoint.
- H2: zero 500s on cursor/param fuzzing.
