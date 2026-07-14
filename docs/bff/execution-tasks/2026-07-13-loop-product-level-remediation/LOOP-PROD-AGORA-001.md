# LOOP-PROD-AGORA-001 — Durable Agora evidence, dataset, and handoff worker

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
| Fleet lane | `agora-evidence-worker` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | integrated stores without default evidence handoff owner |
| Target maturity | product-level |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

將 interaction、feedback、note、journal、insight 事件送入 tenant-scoped durable inbox，由預設 worker 產生 versioned dataset 與 evidence handoff；只可供 Observe/Learn，不得直接 deploy/trade。

Next action: Add the durable Agora evidence worker, versioned dataset/handoff lineage, default deployment, and tenant/recovery proof.

## Dependencies

- `LOOP-PROD-001`
- `LOOP-PROD-002`
- `LOOP-PROD-REC-001`
- `LOOP-PROD-TEACH-001`
- `LOOP-PROD-AUTH-001`
- `AG-GAP-014`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `agora_interaction_evidence`

## Desired-state authorities

- `eligible tenant-scoped Agora interaction events`
- `dataset extraction and handoff policy`

## Actual-state authorities

- `durable evidence inbox`
- `versioned datasets and handoffs`
- `Postgres records`
- `controller truth`

## Declared artifacts

- `services/control-plane/bff`
- `services/knowledge/evidence`
- `docker-compose.yml`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-AGORA-001`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- real interaction/feedback/note/journal/insight events produce versioned tenant-scoped evidence datasets
- default worker is idempotent, restart-safe, replayable, and exposes backlog/DLQ
- target-host Postgres persistence evidence survives full-stack restart
- handoff authority is limited to Observe/Learn and cannot deploy, trade, or promote
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-AGORA-001/`

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
