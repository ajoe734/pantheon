# CANONICAL_CONTRACT_MIGRATION_DECISION.md

Last updated: 2026-04-09
Status: supporting migration decision note for the canonical cutover
Tier: L3 Supporting Design & Migration
Scope: rationale and migration strategy for adopting deployment-stage split and promoting new policy docs
Conflict rule: this file explains why the cutover happened, but active semantics now live in the L1 policy set and L2 planning docs

## 1. 文件目的

本文件定義 Pantheon 是否、以及如何，把近期新增的決議與物件正式覆蓋到現有 repo 的 canonical contracts。

重點回答：

1. 目前 canonical contract 中沒有 `canary`，是否要改？
2. `PersonaCapitalBinding`、`DeploymentPlan`、`RuntimeBinding`、`lineage read model` 誰先成為 v1 真相來源？
3. 新增的決議文件是否要升格為 canonical files？

---

## 2. 現況問題

現有 canonical lifecycle 偏向：

`draft -> candidate -> paper -> live -> retired`

但最新決議已明確把：

- `canary` 視為正式 deployment stage
- `binding` 視為 governance object
- `runtime binding` 視為 execution truth
- `lineage read model` 視為獨立查詢層

若直接覆蓋舊 enum，會造成：
- 舊 code / data migration 較大
- governance state 與 deployment state 再次混在一起

---

## 3. 正式決議：不要粗暴重寫舊 lifecycle enum

### 決議

**不直接重寫舊 artifact lifecycle enum。**

改為：

### A. 保留 artifact / governance state

v1 canonical：
- `draft`
- `candidate`
- `approved`
- `retired`

### B. 新增獨立 deployment_stage

v1 canonical：
- `none`
- `paper`
- `canary`
- `live`
- `frozen`

### 理由

1. governance maturity 與 deployment stage 本來就是不同維度
2. 可以把 `canary` 正式納入，不破壞舊 state machine 太多
3. 後續 paper/canary/live 的 telemetry 與 runtime binding 更清楚

---

## 4. v1 source of truth owner 決議

### 4.1 PersonaCapitalBinding

- **v1 source of truth**：`registry-core` 下的 `capital_pool / binding` schema
- owner plane：Capital Pool Plane
- owner service：`registry-core`（或其 capital-pool module）

### 4.2 DeploymentPlan

- **v1 source of truth**：Governance & Promotion Plane
- owner service：`promotion / deployment planner`

### 4.3 RuntimeBinding

- **v1 source of truth**：Execution Plane
- owner service：`runtime-manager`

### 4.4 lineage read model

- **不是 source of truth**
- 它是 derived read model
- owner：`registry-core lineage module` 或獨立 `lineage-read service`

### 4.5 Lineage 真相原則

- 各服務各自維護正規化 FK / edge
- lineage read model 只做查詢組裝
- `lineage_json` 若存在，僅作 cache / materialized view，不作 source of truth

---

## 5. 新文件是否升格為 canonical

### 正式決議

**要。**

但採分層升格：

#### L1 Canonical Contract / Policy
- `OPENCLAW_RUNTIME_CONTRACT.md`
- `PERSONA_RUNTIME_MODEL.md`
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
- `ROLLBACK_AND_POSITION_SEMANTICS.md`
- `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
- `PAPER_CANARY_LIVE_POLICY.md`

#### L2 Supporting Design / Migration Notes
- `CANONICAL_CONTRACT_MIGRATION_DECISION.md`
- 其他 rationale / design notes

### 要求

這些文件必須加入 `AI_COLLABORATION_GUIDE.md` 所維護的正式真相清單，否則它們只能算 supporting docs，不算 canonical。

---

## 6. migration 路徑

### Phase 1：文件層 canonical 化

先做：
- 把上述文件加入 canonical / supporting 清單
- 在主 architecture docs 與 package docs 引用它們

### Phase 2：schema 補欄位

至少新增：
- `deployment_stage`
- `persona_capital_binding`
- `deployment_plan`
- `runtime_binding`
- `runtime_binding.rollback_parent`

### Phase 3：runtime / telemetry 對齊

- telemetry schema 補 `deployment_stage`
- runtime manager 寫 `RuntimeBinding`
- promotion planner 寫 `DeploymentPlan`

### Phase 4：lineage read model 補齊

- 將 source / registry / governance / runtime / telemetry 的 edge 統一查詢

---

## 7. compatibility 規則

### 7.1 舊資料相容

若舊記錄只有：
- `candidate`
- `paper`
- `live`
- `retired`

則遷移時：
- `paper` -> `approved + deployment_stage=paper`
- `live` -> `approved + deployment_stage=live`

### 7.2 canary 為新 stage

舊資料沒有 canary 時：
- 視為 `deployment_stage in (paper, live)` 兩態模型
- 不回填偽 canary

---

## 8. 對現有 repo 的覆蓋原則

### 8.1 不覆蓋舊 README 的歷史語義

現有 README 保留，但要加註：
- artifact state 與 deployment stage 已拆開
- `canary` 為新引入 deployment stage

### 8.2 canonical README / contract 應補的文字

建議新增一句：

> Lifecycle governance distinguishes artifact state from deployment stage. `canary` is modeled as a deployment stage rather than a direct replacement of artifact state values.

---

## 9. owner matrix

| 物件 | v1 truth owner | write owner | read aggregation owner |
|---|---|---|---|
| PersonaCapitalBinding | registry-core | governance / binding workflow | BFF / lineage read model |
| DeploymentPlan | promotion service | promotion controller | BFF / incident / lineage read model |
| RuntimeBinding | runtime-manager | runtime manager | BFF / telemetry / lineage read model |
| lineage read model | derived only | lineage module | UI / audit / incident queries |

---

## 10. Canonical adoption checklist

- [ ] 將 7 份新文件列入正式真相清單
- [ ] 更新 architecture / package docs 引用
- [ ] schema 加入 `deployment_stage`
- [ ] schema 加入 `persona_capital_binding`
- [ ] schema 加入 `deployment_plan`
- [ ] schema 加入 `runtime_binding`
- [ ] runtime manager 實寫 binding
- [ ] telemetry 補 deployment stage 與 binding ref
- [ ] lineage read model 以 FK/edge 組裝

---

## 11. Actions → Commands 收斂時間表

### 背景

BFF 目前有兩條寫入路徑：

| 路徑 | 狀態 | 說明 |
|---|---|---|
| `/bff/actions/{entityType}/{entityId}/{actionId}` | 現役（過渡期） | `runAction.ts` 的 canonical live-write seam；所有 entityType 的寫入都走這條路 |
| `/bff/v1/commands` | 最終合約 | command admission / idempotency / RBAC / audit 的正式統一入口 |
| `/api/v1/operator/commands` | Legacy | 向下相容；不得靜默移除 |

`BFF_COMMAND_API_CONTRACT.md` §8 Command Adapter Mapping 建立了從 actions 路徑到 commands 路徑的完整映射表，覆蓋全部明確映射 entityType（strategy / persona / capital-pool / rebalance / deployment / evolution-program / research-experiment / artifact / ranking-formula / runtime / tool / mcp-server / mcp-tool / skill / channel）、review 指出的 active caller overlay（例如 strategy `update_params`、capital-pool `adjust_budget`、deployment `promote_live`、evolution-program `stop`、research-experiment `promote_artifact`、skill `publish` / `retire`、mcp-server `test_connection` / `health_check` / `update_env_grants`、mcp-tool `grant_env`）、generic approval / alert / incident fallback route family，以及 3 條特殊路徑（approvals decide / alerts acknowledge / v5 interventions decide）與 confirm-token lifecycle rows。

### 收斂里程碑

| Wave | 目標日期 | 任務 | 說明 |
|---|---|---|---|
| Wave 1 | 2026-05-13 | BFF-CONSOL-004 | 本文件 §8 mapping 表寫定（spec-only，不動 runtime） |
| Wave 1 | 2026-05-13 | BFF-CONSOL-001/002/003 | Frontend ↔ backend route manifest 對齊，建立 CI diff baseline |
| Wave 2 | 2026-05-13 | BFF-CONSOL-008/009/010 | Canonical fixture pack A/B/C，確保 live list 回 ≥1 non-empty entry |
| Wave 2 | 2026-05-13 | BFF-CONSOL-019 前置 | EP5 paper-canary closeout（Day 12 gate） |
| Wave 3 | EP5 closeout 後 | BFF-CONSOL-019 | 後端 `/bff/actions/*` 在 BFF 內轉 `/bff/v1/commands` admission；PR 準備好但 hold 在 review 直到 EP5 closeout signal |
| Wave 3 | EP5 closeout 後 | BFF-CONSOL-020 | `runAction.ts` 新 caller 優先發 `/bff/v1/commands`；舊 caller 透過 BFF adapter 轉發 |
| Wave 3 | EP5 closeout 後 | BFF-CONSOL-021 | Receipt dual-write soak（≥7 天）；action receipt + command receipt 並存 |
| Wave 4 | dual-write soak 完成後 | BFF-CONSOL-024 | 舊 action receipt 標 deprecated；前端 default caller 切 `/bff/v1/commands` |
| Wave 4 | cutover 完成後 | BFF-CONSOL-027 | Final BFF consolidation acceptance packet |

### 收斂原則

1. **不中斷現有 caller** — `/bff/actions/*` 路徑在 Wave 3 之前繼續完整運作。Wave 3 之後 BFF adapter 在後端透明轉發，路徑本身不下線。
2. **EP5 gate 不得繞過** — BFF-CONSOL-019 runtime change 在 EP5 paper-canary closeout 之前禁止 merge 至 main。
3. **Dual-write soak 決定 receipt deprecation 時間** — Wave 4 的 receipt deprecation 以 BFF-CONSOL-021 soak window 完成為觸發條件，不設固定日期。
4. **Legacy route 保留** — `/api/v1/operator/commands` 在明確的 migration test 通過前不得移除，維持向下相容。
5. **Spec-first** — 本 §11 與 `BFF_COMMAND_API_CONTRACT.md` §8 是 spec；runtime 行為仍由後續 BFF-CONSOL-019/020/021 task 實作。

---

## 13. 最終結論（原 §11）

本文件的正式決議是：

1. **要覆蓋 canonical contract，但不是粗暴重寫舊 lifecycle enum**
2. **artifact state 與 deployment stage 正式拆開**
3. **`canary` 作為 deployment stage 引入**
4. **`PersonaCapitalBinding / DeploymentPlan / RuntimeBinding` 都有明確 v1 truth owner**
5. **lineage read model 不是 source of truth**
6. **新增決議文件應升格為 canonical / supporting 正式文件群**
