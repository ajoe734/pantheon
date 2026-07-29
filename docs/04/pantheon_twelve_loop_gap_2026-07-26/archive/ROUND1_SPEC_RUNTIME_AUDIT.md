# Round 1 — Specification-to-Runtime Audit

Audit timestamp: `2026-07-26T05:56:00Z`

Method: read the canonical trigger/concurrency policy from source to output,
then compare it with `origin/dev` Compose ownership, live container state,
Postgres controller truth, and current worker logs. This round asks only:
“Does the declared trigger currently cause the declared authoritative
outcome?”

## Global findings

1. Only Capital Pool Execution is supposed to be continuously resident.
   Source, Strategy, Alpha, Teaching, Agora, Imitation, Consultation,
   Deployment, Reconciliation, and Evolution must be activated by their
   declared schedule/event/command. A healthy API without its trigger owner is
   not an operating loop.
2. `docs/deployment/loop-catalog.registry.json` still marks every
   `controller_contract.status` as `not_implemented`.
3. BFF deliberately rejects runtime controller health when the catalog status
   is not `implemented` or `proven_live`.
4. `loop_controller_records` contains only:
   - `source_ingestion`: last heartbeat `2026-07-20T09:08:43Z`, expired lease;
   - `strategy_distillation`: current heartbeat at the audit time.
5. A container being `Up` proves process presence only. Several workers are
   idle, stub-backed, or functionally degraded.

## Per-loop audit

### 1. Source Ingestion

Declared contract:

- scheduled/cron primary trigger;
- manual secondary trigger;
- bounded `SourceRecord` output;
- not continuous external crawling.

Observed:

- `source-ingest` API is healthy;
- `source-ingest-scheduler` is absent from the running fleet;
- canonical `origin/dev` config makes the scheduler opt-in, non-restarting,
  and one-tick by default;
- the only controller record is stale and its lease expired.

Verdict: trigger unavailable by default. External-egress safety is correct,
but the implementation conflates safe scheduled reconciliation with the
permission to perform a provider pull.

Required runtime outcome:

- a continuously supervised due-state reconciler;
- external fetch executed only under exact connector/host policy;
- missed schedule catch-up;
- current controller truth and SourceHealth readback.

### 2. Strategy Distillation

Declared contract:

- event-driven on new normalized source;
- batch catch-up secondary path;
- writes mutable `StrategySpec` draft only.

Observed:

- worker is resident and records a current controller heartbeat;
- current ticks have no eligible sources;
- trigger is polling/catch-up rather than a transactionally linked source
  event;
- queue and seed/evidence paths include JSONL files.

Verdict: process is alive, positive product flow is unproved, and event
durability is incomplete.

### 3. Alpha Replication

Declared contract:

- reviewed StrategySpec admission;
- scheduled revalidation;
- authoritative `ExperimentRun`;
- no automatic full replication of every draft.

Observed:

- worker is resident;
- its own module describes the output as stub ExperimentRun records;
- allowed dispatch modes are `stub`, `handoff_only`, and `manual`;
- `production_activation` is disabled;
- no current loop controller record exists.

Verdict: safe stub/handoff component, not the specified replication loop.

### 4. Persona Teaching

Declared contract:

- user command creates teaching session;
- preview/evaluation is asynchronous;
- evaluation must pass before persona mutation.

Observed:

- service and preview/eval worker are healthy;
- job lease/fencing implementation and contract evidence exist;
- no current positive hosted job was observed;
- no global teaching loop controller record exists.

Verdict: strongest component implementation, but current hosted terminal
readback and loop-level truth are missing.

### 5. Agora Interaction Evidence

Declared contract:

- user/command-driven evidence capture;
- async or command-backed dataset extraction and handoff;
- Observe/Learn only; never direct runtime mutation.

Observed:

- capture/backlog/DLQ/process routes exist;
- no supervised dataset extraction process is present;
- no explicit orchestrator currently owns the process command;
- no global controller record exists.

Verdict: durable-looking surfaces exist, but execution ownership is absent.

### 6. Human Imitation / Shadow Evaluation

Declared contract:

- batch/scheduled evaluation;
- produces model/policy/candidate;
- candidate requires experiment, approval, and deployment.

Observed:

- service is healthy;
- scheduler is an opt-in Compose profile and not running;
- scheduler has no restart or health contract in canonical Compose;
- production adapters are disabled.

Verdict: schedule trigger and authoritative training/evaluation outcome are
unavailable.

### 7. Consultation

Declared contract:

- on-demand event-driven;
- committee/red-team asynchronous workflow;
- memo/handoff is advisory or review-bound.

Observed:

- Consultation API is healthy;
- `workflow_executor.py` exists;
- no consultation executor service is declared/running in Compose;
- existing workflow tests patch HTTP into in-process TestClient boundaries.

Verdict: API surface is live; asynchronous workflow loop is not deployed.

### 8. Promotion / Deployment

Declared contract:

- explicit command;
- durable async saga;
- immutable approved artifact to DeploymentPlan to RuntimeBinding.

Observed:

- deployment outbox consumer is running;
- historical positive, timeout, retry, and kill-switch artifacts exist;
- the accepted proof is not bound to the current replacement dev deployment;
- no global controller record exists.

Verdict: likely functional component chain, but no current authoritative
product proof or accepted controller truth.

### 9. Capital Pool Execution

Declared contract:

- only continuously resident execution loop;
- active RuntimeBinding to orders, fills, positions, and heartbeat;
- strict isolation from control-plane learning/governance activity.

Observed:

- paper fleet reconciler and signal producer are running;
- six runtime summaries exist;
- paper order activity is present;
- no live-capital proof is requested or permitted;
- signal consumers preserve fail-open compatibility for missing binding,
  runtime, and capital-pool scope.

Verdict: paper execution is active, but strict isolation and product recovery
proof are incomplete.

### 10. Telemetry / Reconciliation

Declared contract:

- telemetry event-driven;
- reconciliation scheduled and incident-triggered;
- produces DriftReport and IncidentCase without affecting runtime.

Observed:

- telemetry API and reconciliation service are healthy at process level;
- drift consumer is `degraded`;
- all six runtime summaries are rejected for missing top-level `trace_id`;
- last consumer success was `2026-07-24T04:30:52Z`;
- scheduled reconciliation repeatedly times out after three attempts.

Verdict: actively broken in current dev.

Direct contract contradiction:

- telemetry summary removes top-level `trace_id` when a later heartbeat omits
  it;
- reconciliation requires top-level `trace_id` and does not consume the
  preserved lifecycle identity.

### 11. Evolution

Declared contract:

- threshold trigger plus daily sweep;
- cooldown and one-active-decision-per-target;
- produces governed EvolutionDecision.

Observed:

- threshold, daily, and dispatch workers are resident;
- six summaries are evaluated and zero candidates produced;
- five lack numeric drawdown;
- one lacks approved expected-drawdown baseline;
- documented rollback follow-through still contains an operator/dispatch gap
  before Runtime Manager.

Verdict: trigger processes exist, but current input contract and terminal
action dispatch are incomplete.

### 12. BFF Health Monitoring

Declared contract:

- continuous probe plus error-rate event trigger;
- health metric to telemetry to incident;
- degraded BFF does not affect active runtimes.

Observed:

- background monitor starts inside BFF;
- default target set covers only telemetry, incidents, runtime manager,
  persona, and deployment;
- monitor uses fake trading binding sentinel values;
- its own module states those events are expected to enter telemetry DLQ when
  binding validation is active;
- incidents are created with empty `telemetry_event_ids`;
- probe counters and tracked incident IDs are process memory.

Verdict: operator health display exists, but the declared
metrics-to-telemetry-to-incident chain is self-contradictory and incomplete.

## Round-1 delta register

| Severity | Gap |
| --- | --- |
| P0 | Telemetry summary/consumer identity mismatch |
| P0 | Scheduled reconciliation timeout |
| P0 | Evolution lacks drawdown/baselines and yields no candidates |
| P0 | BFF health telemetry is expected to DLQ |
| P1 | Source scheduled controller not active |
| P1 | Alpha produces stub/local run rather than authoritative ExperimentRun |
| P1 | Agora dataset processing has no execution owner |
| P1 | Shadow scheduler not active |
| P1 | Consultation executor not deployed |
| P1 | Evolution approved-action dispatch contains manual discontinuity |
| P1 | Capital signal isolation is fail-open for unscoped signals |
| P2 | Ten loops have no current global controller record |
| P2 | All twelve catalog controller contracts remain `not_implemented` |

## Round-1 conclusion

No loop may be promoted from this round alone. Capital has real governed-paper
activity, Strategy Distillation and Teaching have meaningful running
components, and Deployment has historical cross-service proof. Those facts
reduce implementation work but do not satisfy current end-to-end acceptance.
