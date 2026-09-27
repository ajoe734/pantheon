#!/usr/bin/env python3
"""Auditable evaluation runner for typed semantic extraction.

SIMPLIFY-EXTRACTION-CONTRACT-FOUNDATION-001:
Runs the deterministic baseline extractor against frozen evaluation inputs,
computes per-case metrics, macro-F1, field-F1, abstention recall, critical support,
and zero-breach checks, and produces an auditable manifest conforming to
services/source_ingestion/evaluation/semantic_extraction_manifest.schema.json.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import jsonschema

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.source_ingestion.semantic_extraction import (
    DeterministicBaselineExtractor,
    ExtractionTaskType,
    SemanticExtractionRequest,
    SemanticExtractionResult,
    _utc_now,
)
from services.source_ingestion.semantic_extraction_client import (
    SemanticExtractionClient,
)

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_CASES_PATH = EVAL_DIR / "semantic_extraction_cases.jsonl"
DEFAULT_SCHEMA_PATH = EVAL_DIR / "semantic_extraction_manifest.schema.json"
DEFAULT_MANIFEST_OUT = EVAL_DIR / "evaluation_manifest.json"


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _calculate_f1(tp: int, fp: int, fn: int) -> Tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def run_evaluation(
    cases_path: Path = DEFAULT_CASES_PATH,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
    manifest_out: Optional[Path] = DEFAULT_MANIFEST_OUT,
    split_filter: Optional[str] = None,
    client: Optional[SemanticExtractionClient] = None,
    task_id: str = "SIMPLIFY-EXTRACTION-CONTRACT-FOUNDATION-001",
) -> dict[str, Any]:
    """Run full evaluation and emit verified manifest."""
    if not cases_path.exists():
        raise FileNotFoundError(f"Cases file not found: {cases_path}")

    cases: list[dict[str, Any]] = []
    with cases_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))

    if split_filter and split_filter != "all":
        cases = [c for c in cases if c.get("split") == split_filter]

    corpus_sha256 = _compute_sha256(cases_path)
    run_id = f"eval-run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    # Counts
    split_counts: dict[str, int] = defaultdict(int)
    lang_counts: dict[str, int] = defaultdict(int)
    task_type_counts: dict[str, int] = defaultdict(int)
    provenance_counts: dict[str, int] = defaultdict(int)

    # Intent classification counters: per class tp, fp, fn
    all_classes: set[str] = set()
    intent_tp: dict[str, int] = defaultdict(int)
    intent_fp: dict[str, int] = defaultdict(int)
    intent_fn: dict[str, int] = defaultdict(int)

    # Field extraction counters (seed & lesson)
    field_tp = 0
    field_fp = 0
    field_fn = 0

    # Abstention counters
    abstention_expected_count = 0
    abstention_correct_count = 0

    # Critical support & validity counters
    total_spans = 0
    valid_spans = 0
    critical_support_passed = 0
    critical_support_total = 0

    # Breaches
    tenant_source_breaches = 0

    latencies_ms: list[float] = []
    costs_usd: list[float] = []
    case_results: list[dict[str, Any]] = []

    for c in cases:
        case_id = c["case_id"]
        split = c.get("split", "train")
        lang = c.get("language", "en")
        task_type = c.get("task_type", "comprehensive")
        inp = c["input"]
        expected = c["expected"]
        prov = c.get("provenance", {})

        split_counts[split] += 1
        lang_counts[lang] += 1
        task_type_counts[task_type] += 1
        prov_source = prov.get("label_source", "unknown")
        provenance_counts[prov_source] += 1

        req = SemanticExtractionRequest(
            source_id=inp["source_id"],
            text=inp["text"],
            task_type=task_type,
            tenant_id=inp.get("tenant_id", "default_tenant"),
            source_type=inp.get("source_type", "internal_note"),
            source_status=inp.get("source_status", "raw"),
            license_scope=inp.get("license_scope", "internal"),
            event_time=inp.get("event_time"),
            as_of=inp.get("as_of"),
        )

        t0 = time.monotonic()
        if client is not None:
            res = client.extract(req)
        else:
            res = DeterministicBaselineExtractor.extract(req)
        dur_ms = (time.monotonic() - t0) * 1000.0

        latencies_ms.append(dur_ms)
        costs_usd.append(res.cost_usd)

        # 1. Breach check: Did an unadmitted request produce data?
        should_admit = expected.get("should_admit", True)
        if not should_admit and res.status == "completed":
            tenant_source_breaches += 1

        # 2. Abstention check
        expected_abstain = expected.get("is_abstained", False)
        if expected_abstain:
            abstention_expected_count += 1
            if res.is_abstained:
                abstention_correct_count += 1

        # 3. Intent evaluation
        exp_intent = expected.get("expected_intent")
        actual_intent = res.intent.primary_intent if res.intent else None
        if exp_intent:
            all_classes.add(exp_intent)
            if actual_intent:
                all_classes.add(actual_intent)

            if not res.is_abstained and actual_intent == exp_intent:
                intent_tp[exp_intent] += 1
            else:
                intent_fn[exp_intent] += 1
                if actual_intent and actual_intent != exp_intent:
                    intent_fp[actual_intent] += 1

        # 4. Field evaluation (seed & lesson)
        exp_seed = expected.get("expected_seed")
        if exp_seed and res.strategy_seed and not res.is_abstained:
            if "asset_class" in exp_seed:
                exp_assets = set(exp_seed["asset_class"])
                act_assets = set(res.strategy_seed.asset_class)
                field_tp += len(exp_assets.intersection(act_assets))
                field_fp += len(act_assets - exp_assets)
                field_fn += len(exp_assets - act_assets)

            if "market_scope" in exp_seed:
                exp_mkts = set(exp_seed["market_scope"])
                act_mkts = set(res.strategy_seed.market_scope)
                field_tp += len(exp_mkts.intersection(act_mkts))
                field_fp += len(act_mkts - exp_mkts)
                field_fn += len(exp_mkts - act_mkts)

            if "required_data" in exp_seed:
                exp_data = set(exp_seed["required_data"])
                act_data = set(res.strategy_seed.required_data)
                field_tp += len(exp_data.intersection(act_data))
                field_fp += len(act_data - exp_data)
                field_fn += len(exp_data - act_data)

        # 5. Span validity and critical support
        case_spans_valid = True
        for span in res.source_spans:
            total_spans += 1
            if span.is_valid(inp["text"]):
                valid_spans += 1
            else:
                case_spans_valid = False

        if not res.is_abstained and res.status == "completed":
            critical_support_total += 1
            if len(res.source_spans) > 0 and case_spans_valid:
                critical_support_passed += 1

        # Per-case record
        case_passed = (
            (should_admit == (not res.is_abstained if not expected_abstain else True))
            and (exp_intent is None or actual_intent == exp_intent or (expected_abstain and res.is_abstained))
            and case_spans_valid
        )

        case_results.append({
            "case_id": case_id,
            "split": split,
            "language": lang,
            "task_type": task_type,
            "status": res.status,
            "is_abstained": res.is_abstained,
            "abstention_reason": res.abstention_reason,
            "passed": case_passed,
            "extracted_intent": actual_intent,
            "expected_intent": exp_intent,
            "source_spans_count": len(res.source_spans),
            "critical_support_valid": case_spans_valid,
            "latency_ms": round(dur_ms, 2),
            "cost_usd": res.cost_usd,
            "error": res.failure_message,
        })

    # Aggregate metric calculations
    # Intent macro-F1
    f1_list: list[float] = []
    for cls_name in all_classes:
        tp = intent_tp[cls_name]
        fp = intent_fp[cls_name]
        fn = intent_fn[cls_name]
        _, _, cls_f1 = _calculate_f1(tp, fp, fn)
        f1_list.append(cls_f1)
    intent_macro_f1 = sum(f1_list) / len(f1_list) if f1_list else 1.0

    # Field F1
    _, _, field_f1 = _calculate_f1(field_tp, field_fp, field_fn)

    # Abstention recall
    abstention_recall = (
        abstention_correct_count / abstention_expected_count
        if abstention_expected_count > 0
        else 1.0
    )

    # Critical support & validity
    critical_support_pct = (
        (critical_support_passed / critical_support_total * 100.0)
        if critical_support_total > 0
        else 100.0
    )
    source_validity_pct = (
        (valid_spans / total_spans * 100.0)
        if total_spans > 0
        else 100.0
    )

    latencies_sorted = sorted(latencies_ms)
    p50_latency = latencies_sorted[len(latencies_sorted) // 2] if latencies_sorted else 0.0
    p95_idx = int(len(latencies_sorted) * 0.95)
    p95_latency = latencies_sorted[min(p95_idx, len(latencies_sorted) - 1)] if latencies_sorted else 0.0
    mean_cost = sum(costs_usd) / len(costs_usd) if costs_usd else 0.0

    manifest: dict[str, Any] = {
        "manifest_version": "semantic_extraction_manifest.v1",
        "run_id": run_id,
        "evaluated_at": _utc_now(),
        "task_id": task_id,
        "corpus_id": "semantic_extraction_cases.v1",
        "corpus_sha256": corpus_sha256,
        "total_cases": len(cases),
        "split_counts": dict(split_counts),
        "language_counts": dict(lang_counts),
        "task_type_counts": dict(task_type_counts),
        "baseline_metrics": {
            "intent_macro_f1": round(intent_macro_f1, 4),
            "field_f1": round(field_f1, 4),
            "abstention_recall": round(abstention_recall, 4),
            "critical_support_pct": round(critical_support_pct, 2),
            "source_validity_pct": round(source_validity_pct, 2),
            "tenant_source_breaches": tenant_source_breaches,
            "p50_latency_ms": round(p50_latency, 2),
            "p95_latency_ms": round(p95_latency, 2),
            "mean_cost_usd": round(mean_cost, 6),
        },
        "downstream_thresholds": {
            "macro_f1_min": 0.95,
            "field_f1_min": 0.95,
            "abstention_recall_min": 0.95,
            "critical_support_pct_min": 100.0,
            "source_validity_pct_min": 100.0,
            "zero_breaches": 0,
            "target_p95_seconds": 10.0,
            "max_deadline_seconds": 15.0,
        },
        "provenance_counts": dict(provenance_counts),
        "case_results": case_results,
    }

    # Validate against schema
    if schema_path.exists():
        schema_dict = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = jsonschema.Draft7Validator(schema_dict)
        errors = list(validator.iter_errors(manifest))
        if errors:
            err_msg = "\n".join(f"{list(e.path)}: {e.message}" for e in errors)
            raise ValueError(f"Manifest schema validation failed:\n{err_msg}")

    # Write manifest out
    if manifest_out:
        manifest_out.parent.mkdir(parents=True, exist_ok=True)
        with manifest_out.open("w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    return manifest


def main():
    parser = argparse.ArgumentParser(description="Run semantic extraction evaluation.")
    parser.add_argument("--cases-path", type=Path, default=DEFAULT_CASES_PATH, help="Path to cases.jsonl")
    parser.add_argument("--schema-path", type=Path, default=DEFAULT_SCHEMA_PATH, help="Path to manifest schema")
    parser.add_argument("--manifest-out", type=Path, default=DEFAULT_MANIFEST_OUT, help="Path to output manifest JSON")
    parser.add_argument("--split", type=str, default="all", choices=["all", "train", "validation", "holdout"], help="Split filter")
    args = parser.parse_args()

    print(f"Running semantic extraction evaluation on {args.cases_path}...")
    manifest = run_evaluation(
        cases_path=args.cases_path,
        schema_path=args.schema_path,
        manifest_out=args.manifest_out,
        split_filter=args.split,
    )

    metrics = manifest["baseline_metrics"]
    print("=" * 60)
    print("SEMANTIC EXTRACTION BASELINE EVALUATION REPORT")
    print("=" * 60)
    print(f"Run ID:                {manifest['run_id']}")
    print(f"Total Cases:           {manifest['total_cases']}")
    print(f"Language Counts:       {manifest['language_counts']}")
    print(f"Split Counts:          {manifest['split_counts']}")
    print("-" * 60)
    print(f"Intent Macro-F1:       {metrics['intent_macro_f1']:.4f} (Threshold >= 0.95)")
    print(f"Field F1:              {metrics['field_f1']:.4f} (Threshold >= 0.95)")
    print(f"Abstention Recall:     {metrics['abstention_recall']:.4f} (Threshold >= 0.95)")
    print(f"Critical Support:      {metrics['critical_support_pct']:.2f}% (Threshold == 100.0%)")
    print(f"Source Validity:       {metrics['source_validity_pct']:.2f}% (Threshold == 100.0%)")
    print(f"Tenant/Source Breach:  {metrics['tenant_source_breaches']} (Threshold == 0)")
    print(f"p50 Latency:           {metrics['p50_latency_ms']:.2f} ms")
    print(f"p95 Latency:           {metrics['p95_latency_ms']:.2f} ms")
    print(f"Mean Cost:             ${metrics['mean_cost_usd']:.6f}")
    print("=" * 60)
    print(f"Manifest written to: {args.manifest_out}")


if __name__ == "__main__":
    main()
