# Task Brief: SUP-TASK-FAILURE-STREAK-SCHEMA-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add a failure_streak counter and quarantined status to the task schema
- Status: in_progress (re-submitted for independent review at PR #4564 exact head)
- Owner: Claude
- Reviewer: Antigravity
- Next: Antigravity reviews PR #4564 at its exact head and approves with `REVIEW_PR=4564` and `REVIEW_HEAD_SHA=<exact head>`, and **must omit `REVIEW_FILE`** (see "Command-Root Defect" below; the canonical row already binds `review_file`). Owner then runs closeout once the PR merges.

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

## Independent Review
- Review evidence manifest: `docs/deployment/evidence/supervisor/SUP-TASK-FAILURE-STREAK-SCHEMA-20260804/evidence.json`
- Reviewer of record: Antigravity (independent; not the owner)
- Decision at this head: `pending_independent_review`
- The canonical row already binds this path in `review_file`; the manifest is committed and
  present in the PR diff *before* approval is requested, per the review evidence manifest rule.
- Approval command for this head:

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
Focused suites re-run in the task worktree on 2026-08-05, after merging `origin/dev`:

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
