# Agora Code and Scenario GAP Audit

## 1. Audit method and completion rule

The audit starts from the product outcome, not from the number of merged tasks
or endpoints. For each scenario it checks four separate conditions:

1. **contract exists** — schema, client, or endpoint is present;
2. **reachable** — the current user interface invokes the path;
3. **authoritative** — the owning service derives and persists truth with
   correct identity, role, concurrency, and tenant scope;
4. **operationally proven** — the current exact hosted pair completes the path
   with durable readback.

An endpoint or component that satisfies only condition 1 is not a completed
feature. Tests that seed a store directly do not prove that a production
producer exists.

Severity used below:

- **P0 correction**: security, authority, identity, or false-success design;
- **P1 journey blocker**: prevents an ordinary user from completing the core
  journey;
- **P2 completeness**: important product behavior after the core path works;
- **P3 cleanup**: dead/duplicate code or maintainability debt.

## 2. End-to-end scenario verdict

| ID | User outcome | Current verdict | Principal blocker |
|---|---|---|---|
| S01 | Authenticate and receive a private Agora/servant context | Partial | Identity and private Workshop storage exist, but privacy proof is not carried across every downstream object |
| S02 | Create a Workshop from the UI with a strategy hypothesis | Blocked | Backend create exists; current Workshop page is list/select-only and exposes no create flow |
| S03 | Converse and receive strategy reconstruction, assumptions, contradictions, completeness, and one NBQ | Blocked | Composer invokes Persona daily interaction, not Workshop reconstruction; no reconstruction worker materializes StrategySpec structure |
| S04 | Review typed Workshop cards and act on plan/version/consultation/conclusion cards | Blocked | Cards and clients exist, but the runtime page does not render `WorkshopCardRenderer` or expose most governed actions |
| S05 | Produce, compare, and select an immutable StrategySpec draft | Partial/blocked | Durable downstream operation/readback exists, but no normal producer turns the conversation into an authoritative draft; fallback identities can manufacture readiness inputs |
| S06 | Approve and run real governed research with progress and artifacts | Blocked | Research plan/run APIs exist, but ownership/write-role controls are wrong and dispatch does not prove a real governed worker path |
| S07 | Build a real candidate pool from strategy/research evidence | Blocked | Production pool creation inserts two hard-coded prototype candidates; frontend passes lens IDs where pool IDs are required |
| S08 | Generate a strategy-specific, live-data Trading Room workspace | Blocked | Workspace compiler exists, but caller submits readiness/freshness and most widget data sources are unwired or client-faked |
| S09 | Review/research/shadow/park candidates and read canonical state | Blocked | Visible drawer actions mutate React state; a separate BFF-wired drawer is runtime-orphaned |
| S10 | Receive a real decision event and create a governed intent/handoff | Blocked | No production decision-event producer was found; decision, intent, and handoff records are not owner-scoped |
| S11 | Observe real owner-scoped strategy performance and act on governed suggestions | Partial/blocked | TradeJourney projection and action receipts are sound; no production suggestion producer exists, and frontend also consumes an unscoped Management attribution projection |
| S12 | Extract eligible Agora interaction evidence into tenant-safe datasets | Partial | Durable extraction/inbox/handoff exists, but request handling runs worker work inline and current data showed pending handoffs without a downstream consumer/ACK loop |
| S13 | Train/evaluate a policy candidate asynchronously from the dataset | Partial | Tenant-scoped candidate/lease and behavior-cloning primitives exist; handoff may process inline and no durable Agora handoff dispatcher was found |
| S14 | Obtain independent Consultation review and sponsor decision | Blocked | Candidate intake immediately creates a published memo, defaults to approved, and reports high confidence without an independent executor |
| S15 | Use the whole journey on a currently accepted hosted exact pair | Blocked | Hosted manifest/running BFF SHA drift and `/readyz` is degraded |

No row proves the complete S02–S15 chain. Agora therefore has no currently
accepted end-to-end product journey.

## 3. Strategy Workshop findings

### GAP-W01 — The product has no normal Workshop creation path in the UI

- **Severity:** P1
- **Frontend evidence:**
  `execute-plans:src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx`
  loads and selects existing Workshops but does not call the existing
  `createWorkshop` client.
- **Backend evidence:**
  [`strategy_workshop/router.py`](../../../services/control-plane/bff/agora/strategy_workshop/router.py)
  provides `POST /bff/agora/workshops`.
- **Impact:** a new user cannot start the canonical journey without external
  seeding or a direct API call.
- **Required correction:** add an explicit create/start flow only after the
  command/reconstruction design in the SD is implemented.

### GAP-W02 — The Workshop composer is wired to the wrong domain workflow

- **Severity:** P0 correction / P1
- **Frontend evidence:** the composer calls `submitDailyInteraction`; it does
  not call `postWorkshopMessage`.
- **Backend evidence:**
  [`interaction/router.py`](../../../services/control-plane/bff/agora/interaction/router.py)
  and its runner produce Persona daily interaction/consultation material.
  They do not reconstruct a twelve-block StrategySpec, calculate authoritative
  completeness, or emit one next-best question.
- **Impact:** the page visually called “Strategy Workshop” behaves as a Persona
  consultation timeline. Adding more Persona cards would deepen the wrong
  abstraction instead of producing a strategy.
- **Required correction:** Workshop owns message acceptance and reconstruction
  jobs. Persona consultation becomes an optional Workshop tool/subprocess, not
  the main composer target.

### GAP-W03 — Typed Workshop cards exist but are disconnected from runtime

- **Severity:** P1/P3
- **Evidence:**
  `execute-plans:src/agora/components/WorkshopCardRenderer.tsx` and specialized
  version/research/backtest/consultation components are imported by tests but
  not by the current Workshop page. The page fetches cards but primarily
  renders `DailyInteractionTimeline` and an optional governed proposal card.
- **Impact:** implemented research/version/consultation clients and cards do
  not create reachable product behavior.
- **Required correction:** route the canonical Workshop event/card projection
  through one renderer and remove the parallel daily-interaction rendering
  path from the core strategy conversation.

### GAP-W04 — Completeness is client-writeable derived truth

- **Severity:** P0 correction
- **Evidence:**
  [`strategy_workshop/router.py`](../../../services/control-plane/bff/agora/strategy_workshop/router.py)
  exposes `POST /bff/agora/workshops/{workshop_id}/completeness`; the caller can
  supply `state_map_json`, blockers, and the next question.
- **Impact:** a client or compromised caller can state that a strategy is
  complete and control the next question. Completeness is not a user command;
  it is a server-owned projection of confirmed strategy facts and evidence.
- **Required correction:** remove the public writer. Only the reconstruction
  worker may append a reconstruction result whose projector derives
  completeness and NBQ.

### GAP-W05 — Readiness can be assembled from synthetic identity

- **Severity:** P0 correction
- **Evidence:** readiness logic falls back to values such as
  `unbound-{workshop_id}` and cascades Workshop IDs into version/Registry
  fields. Helper-import failure also has a permissive fallback.
- **Impact:** an unbound Workshop can appear structurally ready without an
  immutable Registry StrategySpec. Distinct identity concepts become
  interchangeable.
- **Required correction:** fail closed unless `strategy_id`, immutable
  `strategy_spec_registry_id`, and `workshop_version_id` are present and their
  lineage reads back from the owning stores.

### GAP-W06 — Async acknowledgment performs provider work in the request

- **Severity:** P0 correction
- **Evidence:** the Persona interaction route accepts `BackgroundTasks` but
  calls `execute_resource(resource)` inline for queued/running work; retry and
  recovery paths also execute inline.
- **Impact:** HTTP 202 does not mean durable acceptance, provider latency is
  coupled to the request, and crash/retry semantics are misleading.
- **Required correction:** request transaction persists command + outbox +
  receipt; a separately leased worker performs provider work and emits result
  events.

### GAP-W07 — Idempotency semantics are inconsistent with the contract

- **Severity:** P0 correction
- **Evidence:** basic Workshop create/message/completeness/reassess paths turn a
  seen idempotency key into 409, while later version/research/consult/conclude
  operations use richer receipts and resumable partial-effect handling.
- **Contract conflict:** EV-06 in the original design requires repeated
  `Idempotency-Key` to return the prior command result.
- **Required correction:** generalize the richer command receipt protocol to
  all Agora writes; same key + same request hash replays the result, while same
  key + different hash conflicts.

### GAP-W08 — Canonical Workshop adapters omit an explicit service identity

- **Severity:** P0 boundary verification
- **Evidence:**
  [`strategy_workshop/operations.py`](../../../services/control-plane/bff/agora/strategy_workshop/operations.py)
  performs direct HTTP requests with `Accept`/`Content-Type` only; it does not
  attach an explicit service credential or tenant propagation header.
- **Impact:** this boundary either relies on an undocumented network trust
  assumption or will fail once downstream strict service auth is enforced.
- **Required correction:** use the shared authenticated service client,
  propagate a signed tenant/user/trace context, and add negative tests. This is
  a verification item: downstream auth behavior must be checked before choosing
  the final credential mechanism.

## 4. Research and candidate findings

### GAP-R01 — Research mutations require only read authority

- **Severity:** P0 correction
- **Evidence:**
  [`research/router.py`](../../../services/control-plane/bff/agora/research/router.py)
  builds one scope helper around `require_read_role` and reuses it for create,
  approve, cancel, dispatch, review, discussion, and monitoring mutations.
- **Impact:** a read-only Agora user may mutate research/candidate state.
- **Required correction:** split read and write dependencies; every mutation
  requires write role, owner scope, CAS where mutable state exists, and audit.

### GAP-R02 — Research plan/run ownership is absent

- **Severity:** P0 correction
- **Evidence:** generated plan records omit tenant/user ownership; get/list,
  approve/cancel/dispatch, run read/cancel, and artifact access use global IDs
  or Workshop/plan IDs without binding the caller to the stored owner.
- **Impact:** guessed identifiers can cross user boundaries and violate
  ISO-U01/U04/U05.
- **Required correction:** tenant/user columns and compound unique keys are
  mandatory on plans, runs, stages, artifacts, discussions, and receipts. A
  foreign object returns non-enumerating 404.

### GAP-R03 — Production candidate creation inserts fixture truth

- **Severity:** P0 correction
- **Evidence:** candidate-pool creation calls `_default_registry_candidates`
  and returns two hard-coded prototype candidates and metrics.
- **Impact:** a successful response can falsely claim that real strategies were
  discovered and scored.
- **Required correction:** remove the default candidate function from the
  production profile. Empty authoritative input returns an empty pool plus an
  explicit reason. Fixtures live only in test or an unmistakable demo profile.

### GAP-R04 — Research “dispatch” does not prove backend execution

- **Severity:** P1
- **Evidence:** the BFF research facade materializes queued plan/run state, but
  no complete path in the audited route proves dispatch to typed governed
  research backends and readback of their artifacts.
- **Impact:** queued can be mistaken for running/complete.
- **Required correction:** a research dispatcher consumes durable outbox rows,
  invokes allowlisted backend adapters, records backend job identity, and
  projects ordered progress/artifact events.

### GAP-R05 — Frontend strategy-lens identity is incompatible with pool identity

- **Severity:** P1
- **Evidence:** Trading Room declares fixed IDs `lens-A` through `lens-E`, then
  calls `listCandidatePoolMembers(activeLensId)`. BFF pool IDs are `cpool-*`.
- **Impact:** the live client asks a pool endpoint for a non-pool identifier.
- **Required correction:** introduce an authoritative strategy-to-pool
  projection. The UI selects a strategy/version or named view; the server
  resolves its current pool ID.

## 5. Trading Room findings

### GAP-T01 — Frontend candidate actions are local-only

- **Severity:** P0 false-success / P1
- **Evidence:** the drawer embedded in
  `execute-plans:src/agora/pages/trading-room/TradingRoomPage.tsx` changes local
  candidate state and selection. A separate
  `src/agora/components/CandidateReviewDrawer.tsx` calls the BFF review API but
  is not imported by the runtime page.
- **Impact:** the user sees an accepted action that has no durable effect.
- **Required correction:** consolidate one drawer whose actions create durable
  command receipts and refresh canonical member state.

### GAP-T02 — Workspace generation trusts caller-owned readiness and freshness

- **Severity:** P0 correction
- **Evidence:** the proposal request accepts caller fields including
  `strategyVersion`, `evidenceRefs`, `dataFreshness`, and
  `tradingRoomReady` (defaulting true) and passes the boolean to generation.
- **Impact:** the client can assert the gate it is supposed to satisfy.
- **Required correction:** request carries only a strategy/version identity and
  optional user intent. Server resolves the selected Registry version,
  readiness assessment, evidence, and freshness from owning services.

### GAP-T03 — The “servant-generated workspace” is a deterministic compiler

- **Severity:** P1 design correction
- **Evidence:** the backend generator validates/expands a fixed set of seven
  winner-branch views. Frontend prompt/revision behavior uses keyword and regex
  functions such as `parseNewWidgetPrompt` and constructs `proposedSpec`
  locally.
- **Impact:** deterministic code and local parsing are presented as agent
  authorship; adding more keyword cases will not create a reliable servant.
- **Required correction:** rename and preserve the backend logic as a
  `WorkspaceCompiler`. If natural-language authoring is desired, a separately
  governed servant produces typed `WorkspaceIntent`; the compiler alone
  validates and expands it.

### GAP-T04 — Most displayed workspace capability has no live data source

- **Severity:** P1
- **Evidence:** nine data sources declare `wired:false`.
  `WorkspaceGridEditor.getWidgetData` derives only decision queue, position
  action, and candidate ranking; other widgets have no canonical data.
  Data-range/benchmark paths display “Mock Mode”; usefulness and evidence
  actions are client-only.
- **Impact:** a visually complete dashboard can contain no live strategy data.
- **Required correction:** define a server query contract for every allowlisted
  widget. Each response includes `source`, `status`, `as_of`, `cutoff`,
  `lineage`, and an explicit unavailable reason. Hide or disable unwired
  widgets in live mode.

### GAP-T05 — Decision events, intents, and handoffs are globally stored

- **Severity:** P0 correction
- **Evidence:** Trading Room store records for decision events, intents, and
  handoffs do not carry/enforce tenant/user ownership; aggregate reads list
  decision events globally.
- **Impact:** direct ISO-U04 violation and possible cross-user decision leakage.
- **Required correction:** owner-scope every record and query, including SSE
  replay, idempotency keys, receipts, and audit rows.

### GAP-T06 — There is no production decision-event producer

- **Severity:** P1
- **Evidence:** non-test search found `upsert_decision_event` used by stores and
  tests, not by a production signal/event adapter.
- **Impact:** the decision queue can be demonstrated only with directly seeded
  state.
- **Required correction:** add an allowlisted signal projection that converts
  authoritative strategy/runtime observations into evidence-bound decision
  events. It has no order authority.

### GAP-T07 — Workspace and version persistence is not atomic

- **Severity:** P0 correction
- **Evidence:** routes call workspace upsert and version recording separately;
  the PostgreSQL JSON aggregate commits each method independently.
- **Impact:** a crash can leave the current workspace without its immutable
  version or vice versa.
- **Required correction:** one database transaction appends the command/event,
  stores the workspace snapshot, creates the immutable version, and updates the
  pointer with CAS.

### GAP-T08 — Aggregate risk and position truth is placeholder state

- **Severity:** P1
- **Evidence:** aggregate construction hard-codes
  `position_summaries=[]` and `risk_summary=normal`.
- **Impact:** absence of data is presented as normal risk.
- **Required correction:** use explicit `unavailable/degraded/healthy` source
  state; never map missing risk data to normal.

## 6. Strategy Performance findings

### GAP-P01 — Suggestion/action infrastructure has no producer

- **Severity:** P1
- **Evidence:**
  [`performance/store.py`](../../../services/control-plane/bff/agora/performance/store.py)
  has scoped suggestions, CAS, receipts, action audit, and restart-safe
  persistence. Non-test search found no caller of `upsert_suggestion`.
- **Impact:** the frontend can operate on suggestions only if state is seeded
  externally or by tests.
- **Required correction:** a governed evolution/performance projection creates
  suggestions from owner-scoped evidence and explicit policy, with provenance
  and expiry.

### GAP-P02 — Agora consumes a Management projection without owner scope

- **Severity:** P0 correction
- **Evidence:** Strategy Performance uses both the Agora projection and
  `getTradingRoomPerformanceAttribution`, which calls
  `/bff/management/performance-attribution/by-strategy`. The Management route
  reads global read-store facts and does not implement Agora tenant/user
  ownership.
- **Impact:** cross-product leakage and an authority boundary violation.
- **Required correction:** create an Agora-owned, tenant/user-scoped
  `StrategyPerformanceIndex`; the frontend must not consume the Management
  projection directly.

### GAP-P03 — The good foundation must be retained

- **Severity:** design constraint
- **Evidence:**
  [`performance/service.py`](../../../services/control-plane/bff/agora/performance/service.py)
  projects tenant/user-scoped TradeJourney events; mutations require write
  role and the suggestion store provides receipts/audit.
- **Required action:** reuse these primitives rather than rebuilding a second
  performance action system.

## 7. Dataset, policy learning, and Consultation findings

### GAP-L01 — Dataset acceptance and worker execution are coupled

- **Severity:** P1 design correction
- **Evidence:** extraction stores durable evidence/inbox/dataset/handoff records
  with correct tenant scope, but the request invokes `process_inbox` and waits
  briefly for completion.
- **Impact:** the path looks asynchronous while request ownership still drives
  work; throughput and crash semantics are unclear.
- **Required correction:** acceptance returns after transactionally writing
  evidence + inbox/outbox; a separately leased worker extracts and publishes.

### GAP-L02 — No durable Agora-to-policy-learning handoff dispatcher was found

- **Severity:** P1
- **Evidence:** the BFF exposes list/ACK endpoints and policy-learning exposes
  `POST /api/policy-learning/agora-handoff`; non-test source search found no
  worker that leases pending BFF handoffs, sends them to policy-learning, reads
  back durable admission, and ACKs the source handoff. The policy scheduler
  discovers datasets through its own tick and processes its own candidate
  backlog; it is not that dispatcher.
- **Observed consequence:** the bounded runtime snapshot contained pending
  handoffs and no complete learn chain.
- **Required correction:** an explicit outbox consumer with retry, lease,
  idempotency, signed scope propagation, canonical readback, and source ACK.

### GAP-L03 — Policy candidate processing can run inline in intake

- **Severity:** P1 design correction
- **Evidence:** `process_immediately` may claim and process a candidate inside
  the handoff HTTP request.
- **Impact:** durable ingestion and expensive training/evaluation are conflated.
- **Required correction:** handoff acknowledges only durable admission;
  scheduler/worker leases and processes later. A synchronous test helper may
  exist only outside the production route.

### GAP-L04 — Consultation candidate intake self-attests a terminal result

- **Severity:** P0 correction
- **Evidence:**
  [`consultation/main.py`](../../../services/consultation/main.py) candidate
  intake immediately creates a published terminal memo, assigns confidence
  `0.95`, defaults `auto_decision` to `approved`, marks the request published,
  and creates a sponsor-decision bridge proposal.
- **Impact:** policy learning effectively writes its own “independent review”
  result. No committee/provider/executor has reviewed the candidate.
- **Required correction:** intake creates only a submitted request/work item.
  The existing Consultation workflow executor/committee machinery must produce
  findings and a memo; sponsor decision is a distinct human/governed command.

### GAP-L05 — A processed learning candidate is not a promoted runtime policy

- **Severity:** P2/explicit boundary
- **Evidence:** policy-learning candidates remain fail-closed with no runtime
  effect and require experiment approval/deployment gates.
- **Impact:** documents and UI must not equate behavior-cloning output,
  Consultation approval, deployment, or capital authority.
- **Required correction:** retain the boundary. Runtime promotion is a separate
  future governed flow and is outside Agora product completion.

## 8. Frontend dead, duplicate, and misleading code

The detailed disposition is in the next document. Confirmed candidates are:

| Code | Finding | Disposition |
|---|---|---|
| `execute-plans:src/agora/AgoraApp.tsx` | Old “Phase M0 / coming soon” shell; no runtime import | Delete after route/import guard |
| `execute-plans:src/agora/dashboard/*` | Parallel dashboard implementation not reached by Agora routes | Consolidate useful tests/spec logic into Trading Room, then delete |
| `execute-plans:src/agora/widgets/WidgetRenderer.tsx` and old revision drawer | Used only by the dead dashboard island/tests; live path uses `ChartSpecRenderer` and `trading-room/*` | Consolidate then delete |
| `execute-plans:src/agora/components/CandidateReviewDrawer.tsx` | BFF-wired duplicate not used by runtime page | Make canonical or merge into page component; remove duplicate |
| hard-coded `STRATEGY_LENSES` and sample candidates | Production page contains static product truth | Move to explicit fixtures/story data or delete |
| local prompt/revision regex | Client pretends to author servant proposals | Remove from live mode; use typed intent + backend compiler |
| local “useful/evidence/mock range” actions | Visual success without durable behavior | Hide until canonical commands exist, then wire/read back |

## 9. Hosted and deployment gap

The current hosted environment cannot be used to close any of the product gaps:

- the frontend manifest declares BFF SHA
  `be956c07aca889043ef301389412b6744452f20b`;
- the running `/bff/version` reports
  `6367cea609e9d19053130ab8f9b1946d5d35dfc6`;
- `/readyz` is HTTP 503/degraded and reports lifecycle projection repair-only
  state and a cursor mismatch;
- write defaults are correctly safe/read-only, so old hosted write proof is not
  a new write acceptance for this drifted pair.

Deployment repair is deliberately last in the dependency chain. Redeploying
the present source would not repair the product design gaps above.
