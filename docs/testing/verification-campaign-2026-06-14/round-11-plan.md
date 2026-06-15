# Round 11 — Data/computation correctness (not shape)

**Date:** 2026-06-15
**New phase (rounds 11–20):** rounds 1–10 verified the contract *surface*
(reachability, status codes, envelopes, routing). This phase verifies the
*content*: are computed/aggregated values actually **correct**, not just
well-shaped?

## Why this round (not a duplicate)

No prior round checked a single computed number for correctness. This is the
first semantic-correctness round.

## Hypotheses

- H1: summary/aggregate fields equal a recomputation from the underlying items
  (e.g. persona-fleet `critical/degraded/healthy` counts).
- H2: every list endpoint's declared `total` equals its item count when not
  paginated (no miscount/off-by-one).
- H3 (cross-surface): the same entity population reported by two endpoints
  agrees (persona-league vs persona-fleet), modulo documented filters.
- H4 (derived fields): boolean/derived fields are internally consistent with
  their inputs and documented semantics.

## Method

1. Fetch persona-fleet; recompute the health tally from `items`; compare to
   `summary`.
2. Sweep all param-free GET list endpoints; compare `total` to `len(items)`
   where `next_page_token` is null.
3. Compare persona-league vs persona-fleet id sets (subset + delta).
4. Inspect derived governance/health fields for internal consistency; trace any
   anomaly to its code derivation.

## Pass criteria

- H1–H3: exact matches (or differences explained by a documented filter).
- H4: anomalies are either real defects (fixed via dev workflow) or explained by
  the field's documented derivation.
