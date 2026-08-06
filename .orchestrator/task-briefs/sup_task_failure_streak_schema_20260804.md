# Task Brief: SUP-TASK-FAILURE-STREAK-SCHEMA-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add a failure_streak counter and quarantined status to the task schema
- Status: in_progress (delivery merged; closeout PR #4564 awaiting exact-head reviewer approval)
- Owner: Claude
- Reviewer: Antigravity
- Next: Both prior blockers are resolved. Antigravity approves PR #4564 at its exact head with `REVIEW_PR` / `REVIEW_HEAD_SHA`; the owner then re-runs the Canonical Review Gate workflow at that same head (the leased command root cannot dispatch it yet, see "Gate Re-Run Is Still Manual"), waits for the merge, and closes out.

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
from the diff cannot be reviewed at the exact head. Since then the branch is always
merged forward from `origin/dev`, never rebased, so the manifest stays visible in the
diff. Verified at this revision: `git diff --name-only origin/dev...HEAD` lists exactly
this brief and that manifest.

## Resolved: Command-Root Defect (dispatch 13 root cause)
Antigravity reported at 2026-08-05T04:34Z that exact-head approval carrying `REVIEW_FILE`
was rejected because `review_evidence_file_committed()` in `scripts/ai_status.py` called
`gh api repos/<slug>/contents/<path> -f ref=<sha>`; `-f` makes `gh` POST a form field, so
the Contents API returned 404 for every ref and `command_approve` failed closed.

The report was accurate for the *running* command root, and the cause was runtime lag: the
fix (`gh api --method GET ...?ref=<sha>`) is commit `83b6fd0351c86c36d521086139f78918d157b87d`,
authored under this task and merged to `origin/dev` via delivery PR #4533. At the time the
leased root `/home/lupin/pantheon-ci-deploy/dev-root` was pinned at `4361a26ad`, 57 commits
behind, and did not contain it.

**Resolved.** The leased root is now `f90e0aae6cb5e86f18b20db9f30bc834f6115745`
("Merge pull request #4572 from ajoe734/task/SUP-CONFIG-REQUIRED-CHECKS-SYNC-20260805"), and
`git merge-base --is-ancestor 83b6fd035 f90e0aae6` succeeds. `REVIEW_FILE` no longer trips
this path. It still does not need to be passed: the canonical row already binds
`review_file = docs/deployment/evidence/supervisor/SUP-TASK-FAILURE-STREAK-SCHEMA-20260804/evidence.json`,
so approve and done both preserve the reviewer-bound path by omitting it.

## Resolved: Review Bridge Blocker (dispatch 14 root cause)
Antigravity's dispatch-14 report:

> GitHub review bridge error on approve: Unprocessable Entity (HTTP 422); base branch
> 'dev' does not require 'Pantheon canonical review gate'.

Both halves were real. All Pantheon agents share the `ajoe734` account, which also authors
every task PR, so `event: APPROVE` is rejected as a self-review (this is why the bridge
exists at all); and with `Pantheon canonical review gate` absent from `dev`'s required
contexts, `_required_status_contexts()` reported `context_required = false`, so the bridge
had no second path and `bridge_review_decision()` raised with `review is None and status is None`.

**Resolved.** `Pantheon canonical review gate` was restored to `dev` by
SUP-REASSIGNMENT-VERIFIER-ARCHIVE-FALLBACK-20260805 (PR #4567) and declared in config by
SUP-CONFIG-REQUIRED-CHECKS-SYNC-20260805 (PR #4572). Verified live on 2026-08-06 against
`repos/ajoe734/pantheon/branches/dev/protection/required_status_checks`:

```
strict:   false
contexts: ["Commit trailers", "Runtime mirror guard", "Smoke acceptance",
           "Pantheon canonical review gate"]
```

`strict: false` means a `BEHIND` base is not itself a merge blocker; PR #4564 is
`MERGEABLE` and `BLOCKED` solely on the gate context. The bridge can now take its
`required_commit_status` path, so approve records a verdict instead of failing closed.

## Gate Re-Run Is Still Manual (this dispatch)
One narrower runtime lag remains, and it is the reason approval alone will not unblock the
merge.

SUP-REVIEW-GATE-DISPATCH-RETRIGGER-20260805 (PR #4576) established, empirically, that
pushing the review-proof tag is necessary but *not sufficient*: GitHub pins the named
required context to the identity that has historically posted it -- this workflow's own
`GITHUB_TOKEN` run -- so the best-effort `gh api` status that
`github_review_bridge._submit_required_status()` posts from the personal-token host process
shows `success` in a status listing while `mergeable_state` stays `blocked`. That task added
`_dispatch_canonical_review_gate_workflow()` to re-dispatch the workflow right after a tag
lands, plus a `workflow_dispatch` trigger on the workflow itself.

That fix is on `origin/dev` but **not** in the leased command root: `f90e0aae6` is 14
commits behind `origin/dev`, and `grep -n workflow_dispatch` finds nothing in its
`scripts/git/github_review_bridge.py`. So the approve that Antigravity runs will push the
proof tag and post the host-side status, but will not wake the workflow.

Two consequences, both handled here rather than escalated:

1. This revision merges `origin/dev` forward, so the PR head branch now carries the
   `workflow_dispatch` trigger and the current `canonical_review_gate_ci.py`. A dispatch
   against `ref = task/SUP-TASK-FAILURE-STREAK-SCHEMA-20260804` is therefore valid, and
   works as soon as the command root is re-leased onto a dev tip containing PR #4576.
2. Until then the owner re-runs the gate himself after approval, at the same head, which
   does not touch the exact-head approval binding:

```bash
HEAD_SHA="$(gh pr view 4564 --json headRefOid --jq .headRefOid)"
RUN_ID="$(gh run list --workflow canonical-review-gate.yml --commit "$HEAD_SHA" \
  --json databaseId --jq '.[0].databaseId')"
gh run rerun "$RUN_ID"
```

Refreshing the root the supervisor runs from stays out of scope for an auto worker; this is
recorded as a Human/Ops follow-up, not a blocker, because the manual re-run clears it.

## Independent Review
- Review evidence manifest: `docs/deployment/evidence/supervisor/SUP-TASK-FAILURE-STREAK-SCHEMA-20260804/evidence.json`
- Reviewer of record: Antigravity (independent; not the owner)
- Decision at this head: `pending_independent_review`
- The canonical row already binds this path in `review_file`; the manifest is committed and
  present in the PR diff *before* approval is requested, per the review evidence manifest rule.
- Reviewable surface at this head: this brief and that manifest only. The merged product
  deliverable is not re-litigated.
- Approval command for this head:

```bash
AI_NAME=Antigravity \
REVIEW_PR=4564 \
REVIEW_HEAD_SHA="$(gh pr view 4564 --json headRefOid --jq .headRefOid)" \
"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" approve \
  SUP-TASK-FAILURE-STREAK-SCHEMA-20260804 "<specific independent review evidence>"
```

`REVIEW_FILE` may now be passed safely (the `-f ref=` defect is gone from the leased root),
but it is unnecessary and omitting it preserves the row's existing binding.

## Ownership History
- Original pair: owner Codex, reviewer Codex2. Commit `ae885297d` therefore carries `LLM-Agent: Codex` / `Reviewer: Codex2`.
- Audited reassignment: exactly one `Orchestrator` `task_reassigned` event, ts 2026-08-05T01:39:22Z, event_id `supervisor-reassign-a78027c58bcd5cd1cc956c21ff58bf413a0247271f33ce37eae5b63ff1081c05`, changing owner Codex to Claude and reviewer Codex2 to Antigravity ("Codex quota exhausted 2026-08-05").

## Verification
Focused suites re-run in the task worktree on 2026-08-06, after merging `origin/dev` forward
at this revision:

```
PYTHONPATH=.orchestrator .venv/bin/python -m pytest .orchestrator/test_supervisor.py \
  -k 'TaskFailureStreakTaskSchemaTests or ReviewApprovedWorkflowTests or PollWorkersRecoveryTests' -q
```

```
.venv/bin/python -m pytest scripts/test_ai_status.py -k 'ReviewApprovedWorkflowTests' -q
```

Results are recorded under `verification.commands` in the evidence manifest.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
