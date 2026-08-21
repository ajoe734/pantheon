# Task Closeout Finalization Spec

Status: active operating rule for execution tasks
Last updated: 2026-08-21 (non-retryable governance holds)

This spec applies when a task is in `review_approved` or a worker is
dispatched with `owned_finalize_dispatch`.

## Closeout Owner Rule

Only the task owner may move a `review_approved` task to `done`. The
owner is responsible for making the approved state durable, auditable,
and publish-ready before running `scripts/ai-status.sh done`.

## Review Evidence Manifest Rule

Product-level and loop tasks fail closed at `done` unless the canonical task
row records a task-scoped review evidence manifest in `review_file`. Chat
feedback, a green PR, and a reviewer approval message do not replace this
field.

**This is now a hard precondition, not a preference** (tightened
2026-08-04, SUP-REVIEW-PIPELINE-INTEGRITY-20260804): the review evidence
manifest must already be committed and present in the PR diff *before* a
reviewer is asked to approve. The reviewer binds it during approval:

```bash
AI_NAME=<Reviewer> \
REVIEW_FILE=<repo-relative-task-evidence-path> \
"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" approve \
  "$TASK" "<specific independent review evidence>"
```

Before owner closeout, inspect the canonical row through the governed command
root, not the worktree's possibly stale `ai-status.json`:

```bash
"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" show "$TASK"
```

Do not write, edit, or add the evidence manifest as a new commit *after*
approval to satisfy this rule at `done` time. A commit added post-approval
changes the PR head SHA, which invalidates the exact-head approval binding in
`scripts/git/task_review_merge_gate.py` and forces a full new independent
review cycle for a change that only added bookkeeping -- this is precisely
the livelock diagnosed in SUP-REVIEW-PIPELINE-INTEGRITY-20260804. If the
reviewer approved without recording `review_file` because a manifest was
already committed and reviewed but not bound to the field, the owner may bind
that same already-committed, already-reviewed manifest while running `done`
without creating a new commit. This is an explicit path supported by
`scripts/ai_status.py`; it is not permission to invent new evidence, write a
manifest for the first time at closeout, replace the reviewed artifact, or
edit generated status files:

```bash
AI_NAME=<Owner> \
REVIEW_FILE=<repo-relative-task-evidence-path> \
"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" done \
  "$TASK" "<checkpoint message>"
```

If no evidence manifest was committed before review started, stop and get a
fresh review of a commit that includes one -- do not paper over the gap with
a post-approval evidence commit.

## Non-Retryable Governance Gate Rule

If the governed `done` command rejects finalization because a review,
reassignment, evidence, identity, or policy binding is missing or invalid,
that is a canonical governance hold, not an implementation invitation. Do
not edit `$PANTHEON_COMMAND_ROOT`, its scripts, or its imported modules; do not
patch the guard that rejected the transition; and do not repeatedly invoke
`done` with the same evidence.

Record the existing canonical blocker and stop the worker cleanly:

```bash
AI_NAME=<Owner> \
"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" blocker \
  "$TASK" "<exact failed gate and required corrective evidence>" "<required actor>"
```

Use the actor named by the failed gate. Use `Human/Ops` only when operator
evidence or a policy decision is genuinely required. The canonical `blocker`
transition sets `status=blocked`, `waiting_for`, and an open blocker record;
Supervisor boot reconciliation treats that existing explicit hold as the
worker's durable outcome and must not reconstruct or retry the same finalize
request. Reopen the task only after the named corrective evidence exists.

## Generated Task-Brief Rule

The task brief is generated execution context, not a git-backed approval
ledger. Once a task is `review_approved`, do not edit or commit its task brief
just to copy the current status, `next` message, review decision, or closeout
note. `ai_status.py approve` has already recorded the canonical approval and
the immutable activity event. A task-brief-only commit after approval moves the
PR head and can invalidate the review it merely repeats.

If a task brief appears changed during an owner-finalize dispatch, leave that
generated state out of the closeout commit and inspect the canonical row with
the governed `show` command instead. A task-specific artifact that genuinely
needs review must be added before review begins and approved as part of the
same PR head.

For the twelve-loop delivery, the normal manifest is
`docs/deployment/evidence/twelve-loop-gap/<TASK-ID>/evidence.json`. Confirm the
selected file is present in the merged task PR and contains the independent
review decision before using it.

## Required Closeout Checklist

1. Re-read the task brief, reviewer approval, and touched artifacts.
   - Run the governed `show` command above and confirm `review_file` names the
     committed, reviewed task evidence manifest.
   - If `review_file` is absent, select the exact reviewed manifest now and
     carry it in `REVIEW_FILE` on the final `done` command.
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
8. Run
   `AI_NAME=<Owner> REVIEW_FILE=<evidence-path> "$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" done <task-id> "<checkpoint message>"`
   only after the PR is merged when the canonical row does not already contain
   `review_file`. If it is already present, omit `REVIEW_FILE` and preserve the
   reviewer-bound path. An open PR, auto-merge enabled, or green checks are not
   sufficient.

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

# 4. Wait until GitHub reports the PR merged into dev, inspect the canonical
#    row, then run done with the reviewed evidence manifest when needed.
"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" show "$TASK"
AI_NAME=<Owner> \
REVIEW_FILE=<repo-relative-task-evidence-path> \
"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" done \
  "$TASK" "<checkpoint message>"
```

On an authoritative Phase-6 deployment, Supervisor-issued commands receive the
journal binding automatically. A direct Human/Ops invocation must export the
same absolute, git-external binding from the provisioned live config first:

```bash
export PANTHEON_TASK_STATE_STORE_MODE=authoritative
export PANTHEON_TASK_STATE_EVENT_LOG="$(jq -r '.task_state_store.event_log' "$LIVE_CONFIG")"
```

The wrapper rejects the command if either value is missing; do not point it at
the relative repository template.

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

## Operator Recovery For Already-Merged Work

If the canonical task row lost its approval state but an immutable task brief
already records the independent `review_approved` decision and the delivery is
already merged, `Human/Ops` may use `reconcile_merged_done`. This is a recovery
path, not a substitute for normal review or owner closeout.

The command fails closed unless all of the following are true:

- the actor is `Human/Ops`;
- the evidence file is tracked, byte-identical to the supplied Pantheon commit,
  and that commit is an ancestor of Pantheon `origin/dev`;
- the evidence binds the exact task id, owner, reviewer, and
  `review_approved` status from the canonical row;
- if the canonical reviewer changed after approval, the activity audit contains
  an exact `task_reassigned` event whose timestamp and message still match the
  task row and whose owner did not change;
- the task resolves to one delivery repository;
- the supplied delivery checkout has the expected GitHub origin, and the full
  delivery commit is an ancestor of its `origin/dev`;
- the merged evidence file cites that repository and full delivery commit.

Example:

```bash
AI_NAME=Human/Ops \
RECONCILE_EVIDENCE_FILE=.orchestrator/task-briefs/<task>.md \
RECONCILE_EVIDENCE_COMMIT=<pantheon-merged-evidence-commit> \
RECONCILE_DELIVERY_REPOSITORY=<owner/repo> \
RECONCILE_DELIVERY_ROOT=</absolute/clean/repo/root> \
RECONCILE_DELIVERY_COMMIT=<full-delivery-commit> \
./scripts/ai-status.sh reconcile_merged_done <task-id> "<recovery reason>"
```

The command records both merge targets and commits in delivery metadata,
resolves stale blockers/handoffs, and archives the task through the same
canonical transaction as `done`. Never use it with a draft, unmerged review
file, inferred reviewer identity, or a commit that is not already on `dev`.

## Reviewer Recovery When A Reject Verdict Cannot Be Recorded

`reconcile_merged_done` above only covers a task that was already merged and
whose independent review already **passed**. A different, harder case: the
task's PR merged before the assigned reviewer finished an independent review
(for example the delivery repository's required-status-check for Pantheon
review was never actually satisfiable, or the PR was merged by an identity
that bypasses branch protection), and the reviewer's real verdict is
**reject**. `reopen` requires the bound GitHub PR to still be open
(`scripts/git/github_review_bridge.py` fails closed with
`GitHub PR #<N> is not open`), so it cannot record a reject against an
already-merged head. Do not treat that failure as permission to hand-edit
state, bypass the bridge, or silently drop the review.

The task's own pure lifecycle already has the correct exit for this:
`supersede` is legal directly from `review` (`.orchestrator/rewrite/task_machine.py`)
and, unlike `reopen`/`approve`, carries no GitHub PR-liveness check.

```bash
AI_NAME=<Reviewer or Owner or Human/Ops> \
"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" supersede \
  <task-id> "<independent review verdict: reject, with the specific defects>" \
  [<replacement-task-id>]
```

Run this instead of retrying `reopen` once the bridge reports the PR is not
open. Record the actual defects found in the message (they are real review
evidence, not discarded) and, when the fix is nontrivial, open a replacement
task and pass its id so the defects have somewhere to land. This does not
retroactively fix the stray merge -- if the merged head shipped a real
defect, that is a separate closeout/rollback decision for the owner or
Human/Ops, not something this command resolves. It only unblocks the
stranded canonical row so the fleet dispatcher stops treating it as pending
review forever: the `review_ready_dispatch` binding is keyed to the PR's
head SHA, which does not change after merge, so a stuck `review` row with no
`supersede` is otherwise undispatchable indefinitely.

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
