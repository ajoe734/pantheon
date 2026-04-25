# Pantheon 全藍圖實作完成計劃（BP6）

> Historical planning record: this document captures one 2026-04-17 planning proposal and should be read as execution/planning history, not as immutable blueprint truth.

**作成日期：2026-04-17**
**Sprint 目標：把系統藍圖完整實現**
**確認人：Product Owner**
**準備人：Claude（整合 Codex 差異分析）**

---

## 人工確認決策記錄

| 問題 | 決策 |
|------|------|
| PKT-006~009 是否納入下一個 sprint？ | **是** |
| evaluation / memory 服務是否要補實作？ | **是** |
| Sprint 目標？ | **把系統藍圖完整實現** |
| RL approval gate？ | **本 sprint 無法執行**（前置條件：先完成 Qlib adapter + 3 個月運作歷史）|

---

## 一、現況快照

### 已完成（不再需要動）

- DEVELOPMENT_WORKBREAKDOWN.md 全 28 個規範任務 ✅
- Phase 0~6 全部完成 ✅
- BP5 執行波 89 個任務全部歸檔 ✅
- 4 個核心 OSS 整合（OpenClaw / DSPy / imitation / MLflow）✅
- CI/CD + GCP repo 端基礎設施 ✅
- 所有服務領域 API 語義層（registry / governance / deployment / capital / runtime / telemetry / lineage / incident / evolution / persona）✅

### 仍有落差的五個區塊

```
A. Lovable 前端執行波       — 16/19 封包未完成，含 4 個新封包
B. BFF Gap 後端遺留         — 5 個 open gap 阻擋部分封包
C. OSS 激活波               — Qlib/TRL 最高優先；RL 有門控
D. 空服務實作               — evaluation / feedback / memory
E. 技術債 / 狀態文件漂移    — 測試缺口、規劃文件過期
```

---

## 二、完整任務清單

### Wave 1：UI 積壓收尾 + BFF Gap 解決（最高優先）

這一波最重要，許多後續工作被這裡阻擋。

| 任務 ID | 描述 | Owner | Reviewer | 依賴 |
|---------|------|-------|----------|------|
| BP6-UI-REVIEW-001 | Pantheon review/整合 PKT-002-incident-detail（ui-done 已返回） | Claude | Codex2 | - |
| BP6-UI-REVIEW-002 | Pantheon review/整合 PKT-003-post-incident-review | Claude | Codex2 | - |
| BP6-UI-REVIEW-003 | Pantheon review/整合 PKT-004-persona-drilldowns | Claude | Codex2 | - |
| BP6-UI-REVIEW-004 | Pantheon review/整合 PKT-005-sse-substrate + 解決後端遺留 | Claude | Codex2 | - |
| BP6-BFF-001 | 調查並解決 5 個 open BFF gap（F-042、PKT-002×3、PKT-003） | Claude | Codex2 | - |

**驗收**：
- 4 個 ui-done 封包有 Pantheon 整合記錄並關閉
- 5 個 BFF gap 請求的 status 更新為 `resolved` 或 `closed-as-stale`

---

### Wave 2：Lovable 第二波觸發（Wave 1 完成後）

| 任務 ID | 描述 | Owner | Reviewer | 依賴 |
|---------|------|-------|----------|------|
| BP6-LUV-011 | 觸發 PKT-001-deployment-review + PKT-001-governance-review-queue → Lovable 執行 → Pantheon 整合 | Codex2 | Claude | - |
| BP6-LUV-012 | 觸發 PKT-002-incident-action-drawer + PKT-002-incident-home → Lovable → Pantheon 整合 | Codex2 | Claude | - |
| BP6-LUV-013 | 觸發 PKT-004-capital-binding-drilldowns + PKT-004-deployment-approval-drilldowns → Lovable → 整合 | Codex | Claude | - |
| BP6-LUV-014 | 觸發 PKT-005-degradation-banner → Lovable → Pantheon 整合 | Codex | Claude | - |
| BP6-LUV-015 | 觸發 F-042（Promotion Review UI）→ Lovable → 整合 | Codex2 | Claude | BP6-BFF-001 |
| BP6-LUV-016 | 觸發 PKT-004-persona-management → Lovable → 整合 | Codex2 | Claude | BP6-BFF-001 |

**驗收**：
- 每個封包都有 `loop-complete` 或 `ui-done + Pantheon 整合` 記錄

---

### Wave 3：新封包 PKT-006~009（已確認納入）

| 任務 ID | 描述 | Owner | Reviewer | 依賴 |
|---------|------|-------|----------|------|
| BP6-LUV-017 | PKT-006-approval-queue：Lovable 執行 + Pantheon 整合 | Codex2 | Claude | - |
| BP6-LUV-018 | PKT-007-deployment-diff：Lovable 執行 + Pantheon 整合 | Codex2 | Claude | - |
| BP6-LUV-019 | PKT-008-rollback-review：Lovable 執行 + Pantheon 整合 | Codex | Claude | - |
| BP6-LUV-020 | PKT-009-governance-audit-rail：Lovable 執行 + Pantheon 整合 | Codex | Claude | - |

---

### Wave 4：空服務實作（已確認：evaluation / feedback / memory）

| 任務 ID | 描述 | Owner | Reviewer | 依賴 |
|---------|------|-------|----------|------|
| BP6-SVC-FB-001 | 實作 `services/feedback/` 基礎路徑：preference events 儲存、trajectory schema → .py 實作 + tests | Claude | Codex2 | - |
| BP6-SVC-EVAL-001 | 實作 `services/evaluation/` 核心路徑：evaluator contract → .py 實作 + tests | Claude | Codex | - |
| BP6-SVC-MEM-001 | 實作 `services/memory/` 核心路徑：institutional memory entry → .py 實作 + tests | Codex2 | Claude | - |
| BP6-TEST-001 | 補充 `services/runtime-manager/` 測試覆蓋（目前 0 個測試）| Codex | Claude | - |

**驗收**：
- 三個服務至少有基礎 .py 實作 + smoke test
- runtime-manager 有最少 3 個單元測試覆蓋核心路徑

---

### Wave 5：OSS 激活波

#### Wave 5-A：Qlib（最高優先，其他 OSS 的前置條件）

| 任務 ID | 描述 | Owner | Reviewer | 依賴 |
|---------|------|-------|----------|------|
| OSS-NEXT-001 | 建立 `services/research/qlib/adapter/` governed data-handler adapter，LightGBM-first smoke test，registry 相容輸出格式 | Codex | Claude | - |
| OSS-NEXT-001-SIDECAR-ACCEPTANCE | OSS-NEXT-001 驗收封包 | Codex2 | Claude | OSS-NEXT-001 |

**驗收**：
- governed Qlib adapter 可讀取 OHLCV 並輸出 `StrategySpec` 格式
- LightGBM smoke test 通過
- `OSS_INTEGRATION_CHECKLIST.md` 中 Qlib 更新至 `smoke-tested`

#### Wave 5-B：TRL（需 feedback 服務先就緒）

| 任務 ID | 描述 | Owner | Reviewer | 依賴 |
|---------|------|-------|----------|------|
| OSS-NEXT-002 | TRL 激活基線：package pin (trl>=0.8.0)、preference-pair pipeline、DPO smoke test、governed artifact path | Codex | Claude | BP6-SVC-FB-001 |
| OSS-NEXT-002-SIDECAR-ACCEPTANCE | OSS-NEXT-002 驗收封包 | Codex2 | Claude | OSS-NEXT-002 |

**驗收**：
- trl>=0.8.0 已 pin 並記錄
- DPO training smoke test 通過
- `OSS_INTEGRATION_CHECKLIST.md` 中 TRL 更新至 `smoke-tested`

#### Wave 5-C：未啟動框架任務物化

| 任務 ID | 描述 | Owner | Reviewer | 依賴 |
|---------|------|-------|----------|------|
| OSS-NEXT-005 | vectorbt 任務物化：上游版本選定、governed adapter 設計（backtesting I/O）、smoke test 計劃 | Codex | Claude | - |
| OSS-NEXT-006 | statsmodels 任務物化：regime analysis / econometrics 使用場景綁定、adapter 設計 | Codex2 | Codex | - |
| OSS-NEXT-007 | QuantLib 任務物化：衍生品定價範疇限定、adapter 設計 | Claude | Codex2 | - |

**注意**：Wave 5-C 是「任務物化」階段，輸出是清楚的下一波任務定義，不是完整實作。

#### Wave 5-D：RL 路徑（本 Sprint 不執行，等門控）

| 條件 | 狀態 | 說明 |
|------|------|------|
| Qlib adapter 就緒 | 待 OSS-NEXT-001 完成 | 前置條件 |
| Qlib 運作 3 個月 | 未開始 | 最早 2026-07 才能評估 |
| RL approval gate 審閱 | 未安排 | 等 Qlib 飽和後再進行 |

**決策**：RL path（FinRL / RLlib / Ray Tune）在本 Sprint **保持 `closed` 狀態**，不開啟實作 lane。

#### Wave 5-E：W&B（等 MLflow 30 天門控）

目前 MLflow 整合完成日期：2026-04-15。30 天門控滿足日期：**2026-05-15**。
W&B 任務安排不早於 2026-05-15。

#### Wave 5-F：已整合框架回歸刷新

| 任務 ID | 描述 | Owner | Reviewer | 依賴 |
|---------|------|-------|----------|------|
| OSS-NEXT-008 | OpenClaw / DSPy / imitation / MLflow：BP5 後回歸驗證，smoke evidence 更新 | Codex | Claude | - |

---

### Wave 6：技術債 + 狀態文件清理（可與其他波次並行）

| 任務 ID | 描述 | Owner | Reviewer | 依賴 |
|---------|------|-------|----------|------|
| BP6-STATE-001 | 更新 `ai-status.json` sprint objective 為 "把系統藍圖完整實現" | Codex | Claude | - |
| BP6-STATE-002 | 補寫 `phase5/consensus-packet.md` 實際接受內容（目前仍是模板） | Codex | Claude | - |
| BP6-STATE-003 | 更新 `execution-materialization.md` 和 `planning-session.json` 反映已完成狀態 | Codex | Claude | - |
| BP6-STATE-004 | GCP 環境 bootstrap 手動步驟確認（DB users、secret versions）並留下執行記錄 | Gemini | Claude | GCP 環境存取 |

---

## 三、依賴關係圖

```
Wave 1（BFF gap + UI review）
  │
  ├──→ Wave 2（Lovable 第二波，大部分封包不依賴 Wave 1）
  │      └──→ Wave 3（PKT-006~009，可並行）
  │
  ├──→ Wave 4（空服務實作）
  │      └──→ OSS-NEXT-002（TRL 依賴 feedback 服務）
  │
  └──→ Wave 6（狀態清理，完全並行）

Wave 5-A（Qlib）── 獨立，可立即開始
Wave 5-C（vectorbt/statsmodels/QuantLib 任務物化）── 獨立
Wave 5-F（回歸刷新）── 獨立

Wave 5-B（TRL）── 依賴 Wave 4 的 BP6-SVC-FB-001
Wave 5-D（RL）── 本 Sprint 不執行
Wave 5-E（W&B）── 等 2026-05-15

BP6-STATE-004（GCP bootstrap）── 依賴人工環境存取，Gemini quota 恢復後
```

---

## 四、執行順序建議（考慮 Agent 並行）

### 第一批（立即可派工）

以下任務相互獨立，可並行：

- BP6-UI-REVIEW-001~004（Claude）
- BP6-BFF-001（Claude / Codex2）
- OSS-NEXT-001（Codex，Qlib adapter）
- OSS-NEXT-008（Codex，回歸刷新）
- BP6-SVC-FB-001（Claude，feedback 服務）
- BP6-SVC-EVAL-001（Claude，evaluation 服務）
- BP6-SVC-MEM-001（Codex2，memory 服務）
- BP6-TEST-001（Codex，runtime-manager 測試）
- BP6-STATE-001~003（Codex，狀態清理）
- OSS-NEXT-005（Codex，vectorbt 任務物化）
- OSS-NEXT-006（Codex2，statsmodels 任務物化）
- OSS-NEXT-007（Claude，QuantLib 任務物化）

### 第二批（Wave 1 完成後）

- BP6-LUV-011~016（觸發 Lovable 第二波）

### 第三批（第二批後）

- BP6-LUV-017~020（PKT-006~009）
- OSS-NEXT-002（TRL，需 feedback 服務就緒）

### 第四批（門控就緒後）

- BP6-STATE-004（Gemini quota 恢復後執行）
- Wave 5-E（W&B，2026-05-15 後）

---

## 五、Agent 任務分配表

| Agent | 主要任務 | 備注 |
|-------|----------|------|
| **Claude** | BP6-UI-REVIEW-001~004、BP6-BFF-001、BP6-SVC-FB-001、BP6-SVC-EVAL-001、OSS-NEXT-007 | execution + governance lane |
| **Codex2** | BP6-LUV-011~016、BP6-SVC-MEM-001、OSS-NEXT-006 | integration + acceptance |
| **Codex** | OSS-NEXT-001、OSS-NEXT-005、OSS-NEXT-008、BP6-LUV-017~020、BP6-TEST-001、BP6-STATE-001~003 | schema + OSS + status |
| **Gemini** | BP6-STATE-004（quota 恢復後） | GCP lane |
| **Copilot** | Wave 5-D 準備工作（RL evidence 蒐集）| quota 恢復後，為未來 RL gate 收集數據 |

---

## 六、「全藍圖實作完成」驗收標準

### 前端閉環
- [ ] 全部 19 個 Lovable 封包 `loop-complete` 或 `ui-done + 整合`
- [ ] PKT-006~009（4 個新封包）全部完成
- [ ] 0 個 BFF gap 保持 open 狀態

### OSS 生態系統
- [ ] Qlib：`smoke-tested`（governed adapter + LightGBM smoke test 通過）
- [ ] TRL：`smoke-tested`（DPO pipeline + smoke test 通過）
- [ ] vectorbt / statsmodels / QuantLib：任務已物化，下一波有具體任務 ID
- [ ] OpenClaw / DSPy / imitation / MLflow：回歸刷新確認

### 服務完整性
- [ ] `services/feedback/`：有實際 .py 實作 + smoke test
- [ ] `services/evaluation/`：有實際 .py 實作 + smoke test
- [ ] `services/memory/`：有實際 .py 實作 + smoke test
- [ ] `services/runtime-manager/`：有最少基礎測試覆蓋

### 環境與狀態
- [ ] `ai-status.json` sprint objective 已更新
- [ ] phase5 規劃文件已清理（consensus-packet、execution-materialization、planning-session.json）
- [ ] GCP 環境 bootstrap 有執行確認記錄

### 有條件項目（本 Sprint 不強求）
- [ ] RL：等 Qlib 3 個月歷史後再評估（最早 2026-07）
- [ ] W&B：等 MLflow 30 天門控（2026-05-15 後）

---

## 七、任務計數摘要

| 類別 | 任務數 |
|------|--------|
| UI 積壓收尾（Wave 1） | 5 |
| Lovable 第二波（Wave 2） | 6 |
| 新封包 PKT-006~009（Wave 3） | 4 |
| 空服務實作（Wave 4） | 4 |
| OSS 激活波（Wave 5） | 7（不含 RL/W&B） |
| 技術債/狀態清理（Wave 6） | 4 |
| **合計** | **30 個任務** |

---

## 八、RL 路徑時間線（供未來規劃）

```
2026-04-17  OSS-NEXT-001（Qlib adapter）開始
2026-04-末  Qlib adapter 就緒，開始收集 production 歷史
2026-07-17  Qlib 3 個月歷史門控滿足（最早）
2026-07-末  RL Approval Gate 審閱（若 Qlib 開始飽和）
2026-08+    若 gate approved → FinRL / RLlib adapter 實作開始

2026-05-15  W&B 30 天 MLflow 門控滿足 → W&B 激活評估
```

---

*參考文件：*
- *`docs/reviews/2026-04-16-full-blueprint-gap-analysis.md`*
- *`docs/reviews/2026-04-16-oss-ecosystem-gap-analysis.md`*
- *`docs/reviews/2026-04-17-next-wave-implementation-plan.md`（本文件初版）*
- *`DEVELOPMENT_WORKBREAKDOWN.md`*
- *`OSS_INTEGRATION_CHECKLIST.md`*
- *`services/learning/rl/RL_PATH_APPROVAL_GATE.md`*
