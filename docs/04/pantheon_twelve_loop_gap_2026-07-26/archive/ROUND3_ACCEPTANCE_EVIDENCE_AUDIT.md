# Round 3 — Acceptance, Recovery, and Evidence Audit

Audit timestamp: `2026-07-26T06:05:00Z`

Method: ignore implementation labels and work backward from the SA-21
acceptance clauses, proof ladder, product-evidence schema, closeout guardrail,
and current deployment identity. A requirement is counted as passed only when
the evidence has the same scope as the claim and is not contradicted by newer
runtime truth.

## Universal acceptance gates

Every loop must prove all applicable rows below.

| Gate | Required evidence | Baseline |
| --- | --- | --- |
| Trigger | Real scheduled/event/command input reaches the worker | incomplete for most loops |
| Desired state | Named canonical query and non-seed record | missing from catalog |
| Actual state | Named authoritative observation and terminal readback | incomplete |
| Ownership | One durable controller/worker owns transition | missing for Source, Agora, Shadow, Consultation |
| Idempotency | Duplicate tick/event/command is harmless | local tests only or missing |
| Concurrency | Lease/fencing or uniqueness under two workers | incomplete |
| Failure truth | Error produces degraded/failure, not green | contradicted in Distillation/Teaching paths |
| Retry/DLQ | Bounded retry, durable DLQ, replay proof | inconsistent |
| Restart | Worker, DB, dependency, and stack recovery | current-host proof missing |
| Correlation | Request, event, receipt, downstream identity | broken by telemetry trace contract |
| Security | RBAC, tenant, MFA, approval and environment negatives | missing/invalid for several services |
| Safety | No live capital; no approval bypass; no direct runtime mutation | partial |
| BFF truth | desired, controller, success/failure, actual, provenance | global projection incomplete |
| Delivery | branch, commit, PR, checks, merge, current deployment identity | stale for historical packets |
| Review | distinct formal reviewer verdict | absent in most evidence |
| Final authority | protected Human/Ops verdict consumed at transition time | descriptive metadata only |

### Program closeout authority

`requires_human_ops_signoff` is not itself an enforcement mechanism. The
current generic done guard does not consume that field, and no protected
verdict service is installed in the baseline. Without a separate guard task,
the fleet could satisfy ordinary review/done flow and self-close the final task.

The remediation DAG must therefore install `L12-SIGNOFF-001` before
`L12-CLOSE-001`. The guard must require an authorized server-side Human/Ops
verdict bound to the exact program/catalog/task, closeout-manifest digest,
target, FE/BFF identities, expiry, revocation state, ledger record, and nonce.
Missing, stale, rejected, revoked, replayed, candidate-authored, fleet-authored,
or mismatched verdicts must fail both review-approved and done transitions.

## Catalog and controller admission

SA-21 prohibits `reconciled` status without owner, desired query, actual query,
and restart behavior. At baseline:

- all twelve controller contracts are `not_implemented`;
- controller names, desired queries, actual queries, restart behavior, and
  liveness fields remain planned/null for the canonical loops;
- BFF will not accept runtime records while the catalog remains in that state;
- only two controller records exist;
- even accepted controller records project downstream actual state as
  `unobserved` and do not prove desired-state presence.

Therefore no catalog maturity promotion is allowed until after the relevant
implementation and current-host drill. Flipping catalog flags is a closeout
action, not a repair.

## Per-loop acceptance reversal

### 1. Source Ingestion

SA-21 acceptance:

- Persona requirement creates/verifies connector and schedule without manual
  calls;
- duplicate controller tick creates no duplicates;
- restart catches missed schedules;
- connector classes report truthful health;
- BFF does not treat static source labels as live truth.

Missing proof:

- no current Persona requirement to scheduled pull hosted chain;
- no current supervisor/restart proof;
- no current allowed-provider target run;
- no expired credential/rate-limit/provider-isolation matrix;
- no current BFF SourceHealth terminal readback;
- evidence is pending independent review and merge admission.

### 2. Strategy Distillation

Canonical acceptance:

- normalized SourceRecord causes draft update without manual re-distillation;
- duplicates and concurrent workers are safe;
- immutable approved artifact is untouched;
- downstream failure is durable and replayable.

Missing proof:

- current dev has no positive eligible SourceRecord;
- no transactional source outbox to distillation inbox proof;
- no two-worker/fencing proof;
- no crash-after-downstream-before-ack proof;
- no real Registry outage/DLQ/replay proof;
- existing evidence remains `review_required_evidence_only`.

### 3. Alpha Replication

Canonical acceptance:

- only reviewed StrategySpec enters queue;
- scheduled revalidation produces authoritative ExperimentRun;
- rejected/unapproved spec cannot mutate research state;
- duplicate/restart/DLQ behavior is deterministic.

Missing proof:

- current output is stub/local rather than authoritative research run;
- no real registry-to-research service-boundary packet;
- no tenant collision/lease-expiry proof;
- no current deployment/controller identity;
- formal reviewer verdict is absent.

### 4. Persona Teaching

Canonical acceptance:

- teaching session is asynchronous;
- evaluation failure prevents persona mutation;
- successful commit has authoritative before/after readback;
- restart does not lose or duplicate the job.

Existing strength:

- product evidence passes the current closeout replay.

Remaining proof:

- current replacement-dev session-to-persona terminal chain;
- service inbound RBAC/tenant enforcement;
- HA persistence and two-worker recovery;
- global controller truth and current deployment identity.

The historic accepted packet remains useful contract evidence but is not a
blanket current-host operability result.

### 5. Agora Interaction Evidence

Canonical acceptance:

- all supported interactions enter governed evidence;
- extraction produces Observe/Learn dataset and handoff only;
- duplicate evidence is harmless;
- processor failure is visible/replayable;
- no runtime/promotion mutation occurs.

Missing proof:

- no current execution owner;
- no real authenticated OperatorIdentity path test;
- no cross-tenant IDOR negative matrix;
- Idempotency-Key conflict behavior unproved;
- no concurrent processor/lease proof;
- no current dataset-to-downstream acknowledgement;
- reviewer verdicts are absent or evidence-only.

### 6. Human Imitation / Shadow Evaluation

Canonical acceptance:

- real Agora dataset is discovered on schedule;
- seed fallback is rejected in product mode;
- candidate is produced and remains non-runtime-affecting;
- experiment, approval, and deployment gates are enforced.

Missing proof:

- scheduler not active in current fleet;
- no restart/duplicate tick proof under current Compose;
- no current real DatasetVersion content proof;
- no authoritative training artifact and candidate lineage;
- no complete positive/negative approval-to-deployment path;
- evidence remains review-required.

### 7. Consultation

Canonical acceptance:

- committee/red-team workflow runs asynchronously;
- participant qualification is enforced;
- memo/handoff is durable;
- handoff is consumed exactly once or visibly blocked.

Missing proof:

- executor is not deployed;
- current tests use patched in-process HTTP;
- no two-executor race proof;
- no crash proof at participant, memo, publish, and handoff boundaries;
- no downstream acknowledgement/DLQ/replay proof;
- no current auth/tenant/hosted evidence;
- evidence remains review-required.

### 8. Promotion / Deployment

SA-21 acceptance:

- immutable approved artifact dispatches once and retries safely;
- duplicate outbox does not duplicate RuntimeBinding;
- runtime-manager failure enters retry/DLQ;
- BFF separates approval, plan, saga, binding, and fleet status.

Existing strength:

- historical service-boundary and failure artifacts cover much of this
  behavior.

Missing proof:

- evidence file is not an accepted current `evidence.json`;
- proof is bound to the superseded dev host/project;
- no current replacement-dev exact deployment identity;
- no current stack restart/RPO=0 and compensation packet;
- no current accepted controller/BFF stage truth.

### 9. Capital Pool Execution

SA-21 acceptance:

- all active paper bindings recreate workers after stack restart;
- killing one worker restarts only that worker;
- paused/retired binding stops its worker;
- no runtime consumes another binding's signal;
- BFF distinguishes binding, worker, session, heartbeat, telemetry.

Existing strength:

- current paper fleet and signals are active.

Missing proof:

- unscoped legacy signal is currently allowed, contradicting strict isolation;
- no current six-binding kill-one/retire/full-stack drill;
- no current duplicate signal and DLQ packet;
- telemetry correlation is currently broken downstream;
- evidence schema lacks required `mutation_rule` and reviewer verdict.

### 10. Telemetry / Reconciliation

SA-21 acceptance:

- heartbeat loss/order-rejection spike opens incident automatically;
- duplicates do not duplicate incidents;
- incident links telemetry, binding, runtime, and reconciliation IDs;
- BFF incident/runtime panels agree.

Contradictory current evidence:

- six of six summaries are rejected for missing trace identity;
- consumer is degraded;
- scheduler is unhealthy and timing out;
- no DriftReport or IncidentCase is emitted from the current summaries.

Required proof after repair:

- lifecycle-with-trace followed by heartbeat-without-trace regression;
- all six current summaries reconcile;
- order rejection, heartbeat loss, PnL/drawdown drift and recovery;
- duplicate, DLQ/replay, consumer/dependency/DB/full-stack restart;
- current BFF and authority-store agreement.

Historic reconciliation contract acceptance does not override this newer
failure.

### 11. Evolution

SA-21 acceptance:

- resolved incident does not disappear into manual backlog;
- published postmortem creates exactly one proposal per target/cluster;
- daily sweep respects cooldown/active lock;
- approved action uses the correct gate;
- BFF separates proposed, reviewed, approved, dispatched, executed.

Missing proof:

- current threshold inputs fail closed for all six runtimes;
- no positive current threshold candidate;
- approved-action durable delivery to Runtime Manager/Deployment is not proved;
- no crash/replay proof at each outbox boundary;
- no current compensation readback;
- no current full anomaly-to-executed-action drill.

### 12. BFF Health Monitoring

SA-21 acceptance:

- all loops have operator-visible current/target maturity, owner, controller,
  last success/failure, and evidence;
- downstream degradation emits accepted telemetry and incident;
- seed/fixture is never shown as live;
- cross-loop source and runtime/evolution drills pass.

Missing proof:

- health telemetry is expected to DLQ under strict binding validation;
- incident correlation contains no telemetry event IDs;
- only five downstream targets are probed;
- error-rate spike trigger is not proved;
- monitor state is process memory;
- no BFF restart/dedup/recovery proof;
- existing BFF-004 evidence explicitly states no Compose/dev VM drill.

## Current closeout replay

The current guardrail result is:

```text
4/20 replay source(s) passed closeout truth replay
```

Dominant failure classes:

- no formal independent reviewer verdict;
- `review_required` or blocked overall admission;
- schema-invalid evidence;
- missing terminal hosted readback;
- missing restart/RPO proof;
- missing RBAC/tenant/MFA/no-live-capital/two-person evidence;
- stale host/deployment identity;
- blocking residual risk.
- no protected transition-time Human/Ops verdict consumer.

## Required proof levels

- Controller implementation: EP1 unit/contract plus idempotency.
- Local composed process: EP2.
- Real cross-service boundary: EP3.
- Runtime-affecting behavior: EP4 governed paper.
- Human-approved live/canary: EP5 only when explicitly in scope.

EP1/EP2 must never be used to claim `proven-live`. This program does not
require enabling live capital. Capital and runtime-affecting closeout may stop
at governed-paper product proof when the policy target is paper.

## Round-3 conclusion

The missing work is not only “more tests.” Several acceptance failures expose
real code and deployment gaps. Evidence repair must follow implementation and
current-host drills; rewriting old manifests without rerunning the behavior is
false closure.
