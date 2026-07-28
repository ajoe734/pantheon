# Task Brief: L12-GAP-MERGE-QUEUE-20260728

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Claude-priority merge queue for L12 closeout PRs: handle #4286 exact-head reapproval path, #4285 reviewer gate, #4290 closeout review/merge, and safe root-freeze/merge only when gates are green.
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Human/Ops unblock: L12-EVO-001 is now done and archived at 2026-07-28T15:29:31Z. PR #4285 merged as 8a35d0e3dc429acb579816df1f40f2ad24b59919; follow-up closeout PR #4302 merged as 9f7f95e1a10a46248a81b26159e373f75525222f; Codex2 review bound #4302 head 27956cb1c1001d6ebe1acd99cb5c79902a930d18. Continue merge queue on remaining review_approved tasks only; do not revisit stale #4285 heads.

## Summary
- The original L12 closeout queue is drained through PR #4285: its exact
  reviewed head merged, the reviewed closeout evidence follow-up PR #4302
  merged, and `L12-EVO-001` is canonically done.
- Continue only with tasks already in `review_approved`; do not reopen
  implementation tasks or reuse an approval after a PR head changes.
- `L12-DIST-001` PR #4286 and `L12-FLEET-WORKER-OUTCOME-001` PR #4301 are
  merged. Their owners, not this queue wrapper, control canonical `done`.
- `OPS-L12-CLAUDE-DISPATCH-SMOKE-20260728` PR #4300 remains open at reviewed
  head `607df32e1dc658080a282858aa1441967c3df700`, but is behind current `dev`.
  It must compose current `dev`, pass fresh checks, and receive a new exact-head
  Codex2 approval before the root-freeze context can be released.

## Queue Receipts
- PR #4285 reviewed head
  `0fc918b747cf38262360b6045dadd25f157ed9d9` merged as
  `8a35d0e3dc429acb579816df1f40f2ad24b59919`.
- PR #4302 reviewed head
  `27956cb1c1001d6ebe1acd99cb5c79902a930d18` merged as
  `9f7f95e1a10a46248a81b26159e373f75525222f`; `L12-EVO-001` is done and
  archived.
- PR #4301 reviewed head
  `25f238f94282f2cd8541ff488b003b5e983fd864` passed Branch CI, canonical
  review, and root-freeze checks, then merged as
  `d97c25d3cc8860118dd4d0f3c9fafd38490d89c0`.
- PR #4300 reviewed head
  `607df32e1dc658080a282858aa1441967c3df700` has green historical Branch CI
  and canonical review, but no root-freeze release and is behind `dev`; it is
  not merge-eligible.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
