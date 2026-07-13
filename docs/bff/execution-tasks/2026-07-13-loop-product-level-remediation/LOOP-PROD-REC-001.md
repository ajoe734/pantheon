# LOOP-PROD-REC-001 — Full-stack loop recovery and fault-injection harness

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
| Fleet lane | `loop-recovery-harness` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | fragmented restart tests without uniform admitted-command guarantees |
| Target maturity | contract |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

建立可重複的 target-dev recovery harness，在 outbox、downstream mutation、receipt、projection 各切點注入故障，並驗證 duplicate、lease expiry、timeout、worker/BFF/DB/full-stack restart。

Next action: Implement the shared failure matrix, deterministic injection points, recovery assertions, and evidence manifest output.

## Dependencies

- `LOOP-PROD-001`
- `LOOP-PROD-002`

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

- `task-specific desired state and admitted commands`
- `failure matrix`
- `accelerated test controller interval`

## Actual-state authorities

- `canonical effects and receipts`
- `controller records and DLQ`
- `checksummed recovery evidence manifest`

## Declared artifacts

- `scripts/run_loop_product_recovery_matrix.py`
- `scripts/test_loop_product_recovery_matrix.py`
- `services/loop-control`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-REC-001`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- harness injects failure before/after outbox persist, before/after downstream mutation, after mutation before receipt, and before projection
- admitted commands have RPO=0 and recover within two declared test controller intervals
- duplicates, retries, lease expiry, timeout, replay, and restarts create no duplicate canonical side effect
- production cadence is unchanged and all injected effects remain target-dev/paper only
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-REC-001/`

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
