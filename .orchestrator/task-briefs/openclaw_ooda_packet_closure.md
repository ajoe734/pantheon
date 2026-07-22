# OPENCLAW-OODA-PACKET-CLOSURE

## 一句話
把「cron 喚醒 persona agent 跑一輪」與「持久化一筆 OODA packet / 業務成果」**接起來** —— 目前這兩者完全沒接，cron run 會 `status ok` 但不產出任何 packet。

## 背景 / 根因（已由 OPENCLAW-LIVE-WIRING 查證）
- OPENCLAW-LIVE-WIRING 修好了 cron **wiring**：persona 註冊上 OpenClaw cron、`cron.run` → `status ok`、agent 有被喚醒。
- 但 live 探測顯示：cron run 完成後 agent `finalAssistantVisibleText` 為空、`/bff/ooda/packets` 前後不變 —— **沒有任何 code 把 cron-driven 的 persona turn 變成一筆持久化 OODA packet**。
- 查證：OODA packet 是 `services/persona/ooda_cycle_runtime.py`（`run_management_persona_ooda_cycles` / `_build_closed_cycle_packet`）產生的，但那是一個**靜態 AlphaSeedSource 餵的 batch runtime**，input 是寫死的 seed refs，**完全沒讀 gateway cron runs / 沒接 cron dispatch**。兩套機制各跑各的。
- 這是**設計缺口（沒建）**，不是 bug；對上 [[pantheon_dev_loop_not_closed_2026-06-14]]、[[pantheon_dev_no_real_strategies_2026-06-14]]。

## 要做什麼（需先做設計決策）
1. **設計**：定義 cron systemEvent（`{kind:pantheon.workflow.dispatch, persona_id, workflow_id, upstream_entrypoint, ...}`）被 persona agent 收到後，該產生什麼、由誰持久化成 OODA packet：
   - 選項 A：persona agent 具「寫回 Pantheon」的 tool（telemetry / packet endpoint），turn 內主動落一筆帶真實指紋（trace_id / 上游時間戳）的 packet。
   - 選項 B：Pantheon 側監看 gateway cron runs（`cron.runs`）→ 取 agent 輸出 → 由 `ooda_cycle_runtime` 之外的閉環寫 packet。
   - 選項 C：`upstream_entrypoint`（如 `deployment.plan`）實際觸發一個 gateway workflow handler，其輸出即 packet 來源。
   - 先評估三者、選一個，寫在 PR 說明為何。**禁止**只把 `ooda_cycle_runtime` 的靜態 seed 假裝成 cron 產物（讀時合成冒充 producer）。
2. **實作**：接上選定路徑，讓 cron run 的 persona turn 真的落一筆 packet。
3. **guardrails**：persona SOUL 已含 paper-only 護欄；packet 要標明 paper/live 與資料來源；沒有真實輸入時誠實標記，不要 fabricate。

## 驗收（唯一標準：live，非 mock/seed）
1. 建立/取一個 persona，force-run 其 OODA cron job → **`/bff/ooda/packets?persona_id=...` 計數 +1**，且該 packet 帶**真實 producer 指紋**（cron runId / trace_id / 上游時間戳），非 fixture、非讀時合成。
2. 附證據鏈：cron.run 指令 → cron.runs status → 新 packet 的指紋對得上該 run。
3. 既有測試綠；新增一個會真的驗「cron→packet」閉環的 live smoke。

## 禁止
- 禁止用靜態 AlphaSeedSource / 讀時合成冒充 cron 產出。
- 禁止只改 mock 測試收工。
- 禁止動 supervisor poll/sleep cadence。

## 相關檔
- `services/persona/ooda_cycle_runtime.py`（現有 batch packet 產生器）
- `integrations/openclaw/adapter/cron_transport.py`、`services/control-plane/cron/persona_cron_registrar.py`（cron dispatch 端 systemEvent）
- `services/control-plane/bff/*ooda*`（packet 讀面 / store）
- persona SOUL：`integrations/openclaw/persona_agent_sync.py::build_persona_soul`
- 前置：OPENCLAW-PERSONA-CRON-BACKFILL（要有 job 在跑才驗得了）
