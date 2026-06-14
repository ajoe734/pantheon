# Round 9 — Systematic route-resolution audit

**Date:** 2026-06-14
**Depth/breadth step:** Round 2 found and fixed one shadowed route
(`incidents/stream`). Round 9 generalizes that to the **whole** 447-path
surface: are there *other* dead routes from shadowing or duplicate
registration? This is a route-resolution-correctness audit against the live
in-process route table (true registration order).

## Why this round (not a duplicate)

Round 2 fixed a single instance; no doc proves that was the *only* instance, nor
audits duplicate `(method, path)` registrations or duplicate operationIds across
the BFF.

## Hypotheses

- H1: no static route is shadowed by an earlier parameterized (`{param}`) route
  (the `incidents/stream` class of bug).
- H2: where a `(method, path)` is registered more than once, the **first match**
  (the served handler) is the intended dedicated handler, not a generic
  alias/stub.
- H3: no duplicate operationIds (which break client generation).

## Method

1. Walk `app.routes` in registration order; for each static route, check whether
   any earlier `{param}` route's regex matches it.
2. Group routes by `(method, path)`; flag any with >1 handler; determine the
   first-match winner and compare to the intended handler.
3. Generate the OpenAPI doc and tally operationIds.

## Pass criteria

- H1: zero shadowed static routes.
- H2: every duplicated route resolves to its intended handler; otherwise the
  active mis-resolution is a defect to fix.
- H3: zero duplicate operationIds.
- Latent hazards (benign duplicates) are locked with a guard test.
