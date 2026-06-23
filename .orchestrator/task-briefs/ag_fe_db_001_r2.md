# Task Brief: AG-FE-DB-001-R2

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora dashboard widget runtime in execute-plans (registry/renderer/chart)
- Status: in_progress
- Owner: Claude
- Reviewer: Claude2
- Next: PR #2280 open with auto-merge; scatter size-encoding improvement committed (dddcc31b); waiting for CI and merge

## Summary
在 execute-plans 實作 Agora dashboard widget runtime(原 AG-FE-DB-001 phantom,未交付):src/agora/widgets/registry.ts(只註冊 design-closure A3 widget_registry.v1.json 的 active widgets)、WidgetRenderer.tsx、ChartSpecRenderer.tsx(ChartSpec 依 A3 grammar 對應 Recharts/ECharts)。package.json 加 echarts/echarts-for-react/react-grid-layout(+@types)。前後端 registry checksum 一致。 【UI 嚴格照設計稿】依 SD §9-§12/§23 IA + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure A3 widget_registry/chart grammar + contract-closure/05 page composition + design-closure-round2(v1.3 workshop_card/trading/research schema)實作;ChartSpec 對應 Recharts(metric/line/area/bar)+ ECharts(heatmap/network/sankey/candlestick/gauge);資料一律走 BFF client(src/lib/bff-v1/agora/*),頁面禁止直接 fetch;agent 產出只能是宣告式 spec,前端不得 eval 任意 code。設計稿沒涵蓋先 STOP 問。 【硬性交付規則 — 防 phantom】此為 execute-plans(前端 repo)交付任務。產出物必須**真的 commit 到 execute-plans repo、push、開 PR 到 execute-plans dev**;在檔案真的出現在 execute-plans origin/dev (經 merged PR)之前,**絕對不可把任務標 done**。只在 worktree、只在本地、PR 未 merge,一律**不算完成**。若無法 checkout 或 push 到 execute-plans(例如認證/路徑問題),立刻 STOP 開 blocker、寫清楚錯誤,**不可假裝完成**。
