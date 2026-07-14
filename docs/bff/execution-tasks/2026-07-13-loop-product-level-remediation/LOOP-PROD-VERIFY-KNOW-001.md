# LOOP-PROD-VERIFY-KNOW-001 — Target-dev Knowledge spine product verifier

Status: ready for fleet dispatch after the program packet is merged

Canonical catalog: `tasks.json`

Source plan:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/LOOP_PRODUCT_LEVEL_REMEDIATION_PLAN_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex2 |
| Reviewer | Codex |
| Wave | 2 |
| Fleet lane | `verify-knowledge-spine` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | component/integrated fragments |
| Target maturity | product-level |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

在 clean target-dev 驗證 Scenario A：real source→schedule→SourceRecord→distillation draft→reviewed replication→ExperimentRun→teaching/consultation/evidence handoff，含 duplicate/source/provider failure/restart/immutable protection。

Next action: Run and archive the complete target-dev Knowledge spine product drill with failure and recovery branches.

## Dependencies

- `LOOP-PROD-SRC-001`
- `LOOP-PROD-DIST-001`
- `LOOP-PROD-ALPHA-001`
- `LOOP-PROD-TEACH-001`
- `LOOP-PROD-AGORA-001`
- `LOOP-PROD-AGORA-002`
- `LOOP-PROD-AGORA-003`
- `LOOP-PROD-CONS-001`
- `LOOP-PROD-IMIT-001`
- `LOOP-PROD-BFF-001`

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

## Desired-state authorities

- `Scenario A run specification`
- `dynamic persona/source/provider identities`

## Actual-state authorities

- `canonical source, draft, experiment, teaching, consultation, dataset/handoff records`
- `controller truth for every segment`

## Declared artifacts

- `scripts/verify_loop_product_knowledge_spine.py`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-VERIFY-KNOW-001`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- one dynamic run traverses every Scenario A canonical authority with shared lineage
- duplicate, source failure, provider unavailable, stale draft, immutable protection, DLQ/replay, and restart assertions pass
- all segment controller records are fresh and match terminal canonical readbacks
- verifier emits a checksummed machine evidence manifest and safe cleanup instructions
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-VERIFY-KNOW-001/`

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
