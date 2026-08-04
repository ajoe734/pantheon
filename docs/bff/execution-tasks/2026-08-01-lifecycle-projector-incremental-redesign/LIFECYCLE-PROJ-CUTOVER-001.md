# LIFECYCLE-PROJ-CUTOVER-001 — Target-dev canary and cutover

Status: depends on hotfix review, migration, and capacity tasks all `done`

| Field | Value |
| --- | --- |
| Owner capability | Antigravity — dev release, hosted probes, migration, and rollback |
| Independent reviewer | Human/Ops — release, security, readback, and rollback |
| Branch | `task/lifecycle-proj-cutover-001` |
| Worktree | `/tmp/pantheon-worker-worktrees/pantheon/lifecycle-proj-cutover-001` |
| Merge target | `dev` |

## Objective

Deploy exact merged identities, canary the Postgres reader, prove a real paper
lifecycle and rollback, then switch all target-dev paper reads.

## Declared artifacts

- `docker-compose.yml`
- `services/trade_journey/hosted_lifecycle_probe.py`
- `services/trade_journey/hosted_bff_readback.py`
- their focused tests
- `docs/operations/lifecycle-projector-incremental-runbook.md`
- `docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-CUTOVER-001/`

## Acceptance and validation

- Record merged SHA, image digest, migration/config identity, and controller row
  before enabling reads.
- Require shadow backlog zero, unexplained parity zero, capacity pass, and
  security pass; canary an authorized target-dev paper scope first.
- Prove one real paper signal-to-reconciliation lifecycle through authenticated
  journey and loop readback.
- Prove stale/conflict, cross-scope, sensitive-field, and unauthorized failures.
- Rehearse rollback to the last accepted reader and forward recovery without
  data loss; stale JSON must remain labeled stale.
- Observe 24 hours inside freshness/resource/error gates and publish reviewed
  PR, merge SHA, deployment manifest, and checksummed evidence.

## Boundaries, rollout, and rollback

No production, live capital/broker, legacy retirement, or supervisor changes.
Rollout is shadow -> paper canary -> all target-dev paper -> 24-hour observation.
Rollback changes one BFF backend flag, restarts only BFF, and stops the new
worker if required.
