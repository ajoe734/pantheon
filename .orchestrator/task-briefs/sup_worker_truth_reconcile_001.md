# Task Brief: SUP-WORKER-TRUTH-RECONCILE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reconcile supervisor worker truth without config mutation
- Status: review_approved
- Owner: Claude
- Reviewer: Codex2
- Next: Independent review passed: PR #4215 head aa70767538baefc1a331b5e7ef51a0cad903c6b9 merged to dev as 6445eacd603f0bcfb8893508fbffe341a67dd309 with all visible trailer/runtime-mirror/smoke checks successful. Verified current-owner identity, observed commit progress, full delivery head, dispatch-time trailer, exact merge ancestry, squash PR metadata, branch movement, and all lookup/git ambiguity gates fail closed. Independent focused run: 65/65 OK; full supervisor: 399/399 OK; live real-repo probes bind PR #4213 as squash_pr_metadata and PR #4212 as merge_ancestry while wrong-head and post-merge dispatch return None. Rewrite suite matches evidence at 95/97 with only the two pre-existing missing-pytest import errors. No config or hand-edited task-board change; reviewed manifest is merged at docs/deployment/evidence/supervisor/SUP-WORKER-TRUTH-RECONCILE-001/evidence.json.

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

## Owner Closeout Record (Claude, 2026-07-26)

Independent review by Codex2 passed and the delivery is merged. Closeout is a
records-and-verification pass only: no supervisor logic, test, evidence, or
config file was changed after approval.

Merge state confirmed from this worktree against a freshly fetched `origin/dev`:

- task branch head `aa70767538baefc1a331b5e7ef51a0cad903c6b9` is an ancestor of
  `origin/dev`
- PR #4215 merge commit `6445eacd603f0bcfb8893508fbffe341a67dd309` is on `dev`
- the reviewed manifest
  `docs/deployment/evidence/supervisor/SUP-WORKER-TRUTH-RECONCILE-001/evidence.json`
  is tracked on `origin/dev` alongside `evidence.md` and
  `prefix-reproduction.txt`, and is already bound as `review_file` on the
  canonical task row, so no new `REVIEW_FILE` binding is introduced at `done`

Closeout verification re-run in this worktree:

```bash
cd .orchestrator
python3 -m unittest test_supervisor                      # 399 tests, OK
python3 -m unittest -v \
  test_supervisor.AllowedRateLimitNoticeTests \
  test_supervisor.FreshAuthProbeLaneHoldTests \
  test_supervisor.OwnerlessInProgressReconciliationTests \
  test_supervisor.MergedDeliveryEvidenceTests \
  test_supervisor.SquashMergedDeliveryEvidenceTests \
  test_supervisor.MergedPullRequestLookupTests \
  test_supervisor.WorkerDeliveryIdentityTests            # 65 tests, OK
```

Both counts match the reviewer's recorded evidence (65/65 focused, 399/399
full). The round-2 record above cites 55 focused tests because it was written
before the squash-shape classes landed mid-round; the delivered and reviewed
focused count is 65.

Generated dashboard mirrors (`dashboard-bundle.json`,
`docs-site/dashboard-bundle.json`) and empty
`.orchestrator/assistant-dev-packets/` lock files are rewritten as a side effect
of running the supervisor suite. They are not task-owned output and were
reverted rather than folded into the closeout commit.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
