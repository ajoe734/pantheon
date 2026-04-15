# OPERATOR_ACCEPTANCE_MATRIX

Last updated: 2026-04-14
Status: canonical operator acceptance matrix for Pantheon
Tier: L1 Platform Architecture & Policy
Owner: Claude (BG-006)
Reviewer: Codex
Scope: Operator surface taxonomy, access paths, permission model, degraded behaviour, and acceptance drill status for BFF, internal API, CLI, fallback, and support-only paths
Conflict rule: This document is the authoritative operator acceptance spec. Wider HA/resilience semantics defer to `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`; emergency action semantics defer to `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`; deployment and binding write authority defer to `BINDING_AND_DEPLOYMENT_SEMANTICS.md`.

---

## 1. 目的

本文件回答以下問題：

1. Pantheon 提供哪些 operator 控制面路徑（surfaces）？
2. 每條路徑的正式性質為何（authoritative / composed / fallback / support-only）？
3. 每條路徑可執行哪些動作，以及需要哪些權限？
4. 各路徑對應的 canonical object 是什麼？
5. 降級時（degraded mode）的行為與限制為何？
6. 各路徑的測試狀態與 operator drill 狀態為何？

---

## 2. 路徑類型定義

| 類型 | 定義 |
|---|---|
| **authoritative** | 這條路徑是該操作的唯一或主要真相來源；所有寫入皆對 canonical object 直接生效，且帶有完整 audit。 |
| **composed** | 路徑聚合多個下游服務，提供方便操作的 façade；底層寫入仍由 authoritative 服務負責。 |
| **fallback** | 主路徑不可用時啟用；語義上等同 authoritative，但操作對象更窄、保護更嚴，需要更高階 RBAC。 |
| **support-only** | 僅供診斷/debug 使用；不執行任何域寫入；不得用於生產控制。 |

---

## 3. 路徑目錄（Surface Inventory）

Pantheon 定義五條 operator 路徑：

| Surface ID | 路徑名稱 | 入口 | 類型 | 上線前提 |
|---|---|---|---|---|
| `S-BFF` | BFF UI / command surface | Pantheon Console → `pantheon-bff` | composed | BFF ≥ 1 healthy replica + LB |
| `S-IAPI` | Internal API surface | 直連各 internal service API | authoritative | 各 service 健康 + mTLS |
| `S-CLI` | Admin CLI surface | `pantheon-admin` CLI tool | fallback | CLI binary + service token |
| `S-EMRG` | Emergency fast path | kill-switch controller → runtime-manager fast path | fallback | runtime-manager healthy |
| `S-SUPP` | Support-only / diagnostic | debug / health / trace endpoints | support-only | deployment runtime + support role |

---

## 4. Operator Acceptance Matrix

每個操作類別對應多條路徑，並標明各路徑的正式性質、canonical object、降級行為、所需權限、測試狀態與 drill 狀態。

### 4.1 部署與提升（Deployment & Promotion）

| Surface | 操作 | Canonical Object | 路徑類型 | 所需 Role | 降級行為 | 測試狀態 | Drill 狀態 |
|---|---|---|---|---|---|---|---|
| `S-BFF` | 提交 deployment review / approve | `ApprovalDecision`, `DeploymentPlan` | composed | `governance.approver` | BFF 不可用時顯示 degraded panel；動作需改走 `S-IAPI` | spec defined | not drilled |
| `S-BFF` | 建立 / 查詢 RuntimeBinding | `RuntimeBinding` | composed | `deployment.operator` | BFF 不可用時 read-only；寫入需走 `S-CLI` 或 `S-IAPI` | spec defined | not drilled |
| `S-IAPI` | 直接寫入 DeploymentPlan | `DeploymentPlan` | authoritative | `deployment.admin` + mTLS | N/A（本身即降級目標） | spec defined | not drilled |
| `S-CLI` | 發起 deployment via CLI | `ApprovalDecision`, `RuntimeBinding` | fallback | `deployment.admin` | 僅限 emergency promotion；audit mandatory | spec defined | not drilled |

**驗收條件：**
- `DeploymentPlan` 必須由 `promotion-review-svc` 或具有 `deployment.admin` role 的 internal API call 所寫入；BFF 僅為 façade
- `RuntimeBinding` 只能在 `DeploymentPlan` 存在且 approved 後建立
- BFF 故障期間，operator 可通過 `S-IAPI` 或 `S-CLI` 完成 deployment；兩條路徑都必須有 audit trail

---

### 4.2 Runtime 控制（Runtime Control）

| Surface | 操作 | Canonical Object | 路徑類型 | 所需 Role | 降級行為 | 測試狀態 | Drill 狀態 |
|---|---|---|---|---|---|---|---|
| `S-BFF` | Pause runtime | `RuntimeBinding.status` via `runtime-manager-svc` | composed | `runtime.operator` | BFF 不可用時不可操作；需走 `S-IAPI` 或 `S-CLI` | spec defined | not drilled |
| `S-BFF` | Rollback | `RuntimeBinding` via `runtime-manager-svc` | composed | `runtime.operator` | BFF 不可用時不可操作；需走 `S-IAPI` 或 `S-CLI` | spec defined | not drilled |
| `S-IAPI` | Pause / rollback via runtime-manager API | `RuntimeBinding`, `RuntimeStatus` | authoritative | `runtime.admin` + mTLS | N/A | spec defined | not drilled |
| `S-EMRG` | Emergency pause / liquidate / risk-off | `RuntimeBinding`, `RuntimeStatus` via kill-switch controller | fallback | `emergency.operator` | 此路徑本身為降級路徑；若 runtime-manager 不可用則無法執行 | spec defined | not drilled |
| `S-CLI` | Pause via admin CLI | `RuntimeBinding.status` | fallback | `runtime.admin` | BFF 不可用時的 admin CLI 直接路徑；非 kill-switch 緊急路徑 | spec defined | not drilled |

**驗收條件：**
- BFF 不得是 kill-switch 唯一路徑（見 `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` §8.5）
- `S-EMRG` 路徑必須能在 BFF 完全不可用情況下獨立運作
- Pause / liquidate / risk-off 動作必須寫入 `RuntimeStatus` 並觸發 audit event
- 所有 emergency path 動作需在 5 秒內送達 runtime-manager（SLA 待定義）

---

### 4.3 Kill Switch（緊急停機）

| Surface | 操作 | Canonical Object | 路徑類型 | 所需 Role | 降級行為 | 測試狀態 | Drill 狀態 |
|---|---|---|---|---|---|---|---|
| `S-BFF` | Manual emergency stop（UI trigger） | `RuntimeStatus` via kill-switch controller → runtime-manager fast path | composed | `emergency.operator` | BFF 不可用時不可操作；改走 `S-EMRG` 直接路徑 | spec defined | not drilled |
| `S-EMRG` | Direct kill-switch → runtime-manager fast path | `RuntimeStatus`, `RuntimeBinding` | fallback | `emergency.operator` | 若 runtime-manager 不可用，動作無法完成；不直接繞過 LEAN runtime | spec defined | not drilled |
| `S-CLI` | Kill-switch via admin CLI | `RuntimeStatus` | fallback | `emergency.operator` | 與 `S-EMRG` 平行入口 | spec defined | not drilled |

**驗收條件（對應 `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` §10）：**
- Kill switch 不直打 LEAN runtime；最短路徑 = runtime-manager fast path
- BFF kill-switch trigger 必須最終路由至 `S-EMRG` 路徑
- 所有 kill-switch 動作必須有 audit + telemetry event
- `S-CLI` kill-switch 路徑必須在 `S-BFF` 完全不可用情境下通過 drill

---

### 4.4 Governance / Approval

| Surface | 操作 | Canonical Object | 路徑類型 | 所需 Role | 降級行為 | 測試狀態 | Drill 狀態 |
|---|---|---|---|---|---|---|---|
| `S-BFF` | 查詢 approval queue | `ApprovalDecision` read model | composed | `governance.reviewer` | BFF 不可用時不可查詢；audit queue 不丟失 | spec defined | not drilled |
| `S-BFF` | 提交 approval decision | `ApprovalDecision` via `promotion-review-svc` | composed | `governance.approver` | BFF 不可用時需走 `S-IAPI` | spec defined | not drilled |
| `S-IAPI` | 直接寫入 ApprovalDecision | `ApprovalDecision` | authoritative | `governance.admin` + mTLS | N/A | spec defined | not drilled |

**驗收條件：**
- `ApprovalDecision` 必須由 `promotion-review-svc` 寫入；BFF 僅為 façade
- approval 路徑不在 emergency path 上；BFF 故障不得阻塞已 queued 的 approval event

---

### 4.5 Monitoring / Telemetry Read

| Surface | 操作 | Canonical Object | 路徑類型 | 所需 Role | 降級行為 | 測試狀態 | Drill 狀態 |
|---|---|---|---|---|---|---|---|
| `S-BFF` | 查詢 telemetry / dashboard | `TelemetryIngest` read model | composed | `observer` | BFF 不可用時 dashboard 不可用；telemetry pipeline 不受影響 | spec defined | not drilled |
| `S-IAPI` | 直接查詢 telemetry-ingest-svc | `TelemetryIngest` | authoritative | `telemetry.reader` + mTLS | N/A | spec defined | not drilled |
| `S-SUPP` | 診斷端點（health / trace） | N/A（read-only） | support-only | `support.readonly` | 僅限診斷；無域寫入 | not implemented | not drilled |

**驗收條件：**
- BFF dashboard 故障時，telemetry ingestion pipeline 不得中斷
- `S-SUPP` 端點不得暴露可執行動作或可修改 canonical object 的 API

---

### 4.6 Persona / Binding 管理

| Surface | 操作 | Canonical Object | 路徑類型 | 所需 Role | 降級行為 | 測試狀態 | Drill 狀態 |
|---|---|---|---|---|---|---|---|
| `S-BFF` | 查詢 / 建立 PersonaCapitalBinding | `PersonaCapitalBinding` via `persona-control-svc` | composed | `persona.manager` | BFF 不可用時寫入需走 `S-IAPI` | spec defined | not drilled |
| `S-IAPI` | 直接寫入 PersonaCapitalBinding | `PersonaCapitalBinding` | authoritative | `persona.admin` + mTLS | N/A | spec defined | not drilled |

**驗收條件：**
- `PersonaCapitalBinding` 寫入路徑必須由 `persona-control-svc` 驗證；BFF 僅為 façade
- binding 與 deployment 語意分離（見 `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §3）

---

### 4.7 Evolution / Post-Incident

| Surface | 操作 | Canonical Object | 路徑類型 | 所需 Role | 降級行為 | 測試狀態 | Drill 狀態 |
|---|---|---|---|---|---|---|---|
| `S-BFF` | 提交 evolution review / drift review | `EvolutionDecision` via `evolution-svc` | composed | `evolution.reviewer` | BFF 不可用時 review 無法提交；queue 保留 | spec defined | not drilled |
| `S-IAPI` | 直接寫入 EvolutionDecision | `EvolutionDecision` | authoritative | `evolution.admin` + mTLS | N/A | spec defined | not drilled |

---

## 5. Role 定義

| Role | 描述 | 可用路徑 | 備注 |
|---|---|---|---|
| `observer` | 只讀觀察者 | `S-BFF`（read）、`S-SUPP` | 無寫入權限 |
| `governance.reviewer` | 治理審核者 | `S-BFF` | 可查詢 approval queue，不可提交 |
| `governance.approver` | 治理核准者 | `S-BFF` | 可提交 ApprovalDecision；直接 S-IAPI 寫入需 governance.admin |
| `governance.admin` | 治理管理員 | `S-IAPI` | 直接寫入；僅限 internal API + mTLS |
| `deployment.operator` | 部署操作員 | `S-BFF` | 正常路徑使用 BFF；CLI fallback 需升至 deployment.admin |
| `deployment.admin` | 部署管理員 | `S-IAPI`, `S-CLI` | 可走 internal API 與 CLI fallback |
| `runtime.operator` | 執行環境操作員 | `S-BFF` | 正常路徑使用 BFF；S-EMRG 需 emergency.operator；CLI fallback 需 runtime.admin |
| `runtime.admin` | 執行環境管理員 | `S-IAPI`, `S-CLI` | 可走 internal API 與 admin CLI fallback；S-EMRG 需 emergency.operator |
| `emergency.operator` | 緊急操作員 | `S-EMRG`, `S-CLI`, `S-BFF`（trigger） | 高優先，可觸發 kill-switch |
| `persona.manager` | Persona 管理員 | `S-BFF` | 正常路徑 |
| `persona.admin` | Persona 管理進階 | `S-IAPI` | 直接寫入 binding |
| `evolution.reviewer` | 演化審核者 | `S-BFF` | 正常路徑 |
| `evolution.admin` | 演化管理員 | `S-IAPI` | 直接寫入 EvolutionDecision |
| `telemetry.reader` | Telemetry 讀取 | `S-IAPI`, `S-BFF` | 無寫入 |
| `support.readonly` | 支援診斷 | `S-SUPP` | 嚴格 read-only |

> **原則**：fallback 路徑（`S-CLI`, `S-EMRG`）所需 role 不低於正常路徑；提升 role 不得繞過 BFF 正常路徑的 audit 要求。

---

## 6. 降級情境摘要

| 降級情境 | 影響路徑 | 不受影響路徑 | operator 可用替代 |
|---|---|---|---|
| BFF 部分不可用（部分 downstream 失聯） | `S-BFF`（受影響工作台） | `S-BFF`（其他工作台）, `S-IAPI`, `S-CLI`, `S-EMRG` | 繼續用未受影響工作台；高優先操作走 `S-IAPI` 或 `S-CLI` |
| BFF 完全不可用 | `S-BFF`（全部） | active runtime, runtime-manager, kill-switch, `S-IAPI`, `S-CLI`, `S-EMRG` | 所有控制操作走 `S-IAPI` / `S-CLI` / `S-EMRG` |
| runtime-manager 不可用 | `S-EMRG`（寫入受阻）, `S-BFF` runtime read surfaces（RT-01–RT-04 降級或不可用） | `S-BFF`（non-runtime read）, `S-IAPI`（non-runtime）, `S-SUPP` | 無法執行 emergency runtime action；runtime read surfaces 可能降級或不可用；需立即修復 runtime-manager |
| Internal service 單點故障 | `S-IAPI`（受影響 service）, `S-BFF`（對應 panel） | 其餘 service 路徑, `S-SUPP` | 走備援 replica；支援診斷走 `S-SUPP` |

> **核心限制**：任何情境下，**BFF 故障不得影響 active runtime 的執行或 kill-switch 路徑**（見 `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` §2.2 與 §8.5）。

---

## 7. 驗收證據要求（Acceptance Evidence）

下列驗收證據需在 production sign-off 前補齊：

| 驗收項目 | 對應 Surface | 目前狀態 | 負責人 |
|---|---|---|---|
| BFF down scenario drill | `S-CLI`, `S-EMRG` | not drilled | runtime ops team |
| CLI fallback drill（pause / kill-switch） | `S-CLI` | not drilled | platform team |
| Emergency fast path drill | `S-EMRG` | not drilled | runtime ops team |
| Support-only path isolation test（確認無寫入） | `S-SUPP` | not implemented | security / platform team |
| BFF degraded panel smoke test | `S-BFF` | spec defined | frontend team |
| Internal API auth + audit smoke test | `S-IAPI` | spec defined | backend team |
| Role matrix RBAC enforcement test | all surfaces | not tested | platform / security team |
| Lovable / front repo cutover confirmation | `S-BFF` | pending cutover | frontend team |

---

## 8. 路徑間優先序（Canonical Routing Rules）

1. **正常操作**：使用 `S-BFF`（composed + audit 由 BFF 負責聚合）。
2. **BFF 不可用**：切換至 `S-IAPI` 或 `S-CLI` 執行 authoritative 寫入（含 pause、rollback）；僅 kill-switch 等真正的 emergency 操作走 `S-EMRG`。
3. **S-IAPI 不可用**：若是 emergency runtime 操作，使用 `S-EMRG`（runtime-manager fast path）。
4. **診斷查詢**：使用 `S-SUPP`；禁止任何域寫入。
5. **任何路徑的寫入必須**：產生 audit log，更新對應 canonical object，並觸發 telemetry event。

---

## 9. 後續開放項（Non-blocking，不影響 v1 真相）

以下項目屬後續細化，不是本文件目前生效的前置條件：

- `S-CLI` production hardening（automated integration tests, production WSGI config）
- `S-EMRG` 的 action SLA 定義（目標：5s 到達 runtime-manager）
- `S-SUPP` 的端點目錄與 auth 規格
- Role → permission mapping 的 RBAC engine 規格
- Dual control policy（高風險環境下的雙人核准）
- BFF session / SSE fallback policy（WebSocket → polling fallback）

---

## 10. 參考文件

| 文件 | Tier | 說明 |
|---|---|---|
| `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` | L1 | BFF HA 策略、降級模式、後備控制路徑定義 |
| `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` | L1 | Kill switch 分級、safe mode 狀態機、動作選擇矩陣 |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | L1 | Binding vs deployment 語意區分、write authority |
| `PAPER_CANARY_LIVE_POLICY.md` | L1 | 部署階段政策（paper / canary / live 轉換條件）|
| `OPENCLAW_RUNTIME_CONTRACT.md` | L1 | OpenClaw runtime 契約與 adapter 邊界 |
| `Pantheon_API_Service_Contract_設計版.md` | L3 | Service 拓樸與 API 責任邊界（future-state）|
| `Pantheon_Blueprint_Gap_Review_v1.md` | L3 | GAP-06 原始需求定義 |
