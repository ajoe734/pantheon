# Agora Execution-Task Preparation

## 1. Purpose and dispatch boundary

This document prepares the corrected design for a later, separate
execution-task generation pass. The labels `WP-00` through `WP-11` are design
work-package references only:

- they are not canonical task IDs;
- they are not supervisor inbox packets;
- they do not authorize implementation;
- nothing in this packet has been dispatched.

When the operator requests task generation, each implementation/review item
must receive a registered canonical task ID through the governed task command
or assistant dev bridge. Do not copy a `WP-*` label into task state as though it
were a task ID.

## 2. Dependency and merge order

```text
WP-00 frozen contracts and migration guards
  ├─ WP-01 authority, scope, idempotency foundation
  │    ├─ WP-02 Workshop reconstruction backend
  │    │    └─ WP-03 Workshop frontend journey
  │    ├─ WP-04 Research dispatcher and candidate truth
  │    │    └─ WP-05 Candidate-review frontend
  │    └─ WP-06 Trading Room authority/data/atomicity
  │         └─ WP-07 Trading Room frontend consolidation
  ├─ WP-08 Agora Strategy Performance index and suggestions
  └─ WP-09 Dataset outbox and policy-learning worker boundary
       └─ WP-10 independent Consultation workflow

WP-03 + WP-04 + WP-05 + WP-06 + WP-07 + WP-08 + WP-09 + WP-10
  └─ WP-11 cross-repo E2E, exact-pair deployment, and hosted acceptance
```

WP-01 is the authority gate. No dependent task may implement a feature by
retaining client-writeable truth, read-role mutations, unscoped records,
fixture production data, or conflict-only idempotency.

## 3. Task-generation rules

Every canonical task produced later must state:

1. one repository and one merge target (`pantheon:dev` or
   `execute-plans:dev`);
2. objective and user-visible outcome;
3. current code evidence and the exact GAP/DC IDs being closed;
4. declared repo-relative file scope; never `.` and never an
   `execute-plans/` directory inside Pantheon;
5. explicit out-of-scope boundaries;
6. dependencies by canonical task ID after materialization;
7. owner capability and a different independent reviewer capability;
8. expected clean worktree and `task/<canonical-id>` branch;
9. contract, unit, integration, isolation, restart, and/or browser validation;
10. artifacts: diff, tests, receipts, review, PR/check/merge evidence, and
    deployment proof when applicable;
11. rollback/fail-closed behavior;
12. completion definition that requires canonical readback, not endpoint
    existence.

Cross-repository work is split into separate backend-contract, frontend-client,
and exact-pair-integration tasks. Merge backend additive contracts before the
frontend consumer; remove deprecated contracts only after caller inventory
proves zero use.

## 4. Work packages

### WP-00 — Freeze contracts, callers, and unsafe-path migration guards

**Objective:** create the implementation baseline that prevents workers from
building on a stale or parallel Agora design.

**Repository:** Pantheon documentation/contracts task first; any generated FE
contract update is a separate `execute-plans` task.

**Pantheon scope candidates:**

- `services/control-plane/bff/agora/models.py`
- `services/control-plane/bff/agora/**/models.py`
- `services/control-plane/bff/agora/**/router.py` only for capability flags or
  typed fail-closed guards
- canonical Agora OpenAPI/capability schemas and contract tests

**Required work:**

- inventory every frontend caller of completeness, readiness, Research,
  candidate, workspace, Performance, dataset, and Consultation routes;
- mark unsafe fields/routes deprecated and disabled in live profile;
- define schema versions for scope envelope, command receipt,
  reconstruction result, workspace intent/data, and Consultation intake;
- record a data-migration inventory and legacy-row ownership classification;
- define live-profile fixture prohibition tests.

**Out of scope:** implementing reconstruction, Research adapters, widgets, or
deployment.

**Acceptance:** one versioned compatibility matrix names old caller, temporary
behavior, replacement, removal gate, and responsible future task; live mode
cannot silently use unsafe fallback.

**Validation:** OpenAPI/schema validation, capability manifest tests, source
search for callers, and explicit fail-closed route tests.

**Rollback:** capability flags can re-expose read-only legacy projections, not
unsafe writes or fixture truth.

**Artifacts:** caller matrix, migration report, generated-contract diff,
validation logs, independent design review.

### WP-01 — Authority, scope, and command protocol foundation

**Closes:** DC-02, DC-04, DC-05, DC-06; GAP-W04/W05/W07/W08, GAP-R01/R02,
GAP-T02/T05/T07.

This package must be split into bounded backend tasks rather than one large
router rewrite.

#### WP-01A — Shared command receipt and service context

**Owner capability:** backend application architecture, auth, persistence.

**Pantheon scope candidates:**

- a new shared Agora command/receipt module under
  `services/control-plane/bff/agora/`
- Workshop/Research/Trading Room receipt stores and focused tests
- authenticated service-client infrastructure used by Workshop adapters

**Acceptance:** replay matrix, request-hash conflict, CAS, partial-effect
adoption, signed tenant delegation, sanitized downstream errors, restart
readback.

#### WP-01B — Research owner/write-role migration

**Owner capability:** backend API/security/PostgreSQL migration.

**Scope candidates:**

- `services/control-plane/bff/agora/research/router.py`
- `services/control-plane/bff/agora/research/store.py`
- Research store/route isolation and migration tests

**Acceptance:** every mutation requires write role; plans/runs/artifacts and
their receipts are tenant/user scoped; foreign IDs do not enumerate; ambiguous
legacy records are quarantined.

#### WP-01C — Trading decision/intent/workspace transaction migration

**Owner capability:** backend API/security/transaction design.

**Scope candidates:**

- `services/control-plane/bff/agora/trading_room/router.py`
- `services/control-plane/bff/agora/trading_room/store.py`
- Trading Room store/route/isolation/failure-injection tests

**Acceptance:** scoped decisions/intents/handoffs/SSE; write roles; atomic
workspace + immutable version + pointer + receipt transaction; missing risk is
not normal.

**Out of scope for all WP-01 tasks:** new UI features, new research backends,
new widget types, policy promotion.

**Merge gate:** independent security review plus two-user/two-tenant negative
tests. Dependent packages cannot merge around a failing gate.

### WP-02 — Workshop reconstruction backend and authoritative readiness

**Closes:** GAP-W02/W04/W05/W06/W07 and DC-01/DC-03.

**Owner capability:** backend domain/workflow, provider adapter, privacy.

**Pantheon scope candidates:**

- `services/control-plane/bff/agora/strategy_workshop/`
- a new reconstruction application/worker module with narrow responsibility
- Workshop worker compose/service configuration
- Workshop schemas, OpenAPI, and focused tests
- optional Persona adapter boundary, not Persona daily-interaction redesign

**Required work:**

1. split the router behind stable controllers into command service,
   reconstruction projector, readiness policy, and adapters;
2. create durable reconstruction outbox, leases, recovery, and DLQ;
3. materialize validated `StrategyReconstructionResult` and typed card events;
4. make completeness/readiness read-only derived projections;
5. eliminate synthetic identity and permissive readiness fallback;
6. create/adopt immutable Registry draft via canonical readback;
7. preserve private-content/redaction rules.

**Out of scope:** adding Trading Room widgets; replacing the entire Persona
service; live trading.

**Acceptance:** create -> message -> durable 202 receipt -> worker ->
reconstruction/completeness/NBQ/cards -> Registry draft/readback works after
BFF/worker restart. Stale worker result is superseded. Same idempotency key
replays. No client can write completeness/readiness.

**Validation:** unit schema/policy; Postgres transaction/restart; provider
malformed/stale/timeout tests; owner/SSE isolation; fake identity negative test;
contract/OpenAPI generation.

**Rollback:** disable reconstruction capability and keep Workshop readable;
never restore public derived-truth writers.

**Artifacts:** state transition trace, receipts/events, Registry readback,
privacy log scan, worker recovery proof, review.

### WP-03 — Complete Workshop frontend journey

**Depends on:** WP-00 frontend contracts and WP-02 additive backend contract.

**Closes:** GAP-W01/W02/W03.

**Repository:** `ajoe734/execute-plans`, target `dev`.

**Owner capability:** React/TypeScript, accessibility, BFF client integration.

**Scope candidates:**

- `src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx`
- `src/agora/components/WorkshopCardRenderer.tsx`
- specialized Workshop card components and their tests
- Agora BFF client/types for Workshop only
- route/browser tests for Workshop

**Required work:** create/start UI; post messages to Workshop; render one
canonical typed timeline; show receipt/worker/degraded states; allow plan,
version, consultation, and conclude commands only when cards/capabilities permit;
read back canonical state after each action.

**Out of scope:** Persona daily-interaction feature removal outside the Agora
core route, Trading Room redesign, hard-coded mock fallback.

**Acceptance:** a new user can create a Workshop and reach an authoritative
draft without seeding/direct API. Reload/reconnect/replay preserves state.
Mobile and desktop flows pass. No page action reports success from local state.

**Rollback:** feature-gate the new create/write flow while retaining safe
read-only Workshop history.

**Artifacts:** frontend tests, authenticated browser video/screenshots,
request/receipt/event trace, accessibility proof, independent UX review.

### WP-04 — Real Research dispatch and candidate truth

**Depends on:** WP-01B; consumes authoritative selected StrategySpec from
WP-02.

**Closes:** GAP-R03/R04/R05.

**Owner capability:** backend research orchestration, data lineage, worker
reliability.

**Pantheon scope candidates:**

- `services/control-plane/bff/agora/research/`
- allowlisted Research adapter/dispatcher modules
- worker service configuration
- Registry/research readback adapters
- research/candidate contract and integration tests

**Required work:** remove production default candidates; implement outbox
dispatcher and backend job adoption; project progress/artifacts; build pools
only from selected versions and eligible completed artifacts; expose
strategy/version-to-current-pool lookup; explicit empty/excluded result.

**Out of scope:** inventing new quantitative algorithms, live execution,
frontend page changes.

**Acceptance:** approved plan produces a real or explicitly unavailable
backend run, checksum-bearing artifacts, and a pool whose every member traces
to Registry/research evidence. Empty input yields zero members, not fixtures.
Crash after backend create resumes without duplicate job.

**Validation:** adapter contract, lease/recovery, partial failure, scope,
idempotency, real-vs-fixture labeling, Postgres restart, no-default-candidate
source/behavior test.

**Rollback:** disable adapter/candidate generation and expose reason; never
restore production fixture population.

### WP-05 — Canonical candidate-review frontend

**Depends on:** WP-04.

**Repository:** `ajoe734/execute-plans`, target `dev`.

**Owner capability:** React/TypeScript and BFF integration.

**Scope candidates:**

- `src/agora/pages/trading-room/TradingRoomPage.tsx`
- `src/agora/components/CandidateReviewDrawer.tsx`
- candidate BFF client/types and tests

**Required work:** resolve pool from strategy/version; consolidate one drawer;
wire review/park/research/shadow/select to durable commands; show receipts and
canonical member readback; remove lens-as-pool usage and candidate fixtures.

**Acceptance:** reload after each action preserves state; conflict/degraded
states are visible; empty pool stays empty; no local-only success.

**Rollback:** retain read-only candidate view; disable writes.

### WP-06 — Trading Room authority, data adapters, compiler, and decision producer

**Depends on:** WP-01C, WP-02 readiness, WP-04 candidate truth.

**Closes:** GAP-T02/T03/T04/T06/T07/T08.

This package should be split into at least three backend tasks.

#### WP-06A — WorkspaceCompiler and authoritative proposal context

Rename/extract compiler, define typed WorkspaceIntent, resolve readiness and
freshness server-side, reject caller truth, and provide deterministic proposal
validation.

#### WP-06B — Widget query adapters

Implement only the initial allowlisted real sources. Each adapter supplies
status/as-of/cutoff/lineage and tenant tests. Unwired widgets are not available
in live registry.

#### WP-06C — Decision event/intent producer

Project owner-scoped signal/risk/runtime evidence into decision events and
create request-only governed intents/handoffs. Prove no order route.

**Owner capability:** backend domain/data integration/security; reviewer must
cover order-authority boundary.

**Pantheon scope candidates:**

- `services/control-plane/bff/agora/trading_room/`
- source-specific read-only adapters under a narrow Agora query boundary
- capability/OpenAPI schemas and integration tests
- worker/projection service configuration for decision events

**Acceptance:** workspace generation fails without authoritative readiness;
all visible widgets have real or explicit unavailable data; workspace writes
are atomic; real producer creates decision event; approval creates intent only;
tenant and restart tests pass.

**Rollback:** feature-disable affected widget/source or decision projection and
show unavailable; never fabricate normal risk or local event rows.

### WP-07 — Trading Room frontend consolidation and dead-code removal

**Depends on:** WP-05 and WP-06 contracts.

**Repository:** `ajoe734/execute-plans`, target `dev`.

**Closes:** GAP-T01/T03/T04 plus P3 cleanup.

**Owner capability:** senior React/TypeScript architecture and UX.

**Scope candidates:**

- active Trading Room page and `src/agora/trading-room/*`
- `src/agora/widgets/*`
- `src/agora/dashboard/*`
- `src/agora/AgoraApp.tsx`
- Agora tests/import guards

**Required work:** one runtime widget renderer, one revision flow, one
candidate drawer; display server data status/lineage; replace local prompt
parser with typed intent call; wire evidence/usefulness actions or remove them;
delete obsolete M0 shell, dashboard island, duplicate widgets/drawers, sample
data, and mock-mode live actions after coverage migration.

**Out of scope:** broad non-Agora frontend cleanup or Management UI changes.

**Acceptance:** route/import graph has no obsolete island; production bundle
contains no Agora candidate/widget fixtures; browser actions always produce
BFF receipt/readback; unavailable sources are honest.

**Validation:** typecheck, focused/unit tests, route/import dead-code test,
bundle/source fixture guard, desktop/mobile Playwright, accessibility.

**Rollback:** revert presentation to safe read-only canonical data; deleted
fixture/local-success paths are not restored.

### WP-08 — Owner-scoped Strategy Performance and suggestion producer

**Closes:** GAP-P01/P02 while retaining GAP-P03 foundation.

Split backend and frontend tasks.

#### WP-08A — Backend index and producer

**Pantheon scope candidates:**

- `services/control-plane/bff/agora/performance/`
- owner-scoped TradeJourney/performance adapters
- governed suggestion producer/worker
- route/store/integration tests

**Acceptance:** index is tenant/user scoped and separates observed vs
simulated; suggestions are produced from named evidence/policy; apply produces
a proposal/receipt rather than direct runtime mutation; no Agora use of global
Management attribution is necessary.

#### WP-08B — Frontend migration

**execute-plans scope candidates:**

- `src/agora/pages/strategy-performance/StrategyPerformancePage.tsx`
- Agora performance clients/types/tests

**Acceptance:** all panels use Agora-scoped endpoints; zero direct Management
attribution calls; empty/degraded states; governed action readback after reload.

**Rollback:** read-only performance projection; suggestion writes disabled.

### WP-09 — Dataset outbox dispatcher and policy worker boundary

**Depends on:** WP-01 command/service-context foundation.

**Closes:** GAP-L01/L02/L03.

Split extraction/dispatch and policy-learning changes if scopes become large.

**Owner capability:** backend distributed workflows, data privacy, ML lineage.

**Pantheon scope candidates:**

- `services/control-plane/bff/agora/dataset_extraction/`
- new handoff dispatcher worker and service configuration
- `services/policy-learning/main.py`
- `services/policy-learning/scheduler_worker.py`
- dataset/policy contracts and failure-injection tests

**Required work:** API admit-only extraction; leased extraction/outbox worker;
signed handoff to policy-learning; durable admission readback then source ACK;
remove production inline processing; preserve tenant-safe dataset authority and
fail-closed runtime effect.

**Acceptance:** one eligible event becomes DatasetVersion, pending handoff,
durably admitted candidate, ACKed handoff, and asynchronously processed
candidate. Crash at every boundary resumes without loss/duplicate. Ineligible
or private raw content never enters the dataset.

**Rollback:** pause consumers with pending outbox intact; do not ACK
undelivered data and do not enable seed fallback.

### WP-10 — Independent policy-candidate Consultation

**Depends on:** WP-09 processed candidate with lineage.

**Closes:** GAP-L04 and enforces DC-09.

**Owner capability:** Consultation workflow/governance, auth, independent
review semantics.

**Pantheon scope candidates:**

- `services/consultation/main.py`
- `services/consultation/models.py`
- `services/consultation/workflow_executor.py`
- consultation store/sponsor bridge and focused tests
- policy-learning Consultation client if contract changes

**Required work:** make intake submitted-only; assign independent executor;
collect/evaluate evidence; publish real memo; separate sponsor decision; remove
default auto-approval and fixed confidence; idempotent replay returns the
current staged result.

**Acceptance:** intake alone cannot publish/approve; producer identity cannot
review its own candidate; memo cites canonical dataset/artifact/evaluation;
sponsor decision requires separate write role/receipt; rejected/deferred paths
are first-class.

**Rollback:** hold requests submitted/pending; never fall back to terminal auto
approval.

### WP-11 — Full journey, cross-repo release, and hosted acceptance

**Depends on:** all user-journey packages above. It must not implement missing
product code; failures return to the owning task.

Split into verifier, frontend release, backend release, and hosted evidence
tasks under canonical IDs.

**Owner capability:** cross-repo integration/E2E/release; independent reviewer
must validate evidence and tenant isolation.

**Scope candidates:**

- dedicated Agora E2E/verifier tests and evidence templates in Pantheon;
- `execute-plans` Playwright coverage;
- compatibility manifest/bundle/OpenAPI generated artifacts;
- deployment workflow/evidence paths only as required for exact-pair release.

**Acceptance sequence:**

1. full winner-branch browser journey from create to governed intent;
2. real Performance readback and governed suggestion action;
3. eligible dataset -> policy candidate -> independent Consultation chain;
4. two-user/two-tenant negative suite;
5. BFF and worker restart/readback;
6. frontend generated contract matches backend capability/hash;
7. gate-before-switch deploy with live/strict/safe write defaults;
8. served FE/BFF identities match accepted manifest;
9. `/readyz` healthy with active workers and cursor agreement;
10. desktop/mobile hosted proof with exact receipt, event, artifact, dataset,
    candidate, memo, and sponsor-decision lineage.

**Out of scope:** supervisor V2 changes, Lovable, legacy host, production/live
capital activation, and fixing product defects inside the verifier task.

**Rollback:** switch back to the previous accepted exact pair and safe
read-only profile; preserve failed candidate manifest/evidence; do not edit the
manifest to match a running drifted deployment.

## 5. Independent review assignments

The later task generator should require these capability separations:

| Work | Owner capability | Reviewer capability |
|---|---|---|
| Auth/scope/command protocol | backend security/persistence | independent security + failure-injection reviewer |
| Reconstruction/provider | domain workflow/provider/privacy | schema/privacy and product-journey reviewer |
| Research/candidate | research orchestration/data lineage | quantitative lineage + tenant-isolation reviewer |
| Trading Room | backend data/decision boundary | no-order governance + frontend contract reviewer |
| Frontend consolidation | senior React/TypeScript | UX/accessibility + BFF truth reviewer |
| Performance | telemetry/evolution projection | attribution/privacy reviewer |
| Dataset/policy | privacy/distributed worker/ML | data-governance + failure-recovery reviewer |
| Consultation | consultation/governance | reviewer independent from policy-learning owner |
| Hosted closeout | release/E2E | independent evidence auditor |

Configured agent names alone do not establish independence. The materialized
task must use current identity/quota evidence and a reviewer different from the
implementer.

## 6. Stop conditions for the implementation fleet

A worker must stop and return a design blocker instead of expanding scope when:

- a required owner identity cannot be derived without guessing;
- a downstream service lacks an authenticated tenant propagation contract;
- migration encounters ambiguous legacy ownership;
- a proposed UI feature has no authoritative data producer;
- a required adapter would silently use fixture/stub data in live mode;
- an operation would create order or capital authority;
- the task would need Supervisor V2 or unrelated control-plane changes;
- the declared file scope overlaps a different active task without a merge
  order.

The correction is a new/updated governed task, not an opportunistic patch in an
adjacent worker branch.

## 7. Final task-generation checklist

Before sending a single batch to the supervisor in the later execution phase:

- refresh one bounded snapshot of `origin/dev`, open PRs, canonical tasks,
  branches, and worktrees;
- deduplicate completed or active scope once; do not chase ongoing workers and
  continuously rewrite the packet;
- map every GAP/DC ID to exactly one owning task and optional dependent tasks;
- ensure no task mixes Pantheon and `execute-plans` source changes;
- ensure no frontend task begins before its additive backend contract;
- assign explicit merge order and rollback for data/schema migrations;
- require all P0 correction gates before P1 feature completion;
- materialize through the governed bridge/command and wait for supervisor
  receipt plus canonical task rows before claiming implementation is underway.
