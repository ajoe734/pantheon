# Task Brief: AG-DYNUI-PROD-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Trading Room error diagnostics and stale bundle recovery
- Status: todo
- Owner: Codex2
- Reviewer: Claude
- Next: Assignment created from Agora DYNUI production-gap packet.

## Summary
修 Trading Room production error state：不能只顯示 Failed to load Trading Room；保留 BFF status/code/request/correlation，提供 retry/safe reload，並讓 probe 能抓出 stale bundle/cache/header 問題。
