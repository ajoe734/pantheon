# 03 實作工作包、同步清理與驗收規格

版本：2026-09-11 交付版（V2）  
狀態：**已批准並落地之單一 Execution Contract 規範**。

---

## 1. 派工規則與工作包結構說明

本規畫依循現行 repository 正式 admission 準則：**單一 Task 僅能對應單一 Target Repository**。

- **原 16 個設計單元**：包含 1 個文件交付單元（DOC）、3 個架構契約決策單元（D-COMMAND, D-EVOLUTION, D-JOBS）及 12 個實作單元（U1, U2, U3, U9, U4, U5, U6, U7, U8A, U10A, U8B, U10B）。
- **跨 Repository 拆分（16 → 19 任務）**：其中 U9、U8B、U10B 原規畫同時涵蓋 Pantheon 後端與 execute-plans 前端檔案。為符合單一 repo 交付規範，此 3 個工作包正式拆解為 **先 BE 後 FE** 的有序配對任務：
  1. U9 拆分為 `BFF-CANONICAL-COMMAND-API-RETIREMENT-001`（BE）與 `FE-CANONICAL-COMMAND-API-RETIREMENT-001`（FE）。
  2. U8B 拆分為 `EVO-PROGRAM-ACTIONS-CLOSURE-CORRECTIVE-001`（BE）與 `FE-EVOLUTION-PROGRAM-ACTIONS-CLOSURE-001`（FE）。
  3. U10B 拆分為 `RESEARCH-JOBS-ACTIONS-CLOSURE-CORRECTIVE-001`（BE）與 `FE-RESEARCH-JOBS-ACTIONS-CLOSURE-001`（FE）。
- **總計 19 個 Canonical Execution Tasks**：1 個文件交付 ＋ 3 個決策 ＋ 15 個實作任務。

路徑慣例：
- `BFF/` 代表 Pantheon 倉庫之 `services/control-plane/bff/`。
- `FE/` 代表獨立前端倉庫 `ajoe734/execute-plans` 之根目錄（Pantheon 內不得存放前端源碼目錄）。

每個工作包完成時均須交付：Base / Head / Merge SHA、原機制 → 保留 Owner → Consumer 對照清冊、被取代程式碼之同步刪除清單、負向與回歸測試結果，以及 TaskStore 權威狀態。

---

## 2. 19 個執行任務規格詳情

### DOC — 規畫文件與執行契約交付
- **Task ID**: `BFF-UPSTREAM-V2-PLAN-DELIVERY-001`
- **Target Repo**: `pantheon` | **Owner**: Antigravity | **Reviewer**: Codex
- **範圍**: `docs/operations/bff-upstream-v2-20260911/` 下之 6 份主文件與 `dispatch-map.json`，以及 `docs/deployment/evidence/BFF-UPSTREAM-V2-PLAN-DELIVERY-001/evidence.json`。
- **職責**: 發布批准之 V2 規畫與唯一派工映射，使所有下游 worker 基於同一份 committed 契約執行。本任務為純文件交付，無產品 runtime 修改。

---

### D-COMMAND — CommandStore 儲存契約決策
- **Task ID**: `BFF-COMMAND-STORE-CONTRACT-DECISION-001`
- **Target Repo**: `pantheon` | **Owner**: Codex | **Reviewer**: Antigravity
- **職責**: 評估並定案 CommandStore 跨實例一致性機制（單機 sidecar flock/fsync 或共享 Postgres backend 方案）。形成明確架構記錄，作為 U3 實作前置。

---

### D-EVOLUTION — Evolution 生命週期與操作契約決策
- **Task ID**: `BFF-EVOLUTION-LIFECYCLE-CONTRACT-DECISION-001`
- **Target Repo**: `pantheon` | **Owner**: Codex | **Reviewer**: Antigravity2
- **職責**: 確定 Evolution 6 狀態模型、run 狀態、review/promotion/freeze/pause/resume 等操作之合法轉移與授權規則。作為 U8A/U8B 實作前置。

---

### D-JOBS — Research／Jobs 來源與操作契約決策
- **Task ID**: `BFF-RESEARCH-JOBS-CONTRACT-DECISION-001`
- **Target Repo**: `pantheon` | **Owner**: Codex | **Reviewer**: Antigravity
- **職責**: 確認納入管理介面之 6 類 Job 來源清單、資格要求、以及 cancel/retry/archive/promote 操作語意。作為 U10A/U10B 實作前置。

---

### U1 — HTTP／Auth 真正組裝與單一安全政策
- **Task ID**: `BFF-HTTP-AUTH-COMPOSITION-SEAM-CORRECTIVE-001`
- **Target Repo**: `pantheon` | **Owner**: Codex | **Reviewer**: Antigravity
- **主要 Write-Set**: `BFF/main.py`、`BFF/core/app_factory.py`、`BFF/core/lifespan.py`、`BFF/core/http_security.py`、`BFF/core/errors.py`、`BFF/auth/policy.py`、`BFF/auth/service.py`、`BFF/auth/router.py`、`BFF/auth/handlers.py`、`services/runtime_auth_inbound.py` 及相關測試。
- **實作重點**: 抽離 app factory、middleware 與 error handlers；組裝 ProviderReadinessCache 與 lifespan；補齊 JWKS prewarm（直連/discovery/cache-hit 全覆蓋，失敗 fail closed）。
- **同步清理**: 清理 `main.py` 內搬出之重複邏輯、可選 no-op guard、重複 error envelope。
- **驗收標準**: JWT/cookie/MFA/role 驗證，401/403/500 一致格式與 CORS，SSE streaming 正常，背景 refresh 乾淨啟動與關閉。

---

### U2 — Persona Projection 單一 Owner
- **Task ID**: `BFF-LOOPS-PAPER-V5-PROJECTION-SEAM-CORRECTIVE-001`
- **Target Repo**: `pantheon` | **Owner**: Codex | **Reviewer**: Antigravity
- **主要 Write-Set**: `BFF/main.py`、`BFF/personas/service.py`、`BFF/personas/router.py`、`BFF/runtime/router.py`。
- **重要路徑說明**: 必須同交付遷移五個精確測試檔案，均直接位於 `services/control-plane/bff/` 根目錄下（**非** `tests/` 子目錄）：
  1. `services/control-plane/bff/test_p0_tw_paper_activate_honesty.py`
  2. `services/control-plane/bff/test_loop_auto_bff004_cross_loop_drill.py`
  3. `services/control-plane/bff/test_bff_promotion_review_governance.py`
  4. `services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py`
  5. `services/control-plane/bff/test_srclive_overlay_contract.py`
- **實作重點**: 合併 10 組相同 AST body 與 1 組近似 helper；搬出真實 health builder；Runtime、Persona、Assistant 共用同一 app-scoped 實例。
- **同步清理**: 刪除 `main.py` 重複 projection/builder，刪除模組全域快取別名。
- **驗收標準**: TTL/monotonic clock/deep-copy 正確，tenant/persona 隔離，上述五個測試全數通過。

---

### U3 — Command Admission／Confirmation／Audit 一次收斂
- **Task ID**: `BFF-AUDIT-ADMISSION-PROJECTION-SEAM-CORRECTIVE-001`
- **Target Repo**: `pantheon` | **Owner**: Codex | **Reviewer**: Antigravity2
- **主要 Write-Set**:
  - Core: `BFF/main.py`、`BFF/command_adapters/service.py`、`BFF/command_adapters/router.py`、`BFF/command_queue.py`、`BFF/governance/command_audit.py`。
  - Domain Callers: `personas/`、`agora/`、`governance/`、`incidents/`、`tools_integrations/`、`deployment/`、`runtime/`、`control_loops/`、`jobs/`、`evolution/`、`research/` 等 42 個檔案。
- **實作重點**: 
  1. 將 `main.py` 完整 normalization/preconditions/foundation/receipt 搬入 `CommandAdapterService`。
  2. 單一 confirmation 狀態機（create/read/redeem/revoke/expiry），同鍵並發與 active-target 在同 transaction 驗證。
  3. Replay 綁定 `tenant + actor + canonical namespace + key`；更換 target 時回傳 409。
  4. 分清「待執行指令」與「已完成 domain mutation 之稽核」，避免二次執行副作用。
- **同步清理**: 刪除 `main.py`、service fallback、router-local persist；刪除 Persona 重複 semantic helper（徹底修復 NameError）；刪除 Tools/Governance 假 accepted fallback；修復 Alert ack 吞錯問題。
- **驗收標準**: 同鍵 20 並發恰一次 dispatch；同鍵不同 payload 回傳 409；跨 tenant/actor 隔離；CommandStore 實例一致性與無死鎖。

---

### U9 — 舊 Generic Write API 退役（BE）
- **Task ID**: `BFF-CANONICAL-COMMAND-API-RETIREMENT-001`
- **Target Repo**: `pantheon` | **Owner**: Claude | **Reviewer**: Codex
- **主要 Write-Set**: `BFF/main.py`、`BFF/command_adapters/router.py`、`BFF/command_adapters/service.py`、OpenAPI 契約、現役 smoke 與測試（共 50 檔）。
- **實作重點**: 保留 `POST /bff/v1/commands`；退役重複之 `POST /bff/actions/{...}` 與 `POST /api/v1/operator/commands`；保留 status 查詢與 action catalog。
- **同步清理**: 移除後端所有已退役路由之 handler 與 OpenAPI 宣告。

---

### U9-FE — 舊 Generic Write API 消費端遷移（FE）
- **Task ID**: `FE-CANONICAL-COMMAND-API-RETIREMENT-001`
- **Target Repo**: `execute_plans` | **Owner**: Claude | **Reviewer**: Codex
- **主要 Write-Set**: `src/lib/bff-v1/writes.ts`、`src/lib/bff-v1/personas.ts`、`src/lib/bff-v1/paths.ts`、`src/management/pages/PersonaDetail.tsx`、HighRiskConfirm 消費端及測試（共 22 檔）。
- **實作重點**: 前端全面改接 canonical command client；四個 Persona 動作（run_eval, restrict_tools, suspend, retire）改接同一傳輸層；Confirmation expiry 改讀伺服器值。
- **驗收標準**: 前端無任何舊 POST 呼叫端，exact BE/FE 配對測試通過。

---

### U4 — Journal Context 與唯一 Visibility Policy
- **Task ID**: `BFF-JOURNAL-CONTEXT-SEAM-CORRECTIVE-001`
- **Target Repo**: `pantheon` | **Owner**: Codex | **Reviewer**: Antigravity
- **主要 Write-Set**: `BFF/main.py`、`BFF/agora/service.py`、`BFF/agora/interaction/router.py`、`BFF/agora/interaction/context_resolver.py` 及測試。
- **實作重點**: typed resolver 讀取 bound AgoraService 與 typed journal readers；維持各 domain event/journal 原有 owner。
- **同步清理**: 刪除 main resolver 與轉 unscoped reader 之不良 fallback。
- **驗收標準**: 來源缺失明確 unavailable，`audience_verified: false` 不擅改，B05 真 router 全檔回歸。

---

### U5 — Assistant Typed Source Collectors
- **Task ID**: `BFF-ASSISTANT-SOURCE-COLLECTOR-SEAM-CORRECTIVE-001`
- **Target Repo**: `pantheon` | **Owner**: Claude | **Reviewer**: Codex
- **主要 Write-Set**: `BFF/main.py`、`BFF/assistant/context_composer.py`、`BFF/assistant/routes.py`、`BFF/assistant/source_collectors.py`。
- **實作重點**: typed collector 由真實 PersonaService、command audit projector 與 domain read ports 讀取。
- **同步清理**: 刪除 main collector 與回呼 main；不引進 shell 或 repo write 依賴。
- **驗收標準**: B02 四檔測試通過；在 Jobs 真實來源完成前，明確標記為來源未完成而非假資料。

---

### U6 — Management NL 單一 Use Case 與 Durable Replay
- **Task ID**: `BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001`
- **Target Repo**: `pantheon` | **Owner**: Codex | **Reviewer**: Antigravity2
- **主要 Write-Set**: `BFF/main.py`、`BFF/core/app_factory.py`、`services/control-plane/bff/management_nl_command_idempotency.py`、`BFF/assistant/management_service.py`。
- **實作重點**: ask 與 stream 共用單一 NL use case 及既有 store；先高風險拒絕與 tenant 檢查，再執行 retrieval/provider；未知結果保持 uncertain。
- **同步清理**: 徹底刪除記憶體字典、雙寫 bridge 與切換 flag。保留對話 session/turn。
- **驗收標準**: 重複請求回傳 409、重啟與中斷後 stream 不重送 provider，背景任務乾淨回收。

---

### U7 — Evolution Mutation-Review／Journal Read Truth
- **Task ID**: `BFF-EVOLUTION-REVIEW-JOURNAL-SEAM-CORRECTIVE-001`
- **Target Repo**: `pantheon` | **Owner**: Antigravity | **Reviewer**: Codex
- **主要 Write-Set**: `BFF/main.py`、`BFF/governance/service.py`、`BFF/governance/router.py`、`BFF/evolution/service.py`、`BFF/evolution/router.py`。
- **實作重點**: 沿用 Governance policy，直接與 nested 讀取一致由 actor/state/evidence 導出 allowedActions；證據缺失回傳 503/unavailable。
- **同步清理**: 刪除 nested projection copy、刪除固定 fresh/ok 與假健康種子資料。
- **驗收標準**: B10 已記錄之 8 個失敗案例全部重驗通過。

---

### U8A — Evolution Program 持久化 Aggregate／Typed Ports
- **Task ID**: `EVO-PROGRAM-OWNER-CONTRACT-CORRECTIVE-001`
- **Target Repo**: `pantheon` | **Owner**: Codex | **Reviewer**: Antigravity
- **主要 Write-Set**: `services/evolution/main.py`、`client.py`、`models.py`、`BFF/evolution/service.py`、`BFF/evolution/router.py`、`BFF/ports/read_surface_ports.py` 等 27 檔。
- **實作重點**: 建立 program aggregate，沿用 `PostgresJsonOwnerStore` 交易原語，讀寫分離；PATCH 限制可變欄位，生命週期改動走同一 transition policy。
- **同步清理**: 刪除向 read port 呼叫 write 方法、刪除直接 DB fallback 與 hardcoded prog-001。
- **驗收標準**: 真 DB CRUD、20 同鍵並發一致性、revision race 與 crash 測試通過。

---

### U10A — Research／Jobs 真實 Owner 與讀取接線
- **Task ID**: `BFF-RESEARCH-JOBS-OWNER-BINDING-CORRECTIVE-001`
- **Target Repo**: `pantheon` | **Owner**: Codex | **Reviewer**: Antigravity2
- **主要 Write-Set**: `BFF/main.py`、`BFF/command_adapters/evolution_adapter.py`、`BFF/command_adapters/registry.py`、`BFF/jobs/router.py`、`BFF/research/router.py` 等 39 檔。
- **實作重點**: ResearchWriteOwner 先具備 tenant/idempotency/transaction 一致性；兩套 experiment API 改接同一 owner；Registry 一個 command tuple 恰一 handler；Jobs list/detail/SSE 綁定真實來源與 scope。
- **同步清理**: 刪除記憶體 `_experiments` writer、直接 DB fallback、修復 SSE 忽略 jobId。
- **驗收標準**: 真實來源 ID 讀回、未授權拒絕、B02/B10/B16 受影響測試通過。

---

### U8B — Evolution 真實操作（BE）
- **Task ID**: `EVO-PROGRAM-ACTIONS-CLOSURE-CORRECTIVE-001`
- **Target Repo**: `pantheon` | **Owner**: Antigravity | **Reviewer**: Codex
- **主要 Write-Set**: `services/evolution/` dispatch outbox / worker / receipts、`BFF/evolution/service.py`、`BFF/command_adapters/evolution_adapter.py` 等 18 檔。
- **實作重點**: program → decision → run → candidate → approval → terminal receipt 沿用既有執行鏈，不自造第二個 scheduler。
- **同步清理**: 刪除假 run ID、未驗證的 executed 狀態。

---

### U8B-FE — Evolution 真實操作（FE）
- **Task ID**: `FE-EVOLUTION-PROGRAM-ACTIONS-CLOSURE-001`
- **Target Repo**: `execute_plans` | **Owner**: Antigravity | **Reviewer**: Codex
- **主要 Write-Set**: EvolutionDetail、evolution client、writes/DTO、stateMachine 等 17 檔。
- **實作重點**: 補齊 Resume handler、展示真實 run/candidate ID、真實 action 執行結果。
- **同步清理**: 移除前端固定空值列表與 browser-local 假儲存。

---

### U10B — Research／Jobs 真實操作（BE）
- **Task ID**: `RESEARCH-JOBS-ACTIONS-CLOSURE-CORRECTIVE-001`
- **Target Repo**: `pantheon` | **Owner**: Antigravity2 | **Reviewer**: Codex
- **主要 Write-Set**: 選定 domain owner 之 action 綁定、experiment/job adapters 等 13 檔。
- **實作重點**: 實作取消（requested → accepted → worker stopped）、重試（eligible 狀態與 attempt lineage）、封存與 promote 語意。
- **同步清理**: 刪除 unsupported 假 executed、假 cancel 成功。

---

### U10B-FE — Research／Jobs 真實操作（FE）
- **Task ID**: `FE-RESEARCH-JOBS-ACTIONS-CLOSURE-001`
- **Target Repo**: `execute_plans` | **Owner**: Antigravity2 | **Reviewer**: Codex
- **主要 Write-Set**: FE jobs/experiments 操作端、CommandCenter、StrategyDetail 等 15 檔。
- **實作重點**: 逐 source 呈現真實動作結果與進度，未知外部結果不自動盲目重試。

---

## 3. 其他 3 個保留 Blocked 任務之處置界線

| 任務 ID | 處置方式與職責界線 | 嚴格禁止事項 |
|---|---|---|
| `BFF-TEST-FULL-MIGRATION-CORRECTIVE-001` | 待 8 個原 blocked 批次 ＋ B08/B11/B12/B18 完成後，做全量真 router 與跨 batch 回歸。 | 不得把 source scope 塞入該 evidence-only 任務；不另造競爭修復器。 |
| `PPL-ALLOC-007` | 尋求真正 authenticated import 與 role/generation provenance；不足時如實維持 hold。 | 不 reset generation、不偽造完成、不重做 FE。 |
| `FE-EXACT-PAIR-PROTOCOL-001` | 於 PR #745 補齊 parent delegation、image qualification、whole-pair restore。 | 不另造 release controller，不以 FE-only 代表整體恢復。 |
