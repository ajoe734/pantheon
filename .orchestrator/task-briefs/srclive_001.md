# Task Brief: SRCLIVE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: TW 官方源 live 啟用 (twse/tpex/mops)
- Status: todo
- Owner: Codex
- Reviewer: Claude
- Next: Helper-claimed by Codex while Claude is dispatch-paused.

## Summary
persona-tw-equity 目前 2/5 可讀(shioaji/finmind 綠),twse/tpex/mops 仍釘在 2026-05-01 離線 smoke。連接器 tw-twse-tpex-official-market(真 TWSE/TPEx OpenAPI,今日實測 HTTP 200)與 tw-mops-official-disclosures 皆已存在、已在 active_universe DEFAULT_SOURCE_UPDATE_RULES,BFF 對照表也已含 twse/tpex/mops。缺口純粹是 live dev 的 source-ingest 服務從沒實際跑過這兩個連接器→health-usage-snapshot 沒有它們→疊加層維持靜態。工作:(1) 在 source-ingest 服務上把這兩個連接器排程/觸發一輪真實 ingest run(twse 日價、tpex 日價、mops 公告),(2) 確認 snapshot 回報三者 status:ok 並有 last_success_at/row_count,(3) 產出可重跑的啟用 runbook(觸發指令+驗證 curl),(4) 若 dev source-ingest 服務未起或缺資料卷,寫清楚阻塞點交 orchestrator 處理。不改 BFF 對照(已正確)。

[設計規則] 唯讀疊加層 _overlay_source_health_truth 是真相來源:provider 翻 read_ok 的唯一合法路徑是(1) BFF _SOURCE_PROVIDER_CONNECTOR_CANDIDATES 有 provider_key→connector_id 對照,且(2) source-ingest /api/source-ingest/health-usage-snapshot 回報該 connector status:ok。嚴禁硬寫 read_ok 或假綠;沒有即時健康就誠實顯示 credential_unavailable / read_unavailable 並附 reason。
