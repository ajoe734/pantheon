# Task Audit Evidence: SUP-L12-STALE-PR-RETIRE-20260729

- Task owner: `Claude`
- Independent reviewer: `Antigravity`
- Audit timestamp: `2026-08-06T11:25:10Z` (recheck of the `2026-08-06T10:37:41Z` recheck of the `2026-08-04T15:06:41Z` audit)
- Source audit: [1025Z three-pass gap audit](../../../../04/pantheon_twelve_loop_gap_2026-07-26/archive/THREE_PASS_GAP_AUDIT_2026-07-29T1025Z.md)

## Decision

`#4367` is the only scoped stale duplicate safe to retire. It is already
closed without merge and duplicates the delivery and closeout evidence merged
by `#4365` and `#4366`; this task made no GitHub mutation at all.

The remaining scoped PRs were preserved. `#4364` is active OBS product proof,
and `#4313` / `#4297` were preserved for owner refresh rather than closed.
Those two are the sharpest check on this decision: **both were refreshed by
their owners and merged into `dev`** — `#4297` on 2026-08-05 and `#4313` at
2026-08-06T11:01:23Z, the latter between the previous recheck and this one.
Closing either as a stale duplicate would have destroyed work that shipped.
`#4364` is now the only scoped PR still open, and it is the one PR this
task's acceptance explicitly forbids closing.

| PR | GitHub state/head | Canonical task | Action | Exact reason |
| --- | --- | --- | --- | --- |
| [#4367](https://github.com/ajoe734/pantheon/pull/4367) | `CLOSED`, `574e420ec4e78b58a6fdc530fffe2d9ab4220295`, unmerged | `SUP-L12-REVIEW-PRIORITY-GATE-20260729`, archived | `RETIRED_ALREADY_CLOSED` | Duplicate receipt; #4365 delivery (`18e102a1950ab3aa9a2e9f97ad50313d1fa93d5d`) and #4366 closeout evidence (`8ea01a8e3993b3dabc6cd475c7058d299eaf4a01`) are both on `origin/dev`. |
| [#4364](https://github.com/ajoe734/pantheon/pull/4364) | `OPEN`, `f3756cec99a8c44d47c075a475c25cf86a4d3171`, `BLOCKED` | `L12-VERIFY-OBS-001`, owner `Antigravity`, reviewer `Claude2`, `review` | `PRESERVED_OPEN` | Active product proof, explicitly protected by this task's acceptance. The row's stale next-head claim does not authorize closing; its owner/reviewer need an exact current-head refresh and review. |
| [#4297](https://github.com/ajoe734/pantheon/pull/4297) | `MERGED` 2026-08-05, head `70360fb43755cf4b21c918f4a7996433acb22172`, merge commit `5c3f2dd9f9c2bdf4065e3751edfe39518bd5fa61` | `L12-FLEET-STATUS-SYNC-001`, owner `Claude`, reviewer `Antigravity`, archived `done` | `PRESERVED_THEN_RESOLVED_BY_OWNER_REFRESH` | Preserved on 2026-08-04 as `PRESERVED_REFRESH_REQUIRED` when its live head `23a7d3244ad89d093a006ff6ace86f13053d794c` diverged from the recorded review binding. The owner refreshed the head and it merged; the merge commit is an ancestor of `origin/dev`. |
| [#4313](https://github.com/ajoe734/pantheon/pull/4313) | `MERGED` 2026-08-06T11:01:23Z, approved head `14dfa2c9ae14be4c2e4e22b421cc563871438f7a`, merge commit `b9db8082360a1d723780218c27b5779e1e80be1f` | `L12-FLEET-STATUS-SYNC-CLOSEOUT-20260728`, owner `Claude`, reviewer `Antigravity`, archived `done` | `PRESERVED_THEN_RESOLVED_BY_OWNER_REFRESH` | Preserved as the task-scoped closeout path with no accepted replacement, on the rule that only its own owner and reviewer may resolve it. They did: the head was refreshed from `88a2c7b6bd96e23c8323b15f2675f413b7f9a5c2`, `Antigravity` approved that exact head at 10:57:27Z, and it merged. Both the merge commit and the approved head are ancestors of `origin/dev`. |

The complete machine-readable snapshot, scope boundary, ancestry verification,
and per-PR recheck are in [`evidence.json`](evidence.json); its checksum file
is [`evidence.sha256`](evidence.sha256).

## Scope boundary

Only the PR set named by the 1025Z gap audit is adjudicated here. Fourteen
other L12-prefixed PRs were open on 2026-08-06 (`#4561`, `#4550`, `#4528`,
`#4468`, `#4465`, `#4455`, `#4452`, `#4450`, `#4425`, `#4395`, `#4389`,
`#4386`, `#4382`, `#4362`). They belong to distinct nonterminal canonical rows
with their own owners and reviewers and are listed in `evidence.json` but
deliberately not adjudicated; retiring them from here would preempt those rows.

## Reassignment and prior-review resolution

The 2026-08-05 Codex-quota mass reassignment moved this task from
`Codex`/`Codex2` to `Claude`/`Antigravity` and overwrote `next` without
re-examining the underlying block. Both findings from the prior `Codex2`
review are resolved and re-verified at head
`343bc50bedd6ca9012613ba5d4167dfad0ce4b83`:

1. The trailing blank line at EOF in this README is gone —
   `git diff --check origin/dev...HEAD` is clean and
   `sha256sum -c evidence.sha256` reports `OK`.
2. The committed task brief no longer claims `review_approved` or an
   independent review at the old head; it records `in_progress` and no
   review decision.

No surviving blocking condition was found. PR `#4372` is `BLOCKED` only
because the canonical review gate has no review-proof tag for its head, which
is the ordinary pre-approval state; `Commit trailers`, `Python packaging
provision`, `Runtime mirror guard`, and `Smoke acceptance` all pass. The task
therefore continued rather than being re-blocked.

## Recheck at 2026-08-06T11:25Z

The 10:37:41Z audit commit `122fb18105745251a298c6e15325eaaaf420e357` was
written before `#4313` merged, so every scoped PR was re-observed against live
GitHub and against `origin/dev` at `511757c0f08e925c6caec5ff696a5adbe4226209`.
Only `#4313` changed: `OPEN` -> `MERGED`. `#4367`, `#4364`, and `#4297` are
unchanged. No decision was reversed by this recheck; the `#4313` merge confirms
the preservation call rather than contradicting it.

## Reviewer decision

This manifest is pending the independent `Antigravity` review. The reviewer
must validate the exact PR heads and rechecks above at the exact `#4372` head,
then record an approval or a concrete change request through the governed
status command with `REVIEW_FILE` bound to this manifest.

Note for the reviewer: `#4313`'s merge is recent, and `#4364` is the only
scoped PR still open. If `#4364` also changes state before the review lands,
that is an observation to record, not a reason to close it — this task may not
close `#4364` under any outcome.

## Branch provenance disclosure

Before the original dispatch, the branch already contained commit
`ac6ee7f2f1b4290fa067024fd84efa5a79832647`, whose message incorrectly names
`P0-TW-PAPER-ACTIVATE-001`. Its actual patch changes only this task brief, and
the complete PR diff against `origin/dev` is limited to the four task-scoped
files listed in `evidence.json`. This task did not rewrite the pushed branch
history or force-push. Commits `0484563ba..343bc50be` carry
`LLM-Agent: Codex` / `Reviewer: Codex2` because they predate the reassignment
and are already pushed; commits from this cycle carry `LLM-Agent: Claude` /
`Reviewer: Antigravity`. The independent reviewer should assess this
disclosure together with the exact net PR diff.
