# Task Brief: EVOLOOP-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Deploy evolution dispatch worker
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Run 29314870187 and artifact 8303995676 proved exact-ref Compose ownership, automatic research dispatch, active-live freeze non-consumption, and restart idempotence at 47a009bff. Branch refreshed to current dev and compatibility anchored/pushed at 633d6f6fb; focused 49 and full evolution 239 passed. Final refreshed-head dev/root probe waits on the acknowledged execute-plans PR #328 stable-deploy hold.

## Summary
把已寫好的 services/evolution/dispatch_worker.py(LOOP-AUTO-EVO-004)部署成 dev 預設啟動的 compose 服務:輪詢 approved EvolutionDecision、走 gated execute 路徑派執行。自帶 interval env、fail-closed、不碰任何既有 cadence。證據:一筆 approved decision 不經人工 curl 自動轉 executed 並帶 dispatch metadata。
