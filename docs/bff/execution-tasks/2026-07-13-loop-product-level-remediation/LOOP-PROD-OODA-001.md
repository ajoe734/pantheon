# LOOP-PROD-OODA-001 — Per-Persona OODA schedule reconciliation and product proof

Status: ready for fleet dispatch after the program packet is merged

Canonical catalog: `tasks.json`

Source plan:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/LOOP_PRODUCT_LEVEL_REMEDIATION_PLAN_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex2 |
| Reviewer | Codex |
| Wave | 1 |
| Fleet lane | `persona-ooda-controller` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | integrated cron-to-packet substrate without registered/default live truth |
| Target maturity | product-level |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

在既有 cron→provider turn→packet 基礎上，將 eligible persona desired state reconcile 成 exact canonical schedules，修 missing/orphan jobs；一次 run 對應一次 real turn 與一個 packet/terminal failure，Act 只能 proposal。

Next action: Productize the completed cron-to-packet substrate with desired-state reconciliation, overlay health, restart/orphan repair, and safety proof.

## Dependencies

- `LOOP-PROD-000`
- `LOOP-PROD-001`
- `LOOP-PROD-002`
- `LOOP-PROD-REC-001`
- `OPENCLAW-CRON-WRITE-SCOPE`
- `OPENCLAW-PERSONA-CRON-BACKFILL`
- `OPENCLAW-OODA-PACKET-CLOSURE`
- `LOOP-PROD-BFF-001`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `per_persona_ooda`

## Desired-state authorities

- `eligible persona set`
- `per-persona OODA cadence and provider policy`

## Actual-state authorities

- `canonical cron jobs`
- `provider turns`
- `persisted OODA packets`
- `orphan repair and controller records`

## Declared artifacts

- `services/control-plane/cron`
- `services/control-plane/ooda`
- `services/ooda`
- `docker-compose.yml`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-OODA-001`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- eligible persona set converges to exactly the canonical cron set and repairs missing/orphan jobs
- one cron run maps to one real provider turn and one packet or explicit terminal failure
- Observe, Orient, Decide, Act-proposal-only, and Learn evidence are linked per persona
- duplicate run, provider outage, orphan repair, worker/full-stack restart, and no direct LEAN/broker call are proven
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-OODA-001/`

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
