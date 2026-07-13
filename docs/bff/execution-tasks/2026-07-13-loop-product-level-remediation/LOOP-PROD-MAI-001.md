# LOOP-PROD-MAI-001 — Hosted Management AI repair and dev-bridge backend proof

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
| Fleet lane | `management-ai-repair-be` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | component/provider readiness without hosted repair sentinel closure |
| Target maturity | product-level |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

在 hosted strict auth 下證明 debug read-only 與 repair 完整鏈：activate→prepare narrow clean worktree→forward metadata→sentinel write/readback→SA/SD→pending packet→supervisor processed receipt→archive→deactivate。

Next action: Prove the complete hosted Management AI repair and dev-bridge lifecycle with narrow scope, receipts, security negatives, and restart recovery.

## Dependencies

- `LOOP-PROD-AUTH-001`
- `LOOP-PROD-001`
- `LOOP-PROD-002`
- `LOOP-PROD-REC-001`
- `LOOP-PROD-TJ-001`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `per_persona_ooda`
- `bff_health_monitoring`

## Desired-state authorities

- `authorized kernel_debug/kernel_repair mode`
- `declared repo-relative scope and expected branch`
- `SA/SD generation request`

## Actual-state authorities

- `prepared clean worktree metadata`
- `OpenClaw conversation and sentinel readback`
- `dev-bridge packet/receipt`
- `supervisor archive and mode audit`

## Declared artifacts

- `services/control-plane/bff`
- `services/openclaw-adapter`
- `scripts/verify_management_ai_repair.py`
- `scripts/ai_status.py`
- `docs/operations`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-MAI-001`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- kernel_debug provider work is read-only and repair requires active authorized kernel_repair
- prepare returns a clean narrow task worktree and exact metadata forwarded to the conversation
- harmless in-scope sentinel write/readback, SA/SD generation, pending packet, supervisor processing receipt, and archive all correlate
- dot scope, shared checkout, wrong repo/branch/role/tenant/MFA, duplicate, and post-deactivation writes fail closed
- BFF/adapter/supervisor restart preserves admitted work with RPO=0 and bounded recovery
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-MAI-001/`

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
