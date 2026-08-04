# #4396 current-head governed-closeout gate

Task: `SUP-L12-RUNNING-OWNER-EXACT-HEAD-PR-CLOSEOUT-V2-20260731`

Owner: Codex

Reviewer: Codex2

Review manifest: `evidence.json`

## Result

This task verifies the V2 supersession boundary and records why neither PR
#4396 nor its subject PR #4386 can yet be counted as completed support work.

The failed requeue receipt proves that the prior task ID is immutable-bound to
the old Wave 0X packet/spec. The prior ID was not edited or reused.

PR #4396 is open, not draft, and has successful Branch CI checks. Its current
head is `ba282edd81c00e75d3c96c820922ee3bb9d7f6ac`, which descends from both the
historical expected head and the canonical review-binding head. It is now
`BEHIND` `dev`, has no GitHub review decision, and its auto-integrator dry run
rejects the recorded approval because Antigravity is not the currently assigned
canonical reviewer (Codex).

PR #4386 remains open with a conflicting current head. No protected merge or
governed owner closeout exists for it.

## V2 delivery PR

This evidence is on ReviewBus PR [#4468](https://github.com/ajoe734/pantheon/pull/4468)
at `9636815e1102da9f3257f8a26f26e30d05d9b087`; auto-merge is disabled by the
review-before-merge policy. The Python packaging and runtime-mirror checks pass.

The canonical review gate is intentionally unsatisfied until Codex2 records a
review-proof tag for this exact head. The trailer gate also rejects two
overlength subjects, including pre-existing pushed commit `7cc9b02…`. This task
does not rewrite a pushed commit; maintainer-approved replacement or rewrite
authority is required before #4468 can pass CI and merge.

## Required next actions

1. Codex2 independently reviews this V2 evidence and decides the safe
   remediation for #4468's pre-existing pushed trailer failure.
2. The authorized reviewer/root-freeze path must bind a valid current-head
   review for #4396 and resolve its merge gate.
3. #4386 must be protected-merged and its actual owner must run governed
   closeout before downstream L12 work can count it.

Exact readbacks and commands are in `validation.txt`.
