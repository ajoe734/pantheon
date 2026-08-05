# Task Brief: SUP-TASK-FAILURE-STREAK-SCHEMA-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add a failure_streak counter and quarantined status to the task schema
- Status: in_progress (delivery merged; closeout blocked on the GitHub review bridge, see "Review Bridge Blocker")
- Owner: Claude
- Reviewer: Antigravity
- Next: Blocked on Human/Ops. `Pantheon canonical review gate` is not a required status context on `dev`, and the shared GitHub account makes an approving self-review a 422, so `github_review_bridge.bridge_review_decision()` can record no verdict at all. Once Ops restores that required context, Antigravity approves PR #4564 at its exact head with `REVIEW_PR` / `REVIEW_HEAD_SHA` and **without** `REVIEW_FILE`; the owner then closes out after the PR merges.

## Summary
Makes repeated dispatch failure visible on the board itself instead of only in raw activity-log JSONL, closing the exact gap that made SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803 indistinguishable from an untouched task after 5 failed attempts.

## Delivery (already merged)
- Repository: `ajoe734/pantheon`
- Delivery commit: `ae885297d9ed285153e7cb9dfd31c65623888a70`
- Merge commit: `c37be6ea4a131d49aa1a1ea240258b4ab1a2efd4`
- Delivery PR: #4533, merged into `dev` at 2026-08-05T00:06:10Z
- Merge target: `origin/dev` (both commits re-verified as ancestors on 2026-08-05)
- Changed artifacts: `.orchestrator/supervisor.py`, `scripts/ai_status.py`, plus the accompanying supervisor and ai_status regression tests

The product deliverable is unchanged by this closeout cycle and is not re-delivered here.

## Closeout Cycle (PR #4564)
This PR carries task artifacts only: the review evidence manifest and this brief.

It exists because the owner/reviewer pair was reassigned Codex/Codex2 -> Claude/Antigravity
93 minutes *after* PR #4533 merged, so no commit on this branch had ever been authored
under the current pair. That, not any gap in the delivery or in the original review, is
what held this task across dispatches 6-11.

Dispatch 12 history: PR #4564 was rebased onto a dev tip that already contained merged
PR #4533, which dropped `docs/deployment/evidence/supervisor/SUP-TASK-FAILURE-STREAK-SCHEMA-20260804/evidence.json`
from the PR diff. Antigravity reopened the task on that basis, since a manifest absent
from the diff cannot be reviewed at the exact head. That revision restored the manifest
to the PR diff and rebound it to the current pair; the branch is merged forward from
`origin/dev` rather than rebased, so the manifest stays visible in the diff.

## Command-Root Defect (dispatch 13 root cause)
Antigravity reopened the task again at 2026-08-05T04:34Z, reporting that exact-head
approval was rejected by a bug in `scripts/ai_status.py`:
`review_evidence_file_committed()` invoked
`gh api repos/<slug>/contents/<path> -f ref=<sha>`. `-f` makes `gh` issue a POST with a
form field, so the GitHub Contents API returns 404 for every ref, and `command_approve`
fails closed on any approval that carries `REVIEW_FILE`.

The report is accurate for the *running* command root, and the reason is a runtime lag,
not a repository defect:

- The fix (`gh api --method GET ...?ref=<sha>`) is commit `83b6fd0351c86c36d521086139f78918d157b87d`,
  authored under this very task and already merged into `origin/dev` through delivery
  PR #4533.
- The leased command root `/home/lupin/pantheon-ci-deploy/dev-root` is pinned at
  `4361a26ad9ff375ae61667ceb689b6fa28ff8058` (merge of PR #4549, 2026-08-04T23:02+0800),
  which is 57 commits behind `origin/dev` and predates `c37be6ea4`. `83b6fd035` is
  therefore *not* an ancestor of the running root, which still executes the buggy form.
- Reproduced directly against the live API at the current PR head
  `394762d397cd882b77179da288e27e7244169788`: the `-f ref=` form returns
  `404 Not Found`; the `--method GET ...?ref=` form returns `{"type": "file"}`.

Refreshing the leased command root is out of scope for an auto worker (the supervisor is
running from it), so this task does not attempt a runtime cutover and does not re-land a
fix that is already on `dev`. It is routed around instead:

`command_approve` only calls `review_evidence_file_committed()` when the `REVIEW_FILE`
environment variable is set (`if review_file and binding:`). The canonical task row
*already* records
`review_file = docs/deployment/evidence/supervisor/SUP-TASK-FAILURE-STREAK-SCHEMA-20260804/evidence.json`,
so approving without `REVIEW_FILE` preserves the reviewer-bound path and skips only the
broken re-verification helper. `command_done` is likewise gated on
`if done_review_file and not task.get("review_file")`, so owner closeout must also omit
`REVIEW_FILE`.

This is a routing decision, not a relaxation of the Review Evidence Manifest Rule: the
manifest is committed, is present in the PR #4564 diff at the reviewed head, and is
verified there through the Contents API by hand above.

## Review Bridge Blocker (dispatch 14 root cause)
Routing around the command-root defect got the approve past `review_evidence_file_committed()`
and into the GitHub review bridge, where it failed on a different and *fleet-wide* gap.
Antigravity's report:

> GitHub review bridge error on approve: Unprocessable Entity (HTTP 422); base branch
> 'dev' does not require 'Pantheon canonical review gate'.

Both halves are real, and together they leave `bridge_review_decision()` with no way to
record any verdict:

1. **The review path 422s.** All Pantheon agents share the `ajoe734` GitHub account, which
   is also the author of every task PR, so GitHub rejects `event: APPROVE` on
   `POST /repos/ajoe734/pantheon/pulls/4564/reviews` as a self-review. This is a known and
   documented condition -- it is the reason the bridge exists at all
   (`scripts/git/github_review_bridge.py` module docstring).
2. **The required-status path is switched off.** `_required_status_contexts()` reads live
   branch protection; `dev` currently requires only
   `["Commit trailers", "Runtime mirror guard", "Smoke acceptance"]` (verified
   2026-08-05 against `repos/ajoe734/pantheon/branches/dev/protection/required_status_checks`;
   `master` is identical, and the repository has no rulesets). Because
   `Pantheon canonical review gate` is absent, `context_required` is false and the bridge
   never posts the status.

With `review is None and status is None`, the bridge raises, and `bridge_github_review_decision()`
converts that into a `SystemExit` *before* any canonical state change -- so the task
correctly stays out of `review_approved` rather than manufacturing an internal-only
approval. Nothing here is a defect in this task's delivery.

Why the context is missing: canonical review gate **v1**
(SUP-REVIEW-PIPELINE-INTEGRITY-20260804) re-derived review policy from a runner-local
`ai-status.json`, reported failure for every task, and "had to be pulled from branch
protection the same day it shipped" (`.github/workflows/canonical-review-gate.yml` header).
**v2** (SUP-REVIEW-GATE-GIT-NATIVE-PROOF-20260804) replaced it with the git-native
review-proof tag check that is now live and correct, but the required context was never
restored on `dev`. `github_review_bridge.py` anticipates exactly this state in its own
comment: the failure "stops applying once the tag-based check is back in dev's required
contexts". `.orchestrator/config.json branch_workflow.task_pr.required_status_checks`
(mirrored in `docs/conventions/GIT_WORKFLOW.md` section 11) still lists only the original
three contexts, so the config-as-documented and the live protection agree with each other
and both predate v2.

Consequences confirmed on this PR's head `f9a881238056d84aa9372bd9912c559a8a8abce4`:

- `Pantheon canonical review gate` is posted as a **failure** by the workflow
  (`no review-proof tag (pantheon-review/approve/f9a8812...) for head f9a`), which is the
  correct v2 answer for a head that has not been approved yet. It is advisory only, since
  the context is not required, so `mergeStateStatus` is `UNSTABLE` rather than `BLOCKED`.
- No `refs/tags/pantheon-review/approve/f9a88123...` exists, and no review of any state has
  been recorded on PR #4564.
- The only recent proof tag on a `dev`-based PR head
  (`pantheon-review/approve/4328ef513...`, tagger `Pantheon Review Bridge`,
  2026-08-05T03:53:30Z) carries the tag message `"message": "Approval tag test"`. It is a
  bridge test artifact from the gate v2 task, not evidence that the approve path is
  currently working end to end.

This is not specific to SUP-TASK-FAILURE-STREAK-SCHEMA-20260804: while both conditions
hold, **every** PR-backed `approve` in the fleet fails closed the same way.

### Required Human/Ops action
Restoring the required context is a repository-admin change with fleet-wide blast radius,
so an auto worker does not make it unilaterally: 66 PRs are currently open against `dev`,
and each would need a review-proof tag at its exact head before it could merge. That is
the intended `review_before_merge` policy, but the cutover is an operator decision and
overlaps the still-open `OPS-GITHUB-CANONICAL-REVIEW-ENFORCEMENT-001` (PR #4303).

```bash
gh api --method PATCH \
  repos/ajoe734/pantheon/branches/dev/protection/required_status_checks \
  -f strict=true \
  -f 'contexts[]=Commit trailers' \
  -f 'contexts[]=Runtime mirror guard' \
  -f 'contexts[]=Smoke acceptance' \
  -f 'contexts[]=Pantheon canonical review gate'
```

`.orchestrator/config.json branch_workflow.task_pr.required_status_checks` and
`docs/conventions/GIT_WORKFLOW.md` section 11 should gain the same fourth context in the
same change, so config-as-documented and live protection do not drift again.

The cutover is not free, and the two side effects below are why it needs an operator rather
than an auto worker. Audited against the 66 open `dev` PRs on 2026-08-05:

- **43 have no `Pantheon canonical review gate` status at their head at all.** The workflow
  only triggers on `pull_request` `opened`/`synchronize`/`reopened`/`ready_for_review`, so a
  PR opened before gate v2 shipped never ran it. Making the context required leaves those PRs
  permanently *pending* until something pushes their head. This self-heals for anything that
  then gets approved, because the bridge posts its own `success` for the same context and the
  newest status per context wins.
- **5 have a non-`task/` head branch** (for example `review-evidence/...`). `resolve_task_id()`
  in `scripts/git/canonical_review_gate_ci.py` returns `None` for those and the gate posts
  `failure` by design, and they also cannot be approved through the bridge, so requiring the
  context blocks them outright until they are recut onto `task/` branches or explicitly
  exempted.

(The remaining 20 already carry the expected `failure` for an unapproved head; those clear
normally at approve time.)

Once the context is required, the bridge takes its `required_commit_status` path (accepted
by `GITHUB_REVIEW_MODES` in `scripts/ai_status.py`), posts `success` on the exact head --
which supersedes the workflow's advisory failure for that context -- and pushes the
review-proof tag, so no re-run of the gate workflow is needed to clear the check.

## Independent Review
- Review evidence manifest: `docs/deployment/evidence/supervisor/SUP-TASK-FAILURE-STREAK-SCHEMA-20260804/evidence.json`
- Reviewer of record: Antigravity (independent; not the owner)
- Decision at this head: `pending_independent_review`
- The canonical row already binds this path in `review_file`; the manifest is committed and
  present in the PR diff *before* approval is requested, per the review evidence manifest rule.
  Re-verified at head `f9a881238056d84aa9372bd9912c559a8a8abce4` via
  `gh api --method GET repos/ajoe734/pantheon/contents/<manifest>?ref=f9a8812...` -> `type: file`.
- Approval command for this head (will fail closed until the Human/Ops action above lands):

```bash
AI_NAME=Antigravity \
REVIEW_PR=4564 \
REVIEW_HEAD_SHA=<exact PR #4564 head oid> \
"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" approve \
  SUP-TASK-FAILURE-STREAK-SCHEMA-20260804 "<specific independent review evidence>"
```

Do not add `REVIEW_FILE` to that command while the command root is pinned at `4361a26ad`.

## Ownership History
- Original pair: owner Codex, reviewer Codex2. Commit `ae885297d` therefore carries `LLM-Agent: Codex` / `Reviewer: Codex2`.
- Audited reassignment: exactly one `Orchestrator` `task_reassigned` event, ts 2026-08-05T01:39:22Z, event_id `supervisor-reassign-a78027c58bcd5cd1cc956c21ff58bf413a0247271f33ce37eae5b63ff1081c05`, changing owner Codex to Claude and reviewer Codex2 to Antigravity ("Codex quota exhausted 2026-08-05").

## Verification
Focused suites re-run in the task worktree on 2026-08-05, after merging `origin/dev`, and
again at this revision:

```
PYTHONPATH=.orchestrator .venv/bin/python -m pytest .orchestrator/test_supervisor.py \
  -k 'TaskFailureStreakTaskSchemaTests or ReviewApprovedWorkflowTests or PollWorkersRecoveryTests' -q
```

Result: 81 passed, 514 deselected, 4 subtests passed.

```
.venv/bin/python -m pytest scripts/test_ai_status.py -k 'ReviewApprovedWorkflowTests' -q
```

Result: 39 passed, 132 deselected.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
