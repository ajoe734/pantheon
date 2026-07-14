# LOOP-PROD-PER-001 — Persona provisioning through binding and first-evaluation readback

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
| Fleet lane | `persona-provisioning-path` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | partial provisioning with missing first evaluation schedule proof |
| Target maturity | product-level |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

persona create 必須保持 provisioning，直到 canonical RuntimeBinding、paper worker 與 first evaluation schedule 全部 read back；使用真實 persona identity，不可 seed binding，失敗需 terminal/restart-safe。

Next action: Close create-paper provisioning with canonical binding, worker, first-evaluation schedule, dynamic identity, and terminal failure truth.

## Dependencies

- `LOOP-PROD-DEP-001`
- `LOOP-PROD-CAP-001`
- `LOOP-PROD-OODA-001`
- `PPL-ALLOC-010`
- `PPL-ALLOC-011`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `promotion_deployment`
- `capital_pool_execution`
- `per_persona_ooda`

## Desired-state authorities

- `create-paper persona specification`
- `deployment and evaluation schedule policy`

## Actual-state authorities

- `persona lifecycle state`
- `RuntimeBinding`
- `paper worker heartbeat`
- `first evaluation schedule`
- `controller records`

## Declared artifacts

- `services/control-plane/bff`
- `services/deployment`
- `services/runtime-manager`
- `services/control-plane/cron`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-PER-001`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- create response remains provisioning until binding, worker, and first evaluation schedule are authoritatively read back
- new dynamic persona identity propagates without seed name, pool, binding, or metric substitution
- downstream failure reaches an explicit terminal lifecycle state and survives restart
- duplicate create and retry converge to one persona, one binding, one worker, and one evaluation schedule
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-PER-001/`

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
