# LOOP-PROD-001 — Durable controller truth substrate

Status: ready for fleet dispatch after the program packet is merged

Canonical catalog: `tasks.json`

Source plan:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/LOOP_PRODUCT_LEVEL_REMEDIATION_PLAN_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex2 |
| Reviewer | Codex |
| Wave | 0 |
| Fleet lane | `controller-truth-store` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | registry-only or process-local controller metadata |
| Target maturity | reconciled |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

建立 tenant/environment scoped 的 durable controller record store、writer SDK 與 projector，記錄 desired/actual query、lease、dedupe、heartbeat/tick/success/failure/repair、backlog、deployment SHA 與 evidence truth level。

Next action: Implement the durable loop-controller record contract, writer SDK, store, restart proof, and BFF read path.

## Dependencies

- `LOOP-PROD-000`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `source_ingestion`
- `strategy_distillation`
- `alpha_replication`
- `persona_teaching`
- `agora_interaction_evidence`
- `human_imitation_shadow_evaluation`
- `consultation`
- `promotion_deployment`
- `capital_pool_execution`
- `telemetry_reconciliation`
- `evolution`
- `bff_health_monitoring`
- `per_persona_ooda`

## Desired-state authorities

- `loop catalog desired_state_sources`
- `controller-specific desired-state queries`
- `deployment ownership declarations`

## Actual-state authorities

- `durable controller record store`
- `controller writer SDK`
- `downstream authoritative readback queries`

## Declared artifacts

- `services/loop-control`
- `services/control-plane/bff/loop_inventory.py`
- `schemas/loop-controller-record.schema.json`
- `docker-compose.yml`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- Postgres-backed records survive worker, BFF, database, and full-stack restart
- stale lease and duplicate writes cannot manufacture accepted liveness
- writer stores exact controller instance, deployment SHA, backlog, lag, DLQ, readback, evidence refs, and freshness
- BFF can query records without falling back to registry metadata
- archive branch, PR, required checks, merge SHA, independent reviewer verdict, and residual risk owner/expiry
- use authoritative pre/post readback; submitted, queued, seed, stub, fixture, snapshot, or synthetic receipt is not terminal success
- prove duplicate safety, failure/degraded behavior, restart/recovery, and rollback or compensation with admitted-command RPO=0
- record exact deployment identity and controller truth when deployed; no registry-only maturity promotion
- preserve RBAC, tenant, MFA, two-person, environment, and no-live-capital boundaries wherever applicable
- write a redacted append-only checksummed evidence.json; missing, indirect, stale, or contradicted requirements fail closed

## Required proof

- focused and adjacent local validation
- merged PR and merge SHA
- target deployment identity where applicable
- canonical request, receipt, and terminal downstream readback correlation
- duplicate and restart/recovery evidence
- failure, degraded, rollback or compensation evidence
- security negative evidence and independent review

Reviewer approval must set `review_file` to the redacted, append-only,
checksummed machine evidence manifest under:

`docs/deployment/evidence/loop-product-level/LOOP-PROD-001/`

## Non-goals

- No panel-only closure
- No seed fixture as live proof
- No approval gate bypass
- No synthetic receipt as terminal execution proof
- No live-capital or live-broker side effect

## Dispatch and closeout rules

- start only after every dependency is done; superseded does not satisfy a dependency
- use one clean task worktree in the declared repository and merge to dev through a reviewed PR
- do not silently narrow acceptance; record an evidence-backed blocker when the product outcome cannot be met
- reviewer must set review_file to the checksummed machine evidence manifest before approval
- deploy and verify exact target identity when the task changes a deployed capability

A passing unit test, route response, static panel, registry row, seed, snapshot,
queued command, or generated receipt cannot by itself close this task.
