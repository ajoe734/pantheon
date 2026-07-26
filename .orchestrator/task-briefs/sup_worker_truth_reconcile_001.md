# Task Brief: SUP-WORKER-TRUTH-RECONCILE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reconcile supervisor worker truth without config mutation
- Status: in_progress
- Owner: Claude
- Reviewer: Codex2
- Next: Independent rejection after PR #4212 merge 8703d1f5d: valid allowed-warning/auth-probe repairs landed, but ownerless reconciliation is unsafe and task acceptance remains open. Current merged code treats any old Task-ID trailer on dev plus any latest completed owner-dispatch worker as evidence for the current in_progress delivery; it neither binds the merge commit/PR head to that exact terminal worker nor verifies worker target identity equals the current canonical owner. A reopened or reassigned task can be falsely moved to review. Continue the same canonical task with a follow-up PR from current dev: exact worker-delivery/PR-head/merge ancestry and timestamp binding, current-owner identity binding, fail-closed absent linkage, and negative tests for reopened same ID, reassigned owner, stale terminal worker plus new work, deleted branch/unpushed work, and older-only merged Task-ID commits. Codex2 must formally review before approval. Do not load this merged runtime into live supervisor until the follow-up is merged; no config edit.

## Summary
修正 supervisor 對 terminal worker outcome、allowed_warning、provider fresh probe 與 ownerless in_progress 的 authoritative reconciliation；不得直接改 config 或手改 task board。

## Owner Implementation Record — Round 2 (Claude, 2026-07-26)

Follow-up to the Codex2 rejection of PR #4212 (merged `8703d1f5d`). Cut from
current `dev` `f687d7aeb`. The accepted round-1 repairs (`allowed_warning`
rate-limit classifier, live auth-probe lane hold, queue/lease settlement) are
untouched; only the ownerless `in_progress` evidence rule is rebuilt. No
`.orchestrator/config.json` edit and no hand-edited task board.

Ownerless reconciliation now has to name one specific delivery by one specific
current owner, and prove every link:

1. **Current-owner identity binding** — the latest owner-dispatch worker must
   itself resolve, through the agent registry, to the task row's current owner.
   The candidate is deliberately not pre-filtered by owner, so a reassignment
   fails closed instead of falling back to an older matching worker.
2. **Delivery binding** — `work_progress_snapshot.commit_sha`, the commit the
   worker's own worktree was last observed at, must be a full sha and must be an
   ancestor of the integration base.
3. **Timestamp binding** — a `Task-ID:` trailer commit must be reachable from
   that head *and* dated at or after the worker's dispatch, so a previous
   round's merged commits are not counted for a reopened task.
4. **Run binding** — the worker must have recorded commit progress during this
   run; a clean rerun over an already merged branch delivered nothing.
5. **Merge ancestry** — the merge commit carrying the head into the base is
   recorded for audit (a fast-forward merge legitimately has none, so it is not
   itself a gate).
6. **Branch binding** — a surviving branch that is ahead of the base, or that
   moved past the delivery head, blocks the transition; a git failure now reads
   as unmerged rather than as clean.

`pr_url` is explicitly **not** a binding: it is scraped from provider output and
the live state at 2026-07-26T20:21Z carried a malformed value naming an
unrelated PR. It is recorded with `pr_url_is_authoritative: false` and pinned by
a regression test.

### Mid-round: squash-merged deliveries (Codex2 audit of anchor `051eef7c0`)

Codex2 accepted the binding above but found that exact git ancestry can never
see a **squash** merge — correct, but permanently inert for that shape, which
brings the redispatch loop straight back. The live case is PR #4213, head
`9e484e252` squash-merged to `0410a89f0` on `dev`.

`merged_delivery_commits` now recognises two shapes. `merge_ancestry` is tried
first (local git only). `squash_pr_metadata` runs only after ancestry fails and
binds authoritative GitHub PR metadata: exactly one merged PR whose
`headRefOid` equals this worker's delivery head, `state` `MERGED`, `baseRefName`
the expected integration branch, `mergedAt` at or after the dispatch, and a
`mergeCommit` that is an ancestor of the base and itself carries this task's
`Task-ID:` trailer dated at or after the dispatch. The task branch name is only
the lookup key; a task id alone never implies a squash; provider prose and
`pr_url` are never consulted. A disabled lookup, missing `gh`, unknown
repository, non-zero exit, timeout, unparseable payload, or two records claiming
the same head all fail closed. The base comparison in the branch check is
skipped for the squash shape only, because a squash rewrites the commits and the
original branch is legitimately never an ancestor of the base.

Evidence: `docs/deployment/evidence/supervisor/SUP-WORKER-TRUTH-RECONCILE-001/`
(`evidence.json`, `evidence.md`, `prefix-reproduction.txt`).

Verification: 44 of the 55 focused tests fail against the merged round-1
supervisor (`origin/dev:.orchestrator/supervisor.py`), including six that
reproduce the exact false positives Codex2 described; all 55 pass after.
`python3 -m unittest test_supervisor` reports 399 tests OK. A live, unmocked
re-run against the real repository and real GitHub metadata resolves PR #4213 as
`squash_pr_metadata` and PR #4212 as `merge_ancestry`, with a wrong delivery
head and a post-merge dispatch both returning `None`.

Delivery: PR #4215.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
