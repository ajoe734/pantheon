# Task Brief: AG-GOV-WORKSHOP-CONTRACT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair Governance–Workshop approval and Registry identity contracts
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Next: Review failed at pushed f59200906 / merged PR #4036 (merge 0346b28790d9534cfff76625caeadee8d5ea13b8): acceptance requires Workshop research/conclude to accept only a decided approval, but _require_approval uses 'if state and ...' and therefore accepts a missing state, and it also accepts noncanonical state/outcome aliases. Reviewer reproduction against the existing public router harness: approval with state removed returned HTTP 202 and a completed research dispatch; outcome='accepted' also returned HTTP 202/completed. Make the gate fail closed on exact canonical decision_state/state='decided' and canonical outcome in {'approved','approved_with_conditions'}, add negative coverage for both research and conclude proving no downstream dispatch/conclusion for missing/non-decided state and noncanonical outcome, and update the task evidence. Other verification passed: py_compile; focused suite 174 passed, 5 skipped; git diff --check; PR CI green.

## Summary
修正 Governance approval 與 Strategy Workshop 的空集合 target-type 合約，以及 Registry entry ID 被誤當 strategy ID 的語意錯置；補齊真實 public API、restart persistence 與 exact-pair hosted regression。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
