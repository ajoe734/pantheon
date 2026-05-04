# PAPER_CANARY_LIVE_POLICY.md

Last updated: 2026-04-09
Status: canonical deployment-stage policy
Tier: L1 Platform Architecture & Policy
Scope: `paper`, `canary`, `live`, and `frozen` stage semantics, thresholds, funding, approval, veto, and runtime expectations
Conflict rule: this document overrides broader stage wording in architecture/planning docs; binding ownership and runtime write authority defer to the binding/deployment semantics doc

## 1. 文件目的

本文件正式定義 Pantheon 中 `paper`、`canary`、`live` 三種 deployment stage 的語意差異、promotion 條件、資金與風險限制，以及與治理 / runtime manager / telemetry 的互動方式。

本文件重點回答：

- `paper` 與 `canary` 到底差在哪裡
- `paper -> canary`、`canary -> live` 的 metrics threshold 是什麼
- `canary` 是否屬於真實資金與真實委託
- promotion 時誰批准、誰有 veto、哪些條件為必要條件

---

## 2. 正式定義

### 2.1 Paper

`paper` 是：

- 真實市場資料
- 真實 artifact / config / loader / runtime path
- 模擬撮合
- 不送真實委託
- 不使用真實資金

`paper` 的主要目的是驗證：

- artifact correctness
- loader / binding correctness
- strategy logic correctness
- risk policy correctness
- simulated execution behavior

### 2.1.1 Broker sandbox / paper-account API smoke

`paper` 不代表 broker order API 不串接。正確邊界是：

- broker SDK / order API 應盡早用 broker-provided paper account、sandbox、
  simulation mode 或 test credentials 串起來；
- smoke 必須覆蓋 auth、account/readiness、order intent validation、最小測試委託
  place、cancel/replace、open-order/status/readback、execution/no-fill/fill
  disposition、telemetry 與 reconciliation packet；
- 測試環境不得使用 production live credentials、不得使用真實資金，也不得在
  telemetry 裡標成 production `live_order_submitted`；
- 若 broker 支援 validate-only 或 paper trading，必須先跑該路徑，再討論
  canary/live 真實資金路徑。

因此 `live fail-closed` 只擋未批准的 production live order side effect，不擋
broker API integration、paper broker smoke、sandbox order smoke 或 readback probe。

### 2.2 Canary

`canary` 是：

- 真實市場資料
- 真實 artifact / config / loader / runtime path
- 真實委託
- 真實資金
- 縮量資金 / 縮量曝險
- 更嚴格的風控與監控

`canary` 的主要目的是驗證：

- 真實市場執行行為
- 真實 slippage / rejection / latency / partial fill
- artifact 與 pool / broker / venue 的真實兼容性
- rollback readiness

### 2.3 Live

`live` 是：

- 真實市場資料
- 真實 artifact / config / loader / runtime path
- 真實委託
- 真實資金
- 正式批准的 pool exposure 與資金範圍

---

## 3. 正式區別總表

| mode | 市場資料 | 委託 | 資金 | 撮合 | 風控 | 主要目的 |
|---|---|---|---|---|---|---|
| paper | 真實 | 模擬；可含 broker sandbox / paper-account 測試委託 | 0 | 模擬或 broker test environment | 正式規則 + simulated checks | 驗證策略 / loader / runtime / broker API 路徑 |
| canary | 真實 | 真實 | 真實，但縮量 | 真實 | 更嚴格 | 驗證真實執行與回退能力 |
| live | 真實 | 真實 | 真實，正式額度 | 真實 | 正式規則 | 正式部署 |

---

## 4. Canonical deployment state 建議

本文件建議 deployment stage 採獨立欄位：

- `none`
- `paper`
- `canary`
- `live`
- `frozen`

而不是把 `paper/canary/live` 混在 artifact lifecycle enum 內。

---

## 5. Paper promotion policy

### 5.1 進入 Paper 的必要條件

artifact 必須：

- 已通過 `Patch Validators`
- 已有完整 lineage
- 已有 formal `ApprovalDecision`
- 已有對應 `rollback_target`
- 已通過 pool compatibility / runtime loader checks

### 5.2 Paper 資金語意

- paper 不使用真實資金
- 允許使用虛擬 NAV / virtual capital 進行 sizing
- 不產生真實 broker exposure

### 5.3 Paper telemetry 要求

paper 必須產生與 canary/live 同 schema 的 telemetry：

- target weights
- simulated order intent
- simulated fills
- positions
- exposure
- slippage estimate
- runtime health

這是為了讓 reconciliation 可以比較 `paper` 與 `canary/live`。

---

## 6. Paper -> Canary Promotion Policy

### 6.1 v1 全域必要條件

以下為預設 global threshold，可由 `DeploymentPolicy` 依 strategy family 覆蓋。

#### 時間 / 样本條件
- 至少 **20 個交易日** paper 觀察期
- 或（高頻 / 高換手策略）至少 **10 個 session + 200 筆 paper orders**

#### 穩定性條件
- 無未解決 `Severity-1` 或 `Severity-2` incident
- runtime / loader integrity issue = **0**
- reconciliation mismatch rate < **1%**
- governance / approval mismatch = **0**

#### 表現條件
- paper 最大回撤不得超過研究預期的 **1.2 倍**
- 模型預估 slippage 與 paper simulated slippage 偏差 < **25%**
- turnover 不得超過策略定義上限的 **110%**
- risk policy breach count = **0**

#### 治理條件
- `rollback_target` 已存在
- `Risk Owner` 已 review
- `Operator` 已指定 canary 觀察 owner

### 6.2 Canary 初始資金與風險限制

v1 預設：

- `canary_capital = min(5% pool NAV, strategy_canary_cap)`
- `gross_limit = 25% of planned live gross`
- `single_name_limit = 50% of live single-name limit`
- `turnover_limit = 75% of live turnover limit`（若策略族要求更保守）

### 6.3 Canary 特殊保護

canary 模式必須預設開啟：

- 更低 alert threshold
- 更嚴格 slippage alert
- 更短 rollback decision latency
- 更高 heartbeat 敏感度

---

## 7. Canary -> Live Promotion Policy

### 7.1 v1 全域必要條件

#### 時間 / 樣本條件
- 至少 **10 個交易日** canary 觀察期
- 或至少 **50 筆真實委託**

#### 事故條件
- 無未解決 `Severity-1`
- 無 governance / loader / binding mismatch
- 無 forced kill-switch event

#### 執行條件
- realized slippage 較 paper expectation 惡化不超過 **20%**
- order reject rate < **0.5%**
- fill rate ≥ **90%**（適用流動性正常標的）
- target vs executed exposure tracking error 在策略族容忍範圍內

#### 風險條件
- canary 最大回撤 < pool kill-threshold 的 **50%**
- no risk policy hard breach
- no unresolved reconciliation anomaly

#### 人工條件
- `Reviewer` approval
- `Risk Owner` approval
- `Operator` approval

### 7.2 Live 放量策略

從 `canary -> live` 不一定一次放滿額度。建議支援：

- `live_stage_1`
- `live_stage_2`
- `full_live`

若目前不想再加 enum，至少要讓 `DeploymentPlan` 支援：
- `capital_scale_pct`
- `gross_scale_pct`
- `ramp_schedule`

`DEP-001` 已正式把這三者收斂為：

- `DeploymentPlan.scale.capital_scale_pct`
- `DeploymentPlan.scale.gross_scale_pct`
- `DeploymentPlan.scale.ramp_schedule`

---

## 8. Paper / Canary / Live 的 owner 與 veto 權

### Paper
- submitter：Research / Governance workflow
- approver：Reviewer + Risk review through gate
- veto：Patch Validators / Loader Checks / Governance Gate

### Canary
- submitter：Promotion Controller
- approver：Reviewer + Risk Owner + Operator
- veto：Risk Owner / Loader Checks / Runtime Manager / Kill Switch Policy

### Live
- submitter：Promotion Controller
- approver：Reviewer + Risk Owner + Operator
- veto：Governance Committee（必要時）、Risk Owner、Loader Checks、Kill Switch Policy

---

## 9. runtime 行為要求

### 9.1 Paper runtime
- 可獨立 instance
- 不綁真實 broker account
- 可綁 broker sandbox / paper account / test credentials 進行 order API smoke
- 仍必須走正式 loader / binding / telemetry path

### 9.2 Canary runtime
- 綁真實 broker account / subaccount
- 使用真實委託
- 預設更嚴格 heartbeat / alert / rollback policy

### 9.3 Live runtime
- 綁正式 broker account / subaccount
- 使用正式 pool exposure
- 預設風控與 canary 不同，但不更寬鬆於 canary 的 kill conditions

---

## 10. telemetry 要求

paper / canary / live 三種模式必須共享同一 telemetry schema，但要額外記：

- `deployment_stage`
- `is_real_order`
- `is_real_capital`
- `sim_fill_flag`
- `capital_scale_pct`

這樣第四包的 reconciliation / drift 才能做 apples-to-apples 比較。

---

## 11. Metrics threshold 的 override 層級

v1 規則：

1. global default
2. strategy family override
3. capital pool override
4. explicit deployment plan override（最嚴格，不得更寬鬆於 hard policy）

其中 explicit deployment plan override 的正式欄位位於：

- `DeploymentPlan.scale.*`
- `DeploymentPlan.pre_checks[]`
- `DeploymentPlan.post_checks[]`
- `DeploymentPlan.schedule_window`
- `DeploymentPlan.rollback`

---

## 12. API / contract 建議

- `GET /api/deployment-policies`
- `GET /api/deployment-policies/:id`
- `POST /api/promotion/plans`
- `POST /api/promotion/plans/:id/approve`
- `POST /api/promotion/plans/:id/reject`
- `GET /api/runtime-bindings/:id`

事件建議：
- `deployment.paper_started`
- `deployment.canary_started`
- `deployment.live_started`
- `deployment.canary_promoted`
- `deployment.live_promoted`
- `deployment.roll_back`

---

## 13. 與其他文件關係

本文件應與以下 canonical 文件一起使用：

- `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
- `ROLLBACK_AND_POSITION_SEMANTICS.md`
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
- `CANONICAL_CONTRACT_MIGRATION_DECISION.md`
- `services/control-plane/governance/deployment_plan.contract.md`

---

## 14. Canonical status 建議

本文件應升格為 **canonical deployment policy file**。
