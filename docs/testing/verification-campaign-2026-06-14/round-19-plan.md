# Round 19 — Graceful degradation & degradation-signal consistency

**Date:** 2026-06-15
**Depth/breadth step:** Round 1 found F2 — an OODA card reporting stage
`status: ok` while its backing source was `missing` (false-green). Round 19
generalizes that across **every composed surface** in the API: when a source is
missing/unavailable, does the system degrade gracefully (200 + explicit marker)
and never mis-signal a dead source as healthy?

## Hypotheses

- H1 (resilience): an unavailable backing source yields a 200 degraded response
  with an explicit `unavailable`/`degraded` marker — not a 500.
- H2 (no false-green, generalized F2): no `meta.surfaces.*` entry reports
  `status: ok` while its `source` is `missing`.

## Method

1. Fetch every param-free GET endpoint; recursively collect every
   `meta.surfaces.*` `{status, source}` entry (including nested under `data`).
2. Tally statuses; flag any `status==ok && source==missing` (the F2 pattern).
3. Confirm degraded/unavailable surfaces are served on 200 responses.

## Pass criteria

- H1: degraded/unavailable surfaces appear on 200 responses (no 500).
- H2: zero false-green surfaces; any found is fixed via the dev workflow.
