# LOOP-PROD-VERIFY-OODA-001 — Multi-persona OODA overlay product verifier

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
| Fleet lane | `verify-persona-ooda` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | single-path integrated proof |
| Target maturity | product-level |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

至少以三個動態 persona 各自完成 real OODA packet chain，驗證 duplicate cron、orphan repair、restart、provider outage、Learn attribution，並確認 Act 僅 proposal、無直接執行。

Next action: Run the multi-persona OODA overlay drill and archive independence, repair, outage, restart, attribution, and proposal-only proof.

## Dependencies

- `LOOP-PROD-OODA-001`
- `LOOP-PROD-VERIFY-KNOW-001`
- `LOOP-PROD-VERIFY-EXEC-001`
- `LOOP-PROD-MAI-001`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `per_persona_ooda`

## Desired-state authorities

- `three eligible dynamic personas and OODA schedules`
- `provider and proposal-only policy`

## Actual-state authorities

- `canonical cron jobs/runs`
- `provider turns`
- `OODA packets`
- `Learn evidence and controller records`

## Declared artifacts

- `scripts/verify_loop_product_persona_ooda.py`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-VERIFY-OODA-001`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- at least three personas independently close real Observe/Orient/Decide/Act-proposal/Learn packet chains
- duplicate cron, orphan repair, worker/full-stack restart, provider outage, and replay preserve exactly-once packet identity
- Learn evidence remains attributed to the correct persona without seed substitution
- Act produces only a governed proposal and no direct LEAN, broker, capital, or deployment mutation
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-VERIFY-OODA-001/`

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
