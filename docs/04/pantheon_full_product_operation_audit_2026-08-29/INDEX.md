# Pantheon 全產品運作 GAP 重整、系統分析、系統設計與平行執行 DAG — 2026-08-30

| 欄位 | 內容 |
|---|---|
| 文件狀態 | **全產品 GAP 盤點處置、目標 SA/SD 與平行執行 DAG 凍結基準** |
| 規劃依據 | `docs/04/pantheon_full_product_operation_audit_2026-08-29/FULL_OPERATION_AUDIT_2026-08-29.md`（含 2026-08-29T23:41Z 最新稽核基準與 post-cutoff PRs #5410~#5426） |
| Pantheon 基線 | `origin/dev@9c9adf426`（含 PR #5426 Agora baseline 500 修復、PR #5425 Agora projection receipt 綁定、PR #5424 TW bounded refresh 讀回、PR #5417 TW market session 新鮮度、PR #5411 Source frontier 範圍修復） |
| execute-plans 基線 | `origin/dev@bd03c863e`（含 PR #694 evidence manifest，前端 UI 程式碼同 `5ffee3db8` Workshop tenant 修正） |
| Hosted live BFF | `dcb14231d29f08f1646a4ee962b83fd2d4b67560`（VM `pantheon-lupin-dev` IP `35.201.204.12`，PostgreSQL checkpoint `7,649,369`，backlog 0，quarantine 0，ready/live） |
| Hosted live FE | `c230fc76bef78fc297135152f2acba690314bb9d`（pairId `0429052b...`，profile `read-only`，`VITE_BFF_MODE=live`，`VITE_BFF_FALLBACK=strict`，`VITE_BFF_REAL_WRITES=false`） |
| 目標運行環境 | Desktop browser；Paper/Simulation 交易循環；Source dev 常態 `reconcile-only`；dev-login auth stub |
| 執行治理範圍 | 本文件包為純文件規劃凍結；不直接修改產品代碼；執行任務需待本包經獨立審查合併後始得透過 canonical 治理工具 materialization |

---

## 1. 目的與核心原則

本規劃包（`FULL-OPERATION-GAP-SA-SD-PLAN-FREEZE-20260830`）旨在將 2026-08-29 全產品運作稽核所識別之 **OP-G01 至 OP-G20** 共 20 項產品與架構落差，以最新 `origin/dev`、`execute-plans` dev、hosted runtime 與 canonical task store 現狀為基準，進行嚴謹處置分類、目標系統分析（SA）、系統設計（SD）、並產出可平行化且無相依環（acyclic）之執行 DAG 與任務目錄（Execution Task Catalog）。

### 核心原則與邊界

1. **單一真實來源與單一擁有者（Single Source of Truth & Single Owner）**：
   - 拒絕相容層、雙重寫入、雙重資料庫或暫存假 facade。
   - 每一項可見功能、核心數據與 20 項 GAP 均有且僅有一個明確的 canonical primary execution owner。
2. **不重工與歷史事實不可變（No Redundant Work & Immutable Task History）**：
   - 充分繼承 ACG（Architecture Cleanup Gap）與 PFG（Product Functional Closure）已合併之有效產出，不重開或覆蓋已完成之歷史任務。
   - 針對 PR #5410~#5426 已在 `origin/dev` 完成之 source-level 修復（如 bare image ID 正規化、frontier recovery、snapshot alias、min-closes 歷史、TW session freshness、Agora projection 綁定與 baseline 500 修復），將其處置明確定位為「source 已修復，待 live atomic switch 與 hosted 閉環驗證」，避免重複開發。
3. **熱點檔案獨占擁有（Exclusive Hot-File Ownership）**：
   - 針對跨任務共用檔案（如 `services/control-plane/bff/main.py`、`execute-plans/src/App.tsx`、`execute-plans/src/lib/bff-v1/index.ts`、`docker-compose.yml`、`scripts/deploy_nonprod_vm.sh`），在各準備階段由模組化子目錄獨立實作，並指定單一整合任務在集成波次（Assembly Wave）進行切換，防止多 worker 同時編輯造成工作樹衝突。
4. **共享執行資源模型化（Execution Resource Modeling）**：
   - 將實體部署目標 `pantheon-dev` VM（`35.201.204.12`）模型化為容量為 1（`capacity=1`）之執行資源 `pantheon-dev-vm`。
   - 本地開發、單元測試、契約測試、靜態分析與 PR 審查維持高度平行（Parallel Preparation Lanes）；僅部署切換與 hosted 驗收階段依序取得 VM 資源。
5. **Supervisor Clone Session 平行機制與註冊身分邊界**：
   - 任務擁有者全面指派給具備 active capacity 之註冊身分 `Antigravity` 與 `Antigravity2`（共 13 項實作任務：Antigravity 7 項、Antigravity2 6 項）。
   - Supervisor 支援透過 clone sessions 在各獨立 git worktree lease 中平行派發多個同型 worker，不受單一進程限制。
   - 審查者指派給相異且具備即時審查能力之註冊身分（`Antigravity`、`Antigravity2`、`Codex2`、`Claude`、`Claude2`），禁止使用未註冊或已退役之幽靈身分，且在 materialization 時自動執行 live capacity preflight 檢驗。
6. **功能優先邊界（Functional-First Boundary & Non-Goals）**：
   - 專注於 Desktop browser 之完整 Management 與 Agora 功能旅程。
   - 專注於 Paper/Simulation 交易循環；不包含真實資金（Real Capital）或 live broker 交易權限。
   - Source Ingestion 在 dev 維持 `reconcile-only` 預設模式，僅允許人工、單次、有界（max 1 tick, max 100 records）之 provider pull 驗證。
   - 認證採用現有 dev-login / JWT stub，不新增複雜資安基礎設施或 mobile 測試作為功能完成前置。

---

## 2. 文件結構與導覽

本規劃套件由以下 6 份文件構成，彼此引用並構成完整治理鏈：

| 文件路徑 | 文件性質 | 主要內容摘要 |
|---|---|---|
| [`INDEX.md`](INDEX.md) | 總導覽與規劃索引 | 規劃背景、基線版本、核心原則、文件導覽、機器可讀檢驗與非目標宣告。 |
| [`CURRENT_GAP_DISPOSITION_2026-08-30.md`](CURRENT_GAP_DISPOSITION_2026-08-30.md) | 落差處置矩陣 | OP-G01 至 OP-G20 逐項處置、直接證據、歷史對齊與單一任務歸屬。 |
| [`SA_GAP_REMEDIATION_2026-08-30.md`](SA_GAP_REMEDIATION_2026-08-30.md) | 目標系統分析 (SA) | 6 大子系統之單一 owner 架構、資料流、狀態不變量與邊界規範。 |
| [`SD_GAP_REMEDIATION_2026-08-30.md`](SD_GAP_REMEDIATION_2026-08-30.md) | 系統設計規格 (SD) | 11 個設計單元（Design Units）之程式碼面、DTO 契約、狀態轉移與測試規範。 |
| [`EXECUTION_DAG_2026-08-30.md`](EXECUTION_DAG_2026-08-30.md) | 平行執行 DAG | 5 波次（Wave 0~4）相依圖、熱點檔案擁有者分配、資源鎖定與交付排程。 |
| [`EXECUTION_TASK_CATALOG_2026-08-30.json`](EXECUTION_TASK_CATALOG_2026-08-30.json) | 機器可讀任務目錄 | 1 個凍結任務 + 13 個實作/整合任務之完整 V2 TaskStore 契約 JSON。 |

---

## 3. 20 項 GAP 處置總覽摘要

| GAP ID | 嚴重度 | 標題摘要 | 處置分類 | 單一歸屬執行任務 |
|---|---:|---|---|---|
| **OP-G01** | P0 | Agora research 偽造 `real` 候選真值 | 主動修復 (Active Remediation) | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| **OP-G02** | P0 | Agora PerformanceSuggestion 無 production caller | 主動修復 (Active Remediation) | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| **OP-G03** | P0 | current source FE/BFF 尚未成對部署 live VM | 部署前置與閉環 (Hosted Precondition) | `OPGAP-HOSTED-DEV-PROMOTION-20260830` |
| **OP-G04** | P0 | release gate 把 skip/fail 包成 false-green | 治理門禁修復 (Gate Hardening) | `OPGAP-DEPLOY-RELIABILITY-20260830` |
| **OP-G05** | P1 | auth readiness 同步依賴 OpenClaw 探針延遲 | 主動修復 (Active Remediation) | `OPGAP-BE-BFF-CORE-20260830` |
| **OP-G06** | P0 | Management generic CRUD 無 durable owner | 主動修復 (Active Remediation) | `OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830` |
| **OP-G07** | P1 | Frontend production graph 可達 mock/seed | 主動修復 (Active Remediation) | `OPGAP-FE-BUNDLE-CLEANUP-20260830` |
| **OP-G08** | P1 | BFF `main.py` 巨型路由未完成拆分 | 主動修復 (Active Remediation) | `OPGAP-BFF-MAIN-ASSEMBLY-20260830` |
| **OP-G09** | P1 | Agora routers 跨域 import 私有 helper/store | 主動修復 (Active Remediation) | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| **OP-G10** | P2 | generic legacy action adapter 殘留 dead code | 清理刪除 (Cleanup & Deletion) | `OPGAP-BE-BFF-CORE-20260830` |
| **OP-G11** | P0 | 十二循環完整 deployed proof 為 opt-in/skipped | 驗證與簽收 (Verify & Closure) | `OPGAP-HOSTED-E2E-ACCEPTANCE-20260830` |
| **OP-G12** | P1 | current Source Management 缺 hosted 效應證據 | 主動修復與驗證 (Active Remediation) | `OPGAP-BE-SOURCE-MANAGEMENT-20260830` |
| **OP-G13** | P1 | 同步 FastAPI `TestClient` 在 AnyIO portal 死鎖 | 測試工具修復 (Tooling Fix) | `OPGAP-BE-BFF-CORE-20260830` |
| **OP-G14** | P1 | Management/Agora authenticated hosted UI 缺證據 | 驗證與簽收 (Verify & Closure) | `OPGAP-HOSTED-E2E-ACCEPTANCE-20260830` |
| **OP-G15** | P1 | research adapters 宣告與產品 UI 不一致 | 契約對齊與 UI (Contract & UI Alignment) | `OPGAP-FE-AGORA-WORKSHOP-20260830` |
| **OP-G16** | P0 | deployment lease 與 rollback 共用脆弱遠端依賴 | 部署韌性強化 (Deploy Reliability) | `OPGAP-DEPLOY-RELIABILITY-20260830` |
| **OP-G17** | P0 | Registry→Deployment→RuntimeBinding 投影未自然產生 | 主動修復 (Active Remediation) | `OPGAP-BE-RUNTIME-BINDING-20260830` |
| **OP-G18** | P1 | Management Postmortem 缺少 canonical read owner | 主動修復 (Active Remediation) | `OPGAP-BE-MGMT-POSTMORTEM-20260830` |
| **OP-G19** | P0 | Source-to-Agora projection 綁定在部署門禁中斷 | 部署驗證與閉環 (Verify & Close) | `OPGAP-HOSTED-DEV-PROMOTION-20260830` |
| **OP-G20** | P0 | paper-signal-producer 運行時健全度與生命週期閉環 | 部署驗證與閉環 (Verify & Close) | `OPGAP-HOSTED-DEV-PROMOTION-20260830` |

---

## 4. 機器可讀不變量檢驗指令與結果 (Machine-Checkable Invariant Proofs)

本規劃套件之任務目錄（`EXECUTION_TASK_CATALOG_2026-08-30.json`）具備以下 100% 確定性之 jq 機器檢驗保證：

1. **GAP 單一擁有者檢驗 (OP-G01..OP-G20 恰好各出現一次)**：
   ```bash
   jq -e '[.tasks[].gaps[]] | sort | (length == 20 and . == ["OP-G01","OP-G02","OP-G03","OP-G04","OP-G05","OP-G06","OP-G07","OP-G08","OP-G09","OP-G10","OP-G11","OP-G12","OP-G13","OP-G14","OP-G15","OP-G16","OP-G17","OP-G18","OP-G19","OP-G20"])' docs/04/pantheon_full_product_operation_audit_2026-08-29/EXECUTION_TASK_CATALOG_2026-08-30.json
   # Result: true (Exit 0)
   ```
2. **修改構件唯一性檢驗 (無跨任務衝突寫入)**：
   ```bash
   jq -e '[.plan_freeze_task.artifacts[], .tasks[].artifacts[]] | length == (unique | length)' docs/04/pantheon_full_product_operation_audit_2026-08-29/EXECUTION_TASK_CATALOG_2026-08-30.json
   # Result: true (Exit 0)
   ```
3. **Owner != Reviewer 檢驗 (嚴格獨立審查)**：
   ```bash
   jq -e '[.plan_freeze_task, .tasks[]] | all(.owner != .reviewer)' docs/04/pantheon_full_product_operation_audit_2026-08-29/EXECUTION_TASK_CATALOG_2026-08-30.json
   # Result: true (Exit 0)
   ```
4. **Dev VM 執行資源限定檢驗 (僅 Wave 3/4 宣告 `pantheon-dev-vm`)**：
   ```bash
   jq -e '[.tasks[] | if (.id == "OPGAP-HOSTED-DEV-PROMOTION-20260830" or .id == "OPGAP-HOSTED-E2E-ACCEPTANCE-20260830") then .execution_resources == ["pantheon-dev-vm"] else .execution_resources == [] end] | all' docs/04/pantheon_full_product_operation_audit_2026-08-29/EXECUTION_TASK_CATALOG_2026-08-30.json
   # Result: true (Exit 0)
   ```
5. **註冊身分合法性檢驗 (無幽靈或退役身分)**：
   ```bash
   jq -e '[.plan_freeze_task, .tasks[]] | all((.owner | IN("Antigravity", "Antigravity2", "Codex2", "Claude", "Claude2", "Copilot")) and (.reviewer | IN("Antigravity", "Antigravity2", "Codex2", "Claude", "Claude2", "Copilot")))' docs/04/pantheon_full_product_operation_audit_2026-08-29/EXECUTION_TASK_CATALOG_2026-08-30.json
   # Result: true (Exit 0)
   ```

---

## 5. 交付與簽收條件

本凍結套件之簽收需滿足：
1. 本套件 6 份文件內容一致、交叉引用精確、無死結或幽靈相依。
2. Git diff 嚴格受限於 `docs/04/pantheon_full_product_operation_audit_2026-08-29/` 目錄，不污染任何產品或工具程式碼。
3. 取得指派 Reviewer（`Codex2`）之獨立 exact-head 審查核可。
4. 通過 GitHub Actions CI 並合併至 `origin/dev`。
5. 由 Owner（`Antigravity`）以 `scripts/ai-status.sh done` 正式收尾本規劃任務，始得進行後續實作任務 materialization。
