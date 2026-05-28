# PERSONA_ONBOARDING_WIZARD_SPEC

- **Task**: PERSONA-ONBOARD-2026-05-28
- **Status**: draft for frontend implementation
- **Tier**: L4 — frontend UX / BFF surface contract
- **Owner（提案）**: Operator Console / Lovable frontend team
- **Owner（驗收）**: Pantheon BFF + Governance Plane
- **依據（L1 不可更動）**:
  - [PERSONA_RUNTIME_MODEL.md](../../../PERSONA_RUNTIME_MODEL.md)
  - [BINDING_AND_DEPLOYMENT_SEMANTICS.md](../../../BINDING_AND_DEPLOYMENT_SEMANTICS.md)
- **配套 BFF 改動**: F4 patch — `GET /api/v1/operator/persona-management/{persona_id}` 新增 `data.health` surface（同 `bff/management/persona-fleet` 的 `_project_persona_fleet_health()` 計算）

---

## 1. 文件目的

當管理者透過 frontend 建立一個 persona 之後，現況是：

- Persona 只停在 `lifecycle_state = "draft"`
- 沒有 capital pool binding、沒有 deployment plan、沒有 runtime binding、沒有 runtime
- UI 端不顯示「為什麼這個 persona 沒在跑」「下一步要做什麼」
- 管理者必須自己跨多個頁面、知道每個物件的存在順序，才能讓 persona 動起來

這個落差**不是架構錯誤，是 UX 缺角**。L1 policy 故意把 binding / deployment / runtime 拆三層是為了 governance / audit / 責任分離（見 [BINDING_AND_DEPLOYMENT_SEMANTICS.md §3](../../../BINDING_AND_DEPLOYMENT_SEMANTICS.md)）。但「拆三層」是架構責任，「使用者體驗統一」是 UX 責任。

本 spec 規範 frontend 怎麼把架構複雜度收回到 UI 後台，**而不讓 BFF 提供 composite endpoint**（BFF 維持 atomic CRUD，符合 governance plane 寫入授權的拆分）。

---

## 2. 設計原則

1. **BFF 不做 composite**：所有 lifecycle 變動仍走 atomic endpoint（`POST /bff/personas/{id}/actions`、`POST /api/v1/bindings`、…），governance / audit trail 維持每段獨立。
2. **Frontend 負責 orchestration**：wizard 串呼叫順序，但每一步背後仍是 governance 認可的單一動作。
3. **Wizard 與 advanced mode 並存**：
   - Wizard = 給「我要讓這個 persona 跑起來」的人
   - Advanced mode = 給只想做其中一步的 admin（例如只更新 binding、或單獨建 deployment plan）
4. **缺什麼必須顯示**：任何 persona 卡在某階段，UI 必須顯示具體卡點 + 下一步可執行動作。**「未完成」不等於「不顯示」**。
5. **不可繞過 governance gate**：dev 環境 wizard 可設「auto-approve」模式（明示 banner），prod / canary / live 必須走人為 approval queue。

---

## 3. Persona 啟動 5 階段狀態機

Persona 從建立到實際執行，跨四個 plane：

```text
[Persona Plane]    [Capital Pool Plane]    [Governance Plane]    [Execution Plane]
     │                      │                      │                      │
     ▼                      ▼                      ▼                      ▼
[1] Persona            [2] PersonaCapital     [3] DeploymentPlan    [5] Runtime
    lifecycle              Binding                                       啟動 +
    advance                                                              telemetry
                                                  ▼
                                            [4] Approval（governance）
                                                  │
                                                  ▼
                                            RuntimeBinding（write）
```

### 階段對照表

| 階段 | 物件 | 起始狀態 | 完成狀態 | 寫入授權 plane |
|---|---|---|---|---|
| 1 | Persona | `draft` | `paper_owner`（最小可 sponsor 部署的狀態） | Persona Plane → Governance Plane |
| 2 | PersonaCapitalBinding | 不存在 | `status=active`、`role=paper_owner`、`allowed_deployment_scope=paper` | Governance Plane → Capital Pool Plane |
| 3 | DeploymentPlan | 不存在 | `state=approved` 且綁定 binding + approved artifact | Governance Plane |
| 4 | ApprovalDecision | 不存在 | `state=approved`（dev 可 auto，prod 必須人工） | Governance Plane |
| 5 | RuntimeBinding + Runtime | 不存在 | `runtime_status=active` 並且 runtime-manager 啟動 OpenClaw agent loop | Execution Plane |

**任一階段失敗，後續階段必須阻擋，UI 顯示阻擋原因。**

---

## 4. 每階段的 BFF endpoint 對應

> 所有 endpoint 均為 atomic，frontend 自行串呼叫。endpoint 詳細 schema 見 [BFF_API_CONTRACT.md](../../../services/control-plane/bff/BFF_API_CONTRACT.md) 與 BFF live `/openapi.json`。

| 階段 | 動作 | HTTP | endpoint | 必要欄位 | 預期回應 |
|---|---|---|---|---|---|
| 1 | Persona advance lifecycle | POST | `/bff/personas/{persona_id}/actions/{action_id}` | `action_id=AdvanceLifecycle`、`target_state=paper_owner`、`confirm_token` | 200 + 新 `lifecycle_state` |
| 2a | 建 capital pool（若無） | POST | `/bff/capital-pools` | name / risk_policy_ref / params | 201 + `pool_id` |
| 2b | Pool advance（draft → active） | POST | `/bff/capital-pools/{pool_id}/actions/{action_id}` | `action_id=ApprovePool` 之類（見 action_catalog） | 200 |
| 2c | 建 PersonaCapitalBinding | POST | `/api/v1/bindings`（或 `/bff/management/...`，視 wave 進度） | persona_id、capital_pool_id、role、allowed_deployment_scope、mandate、budget | 201 + `binding_id` |
| 3 | 建 DeploymentPlan | POST | `/api/v1/deployment-plans` | binding_id、artifact_id（已 approved）、deployment_mode=paper、capital_pool_id | 201 + `plan_id` |
| 4 | 提送 governance approval | POST | `/bff/management/governance/approval-queue` 之類 endpoint（待 BFF wave 補齊） | plan_id | 201 + `decision_id` |
| 5a | runtime-manager 啟動 | POST | `/bff/runtimes/{runtime_id}/actions/{action_id}` | `action_id=PauseRuntime` 反向 / 或 `StartRuntime`（見 action_catalog） | 200 + runtime_status |
| 5b | （可選）餵 training session 暖機 | POST | `/api/v1/training/sessions` | persona_id、binding_id、initial signals | 201 + `session_id` |

### 4.1 哪些 endpoint 目前在 BFF 缺/未驗

依 [pantheon_bff_delta_2026-05-24](../../pantheon_bff_api_gap_2026-05-24_delta/) audit，下列項目目前在 lupin dev 仍 404 或 stub，前端實作 wizard 前**必須先 audit**：

- 階段 1 的 `AdvanceLifecycle` action 是否已對應到 persona_registry_service 寫入（目前 `persistenceMode=bff_local_dev_store` 暗示尚未流入 canonical service）
- 階段 2c 的 `POST /api/v1/bindings` 是否已 wired
- 階段 4 governance approval-queue write endpoint
- 階段 5a runtime start action 是否已綁到 runtime-manager

**Frontend 實作 wizard 前必須 step 1: 跑一次 8 個 endpoint smoke test，把實際 status code / response shape 列出來再開工。**

---

## 5. health.reasons 中文映射表

BFF `_project_persona_fleet_health()`（見 [main.py:25833](../../../services/control-plane/bff/main.py)）回傳的 `health.reasons` 是英文 enum string，frontend 必須用以下表把它翻成 i18n key 並顯示中文（注意 i18n key 不可改，中文 fallback 可改）。

| `reasons` 值 | i18n key | 中文（zh-TW） | 嚴重度 | 對應階段 | 建議下一步 |
|---|---|---|---|---|---|
| `persona_lifecycle_not_active` | `persona.health.lifecycle_not_active` | Persona 仍在 `draft` / 非 `active` 狀態，無法 sponsor 部署 | medium | 階段 1 | 開啟 wizard 第 1 步 / 或 advanced「升級 lifecycle」 |
| `no_runtime_binding` | `persona.health.no_runtime_binding` | 尚未建立 RuntimeBinding，runtime-manager 沒有要執行的工作 | medium | 階段 5 | 確認階段 2–4 已完成 / 開啟 wizard |
| `active_incident` | `persona.health.active_incident` | 此 persona 關聯的 incident 尚未結案 | high | 維運 | 跳至 incident detail page |
| `drawdown_threshold` | `persona.health.drawdown_threshold` | 最新 telemetry drawdown ≥ 10%，已觸發警戒 | high | 維運 | 評估是否觸發 risk-off |
| `negative_pnl` | `persona.health.negative_pnl` | 最新 telemetry pnl ≤ -5% | medium | 維運 | 評估是否暫停 runtime |
| `runtime_status_attention` | `persona.health.runtime_status_attention` | 部分 runtime 狀態異常（非 active/ready/running/idle） | medium | 維運 | 跳至 runtime detail page |

### 5.1 未來新增 reasons 的命名規則

任何新增 reason 字串必須：

- 使用 snake_case 英文 enum
- 對應一筆 i18n key（前端統一管理）
- 同步更新本表
- 同步更新 [`_project_persona_fleet_health()`](../../../services/control-plane/bff/main.py)
- 同步更新本文件 §6 渲染對應

---

## 6. Persona Card readiness 渲染規格

### 6.1 卡片頂部 health 摘要

```text
┌─────────────────────────────────────────────────┐
│ TW-BlueChip-Guard                  [Draft] [⚠] │
│ generalist · low risk · pantheon-dev-browser    │
│                                                  │
│ ● Persona lifecycle              ○ 未升級 (1/5)  │  ← 卡片 readiness checklist
│ ○ Capital binding                ○ 未建立 (2/5)  │
│ ○ Deployment plan                ○ 未建立 (3/5)  │
│ ○ Approval                       ○ 未送審 (4/5)  │
│ ○ Runtime                        ○ 未啟動 (5/5)  │
│                                                  │
│ ▸ 點此啟動 Onboarding Wizard                    │
│ ▸ Advanced：手動操作                            │
└─────────────────────────────────────────────────┘
```

### 6.2 渲染規則

- **5 個 checklist 項目固定顯示**（不論完成與否），對應 §3 的 5 階段
- 每個項目左側用 `●`（完成 / lifecycle 已通過該層）或 `○`（未完成）
- **健康度 chip**：
  - `health.status === "healthy"` → 綠色「Healthy」
  - `"degraded"` → 黃色「Degraded」+ 顯示 reasons 數量
  - `"critical"` → 紅色「Critical」+ 顯示 reasons 數量
- 將 `health.reasons` 列為 expandable tooltip，每個 reason 顯示中文（用 §5 表）
- **若任一 reason 對應 §5 的「建議下一步」**，在該 reason 旁顯示一顆可點按鈕

### 6.3 已完成 persona 的顯示（happy path）

當 5 階段都 ✅，卡片改為：

```text
┌─────────────────────────────────────────────────┐
│ TW-BlueChip-Guard              [Active] [✅]   │
│ generalist · low risk · paper deployment        │
│                                                  │
│ 5/5 ● Live runtime · 2 active sessions          │
│ PnL: +2.1% · Drawdown: 1.4% · 24h trades: 18    │
│                                                  │
│ ▸ View runtime detail / telemetry               │
└─────────────────────────────────────────────────┘
```

### 6.4 必要欄位 → BFF surface 來源

| UI 元素 | 來源 BFF surface | 取值 path |
|---|---|---|
| Persona 名稱 | `persona-management/{id}` | `data.persona.name` |
| Lifecycle state | `persona-management/{id}` | `data.persona.lifecycle_state` |
| Health chip | `persona-management/{id}` | `data.health.status`（**F4 新增**） |
| Reasons tooltip | `persona-management/{id}` | `data.health.reasons[]`（**F4 新增**） |
| Binding ✅/❌ | `persona-management/{id}` | `data.bindings.length > 0` |
| Runtime ✅/❌ | `persona-management/{id}` | `data.runtimeBindings.length > 0`（**F4 新增**） |
| Active incident 警示 | `persona-management/{id}` | `data.activeIncidents.length > 0`（**F4 新增**） |
| Telemetry summary | `persona-management/{id}` | `data.health.latest_telemetry_at`（**F4 新增**） |

---

## 7. Onboarding Wizard 5-step 流程設計

### 7.1 進入點

- 從 persona detail page 點「Onboarding Wizard」按鈕
- 從 persona list 點某張 draft card 上的「啟動」捷徑
- 直接 URL：`/personas/{persona_id}/onboarding`

### 7.2 Step 1：升級 Persona Lifecycle

| 欄位 | 內容 |
|---|---|
| UI 標題 | 「升級 Persona 到 PAPER_OWNER」 |
| 顯示資訊 | 目前 state、目標 state、advance 後可開放的能力（sponsor paper deployment） |
| 必填輸入 | `confirm_token`（dev 可 auto-fill；prod 需手動 + MFA） |
| 呼叫 | `POST /bff/personas/{persona_id}/actions/AdvanceLifecycle` |
| 成功條件 | response `lifecycle_state === "paper_owner"` |
| 失敗處理 | 顯示 ErrorCode + retryable hint；不允許跳下一步 |

### 7.3 Step 2：選定 Capital Pool 並建 Binding

| 欄位 | 內容 |
|---|---|
| UI 標題 | 「選擇 Capital Pool 並建立綁定」 |
| 顯示資訊 | 列出目前 active 的 capital pool；若無則顯示「先建立 pool」按鈕（嵌入子流程） |
| 必填輸入 | capital_pool_id、role（advisor/paper_owner/live_owner，wizard 預設 paper_owner）、allowed_deployment_scope（預設 paper）、mandate、budget |
| 呼叫 | (a) 若 pool 是 draft → `POST /bff/capital-pools/{pool_id}/actions/ApprovePool` ;（b）`POST /api/v1/bindings` |
| 成功條件 | 取得 `binding_id` 且 `status=active` |
| 失敗處理 | 若 governance 拒絕 → 顯示拒絕原因；不允許跳下一步 |

### 7.4 Step 3：選定 Artifact 並建 DeploymentPlan

| 欄位 | 內容 |
|---|---|
| UI 標題 | 「選擇 Artifact 並建立部署計畫」 |
| 顯示資訊 | 該 binding 可選用的 approved artifacts（從 strategy-specs 過濾） |
| 必填輸入 | artifact_id、deployment_mode=paper（wizard 鎖定 paper）、capital_pool_id、rollback_target（建議帶預設） |
| 呼叫 | `POST /api/v1/deployment-plans` |
| 成功條件 | 取得 `plan_id` |
| 失敗處理 | 若 artifact 未 approved → block，提示「先到 strategy-specs 走 approval」 |

### 7.5 Step 4：Governance Approval

| 欄位 | 內容 |
|---|---|
| UI 標題 | 「送交治理審核」 |
| 顯示資訊 | plan 摘要、reviewer 角色、SLA 預估 |
| 必填輸入 | reviewer comment（可空）、confirm_token |
| 呼叫 | `POST /api/v1/approval-decisions`（提案）+ 之後 `POST /api/v1/approval-decisions/{decision_id}` 由 reviewer approve |
| dev 環境 | 可開「auto-approve」toggle，UI 須有明顯 banner 「⚠ Auto-approve enabled — only valid in dev」 |
| 成功條件 | `decision_state === "approved"` |
| 失敗處理 | 若 rejected → 顯示拒絕原因；可選擇修改 plan 或廢棄 |

### 7.6 Step 5：建立 RuntimeBinding 並啟動 Runtime

| 欄位 | 內容 |
|---|---|
| UI 標題 | 「啟動 Runtime」 |
| 顯示資訊 | 將要啟動的 runtime 環境、預期 OpenClaw agent loop 啟動 |
| 必填輸入 | confirm_token |
| 呼叫 | `POST /bff/runtimes/{runtime_id}/actions/StartRuntime` 或對應 action_id（依 action_catalog） |
| 成功條件 | runtime_status 出現於 `/api/v1/operator/runtime-state`，且 `runtime_status === "active"` |
| 失敗處理 | 顯示 runtime-manager 錯誤；提供「rollback to step 4」入口 |

### 7.7 完成

- Wizard 結束畫面顯示 5/5 + 連結到「View runtime detail」
- 同時觸發一次 `persona-management/{id}` refresh，使 detail page 顯示新的 health 狀態

---

## 8. Advanced mode（單獨開每階段）

- 每階段在 persona detail page 各有獨立區塊（lifecycle / bindings / deployment plans / approvals / runtimes）
- 每個區塊有「Edit / Create / Advance」按鈕，**不受 wizard 阻擋**
- 進階使用者可只做其中一步（例如新增第二個 binding 到不同 pool）
- 任何單步操作完成後，**health.reasons 必須同步更新**（前端 refetch `persona-management/{id}`）

### 8.1 權限分流

- Wizard：開放給 `role=persona_operator` 以上
- Advanced lifecycle action：開放給 `role=governance_reviewer` 以上
- Advanced runtime action：開放給 `role=runtime_operator` 以上
- Live mode advance：必須 `role=live_owner_approver` + MFA

（角色定義以實際 `_extract_identity()` / `_require_*_role()` 為準）

---

## 9. 錯誤處理 / Rollback

### 9.1 階段內失敗

- 任何 step 失敗，顯示 ErrorCode（canonical 26 enum，見 [pantheon_bff_api_gap_2026-05-25_delta_v3](../../pantheon_bff_api_gap_2026-05-25_delta_v3/)）
- 提供：retry / cancel wizard / 跳到 advanced mode

### 9.2 跨階段 rollback

- 完成 step N 之後在 step N+1 失敗 → 不自動 rollback step N（避免破壞 audit trail）
- UI 顯示「目前停在 step N」，使用者可：
  - 從 step N+1 重試
  - 或進 advanced mode 手動 rollback step N（例如刪除剛建的 binding）

### 9.3 dev 環境 reset 捷徑

- 提供「Reset persona to draft」按鈕（dev only），背後串：
  - 刪 RuntimeBinding → 刪 DeploymentPlan → 刪 Binding → 把 persona 降回 draft
- prod 不開放此按鈕

---

## 10. 與既有 BFF surface 的對應

| BFF endpoint | wizard / readiness 用途 | F4 新增 |
|---|---|---|
| `GET /bff/management/persona-fleet` | 列表頁的 readiness summary | （已有 health） |
| `GET /api/v1/operator/persona-management/{persona_id}` | detail page 的 readiness summary + wizard 進入點 | ✅ 補上 `data.health` |
| `GET /api/v1/personas/{persona_id}` | wizard step 1 read | （已有） |
| `GET /api/v1/bindings` | wizard step 2 read | （已有） |
| `GET /api/v1/deployment-plans` | wizard step 3 read | （已有） |
| `GET /api/v1/operator/runtime-state` | wizard step 5 verify | （已有，degraded） |
| `POST /bff/personas/{id}/actions/{action_id}` | step 1 + 5 write | （已有） |
| `POST /api/v1/bindings` | step 2 write | 待 audit |
| `POST /api/v1/deployment-plans` | step 3 write | 待 audit |
| `POST /api/v1/approval-decisions` | step 4 write | 待 audit |

---

## 11. 開放問題

1. **Capital pool advance 的 action_id** 在 action_catalog 中尚未確認對應 ID（看 [bff/action_catalog.py:440](../../../services/control-plane/bff/action_catalog.py) 只有 `CapitalPoolAction` umbrella）。需要 BFF 補一份明確的 lifecycle action enum。
2. **Persona advance 的 action_id**：同上，需從 `PersonaAction` umbrella 落到具體 enum。
3. **dev auto-approve 模式的後端開關**：是否要在 `_extract_identity()` 加 `dev_auto_approve` claim、或直接由 frontend 在 step 4 自動呼叫 reviewer endpoint？建議走後者，避免後端帶 mode flag。
4. **Persona persistence 從 `bff_local_dev_store` 流入 `persona_registry_service`** 的時點 — 是 step 1 lifecycle advance 時自動 promote，或需要單獨呼叫？wizard step 1 觸發後必須驗證 `canonicalWriteAuthority` 改變。
5. **i18n key 列表是否需要單獨檔案**：建議 frontend 在自己的 i18n 模組維護，本 spec 只負責 reasons enum 與中文 fallback 的對應。

---

## 12. 驗收條件（DoD）

frontend wizard 落地 PR 必須通過：

- [ ] 8 個關鍵 endpoint 的 smoke test 都過（見 §4.1）
- [ ] 每張 persona card 顯示 5-step checklist + health chip
- [ ] reasons tooltip 顯示中文（依 §5 表）
- [ ] wizard 從 draft persona 串完 5 步在 lupin dev 可成功啟動 1 個 runtime
- [ ] 任一階段失敗的 UI 顯示 ErrorCode 與「下一步」按鈕
- [ ] advanced mode 仍可獨立操作每階段
- [ ] dev auto-approve banner 在 step 4 正確顯示
- [ ] BFF `persona-management/{id}` 的 `data.health` 與 `persona-fleet` item 的 `health` 欄位完全一致（F4 PR 已過）

---

## 13. 相關文件

- [PERSONA_RUNTIME_MODEL.md](../../../PERSONA_RUNTIME_MODEL.md) — persona / session / capability 三層分離 + lifecycle gating
- [BINDING_AND_DEPLOYMENT_SEMANTICS.md](../../../BINDING_AND_DEPLOYMENT_SEMANTICS.md) — binding / deployment / runtime 三層拆分
- [PAPER_CANARY_LIVE_POLICY.md](../../../PAPER_CANARY_LIVE_POLICY.md) — deployment stage gating
- [SA-15_governance_boundary_gap_analysis.md](../pantheon_sa/SA-15_governance_boundary_gap_analysis.md) — cross-boundary enforcement
- [BFF API gap v3](../pantheon_bff_api_gap_2026-05-25_delta_v3/) — 24/24 management routes 與 ErrorCode 對齊狀態
