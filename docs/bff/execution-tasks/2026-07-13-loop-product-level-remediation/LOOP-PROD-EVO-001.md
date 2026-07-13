# LOOP-PROD-EVO-001 — Real Evolution target-plane dispatcher

Status: ready for fleet dispatch after the program packet is merged

Canonical catalog: `tasks.json`

Source plan:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/LOOP_PRODUCT_LEVEL_REMEDIATION_PLAN_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex2 |
| Reviewer | Codex |
| Wave | 1 |
| Fleet lane | `evolution-target-dispatcher` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | api-only with synthetic SUBMITTED result |
| Target maturity | product-level |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

approved EvolutionDecision 必須呼叫 canonical governance/deployment/runtime target plane，不得 synthetic SUBMITTED；預設 worker 持久化 terminal receipt、target post-state 與 formal journal link。

Next action: Implement the real evolution dispatcher, target readback, journal linkage, default worker, and governed recovery/rollback paths.

## Dependencies

- `EVOCHAIN-011`
- `LOOP-PROD-DEP-001`
- `LOOP-PROD-TEL-001`
- `LOOP-PROD-REC-001`
- `LOOP-PROD-TEL-002`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `evolution`

## Desired-state authorities

- `approved EvolutionDecision`
- `cooldown/singleton policy`
- `freeze/risk-off/rollback/redeploy governance state`

## Actual-state authorities

- `durable target command`
- `canonical target-plane receipt/post-state`
- `formal journal entry`
- `controller record`

## Declared artifacts

- `services/evolution`
- `services/governance`
- `services/deployment`
- `services/runtime-manager`
- `docker-compose.yml`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-EVO-001`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- approved decisions dispatch to the correct canonical authority and read back terminal target state
- SUBMITTED cannot be manufactured without actual delivery; no-op/dry-run use distinct states
- cooldown, singleton, duplicate, retry, DLQ/replay, restart, compensation, and rollback pass
- unauthorized, unapproved, wrong-target, unsafe environment, and live-capital attempts fail closed
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-EVO-001/`

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
