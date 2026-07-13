# LOOP-PROD-TJ-003 — Trade Journey superseding product closeout

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
| Fleet lane | `closeout-trade-journey` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | prior closeout predates producer/action gaps |
| Target maturity | product-level |
| Human/Ops final sign-off | required |

## Product outcome

保留已完成 TJ-E2E-012 為舊 evidence，不重開；以 TJ-E2E-014、新 canonical projector/actions/UI 與 execution verifier 產生新的 product closeout。

Next action: Archive the new Trade Journey product closeout after producer, lifecycle truth, actions, hosted UX, and recovery all pass.

## Dependencies

- `TJ-E2E-012`
- `TJ-E2E-014`
- `LOOP-PROD-TJ-001`
- `LOOP-PROD-TJ-002`
- `LOOP-PROD-VERIFY-EXEC-001`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `capital_pool_execution`
- `telemetry_reconciliation`
- `evolution`

## Desired-state authorities

- `Trade Journey product specification`
- `one dynamic paper lifecycle and governed action matrix`

## Actual-state authorities

- `canonical journey stages`
- `action receipts/readback`
- `SSE replay`
- `data quality and hosted evidence`

## Declared artifacts

- `docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/closeout/TRADE-JOURNEY`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-TJ-003`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- one real paper trade forms one canonical correlated multi-stage journey without manual live backfill
- governed actions, replay, restart, SSE reconnect, partial-source, data-quality, and terminal failure pass
- strict-auth desktop/mobile evidence identifies exact deployed FE/BFF SHAs
- closeout explicitly supersedes only the product claim, not historical TJ-E2E-012 records
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-TJ-003/`

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
