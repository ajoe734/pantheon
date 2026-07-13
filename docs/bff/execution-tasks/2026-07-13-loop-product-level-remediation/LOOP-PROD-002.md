# LOOP-PROD-002 — Product evidence schema and anti-false-close gate

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
| Fleet lane | `evidence-closeout-guard` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | loop guard checks review_file but not complete product evidence |
| Target maturity | contract |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

建立 machine-readable product evidence schema 與 supervisor closeout guard，拒絕 phantom cross-repo delivery、mock-only live claim、缺 terminal readback/restart/hosted/security/reviewer 或 unsupported maturity。

Next action: Ship the evidence schema, verifier, supervisor done gate integration, and negative regression suite.

## Dependencies

- `LOOP-PROD-000`
- `LOOP-PROD-001`

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

- `master plan product gates G0-G6`
- `task proof_required and non_goals`
- `repository delivery gate rules`

## Actual-state authorities

- `checksummed evidence.json manifests`
- `merged PR and deployment metadata`
- `controller truth records`
- `hosted test reports`

## Declared artifacts

- `schemas/product-evidence.schema.json`
- `scripts/loop_done_guardrail.py`
- `scripts/ai_status.py`
- `scripts/test_loop_done_guardrail.py`
- `docs/deployment/evidence`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- guard rejects missing or stale requirement evidence and emits exact blocking requirement IDs
- cross-repo artifact routing and merge-target ancestry are verified before done
- frontend claims require exact hosted desktop/mobile, strict performance, accessibility, and network evidence
- maturity may only be raised from accepted controller truth and reviewed evidence
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-002/`

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
