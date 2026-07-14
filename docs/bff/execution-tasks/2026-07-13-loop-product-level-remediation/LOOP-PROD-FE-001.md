# LOOP-PROD-FE-001 — Gate-before-deploy safe execute-plans release

Status: ready for fleet dispatch after the program packet is merged

Canonical catalog: `tasks.json`

Source plan:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/LOOP_PRODUCT_LEVEL_REMEDIATION_PLAN_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex |
| Reviewer | Codex2 |
| Wave | 0 |
| Fleet lane | `fe-safe-release` |
| Repository | `execute-plans` |
| Merge target | `dev` |
| Current maturity | failed post-switch deploy can remain live with unsafe write flags |
| Target maturity | product-level |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

重構 execute-plans dev release：exact candidate SHA integration gate 先通過，candidate pre-probe 後才 atomic switch，post-switch failure 自動 rollback；safe writes default false，browser 不含 bearer/client secret，並拒絕 out-of-order deploy。

Next action: Make the exact-SHA gate, candidate probe, atomic switch, rollback, safe auth, and hosted quality checks one fail-closed release path.

## Dependencies

- `LOOP-PROD-002`
- `LOOP-PROD-AUTH-001`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `bff_health_monitoring`

## Desired-state authorities

- `GitHub dev head SHA`
- `verified immutable frontend artifact`
- `strict BFF compatibility and auth contract`
- `previous accepted release`

## Actual-state authorities

- `hosted deployment.json`
- `release symlink target`
- `integration-gate run and artifact digest`
- `post-switch probes and rollback journal`

## Declared artifacts

- `execute-plans/.github/workflows`
- `execute-plans/scripts`
- `execute-plans/public/deployment.json`
- `execute-plans/e2e`
- `execute-plans/docs/04/loop-product-level`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- deployment cannot start until the exact SHA and artifact digest pass required integration checks
- candidate assets, contract, browser, auth, and write posture are probed before live switch
- post-switch failure restores and re-probes the prior SHA; a target-dev rollback drill passes
- older or out-of-order SHA cannot replace a newer accepted release; emergency override is audited and cannot bypass integrity/auth
- manifest shows real and dev-stub writes false by default and bundle/storage contains no bearer or privileged credential
- hosted desktop 1440px and mobile 390px pass axe serious/critical zero, keyboard/focus, reduced motion, FE_INT_GATE_PERF_STRICT=1, and zero unexpected console/CORS/chunk/BFF errors
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

`execute-plans/docs/04/loop-product-level/LOOP-PROD-FE-001/`

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
