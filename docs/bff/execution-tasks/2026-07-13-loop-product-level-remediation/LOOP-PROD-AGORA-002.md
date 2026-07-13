# LOOP-PROD-AGORA-002 — Implement six deferred Strategy Workshop operations

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
| Fleet lane | `strategy-workshop-bff` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | honest fail-closed 501 operations |
| Target maturity | integrated |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

實作 v1.5 六個目前故意 501 的 operations：GET/POST versions、select version、POST research-runs、POST consultations、POST conclude；全部走 canonical store/command 並更新 OpenAPI/bundle/compat manifest。

Next action: Implement the six canonical operations and close contract, governance, idempotency, and compatibility gaps.

## Dependencies

- `LOOP-PROD-CONS-001`
- `LOOP-PROD-ALPHA-001`
- `AG-GAP-005`
- `LOOP-PROD-ATTEST-001`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `agora_interaction_evidence`
- `consultation`
- `alpha_replication`

## Desired-state authorities

- `Strategy Workshop v1.5 OpenAPI operations`
- `version/research/consultation/conclude governance policy`

## Actual-state authorities

- `canonical StrategySpec version store`
- `ExperimentRun store`
- `consultation store`
- `conclusion receipt and audit`

## Declared artifacts

- `services/control-plane/bff`
- `contracts`
- `schemas`
- `docs/contracts/agora`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-AGORA-002`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- all six operations perform canonical commands and return terminal authoritative readback instead of 501
- RBAC, MFA, idempotency, stale revision, wrong tenant, and missing approval negatives pass
- OpenAPI, contract bundle, generated hashes, and compatibility manifest are exact and green
- no conclude path directly deploys or trades
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-AGORA-002/`

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
