# Round 14 — Results

**Executed:** 2026-06-15 (UTC).

## H1 — intra-instance concurrency: PASS

Fired **20 concurrent** identical creates with the same `Idempotency-Key`
against `/bff/evolution-programs` (in-process async). Result: 20×201, **1
distinct** `program_id`. No TOCTOU double-create: the BFF runs a single uvicorn
worker (`Dockerfile` CMD has no `--workers`), and the idempotency
check→create→store critical section has no awaited yield point, so the event
loop serializes concurrent same-key requests. Locked by
`test_idempotency_concurrency_guard.py` (1 passed).

## H2 — idempotency store durability/scope

Two tiers exist:

- **Durable** — `IdempotencyRecord.reserve()` (foundation-backed, `main.py:1319`)
  for final-contract commands. Survives restart and is shared across instances.
- **In-memory per-process** — module-level dicts
  (`_EVOL_EXP_BFF_IDEMPOTENCY`, `_CAPITAL_BFF_IDEMPOTENCY`,
  `_STRATEGY_*_BFF_IDEMPOTENCY`, `_AGORA_CORE_BFF_IDEMPOTENCY`,
  `_MCP_IMPORT_IDEMPOTENCY`, `_MGMT_NL_IDEMPOTENCY`, …) for facade creates.

### F11 — facade-create idempotency is per-process (LOW–MED; documented, not migrated)

The in-memory dicts are local to one BFF process. Today this is correct
(single worker, single dev instance). But the guarantee **does not survive a
restart** and **is not shared across BFF instances** — under the documented HA
posture (`BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, multiple instances behind the
load balancer) the same Idempotency-Key landing on different instances would not
be deduplicated, allowing a double-create on retry. The codebase already
acknowledges per-process storage limits elsewhere
(`assistant/transcript_store.py`: "not shared across BFF workers").

**Decision: documented, not migrated.** Critical capital/deployment commands use
the durable foundation-backed record; the facade creates (evolution programs,
tools, strategy seeds, agora core, mgmt-nl) use in-memory. Migrating them to the
durable store is a large, cross-endpoint change and may be an intentional tier
(non-capital facade creates). Recommendation for the BFF owner: before enabling
multi-instance HA, move facade-create idempotency to the durable
`IdempotencyRecord` store, or pin idempotency-bearing facade routes to a single
instance.

## Net

H1 **PASS** (no intra-instance race; guarded by a new test). H2 surfaced F11 —
a real durability/HA limitation of the in-memory facade idempotency, documented
with severity and a pre-HA recommendation rather than a risky mass migration.
