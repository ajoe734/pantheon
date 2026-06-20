# A2 — Candidate ScoringRecipe 規格

> 狀態：Design Frozen v1.0  
> 阻擋解除：`AG-BE-CP-001`、`AG-FE-TR-002`  
> 原則：Candidate score 是版本化、可解釋、per-strategy 的 ranking recipe；不是由 LLM 臨時給一個神秘分數。

---

## 1. 目標

Candidate ScoringRecipe 負責把策略版本產生的候選標的轉為：

- 可比較的原始分數。
- 可解釋的 component contribution。
- Evidence confidence。
- Risk penalty。
- 最終 effective ranking。

它不決定下單，也不取代交易員裁示。候選可以被交易員保留、剔除、暫放、深入研究或送 Shadow。

---

## 2. 計算模型

```text
base_score = 100 × Σ(positive_component.normalized_value × positive_component.weight)
penalty_score = 100 × Σ(penalty_component.normalized_value × penalty_component.weight)
raw_score = clamp(base_score - penalty_score, 0, 100)
confidence_multiplier = 0.60 + 0.40 × evidence_confidence
effective_score = raw_score × confidence_multiplier
```

顯示時必須同時呈現：

```text
raw_score
confidence
risk_penalty
final effective_score
```

不得只呈現 final score。

---

## 3. Normalization

支援下列 transform：

```text
min_max
robust_zscore
percentile_rank
binary
logistic
piecewise_linear
inverse_percentile
```

預設規則：

- Cross-sectional component 使用 universe 內 percentile rank。
- 金額／成交量等長尾資料先 winsorize 1%／99%，再 robust z-score 或 percentile。
- 機率、信賴值、資料品質已在 `[0,1]` 者直接使用。
- 所有 transform 需記錄 `reference_universe_id`、`data_cutoff` 與參數。

---

## 4. Missing Value Policy

每個 component 必須宣告：

```text
reject_candidate
score_zero
impute_median
mark_needs_research
cap_final_score
not_applicable
```

Critical component 缺失的處理：

- `data_quality` < 0.50：`effective_score` 上限 49，狀態 `needs_more_research`。
- `liquidity` 缺失且策略會進入部位：不可進 `approved_for_monitoring`。
- `evidence_confidence` 缺失：預設 0.25，並顯示資料不足。
- 法遵 suppression 命中：不排名，狀態 `suppressed`。

---

## 5. Component Contract

```ts
type CandidateScoreComponent = {
  componentId: string;
  label: string;
  category: "alpha" | "confidence" | "liquidity" | "risk" | "execution" | "data_quality" | "custom";
  rawValue: number | null;
  normalizedValue: number | null;
  transform: string;
  direction: "higher_better" | "lower_better";
  weight: number;
  contribution: number;
  missingPolicy: string;
  evidenceRefs: string[];
  explanation: string;
};
```

```ts
type CandidateScoreResult = {
  candidateId: string;
  recipeId: string;
  recipeVersion: number;
  rawScore: number;
  penaltyScore: number;
  evidenceConfidence: number;
  effectiveScore: number;
  rank: number | null;
  band: "priority_review" | "discuss" | "needs_research" | "park" | "suppressed";
  components: CandidateScoreComponent[];
  blockers: string[];
  dataCutoff: string;
};
```

---

## 6. Weight Rules

- Positive component 權重合計必須等於 1.0。
- Penalty 權重合計不得大於 0.50。
- 單一 positive component 權重上限 0.25。
- 單一 penalty contribution 上限 20 分。
- `data_quality` 與 `evidence_confidence` 不得完全移除。
- Per-strategy override 必須建立新 recipe version，不可原地改。
- 使用者可裁示權重，但交易僕人必須說明預期影響並提供 before/after preview。

---

## 7. Default Score Bands

| effective_score | band | 預設處理 |
|---:|---|---|
| 80–100 | priority_review | 優先逐檔討論 |
| 65–79.99 | discuss | 進待討論池 |
| 50–64.99 | needs_research | 建立補充研究 |
| 0–49.99 | park | 暫放／剔除候選 |
| n/a | suppressed | 不顯示排名，只顯示 suppression reason |

交易員可以保留低分候選，但必須記錄 override reason。

---

## 8. 贏家分點策略預設 Recipe

Positive components：

| component | weight | 說明 |
|---|---:|---|
| `branch_historical_profitability` | 0.20 | 分點歷史交易成本後績效 |
| `branch_identity_confidence` | 0.10 | 關係人—分點概率映射信賴值 |
| `information_lead_proxy` | 0.12 | 公開資料事件領先關聯 proxy |
| `accumulation_persistence` | 0.12 | 連續買進、集中度與價格承接 |
| `expected_value` | 0.18 | 後續報酬概率 × payoff 的成本後 EV |
| `liquidity_capacity` | 0.10 | 流動性與可承接量 |
| `catalyst_alignment` | 0.08 | 產業／事件催化一致性 |
| `data_quality` | 0.10 | coverage、PIT、缺漏、來源品質 |

Penalty components：

| component | max weight | 說明 |
|---|---:|---|
| `related_branch_distribution_risk` | 0.15 | 關聯分點反向出貨或資金遷移風險 |
| `price_extension_risk` | 0.10 | 股價已過度擴張 |
| `concentration_risk` | 0.08 | 單一分點／股票貢獻過度集中 |
| `capacity_shortfall` | 0.10 | 對交易員預期部位容量不足 |

### Winner Branch 特殊 Gate

- `branch_historical_profitability` 樣本少於 10 次完整 round-trip：標 `low_sample`，confidence 不得超過 0.45。
- 關係人映射若只有單一共同時間點：`branch_identity_confidence` 上限 0.35。
- Event lead proxy 少於 5 個獨立事件：不得標為 high confidence。
- 關聯分點 distribution risk > 0.80：候選最多進 `needs_research`，不可直接 priority review。

---

## 9. Per-Strategy Override

Recipe override 來源：

```text
servant_proposal
user_decision
research_result
shadow_evaluation
governance_policy
```

每次 override 必須包含：

```json
{
  "base_recipe_id": "recipe_winner_branch_v1",
  "proposed_version": 2,
  "changes": [
    {"component": "liquidity_capacity", "from": 0.10, "to": 0.16},
    {"component": "information_lead_proxy", "from": 0.12, "to": 0.08}
  ],
  "reason": "使用者為大額資金持有者，容量限制較一般策略重要",
  "evaluation_plan": "re-rank + shadow compare"
}
```

---

## 10. UI 要求

Candidate table 顯示：

```text
Rank
Symbol
Effective Score
Confidence
Top 3 positive drivers
Top 2 penalties
Data quality badge
Status
```

點擊 score 打開 decomposition drawer：

- raw／normalized value。
- weight。
- contribution。
- evidence。
- missing／cap reason。
- recipe version。

不得用單一星等取代 decomposition。

---

## 11. Governance

- Recipe 是 StrategyVersion 的一部分或明確 ref。
- 同一 CandidatePool 的所有候選必須使用同一 recipe version 與 data cutoff。
- 重算需建立新的 CandidatePool snapshot 或 score run，不可覆寫歷史。
- 交易員 override、剔除原因與後續 outcome 必須保留供 Shadow／preference learning。

---

## 12. Definition of Done

- JSON Schema 驗證通過。
- Default winner-branch recipe 可產生 deterministic ranking。
- 權重、transform、missing policy、evidence、penalty 可追溯。
- 前後端使用同一 canonical recipe schema。
- Per-strategy override 版本化。
- UI 可顯示 score decomposition。
- 缺 critical data 時不會輸出看似精確的高分。
