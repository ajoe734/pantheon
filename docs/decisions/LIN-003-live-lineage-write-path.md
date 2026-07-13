# LIN-003 Live Lineage Write Path

Status: proposed, blocking dependency for EVOCHAIN-001 and every future
telemetry-derived incident producer
Last updated: 2026-07-13
Source of truth inputs:
- `docs/decisions/LIN-002-lineage-ownership.md`
- `docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/EVOCHAIN-001-threshold-breach-producer.md`
  (round-1/round-2/round-3 review threads, PR #3509)
Tier: L1 Platform Architecture & Policy (dependency of L2 execution tasks)
Scope: making a real, live-deployed telemetry event and its upstream
deployment lineage resolvable through `CanonicalReferenceValidator`'s
default (unmocked) lookup path

## Problem

`services/incidents/main.py`'s module-level `reference_validator =
CanonicalReferenceValidator()` resolves telemetry/lineage references through
`_TelemetryLineageLookup` (`services/incident/reference_validation.py`),
which — in the deployed default (compose sets `PANTHEON_TELEMETRY_URL`) —
calls telemetry's `/api/telemetry/lineage/*` routes. Those routes are served
by a `LineageReadService` built once at telemetry process `startup()`
(`services/telemetry/main.py::_build_lineage_service()`) from the static
LIN-001A benchmark corpus (`services/registry/lineage/
lin001a_benchmark_corpus.json`) — a fixed set of 4 demo `runtime_bindings`,
2 `capital_pools`, 3 `persona_capital_bindings`, and 8 `telemetry_events`.

Two independent gaps compound into a permanent 422 for any producer that
posts an incident referencing a genuinely running (not static-benchmark)
paper/canary/live binding:

1. **No live write path for telemetry events.** Nothing in
   `TelemetryIngestService.ingest()` (`services/telemetry/ingest_svc.py`)
   adds a node/edge to the lineage graph for a freshly-ingested event. A
   real, just-admitted event can never resolve via
   `telemetry_event_trace`, no matter how long a caller polls.
2. **No live write path for the deployment lineage chain.** The static
   corpus's 4 `runtime_bindings` are fixed demo IDs
   (`rb-alpha-canary-001`, `rb-alpha-live-001`, `rb-beta-live-001`,
   `rb-beta-canary-rollback-001`) — none `paper` stage, none matching any
   real dev-deployed persona (e.g. the `evo-vslice-1` seed). Even if gap 1
   were closed, a telemetry event's edges would point at a
   `runtime_binding`/`deployment_plan`/`capital_pool`/`persona_binding`
   node that was never registered, because nothing in the real deploy flow
   (`services/runtime-manager`, `services/control-plane/governance`)
   writes those nodes into telemetry's lineage graph either.

This is confirmed structural (not a timing/race condition): no amount of
polling after `POST /api/telemetry/ingest` or after a real
`RuntimeManagerService.deploy()` call would ever make either resolve, given
today's code. It was independently reproduced across three review rounds
of EVOCHAIN-001 (PR #3509) and is shared by every current and future
telemetry-derived incident producer (drift reports, other threshold
producers), not specific to that task.

## Why this is not a single-task fix

Closing this for real requires:

- `services/telemetry/lineage_read/service.py`: an incremental
  `register_*` mutation API on `LineageReadService`/`LineageGraph` (the
  graph's `add_node`/`add_edge` primitives already support this cheaply;
  the corpus loader's per-node-type blocks are the template), with a lock
  around mutation since ingest runs on a background loop while lineage-read
  HTTP routes run on request threads.
- `services/telemetry/ingest_svc.py` + `main.py`: wiring `ingest()` to call
  that registration API (mirroring the existing optional
  `runtime_summary_store`/`trade_episode_projection_store` post-accept
  projection pattern) — for both telemetry events and, notably, the
  runtime-binding/deployment-plan/capital-pool/persona-binding identity a
  telemetry event carries, the first time that binding is observed.
- Very likely `services/runtime-manager/service.py` (`deploy()`) and/or
  `services/control-plane/governance`: a live source for the upstream
  deployment-lineage chain (deployment_plan -> approval_decision ->
  candidate_artifact -> experiment_run -> ...), since a telemetry-observed
  binding_id alone does not carry its full upstream research/governance
  chain — only the deploy flow that created the binding does.
- A decision on `services/incident/reference_validation.py`'s local-corpus
  mode (`_TelemetryLineageLookup._query_local`): it builds its own
  independent `LineageReadService` straight from the static file on first
  use, structurally disconnected from telemetry's live in-process graph
  even if telemetry's own HTTP-mode path is fixed. Whether that mode is
  deprecated, or also wired to a shared live index, is an explicit call this
  decision needs before implementation.

Each of the above is a shared, independently-reviewable subsystem change
touching multiple services (`telemetry`, `runtime-manager`,
`control-plane/governance`, `incident`) — per `docs/decisions/
LIN-002-lineage-ownership.md`'s "Phase 0: transitional coexistence" framing,
this is exactly the kind of cross-cutting lineage migration work that
decision anticipated as a separate initiative, not something a single
producer task (`services/evolution`, `services/incidents`,
`docker-compose.yml`) should special-case or absorb.

## Exit evidence required

A real default-wiring test with no fake/injected lookups:

```text
POST /api/telemetry/ingest (real event)        -> 202
POST /api/incidents/consume-threshold           -> 201 (real IncidentCase)
same payload replayed                           -> 200 (deduped, same incident)
```

exercised against the *default* (unmocked) `CanonicalReferenceValidator()`
and a *live-deployed* (not corpus-fixture) `runtime_binding` — i.e. either a
live running telemetry+runtime-manager stack (matching EVOCHAIN-010's
"producer-chain live verifier" scope) or an equivalent in-process
integration test that seeds a real `RuntimeBindingStore`/`deploy()` call and
a real bound telemetry HTTP server, with no hand-rolled fake lookup classes.

## Consequences until this lands

- Every telemetry-derived incident producer (EVOCHAIN-001 included) can
  only be proven reference-shape-consistent with `CanonicalReferenceValidator`
  using injected fakes shaped like what the canonical stores would return
  once wired — not against the literal default validator instance running
  in compose.
- `test_consume_threshold_route_422s_against_default_deployed_reference_validator`
  (`services/evolution/test_threshold_sweep_worker.py`) pins today's actual
  422 behavior so this gap closing is a visible, deliberate change to that
  test, not a silent regression.
- EVOCHAIN-001 remains blocked on this task for its
  "breach POSTs canonical payload accepted by `ThresholdTelemetryIncidentConsumer`
  and creates an `IncidentCase`" acceptance criterion against the *default*
  deployed path; EVOCHAIN-010 (producer-chain live verifier) depends on both
  EVOCHAIN-001 and this task landing first.
