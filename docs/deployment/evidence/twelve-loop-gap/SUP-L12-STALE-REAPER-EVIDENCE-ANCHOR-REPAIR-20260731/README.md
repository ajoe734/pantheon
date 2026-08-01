# PR #4385 stale-reaper evidence-anchor repair

Task: `SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-20260731`

This packet repairs the concrete referential-integrity defect identified by
PR #4395. Before editing, PR #4385 still pointed at exact head
`f5e70e86e01bde005dae5fed94b151c9bc07f389`. Its subject README and both
machine-readable anchor fields named nonexistent commit
`9d53a94a265c55af4c8d15c50ab3751f1440ac0f`; the actual rebased implementation
anchor is `9d53a94a295d71ee49aea6f4b96e47fbcfd29093`.

## Repair topology

Commit `87dd23dc84552dc58f72bd58cb58b968d358b684` composes current
`origin/dev@93e5b3d4ad0ad94f79bfe512ba3f67402da8d468` with the rejected PR #4385
head. Keeping #4385 as the second parent preserves the actual implementation
anchor in the delivered ancestry. The merge also changes the subject README
and both subject manifest anchor fields to the same real full SHA.

The new task branch and PR #4452 supersede #4385 as the governed delivery path. This
avoids treating the original task's stale `review_approved` row as authority
for an unreviewed head while retaining the exact implementation history that
PR #4395 inspected.

## Owner verification

- The five focused stale missing-process failure-streak regressions passed.
- The complete supervisor suite passed all 473 tests.
- `evidence.json` parses and its anchor assertions pass.
- `git diff --check origin/dev..HEAD` passes.
- the task commit trailer range check passes.
- `.orchestrator/config.json` has no diff from `origin/dev`.
- the invalid SHA is not a commit; the real SHA and rejected #4385 head are
  ancestors of the superseding branch.

The full commands and results are in [`evidence.json`](evidence.json).

## Admission boundary

This is owner evidence only. It does not approve PR #4452's current task head, merge
the task PR, close the original subject task, prove live promotion, or resume
Wave 0. Antigravity must review the exact task PR head, after which the normal
protected merge and governed owner closeout remain mandatory.

## Owner closeout audit

The governed task row reached `review_approved`, but the immutable Antigravity
approval event at `2026-08-01T14:41:16Z` names reviewed SHA
`14487789314c4495e865a7d7ef1aae9c43d70650`. That object does not exist. PR
#4452, the local task branch, and the remote task ref instead all resolve to
`1448778931c2058fceb715ad13423e639f5c0865`, which had been the branch head
since `2026-08-01T14:34:48Z`.

The PR has no GitHub review, the canonical task row has no `review_file`, and
the manifest had no independent decision to bind. Owner closeout therefore
fails closed: the review status row alone does not satisfy the task's exact-head
acceptance. Antigravity must review the then-current PR head and bind this
committed manifest before protected merge and governed `done`.
