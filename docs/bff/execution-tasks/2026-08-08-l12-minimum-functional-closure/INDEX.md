# Twelve-loop minimum functional closure

Date: 2026-08-08

Frozen source baseline: `a55721bce2a7bc0a4dc01dd6eba1b48a58b78312`

## Objective

Make each of the twelve loops complete one real development happy path:

```text
trigger -> owning worker/controller -> persisted terminal output
        -> actual-state readback -> next loop can consume the output
```

This plan targets the smallest usable development system. It does not add
security hardening, enterprise HA, load testing, compliance work, broad
evidence recuts, or live-capital activation. Capital execution is accepted on
the paper runtime.

Normal repository delivery remains required: clean task worktree, focused
tests, commit, PR to `dev`, independent review, merge, and bounded dev rollout.

## Frozen current evidence

- Canonical catalog: 3/12 controllers are registered; 9/12 are
  `not_implemented`.
- Desired-state and actual-state query status is `planned` for all twelve.
- The running Compose stack is not current source truth and does not prove
  twelve-loop closure.
- Source ingestion is unhealthy; dependent Source scheduler, Distillation,
  Teaching, and paper-fleet services are not running.
- Runtime verification reports four functional failures: paper fleet not
  started, an active binding references a missing deployment plan, BFF exposes
  the missing plan, and duplicate signal identity can lose a signal.
- BFF readiness is degraded because lifecycle projection is stale.

## Definition of minimum functional closure

A loop is complete only when one real, non-fixture request or scheduled tick:

1. reaches the intended worker/controller;
2. produces and persists the loop's specified terminal output;
3. is visible through the loop's actual-state readback; and
4. can be read by the next loop or final operator surface.

Only one normal-path proof is required for this minimum milestone. Concurrency,
chaos, failover, exhaustive negative cases, performance, and security
hardening are explicitly deferred.

## Work waves

### M0 - reconcile old planning without parallel dispatch

`SUP-L12-MIN-FUNCTION-DAG-RECONCILE-20260808` verifies that all 28 old catalog
task IDs are absent from active tasks (0 active), 1 ID
(`L12-VERIFY-LEARN-REAL-VERIFIER-001`) is archived as superseded by
`L12-MIN-E2E-20260808`, 0 open PRs, 0 matching branches or worktrees at admission
time, and records this successor program as the only minimum-function dispatch
source. It must not edit `.orchestrator/config.json`, canonical state JSON, or
the old catalog.

The admission operator must also reconcile two active rows from the older
twelve-loop closeout chain before shared integration is materialized:

- canonical-supersede active `L12-HOSTED-001` with replacement
  `L12-MIN-HOSTED-20260808`;
- canonical-supersede active `L12-CLOSE-001` with replacement
  `L12-MIN-CLOSE-20260808`; and
- perform both transitions through the governed task command, never by editing
  task state JSON. The old close row owns a guarded
  `docs/deployment/loop-catalog.registry.json` scope, so
  `L12-MIN-INTEGRATE-20260808` must not materialize before this transition.

Existing `L12-VERIFY-*` work and the lifecycle-projector DAG are preserved.
They are inputs or ordered dependencies, not duplicate implementation tasks.

### M1 - twelve loop owners

Twelve independent tasks repair or complete the actual happy path. Existing
workers are reused. Shared catalog, Compose, and BFF binding files are excluded
from these tasks so that parallel owners do not collide.

### M2 - one shared integration owner

`L12-MIN-INTEGRATE-20260808` alone edits the shared loop catalog, Compose,
loop-control binding, and BFF inventory/health surfaces after all twelve loop
owners finish. It is dependency-ordered after the existing lifecycle-projector
tasks that touch the same BFF surfaces.

### M3 - one complete verifier

`L12-MIN-E2E-20260808` runs twelve happy-path cases and one chain case. It does
not reopen product implementation. A failure returns to the owning M1/M2 task.

### M4 - deploy and hosted readback

`L12-MIN-HOSTED-20260808` deploys the merged `dev` source through the existing
Pantheon dev deployment path, proves exact hosted identities, and repeats the
twelve terminal readbacks. `L12-MIN-CLOSE-20260808` closes only when all twelve
rows pass.

## Loop-by-loop minimum outputs

| Loop | Trigger | Minimum terminal output | Next consumer/readback |
|---|---|---|---|
| Source Ingestion | one scheduled/manual ingestion tick | `SourceRecord` | Distillation reads it |
| Strategy Distillation | the new `SourceRecord` | terminal `StrategySpec` draft | Alpha discovery reads it |
| Alpha Replication | one approved strategy | terminal replication/experiment result | Teaching/research readback |
| Persona Teaching | one teaching command | terminal teaching/evaluation session | operator/Learning verifier |
| Agora Evidence | one interaction/journal event | persisted evidence/dataset handoff | Imitation reads it |
| Imitation / Shadow Evaluation | one scheduled dataset evaluation | terminal shadow candidate | operator/Learning verifier |
| Consultation | one consultation request | terminal `ConsultMemo` | promotion input/readback |
| Promotion / Deployment | one approved paper deploy command | terminal `DeploymentPlan` and `RuntimeBinding` | Capital reads binding |
| Capital Execution | one active paper binding and signal | paper order/fill/position readback | Telemetry reads event |
| Telemetry / Reconciliation | one runtime event | `DriftReport` or `IncidentCase` terminal readback | Evolution reads it |
| Evolution | one eligible incident/postmortem | terminal `EvolutionDecision` and downstream receipt | BFF/operator reads it |
| BFF Health Monitoring | one dependency fail/recover observation | health event and current status | telemetry/operator reads it |

## Legacy 28-task reconciliation

The old catalog remains historical input. Its task IDs are not reused.
`legacy-28-reconciliation.json` maps every old task to a successor or removes
it from the minimum-function critical path.

- The nine old controller tasks are superseded by corresponding M1 tasks with
  corrected implementation scopes.
- The sixteen evidence-only tasks are not dispatched as standalone blockers.
  Each M1 task records one bounded functional result in its own evidence root.
- The old shared integration and release gate are superseded by M2 and M3.
- The old Learning-only verifier is superseded by the twelve-loop verifier.
- Existing canonical lifecycle-projector tasks remain dependencies where file
  scopes overlap; they are not duplicated.
- Existing `L12-HOSTED-001` and `L12-CLOSE-001` are active `todo` rows, not
  historical rows. Their older acceptance scope is broader than this minimum
  functional milestone and `L12-CLOSE-001` guards the shared catalog path.
  They are canonical-superseded by the minimum hosted/close successors before
  shared integration admission; their source documents and branches are not
  edited.
- Existing `L12-VERIFY-KNOW-001`, `L12-VERIFY-RUNTIME-001`, and
  `L12-VERIFY-OBS-001` remain intact. Their results may be consumed, but they
  do not replace the one-invocation minimum twelve-loop verifier.

## Validation

Per M1 task:

- focused component tests for the changed worker/controller;
- one real normal-path trigger and terminal readback;
- proof the next consumer can read the produced identifier;
- no unrelated service or shared integration file changes.

M2:

- 12/12 catalog controller declarations;
- 12/12 rendered Compose controller ownership;
- 12/12 BFF inventory and health rows with non-null controller identity;
- focused loop catalog/inventory/health tests.

M3/M4:

- twelve normal-path cases pass from one invocation;
- the chain case carries correlated identifiers across all twelve rows;
- hosted manifest identifies the actually served FE/BFF source;
- hosted BFF readiness is `200` and all twelve terminal readbacks are current.

## Merge and rollout order

1. M0 reconciliation task.
2. M1 tasks in parallel when their declared dependencies are complete.
3. M2 shared integration.
4. M3 complete verifier.
5. M4 hosted deployment and closeout.

## Rollback

- Before M2, each M1 PR can be reverted independently.
- If M2 fails, revert only the shared integration PR; keep merged component
  implementations dormant.
- If hosted verification fails, retain the previous served release and record
  the failing loop; do not switch the hosted symlink.
- No rollback path enables live capital. Paper runtime remains the boundary.
