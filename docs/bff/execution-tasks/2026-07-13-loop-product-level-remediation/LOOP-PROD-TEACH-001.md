# LOOP-PROD-TEACH-001 — Fail-closed Persona Teaching on authoritative data

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
| Fleet lane | `persona-teaching-worker` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | api-only with stub data and auto-pass proof |
| Target maturity | product-level |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

移除 STUB1/STUB2、stub-ref 與 unconditional passed proof；evaluation 讀 canonical versioned dataset、freshness、threshold policy，資料不足即 fail closed，preview worker 預設啟動。

Next action: Replace stub teaching proof with authoritative versioned data, fail-closed evaluation, default ownership, and terminal readback.

## Dependencies

- `LOOP-PROD-SRC-001`
- `LOOP-PROD-REC-001`
- `LOOP-PROD-ALPHA-001`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `persona_teaching`

## Desired-state authorities

- `governed teaching request`
- `canonical dataset version`
- `threshold and policy version`
- `approval state`

## Actual-state authorities

- `evaluation metrics and proof`
- `persona governed target`
- `training job store`
- `controller record`

## Declared artifacts

- `services/training-session`
- `services/knowledge/evidence`
- `docker-compose.yml`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-TEACH-001`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- no production path references STUB1, STUB2, stub-ref, or manufactures a passing proof
- missing, stale, invalid, or insufficient data cannot pass commit admission
- default worker records dataset, policy, thresholds, metrics, approval, and terminal persona readback
- duplicate job, stale candidate, approval negative, worker restart, and rollback are proven
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-TEACH-001/`

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
