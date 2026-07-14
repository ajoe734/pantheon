# PPL-ALLOC-012 — Quarterly ranking projection 曝露 stage / current-weight / evidence tuple

## 問題（來源：PPL-ALLOC-009 hosted 驗收 blocker，2026-07-13）

`PPL-ALLOC-009-HOSTED-EVIDENCE-2026-07-13.json` 記錄
`allocation_contract_probe.authoritative_ranking_join=false`：

hosted `GET /bff/management/quarterly-ranking`（及其 recommendations 投影）
沒有曝露 per-persona 的 `stage`、`current_weight`、evidence refs tuple，
導致無法「從單一 immutable ranking response 推導出 rebalance proposal」。
009 的 probe 只能自行拼 snapshot（`ppl-alloc-009-20260713T041416Z`），
違反規格「Rankings Center produces a reproducible ranking snapshot →
Governance Decisions creates a recommendation referencing that snapshot」
的治理鏈要求（MANAGEMENT_PERFORMANCE_RANKING_IA_GAP.md Governance Cycle）。

## 目標

1. quarterly ranking 投影（list + drilldown + recommendations）每個 persona row
   曝露：`stage`（paper_running/canary_running/live_running…）、
   `current_weight`（現行資本權重，paper 為 null/paper-ledger 標示）、
   `eligibility`/exclusion reasons、evidence refs、以及 immutable
   `ranking_snapshot_id`。
2. Rebalance proposal / allocation-policy evaluate 可引用該 snapshot id，
   使 proposal 的 current/target/delta 與 cap reasons 能對回同一份 ranking
   response（authoritative ranking join）。
3. 與 PPL-ALLOC-010 的原則一致：數值來自真實遙測/資本綁定讀模型，
   不得以市場 seed 冒充 per-persona 值；缺資料就明示 partial/缺，不造假。

## 邊界

- 只動 read model / 投影與其契約測試；不動 ranking 計分公式本身。
- 不動 supervisor/poll cadence。標準 git workflow。

## 驗收

- [ ] contract tests 覆蓋新欄位與 snapshot join，全綠。
- [ ] merge dev + dev 部署後 live curl：quarterly-ranking response 內含
      stage/current_weight/evidence/snapshot id；用該 snapshot 走
      allocation-policy evaluate → proposal create，欄位可對回。
- [ ] 證據歸檔 archive/，並在 PPL-ALLOC-009 標記 ranking-join blocker 已清。
