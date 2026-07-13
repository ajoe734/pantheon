# LOOP-PROD-MAI-003 — Management AI/OpenClaw product closeout

Status: ready for fleet dispatch after the program packet is merged

Canonical catalog: `tasks.json`

Source plan:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/LOOP_PRODUCT_LEVEL_REMEDIATION_PLAN_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex |
| Reviewer | Codex2 |
| Wave | 3 |
| Fleet lane | `closeout-management-ai` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | component readiness without full hosted closeout |
| Target maturity | product-level |
| Human/Ops final sign-off | required |

## Product outcome

彙整 exact BFF/FE SHAs、repair sentinel、SA/SD→task→supervisor receipts、debug/repair security negatives、restart/rollback 與 residual risk，形成 Management AI product closeout。

Next action: Publish the Management AI product closeout only after the complete hosted repair, UI, security, restart, and rollback evidence passes.

## Dependencies

- `LOOP-PROD-MAI-001`
- `LOOP-PROD-MAI-002`
- `LOOP-PROD-VERIFY-OODA-001`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `per_persona_ooda`
- `bff_health_monitoring`

## Desired-state authorities

- `Management AI/OpenClaw product runbook`
- `authorized debug and repair scenarios`

## Actual-state authorities

- `mode/worktree/conversation/sentinel/dev-bridge/supervisor receipts`
- `hosted UI evidence`
- `security and rollback records`

## Declared artifacts

- `docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/closeout/MANAGEMENT-AI`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-MAI-003`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- exact deployed BFF and FE identities match the successful hosted repair chain
- sentinel, SA/SD, task packet, pending/processed receipts, task archive, and deactivation correlate
- read-only debug and repair security negative matrix passes before and after deactivation
- restart, RPO/recovery, degraded, rollback, cleanup, and residual risks are independently reviewed
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-MAI-003/`

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
