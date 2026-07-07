# Task Brief: AG-DYNUI-FULL-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Wire frontend Workshop to Trading Room handoff
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: Codex2 review approved for execute-plans PR #185 at 722cb18; PR is merged as 4cce2d10, integration-gate succeeded, local diff/test/build validation passed. Codex owner should finalize only after recording dev FE deploy and hosted screenshot evidence.

## Summary
修 execute-plans Strategy Workshop CTA：ready 後必須帶 strategyId/strategyVersion/readiness context 進 /agora/trading-room/:strategyId，不可只 navigate('/agora/trading-room')。
