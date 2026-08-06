# #4396 governed closeout routing

## Decision

The integration gap this task was opened for is **resolved**. ReviewBus PR
#4396 did not need a draft-PR workaround and did not merge around the gate: it
was squash-merged into `dev` on 2026-08-05 as
`9cb030dc1b6944334f3717af6c0d5f2fc5f10cd9`, and its reconcile task
`SUP-L12-RUNNING-OWNER-EXACT-HEAD-RECONCILE-20260731` is archived `done` with
its own review manifest present on `dev`.

Subject PR #4386 is a separate matter and is **still not complete**. It remains
open and conflicting, and its canonical row
`SUP-L12-RUNNING-OWNER-RECONCILE-20260729` is `todo`. Nothing in this evidence
counts it, or the running-owner support claim that depends on it, as delivered.

## Why this task was reopened

This task's blocked state predated the 2026-08-05 Codex-quota mass
reassignment. That reassignment overwrote `next` with a reassignment note but
never re-examined whether the underlying block still applied, and blocked tasks
are structurally invisible to `dispatch_ready_tasks` (root cause tracked in
`SUP-BLOCKED-TASK-RECONCILIATION-20260804`). Human/Ops reopened it under owner
`Claude` / reviewer `Antigravity` to re-test the recorded block against live
state.

Every recorded blocker was re-tested. None of them still apply.

| Blocker recorded 2026-08-04 | Live state on 2026-08-06 |
| --- | --- |
| `review_evidence_file_committed` sent `gh api -f ref=<sha>` without `--method GET`, so GitHub did a POST and returned 404 | Fixed on `dev`: the call is now `gh api --method GET repos/<repo>/contents/<path>?ref=<sha>`, covered by `test_review_evidence_file_committed_uses_exact_head_get_query` |
| `dev` required-status policy lacked `Pantheon canonical review gate`, so no governed review could be recorded | Fixed on `dev`: protection now requires `Commit trailers`, `Runtime mirror guard`, `Smoke acceptance`, **and** `Pantheon canonical review gate` |
| Task PR #4550 failed `Commit trailers` on a 76-character commit subject | Fixed in this run by resetting the task branch onto the current `dev` tip and dropping the over-length commit |

This branch had carried its own independent fix for the first defect. `dev`
already contains an equivalent, slightly better fix that landed through PR
#4396's merge, so the duplicate was **dropped rather than re-landed**. This
task's remaining deliverable is the evidence record itself.

## Observed state

| Surface | Observed state | Consequence |
| --- | --- | --- |
| PR #4396 | `MERGED` 2026-08-05T02:00:30Z, squash commit `9cb030dc1b6944334f3717af6c0d5f2fc5f10cd9`, ancestor of `dev` | Governably integrated through protected merge. |
| Reconcile task row | Archived `done`, owner `Antigravity`, reviewer `Claude`, closed 2026-08-05T02:04:15Z | Owner closeout followed the merge, in that order. |
| Historical gate denial | `approval_reviewer_mismatch` against the then-current `Codex` reviewer assignment | Superseded; no merge ever occurred under the mismatched authority. |
| PR #4386 | Open, `CONFLICTING`, head `43d59e78ea361985146008fbe65f6436f8c0595b`, canonical review gate `FAILURE` | Not countable as complete. |
| `SUP-L12-RUNNING-OWNER-RECONCILE-20260729` | `todo`, owner `Claude`, reviewer `Antigravity` | Not `review_approved`; auto-integrator reports `candidate_count 0`. |

## Safety repair (historical, 2026-08-04)

The then-owner ran this narrowly scoped action only *after* the gate had
rejected the pending merge authority:

```bash
gh pr merge 4396 --repo ajoe734/pantheon --disable-auto
```

The readback showed `autoMergeRequest: null`. No branch, commit, review, or
merge state was otherwise changed. The later merge went through the normal
governed path, not through that revoked standing authority.

## Remaining work, outside this task

`SUP-L12-RUNNING-OWNER-RECONCILE-20260729` (owner `Claude`, reviewer
`Antigravity`) must resolve the PR #4386 conflict against current `dev`, obtain
a fresh exact-head review, and merge under protected-merge rules. Only then may
the running-owner support claim be counted complete.

See [`evidence.json`](evidence.json) and [`validation.txt`](validation.txt) for
the machine-readable decision and command receipts.
