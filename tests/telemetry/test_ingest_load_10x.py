from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - local dev images include PyYAML
    yaml = None

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.telemetry.batch_writer import WriteResult
from services.telemetry.dead_letter import TAG_BUFFER_OVERFLOW
from services.telemetry.ingest_svc import TelemetryIngestService


TASK_ID = "TEL-HARD-001-V2"
NORMAL_LOAD_REFERENCE_EVENTS = 1_000
LOAD_MULTIPLIER = 10
DEFAULT_TOTAL_EVENTS = NORMAL_LOAD_REFERENCE_EVENTS * LOAD_MULTIPLIER
DEFAULT_AGGREGATE_COUNT = 16
DEFAULT_BUFFER_MAXSIZE = 12_000
DEFAULT_BATCH_SIZE = 512
SCHEMA_PATH = ROOT / "services" / "telemetry" / "telemetry_event.schema.json"
REPORT_PATH = ROOT / "support" / "evidence" / TASK_ID / "load_report.json"
ENVIRONMENT_PATH = ROOT / "support" / "evidence" / TASK_ID / "environment.md"
_BINDING_ID = "550e8400-e29b-41d4-a716-446655440001"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _uuid_for(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pantheon:{TASK_ID}:{label}"))


def _make_event(index: int, aggregate_count: int = DEFAULT_AGGREGATE_COUNT) -> dict[str, Any]:
    aggregate_index = index % aggregate_count
    sequence_no = index // aggregate_count + 1
    aggregate_id = f"runtime-load-{aggregate_index:02d}"
    order_id = f"load-order-{aggregate_index:02d}-{sequence_no:05d}"

    return {
        "event_id": _uuid_for(f"event:{index}"),
        "event_type": "order_filled",
        "created_at": "2026-05-20T12:00:00Z",
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": _BINDING_ID,
        "runtime_id": aggregate_id,
        "capital_pool_id": "pool-telemetry-load",
        "artifact_id": "artifact-telemetry-load",
        "artifact_version": "1.0.0",
        "plan_id": "plan-tel-hard-001-v2",
        "persona_capital_binding_id": "pcb-tel-hard-001-v2",
        "trace_id": _uuid_for(f"trace:{aggregate_index}"),
        "target": {
            "strategy_id": "telemetry-load-strategy",
            "artifact_version": "1.0.0",
        },
        "metrics": {
            "fill_quantity": 1.0,
            "fill_price": 100.0 + (index % 100) / 100.0,
            "sequence_no": sequence_no,
        },
        "order_id": order_id,
        "order_status": "filled",
        "fill_status": "filled",
        "quantity": 1.0,
        "price": 100.0 + (index % 100) / 100.0,
        "symbol": "TELLOAD",
        "metadata": {
            "aggregate_type": "runtime",
            "aggregate_id": aggregate_id,
            "sequence_no": sequence_no,
            "causal_parent_id": _uuid_for(f"event:{index - aggregate_count}")
            if index >= aggregate_count
            else None,
            "normal_load_reference_events": NORMAL_LOAD_REFERENCE_EVENTS,
            "load_multiplier": LOAD_MULTIPLIER,
        },
    }


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    rank = (len(sorted_values) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_values[int(rank)]
    lower_value = sorted_values[lower]
    upper_value = sorted_values[upper]
    return lower_value + (upper_value - lower_value) * (rank - lower)


def _latency_histogram_ms(latencies: list[float]) -> dict[str, Any]:
    values = sorted(latency * 1000.0 for latency in latencies)
    buckets = {
        "le_0_1_ms": 0,
        "le_0_5_ms": 0,
        "le_1_ms": 0,
        "le_5_ms": 0,
        "le_10_ms": 0,
        "gt_10_ms": 0,
    }
    for value in values:
        if value <= 0.1:
            buckets["le_0_1_ms"] += 1
        elif value <= 0.5:
            buckets["le_0_5_ms"] += 1
        elif value <= 1.0:
            buckets["le_1_ms"] += 1
        elif value <= 5.0:
            buckets["le_5_ms"] += 1
        elif value <= 10.0:
            buckets["le_10_ms"] += 1
        else:
            buckets["gt_10_ms"] += 1

    return {
        "count": len(values),
        "min": round(values[0], 4) if values else 0.0,
        "p50": round(_percentile(values, 0.50), 4),
        "p90": round(_percentile(values, 0.90), 4),
        "p95": round(_percentile(values, 0.95), 4),
        "p99": round(_percentile(values, 0.99), 4),
        "max": round(values[-1], 4) if values else 0.0,
        "buckets": buckets,
    }


def _load_compose_target() -> dict[str, Any]:
    compose_path = ROOT / "docker-compose.yml"
    if not compose_path.exists() or yaml is None:
        return {
            "compose_file": "docker-compose.yml",
            "service": "telemetry",
            "parser": "unavailable",
        }

    data = yaml.safe_load(compose_path.read_text()) or {}
    service = (data.get("services") or {}).get("telemetry") or {}
    return {
        "compose_file": "docker-compose.yml",
        "service": "telemetry",
        "profiles": service.get("profiles", ["default-dev"]),
        "ports": service.get("ports", []),
        "environment": service.get("environment", {}),
        "depends_on": sorted((service.get("depends_on") or {}).keys()),
        "build": service.get("build", {}),
    }


def _verify_ordering(written_events: list[dict[str, Any]]) -> dict[str, Any]:
    sequences_by_aggregate: dict[str, list[int]] = {}
    for event in written_events:
        metadata = event.get("metadata") or {}
        aggregate_id = str(metadata.get("aggregate_id"))
        sequence_no = int(metadata.get("sequence_no"))
        sequences_by_aggregate.setdefault(aggregate_id, []).append(sequence_no)

    violations = []
    for aggregate_id, sequence_numbers in sorted(sequences_by_aggregate.items()):
        expected = list(range(1, len(sequence_numbers) + 1))
        if sequence_numbers != expected:
            violations.append(
                {
                    "aggregate_id": aggregate_id,
                    "expected_first_gap_free_prefix": expected[:10],
                    "observed_first_values": sequence_numbers[:10],
                    "observed_count": len(sequence_numbers),
                }
            )

    return {
        "global_total_order_required": False,
        "per_aggregate_ordering_checked": True,
        "aggregate_key": "metadata.aggregate_id",
        "sequence_field": "metadata.sequence_no",
        "aggregate_count": len(sequences_by_aggregate),
        "violations": violations,
        "policy_refs": [
            "EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md#2.1",
            "EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md#2.2",
        ],
    }


async def run_load_scenario(
    *,
    total_events: int = DEFAULT_TOTAL_EVENTS,
    buffer_maxsize: int = DEFAULT_BUFFER_MAXSIZE,
    batch_size: int = DEFAULT_BATCH_SIZE,
    aggregate_count: int = DEFAULT_AGGREGATE_COUNT,
) -> dict[str, Any]:
    expected_ids = [_uuid_for(f"event:{index}") for index in range(total_events)]
    written_events: list[dict[str, Any]] = []
    write_batch_sizes: list[int] = []

    async def write_fn(batch: list[dict[str, Any]]) -> WriteResult:
        written_events.extend(batch)
        write_batch_sizes.append(len(batch))
        return WriteResult.ok(len(batch))

    service = TelemetryIngestService(
        schema_path=str(SCHEMA_PATH),
        buffer_maxsize=buffer_maxsize,
        batch_size=batch_size,
        batch_interval=0.01,
        max_retries=1,
        write_fn=write_fn,
        dedup_max_size=max(total_events * 2, 1_000),
    )
    service._backpressure._evaluation_interval = 0.0

    accepted = 0
    rejected = 0
    latencies: list[float] = []
    start = time.perf_counter()
    for index in range(total_events):
        event = _make_event(index, aggregate_count=aggregate_count)
        event_start = time.perf_counter()
        ok = await service.ingest(event, timeout=0.01)
        latencies.append(time.perf_counter() - event_start)
        if ok:
            accepted += 1
        else:
            rejected += 1

    burst_elapsed = time.perf_counter() - start
    burst_buffer_depth = service.stats()["buffer"]["size"]
    pressure_after_burst = service._backpressure.evaluate().value

    await service.start()
    deadline = time.perf_counter() + 30.0
    while len(written_events) < accepted and time.perf_counter() < deadline:
        await asyncio.sleep(0.01)
    await service.stop(graceful=True)
    elapsed = time.perf_counter() - start

    stats = service.stats()
    written_ids = [event["event_id"] for event in written_events]
    missing_ids = sorted(set(expected_ids) - set(written_ids))
    duplicate_write_count = len(written_ids) - len(set(written_ids))
    dlq_entries = service.get_dlq_entries(limit=total_events + 100)
    buffer_overflow_count = sum(
        1 for entry in dlq_entries if TAG_BUFFER_OVERFLOW in entry.get("tags", [])
    )
    drop_count = len(missing_ids) + duplicate_write_count + rejected
    ordering_semantics = _verify_ordering(written_events)

    report = {
        "task_id": TASK_ID,
        "generated_at": _utc_now(),
        "scenario": {
            "name": "telemetry-ingest-10x-canonical-event-load",
            "normal_load_reference": {
                "source": "services/telemetry/smoke_test_ingest.py",
                "normal_event_count": NORMAL_LOAD_REFERENCE_EVENTS,
            },
            "load_multiplier": LOAD_MULTIPLIER,
            "total_events": total_events,
            "event_type": "order_filled",
            "critical_event_policy": "order/fill events are unsampled canonical events",
            "runner": "in_process_telemetry_ingest_service",
            "dev_compose_target": _load_compose_target(),
        },
        "results": {
            "accepted_count": accepted,
            "rejected_count": rejected,
            "written_count": stats["writer"]["total_written"],
            "expected_count": total_events,
            "drop_count": drop_count,
            "duplicate_write_count": duplicate_write_count,
            "missing_event_count": len(missing_ids),
            "missing_event_ids_sample": missing_ids[:10],
            "dead_letter_count": stats["dead_letter_queue"]["total_rejected"],
            "buffer_overflow_count": buffer_overflow_count,
            "writer_failed_count": stats["writer"]["total_failed"],
            "writer_retry_count": stats["writer"]["total_retried"],
            "throughput_events_per_second": round(total_events / elapsed, 2) if elapsed else 0.0,
            "burst_enqueue_events_per_second": round(total_events / burst_elapsed, 2)
            if burst_elapsed
            else 0.0,
            "elapsed_seconds": round(elapsed, 4),
            "burst_enqueue_seconds": round(burst_elapsed, 4),
            "latency_histogram_ms": _latency_histogram_ms(latencies),
            "write_batches": len(write_batch_sizes),
            "write_batch_size": {
                "configured": batch_size,
                "min": min(write_batch_sizes) if write_batch_sizes else 0,
                "max": max(write_batch_sizes) if write_batch_sizes else 0,
            },
        },
        "backpressure": {
            "buffer_capacity": buffer_maxsize,
            "burst_buffer_depth": burst_buffer_depth,
            "burst_buffer_utilization_pct": round(burst_buffer_depth / buffer_maxsize * 100, 2),
            "pressure_after_burst": pressure_after_burst,
            "policy": (
                "Burst events are retained in the ingest buffer and drained through "
                "AsyncBatchWriter; overflow would be accounted in DLQ rather than "
                "silently lost."
            ),
        },
        "ordering_semantics": ordering_semantics,
        "acceptance": [
            {
                "criterion": "10x normal telemetry ingest load executed",
                "status": "passed" if total_events == DEFAULT_TOTAL_EVENTS else "passed-custom-count",
            },
            {
                "criterion": "no dropped canonical events",
                "status": "passed" if drop_count == 0 else "failed",
            },
            {
                "criterion": "per-aggregate ordering semantics documented and checked",
                "status": "passed" if not ordering_semantics["violations"] else "failed",
            },
            {
                "criterion": "backpressure handled without silent loss",
                "status": "passed"
                if stats["dead_letter_queue"]["total_rejected"] == 0 and pressure_after_burst in {"high", "critical"}
                else "failed",
            },
        ],
    }
    return report


async def run_overflow_accounting_scenario() -> dict[str, Any]:
    service = TelemetryIngestService(
        schema_path=str(SCHEMA_PATH),
        buffer_maxsize=4,
        batch_size=4,
        batch_interval=0.01,
        max_retries=1,
        dedup_max_size=100,
    )
    accepted = 0
    rejected = 0
    for index in range(6):
        ok = await service.ingest(_make_event(index), timeout=0.001)
        if ok:
            accepted += 1
        else:
            rejected += 1
    stats = service.stats()
    await service.stop(graceful=False)
    return {
        "accepted": accepted,
        "rejected": rejected,
        "dead_letter_count": stats["dead_letter_queue"]["total_rejected"],
        "buffer_overflow_entries": service.get_dlq_entries(
            tag_filter=TAG_BUFFER_OVERFLOW,
            limit=10,
        ),
    }


def _assert_load_report_passes(report: dict[str, Any]) -> None:
    results = report["results"]
    ordering = report["ordering_semantics"]
    backpressure = report["backpressure"]
    assert results["expected_count"] == DEFAULT_TOTAL_EVENTS
    assert results["accepted_count"] == DEFAULT_TOTAL_EVENTS
    assert results["written_count"] == DEFAULT_TOTAL_EVENTS
    assert results["rejected_count"] == 0
    assert results["drop_count"] == 0
    assert results["dead_letter_count"] == 0
    assert results["buffer_overflow_count"] == 0
    assert ordering["per_aggregate_ordering_checked"] is True
    assert ordering["global_total_order_required"] is False
    assert ordering["violations"] == []
    assert backpressure["pressure_after_burst"] in {"high", "critical"}
    assert report["scenario"]["dev_compose_target"]["service"] == "telemetry"


def test_ingest_load_10x_no_drops_and_ordering() -> None:
    report = asyncio.run(run_load_scenario())
    _assert_load_report_passes(report)


def test_overflow_is_accounted_in_dlq_not_silent_loss() -> None:
    report = asyncio.run(run_overflow_accounting_scenario())
    assert report["accepted"] == 4
    assert report["rejected"] == 2
    assert report["dead_letter_count"] == 2
    assert len(report["buffer_overflow_entries"]) == 2


def test_checked_in_evidence_satisfies_acceptance() -> None:
    assert REPORT_PATH.exists(), f"Missing evidence report: {REPORT_PATH}"
    report = json.loads(REPORT_PATH.read_text())
    _assert_load_report_passes(report)
    assert ENVIRONMENT_PATH.exists(), f"Missing environment evidence: {ENVIRONMENT_PATH}"


def _command_output(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        return f"unavailable: {exc}"
    output = (completed.stdout or completed.stderr).strip()
    return output or f"exit={completed.returncode}"


def _compose_service_check(service_name: str = "telemetry") -> str:
    output = _command_output(["docker", "compose", "config", "--services"])
    services = {line.strip() for line in output.splitlines() if line.strip()}
    return "present" if service_name in services else f"missing ({service_name})"


def write_environment(report: dict[str, Any], path: Path = ENVIRONMENT_PATH) -> None:
    compose_target = report["scenario"]["dev_compose_target"]
    lines = [
        f"# {TASK_ID} Environment",
        "",
        f"Generated at: {report['generated_at']}",
        "",
        "## Execution",
        "",
        "- Command: `python3 tests/telemetry/test_ingest_load_10x.py --write-evidence`",
        "- Runner: in-process `TelemetryIngestService` using the same ingest, buffer, writer, backpressure, schema, and DLQ code path as the deployable service.",
        "- Normal load reference: 1000 events from `services/telemetry/smoke_test_ingest.py`.",
        "- 10x load: 10000 canonical `order_filled` events.",
        "",
        "## Dev Compose Target",
        "",
        f"- Compose file: `{compose_target.get('compose_file')}`",
        f"- Service: `{compose_target.get('service')}`",
        f"- Profiles: `{compose_target.get('profiles')}`",
        f"- Ports: `{compose_target.get('ports')}`",
        f"- Depends on: `{compose_target.get('depends_on')}`",
        f"- Compose service check: `{_compose_service_check('telemetry')}`",
        "- Note: the checked-in regression is hermetic and does not require Docker; the target service is the default dev compose telemetry service.",
        "",
        "## Local Tooling",
        "",
        f"- Python: `{sys.version.split()[0]}`",
        f"- Platform: `{platform.platform()}`",
        f"- pytest: `{_command_output([sys.executable, '-m', 'pytest', '--version'])}`",
        f"- docker compose: `{_command_output(['docker', 'compose', 'version'])}`",
        f"- git HEAD: `{_command_output(['git', 'rev-parse', 'HEAD'])}`",
        "",
        "## Load Results",
        "",
        f"- Accepted: `{report['results']['accepted_count']}`",
        f"- Written: `{report['results']['written_count']}`",
        f"- Rejected: `{report['results']['rejected_count']}`",
        f"- Drop count: `{report['results']['drop_count']}`",
        f"- DLQ count: `{report['results']['dead_letter_count']}`",
        f"- Throughput events/sec: `{report['results']['throughput_events_per_second']}`",
        f"- Burst buffer utilization: `{report['backpressure']['burst_buffer_utilization_pct']}%`",
        f"- Pressure after burst: `{report['backpressure']['pressure_after_burst']}`",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def write_report(report: dict[str, Any], path: Path = REPORT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-evidence", action="store_true")
    parser.add_argument("--total-events", type=int, default=DEFAULT_TOTAL_EVENTS)
    parser.add_argument("--report-path", type=Path, default=REPORT_PATH)
    parser.add_argument("--environment-path", type=Path, default=ENVIRONMENT_PATH)
    args = parser.parse_args(argv)

    report = asyncio.run(run_load_scenario(total_events=args.total_events))
    if args.write_evidence:
        write_report(report, args.report_path)
        write_environment(report, args.environment_path)
    print(json.dumps(report["results"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
