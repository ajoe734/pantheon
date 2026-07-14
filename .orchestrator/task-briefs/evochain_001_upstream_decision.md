# EVOCHAIN-001 上游資料裁決（2026-07-14，Human/Ops 查證）

給 EVOCHAIN-001 owner/reviewer：producer 的 fail-closed 行為**正確，不要再為
「0 incident」空轉 review**。0 candidate 的根因在上游資料，逐層查證如下。

## 查證事實（live，2026-07-14T03:00Z 前後）

1. `GET telemetry:8083/api/telemetry/runtime-summaries` 回 14 筆 paper summary：
   - **全部 14 筆 `drawdown=None`**。投影層（`runtime_summary.py`）支援
     `drawdown`/`drawdown_pct` 映射，但**從來沒有任何 telemetry event 帶過
     drawdown**——paper runtime / loop 端沒有任何東西在算 drawdown。
   - **全部 14 筆 `pnl=0.0`**，其中 rb-31bd3cf07cc 有 `total_trades=7325`。
     幾千筆成交 PnL 恰好 0.0，PnL 供給鏈本身可疑（fills 沒有 mark-to-market，
     或 pnl event 恆報 0）。
2. `threshold_sweep_baselines.json` **設計上 ships empty**：
   `rolling_drawdown_multiple` 需要 per-artifact `expected_drawdown` baseline，
   沒有 baseline 就 fail-closed。即使 drawdown 欄位補上，這條 threshold 仍不會
   fire，直到 governance 核一個 baseline 進去。
3. `rolling_pnl_floor`（用現有 `pnl` 欄位）目前 `enabled: false`，且因 pnl
   全 0，啟用也不會 fire。

## 裁決

- **EVOCHAIN-001 的驗收邊界維持現狀**：producer 對「資料存在時」的行為
  （evaluate/dedupe/fail-closed/admit telemetry event）以測試與 tick 診斷證明
  即可收斂。**不要把「上游沒有 drawdown 資料」算在 001 頭上**，也不要在 001
  裡動 telemetry/runtime 的 code（scope 外）。
- 真資料閉環的缺口屬於**新的上游任務**（paper runtime/projector 計算並發出
  `pnl_snapshot`/`drawdown_snapshot` events + 修 pnl 恆 0 + 核一個
  `expected_drawdown` baseline），由 Human/Ops 決定是否加派；001 不需等它。
- 鏈路的機械證明（注入 breach → incident → sweep → journal）由 EVOCHAIN-010
  verifier 負責，001 不需自證 end-to-end。

## 給 EVOCHAIN-010 的提示

verifier 注入 breach 時，請走 producer 的正式入口（合成一筆含 `drawdown` 的
summary 或直接 POST threshold-breach payload 到 incidents consumer），並斷言
dedupe key 防重放；不要繞過 producer 直接寫 incident store。
