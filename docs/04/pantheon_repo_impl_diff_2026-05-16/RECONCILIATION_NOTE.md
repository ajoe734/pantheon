# 報告與實況校準筆記 (RECONCILIATION_NOTE)

> 撰寫於：2026-05-16 (歸檔當下)
> 目的：把 `REPORT.md` 的盤點結論跟「歸檔當下實際 repo 狀態」做差異註記，避免後續讀者把 REPORT 當成 ground truth

REPORT.md 本身是 v3.0 重新盤點報告。但它寫作時間是 2026-05-16 早段，當天稍後 Sprint `2026-05-16-pantheon-bff-p0-foundation` 又跑掉很多 task。這份 NOTE 是事後校準：

## 1. REPORT 認為「仍 pending」但實際**已歸檔**的任務

| REPORT 列為缺口 | 實際 archive |
|---|---|
| SRC-001..005 | SRC-001 / 002 / 003 / 004 / 005 ✓ 全部 archived |
| STRAT-001..004 | STRAT-001..004 ✓ archived |
| TRN-005..007 (REPORT 預期新增) | TRN-001..004 ✓ archived（REPORT 本身已認知）；005..007 為 REPORT 預期，尚未派工 |
| EXP-002 / EXP-003 / VBT-001 | EXP-001/002/005 ✓ + VBT-001 ✓ archived；EXP-003、EXP-004 未派 |
| QLIB-001 | QLIB-001 ✓ archived；QLIB-002、STAT-001 未派 |
| IMT-005..008 | IMT-001..004 ✓（REPORT 已認知）；005..008 未派 |
| GOV-001 / DEP-001..003 | GOV-001 ✓、DEP-001 ✓、DEP-002 ✓、DEP-003 ✓；GOV-002/003、DEP-004 未派 |
| RT-001..003 / EX-002 / EX-003 | RT-001 ✓、RT-002 ✓、RT-003 ✓、RT-004 ✓、EX-002-RB ✓、EX-003 ✓ 全部 archived |
| TEL-001 / TEL-002 / AUD-* / ALT-001 / INC-001 / POST-001 / REC-001 | TEL-001 ✓、TEL-002-RB ✓、AUD-002 ✓、ALT-001 ✓、INC-001-RB ✓、POST-001 ✓、REC-001 ✓ |
| EVO-001 / LOOP-001 / SENT-001 | EVO-001 ✓、LOOP-001-RB ✓、SENT-001 ✓ |
| ASK-001..005 | 全部 ✓ archived（REPORT 已認知） |

**統計**：REPORT 列出的 60+ 個「需排入開發」任務中，約 **52 個在歸檔當下已 archived**。

## 2. 真正仍未完成的部分

對照 sprint objective（6 EPIC / 59 task）後，目前 archive 中**未見**的 ID 有：

| ID | EPIC | 說明 | 推測原因 |
|---|---|---|---|
| GOV-002 | EPIC-GOV-DEPLOY | approval decide endpoint | 可能併入 GOV-001 範圍實作 |
| GOV-003 | EPIC-GOV-DEPLOY | canonical action endpoint | 可能由 P0-ACT-001 涵蓋 |
| DEP-004 | EPIC-GOV-DEPLOY | pool/runtime compatibility checks | 未派工 / 順延 |
| TEL-003 | EPIC-TELEMETRY | `/bff/alerts` endpoint | 可能由 ALT-001 涵蓋 |
| TEL-004 | EPIC-TELEMETRY | `/bff/incidents` endpoint | 可能由 INC-001-RB 涵蓋 |
| TEL-005 | EPIC-TELEMETRY | runtime heartbeat ingest | 未派工 / 順延 |
| AUD-001 | EPIC-TELEMETRY | audit backend | 已存在 AUD-CLAUDE/CODEX/GEMINI/GROK-001 + AUD-002，可能 lane 分派 |
| EXP-003 / EXP-004 | EPIC-RESEARCH | Qlib adapter / vectorbt adapter | EXP-005 已 archived；EXP-003/004 順延或合併 |
| QLIB-002 / STAT-001 | EPIC-RESEARCH | rolling pipeline / statsmodels adapter | 順延到下一輪 |
| MGMT-BROKER-002 | Track E carry-over | Shioaji credentials | 仍 blocked，等人提供 API_KEY/SECRET_KEY |

## 3. 對 REPORT 結論的修正

- REPORT § 5 「P0 重新驗證」的前提（5/09 probe 已過時）**仍正確**，但 REPORT 寫作後當天，Sprint `2026-05-16-pantheon-bff-p0-foundation` 已執行完絕大部分 P0-* 任務（P0-BFF-001..004、P0-ACT-001、P0-APP-001、P0-REG-001、P0-PER-001、P0-CAP-001、P0-AUD-001 全部 archived）
- REPORT § 5.2 P1（Governance → Runtime）任務也大致 archive 完成
- REPORT § 5.3 P2（Telemetry / Evolution）僅 TEL-003/004/005 等三個還沒明確派工，其他都 archived
- REPORT § 5.4 P3（Research / Learning）的 SRC / STRAT / TRN / IMT / ASK / EXP 都 archive 完成；剩下的是 P3 後段的 OSS adapter（VBT、QLIB、STAT、QuantLib、RL）和 Imitation 訓練本體（IMT-005..008）

## 4. 對「下一階段實作項目」的影響

讀 REPORT 時要把上述 1-3 套用上去。**REPORT 列出的優先級清單裡，P0/P1/P2 大致已收，P3 仍是主要長尾**。具體還需開發的項目見 `MEMORY-PANTHEON-NEXT-DEV-ITEMS-2026-05-16` 整理或要求另起一份清單。

## 5. 為什麼還是要留 REPORT

雖然細節落後實況 8 小時左右，REPORT 仍有保存價值：

1. 它是 sprint `2026-05-16-pantheon-bff-p0-foundation` 開跑前的最後一張 GAP 快照
2. 它正確識別了 LEAN runtime / broker credentials / loader migration 這些**長尾結構性 GAP**，這些 GAP 不會在 8 小時 sprint 衝刺中被解掉
3. 它指出的 OSS adapter（Qlib、vectorbt、statsmodels、QuantLib、RL Lab）這類 Track 仍是合理的 next sprint 候選

## 6. 文件分層

- L0/L1 canonical 不變
- 本 REPORT.md 為 L3 supporting analysis；不覆蓋 L1
- 與 `docs/04/pantheon_sa_supplemental_2026-05-15/GAP_dev_team_master_rebaseline_2026-05-15.md` 互補：GAP 文件是 sprint 啟動依據，本 REPORT 是 sprint 中段的差異盤點
