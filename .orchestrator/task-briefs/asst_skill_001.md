# Task Brief: ASST-SKILL-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Define assistant-skill descriptor schema and effective-catalog resolver
- Status: review
- Owner: Codex
- Reviewer: Claude
- Next: Implementation merged in PR #1160 (merge commit a5a4ea77162fdcb8f16f433f829bf06484b0d0b5; head 0cb63a68b74be9b314885a483d1584afbd800f7c). Local validation passed and GitHub checks passed. Please review/approve so owner can run done closeout.

## Summary
定義 assistant-skill descriptor（id/title/surface/mode_gate/role/confirm_policy/input_schema/handler_ref/result_surface），並讓 OpenClaw tool/workflow policy 以 deny-by-default 解析每個 operator/agent/mode 的 effective skills，沿用既有 /api/openclaw-adapter/tools 發現端點，不另建 registry。
