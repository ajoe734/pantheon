# OPS-TASK-PR-TRIAGE-002 evidence report

Generated from live GitHub and git evidence at `2026-07-29T03:47:00Z`.
Base proof: `origin/dev` = `31d7eaebcb012a9beb5f5f0057db401d378b5beb`.

## Cohort result

The fixed audit cohort contains **25** task PRs: 25 remain open and 0 are now closed or merged.
Repository-wide, **36** task PRs are open at this snapshot; that global count includes recent PRs outside the fixed overdue cohort.

| PR | State | Merge | Draft | Disposition | Owner | Evidence |
|---:|---|---|:---:|---|---|---|
| [#4262](https://github.com/ajoe734/pantheon/pull/4262) | OPEN | BEHIND | no | active-repair | Antigravity | open task PR needs owner refresh/rebase or an explicit retirement decision |
| [#4073](https://github.com/ajoe734/pantheon/pull/4073) | OPEN | BEHIND | no | active-repair | Orchestrator | open task PR needs owner refresh/rebase or an explicit retirement decision |
| [#3949](https://github.com/ajoe734/pantheon/pull/3949) | OPEN | BEHIND | no | active-repair | Claude | open task PR needs owner refresh/rebase or an explicit retirement decision |
| [#3820](https://github.com/ajoe734/pantheon/pull/3820) | OPEN | BEHIND | yes | protected-retain | Codex | draft PR is protected from automatic retirement |
| [#3817](https://github.com/ajoe734/pantheon/pull/3817) | OPEN | DIRTY | no | conflict-needs-owner | Codex | GitHub merge state is DIRTY and no supersession proof exists |
| [#3799](https://github.com/ajoe734/pantheon/pull/3799) | OPEN | BEHIND | no | active-repair | Codex | open task PR needs owner refresh/rebase or an explicit retirement decision |
| [#3788](https://github.com/ajoe734/pantheon/pull/3788) | OPEN | BEHIND | no | active-repair | Codex | open task PR needs owner refresh/rebase or an explicit retirement decision |
| [#3779](https://github.com/ajoe734/pantheon/pull/3779) | OPEN | DIRTY | no | conflict-needs-owner | Codex | GitHub merge state is DIRTY and no supersession proof exists |
| [#3774](https://github.com/ajoe734/pantheon/pull/3774) | OPEN | DIRTY | no | conflict-needs-owner | Antigravity | GitHub merge state is DIRTY and no supersession proof exists |
| [#3763](https://github.com/ajoe734/pantheon/pull/3763) | OPEN | BEHIND | no | active-repair | Antigravity | open task PR needs owner refresh/rebase or an explicit retirement decision |
| [#3736](https://github.com/ajoe734/pantheon/pull/3736) | OPEN | DIRTY | no | conflict-needs-owner | Claude | GitHub merge state is DIRTY and no supersession proof exists |
| [#3638](https://github.com/ajoe734/pantheon/pull/3638) | OPEN | DIRTY | yes | protected-retain | Codex | draft PR is protected from automatic retirement |
| [#3572](https://github.com/ajoe734/pantheon/pull/3572) | OPEN | DIRTY | yes | protected-retain | Codex | draft PR is protected from automatic retirement |
| [#3554](https://github.com/ajoe734/pantheon/pull/3554) | OPEN | DIRTY | yes | protected-retain | Codex | draft PR is protected from automatic retirement |
| [#3039](https://github.com/ajoe734/pantheon/pull/3039) | OPEN | DIRTY | no | conflict-needs-owner | Claude | GitHub merge state is DIRTY and no supersession proof exists |
| [#2550](https://github.com/ajoe734/pantheon/pull/2550) | OPEN | DIRTY | no | conflict-needs-owner | Orchestrator | GitHub merge state is DIRTY and no supersession proof exists |
| [#1680](https://github.com/ajoe734/pantheon/pull/1680) | OPEN | BEHIND | no | active-repair | claude-opus-4-8 | open task PR needs owner refresh/rebase or an explicit retirement decision |
| [#1635](https://github.com/ajoe734/pantheon/pull/1635) | OPEN | BEHIND | no | active-repair | Claude | open task PR needs owner refresh/rebase or an explicit retirement decision |
| [#1554](https://github.com/ajoe734/pantheon/pull/1554) | OPEN | DIRTY | no | conflict-needs-owner | Claude | GitHub merge state is DIRTY and no supersession proof exists |
| [#1552](https://github.com/ajoe734/pantheon/pull/1552) | OPEN | BEHIND | no | active-repair | Claude | open task PR needs owner refresh/rebase or an explicit retirement decision |
| [#1551](https://github.com/ajoe734/pantheon/pull/1551) | OPEN | BEHIND | no | active-repair | Claude | open task PR needs owner refresh/rebase or an explicit retirement decision |
| [#1548](https://github.com/ajoe734/pantheon/pull/1548) | OPEN | BEHIND | no | active-repair | Claude | open task PR needs owner refresh/rebase or an explicit retirement decision |
| [#1544](https://github.com/ajoe734/pantheon/pull/1544) | OPEN | BEHIND | no | active-repair | Claude | open task PR needs owner refresh/rebase or an explicit retirement decision |
| [#1539](https://github.com/ajoe734/pantheon/pull/1539) | OPEN | BEHIND | no | active-repair | Claude | open task PR needs owner refresh/rebase or an explicit retirement decision |
| [#1531](https://github.com/ajoe734/pantheon/pull/1531) | OPEN | DIRTY | no | conflict-needs-owner | Claude | GitHub merge state is DIRTY and no supersession proof exists |

## Superseded closure manifest

Only the following still-open PRs passed the fail-closed closure rule.

| PR | Head | Owner | Durable evidence |
|---:|---|---|---|
| - | - | - | No closure candidates |

## Branch inventory and deletion dry run

- Remote task branches: 2233
- No-open-PR task branches: 2197
- Dry-run deletion candidates: 1334
- No branch deletion command exists in this task or tool.

Disposition counts:

- `abandoned-unproven`: 83
- `active-repair`: 24
- `conflict-needs-owner`: 8
- `merged-reachable`: 2064
- `protected-retain`: 50
- `superseded`: 4

The machine-readable report contains every branch, joined PR history, active/archive state, reachability, age, and exclusion reasons. The separate dry-run manifest includes only heads older than the retention window that have no open PR and are already ancestors of current `dev`.

## Review handoff

The task branch composed `origin/dev` at
`31d7eaebcb012a9beb5f5f0057db401d378b5beb` through merge commit
`93ba38a7d9a153990930fea859718d22ac1787ce`. The generated evidence is
therefore based on the same immutable `dev` head present in the task branch.

The assigned reviewer is **Codex**. Review must bind PR #4296 and the exact
head published after this evidence refresh; this report deliberately does not
pre-claim that future approval. Until the governed reviewer command records
that exact head, the `Pantheon canonical review gate` failure is expected and
merge remains unauthorized.

This refresh did not close or merge a PR, delete a branch, enable auto-merge,
or push directly to a protected branch.
