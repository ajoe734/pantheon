# Task Brief: ASST-SKILL-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Define assistant-skill descriptor schema and effective-catalog resolver
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: All acceptance criteria met: descriptor schema complete, deny-by-default resolver via existing policy layer, effective_skills returned per mode/role, no second registry, tests cover allow/deny/per-mode. Returned to Codex for done closeout.

## Summary
定義 assistant-skill descriptor（id/title/surface/mode_gate/role/confirm_policy/input_schema/handler_ref/result_surface），並讓 OpenClaw tool/workflow policy 以 deny-by-default 解析每個 operator/agent/mode 的 effective skills，沿用既有 /api/openclaw-adapter/tools 發現端點，不另建 registry。
