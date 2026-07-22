# LOOP-PROD-BFF-001 — Authoritative BFF health monitoring and loop-health projection

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
| Fleet lane | `bff-health-controller` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | api-only monitor and registry-only loop-health |
| Target maturity | product-level |
| Human/Ops final sign-off | not a separate ordinary task |

## Product outcome

BFF monitor 使用各 downstream 真實 readiness contract，telemetry 具 canonical identities 並持久化 incident/recovery；/bff/v5/loop-health 讀 controller snapshots，不再 registry-only，清楚區分 stale/snapshot/scheduled/reconciled/live。

Next action: Make BFF and loop health authoritative, durable, identity-correct, and visibly honest across outage and restart.

## Dependencies

- `LOOP-PROD-001`
- `LOOP-PROD-TEL-001`
- `LOOP-PROD-AUTH-001`
- `LOOP-PROD-REC-001`
- `LOOP-PROD-EVO-001`

Only `done` satisfies a dependency. `superseded`, `cancelled`, missing,
or merely submitted work does not.

## Loop scope

- `bff_health_monitoring`

## Desired-state authorities

- `downstream readiness contracts`
- `loop controller record contract`
- `monitor policy`

## Actual-state authorities

- `health probes and durable monitor events`
- `incident/recovery records`
- `loop controller snapshots`
- `/bff/v5/loop-health`

## Declared artifacts

- `services/control-plane/bff`
- `services/bff`
- `docker-compose.yml`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-BFF-001`

This task owns exactly one repository. For execute-plans tasks every declared
artifact carries the `execute-plans/` routing prefix; Pantheon evidence
aggregation is performed by a separate Pantheon verifier or closeout task.

## Acceptance

- each downstream probe follows its actual readiness contract and carries valid tenant/environment/component identity
- probe failure, error-rate degradation, incident, recovery, and freshness transitions are durable
- loop-health returns twelve canonical rows plus OODA overlay with controller records and no registry-only accepted liveness
- outage, stale snapshot, BFF/worker/DB/full-stack restart, RPO, and recovery-time proof pass
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

`docs/deployment/evidence/loop-product-level/LOOP-PROD-BFF-001/`

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
