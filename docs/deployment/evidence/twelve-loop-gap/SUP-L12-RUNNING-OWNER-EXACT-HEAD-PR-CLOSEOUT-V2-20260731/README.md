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

## Required next actions

1. Codex2 independently reviews this V2 evidence and the current facts.
2. The authorized reviewer/root-freeze path must bind a valid current-head
   review for #4396 and resolve its merge gate.
3. #4386 must be protected-merged and its actual owner must run governed
   closeout before downstream L12 work can count it.

Exact readbacks and commands are in `validation.txt`.
