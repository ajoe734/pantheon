# LOOP-PROD-TJ-002 — Hosted Trade Journey action controls

Status: ready for fleet dispatch after the program packet is merged

Canonical catalog: `tasks.json`

Source plan:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/LOOP_PRODUCT_LEVEL_REMEDIATION_PLAN_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex |
| Reviewer | Codex2 |
| Wave | 2 |
| Fleet lane | `trade-journey-actions-fe` |
| Repository | `execute-plans` |
| Merge target | `dev` |
| Current maturity | journey UI lacks governed action controls |
| Target maturity | product-level |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

在 execute-plans 加入 action availability、confirmation、terminal receipt/refetch、authenticated SSE reconnect，以及 partial-source/degraded/error UX；完成 desktop/mobile/a11y/strict auth evidence。

Next action: Build and host the governed Trade Journey controls with terminal truth, resilient SSE, and complete product-state coverage.

## Dependencies

- `LOOP-PROD-TJ-001`
- `LOOP-PROD-FE-001`
- `MGMT-SSE-001`
- `OPS-EP-DEV-MAIN-RECONCILE-001`
- `LOOP-PROD-AGORA-003`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `capital_pool_execution`
- `telemetry_reconciliation`
- `evolution`

## Desired-state authorities

- `journey action availability contract`
- `authenticated user intent and revision`

## Actual-state authorities

- `action request/receipt/refetch`
- `SSE event/replay`
- `hosted UI states and audit`

## Declared artifacts

- `execute-plans/src`
- `execute-plans/e2e`
- `execute-plans/docs/04/loop-product-level/LOOP-PROD-TJ-002`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- UI renders only authorized actions with explicit confirmation and terminal receipt/refetch
- SSE reconnect/replay produces no duplicate stage or receipt card
- desktop/mobile prove loading, empty, stale, denied, degraded, partial-source, terminal failure, and success
- strict-auth, tenant, MFA, keyboard/focus, accessibility, performance, and zero unexpected browser/network errors pass
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

`execute-plans/docs/04/loop-product-level/LOOP-PROD-TJ-002/`

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
