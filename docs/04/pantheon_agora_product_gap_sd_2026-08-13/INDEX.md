# Agora Product GAP and System Design Re-baseline — 2026-08-13

Status: **planning baseline; not dispatched; not an implementation closeout**

## Executive verdict

Agora has a substantial amount of implemented API, UI, persistence, contract,
and historical hosted-proof work. That work is not equivalent to a usable
Agora product journey.

At the frozen source baseline, no complete path proves that an Agora user can
start with a strategy hypothesis, have the hypothesis reconstructed into an
authoritative StrategySpec, run governed research, obtain real candidates,
build a data-backed Trading Room, make a tenant-isolated decision, observe real
performance, and carry eligible evidence through learning and independent
consultation. Several missing links are not ordinary feature gaps: they are
authority, identity, asynchronous-processing, and isolation errors that must be
replaced before more UI or endpoint surface is added.

The required product statement is therefore:

> Agora's major visual and contract surfaces exist, but the current product is
> not end-to-end usable. The next development cycle must first correct the
> authoritative workflow and remove production mock paths, then complete the
> user journey on top of that corrected design.

## Scope and frozen evidence

This audit is a one-time source, task, PR, and hosted-state snapshot. It does
not follow auto-worker progress and does not continuously rewrite the plan.

| Surface | Frozen baseline |
|---|---|
| Pantheon source | `ajoe734/pantheon` `origin/dev` at `3307552b55af75850dab1d50e58cef9f86e10b53` |
| Agora frontend source | `ajoe734/execute-plans` `origin/dev` at `3ee9f962a36626f085e2ca1c088b3ce4b4d08e6f` |
| Audit date | 2026-08-13 UTC |
| Hosted manifest | frontend `6a8d2d9b4f725056735eefd7165ef47b52cda53d`; declared BFF `be956c07aca889043ef301389412b6744452f20b` |
| Actual hosted BFF | `/bff/version` reported `6367cea609e9d19053130ab8f9b1946d5d35dfc6` |
| Hosted readiness | `/readyz` returned HTTP 503/degraded; lifecycle projector controller/worker were `repair_only`, `accepted_live=false`, and the lifecycle cursor was inconsistent |

The hosted manifest is safe/read-only (`live` BFF mode, strict fallback, real
and stub writes disabled), but its declared BFF SHA does not match the running
BFF. Historical exact-pair evidence remains valid history; it is not current
acceptance evidence.

## What was and was not done in this planning pass

Done:

- read the current frontend and backend implementation at the frozen commits;
- trace the intended user journey against actual runtime call paths;
- separate implemented endpoints from reachable product behavior;
- identify incorrect design that must be removed or replaced;
- define the target system design and dependency order;
- prepare work-package boundaries for a later execution-task generation pass.

Not done:

- no Agora product code was changed;
- no supervisor, supervisor V2, worker, or runtime control code was changed;
- no execution task was created or queued;
- no task was sent to the supervisor;
- no live write, deployment, service restart, or data mutation was performed;
- no Lovable path was used or proposed.

`execute-plans` is the active frontend repository and Pantheon-owned hosting is
the delivery target. Supervisor behavior is delivery machinery, not an Agora
product dependency, and is outside this product design.

## Document set

1. [Code and scenario GAP audit](01_CODE_AND_SCENARIO_GAP_AUDIT.md) — the
   intended user journey, current behavior, severity, and code evidence.
2. [Code disposition and design corrections](02_CODE_DISPOSITION_AND_DESIGN_CORRECTIONS.md)
   — what to keep, refactor, quarantine, or delete, and why.
3. [Agora target system design](03_SD_AGORA_COMPLETE_PRODUCT.md) — authoritative
   boundaries, state machines, commands, events, persistence, security, and
   observability.
4. [Execution-task preparation](04_EXECUTION_TASK_PREPARATION.md) — ordered work
   packages, file scopes, acceptance gates, merge order, and rollback rules.
   These are planning work packages, not canonical task IDs or dispatch packets.
5. [Frozen evidence and deduplication record](05_FROZEN_EVIDENCE_AND_DEDUP.md) —
   source/runtime observations and how earlier completed work is interpreted.

## Relation to prior Agora documents

This packet does not erase historical delivery evidence. It changes the
question being answered:

- [2026-07-22 remaining-work GAP](../pantheon_agora_remaining_work_2026-07-22/REMAINING_WORK_GAP.md)
  correctly records delivered contracts and historical hosted closeout, but
  its feature-completion framing is insufficient for current product usability.
- [Winner-branch E2E and isolation design](../pantheon_agora_cross_repo_2026-06-20/design-closure-round2/06_winner_branch_e2e_and_isolation.md)
  remains the canonical user-journey intent used by this audit.
- [AG-HOSTED-CLOSE-002 evidence](../../deployment/evidence/agora/ag-hosted-close-002.md)
  remains historical acceptance for its exact pair and date.

The new packet supersedes any conclusion that “most task rows are done” proves
that the present Agora environment or the complete Agora product journey is
done.

## Release gates for the future implementation cycle

Later implementation may claim Agora product completion only when all of the
following are true:

1. the authority and tenant-isolation corrections in this SD are merged;
2. production routes contain no hidden fixture or local-only success path;
3. the full user journey is proven through canonical services and durable
   workers, not seeded rows or direct store calls;
4. frontend actions read back canonical state after each write;
5. learning and consultation use separate durable actors and do not
   self-approve;
6. an exact FE/BFF pair is accepted and served with `/readyz` healthy;
7. authenticated cross-user negative tests and restart/readback tests pass;
8. evidence names exact commits, request/receipt IDs, dataset lineage, and
   hosted timestamps.
