# Round 3 — Parameterized-route robustness (no unhandled 500s)

**Date:** 2026-06-14
**Depth/breadth step:** Round 2 swept the 196 parameter-free GET paths. Round 3
covers the **other half** — the 139 parameterized (`{id}`) GET routes — and
goes **deeper** on robustness: a published route must degrade gracefully
(clean 4xx) for unknown ids, never throw an unhandled exception (500). A 500 on
a not-found id signals a missing null-guard or a bug in the error path itself.

## Why this round (not a duplicate)

No prior doc exercises the error/negative path of the parameterized contract.
The BFF gap audits checked existence; Round 2 checked liveness of static GETs.
Round 3 is the first to assert **error-path correctness** across the `{id}`
surface — the place latent server errors hide because the happy path looks fine.

## Hypotheses

- H1: every parameterized GET route returns a well-formed 4xx (typically 404)
  for a syntactically-valid but non-existent id — never a 500.
- H2 (class generalization): no BFF handler references an ErrorCode member that
  does not exist (such a reference 500s whenever its branch is hit).

## Method

1. Enumerate parameterized GET paths (139) from live OpenAPI.
2. Substitute each `{param}` with a benign non-existent id; probe with stub
   auth; classify status codes.
3. For any 5xx, reproduce locally via TestClient to capture the traceback and
   root-cause it.
4. Generalize: statically scan `main.py` for `ErrorCode.<NAME>` references and
   validate every NAME against the live `ErrorCode` enum.

## Pass criteria

- H1: zero 5xx across the parameterized GET surface, OR every 5xx root-caused
  and fixed via the dev workflow.
- H2: zero invalid ErrorCode references, locked by a static regression test.
