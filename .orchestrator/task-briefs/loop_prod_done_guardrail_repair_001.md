# Task Brief: LOOP-PROD-DONE-GUARDRAIL-REPAIR-001

> Temporary coordination routing: until
> `OPS-WORKTREE-CENTRAL-STATUS-ROOT-POSTMERGE-001` is accepted, every owner or
> reviewer working this task must run governed state, review, handoff and
> closeout commands through `/home/lupin/code/pantheon/scripts/ai-status.sh`
> with its own real identity (`AI_NAME=Codex2` for the owner or
> `AI_NAME=Claude` for the reviewer).
> Do not use the task-worktree wrapper for state. Git and tests stay in the
> task worktree. Verify the result with the central `show` command.

## Responsibility

Owner Codex2 implements. Reviewer Claude performs the independent review.
The planner does not implement this task.

## Required result

Repair the loop closeout guard so a product task cannot become done when its
evidence is blocked, pending, review-required, missing a formal reviewer
verdict, or still has a blocking residual risk.

Replay the authoritative live archive read-only from:

`/home/lupin/code/pantheon/ai-task-archive/tasks`

The frozen product-closure set is exactly these 18 IDs:

- LOOP-PROD-AGORA-001
- LOOP-PROD-AGORA-002
- LOOP-PROD-ALPHA-001
- LOOP-PROD-AUTH-001
- LOOP-PROD-CAP-001
- LOOP-PROD-CONS-001
- LOOP-PROD-DEP-001
- LOOP-PROD-DIST-001
- LOOP-PROD-GAP-ADDENDUM-001
- LOOP-PROD-GAP-ADDENDUM-002
- LOOP-PROD-IMIT-001
- LOOP-PROD-MAI-001
- LOOP-PROD-OODA-001
- LOOP-PROD-REC-001
- LOOP-PROD-RUNTIME-BOOT-001
- LOOP-PROD-SRC-001
- LOOP-PROD-TEACH-001
- LOOP-PROD-TEL-001

Do not derive this set from a glob. Reject a missing ID, unexpected ID,
duplicate ID, malformed snapshot, or filename/task-ID mismatch. Hash every
source snapshot before and after replay and prove it was not modified.

Each result must be one of `valid_closure`, `stale_evidence`, or
`false_closure`. Every non-valid result must name a unique repair task ID
and list the exact missing or contradictory proof.

## Required tests and delivery

Add focused tests for the exact 18-ID set, missing/extra/duplicate sources,
malformed JSON, filename/task-ID mismatch, immutable hashes, classification,
and repair task IDs. Run the closeout guard tests and syntax checks.

PR #3739 and PR #3741 are interim, zero-independent-review work and cannot
close this task. Submit the corrected follow-up through a PR, then hand off
to Claude. Do not mark done until Claude independently reviews the exact
candidate and the merged result is verified.
