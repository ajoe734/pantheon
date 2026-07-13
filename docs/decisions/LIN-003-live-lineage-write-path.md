# LIN-003 Live Lineage Write Path

Status: draft-canonical
Last updated: 2026-07-13
Source of truth inputs:
- `docs/decisions/LIN-002-lineage-ownership.md`
- EVOCHAIN-001 round-2/round-3 reviews (PR #3509)
Tier: L1 Platform Architecture & Policy
Scope: telemetry-side live lineage write path for the deployed
`CanonicalReferenceValidator` incident-resolution gap
Conflict rule: this decision governs the live lineage write path until
superseded by a newer explicit lineage decision; it operates inside the
read-ownership boundary already set by `LIN-002-lineage-ownership.md`

## Decision

`services/telemetry`'s lineage read service (`LineageReadService`, LIN-002)
now also accepts live writes: every telemetry event that
`TelemetryIngestService.ingest()` accepts is admitted into the same
in-memory lineage graph the deployed lineage-read HTTP routes serve,
along with a thin `runtime_binding` node built from the already-resolved
authoritative `RuntimeBinding` record when that binding is not yet a graph
node.

## Root cause (EVOCHAIN-001 round-3, blocker #1)

`services/telemetry/main.py` built exactly one `LineageReadService` at
process startup, bootstrapped once from the static LIN-001A benchmark
corpus (`_build_lineage_service()`). Telemetry ingest
(`TelemetryIngestService.ingest()`) validated and buffered accepted events
but never wrote them into that graph. Compose wires
`services/incident`'s `CanonicalReferenceValidator` at the default
`PANTHEON_TELEMETRY_URL`, so every incident evidence check resolves
telemetry/runtime-binding references through telemetry's lineage HTTP
routes (`/api/telemetry/lineage/...`). Because the graph never grew past
its startup snapshot, a freshly-accepted telemetry event or a freshly-live
`RuntimeBinding` outside the static corpus always 404'd
(`node_not_found`), which `CanonicalReferenceValidator` turns into a 422 —
even though the event was legitimately admitted moments earlier. This
blocked every deployed producer that POSTs an incident referencing live
telemetry, including EVOCHAIN-001's threshold-breach producer.

## What LIN-003 delivers

1. `LineageReadService.admit_telemetry_event(event, binding)`
   (`services/telemetry/lineage_read/service.py`) — incrementally adds the
   accepted event as a `telemetry_event` node plus its standard edges
   (`runtime_binding`, `deployment_plan`, `capital_pool`,
   `persona_capital_binding` — mirroring what `CorpusLoader` already does
   for the static corpus), and, only when missing, a thin `runtime_binding`
   node from the resolved binding record. Idempotent by node key; guarded by
   an `RLock` shared with `query()` since the write path runs on the ingest
   asyncio thread while reads run on Flask request threads against the same
   graph.
2. `TelemetryIngestService` gains a `lineage_write_store` constructor
   parameter (`services/telemetry/ingest_svc.py`); on every accepted event it
   calls `admit_telemetry_event(event, resolved_binding)`, reusing the
   `RuntimeBinding` record already resolved during evidence-contract
   validation (E-1 cross-check) rather than issuing a second
   runtime-manager lookup. Admission failures are logged and never fail the
   ingest call — lineage write is best-effort, not on the ingest
   critical path.
3. `services/telemetry/main.py` builds the `LineageReadService` before the
   ingest service at `startup()` and passes it in as `lineage_write_store`,
   so the process serving `/api/telemetry/lineage/*` and the process
   accepting `/api/telemetry/ingest` share one live graph instance.

### Why this closes the validator gap without a separate corpus reload

`CanonicalReferenceValidator.validate_incident()` only needs two lineage
queries to resolve a live event: `telemetry_event_trace(event_id)` and,
when `lineage_ref` is set, `runtime_binding_projection(binding_id)`. Both
build their `refs` bucket (`runtime_binding_ids`, `deployment_plan_ids`,
`capital_pool_ids`, `persona_capital_binding_ids`, `artifact_refs`) by
reading the *target node's own data fields* first
(`_merge_refs_from_node` in `lineage_read/service.py`), not by requiring
every referenced ancestor node to independently exist in the graph.
Telemetry events and RuntimeBinding records already carry every FK field
the validator checks. So admitting just the `telemetry_event` node (and,
for `lineage_ref` checks, the `runtime_binding` node) is sufficient —
`deployment_plan` / `capital_pool` / `persona_capital_binding` nodes do not
need to exist as first-class graph nodes for these two query families to
resolve correctly.

## Explicitly out of scope (follow-on work)

This task intentionally does not close the full lineage write surface —
matching the task brief's note that the gap "needs live writes from
telemetry ..., runtime-manager, and control-plane/governance — not
something a single producer task can close":

1. **runtime-manager**: still only a *read* dependency for telemetry
   (`PANTHEON_RUNTIME_MANAGER_URL` HTTP lookup at ingest time, and the
   `RuntimeManagerClient` used by `services/incident/reference_validation.py`
   for the direct binding-identity check). runtime-manager does not itself
   push RuntimeBinding creation/retirement into any lineage graph; telemetry
   pulls what it needs opportunistically per event. A durable, replicated
   RuntimeBinding lineage write (so the binding is resolvable even before
   the first telemetry event referencing it lands) is follow-on work.
2. **control-plane/governance**: `deployment_plan`,
   `persona_capital_binding`, `capital_pool`, and `approval_decision` nodes
   are still only populated from the static LIN-001A corpus. Live
   `capital_pool_projection` / `forensic_plan_trace` queries for pools or
   plans created after corpus load still 404
   (`traverse_from` requires the *starting* node to exist). This is fine for
   `CanonicalReferenceValidator` today (see above) but leaves the
   pool/plan-centric lineage read routes stale for anything not in the
   corpus. Governance/control-plane emitting live writes for these node
   types is a separate task.
3. **Durability and horizontal scale**: the live graph is in-memory only. A
   telemetry process restart drops all live-admitted lineage until the
   corresponding events are re-ingested; multiple telemetry replicas each
   hold their own graph, so an event admitted on one replica is invisible
   to a lineage read hitting a different replica. The current compose
   topology runs a single telemetry instance, so this is a known and
   accepted limitation, not a regression — but it must be resolved (e.g. a
   shared/durable lineage store) before telemetry is horizontally scaled.

## Exit evidence

- `services/telemetry/lineage_read/test_service.py`: unit coverage for
  `admit_telemetry_event` — new node + edges, idempotent replay, thin
  runtime_binding admission when missing, corpus-loaded binding left
  untouched.
- `services/telemetry/test_lineage_write_path.py`: integration coverage
  proving the default-wiring contract end to end, in two layers:
  - `TestLiveLineageWritePathDefaultWiring`: `TelemetryIngestService` wired
    with a real `LineageReadService` (no fakes) admits an event through
    `ingest()`, and the same `LineageReadService.query()` used by the
    deployed HTTP routes then resolves `telemetry_event_trace` and
    `runtime_binding_projection` for that event/binding; a subsequent
    `CanonicalReferenceValidator.validate_incident()` call — against a small
    adapter that forwards to the live `LineageReadService.query()`, not a
    hand-authored fixture — succeeds for an `IncidentCase` referencing data
    that was never in the static corpus. A control test confirms the same
    scenario 404s (`node_not_found`) without `lineage_write_store` wired,
    proving the fix isn't passing vacuously.
  - `TestLiveLineageWritePathFullStackHTTPRoute`: the literal
    "telemetry ingest 202 -> incident 201 -> same replay 200" acceptance
    form — a real `RuntimeManagerClient`-backed `RuntimeManagerService`
    deploy, real telemetry ingest, and the real
    `services.incidents.main` FastAPI app's
    `POST /api/incidents/consume-threshold` route (module-level
    `CanonicalReferenceValidator` swapped for one wired to this test's live
    runtime-manager client and lineage service, no other lookups replaced)
    return 201 on first delivery and 200 on idempotent replay.

## Consequences

1. EVOCHAIN-001 (and any other deployed incident producer) can now retest
   its default-wiring acceptance criterion against a telemetry instance
   that carries this change; the producer-side task itself is unaffected
   and out of scope here.
2. EVOCHAIN-010 (producer-chain live verifier) can rely on telemetry
   resolving live-ingested events without a corpus reload, but should still
   expect `capital_pool_projection` / `forensic_plan_trace` to stay
   corpus-bound until the control-plane/governance follow-on lands.
