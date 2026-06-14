# Round 10 — Undocumented/hidden-route audit + campaign close-out

**Date:** 2026-06-14
**Depth/breadth step:** Round 2 checked documented→live (ghost routes). Round 10
checks the **inverse and the complement**: live→documented. Are there routes
served by the app but **absent from the OpenAPI** (`include_in_schema=False`)?
Hidden state-mutating endpoints are a security/contract-completeness risk — an
undocumented shadow API. This closes the contract-surface story and the
campaign.

## Why this round (not a duplicate)

No prior round or doc enumerates the BFF's hidden/undocumented routes. Round 2's
sweep used the OpenAPI as ground truth and therefore could not see anything
excluded from it.

## Hypotheses

- H1: every hidden route is a benign framework/infra route (doc UI, openapi,
  CORS preflight) — no hidden state-mutating business endpoint.
- H2: every in-schema route is present in the generated OpenAPI `paths` (no
  silent omission).

## Method

1. Walk `app.routes`; collect routes with `include_in_schema=False`.
2. Collect in-schema routes whose path is absent from `app.openapi()["paths"]`.
3. Classify each hidden route; flag any business/mutation route.

## Pass criteria

- H1: all hidden routes are framework/infra; any hidden business mutation is a
  defect.
- H2: zero in-schema routes missing from the spec.
- Campaign summary written.
