# Task Brief: L12-GITHUB-REVIEW-BRIDGE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Antigravity is quota-blocked and Claude is temporarily quota-paused; dispatching to available real Codex worker so review bridge work does not stall.
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: Independent review passed: PR #4280 head 3a575f7088ef5a81ac2d1be3719563776e535b1b merged as 16296c35fd2e604f3ecf2d06dec80da0040ee8e0; follow-up PR #4281 head cfdef1b368ebbd54ca46385d3d9f66d0d5ab3fe3 merged as cd09255a5ad82b3089ea7deb325dfe5ad7178a83. Both heads have successful branch-policy-required Pantheon canonical review gate statuses while GitHub reviews are empty; all visible PR checks pass. Local focused validation passed: 165 pytest cases with 31 subtests, py_compile, both PR trailer ranges, and both diff checks.

## Summary
Bind fleet reviewer decisions to GitHub review gates

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
