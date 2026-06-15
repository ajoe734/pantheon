# Round 14 — Concurrency & idempotency durability

**Date:** 2026-06-15
**Depth/breadth step:** Round 13 verified *sequential* idempotency. Round 14
goes deeper: the **concurrent** replay race (TOCTOU between the idempotency
check and the store) and the **durability/scope** of the idempotency store
(does the guarantee survive restart and hold across BFF instances?).

## Hypotheses

- H1: N concurrent requests with the same Idempotency-Key create exactly one
  resource (no double-create race within an instance).
- H2: the idempotency store backing each command class is appropriate to its
  criticality (durable/shared for capital-affecting commands).

## Method

1. Inspect the idempotency stores and the BFF launch configuration (worker
   count).
2. Live async race: fire 20 concurrent identical same-key creates against the
   in-process app; count distinct resource ids.
3. Classify each idempotency store as durable vs in-memory per-process.

## Pass criteria

- H1: exactly one resource from concurrent same-key creates.
- H2: critical commands use durable idempotency; any gap where a correctness
  guarantee depends on per-process memory under a multi-instance posture is
  documented with severity and recommendation.
