# Round 13 — Idempotency-Key replay correctness

**Date:** 2026-06-15
**Depth/breadth step:** Write-safety semantics. Many command routes require an
`Idempotency-Key`; this round verifies the **replay contract** — a retried
request must not double-create, and a key reused with a different payload must
be rejected.

## Why this round (not a duplicate)

Round 4 checked input robustness, not idempotency semantics. This round verifies
the actual replay/conflict behavior end-to-end.

## Hypotheses

- H1: `Idempotency-Key` is required (no key → 400, no keyless collision).
- H2: replay (same key + same body) returns the **identical** prior result, not
  a new resource.
- H3: same key + different body → **409 IDEMPOTENCY_CONFLICT**.
- H4: a new key → a new resource.

## Method

1. Read `_resolve_final_idempotency_key` and `_evol_exp_bff_idempotency_check`.
2. In-process (mutation-safe) sequence against `/bff/evolution-programs`:
   create, replay, conflict, new-key.
3. Survey existing test coverage of idempotency conflict.

## Pass criteria

- H1–H4 hold; gaps fixed via dev workflow, otherwise confirm existing coverage.
