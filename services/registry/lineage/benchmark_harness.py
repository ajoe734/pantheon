#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from time import perf_counter_ns
from typing import Any


DEFAULT_CORPUS = Path(__file__).with_name("lin001a_benchmark_corpus.json")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def nearest_rank_percentile(samples: list[float], percentile: int) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
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


class LineageBenchmarkHarness:
    def __init__(self, corpus: dict[str, Any]) -> None:
        self.corpus = corpus
        self.metadata = corpus.get("metadata", {})
        self.query_families = {
            family["query_family"]: family for family in corpus.get("query_families", [])
        }
        node_sets = corpus.get("node_sets", {})
        self.capital_pools = {record["pool_id"]: record for record in node_sets.get("capital_pools", [])}
        self.persona_bindings = {
            record["binding_id"]: record
            for record in node_sets.get("persona_capital_bindings", [])
        }
        self.deployment_plans = {
            record["plan_id"]: record for record in node_sets.get("deployment_plans", [])
        }
        self.runtime_bindings = {
            record["binding_id"]: record for record in node_sets.get("runtime_bindings", [])
        }
        self.telemetry_events = {
            record["event_id"]: record for record in node_sets.get("telemetry_events", [])
        }

        self.bindings_by_pool = self._group(self.runtime_bindings.values(), "capital_pool_id")
        self.bindings_by_plan = self._group(self.runtime_bindings.values(), "plan_id")
        self.persona_bindings_by_pool = self._group(
            self.persona_bindings.values(), "capital_pool_id"
        )
        self.plans_by_pool = self._group(self.deployment_plans.values(), "capital_pool_id")
        self.events_by_binding = self._group(
            self.telemetry_events.values(), self._event_binding_id
        )
        self.events_by_plan = self._group(self.telemetry_events.values(), "plan_id")
        self.events_by_pool = self._group(self.telemetry_events.values(), "capital_pool_id")

    def _group(self, records: Any, key: Any) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            if callable(key):
                bucket = key(record)
            else:
                bucket = record.get(key)
            if bucket:
                grouped[str(bucket)].append(record)
        return dict(grouped)

    def _chain_item(self, item_type: str, item_id: str, **extra: Any) -> dict[str, Any]:
        payload = {"type": item_type, "id": item_id}
        payload.update(extra)
        return payload

    def _artifact_ref(self, artifact_id: str, artifact_version: str) -> str:
        return f"{artifact_id}@{artifact_version}"

    def _event_binding_id(self, event: dict[str, Any]) -> str | None:
        return event.get("runtime_binding_id") or event.get("binding_id")

    def _plan_persona_binding_id(self, plan: dict[str, Any]) -> str | None:
        return plan.get("persona_capital_binding_id") or plan.get("binding_id")

    def _event_stage(self, event: dict[str, Any]) -> str | None:
        return event.get("deployment_stage") or event.get("environment")

    def _sorted_by_field(
        self, records: list[dict[str, Any]], field: str, fallback_field: str
    ) -> list[dict[str, Any]]:
        return sorted(
            records,
            key=lambda record: (record.get(field, ""), record.get(fallback_field, "")),
        )

    def _runtime_refs(self, bindings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        refs: list[dict[str, Any]] = []
        for binding in bindings:
            runtime_id = binding.get("runtime_id")
            if runtime_id and runtime_id not in seen:
                seen.add(runtime_id)
                refs.append(self._chain_item("runtime_ref", runtime_id))
        return refs

    def _event_items(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            self._chain_item("telemetry_event", event["event_id"], event_type=event["event_type"])
            for event in self._sorted_by_field(events, "event_produced_at", "event_id")
        ]

    def query_runtime_binding_projection(self, binding_id: str) -> dict[str, Any]:
        binding = self.runtime_bindings[binding_id]
        plan = self.deployment_plans.get(binding["plan_id"])
        persona_binding = self.persona_bindings.get(binding["persona_capital_binding_id"])
        capital_pool = self.capital_pools.get(binding["capital_pool_id"])
        events = self.events_by_binding.get(binding_id, [])

        upstream_chain: list[dict[str, Any]] = []
        if capital_pool:
            upstream_chain.append(self._chain_item("capital_pool", capital_pool["pool_id"]))
        if persona_binding:
            upstream_chain.append(
                self._chain_item("persona_capital_binding", persona_binding["binding_id"])
            )
        if plan:
            upstream_chain.append(self._chain_item("deployment_plan", plan["plan_id"]))
        upstream_chain.append(
            self._chain_item(
                "artifact_ref",
                self._artifact_ref(binding["artifact_id"], binding["artifact_version"]),
            )
        )

        downstream_chain = [self._chain_item("runtime_ref", binding["runtime_id"])]
        downstream_chain.extend(self._event_items(events))

        conflict_markers: list[dict[str, Any]] = []
        if binding.get("rollback_parent"):
            conflict_markers.append(
                self._chain_item("rollback_parent", binding["rollback_parent"])
            )
        if binding.get("rollback_action_type"):
            conflict_markers.append(
                self._chain_item("rollback_action_type", binding["rollback_action_type"])
            )
        mismatched_events = [
            event["event_id"]
            for event in events
            if self._event_stage(event) and self._event_stage(event) != binding["deployment_mode"]
        ]
        if mismatched_events:
            conflict_markers.append(
                self._chain_item("deployment_stage_mismatch", ",".join(sorted(mismatched_events)))
            )

        return {
            "target_type": "runtime_binding",
            "target_id": binding_id,
            "projection_updated_at": self.metadata.get("projection_updated_at"),
            "upstream_chain": upstream_chain,
            "downstream_chain": downstream_chain,
            "conflict_markers": conflict_markers,
            "telemetry_event_count": len(events),
            "binding_status": binding["status"],
        }

    def query_capital_pool_projection(self, pool_id: str) -> dict[str, Any]:
        capital_pool = self.capital_pools[pool_id]
        persona_bindings = self._sorted_by_field(
            self.persona_bindings_by_pool.get(pool_id, []), "binding_id", "persona_id"
        )
        plans = self._sorted_by_field(self.plans_by_pool.get(pool_id, []), "created_at", "plan_id")
        bindings = self._sorted_by_field(
            self.bindings_by_pool.get(pool_id, []), "effective_at", "binding_id"
        )
        events = self.events_by_pool.get(pool_id, [])

        downstream_chain: list[dict[str, Any]] = [
            self._chain_item("persona_capital_binding", record["binding_id"])
            for record in persona_bindings
        ]
        downstream_chain.extend(
            self._chain_item("deployment_plan", record["plan_id"]) for record in plans
        )
        downstream_chain.extend(
            self._chain_item("runtime_binding", record["binding_id"]) for record in bindings
        )
        downstream_chain.extend(self._runtime_refs(bindings))
        downstream_chain.extend(self._event_items(events))

        active_bindings = [record for record in bindings if record["status"] == "active"]
        conflict_markers: list[dict[str, Any]] = []
        if capital_pool.get("single_runtime_enforced", True) and len(active_bindings) > 1:
            conflict_markers.append(
                self._chain_item(
                    "single_runtime_violation",
                    ",".join(record["binding_id"] for record in active_bindings),
                )
            )

        return {
            "target_type": "capital_pool",
            "target_id": pool_id,
            "projection_updated_at": self.metadata.get("projection_updated_at"),
            "upstream_chain": [self._chain_item("capital_pool", pool_id)],
            "downstream_chain": downstream_chain,
            "conflict_markers": conflict_markers,
            "telemetry_event_count": len(events),
            "active_runtime_binding_count": len(active_bindings),
        }

    def query_telemetry_event_trace(self, event_id: str) -> dict[str, Any]:
        event = self.telemetry_events[event_id]
        binding_id = self._event_binding_id(event)
        binding = self.runtime_bindings.get(binding_id or "")
        plan = self.deployment_plans.get(event["plan_id"])
        capital_pool = self.capital_pools.get(event["capital_pool_id"])
        persona_binding = self.persona_bindings.get(event["persona_capital_binding_id"])

        upstream_chain: list[dict[str, Any]] = []
        if capital_pool:
            upstream_chain.append(self._chain_item("capital_pool", capital_pool["pool_id"]))
        if persona_binding:
            upstream_chain.append(
                self._chain_item("persona_capital_binding", persona_binding["binding_id"])
            )
        if plan:
            upstream_chain.append(self._chain_item("deployment_plan", plan["plan_id"]))
        if binding:
            upstream_chain.append(self._chain_item("runtime_binding", binding["binding_id"]))
        upstream_chain.append(
            self._chain_item(
                "artifact_ref",
                self._artifact_ref(event["artifact_id"], event["artifact_version"]),
            )
        )

        downstream_chain: list[dict[str, Any]] = []
        if event.get("runtime_id"):
            downstream_chain.append(self._chain_item("runtime_ref", event["runtime_id"]))

        conflict_markers: list[dict[str, Any]] = []
        if binding and self._event_stage(event) != binding["deployment_mode"]:
            conflict_markers.append(
                self._chain_item("deployment_stage_mismatch", event["event_id"])
            )
        if event.get("rollback_parent"):
            conflict_markers.append(
                self._chain_item("rollback_parent", event["rollback_parent"])
            )
        if event.get("rollback_action_type"):
            conflict_markers.append(
                self._chain_item("rollback_action_type", event["rollback_action_type"])
            )

        return {
            "target_type": "telemetry_event",
            "target_id": event_id,
            "projection_updated_at": self.metadata.get("projection_updated_at"),
            "upstream_chain": upstream_chain,
            "downstream_chain": downstream_chain,
            "conflict_markers": conflict_markers,
            "event_type": event["event_type"],
        }

    def query_forensic_plan_trace(self, plan_id: str) -> dict[str, Any]:
        plan = self.deployment_plans[plan_id]
        persona_binding_id = self._plan_persona_binding_id(plan)
        persona_binding = self.persona_bindings.get(persona_binding_id or "")
        capital_pool = self.capital_pools.get(plan["capital_pool_id"])
        bindings = self._sorted_by_field(self.bindings_by_plan.get(plan_id, []), "effective_at", "binding_id")
        events = self.events_by_plan.get(plan_id, [])

        upstream_chain: list[dict[str, Any]] = []
        if capital_pool:
            upstream_chain.append(self._chain_item("capital_pool", capital_pool["pool_id"]))
        if persona_binding:
            upstream_chain.append(
                self._chain_item("persona_capital_binding", persona_binding["binding_id"])
            )
        upstream_chain.append(self._chain_item("deployment_plan", plan["plan_id"]))
        upstream_chain.append(
            self._chain_item(
                "artifact_ref",
                self._artifact_ref(plan["artifact_id"], plan["artifact_version"]),
            )
        )

        downstream_chain = [
            self._chain_item("runtime_binding", record["binding_id"]) for record in bindings
        ]
        downstream_chain.extend(self._runtime_refs(bindings))
        downstream_chain.extend(self._event_items(events))

        conflict_markers: list[dict[str, Any]] = []
        rollback = plan.get("rollback") or {}
        if rollback.get("action_type"):
            conflict_markers.append(
                self._chain_item("rollback_action_type", rollback["action_type"])
            )
        if rollback.get("target_artifact_id") and rollback.get("target_version"):
            conflict_markers.append(
                self._chain_item(
                    "rollback_target_artifact",
                    self._artifact_ref(
                        rollback["target_artifact_id"], rollback["target_version"]
                    ),
                )
            )
        for binding in bindings:
            if binding.get("rollback_parent"):
                conflict_markers.append(
                    self._chain_item("rollback_parent", binding["rollback_parent"])
                )

        return {
            "target_type": "deployment_plan",
            "target_id": plan_id,
            "projection_updated_at": self.metadata.get("projection_updated_at"),
            "upstream_chain": upstream_chain,
            "downstream_chain": downstream_chain,
            "conflict_markers": conflict_markers,
            "runtime_binding_count": len(bindings),
            "telemetry_event_count": len(events),
        }

    def run_case(self, query_family: str, params: dict[str, Any]) -> dict[str, Any]:
        if query_family == "runtime_binding_projection":
            return self.query_runtime_binding_projection(params["binding_id"])
        if query_family == "capital_pool_projection":
            return self.query_capital_pool_projection(params["pool_id"])
        if query_family == "telemetry_event_trace":
            return self.query_telemetry_event_trace(params["event_id"])
        if query_family == "forensic_plan_trace":
            return self.query_forensic_plan_trace(params["plan_id"])
        raise KeyError(f"Unsupported query family: {query_family}")


def flatten_result_ids(result: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for item in result.get("upstream_chain", []):
        ids.add(item["id"])
    for item in result.get("downstream_chain", []):
        ids.add(item["id"])
    for item in result.get("conflict_markers", []):
        ids.add(item["id"])
    return ids


def validate_case(case: dict[str, Any], result: dict[str, Any]) -> None:
    observed = flatten_result_ids(result)
    missing_ids = [item for item in case.get("expected_ids", []) if item not in observed]
    missing_markers = [
        item for item in case.get("expected_marker_ids", []) if item not in observed
    ]
    if missing_ids or missing_markers:
        raise ValueError(
            f"Case {case['case_id']} failed validation. "
            f"Missing ids: {missing_ids}. Missing markers: {missing_markers}."
        )


def benchmark_case(
    harness: LineageBenchmarkHarness,
    case: dict[str, Any],
    iterations: int,
    warmup: int,
) -> tuple[dict[str, Any], dict[str, Any], list[float]]:
    query_family = case["query_family"]
    params = case["params"]

    first_result = harness.run_case(query_family, params)
    validate_case(case, first_result)

    for _ in range(warmup):
        harness.run_case(query_family, params)

    samples_ms: list[float] = []
    for _ in range(iterations):
        started = perf_counter_ns()
        harness.run_case(query_family, params)
        elapsed_ms = (perf_counter_ns() - started) / 1_000_000.0
        samples_ms.append(elapsed_ms)

    return first_result, summarize_samples(samples_ms), samples_ms


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the LIN-001A lineage benchmark harness.")
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
    harness = LineageBenchmarkHarness(corpus)
    config = corpus.get("benchmark_config", {})
    iterations = args.iterations or int(config.get("measurement_iterations", 50))
    warmup = args.warmup or int(config.get("warmup_iterations", 5))

    family_samples: dict[str, list[float]] = defaultdict(list)
    case_reports: list[dict[str, Any]] = []
    budget_failures: list[str] = []

    for case in corpus.get("benchmark_cases", []):
        first_result, sample_summary, samples_ms = benchmark_case(
            harness, case, iterations=iterations, warmup=warmup
        )
        family_samples[case["query_family"]].extend(samples_ms)
        case_reports.append(
            {
                "case_id": case["case_id"],
                "query_family": case["query_family"],
                "params": case["params"],
                "summary": sample_summary,
                "validated_ids": sorted(flatten_result_ids(first_result)),
            }
        )

    family_reports: list[dict[str, Any]] = []
    for family_name, family_config in harness.query_families.items():
        summary = summarize_samples(family_samples.get(family_name, []))
        within_budget = summary["p95_ms"] <= float(family_config["p95_target_ms"])
        family_reports.append(
            {
                "query_family": family_name,
                "sla_bucket": family_config["sla_bucket"],
                "p95_target_ms": family_config["p95_target_ms"],
                "summary": summary,
                "within_budget": within_budget,
                "required_edges": family_config.get("required_edges", []),
            }
        )
        if not within_budget:
            budget_failures.append(family_name)

    report = {
        "task_id": corpus.get("metadata", {}).get("task_id"),
        "corpus": str(corpus_path),
        "warmup_iterations": warmup,
        "measurement_iterations": iterations,
        "percentile_method": config.get("percentile_method", "nearest_rank"),
        "limitations": [
            "in-memory traversal only",
            "measures query contract repeatability, not database/network latency",
            "intended as the fixed corpus and query-suite baseline for LIN-001 and LIN-002",
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
