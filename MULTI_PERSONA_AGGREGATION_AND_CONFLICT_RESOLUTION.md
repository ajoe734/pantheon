# MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION

Last updated: 2026-04-09
Status: canonical multi-persona aggregation and conflict resolution policy for Pantheon
Tier: L1 Platform Architecture & Policy
Scope: multi-advisor proposal aggregation, conflict resolution, synthesis service ownership, and single approved artifact production per capital pool
Conflict rule: this document overrides vague multi-persona mentions in architecture or capital pool docs; persona registry and session semantics defer to PERSONA_RUNTIME_MODEL.md; binding and deployment authority defer to BINDING_AND_DEPLOYMENT_SEMANTICS.md

---

## 1. 目的

本文件定義當多個 persona 同時對同一個 capital pool 提出建議時，Pantheon 如何：

- 聚合多人格提案
- 解決互相衝突的方向 / 權重 / 風險偏好
- 決定誰可以成為 live deployment sponsor
- 產出唯一的 `AllocationPolicyArtifact`

本文件覆蓋下列模糊點：

- 「上游聚合」到底是什麼 service
- optimizer-svc 是否負責仲裁
- Persona A 買、Persona B 賣時誰說了算
- 多 advisor persona 綁到同一個 pool 時的裁判機制

---

## 2. 結論摘要

### 2.1 必須有專門仲裁邏輯

Pantheon 必須有正式的 allocation aggregation / portfolio synthesis 能力，
負責將多 persona 提案合成成**唯一**的 `AllocationPolicyArtifact`。

**v1 實作方式：**

> `allocation-aggregator` 在 v1 先作為 `optimizer-svc` 內部的一個 domain module 實作。
> module 名稱建議為 `portfolio-synthesis` 或 `allocation-aggregation`。
> 暫不增加新的 deployable service。

這樣做同時滿足兩件事：

- 語義上把仲裁邏輯獨立出來，不是讓 optimizer 假裝自己就是仲裁器
- 部署上不擴張 service 數量，18 個服務清單保持穩定

**何時抽出為獨立 service：**

若出現以下條件，再提取為獨立 `allocation-aggregator-svc`：

- 多 pool / 多 sleeve 同時做 synthesis
- committee + optimizer + conflict resolution 工作量明顯放大
- 需要獨立擴縮、隔離、SLA

### 2.2 optimizer 不是仲裁者
Portfolio/Risk optimizer 只負責在既定目標與約束下求解配置，**不是衝突仲裁者**。

### 2.3 同一 pool / 同一 deployment scope 下，只有一個 active deployment sponsor
多人格可以同時作為：
- advisor
- paper_owner

但同一 pool / 同一 deployment scope 下，只有一個 active sponsor 能送出 live deployment。

### 2.4 優先序
正式仲裁優先序為：

1. pool risk policy
2. governance hard rules
3. committee override
4. aggregation weighting
5. persona suggestions

---

## 3. 模型

### 3.0 觸發時機

allocation aggregation 的觸發模型定義於 `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`。

簡述：
- 主要觸發：human / review-driven（reviewer 決定將多 persona proposal 送聚合）
- 次要觸發：scheduled re-synthesis（週期性重新評估當前 allocation）
- 不是：每個新 proposal 進來就自動觸發

### 3.1 Persona 建議輸入格式

每個 persona 對 pool 或 candidate universe 的建議必須標準化成：

```text
PersonaAllocationProposal
- proposal_id
- persona_id
- capital_pool_id
- scope_ref
- target_type (asset / sleeve / basket / pool)
- directions[]
- target_weights[]
- conviction
- uncertainty
- rationale_ref
- regime_ref
- valid_from
- valid_to
```

## 3.2 聚合輸出格式

聚合後輸出唯一：

```text
AllocationPolicyArtifact
- artifact_id
- capital_pool_id
- sponsor_persona_id
- synthesis_method
- target_weights
- constraints_bundle
- risk_budget
- provenance_refs[]
- conflict_resolution_log[]
```

---

## 4. ownership

## 4.1 allocation-aggregator module

負責：
- 收集多 persona proposal
- 執行仲裁 / 加權 / committee escalation
- 產出唯一 allocation artifact
- 記錄 conflict_resolution_log

v1 實作：作為 `optimizer-svc` 內部 domain module（非獨立 deployable service）。

## 4.2 optimizer layer
負責：
- 在聚合後目標之上求解
- 產出 constrained allocation result
- 不決定哪個 persona 比較對

## 4.3 governance plane
負責：
- 決定何時允許 committee override
- 決定何時允許某 persona 成為 sponsor
- 決定哪些 pool 允許 multi-persona aggregation

---

## 5. 衝突類型

## 5.1 Direction conflict
例：
- Persona A: buy
- Persona B: sell

## 5.2 Weight conflict
例：
- A 建議 8%
- B 建議 1%

## 5.3 Horizon conflict
例：
- A 是短期 event-driven
- B 是中期 trend

## 5.4 Risk posture conflict
例：
- A 想加槓桿
- B 想降風險

## 5.5 Regime conflict
例：
- A 判定 risk-on
- B 判定 risk-off

---

## 6. 仲裁機制

## 6.1 Hard veto
當 proposal 違反下列任何一項，直接 veto：
- pool risk policy
- forbidden asset class
- forbidden strategy family
- governance prohibition
- compliance block

此時 proposal 不進入加權。

## 6.2 Weighted fusion
對剩餘 proposal 進行加權。

建議權重來源：
- recent reliability score
- regime fit score
- confidence
- uncertainty penalty
- sponsor preference factor

示意：

```text
effective_weight
= reliability_score
* regime_fit
* confidence
* (1 - uncertainty)
* governance_multiplier
```

## 6.3 Committee override
當任一條件成立時，送 committee：
- long vs short 方向衝突且都高 conviction
- risk posture conflict 達重大程度
- sponsor persona 不明確
- pool 為高重要性 / 高資金量級
- 高風險策略族首次進 canary/live

committee 的輸出：
- choose sponsor
- choose synthesis rule
- reject all
- demand more data / re-run

## 6.4 Sponsor rule
同一 pool 同一 deployment scope 僅允許一個 sponsor persona。  
sponsor 的責任是：
- 持有最後 deployable allocation artifact 的治理責任
- 不是說 sponsor 一定獨自貢獻所有 alpha，而是它對這次合成結果負最終 owner 責任

---

## 7. 單 Pool 單 Runtime 的落地規則

在沒有 `PoolSleeve` 物件之前，Pantheon 採：

- **單 Pool**
- **單 Runtime**
- **單 Active Artifact**

因此，多 persona 同池共存的唯一正確方式是：
- 在上游 aggregation 合成
- 再只把唯一 artifact 送進 execution

不得在同一 pool runtime 內並行載入兩個相互獨立的 live artifact 互相打架。

---

## 8. PoolSleeve 的未來擴充

v1 不引入 `PoolSleeve`。  
若未來引入，則需新增：
- `pool_sleeve_id`
- sleeve-level budget
- sleeve-level sponsor
- sleeve-level aggregation rule

在那之前，不允許以「未來 sleeve 會解決」作為繞過仲裁定義的理由。

---

## 9. 與 risk policy 的關係

### 正式優先序
`pool risk policy > governance hard rules > committee > aggregator > persona`

也就是說：
- pool risk policy 永遠比 persona 大
- persona 不能用 aggregation 來突破 pool hard limits

---

## 10. conflict_resolution_log 要求

每次聚合都要留下結構化 log：

```text
- proposal_ids[]
- vetoed_proposals[]
- weighting_inputs
- weighting_outputs
- committee_ref (if any)
- sponsor_persona_id
- rejected_reason (if all rejected)
- timestamp
```

這是後續 governance、postmortem、lineage 的關鍵證據。

---

## 11. v1 決策

1. allocation-aggregator 在 v1 作為 `optimizer-svc` 內部 module 實作，暫不增加新的 deployable service
2. optimizer 不負責仲裁（arbitration 與 solving 分離）
3. 多 persona 同 pool 時，必須先聚合再 deploy
4. 同一 pool / 同一 scope 僅允許一個 sponsor
5. risk policy 永遠高於 persona 建議
6. v1 不引入 `PoolSleeve`
7. 每次聚合都必須產生 `conflict_resolution_log`
8. 若未來出現多 pool / 多 sleeve 高負載需求，再提取為獨立 `allocation-aggregator-svc`

---

## 12. 後續規格拆解（non-blocking，不影響目前 L1 真相）

以下項目屬於後續組合邏輯與營運規則拆解，不是本文件目前生效的前置條件。

- sponsor selection policy
- regime fit calculation
- committee escalation thresholds
- aggregation weighting calibration
- `AllocationPolicyArtifact` schema 詳細版
