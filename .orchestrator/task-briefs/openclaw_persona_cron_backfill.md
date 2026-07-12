# OPENCLAW-PERSONA-CRON-BACKFILL

## 一句話
把**所有既有 persona**（17 個）的 4 個 OODA cron job 補齊註冊（現在只灌了 5/68），並釐清 live job 的 `sessionTarget` 為何顯示 `main` 而非 persona 自己。

## 背景（已由 OPENCLAW-LIVE-WIRING live 釘死）
- 修好 schedule schema/transport 後，backfill 用 **host docker-exec 進 gateway（全 scope）** 已證可行，但灌到一半被一個**並行的 `docker compose up --build`** 重建 gateway 容器打斷，只成功 5 個 job。
- 已驗證：cron.list 有 job、force-run → `cron.runs status ok`（persona cron 會 fire）。
- reconcile 工具已存在：`scripts/reconcile_persona_ooda_cron.py`（idempotent，會 skip 既有）。
- **待查疑點**：live 上那 5 個 job 的 `sessionTarget=main`，但 registrar/backfill code 設的是 persona 自己的 agent id（`persona-<id>`，單元測試 `test_session_target_defaults_to_persona_own_agent` 有覆蓋）。live 值對不上，需釐清是哪個路徑寫的、以及正確值該是什麼。

## 要做什麼
1. **釐清 sessionTarget**：確認 persona OODA job 的 `sessionTarget` 正確值（persona 自己 vs main-orchestrator）。若該是 persona 自己，查那 5 個 live job 為何是 main（是否被別的路徑/舊 default 寫的），必要時清掉重灌。**先定義正確語意再補灌**，別把錯的放大 17 倍。
2. **補完 backfill**：對 17 個既有 persona 跑 `reconcile_persona_ooda_cron.py`（或等 OPENCLAW-CRON-WRITE-SCOPE 完成後走 BFF/adapter 路徑），確認 `cron.list total` 到位（17×4=68，扣掉合理 skip）。跑在**穩定**的環境（先確認沒有並行 compose-up 在 churn）。
3. **收尾自癒**：確保新建 persona 走 creation-time 自動註冊（依賴 OPENCLAW-CRON-WRITE-SCOPE），既有的靠 reconcile。考慮把 reconcile 掛成低頻自癒（**不得**動既有 supervisor cadence；如需排程要獨立且說明）。

## 驗收（live）
1. `cron.list total` == 既有 persona 數 × 4（扣掉已存在的 skip），附前後計數。
2. 每個 job 的 `sessionTarget` == 已定義的正確值（附抽樣）。
3. 對 ≥2 個不同 persona 各 force-run 一個 job → `cron.runs status ok`。
4. 重跑 reconcile 為 no-op（idempotent，全 skip）。

## 禁止
- 環境不穩（有 compose-up 在 churn）時不要灌，會被中途重建打斷。
- 禁止造假 cron 紀錄。
- 禁止動 supervisor poll/sleep cadence。

## 相關檔
- `scripts/reconcile_persona_ooda_cron.py`
- `services/control-plane/cron/persona_cron_registrar.py`（`reconcile_personas`、`_register_one` 的 sessionTarget）
- 前置：OPENCLAW-CRON-WRITE-SCOPE（若走 BFF/adapter 路徑）
- 註：dev 上曾出現不明 `docker compose up -d --build` loop（pid 3566329 附近），先確認來源並停掉再灌
