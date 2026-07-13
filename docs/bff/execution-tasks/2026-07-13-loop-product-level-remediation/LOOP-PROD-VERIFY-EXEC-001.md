# LOOP-PROD-VERIFY-EXEC-001 — Target-dev Execution spine product verifier

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
| Fleet lane | `verify-execution-spine` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | partial paper runtime with manual gaps |
| Target maturity | product-level |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

在 clean target-dev 驗證 Scenario B：create→plan→binding→worker→signal/decision/order/fill/position→telemetry/Journey→incident/postmortem/evolution→real target command，含 stack restart/isolation/rollback/no-live-capital。

Next action: Run and archive the full target-dev paper Execution spine, recovery matrix, isolation, rollback, and no-live-capital proof.

## Dependencies

- `LOOP-PROD-PER-001`
- `LOOP-PROD-DEP-001`
- `LOOP-PROD-CAP-001`
- `LOOP-PROD-TEL-001`
- `LOOP-PROD-TEL-002`
- `LOOP-PROD-EVO-001`
- `LOOP-PROD-BFF-001`
- `PPL-ALLOC-012`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `promotion_deployment`
- `capital_pool_execution`
- `telemetry_reconciliation`
- `evolution`
- `bff_health_monitoring`

## Desired-state authorities

- `Scenario B dynamic create-paper specification`
- `safe paper signal and evolution policies`

## Actual-state authorities

- `persona, plan, binding, worker, lifecycle events, telemetry, journey, incident, postmortem, evolution command/post-state`

## Declared artifacts

- `scripts/verify_loop_product_execution_spine.py`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-VERIFY-EXEC-001`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- one dynamic persona completes the full canonical Scenario B identity chain
- full-stack restart, duplicate command, binding isolation, stop, timeout, DLQ/replay, rollback, and degraded behavior pass
- evolution ends at a real governed target post-state, not SUBMITTED
- all orders and capital are paper/dev with explicit false real-order and real-capital assertions
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-VERIFY-EXEC-001/`

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
