# Task Audit Evidence: SUP-L12-STALE-PR-RETIRE-20260729

- Task owner: `Codex`
- Independent reviewer: `Codex2`
- Audit timestamp: `2026-08-04T15:06:41Z`
- Source audit: [1025Z three-pass gap audit](../../../../04/pantheon_twelve_loop_gap_2026-07-26/archive/THREE_PASS_GAP_AUDIT_2026-07-29T1025Z.md)

## Decision

`#4367` is the only scoped stale duplicate safe to retire. It is already
closed without merge and duplicates the delivery and closeout evidence merged
by `#4365` and `#4366`; this task made no further GitHub mutation.

The remaining scoped PRs must stay open. `#4364` is active OBS product proof;
`#4297` is an active `review_approved` status-sync task whose live head no
longer matches the recorded review binding; and `#4313` is an active blocked
closeout task. Closing any of them would discard an active proof or a declared
refresh path rather than retire a duplicate.

| PR | GitHub state/head | Canonical task | Action | Exact reason |
| --- | --- | --- | --- | --- |
| [#4367](https://github.com/ajoe734/pantheon/pull/4367) | `CLOSED`, `574e420ec4e78b58a6fdc530fffe2d9ab4220295`, `BEHIND` | `SUP-L12-REVIEW-PRIORITY-GATE-20260729` archived in the source audit | `RETIRED_ALREADY_CLOSED` | Duplicate receipt; #4365 delivery (`18e102a1950ab3aa9a2e9f97ad50313d1fa93d5d`) and #4366 closeout evidence (`8ea01a8e3993b3dabc6cd475c7058d299eaf4a01`) are both on `origin/dev`. |
| [#4364](https://github.com/ajoe734/pantheon/pull/4364) | `OPEN`, `f3756cec99a8c44d47c075a475c25cf86a4d3171`, `BEHIND` | `L12-VERIFY-OBS-001`, owner `Antigravity`, reviewer `Claude2`, `review` | `PRESERVED_OPEN` | Active product proof. The task row's stale next-head claim does not authorize closing; owner/reviewer need an exact current-head refresh and review. |
| [#4297](https://github.com/ajoe734/pantheon/pull/4297) | `OPEN`, `23a7d3244ad89d093a006ff6ace86f13053d794c`, `BEHIND` | `L12-FLEET-STATUS-SYNC-001`, owner `Codex2`, reviewer `Codex`, `review_approved` | `PRESERVED_REFRESH_REQUIRED` | Live head differs from canonical review binding `38057216e8e2a02f2acb3f375a119286af6e01b2`; refresh and independent exact-head review are required. |
| [#4313](https://github.com/ajoe734/pantheon/pull/4313) | `OPEN`, `88a2c7b6bd96e23c8323b15f2675f413b7f9a5c2`, `BEHIND` | `L12-FLEET-STATUS-SYNC-CLOSEOUT-20260728`, owner `Codex`, reviewer `Codex2`, `blocked`, waiting for `Codex2` | `PRESERVED_BLOCKED` | Current task-scoped closeout path has no accepted replacement. |

All three open PRs report no auto-merge request. The complete machine-readable
snapshot, status observations, and ancestry verification are in
[`evidence.json`](evidence.json); its checksum file is
[`evidence.sha256`](evidence.sha256).

## Reviewer decision

This manifest is pending the independent `Codex2` review. The reviewer must
validate the exact PR heads above, record an approval or concrete change
request through the governed status command, and bind this manifest before the
owner can finalize the task.
