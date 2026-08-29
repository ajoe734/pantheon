# System Design — Pantheon Architecture Cleanup

Status: file-level development design; package IDs below are planning identifiers,
not supervisor task IDs

## 1. Delivery rules

Every cleanup change must satisfy these rules:

1. start from current `origin/dev` in the owning repository;
2. add or update characterization tests before switching behavior;
3. retain the public HTTP/UI contract unless this document identifies the old path
   as shadowed or false-success;
4. move callers and delete the replaced implementation in the same bounded wave;
5. never add a second store, route, compatibility layer, or truth source;
6. run focused tests plus the owning repository's build/contract gates; and
7. bind runtime evidence to the exact deployed source identity.

## 2. Permanent architecture gates (ACG-00)

These guards land first and remain after cleanup.

### 2.1 Backend route uniqueness

Replace the allow-list behavior in
`services/control-plane/bff/test_route_resolution_no_shadowing.py`. The current test
explicitly accepts known duplicate routes and checks only four winners. Extend or
replace it so the constructed FastAPI app fails on every duplicate normalized
`(method, path-shape)` pair.

The scanner must:

- normalize every `{parameterName}` to one token;
- include routes from included `APIRouter` instances;
- exclude framework-generated `HEAD` only by an explicit rule;
- report route order, owner module, handler qualname, and operation ID;
- assert one registration per method/shape; and
- separately assert that no earlier parameter route shadows a later literal route.

The guard must run after all router inclusion and must also reject code that mutates
`app.router.routes` to choose a preferred duplicate. Delete
`_prefer_latest_bff_gap004_routes` only after the fifteen masked families have one
canonical registration.

Update the backend route-manifest generator to include `owner_module` and
`handler_qualname`. Regenerate the snapshot only after the collision count is zero.

### 2.2 Frontend dependency gate

Add a production-import graph test in `execute-plans` that ignores tests, generated
build output, and type-only edges when configured, but rejects:

- runtime SCCs;
- `transport -> domain client` imports;
- `bff -> bff-v1` or `bff-v1 -> bff` after the relevant migration wave;
- `bff-v1 -> v5` inverse dependencies; and
- UI imports from deprecated API barrels.

The gate prints the shortest cycle, not only a file count.

### 2.3 Entrypoint gate

Add a subprocess test that runs every persistent Python Compose command and its
healthcheck from a working directory outside the repository. At minimum it must run
the Agora interaction worker's exact command with `--healthcheck`. Import and
dependency construction happen before success is returned.

### 2.4 Exact component gate

Add one reusable deployment verifier that accepts the expected backend SHA and a
required-service manifest. For each service it checks:

- Compose resolves the service;
- exactly one active container exists;
- state is running, not restarting or exited;
- configured health is healthy;
- image label `org.opencontainers.image.revision` equals the expected SHA; and
- the configured command matches the manifest entrypoint.

Contract tests run the verifier against fixtures; the dev deploy runs it against the
actual Docker daemon before acceptance.

## 3. BFF route ownership cleanup (ACG-01)

### 3.1 Collision closure

Create focused domain routers using existing BFF conventions. Suggested physical
owners are:

| Behavior | Target module |
|---|---|
| BFF event list and stream | `services/control-plane/bff/events/router.py` |
| Evolution programs/actions | `services/control-plane/bff/evolution/router.py` |
| Research experiments/jobs | `services/control-plane/bff/research/router.py` |
| ranking formula reads/writes | a focused router in the existing Capital/Management read-model package |
| Agora signal/journal aliases | existing `agora` subrouters |
| generic action command | `command_adapters` route module using `_submit_canonical_action_command` behavior |

If an existing package already owns the route family at implementation time, extend
that package instead of creating the suggested path.

For Events, Evolution, and Experiments, characterize both current implementations.
The current runtime route table deliberately prunes the earlier declaration and
keeps the later handler for these masked families; source order alone does not prove
runtime ownership. Merge the later contract/filter behavior with durable
store-backed behavior, eliminate `_GOV_BFF_*_OVERLAY`, delete both the replaced
declarations and the pruning table, and retain one route.

Delete all later generic signal, journal, SSE, and ranking registrations. They do
not receive a deprecation period because they are already unreachable behind the
earlier route.

### 3.2 Domain extraction from `main.py`

After collisions are zero, migrate domain families in bounded batches. Each batch:

- moves models/helpers used only by that family;
- introduces explicit constructor dependencies for auth, clock, client/store, and
  command submission;
- registers the new router once;
- deletes the original route decorators and bodies; and
- runs an OpenAPI and black-box response diff against the frozen contract.

The exclusive BFF integration lane owns edits to `main.py`. Domain lanes can prepare
new router modules and tests in parallel but do not independently patch the
monolith.

### 3.3 App factory finish

When route migration is complete, create a small app-composition module and retain
`main.py` only as the container import shim if changing `uvicorn main:app` is not
worthwhile. No route body may remain in that shim.

### 3.4 Acceptance

- zero normalized method/path collisions;
- OpenAPI operation IDs are unique;
- no route is served by a generic echo/empty handler when a domain owner exists;
- the collision-family contract tests pass;
- full BFF test suite passes; and
- the built BFF image starts and serves `/livez`, `/readyz`, `/bff/version`, and
  representative routes from every migrated family.

## 4. Read store decomposition (ACG-02)

### 4.1 Inventory artifact

Generate a temporary migration inventory from the AST and repository call graph. For
each of 457 methods record:

- current method name and line;
- production callers;
- test-only callers;
- dataset/store/client used;
- mutation or read behavior;
- target typed port;
- disposition; and
- zero-caller deletion proof.

The inventory is migration evidence, not a runtime registry.

### 4.2 Fixture extraction

Move `_default_read_data`, fixture-pack merging, and all contract seed helpers out of
product `read_store.py` into test-only factories. Update tests to request explicit
datasets or fake domain ports. Do not copy the 3,708-line default blob into another
product module.

Once no product or test constructor needs it:

- delete `allow_local_snapshot_fallback` from `ReadSurfaceStore`;
- remove `PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK` from Compose/deploy config;
- delete production fixture-pack loading; and
- keep JSON fixtures only where a current contract test explicitly consumes them.

### 4.3 Typed domain migrations

Implement migrations in parallel by target domain, but let one integration lane
delete methods from `read_store.py`:

| Lane | Caller target | Required behavior |
|---|---|---|
| Persona/Training | narrow service client/projection | list/detail/capability/session reads |
| Consultation | existing `ConsultationServiceClient` | request/session/transcript/memo reads |
| Research/Search | typed clients and durable experiment projection | no process-local job/experiment overlay |
| Governance/Deployment/Evolution | service clients and command adapters | no local foreign-domain mutation |
| Runtime/Capital/Ranking | Runtime/Capital clients and named BFF projection repository | BFF-owned writes isolated from foreign reads |
| Lifecycle/Telemetry/Incident | existing readers | preserve exact identity and freshness semantics |
| Agora | package stores/application operations | no Agora persistence in the generic store |
| Management | dependencies injected from the above | composition only |

Do not preserve the public surface of `ReadSurfaceStore` as a forwarding class. A
route moves directly to its narrow port and the method disappears.

### 4.4 Final deletion

Delete `ReadSurfaceStore`, then decide the two generic adapters dataset by dataset.
If a snapshot is a legitimate historical read contract, give it a named owner and a
typed API. Delete the generic string selector when no dataset remains.

### 4.5 Acceptance

- production import search finds no `ReadSurfaceStore`;
- product source cannot load fixture packs or seed examples;
- BFF startup with missing owners reports unavailable/degraded, not fixture data;
- routes retain response and error contracts;
- each foreign-domain mutation goes through its owner; and
- `read_store.py` is deleted or contains only a small, explicitly retained adapter
  whose callers and authority are documented.

## 5. Frontend API graph cleanup (ACG-03, `execute-plans` repository)

### 5.1 Keep the existing canonical surface

Keep `src/lib/bff-v1` as the public production API owner. Consolidate low-level
behavior into its existing `liveTransport`, `liveStatus`, runtime-environment,
write, and SSE modules. Do not create `src/lib/api`, a new transport wrapper, or a
fourth version layer.

`src/lib/v5` becomes pure DTO/view-model/adapter code. `src/lib/bff` is drained as a
migration source; only explicitly isolated test/demo fixtures may remain until their
own deletion. No response projection, endpoint catalog, or page-specific behavior
belongs in low-level transport.

### 5.2 Domain client migration

Break the proven SCCs first:

- change `runActionSafe.ts` to import `tryRunAction` directly from `./writes`;
- move `BffMode` to the existing runtime environment or DTO module; and
- move `ManagementListMeta` to the existing DTO module or its specific envelope.

Then consolidate without forwarding facades:

1. merge `bff/liveRead.ts` and `bff/liveTransport.ts` into existing `bff-v1`
   transport/status modules; move the sole external OODA detail caller from
   `bff/client.ts` into `managementConsoleReads`;
2. merge required `bff/runAction.ts` and `bff/commandClient.ts` behavior into
   `bff-v1/writes.ts`, which already has stricter live-write refusal;
3. migrate production consumers of mock mutations, write fallback, write overlay,
   and unconditional mock persistence to typed domain writes;
4. merge realtime side effects into existing `bff-v1/sse` modules;
5. make existing `bff-v1/v5.ts` the live V5 API owner and keep only pure transforms
   in `v5`; and
6. migrate the 53 live legacy/seed-accessor consumers by entity family.

Public UI imports from `bff-v1` may remain while the public surface is canonical;
package-internal code must use direct relative imports and may not import its own
barrel.

### 5.3 Remove version layers

When a domain has zero imports from an old path, delete its old files and barrel
exports. Delete `legacy.ts` only after its 53 live consumer files move. Delete local
success from `writeFallback.ts`; `PersonaOnboarding` must surface canonical write
failure/unavailability. Move `@/mocks/seed` bootstrap out of `src/main.tsx` reachability
into a compile-time demo/test entry. Do not keep a barrel that only re-exports the
canonical implementation.

Generated types remain in a generated/contract path with their generation command
and source hash. Handwritten client logic may import them; generated files may not
import client/runtime code.

### 5.4 Acceptance

- no runtime SCC;
- no type-only SCC in the audited API layers;
- no `bff ↔ bff-v1` or `bff-v1 ↔ v5` inverse pair;
- no Rollup cycle warning;
- no strict production reachability from `src/main.tsx` to mock seed, persistence,
  mutation, overlay, or synthetic-success code;
- strict live typecheck, unit tests, and production build pass;
- API contract coverage remains complete; and
- representative authenticated reads, writes, and SSE reconnects pass against the
  exact candidate BFF.

## 6. Management loop truth cleanup (ACG-04)

### 6.1 Extract route and projection

Move loop-inventory and loop-health HTTP code from `main.py` into a focused
Management loop router. Retain `loop_inventory.py` only as the static catalog and
projection implementation or rename it once; do not duplicate its projection.

Replace `_async_loop_health_records` with one controller-store query scoped by
tenant and environment. Admission continues to validate freshness, lease/fencing,
deployment SHA, controller name, truth level, and evidence references.

Complete the admission predicate in the existing `loop_inventory` projection. It
must require a stable catalog controller contract, exact deployed SHA,
controller-specific cadence freshness, authoritative desired and downstream actual
state, internally consistent trigger/success/failure evidence, and terminal output
or next receipt. Remove the current permissive rule that accepts any nonblank
controller name when the catalog identity is blank. Reuse the BFF's existing
deployed-SHA source instead of adding a second identity resolver.

### 6.2 Remove alternate authorities

- delete `loop_health` from generic BFF snapshot dataset specs;
- delete `ReadSurfaceStore.list/get_loop_health_records` and adapter forwards;
- delete the BFF request-path fallback to snapshot records;
- stop calling `publish_loop_12_controller_truth` during reads;
- remove downstream target overlays from loop rows; and
- keep `DownstreamHealthMonitor.get_state()` under the downstream-health route.

Implement Loop 12 publication through a real owner writer. The other nine missing
controller implementations are functional work that can proceed independently once
the one-store contract is fixed.

Return exactly twelve canonical loop rows from the runtime health surface. Preserve
the composite overlay only in inventory or a separately named noncanonical field;
do not append it to the canonical `items` array.

In `execute-plans`, replace `current_maturity` / `target_maturity` reads with
`runtime_maturity`, and derive live state from the backend's existing nested
`live_status.operator_truth` (or the same accepted packet field used by the final
contract). Delete the stale DTO fields and top-level `operator_truth_source`
expectation. Do not add compatibility aliases to the backend.

### 6.3 Acceptance

- exactly twelve catalog rows always appear;
- only accepted current controller records can mark a row observed/reconciled/live;
- absent records remain explicitly unobserved;
- a degraded component probe does not rewrite another loop's controller record;
- a GET does not write or schedule a controller heartbeat;
- frontend and backend loop DTO contract tests agree on runtime maturity and nested
  operator truth;
- tenant/environment isolation and lease fencing tests pass;
- restart readback comes from PostgreSQL; and
- current Management and cross-loop E2E consume the same endpoint and identity.

## 7. Runtime Manager consolidation (ACG-05)

### 7.1 Semantic parity inventory

Compare the old execution `RuntimeManager` methods (`deploy`, `pause`,
`complete_pause`, `resume`, `record_failure`, `replace`, `rollback`, emergency
dispatch) against `RuntimeManagerService`. Add missing characterization cases to the
deployed service suite. Valid unique behavior is merged into the deployed service;
test-only shortcuts are not.

### 7.2 Importable kernel

Create or select one importable underscore-named Python package for Runtime Binding,
kill-switch primitives, and the service implementation. The stable Compose service
name remains `runtime-manager`. Its existing deployment directory may be a thin
Docker/HTTP wrapper, but it must not own a second state machine.

Move the execution binding schema/contract with history-preserving file moves where
possible. Replace `sys.path` and path-based `importlib` loading with normal imports.

### 7.3 Migrate tests and worker

- change `tests/e2e/test_deployment_plan_to_paper_run.py` and
  `tests/e2e/test_allocation_policy_to_paper_run.py` to use the deployed service or
  its real HTTP client;
- move `paper_fleet_reconciler.py`, its Dockerfile, and tests to an explicitly named
  paper-fleet worker package; and
- keep its Compose service identity `paper-fleet-reconciler` unless a separate
  deployment migration proves a rename is safe.

Delete `services/execution/runtime-manager/runtime_manager.py` only after zero source
and test callers remain.

### 7.4 Acceptance

- one `RuntimeManager` state-machine class in production source;
- E2E uses the Compose-deployed semantics;
- no path-based sibling imports;
- Runtime Binding lifecycle, replacement, rollback, transition, and kill-switch
  suites pass; and
- runtime-manager and paper-fleet containers start at the exact candidate revision.

## 8. Agora Workshop decomposition (ACG-06)

### 8.1 Public internal APIs first

Extract `_ws_publish`/replay and `_build_readiness_assessment` from the router into
public domain modules. Switch Research, Interaction, and Trading Room imports to the
public modules before splitting route bodies. Add import-boundary tests that reject
imports of underscore-prefixed symbols from another router.

### 8.2 Router split

Move the existing 18 handlers without rewriting behavior:

- session router: list/create/detail/messages/events/completeness/cards/readiness;
- version router: list/create/select;
- execution router: reconstruct/research/consultation/conclude; and
- stream router: SSE connect/replay.

The package `router.py` becomes constructor/composition only. Shared request models
move to a single API models module, not copied per router.

### 8.3 Store split and merge

Extract DDL/schema naming, memory backend, and Postgres backend into separate files
behind one structural protocol. Fold the three-method
`PostgresStrategyWorkshopStore` bootstrap behavior into the canonical schema module
or `PostgresWorkshopStore`, update its bootstrap test, and delete the class.

Memory is injected explicitly by tests. Production configuration must select
Postgres and fail if it cannot construct it.

### 8.4 Frontend page split

In `execute-plans`, keep the existing Agora route and
`bff-v1/agora/workshops.ts`. Do not insert a new controller/query/store wrapper.

- extract `WorkshopListView` from the current list/create block and navigate on
  select/create to `/agora/strategy-workshop/:id`;
- extract `WorkshopSessionView` from the current detail block and render it only for
  the URL-selected ID;
- leave the page root as a thin collection/detail switch;
- move only pure reducers/helpers to an optional pure session-model file;
- delete local `selectedWorkshopId` auto-selection and update tests that expect a
  session at the collection URL; and
- delete `TradingDeskLayout`'s duplicate `useServantWorkshopContext` GET. Make its
  drawer ID-only/presentational or render Workshop-aware content inside the session.

Move ETag read → message POST → durable event readback into one operation in the
existing Workshop client. Reconstruction remains a separate receipt. Persona daily
interaction remains active but becomes an explicit optional action with its own
receipt and cannot gate or relabel the durable Workshop message.

Before acceptance, characterize initial request failures that are currently reduced
to null/empty and prove that the SSE effect does not reconnect whenever
`lastEventId` changes.

### 8.5 Acceptance

- no other router imports a private Workshop router helper;
- one production Postgres store and one explicit test memory store;
- all 18 route contracts and restart persistence pass;
- interaction worker reads/writes the same Workshop owner;
- frontend strict build and Workshop tests pass; and
- list selection and creation navigate to the canonical detail URL;
- the shell and session do not issue duplicate Workshop projection GETs;
- an optional Persona failure cannot turn a durable Workshop message receipt into a
  failed message; and
- SSE does not reconnect per received event; and
- hosted create → message → readiness → version → research/consult → conclude →
  reload journey succeeds on the exact FE/BFF pair.

## 9. Source Ingestion entrypoint cleanup (ACG-07)

### 9.1 Runtime construction

Extract path/env resolution and object construction from `main.py` into a runtime
module with an explicit factory. It constructs current classes only; it does not
wrap them in another generic repository.

The runtime exposes or provides narrow operations for:

- ingest execution and replay;
- connector/catalog/controller reconciliation;
- proposals;
- health/coverage; and
- Management commands/readback.

### 9.2 Pipeline consolidation

Move `_run_job`, evidence persistence, market snapshot/storage persistence,
distillation admission, receipt creation, health/usage update, and search-refresh
notification into one ingest application service. All triggering routes and the
controller/scheduler use this operation.

The existing `IngestManager`, `IngestionScheduler`, stores, evidence builder, and
domain helpers remain canonical; do not clone them.

### 9.3 Router split

Create five router modules matching the groups in the GAP. Move request models to a
shared API models module. Each router accepts explicit operations/dependencies from
the app factory.

Finally reduce `main.py` to app construction and router inclusion. Delete every
original decorator/body after registration switches.

### 9.4 Acceptance

- all 68 existing route contracts remain registered once;
- no router imports Source Ingestion `main`;
- controller, manual job, source-record ingest, scheduled run, and frontier replay
  share one pipeline;
- evidence, receipt, watermark, DLQ, health, usage, and distillation tests pass;
- bounded source E2E and restart readback pass; and
- container starts and readiness reports the exact source identity.

## 10. Dead frontend NL/stub removal (ACG-08, `execute-plans` repository)

Delete these proven leaves together:

- `management/components/nl/NlAssistantDrawer.tsx`;
- `management/pages/oversight/NlConsole.tsx`;
- `management/pages/oversight/_stubs.tsx`;
- deprecated fixed responder `lib/bff-v1/managementNl.ts`;
- the NL re-export from `lib/v5/management/index.ts`;
- obsolete provider/gateway flags in `.env.example`; and
- `mgmt.nl.*` / `stubNotice` locale keys whose only runtime consumers are the dead
  UIs.

`lib/v5/management/nl.ts` still owns the `ManagementNlContext` type imported by
`useManagementNlContext.ts`. Move that interface into the active hook or existing
Management AI UI snapshot type, then delete the fixed classifier/responder types.
Keep `useManagementNlContext.ts` because `AgentPanelBody` uses it, but remove its
stale `/management/ask` label; that route redirects.

Do not delete the active `managementAi.ts` client, `AgentPanelBody`, floating panel,
or `POST /bff/management/nl/ask`. Do not infer that other zero-TypeScript-caller
Agora/SSE modules are dead until generated-contract and test callers are verified.

Acceptance is zero production imports/exports, clean typecheck, no bundle chunk for
the removed UI, passing Management navigation tests, a browser proof that the
floating panel reaches the BFF or reports provider degradation, and no fixed mock
answer reachable in strict production.

## 11. Entrypoint and exact deployment closure (ACG-09)

### 11.1 Worker entrypoint

Move the Agora interaction CLI into the Interaction package or make the script a
thin import shim. Ensure both repository root and BFF package resolution are defined
by the image/package configuration rather than current working directory luck.

Replace the nonexistent `FastBffReadStore` branch with a narrow Persona discovery
port backed by the canonical Persona service/client. Required dependency
construction errors terminate startup and fail health; they never install an empty
implementation.

`--healthcheck` must import the worker, construct non-network configuration, and
validate required dependency factories. It may skip the long-running loop and live
database mutation, but it cannot return before imports.

### 11.2 Compose and deploy

- add `agora-interaction-worker` to `REQUIRED_LOOP_WORKERS` under Loop 5;
- make the BFF component build/recreate it with `operator-bff`;
- include it in BFF rollback;
- run the exact component verifier after root and BFF deploys;
- fail the deployment when any required service is missing/restarting/unhealthy or
  has the wrong revision; and
- record the backend required-component receipt before the hosted pair is marked
  accepted.

### 11.3 Tests

Add or update:

- launcher subprocess test from a foreign CWD;
- worker factory test proving no empty Persona fallback;
- Compose command/healthcheck contract test;
- built BFF image entrypoint smoke;
- required-worker list test;
- BFF component recreate/rollback contract test;
- root post-deploy exact-service fixture tests; and
- live dev exact revision/health verification.

### 11.4 Acceptance

- worker completes import and healthcheck in the built image;
- worker processes one durable test interaction and survives restart;
- Persona discovery returns canonical data or truthful unavailability;
- restart count remains stable at zero during the bounded proof;
- all required services match the candidate SHA; and
- hosted acceptance cannot succeed when a required process is broken.

## 12. Integration package (ACG-10)

After ACG-01 through ACG-09 land, perform one integration pass; do not implement new
features during it.

Required gates:

1. backend static compile/import and route uniqueness;
2. focused domain suites for every moved route/store;
3. full Source Ingestion, Runtime Manager, Agora, and BFF suites;
4. frontend lint/typecheck/unit/strict-live build and import graph;
5. Compose config and all persistent entrypoint smokes;
6. current twelve-loop research/runtime/human-learning E2E suites with skips
   reported, not counted as passes;
7. Management loop-health exactly-twelve truth test;
8. hosted Agora and Management user journeys;
9. cross-loop exact-identity/receipt journey;
10. exact component deployment, restart/readback, and public read-only restore.

If a functional test fails, produce a failure report tied to the owning canonical
path. Do not immediately add a repair route, fallback, or alternate store. The report
must first determine whether the canonical implementation, caller migration, test
assumption, or deployment identity is wrong.

## 13. Parallel delivery map

| Lane | Can start after ACG-00 | Main write hotspot | Parallel-safe preparation |
|---|---|---|---|
| BFF collision/domain routers | yes | BFF `main.py` | new router modules and characterization tests |
| Read-store domain ports | yes | `read_store.py` | typed clients/fakes and route tests |
| Frontend API graph | yes | API barrels | transport/domain client modules |
| Management truth | yes | BFF `main.py`, later read store | projector/router and controller-store tests |
| Runtime Manager | yes | runtime service/package paths | parity tests and importable kernel |
| Workshop backend | yes | Workshop router/store entry files | events/readiness/routes/store modules |
| Workshop frontend | after canonical client shape is fixed | Workshop page | panel/controller extraction |
| Source Ingestion | yes | Source `main.py` | runtime, operation, routers, models |
| dead frontend UI | yes after caller snapshot | exports/routes | deletion patch and focused tests |
| deployment/entrypoint | yes | deploy script/workflow | verifier and launcher tests |

Only the hotspot switch is serialized. Preparation, characterization, and focused
domain implementation remain parallel.

## 14. Execution-task generation rule

When the operator later requests materialization, create tasks from these packages
with exclusive artifact ownership. Do not create one task per method or a long
dependency chain. Recommended roots are ACG-00, ACG-01, ACG-02 domain lanes, ACG-03,
ACG-04, ACG-05, ACG-06 backend/frontend, ACG-07, ACG-08, and ACG-09, followed by one
ACG-10 integration task.

The BFF `main.py`, `read_store.py`, frontend barrels, Workshop entry files, Source
`main.py`, and deploy script each have exactly one integration owner. Parallel tasks
prepare inputs but do not all claim those files.
