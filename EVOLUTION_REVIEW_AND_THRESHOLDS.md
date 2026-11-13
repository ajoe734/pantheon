# EVOLUTION_REVIEW_AND_THRESHOLDS.md

Last updated: 2026-04-11
Status: canonical evolution governance policy
Tier: L1 Platform Architecture & Policy
Scope: EvolutionDecision lifecycle, review owners, thresholds, automation boundaries, and allowed actions
Conflict rule: this document overrides broader evolution wording in architecture/planning docs; rollback execution details still defer to the rollback policy document

## 1. 文件目的

本文件定義 Pantheon 在 **Evolution Plane** 中對 `EvolutionDecision` 的正式審核流程、owner、風險分級、threshold、執行權限與回灌機制。

本文件的目的是把原本在系統分析與討論中的模糊點收斂成可實作契約，特別回答以下問題：

- `proposed -> reviewed -> approved -> executed` 中的 `reviewed` 由誰負責
- drift / degradation / correction 等 threshold 如何定義
- `freeze`、`rollback`、`retrain`、`mutate` 等動作如何分層
- 哪些 evolution 可自動執行，哪些必須走人工審核或委員會流程

本文件是 **governance policy**，不是 runtime loader 文件，也不是 research worker 文件。

---

## 2. 適用範圍

本文件適用於下列目標：

- `StrategySpec`
- `AlphaTemplate`
- `CandidateArtifact`
- `AllocationPolicyArtifact`
- `Persona`
- `PersonaCapitalBinding`
- `CapitalPool`（僅限行為層/風險層調整，不取代第三包中的 deployment 與 runtime action）

---

## 3. 核心原則

### 3.1 Evolution 是 governance 動作，不是研究 worker 的副作用

研究 worker 可以提出 `retrain` 建議，drift detector 可以提出 `freeze` 建議，但 **只有 Evolution Controller + Review Owners** 可以把它變成正式 `EvolutionDecision`。

### 3.2 EvolutionDecision 的 owner 分成提案者與核准者

- **提案者**：Evolution Controller（系統）
- **核准者**：依風險等級不同，由 Reviewer、Risk Owner 或 Governance Committee 擔任

### 3.3 Freeze 與 Rollback 不同

- `rollback`：deployment / runtime mitigation，偏 operational action
- `freeze`：governance quarantine，偏 lifecycle state change

### 3.4 所有 threshold 都採「全域預設 + 策略族 / persona / pool override」模式

文件先定 v1 全域預設值；實際生效規則由 `EvolutionPolicy` / `DriftPolicy` / `DeploymentPolicy` 允許逐層 override。

---

## 4. EvolutionDecision 狀態機

```mermaid
stateDiagram-v2
    [*] --> proposed
    proposed --> reviewed
    reviewed --> approved
    reviewed --> rejected
    approved --> executed
    approved --> canceled
    executed --> superseded
    rejected --> [*]
    canceled --> [*]
    superseded --> [*]
```

### 4.1 狀態說明

- `proposed`：系統或規則引擎提出建議，尚未審核
- `reviewed`：已進入正式 review，owner 已受理
- `approved`：允許執行
- `executed`：動作已實施（或已提交到下游 plane 執行）
- `rejected`：決議不採納
- `canceled`：尚未執行前被取消
- `superseded`：被更新版本決議覆蓋

---

## 5. EvolutionDecision 類型

### 5.1 低風險類型

- `observe`
- `revalidate`
- `retrain`
- `require_more_data`
- `flag_for_review`

### 5.2 中風險類型

- `reduce_budget`
- `tighten_risk_policy`
- `mutate_persona_route_policy`
- `mutate_consult_policy`
- `freeze_paper`
- `freeze_canary`

### 5.3 高風險類型

- `freeze_live_strategy`
- `retire_strategy`
- `retire_alpha_template`
- `split_persona`
- `merge_persona`
- `remove_live_owner_role`
- `restrict_pool_eligibility`
- `force_risk_off`

---

## 6. Reviewed / Approved 的 owner 定義

### 6.1 低風險決議

#### reviewed owner
- `Reviewer on Duty`
- 允許規則先行自動 review，再由 reviewer 追認

#### approved owner
- `Reviewer on Duty`
- 若策略族被標記為「manual-only」，則不得自動批准

### 6.2 中風險決議

#### reviewed owner
- `Reviewer`
- `Risk Owner`

#### approved owner
- `Risk Owner`
- 必要時加 `Operator`

### 6.3 高風險決議

#### reviewed owner
- `Governance Committee`

#### Governance Committee 固定組成（v1）
- `Research Lead`
- `Risk Owner`
- `Operator / Platform Owner`

#### approved owner
- `Governance Committee`
- 任一席次拒絕，則不得自動批准

---

## 7. Threshold v1 預設值

> 以下為 v1 全域預設。最終實施時允許依 strategy family、asset class、pool 進行 override。

### 7.1 Performance degradation threshold

觸發 `proposed`：

- 最近 20 交易日風險調整績效（Sharpe / IR / strategy family 定義指標）低於 paper/canary 基線的 **50%**
- 或 rolling drawdown 超過研究預期區間的 **1.25 倍**（`reconciliation-drift` 為此 drawdown 1.25x incident 的唯一 producer：一旦 `observed / expected_drawdown` 超過 `services/evolution/config/threshold_sweep_thresholds.json` 之 `rolling_drawdown_multiple`（`comparator="gt"`, `1.25`），由 `reconciliation-drift` 依 causal `drawdown_event_id` 建立 governed `DriftReport` 並轉為 `IncidentCase`，再由 Evolution daily sweep 轉為 `RETRAIN`（`(1.25x, 1.5x)`，`LOW` risk）或維持既有 `>= 1.5x` critical `FREEZE` 路徑；Evolution `threshold_sweep_worker` 不再重複發送該指標之 derived telemetry echo 或 incident）
- 或連續 **3 個評估窗**明顯劣於 baseline

### 7.2 Execution drift threshold

觸發 `proposed`：

- realized slippage 較最近 20 交易日基線惡化 **25% 以上**
- order reject rate > **1.0%**
- partial fill / timeout anomaly 連續 **3 個交易日**異常

### 7.3 Feature / label / policy drift threshold

- PSI > **0.20**：warning
- PSI > **0.30**：mandatory review
- label generation mismatch rate > **0.5%**：mandatory review
- policy output deviation 超過 baseline envelope：warning / review（依策略族定義）

### 7.4 Human correction threshold

- 同一 persona 在 **5 個 trainer session 內超過 3 次重大人工修正**
- 同一 strategy 在 **14 天內被連續 reject 2 次以上**

### 7.5 Governance / incident threshold

直接進高風險路徑：

- 任一 `Severity-1` incident
- 同一 artifact 在 **30 天內 2 次 Severity-2 incident**
- execution loader / binding / approval mismatch 未解決

### 7.6 自動 freeze 提議條件

直接產生高風險 `proposed`：

- live artifact 發生 `Severity-1`
- drift + drawdown 同時超標
- rollback 已執行但問題仍持續

---

## 8. EvolutionPolicy / DriftPolicy

### 8.1 EvolutionPolicy

建議 schema：

```yaml
policy_id: default-evolution-policy-v1
scope:
  strategy_family: [equity_cross_sectional, stat_arb, macro_overlay]
  asset_class: [equity, futures, options]
low_risk_actions:
  auto_review_allowed: true
  auto_approve_allowed: false
medium_risk_actions:
  reviewer_required: true
  risk_owner_required: true
high_risk_actions:
  governance_committee_required: true
freeze_rules:
  severity1_auto_propose: true
  repeated_severity2_window_days: 30
  repeated_severity2_count: 2
```

### 8.2 DriftPolicy

建議 schema：

```yaml
policy_id: default-drift-policy-v1
performance:
  window_days: 20
  sharpe_ratio_floor_pct_of_baseline: 0.5
  max_expected_drawdown_multiplier: 1.25
execution:
  slippage_drift_pct: 0.25
  reject_rate_max: 0.01
feature:
  psi_warning: 0.20
  psi_review: 0.30
label:
  mismatch_rate_max: 0.005
human_correction:
  sessions_window: 5
  major_corrections_threshold: 3
```

---

## 9. Freeze / Retrain / Revalidate / Mutate 的語意

### 9.1 `retrain`
重跑模型 / policy / feature pipeline。屬研究面動作。

### 9.2 `revalidate`
不一定重訓，但重新做 rolling / OOS / paper admissibility。屬研究 + governance 交界動作。

### 9.3 `mutate`
小幅調整 persona/strategy 的結構參數，例如：
- route policy
- consult policy
- risk tolerance
- allowed tools

#### 9.3.1 Paper persona harness amendment (2026-11-12, D10)

The existing Persona write owner may adopt a versioned `persona_harness.v1`
patch automatically while the persona is active and `paper_owner`. This narrow
exception to the operator approval default requires all of the following:

- A current-base static precheck passes, including resolved trajectory evidence
  and the rule that the patch cannot become more permissive.
- The latest stored, deny-all shadow comparison is complete and recommends
  `CANDIDATE_BETTER`, with the exact base version/digest and candidate digest.
  `KEEP_BASE` and unavailable evidence retain the base.
- A published consultation request and memo bind the same persona, tenant,
  patch, base version and candidate digest. The owner rechecks independent
  persona contributions. The risk persona must support; guardrail or consult
  policy changes also require red team participation. Missing answers, risk
  opposition and unresolved conditional approvals require an operator decision.

Only the harness surface is mutable: identity inputs, tighter guardrails,
consult policy, tool guidance, skills, workflow cadence, cycle thresholds and
memory retrieval policy. Risk/capital limits, environment ceiling, execution
authority and live permissions remain outside this exception. Outside paper,
the owner records and delivers a governance proposal; it never activates the
patch. Persona governance proposals use the existing medium-risk
`mutate_persona_route_policy` / `mutate_consult_policy` actions and retain the
operator-required intent. The ordinary DSPy `prompt_bundle` path in
`services/control-plane/persona/lp001/contract.md` retains its operator gate;
this amendment applies only to the versioned harness owner path above.

One experiment may be active per persona. The coordinator starting values are
an observation window of **10 distinct trading days** and a component cooldown
of **7 calendar days after observation or rollback**. Trusted owner construction
can set the windows; each adoption freezes its observation window and criterion.
Components are the actual surface keys, so aliases such as `memory` and
`memory_policy` share a cooldown. Rollback can restore an observed version only
while it remains the current version; it cannot overwrite a later experiment.

Observation is an owner operation, outside the trade path. Its request supplies
packet references rather than caller verdicts or scores. The owner resolves
terminal paper Observe packets for the exact tenant, persona and adopted
version. Paired owner measurements must carry a trading date, base/candidate
version, `net_of_costs_total_return` criterion, evidence, confidence and any
confounders. Missing packets, wrong versions, duplicate dates, changed criteria
or an unfinished window record a refusal and keep the experiment active.
Absent paired measurements or declared confounders yield `confounded` once a
complete observation window exists; absent facts never become zero returns.

The deterministic review records `helped`, `hurt`, `no_effect` or `confounded`
and emits a `harness_experience` with cue, lesson, evidence and confidence to
the existing Memory Plane. `hurt` automatically proposes restoring the recorded
rollback target through governance; a proposal does not execute a rollback.
Later proposals include relevant prior experience references for every changed
component. Persona state retains the source event and replayable delivery intent,
while Memory owns the learning entries. Missing service configuration, credentials
or receipts leave deliveries pending with an error; replay through the owning
operation repairs them without inventing successful publication. The existing
owner read API exposes these statuses.

This path introduces no trade-path model call or new credential. Naturally
occurring paired book facts are required for hosted efficacy claims; local
fixture results and a source merge do not prove hosted adoption or benefit.

### 9.4 `freeze`
禁止該 strategy / artifact / persona 進一步 promotion 或新 deploy，直到解除。

### 9.5 `retire`
永久退出可用範圍，不再新建 deploy，但保留歷史資料。

---

## 10. Freeze 與 Rollback 的正式區別

| 項目 | Freeze | Rollback |
|---|---|---|
| 類型 | Governance state change | Runtime / deployment action |
| 目的 | 隔離、禁止後續 deploy | 立即止血、替換/停用當前 deploy |
| 發生位置 | Governance / Evolution | Runtime / Deployment |
| 是否必然影響當前 runtime | 不一定 | 一定 |
| 是否可獨立發生 | 可以 | 可以 |

### 實務規則

- `freeze` 可在無當前 runtime 的情況下發生
- `rollback` 可在不 freeze 的情況下發生
- 高風險 incident 通常會同時觸發兩者，但需分別建模

---

## 11. Operational Evolution Routing Boundary

`EvolutionDecision` 的 normal path 必須明確回答四件事：

1. 哪個 threshold / incident 會觸發哪條 action path
2. reviewed / approved owner 是誰
3. cooldown / observation window 從哪裡來
4. 下游到底是哪個 plane 執行，且誰是唯一 write owner

### 11.1 Normal-path action routing matrix

| Action path | Trigger / threshold source | reviewed owner | approved owner | cooldown / observation | Primary execution plane | Authoritative writer | Downstream boundary |
|---|---|---|---|---|---|---|---|
| `freeze` on `paper` / `canary` | §7.3–§7.6 觸發 freeze，且 target stage 為 `paper` 或 `canary` | `Reviewer`, `Risk Owner` | `Risk Owner`（必要時加 `Operator`） | 依 `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` §5.2 的中風險 path | `governance` | Governance Plane 寫 target quarantine / admissibility state | 預設只做 governance quarantine；不自動發 runtime rollback |
| `freeze` on `live` with no active runtime | §7.5–§7.6；target stage 是 `live`，但當前沒有 active runtime 需要止血 | `Governance Committee` | `Governance Committee` | 依 `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` §5.2 的高風險 path | `governance` | Governance Plane 寫 target quarantine / admissibility state | 仍是 high-risk freeze；只是沒有 companion deployment/runtime follow-through |
| `freeze` on `live` with active runtime | §7.5–§7.6；尤其是 Severity-1、repeated Severity-2、unresolved binding/approval mismatch、或 rollback 已執行但問題仍持續 | `Governance Committee` | `Governance Committee` | 依 `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` §5.2 的高風險 path | `governance` 為主，並可伴隨 `deployment` / `runtime` follow-through | Governance Plane 只寫 freeze decision；DeploymentPlan 仍由 Governance/Promotion plane 建立；RuntimeBinding 仍只能由 Runtime Manager 改寫 | 若只需停止新 entries 並保留既有 book，建立 `DeploymentPlan(current_stage -> frozen)`；若 artifact / config 已不安全，則另外走 rollback request |
| `rollback` operational follow-through | 來自 approved evolution decision、active incident、或 postmortem follow-up；threshold 依 §7.5–§7.6，action semantics 依 `ROLLBACK_AND_POSITION_SEMANTICS.md` | 沿用觸發它的 parent review chain；normal path 不建立平行 approval chain | 沿用觸發它的 parent approval chain；fast-path 例外留給 `EVO-005` | rollback 不開新的 evolution cooldown；沿用 parent `EvolutionDecision` 的 cooldown / observation | `runtime` | `Rollback Controller` 決定 rollback request；`Runtime Manager` 是 `RuntimeBinding` / position cutover 的唯一 writer | 必須消費既有 `DeploymentPlan.rollback.action_type`、incident evidence、與 risk / incident policy；不得由 evolution plane 直接改 binding |
| `retrain` / `revalidate` | §7.1、§7.3、§7.4 的 performance / feature / human-correction 訊號 | `Reviewer on Duty` | `Reviewer on Duty`；若 policy 允許可由自動 gate 追認 | 依 `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` §5.2 的低風險 path | `research` | Research workflow / job system 寫 research work item；registry / deployment 寫入仍屬後續 plane | executed 代表 research job / work item 已被受治理地建立，不代表可直接 redeploy |
| redeploy follow-through | retrain / revalidate / revive / freeze-lift 之後，新的 artifact 已重新通過 approval 與 stage gate；threshold 依 `PAPER_CANARY_LIVE_POLICY.md` §5–§7 | 依 target stage 的 deployment reviewer 鏈 | `paper`: review gate；`canary` / `live`: `Reviewer + Risk Owner + Operator` | 不建立新的 evolution cooldown；必須先滿足 parent decision observation，之後再受 deployment stage policy 約束 | `deployment` | Governance / Promotion plane 建立新的 `ApprovalDecision` 與 `DeploymentPlan`；`Runtime Manager` 僅消費 plan | redeploy 不是獨立 `EvolutionDecision.action_type`；它是 approved evolution outcome 的 deployment follow-through，不得形成 shadow runtime command surface |

### 11.2 Freeze-only vs freeze-plus-operational-mitigation

- `freeze` 在沒有 active runtime 時，只有 governance quarantine，沒有 runtime side effect。
- `freeze` 在 `paper` / `canary` 有 active runtime 時，預設仍是 governance quarantine；只有當 reviewer / risk owner 明確要求停止該 runtime 的新 entries，才建立 `DeploymentPlan(current_stage -> frozen)`。
- `freeze` 在 `live` 有 active runtime 時，必須先做 governance quarantine，再依 incident / risk policy 決定 companion operational path：
  - 只需停止新 entries、保留現有 book：走 `DeploymentPlan(current_stage -> frozen, transition_type = freeze, runtime_action = freeze_binding)`。
  - 需要替換 artifact 或把 runtime 切到 fallback：由 `Rollback Controller` 發出 rollback request，預設採 `pause_then_replace`。
  - 需要 flatten / zero exposure：由 `Risk Policy` / `Incident Classifier` / `Kill Switch` 將 rollback 升級為 `liquidate_then_replace`。
- `freeze` 與 `rollback` 可以同時存在，但必須是兩個獨立動作：一個治理隔離未來 deployability，一個處理目前 active deployment。

### 11.3 Threshold → default action-path mapping

| Trigger family | Default proposed action | Required operational follow-through |
|---|---|---|
| performance degradation（Sharpe < 50%、drawdown > 1.25x、連 3 個評估窗劣化） | `retrain` 或 `revalidate` | 無；先回 research plane |
| execution drift（slippage drift、reject rate、partial-fill anomaly） | `revalidate`、`tighten_risk_policy`、必要時 `flag_for_review` | 若已升級成 active incident，才可能伴隨 `freeze` 或 rollback |
| feature / label drift（PSI、label mismatch） | `observe`、`revalidate`、`retrain` | 無；不得直接碰 runtime |
| human correction / repeated reject | `retrain`、`flag_for_review` | 無；後續如需 redeploy，仍要走新的 approval + deployment plan |
| Severity-1 / repeated Severity-2 / unresolved loader-binding-approval mismatch | `freeze`（依 target stage 定 medium/high） | 若有 active runtime，必須再決定是 `frozen` deployment follow-through 還是 rollback |
| rollback executed but problem persists | `freeze` + committee review | 若 runtime 仍不安全，rollback action 可由 `pause_then_replace` 升級為 `liquidate_then_replace` |

---

## 12. `executed`、cooldown、以及下游完成的正式語意

### 12.1 `executed` 代表什麼

`EvolutionDecision.executed` 的正式定義是：

- 該決議已被 **authoritative downstream plane 接受**
- 並且已產生不可變的 command / job / plan / state-change reference

它 **不代表** 下游 plane 已完成全部長流程。

### 12.2 依 action path 的 `executed` 判準

- governance-only `freeze` / `retire` / `restrict_pool_eligibility`：
  - governance state change 已寫入，並取得 state-change ref
  - 目前唯一可執行的是 **paper 階段、`candidate_artifact` 的 freeze**，且只有一條序列：
    Evolution owner 以自己的 propose-only service principal 向 Governance 提出一筆
    `evolution_execute` 申請（owner 自行計算 `subject.proposal_id` 與 `subject.proposal_content_digest`，
    request id 為 deterministic，重送會重用同一筆）→ 人類在既有 `/bff/approvals/{id}/decide` 決定**一次**
    （決定者不得是提案者，Governance 語意不變）→ 同一筆核准同時供 `ReviewMutation` 與
    `ApproveEvolutionDecision` 使用 → `ExecuteEvolutionAction` 先向 owner 取得 receipt binding
    （owner 重跑 `require_execution_approval`，失敗則不寫任何 Governance 資料）→ 在 Governance 寫入綁定
    decision 的 FreezeOrder（`scope=artifact`、`status=active`，帶 `evolution_decision_id`、`tenant_id`、
    `target_id`、`target_version`、`proposal_content_digest`、`approval_decision_id`）→ 以
    `execution_receipt`（`governance_freeze_order`）呼叫 `/execute`。重試會重用同一筆 FreezeOrder，
    不會重複建立，也不會產生第二個 cooldown。其他 freeze 範圍以 `unsupported_freeze_scope` 拒絕。
  - **重要**：FreezeOrder 只是治理隔離紀錄（governance quarantine record）。目前除 BFF read model 外，
    沒有任何服務會讀取並強制執行它，所以 `executed` **不會停止交易**；本變更不新增任何 enforcement，
    回應與 BFF 契約都明確標示 `trading_stopped: false`。
  - governance plane 永不自動派工：dispatch worker 對這類已核准的 decision 只保持（hold），
    不 submit、不計失敗次數、不進 dead letter；decision 被 executed 後由既有 converged 路徑發佈 outbox。
- research `retrain` / `revalidate`：
  - research job 或 governed work item 已建立
- deployment follow-through：
  - 新的 `DeploymentPlan` 已建立並被正式接手
- runtime rollback follow-through：
  - `Rollback Controller` 已接受 rollback request，並產生 runtime mitigation ref

### 12.3 cooldown / observation 何時開始

- cooldown 與 observation 都從 authoritative downstream plane 接受該 work item 的時間點開始
- downstream plane 後續成功 / 失敗 / 被取消，應回寫 incident / postmortem / audit / read model，但不改變 write owner 分界
- 若 downstream plane 失敗，應由新的 incident / postmortem / evolution proposal 處理，而不是讓原本 decision 越權直接修改 runtime / deployment object

---

## 13. write authority / sync-async 定義

### 13.1 write authority

- `Evolution Controller`：建立 `proposed`
- `Reviewer / Risk Owner / Governance Committee`：寫 `reviewed`, `approved`, `rejected`, `canceled`
- `Governance Plane`：寫 freeze / retire / eligibility 類 target governance state
- `Research Plane`：寫 retrain / revalidate work item 與其 research outputs
- `Governance / Promotion Plane`：寫 `ApprovalDecision`、`DeploymentPlan`
- `Rollback Controller`：寫 rollback request / mitigation command
- `Runtime Manager`：寫 `RuntimeBinding`、position ownership cutover、telemetry cutover

### 13.2 sync / async

#### 同步
- 建立 `proposed`
- 寫入 `reviewed`
- 寫入 `approved` / `rejected` / `canceled`
- authoritative downstream plane 接受 work item，並讓 decision 進入 `executed`

#### 非同步
- retrain / revalidate job completion
- deployment plan orchestration與 runtime activation
- rollback mitigation completion
- incident / postmortem follow-up 與 freeze-lift / redeploy readiness 判定

---

## 14. API / contract 建議

### API
- `POST /api/evolution/proposals`
- `GET /api/evolution/proposals/:id`
- `POST /api/evolution/proposals/:id/review`
- `POST /api/evolution/proposals/:id/approve`
- `POST /api/evolution/proposals/:id/reject`
- `POST /api/evolution/proposals/:id/execute`

### 事件
- `evolution.proposed`
- `evolution.reviewed`
- `evolution.approved`
- `evolution.rejected`
- `evolution.executed`
- `evolution.deployment_followthrough_requested`
- `evolution.rollback_requested`

---

## 15. 與其他 canonical 文件的關係

本文件應與以下文件一起閱讀：

- `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`
- `ROLLBACK_AND_POSITION_SEMANTICS.md`
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
- `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`
- `PAPER_CANARY_LIVE_POLICY.md`
- `services/control-plane/governance/evolution_controller_contract.md`
- `CANONICAL_CONTRACT_MIGRATION_DECISION.md`

---

## 16. Canonical status 建議

建議本文件維持為 **canonical governance policy file**，並作為 `EVO-004` 的 L1 routing truth。

---

## 17. 自動產生演化方向的達標標準 (Automatic program qualification)

操作者決定 2026-11-04：演化方向可以手動建立，也可以自動產生；自動產生必須有篩選標準，只有達標者才建立 program、送人工審核、再進入真正的演化。2026-11-05 取代 `EVOLUTION-PROGRAM-QUALIFY-DRAFT-20261104`，修正其中兩項在真實資料上永遠達不到的寫法。

Operator decision 2026-11-04: automatically generated programs must pass this standard before human review. Decision 2026-11-05 replaces the draft wording of two criteria that real data could never reach.

### 17.1 執行位置與結果

- 在既有 `POST /api/evolution/daily-sweep` 結尾執行（`services/evolution/program_qualification.py`），不新增 worker 或 container。
- 對每一筆 `proposed` 的 `EvolutionDecision` 逐項判定。六項全過才經 `ProgramService` 建立恰好一個 program：`origin=auto_qualified`、`created_by=evolution-daily-sweep`、`source_decision_ids`、`qualification`（`policy_version`、`evaluated_at`、每項的 `observed`/`threshold`/`result`）；之後系統身分只執行一次 `submit_evolution_review`，不核准、不派送。
- 重複 sweep 對 `tenant + decision_id` 冪等。
- 標準由 `services/evolution/config/program_qualification_policy.json` 提供（含 `policy_version`）。檔案缺失或無效時 fail closed：不建立任何 draft，並記錄原因（`GET /api/evolution/program-qualification` 的 `failure_reason`、`GET /api/evolution/sweep-status`）。
- qualifier 失敗不使 sweep 失敗、不 rollback；失敗經 `sweep-status` 的 `qualifier_last_failure_*` 呈現。
- 報告（`policy_version`、`evaluated_at`、`evaluated`、`qualified`、`drafted`、依第一個失敗項的計數、分頁的每筆決策與六項結果）持久化於 `EVOLUTION_DATA_DIR`，重啟後仍可讀；BFF 為 `GET /bff/management/evolution-qualification`。

### 17.2 六項標準與預設值

| # | 標準 | 通過條件 |
|---|---|---|
| 1 | `real_threshold_signal` | snapshot `breached`，`signal_type` ∈ {`performance_degradation`, `execution_drift`, `feature_drift`, `human_correction`}，`metric_name` 在 policy allowlist（目前只有 `rolling_drawdown_multiple`），`observed_value`/`threshold_value` 為數值且用 comparator 重新判定仍超標。`manual_review`、`incident_followup_required`、`severity1_incident_count` 永不通過。 |
| 2 | `real_target` | `target_id` 經 registry owner 讀回為允許的 artifact 類型（registry 僅驗證身分與 artifact_type，不提供 stage）；stage 取自決策指名之 runtime 的 `GET /api/telemetry/runtime-summaries/{runtime_id}`，其 `artifact_id` 必須等於 `target_id`，且 `deployment_stage` 在 policy 內（預設 `paper`）；決策未指名 runtime、summary 指向其他 artifact 或讀不到一律 `target_unverifiable`；`infra-component` 目標拒絕；其 persona 的 `data_scope` 為 test/acceptance/fixture/synthetic 時拒絕（`allow_test_scope` 預設 false）；無法讀回 = `target_unverifiable`。 |
| 3 | `persistent_breach` | 目標 binding 的 drift 評估歷史（`GET /api/reconciliation-drift/evaluations`）在 `lookback_days`（預設 30）內，同一 `threshold_metric` 的 `governed_threshold_breached=true` 涵蓋至少 `min_distinct_breach_days`（預設 3）個不同的行情日。行情日 = 該評估所引用 `drawdown_snapshot` 事件（`drift_checks[].drawdown_event_id`）的 `metadata.mark_refs[].as_of` 日期；週末或假日重複同一個 mark、同一天多次評估、未指名因果事件的評估都不加天數。IncidentCase 與 decision 不是計數來源（同一 binding 與 cluster 永遠只有一筆 IncidentCase）。 |
| 4 | `fresh_evidence` | `GET /api/telemetry/runtime-summaries/{runtime_id}` 通過與 `threshold_sweep_worker` 相同的 stale/degraded 與逐指標年齡檢查（`metric_max_age_seconds` 預設 172800），且該 binding 的最新評估（`latest_evaluation_window_seconds` 預設 3600 內）仍為 `governed_threshold_breached=true`。IncidentCase 第一個事件的年齡不影響此項。讀不到即失敗。 |
| 5 | `executable_action` | `action_type` 在 `_ACTION_PATHS` 對應 `execution_plane=research`（`retrain`、`revalidate`）。`freeze`、`flag_for_review` 等不建立 program，維持原決策路徑。 |
| 6 | `no_open_duplicate` | 決策仍為 `proposed`；同 target 與 metric 沒有 `draft`/`under_review`/`active`/`paused` 的 program；`stopped` 的 program 在 `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` §5.2 的 cooldown（research 路徑 3 天）內也擋。Program 生命週期沒有 `rejected` 狀態，`stopped` 是人為終止的 owner 狀態。 |

### 17.3 讀取邊界

- 需要讀其他服務的標準（2、3、4）只對已通過標準 1、5、6 的決策執行；數百筆無關提案不做任何逐筆 owner 讀取，其餘項目回報 `not_evaluated`。
- 讀取的服務與憑證：reconciliation-drift（`RECONCILIATION_DRIFT_URL`/`RECONCILIATION_DRIFT_AUTH_TOKEN`）、telemetry（`PANTHEON_TELEMETRY_API_URL`/`PANTHEON_TELEMETRY_SERVICE_TOKEN`）、registry（`PANTHEON_REGISTRY_API_URL`/`EVOLUTION_REGISTRY_SERVICE_TOKEN[_FILE]`）、persona（`PERSONA_URL`/`EVOLUTION_PERSONA_SERVICE_TOKEN[_FILE]`）。任一缺失 fail closed。檔案型憑證（`*_SERVICE_TOKEN_FILE`，由 `scripts/issue_dev_paper_principals.py` 簽發：registry 僅 `registry-reader`、persona 僅 `viewer`）於每次讀取時才解析，續簽後無需重啟；檔案不可讀只使該次讀取 fail closed。
- 目前只有 `rolling_drawdown_multiple`（經 reconciliation-drift）能產生通過標準 1 的指標；Sharpe、slippage、reject rate、PSI、human correction 有 evaluator 但尚無 producer。
