# LOOP-PROD-CLOSE-001 — Baseline 12-loop plus OODA product checkpoint

> Program-control overlay: this preserved baseline task is checkpoint-only.
> `LOOP-PROD-CLOSE-002` is the sole completion authority and cannot finish until
> `LOOP-PROD-SIGNOFF-001` has installed protected Human/Ops verdict enforcement.

Status: ready for fleet dispatch after the program packet is merged

Canonical catalog: `tasks.json`

Canonical contract SHA-256: `1e7ee295ddc306f605845270e31857ba68a14c3d0e82dd6ece0f3b394f186ebb`

Source plan:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/LOOP_PRODUCT_LEVEL_REMEDIATION_PLAN_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex2 |
| Reviewer | Codex |
| Wave | 4 |
| Fleet lane | `global-loop-product-closeout` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | program planning baseline |
| Target maturity | product-level |
| Human/Ops final sign-off | required |

## Product outcome

This task closes the original 36-task baseline evidence set only. Its `done`
state is necessary but never sufficient for completion of the additive program.

從 clean target-dev 重跑四大 scenarios；12 canonical loops 加 OODA overlay 全部要有 current controller records、無 registry-only truth，maturity 僅由 evidence 推導，並取得獨立 Human/Ops verdict。

Next action: Run the clean global product drill, reconcile evidence-derived maturity, obtain Human/Ops review, and close only with zero blocking product gaps.

## Dependencies

- `LOOP-PROD-002`
- `LOOP-PROD-AUTH-001`
- `LOOP-PROD-FE-001`
- `LOOP-PROD-REC-001`
- `LOOP-PROD-VERIFY-KNOW-001`
- `LOOP-PROD-VERIFY-EXEC-001`
- `LOOP-PROD-VERIFY-HUMAN-001`
- `LOOP-PROD-VERIFY-OODA-001`
- `LOOP-PROD-PPL-001`
- `LOOP-PROD-TJ-003`
- `LOOP-PROD-PINT-001`
- `LOOP-PROD-MAI-003`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `source_ingestion`
- `strategy_distillation`
- `alpha_replication`
- `persona_teaching`
- `agora_interaction_evidence`
- `human_imitation_shadow_evaluation`
- `consultation`
- `promotion_deployment`
- `capital_pool_execution`
- `telemetry_reconciliation`
- `evolution`
- `bff_health_monitoring`
- `per_persona_ooda`

## Desired-state authorities

- `master plan exit criteria`
- `all primary and external task evidence`
- `clean target-dev scenario specifications`

## Actual-state authorities

- `controller truth for 12 canonical loops and OODA overlay`
- `four scenario evidence manifests`
- `PPL/TJ/PINT/MAI closeouts`
- `exact release identities and Human/Ops verdict`

## Declared artifacts

- `docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/closeout`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-CLOSE-001`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- this task is a baseline scenario checkpoint only; `done` cannot mark the
  program complete, bypass SIGNOFF-001, or substitute for CLOSE-002
- all primary dependencies and required external dependencies are done, never superseded to a weaker outcome
- clean target-dev rerun passes Knowledge, Execution, Human Interaction, and Management Repair scenarios
- twelve canonical loop rows plus OODA overlay have fresh controller records and no accepted registry-only/snapshot/seed truth
- exact PR, checks, merge SHAs, FE/BFF/image identities, evidence checksums, reviewer verdicts, and residual risks are archived
- independent Human/Ops checkpoint verdict is recorded for later exact final
  re-verification; otherwise the program remains active
- archive branch, PR, required checks, merge SHA, independent reviewer verdict, and residual risk owner/expiry
- use authoritative pre/post readback; submitted, queued, seed, stub, fixture, snapshot, or synthetic receipt is not terminal success
- prove duplicate safety, failure/degraded behavior, restart/recovery, and rollback or compensation with admitted-command RPO=0
- record exact deployment identity and controller truth when deployed; no registry-only maturity promotion
- preserve RBAC, tenant, MFA, two-person, environment, and no-live-capital boundaries wherever applicable
- write a redacted append-only checksummed evidence.json; missing, indirect, stale, or contradicted requirements fail closed

## Required proof

- catalog-bound completion overlay declares this task `checkpoint_only`; this
  task emits bound checkpoint evidence and verdict digests but does not mutate
  the immutable overlay, append the protected consumption record, or complete
  the program
- focused and adjacent local validation
- merged PR and merge SHA
- target deployment identity where applicable
- canonical request, receipt, and terminal downstream readback correlation
- duplicate and restart/recovery evidence
- failure, degraded, rollback or compensation evidence
- security negative evidence and independent review

Reviewer approval must set `review_file` to the redacted, append-only,
checksummed machine evidence manifest under:

`docs/deployment/evidence/loop-product-level/LOOP-PROD-CLOSE-001/`

## Non-goals

- No panel-only closure
- No seed fixture as live proof
- No approval gate bypass
- No synthetic receipt as terminal execution proof
- No live-capital or live-broker side effect

## Dispatch and closeout rules

- publish only a baseline checkpoint verdict; never publish program completion
- task-local metadata cannot override the catalog-bound program overlay
- start only after every dependency is done; superseded does not satisfy a dependency
- use one clean task worktree in the declared repository and merge to dev through a reviewed PR
- do not silently narrow acceptance; record an evidence-backed blocker when the product outcome cannot be met
- reviewer must set review_file to the checksummed machine evidence manifest before approval
- deploy and verify exact target identity when the task changes a deployed capability

A passing unit test, route response, static panel, registry row, seed, snapshot,
queued command, or generated receipt cannot by itself close this task.
