# System Design — Pantheon Structural Closure

Status: implementation-planning design; no production or capital authorization

Normative architecture: [SA.md](SA.md)

Source audit: [REPORT.md](REPORT.md)

## 1. Delivery definition

This program replaces incomplete composition and competing state paths with
canonical domain ownership. Completion includes source, migration, deletion,
tests, exact-pair deployment and hosted same-run evidence. Adding APIs without
retiring the replaced implementation is incomplete.

## 2. Required starting conditions

Before any implementation packet begins:

1. fetch current `origin/dev` in both Pantheon and `execute-plans`;
2. use clean task worktrees and record exact baseline SHAs;
3. inventory active tasks/PRs touching the declared files;
4. freeze the current HTTP DTO and domain event compatibility snapshots;
5. produce the machine-readable ownership and caller inventories described
   below; and
6. keep safe writes, external providers and real-capital execution disabled.

No implementation packet may edit canonical task/queue JSON by hand.

## 3. Mandatory design artifacts

Wave 0 produces these reviewed artifacts before behavior changes:

### 3.1 Aggregate ownership registry

Proposed path:
`docs/02-architecture/product-aggregate-ownership.yaml`

Minimum schema:

```yaml
version: 1
aggregates:
  persona:
    command_owner: persona_application
    store_owner: persona_registry
    read_projection: management_persona_projection
    event_subjects: [persona.lifecycle.v1]
    bff_routers: [personas]
    forbidden_writers:
      - _PERSONA_BFF_OVERLAY
```

Validation fails when:

- an aggregate has zero or multiple command/store owners;
- a BFF router is declared as store owner;
- two workers claim the same subject without partition/lease policy; or
- a forbidden writer appears in production reachability.

### 3.2 Mutation-to-owner inventory

Machine-readable columns:

```text
route, command, router, application_owner, store_owner, table_or_stream,
outbox_subject, idempotency_scope, readback_projection, legacy_path,
legacy_removal_wave
```

Every non-GET route must appear exactly once. CI compares mounted routes with
the inventory and rejects omissions or duplicate authorities.

### 3.3 Worker ownership inventory

For each Compose worker/scheduler record:

```text
service, profile, input_subject, durable_consumer, lease_key,
partition_policy, output_subject, retry_policy, dlq, readiness_probe
```

Two consumers may share a subject only when an explicit consumer group and
partition policy prove they are replicas of the same owner.

### 3.4 Symbol disposition inventory

Classify all 208 duplicate-definition groups and the 17 unreachable tails:

```text
symbol, locations, canonical_owner, disposition,
production_callers_before, production_callers_after, deletion_wave
```

Allowed dispositions: `KEEP_OWNER`, `MOVE_SHARED_VALUE`, `DELETE_DUPLICATE`,
`DELETE_DEAD`, `TEST_ONLY`. `COMPATIBILITY_FOREVER` is forbidden.

## 4. Composition-root redesign

### 4.1 End state

`main.py` retains only:

- configuration loading;
- concrete dependency construction;
- router factory invocation and mounting;
- FastAPI lifecycle/startup/shutdown;
- middleware and global exception registration; and
- health/readiness aggregation over injected components.

It must not define domain DTO projection, lifecycle transitions, persistence,
overlay mutation, command execution or domain-specific error mapping.

### 4.1A Import and package normalization

The current production tree still mutates `sys.path` 18 times and uses dynamic
namespace lookup. Normalize one import root through container/test invocation,
then migrate imports without introducing a parallel package tree.

Order:

1. record every executable entrypoint and its current working directory;
2. make those entrypoints install/use one BFF package root;
3. convert domain imports to stable absolute or package-relative imports;
4. remove runtime `sys.path` mutation from domain/router modules;
5. replace `globals()` dependency lookup with explicit injection;
6. delete namespace-copy forwarding such as `globals()[name] = value`; and
7. retain executable-boundary shims only until all recorded entrypoints pass,
   then delete them.

Do not relocate all code into a second package and leave forwarding files in
the old tree. That would reproduce the current duplicate-module problem.

### 4.2 Router dependency design

Each domain exposes a dependency record rather than reaching a global object.
Example shape, not a mandated class name:

```python
@dataclass(frozen=True)
class PersonaRouterDependencies:
    queries: PersonaQueries
    commands: PersonaCommands
    events: PersonaEvents
    authorize: AuthorizeRequest
```

Rules:

- interfaces live with the consuming domain;
- concrete adapters live with the owner/integration;
- router factories receive dependencies explicitly;
- no `get_read_store()` closure over global state;
- no generic `execute(name, payload)` command method;
- test doubles implement only the typed domain contract.

### 4.2A Router decomposition

Large router factories are split by resource/use-case while sharing one domain
dependency record. Suggested boundaries:

```text
personas/routes/{collection,detail,lifecycle,provisioning,events}.py
research/routes/{tickets,experiments,runs,artifacts,logs}.py
agora/trading_room/routes/{sessions,decisions,events}.py
agora/research/routes/{plans,runs,candidates}.py
strategies/routes/{collection,lifecycle,ranking}.py
```

Each sub-router may perform HTTP parsing, authorization invocation, DTO
translation and status mapping. Business branching, persistence and retries
belong to application use cases. A parent router only includes sub-routers; it
does not proxy their symbols or duplicate handlers.

Maintainability guardrails for changed code:

- a route handler delegates one application use case and contains no store
  transaction;
- a router factory does not exceed a reviewed bounded size (initial target 300
  lines) unless an explicit exception explains cohesion;
- an application use-case function has one transaction/command objective;
- dependency records remain domain-specific; and
- no wildcard imports, dynamic namespace copies or service locators.

The line target is a review signal, not a reason to create meaningless files.

### 4.3 Read/query contracts

The six current `ReadSurfacePorts` domains are decomposed into domain protocols.
Queries return a typed result envelope:

```text
data | unavailable
source_owner
source_version
observed_at
freshness
degradation_reason
```

Query ports never expose `create_*`, `update_*`, `record_*`, `patch_*` or
`put_*` methods. This prevents recurrence of the current mixed read/write
facade.

### 4.4 Command contracts

Commands return canonical receipts:

```yaml
CommandReceipt:
  command_id: string
  aggregate_type: string
  aggregate_id: string
  aggregate_version: integer
  status: accepted|completed|rejected
  event_id: string|null
  correlation_id: string
  owner: string
  committed_at: datetime|null
```

An `accepted` asynchronous command requires a durable command/outbox row. A
process-local object does not qualify.

### 4.5 Specific missing-method dispositions

| Current call | Correct design |
|---|---|
| `create_runtime_binding` | typed Runtime Manager command port |
| `create_deployment_plan` | Deployment application service, transaction plus outbox |
| `update_persona` | Persona application command with optimistic version |
| `create_experiment_bff` | Research orchestrator submit command |
| experiment list/artifacts/logs/metrics | Research query port |
| `get_job_logs_bff` | owning Job query/log port |
| `create_research_ticket` | Research intake command |
| `record_agora_audit_event` | canonical audit/event owner, not read facade |
| `record_sponsor_decision` | selected governance/decision owner |
| ranking formula create/patch | keep deprecation response; delete unreachable body |

No row is implemented by adding it to `ReadSurfacePorts`.

## 5. Durable-state convergence

### 5.1 Overlay migration protocol

Apply separately to Persona, Strategy, Incident, Job and Ranking:

1. identify authoritative schema and store owner;
2. stop new overlay writes in shadow mode while keeping owner writes canonical;
3. backfill only records missing from the canonical store with source and
   checksum metadata;
4. compare canonical query results with legacy projections for bounded time;
5. prove restart and multi-replica consistency;
6. switch reads to owner projection;
7. delete overlay definitions, mutation helpers and tests that reach them; and
8. add forbidden-symbol/import checks.

There is no reverse fallback after Step 6. Rollback deploys the previous exact
release and database-compatible schema; it does not re-enable dual writes.

### 5.2 Backfill requirements

- idempotent and resumable cursor;
- tenant-scoped transaction boundaries;
- source record checksum and timestamp;
- conflict report, never last-write-wins guessing;
- dry-run counts before mutation;
- no deletion of canonical rows;
- post-backfill fresh-reader parity; and
- redacted evidence with counts/digests, not product content.

### 5.3 Decision Journal convergence

Wave 0 must decide the owner using a reviewed scorecard:

| Criterion | Required |
|---|---|
| transaction and optimistic concurrency | yes |
| append-only history/audit | yes |
| tenant/user isolation | yes |
| outbox or domain event support | yes |
| Agora API contract coverage | yes |
| Governance consumer coverage | yes |
| restart/fresh-reader behavior | yes |

Implementation sequence:

1. extend the selected owner only for missing domain capabilities;
2. point Agora and Governance consumers to it through typed adapters;
3. migrate existing rows with immutable source IDs;
4. run read parity and single-write assertions;
5. remove the unselected implementation, schema bootstrap and tests; and
6. assert zero production callers and zero table writes for the retired path.

Do not build a journal-to-journal replication bridge.

## 6. Agora design

### 6.1 Interaction lifecycle

```text
POST workshop message
  -> transaction: request + outbox
  -> durable worker claim/lease
  -> terminal interaction result
  -> research/dataset handoff event
  -> SSE event with durable cursor
  -> fresh GET reconstruction
```

Required properties:

- idempotency scoped by tenant, session and client key;
- claim timeout and retry count persisted;
- terminal failure and DLQ visible to Management;
- replay cursor survives BFF/worker restart;
- private content stored only in the existing protected content owner; and
- no synchronous fake completion when the worker is absent.

### 6.2 Research provenance

The research execution owner emits:

```yaml
ResearchExecutionReceipt:
  receipt_id: string
  run_id: string
  executor: string
  mode: real|simulation
  backend_reference: string|null
  artifact_digest: string|null
  correlation_id: string
  completed_at: datetime
```

Agora resolves the receipt by `run_id` and verifies owner, correlation and
terminal state. Public request payloads cannot set `has_real_receipt` or an
equivalent trust bit. Unknown receipt version returns `unavailable`.

### 6.3 Candidate and decision handoffs

One terminal research result may create one candidate admission per
idempotency identity. One Trading Room decision transaction writes:

- canonical DecisionEvent;
- journal/audit reference; and
- exactly one outbox handoff to the selected policy or consultation owner.

Retries reuse the same IDs. Tests assert no second decision or handoff.

### 6.4 Performance suggestions

Attach `PerformanceSuggestionProducer` to the canonical evaluation/telemetry
consumer that owns the input event. Do not add a new scheduler. The consumer
persists the suggestion in the selected performance store and emits its
read-model event. The BFF only queries it.

### 6.5 SSE ownership

Keep domain-specific Agora and Deployment SSE routers. The generic event router
may provide aliases only when composition explicitly enables them. Add a
parameterized composition test proving exactly one handler per normalized
method/path under every supported flag combination.

## 7. Management and Management AI design

### 7.1 Management projection

Management uses purpose-built query models assembled from owner projections,
not arbitrary store access. Each row contains:

```yaml
ManagementObservation:
  subject_type: string
  subject_id: string
  status: string
  owner: string
  source_kind: live|replayed|backfill|unavailable
  source_version: string|null
  observed_at: datetime|null
  freshness_seconds: number|null
  degradation_reason: string|null
  correlation_id: string|null
```

Unavailable domains remain rows with explicit degradation. They are not
silently omitted and never populated from seed data in strict-live mode.

### 7.2 Twelve-loop truth projector

Persist a projection keyed by `(release_id, correlation_id, loop_id)`. The
projector consumes existing canonical receipts and records:

```yaml
LoopObservation:
  loop_id: integer
  correlation_id: string
  stimulus_id: string
  terminal_id: string|null
  terminal_status: string
  next_consumer_receipt_id: string|null
  owner: string
  release_id: string
  observed_at: datetime
  freshness_status: fresh|stale|unavailable
  provenance: live|replay|backfill
```

Rules:

- an absent next receipt means `open`, not complete;
- backfill never overwrites a newer live observation;
- static registry data supplies label/order only;
- all writes are idempotent by event/receipt identity; and
- rebuild output must equal incremental output.

### 7.3 Management AI boundary

`POST /bff/management/nl/ask` may:

- query Management projections;
- explain degradation with source/timestamp;
- propose a typed paper-safe product command; and
- execute only after existing confirmation/authority checks.

It may not:

- read or write repository files;
- create SA/SD/task packets;
- mutate supervisor/task state;
- execute shell or deployment commands; or
- infer readiness from development-tooling state.

Provider failure returns a typed degraded diagnostic. It does not disable
ordinary Management queries.

## 8. Dead-code and duplicate removal

### 8.1 Unreachable tails

Delete the 17 audited tails. Preserve only the minimal explicit deprecation
response where the public route still exists. Tests must assert the response,
not patch or exercise the dead implementation.

### 8.2 Copied definitions

Process the 208 groups in dependency order:

1. identity/auth/error value types;
2. surface status and DTO projection helpers;
3. Persona/Management domain behavior;
4. remaining small utilities.

Cross-domain pure value objects may move to existing `core` or a specifically
named module. Domain behavior moves only to its domain owner. A generic
`shared.py`, `utils2.py`, `manager.py` or `facade.py` is rejected.

### 8.3 Reverse dependency

Move `_surface_degradation_reason` to the existing typed surface-status owner
or define the behavior within Deployment if it is domain-specific. Then remove
`deployment/service.py -> main.py`. Add an import-boundary test forbidding any
production import of the composition root.

### 8.4 Compatibility budget

Every retained adapter must declare:

```text
owner, sole caller, reason, introduced_at, removal_condition, removal_wave
```

CI fails new undeclared compatibility adapters. Wave 7 removes those whose
conditions are satisfied.

### 8.5 Test architecture migration

The audit found 218 tests importing `main`. Classify them into:

1. **composition tests** — may import `bootstrap.app`; keep a small allowlist;
2. **router contract tests** — construct a router with typed doubles;
3. **application tests** — invoke a use case with domain port doubles;
4. **adapter integration tests** — use the real database/message dependency;
5. **hosted tests** — call the deployed HTTP surface only.

Migrate categories 2–4 away from `main` and remove `sys.path` mutation plus
global monkeypatching. The test suite gets common typed fixtures per domain,
not one universal fake store. Test collection/import time and per-file runtime
are recorded so the current silent timeouts become visible regressions.

### 8.6 Maintainability gates

Add repository checks for changed BFF code:

- dependency-direction/import boundary violations;
- production `sys.path` mutation outside a temporary executable allowlist;
- `globals()`/namespace forwarding in domain/router code;
- direct store access from routers;
- direct `main` imports outside composition smoke tests;
- new top-level mutable command/idempotency state;
- duplicate normalized route ownership;
- duplicate AST bodies above an agreed minimum size; and
- test collection or focused-file timeout budgets.

Metrics are trend gates during migration. Baselines may decrease wave by wave
but must never increase. Final closure removes temporary allowlists.

## 9. Frontend strict-live design

Changes occur only in the separate `ajoe734/execute-plans` repository.

Required build contract:

```text
VITE_BFF_MODE=live
VITE_BFF_FALLBACK=strict
VITE_BFF_REAL_WRITES=false
VITE_BFF_ALLOW_DEV_STUB_WRITES=false
VITE_BFF_EMBEDDED_BEARER_TOKEN=false
```

The production dependency graph must show zero reachable imports of fixture,
seed, mock transport or local write-overlay modules. Test-only dynamic imports
must be excluded from the production bundle. Browser errors expose typed
unavailable/degraded states rather than fabricated data.

## 10. Exact-pair delivery repair

### 10.1 One release identity

Use the existing release manifest with exact:

- Pantheon commit and image digests;
- execute-plans commit and artifact checksum;
- compatibility and migration-set digests;
- environment/config schema identity; and
- workflow run identity.

### 10.2 Baseline-before-switch algorithm

1. resolve one authoritative target from merged environment configuration;
2. fetch FE manifest with content-type, HTTP status and non-empty body checks;
3. parse schema and verify accepted/standby state;
4. fetch BFF version/readiness;
5. validate both SHAs belong to the expected protected branches;
6. persist and checksum rollback baseline evidence;
7. only then acquire lease and mutate the environment;
8. deploy immutable candidate;
9. run hosted gates; and
10. switch only after exact served readback.

An HTML/error/empty response at Step 2 produces an explicit manifest-boundary
failure, not a generic JSON traceback. It must not create a fallback baseline.

### 10.3 Rollback

Rollback installs the exact recorded FE artifact and BFF image, replays only
compatible reversible migrations, switches atomically and re-observes both
identities. A rollback rehearsal is mandatory before closure.

## 11. Implementation packets and dependencies

| Wave | Packet | Purpose | Depends on | Mandatory deletion |
|---:|---|---|---|---|
| 0 | `STRUCT-OWNERSHIP-001` | registries, caller/table/worker inventories, ADR decisions | none | none |
| 1 | `BFF-COMPOSITION-001` | explicit domain dependencies; production factory completeness | Wave 0 | global `read_store` callers and reverse import |
| 1 | `BFF-PACKAGE-001` | normalize imports and remove dynamic namespace wiring | Wave 0 | production domain `sys.path`/`globals()` forwarding |
| 1 | `BFF-TEST-ARCH-001` | typed router/application fixtures and bounded test collection | Wave 0 | non-composition tests importing/patching `main` |
| 2 | `BFF-ROUTER-STRUCT-001` | split giant factories by resource/use case | composition/package | proxy closures and copied handlers |
| 1 | `BFF-DEADCODE-001` | remove unreachable tails and stale tests | Wave 0 | all 17 tails |
| 2 | `DOMAIN-WRITERS-001` | route mutations to Persona/Strategy/Runtime/Deployment/Research/Job/Incident/Ranking owners | Wave 1 | BFF-local mutation fallbacks |
| 2 | `JOURNAL-OWNER-001` | select/migrate one Decision Journal | Wave 0 | unselected implementation |
| 2 | `OVERLAY-RETIRE-001` | backfill, parity, cut over and delete overlays | domain owners | five overlay authorities |
| 3 | `AGORA-CHAIN-001` | durable interaction/research/candidate/decision/suggestion chain | Waves 1-2 | fake/manual-only paths |
| 4 | `LOOP-TRUTH-001` | receipt-derived twelve-loop projection | Waves 2-3 | incident/static success substitution |
| 4 | `MGMT-READ-001` | owner-backed Management and AI projections | Loop truth | seed/fallback live paths |
| 5 | `FE-STRICTLIVE-001` | production bundle reachability and degraded UX | Management contracts | FE mocks/overlays from live graph |
| 5 | `DEV-DELIVERY-001` | reconcile target; repair baseline; exact-pair deploy | merged backend/FE | stale current environment claims |
| 6 | `L12-HOSTED-001` | one correlated twelve-loop run | accepted pair | skip paths for mandatory cases |
| 6 | `MGMT-AGORA-E2E-001` | authenticated journeys and restart/replay | accepted pair | fixture-only acceptance |
| 7 | `STRUCT-RETIRE-001` | final copied-body/compatibility deletion | hosted acceptance | satisfied adapters/copies |

Packets in the same wave may run concurrently only when their file/table/event
scopes do not overlap. One owner may not implement both sides of a parity
comparison without independent review.

## 12. Test design

### 12.1 Static architecture gates

- no production import from `main`;
- no dynamic namespace-copy forwarding;
- no production domain/router `sys.path` mutation;
- direct `main` imports limited to the reviewed composition-test allowlist;
- no production references to forbidden overlay symbols;
- no query protocol method begins with mutation verbs;
- every mounted mutation route maps once in ownership inventory;
- normalized method/path uniqueness for every composition profile;
- exact duplicate-definition count cannot increase and reaches the reviewed
  target at each wave;
- no statements after unconditional top-level return/raise; and
- no second journal/store/scheduler owner.

### 12.2 Contract tests

- typed owner unavailable behavior;
- command receipt identity and idempotency;
- optimistic concurrency/conflict semantics;
- tenant/auth isolation;
- cursor pagination and deterministic ordering;
- SSE replay, retention and reconnect;
- provenance downgrade on missing/invalid receipts; and
- projection rebuild equals incremental state.

### 12.3 Integration tests

- real production factory boots with all required command owners;
- every mounted route executes against owner-backed test containers;
- overlay-disabled restart and multi-replica readback;
- queue claim/ack/retry/DLQ with one consumer owner;
- outbox transaction and duplicate delivery;
- Decision Journal migration parity and old writer rejection; and
- Management projection shows explicit degradation when an owner is down.

Route test files must finish within a documented budget. A timeout is a failure,
not an unreported partial pass.

### 12.4 Hosted gates

On one exact pair:

1. authenticate and record actor/tenant scope;
2. create a new correlation ID;
3. stimulate Loop 1 through owner API/scheduler;
4. observe terminal and next-consumer receipts through Loop 11;
5. read all twelve Loop Truth rows through Management;
6. complete Agora workshop-to-suggestion journey;
7. complete Management AI query and confirmed paper-safe command;
8. restart BFF and selected workers;
9. reconnect SSE from cursor and fresh-read all critical aggregates;
10. verify no duplicate side effects; and
11. rehearse rollback and re-read exact served identities.

No mandatory case may be skipped. Provider-dependent cases must use an
authorized sandbox/real backend or remain open; mocks cannot close them.

## 13. Observability and evidence

Every command/event/log carries `release_id`, `correlation_id`, `causation_id`,
`tenant_id`, `owner` and record/event ID. Dashboards separate:

- product owner readiness;
- loop closure status;
- Management AI provider status;
- development-tooling health; and
- deployment status.

Evidence bundles contain identities, timestamps, counts, digests and redacted
receipts. They do not contain secrets, full private content or fabricated
success rows.

## 14. Rollout and rollback gates

Each behavioral migration uses:

```text
characterize -> owner implementation -> shadow compare -> cutover -> delete
```

Not:

```text
new wrapper -> dual write forever -> later cleanup
```

Gate requirements:

- characterization passes before behavior movement;
- owner write is canonical before shadow comparison;
- parity threshold is exact for identity/status/version fields;
- cutover PR includes deletion or a dated next-wave removal packet;
- schema changes are backward compatible through one rollback window; and
- post-cutover failure rolls back the exact release, not individual source
  files on the host.

## 15. Definition of done

The program is complete only when:

1. ownership, mutation and worker registries are current and enforced;
2. `main.py` is a composition root with no domain implementation;
3. imports resolve from one stable package root without domain runtime path
   mutation or namespace copying;
4. router/application/adapter responsibilities follow the declared dependency
   direction and focused tests no longer require global monkeypatching;
5. `ReadSurfacePorts` and global `read_store` are retired;
6. all production routes use typed domain query/command ports;
7. all five process-local state authorities are removed;
8. one Decision Journal implementation remains;
9. the 17 unreachable tails and classified duplicate bodies are deleted;
10. all local static/contract/integration gates complete without timeout;
11. strict-live frontend has no mock/seed/write fallback reachability;
12. exact-pair deployment and rollback evidence are accepted;
13. twelve loops complete with one correlation chain and fresh readback;
14. authenticated Management, Management AI and Agora hosted journeys pass;
15. no real-capital action was enabled by this program; and
16. independent review confirms no new facade, store, scheduler, journal,
    projection authority or deploy lane was introduced.
