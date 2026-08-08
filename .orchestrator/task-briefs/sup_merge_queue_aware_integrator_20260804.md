# SUP-MERGE-QUEUE-AWARE-INTEGRATOR-20260804

Status: proposed
Owner: Claude
Reviewer: Human/Ops
Depends on: none (prerequisite for enabling a `dev` merge-queue ruleset)

## Problem

`scripts/git/auto_integrator.py` is the script that actually lands a
`review_approved` task's PR into `dev`: once the review gate allows it, it
calls `gh pr merge` and, in the same breath, immediately calls
`reconcile_done()` (which runs `ai-status.sh done`, marking the Pantheon task
`done`) and returns action `"merged"`. This assumes `gh pr merge` completes
synchronously.

Per `gh pr merge --help`: "When targeting a branch that requires a merge
queue... If required checks have passed, the pull request will be added to
the merge queue." Enabling a merge-queue-required ruleset on `dev` (part D of
the original SUP-REVIEW-PIPELINE-INTEGRITY-20260804 four-part design) would
make this call *enqueue* rather than merge -- the actual merge lands
asynchronously, whenever the queue processes it, not within this process's
lifetime. Deploying that ruleset without fixing this first would make
`reconcile_done` fire (marking tasks `done`) for merges that have not
actually landed.

## Fix

After issuing the merge command, re-fetch the PR and only proceed with
`reconcile_done` if `state == "MERGED"`. If not yet landed, return a new
`"queued_for_merge"` result instead -- not a failure, not a block. This
needs no new resumption machinery: `integrate_candidate` already has a
fallback path (`fetch_pr_for_task(..., state="merged")`, used when the PR
is no longer `open`) that independently re-validates the merge commit and
the gate decision before reconciling an already-merged PR. The next
auto-integrator pass finds a since-landed queued PR through that existing,
already-tested path.

`main()`'s exit-code mapping gets `"queued_for_merge"` added alongside
`"waiting"`/`"auto_merge_enabled"` (exit 1: more work soon, not blocked).

## Test fixture note

`test_auto_integrator.py`'s `FakeRunner` previously treated `self.pr` as
static except for the `--disable-auto` case. It now models the real
open-to-merged transition: an actual (non-`--auto`, non-`--disable-auto`)
`gh pr merge` call flips `state` to `MERGED` (`merge_lands_synchronously`
constructor flag, default `True`), so the pre-merge gate check still sees
`OPEN` and the post-merge re-check sees `MERGED` -- covering both the
existing (no queue) synchronous case and the new queued case
(`merge_lands_synchronously=False`) with one mechanism rather than
per-test fixture hacks. Four pre-existing tests in
`test_task_review_merge_gate.py` (which imports this `FakeRunner`) needed no
fixture changes once this was modeled correctly at the runner level.

## Explicitly not done here

The `dev` merge-queue ruleset itself is not enabled by this PR. Deploy this
first, confirm `dev-root` picks it up, *then* enable the ruleset as a
separate step -- see SUP-REVIEW-PIPELINE-INTEGRITY-20260804 part D.

## Test plan

- `pytest scripts/git/test_auto_integrator.py` -- 11 passed (2 new: lands
  synchronously -> merged; does not land -> queued_for_merge, no
  reconcile_done call)
- `pytest scripts/git/test_task_review_merge_gate.py
  scripts/git/test_git_workflow_helpers.py scripts/git/test_task_pr_triage.py
  scripts/git/test_github_review_bridge.py
  scripts/git/test_canonical_review_gate_workflow.py` -- 219 total, zero
  regressions once `FakeRunner` modeled the transition (not fixture patches)
- `pytest scripts/test_ai_status.py` -- 169 passed/31 subtests, unaffected
- `pytest .orchestrator/test_github_bus.py` -- 28 passed, unaffected
