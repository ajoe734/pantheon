# Round 2 — Contract-surface conformance breadth & ghost-route audit

**Date:** 2026-06-14
**Depth/breadth step over Round 1:** Round 1 probed a handful of control-plane
surfaces. Round 2 goes **broad** — every parameter-free GET path in the live
OpenAPI (196 of 447) — and **deep** — asserting the declared contract surface
is actually reachable (no "ghost routes" that 404/500 despite being published),
plus auditing route-ordering shadow bugs.

## Why this round (not a duplicate)

Prior BFF API gap audits (`docs/04/pantheon_bff_api_gap_*`, 05-2x) enumerated
*missing* endpoints against a spec. This round does the inverse and is novel:
it takes the **live, self-reported** OpenAPI as ground truth and checks that
each published route is genuinely reachable — catching routes that exist in the
schema but are dead at runtime (shadowing, misregistration). No prior doc does
runtime liveness conformance against the live schema.

## Hypotheses

- H1 (auth breadth): protected routes uniformly require auth (401 unauth).
- H2 (no ghost routes): every parameter-free published GET path resolves to its
  intended handler — none returns 404/500 due to shadowing or misregistration.
- H3 (deprecation honesty): routes that return 410 do so intentionally.
- H4 (doc/contract drift): docs-site URL references resolve against the live
  contract.

## Method

1. Enumerate `paths` from live `openapi.json`; select param-free GETs (196).
2. Probe each with stub auth; classify status codes.
3. For any non-200/401/422/410 result, inspect the route in code and determine
   whether it is a genuine defect.
4. Cross-check docs-site endpoint references against the live `paths` set.

## Pass criteria

- H1: all sampled protected routes 401 without a token.
- H2: zero ghost routes, OR every anomaly explained + any genuine defect fixed
  via the dev workflow.
- H3: 410s map to intentionally-retired endpoints.
- H4: drift documented; aspirational/stale references flagged.
