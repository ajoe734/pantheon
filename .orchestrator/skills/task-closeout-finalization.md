# Task Closeout Finalization Spec

Status: active operating rule for execution tasks
Last updated: 2026-04-29

This spec applies when a task is in `review_approved` or a worker is dispatched with `owned_finalize_dispatch`.

## Closeout Owner Rule

Only the task owner may move a `review_approved` task to `done`. The owner is responsible for making the approved state durable, auditable, and publish-ready before running `scripts/ai-status.sh done`.

## Required Closeout Checklist

1. Re-read the task brief, reviewer approval, and touched artifacts.
2. Confirm the approved scope is still true in the current worktree.
3. Update task-specific records when needed: review notes, acceptance packet, handoff packet, evidence note, or narrow docs that describe the delivered behavior.
4. Do not broaden canonical architecture docs unless the task explicitly changes canonical truth.
5. Run focused verification appropriate to the task and record the exact commands in the finalization message or task artifact.
6. Inspect `git status --short` and separate task-owned changes from unrelated dirty worktree changes.
7. Create a task-scoped commit before finalizing whenever the task changed repo files and an isolated commit is possible.
8. Run `AI_NAME=<Owner> ./scripts/ai-status.sh done <task-id> "<checkpoint message>"` only after the above is complete.

## Commit Requirements

Task closeout commits must be narrow and traceable.

- Commit subject must include the task id.
- Commit body must include:
  - `LLM-Agent: <owner>`
  - `Task-ID: <task-id>`
  - `Reviewer: <reviewer>`
- The body should also include a short verification summary.
- Stage only files that belong to this task.
- Never commit unrelated user or worker changes just to make the worktree clean.

If unrelated dirty files prevent an isolated task commit, the owner may still finalize only when the reviewed deliverable is already durable and the `done` message clearly states why no isolated commit was created. This is an exception, not the default.

## Status And Archive Effects

`scripts/ai-status.sh done` is the canonical closeout command. It updates:

- `ai-status.json`
- `current-work.md`
- `docs-site/*` mirrors
- `ai-task-archive/tasks/<task-id>.json`
- delivery metadata, including branch, HEAD commit, worktree dirtiness, remote/upstream, and push status

Do not edit these generated state files by hand during closeout.

## Push Policy

Closeout is not complete until the finished work is published to the configured upstream whenever that is safely possible.

- Default: after the task-scoped commit, `done` transition, generated state/archive update, and any required state/archive commit, run a normal non-force `git push` to the configured upstream.
- If delivery metadata shows `push_status: ahead`, treat the task as publish-incomplete until the branch is pushed or an explicit human hold says not to publish.
- Chair man must approve a pending normal non-force `git push` when the branch/upstream are clear, the commit metadata matches the task or closeout batch, and no human hold is present.
- Never approve or run `git push --force`, `--mirror`, `--delete`, `--all`, `--tags`, or broad ambiguous push commands as routine closeout.
- If there is no upstream, leave `push_status: no_upstream`, record the publication gap, and escalate for a remote/upstream decision instead of inventing a remote target.

## Chair Man Oversight

Chair man should flag any completed task with one of these closeout gaps:

- `review_approved` remains idle while its owner is available.
- `done` was recorded without a task-scoped commit and no exception note.
- `push_status: ahead` remains after task finalization on a branch with a configured upstream.
- finalization skipped required review, acceptance, or evidence artifacts.

Chair man should recommend owner re-dispatch, a small closeout follow-up, or approve the scoped normal push depending on the gap.
