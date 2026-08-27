# Pantheon Architecture Cleanup GAP — 2026-08-27

Status: **code-first baseline; implementation and task materialization have not
started**

Precedence: this document updates architecture-cleanup conclusions for the frozen
source identities in [`INDEX.md`](INDEX.md). It does not replace the twelve-loop
functional GAP, Agora functional GAP, or their historical evidence.

## 1. Executive verdict

Pantheon does not currently have a clean single-owner product architecture. The
problem is not merely that several files are large. The code contains observable
duplicate route registration, bidirectional frontend API dependencies, a BFF store
that combines unrelated domains and fixture generation, and runtime truth assembled
from authorities that can disagree.

The highest-risk facts are:

1. `services/control-plane/bff/main.py` has 69,561 lines and 493 source-level
   `@app` route decorator lines (492 use literal paths). It contains **21
   normalized method/path collision groups containing 43 registrations**. Fifteen
   groups are hidden by runtime
   mutation of `app.router.routes`; six groups remain multiply registered. The
   resulting route table depends on source order and mutation timing rather than
   structural ownership.
2. `ReadSurfaceStore` is a 13,498-line class with 457 methods. It combines generic
   snapshots, HTTP clients, domain projections, local writes, test fixtures, and
   compatibility behavior. Its source file is 20,883 lines.
3. `execute-plans` has bidirectional production imports between `bff` and `bff-v1`,
   plus bidirectional imports between `bff-v1` and `v5`. These are layer inversions;
   the proven runtime SCCs are currently internal to `bff-v1`. A live build already
   warns about the `runActionSafe` / `legacy` barrel cycle.
4. Management loop health is composed from a static catalog, PostgreSQL controller
   records, BFF-local snapshot files, and a BFF downstream monitor that can create or
   mutate loop rows. Only the first two have legitimate long-term ownership roles.
5. `services/runtime-manager/` is the deployed owner, while
   `services/execution/runtime-manager/runtime_manager.py` still supplies a second
   manager used by two E2E tests. Those tests can pass without exercising the
   deployed owner.
6. Agora Workshop is already a package, but its 4,073-line router owns routes,
   readiness policy, cards, SSE state, reconstruction orchestration, versioning,
   research, consultation, and conclusion. Other routers import its private
   helpers. The store file has both a production Postgres class and a second
   bootstrap-only Postgres class.
7. Source Ingestion's 4,127-line entrypoint defines 68 routes, 27 request/model
   classes, global runtime construction, ingest orchestration, proposals, health,
   coverage, and Management commands even though most domain modules already exist.
8. Three frontend NL/stub UI files have no production callers. Their associated
   compatibility reads must not be retained solely because the dead UI imports them.
9. The current dev `agora-interaction-worker` container was observed restarting with
   restart count 438 and `ModuleNotFoundError: No module named 'services'`. The
   launcher also imports a nonexistent `FastBffReadStore` and silently substitutes an
   empty Persona stub. Existing unit tests did not execute the real container command,
   and the deployer's required-worker list omits this worker.

The cleanup direction is therefore deletion and consolidation, not a new façade over
the old façade. The target is one route owner, one typed domain port per caller, one
runtime truth store, and a deploy gate that executes the exact shipped entrypoint.

## 2. Audit method

The conclusions come from current code rather than task history:

- AST and normalized-path inventory of BFF route decorators;
- class and method inventory plus production/test caller searches;
- import-edge and strongly connected component inspection in `execute-plans`;
- Compose, Dockerfile, launcher, deployment-script, and test inspection;
- live Docker container state and logs for the currently installed root stack; and
- the hosted deployment manifest for exact FE/BFF identities.

A filename is not marked REMOVE until a production, test, workflow, Compose, and
documentation search has been considered. A large file is not automatically a gap:
generated contracts, localization catalogs, and coherent validation programs can be
large without creating duplicate authority.

## 3. Disposition summary

| Priority | Current defect | Primary disposition | Completion result |
|---:|---|---|---|
| 1 | BFF entrypoint has 493 source decorators and route collisions/pruning | KEEP app composition; MIGRATE domain routes; MERGE or REMOVE every collision | one registered handler per normalized method/path |
| 2 | one 457-method cross-domain store | MIGRATE callers by domain; REMOVE fixture/runtime mixing and final God class | routes depend on narrow canonical clients/stores |
| 3 | `bff`, `bff-v1`, and `v5` import in both directions | KEEP one transport; MIGRATE clients by domain; REMOVE barrels/legacy cycle | acyclic UI → domain client → transport graph |
| 4 | Management loop truth has multiple live candidates | KEEP catalog metadata and controller store; MIGRATE writers; REMOVE snapshot/monitor authority | twelve rows joined from catalog plus current owner records |
| 5 | two runtime-manager implementations/directories | KEEP deployed service and execution contract kernel; MIGRATE two E2E callers; REMOVE duplicate manager | E2E exercises the deployed owner |
| 6 | Workshop router/store/page are multi-responsibility hubs | KEEP Workshop domain; MIGRATE helpers/routes/components; MERGE Postgres bootstrap | no cross-router private imports; one Postgres store |
| 7 | Source Ingestion entrypoint owns application and all routes | KEEP app factory; MIGRATE five route families and pipeline orchestration | small composition root over existing domain modules |
| 8 | unused NL/stub UI and compatibility reads remain | REMOVE proven zero-caller UI; VERIFY shared helpers before deletion | no dead screen or stub-only API surface |
| 9 | shipped entrypoint is broken and exact deployment ignores it | MIGRATE launcher to package-safe imports; REMOVE empty fallback; add exact gates | every required shipped command starts at accepted SHA |

## 4. Priority 1 — `main.py` route ownership and duplicate routes

### 4.1 Current ownership defect

`main.py` is simultaneously the application factory, dependency container, error
middleware, authentication implementation, domain service adapter, SSE broker,
projection layer, command layer, and route owner. Router inclusion begins only near
line 69,232. Extracted routers therefore coexist with hundreds of routes that still
own the same domains in the entrypoint.

The correct retained responsibility is narrow:

| Artifact | Disposition | Canonical responsibility |
|---|---|---|
| `services/control-plane/bff/main.py` | **KEEP** | construct the FastAPI app, lifecycle hooks, shared middleware, and include domain routers |
| domain route bodies in `main.py` | **MIGRATE** | move intact behavior to the owning domain router, then delete the original body in the same wave |
| module-global mutable overlays in `main.py` | **REMOVE** or **MIGRATE** | delete shadow-only overlays; move valid state to the canonical domain store |
| generic compatibility route bundles | **REMOVE** after caller proof | they must not remain as catch-all implementations behind real routes |

### 4.2 Complete normalized collision inventory

Path parameter names are normalized because `/x/{id}` and `/x/{itemId}` match the
same requests. Line numbers refer to Pantheon source baseline `f4a14b2`.

The six active groups are Agora signal detail, Events SSE, Agora Journal patch,
generic Action submission, Agora signal feedback, and Ranking Formula creation. The
Events list plus all listed Evolution and Experiment groups are the fifteen groups
currently hidden by route-list pruning; they remain source-level duplicate owners.

| Method and normalized path | Registered handlers | Disposition and target |
|---|---|---|
| `GET /bff/agora/signals/{param}` | real detail at 24060; generic alias at 68119 | **REMOVE** generic alias; **KEEP/MIGRATE** real handler into Agora router |
| `GET /bff/events` | telemetry/read-store list at 57634; governance audit list at 61631 | **MERGE** semantics into one Events router; preserve required filters and one envelope |
| `GET /bff/events/stream` | authenticated/liveness stream at 56975; generic stream alias at 57659; empty generic alias at 68119 | **KEEP/MIGRATE** 56975 behavior; **REMOVE** the other two registrations |
| `GET /bff/evolution-programs` | service/read-store route at 57249; in-memory overlay route at 60917 | **MERGE** required response/filter behavior into one service-backed Evolution router; **REMOVE** overlay |
| `POST /bff/evolution-programs` | durable/read-store route at 57273; in-memory overlay route at 60949 | **MERGE**, with durable owner only |
| `GET /bff/evolution-programs/{param}` | 57309 and 61001 | **MERGE** into the same Evolution router |
| `PATCH /bff/evolution-programs/{param}` | 57333 and 61027 | **MERGE**; no BFF process-memory authority |
| `GET /bff/evolution-programs/{param}/runs` | 57371 and 61068 | **MERGE** |
| `GET /bff/evolution-programs/{param}/candidates` | 57395 and 61122 | **MERGE** |
| `POST /bff/evolution-programs/{param}/actions/{param}` | 57419 and 61157 | **MERGE** command behavior and retain one idempotency path |
| `GET /bff/experiments` | read-store route at 57453; compatibility/overlay route at 61192 | **MERGE** into one Research Experiments router |
| `POST /bff/experiments` | 57474 and 61222 | **MERGE**, with durable owner only |
| `GET /bff/experiments/{param}` | 57507 and 61277 | **MERGE** |
| `POST /bff/experiments/{param}/actions/{param}` | 57531 and 61303 | **MERGE** |
| `GET /bff/experiments/{param}/logs` | 57563 and 61336 | **MERGE** |
| `GET /bff/experiments/{param}/metrics` | 57586 and 61364 | **MERGE** |
| `GET /bff/experiments/{param}/artifacts` | 57609 and 61392 | **MERGE** |
| `PATCH /bff/agora/journal/{param}` | durable merge-patch route at 23205; generic echo stub at 68547 | **REMOVE** stub; **KEEP/MIGRATE** durable handler |
| `POST /bff/agora/signals/{param}/feedback` | durable feedback route at 24202; generic accepted stub at 68541 | **REMOVE** stub; **KEEP/MIGRATE** durable handler |
| `POST /bff/actions/{param}/{param}/{param}` | handlers at 62270 and 62311 both call the same helper but publish different operation metadata | **MERGE** route metadata into one registration, then **REMOVE** the duplicate |
| `POST /bff/ranking-formulas` | durable store route at 67887; generic create echo at 68520 | **REMOVE** generic route; **KEEP/MIGRATE** durable route |

For the six active groups, registration order makes the earlier Starlette route the
dispatcher while OpenAPI path generation can retain metadata from a later
registration. Counting only literal path strings would also miss groups whose path
parameter names differ.

The other fifteen groups are not clean: `_prefer_latest_bff_gap004_routes()` at
`main.py:61666-61727` rewrites `app.router.routes[:]` after registration and keeps
the last handler for a hard-coded set of normalized paths. It masks the duplicate
Evolution, Experiment, and Events families but runs before the final generic alias
bundle, so it cannot remove the six later collisions. Its route set also contains
Job paths, demonstrating that the mechanism is a compatibility preference table,
not domain ownership.

Required disposition for this pruning mechanism:

1. **MERGE** the required contract and durable behavior for all fifteen masked
   groups into their canonical routers.
2. **REMOVE** the replaced declarations in the same migration wave.
3. **REMOVE** `_BFF_GAP004_ROUTE_PATHS`, its normalization/method helpers, and
   `_prefer_latest_bff_gap004_routes()`.
4. **VERIFY** zero normalized duplicates only after all routers have been mounted;
   assert both runtime handler identity and OpenAPI uniqueness.

### 4.3 Route-family migration boundary

Route movement must be grouped by owner, not by whichever block is easiest to cut:

| Route family | Disposition | Target owner |
|---|---|---|
| auth/session/version/health | **MIGRATE** | `bff/app.py`, `bff/auth/`, and a small platform router |
| Persona/training/consultation | **MIGRATE** | existing Persona, Training, and Consultation client/router packages |
| research/experiments/alpha/search | **MIGRATE** | research domain router over research/search service clients |
| governance/deployment/evolution | **MIGRATE/MERGE** | separate domain routers; writes remain at owning services |
| runtime/capital/ranking | **MIGRATE** | Runtime and Capital routers over their owning services/stores |
| Management/loop/read models | **MIGRATE** | `management_read_models` plus a dedicated loop-truth router |
| Agora routes still in `main.py` | **MIGRATE** | existing `agora.router` subrouters; do not add another Agora façade |
| generic final aliases | **REMOVE** | only individually proven aliases survive, inside the owning router |

## 5. Priority 2 — `read_store.py` 457-method God class

### 5.1 What is mixed together

`read_store.py` has three adapter/store classes:

| Class | Lines | Methods | Disposition |
|---|---:|---:|---|
| `CanonicalSnapshotAdapter` | 402 | 14 | **VERIFY/MIGRATE** only datasets that still have a legitimate snapshot contract; remove generic cross-domain use |
| `ServiceBackedReadAdapter` | 987 | 20 | **MERGE** its HTTP/store selection into typed domain clients; remove string-dispatched generic ownership |
| `ReadSurfaceStore` | 13,498 | 457 | **MIGRATE** callers domain by domain, then **REMOVE** the class |

The same file also contains `_default_read_data()` from lines 3,676–7,383, about
3,708 lines of embedded defaults and fixture merging. Those fixtures are guarded by
`allow_local_snapshot_fallback`, which defaults false in the deployed BFF, but they
remain product code and are used extensively by tests.

The coupling is measurable: `main.py:1198` is the sole product constructor, but
`main.py` contains 631 direct `read_store.<attribute>` uses over 215 distinct
attributes. Another 162 test files construct `ReadSurfaceStore` directly. Fifty
public methods are command-shaped mutations, 49 methods invoke `_save`, 100 refer to
the generic service adapter, 94 refer to local fallback, and 125 are static/class
projection helpers. An immediate rename or physical split would preserve the same
God object behind more files.

### 5.2 Complete method-cluster disposition

The line ranges below partition all 457 methods; they are migration units, not new
runtime layers.

| Lines | Current cluster | Disposition and destination |
|---:|---|---|
| 7386–7584 | construction/configuration/parsing | **KEEP** the existing object only as a temporary delegating seam; **MIGRATE** parsing helpers with their owners |
| 7585–7612 | workflow/hook catalog | **MERGE** into the provider behind `console_gap/workflows_hooks.py` |
| 7614–8549 | dormant providers, Research OSS, OpenClaw snapshots | **MERGE** transport with `OpenClawOpsClient`; **MIGRATE** projections to a focused read model |
| 8551–8952 | Consultation discovery/projection | **MERGE** with `ConsultationServiceClient` and `ConsultationStore` |
| 8953–9551 | bootstrap, default backfill, JSON persistence, source arbitration | **REMOVE** production fixtures/fallback after explicit test injection; retain only during caller migration |
| 9552–9795 | Decision Journal | **VERIFY** durable owner, then **MIGRATE** to its dedicated store/service |
| 9797–10603 | legacy Agora signal/session/committee/insight/memory/audit | **MERGE** matching data with existing Agora stores; **MIGRATE** unmatched projections; remove local writes |
| 10604–12068 | Persona/Capital/Deployment/Runtime DTOs and writes | **MERGE** primitives with existing owners; **MIGRATE** pure projectors |
| 12074–12584 | ranking/rebalance/allocation/containment/league/Evolution | **MERGE** capital-owned behavior; **MIGRATE** BFF projections; **VERIFY** owners before overlay deletion |
| 12591–13430 | OODA/interventions/experiment-job-event compatibility/review queues | **MERGE** OODA with its store; **MIGRATE** queues to Management read models; remove route compatibility duplicates |
| 13437–14806 | research tickets/notes/evidence/insights/specifications | **MIGRATE** projections into Knowledge; **MERGE** primitive reads with Research/Memory owners |
| 14809–16511 | institutional memory/research/search/source operations | **MERGE** with existing Memory, Research, Search, and Source Ingestion clients/stores |
| 16517–17334 | incidents/postmortems/loops/governance/lineage/telemetry | **MERGE** primitive reads with owners; **MIGRATE** cross-domain composition to Management operations read models |
| 17340–18182 | Persona/training sessions/previews/capabilities | **MERGE** with Persona Registry and Training Session service |
| 18188–19724 | Consultation plus trainer controls | **MERGE** each half with its existing service after separating the mixed cluster |
| 19733–20397 | trainer replay and rapid evaluation | **MERGE** replay with Training Session; **VERIFY/MIGRATE** rapid-eval ownership |
| 20401–20459 | governance/alpha/skills/tools/MCP catalogs | **MERGE** into providers behind already-isolated routers |
| 20463–20883 | five Management composed read models | **MERGE first** into `management_read_models`; that router is their only product caller |

Confirmed source-wide zero-caller methods are `get_job_logs_bff` and
`get_evolution_decision`, so they are **REMOVE** candidates after a focused
reflection/reference test. `create_persona_binding`, `create_capital_pool`,
`get_loop_health_record`, `list_telemetry_events`, the experiment/job/event
compatibility block, and the always-empty `list_alpha_factory_cards` remain
**VERIFY** until their test-only or indirect callers have moved.

### 5.3 Required disposition

| Existing concern | Disposition | Canonical destination |
|---|---|---|
| embedded default records and fixture-pack merging | **MIGRATE** | test-only fixture builders under the test tree |
| `PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK` production escape hatch | **REMOVE** after test callers migrate | explicit test fixture injection; production has no seed fallback |
| generic dataset snapshot reader | **VERIFY** each dataset | keep only bounded historical/read-only contracts with a named owner |
| service URL and HTTP selection | **MIGRATE** | typed client in the domain package |
| Consultation access | **MIGRATE** | existing `ConsultationServiceClient` / Consultation store |
| Lifecycle projection access | **MIGRATE** | existing configured trade-journey projection reader |
| Agora reads and writes | **MIGRATE** | Agora package stores and routers |
| Governance/Deployment/Capital/Runtime reads | **MIGRATE** | corresponding service clients; no local shadow writes |
| BFF-owned ranking/allocation projections | **MIGRATE** | a named BFF projection repository, separate from cross-domain reads |
| process-local evolution/research/job overlays | **REMOVE** after durable-path parity | owning service or durable BFF projection |
| final `ReadSurfaceStore` compatibility object | **REMOVE** | route modules receive only the narrow ports they use |

The migration must not create a new 457-method `ReadStoreFacade`. The existing
`ReadSurfaceStore` may delegate temporarily while its 162 concrete test callers and
product routes move, but every migrated method is then deleted. Extract shared
source/provenance ports and pure projectors before domain clusters; otherwise a
mechanical file split only creates a distributed God object.

## 6. Priority 3 — frontend `bff` / `bff-v1` / `v5` dependency direction

### 6.1 Current graph

The production tree contains 20 files under `src/lib/bff`, 96 under
`src/lib/bff-v1`, and 44 under `src/lib/v5`. Static imports include:

- 17 `bff-v1 → bff` production edges;
- 13 `bff → bff-v1` production edges;
- 5 `v5 → bff-v1` production edges; and
- 4 `bff-v1 → v5` production edges.

The three directories are therefore version labels, not dependency layers. They
form a cyclic quotient/layer graph, although they do not form an additional
cross-directory file SCC. The exact production runtime SCC is:

`bff-v1/index.ts` → `legacy.ts` → `runActionSafe.ts` → `bff-v1/index.ts`.

`runActionSafe.ts` value-imports `tryRunAction` from its own package barrel; it must
import `./writes` directly. The `client`/`liveStatus` and Management
read/data-source pairs are TypeScript type-only SCCs. Their shared `BffMode` and
`ManagementListMeta` types move to existing lower-level type/runtime modules; they
do not justify a new shared-types wrapper. The strict live build currently passes
but warns that the runtime SCC can split mutually dependent modules into different
chunks and break execution order.

### 6.2 Required disposition

| Surface | Disposition | Target |
|---|---|---|
| `bff-v1` public API surface, `writes.ts`, and `v5.ts` | **KEEP** | existing sole production network/auth/SSE/read/write/path/receipt owner |
| domain clients currently split by version directory | **MERGE** | existing `bff-v1` domain modules; no new API directory or wrapper |
| package-internal imports from `@/lib/bff-v1` barrel | **MIGRATE** | direct relative module imports; public UI imports may remain until their domain moves |
| `runActionSafe` self-barrel edge | **MIGRATE** | direct `./writes` import; retain the function and its eleven live caller files |
| `bff/liveRead.ts` and `bff/liveTransport.ts` | **MERGE** | existing `bff-v1/liveTransport.ts`, `liveStatus.ts`, and runtime environment modules |
| `bff/client.ts` | **MIGRATE** | move its sole external OODA read to `bff-v1/managementConsoleReads.ts`, then delete |
| `bff/runAction.ts` and `bff/commandClient.ts` | **MERGE** | retain needed actions/command adaptation in `bff-v1/writes.ts`, then delete old modules |
| `bff/realtime.ts` and `v5/events.ts` transport behavior | **MERGE** | existing `bff-v1/sse/bridge.ts` / `liveSse.ts`; keep only pure event DTOs in `v5` |
| `bff/v5.ts` | **MERGE** | live behavior into `bff-v1/v5.ts`; pure transforms remain in `v5`; fixture behavior leaves production |
| `v5` network/mutation imports | **MIGRATE** | calls move upward to `bff-v1`; `v5` becomes pure DTO/view-model/adapter code |
| `bff-v1/legacy.ts` and 53 live `bff` seed-accessor consumers | **MIGRATE** | move by entity family; delete only at zero callers |
| `bff-v1/writeFallback.ts` local-success path | **REMOVE** | `PersonaOnboarding` uses canonical writes and renders truthful failure/unavailability |
| production imports of mock mutations, write overlay, and unconditional mock persistence | **MIGRATE** | typed `bff-v1` writes; mock bootstrap is isolated to a compile-time demo/test entry |
| `bff/types.ts` | **VERIFY** | 104 non-test files still import it; replace per domain before deletion |
| generated Agora/type contract files | **VERIFY** | retain as generated contract artifacts if generation and consumers are proven |
| legacy `bff` implementation files and empty version barrels | **REMOVE** only after zero importers | existing canonical `bff-v1` owner and pure `v5` remain |

`bff-v1` already contains the stricter live-write refusal and the Workshop client;
it is the least-change canonical owner. No fourth API directory is allowed.
Compatibility mapping belongs in the one existing domain client that needs it and
is deleted with its last caller. Strict production reachability must also prove that
`src/main.tsx` cannot pull in `@/mocks/seed`, mock mutation, persistence, overlay, or
synthetic-success code.

## 7. Priority 4 — Management loop truth has multiple authorities

### 7.1 Current sources

`GET /bff/v5/loop-health` currently combines:

1. `docs/deployment/loop-catalog.registry.json` for twelve static loop identities,
   owner declarations, and expected controller contracts;
2. `services/loop-control/LoopControllerStore` PostgreSQL rows;
3. `ReadSurfaceStore` file/snapshot datasets named `loop_health.json` or
   `loop-health.json`; and
4. `DownstreamHealthMonitor`, which can synthesize Loop 12 and overlay component
   failures onto any loop row inside the BFF request path.

The current projection also places one composite overlay in the same returned
`items` array, so the unit contract expects 13 rows while deployed twelve-loop
acceptance filters back to the twelve canonical loop IDs. That disagreement is a
truth-shape defect, not an extra loop.

The catalog currently declares only three controller contracts as implemented and
nine as not implemented. Static catalog rows are still projected for all twelve,
which is appropriate for inventory but cannot become live truth.

### 7.2 Canonical split

| Mechanism | Disposition | Authority after cleanup |
|---|---|---|
| loop catalog | **KEEP** | static identity, owner, expected contract, and classification only |
| PostgreSQL `loop_controller_records` via `LoopControllerStore` | **KEEP** | sole current runtime/controller truth store |
| owner controller writers | **MIGRATE/complete** | each loop owner writes its own fenced record through `LoopControllerWriter` |
| BFF local `loop_health` snapshot fallback | **REMOVE** from operator truth | historical evidence may remain archived but is not joined as current state |
| downstream monitor synthetic Loop 12 record | **MIGRATE** | Loop 12 owner publishes a real controller record |
| downstream monitor overlays for Loops 1–11 | **REMOVE** from loop truth | remain raw component diagnostics at `/bff/v5/downstream-health` |
| `loop_inventory.py` projection | **MIGRATE** out of `main.py`, otherwise **KEEP** | pure join of catalog metadata plus current controller rows |

Component health is evidence an owning controller may consume; it is not itself
authority to mutate another loop's state. If a controller row is absent, stale,
fenced, or malformed, Management must display `unobserved`/`degraded`, not manufacture
a replacement from catalog or BFF process state.

### 7.3 Concrete authority violations

- `main.py:63459-63468` calls `publish_loop_12_controller_truth()` while serving a
  read request. **REMOVE** the read-side write; Loop 12 publication belongs in its
  background owner through `LoopControllerWriter`.
- The current monitor payload omits required conformance fields and uses a desired
  state shape that `LoopControllerStore` rejects. **MIGRATE** it to the existing
  shared conformance/writer contract; do not loosen validation or add a second Loop
  12 row format.
- `main.py:63470-63524` can mutate an accepted row or manufacture rows for Loops
  1–11 from BFF dependency probes. **REMOVE** this authority. Probe failures remain
  supplemental downstream-health evidence.
- `loop_inventory.py` currently accepts any nonblank controller name when the
  catalog has no controller identity, uses one fixed 900-second freshness window,
  and does not require exact deployment SHA, authoritative desired/actual state, or
  terminal/next receipt. **MERGE** the already-specified admission predicates into
  this one projection; do not add another validator.
- `/loop-health` currently mixes the composite overlay into the twelve-loop array.
  **MIGRATE** overlay visibility to a separate noncanonical section or inventory
  surface; the canonical health array contains exactly twelve stable IDs.

### 7.4 Frontend truth-contract mismatch

The live frontend fetches only `/bff/v5/loop-health`, but its types and view still
expect retired `current_maturity` / `target_maturity` fields and a top-level
`operator_truth_source`. The backend intentionally emits `runtime_maturity` and
nests accepted operator truth under `live_status.operator_truth` and the evidence /
truth-source packets. The current frontend calculation consequently reports zero
live loops and renders rows degraded even when backend admission succeeded.

The disposition is **MIGRATE** the existing frontend DTO/view to the actual nested
backend contract and **REMOVE** the retired fields. Re-adding static maturity or a
duplicate top-level backend alias would create another truth source and is
explicitly rejected.

## 8. Priority 5 — `services/runtime-manager` dual-directory ownership

The two directories should not be blindly collapsed because they contain different
valid concepts:

- `services/runtime-manager/` is the documented, Compose-deployed HTTP and service
  owner on port 8081. `RuntimeManagerService` is the actual writer.
- `services/execution/runtime-manager/` owns the `RuntimeBinding` contract/schema,
  kill-switch kernel, and paper fleet reconciler.

The defect is the second executable manager in
`services/execution/runtime-manager/runtime_manager.py`. It defines another
`RuntimeManager` with deploy/pause/resume/replace/rollback behavior. No production
container imports it, but two E2E tests load it directly by path, so those tests
exercise a non-deployed mechanism.

| Artifact | Disposition | Result |
|---|---|---|
| `services/runtime-manager/main.py` | **KEEP** | one deployable HTTP owner |
| `services/runtime-manager/service.py::RuntimeManagerService` | **KEEP** | one runtime mutation implementation |
| execution `runtime_binding.py`, schema, contract, authority matrices | **KEEP** | importable domain contract/kernel |
| execution `kill_switch_controller.py` | **KEEP/MIGRATE** | retain kernel; move to an importable package path without `sys.path` hacks |
| execution `runtime_manager.py::RuntimeManager` | **MERGE** unique tested semantics into service, then **REMOVE** | no second manager |
| two E2E tests that load the old file | **MIGRATE** | exercise HTTP/service/client path used in Compose |
| execution Runtime Manager Dockerfile that launches only `paper_fleet_reconciler.py` | **MIGRATE** | explicitly named paper-fleet worker package/image |
| `paper_fleet_reconciler.py` behavior | **KEEP/MIGRATE** | keep behavior, separate its worker ownership from Runtime Manager naming |

The current `sys.path` and `importlib` workarounds are symptoms of the hyphenated
directory boundary. Cleanup creates one normally importable runtime-control domain
package; it does not create a third implementation.

## 9. Priority 6 — Agora Workshop router, store, and page

### 9.1 Backend

The Workshop package is the correct owner and must be retained. Its internal
boundaries are not clean:

- `router.py`: 4,073 lines, 18 route registrations, SSE state, cards, readiness,
  reconstruction, versioning, research, consultation, and conclusion;
- `store.py`: 3,510 lines; a 30-method memory store, a 33-method production Postgres
  store, and a second bootstrap-only `PostgresStrategyWorkshopStore`; and
- Research, Interaction, and Trading Room code imports private functions from the
  Workshop router (`_ws_publish` and `_build_readiness_assessment`).

| Concern | Disposition | Target |
|---|---|---|
| `agora.strategy_workshop` package | **KEEP** | sole Workshop domain owner |
| route composition | **MIGRATE** | small package router that includes session, version, execution, and stream routers |
| `_ws_publish` / replay state | **MIGRATE** | public Workshop event-stream module; all publishers use it |
| readiness/card computation | **MIGRATE** | domain projection module; Trading Room imports that public API |
| reconstruction/research/consult/conclude orchestration | **MIGRATE** | operation/application services already present or extracted once |
| `PostgresWorkshopStore` | **KEEP** | sole production Workshop persistence implementation |
| `PostgresStrategyWorkshopStore` | **MERGE** bootstrap contract into canonical store/schema module, then **REMOVE** | one Postgres store |
| `MemoryWorkshopStore` | **KEEP** only as explicit test adapter | never a silent production fallback |

### 9.2 Frontend page

`StrategyWorkshopPage.tsx` is about 2,054 lines and combines route/query state,
server mutations, streaming, timeline/cards, version selection, research,
consultation, and conclusion UI. The existing URL router and
`bff-v1/agora/workshops.ts` client are already the canonical owners and are **KEEP**.
The client already owns the Workshop HTTP, exact ETag, durable event, lifecycle and
SSE contracts; no controller/store/client wrapper is needed.

The minimum clean split is:

| Current concern | Disposition | Target |
|---|---|---|
| list/create rendering at `StrategyWorkshopPage.tsx:254-441` | **MIGRATE** | `WorkshopListView`, receiving `onOpenWorkshop(id)` |
| session rendering at `:443-2036` | **MIGRATE** | `WorkshopSessionView`, rendered only for the URL-selected ID |
| root switch at `:2038-2054` | **KEEP** | thin collection/detail composition |
| local `selectedWorkshopId` and auto-selection | **REMOVE** | router-owned `/agora/strategy-workshop/:id` identity |
| duplicate `useServantWorkshopContext` shell GET in `TradingDeskLayout.tsx` | **REMOVE** | ID-only/presentation-only drawer or data passed from the URL-owned session |
| pure reducers/helpers embedded in the page | **MIGRATE** | optional pure session-model helper; no network or store authority |

The current message action also treats the canonical Workshop write/reconstruction
and optional Persona daily-interaction submission as one transaction. A Workshop
message can already be durably stored before Persona submission fails, yet the UI
then labels the whole send failed. **MIGRATE** ETag → message POST → durable event
readback into the existing `workshops.ts` operation, retain reconstruction as its own
receipt, and make Persona consultation an explicit secondary action with a separate
receipt. Keep active `dailyInteractions` behavior; remove only its authority to gate
the base Workshop composer.

Two remaining behaviors are **VERIFY**: initial requests currently swallow errors
into null/empty, and the SSE effect depends on `lastEventId` that changes for every
event. Tests must prove truthful unavailable state and no reconnect-per-event before
the page split is accepted.

## 10. Priority 7 — Source Ingestion `main.py`

`services/source_ingestion/main.py` is 4,127 lines with 68 routes and 27 Pydantic
request/model classes. It also constructs every store and manager as module globals
and contains the full ingest post-processing pipeline.

Most domain behavior already has a home: `active_universe.py`,
`connector_coverage_matrix.py`, `connector_definitions.py`, `controller_state.py`,
`controller_worker.py`, `gap_report.py`, `ingest_manager.py`, `policy_registry.py`,
`registry/proposals.py`, `source_health.py`, `source_management_commands.py`,
`source_management_store.py`, and `scheduler.py`.

| Current block | Disposition | Target |
|---|---|---|
| FastAPI creation and health registration | **KEEP** | small app factory/entrypoint |
| env/path parsing and object construction | **MIGRATE** | explicit Source Ingestion runtime/composition module |
| request/response models | **MIGRATE** | `api_models.py`, grouped by route family |
| connector/job/record/frontier/DLQ/schedule/audit routes | **MIGRATE** | ingest operations router |
| registry/policy/catalog/active-universe/controller/provisioning routes | **MIGRATE** | catalog/controller router |
| source-change proposal routes | **MIGRATE** | proposal router over existing proposal store/adapter |
| health/usage/retirement/coverage/alerts/gap-report routes | **MIGRATE** | source observability router over existing modules |
| Management connector/command/source routes | **MIGRATE** | source-management router over existing command engine/store |
| `_run_job` and post-processing helpers | **MERGE** | one application service over Scheduler, evidence, market storage, and distillation queue |
| original route/helper bodies in `main.py` | **REMOVE** in the same migration wave | no duplicate route implementation remains |

The result should be a composition root, not five routers that import globals back
from `main.py`.

## 11. Priority 8 — frontend dead NL and stub surfaces

Production caller inspection found these zero-caller UI artifacts:

| Artifact | Current caller evidence | Disposition |
|---|---|---|
| `src/management/components/nl/NlAssistantDrawer.tsx` | symbol occurs only in its own definition | **REMOVE** |
| `src/management/pages/oversight/NlConsole.tsx` | `ManagementNlConsole` is referenced only by the dead `_stubs.tsx` | **REMOVE** |
| `src/management/pages/oversight/_stubs.tsx` | no importer; its App-compatibility comment is stale | **REMOVE** |
| `src/lib/bff-v1/managementNl.ts` | deprecated fixed responder imported only by the two dead UIs | **REMOVE** |
| fixed responder/types in `src/lib/v5/management/nl.ts` | only `ManagementNlContext` still has a live consumer | **MIGRATE** that interface into its active hook/type owner, then remove fixed behavior |
| `useManagementNlContext.ts` | active in `AgentPanelBody`; no fixed responder needed | **KEEP**, while removing its stale `/management/ask` label |
| `v5/management/index.ts` NL re-export | only forwards the dead module | **REMOVE** the export; **VERIFY** the whole barrel before deleting it |
| old Management NL provider/gateway env flags and `mgmt.nl.*` / `stubNotice` locale keys | reachable only from dead fixed UI | **REMOVE** |

The canonical Management AI surface is **KEEP**: `managementAi.ts` calls and streams
`POST /bff/management/nl/ask`, and `AgentPanelBody` is mounted through the floating
panel in `ManagementLayout`. `/management/ask` already redirects rather than mounting
the old console. File deletion requires typecheck, production build, route smoke,
zero-import proof, and a browser check that the floating live panel reaches the BFF
or displays truthful provider degradation.

Other modules with zero production TypeScript importers—Agora dashboard, research,
servant, and SSE payload helpers—remain **VERIFY** because generated-contract and
test ownership has not yet been proven. Active files containing the word `stub` are
not removed merely by name.

## 12. Priority 9 — container entrypoint and exact deployment tests

### 12.1 Proven runtime failure

The root-stack `pantheon-agora-interaction-worker-1` was observed in `restarting`
state with restart count 438. Its repeated error is:

```text
ModuleNotFoundError: No module named 'services'
```

The chain is the shipped Compose command
`python scripts/run_agora_interaction_worker.py` → `agora.interaction.worker` →
`openclaw_ops_client` → `services.persona.runtime_profile`. The launcher adds only
`services/control-plane/bff` to `sys.path`, not the repository root.

The same launcher tries `from store import FastBffReadStore`, but no such symbol or
module exists in the repository. It catches every exception and installs a
`MinimalReadStore` that always returns an empty Persona list. That fallback would
make a successfully started worker functionally incomplete and falsely healthy.

### 12.2 Deployment gap

`deploy_nonprod_vm.sh` validates a `REQUIRED_LOOP_WORKERS` list but omits
`agora-interaction-worker`. A BFF-only deployment rebuilds and recreates
`operator-bff` and the lifecycle projector, not the worker that shares the BFF
image. The hosted manifest can consequently be `accepted` for an exact FE/BFF pair
while the newly introduced worker is on another identity or restarting.

| Mechanism | Disposition | Required result |
|---|---|---|
| worker behavior | **KEEP** | durable interaction execution remains |
| current script-path import bootstrap | **MIGRATE** | package-safe launcher that works from an arbitrary working directory |
| nonexistent `FastBffReadStore` branch | **REMOVE** | explicit typed Persona dependency; no empty fallback |
| `MinimalReadStore` catch-all | **REMOVE** | startup/readiness fails if required Persona discovery cannot be constructed |
| Compose command and healthcheck | **VERIFY/MIGRATE** | both invoke the same validated package entrypoint |
| required-worker manifest | **MIGRATE** | include Agora interaction worker under Loop 5 |
| BFF-only deploy component | **MIGRATE** | build/recreate/rollback BFF-owned worker with operator BFF |
| post-deploy check | **MERGE** | one reusable gate checks existence, running/health state, and exact OCI revision for every required service |
| hosted acceptance | **MIGRATE** | accepted only after required component receipt passes; FE/BFF pair evidence remains intact |

The minimum test pyramid is: subprocess entrypoint test from a foreign CWD, built
image command/healthcheck smoke, Compose required-service contract, root and BFF
deploy-script contract tests, and a hosted exact-revision/runtime-state gate.

## 13. Additional large-file watchlist

These files are not added to the first cleanup program solely because of size:

| Artifact | Observation | Disposition |
|---|---|---|
| `services/control-plane/persona/agent_usability_validation.py` | about 23,384 lines; specialized validation program | **VERIFY** responsibilities and generation status before any split |
| Agora Trading Room router | about 3,683 lines | **VERIFY** after Workshop helper extraction removes its private dependency |
| paper runtime and OpenClaw adapter entrypoints | about 3,600 lines each | **VERIFY** caller/ownership cohesion after priorities 1–9 |
| frontend localization catalogs and generated Agora types | large but data/contract shaped | **KEEP/VERIFY**, not automatic cleanup targets |

This watchlist prevents the first program from becoming an unbounded rewrite while
still recording likely follow-up evidence work.

## 14. What must not be built

- another `main_v2.py`, `read_store_v2.py`, API `v6`, or Workshop façade;
- a compatibility router that keeps both old implementations alive indefinitely;
- a Management truth database beside `loop_controller_records`;
- a BFF overlay that pretends unavailable owner data is live;
- a generic repository with hundreds of string-selected datasets;
- tests that import an old implementation directly instead of the deployed owner;
- healthchecks that return success before importing or constructing required code;
- fixture-backed production success; or
- security, compliance, or enterprise controls unrelated to making the current
  product paths clean and operational.

## 15. Exit criteria for the cleanup program

The architecture cleanup is complete only when all of the following are true:

1. the normalized route uniqueness test reports zero collisions;
2. `main.py` is an app composition root and no domain router imports it;
3. `ReadSurfaceStore` and production fixture seeding are removed;
4. the frontend dependency graph has no prohibited inverse edge or runtime SCC;
5. Management loop truth joins only catalog metadata and current controller rows;
6. E2E tests and Compose use one Runtime Manager implementation;
7. Workshop has no cross-router private imports and one production Postgres store;
8. Source Ingestion `main.py` owns no domain pipeline or route implementation;
9. zero-caller NL/stub files and their stub-only helpers are deleted;
10. every required container entrypoint starts, reports truthful health, and matches
    the accepted source revision; and
11. existing twelve-loop, Management, Agora, and cross-loop functional tests pass
    without fixture or duplicate-path substitution.

Passing architecture tests alone does not prove all twelve loops are functionally
closed. The final functional E2E remains a separate acceptance gate after cleanup.
