# LOOP-PROD-000 — Canonical loop inventory and OODA overlay truth

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
| Fleet lane | `loop-inventory-truth` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | registry-only with an unregistered OODA overlay |
| Target maturity | contract |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

校正 loop catalog、BFF inventory 與 verification index：維持 12 個 L1 canonical loops，新增 per_persona_ooda 為 composite_overlay 並宣告 composed_of；archived task 不得被投影成 live maturity。

Next action: Reconcile the canonical catalog, OODA overlay classification, verification counts, and maturity projection tests.

## Dependencies

- None

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

- `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`
- `Pantheon_總索引版系統分析文件.md Per-Persona OODA`
- `docs/deployment/loop-catalog.registry.json`

## Actual-state authorities

- `/bff/v5/loop-inventory`
- `/bff/v5/loop-health`
- `verification index counts`
- `ai-task-archive task states`

## Declared artifacts

- `docs/deployment/loop-catalog.registry.json`
- `services/control-plane/bff/loop_inventory.py`
- `services/control-plane/bff/test_loop_inventory_read_model_contract.py`
- `services/control-plane/bff/test_loop_health_read_model_contract.py`
- `docs/04`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- catalog contains exactly twelve canonical loops and one composite OODA overlay with composed_of metadata
- verification indexes and tests use the same 12+overlay classification
- archived task completion cannot create controller liveness or maturity
- L1 continuously-resident execution claim remains limited to Capital Pool Execution
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-000/`

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
