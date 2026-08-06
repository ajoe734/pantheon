# Task Brief: SUP-DISPATCH-REFACTOR-PROPOSAL-DOC-COMMIT-20260806

## Task
- Title: Commit the missing supervisor dispatch refactor proposal doc
- Owner: Claude
- Reviewer: Antigravity
- Delivery PR: https://github.com/ajoe734/pantheon/pull/4587 (base `dev`)
- Artifact: `docs/04/supervisor_dispatch_refactor_proposal_2026-08-04.md`

## Problem

`docs/04/supervisor_dispatch_refactor_proposal_2026-08-04.md` was written on
2026-08-04 during a live supervisor-dispatch debugging session and immediately
used as a hard, named acceptance-criteria dependency by five dispatched tasks.
It was only ever committed to the local status-root checkout
(`/home/lupin/pantheon`); it was never pushed to `dev`.

Any worker whose task worktree branched from `dev` therefore found the file
missing and correctly refused to proceed. That is the reason
`SUP-BLOCKED-TASK-RECONCILIATION-20260804` has been sitting `blocked` since
`2026-08-06T02:25:13Z`.

## Delivery

- Commit `5b99d94ea48994da0313562609813032fda8b92a`
  ("SUP-DISPATCH-REFACTOR-PROPOSAL-DOC-COMMIT: add missing doc") adds the file,
  222 insertions, 1 file changed. Pure documentation addition; no code or
  behavior change.
- The committed blob is byte-identical to the status-root copy at
  `/home/lupin/pantheon/docs/04/supervisor_dispatch_refactor_proposal_2026-08-04.md`
  (`diff` reports no difference).
- The task branch was merged forward from `origin/dev` so the PR is not stale;
  the two-dot diff against `origin/dev` is still the single added doc.

## Acceptance verification (2026-08-06, by owner)

Acceptance criterion 3 asks that the doc content match what the five
2026-08-04 tasks actually cite. Section headings present in the committed doc:

| Task | Cited section | Present in committed doc |
| --- | --- | --- |
| `SUP-PROVIDER-PROBE-HYSTERESIS-20260804` | Problem 1 | yes — "the capability probe is flaky and directly gates dispatch, inline, on every cycle" |
| `SUP-DISPATCH-EXPLAIN-TOOL-20260804` | Problem 2 | yes — "no \"why not dispatched\" explain path" |
| `SUP-STATUS-OUTBOX-INTEGRITY-VISIBILITY-20260804` | Problem 3 | yes — fail-closed integrity gate is also the only status write-back path |
| `SUP-TASK-FAILURE-STREAK-SCHEMA-20260804` | Problem 4 | yes — "retry exhaustion is invisible at the board level" |
| `SUP-BLOCKED-TASK-RECONCILIATION-20260804` | Problem 5 | yes — "`blocked` is a one-way door, and every fix so far has been a point patch" |

The four `done` tasks' citations were read from
`ai-task-archive/tasks/<id>.json`; the still-`blocked`
`SUP-BLOCKED-TASK-RECONCILIATION-20260804` citation was read from the live
canonical row. Every cited section resolves.

Known cosmetic drift, deliberately not "fixed" so the committed file stays
byte-identical to the reviewed status-root original: `## Problem 5` is the last
heading in the file, appearing after `## Suggested sequencing`, because it was
appended after a second debugging session the same day. Content is complete;
only the ordering is out of sequence.

## Remaining acceptance

Criterion 1 ("must exist on dev; verify it is readable in a fresh
clone/worktree") cannot be satisfied by the owner alone — it is satisfied when
PR #4587 merges into `dev`. Verified at closeout, not before.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and
  `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Governed status changes run through `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh`.
