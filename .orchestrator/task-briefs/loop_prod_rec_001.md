# Task Brief: LOOP-PROD-REC-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Full-stack loop recovery and fault-injection harness
- Status: todo
- Owner: Codex
- Reviewer: Claude
- Next: Helper-claimed by idle Codex; previous owner Claude becomes reviewer.

## Summary
建立可重複的 target-dev recovery harness，在 outbox、downstream mutation、receipt、projection 各切點注入故障，並驗證 duplicate、lease expiry、timeout、worker/BFF/DB/full-stack restart。
