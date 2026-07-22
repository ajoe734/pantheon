# LOOP-PROD-CONS-001 — Real-participant Consultation workflow

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
| Fleet lane | `consultation-worker` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | api-only workflow can manufacture advisory success |
| Target maturity | product-level |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

移除自動製造 committee/memo/recommendation 的成功路徑；只有合格真實 participant/provider-authored transcript、memo 與 review evidence 才能 publish/handoff，provider 不可用時必須 waiting/blocked。

Next action: Replace manufactured consultation success with real authorship, reviewed publication, default worker ownership, and failure truth.

## Dependencies

- `LOOP-PROD-001`
- `LOOP-PROD-002`
- `LOOP-PROD-REC-001`
- `LOOP-PROD-AGORA-001`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `consultation`

## Desired-state authorities

- `submitted consultation request`
- `eligible participant/provider policy`
- `review and publication gates`

## Actual-state authorities

- `participant assignment`
- `authored transcript and memo`
- `review evidence`
- `publication/handoff receipt`
- `controller record`

## Declared artifacts

- `services/consultation`
- `services/control-plane/bff`
- `docker-compose.yml`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-CONS-001`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- only verifiably eligible participants/providers can author transcript and memo
- unavailable participant/provider remains waiting or blocked and never auto-approves or publishes
- publication and governance handoff require independent review evidence and terminal readback
- default worker proves exactly-once, retry, replay, restart, tenant isolation, and advisory-only authority
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-CONS-001/`

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
