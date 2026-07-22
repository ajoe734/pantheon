# Task Brief: EVOLOOP-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Deploy evolution dispatch worker
- Status: review_approved
- Owner: Antigravity
- Reviewer: Claude
- Next: Review approved: hosted dev proof (run 29319983880 / artifact 8306118270) verified against local test suite, compose config, and evidence checksum; returning to owner for finalization

## Summary
把已寫好的 services/evolution/dispatch_worker.py(LOOP-AUTO-EVO-004)部署成 dev 預設啟動的 compose 服務:輪詢 approved EvolutionDecision、走 gated execute 路徑派執行。自帶 interval env、fail-closed、不碰任何既有 cadence。證據:一筆 approved decision 不經人工 curl 自動轉 executed 並帶 dispatch metadata。
