# 05 查證證據、測試結果與完整驗收界線

版本：2026-09-11 交付版（V2）  
狀態：**依據批准規畫與實地查證展開之證據與驗收階梯規範**。

---

## 1. 實地查證事實與證據摘要

所有查證作業均於隔離工作區進行，查證時間標註為 **2026-09-11 00:56 UTC 至 02:34 UTC**。

| 查證項目 | 實證事實 | 查證方法與限制說明 |
|---|---|---|
| BE / FE 基準身份 | BE dev `e3ef0a4b6c8500f19019196e67fcbd5df6df67dc`；FE dev `d0f2186c12355cb4aa7179f14069c7e627b8e840`。 | fresh fetch 驗證；dev-root checkout 與 supervisor command runtime 維持 `ba6c9e99...`；不把工作樹當成已升版之最新 dev。 |
| 16 個核心原始碼 Blob | 兩版本間之 16 個 production 檔案完全相等（SHA-1 一致）。 | 詳見 01 號文件；確認可安全閱讀不可變 source，不冒用未提交的工作樹。 |
| 5 個 U2 精確測試檔案 Blob | `test_p0_tw_paper_activate_honesty.py` (`bde626...`)、`test_loop_auto_bff004_cross_loop_drill.py` (`1db668...`)、`test_bff_promotion_review_governance.py` (`243e3b...`)、`test_pathreon_market_persona_fleet_contract.py` (`fa54f5...`)、`test_srclive_overlay_contract.py` (`07b58b...`)。 | 經查證全數直接位於 `services/control-plane/bff/` 根目錄，非 `tests/` 子目錄。 |
| Canonical 任務現況 | 11 個 blocked 任務逐一核實，包括 generation、owner、waiting_for。 | 透過 live config 指定之 V2 事件日誌以正式 command show 讀取。 |
| 現況與擴充 DAG | 現況 207 節點；擴充規畫為 221 節點（DAG、無環）；64 個直接下游依賴組合全數證實造環。 | 以 TopologicalSorter 靜態拓撲排序驗證無循環。 |
| PR 精確狀態 | 8 個測試批次 PR 仍 OPEN；OPS 與 B09 已 merge 且 canonical done；外部 review 狀態在部分 PR 顯示 FAILURE。 | 清楚分離 merged、canonical done 與當前 checks 三種不同事實。 |
| 機制 AST 盤點 | 26 個具名機制定義、65 個具名呼叫者、20 個 persist 候選、124 個 idempotency 賦值。 | 有界 AST 分析；作為呼叫端清查候選，不等於已證實之動態 reachability。 |
| Architecture 測試重跑 | **8 passed in 1.52s, exit code 0**。 | 於隔離環境重跑核心架構測試，見下節。 |

### 1.1 Architecture 測試執行紀錄

於隔離 probe 環境下重跑不可變架構測試套件：

```bash
pytest -q -p no:cacheprovider services/control-plane/bff/tests/test_bff_test_architecture.py
```
- **結果**：8 passed in 1.52s, exit code 0。
- **邊界宣告**：本測試僅證明架構規則與語法結構無語法錯誤，未跑完 61 個測試檔案，不代表全系統測試已通過。

### 1.2 隔離動態探針重現事實

重用新建 TemporaryDirectory、無外網、未 import `bff.main` 之隔離探針環境：

1. **Persona Semantic Action**：真 service 調用重現 `NameError: _scoped_idempotency_cache_key`，證實缺少模組全域依賴。
2. **Alert Acknowledge**：調用真 async endpoint，回傳 status 202 及 submitted，但底層持久化紀錄數為 0。
3. **Governance Action**：在缺少 admission dependency 情況下仍回傳 accepted 及虛構 command ID。
4. **Auth Token 解析**：未注入 guard 之 service 可接受非正式 token 格式，證實依賴 factory 注入之脆弱性。
5. **CommandStore 實例快照**：已快取之實例無法感知其他實例之新寫入，程序內鎖無法跨實例保護。

---

## 2. 歷史工作紀錄之沿用與取代界線

歷史工作紀錄（包括 2026-09-10 解卡紀錄與 2026-09-11 第一版草案及相關 probes/inventories）：
- **本 V2 全面取代歷史方案**：歷史文件僅保留作為不可變之歷史證據，不作為持久依賴，亦不得形成兩套競爭方案。
- **61 檔測試清冊（795 個 AST `test_*` 函式）**：此為原始覆蓋範圍清單，**795 是 AST 函式數量，非 passed 數量**。後續任務驗收必須以真實執行之 exit code、pass/fail/skip 為準，嚴禁以 skip、fixture fake success 或刪減 assertion 冒充通過。

---

## 3. 獨立複核後已修正的規畫錯誤清單

1. **Command 旁路收斂範圍**：U3 將 Persona、Agora、Tools、Governance、Incidents 全數納入，不限於 main/router/service。
2. **Replay Scope 規範**：固定為 `tenant + actor + canonical namespace + key`；更換 target 回傳 409。
3. **讀取權限分離**：區分 replay 擁有者與 role/tenant 讀取授權，合法 reviewer 讀取他人 command 不得被誤拒。
4. **Evolution 狀態模型**：撤回簡化三狀態，全面對齊現役 FE 之 6 program 狀態與獨立 run 狀態。
5. **PATCH 控制邊界**：PATCH 限於 metadata，不可繞過生命週期政策直接變更 status。
6. **Outbox 職責分離**：僅 approved-decision 沿用 dispatch outbox，metadata CRUD 走 aggregate 交易。
7. **Jobs 來源完整性**：撤回 gateway-only 與刪除未實作操作之建議；六類來源明列，未實作者列為未完成義務。
8. **U8B 納入共享檔案**：確認 U8B 亦會修改 main、adapter、registry、catalog，排定於 U10A 之後。
9. **工具能力核實**：確認本輪採用正式 `dependency-contract` 原子更新，無 `artifact_conflict_guard` 阻礙，不需擴充排程工具。
10. **U2 檔案路徑校正**：五個精確測試檔案位於 BFF 根目錄，非 `tests/` 子目錄。

---

## 4. 上線前不可省略的十層驗收階梯

| 驗收層級 | 必須提出之具體證明 | 絕對不接受之替代證據 |
|---|---|---|
| 1. 結構與單一實作 | owner / body / caller 清冊完整閉合；main 僅負責組裝；同一 flow 只有一個 state owner；registry 每 tuple 恰一 match。 | 只搬移檔案留下 forwarding wrapper；只用 rg 搜尋字串認定動態路徑消失。 |
| 2. 舊碼與舊 API 清理 | 程式碼、imports、DI、OpenAPI 宣告、URL producer、前端 client/mock 同 commit 清除；無現役舊 POST 呼叫。 | 保留 compatibility flag、雙軌 backend、或刪除歷史 audit 資料。 |
| 3. 安全與授權驗證 | 缺少依賴 fail closed；tenant/role/record 可見性、MFA/approval、token 綁定；跨 scope 負向與合法 reviewer 正向。 | dummy operator、fallback 寬鬆解析、TypeError 後降級重試、關閉 auth。 |
| 4. 命令受理與交易 | replay/hash、confirm consume、durable record、receipt、dispatch 一致；20 並發、race、storage fault、重啟測試通過。 | 僅有 202 或 UUID 即宣稱 executed；只測單一實例記憶體狀態。 |
| 5. Program/Research 持久化 | 真實 DB transaction、同 owner 讀回、revision / idempotency / tenant / ticket-link 一致性。 | 向 read port 呼叫 write、fixture 假 ID、重複提交產生重複紀錄。 |
| 6. 實際 Domain 操作 | 每個保留 action 具備真實 owner、批准、狀態轉移、執行 receipt、readback；取消能真正停止 worker；重試有 attempt lineage。 | 只改 row 狀態、前端 local state 假更新、以 503 測試替換原成功案例宣稱完整。 |
| 7. 原批次與全量回歸 | 61 檔測試逐案對照；U2 五檔重驗；NL/domain/FE 新增覆蓋；parent 全部 dependencies 完成後全量通過。 | 僅執行 8 個架構測試；降低 assertion；跳過紅燈；以 795 AST 函式數當 passed。 |
| 8. FE/BE 契約配對 | 兩倉庫 exact commit SHA 配對；四個 Persona 動作、confirmation expiry/receipt、Evolution/Jobs 真實 ID 與錯誤呈現。 | 僅後端測試通過、前端仍呼叫被刪 API、以新 commit 冒充已部署實例。 |
| 9. 發布與回復協定 | 現役 exact-pair 之 prepare / qualify / activate / restore 流程；gate-before-switch、lease delegation、whole-pair rollback。 | 第二個 release controller、僅 restore 前端宣稱後端復原、假 heartbeat 證據。 |
| 10. 全系統閉環驗收 | 每個 loop 清楚列出 trigger → owner → durable outcome → readback → UI → failure/recovery 證據；真 hosted pair 可追溯。 | supervisor 正常、task 派發完畢、source merge、單一網頁可開啟。 |

---

## 5. 限制與本文件不宣稱事項

- **本交付為文件與執行契約交付**：本任務 `BFF-UPSTREAM-V2-PLAN-DELIVERY-001` 僅負責在版本庫中交付批准之 V2 規畫與 dispatch map，使所有後續 worker 有唯一合約可循。
- **無產品程式修改**：本交付未修改產品 runtime 程式碼。
- **不宣稱線上產品已驗收**：本文件不宣稱 61 個測試檔案已全數綠燈，不宣稱 hosted 環境已驗收，亦不宣稱全循環已閉環。各階段成果應按上述十層驗收階梯由各負責任務逐步取得證據並交付。
