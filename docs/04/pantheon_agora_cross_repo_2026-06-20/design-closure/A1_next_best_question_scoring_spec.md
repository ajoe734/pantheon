# A1 — Next-Best-Question 評分與對話追問規格

> 狀態：Design Frozen v1.0  
> 阻擋解除：`AG-BE-SW-003`  
> 適用：Agora 策略工坊、私人交易僕人 `agora-strategy-completeness` skill  
> 原則：交易僕人不是填表機；先最大化理解，只問會改變研究、風險或策略版本的下一個問題。

---

## 1. 目標

Next-Best-Question（NBQ）負責從策略工坊目前所有未確認事項中，選出**下一個最值得交易員裁示的問題**。它必須：

1. 優先解除會阻擋研究、回測、風險評估或交易操盤室導入的缺口。
2. 不詢問可由 Pantheon 工具、公開資料或既有預設自行推定的低階問題。
3. 一次只提出一個 primary question；最多附兩個 optional clarifications。
4. 先把交易員已說明的內容重構完整，再追問。
5. 所有推定值必須標示為 provisional，不得假裝交易員已確認。

---

## 2. 問題候選來源

候選問題只能由下列來源產生：

- `StrategyCompletenessMap` 中的 `missing`、`weak`、`conflicting`、`inferred_needs_confirmation`。
- ResearchPlan 建立失敗的 typed blocking reason。
- 研究／回測結果中會改變策略規則的未決選項。
- 風險、資料可得性、PIT、成本、流動性、出場／失效等硬門檻。
- 交易員明確表示「這一點要再討論」的項目。

禁止從 UI 欄位順序、schema 欄位順序或 LLM 臨時好奇心產生問題。

---

## 3. Eligibility Gate

候選問題在評分前必須通過全部 eligibility gate：

| Gate | 通過條件 |
|---|---|
| Unanswered | 對話、記憶與 StrategySpec 中沒有已確認答案 |
| Non-derivable | 不能由已授權工具、資料或 deterministic default 直接得到 |
| Decision-relevant | 答案會改變 StrategySpec、ResearchPlan、risk gate、position plan 或 dashboard monitoring requirement |
| Scope-safe | 不要求不必要的私人資訊、其他使用者資料或 Management-only 資料 |
| Non-duplicate | 與最近 5 輪已問問題語義相似度不得高於 0.85，除非答案衝突 |
| User-level appropriate | 不把工程實作細節、框架選擇、欄位格式丟給交易員 |

未通過 eligibility gate 的問題直接淘汰，不進行評分。

---

## 4. 評分公式

所有因子正規化至 `[0, 1]`。

```text
base_score =
    0.30 × information_gain
  + 0.25 × downstream_blocking_weight
  + 0.20 × risk_impact
  + 0.10 × research_cost_reduction
  + 0.15 × user_relevance

penalty =
    0.35 × already_answered_penalty
  + 0.25 × low_level_question_penalty
  + 0.20 × cognitive_burden_penalty
  + 0.20 × premature_optimization_penalty

final_score = clamp(100 × (base_score - penalty), 0, 100)
```

### 4.1 information_gain — 30%

衡量回答後預期減少多少策略不確定性。

```text
information_gain = Σ(target_field_importance × expected_uncertainty_reduction)
```

欄位重要度：

| 類別 | importance |
|---|---:|
| exit / invalidation | 1.00 |
| risk / leverage / portfolio constraints | 0.95 |
| entry / signal confirmation | 0.90 |
| data availability / PIT / identity mapping | 0.90 |
| position sizing / add / reduce | 0.85 |
| universe / candidate rule | 0.80 |
| feature / alpha definition | 0.80 |
| execution / cost / liquidity | 0.80 |
| validation / OOS | 0.75 |
| regime | 0.70 |
| hypothesis wording | 0.55 |
| display / dashboard preference | 0.30 |

### 4.2 downstream_blocking_weight — 25%

| 狀態 | 值 |
|---|---:|
| 阻擋安全／法遵／PIT | 1.00 |
| 阻擋 ResearchPlan 或 backtest | 0.90 |
| 阻擋策略版本定版 | 0.80 |
| 阻擋加入交易操盤室 | 0.75 |
| 只影響結果解釋 | 0.45 |
| 只影響 UI 呈現 | 0.20 |

### 4.3 risk_impact — 20%

回答若能避免資料洩漏、過度槓桿、缺乏出場、流動性不足、錯誤因果、身份誤配等重大風險，給高分。

| 影響 | 值 |
|---|---:|
| 可能使策略風險失控或無法合法表述 | 1.00 |
| 可能使回測失真或 look-ahead | 0.90 |
| 可能顯著改變 drawdown / capacity | 0.75 |
| 影響一般績效 | 0.50 |
| 影響偏好而不影響風險 | 0.20 |

### 4.4 research_cost_reduction — 10%

回答能否避免不必要資料拉取、模型訓練、回測版本、committee session 或高成本研究。

### 4.5 user_relevance — 15%

綜合：

- 與使用者本輪重點的語義相似度。
- 與該交易員過去實際裁示習慣的相關度。
- 是否屬於交易員明確標註「重要」的策略部分。
- 是否適合交易員專業程度。

---

## 5. Penalty 定義

### already_answered_penalty

- 已有 confirmed answer：1.0，直接淘汰。
- 有 provisional answer 且無衝突：0.5。
- 答案分散在多輪，需要確認整合：0.2。

### low_level_question_penalty

符合任一情況即加分：

- 可由 data catalog 查得。
- 可由工具／後端選擇，不需要交易員決定。
- 只是問欄位格式、framework 名稱、技術參數。
- 可以採用可逆且明示的 provisional default。

建議值：

| 類型 | penalty |
|---|---:|
| 交易員不應處理的工程細節 | 1.00 |
| 可由工具直接回答 | 0.80 |
| 可用可逆 default | 0.50 |
| 真正需要個人偏好 | 0.00 |

### cognitive_burden_penalty

問題同時包含 3 個以上獨立裁示、長度超過 80 中文字或需要交易員先理解系統內部術語時，給 0.4–1.0。

### premature_optimization_penalty

基礎定義未完成前詢問細微參數、畫面、模型選擇、調參範圍時給 0.5–1.0。

---

## 6. Mandatory Override

以下缺漏不經一般排序，直接進 mandatory queue；仍一次只問一個：

1. 缺少資料可得時間／PIT 定義，且即將啟動回測。
2. 缺少出場／失效條件，且即將建立 paper / shadow decision。
3. 缺少最大部位、槓桿或風險上限，且即將產生 allocation proposal。
4. 使用者要求將統計關聯斷言為內線／操縱。
5. 同一策略內規則直接衝突。
6. ContextBundle 將包含未授權私人資料。

Priority：法遵／隱私 > PIT／資料洩漏 > risk／leverage > exit/invalidation > execution/cost。

---

## 7. 問題提出規則

### 預設輸出格式

```text
僕人目前已整理：<1–3 句>

目前真正會影響下一步的是：<缺漏名稱>

請您裁示：<單一 primary question>

可先採暫定值：<若適用>
```

### 問題數量

- Primary：最多 1。
- Optional clarifications：最多 2，且必須同屬同一 decision bundle。
- 若 final_score < 55：不追問，採明示 provisional assumptions 繼續。
- 若第一、第二名分差 < 5 且同屬同一 decision bundle，可合併成一個多選裁示卡。

### Tie-breaker

1. mandatory override。
2. downstream_blocking_weight 高者。
3. risk_impact 高者。
4. 交易員近期關注者。
5. 能以選項而非自由文字回答者。

---

## 8. 可調參與學習

權重作為 `QuestionScoringPolicy.v1` 保存，可由離線 eval 更新，不允許私人交易僕人在 session 中自行改權重。

可學習項目：

- 使用者對不同問題類型的回答率。
- 問題後策略定義改善幅度。
- 問題是否導致回測版本有效改善。
- 使用者標註「問題太蠢／可由助手自行處理」的 correction trace。

任何權重更新：offline replay → golden cases → shadow evaluation → policy version review。

---

## 9. Golden Case 最低驗收

至少維護以下回歸案例：

1. 贏家分點／關係人映射：優先詢問映射證據在 score 中是必要條件或加權證據，而不是問資料格式。
2. 產業落後補漲：若 universe 已清楚但出場缺失，優先問失效條件。
3. 技術突破：若入場與停損完整、部位規則缺失，優先問單檔與總曝險。
4. Pair trade：若 pair 已選但 spread/hedge ratio 未定，優先問 hedge 定義或允許工具推定。
5. 事件交易：若事件日期與可得時間不明，先問／確認 PIT，而不是問模型。
6. Options：若 payoff 已定但最大 loss 未明，先問 risk budget。
7. 使用者一次已完整描述：不得逐欄重問，應直接建立 ResearchPlan。
8. 可以由工具查詢：不得問交易員資料筆數、framework 或 API 名稱。
9. 有 provisional defaults：明示後先跑初步研究。
10. 使用者糾正問題太低階：後續相似問題需受 low-level penalty。

完整 golden fixtures 見 `next_best_question_gold_cases.json`。

---

## 10. API / Skill Output

```json
{
  "policy_version": "QuestionScoringPolicy.v1",
  "primary_question": {
    "question_id": "q_...",
    "text": "...",
    "target_fields": ["data.identity_mapping_role"],
    "score": 87.5,
    "mandatory": false,
    "why_now": "...",
    "answer_mode": "single_choice|multi_choice|free_text|confirm_provisional",
    "options": []
  },
  "optional_clarifications": [],
  "provisional_assumptions": [],
  "suppressed_questions": [
    {"question_id": "...", "reason": "derivable_by_tool"}
  ]
}
```

---

## 11. Definition of Done

- 因子、權重、門檻、penalty、mandatory override 全部實作。
- golden fixtures 全數通過。
- 同一輸入、policy version 與 persona context 產生 deterministic ranking。
- 不把 framework、資料欄位格式、可自行查詢資訊問交易員。
- 一次不超過一個 primary question。
- 所有問題能追溯 target field、score components 與 why_now。
