# Task Brief: SUP-GH-TOOL-AUTH-CLASSIFY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Classify GitHub CLI auth errors as tool auth, not provider auth
- Status: todo
- Owner: Codex
- Reviewer: Claude
- Next: Human/Ops urgent fleet repair: implementing as repo PR plus temporary live repair; status lifecycle will not impersonate a Codex worker because no active worker lease exists.

## Summary
修正 supervisor 對 GitHub CLI tool-auth 失敗字串的分類，避免 Require authenticated gh session 被誤判為 LLM provider auth 而 pause Codex/Codex2 fleet。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
