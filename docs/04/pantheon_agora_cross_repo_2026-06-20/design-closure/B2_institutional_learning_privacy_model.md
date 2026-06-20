# B2 — 去識別 Institutional Learning 隱私模型

> 狀態：Design Frozen v1.0  
> 阻擋解除：`AG-BE-EV-001` institutional writeback、跨使用者 aggregate learning  
> 原則：私人個人化、平台共通能力、Alpha contribution 是三條不同資料路徑；私人交易策略預設不共享。

---

## 1. 三層資料域

### Domain P — Private Personalization

- 單一 user／servant 專用。
- 可含私人策略細節、Dashboard 偏好、CorrectionTrace、ShadowOutcome。
- 不跨使用者。
- 不進 institutional model，除非使用者明確 opt-in。

### Domain I — Institutional Aggregate

- 只含去識別、抽象化、最低樣本達標的 workflow／tool／question／presentation pattern。
- 不含完整策略規則、精確參數、稀有 symbol 組合或原始文字。

### Domain A — Alpha Contribution

- 另走 B3 Alpha governance。
- 即使已去識別，也不因為進 Domain I 就自動成為共享 Alpha。

---

## 2. 最小化原則

Institutional learning 只允許收集：

```text
skill success/failure
question type effectiveness
tool routing outcome
workflow step usefulness
widget preference category
generic correction labels
generic risk-check patterns
aggregate shadow outcome metrics
```

預設排除：

```text
raw prompt
raw journal
完整 StrategySpec
精確參數組合
user identity
account/broker identifiers
完整 symbol sequence
罕見事件與唯一交易時間
未授權私有 Alpha
```

---

## 3. Aggregation Thresholds

### Platform-global

- `k >= 10` 個不同使用者。
- 至少 3 個不同時間 bucket。
- 任一使用者貢獻不得超過聚合樣本 20%。
- 對類別屬性至少 `l-diversity >= 2`。

### Desk-level

- `k >= 5` 個不同使用者。
- 需 desk policy 允許。
- 若 desk 使用者少於 5，禁止產生跨人 aggregate。

### Time bucketing

- 最細 7 日 bucket。
- 對低頻或高辨識度事件改用 30 日 bucket。

未達門檻的資料維持 private 或 suppress。

---

## 4. 去識別流程

```text
Private Event
→ Purpose filter
→ Field allowlist
→ Remove direct identifiers
→ Generalize quasi-identifiers
→ Strategy-secret filter
→ k/l threshold
→ Re-identification tests
→ Institutional Feature Record
→ Governance review / training candidate
```

### Generalization examples

| 原始 | Institutional |
|---|---|
| 2330, 2454, 3034 | large-cap semiconductor cohort |
| 分點集中度 23.7% | concentration bucket 20–25% |
| 2026-06-19 09:17 | week bucket |
| 精確交易規則 | generic decision pattern |
| 使用者文字理由 | taxonomy label + redacted summary |

---

## 5. 私有 Alpha 洩漏防護

Institutional writeback 前必須通過：

1. Exact text / n-gram overlap scan。
2. Semantic similarity scan against private StrategySpec corpus。
3. Rule overlap scan：features、thresholds、symbol universe、sequence。
4. Rare combination detector。
5. Private source reference removal。

Review trigger：

```text
semantic_similarity >= 0.85
or rule_overlap >= 0.70
or rare_combination_count < 10 users
```

命中時：禁止自動寫入，轉 human privacy review。

Institutional corpus 不得保存可重建完整私有策略的 component combination。

---

## 6. Re-identification Audit

每批資料需執行：

- Quasi-identifier uniqueness test。
- Nearest-neighbor linkage simulation。
- Canary string leakage scan。
- Membership inference test（若用於模型訓練）。
- Model extraction prompt test。
- Small-cohort suppression test。

任一測試超標即 reject batch。

### Default thresholds

```text
unique quasi-identifier rows = 0
estimated re-identification risk <= 0.05
canary leakage = 0
membership inference advantage <= 0.10
```

---

## 7. Consent / Control

每位使用者 profile 需有：

```text
private_personalization_enabled = true
institutional_aggregate_opt_in = false by default
alpha_contribution_opt_in = false by default
research_quality_telemetry = required for service safety, minimized
```

使用者可：

- 查詢自己的資料用途摘要。
- 撤回未來 institutional contribution。
- 要求刪除可刪除的 private records。
- 不因拒絕共享 Alpha 而失去私人助手功能。

既有已納入模型的資料依 retention／model governance 處理，需有不可逆情況說明。

---

## 8. Institutional Feature Record

```ts
type InstitutionalFeatureRecord = {
  recordId: string;
  cohortId: string;
  patternType: "question_effectiveness" | "tool_routing" | "workflow_pattern" | "widget_preference" | "risk_check" | "shadow_metric";
  generalizedFeatures: Record<string, unknown>;
  aggregateMetrics: Record<string, number>;
  distinctUserCount: number;
  timeBucketCount: number;
  maxSingleUserShare: number;
  privacyTests: PrivacyTestResult[];
  sourceBatchRef: string;
  consentPolicyVersion: string;
  status: "candidate" | "approved" | "rejected" | "retired";
};
```

禁止包含 user ID、private strategy ID、raw text、exact symbol path。

---

## 9. Retention

建議預設：

| 類型 | 保留 |
|---|---|
| Raw private interaction | 90 日，可依服務需求延長但需明示 |
| Private structured memory | 使用期間 + 12 個月，使用者可刪 |
| Private strategy/evidence | 依策略與稽核政策 |
| Institutional aggregate | 24 個月後重新評估 |
| Privacy audit logs | 24 個月 |
| Alpha contribution records | 依 B3 永久 lineage／retirement policy |

敏感欄位需 field encryption 或 private object store。

---

## 10. Management Surface

Management 只可看：

```text
cohort size
privacy status
aggregate pattern
risk flags
model usefulness
writeback status
```

不得提供「Reveal original」或跨使用者 drill-down 到 raw content。

---

## 11. Definition of Done

- Private、institutional、Alpha 三路資料分開。
- k/l、單人占比、time bucket 門檻已實作。
- 稀有策略與語義重建風險有 suppression。
- opt-in 狀態可審計。
- re-identification／membership inference 測試可執行。
- Management 無法取得 raw private data。
- 未達門檻資料不會進 institutional corpus。
