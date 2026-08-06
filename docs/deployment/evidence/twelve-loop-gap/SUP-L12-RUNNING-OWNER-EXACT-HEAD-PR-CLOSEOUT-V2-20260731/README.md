# #4396 current-head governed-closeout gate

Task: `SUP-L12-RUNNING-OWNER-EXACT-HEAD-PR-CLOSEOUT-V2-20260731`

Owner: Claude

Reviewer: Antigravity

Review manifest: `evidence.json`

Observed: 2026-08-06T11:50:07Z (supersedes the 2026-08-04T15:27:55Z reading)

## Why this was re-observed

Human/Ops reopened this task because its blocked state predated the 2026-08-05
Codex-quota mass reassignment, which overwrote `next` without re-examining
whether the underlying block still applied. This revision is that
re-examination, performed under the new owner/reviewer pair.

## Result of the re-verification

The prior block **partially** still applies.

**Discharged.** PR #4396 merged at `9cb030dc1b6944334f3717af6c0d5f2fc5f10cd9`
on 2026-08-05T02:00:30Z, and that merge commit is an ancestor of `origin/dev`.
The task summary's "still blocked by merge/root-freeze closeout" condition for
the subject PR no longer holds, and the earlier `approval_reviewer_mismatch`
auto-integrator blocker is moot. PR #4468 is also no longer `BEHIND`; GitHub
now reports `mergeable=MERGEABLE`.

**Still applies.** PR #4468 is `OPEN`/`BLOCKED` on the Commit trailers required
check, and PR #4386 is still `OPEN`/`CONFLICTING` (head moved to
`43d59e78ea361985146008fbe65f6436f8c0595b`, conflict unchanged), so it still
cannot be counted as completed support work.

**New.** The canonical status plane is `fail_closed` fleet-wide. Every governed
`ai-status.sh` command — `show`, `recover`, and `blocker` — aborts on the
`activity_audit_integrity` invariant.

## Canonical status plane is fail-closed

The supervisor emitted `task_reassigned` event
`supervisor-reassign-6d984db0…` twice for
`LIFECYCLE-PROJ-HOTFIX-COMPOSED-HEAD-REVIEW-20260801` at 2026-08-06T10:18:56Z.
The two rows are byte-identical and sit at lines 794 and 809 of the archived
member `ai-activity-log.jsonl-d234b0ec….gz`, which rotated at 11:36.

Because the duplicate is inside an already-rotated archive, `recover` cannot
clear it — repairing it means editing an immutable audit archive, which workers
are forbidden to do. This is a fleet-wide Human/Ops escalation: until it is
repaired, no task can record any governed status transition. That includes the
blocker for this task, which is why the canonical row still reads
`in_progress` rather than `blocked`.

## V2 delivery PR

This evidence is on PR [#4468](https://github.com/ajoe734/pantheon/pull/4468)
at `4bdfe4cdb2c54760b82a90429b53301c30b730f7`. Python packaging provision and
runtime mirror guard pass; smoke acceptance is skipped after the failed
prerequisite.

The Commit trailers check rejects two subjects over the 72-character limit:
`9636815e1…` (78) and `7cc9b02fa…` (112). Both were pushed before this
worker's dispatch. The check re-scans every commit in the PR range on each new
head, so **no follow-up commit can make it green** — the only fixes are a
maintainer-approved replacement branch or an authorized history rewrite. That
authorization has not been granted, and the closeout spec forbids force-push
recovery without it, so this worker performed neither.

The canonical review gate failure (`no review-proof tag` for `4bdfe4cdb…`) is
expected and not independently actionable while the trailer check stays red.
No exact-head approval is bound to this head, so the head is not frozen.

## Required next actions, in order

1. Human/Ops repairs the duplicated activity event in the archived audit member
   so `ai-status.sh recover` can complete and the fleet can write status again.
2. A maintainer authorizes the replacement branch or history rewrite for
   `task/SUP-L12-RUNNING-OWNER-EXACT-HEAD-PR-CLOSEOUT-V2-20260731`.
3. The owner rebuilds the branch from `dev` tip with subjects ≤ 72 characters
   and the required trailers, then reopens the exact-head PR.
4. Reviewer Antigravity binds review proof to the new exact head; the
   auto-integrator merges; the owner runs governed `done`.
5. Separately, #4386 must be protected-merged and its own owner must run
   governed closeout before downstream L12 work can count it.

Exact readbacks and commands are in `validation.txt`.
