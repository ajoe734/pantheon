# Task Brief: SUP-L12-STALE-PR-RETIRE-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Retire stale L12 PRs after 1025Z gap audit
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude2
- Next: Human/Ops dependency freshness gate: PR #4372 head 07f163cb21e047a491b1b90c5422dbba69ea0563 is rebased, but its evidence binds live PR #4364 head ecf17e9d088e37102b4128ebc2a7d77e4328be8a. That #4364 head has been reopened because it diverges from current dev and is not acceptable proof. Wait for L12-VERIFY-OBS-001 to produce a non-BEHIND exact head, then refresh #4372 evidence/README/evidence.sha256 against that live head, rerun CI, and handoff Claude2.

## Summary
Retire or supersede stale L12 PRs without closing active product proof.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
