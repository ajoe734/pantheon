# Task Brief: AG-FE-TR-001-R2

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Trading Room page + BFF client in execute-plans
- Status: done
- Owner: Claude2
- Reviewer: Claude
- Next: Closeout finalized. Deliverables (TradingRoomPage.tsx + tradingRoom.ts) in execute-plans origin/dev via merged PR #2279; 54 tests pass, tsc clean. Task brief synced to done.

## Summary
在 execute-plans 實作交易作戰室頁(原 AG-FE-TR-001 phantom):依 SD §10.4/§12.1 與 v1.3 trading_room_aggregate/decision schema 做 src/agora/pages/trading-room/TradingRoomPage.tsx(多策略 switcher、route /agora/trading-room/:strategyId)與 BFF client src/lib/bff-v1/agora/tradingRoom.ts(live strict、不直接 fetch);掛進 TradingDeskLayout(AG-FE-SW-001 已在 dev)。Agora 不下單(只呈現 + governed intent)。 【UI 嚴格照設計稿】依 SD §9-§12/§23 IA + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure A3 widget_registry/chart grammar + contract-closure/05 page composition + design-closure-round2(v1.3 workshop_card/trading/research schema)實作;ChartSpec 對應 Recharts(metric/line/area/bar)+ ECharts(heatmap/network/sankey/candlestick/gauge);資料一律走 BFF client(src/lib/bff-v1/agora/*),頁面禁止直接 fetch;agent 產出只能是宣告式 spec,前端不得 eval 任意 code。設計稿沒涵蓋先 STOP 問。 【硬性交付規則 — 防 phantom】此為 execute-plans(前端 repo)交付任務。產出物必須**真的 commit 到 execute-plans repo、push、開 PR 到 execute-plans dev**;在檔案真的出現在 execute-plans origin/dev (經 merged PR)之前,**絕對不可把任務標 done**。只在 worktree、只在本地、PR 未 merge,一律**不算完成**。若無法 checkout 或 push 到 execute-plans(例如認證/路徑問題),立刻 STOP 開 blocker、寫清楚錯誤,**不可假裝完成**。
