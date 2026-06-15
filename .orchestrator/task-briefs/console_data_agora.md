# Task Brief: CONSOLE-DATA-AGORA

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Populate /bff/agora/* (20 surfaces)
- Status: review
- Owner: Codex
- Reviewer: Claude2
- Next: Ready for review. PR #1695 merged to dev at merge commit 2a820e3be6366d8fe56b4b68eeb6fb819c00e584. Implemented consultation-to-agora projection script, BFF service-store wiring, compose env defaults, contract test, and evidence note. Validation: py_compile projection/read_store/test; pytest console_data_agora_projection; pytest bff agora core/extended; pytest consultation compose activation; git diff --check. Local producer proof created request cr-430a59573e9e, memo mem-ddb8921b461a, handoff gh-48713d4d6166 and projected /bff/agora surfaces count>0/status=ok in strict store mode; remote dev BFF pre-deploy still source=missing until deployed/projection run.

## Summary
consultation/agora producers 產真 session/inbox/signals/journal/notes 等;接各 agora read-surface。用真實 producer 產生真資料，投影或接線到 BFF read stores；驗收 BFF agora 面 count>0 且 surface status=ok；加 contract test；stub dispatch 為 dev 安全姿態。
