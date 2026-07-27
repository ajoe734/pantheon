# Task Brief: OPS-PUBLISH-ENGINE-UNIFICATION-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Unify nightly publish cut engine across Pantheon and execute-plans
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: Codex2 exact-head review approved Pantheon head 885f9c34d2d83d08e1564089395fe337ea99b033 and execute-plans head ec9ca4d7eb255b72366c43076ad477a3daa77fcf. Pantheon PR #4255 merged as 33e1c4d64e4accceab4d803e7b4ce2324f44306a and execute-plans PR #557 merged as cbc16830a096077f978b4efe499dd8fa85f166f2. Owner closeout records the review and merge evidence, then merges the closeout PR before governed done.

## Summary
把 pantheon 與 execute-plans 的 nightly publish helper 收斂成同一份契約，修掉 pipefail/SIGPIPE 141 與 inadmissible publish-to-deploy dispatch。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
