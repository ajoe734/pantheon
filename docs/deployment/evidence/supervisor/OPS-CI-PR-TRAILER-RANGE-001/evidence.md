# OPS-CI-PR-TRAILER-RANGE-001 — Branch CI commit-range contract repair

Task: Scope PR commit-trailer CI to the exact task head
Owner: Claude · Reviewer: Codex2 · Phase: Fleet delivery governance

Scope rule honoured throughout: **the validator is not touched.**
`scripts/git/check_commit_trailers.py`, `.orchestrator/config.json`, the
required trailers, the 72-char subject rule and the merge/promote exemptions
are all byte-identical after this task. Only the *range* the gate is pointed at
changes.

## 1. What actually failed

Dev commit `0410a89f0` is a squash-merge landed by PR #4213:

```
OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001: record isolation evidence (#4213)
```

79 characters. It carries valid trailers, and on `dev` it was never judged as an
ordinary commit. Two *different* range bugs then dragged it into other tasks'
scans, where it is judged as an ordinary commit and fails the 72-char rule.

### 1.1 PR #4211 — the synthetic merge commit (run 30219467575)

The workflow passed `HEAD_SHA: ${{ github.sha }}`. On a `pull_request` event
`github.sha` is **not** the PR head — it is GitHub's synthetic
`refs/pull/N/merge` commit: the PR head already merged into the *current* base
tip. `BASE_SHA` was `pull_request.base.sha`, which is frozen when the PR is
opened. The gap between them is every commit `dev` gained in the meantime.

Scanned range `03389c0..0942107` covered 11 commits. Seven belong to PR #4211.
The rest were `0410a89f0`, `07747254e`, and two commits owned by
`task/SUP-WORKER-TRUTH-RECONCILE-001`. Ancestry, replayed against real objects:

```
0410a89f0 from PR#4211 head                           : not reachable
0410a89f0 from PR#4211 synthetic refs/pull/4211/merge : REACHABLE
```

The PR's own head has nothing to do with the commit that failed it.

### 1.2 PR #4215 — the previous branch tip (run 30219364096)

`#4215` failed on the **push** event, not the PR event, with range
`0ffc9404c..3984f143a`. Here `github.event.before` was a genuine ancestor, and
`0410a89f0` genuinely *is* reachable from the pushed head — because the worker
synced `dev` into `task/SUP-WORKER-TRUTH-RECONCILE-001` between the two pushes.
Measuring from the previous branch tip means every commit a branch merges in
becomes, for one push, a commit that branch is held responsible for.

Same blast radius, different mechanism. Fixing only the `pull_request` head
would have left half the fleet failure in place.

## 2. The repaired contract

`scripts/git/resolve_commit_trailer_range.py` now answers one question per
event shape: *which commits does this ref own that its integration target does
not already have?*

| shape | range |
| --- | --- |
| `pull_request` | `<base branch>..<pull_request.head.sha>` |
| push on `task/**`, `hotfix/**` | `<integration target>..<github.sha>` |
| push on `dev`, `master`, `publish/**`, `promote/**` | unchanged: `before` → merge base → `head^` → `head` |

Three details matter.

**`github.sha` is never the PR head.** The resolver takes an explicit
`--pr-head-sha` and refuses to run without it, rather than accepting a head
argument it cannot tell apart from a merge commit.

**The live base tip beats `base.sha`.** Candidates are tried
`origin/<base ref>`, `<base ref>`, then the event's `base.sha`. Two-dot
exclusion against a *stale* base still admits base commits that arrived through
a later sync merge —
`PullRequestTrailerRangeLiveRegressionTests::test_stale_base_sha_alone_would_still_admit_the_merged_dev_commit`
pins that, which is why the frozen SHA is the last resort and not the first.

**Everything fails closed.** A missing `--pr-head-sha`, an unavailable head
object, or no resolvable base is `exit 1` with nothing on stdout — never a
silent fallback to `head^..head`, which on a synthetic merge is a base-branch
commit and on a real head is the fork point. The workflow also aborts when the
resolved range is empty, so a resolver failure cannot be read as "nothing to
check".

Anchoring on the target reference rather than a recorded SHA is what makes
base movement, rebase, force-push and brand-new branches stop being special
cases: none of them change `origin/dev..<head>`.

The `origin/<base ref>` dependency is safe. Both failed runs show the
`Commit trailers` job fetching `+refs/heads/*:refs/remotes/origin/*` at
`fetch-depth: 0`, on both event types.

## 3. Coverage is not weakened

Work-branch push coverage **widens**: every commit the branch owns is checked
on every push, instead of only the slice since the last push. That is the same
set the PR gate already enforced, so it introduces no new class of blocking
failure. `dev`, `master`, `publish/**` and `promote/**` stay on the previous
decision path, including the `head^` and bare-`head` fallbacks and the
promote-PR / master promote-merge skips in the workflow.

A task head that is genuinely malformed still fails:
`test_repaired_range_still_fails_a_malformed_task_head` puts a commit with no
`Reviewer` trailer on a task branch and asserts `exit 1` under the repaired
range.

## 4. Verification

```
python3 -m unittest scripts.git.test_git_workflow_helpers      # 52 passed (was 40)
```

Twelve new tests: seven unit tests for the `pull_request` and work-branch push
contracts and their fail-closed paths, plus a real-git regression class that
builds a repository shaped like the incident (a base commit, task branches cut
before `dev` advanced, an overlong squash-merge commit on `dev`, a branch that
merged `dev` back in, and synthetic `refs/pull/N/merge` commits) and runs the
**real** `check_commit_trailers.py` over old and new ranges.

The live replay is in `live-range-reproduction.txt`: an isolated clone with
`refs/remotes/origin/dev` reset to `0410a89f0`, the tip both runs saw.

```
PR #4211  old  03389c0..0942107      exit 1  ← 0410a89f0, 11 commits, 2 owners
PR #4211  new  origin/dev..4e24e895f exit 0  ← 7 commits, all this task's
PR #4215  old  0ffc9404c..3984f143a  exit 1  ← 0410a89f0
PR #4215  new  origin/dev..3984f143a exit 0
```

## 5. Residual risk

1. **Runtime mirror guard is already a no-op.** That job checks out at the
   default depth 1, so its `git diff BASE_SHA HEAD_SHA` cannot resolve either
   object and the failure is swallowed by `|| true`, leaving `CHANGED` empty.
   That is why it never showed the same contamination. Left alone: enabling it
   means enforcing the generated-file guard over history that has never been
   checked, which is its own task.
2. **Not-yet-merged commits from another branch stay in a push range.** The
   integration target is read at CI time. The PR #4215 replay contains one
   (`07747254e`, which landed on `dev` later through PR #4214); it carries valid
   trailers and passes. This is intended — the base branch does not own it yet.
3. **An empty range passes.** If the base tip already contains the PR head — a
   re-run after auto-merge landed — nothing is scanned. Those commits were
   gated on the way in.
