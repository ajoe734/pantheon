# SUP-L12-POST-4380-GAP-REVIEW-20260729

Review of PR [#4382](https://github.com/ajoe734/pantheon/pull/4382) — the
post-#4380 twelve-loop gap audit and fleet dispatch packet.

Reviewed head: `ca00f813f4e6a5dfcfb2cf402ebba425a034d03e`

Read at: `2026-08-06`, `origin/dev = 003688bd7402d051986c07f1769285925af24e1b`

Owner: Claude · Reviewer: Antigravity

## Verdict

**Cannot approve — and cannot reopen either.** This is not a content
rejection. The packet is sound; the PR is structurally unmergeable.

Review requirements 1–4 from the task brief all pass at the live head.
Requirement 5 — "approve the exact PR head through the canonical review
gate, or reopen" — is unexecutable, because neither verb has a target.

## The blocking finding

PR #4382's head branch is `task/L12-POST-4380-GAP-DISPATCH-20260729`, but
**no canonical task row named `L12-POST-4380-GAP-DISPATCH-20260729` has ever
existed** — not in `ai-status.json`, not in `ai-task-archive/tasks/`, and not
anywhere in the activity audit.

The governed gate agrees, run under the leased command root:

```
$ python scripts/git/task_review_merge_gate.py --status-root /home/lupin/pantheon \
    --json check L12-POST-4380-GAP-DISPATCH-20260729 --pr-json /tmp/pr4382.json

allow_merge        = false
allow_auto_merge   = false
revoke_auto_merge  = true
reason             = task_state_unavailable
detail             = canonical task state for L12-POST-4380-GAP-DISPATCH-20260729
                     is missing; refusing to merge
```

GitHub concurs: `mergeStateStatus=BLOCKED`, `autoMergeRequest=null`,
`reviewDecision=""`.

`task_review_merge_gate.py` derives merge authority from the canonical row of
the task id on the head branch and fails closed on a missing row by design.
`ai-status.sh approve` and `reopen` both bind to a task row, and there is no
row here to bind to. Approving *this* review task
(`SUP-L12-POST-4380-GAP-REVIEW-20260729`) would not feed the gate for a
different task id — it would be a no-op that falsely reads as progress.

**No reviewer verdict of any kind can open this gate.** Remediation needs
canonical state authority: Human/Ops or the supervisor.

## Requirement-by-requirement

| # | Requirement | Result |
| --- | --- | --- |
| 1 | Reflects post-#4379/#4380/#4373 facts, base `6f87a207` | PASS, drift disclosed |
| 2 | Does not claim all twelve loops operational | PASS |
| 3 | Preserves the three operator rules | PASS; lane preferences now stale |
| 4 | `tasks.json` valid JSON, digests check out | PASS (4/4 digests) |
| 5 | Approve exact head, or reopen | **UNEXECUTABLE** |

Digests were recomputed directly from the PR-head git blobs, so the result
does not depend on any working-tree checkout. Full transcript in
[`validation.txt`](validation.txt).

Note on requirement 1: the head under review is `ca00f813`, not the
`7766d704` pinned in the task brief. The head moved on 2026-07-30 (Codex,
"refresh graph") after the brief was written. The review targets the live head.

## Currency findings

Seven days of `dev` movement have consumed most of the packet's actionable
surface:

- **Base drift** — packet pins `6f87a207`; `origin/dev` is now `003688bd`.
- **Wave 0 gate already satisfied** — `SUP-ASSISTANT-DEV-BRIDGE-MATERIALIZATION-20260730`
  is archived, so the precondition every other wave depends on now gates nothing.
- **Waves A and C largely archived** — only `L12-VERIFY-OBS-001` (review) and
  `SUP-L12-STALE-PR-RETIRE-20260729` (in_progress) are still live.
- **Wave D central repair never materialized** — `L12-VERIFY-LEARN-REAL-VERIFIER-001`,
  the new task this head introduced and the sole dependency it moved
  `L12-VERIFY-LEARN-001` onto, has no canonical row anywhere; and
  `L12-VERIFY-LEARN-001` is itself already archived.
- **Lane preferences retired** — the graph still prefers Claude2, Codex and
  Codex2 throughout; the 2026-08-05 quota reassignment retired those lanes.

The Wave D finding is worth naming precisely: it is the same defect class the
packet's own Wave 0 exists to prevent — a dispatch naming tasks that were never
materialized into canonical state. The packet reproduces that failure inside its
own graph, and the orphan head branch of PR #4382 is a third instance of it.

## On the previously recorded block

Human/Ops reopened this task at `2026-08-06T10:17:40Z` asking whether the
recorded block still applied. It did not, as recorded:

- **Recorded**: `waiting_for=Antigravity`, `next="Codex quota exhausted
  2026-08-05: reassigned Codex->Claude / Codex2->Antigravity"`.
- **Assessment**: inaccurate. That string was a side effect of the 2026-08-05
  mass reassignment overwriting `next`; it never described a real condition.
  This task was never waiting on Antigravity's availability.
- **Actual condition**: the orphan-PR finding above. Still blocking, but the
  blocking party is Human/Ops or the supervisor — not any reviewer lane.

## Recommended remediation

Preference order:

1. **Retire PR #4382 as superseded.** Most of its dispatch content is archived;
   its actionable surface has shrunk to Wave D. This is the pattern
   `SUP-L12-STALE-PR-RETIRE-20260729` already covers.
2. **Re-land the still-useful content** — chiefly the Wave D graph and the
   `L12-VERIFY-LEARN-REAL-VERIFIER-001` charter — under a task id that has a
   canonical row, refreshed against current `dev`.
3. **Or register a canonical row** for `L12-POST-4380-GAP-DISPATCH-20260729`
   bound to PR #4382 and run a normal exact-head review cycle — but only after
   the packet is refreshed, since approving it as-is would land a dispatch graph
   whose gate and most of whose waves are already archived.

Whichever path is taken, `L12-VERIFY-LEARN-REAL-VERIFIER-001` should be carried
forward as a real canonical row. It is currently a charter that exists only
inside an unmergeable PR.

## Files

- [`evidence.json`](evidence.json) — machine-readable review manifest
- [`validation.txt`](validation.txt) — full command transcript
- [`evidence.sha256`](evidence.sha256) — digests for the two files above

## Handoff refresh (2026-08-06, before reviewer dispatch)

Re-observed immediately before handoff, because an audit that cites other PRs
and other tasks can go stale between the audit commit and the review.

**Verdict unchanged.** The two claims the verdict rests on were re-checked
against live state and both still hold:

- PR #4382 head is still `ca00f813f4e6a5dfcfb2cf402ebba425a034d03e` — the
  reviewed head did not move, so requirement findings 1-4 stand as recorded.
- `task_review_merge_gate.py check L12-POST-4380-GAP-DISPATCH-20260729` still
  returns `allow_merge=false`, `reason=task_state_unavailable`, and GitHub still
  reports `mergeStateStatus=BLOCKED`. Requirement 5 remains unexecutable.

**Corrections to the currency findings:**

- `origin/dev` has advanced from `003688bd7402d051986c07f1769285925af24e1b`
  to `34e1f494a251f6c2292a6675baa0ed65fdab7bb5`. The packet's pinned base
  `6f87a207` is now further behind, which strengthens rather than weakens the
  "retire or re-land" recommendation.
- `SUP-L12-MERGED-ROW-RECONCILE-20260729` archived at `2026-08-06T01:21:15Z`;
  it was already recorded as archived and remains so.
- `L12-FE-TRUTH-001` moved from `blocked` to `in_progress`.
- `SUP-L12-STALE-PR-RETIRE-20260729` moved from `in_progress` to `review`.
  This is the task chartered to retire stale PRs, so the recommended remediation
  path for #4382 is closer to hand than when this manifest was first written.
- No packet task id gained or lost a canonical row. In particular
  `L12-VERIFY-LEARN-REAL-VERIFIER-001` is still `MISSING`, and so is
  `L12-POST-4380-GAP-DISPATCH-20260729`.

**Probe limitation, disclosed:** the governed
`ai-status.sh show` command was under sustained `status_task_lock_busy`
contention during this refresh. `SUP-L12-STALE-PR-RETIRE-20260729` and
`L12-VERIFY-OBS-001` were confirmed through the governed command; the remaining
rows in the table below were read from the canonical mirrors
(`ai-status.json`, `ai-task-archive/tasks/*.json`) after eight governed attempts
returned lock-busy. The mirror lagged the governed command by one transition on
`SUP-L12-STALE-PR-RETIRE-20260729`, so treat mirror-sourced rows as
"no later than" the state shown.

## Closeout refresh (2026-08-06, owner finalize dispatch)

The owner was dispatched to finalize this task at `review_approved`. It could
not close on the approved head, so the base was refreshed and every claim was
re-observed. **The verdict is unchanged**; this section records why the head
moved and what was re-checked.

**Why the head moved.** `scripts/git/auto_integrator.py` returned
`waiting` / `rebase_required` for delivery PR #4588:

> PR #4588 needs a refreshed head to land on dev; the approval of
> `93e73039bbb84603917bca824aa37d0ffc24c4b6` would not cover it. Owner
> refreshes the branch and the assigned reviewer re-approves the new head.

For a `review_before_merge` PR the integrator never force-pushes an approved
head. It instead requires `origin/dev` to already be an ancestor of the approved
exact head, so it can smoke that immutable commit. `origin/dev` advanced to
`4ee7fc95fe5c8aafa9c3d8c60f4882b6a2fbaf4c`, past the approved head, so that
ancestry test failed. `origin/dev` was therefore merged forward into the task
branch as `fcca37ebd12a47aaf23fca447a80b162189c95f8`. A rebase was deliberately
not used: rebasing a task branch whose entire delivery *is* an evidence manifest
rewrites the commits that carry that manifest into the PR diff.

A second exact-head review is required as a result. This is a base refresh, not
new review content — the PR diff is still the same 4 evidence files (+586/-0
as captured at `fcca37ebd`, +818/-0 once this revision counts; check clean).

**A stale review-gate CheckRun was also cleared.** PR #4588 was `MERGEABLE` but
`BLOCKED` while its canonical review gate commit status was already `success`.
The cause was a pre-approval Canonical Review Gate run (`31097849961`,
2026-08-06T11:35:22Z) that left a `FAILURE` CheckRun on the same head.
`gh run rerun 31097849961` on the unchanged head concluded `success` and the PR
moved `BLOCKED` -> `CLEAN` without touching the exact-head binding.

**What was re-verified at `origin/dev = 4ee7fc95`:**

- PR #4382 is still `OPEN`, still `BLOCKED`, still at head
  `ca00f813f4e6a5dfcfb2cf402ebba425a034d03e`. The reviewed head did not move.
- Governed `ai-status.sh show L12-POST-4380-GAP-DISPATCH-20260729` still returns
  `Unknown task`, and no archive row exists for it.
- `task_review_merge_gate.py check` still returns `block` /
  `task_state_unavailable`.

So the blocking finding and requirement findings 1-5 all stand.

## Out-of-scope environment finding: dev lost the canonical review gate workflow

This was found while merging `dev` forward — the merge deleted the files from
this worktree because `dev` no longer has them. It is **not** part of this
task's reviewed diff and was **not** remediated here.

`origin/dev` no longer contains `.github/workflows/canonical-review-gate.yml`
or `scripts/git/canonical_review_gate_ci.py`. `origin/master` still contains
both, and that workflow is the *only* difference between the two branches'
workflow sets.

The cause is PR #4590
(`SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729: anchor owner handoff`),
squash-merged as `23ae23c2185d31d2aeacafaa9b051127a6d53136` at
2026-08-06T11:57:30Z with 227 files changed, `+1750/-47932`. A commit titled
"anchor owner handoff" that deletes 166 files is the signature of a stale-base
squash, not an intended removal.

Impact: the `Pantheon canonical review gate` context is required by `dev` branch
protection. Task branches cut from `dev` after `23ae23c2` carry neither the
workflow nor its CI helper, so nothing produces that CheckRun for them.
Approvals still post the context as a commit status through
`github_review_bridge.py`, so gated review PRs can still be satisfied, but the
CI-side gate is gone from the default branch.

Restoring a deleted required-check workflow to `dev` is outside this task's
scope and outside its reviewed diff. It is escalated to Human/Ops in the handoff
message rather than resolved locally.
