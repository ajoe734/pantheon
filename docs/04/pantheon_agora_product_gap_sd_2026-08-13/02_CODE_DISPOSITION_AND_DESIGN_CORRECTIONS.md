# Agora Code Disposition and Design Corrections

## 1. Decision rule

This plan does not reward code simply because it is already merged. Each
surface is evaluated by product authority and runtime use:

- **KEEP** — correct owner, usable primitive, and compatible with the target
  design;
- **REFACTOR** — useful behavior exists, but its boundary, scope, transaction,
  or name is wrong;
- **QUARANTINE** — must be disabled or removed from live product behavior before
  dependent work proceeds;
- **DELETE** — runtime-orphaned, duplicate, misleading, or fixture code with no
  target-state responsibility.

Deletion is staged: first prove imports/routes/tests have moved, then delete.
“Keep for safety” is not a valid reason to leave a second production
implementation reachable.

## 2. Non-negotiable architecture corrections

### DC-01 — Workshop, not Persona daily interaction, owns strategy formation

**Current error:** the Strategy Workshop composer sends Persona daily
interaction requests. That workflow produces opinions/consultation cards, not
an authoritative strategy reconstruction.

**Decision:** Workshop accepts the hypothesis and subsequent messages. A
Strategy Reconstruction worker produces a typed reconstruction result,
completeness, one NBQ, and draft proposal. Persona may be invoked as an
optional evidence/consultation participant from that workflow.

**Prohibited follow-up:** do not add more Persona daily-interaction cases to
simulate missing StrategySpec blocks.

### DC-02 — Derived truth is server-owned

**Current error:** callers can write completeness/readiness/freshness-like
facts.

**Decision:** clients may submit intent and confirmations, never the gate
result. Completeness, readiness, data freshness, candidate scores, performance,
and consultation verdicts are derived by their owning service from immutable
lineage.

**Prohibited follow-up:** no client boolean such as `tradingRoomReady=true` may
unlock a server operation.

### DC-03 — Async means durable acceptance plus a separate worker

**Current error:** HTTP 202 routes execute provider/training/extraction work in
the request or with “process immediately” switches.

**Decision:** the request transaction records command, request digest, receipt,
and outbox. A leased worker processes the outbox and publishes ordered events.

**Prohibited follow-up:** no more inline provider calls behind 202 responses;
no process-local background task as the only durable mechanism.

### DC-04 — One identity vocabulary

The following IDs are distinct and cannot fall back to each other:

| Identity | Meaning | Owner |
|---|---|---|
| `strategy_id` | Stable logical strategy | Strategy Registry/domain |
| `strategy_spec_registry_id` | Immutable StrategySpec version | Strategy Registry |
| `workshop_id` | Conversation aggregate | Workshop |
| `workshop_version_id` | Link/reconstruction milestone inside a Workshop | Workshop |
| `research_plan_id` / `research_run_id` | Governed research lifecycle | Research |
| `candidate_pool_id` / `candidate_id` | Scored candidate projection | Candidate/Research |
| `workspace_id` / `workspace_version_id` | Trading Room materialization | Trading Room |
| `decision_event_id` / `trading_intent_id` | Human decision and no-order intent | Trading Room/Governance |
| `dataset_version_id` | Immutable eligible learning dataset | Dataset authority |
| `policy_candidate_id` | Offline learning output | Policy Learning |
| `consult_request_id` / `memo_id` / `sponsor_decision_id` | Independent review stages | Consultation/Governance |

Synthetic `unbound-*` identities are forbidden in authoritative records.

### DC-05 — One Agora command protocol

All mutations use:

- authenticated tenant/user and a write-capable role;
- target owner lookup before mutation;
- `Idempotency-Key` scoped by tenant/user/operation;
- canonical request hash;
- replay of the prior result for same key + same hash;
- conflict for same key + different hash;
- `If-Match`/CAS for mutable aggregates;
- durable command receipt, audit, and trace ID;
- canonical readback before reporting success.

Basic Workshop and Trading Room conflict-only idempotency must be migrated to
the richer receipt model already used by later Workshop operations and
Performance actions.

### DC-06 — Tenant/user ownership is physical data, not request context only

Every private aggregate, event, artifact, receipt, idempotency record, and SSE
cursor carries `tenant_id` and `user_id`. Store queries require both. Foreign
IDs return a non-enumerating 404. The corrections apply to Research plans/runs
and Trading Room decision events/intents/handoffs before any further feature
work.

### DC-07 — Fixtures never populate live truth

Hard-coded candidates, strategy lenses, metrics, risk-normal defaults, sample
evidence, and mock actions are removed from production paths. If demos are
needed, they require an explicit demo profile and visible `demo` provenance.
Empty/unavailable state is a valid product state and must not become fabricated
success.

### DC-08 — Compilation and generation are different responsibilities

The existing deterministic workspace generator is useful, but it is a
compiler. It should be named and tested as such. A servant may optionally turn
natural language into typed `WorkspaceIntent`; only the compiler can accept,
validate, and expand it into an allowlisted workspace spec.

### DC-09 — Consultation cannot approve its own intake

Policy Learning submits a candidate plus evidence. Consultation admits a work
item. A separate executor/committee publishes a memo. A sponsor/human performs
the governed decision. No default auto-approval, fixed confidence, or terminal
memo is allowed in intake.

### DC-10 — Deployment proof follows product correction

Exact-pair hosting, healthy readiness, browser proof, restart persistence, and
cross-user tests are required closure gates. They are not substitutes for a
missing producer or an incorrect authority boundary.

## 3. Backend disposition

### 3.1 Keep and extend

| Surface | Keep because | Required extension |
|---|---|---|
| Agora identity/private Workshop storage | Correct owner/private foundation | Extend scope through every downstream object and negative test |
| Workshop version/research/consult/conclude command receipts | Durable receipt, partial-effect recording, canonical readback/compensation are sound patterns | Generalize protocol; add authenticated service context |
| Workshop SSE sequence/replay primitives | Correct event-based UI foundation | Project reconstruction/research/workspace events and owner-scope cursors |
| Research/candidate schemas and serialized truth | Useful contract surface | Replace fixture producer and add owner-scoped store/real dispatch |
| Trading Room widget allowlist/validation | Good safety boundary | Move under honestly named WorkspaceCompiler and bind data queries |
| Performance TradeJourney projection | Already tenant/user scoped | Add owner-scoped strategy index and suggestion producer |
| Performance suggestion receipts/audit/CAS | Correct governed action primitive | Feed from a real suggestion producer |
| Dataset evidence/dataset version/handoff records | Tenant-safe, durable lineage foundation | Separate request and worker; add dispatcher/ACK consumer |
| Policy candidate store, leases, recovery, fail-closed runtime effect | Useful offline worker foundation | Make handoff admit-only and worker-driven |
| Consultation core request/memo/workflow executor | Provides a real staged workflow | Route policy candidates through it; remove shortcut |
| v1.13 contracts/generated types/compatibility tooling | Valuable cross-repo guard | Update additively after authority contract changes |

### 3.2 Refactor before feature expansion

| Current code | Problem | Target |
|---|---|---|
| [`strategy_workshop/router.py`](../../../services/control-plane/bff/agora/strategy_workshop/router.py) | Roughly 4,000 lines and mixes HTTP, validation, projection, command orchestration, readiness, and downstream calls | Split route/controller, application commands, projectors, policies, and adapters without changing external behavior in one step |
| [`strategy_workshop/operations.py`](../../../services/control-plane/bff/agora/strategy_workshop/operations.py) | Direct unauthenticated-looking urllib boundary | Shared service client with signed service identity, tenant/user/trace envelope, timeout/retry policy, and sanitized errors |
| [`interaction/router.py`](../../../services/control-plane/bff/agora/interaction/router.py) | Inline execution behind 202 | Admit-only route + leased worker; keep daily interaction as optional Persona feature |
| [`research/router.py`](../../../services/control-plane/bff/agora/research/router.py) and store | Global plan/run state; read role on writes; facade dispatch | Scoped schema/store, write-role dependencies, durable dispatcher and backend readback |
| [`trading_room/router.py`](../../../services/control-plane/bff/agora/trading_room/router.py) and store | Caller-owned truth, global decision state, non-atomic versioning | Authoritative resolver, scoped records, atomic append/materialize/version transaction |
| Workspace generator | Named/marketed as servant generation but deterministic | `WorkspaceCompiler` accepting typed intent and authoritative context |
| [`dataset_extraction/router.py`](../../../services/control-plane/bff/agora/dataset_extraction/router.py) | Request runs inbox work | Admit-only API + outbox worker + delivery receipts |
| Policy handoff endpoint | Optional inline processing | Durable candidate admission only; scheduler/worker processes leases |
| Consultation policy-candidate intake | Creates terminal approved memo | Submitted request only; normal workflow executor performs review |

### 3.3 Quarantine immediately in implementation wave 0

These behaviors should fail closed before dependent product work starts:

1. public completeness mutation;
2. readiness based on synthetic IDs or caller booleans;
3. Research and Trading Room writes authorized by read role;
4. unscoped Research plan/run and Trading decision/intent/handoff reads;
5. production `_default_registry_candidates` population;
6. missing-risk-to-`normal` projection;
7. policy-learning `process_immediately` in production handoff;
8. Consultation `auto_decision=approved` terminal intake.

Where removal would break an existing client, return a typed
`CAPABILITY_TEMPORARILY_DISABLED` response and hide the frontend action until
the corrected path lands. Do not silently retain unsafe semantics for
compatibility.

## 4. Frontend disposition

### 4.1 Keep and wire to canonical state

| Code | Disposition |
|---|---|
| `src/routes/agora.tsx` and `TradingDeskLayout.tsx` | Keep the three-route shell; make command/jobs/journal controls navigate or render actual panels |
| `WorkshopCardRenderer.tsx` and typed card components | Make the one canonical Workshop timeline renderer after contract review |
| `StrategyCompletenessRail.tsx` | Keep as a read-only projection; remove any dependency on client-authored completeness |
| `ChartSpecRenderer.tsx`, widget registry, validation | Keep as allowlisted display/validation primitives |
| Trading Room proposal preview/version UI | Keep after server authority and atomic versioning are corrected |
| Strategy Performance governed action UI | Keep; bind to owner-scoped suggestions and canonical receipts |
| `useAgoraWriteAccess.ts` | Keep as UX gating only; BFF authorization remains mandatory |

### 4.2 Consolidate, then delete duplicates

| Candidate | Evidence | Migration/removal gate |
|---|---|---|
| `src/agora/AgoraApp.tsx` | Not imported by runtime; obsolete M0/coming-soon shell | Route/import test proves no runtime reference, then delete |
| `src/agora/dashboard/*` | Parallel dashboard island not referenced by active Agora routes | Move any unique validation/test cases to Trading Room; delete island |
| `src/agora/widgets/WidgetRenderer.tsx` and `WidgetRevisionDrawer.tsx` | Coupled to the old dashboard island; live path uses ChartSpec/trading-room equivalents | Establish one renderer/revision flow; delete old pair |
| two `CandidateReviewDrawer` implementations | Runtime page uses local-only version; BFF-wired component is orphaned | Extract one canonical component with receipt/readback; delete other implementation |
| page-local candidate fixtures and fixed lens metrics | Mixed into production page | Move exclusively to test fixtures/story/demo profile or delete |

### 4.3 Delete misleading live behavior

The following should not survive as live product logic:

- `parseNewWidgetPrompt` keyword/regex generation;
- client-authored `proposedSpec` masquerading as a servant result;
- local-only “mark useful/not useful” state;
- toast-only evidence access;
- “Mock Mode” benchmark/data-range success in live mode;
- local candidate review/research/shadow state transitions;
- hard-coded sample reason/concerns/evidence merged into live candidate rows;
- fixed lens IDs passed as candidate pool IDs.

The replacement UI may preserve optimistic pending indicators, but it must show
success only after receipt and canonical readback.

## 5. Data migration and compatibility posture

Correcting scope and identity changes stored data. The implementation design
must not silently bless ambiguous legacy rows.

1. Add explicit schema version and ownership columns/fields.
2. Backfill only when ownership is provable from an immutable parent or audit
   record.
3. Quarantine ambiguous rows in a migration report; do not expose them.
4. Rebuild derived projections from authoritative events where possible.
5. Preserve immutable historical receipts; attach a superseding record rather
   than rewriting their content.
6. Update OpenAPI/generated TypeScript additively during migration, then remove
   deprecated unsafe endpoints only after all runtime callers are gone.
7. Make live mode fail closed if a record lacks required owner or lineage.

## 6. Definition of “wrong design corrected”

A design correction is complete only when:

- the old production behavior is unreachable;
- its replacement owns the right truth and persists it durably;
- all callers use the replacement;
- negative authorization, idempotent replay, crash/restart, and canonical
  readback tests pass;
- fixture/demo behavior is impossible in live profile;
- the old implementation and obsolete tests are removed, or a time-bounded
  deprecation with an explicit removal gate is recorded.
