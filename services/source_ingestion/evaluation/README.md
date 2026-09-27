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
  - `strategy_proposal`
  - `market_analysis`
  - `code_explanation`
  - `hypothesis_test`
  - `trade_reflection`
  - `system_query`
  - `data_request`
  - `script_debug`
  - `non_strategy`
- **`strategy_seed`**: Extraction of strategy specification seeds (hypothesis, asset classes, market scopes, required data, confidence, seed kind, status).
- **`trade_lesson`**: Extraction of trade lessons and reflections (scope, proposed changes, confidence).
- **`comprehensive`**: Combined extraction of intent, strategy seed, and trade lesson in a single request.

### 4. Admission, Boundary, and Negative Cases
The dataset includes comprehensive coverage of negative, refusal, and admission boundary conditions:
- **Tenant Isolation**: Missing tenant ID, whitespace-only tenant, invalid character tenant.
- **Source Status**: Rejected/blacklisted source records (`REJECTED`, `EXPIRED`, `UNVERIFIED`).
- **License Scope**: Prohibited license scopes (`INTERNAL_ONLY`, `PROPRIETARY_UNLICENSED`).
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
- No synthetic human labels were invented without verifiable provenance.
- Ambiguous cases have been audited and explicitly tagged with `abstention_expected: true` where appropriate.

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
1. **Metadata**: `run_id`, `created_at`, `git_commit`, `dataset_path`, `dataset_sha256`, `model_identity`, `prompt_identity`, `schema_id`.
2. **Case Counts**: `total_cases`, `language_counts` (`zh-TW`, `en`), `split_counts` (`train`, `validation`, `holdout`).
3. **Aggregate Metrics**: `intent_macro_f1`, `field_f1`, `abstention_recall`, `critical_support_pct`, `source_validity_pct`, `tenant_source_breaches`, `p50_latency_ms`, `p95_latency_ms`, `mean_cost_usd`.
4. **Per-Case Audit Results**: Array of individual evaluation outcomes containing:
   - `case_id`, `split`, `language`, `task_type`
   - `admitted`, `denial_reason`
   - `expected_intent`, `predicted_intent`, `intent_matched`
   - `expected_abstained`, `predicted_abstained`, `abstention_matched`
   - `critical_support_valid`, `source_validity_valid`
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
