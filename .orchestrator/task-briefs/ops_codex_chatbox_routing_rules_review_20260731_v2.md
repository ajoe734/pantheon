# Task Brief: OPS-CODEX-CHATBOX-ROUTING-RULES-REVIEW-20260731-V2

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Validate and hand off chatbox routing rules for independent review
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Codex revalidates PR #4401 exact head against its authored base and hands the refreshed evidence to Claude for independent review.

## Summary
Governed review handoff for the already published routing-policy PR. Codex
validates the exact historical head and hands it to the current canonical
reviewer, Claude. No parallel implementation is authorized, and this task does
not claim that PR #4401 merged or remains the current `dev` policy.

## Owner Revalidation
- Target: PR #4401 head `1cc28e07ecaee0b03c4d26c76e05dcea31952d79`
- Authored base and sole parent: `cf6a8fed7baeb102330bf32af3f59cc347d1e92a`
- Scope: root `AGENTS.md`, 74 additions and 0 deletions
- Result: `git diff --check` passed; 22/22 focused policy assertions passed; all 10 currently visible historical GitHub check runs succeeded
- Current PR disposition: closed on 2026-08-02 without merge
- Boundary: current `origin/dev` contains a later policy revision; this review is bound only to the exact historical PR head above

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
