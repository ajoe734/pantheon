# LOOP-PROD-AUTH-001 — Strict dev auth cutover and exact BFF build identity

Status: ready for fleet dispatch after the program packet is merged

Canonical catalog: `tasks.json`

Source plan:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/LOOP_PRODUCT_LEVEL_REMEDIATION_PLAN_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex2 |
| Reviewer | Codex |
| Wave | 0 |
| Fleet lane | `strict-auth-build-identity` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | auth stub and unknown BFF build identity |
| Target maturity | proven-live |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

沿用既有 /bff/auth/dev-login，將 hosted dev 切到 AUTH_STUB=false/strict；使用短效 role/tenant identities，移除 default all-role bearer，並在 /bff/version 暴露非敏感 git SHA、image digest、build time、environment、config posture。

Next action: Cut hosted dev to governed scoped auth and make exact BFF build/config identity independently verifiable.

## Dependencies

- `LOOP-PROD-002`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `bff_health_monitoring`

## Desired-state authorities

- `dev auth deployment configuration`
- `role/tenant policy`
- `BFF build metadata supplied by deployment`

## Actual-state authorities

- `/bff/auth/dev-login and /bff/me`
- `/bff/version`
- `BFF audit records`
- `hosted RBAC/MFA/two-person matrix`

## Declared artifacts

- `services/control-plane/bff`
- `docker-compose.yml`
- `deploy`
- `docs/deployment/bff-strict-auth-build-identity.md`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-AUTH-001`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- hosted BFF runs strict auth with AUTH_STUB=false and rejects fixed or cross-environment identities
- viewer, operator, approver, risk owner, operator A, and operator B use distinct short-lived scoped identities
- anonymous, wrong tenant, missing MFA/approval, same-actor, expired and replayed two-person proofs fail closed
- version endpoint and health evidence identify exact git SHA, image digest, build time, environment, and safe config fingerprint
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-AUTH-001/`

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
