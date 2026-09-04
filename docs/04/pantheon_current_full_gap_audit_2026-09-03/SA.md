# System Architecture — Pantheon Structural Closure

Status: architecture proposal; implementation requires governed task delivery

Baseline: `ajoe734/pantheon` `origin/dev@675a488d78e8f991e2f1ecfc92e595b2d84625a1`

Source audit: [REPORT.md](REPORT.md)

## 1. Objective

Close the current Management, Agora, twelve-loop and delivery gaps by removing
competing authorities and completing the existing domain ownership model.

The target is not more endpoints or wrappers. It is one durable owner for each
business aggregate, one command path to that owner, one authoritative read
projection, and one exact-pair release authority. The work is complete only
when replaced code is deleted and a new correlated stimulus traverses all
twelve loops on one accepted hosted FE/BFF pair.

## 2. Root causes

The audit findings reduce to five structural defects:

1. **Composition leakage** — `main.py` still contains copied domain behavior,
   global mutable state and helpers imported by domain services.
2. **Contract without implementations** — `ReadSurfacePorts` looks unified but
   production wiring leaves five domain ports unavailable and mounted routes
   call methods the facade does not own.
3. **Multiple state authorities** — process-local overlays and duplicate
   journal/storage implementations coexist with durable domain owners.
4. **Evidence disconnected from execution** — loop registry entries, CI and
   historical manifests are sometimes closer to declarations than current
   runtime receipts.
5. **Release identity discontinuity** — FE, BFF, target environment, rollback
   baseline and hosted readback are not currently admitted as one immutable
   pair.

Fixing only `AttributeError`, adding forwarding methods, or making skipped
tests green with fixtures would preserve these causes and is explicitly
outside this architecture.

## 3. Scope

### 3.1 In scope

- Product BFF composition and domain ports.
- Management read models and Management AI product diagnostics.
- Agora workshop, research, journal, decision, performance and SSE paths.
- Canonical owner handoffs across all twelve product loops.
- Durable persistence and process-local overlay retirement.
- Exact FE/BFF release admission, rollback baseline and hosted verification.
- Dead-code deletion, copied-body elimination and architecture enforcement.
- Separate `execute-plans` changes required for strict-live production paths.

### 3.2 Out of scope

- A new generic repository, service locator, facade, event bus or workflow
  engine.
- Replacing domain stores with a new monolithic BFF database.
- Product routes for development tasks, worktrees, supervisor control or code
  repair.
- New schedulers for queues already owned by an existing worker.
- Production/live-capital enablement.
- Treating historical evidence as current hosted acceptance.
- Keeping duplicate implementations indefinitely behind feature flags.

## 4. Architecture invariants

1. Every mutable aggregate has exactly one canonical writer.
2. BFF routers authenticate, validate and translate; they do not own business
   persistence.
3. A domain command reaches the domain application service directly through a
   typed port; it never mutates a read projection.
4. Read projections are derived and rebuildable. They never become command
   authorities.
5. Process memory may cache immutable/derived data only. It may not acknowledge
   a successful product mutation.
6. `main.py` is a composition root: settings, dependency construction, router
   mounting, lifecycle and no domain behavior.
7. Domain modules never import `main.py`.
8. A compatibility adapter has one caller, an expiry condition and no state.
9. One semantic responsibility has one implementation. Extraction moves
   callers and deletes the old body in the same wave.
10. Loop completion requires terminal output plus next-consumer receipt and
    fresh durable readback for the same correlation chain.
11. Management displays owner observations with provenance and freshness; it
    does not infer healthy from catalog declarations.
12. Development tooling, product runtime and delivery infrastructure exchange
    evidence but never share authority.
13. FE/BFF release identity is an immutable pair; branch heads are candidates,
    not hosted truth.
14. Safe-write defaults remain closed in dev/staging unless the operator
    explicitly authorizes a bounded test.

## 5. Target architecture

```text
execute-plans (strict-live FE)
          |
          v
operator BFF: auth + DTO + typed domain command/query adapters
          |
          +---------------- query ----------------+
          |                                       |
          v                                       v
domain application owner                    Management projection
          |                                  (read-only, provenance)
          v                                       ^
canonical domain store -> outbox -> owning worker |
          |                         |              |
          +---- fresh readback -----+-- receipts --+

Exact-pair delivery authority
  build identities -> admission -> baseline -> switch -> hosted readback
```

The arrows are dependencies. No new always-on service is introduced by this
design.

## 6. Canonical ownership map

| Concern | Canonical owner | BFF responsibility | Retire/forbid |
|---|---|---|---|
| Persona lifecycle | Persona registry/application service | typed command/query adapter | both Persona overlays and copied lifecycle bodies |
| Strategy state | Strategy domain owner | DTO/query and command adapter | `_STRATEGY_BFF_OVERLAY` |
| Runtime binding | Runtime Manager | command admission and projection | BFF-local binding mutation |
| Deployment plan | Deployment service/store/outbox | approve/submit/query | `service.read_store.create_deployment_plan` |
| Experiments | Research orchestrator/store | submit/query artifacts/logs/metrics | generic BFF experiment mutation |
| Jobs | owning job service/store | paginated query/log stream | `_GOV_BFF_JOB_OVERLAY` |
| Incidents | reconciliation/incident owner | query/action adapter | `_GOV_BFF_INCIDENT_OVERLAY` |
| Ranking | ranking domain store | query/evaluate | local `_ranking_snapshots`; deprecated formula bodies |
| Agora journal | one selected durable Journal owner | Agora DTO/API adapter | the unselected journal implementation |
| Agora interaction | interaction store/outbox/worker | request/status/SSE | synchronous fake terminal results |
| Agora research | research owner plus real receipt resolver | plan/run/query | caller-provided unverified `real` provenance |
| Agora suggestions | performance/evaluation consumer | query/display | manual-only or orphan producer path |
| Loop truth | owner-observation projector | twelve-row read surface | static maturity as runtime truth |
| Management AI | OpenClaw diagnostic provider + typed command admission | NL/SSE and confirmation | shell/repository/task authority |
| Release | existing exact-pair deployment workflow | none | alternate deploy lane or mutable branch deployment |

### 6.1 Target package structure

Use the same four responsibilities inside each sufficiently complex domain;
do not manufacture empty layers for small domains:

```text
services/control-plane/bff/
  bootstrap/
    app.py                 # FastAPI construction and lifecycle only
    dependencies.py        # concrete production wiring only
    settings.py
  core/
    http_errors.py         # cross-domain HTTP envelope/value rules
    auth_contracts.py      # identity/authorization value contracts
    observations.py        # provenance/freshness value types
  personas/
    router.py              # HTTP translation
    application.py         # use cases and transaction coordination
    domain.py              # lifecycle rules/value types
    ports.py               # domain-owned protocols
    adapters/              # persistence/external implementations
  research/                # same responsibility pattern where needed
  deployment/
  runtime/
  management_read_models/
  agora/
    interaction/
    research/
    trading_room/
    performance/
```

Dependency direction is:

```text
router -> application -> domain
                    -> domain-owned ports <- adapters
bootstrap -------------------------------> concrete adapters
```

`domain` imports neither FastAPI nor infrastructure. Adapters may import domain
ports/types. Bootstrap is the only layer allowed to know both routers and
concrete adapters. Domain-to-domain communication uses an explicit application
port or canonical event, not another domain's router/store internals.

This is a responsibility map, not a mandate for one file per class. Small,
cohesive modules remain together; large modules split only along use-case or
aggregate ownership boundaries.

## 7. Architecture decisions

### ADR-01 — replace the global read facade with explicit domain dependencies

Do not add the missing mutation methods to `ReadSurfacePorts`. Split mounted
routers by the dependencies they actually use:

- query ports for read-only projections;
- application command ports for mutations; and
- event/SSE ports for replayable streams.

The composition root constructs each concrete port and passes it to the router
factory. An unavailable optional domain is represented by a typed unavailable
query implementation at construction time; required command owners make
startup fail closed.

`read_store` is removed after all callers migrate. It is not renamed to
`data_facade`, `store_v2` or another global service locator.

### ADR-02 — extraction deletes copied implementations

For each `main.py`/domain duplicate group:

1. classify the canonical owner;
2. redirect every production caller to that owner;
3. move shared value-only helpers to a named neutral module only when genuinely
   cross-domain;
4. delete the duplicate body in the same PR; and
5. add an AST boundary test preventing reintroduction.

Mechanical line splitting without deletion is rejected.

### ADR-03 — durable owner wins over overlays

Persona, Strategy, Incident, Job and Ranking overlays do not receive a durable
wrapper. Writes migrate directly to their owners. Reads use owner-backed
projections. During migration, shadow comparison may observe both results but
must never acknowledge from the overlay or fall back to it after owner failure.

Overlay retirement occurs per aggregate after backfill/parity and restart
tests. There is no permanent dual-write phase.

### ADR-04 — choose one Decision Journal

An owner decision is required before implementation. The selection criterion
is aggregate completeness, transaction/outbox support, tenant isolation and
current consumers—not file location. The selected owner receives any missing
contract capability. Agora adapts to it. The other implementation is migrated,
caller-counted to zero and deleted.

No synchronizer between the two journals is permitted.

### ADR-05 — loop truth is receipt-derived

Create one read-only projector over canonical owner observations. It consumes
existing domain events/receipts and emits twelve rows with:

- loop ID and owner;
- stimulus, terminal and next-consumer receipt identities;
- observed source/service/release identity;
- status, freshness and degradation reason; and
- correlation/causation continuity.

It must not issue domain commands, synthesize missing receipts or infer success
from registry maturity.

### ADR-06 — Agora provenance is resolved server-side

The authentic research owner emits a signed/traceable execution receipt. The
Agora projection resolves that receipt from the canonical owner and derives
`real`, `simulation` or `unavailable`. A client boolean cannot promote a run to
`real`.

### ADR-07 — delivery remains one exact-pair lane

Repair the existing deployment workflow rather than creating another. Before
switching it must parse and validate the currently served manifest, establish
a rollback baseline, then deploy the immutable candidate and re-read both FE
and BFF identities. Invalid/missing baseline fails closed before mutation.

### ADR-08 — development tooling stays outside product runtime

SA/SD materialization and implementation dispatch use the local governed
development path. Product Management AI may diagnose product state and submit
typed product commands only. It gains no filesystem, git, supervisor or task
mutation capability.

### ADR-09 — eliminate dynamic module wiring

Production code must not mutate `sys.path`, discover dependencies through
`globals()`, or re-export another module by copying its namespace. Provide one
stable package/import root in the container and test configuration. Temporary
import compatibility is allowed only at the executable boundary, with one
caller and a removal wave; it cannot exist inside domain modules.

### ADR-10 — tests target contracts, not composition globals

Most tests construct router dependencies or application services directly.
Only a small composition test imports the final FastAPI app. Test overrides use
FastAPI dependency overrides or explicit factories; they do not mutate
`bff_main.read_store`, overlays or arbitrary globals.

### ADR-11 — structure uses cohesion guardrails, not arbitrary microservices

Oversized router factories split by resource/use-case sub-router. Oversized
application modules split by aggregate or transaction boundary. Pure value
types may be shared only when semantics are identical. Do not create a network
service, repository wrapper, manager class or generic utility module solely to
reduce line counts.

## 8. Twelve-loop closure architecture

| Loop | Authoritative stimulus | Terminal evidence | Required next receipt |
|---:|---|---|---|
| 1 Source ingestion | scheduled/manual source command | durable SourceRecord | distillation admission |
| 2 Distillation | admitted source record | terminal distillation result | reviewed alpha candidate |
| 3 Alpha replication | reviewed candidate | terminal ExperimentRun | Persona teaching/research receipt |
| 4 Persona teaching | teaching command | evaluation/target update | Agora or consultation handoff |
| 5 Agora interaction | workshop message | terminal interaction/research result | dataset/candidate receipt |
| 6 Shadow evaluation | eligible persona/strategy | terminal shadow candidate | consultation/research receipt |
| 7 Consultation | consultation request | durable memo/decision | governance receipt |
| 8 Promotion/deployment | approved promotion | executable deployment binding | runtime loader receipt |
| 9 Capital execution | executable paper signal | order/fill/position/heartbeat | telemetry ingest receipt |
| 10 Reconciliation | fill/heartbeat | drift or reconciled terminal state | incident/evolution receipt |
| 11 Evolution | incident/postmortem | evolution decision | dispatch-to-next-loop receipt |
| 12 Loop truth | owner observations | twelve-row projection | authenticated Management readback |

This is one causal graph. Twelve isolated API responses do not satisfy it.

## 9. Migration waves

```text
Wave 0  freeze contracts + ownership/caller inventory
   |
Wave 1  composition root + explicit domain ports
   |
Wave 2  durable writers + overlay/journal migration
   |
Wave 3  Agora natural handoffs + receipt provenance
   |
Wave 4  loop-truth projector + Management/AI read paths
   |
Wave 5  FE strict-live and exact-pair delivery repair
   |
Wave 6  same-run 12-loop hosted acceptance
   |
Wave 7  dead-code/compatibility deletion and closeout
```

Each wave must delete what it replaces. Later waves cannot introduce adapters
to preserve an unretired earlier authority.

## 10. Failure semantics

| Failure | Required behavior |
|---|---|
| required command owner unavailable at startup | BFF startup/readiness fails |
| optional read owner unavailable | typed degraded response with provenance; no local fallback data |
| canonical write fails | mutation fails; no overlay acknowledgement |
| outbox publish delayed | durable pending state; idempotent retry |
| duplicate command | same canonical result/receipt, no second side effect |
| missing real research receipt | simulation/unavailable, never `real` |
| loop correlation breaks | affected loop and downstream rows degraded/open |
| SSE disconnect | replay from durable cursor with bounded retention |
| hosted manifest invalid | no switch; preserve existing service |
| rollback identity not provable | deployment blocked before mutation |
| provider unavailable | Management AI degrades honestly; product commands remain independently governed |

## 11. Architecture acceptance

Architecture closure requires all of the following:

- no production domain service imports `main.py`;
- no production `sys.path` mutation or dynamic namespace forwarding inside
  domain/router modules;
- tests importing the composition root are limited to an explicit app
  composition/smoke allowlist;
- router factories contain route registration and HTTP translation only, with
  application use cases independently testable;
- `main.py` contains composition only and no copied domain definitions;
- no global `read_store` service locator;
- every mounted mutation route has one concrete command owner;
- zero production writes to the five audited process-local overlays;
- one Decision Journal implementation and one writer;
- all 17 unreachable tails deleted;
- static and runtime route uniqueness pass;
- worker/queue/lease inventory shows one owner per subject;
- loop truth rows are receipt-derived and survive restart;
- strict-live FE has zero reachable mock/seed/write fallback;
- one exact FE/BFF pair passes deploy, rollback and served-identity readback;
- all mandatory twelve-loop tests execute without skip; and
- authenticated Management, Management AI and Agora journeys pass on that same
  pair.

## 12. Rejected designs

| Rejected option | Reason |
|---|---|
| add all missing methods to `ReadSurfacePorts` | turns a read facade into a new monolithic command/store authority |
| introduce `ReadSurfacePortsV2` | duplicates the same abstraction and prolongs migration |
| wrap overlays with Redis | makes the wrong owner durable instead of removing it |
| dual-write both Decision Journals | creates reconciliation and split-brain failure modes |
| create a new loop orchestrator | duplicates existing domain workers and obscures ownership |
| derive closure from CI/task metadata | evidence is not product execution |
| stand up a second deploy workflow | creates two release authorities |
| keep copied functions as compatibility | fixes continue to diverge across copies |
| mark skipped deployed tests as accepted | absence of execution is not evidence |
