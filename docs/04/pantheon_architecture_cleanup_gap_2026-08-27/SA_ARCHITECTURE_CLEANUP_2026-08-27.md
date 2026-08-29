# System Analysis — Pantheon Architecture Cleanup

Status: implementation-ready analysis; no execution task has been materialized

Baseline and disposition definitions are in [`INDEX.md`](INDEX.md). Current evidence
is in [`CURRENT_GAP_2026-08-27.md`](CURRENT_GAP_2026-08-27.md).

## 1. Objective

Reduce Pantheon to one operational owner for every route, state transition, read
model, and runtime command while preserving the current product contract. This is a
behavior-preserving cleanup except where the current behavior is proven to be a
shadow, empty stub, or false-success path.

The work is successful when a maintainer can answer all four questions from the
code graph alone:

1. Where is this route registered?
2. Which service/store owns the record?
3. Which frontend client calls it?
4. Which executable and exact source identity run it?

## 2. Architecture rules

### 2.1 One owner, many consumers

A compatibility name may delegate to a canonical owner, but it may not own another
store, another route registration with the same shape, or another state machine.
Aliases live next to their canonical route and call the same typed application
operation.

### 2.2 Dependency direction

Backend request flow:

```text
FastAPI app composition
  -> domain router
    -> domain application operation
      -> typed service client or canonical repository
        -> owning product service/store
```

Frontend request flow:

```text
route/page
  -> domain hook/controller
    -> domain API client
      -> shared transport/auth/error normalization
        -> Pantheon BFF
```

Forbidden inverse edges are:

- a domain module importing BFF `main.py`;
- a store importing a router for business behavior;
- one frontend API-version directory importing another in both directions;
- shared transport importing a domain client;
- a product BFF route writing state owned by another service; and
- a test importing a retired implementation instead of the deployed owner.

### 2.3 Truth and metadata are different

Static catalog data defines identity and expected ownership. Runtime state comes
from the owning controller/store. A projection may join those two, but a catalog,
fixture, cached file, task record, or generic downstream probe cannot fill in a
missing runtime owner record.

### 2.4 Move and delete are one delivery unit

Each caller migration has four parts:

1. freeze the observed contract with a characterization test;
2. point the caller at the canonical owner;
3. prove the same or deliberately corrected behavior; and
4. delete the old route, method, overlay, import, or file in the same delivery wave.

Leaving the old implementation for a later unspecified cleanup is not accepted.

## 3. Target ownership map

| Capability | Sole canonical owner | Consumers |
|---|---|---|
| BFF process/app lifecycle | small BFF app factory | container entrypoint and tests |
| route registration | domain router package | app factory |
| authentication/session parsing | BFF platform/auth module | routers through injected dependency |
| domain mutation | owning product service/store | BFF application adapter |
| BFF-owned projection | explicitly named projection repository | its domain router only |
| frontend transport | existing `bff-v1` transport/status/SSE modules | all domain clients |
| frontend contract adaptation | existing `bff-v1` domain client for that contract | hooks/pages |
| loop identity/expected contract | loop catalog | Management projection |
| loop current state | `LoopControllerStore` | Management projection |
| component/downstream diagnostics | `DownstreamHealthMonitor` | downstream-health UI/API only |
| Runtime Binding mutation | `RuntimeManagerService` | Runtime Manager HTTP routes and clients |
| Runtime Binding schema/kernel | importable execution runtime-control package | Runtime Manager service and workers |
| paper fleet reconciliation | explicitly named paper fleet worker package | Compose/deployment |
| Workshop persistence | `PostgresWorkshopStore` | Workshop application operations |
| Workshop event publication | public Workshop stream/event module | Workshop, Research, Interaction |
| Source ingest orchestration | one Source Ingestion application service | Source routers and controller worker |
| source health/proposals/Management commands | existing focused source modules | corresponding Source routers |
| exact deployment truth | deployment controller plus hosted manifest/receipt | release acceptance |

## 4. BFF target composition

### 4.1 App factory

The retained entrypoint should contain only:

- settings and dependency construction;
- FastAPI application creation;
- common middleware and exception handlers;
- lifecycle start/stop hooks;
- router inclusion; and
- `/livez`, `/readyz`, and exact version endpoints where those are platform-level.

It must not contain domain record schemas, route bodies, response projection, SSE
domain buffers, or mutable compatibility overlays.

### 4.2 Routers

Routers own HTTP-only concerns: path/query/header parsing, authentication/role
dependency invocation, mapping domain errors to the existing BFF envelope, and
calling one application operation. A router does not select among local fixture,
file, HTTP, and database implementations.

### 4.3 Application operations

Where `main.py` currently contains legitimate multi-step behavior, extract one
domain operation rather than putting the logic into a generic service. Examples are
submit Evolution action, create ranked formula, and compose loop health.

### 4.4 Route manifest

The backend route manifest becomes generated evidence from the constructed app. It
must record method, normalized path shape, operation ID, owner module, and handler
qualname. CI rejects duplicate method/shape pairs before OpenAPI snapshot comparison.

## 5. Read-model target

### 5.1 No replacement God façade

There will be no global `ReadStoreFacade`, `RepositoryRegistry`, or string-dispatched
`get_dataset(name)` replacement. Route packages receive typed protocols with only
the methods they call.

### 5.2 Domain slices

The existing 457 methods are migrated into these ownership slices only when they
have real callers:

| Slice | Preferred implementation |
|---|---|
| Persona/training | Persona and Training service clients/projections |
| Consultation | existing Consultation client/store |
| Research/search/experiments | Research and Search service clients; one BFF projection only where required |
| Governance/deployment/evolution | respective service clients and command adapter |
| Runtime/capital/ranking | Runtime/Capital clients plus explicitly BFF-owned ranking projection |
| Lifecycle/telemetry/incidents | existing trade-journey, telemetry, incident, and postmortem readers |
| Agora | Agora package stores and application operations |
| Management | composition of the typed clients above; no new Management data owner |

### 5.3 Fixtures

Tests may build a fake implementation of a typed port or load an explicit fixture
factory. Product startup cannot seed examples, merge fixture packs, or silently
switch to local snapshots. A test that needs the legacy combined fixture must use a
test-only compatibility builder during migration; that builder is deleted when its
last test moves.

## 6. Frontend target

### 6.1 Directory semantics

Do not create a fourth API directory. The audited code already has the least-change
canonical owner:

- `src/lib/bff-v1` remains the public production network/auth/SSE/read/write/path and
  receipt surface;
- its existing domain modules become the implementation owners;
- package-internal code imports those modules directly instead of importing its own
  barrel;
- `src/lib/v5` contains only pure DTO, view-model and adapter behavior; and
- `src/lib/bff` is a migration source or explicitly isolated test/demo fixture, then
  is deleted when its live callers reach zero.

The dependency rule is fixed: UI may call the public BFF surface and pure models;
domain client modules depend on existing lower-level transport/runtime/type modules;
transport never imports a domain client; `v5` never performs network or mutation;
and package-internal modules never call one another through the public barrel.

### 6.2 Compatibility

If a current page needs camelCase adaptation or an older response envelope, that
mapping belongs inside its domain client. It is not exposed as `legacy.ts`, and it
does not become a second fetch layer. After all consumers use the canonical return
type, delete the mapping.

### 6.3 Workshop UI

The existing route remains the URL identity owner and existing
`bff-v1/agora/workshops.ts` remains the only Workshop network owner. Split the page
only into list and URL-selected session views plus optional pure helpers. Remove the
layout's second Workshop projection GET; do not add a global Workshop store or query
facade. The durable Workshop message/readback receipt, reconstruction receipt, and
optional Persona interaction receipt are separate outcomes.

## 7. Management loop truth target

The Management projection algorithm is deliberately small:

```text
catalog[12 stable loop identities]
  LEFT JOIN
current fenced controller record by (loop_id, tenant_id, environment)
  -> validate freshness, lease, deployment identity and evidence
  -> project one row per catalog loop
```

An absent or rejected record produces an explicit unobserved/degraded row. The
projection never consults BFF snapshot files or mutates a row from downstream monitor
state. The downstream monitor remains available as a separate drill-down source.

The canonical runtime array contains exactly twelve catalog loop IDs. The composite
overlay, if retained for inventory, is a separate noncanonical projection. A read
request never publishes a heartbeat. Frontend Management consumes
`runtime_maturity` and the accepted nested operator-truth packet emitted by this
projection; it does not ask the backend to restore retired static maturity fields or
a duplicate top-level truth flag.

Each loop owner uses `LoopControllerWriter` or the same conformance/store contract.
The architecture cleanup can migrate the path, but functional completion still
requires the nine currently undeclared/unimplemented owner controllers to exist and
publish current rows.

## 8. Runtime Manager target

`RuntimeManagerService` remains the only state machine. The HTTP module maps requests
to it; clients call the HTTP boundary where an integration test is intended. The
execution package contains importable Runtime Binding and kill-switch primitives,
not another manager.

The paper fleet reconciler becomes a clearly named worker package and image while
retaining its behavior and Runtime Binding input. Directory cleanup is complete when
no runtime/test import points at `services/execution/runtime-manager/runtime_manager.py`
and that file is deleted.

## 9. Workshop target

The Workshop package is divided by responsibility without adding another façade:

- `routes/session` — list/create/detail/messages/events/completeness/cards/readiness;
- `routes/versioning` — version list/create/select;
- `routes/execution` — reconstruct/research/consult/conclude;
- `events` — publish, subscribe, replay, durable replay adapter;
- `readiness` — assessment and card projection;
- existing `operations` / `runner` — canonical application behavior;
- `store/schema`, `store/memory`, and `store/postgres` — one shared protocol and one
  implementation per backend.

This is a physical split of the current owner, not a new service boundary. The
package router only includes its subrouters.

## 10. Source Ingestion target

Source Ingestion remains one deployable service. The composition root builds one
runtime object holding existing stores, scheduler, evidence writer, market writer,
proposal service, health stores, and command engine. Router factories receive that
runtime or narrower operations from it; they never import `main` globals.

The current ingest pipeline is consolidated into one application operation so job,
source-record, scheduled, frontier replay, and controller-triggered ingestion share
the same persistence/evidence/post-processing semantics.

No provider, scheduler, or Management route gains a duplicate store during the
split.

## 11. Deployment truth target

An accepted candidate has two related receipts:

1. cross-repository FE/BFF pair identity; and
2. backend required-component identity and runtime state.

For every required Compose service, the backend receipt records service name,
container/image identity, `org.opencontainers.image.revision`, command, running
state, health state where defined, and observation time. A required service missing,
restarting, unhealthy, or on the wrong revision blocks acceptance.

BFF-only delivery includes every BFF-image-owned persistent process, currently the
operator BFF and Agora interaction worker, plus the lifecycle projector whose
readiness is coupled to BFF. Rollback restores the same set.

## 12. Migration strategy and parallelism

### Wave 0 — characterization and guardrails

This wave changes no product ownership. It adds route-shape inventory, frontend
dependency checks, exact launcher subprocess smoke, required-service deployment
checks, and contract snapshots for collision groups. These tests must first
demonstrate the known failures and then become permanent guards.

### Wave 1 — canonical-owner preparation (parallel)

The following lanes can work concurrently because they prepare new code in separate
owners without switching central callers:

- BFF domain routers/application operations for collision groups;
- typed frontend transport and domain clients;
- loop-truth projector/router over catalog plus controller store;
- Runtime Manager semantic parity and E2E test adapter;
- Workshop event/readiness/store modules;
- Source Ingestion runtime/application operation and subrouters;
- dead UI caller verification; and
- container/deployment receipt verifier.

### Wave 2 — caller switch (bounded integration owners)

Central files have exclusive integration ownership:

| Hot file | Single integration owner responsibility |
|---|---|
| BFF `main.py` | remove/move route registrations and include prepared routers |
| `read_store.py` | delete migrated methods/fixtures; never accept parallel domain edits directly |
| frontend public API barrels | switch exports/imports after domain clients land |
| Workshop package router/store entry files | compose prepared modules and remove old bodies |
| Source Ingestion `main.py` | install app factory/subrouters and delete old bodies |
| deploy script/workflow | install the common exact-component gate once |

This avoids merge-conflict serialization across all domain work while preventing two
workers from editing the same monolith independently.

### Wave 3 — deletion

Deletion occurs as soon as the last caller switches:

- duplicate BFF registrations and overlays;
- migrated `ReadSurfaceStore` methods and test fixtures;
- version barrels and inverse frontend imports;
- local loop-health snapshot authority and monitor overlays;
- duplicate Runtime Manager and bootstrap store;
- old Workshop/Source bodies;
- zero-caller NL/stub UI; and
- broken launcher fallback.

### Wave 4 — integration acceptance

Run backend and frontend contract suites, container entrypoint smokes, current
twelve-loop suites, Management read/write journeys, Agora journey, cross-loop
identity test, exact deployment, restart/readback, and hosted UI checks. This wave
detects functional regression; it does not recreate deleted compatibility paths.

## 13. Rollback policy

Each wave must be independently revertible by source commit. Runtime dual-writing or
indefinite old-route retention is not a rollback mechanism.

- Before a caller switch, no active behavior changes.
- A switch and its deletion are one commit/PR-sized unit where practical.
- If acceptance fails, revert that unit and fix the prepared canonical owner.
- Database schema additions may remain if backward compatible, but no obsolete code
  is kept merely to use an unused table.
- Exact deployment rollback restores the prior verified component set and identities,
  not just the HTTP BFF container.

## 14. Analysis decisions that are still VERIFY

The implementation plan must collect evidence before final deletion for:

- exact frontend generated-type provenance and consumers;
- any external consumer depending on a duplicate route's OpenAPI operation ID or
  response envelope;
- snapshot datasets that have a legitimate bounded historical-read contract;
- unique semantics in the old Runtime Manager not yet covered by the deployed
  service;
- shared Management AI helpers reachable outside the dead NL UI;
- large watchlist modules outside priorities 1–9; and
- hosted component identities when a service intentionally runs a different source
  artifact than the backend candidate.

VERIFY is time-bounded evidence work. It is not permission to keep both mechanisms
indefinitely.
