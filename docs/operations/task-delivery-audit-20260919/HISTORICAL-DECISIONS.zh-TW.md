# Claude 歷史決議與目前任務資料對帳（2026-09-19）

本文件前半保留 2026-09-19 14:05 UTC 的歷史查核；該次查核為唯讀。其後 operator 已要求落實資料修正與派工，結果見本文末節及 [目前清單](CURRENT-DELIVERY.zh-TW.md)。

## 結論

兩項都已有歷史判斷，但處理程度不同：架構拆分的撤銷及責任移交已正式落地，漏清下游依賴；TJ 原交付已在七月完成，九月重新匯入的舊待辦尚未完成資料對帳。不能把它們描述成一批尚待開發的產品功能，也不能把「已有處置結論」寫成「執行資料已修復」。

## 一、架構拆分：撤銷已執行，7 條舊依賴沒有清掉

### 決議及正式紀錄

1. 9/16 16:01:08、16:02:06 UTC：journal sequence 14470、14472，7 張既有任務被加上 `ARCH-MAIN-REMAINING-DOMAINS-001` 前置。
2. 9/17：Claude 重新查證後撤回按領域搬遷 main.py 的計畫。B02/B04/B11 不修改 main.py 即完成；六個領域的消費端彼此重疊；112 個模組級容器只有 7 個直接在執行期被改寫。原大規模搬遷缺乏必要性。
3. 9/17 02:12:37 UTC：sequence 15049 正式將 `ARCH-MAIN-REMAINING-DOMAINS-001` 設為 `done / superseded`；其餘六個領域任務也已撤銷。
4. 修正文件 [PR #5868](https://github.com/ajoe734/pantheon/pull/5868) 已合併，merge `27e49d598391a5a697e1ee446079bbcf0a702519`。本次重新向 GitHub 確認為 MERGED。
5. 起初另建的 `BFF-AUDIT-ADMISSION-IDEMPOTENCY-BYPASS-CORRECTIVE-001`，後來查明與既有 `DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001` 驗收第 2 條及 artifacts 重複。9/17 02:49:13 UTC，sequence 15146 也正式 superseded，責任併回 DOMAIN-WRITERS。Claude 02:52:46 的回覆明確說明這項合併，journal 亦保有完整交接 note。

真正保留的工作：三條仍使用 process-local idempotency 的 CommandStore 旁路，以及已確認無用的死碼。由既有 DOMAIN-WRITERS 任務接續，沒有第二個 owner。SSE buffer 明確延後評估，不能重新包回整批搬遷。

### 尚未同步的 7 張任務

- `BFF-ROUTER-USECASE-CORRECTIVE-001`
- `DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001`
- `STRUCT-RETIRE-001`
- `SIMPLIFY-BFF-RESIDUAL-001`
- `JOURNAL-RUNTIME-CONTRACT-CORRECTIVE-001`
- `BFF-READ-OWNER-WIRING-CORRECTIVE-001`
- `LOOP-RECEIPT-INTEGRATION-CORRECTIVE-001`

完整 journal 中，這 7 條前置最後一次修改就是 9/16 的加入；9/17 撤銷後沒有刪除或改接。最新讀回仍全部存在。因此「撤銷決議已落地」成立，「下游資料已整理完」不成立。DOMAIN-WRITERS 自己也等著這張已撤銷祖先，尤其不能把替代鏈機械改接回 DOMAIN-WRITERS，否則會產生自依賴或循環。

正確修復範圍：沿既有 `dependency-contract` 移除這 7 條過期 ARCH 邊，保留其他真實前置、驗收及 writer 順序。這不代表 7 張產品任務已完成，亦不保證立即全部可派；CB06、測試總驗收及其餘產品整合依賴仍需完成。不應重新建立或復活已撤銷的架構任務。

## 二、TJ：歷史交付已完成，重新匯入的舊 row 尚未對帳

### 原始完成證據

`TJ-E2E-001`～`011` 的 11 份不可變歸檔全部存在，均為 `done / completed`，日期落在 7/11～7/12。每份 archive 記錄的 Pantheon delivery commit 都已驗證是本機 `refs/remotes/origin/dev` 的祖先。這只證明歷史交付沿革，不能代替今天的 hosted 驗收或逐項重新資格審查。

| 前置 | 歸檔日期 UTC | 歷史交付紀錄 |
| --- | --- | --- |
| TJ-E2E-001 | 7/11 | PR #3295；producer/correlation inventory |
| TJ-E2E-002 | 7/11 | commit `08ab63239d661ba3854d10579e1e1a6bf9eff9e5`；correlation envelope contract |
| TJ-E2E-003 | 7/12 | PR #3328；broker correlation propagation |
| TJ-E2E-004 | 7/12 | PR #3408；journey materializer / reverse index |
| TJ-E2E-005 | 7/12 | PR #3411；journeys read API |
| TJ-E2E-006 | 7/12 | execute-plans #269、Pantheon #3450；frontend delivery |
| TJ-E2E-007 | 7/12 | Pantheon #3454/#3456、execute-plans #279；SSE attention |
| TJ-E2E-008 | 7/12 | PR #3452；governed journey actions |
| TJ-E2E-009 | 7/12 | execute-plans #281、Pantheon #3471；frontend acceptance |
| TJ-E2E-010 | 7/12 | PR #3420；replay/backfill |
| TJ-E2E-011 | 7/12 | PR #3460/#3466；SLO and failure drills |

更關鍵的是，`TJ-E2E-012` 自己也已完成：

- 原 owner **Claude2**、reviewer **Codex**。
- Human/Ops verdict 由 [PR #4011](https://github.com/ajoe734/pantheon/pull/4011) 記錄；後續文字對齊由 [PR #4015](https://github.com/ajoe734/pantheon/pull/4015) 完成。
- 最終結案 [PR #4021](https://github.com/ajoe734/pantheon/pull/4021) 於 7/24 01:00:11 UTC 合併，merge `3985daeaddcfd8954da8b82609e7a5ac5972b786`。
- archive 於 7/24 01:04:09 UTC 記錄 completed；結案範圍是當時確切 FE/BFF pair 的 read-only hosted rollout，不包含今天的新部署或 live 資金操作。

以上三個 PR 本次皆重新向 GitHub 確認為 MERGED。

### 九月如何重新變成待辦

本機 journal 第 1 筆 `genesis-migration-20260902-host-rebuild`，於 9/2 10:06:05 UTC 直接匯入一張七月早期版本的 TJ-E2E-012 todo：缺少後來的 acceptance addendum 與完成紀錄。後續 generation 2～12 的變化全部來自 supervisor 因 provider auth/quota 所作的改派；未找到新增產品驗收範圍或重新開發決議。現在是 generation 12、todo；001～012 的 terminal facts 全部缺失。

這支持 Claude 的判定：是重建 task store 帶回舊 row，並非 11 個功能沒做、也不是 012 被正式指派重新做一次。

### Claude 已查明，但沒有修復完

- 9/14 13:48：已發現 012 七月就已合併及取得 verdict。
- 9/14：曾嘗試結案路徑；後來更正自己用了舊 checkout 的判斷，確認 promoted runtime 有既有 recovery 機制，不能宣稱缺工具。
- **9/18 00:21**：看到 `assign` 回「already terminal/archived」，一度推測有人已補回 terminal fact。
- **9/18 00:24**：立即更正，明確讀回「terminal fact: None / live row: todo / 11 個前置事實 0 筆」，確認尚未修復。
- Claude 的記憶 `tj-e2e-012-archive-live-contradiction.md` 同樣保留了這個未解結論。因此不能引用較早「已 terminal」那則，忽略它後來的撤回。

### 可沿用的修復方式與限制

Claude 已用既有 TaskStore 的 audited drain 解決相同類型的 PPL-ALLOC-007。journal sequence 16289（9/17 12:39:43 UTC）可查：同一筆 transaction 移除錯誤復活的 active row，補回原 generation 1、原時間的歷史 terminal fact；七月 archive 保持不變。

TJ 的修復應先完整確定舊 row 沒有新的交付責任，再以既有 TaskStore 正式交易對帳，不應另造 queue、proof 平台或重新開產品實作。也不能照 Claude 記憶末段「先補 11 個前置、再處理 parent」機械執行：現行 collision runbook 明確指出 fact-first 會讓舊 parent 被錯誤派出；應先確保 stale parent 不再具派工資格，再補入經核實的歷史紀錄。原始歸檔與歷史審查不得改寫，新環境驗收不得借用七月的成功結果。

## 查核後已執行的修正

Operator 於同日要求完成文件交付，並最大程度派給 AGY 執行、Codex 審核。

- 既有 `dependency-contract` transaction **21256** 已移除上述 7 條 ARCH 邊；scope、acceptance 及其他前置不變。
- transaction **21258** 解除 OSS-EXECUTION-BASELINE-001 對 STRUCT-RETIRE-001 的非必要等待。兩者 source artifacts 不重疊，前者驗收不消費 BFF 退役輸出；保留 OSS-COVERAGE-PLAN、模擬驗證、paper/live 邊界及下游整合驗收。
- 既有 TaskStore audited-drain transaction **21262** 原子移除 TJ-E2E-012 錯誤復活的 active row，恢復 001～012 共 12 筆歷史 completed facts，保留原 generation 1 與七月歸檔時間。十二份 archive 逐一 SHA-256 讀回完全不變，沒有先放行舊 parent、也沒有冒充新的 hosted 驗收。
- 所有正式交易前均確認 affected tasks 沒有活躍 worker、待啟動 intent 或衝突 lease；TJ 的 active row 另以完整 CAS digest 綁定。不是手改 canonical JSON 或 queue。

正式 transaction ID、request digest、archive digest、恢復的歷史 facts、GitHub merge 讀回及派工快照保留在 [evidence.json](evidence.json)。原始 Claude 對話沒有複製進 repository；本文已將其結論與更正逐一對上 journal、immutable archive 和 GitHub。本文不是另一套 task authority。
