# Round 22 — Input-robustness 500-hunt on the non-BFF fleet

**Date:** 2026-06-15
**Depth/breadth step:** Round 16 found F12 (a 500) in the BFF. Round 22 applies
the same 500-hunt to the **other services** in-process — baseline reads, bad
query params, and malformed bodies — since they are less heavily exercised than
the BFF.

## Hypotheses

- H1: no service endpoint returns a 500 on a baseline GET, a malformed query
  param, or a malformed body (`[]`, `null`, invalid JSON, wrong-typed object).

## Method

1. Import each FastAPI service in an isolated subprocess **under its real module
   name** (so Pydantic forward-refs resolve) and drive it via `TestClient`.
2. Iterate the route table; for each GET, fuzz query params; for each
   POST/PUT/PATCH, send malformed bodies.
3. Flag only `status == 500` (config-gated 503 = graceful; excluded). Skip
   `/openapi.json` and `/docs` (their schema-gen is a harness artifact under an
   in-test import).

## Pass criteria

- H1: zero 500s across the importable services; any genuine 500 fixed via the
  dev workflow.
