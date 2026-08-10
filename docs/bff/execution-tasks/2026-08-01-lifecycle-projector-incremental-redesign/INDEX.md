# Lifecycle Projector Incremental Redesign — Execution Tasks

Status: ready for governed supervisor dispatch after this catalog is merged

Canonical catalog: [tasks.json](tasks.json)

Source design:
`docs/04/pantheon_lifecycle_projector_incremental_redesign_2026-08-01/archive/LIFECYCLE_PROJECTOR_INCREMENTAL_REDESIGN_PLAN_2026-08-01.md`

## Outcome

Replace the current full-history JSON projector with a Postgres-backed
incremental projection whose worker memory is bounded by batch size and affected
aggregates. Preserve lifecycle identity, idempotency, live/recovery truth,
tenant isolation, existing BFF contracts, and rollback.

The permanent redesign is coordination work. Only supervisor-dispatched
auto-workers may implement the packets below. Each task has one owner, an
eligible independent reviewer, a clean worktree/branch, a PR to `dev`, and a
checksummed evidence directory.

## Incident prerequisite

`LIFECYCLE-PROJ-HOTFIX-REVIEW-20260801` is already queued through the signed
assistant dev bridge. It independently reviews exact PR #4448 head
`85e835448f7b86ce77ad9e4e0cc80961879b29c0`, merges only if accepted, and does
not restart the unbounded worker or delete existing projection data.

## Dependency and merge order

```text
LIFECYCLE-PROJ-STORE-001
  +--> LIFECYCLE-PROJ-REDUCER-001 --+
  +--> LIFECYCLE-PROJ-BFF-001 -----+--> LIFECYCLE-PROJ-MIGRATE-001 --+
                                   +--> LIFECYCLE-PROJ-CAPACITY-001 -+

LIFECYCLE-PROJ-HOTFIX-REVIEW-20260801 -------------------------------+
                                                                       |
                                                                       v
                                             LIFECYCLE-PROJ-CUTOVER-001
                                                                       |
                                                                       v
                                             LIFECYCLE-PROJ-RETIRE-001
```

Only `done` satisfies a dependency. `superseded`, `cancelled`, blocked,
missing, submitted, or a passing local test does not.

| Wave | Task | Scope | Owner / reviewer |
| ---: | --- | --- | --- |
| 1 | [LIFECYCLE-PROJ-STORE-001](LIFECYCLE-PROJ-STORE-001.md) | relational schema and transaction store | Antigravity / Human/Ops |
| 2 | [LIFECYCLE-PROJ-REDUCER-001](LIFECYCLE-PROJ-REDUCER-001.md) | pure reducer and bounded worker | Antigravity / Human/Ops |
| 2 | [LIFECYCLE-PROJ-BFF-001](LIFECYCLE-PROJ-BFF-001.md) | indexed BFF reader and pagination | Antigravity / Human/Ops |
| 3 | [LIFECYCLE-PROJ-MIGRATE-001](LIFECYCLE-PROJ-MIGRATE-001.md) | resumable backfill, shadow, and parity | Antigravity / Human/Ops |
| 3 | [LIFECYCLE-PROJ-CAPACITY-001](LIFECYCLE-PROJ-CAPACITY-001.md) | resource, query, and fault gates | Antigravity / Human/Ops |
| 4 | [LIFECYCLE-PROJ-CUTOVER-001](LIFECYCLE-PROJ-CUTOVER-001.md) | target-dev canary, cutover, rollback | Antigravity / Human/Ops |
| 5 | [LIFECYCLE-PROJ-RETIRE-001](LIFECYCLE-PROJ-RETIRE-001.md) | seven-day closeout and guarded legacy retirement | Antigravity / Human/Ops |

Tasks in the same wave may run concurrently only because their initial declared
file scopes do not overlap. Later tasks may integrate earlier scopes only after
all declared dependencies are done and merged.

## Common execution rules

- Repository: `ajoe734/pantheon`; merge target: `dev`.
- Use the expected clean task branch/worktree; do not edit the shared live
  checkout or `/home/lupin/pantheon-ci-deploy/dev-root`.
- Re-audit active tasks, branches, worktrees, and PRs before editing; do not
  duplicate or overwrite concurrent work.
- Stay inside declared artifacts. Expansion requires a governed packet update.
- Stage only intended files; run focused and adjacent validation; commit with
  required trailers; push; open a PR; wait for checks; obtain an independent
  review; merge only when accepted.
- `Codex` and `Codex2` are one identity and are not independent reviewers.
- Evidence goes under
  `docs/deployment/evidence/lifecycle-projector/<TASK-ID>/evidence.json` and is
  redacted, append-only, checksummed, and anchored by the reviewer.
- Missing, stale, contradicted, fixture-only, submitted, or registry-only proof
  fails closed.
- No task authorizes production, live-capital/broker side effects, canonical
  telemetry deletion, or supervisor/control-plane redesign.

## Dispatch receipt requirement

The signed source packet is
[task-packet.source.json](task-packet.source.json). It becomes dispatched only
after the canonical bridge reports a processed receipt and materializes every
task under `ai-task-archive/tasks/`. The source file itself is not a receipt and
must not be described as implementation underway.
