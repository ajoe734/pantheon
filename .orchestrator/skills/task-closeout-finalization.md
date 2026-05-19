# Task Closeout Finalization Spec

Status: active operating rule for execution tasks
Last updated: 2026-05-17 (per-task PR model)

This spec applies when a task is in `review_approved` or a worker is
dispatched with `owned_finalize_dispatch`.

## Closeout Owner Rule

Only the task owner may move a `review_approved` task to `done`. The
owner is responsible for making the approved state durable, auditable,
and publish-ready before running `scripts/ai-status.sh done`.

## Required Closeout Checklist

1. Re-read the task brief, reviewer approval, and touched artifacts.
2. Confirm the approved scope is still true in the current worktree.
3. Update task-specific records when needed: review notes, acceptance
   packet, handoff packet, evidence note, or narrow docs that describe
   the delivered behavior.
4. Do not broaden canonical architecture docs unless the task
   explicitly changes canonical truth.
5. Run focused verification appropriate to the task and record the
   exact commands in the finalization message or task artifact.
6. Inspect `git status --short` and separate task-owned changes from
   unrelated dirty worktree changes.
   - If this task produced anchor commits, either keep or squash them
     according to review needs; the final task commit still needs the
     required `LLM-Agent`, `Task-ID`, `Reviewer`, and verification
     trailers.
   - If `git status --short` shows files from another task or lane
     (for example generated state mirrors, cross-sidecar docs, or
     unrelated task artifacts), record a blocker and stop. Do not fold
     those files into the closeout commit.
7. Create the task PR (see § Per-Task PR Flow below) whenever the task
   changed repo files, then wait for it to merge into the target branch.
8. Run `AI_NAME=<Owner> ./scripts/ai-status.sh done <task-id> "<checkpoint message>"`
   only after the PR is merged. An open PR, auto-merge enabled, or green
   checks are not sufficient.

## Per-Task PR Flow (mandatory)

Pantheon's branch model is **per-task ephemeral branches** with PR
auto-merge into `dev`. Permanent `worker/<name>` branches are retired.

The full safe sequence for any task that produces commits:

```bash
TASK=<task-id>

# 1. Open a fresh task branch from dev tip.
./scripts/git/task_start.sh "$TASK"

# 2. Edit files. Stage and commit via worker_commit.py — never raw
#    `git add` / `git commit` for task work.
python3 scripts/git/worker_commit.py \
  --task-id "$TASK" \
  --message-file /tmp/${TASK}-msg.txt \
  --scope <path1> <path2> ... \
  --index-file /tmp/git-index-task-$TASK

# 3. Push and open PR with auto-merge.
./scripts/git/task_finalize.sh "$TASK"

# 4. Wait until GitHub reports the PR merged into dev, then run done.
AI_NAME=<Owner> ./scripts/ai-status.sh done "$TASK" "<checkpoint message>"
```

### Background Worker Restrictions

Auto workers run without a human-attended terminal. Forbidden:

- Interactive git commands (`git add -p`, `git add -i`,
  `git commit --interactive`, `git rebase -i`).
- Direct push to `dev` or `master` — both are branch-protected, push
  will be rejected.
- Raw `git add .` or `git add -A` — `check_commit_scope.py` will reject
  any commit whose staged files leak outside the declared task scope.

### Preemption Anchor Rule

Before a background worker is reassigned, suspended, or dispatched to a
different task, any non-trivial design diff must be made durable:

1. stay on the current `task/<TASK-ID>` branch
2. write a narrow commit message that says which layer is owned and what
   boundary is intentionally left alone
3. run `worker_commit.py` with explicit `--scope` and the private
   `--index-file`
4. only then allow reassignment or task switching

This rule is mandatory for docs, `.orchestrator/skills/*`,
config/workflow files, and supervisor dispatch or routing contact
points. These surfaces go through task PRs, not session-only diffs. If a
remaining diff is genuinely disposable, record that explicitly in the
handoff note; otherwise, do not rely on stash as the preservation path.

## Shared-Index Footgun (Why worker_commit.py is mandatory)

All workers share one worktree, hence one `.git/index`. If a previous
worker left files staged (interrupted commit, crash) and you run
`git commit`, your commit silently absorbs the leftover. This is the
2026-05-16 sweep-in incident (commit `e06f5cf2`) where a FinRL worker's
narrow `git add` was followed by a `git commit` that captured 8
unrelated foreground files left in the index.

`scripts/git/worker_commit.py` mitigates this in three layers:

1. `git restore --staged --` clears any stale staging before adding.
2. Stages only what was passed via `--scope`; aborts if the resulting
   set leaks outside scope.
3. With `--index-file <path>` uses a private `GIT_INDEX_FILE` so a
   concurrent worker's staging cannot leak into yours even if both run
   simultaneously.

If you must commit outside `worker_commit.py` (foreground human flow):

```bash
git restore --staged --                       # MANDATORY: clear stale staging
git add <explicit list of task files>         # never `git add .` or `-A`
git diff --cached --name-only                 # eyeball the staged set
git commit -F /tmp/${TASK}-msg.txt            # use -F (heredoc is fragile)
```

## Commit Requirements

Task closeout commits must be narrow and traceable.

Subject:

```
<TASK-ID>: <imperative summary>
```

≤ 70 chars. Subjects starting with `Merge `, `Revert `, `promote:`,
`hotfix:`, `publish:`, `OPS-GIT-{WORKFLOW,REDESIGN}-` or
`OPS-{DOC,REBASE}-` skip the trailer check.

Required trailers (enforced by `.githooks/commit-msg`):

```
LLM-Agent: <Owner>
Task-ID: <task-id>
Reviewer: <reviewer, != owner>
```

Optional:

- `Verified: <command summary>` — required when tests / checks ran.
- `Hotfix: yes` — required on hotfix-path commits.
- `Cross-Dir: yes` — required when the commit intentionally spans
  more than 3 top-level directories.

Forbidden:

- Stage files outside the declared task scope.
- Commit unrelated user or worker changes to "clean the worktree".
- `--amend` a commit that has been pushed.
- Empty commits (they jam the rebase loop).

If unrelated dirty files prevent an isolated task commit, the owner may
still finalize **only when** the reviewed deliverable is already
durable and the `done` message clearly states why no isolated commit
was created. This is an exception, not the default.

## Status And Archive Effects

`scripts/ai-status.sh done` is the canonical closeout command. It updates:

- `ai-status.json`
- `current-work.md`
- `docs-site/*` mirrors
- `ai-task-archive/tasks/<task-id>.json`
- delivery metadata, including branch, HEAD commit, worktree dirtiness,
  remote/upstream, and push status.

Do not edit these generated state files by hand during closeout.

## Push and Merge Policy

Closeout is not complete until the finished work has merged into the
target branch (`dev` for Pantheon task PRs). `scripts/ai-status.sh done`
enforces this by verifying the task branch HEAD is an ancestor of the
target branch before it updates `ai-status.json` or archives the task.

- Default: after the task-scoped commit, push the `task/<TASK-ID>`
  branch and open a PR via `task_finalize.sh`. Wait for GitHub to merge
  that PR, then run `scripts/ai-status.sh done`.
- `dev` and `master` are branch-protected: a direct `git push` to
  either will be rejected by GitHub. Workers must always go through PR
  + auto-merge.
- `task/<TASK-ID>` branches are auto-deleted by GitHub when the PR
  merges. If a PR fails CI, the task branch stays for the worker (or
  chair-review) to push a fix commit; do **not** force-push to recover
  unless explicitly authorized.
- If the PR is `BEHIND`, failing checks, or otherwise still open, leave
  the task in `review_approved`; refresh or repair the PR branch first.
- Never use `--force`, `--mirror`, `--delete`, `--all`, or `--tags`
  pushes as routine closeout.

## Chair Man Oversight

Chair man should flag any completed task with one of these closeout gaps:

- `review_approved` remains idle while its owner is available.
- `done` was recorded without a task-scoped commit and no exception note.
- A `task/<id>` PR is open > 24 h without merging (status check failing,
  unresolved review conversation, or stale base).
- `task/<id>` branches that exist on origin without a corresponding
  open PR (zombie task branch — recommend deletion).
- finalization that skipped required review, acceptance, or evidence
  artifacts.

Chair man should recommend owner re-dispatch, a small closeout
follow-up, or approve the scoped normal push depending on the gap.
