# Semantic Extraction Evaluation Suite

This directory contains the frozen evaluation inputs, evaluation manifest schema, runner, and benchmark documentation for the single typed semantic extraction contract (`services.source_ingestion.semantic_extraction`).

## Overview

The evaluation harness establishes a standardized, reproducible, and verifiable benchmark for measuring semantic extraction performance before, during, and after model tuning or consumer migration in `SIMPLIFY-EXTRACTION-001`.

### Core Artifacts
- **`semantic_extraction_cases.jsonl`**: Frozen evaluation dataset of 210 deduplicated, permissioned cases covering Traditional Chinese (`zh-TW`) and English (`en`).
- **`semantic_extraction_manifest.schema.json`**: JSON Schema (Draft-7) defining the evaluation manifest specification, including metadata, aggregate metrics, thresholds, and per-case auditable logs.
- **`run_semantic_extraction_eval.py`**: Evaluation runner executing requests against either the deterministic baseline or an OpenClaw extraction client, computing metrics, verifying constraints, and generating a validated manifest.

---

## Evaluation Dataset Specification

The frozen dataset (`semantic_extraction_cases.jsonl`) contains 210 structured test cases created from deterministic source-derived patterns and financial domain templates.

### 1. Language Distribution
- **Traditional Chinese (`zh-TW`)**: 108 cases (51.4%)
- **English (`en`)**: 102 cases (48.6%)
- Total: 210 cases (satisfies the requirement of >= 200 total, >= 80 zh-TW, >= 80 en).

### 2. Dataset Partitioning (Splits)
- **`train`**: 126 cases (60%)
- **`validation`**: 42 cases (20%)
- **`holdout`**: 42 cases (20%)

Splits are balanced across languages, task types, and expected labels to ensure unbiased validation and holdout evaluation.

### 3. Task Types Covered
- **`intent`**: Intent classification across the 9 canonical `InteractionPrimaryIntent` categories:
  - `strategy_hypothesis`
  - `risk_overlay`
  - `execution_policy`
  - `portfolio_allocation`
  - `persona_policy`
  - `preference_example`
  - `negative_memory`
  - `operational_note`
  - `non_strategy`
- **`strategy_seed`**: Extraction of strategy specification seeds (hypothesis, asset classes, market scopes, required data, confidence, seed kind, status).
- **`trade_lesson`**: Extraction of trade lessons and reflections (scope, proposed changes, confidence).
- **`comprehensive`**: Combined extraction of intent, strategy seed, and trade lesson in a single request.

### 4. Admission, Boundary, and Negative Cases
The dataset includes comprehensive coverage of negative, refusal, and admission boundary conditions:
- **Tenant Isolation**: Missing tenant ID, whitespace-only tenant, invalid character tenant.
- **Source Status**: Rejected/blacklisted source records (`rejected`, `expired`, `prohibited`).
- **License Scope**: Prohibited license scopes (`prohibited`, `restricted_commercial`, `expired`, `proprietary_unlicensed`).
- **Point-in-Time Lookahead**: Records with event timestamps occurring after the `as_of` timestamp.
- **Sensitive Data & PII**:
  - Email addresses (`user@fund.com`)
  - Phone numbers (`+886-912-345-678`)
  - API keys (`AKIAIOSFODNN7EXAMPLE`)
  - Bearer tokens (`Bearer eyJhbGciOi...`)
  - High-precision capital numbers (`$10,000,000.00 USD`)
  - Broker accounts (`IBKR-ACCT-99214`)
  - Private note markers (`[PRIVATE]`)
  - Raw system prompt transcripts (`System Prompt: You are...`)
- **Non-strategy Abstentions**: Routine queries, chit-chat, and greeting text requiring proper abstention.

### 5. Label Provenance & Integrity
- All labels are derived deterministically using canonical schema definitions (`InteractionPrimaryIntent`, `TrainerSeedKind`, `StrategySpecSeedStatus`).
- AI-assisted curation and synthetic template derivation are explicitly labeled in case provenance without fabricated reviewer approvals.
- All cases reference the active governed source schema contract `services/source_ingestion/source_record.schema.json`.
- Template deduplication groups (`tpl_{category}_{index:03d}`) group all bilingual relatives and template variants together, and are strictly partitioned across splits without template or bilingual leakage. Source families (`desk_notes`, `social_discussion`, `market_analysis`, `research_paper`) are represented across splits to ensure balanced evaluation across all domains.

### 6. Ambiguous-Label Ledger (Audited 30 Boundary Cases)
The dataset includes 30 intentionally borderline cases (`case-pers-zh-101` to `case-pref-en-130`) designed to probe intent boundary discrimination between `persona_policy` / `preference_example` and nearby categories (`operational_note`, `strategy_hypothesis`, `execution_policy`). Each case has an explicit adjudication rationale, verified provenance, and aligned bilingual split grouping:

| Case ID | Lang | Split | Target Intent | Source Text Excerpt | Borderline Ambiguity | Adjudication Rationale |
|---|---|---|---|---|---|---|
| `case-pers-zh-101` | zh-TW | train | `persona_policy` | 角色設定：本交易員人格偏好保守穩健，絕不參與高波動未獲利投機個股之短線炒作。... | operational_note / strategy_hypothesis | Specifies trading agent persona, behavioral boundaries, and subjective investment posture; does not state concrete testable quantitative parameters. |
| `case-pers-zh-102` | zh-TW | train | `persona_policy` | 交易風格規範：設定風格為嚴謹量化研究員，一切操作皆須依據統計顯著性回測結果支持。... | operational_note / strategy_hypothesis | Specifies trading agent persona, behavioral boundaries, and subjective investment posture; does not state concrete testable quantitative parameters. |
| `case-pers-zh-103` | zh-TW | train | `persona_policy` | 代理人行為指引：模擬逆向價值型投資大師，在市場極度恐慌拋售時尋找具備深厚護城河之... | operational_note / strategy_hypothesis | Specifies trading agent persona, behavioral boundaries, and subjective investment posture; does not state concrete testable quantitative parameters. |
| `case-pers-zh-104` | zh-TW | train | `persona_policy` | 交易員人格設定：偏好動態避險與非對稱回報，對潛在重大虧損保持高度敏感與警惕。... | operational_note / strategy_hypothesis | Specifies trading agent persona, behavioral boundaries, and subjective investment posture; does not state concrete testable quantitative parameters. |
| `case-pers-zh-105` | zh-TW | validation | `persona_policy` | 決策性格規範：本策略代理人設定為中性理性執行者，嚴禁任何主觀情緒或報復性交易行為。... | operational_note / strategy_hypothesis | Specifies trading agent persona, behavioral boundaries, and subjective investment posture; does not state concrete testable quantitative parameters. |
| `case-pers-zh-106` | zh-TW | validation | `persona_policy` | 風格偏好指引：偏好高周轉率量化套利，著重單筆交易勝率與盈虧比之數學期望值。... | operational_note / strategy_hypothesis | Specifies trading agent persona, behavioral boundaries, and subjective investment posture; does not state concrete testable quantitative parameters. |
| `case-pers-zh-107` | zh-TW | holdout | `persona_policy` | 代理人人格設定：專注於總體經濟週期判斷，長線佈局具備結構性增長潛力之產業趨勢。... | operational_note / strategy_hypothesis | Specifies trading agent persona, behavioral boundaries, and subjective investment posture; does not state concrete testable quantitative parameters. |
| `case-pers-zh-108` | zh-TW | train | `persona_policy` | 交易員風格原則：本交易員專精於選擇權希臘字母平衡，追求Delta中性與Vega正向暴... | operational_note / strategy_hypothesis | Specifies trading agent persona, behavioral boundaries, and subjective investment posture; does not state concrete testable quantitative parameters. |
| `case-pref-zh-109` | zh-TW | train | `preference_example` | 偏好範例：示範如何在財報公告前建立跨式避險部位，例如2025年Q3台積電法說會前的範... | operational_note / few_shot_prompt | Demonstrates exemplary few-shot execution or trading workflow sample; intended as in-context reference rather than active production execution directive. |
| `case-pref-zh-110` | zh-TW | train | `preference_example` | 少樣本範例：展示突破20日均線且成交量大於5日均量時之標準進場示範，供模型參考。... | operational_note / few_shot_prompt | Demonstrates exemplary few-shot execution or trading workflow sample; intended as in-context reference rather than active production execution directive. |
| `case-pref-zh-111` | zh-TW | train | `preference_example` | 操作示範案例：示範當市場暴跌觸發流動性緊縮時，如何以限價單階梯式吸收拋盤之標準範式。... | operational_note / few_shot_prompt | Demonstrates exemplary few-shot execution or trading workflow sample; intended as in-context reference rather than active production execution directive. |
| `case-pref-zh-112` | zh-TW | train | `preference_example` | 範例展示：展示一組標準的配對交易共整合檢定與進出場日誌，供回測引擎作為偏好範式。... | operational_note / few_shot_prompt | Demonstrates exemplary few-shot execution or trading workflow sample; intended as in-context reference rather than active production execution directive. |
| `case-pref-zh-113` | zh-TW | validation | `preference_example` | 交易範例示範：提供三筆在聯準會利率決策公布時之標準避險執行範例，作為少樣本指導。... | operational_note / few_shot_prompt | Demonstrates exemplary few-shot execution or trading workflow sample; intended as in-context reference rather than active production execution directive. |
| `case-pref-zh-114` | zh-TW | holdout | `preference_example` | 多因子範例：示範如何計算個股之動能與低波動綜合評分並產出前十檔持股清單之標準示範。... | operational_note / few_shot_prompt | Demonstrates exemplary few-shot execution or trading workflow sample; intended as in-context reference rather than active production execution directive. |
| `case-pref-zh-115` | zh-TW | holdout | `preference_example` | 風控範例示範：展示當單一持股連續兩日跌停時之緊急應變措施與清算處置標準範本。... | operational_note / few_shot_prompt | Demonstrates exemplary few-shot execution or trading workflow sample; intended as in-context reference rather than active production execution directive. |
| `case-pref-zh-116` | zh-TW | train | `preference_example` | 少樣本展示：示範如何從非結構化財報附註中提取租賃負債與或有負債之結構化欄位範例。... | operational_note / few_shot_prompt | Demonstrates exemplary few-shot execution or trading workflow sample; intended as in-context reference rather than active production execution directive. |
| `case-pers-en-117` | en | train | `persona_policy` | Persona policy: this agent operates with c... | operational_note / strategy_hypothesis | Specifies trading agent persona, behavioral boundaries, and subjective investment posture; does not state concrete testable quantitative parameters. |
| `case-pers-en-118` | en | train | `persona_policy` | Agent persona guideline: systematic statis... | operational_note / strategy_hypothesis | Specifies trading agent persona, behavioral boundaries, and subjective investment posture; does not state concrete testable quantitative parameters. |
| `case-pers-en-119` | en | train | `persona_policy` | Trading persona specification: asymmetric ... | operational_note / strategy_hypothesis | Specifies trading agent persona, behavioral boundaries, and subjective investment posture; does not state concrete testable quantitative parameters. |
| `case-pers-en-120` | en | train | `persona_policy` | Persona behavior rule: contrarian value pe... | operational_note / strategy_hypothesis | Specifies trading agent persona, behavioral boundaries, and subjective investment posture; does not state concrete testable quantitative parameters. |
| `case-pers-en-121` | en | validation | `persona_policy` | Agent decision style: strictly objective e... | operational_note / strategy_hypothesis | Specifies trading agent persona, behavioral boundaries, and subjective investment posture; does not state concrete testable quantitative parameters. |
| `case-pers-en-122` | en | validation | `persona_policy` | Persona profile directive: delta-neutral o... | operational_note / strategy_hypothesis | Specifies trading agent persona, behavioral boundaries, and subjective investment posture; does not state concrete testable quantitative parameters. |
| `case-pers-en-123` | en | holdout | `persona_policy` | Trading persona mandate: long-horizon stru... | operational_note / strategy_hypothesis | Specifies trading agent persona, behavioral boundaries, and subjective investment posture; does not state concrete testable quantitative parameters. |
| `case-pref-en-124` | en | train | `preference_example` | Preference example: few-shot demonstration... | operational_note / few_shot_prompt | Demonstrates exemplary few-shot execution or trading workflow sample; intended as in-context reference rather than active production execution directive. |
| `case-pref-en-125` | en | train | `preference_example` | Few-shot example: demonstrate standard exe... | operational_note / few_shot_prompt | Demonstrates exemplary few-shot execution or trading workflow sample; intended as in-context reference rather than active production execution directive. |
| `case-pref-en-126` | en | train | `preference_example` | Preference example showcase: example demon... | operational_note / few_shot_prompt | Demonstrates exemplary few-shot execution or trading workflow sample; intended as in-context reference rather than active production execution directive. |
| `case-pref-en-127` | en | train | `preference_example` | Exemplary demonstration: reference templat... | operational_note / few_shot_prompt | Demonstrates exemplary few-shot execution or trading workflow sample; intended as in-context reference rather than active production execution directive. |
| `case-pref-en-128` | en | validation | `preference_example` | Few-shot prompt demonstration: sample log ... | operational_note / few_shot_prompt | Demonstrates exemplary few-shot execution or trading workflow sample; intended as in-context reference rather than active production execution directive. |
| `case-pref-en-129` | en | holdout | `preference_example` | Preference guideline example: standard cas... | operational_note / few_shot_prompt | Demonstrates exemplary few-shot execution or trading workflow sample; intended as in-context reference rather than active production execution directive. |
| `case-pref-en-130` | en | holdout | `preference_example` | Demonstration sample: few-shot prompt show... | operational_note / few_shot_prompt | Demonstrates exemplary few-shot execution or trading workflow sample; intended as in-context reference rather than active production execution directive. |

---

## Downstream Acceptance Thresholds

For parent task `SIMPLIFY-EXTRACTION-001` model candidates and tuning evaluations, the downstream acceptance thresholds are:

| Metric | Threshold | Baseline Target | Purpose |
|---|---|---|---|
| **Intent Macro-F1** | $\ge 0.95$ | Within $0.01$ of baseline | Precision & recall across all 9 intent classes |
| **Field F1** | $\ge 0.95$ | No worse than baseline | Accurate extraction of hypothesis, asset class, data |
| **Abstention Recall** | $\ge 0.95$ | $\ge 0.95$ | Faithful abstention on non-strategy or low-confidence text |
| **Critical Support** | $100.0\%$ | $100.0\%$ | Zero hallucinated critical fields without exact source spans |
| **Source Validity** | $100.0\%$ | $100.0\%$ | All cited source span offsets must match exact slice of input |
| **Tenant/Source Breach** | $0$ | $0$ | Zero admission breaches or cross-tenant leaks |
| **p95 Latency** | $\le 10.0$ s | $\le 10.0$ s | Bounded response time under parent token bucket |
| **Total Deadline** | $15.0$ s | $15.0$ s | Hard client timeout deadline |
| **Max Retries** | $\le 1$ | $\le 1$ | Bounded retry policy on transient transport errors |

---

## Evaluation Manifest Schema

The manifest schema is defined in `semantic_extraction_manifest.schema.json`. Every evaluation run produces a JSON manifest containing:
1. **Corpus Constraints & Metadata**: `corpus_total_cases` (>=200), `corpus_split_counts` (train, validation, holdout), `corpus_language_counts` (zh-TW, en), `corpus_sha256`, `run_id`, `evaluated_at`, `model_identity`, `prompt_identity`, `schema_id`, `config_digest`.
2. **Evaluated Subset Counts**: `evaluated_split` (`all`, `train`, `validation`, `holdout`), `total_cases`, `language_counts`, `split_counts`, `task_type_counts`, `failure_counts`.
3. **Aggregate Metrics**: `intent_macro_f1`, `field_f1`, `abstention_recall`, `critical_support_pct`, `source_validity_pct`, `tenant_source_breaches`, `p50_latency_ms`, `p95_latency_ms`, `mean_cost_usd`.
4. **Per-Case Audit Results**: Array of individual evaluation outcomes containing:
   - `case_id`, `split`, `language`, `task_type`, `status`, `is_abstained`, `abstention_reason`, `passed`
   - `extracted_intent`, `expected_intent`
   - `extracted_fields`, `source_spans`, `missing_fields`
   - `critical_support_valid`, `source_spans_count`
   - `latency_ms`, `cost_usd`, `error`

---

## Execution Instructions

Run the evaluation runner using the clean virtual environment:

```bash
# Run full evaluation (all 210 cases against deterministic baseline)
/tmp/clean-bff-venv/bin/python services/source_ingestion/evaluation/run_semantic_extraction_eval.py

# Run only holdout split
/tmp/clean-bff-venv/bin/python services/source_ingestion/evaluation/run_semantic_extraction_eval.py --split holdout

# Custom output destination
/tmp/clean-bff-venv/bin/python services/source_ingestion/evaluation/run_semantic_extraction_eval.py \
  --cases-path services/source_ingestion/evaluation/semantic_extraction_cases.jsonl \
  --manifest-out /tmp/eval_manifest_custom.json
```

### Running Unit & Contract Tests
```bash
/tmp/clean-bff-venv/bin/python -m pytest -v \
  services/source_ingestion/tests/test_semantic_extraction.py \
  services/source_ingestion/tests/test_semantic_extraction_admission.py
```
