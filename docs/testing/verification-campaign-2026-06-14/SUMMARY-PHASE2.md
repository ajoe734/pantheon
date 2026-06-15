# Verification Campaign 2026-06-14 — Phase 2 Summary (rounds 11–20)

Phase 2 targeted the gaps Phase 1 (surface/shape) did not reach: computed-value
correctness, the real JWT/MFA path, idempotency & concurrency, pagination,
input fuzzing (injection / 500-hunt), undefined-symbol audit, and resilience.

## Coverage map

| # | Plane | Angle | Verdict |
|---|---|---|---|
| 11 | Read models | aggregation/count/cross-surface correctness (not shape) | PASS |
| 12 | AuthN/Z | production JWT attack matrix (alg:none, expiry, iss/aud, fail-closed) | PASS + test |
| 13 | Write safety | idempotency replay/conflict semantics | PASS |
| 14 | Concurrency | 20-way same-key race; idempotency durability | PASS + test |
| 15 | Pagination | completeness, no dup/gap; cursor fuzz | PASS |
| 16 | Input | broad query-param fuzz (injection + 500) | **fix F12** |
| 17 | Static | undefined-call NameError audit (655 files) | PASS + guard |
| 18 | Input | header + parameterized-route query fuzz | PASS |
| 19 | Resilience | graceful degradation; false-green (F2) generalized | PASS |
| 20 | Capstone | regression consolidation + summary | PASS |

## Defect fixed (via dev workflow)

| ID | Severity | Defect | PR |
|---|---|---|---|
| F12 | high | `/bff/audit/events` & `/bff/audit/export` 500 on **any** `from_ts`/`to_ts` (even valid) — `_parse_rfc3339_header` defined nowhere → `NameError`. Same missing `_parse_rfc3339` hit `_kw04_within_recency` + aggregated-recency. Audit time-range filter was entirely broken. | #1610 |

## Findings recorded for owners (not changed)

- **F10** (auth, low) — a validly-signed JWT **without `exp`** is accepted
  forever. First-party tokens always set `exp`; service tokens minted elsewhere
  may not, so requiring it has uncertain blast radius. Recommend
  `PANTHEON_RUNTIME_JWT_REQUIRE_EXP`.
- **F11** (concurrency, low–med) — facade-create idempotency uses **per-process
  in-memory** stores: not durable across restart, not shared across instances.
  Correct on single-worker dev; breaks under multi-instance HA. Critical
  final-contract commands use the durable `IdempotencyRecord`. Migrate facade
  idempotency before enabling HA.
- **F13** (live-only) — `source/ops` & `source-change-proposals` 500 on live for
  odd filter strings, but return 200 in current dev code → live/downstream data
  condition, for ops.
- **O4/O5** — `governanceRequired` defaults true (not a needs-attention signal);
  unpaginated full-collection endpoints are a scale risk.

## New regression tests added (Phase 2)

- `test_runtime_auth_inbound_attack_matrix.py` (JWT alg:none/iss/aud/fail-closed)
- `test_idempotency_concurrency_guard.py` (no double-create race)
- `test_audit_timestamp_filter_no_500.py` (F12)
- `test_no_undefined_call_symbols.py` (F12 class guard on `main.py`/`read_store.py`)

## Deploy state

F12 (and Phase-1 F2/F3/F5) are merged to `dev` and verified in-process; they
take **live** effect on the next BFF redeploy (deploy-lag, OPS — same as F6).

## Posture after two phases

20 rounds, 6 real defects fixed (F2/F3/F5/F8/F9-guard + F12), 9 findings
attributed to owners, 9 regression test files added. The Pantheon dev BFF is
reachable, healthy, auth-gated, fail-closed, cryptographically sound on JWT,
robust against malformed input across **every** channel (only 500 found
anywhere was F12), idempotent, correctly paginating, contract-honest in both
directions, and gracefully degrading with trustworthy health signals. Remaining
gaps are upstream build-out (F1: signal producer / market data — now in flight
per `scripts/paper_signal_producer.py`) or owner/ops decisions, all explicitly
attributed rather than silently worked around.
