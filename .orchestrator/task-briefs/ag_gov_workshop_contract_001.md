# Task Brief: AG-GOV-WORKSHOP-CONTRACT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair Governance–Workshop approval and Registry identity contracts
- Status: review_approved
- Owner: Codex2
- Reviewer: Codex
- Next: Independent review approved: PR #4036 head f592009061c4f0ad8741d03656a0823bc35ffe1c merged as 0346b28790d9534cfff76625caeadee8d5ea13b8; follow-up PR #4037 head ae6119811ec4299688ac6860a7c38292d539f33e merged as 49cb982da66ccea5c117a1abc07cb3cb2d345f52, both on dev with Commit trailers, Runtime mirror guard, and Smoke acceptance green. Diff review confirms canonical strategy_workshop schema/API, Registry-vs-strategy identity separation, and exact decided plus approved/approved_with_conditions gating before command admission. Public negative cases prove zero research dispatch, Registry readback, command receipt, session mutation, or event. Independent verification: py_compile passed; focused suite 180 passed/5 skipped with 185 tests collected; direct approved_with_conditions route probe returned research 202 and conclude 200; both PR ranges git diff --check clean. Hosted exact-pair deployment remains the explicitly documented downstream AG-GOV-WORKSHOP-COMPAT-DEPLOY-001 / AG-HOSTED-CLOSE-001 requalification and is not claimed by this contract review.

## Summary
修正 Governance approval 與 Strategy Workshop 的空集合 target-type 合約，以及 Registry entry ID 被誤當 strategy ID 的語意錯置；補齊真實 public API、restart persistence 與 exact-pair hosted regression。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
