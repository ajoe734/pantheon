# Task Brief: EVOCHAIN-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Postmortem publisher on incident resolution
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: PR #3627 已審查通過。實地審閱 incident.py/postmortems/main.py/incidents/main.py/evolution/main.py 及 reliable_delivery.py 差異：prepare/activate/reconcile outbox pattern、durable inbox 去重（fingerprint 比對）、atomic JSON fsync 寫入、file+thread lock 皆設計合理。postmortem_bridge.py 確認未變動，維持純函式契約。本地重跑 services/incident services/incidents services/postmortems services/evolution services/foundation/test_reliable_delivery.py：429 passed, 1 skipped（uvicorn subprocess 鏈測試環境未安裝 uvicorn 而跳過）。4 項 acceptance criteria 全數確認達成，核准進入 review_approved。

## Summary
補上 postmortem 事件鏈缺的呼叫端：incident resolve/close 時產生 postmortem record，經 services/evolution/postmortem_bridge.on_postmortem_published 轉成 proposal，並經 POST /api/evolution/proposals 入庫。bridge 本身保持純函式不動。
