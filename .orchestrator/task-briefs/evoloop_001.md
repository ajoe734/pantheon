# Task Brief: EVOLOOP-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Deploy evolution dispatch worker
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Final exact-ref run 29319983880 and artifact 8306118270 passed at 26ed8b7d4, proving Compose ownership, automatic research dispatch, active-live freeze non-consumption, and restart idempotence. Sanitized evidence is durable in the task packet. Latest dev merge 26931da04 preserved all task runtime/API blobs; full evolution 240 passed. Prepare final evidence commit and hand off to Claude review.

## Summary
把已寫好的 services/evolution/dispatch_worker.py(LOOP-AUTO-EVO-004)部署成 dev 預設啟動的 compose 服務:輪詢 approved EvolutionDecision、走 gated execute 路徑派執行。自帶 interval env、fail-closed、不碰任何既有 cadence。證據:一筆 approved decision 不經人工 curl 自動轉 executed 並帶 dispatch metadata。
