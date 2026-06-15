# Round 13 — Results

**Executed:** 2026-06-15 (UTC). **Method:** code read + in-process TestClient
sequence against `/bff/evolution-programs`.

## Evidence

| Case | Result | Verdict |
|---|---|---|
| no Idempotency-Key | 400 `VALIDATION_FAILED` (key required) | H1 PASS |
| create (key K1, body A) | 201, program `df2f2a01…` | — |
| replay (K1, A) | 201, **identical body** (same id/timestamps) | H2 PASS |
| same key, different body (K1, B) | **409 `IDEMPOTENCY_CONFLICT`** | H3 PASS |
| new key (K2, A) | 201, **distinct id** | H4 PASS |

`_resolve_final_idempotency_key` requires a non-empty key (400 otherwise), so
there is no keyless-collision under `""`. `_evol_exp_bff_idempotency_check`
returns the cached result on a hash match and raises 409 on a hash mismatch —
correct RFC-style idempotency.

## Existing coverage

Idempotency conflict (409) is already covered broadly, including a dedicated
`services/control-plane/bff/tests/test_command_replay_conflict.py` plus ~13 other
contract tests. No new test is warranted — behavior is correct and locked.

## Net

H1–H4 **PASS**. Sequential idempotency replay/conflict semantics are correct and
already well-tested. The **concurrent** replay race (two simultaneous same-key
requests — TOCTOU between the idempotency check and the store) is deferred to
Round 14 (concurrency).
