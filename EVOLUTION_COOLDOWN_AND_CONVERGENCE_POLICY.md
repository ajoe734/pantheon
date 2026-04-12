# EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY

Last updated: 2026-04-11
Status: canonical evolution cooldown and convergence policy for Pantheon
Tier: L1 Platform Architecture & Policy
Scope: evolution triggers, review ownership tiers, cooldown windows, observation windows, and loop prevention rules
Conflict rule: this document refines the evolution process; it refines and overrides the high-level evolution mentions in EVOLUTION_REVIEW_AND_THRESHOLDS.md

## 1. 目的

本文件定義 Pantheon Evolution Plane 的：

- 觸發條件
- reviewed / approved owner
- cooldown
- observation window
- 收斂條件
- 防止 infinite loop 的規則

並補足：
- drift threshold 是多少
- proposed -> reviewed -> approved 誰負責
- freeze 和 rollback 有何差異
- evolution decision 怎麼避免反覆震盪

---

## 2. 結論摘要

### 2.1 EvolutionDecision 狀態
維持：

`proposed -> reviewed -> approved -> executed`

### 2.2 reviewed owner 依風險分層
- 低風險：Reviewer on Duty
- 中風險：Reviewer + Risk Owner
- 高風險：Governance Committee

### 2.3 必須有 cooldown + observation window
同一 target 不可連續無限 mutate。

### 2.4 freeze ≠ rollback
- rollback = runtime/deployment mitigation
- freeze = governance quarantine

---

## 3. 風險分層與 review owner

## 3.1 低風險 evolution
類型：
- retrain
- revalidate
- observe
- require_more_data

reviewed by：
- Reviewer on Duty

approved by：
- Reviewer on Duty 或自動規則追認

## 3.2 中風險 evolution
類型：
- freeze paper artifact
- reduce budget
- mutate route policy
- tighten consult policy

reviewed by：
- Reviewer + Risk Owner

approved by：
- Risk Owner / designated reviewer

## 3.3 高風險 evolution
類型：
- freeze live strategy
- retire alpha template
- split persona
- merge persona
- remove live_owner
- change pool eligibility

reviewed by：
- Governance Committee

Governance Committee 建議固定席次：
- Research Lead
- Risk Owner
- Operator / Platform Owner

---

## 4. 觸發條件

## 4.1 Performance degradation
觸發 proposed：
- 20 日風險調整績效低於基線 **50%**
- 或 rolling drawdown 超過預期 **1.25 倍**
- 或連續 **3 個評估窗**劣於 baseline

## 4.2 Execution drift
觸發 proposed：
- realized slippage 較 20 日基線惡化 **25% 以上**
- 或 order reject rate > **1%**
- 或 partial fill / timeout pattern 連續 **3 個交易日**異常

## 4.3 Feature / label drift
- PSI > **0.20**：warning
- PSI > **0.30**：mandatory review
- label generation mismatch > **0.5%**

## 4.4 Human correction threshold
觸發 proposed：
- 同一 persona 在 **5 個 session 內超過 3 次**重大人工修正
- 同一 strategy 在 **2 週內被連續 reject 2 次以上**

## 4.5 Governance / incident threshold
直接高風險：
- 任一 Severity-1 incident
- 同一 artifact 30 天內 2 次 Severity-2
- unresolved loader / binding / approval mismatch

---

## 5. cooldown 與收斂條件

## 5.1 single-active-rule
同一 target 同時間只能有 **一個 active EvolutionDecision**。

## 5.2 cooldown windows
v1 預設：

| action family | cooldown | observation window | 說明 |
|---|---|---|---|
| `observe` / `require_more_data` / `flag_for_review` / `retrain` / `revalidate` | 3 天 | 7 天 | research-facing action；executed 代表已建立 governed work item，不代表 artifact 已重新 deploy |
| `reduce_budget` / `tighten_risk_policy` / `mutate_persona_route_policy` / `mutate_consult_policy` / `freeze` on `paper` or `canary` | 7 天 | 7 天 | 中風險 path；若有 companion `DeploymentPlan(current_stage -> frozen)`，仍沿用同一 parent window |
| `freeze` on `live` / `retire` / `split_persona` / `merge_persona` / `remove_live_owner_role` / `restrict_pool_eligibility` / `force_risk_off` / `revive` | 14 天 | 14 天 | 高風險 path；若伴隨 rollback，rollback 不開新 window，而是沿用 parent decision |
| rollback companion command | 不另開 window | 不另開 window | rollback 是 operational follow-through，不是新的 `EvolutionDecision` 類型 |
| redeploy follow-through | 不另開 evolution cooldown | 依 parent observation + deployment stage policy | redeploy readiness 仍要通過新的 approval / `DeploymentPlan` / stage gate |

## 5.3 observation window
每次 executed 後都要進 observation：
- 期間允許收集新 drift / incident
- 但不得再次對同 target 做同類結構性動作
- 觀察期的 authoritative clock 以 downstream plane 接受 work item / state change 的時間點起算
- 研究、部署、與 runtime mitigation 的長流程完成時間可晚於 observation start，但不改變 single-active-rule

## 5.4 escalation
若 cooldown 內又發生嚴重問題：
- 不再重複 mutate
- 直接升級到 freeze / rollback / committee review

---

## 6. freeze vs rollback

## 6.1 rollback
- operational mitigation
- 立即把 active deploy 替換 / 回退 / 停止
- 目標：止血

## 6.2 freeze
- governance quarantine
- 禁止該 strategy / artifact 再進新 deploy
- 目標：暫停擴散，等待重新 review

它們常一起出現，但語義不同。

---

## 7. EvolutionDecision 類型

- observe
- retrain
- revalidate
- require_more_data
- flag_for_review
- reduce_budget
- tighten_risk_policy
- mutate_persona_route_policy
- mutate_consult_policy
- freeze
- split_persona
- merge_persona
- retire
- remove_live_owner_role
- restrict_pool_eligibility
- force_risk_off
- revive

---

## 8. v1 決策

1. reviewed owner 依風險分層
2. drift / incident / correction 都可觸發 proposed
3. 強制 cooldown + observation window
4. 同一 target 只允許一個 active decision
5. freeze 與 rollback 正式區分
6. repeated severe issue 直接升級，不做無限 mutate

---

## 9. 後續規格拆解（non-blocking，不影響目前 L1 真相）

以下項目屬於後續 cooldown / convergence 細化，不是本文件目前生效的前置條件。

- drift policy registry schema
- evolution decision API
- committee voting / quorum rules
- automated vs manual approval matrix
