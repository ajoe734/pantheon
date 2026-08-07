# Reviewer Approval Binding Skill

Use this skill whenever you independently review a task and are about to run
`approve`. It applies most importantly to a task that has a delivery pull
request, including a task whose brief does not already record the PR number.

## Why the binding is required

`review_approved` must name the exact pull-request head that the reviewer
inspected. A free-text approval or timestamp cannot prove which commit was
reviewed. An unbound approval may initially land only for a task whose PR is
not yet recorded in durable task metadata, but the review-before-merge gate
will subsequently reject it as `approval_head_binding_missing` and return the
task for another review.

For a PR-backed task, set both `REVIEW_PR` and `REVIEW_HEAD_SHA`. `REVIEW_BASE`
is optional and defaults to `dev`; set it explicitly when the PR targets a
different base. `REVIEW_HEAD_BRANCH` is normally inferred as `task/<TASK>` and
only needs to be supplied for a nonstandard branch.

## Reviewer procedure

1. Complete an independent review of the task branch, the PR diff, and the
   review evidence manifest required by
   `.orchestrator/skills/task-closeout-finalization.md`.
2. Identify the delivery PR and capture its current exact head after the
   review. Do not use an abbreviated SHA or a generic local `HEAD` that is not
   known to be the reviewed PR head.
3. Run the governed approval command with the captured binding. Replace the
   placeholders below with the reviewed task, PR, reviewer identity, and
   already-committed evidence path:

   ```bash
   TASK=<task-id>
   PR=<pr-number>
   HEAD_SHA="$(gh pr view "$PR" --json headRefOid -q .headRefOid)"
   BASE="$(gh pr view "$PR" --json baseRefName -q .baseRefName)"

   AI_NAME=<reviewer> \
   REVIEW_PR="$PR" \
   REVIEW_HEAD_SHA="$HEAD_SHA" \
   REVIEW_BASE="$BASE" \
   REVIEW_FILE=<repo-relative-reviewed-evidence-path> \
   "$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" approve "$TASK" \
     "Independent review approved at the exact PR head."
   ```

4. If the PR head changes at any point after the captured SHA, stop. Review
   the new commit, capture its new `headRefOid`, and issue one fresh exact-head
   approval. Never reuse an approval binding for a different PR head.

## Failure handling

- `REVIEW_PR` without `REVIEW_HEAD_SHA`, or the reverse, is invalid.
- `REVIEW_HEAD_SHA` must be the full 40-hex object ID returned by `gh pr view`.
- If the task truly has no delivery PR, confirm that fact before accepting the
  unbound warning. Do not use an unbound approval as a shortcut for a
  PR-backed task.
- If the reviewed evidence manifest was not committed at the captured head,
  do not approve. Have the owner add it, then perform a fresh review of that
  new head.

The reviewer alone moves a task from `review` to `review_approved`; the owner
then follows the closeout skill to finalize it to `done`.
