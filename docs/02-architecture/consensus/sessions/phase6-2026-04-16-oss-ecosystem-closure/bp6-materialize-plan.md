# BP6 Sprint 物化執行計劃

**日期：2026-04-17**
**會議 ID：phase6-2026-04-16-oss-ecosystem-closure**
**人工確認：已完成（2026-04-17）**

## 現況

現有提議任務（8 個 OSS 任務，已在 session 中）：
- OSS-NEXT-001 ~ OSS-NEXT-008

需要補充任務（22 個）：
- Wave 1：BP6-UI-REVIEW-001~004、BP6-BFF-001（5 個）
- Wave 2：BP6-LUV-011~016（6 個）
- Wave 3：BP6-LUV-017~020（4 個）
- Wave 4：BP6-SVC-FB-001、BP6-SVC-EVAL-001、BP6-SVC-MEM-001、BP6-TEST-001（4 個）
- Wave 6：BP6-STATE-001~004（4 個，STATE-004 依賴 GCP 環境）

## 物化步驟

### Step 1：補充 Wave 1 任務

```bash
# BP6-UI-REVIEW-001
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="整合 PKT-002-incident-detail ui-done 返回內容，完成 Pantheon-side review 與 coordination loop 關閉" \
TASK_ARTIFACTS=".coordination/responses/PKT-002-incident-detail-lovable-ui-task.yaml,.coordination/requests/PKT-002-incident-detail-ui-done.yaml" \
TASK_ACCEPTANCE="PKT-002-incident-detail lovable-ui-task status 更新為 loop-complete,ui-done 請求關閉,整合記錄已 commit" \
python3 scripts/planning_state.py propose-task BP6-UI-REVIEW-001 Claude Codex2 \
  "Integrate PKT-002-incident-detail ui-done return and close the Pantheon review loop"

# BP6-UI-REVIEW-002
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="整合 PKT-003-post-incident-review ui-done 返回內容，完成 Pantheon-side review 與 loop 關閉" \
TASK_ARTIFACTS=".coordination/responses/PKT-003-post-incident-review-lovable-ui-task.yaml,.coordination/requests/PKT-003-post-incident-review-ui-done.yaml" \
TASK_ACCEPTANCE="PKT-003-post-incident-review lovable-ui-task status 更新為 loop-complete,整合記錄已 commit" \
python3 scripts/planning_state.py propose-task BP6-UI-REVIEW-002 Claude Codex2 \
  "Integrate PKT-003-post-incident-review ui-done return and close the Pantheon review loop"

# BP6-UI-REVIEW-003
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="整合 PKT-004-persona-drilldowns ui-done 返回，完成 Pantheon review 與 loop 關閉" \
TASK_ARTIFACTS=".coordination/responses/PKT-004-persona-drilldowns-lovable-ui-task.yaml,.coordination/requests/PKT-004-persona-drilldowns-ui-done.yaml" \
TASK_ACCEPTANCE="PKT-004-persona-drilldowns lovable-ui-task status 更新為 loop-complete,整合記錄已 commit" \
python3 scripts/planning_state.py propose-task BP6-UI-REVIEW-003 Claude Codex2 \
  "Integrate PKT-004-persona-drilldowns ui-done return and close the Pantheon review loop"

# BP6-UI-REVIEW-004
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="整合 PKT-005-sse-substrate ui-done 返回，解決後端 SSE 遺留問題，完成 loop 關閉" \
TASK_ARTIFACTS=".coordination/responses/PKT-005-sse-substrate-lovable-ui-task.yaml,.coordination/requests/PKT-005-sse-substrate-ui-done.yaml,.coordination/responses/PKT-005-sse-substrate-backend-delivery.yaml" \
TASK_ACCEPTANCE="PKT-005-sse-substrate backend followup 解決,lovable-ui-task 更新為 loop-complete,整合記錄已 commit" \
python3 scripts/planning_state.py propose-task BP6-UI-REVIEW-004 Claude Codex2 \
  "Integrate PKT-005-sse-substrate ui-done return, resolve SSE backend followup, and close loop"

# BP6-BFF-001
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="調查並解決 5 個仍開放的 BFF gap 請求（F-042、PKT-002×3、PKT-003），確認每個是仍有效或可關為 stale" \
TASK_ARTIFACTS=".coordination/requests/F-042-bff-gap.yaml,.coordination/requests/PKT-002-incident-action-drawer-bff-gap.yaml,.coordination/requests/PKT-002-incident-detail-bff-gap.yaml,.coordination/requests/PKT-002-incident-home-bff-gap.yaml,.coordination/requests/PKT-003-post-incident-review-bff-gap.yaml" \
TASK_ACCEPTANCE="全部 5 個 bff-gap 請求的 status 更新為 resolved 或 closed-as-stale,若有實際缺口已實作對應端點" \
python3 scripts/planning_state.py propose-task BP6-BFF-001 Claude Codex2 \
  "Investigate and resolve all 5 open BFF gap coordination requests"
```

### Step 2：補充 Wave 2 任務

```bash
# BP6-LUV-011
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="觸發 PKT-001-deployment-review + PKT-001-governance-review-queue 至 Lovable 執行，完成整合 loop" \
TASK_ARTIFACTS=".coordination/responses/PKT-001-deployment-review-lovable-ui-task.yaml,.coordination/responses/PKT-001-governance-review-queue-lovable-ui-task.yaml" \
TASK_ACCEPTANCE="PKT-001-deployment-review 和 PKT-001-governance-review-queue 均達到 loop-complete" \
python3 scripts/planning_state.py propose-task BP6-LUV-011 Codex2 Claude \
  "Execute PKT-001-deployment-review and PKT-001-governance-review-queue through Lovable and integrate"

# BP6-LUV-012
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="觸發 PKT-002-incident-action-drawer + PKT-002-incident-home 至 Lovable 執行，完成整合 loop" \
TASK_ARTIFACTS=".coordination/responses/PKT-002-incident-action-drawer-lovable-ui-task.yaml,.coordination/responses/PKT-002-incident-home-lovable-ui-task.yaml" \
TASK_ACCEPTANCE="PKT-002-incident-action-drawer 和 PKT-002-incident-home 均達到 loop-complete" \
python3 scripts/planning_state.py propose-task BP6-LUV-012 Codex2 Claude \
  "Execute PKT-002-incident-action-drawer and PKT-002-incident-home through Lovable and integrate"

# BP6-LUV-013
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="觸發 PKT-004-capital-binding-drilldowns + PKT-004-deployment-approval-drilldowns 至 Lovable，完成整合" \
TASK_ARTIFACTS=".coordination/responses/PKT-004-capital-binding-drilldowns-lovable-ui-task.yaml,.coordination/responses/PKT-004-deployment-approval-drilldowns-lovable-ui-task.yaml" \
TASK_ACCEPTANCE="兩個封包均達到 loop-complete" \
python3 scripts/planning_state.py propose-task BP6-LUV-013 Codex Claude \
  "Execute PKT-004-capital-binding-drilldowns and PKT-004-deployment-approval-drilldowns through Lovable"

# BP6-LUV-014
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="觸發 PKT-005-degradation-banner 至 Lovable 執行，完成整合 loop" \
TASK_ARTIFACTS=".coordination/responses/PKT-005-degradation-banner-lovable-ui-task.yaml" \
TASK_ACCEPTANCE="PKT-005-degradation-banner 達到 loop-complete" \
python3 scripts/planning_state.py propose-task BP6-LUV-014 Codex Claude \
  "Execute PKT-005-degradation-banner through Lovable and integrate"

# BP6-LUV-015
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="BFF gap 解決後觸發 F-042 至 Lovable 執行，完成 Promotion Review UI 整合 loop" \
TASK_DEPENDS_ON="BP6-BFF-001" \
TASK_ARTIFACTS=".coordination/responses/F-042-lovable-ui-task.yaml,.coordination/requests/F-042-bff-gap.yaml" \
TASK_ACCEPTANCE="F-042 lovable-ui-task 達到 loop-complete,Promotion Review UI 整合完成" \
python3 scripts/planning_state.py propose-task BP6-LUV-015 Codex2 Claude \
  "Execute F-042 Promotion Review UI through Lovable after BFF gap resolution and integrate"

# BP6-LUV-016
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="後端問題解決後觸發 PKT-004-persona-management 至 Lovable，完成整合" \
TASK_DEPENDS_ON="BP6-BFF-001" \
TASK_ARTIFACTS=".coordination/responses/PKT-004-persona-management-lovable-ui-task.yaml" \
TASK_ACCEPTANCE="PKT-004-persona-management 達到 loop-complete" \
python3 scripts/planning_state.py propose-task BP6-LUV-016 Codex2 Claude \
  "Execute PKT-004-persona-management through Lovable after backend resolution and integrate"
```

### Step 3：補充 Wave 3 任務（新封包）

```bash
# BP6-LUV-017
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="觸發 PKT-006-approval-queue 至 Lovable 執行，完成整合 loop" \
TASK_ARTIFACTS=".coordination/responses/PKT-006-approval-queue-lovable-ui-task.yaml,.coordination/responses/PKT-006-approval-queue-contract-ready.yaml" \
TASK_ACCEPTANCE="PKT-006-approval-queue 達到 loop-complete,前端 Approval Queue 畫面整合完成" \
python3 scripts/planning_state.py propose-task BP6-LUV-017 Codex2 Claude \
  "Execute PKT-006-approval-queue through Lovable and integrate into the frontend"

# BP6-LUV-018
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="觸發 PKT-007-deployment-diff 至 Lovable 執行，完成整合 loop" \
TASK_ARTIFACTS=".coordination/responses/PKT-007-deployment-diff-lovable-ui-task.yaml,.coordination/responses/PKT-007-deployment-diff-contract-ready.yaml" \
TASK_ACCEPTANCE="PKT-007-deployment-diff 達到 loop-complete" \
python3 scripts/planning_state.py propose-task BP6-LUV-018 Codex2 Claude \
  "Execute PKT-007-deployment-diff through Lovable and integrate into the frontend"

# BP6-LUV-019
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="觸發 PKT-008-rollback-review 至 Lovable 執行，完成整合 loop" \
TASK_ARTIFACTS=".coordination/responses/PKT-008-rollback-review-lovable-ui-task.yaml,.coordination/responses/PKT-008-rollback-review-contract-ready.yaml" \
TASK_ACCEPTANCE="PKT-008-rollback-review 達到 loop-complete" \
python3 scripts/planning_state.py propose-task BP6-LUV-019 Codex Claude \
  "Execute PKT-008-rollback-review through Lovable and integrate into the frontend"

# BP6-LUV-020
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="觸發 PKT-009-governance-audit-rail 至 Lovable 執行，完成整合 loop" \
TASK_ARTIFACTS=".coordination/responses/PKT-009-governance-audit-rail-lovable-ui-task.yaml,.coordination/responses/PKT-009-governance-audit-rail-contract-ready.yaml" \
TASK_ACCEPTANCE="PKT-009-governance-audit-rail 達到 loop-complete" \
python3 scripts/planning_state.py propose-task BP6-LUV-020 Codex Claude \
  "Execute PKT-009-governance-audit-rail through Lovable and integrate into the frontend"
```

### Step 4：補充 Wave 4 任務（空服務實作）

```bash
# BP6-SVC-FB-001
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="實作 services/feedback/ 基礎路徑：preference events 儲存、trajectory schema → .py 實作 + tests。是 TRL 激活的前置條件。" \
TASK_ARTIFACTS="services/feedback/,services/feedback/preference_store.py,services/feedback/trajectory_store.py" \
TASK_ACCEPTANCE="services/feedback/ 有實作 .py 檔案,preference events 可寫入並讀回,smoke test 通過,至少 3 個單元測試" \
python3 scripts/planning_state.py propose-task BP6-SVC-FB-001 Claude Codex2 \
  "Implement services/feedback/ preference events store and trajectory schema as the TRL activation prerequisite"

# BP6-SVC-EVAL-001
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="實作 services/evaluation/ 核心路徑：evaluator contract 落成 .py 實作 + tests" \
TASK_ARTIFACTS="services/evaluation/,services/evaluation/evaluator.py" \
TASK_ACCEPTANCE="services/evaluation/ 有核心 .py 實作,evaluator 可接收 artifact 並輸出評估結果,smoke test 通過" \
python3 scripts/planning_state.py propose-task BP6-SVC-EVAL-001 Claude Codex \
  "Implement services/evaluation/ core evaluator path with smoke test and unit coverage"

# BP6-SVC-MEM-001
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="實作 services/memory/ 核心路徑：institutional_memory_entry schema 落成 .py 實作 + tests" \
TASK_ARTIFACTS="services/memory/,services/memory/memory_store.py" \
TASK_ACCEPTANCE="services/memory/ 有核心 .py 實作,memory entry 可寫入並查詢,smoke test 通過" \
python3 scripts/planning_state.py propose-task BP6-SVC-MEM-001 Codex2 Claude \
  "Implement services/memory/ institutional memory store with smoke test and unit coverage"

# BP6-TEST-001
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="為 services/runtime-manager/ 補充測試覆蓋（目前 0 個測試），覆蓋核心 RuntimeBinding 寫入和命令路徑" \
TASK_ARTIFACTS="services/runtime-manager/,services/runtime-manager/test_service.py" \
TASK_ACCEPTANCE="runtime-manager 有至少 5 個單元測試,覆蓋 RuntimeBinding 建立、命令分發、smoke test 通過" \
python3 scripts/planning_state.py propose-task BP6-TEST-001 Codex Claude \
  "Add unit test coverage for services/runtime-manager/ covering RuntimeBinding and command dispatch paths"
```

### Step 5：補充 Wave 6 任務（狀態清理）

```bash
# BP6-STATE-001
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="更新 ai-status.json sprint 名稱和 objective 為 '把系統藍圖完整實現'，反映 BP5 已完成、BP6 開始" \
TASK_ARTIFACTS="ai-status.json" \
TASK_ACCEPTANCE="ai-status.json sprint 和 objective 已更新,current-work.md 同步反映新目標" \
python3 scripts/planning_state.py propose-task BP6-STATE-001 Codex Claude \
  "Update ai-status.json sprint objective to full blueprint completion and rotate sprint name to BP6"

# BP6-STATE-002
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="補寫 phase5 consensus-packet.md 實際接受內容，取代目前仍是模板佔位符的狀態" \
TASK_ARTIFACTS="docs/02-architecture/consensus/sessions/phase5-2026-04-15-full-blueprint-gap-closure/consensus-packet.md" \
TASK_ACCEPTANCE="consensus-packet.md 包含真實的接受架構摘要，不再是模板文字" \
python3 scripts/planning_state.py propose-task BP6-STATE-002 Codex Claude \
  "Write the actual phase5 consensus packet content to replace the template placeholder"

# BP6-STATE-003
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="更新 execution-materialization.md 和 planning-session.json 以反映 phase5 已執行完成的真實狀態" \
TASK_ARTIFACTS="docs/02-architecture/consensus/sessions/phase5-2026-04-15-full-blueprint-gap-closure/execution-materialization.md,docs/02-architecture/consensus/sessions/phase5-2026-04-15-full-blueprint-gap-closure/planning-session.json" \
TASK_ACCEPTANCE="execution-materialization.md 更新為 done 狀態，planning-session.json 子欄位與已完成事實一致" \
python3 scripts/planning_state.py propose-task BP6-STATE-003 Codex Claude \
  "Reconcile phase5 planning artifacts to match the already-completed execution reality"

# BP6-STATE-004 (GCP，依賴環境，由 Gemini 執行)
TASK_PHASE="Phase 6: Full Blueprint Completion" \
TASK_SUMMARY_ZH="執行 BP5-GCP-002 歸檔備注中的 operator follow-up：建立 DB users、secret versions，留下執行確認記錄" \
TASK_ARTIFACTS="docs/gcp-bootstrap-confirmation.md" \
TASK_ACCEPTANCE="DB users 已建立,Secret Manager secret versions 已建立,執行記錄已 commit 到 repo" \
python3 scripts/planning_state.py propose-task BP6-STATE-004 Gemini Claude \
  "Complete GCP environment bootstrap operator follow-up: DB users and Secret Manager secret versions"
```

### Step 6：核准並物化

```bash
# 設定 consensus 為 accepted（人工已確認）
python3 scripts/planning_state.py consensus accepted \
  "Product owner confirmed BP6 sprint scope on 2026-04-17. All 30 tasks approved."

# 確認 human gate 已核准
python3 scripts/planning_state.py human-gate approved \
  "Human gate approved by product owner 2026-04-17: PKT-006~009 included, evaluation/memory services included, RL deferred, sprint objective set to full blueprint completion."

# 物化所有任務到 ai-status.json
python3 scripts/planning_state.py materialize
```

### Step 7：更新 Sprint 目標

物化後，需要另外更新 `ai-status.json` 的頂層 sprint 欄位：
```bash
AI_NAME=Claude python3 scripts/ai_status.py update-sprint \
  "2026-04-17-full-blueprint-completion" \
  "把系統藍圖完整實現：關閉所有 Lovable UI loop、補充空服務實作、激活 Qlib/TRL OSS 框架、解決 BFF gap、清理規劃文件"
```

## 預期結果

物化後 ai-status.json 應有 30 個新任務，全部 `status: todo`：

| Wave | 任務 ID | 數量 |
|------|---------|------|
| Wave 1 UI + BFF | BP6-UI-REVIEW-001~004、BP6-BFF-001 | 5 |
| Wave 2 Lovable 2nd | BP6-LUV-011~016 | 6 |
| Wave 3 新封包 | BP6-LUV-017~020 | 4 |
| Wave 4 空服務 | BP6-SVC-FB-001、BP6-SVC-EVAL-001、BP6-SVC-MEM-001、BP6-TEST-001 | 4 |
| Wave 5 OSS | OSS-NEXT-001~008 | 8 |
| Wave 6 狀態清理 | BP6-STATE-001~004 | 4 |
| **總計** | | **31 個** |

（OSS-NEXT-003 關於 RL 決策，物化後 status=todo，但執行時 owner 應記錄為「本 sprint 不開啟，等 Qlib 3 個月後評估」）
