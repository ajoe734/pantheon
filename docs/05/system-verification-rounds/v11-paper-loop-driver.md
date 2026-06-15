# V11 — Paper Loop Driver (A+B+C): closing the loop on the live fleet

**Round:** V11 (follow-on to the V10 capstone open thread)
**Date:** 2026-06-15
**Branch / PR:** task/paper-loop-driver
**Goal:** the V10 capstone showed loops are *provisioned but not proven live*. This
round builds an autonomous signal producer (C), a loop-run projector (B), and runs
a live end-to-end demonstration on the deployed 16-binding paper fleet (A).

## Non-duplication

The five P0 loop/telemetry briefs (`p0_loop_001`, `p0_tel_001`, `p0_tel_proj_001`,
`tel_002_rb`, `ep5_005_v2`) are all `review_approved`/done and cover telemetry
emitter/ingest/projection-into-runtime-status + EP5 proof packet. **None** owns an
autonomous signal producer or a loop-run ledger projection. No overlap.

## C — autonomous signal producer (`scripts/paper_signal_producer.py`)

Emits schema-valid signals (services/research/schema.json v1) onto each active
binding's Redis queue. Two non-obvious facts discovered live and encoded here:

1. **Per-binding queue key.** Workers do NOT read the shared
   `pantheon:signals:pending`; once a runtime resolves its binding it reads
   `pantheon:signals:pending:<binding_id>`. Pushing to the shared key is silently
   ignored (the runtime's own store reported `queue_depth: 0` while the shared key
   held 24). The producer fans out per-binding.
2. **Symbol format.** Symbols must be LEAN `TICKER.MARKET_CODE` (e.g. `AAPL.US`);
   bare `AAPL` fails the runtime symbol parser ("no dot separator") — the signal is
   consumed but execution fails.

## A — live demonstration on the deployed fleet

Fanned out 32 signals (2 per binding × 16) to the per-binding Redis keys, then
observed one poll cycle (workers poll every 30s):

```
pushed 32 signals across 16 key(s)
queue drained 32 -> ~0
MarketOrders emitted: 15 distinct workers each executed a paper MarketOrder
telemetry: repeated POST /api/telemetry/ingest -> 202 (fills accepted) from worker IPs
```

So the left→execute→telemetry path is **live and driven**: a producer tick makes
15/16 paper bindings actually trade and emit accepted telemetry. This is the
substantive proof of loop liveness the V10 capstone was missing — the loops run
end-to-end when fed.

## B — loop-run projector + the v5 ledger reality (`scripts/paper_loop_run_projector.py`)

The projector derives one loop-run record per active binding from `/bff/runtimes`
and writes the dataset bound to `PANTHEON_BFF_LOOP_RUN_STORE`. It works (16 records
produced). **However**, the v5 loop-run *surface* did not light up, and the reason
is a deliberate design fact worth recording:

`read_store.list_loop_runs()` **derives loop-runs from the incidents service first**
(`_derive_loop_run` over non-sentinel incidents); the dedicated
`PANTHEON_BFF_LOOP_RUN_STORE` is used **only as a fallback when incidents are
unavailable**. This precedence is enforced by contract tests
(`test_list_loop_runs_derivation_from_incidents`,
`test_v5_loop_runs_list_contains_seeded_incident_derived_record`). In the live
deployment the incidents service is up and returns zero non-sentinel records, so
the ledger reads empty and shadows the dedicated store.

**Conclusion:** in this read model, a "loop run" visible in the v5 ledger *is* an
incident-derived record. Surfacing real paper executions there is a product/design
decision — emit loop executions as incident-shaped events into the incidents
service — NOT a quick wiring change, and deliberately **not** done here because
injecting synthetic "incidents" for healthy loops would pollute the incidents and
alerting surfaces. The projector and the `PANTHEON_BFF_LOOP_RUN_STORE` fallback
remain available for when that data path is built.

Deployment hygiene: the live wiring used to test B (a hand-added compose env + a
docker-cp'd store file on the dev VM) was **reverted** after the test so the
deployed stack carries no manual drift (verified: BFF env has no LOOP_RUN_STORE,
`/bff/persona-league/{id}` -> 404, auth stub intact).

## Net result vs the V10 open thread

- Loops are now **demonstrably live** via real execution + accepted telemetry (A),
  driven by an autonomous producer (C) — the founding-question proof.
- The v5 loop-run *ledger surface* remains incidents-derived by design; making it
  reflect executions is a scoped read-model follow-up (B documents the exact seam).
