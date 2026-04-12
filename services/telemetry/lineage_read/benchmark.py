#!/usr/bin/env python3
"""
LIN-002 Benchmark Runner

Plugs the LineageReadService into the LIN-001A benchmark corpus to validate
that the iterative graph traversal meets the p95 SLA budgets.

Usage:
    python3 services/telemetry/lineage_read/benchmark.py --enforce-budgets
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from time import perf_counter_ns

# Add project root to path so we can import the service
_project_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from services.telemetry.lineage_read.service import (
    LineageReadService,
    ProjectionBuilder,
    CorpusLoader,
    LineageTraverser,
    LineageGraph,
)

DEFAULT_CORPUS = Path(__file__).parent.parent.parent / "registry" / "lineage" / "lin001a_benchmark_corpus.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def nearest_rank_percentile(samples: list[float], percentile: int) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    from math import ceil
    rank = max(1, ceil((percentile / 100.0) * len(ordered)))
    return ordered[rank - 1]


def summarize_samples(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {"count": 0, "min_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    ordered = sorted(samples)
    return {
        "count": len(ordered),
        "min_ms": round(ordered[0], 6),
        "p50_ms": round(nearest_rank_percentile(ordered, 50), 6),
        "p95_ms": round(nearest_rank_percentile(ordered, 95), 6),
        "max_ms": round(ordered[-1], 6),
    }


def flatten_result_ids(result: dict) -> set[str]:
    """Extract all IDs from a projection result for validation."""
    ids: set[str] = set()
    for item in result.get("upstream_chain", []):
        ids.add(item["id"])
    for item in result.get("downstream_chain", []):
        ids.add(item["id"])
    for item in result.get("conflict_markers", []):
        if "id" in item:
            ids.add(item["id"])
        # Also check for rollback_parent references in chain items
        if item.get("rollback_parent"):
            ids.add(item["rollback_parent"])
        if item.get("rollback_action_type"):
            ids.add(item["rollback_action_type"])
    # Also check chain items for rollback metadata
    for item in result.get("upstream_chain", []):
        if item.get("rollback_parent"):
            ids.add(item["rollback_parent"])
        if item.get("rollback_action_type"):
            ids.add(item["rollback_action_type"])
    for item in result.get("downstream_chain", []):
        if item.get("rollback_parent"):
            ids.add(item["rollback_parent"])
        if item.get("rollback_action_type"):
            ids.add(item["rollback_action_type"])
    return ids


def validate_case(case: dict, result: dict) -> None:
    """Validate that a projection result contains all expected IDs."""
    observed = flatten_result_ids(result)
    missing_ids = [item for item in case.get("expected_ids", []) if item not in observed]
    missing_markers = [
        item for item in case.get("expected_marker_ids", []) if item not in observed
    ]
    if missing_ids or missing_markers:
        raise ValueError(
            f"Case {case['case_id']} failed validation. "
            f"Missing ids: {missing_ids}. Missing markers: {missing_markers}. "
            f"Observed ids: {sorted(observed)}"
        )


def run_query(service: LineageReadService, case: dict) -> dict:
    """Run a single query case against the service."""
    query_family = case["query_family"]
    params = case["params"]
    return service.query(query_family, **params)


def benchmark_case(
    service: LineageReadService,
    case: dict,
    iterations: int,
    warmup: int,
) -> tuple[dict, dict, list[float]]:
    """Benchmark a single query case with warmup and measurement iterations."""
    query_family = case["query_family"]
    params = case["params"]

    # First run for validation
    first_result = run_query(service, case)
    validate_case(case, first_result)

    # Warmup
    for _ in range(warmup):
        run_query(service, case)

    # Measurement
    samples_ms: list[float] = []
    for _ in range(iterations):
        started = perf_counter_ns()
        run_query(service, case)
        elapsed_ms = (perf_counter_ns() - started) / 1_000_000.0
        samples_ms.append(elapsed_ms)

    return first_result, summarize_samples(samples_ms), samples_ms


def main() -> int:
    parser = argparse.ArgumentParser(description="Run LIN-002 lineage read service benchmark.")
    parser.add_argument(
        "--corpus",
        default=str(DEFAULT_CORPUS),
        help="Path to the LIN-001A benchmark corpus JSON file.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Measurement iterations per case. Defaults to benchmark_config.measurement_iterations.",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=None,
        help="Warmup iterations per case. Defaults to benchmark_config.warmup_iterations.",
    )
    parser.add_argument(
        "--enforce-budgets",
        action="store_true",
        help="Exit non-zero if any query family exceeds its configured p95 target.",
    )
    args = parser.parse_args()

    corpus_path = Path(args.corpus).resolve()
    corpus = load_json(corpus_path)
    config = corpus.get("benchmark_config", {})
    iterations = args.iterations or int(config.get("measurement_iterations", 50))
    warmup = args.warmup or int(config.get("warmup_iterations", 5))

    # Initialize the LIN-002 service and load the corpus
    service = LineageReadService()
    service.load_corpus(corpus)

    family_samples: dict[str, list[float]] = defaultdict(list)
    case_reports: list[dict] = []
    budget_failures: list[str] = []

    for case in corpus.get("benchmark_cases", []):
        first_result, sample_summary, samples_ms = benchmark_case(
            service, case, iterations=iterations, warmup=warmup,
        )
        family_samples[case["query_family"]].extend(samples_ms)
        case_reports.append({
            "case_id": case["case_id"],
            "query_family": case["query_family"],
            "params": case["params"],
            "summary": sample_summary,
            "validated_ids": sorted(flatten_result_ids(first_result)),
        })

    # Aggregate by query family
    family_reports: list[dict] = []
    query_families = corpus.get("query_families", [])
    for family_config in query_families:
        family_name = family_config["query_family"]
        summary = summarize_samples(family_samples.get(family_name, []))
        within_budget = summary["p95_ms"] <= float(family_config["p95_target_ms"])
        family_reports.append({
            "query_family": family_name,
            "sla_bucket": family_config["sla_bucket"],
            "p95_target_ms": family_config["p95_target_ms"],
            "summary": summary,
            "within_budget": within_budget,
            "required_edges": family_config.get("required_edges", []),
        })
        if not within_budget:
            budget_failures.append(family_name)

    report = {
        "task_id": "LIN-002",
        "corpus": str(corpus_path),
        "warmup_iterations": warmup,
        "measurement_iterations": iterations,
        "percentile_method": config.get("percentile_method", "nearest_rank"),
        "implementation": "iterative BFS graph traversal (no recursive joins)",
        "limitations": [
            "in-memory graph traversal only",
            "measures service query contract, not database/network latency",
            "production storage adapter (Postgres partition) not yet implemented",
        ],
        "family_reports": family_reports,
        "case_reports": case_reports,
    }
    print(json.dumps(report, indent=2, sort_keys=False))

    if args.enforce_budgets and budget_failures:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
