# LOOP-PROD-ALPHA-001 — Durable Alpha Replication and revalidation worker

Status: ready for fleet dispatch after the program packet is merged

Canonical catalog: `tasks.json`

Source plan:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/LOOP_PRODUCT_LEVEL_REMEDIATION_PLAN_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex |
| Reviewer | Codex2 |
| Wave | 1 |
| Fleet lane | `alpha-replication-worker` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | api-only revalidation logic |
| Target maturity | product-level |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

讓 reviewed immutable StrategySpec 進 durable replication queue，由預設 scheduled/command worker 執行非 stub revalidation，寫入真實 ExperimentRun 與 evidence lineage；production activation 保持 gate-closed。

Next action: Productize the durable replication queue, default revalidator, real ExperimentRun writeback, and safety/recovery proof.

## Dependencies

- `LOOP-PROD-DIST-001`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `alpha_replication`

## Desired-state authorities

- `reviewed immutable StrategySpec snapshots`
- `replication cadence and experiment policy`

## Actual-state authorities

- `durable replication queue`
- `ExperimentRun and evidence stores`
- `controller health and DLQ`

## Declared artifacts

- `services/research/alpha_replication`
- `services/research/experiments`
- `services/research/replication`
- `docker-compose.yml`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-ALPHA-001`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- scheduled and explicit work share one idempotent queue contract
- real eligible input produces a non-stub ExperimentRun with immutable evidence lineage
- retry, timeout, DLQ/replay, duplicate, stale spec, and restart are proven
- no path activates production without the ordinary immutable approval and deployment gates
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-ALPHA-001/`

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
