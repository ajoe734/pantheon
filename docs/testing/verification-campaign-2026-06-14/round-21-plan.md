# Round 21 — Fleet route-resolution audit (beyond the BFF)

**Date:** 2026-06-15
**Phase 3 (rounds 21–35):** broadens from the operator BFF to the rest of the
system — the ~29 other microservices, cross-service concerns, CORS/security,
canonical state, and orchestrator tooling.

Round 9 audited route resolution on the BFF and found F3 (a shadowed SSE route)
+ F9 (benign duplicate registrations). Round 21 applies the same audit to
**every other FastAPI service** — do any of them have dead/shadowed routes or
silent duplicate registrations?

## Hypotheses

- H1: no service has a static route shadowed by an earlier `{param}` route.
- H2: no service registers the same `(method, path)` more than once with
  divergent handlers.

## Method

1. Enumerate FastAPI app entrypoints (`= FastAPI(`) across `services/`.
2. Import each app in an isolated subprocess (PYTHONPATH = repo root + service
   dir); walk `app.routes` in registration order.
3. Flag any static route matched by an earlier `{param}` route, and any
   duplicate `(method, path)`.

## Pass criteria

- H1/H2: zero shadowed routes and zero duplicate registrations across the
  importable services; any found is a defect fixed via the dev workflow.
