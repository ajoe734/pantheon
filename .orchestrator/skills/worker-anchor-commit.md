# Worker Anchor Commit Protocol

Status: active operating rule for background workers
Last updated: 2026-05-17

Use this skill when a worker has made meaningful progress that should
survive reassignment, interruption, provider restart, or `dev` advancing
before closeout.

## Purpose

An anchor commit is not task closeout. It is a durable mid-task marker
that says: this lane owns this layer of the surface, and this is the
boundary it intends to compose with later work.

Uncommitted diffs are not handoff state. Stash is a recovery tool for
disposable local edits, not a preservation path for design intent.

## When To Anchor

Create an anchor commit as soon as the work reaches a describable
intermediate state and any of these are true:

- the change spans multiple files
- the change touches docs that alter process, branch, product, or
  architecture truth
- the change touches `.orchestrator/skills/*`
- the change touches config or workflow files
- the change touches `.orchestrator/supervisor.py`, dispatch policy,
  branch workflow, wakeup templates, or routing contact points
- the change is unlikely to finish within one supervisor cycle
- the worker is about to yield, switch tasks, or be reassigned

## How To Anchor

1. Stay on the current task branch. In the normal Pantheon workflow this
   is `task/<TASK-ID>` created from `dev` by `scripts/git/task_start.sh`.
2. Check the worktree:

   ```bash
   git status --short
   ```

   If dirty files belong to another task, record a blocker and stop.
   Do not stash and continue.

3. Write a narrow commit message:

   ```text
   <TASK-ID>: anchor <scope>

   Owned layer: <what this worker changed>
   Not changing: <nearby layer intentionally left alone>
   Composes with: <PR/task/mainline surface this must compose with>

   LLM-Agent: <Owner>
   Task-ID: <TASK-ID>
   Reviewer: <Reviewer>
   Verified: not run; anchor commit only
   ```

4. Commit through the worker-safe wrapper with explicit scope:

   ```bash
   python3 scripts/git/worker_commit.py \
     --task-id "$TASK" \
     --message-file /tmp/$TASK-anchor-msg.txt \
     --scope <path1> <path2> ... \
     --index-file /tmp/git-index-task-$TASK
   ```

5. Continue implementation, or yield with the anchor commit hash in the
   handoff / blocker note.

## Forbidden

- Do not use `git stash` to preserve design intent on docs, skills,
  config, supervisor, dispatch, branch, wakeup, or routing surfaces.
- Do not stage unrelated dirty files to make the worktree look clean.
- Do not use raw `git add .`, `git add -A`, or interactive staging in a
  background worker.
- Do not treat an anchor commit as review approval or `done`.

## Doc / Skill / Config Special Case

Doc, skill, and config changes must still use task branches and PRs.
They may be lightweight ops-doc tasks, but they should not remain as
session-only diffs. The task still needs a task id, reviewer, PR, and
merge record; the difference is that the review can be a narrow process
or docs review rather than a full runtime acceptance cycle.

## Closeout Interaction

At final closeout, anchor commits may be preserved or squashed according
to the PR's review needs. The last task commit must still carry the
required `LLM-Agent`, `Task-ID`, and `Reviewer` trailers, plus
`Verified` when checks were run.
