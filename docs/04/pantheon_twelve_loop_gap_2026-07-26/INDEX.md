# Pantheon Twelve-Loop Operability Gap Program

Program ID: `pantheon-twelve-loop-gap-2026-07-26`

Status: three-pass audit archived; guarded execution catalog prepared; live
materialization pending merged `dev` delivery and fleet-capacity proof

Merge target: `dev`

## Objective

Bring every loop in `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md` to its declared
trigger, authority, durability, recovery, safety, and operator-truth contract.
Only Capital Pool Execution is continuously resident. The other loops must be
available through their specified event, schedule, command, or health trigger;
they must not be converted into twelve uncontrolled polling processes.

This program does not treat an API route, a running container, a local unit
test, a seed projection, or an old evidence packet as proof that a loop works.
Completion requires authoritative input, durable execution ownership,
terminal downstream readback, restart/replay behavior, current deployment
identity, BFF truth, and accepted evidence.

## Three-pass audit

The gap inventory was deliberately repeated using three different directions:

1. [Round 1 — specification to runtime](archive/ROUND1_SPEC_RUNTIME_AUDIT.md)
   compares each declared trigger/output with current Compose and dev runtime.
2. [Round 2 — implementation and failure paths](archive/ROUND2_IMPLEMENTATION_FAILURE_AUDIT.md)
   follows code, persistence, concurrency, tenant, retry, and handoff behavior.
3. [Round 3 — acceptance and evidence](archive/ROUND3_ACCEPTANCE_EVIDENCE_AUDIT.md)
   works backward from SA-21 acceptance, proof levels, restart, security, and
   closeout admission.

The reconciled source of truth is:

- [Twelve-loop master gap inventory](archive/TWELVE_LOOP_GAP_INVENTORY_2026-07-26.md)
- [Parallel fleet execution plan](archive/PARALLEL_FLEET_EXECUTION_PLAN_2026-07-26.md)
- [Current-state fleet and twelve-loop gap overlay, 2026-07-27](archive/CURRENT_STATE_FLEET_GAP_OVERLAY_2026-07-27.md)
- `docs/bff/execution-tasks/2026-07-26-twelve-loop-gap/tasks.json`

## Baseline verdict

At the audit baseline:

- formal maturity is `0/12 proven-live`;
- all twelve catalog controller contracts remain `not_implemented`;
- Postgres has controller records for only Source Ingestion and Strategy
  Distillation; the Source record is stale;
- Telemetry/Reconciliation is actively degraded because six of six runtime
  summaries lack the top-level identity required by its consumer;
- Evolution evaluates six runtime summaries but produces zero candidates
  because drawdown telemetry and approved baselines are incomplete;
- the current loop evidence replay accepts only four of twenty sources, and
  accepted contract evidence does not override contradictory live runtime
  evidence.
- the existing Human/Ops signoff flag is descriptive metadata; protected,
  transition-time verdict enforcement must be installed before final closeout.

These facts are a baseline, not a completion claim.

## Dispatch rule

The full DAG must be dispatched through the program-specific guarded
dispatcher after this packet is merged. It must not be bulk-materialized
through the Management AI DevTaskPacket bridge because that bridge does not
preserve the loop, maturity, authority, evidence, and product-level contract
fields and has an existing bulk-partial-replay prohibition.

`PPL-ALLOC-009` remains an active Human/Ops-gated external dependency with
broad `services/control-plane/bff` and `execute-plans:src` ownership. The
catalog explicitly blocks every overlapping task on that ID. The dispatcher
also compares all catalog scopes with current nonterminal live tasks and
rejects any undeclared overlap.

Each fleet task must:

- use a clean task worktree and a unique task branch;
- own disjoint artifacts from parallel siblings;
- merge to `dev` through a reviewed PR;
- archive a schema-valid, checksummed `evidence.json`;
- prove duplicate safety, failure truth, restart/recovery, and terminal
  authoritative readback;
- treat `L12-SIGNOFF-001` as a mandatory machine guard before closeout and
  reject fleet- or candidate-issued verdicts;
- keep live capital disabled;
- remain open when current evidence is stale, indirect, synthetic, or
  contradicted.

## Completion boundary

This program is complete only when all implementation and verification tasks
are `done`, every relevant evidence manifest passes closeout replay, the
current hosted deployment identifies the merged code, all twelve controller
truth records are current and accepted, the global restart drill passes, and
the loop catalog is promoted only to the maturity actually proved.
