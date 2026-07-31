# Task Brief: OPS-CODEX-CHATBOX-ROUTING-RULES-REVIEW-20260731-V2

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Validate and hand off chatbox routing rules for independent review
- Status: todo
- Owner: Codex
- Reviewer: Antigravity
- Next: Assignment created

## Summary
Governed review handoff for the already published routing-policy PR. Codex validates and hands off; Antigravity independently reviews the exact head. No parallel implementation is authorized.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Owner Validation Anchor (2026-07-31)
- Inspected PR #4401 at exact base `cf6a8fed7baeb102330bf32af3f59cc347d1e92a` and exact head `1cc28e07ecaee0b03c4d26c76e05dcea31952d79`; the range changes only root `AGENTS.md` with 74 additions and no deletions.
- `git diff --check cf6a8fed7baeb102330bf32af3f59cc347d1e92a 1cc28e07ecaee0b03c4d26c76e05dcea31952d79` passed.
- Twenty-one focused policy-text assertions passed for the bounded direct repair lane, dashboard example, read-only/deduplicated integration planning, governed task packets, supervisor receipt and materialization, monitoring without takeover, read-only extension subagents, sole supervisor dispatch authority, Live Repair preservation, and non-independent Codex/Codex2 identities.
- Cross-checked the exact-head `AGENTS.md` against its existing Live Repair Rule, Management AI dev bridge routes, repair-worktree boundary, and supervisor handoff path; no conflicting or bypassing rule was found.
- GitHub reported PR #4401 as `OPEN`, `MERGEABLE`, and `BEHIND`; all nine visible check runs were successful. The task does not authorize updating the PR, so independent review remains bound to head `1cc28e07ecaee0b03c4d26c76e05dcea31952d79`.
- Governed `progress` and `handoff` did not write: the command failed closed because inherited `PANTHEON_COMMAND_RUNTIME_SHA=88ace74d344036c8421b8df39c2c8ebe54b31233` no longer matched command-root HEAD `894eb813c7cb5609ae517103a727d93ba8cbd1ed`. A fresh supervisor dispatch/runtime binding must perform the Antigravity handoff; do not bypass the runtime guard.
