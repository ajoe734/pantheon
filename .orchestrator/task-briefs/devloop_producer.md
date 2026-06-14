# Task Brief: DEVLOOP-PRODUCER

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Signal producer module: decision -> RedisPendingSignalStore
- Status: todo
- Owner: Codex
- Reviewer: Claude
- Next: Helper-claimed by Codex while Claude is dispatch-paused.

## Summary
新增 signal producer 模組(新檔 services/execution/lean_runtime/signal_producer.py):把 persona/strategy 決策輸出對應到信號 schema v1,enqueue 進 RedisPendingSignalStore。純程式+單元測試,不需 runtime 已部署即可完成。
