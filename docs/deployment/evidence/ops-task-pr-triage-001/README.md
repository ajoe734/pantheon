# OPS-TASK-PR-TRIAGE-001 evidence report

Generated from live GitHub and git evidence at `2026-07-22T17:43:50Z`.
Base proof: `origin/dev` = `beef87e0a96c7ecf770edf312eb05cb896b7cebd`.

## Cohort result

The fixed audit cohort contains **29** task PRs: 27 remain open and 2 were resolved by the lease-repair task.

| PR | State | Merge | Draft | Disposition | Owner | Evidence |
|---:|---|---|:---:|---|---|---|
| [#3949](https://github.com/ajoe734/pantheon/pull/3949) | OPEN | BEHIND | no | active-repair | Claude | open task PR needs owner refresh/rebase or an explicit retirement decision |
| [#3948](https://github.com/ajoe734/pantheon/pull/3948) | MERGED | - | no | merged-reachable | Codex | GitHub records the cohort PR as merged; merged replacement(s) #3956, #3957 |
| [#3936](https://github.com/ajoe734/pantheon/pull/3936) | CLOSED | - | no | superseded | Codex | closed PR has explicit supersession/merged-replacement evidence; merged replacement(s) #3948 |
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
| [#3372](https://github.com/ajoe734/pantheon/pull/3372) | OPEN | BEHIND | no | superseded | Codex | durable task archive records terminal_outcome=superseded; merged replacement(s) #3418, #3435 |
| [#3334](https://github.com/ajoe734/pantheon/pull/3334) | OPEN | BEHIND | no | superseded | Codex | completed task archive cites a different merged Pantheon PR; merged replacement(s) #3327, #3332 |
| [#3317](https://github.com/ajoe734/pantheon/pull/3317) | OPEN | BEHIND | no | superseded | Codex | completed task archive cites a different merged Pantheon PR; merged replacement(s) #3311, #3316 |
| [#3058](https://github.com/ajoe734/pantheon/pull/3058) | OPEN | BEHIND | no | superseded | Codex | durable task archive records terminal_outcome=superseded; merged replacement(s) #3057 |
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
| #3372 | `task/MGMT-PERF-IA-008-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Codex | `ai-task-archive/tasks/MGMT-PERF-IA-008-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.json` (superseded); #3418 merged, #3435 merged |
| #3334 | `task/MGMT-OPS-003-GAP-004-MOBILE-HUMAN-INBOX-FIX-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | Codex | `ai-task-archive/tasks/MGMT-OPS-003-GAP-004-MOBILE-HUMAN-INBOX-FIX-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.json` (completed); #3327 merged, #3332 merged |
| #3317 | `task/MGMT-OPS-003-GAP-003` | Codex | `ai-task-archive/tasks/MGMT-OPS-003-GAP-003.json` (completed); #3311 merged, #3316 merged |
| #3058 | `task/AG-DYNUI-LIVE-WORKSHOP-009` | Codex | `ai-task-archive/tasks/AG-DYNUI-LIVE-WORKSHOP-009.json` (superseded); #3057 merged |

### Applied closure results

The explicit allowlist was applied at `2026-07-22T17:45:13Z`. PRs #3058,
#3317, #3334, and #3372 were each revalidated at the recorded head, received
an evidence comment, and are now closed. For Agora #3058, execute-plans #218
was also rechecked as merged at `9d60297e5c200d05214df7f758ee0c20c224db02`,
with Pantheon evidence #3057 merged at
`b661654b444eb4a111fe6ded0f958d57255f757b`. Exact comment URLs and heads are
recorded in `closure-results.json`.

No branch was deleted.

## Branch inventory and deletion dry run

- Remote task branches: 2091
- No-open-PR task branches: 2064
- Dry-run deletion candidates: 0
- No branch deletion command exists in this task or tool.

Disposition counts:

- `abandoned-unproven`: 1223
- `active-repair`: 15
- `conflict-needs-owner`: 8
- `merged-reachable`: 301
- `protected-retain`: 534
- `superseded`: 10

The machine-readable report contains every branch, joined PR history, active/archive state, reachability, age, and exclusion reasons. The separate dry-run manifest includes only heads older than the retention window that have no open PR and are already ancestors of current `dev`.
