# Pantheon Current Product Code GAP — 2026-08-22

Date: 2026-08-22

Status: current code-first and runtime-backed gap truth

This document is a read-only audit. It does not create, supersede, reopen, or
dispatch canonical tasks. Existing nonterminal task identities are retained and
reused in the implementation plan.

## 1. Executive conclusion

Pantheon has moved materially beyond the 2026-08-20 baseline. Twenty-one of the
twenty-seven product-functional-closure tasks are terminal completed, the Source
controller is bounded and reconcile-only, executable RuntimeBinding projection
exists, the paper fleet no longer consumes roughly 12 GiB, the main Agora
backend/frontend wiring exists, and Management strict-live surfaces no longer
silently report seed or mock mutation success.

The product is still not functionally closed. Seven current gap groups remain:

1. the root dev deploy script can truncate the canonical
   `public.telemetry_events` source while pruning Management AI telemetry;
2. the Lifecycle relational projector schema exists but is not the active
   writer or reader, while the legacy JSON projection occupies about 18 GiB;
3. a manual-only Source policy and a long-lived paper binding currently converge
   on a permanently unhealthy producer once its snapshot ages past 24 hours;
4. twelve-loop truth accepts 0/12 current controller records because static
   controller declarations and current runtime evidence remain inconsistent;
5. the Management journey PR contains route-mocked, hosted-skipped tests rather
   than live product proof;
6. the Agora product journey has no current hosted browser E2E; and
7. backend/frontend consolidation and exact current hosted acceptance have not
   run, so legacy paths and production seed imports remain.

The first two gaps are the most immediate functional-integrity risks. They are
not JWT, reviewer, or task-state problems.

## 2. Frozen audit baseline

### 2.1 Source repositories

| Repository | Audited ref | SHA |
|---|---|---|
| `ajoe734/pantheon` | `origin/dev` | `8cb621e5bac74225d6b7a1a94d4650013aed470d` |
| `ajoe734/execute-plans` | `origin/dev` | `693d8612218e5ec6620c80ab7a16d3429e842f6c` |

The Pantheon source checkout used for this audit was a clean detached worktree
created from `origin/dev`; unrelated changes in `/home/lupin/pantheon` were not
used as product truth.

### 2.2 Hosted dev identity

Observation time: 2026-08-22, approximately 10:22 UTC.

| Surface | Hosted identity/posture |
|---|---|
| BFF `/bff/version` | `97945de7c5193baa9832f6c02674714d889577b9` |
| Frontend deployment manifest | `693d8612218e5ec6620c80ab7a16d3429e842f6c` |
| Frontend profile | `read-only` |
| BFF mode | strict auth; dev-login enabled |
| Source controller | `reconcile_only`; `MAX_TICKS=0` |
| Lifecycle reader | `json` |
| Lifecycle relational writer | `disabled`; projection DSN empty |

The served FE/BFF manifest is internally consistent for its admitted pair, but
the backend is not the current Pantheon `origin/dev`. Exact-pair admission is
therefore historical accepted deployment evidence, not current source closure.

### 2.3 Canonical program state

The 2026-08-20 functional-closure catalog currently reads:

| State | Count | Tasks |
|---|---:|---|
| completed | 21 | plan, component, integration, and three grouped L12 E2E tasks |
| blocked | 3 | Agora journey, Management journey, cross-loop truth |
| todo | 3 | backend consolidation, frontend consolidation, hosted acceptance |

An additional `PFG-L12-HUMAN-E2E-LIVE-R2-20260821` correction is terminal
completed and merged, but is newer than the hosted backend.

Task completion is used only as delivery history. The conclusions below come
from current code and runtime readback.

## 3. Classification rules

Every finding is classified as one of:

- **code gap** — an owner, contract, transition, or caller is missing or wrong;
- **runtime/config gap** — code exists but current Compose/hosted configuration
  selects an incomplete path;
- **data gap** — canonical input was lost or cannot be reconstructed from an
  authoritative source;
- **acceptance gap** — the product path may exist but the required current-dev
  proof is absent or invalid; or
- **cleanup gap** — replacement exists, but duplicate/legacy production wiring
  has not been retired after proof.

These categories must not be closed interchangeably. A JWT cannot repair lost
source data; a PR review cannot bound a 3 GiB state file; and a fixture E2E does
not prove a hosted product journey.

## 4. Current gap matrix

| ID | Area | Class | Priority | Current truth | Required closure |
|---|---|---|---|---|---|
| G-01 | deploy telemetry prune | code | P0 | root dev deploy can truncate `public.telemetry_events` | schema allowlist, negative test, pre/post counts |
| G-02 | Lifecycle projection | runtime/code | P0 | relational tables exist but writer/reader disabled; legacy JSON is 18 GiB | restore/baseline decision, backfill, shadow parity, reader cutover, legacy retirement |
| G-03 | paper input lifecycle | code/design | P0 | valid binding becomes unhealthy after snapshot max age | bounded paper session state and explicit pause/resume/retire behavior |
| G-04 | twelve-loop truth | code/runtime | P0 | 0/12 current records accepted; static declarations reject real owners | reconcile stable owner contracts and runtime records; rework PR #5122 |
| G-05 | Management journey | test/harness | P1 | PR #601 mocks BFF and skips hosted | credentialed no-route-mock hosted browser E2E |
| G-06 | Agora journey | acceptance | P1 | product wiring exists; no full hosted browser journey | operator-live paper-only journey and reload proof |
| G-07 | consolidation/release | cleanup/delivery | P1/P2 | legacy callers, two FE adapter families, seed imports, stale hosted backend | run existing consolidation tasks, then exact current pair acceptance |

## 5. G-01 — Deployment script crosses the telemetry authority boundary

### 5.1 Current code

`scripts/deploy_nonprod_vm.sh` defines
`prune_dev_management_ai_telemetry_for_disk()`. Its name and configuration gate
describe a Management AI storage cleanup. The SQL loop, however, selects every
table named `telemetry_events` in both:

```text
<configured Management AI schema>
public
```

and executes `TRUNCATE TABLE` for each selection.

Relevant code:

- `scripts/deploy_nonprod_vm.sh:1755` — prune function;
- `scripts/deploy_nonprod_vm.sh:1816` — schema selection includes `public`;
- `scripts/deploy_nonprod_vm.sh:1819` — dynamic `TRUNCATE TABLE`;
- `scripts/deploy_nonprod_vm.sh:2068` — invoked during root deploy.

`public.telemetry_events` is not Management AI disposable telemetry. It is the
canonical Lifecycle source consumed by `PostgresLifecycleSource`.

### 5.2 Observed consequence

At approximately 05:21:28 UTC, the deploy run truncated the table. Current
readback later in the day showed:

```text
public.telemetry_events count: approximately 14.5k and increasing
minimum created_at: 2026-08-22 05:21:28+00
```

This corrects the earlier transient observation of zero rows: the table did not
remain empty. New telemetry accumulated after the truncate. The functional loss
is the canonical history before 05:21:28 UTC.

No VM snapshot, machine image, database dump, or telemetry-specific backup was
found during the audit. The legacy JSON projection still contains derived
history, but derived JSON is not equivalent to canonical source events and must
not be relabelled or backfilled as source truth.

### 5.3 Required correction

- prune only the explicitly configured Management AI schema;
- reject `public`, wildcard, empty, or unexpected schemas before SQL execution;
- record target schema/table and pre/post row counts;
- add a negative integration test proving `public.telemetry_events` survives;
- require an explicit data-baseline disposition for pre-05:21 history.

Increasing timeout, adding disk, or restoring from the derived JSON would not
close this gap.

## 6. G-02 — Lifecycle relational cutover is incomplete

### 6.1 Existing implementation to retain

Pantheon already contains:

- `PostgresLifecycleSource`;
- `RelationalLifecycleProjector`;
- `trade_journey_projection` schema and seven relational tables;
- migration, parity, hosted probe, and cutover tooling;
- BFF support for a relational reader backend; and
- a legacy `LifecycleProjector` JSON implementation.

No second projector, source store, or read model is needed.

### 6.2 Current hosted state

| Observation | Value |
|---|---:|
| `LIFECYCLE_PROJECTOR_WRITER_BACKEND` | `disabled` |
| relational projection DSN | empty |
| BFF trade-journey reader | `json` |
| relational event receipts | 0 |
| relational journey stages | 0 |
| relational journeys | 0 |
| relational loop runs | 0 |
| relational controller rows | 0 |
| legacy projection directory | approximately 18 GiB |
| legacy `controller_state.json` | approximately 3.0 GiB |
| retained generations | approximately 9.1 GiB total |
| projector RSS | approximately 6.18 GiB |

The legacy worker polls every second and retains four generations. Each retained
generation is about 2.3 GiB, including roughly 2.0 GiB of journey events and
303 MiB of loop runs.

### 6.3 Why this is a product gap

The current BFF can continue showing derived legacy history, but:

- it cannot reconstruct pre-05:21 source history from canonical rows;
- a restart or state corruption requires loading multi-gigabyte JSON;
- relational cutover evidence cannot be produced with an empty relational
  projector;
- JSON and relational truth can diverge indefinitely; and
- the legacy resource cost is part of normal dev operation, not a bounded
  migration job.

### 6.4 Required correction

The relational implementation must become the sole normal writer and reader
through a staged backfill/shadow/parity/cutover. The legacy JSON projector may
remain read-only during the comparison window, then must be stopped before its
data is archived or deleted.

## 7. G-03 — Manual-only Source and long-lived paper execution have no shared lifecycle

### 7.1 Improvements already completed

- Source controller state and readiness are bounded.
- Raw Compose defaults to `SOURCE_INGEST_CONTROLLER_MODE=reconcile_only`.
- The scheduler container performs local reconcile but does not continuously
  execute providers.
- A canonical one-shot path exists for explicit test runs.
- RuntimeBinding now contains executable artifact and source-snapshot
  projection.
- The paper fleet dropped from roughly 12 GiB to about 137 MiB in the observed
  reconciler process, showing that the lifecycle cursor/compaction work was
  effective.

These changes must not be reimplemented.

### 7.2 Remaining break

The active paper binding references a valid Source market snapshot, but the
snapshot becomes invalid after `86400` seconds. The producer is currently
unhealthy with:

```text
market_input_stale: Source snapshot is older than maximum 86400s
```

The system has no product transition explaining what a long-lived paper session
does when Source egress is intentionally manual-only.

### 7.3 Required correction

Use the existing RuntimeBinding, Source snapshot, producer, and fleet. Express
the bounded paper-session lifecycle through the existing RuntimeBinding
transitions:

```text
prepared
  -> active_with_fresh_snapshot
  -> pending_pause
  -> paused (reason_code=market_input_stale)
  -> resumed_with_new_snapshot | retired
```

`paused_stale_input` may be used as a projected functional label, but it is not
a new RuntimeBinding enum or state store. The existing `paused` status plus
structured `market_input_stale` metadata must stop signal production and child
work, preserve readback, identify the stale snapshot, and require an explicit
bounded Source refresh plus resume/redeploy. It must not trigger continuous
provider pull automatically.

## 8. G-04 — Twelve-loop truth still mixes stable catalog and runtime admission

### 8.1 Current readback

Authenticated hosted `/bff/v5/loop-health` returned:

| Metric | Value |
|---|---:|
| rows | 13 |
| canonical loops | 12 |
| composite overlays | 1 |
| accepted current controller records | 0 |

The static catalog marks Loops 4–12 as `controller.status=not_implemented` even
though current code contains the relevant owners and observation writers.
Loop 12 has a fresh heartbeat but is still rejected.

### 8.2 PR #5122 assessment

Open PR #5122 correctly attempts to:

- remove `current_maturity`, `maturity`, evidence profile, and execution-task
  history from the runtime response;
- derive `runtime_maturity` from current records; and
- add a stimulus-driven cross-loop suite.

It is not complete as currently written. `_project_controller_health()` still
requires the static controller contract status to be `implemented` or
`proven_live`, and runtime qualification still requires the expected controller
name from that declaration. Because Loops 4–12 retain `not_implemented`/empty
contracts, deploying the PR alone cannot reliably make twelve current records
acceptable.

### 8.3 Required correction

The static catalog should retain only stable facts:

- loop ID and classification;
- specification and trigger model;
- canonical owner/controller identity;
- desired/actual query contract; and
- restart/idempotency contract.

It must remove task history and declared current maturity. The stable controller
contracts must be reconciled to the owners that actually exist for all twelve
loops. Current maturity, liveness, failure, and evidence must be derived only
from fresh owner records. PR #5122 should be amended, not duplicated by a second
truth implementation.

## 9. Twelve-loop functional matrix

| Loop | Current code truth | Remaining gap |
|---|---|---|
| 1 Source | bounded controller, manual one-shot, reconcile-only default | current owner record not accepted; one-shot used only during explicit test |
| 2 Distillation | SourceRecord event admission and durable queue exist | current owner record and same-run hosted truth |
| 3 Alpha | reviewed admission, controller, experiment terminal flow exist | current owner record and same-run hosted truth |
| 4 Teaching | preview/eval owner and corrected live E2E exist | catalog contract says not implemented; newest proof not hosted |
| 5 Agora evidence | durable evidence/handoff and reconstruction flow exist | catalog contract and full browser journey |
| 6 Imitation | durable handoff scheduler and Research HTTP handoff exist | catalog contract; direct discovery cleanup after proof |
| 7 Consultation | real provider adapter, executor, Governance handoff exist | catalog contract and same-run hosted contribution/receipt |
| 8 Deployment | executable RuntimeBinding contract exists | catalog contract and stale-input session transition |
| 9 Capital | producer/fleet/order/fill flow exists | stale snapshot makes producer unhealthy; catalog contract |
| 10 Reconciliation | telemetry/reconciler/incident owner exists | Lifecycle source/cutover and current truth record |
| 11 Evolution | threshold/daily/dispatch owner exists | Lifecycle source/cutover and current truth record |
| 12 BFF health | functional worker attribution and heartbeat exist | fresh record rejected by stale catalog contract |

The grouped L12 component/deployed tests demonstrate that substantial code
exists. They do not replace the missing current hosted cross-loop truth.

## 10. G-05 — Management product journey test is not a hosted test

### 10.1 Product code that is already complete

The current frontend/backend code includes:

- strict-live contract mismatch fails visibly instead of returning seed;
- read-only mutation attempts return typed disabled/unavailable;
- Formula, Activity, Paper/Live, and Postmortem surfaces use live adapters or
  typed unavailable states;
- domain action routing and terminal receipt support;
- Management AI provider fallback and conversation persistence; and
- UI action registries for navigation, drawer, focus, and governed action
  routing.

The BFF dev-login and Management NL endpoint were independently exercised:
`/bff/me` returned 200 and `/bff/management/nl/ask` returned an accepted request
and provider answer when supplied a real short-lived token.

### 10.2 Invalid acceptance in PR #601

The open Management journey PR:

- calls `test.skip()` for an external/hosted target;
- intercepts `**/bff/**` and returns fixed JSON;
- supplies a fixture bearer and in-memory responses;
- does not prove an actual provider call;
- does not execute and read back a domain mutation; and
- mainly asserts page root/heading/dialog presence.

This is a component fixture test, not the task's required hosted product E2E.
It must be rewritten or split and renamed; it must not be merged as closure
evidence.

### 10.3 Required correction

The hosted workflow must acquire a short-lived dev-login token server-side,
inject the browser session without embedding credentials in the bundle, disable
all route interception, and prove network provenance, terminal action receipt,
exactly-once semantics, reload persistence, and real provider answer.

## 11. G-06 — Agora is wired but has no complete hosted product journey

### 11.1 Closed implementation gaps

- Workshop messages invoke reconstruction.
- A durable reconstruction worker and result projection exist.
- Research outbox consumer and stage dispatcher exist.
- candidate, decision, performance, and widget projections exist.
- Trading Room resolves a real candidate pool ID.
- the active page uses the shared BFF-backed CandidateReviewDrawer.
- durable policy-learning and Consultation mechanisms are reused.

### 11.2 Remaining gap

`execute-plans/origin/dev` contains no `e2e/agora-product-journey.spec.ts`.
The served frontend is the read-only artifact, so a durable browser journey from
Workshop through Consultation has not been executed. The existing blocked task
correctly requires an operator-live paper-only candidate and governed short-lived
session.

This is primarily an acceptance/harness gap unless the live journey exposes a
new product defect. It must not trigger another Agora store, drawer, dispatcher,
or consultation engine.

## 12. G-07 — Consolidation and current hosted acceptance remain

### 12.1 Backend paths awaiting caller disposition

- `services/policy-learning/agora_dataset_authority.py` direct database
  discovery remains while the normal scheduler uses durable handoff;
- `services/source_ingestion/scheduler_worker.py` remains beside the canonical
  controller and manual one-shot utility;
- compatibility-only `static-paper-runtime` remains in a Compose profile;
- static loop catalog still contains runtime maturity/task history fields; and
- legacy JSON Lifecycle writer remains normal runtime code.

### 12.2 Frontend paths awaiting caller disposition

The important active Agora duplicates are already corrected: the active Trading
Room uses the shared drawer and real pool ID. Remaining cleanup includes:

- `src/lib/bff` and `src/lib/bff-v1` overlapping adapter families;
- ten non-test TypeScript files importing `@/mocks/seed`;
- demo/test mock-completed paths that must remain unreachable in strict live;
  and
- the fixture-only Management journey tests introduced by PR #601.

Seed fixtures do not need wholesale deletion. They must be isolated to explicit
test/demo profiles and excluded from strict-live production callers/bundles.

### 12.3 Delivery closure

After the journey and consolidation tasks pass, the final hosted release must:

- deploy the exact current Pantheon and execute-plans SHAs;
- expose the same BFF SHA through `/bff/version` and the FE manifest;
- keep Source reconcile-only before and after one named one-shot;
- run required L12, Agora, Management, and Management AI cases with zero
  required skips; and
- return the served environment to the intended read-only profile after the
  bounded operator-live acceptance.

## 13. Closed gaps that must not be reimplemented

The following 2026-08-18/20 findings are closed in current code:

- Source state recursion and unbounded readiness scans;
- raw Compose `reconcile_and_pull` default;
- Source manual bounded one-shot;
- SourceRecord-to-Distillation event admission;
- executable artifact/checksum/loader RuntimeBinding projection;
- paper fleet lifecycle cursor and compaction;
- Agora reconstruction worker, Research consumer, and projections;
- Trading Room fixed lens identity and local-only review drawer;
- Management strict-live seed fallback and fake mutation completion;
- Management real read models and synthetic panel removal;
- Management domain action routing;
- Management AI provider fallback and frontend action registry; and
- Management BFF focused read-budget improvements.

New work must compose with these owners. Rebuilding them would be duplicate
work and should be rejected during review.

## 14. Evidence limitations

- The current 14.5k telemetry count changes continuously; the stable audit fact
  is the minimum timestamp boundary at 05:21:28 UTC and the destructive SQL.
- The legacy JSON contains derived history, not recoverable canonical source
  evidence.
- Runtime resource figures are point-in-time observations and acceptance should
  use bounded thresholds, not reproduce the exact numbers.
- A task `done`, PR merged, container healthy, or accepted historical release is
  insufficient without current authority readback.

## 15. Primary code and runtime evidence map

| Subject | Primary evidence |
|---|---|
| broad telemetry prune | `scripts/deploy_nonprod_vm.sh` |
| Lifecycle JSON/relational owners | `services/trade_journey/lifecycle_projector.py` |
| Lifecycle migration/parity | `scripts/lifecycle_projector_migrate.py`, `scripts/lifecycle_projector_parity.py` |
| BFF Lifecycle backend selection | `services/control-plane/bff/main.py`, `services/control-plane/bff/read_store.py` |
| Source reconcile/manual policy | `docker-compose.yml`, `services/source_ingestion/controller_worker.py` |
| Source stored snapshot | `services/source_ingestion/main.py` and snapshot store/client modules |
| RuntimeBinding and fleet | `services/execution/runtime-manager/`, `services/execution/lean_runtime/` |
| paper stale-input failure | `services/execution/lean_runtime/paper_signal_producer.py` and hosted producer health/log |
| loop truth | `docs/deployment/loop-catalog.registry.json`, `services/control-plane/bff/loop_inventory.py` |
| pending cross-loop correction | Pantheon PR #5122, head `be4872a33436ad331ffc764942dc6e2e421f5fc9` |
| Management live adapters | `execute-plans:src/lib/bff-v1/management.ts`, `writes.ts` |
| Management real panels | `execute-plans:src/management/` |
| Management AI UI actions | `execute-plans:src/management/ai/` and action registries |
| invalid journey proof | execute-plans PR #601, head `a85918203830ed7331ee73eadce262f36ca4f821` |
| Agora active journey UI | `execute-plans:src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx`, `trading-room/TradingRoomPage.tsx` |
| shared candidate review | `execute-plans:src/agora/components/CandidateReviewDrawer.tsx` |
| remaining FE seed callers | `execute-plans:src/lib/bff/`, `execute-plans:src/lib/bff-v1/` |
| canonical task state | live V2 TaskStore resolved by `live-supervisor-mainroot-config.json` |
