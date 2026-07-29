# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Reviewer-approved exact head 77dc9e49 remains the prior gate. Owner closeout anchors ab31d3ceb/253858835 now handle both reviewer findings: legacy promote refs without workflow_dispatch return legacy_ci_contract instead of a hard recurring error, and auto-merge requests fail closed then verify auto_merge or merged_at through REST. Live read-only proof on PR 4138 head cb90dc479 observed supports_dispatch=false and a non-mutating legacy disposition; release/v2026.07.29.5 head 57abe669f observed supports_dispatch=true and is the current eligible fresh candidate. Push the final evidence head, reacquire Branch CI and Claude2 exact-head review, then wait for the Human/Ops root merge freeze before merging PR 4262. Only after merge may the owner dispatch the fresh candidate, record its checks/auto-merge/master ancestry, retire ancestry-proven stale PRs, and run done.

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
