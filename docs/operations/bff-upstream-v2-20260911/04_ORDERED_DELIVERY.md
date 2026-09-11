# 04 單一 Writer、正式 Contract 與有序交付 DAG

版本：2026-09-11 交付版（V2）  
狀態：**已批准並落地之有序交付執行架構與 DAG 規範**。

---

## 1. 依賴圖完整性與 64 個循環反例分析

在正式派工前，對 Canonical TaskStore 完整狀態進行圖論分析：
- **現況圖**：包含 207 個 canonical 節點，無循環、無未解析之依賴葉節點。
- **計畫擴充圖**：加入 14 個規畫節點（13 個工作包標籤 ＋ 1 個驗收聚合點），結果為 **221 個節點、無循環（DAG）**。44 條新增依賴分為 12 條同檔案修改次序與 32 條業務語意/能力前置。
- **64 個循環反例證明**：針對 8 個測試批次（B02, B03, B04, B05, B06, B10, B14, B16）與 8 個既有下游 corrective 進行所有跨批次組合分析。實測證明：**若任一測試批次直接全盤依賴任一下游 corrective 整包任務，必將造成循環依賴（8 × 8 ＝ 64 個 counterexamples 全數造環）**。
- **解決方案**：不讓測試批次盲目依賴整個 downstream corrective，而是將共用上游修復精確抽離為 U1～U10B 的有序任務，下游 corrective 僅保留未交付之剩餘整合職責。

---

## 2. 嚴格單一 Writer 執行順序

為確保修改相同檔案時不發生並發衝突與工作樹污染，定義下列嚴格執行鏈：

```text
DOC (規畫文件交付)
 ├─ D-COMMAND (CommandStore契約)   → U3 之必要前置
 ├─ D-EVOLUTION (Evolution契約)    → U8A / U8B 之必要前置
 └─ D-JOBS (Research/Jobs契約)     → U10A / U10B 之必要前置

DOC
 → U1 (HTTP/Auth)
 → U2 (Persona projection)
 → U3 (Command/confirmation/audit 受理與所有同流程旁路)
 → U9 (舊 generic write API 後端退役)
 → U9-FE (舊 generic write API 前端改接)
 → U4 (Journal context 與 visibility)
 → U5 (Assistant source collectors)
 → U6 (Management NL 單一 use case)
 → U7 (Evolution review/journal 讀取)
 → U8A (Program aggregate 與 typed ports)
 → U10A (Research/Jobs owner 與讀取接線)
 → U8B (Evolution 真實操作後端)
 → U8B-FE (Evolution 真實操作前端)
 → U10B (Research/Jobs 真實操作後端)
 → U10B-FE (Research/Jobs 真實操作前端)
```

> **開工規則**：前一 writer 必須完成 accepted-head 驗證、PR merge 至 `dev`、canonical 狀態更新且 lease 完全釋放結算後，下一 writer 才能基於最新的 accepted `dev` 分支接續開工。

### 2.1 原 8 個測試批次之接續矩陣

測試批次不必等待整串實作全部結束。只要其專屬上游 source prerequisites 完成、原 dependencies 滿足且無檔案衝突，即可接續執行：

| 測試批次 | 既有 PR | 新增上游前置（原依賴全部保留） | 核心驗收重點與不可省略原則 |
|---|---|---|---|
| B02 | #5750 | U5、U10A | collector 抽離與真實 Job 資料分層驗收；不以假 fixture 取代。 |
| B03 | #5749 | U1、U3 | 保留已完成之 auth/session 成果，驗證真 JWKS 與 confirm lifecycle。 |
| B04 | #5755 | U1、U3、U6、U8A、U9-FE、U10A | 涵蓋 error/security、replay、program 並發與 API 契約遷移。 |
| B05 | #5746 | U2、U3、U4、U9-FE | 遷移五個跨批次檔案，真 journal/context/approval，不改測其他產品。 |
| B06 | #5747 | U3、U9-FE | 驗證 durable command → foundation → receipt → audit 完整鏈路。 |
| B10 | #5754 | U7、U8A、U10A、U8B-FE、U10B-FE | 包含 program pause/experiment/job cancel；metadata 不能代替真動作驗收。 |
| B14 | #5751 | U2、U3 | projection/honesty 與 intervention 完整保留。 |
| B16 | #5757 | U3、U9-FE、U10A、U8B-FE、U10B-FE | 驗證 receipt/confirmation/race/execution，不以 A 階段關閉 B 階段缺口。 |

---

## 3. 共享檔案 Writer Ledger

| 關鍵共享檔案／目錄範圍 | 本次 Writer 交付次序 | 既有下游／外部 Overlap 隔離處置 |
|---|---|---|
| `BFF/main.py` | U1 → U2 → U3 → U9 → U4 → U5 → U6 → U7 → U8A → U10A → U8B | 嚴格循序，每階段 PR merge 後釋放 lease 方可下一階段開工。 |
| `BFF/core/app_factory.py`、lifespan/security | U1 → U6 | SIMPLIFY-BFF-RESIDUAL 待上游完成後僅收尾剩餘結構。 |
| `BFF/personas/service.py`、router/ranking | U2 → U3 → U9 | U2 抽離 projection，U3 修復 semantic command，U9 退役舊 endpoint。 |
| `BFF/runtime/router.py` | U2 → U3 → U9 | 依精確檔案 grant 循序修改。 |
| `BFF/agora/service.py` 及 context consumer | U3 → U9 → U4 | U3 處理 command，U9 處理 API，U4 處理 context resolver。 |
| `BFF/governance/service.py`／router | U3 → U7（U9 在其間） | U3 接入唯一 admission，U7 修正 mutation-review 規則。 |
| `BFF/command_adapters/service.py`／router | U3 → U9 | U3 集中收斂，U9 退役舊 POST。 |
| `BFF/command_adapters/evolution_adapter.py`／registry | U8A → U10A → U8B → U10B | 每次新增 adapter 時同 commit 移除舊 handled 項目，確保 registry 唯一。 |
| `BFF/ports/read_surface_ports.py` | U8A → U10A | 僅定義唯讀 port，不在此加入 write 方法。 |
| `BFF/evolution/service.py`／router | U3 → U7 → U8A → U8B | 依序收斂 command、review、aggregate、真執行。 |
| `BFF/research/router.py`／experiments | U3 → U10A → U10B | U3 接入受理，U10A 綁定真實 owner，U10B 補齊操作。 |
| U2 的五個 `BFF/test_*.py` 檔案 | U2 原子遷移與重驗 | 遷移至 U2 範圍，已 done 之 B07/B15 保持 terminal 不重開。 |
| FE `writes.ts`、DTO、paths、PersonaDetail | U9-FE → U8B-FE → U10B-FE | 前端獨立倉庫內依序執行，不跨 repo 混寫。 |

---

## 4. 正式 Contract 工具能力與派工處置準則

本輪派工完全基於現行已驗證之工具能力，**不需要亦不允許擴充排程或資料庫工具**：

1. **`dependency-contract`**：支援 1～32 rows 原子交易更新，具備完整 row SHA CAS 比對、generation 遞增、無環檢查、既有 writer 排序檢查、以及 runtime lease/recovery fence 保護。
2. **`artifact-contract`**：單一 path 之稽核異動；存在 immutable `artifact_conflict_guard` 時嚴格拒絕。經查證本批相關 rows 均無此 guard，因此不需要亦未進行任何工具擴充。
3. **正式派工路徑**：採正式 local operator create-only/assign 與 `dependency-contract` 原子更新，絕非手簽半套 packet 再私下修改不可變之 `task_spec`。舊 signed bridge provenance 作為歷史來源妥善保存，修訂作業保留原簽章並於 audit log 完整留存。
4. **權威邊界說明**：本批任務為 source-functional（原始碼功能）任務，**絕非 hosted execution authorization（線上正式環境執行授權）**。

---

## 5. 逐包交付之標準作業程序（SOP）

所有承接本計畫任務之 auto-worker 必須遵循以下完成定義：

1. **工作樹檢查**：使用乾淨之 per-task branch（`task/<TASK-ID>`，由 `dev` 切出）；嚴禁於 dirty 或共用工作樹直接開發。
2. **中間狀態保護**：跨檔案改動達可描述中間狀態時，必須依 `.orchestrator/skills/worker-anchor-commit.md` 建立 anchor commit（格式：`<TASK-ID>: anchor <scope>`），註明 owned layer、not changing 與 composes with。
3. **單一提交與同步清理**：一律使用 `python3 scripts/git/worker_commit.py` 進行 narrow scope commit，包含所有消費端改動與舊程式碼同步刪除。嚴禁互動式 git 指令或全域 `git add .`。
4. **驗證要求**：執行 focused regression 與負向/並發/持久性測試；同檔案測試進行全檔驗證。真實記錄執行指令、exit code、pass/fail/skip 數，絕不得以 AST 函式數量冒充 passed 數。
5. **產品 PR 與獨立審查**：本批次為產品 runtime 修改，**不得套用 tooling 免 review 例外**。透過 `task_finalize.sh` 開啟 PR，由指定之獨立 reviewer 進行 review 與 `approve`。
6. **Supervisor 合併與結案**：待 supervisor 自動將 exact approved head 合併至 `dev` 後，確認工作樹乾淨，再由 owner 執行 `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh done` 正式收尾。
