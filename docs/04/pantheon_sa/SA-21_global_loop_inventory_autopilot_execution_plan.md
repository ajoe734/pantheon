# SA-21 Global Loop Inventory And Autopilot Execution Plan

Date: 2026-06-27
Status: planning artifact for supervisor dispatch
Scope: Pantheon loop maturity inventory, autopilot target state, and execution-task decomposition
Primary sources:

- `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`
- `DELIVERY_CLOSURE_AND_LOOP_STATES.md`
- `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`
- `OPERATOR_ACCEPTANCE_MATRIX.md`
- `docs/deployment/source-connector-framework.md`
- `docs/deployment/runtime-telemetry-hardening-2026-06-06.md`
- `docs/04/pantheon_sa/SA-11_operating_loop_gap_analysis.md`
- `docs/04/pantheon_sa/SA-16_data_search_external_source_gap_analysis.md`
- `docs/04/pantheon_sa/SA-17_telemetry_reconciliation_evolution_gap_analysis.md`

## 1. Purpose

Pantheon already has many domain objects, API routes, seed records, BFF panels,
and service-local tests. That does not mean the loops are alive.

This document exists to make the missing controller layer explicit. It creates a
global loop inventory and turns the inventory into execution-ready task waves for
supervisor and auto-worker dispatch.

The intended end state is policy-gated autopilot:

- the system detects declared desired state;
- an owning controller reconciles desired state into actual state;
- workers and schedulers are supervised and observable;
- health and failure signals flow back into canonical stores;
- BFF and operator surfaces read truth projections, not seeds or static labels;
- governance gates still stop production-affecting actions until approved.

Autopilot does not mean every action bypasses human approval. It means the
machine handles observation, provisioning, scheduling, retry, reconciliation,
evidence capture, and safe proposal generation, while human or governance gates
remain explicit where policy requires them.

## 2. Maturity Vocabulary

Loop maturity is measured end-to-end. A loop with one strong service and three
manual gaps is still classified by the weakest required end-to-end segment.

| Level | Meaning | Closure evidence |
| --- | --- | --- |
| `manual` | Operators or developers must perform the next transition by hand. | Runbook or manual curl path only. |
| `api-only` | Canonical API/store exists, but no durable worker/controller owns the transition. | Route tests or contract tests only. |
| `scheduled` | A cron or scheduler calls something periodically, but it does not reconcile desired and actual state. | Scheduler tests and tick evidence. |
| `reconciled` | An idempotent controller continuously compares desired state with actual state and repairs drift. | Controller tests, restart tests, drift repair proof, metrics. |
| `proven-live` | The reconciled loop has runtime evidence under the target deployment mode, including liveness, recovery, and operator-visible truth. | EP4/EP5-style evidence packet, replay/readback, failure recovery drill. |

Required autopilot task terms:

- Desired state: the declaration or policy that says what should exist.
- Actual state: the observed runtime, store, schedule, worker, or downstream
  condition.
- Reconciler: idempotent controller that moves actual state toward desired
  state.
- Dispatcher: durable worker that consumes outbox/inbox commands and advances a
  saga.
- Truth projection: read model that BFF/operator surfaces may show as live
  truth.
- Gate: explicit approval boundary where autopilot may propose, but not execute,
  a production-affecting mutation.

## 3. Global Loop Inventory

This table marks current end-to-end maturity, not the maturity of the best
individual component inside the loop.

| Loop | Declared trigger | Current maturity | Target maturity | Current gap summary |
| --- | --- | --- | --- | --- |
| Source Ingestion | cron/scheduled plus manual trigger | `api-only` | `reconciled`, then `proven-live` | Connector registry and schedule APIs exist, but persona/data requirements do not auto-register connectors, do not auto-create schedules, and scheduler workers are optional. |
| Strategy Distillation | event-driven on normalized source | `api-only` | `reconciled` | Normalized sources and strategy objects exist, but no event consumer reliably turns new `SourceRecord` evidence into draft `StrategySpec` updates. |
| Alpha Replication | review-driven plus scheduled revalidation | `api-only` | `scheduled` plus `reconciled` | Research task/run APIs and artifact writeback exist, but no durable replication queue owner or revalidation worker closes the loop. |
| Persona Teaching | user-driven; preview/eval async worker | `api-only` | `reconciled` | Teaching sessions and preview/commit routes exist, but async evaluation and persona mutation gates are not owned by a durable worker. |
| Agora Interaction Evidence | command-driven interaction evidence | `api-only` | `reconciled` | Interaction records can be captured, but dataset extraction, handoff creation, and learning-evidence routing are not a governed background loop. |
| Human Imitation / Shadow Evaluation | batch/scheduled plus explicit eval command | `api-only` | `scheduled`, then `reconciled` | Policy-learning/imitation boundaries exist, but trace-to-dataset-to-eval-to-candidate flow is not scheduled or reconciled. |
| Consultation | on-demand event-driven; committee/red-team async | `api-only` | `reconciled` | Consultation state, evidence, transcript, memo, and outbox stores exist, but committee/red-team workflow execution and handoff consumption are not durable. |
| Promotion / Deployment | explicit command-driven async saga | `api-only` | `reconciled`, then `proven-live` | Approval, deployment plans, saga models, and runtime-manager authority exist, but no always-on saga dispatcher closes plan-to-binding-to-runtime feedback. |
| Capital Pool Execution | continuous runtime loop | `manual` with historical EP4 proof | `reconciled`, then `proven-live` | Runtime execution has governed-paper evidence, but active bindings are not guaranteed to reconcile into exactly one supervised worker with restart, stop, and signal isolation. |
| Telemetry / Reconciliation | telemetry event-driven; reconciliation scheduled and incident-triggered | `api-only` for end-to-end loop | `reconciled`, then `proven-live` | Telemetry ingest is relatively mature, but reconciliation, drift report, incident creation, postmortem, and evolution handoff are not an end-to-end automatic chain. |
| Evolution | threshold-triggered plus daily sweep | `api-only` | `scheduled` plus `reconciled` | Evolution decision lifecycle exists, but threshold sweeps, incident/postmortem proposal generation, and approved-action dispatch are not complete. |
| BFF Health Monitoring | continuous plus event-driven | `api-only` | `reconciled`, then `proven-live` | BFF aggregates health/read models, but no continuous BFF/downstream monitor writes authoritative telemetry and incident signals for operator truth. |

## 4. Cross-Loop Root Cause

The recurring failure mode is surface-first construction:

1. A service-local route, schema, fixture, seed, or BFF panel is implemented.
2. Contract tests pass for that layer.
3. No task owns the cross-layer transition.
4. Operators see metadata or seeded projections and assume the loop is alive.

The missing layer is not another panel. It is the set of reconcilers, workers,
schedulers, dispatchers, and truth projections that make desired state flow into
actual state.

Every autopilot execution task must therefore identify:

- the desired-state source;
- the authoritative write owner;
- the actual-state observation path;
- the idempotency key;
- the retry/backoff/dead-letter policy;
- the liveness metric;
- the BFF/operator truth projection;
- the restart and drift-repair proof.

## 5. Autopilot Architecture Standard

All future loop workers should follow the same controller shape unless a
domain-specific policy explicitly overrides it.

### 5.1 Controller Contract

Each reconciler must expose or document:

- controller name and owned loop;
- desired-state input query;
- actual-state observation query;
- reconciliation interval or event trigger;
- idempotency key and conflict behavior;
- max concurrency and lease/lock strategy;
- retry, backoff, and DLQ behavior;
- metrics and health endpoint fields;
- audit events emitted for every mutation;
- dry-run mode for supervisor preview;
- replay mode for recovery and deterministic acceptance tests.

### 5.2 Worker Process Contract

Every always-on or scheduled worker must be:

- represented in `docker-compose.yml` or the deployment manifest;
- started by default when its loop is required for dev/staging truth;
- configured with `restart` semantics appropriate to the environment;
- safe to run more than once if HA later creates duplicate workers;
- observable through health, metrics, and recent heartbeat fields;
- able to stop cleanly without losing in-flight outbox/inbox records.

### 5.3 Store And Event Contract

Controllers should prefer durable outbox/inbox patterns:

- source writes and controller decisions must be persisted before side effects;
- workers must ack only after durable downstream completion;
- duplicates must be harmless;
- stale desired state must be detected by version or expected snapshot;
- every state transition must be replayable from canonical records.

### 5.4 BFF Truth Contract

BFF may aggregate, but it must not invent liveness.

For each loop, BFF-visible status must show:

- desired-state presence;
- controller health;
- last successful reconciliation;
- last failed reconciliation and reason;
- downstream actual-state status;
- whether the panel is reading a seed/snapshot, a registry, or a live truth
  projection.

## 6. Execution Waves

The waves below are ordered to avoid building autopilot on top of false liveness.
Each wave should produce task archive records when dispatched. This planning file
does not itself mark any execution task as started or complete.

### Wave 0 - Loop Inventory Substrate

Goal: make loop truth machine-readable before implementing more controllers.

Tasks:

| Task ID | Title | Depends on | Output |
| --- | --- | --- | --- |
| `LOOP-AUTO-000` | Define loop catalog schema and maturity registry | none | Static catalog schema for loop id, desired state, actual state, owner, maturity, target, evidence. |
| `LOOP-AUTO-001` | Publish current inventory read model | `LOOP-AUTO-000` | JSON or service endpoint that exposes this SA-21 inventory for BFF/operator inspection. |
| `LOOP-AUTO-002` | Add completion guardrails for loop claims | `LOOP-AUTO-000` | Checklist or script that rejects "done" claims without controller, liveness, and evidence fields. |

Acceptance:

- every loop in `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md` has a stable `loop_id`;
- each loop has one current maturity and one target maturity;
- inventory can distinguish seed/fixture, registry metadata, scheduled tick, and
  reconciled live proof;
- no loop can be marked `reconciled` without an owner, desired-state query,
  actual-state query, and restart behavior.

### Wave 1 - Source, Persona Requirements, And Search Index Autopilot

Goal: close the exact gap where persona metadata says `tw_price_daily` but no
live connector and schedule are guaranteed.

Tasks:

| Task ID | Title | Depends on | Output |
| --- | --- | --- | --- |
| `LOOP-AUTO-SRC-001` | Add persona data requirement schema | `LOOP-AUTO-000` | First-class `required_data_sources` model with dataset id, market, cadence, connector candidates, policy gates. |
| `LOOP-AUTO-SRC-002` | Implement source provisioning reconciler | `LOOP-AUTO-SRC-001` | Persona requirement to connector registration and schedule creation controller. |
| `LOOP-AUTO-SRC-003` | Harden source scheduler supervision | `LOOP-AUTO-SRC-002` | Required scheduler service with restart semantics, readiness, and missed-tick metrics. |
| `LOOP-AUTO-SRC-004` | Wire SourceHealth truth projection into persona/BFF panels | `LOOP-AUTO-SRC-002`, `LOOP-AUTO-SRC-003` | Persona panels show real connector, schedule, last fetch, last push, and failure reason. |
| `LOOP-AUTO-SRC-005` | Connect source completion to search index refresh truth | `LOOP-AUTO-SRC-003` | Search index materialization is driven by completed source records or a supervised scheduler, not optional profile drift. |

Acceptance:

- adding a persona requirement for `tw_price_daily` creates or verifies the
  correct connector and schedule without manual API calls;
- duplicate controller ticks do not create duplicate connector or schedule
  records;
- scheduler restart recovers missed due schedules;
- FinMind or payload-push source classes report truthful health even when static
  pull schedules are not applicable;
- BFF/persona panel no longer treats static labels as live source truth.

### Wave 2 - Runtime Fleet And Capital Execution Reconciliation

Goal: guarantee active runtime bindings become supervised runtime workers.

Tasks:

| Task ID | Title | Depends on | Output |
| --- | --- | --- | --- |
| `LOOP-AUTO-RT-001` | Runtime fleet desired-state query | `LOOP-AUTO-000` | Query active paper/canary bindings and runtime policy envelopes from runtime-manager. |
| `LOOP-AUTO-RT-002` | Managed paper runtime fleet reconciler | `LOOP-AUTO-RT-001` | Exactly-one-worker controller for each active paper binding. |
| `LOOP-AUTO-RT-003` | Runtime session reaper and restart alignment | `LOOP-AUTO-RT-002` | Stale monitoring sessions are ended; restarted workers create fresh sessions. |
| `LOOP-AUTO-RT-004` | Runtime-aware signal isolation | `LOOP-AUTO-RT-002` | Signals are scoped by runtime or binding identity; mismatches are rejected or dead-lettered. |
| `LOOP-AUTO-RT-005` | Runtime fleet evidence packet | `LOOP-AUTO-RT-002`, `LOOP-AUTO-RT-003`, `LOOP-AUTO-RT-004` | Restart, kill-one-worker, retire-binding, heartbeat, and signal-isolation proof. |

Acceptance:

- stack restart recreates workers for all active paper bindings;
- killing one worker restarts only that worker and restores heartbeat;
- retiring or pausing a binding stops its worker;
- no runtime can consume another binding's signal;
- BFF runtime board reads binding, worker, session, heartbeat, and telemetry
  truth separately.

### Wave 3 - Deployment Saga Dispatcher

Goal: turn approved deployment plans into runtime bindings through a durable
saga, not manual endpoint stepping.

Tasks:

| Task ID | Title | Depends on | Output |
| --- | --- | --- | --- |
| `LOOP-AUTO-DEP-001` | Deployment outbox consumer | `LOOP-AUTO-000` | Durable worker that consumes deployment saga outbox events. |
| `LOOP-AUTO-DEP-002` | Runtime-manager dispatch adapter | `LOOP-AUTO-DEP-001`, `LOOP-AUTO-RT-001` | Idempotent plan-to-binding command adapter. |
| `LOOP-AUTO-DEP-003` | Saga progress feedback and failure DLQ | `LOOP-AUTO-DEP-001`, `LOOP-AUTO-DEP-002` | Saga status updates, retry policy, blocked reason, DLQ replay. |
| `LOOP-AUTO-DEP-004` | Promotion/deployment BFF truth split | `LOOP-AUTO-DEP-003` | BFF shows approval, plan, saga, binding, and runtime fleet stages separately. |

Acceptance:

- an approved immutable artifact and deployment plan can be dispatched once and
  retried safely;
- duplicate outbox events do not create duplicate bindings;
- failed runtime-manager calls enter retry/DLQ with reason;
- operator can see whether a failure is approval, plan, saga, binding, or
  runtime-fleet related.

### Wave 4 - Telemetry, Reconciliation, Drift, And Incident Autopilot

Goal: make runtime telemetry cause reconciliation and incident outcomes without
manual POSTs.

Tasks:

| Task ID | Title | Depends on | Output |
| --- | --- | --- | --- |
| `LOOP-AUTO-TEL-001` | Telemetry readiness and durable writer guardrail audit | `LOOP-AUTO-000` | Confirm telemetry table readiness, DLQ, writer metrics, and replay semantics. |
| `LOOP-AUTO-TEL-002` | Scheduled reconciliation worker | `LOOP-AUTO-TEL-001`, `LOOP-AUTO-RT-002` | Periodic binding/run reconciliation from telemetry truth. |
| `LOOP-AUTO-TEL-003` | Incident-triggered reconciliation listener | `LOOP-AUTO-TEL-002` | Runtime anomalies trigger immediate reconciliation. |
| `LOOP-AUTO-TEL-004` | Drift report to incident classifier | `LOOP-AUTO-TEL-002`, `LOOP-AUTO-TEL-003` | Threshold breaches open or update incidents with dedupe. |
| `LOOP-AUTO-TEL-005` | Operator evidence and replay suite | `LOOP-AUTO-TEL-004` | Replays order rejection spike, heartbeat loss, PnL drift, and recovery cases. |

Acceptance:

- telemetry heartbeat loss or order rejection spike can open an incident without
  manual reconciliation POST;
- duplicate telemetry or duplicate reconciliation ticks do not duplicate
  incidents;
- incident payload links telemetry event ids, binding id, runtime id, and
  reconciliation record ids;
- BFF incident/runtime panels agree on the same authoritative projection.

### Wave 5 - Postmortem And Evolution Proposal Autopilot

Goal: turn incidents into governed improvement proposals without direct
production mutation.

Tasks:

| Task ID | Title | Depends on | Output |
| --- | --- | --- | --- |
| `LOOP-AUTO-EVO-001` | Incident-to-postmortem draft worker | `LOOP-AUTO-TEL-004` | Resolved incidents create or update postmortem drafts with linked evidence. |
| `LOOP-AUTO-EVO-002` | Postmortem-to-evolution proposal bridge | `LOOP-AUTO-EVO-001` | Published postmortems can propose `EvolutionDecision` records. |
| `LOOP-AUTO-EVO-003` | Evolution daily sweep worker | `LOOP-AUTO-EVO-002` | Threshold/cooldown-governed sweep proposes missing decisions. |
| `LOOP-AUTO-EVO-004` | Approved evolution action dispatcher | `LOOP-AUTO-EVO-002`, `LOOP-AUTO-DEP-001` | Approved decisions dispatch to research, deployment, or runtime command paths through gates. |
| `LOOP-AUTO-EVO-005` | Evolution rollback and follow-through proof | `LOOP-AUTO-EVO-004` | Evidence that approved rollback or mitigation commands reach runtime-manager/deployment safely. |

Acceptance:

- incident resolution does not disappear into a manual backlog;
- postmortem publication can create exactly one evolution proposal per target
  and incident cluster;
- daily sweep respects cooldown and active-decision locks;
- approved production-affecting evolution actions require the correct gate;
- BFF shows proposed, reviewed, approved, dispatched, and executed stages
  separately.

### Wave 6 - Strategy, Research, Teaching, Imitation, And Consultation Workers

Goal: close the knowledge and learning loops after source/runtime truth is
available.

Tasks:

| Task ID | Title | Depends on | Output |
| --- | --- | --- | --- |
| `LOOP-AUTO-KNOW-001` | Source-to-strategy distillation worker | `LOOP-AUTO-SRC-005` | New normalized sources enqueue distillation jobs and update draft strategy heads. |
| `LOOP-AUTO-KNOW-002` | Alpha replication queue and revalidation worker | `LOOP-AUTO-KNOW-001` | Reviewed strategy specs enter replication queue and scheduled revalidation. |
| `LOOP-AUTO-KNOW-003` | Persona teaching async preview/eval worker | `LOOP-AUTO-SRC-004` | Teaching sessions run async evaluation before any persona-affecting commit. |
| `LOOP-AUTO-KNOW-004` | Agora evidence dataset extraction worker | `LOOP-AUTO-KNOW-003` | Interaction evidence becomes governed learning datasets without touching runtime authority. |
| `LOOP-AUTO-KNOW-005` | Human imitation and shadow evaluation scheduler | `LOOP-AUTO-KNOW-004`, `LOOP-AUTO-TEL-005` | Trace datasets run imitation/shadow eval and produce gated candidates. |
| `LOOP-AUTO-KNOW-006` | Consultation workflow executor | `LOOP-AUTO-KNOW-001` | Committee/red-team participants, memo generation, and governance handoff run through durable workflow. |

Acceptance:

- source records can update strategy drafts without manual re-distillation;
- strategy replication remains review-gated but revalidation is scheduled;
- teaching commits cannot affect persona behavior without async eval proof;
- Agora evidence is routed into Observe/Learn datasets only, never direct runtime
  mutation;
- imitation candidates must pass experiment, approval, and deployment gates;
- consultation handoffs are consumed exactly once or reported as blocked.

### Wave 7 - BFF Operator Truth And Cross-Loop Closure

Goal: ensure operators can see which loops are truly alive.

Tasks:

| Task ID | Title | Depends on | Output |
| --- | --- | --- | --- |
| `LOOP-AUTO-BFF-001` | Loop health read model | `LOOP-AUTO-001` | BFF endpoint/panel for loop maturity, controller health, and evidence. |
| `LOOP-AUTO-BFF-002` | BFF downstream health monitor | `LOOP-AUTO-BFF-001`, `LOOP-AUTO-TEL-001` | Continuous probes emit telemetry and incidents for BFF/downstream degradation. |
| `LOOP-AUTO-BFF-003` | Seed/snapshot truth labeling | `LOOP-AUTO-BFF-001` | Panels explicitly label seed, fixture, snapshot, registry, scheduled, and live truth. |
| `LOOP-AUTO-BFF-004` | Cross-loop operator drills | all prior waves | Drill package for source autopilot, deployment autopilot, runtime recovery, telemetry incident, evolution proposal. |

Acceptance:

- operator can list all loops and see current maturity, target maturity, owner,
  controller status, last success, last failure, and evidence packet;
- BFF outage or downstream degradation emits telemetry/incident signals;
- no panel displays seed/fixture data as if it were live;
- final drill demonstrates at least one full source-to-health flow and one full
  runtime-to-incident-to-evolution-proposal flow.

## 7. Supervisor Dispatch Rules

When this plan is converted into execution tasks, the supervisor should enforce
these rules.

1. No task may close by adding only seeds, fixtures, static metadata, or panel
   copy.
2. No `api-only` task may claim `reconciled` without a durable worker or
   controller.
3. No scheduled task may claim `reconciled` unless it repairs drift, not only
   calls an endpoint.
4. No BFF task may become the authoritative writer unless the domain policy
   already assigns BFF that authority.
5. Every controller must be idempotent under duplicate ticks and duplicate
   events.
6. Every worker must expose liveness, last success, last failure, and replay or
   DLQ behavior.
7. Every production-affecting mutation must pass the relevant governance gate.
8. Every task must update or create operator-visible truth so the same gap does
   not hide behind a green panel.
9. A task cannot raise loop maturity above the evidence it collected.
10. Final closure requires branch, commit, PR, checks, merge, and evidence
    packet or explicit blocker record.

## 8. Task Packet Template

Future task records should use this shape.

```json
{
  "task_id": "LOOP-AUTO-AREA-###",
  "title": "Short imperative title",
  "phase": "Global Loop Autopilot",
  "task_class": "execution",
  "owner": "Codex",
  "reviewer": "Claude",
  "depends_on": ["LOOP-AUTO-000"],
  "loop_ids": ["source_ingestion"],
  "current_maturity": "api-only",
  "target_maturity": "reconciled",
  "desired_state_sources": ["Persona.required_data_sources"],
  "actual_state_sources": ["source-ingest connectors", "source-ingest schedules", "SourceHealth"],
  "mutates_canonical": true,
  "artifacts": [
    "services/...",
    "docker-compose.yml",
    "docs/deployment/evidence/..."
  ],
  "acceptance": [
    "Idempotent duplicate tick behavior is tested",
    "Worker restart behavior is tested",
    "BFF truth projection reads canonical health"
  ],
  "proof_required": [
    "unit tests",
    "contract tests",
    "local service smoke",
    "restart or replay evidence"
  ],
  "non_goals": [
    "No production live-capital mutation",
    "No panel-only closure"
  ]
}
```

## 9. Proof Ladder For This Plan

Use `EXECUTION_PROOF_AND_MATURITY_LEVELS.md` as the proof ladder.

| Claim | Minimum proof |
| --- | --- |
| Loop catalog exists | EP0/EP1: schema/docs plus tests if code-backed. |
| Controller implemented | EP1: controller unit/contract tests and idempotency tests. |
| Controller works locally | EP2: local composed service smoke. |
| Cross-service loop works | EP3: stack smoke with real service boundaries. |
| Runtime-affecting loop works | EP4: governed paper execution or equivalent runtime evidence. |
| Canary/live loop works | EP5: human-approved canary/live packet with rollback proof. |

Do not use EP1 or EP2 evidence to claim `proven-live`.

## 10. Initial Priority Order

The first execution sprint should not try to build every loop at once.

Recommended order:

1. `LOOP-AUTO-000` through `LOOP-AUTO-002`.
2. `LOOP-AUTO-SRC-001` through `LOOP-AUTO-SRC-004`.
3. `LOOP-AUTO-RT-001` through `LOOP-AUTO-RT-005`.
4. `LOOP-AUTO-DEP-001` through `LOOP-AUTO-DEP-004`.
5. `LOOP-AUTO-TEL-001` through `LOOP-AUTO-TEL-005`.
6. `LOOP-AUTO-EVO-001` through `LOOP-AUTO-EVO-005`.
7. `LOOP-AUTO-KNOW-001` through `LOOP-AUTO-KNOW-006`.
8. `LOOP-AUTO-BFF-001` through `LOOP-AUTO-BFF-004`.

Rationale:

- source/persona truth must be fixed before knowledge loops can be trusted;
- runtime fleet truth must be fixed before telemetry/reconciliation can be
  treated as proof;
- deployment saga dispatch must be fixed before evolution can safely execute
  approved runtime or deployment actions;
- BFF truth work should expose every loop as it becomes real, then close with
  cross-loop drills.

## 11. Non-Goals

This plan does not authorize:

- live-capital execution;
- bypassing promotion, evolution, runtime, or emergency approval gates;
- treating seed fixtures as live evidence;
- rewriting domain ownership so BFF becomes a canonical writer;
- dispatching all listed tasks in parallel without dependency checks;
- marking any task complete without repo workflow closure and evidence.

## 12. Open Questions For First Dispatch

These should be answered during `LOOP-AUTO-000` rather than blocking this
planning artifact.

1. Should the loop catalog live as a static JSON artifact, a small service-owned
   registry, or both?
2. Which service owns persona data-source requirements: persona-control,
   source-ingest, or a new loop-controller package?
3. Which deployment target is the first proof target for autopilot: local stack,
   dev VM, or paper-runtime staging?
4. Should controller leases use Postgres advisory locks, Redis locks, or the
   existing service-local store patterns?
5. Which loops are allowed to become HA/multi-worker in the first pass, and
   which must remain singleton with explicit lease proof?

## 13. Completion Definition

This planning artifact is complete when:

- the global inventory exists in repo history;
- every canonical loop has a current maturity and target maturity;
- each loop has at least one execution task path toward autopilot;
- supervisor dispatch rules and task template are present;
- the change is committed, pushed, opened as a PR, and merged or explicitly
  reported as blocked.

The autopilot project itself is not complete until the listed task waves have
implemented, proven, and merged the relevant controllers, workers, projections,
and evidence packets.
