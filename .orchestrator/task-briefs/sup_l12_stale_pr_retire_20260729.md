# Task Brief: SUP-L12-STALE-PR-RETIRE-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Retire stale L12 PRs after 1025Z gap audit
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: Independent review: PR #4372 remote exact head eedccf8c6aaf495d85e6ff2b07fa4a678b000db4 verified; committed manifest checksum and JSON parse match, net diff is only the four scoped evidence/brief files, #4367 is CLOSED/unmerged, #4364/#4297/#4313 remain OPEN with canonical review/review_approved/blocked evidence, and #4365/#4366 merge commits are ancestors of origin/dev. Reviewed disclosed ac6ee7f2 provenance: its actual patch is only this task brief; no BFF content enters the PR.

## Summary
Retire or supersede stale L12 PRs without closing active product proof.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
