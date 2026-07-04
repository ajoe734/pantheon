# Task Brief: AG-DYNUI-PROD-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Trading Room error diagnostics and stale bundle recovery
- Status: review_approved
- Owner: Codex2
- Reviewer: Claude
- Next: Review approved: diagnostics/probe/cache hardening verified independently (tests, build, node --check, bash -n). Owner should capture real hosted proof after this branch is deployed (deploy dispatch needs human approval) before running done.

## Summary
修 Trading Room production error state：不能只顯示 Failed to load Trading Room；保留 BFF status/code/request/correlation，提供 retry/safe reload，並讓 probe 能抓出 stale bundle/cache/header 問題。
