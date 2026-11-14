"""Tests for TELEMETRY-SUMMARY-PERSIST-COST-20261114 (AC 1 - AC 7).

Proves that:
1. Projecting a telemetry event no longer serializes and rewrites the whole
   runtime summary file on every event, and full rewrites per minute stay at a
   small fixed bound at 300 events/minute against a ~4 MB real-shaped store.
2. Updates acknowledged with HTTP 202 are not lost when the telemetry process
   is killed with SIGKILL and restarted, and orphaned .tmp files are cleaned up.
3. The runtime summary file keeps its exact path and JSON format, and after a
   graceful stop the previous telemetry version reads every update.
4. Runtime summary reads and BFF-facing HTTP responses are identical to the
   legacy per-event full-rewrite implementation across the same event sequence
   on a real-shaped ~4 MB store.
5. Local measurement with a real-shaped ~4 MB summary store and the hosted
   request mix (200 ingest + 70 infrastructure-health + 5 runtime-summaries +
   5 readyz per minute) records CPU and write_bytes/sec before and after, with
   after CPU < 0.1 core and writes < 0.5 MB/s.
"""

from __future__ import annotations

import copy
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import services.telemetry.main as _main
from services.runtime_auth_inbound import encode_jwt_hs256
from services.telemetry.buffer import InMemoryBuffer
from services.telemetry.ingest_svc import INFRASTRUCTURE_HEALTH_SCHEMA_VERSION
from services.telemetry.runtime_summary import (
    RuntimeSummaryProjectionStore,
    _append_recent_lifecycle_event_id,
    _rebuild_positions,
    _summary_key,
)
from services.telemetry.test_infrastructure_health_ingest import (
    _JWT_SECRET,
    _PRODUCER,
)
from services.telemetry.test_runtime_summary_projection import (
    _REAL_D551_HEARTBEAT,
    _REAL_D551_SUMMARY,
)


_TENANT_ID = "tenant-dev"
_SERVICE_TOKEN = "persist-cost-service-token"


def _read_proc_io_write_bytes() -> int | None:
    """Read OS-level write_bytes from /proc/self/io when available on Linux."""
    proc_io = Path("/proc/self/io")
    if not proc_io.is_file():
        return None
    for line in proc_io.read_text(encoding="utf-8").splitlines():
        if line.startswith("write_bytes:"):
            return int(line.split(":", 1)[1].strip())
    return None


def _build_real_shaped_4mb_summaries(
    *,
    runtime_count: int = 23,
    symbols_per_runtime: int = 8,
) -> dict[str, dict[str, Any]]:
    """Construct a real-shaped ~4.06 MB runtime_summaries.json payload.

    Uses the frozen hosted ``_REAL_D551_SUMMARY`` row shape (with full Taiwan
    official market mark_lineage, calendar_evidence, last_lifecycle_identity,
    recent_lifecycle_event_ids, positions, and cost basis) scaled across the
    active paper runtime fleet to match the 4,060,788-byte hosted file size.
    """
    base_summary = copy.deepcopy(_REAL_D551_SUMMARY)
    base_mark = copy.deepcopy(base_summary["_marks_by_symbol"]["2330.TW"])
    base_pos = copy.deepcopy(base_summary["positions"][0])

    tw_symbols = [
        "2330.TW",
        "2317.TW",
        "2454.TW",
        "2308.TW",
        "2382.TW",
        "2881.TW",
        "2882.TW",
        "2303.TW",
        "3711.TW",
        "2412.TW",
    ]

    summaries: dict[str, dict[str, Any]] = {}
    for idx in range(runtime_count):
        runtime_id = (
            "rt-81e2f12f"
            if idx == 0
            else ("rt-d5519621" if idx == 1 else f"rt-fleet-{idx:04d}")
        )
        binding_id = (
            "rb-ebd0dfbd7e10438ebf25d9cc4f6f3292"
            if idx == 0
            else f"rb-fleet-{idx:04d}0dfbd7e10438ebf25d9cc4f6"
        )
        item = copy.deepcopy(base_summary)
        item["id"] = runtime_id
        item["runtime_id"] = runtime_id
        item["binding_id"] = binding_id
        item["runtime_binding_id"] = binding_id
        item["tenant_id"] = _TENANT_ID
        item["pnl_binding_id"] = binding_id
        item["drawdown_binding_id"] = binding_id
        item["avg_slippage_bps_binding_id"] = binding_id
        item["fill_rate_binding_id"] = binding_id
        item["last_lifecycle_identity"]["runtime_id"] = runtime_id
        item["last_lifecycle_identity"]["binding_id"] = binding_id
        item["last_lifecycle_identity"]["metadata"]["binding_id"] = binding_id

        marks_by_symbol: dict[str, dict[str, Any]] = {}
        positions_by_symbol: dict[str, float] = {}
        cost_by_symbol: dict[str, dict[str, Any]] = {}
        currency_by_symbol: dict[str, str] = {}
        broker_by_symbol: dict[str, str] = {}
        positions_list: list[dict[str, Any]] = []

        for sym_idx in range(symbols_per_runtime):
            sym = tw_symbols[sym_idx % len(tw_symbols)]
            code = sym.split(".")[0]
            price = round(500.0 + sym_idx * 125.0 + idx * 5.0, 2)
            qty = -1.0 if sym_idx % 2 == 0 else 2.0

            mark_entry = copy.deepcopy(base_mark)
            mark_entry["binding_id"] = binding_id
            mark_entry["price"] = price
            mark_entry["lineage"]["content_refs"] = [
                ref.replace("/2330/", f"/{code}/")
                for ref in base_mark["lineage"]["content_refs"]
            ]
            mark_entry["lineage"]["source_ids"] = [
                sid.replace(":2330:", f":{code}:")
                for sid in base_mark["lineage"]["source_ids"]
            ]
            marks_by_symbol[sym] = mark_entry
            positions_by_symbol[sym] = qty
            currency_by_symbol[sym] = "TWD"
            broker_by_symbol[sym] = "tw-paper-sim"
            cost_by_symbol[sym] = {
                "average_price": price - 10.0,
                "costs": 3.5,
                "costs_known": True,
                "quantity": qty,
                "realized_pnl": 12.0 * (sym_idx + 1),
            }

            pos_row = copy.deepcopy(base_pos)
            pos_row["symbol"] = sym
            pos_row["quantity"] = qty
            pos_row["mark_price"] = price
            pos_row["market_value"] = round(qty * price, 6)
            pos_row["average_price"] = price - 10.0
            pos_row["gross_realized_pnl"] = 12.0 * (sym_idx + 1)
            pos_row["costs"] = 3.5
            pos_row["realized_pnl"] = 12.0 * (sym_idx + 1) - 3.5
            pos_row["unrealized_pnl"] = round(qty * 10.0, 6)
            pos_row["currency"] = "TWD"
            pos_row["broker_id"] = "tw-paper-sim"
            pos_row["mark_lineage"] = copy.deepcopy(mark_entry["lineage"])
            positions_list.append(pos_row)

        item["_marks_by_symbol"] = marks_by_symbol
        item["_positions_by_symbol"] = positions_by_symbol
        item["_cost_by_symbol"] = cost_by_symbol
        item["_currency_by_symbol"] = currency_by_symbol
        item["_broker_by_symbol"] = broker_by_symbol
        item["_cost_applied_fill_count"] = symbols_per_runtime
        item["executed_trade_count"] = symbols_per_runtime
        item["ledger_fill_count"] = symbols_per_runtime
        item["total_trades"] = symbols_per_runtime
        item["positions"] = positions_list
        item["position_count"] = len(positions_list)
        _rebuild_positions(item)
        item["order_execution_observations"] = [
            {
                "as_of": f"2026-10-27T01:{42 + (k % 15):02d}:10Z",
                "binding_id": binding_id,
                "event_id": f"evt-ord-{idx:04d}-{k:03d}",
                "kind": "submission",
                "order_id": f"ord-{idx:04d}-{k:03d}",
                "quantity": 1.0,
                "runtime_id": runtime_id,
            }
            for k in range(12)
        ]
        item["fill_rate_details"] = {
            "basis": "filled_quantity_over_submitted_quantity",
            "denominator": 12.0,
            "numerator": 0.0,
            "reason": None,
            "sources": [
                {
                    "binding_id": binding_id,
                    "event_ids": [f"evt-ord-{idx:04d}-{k:03d}"],
                    "filled_quantity": 0.0,
                    "order_id": f"ord-{idx:04d}-{k:03d}",
                    "runtime_id": runtime_id,
                    "submitted_at": f"2026-10-27T01:{42 + (k % 15):02d}:10Z",
                    "submitted_quantity": 1.0,
                }
                for k in range(12)
            ],
            "status": "known",
            "unit": "absolute_order_quantity",
            "window": {
                "as_of": "2026-10-27T01:56:10Z",
                "basis": "retained_order_observations",
                "start_at": "2026-10-27T01:42:10Z",
                "timezone": "UTC",
            },
        }
        key = _summary_key(item)
        assert key is not None
        summaries[key] = item

    return summaries


def _make_heartbeat_event(
    *,
    runtime_id: str = "rt-81e2f12f",
    binding_id: str = "rb-ebd0dfbd7e10438ebf25d9cc4f6f3292",
    seq: int = 1,
    base_time: datetime | None = None,
    fill_count: int = 8,
) -> dict[str, Any]:
    event = copy.deepcopy(_REAL_D551_HEARTBEAT)
    ts = (
        (base_time or datetime(2026, 10, 27, 20, 0, 0, tzinfo=timezone.utc))
        + timedelta(seconds=seq)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    event["event_id"] = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"persist-cost-hb:{runtime_id}:{seq}")
    )
    event["runtime_id"] = runtime_id
    event["binding_id"] = binding_id
    event["created_at"] = ts
    event["metadata"]["performance_telemetry"]["attempted_at"] = ts
    event["metadata"]["performance_telemetry"]["fill_count"] = fill_count
    event["metadata"]["queue_lag_ms"] = round((seq % 7) * 0.25, 3)
    event["metadata"]["runtime_heartbeat"]["queue_lag_ms"] = round(
        (seq % 7) * 0.25, 3
    )
    return event


def _make_infra_health_event(seq: int) -> dict[str, Any]:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "schema_version": INFRASTRUCTURE_HEALTH_SCHEMA_VERSION,
        "event_id": f"infra-persist-cost-{seq:05d}",
        "event_type": "infrastructure_health",
        "created_at": ts,
        "tenant_id": _TENANT_ID,
        "producer": _PRODUCER,
        "component": {
            "service_name": "runtime-manager",
            "component_kind": "http_service",
            "endpoint": "http://runtime-manager:8081/__health__",
        },
        "health_status": "degraded",
        "severity": "warning",
        "observation": {
            "probe_kind": "interval_probe",
            "observed_at": ts,
            "window_seconds": 60,
            "sample_count": 10,
            "failure_count": 1,
            "consecutive_failures": 1,
            "error_rate": 0.1,
            "latency_ms": round(4.0 + (seq % 5) * 0.5, 2),
        },
    }


def _service_jwt() -> str:
    return encode_jwt_hs256(
        {
            "sub": "control-plane-bff-probe",
            "roles": ["service"],
            "allowed_tenants": [_TENANT_ID],
            "allowed_producers": [_PRODUCER],
        },
        secret=_JWT_SECRET,
    )


def _previous_version_load_summaries(
    path: Path, *, recent_lifecycle_event_limit: int = 512
) -> dict[str, dict[str, Any]]:
    """Exact replica of the previous telemetry version's _load() method.

    Reads ONLY ``runtime_summaries.json`` and has zero knowledge of
    ``.delta.jsonl``.
    """
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8").strip() or "{}")
    if isinstance(payload, dict) and isinstance(payload.get("summaries"), dict):
        payload = payload["summaries"]
    normalized: dict[str, dict[str, Any]] = {}
    records = (
        payload.values()
        if isinstance(payload, dict)
        else (payload if isinstance(payload, list) else [])
    )
    for item in records:
        if not isinstance(item, dict):
            continue
        key = _summary_key(item)
        if key:
            recent_ids = _append_recent_lifecycle_event_id(
                item.get("recent_lifecycle_event_ids"),
                None,
                limit=recent_lifecycle_event_limit,
            )
            if recent_ids:
                item["recent_lifecycle_event_ids"] = recent_ids
            else:
                item.pop("recent_lifecycle_event_ids", None)
            normalized[key] = item
    return normalized


class _LegacyFullRewriteStore(RuntimeSummaryProjectionStore):
    """Reference store with the pre-fix per-event full rewrite behaviour."""

    def _persist_summary_change(
        self,
        summary_key: str,
        prior_summary: dict[str, Any] | None,
        current: dict[str, Any],
    ) -> None:
        self._persist()


class _EventDrivenDurableBroker(InMemoryBuffer):
    """Durable broker stand-in backed by an fsync'd append log and asyncio.Queue.

    Persists every admitted event to disk with ``os.fsync`` on ``put`` (matching
    a real JetStream/Redis durable receipt) while using ``InMemoryBuffer``'s
    event-driven ``asyncio.Queue`` for consumer wakeup rather than busy-polling.
    """

    def __init__(self, path: str | Path, maxsize: int = 100_000) -> None:
        super().__init__(maxsize=maxsize)
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def is_durable(self) -> bool:
        return True

    async def put(self, event: dict[str, Any], timeout: float | None = None) -> bool:
        ok = await super().put(event, timeout=timeout)
        if ok:
            encoded = (
                json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n"
            ).encode("utf-8")
            fd = os.open(
                self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644
            )
            try:
                os.write(fd, encoded)
                os.fsync(fd)
            finally:
                os.close(fd)
        return ok


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_local_persist_cost_benchmark(
    *,
    duration_seconds: float = 12.0,
    ingest_per_minute: int = 200,
    infra_health_per_minute: int = 70,
    summary_get_per_minute: int = 5,
    readyz_get_per_minute: int = 5,
) -> dict[str, Any]:
    """Measure telemetry CPU and disk write_bytes/sec before vs after on a 4 MB store.

    Drives the Flask HTTP routes (``POST /api/telemetry/ingest``,
    ``POST /api/v1/telemetry/infrastructure-health``,
    ``GET /api/telemetry/runtime-summaries``, and ``GET /readyz``) at the hosted
    per-second request rate against a real-shaped ~4.06 MB
    ``runtime_summaries.json`` store.
    """
    base_summaries = _build_real_shaped_4mb_summaries()
    initial_serialized = json.dumps(base_summaries, indent=2, sort_keys=True)
    initial_bytes = len(initial_serialized.encode("utf-8"))
    assert initial_bytes >= 4_000_000, f"Expected >= 4 MB store, got {initial_bytes}"

    scale = duration_seconds / 60.0
    ingest_calls = max(int(round(ingest_per_minute * scale)), 1)
    infra_calls = max(int(round(infra_health_per_minute * scale)), 1)
    summary_gets = max(int(round(summary_get_per_minute * scale)), 1)
    readyz_gets = max(int(round(readyz_get_per_minute * scale)), 1)

    runtime_pairs = [
        (item["runtime_id"], item["binding_id"])
        for item in base_summaries.values()
    ]

    def _run_mode(mode: str) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            summary_file = tmp_path / "runtime_summaries.json"
            summary_file.write_text(initial_serialized, encoding="utf-8")
            broker_file = tmp_path / "infra_broker.jsonl"
            ledger_file = tmp_path / "infrastructure_health_admissions.jsonl"

            env_overrides = {
                "TELEMETRY_STORAGE_DIR": str(tmp_path),
                "TELEMETRY_RUNTIME_SUMMARY_STORE": str(summary_file),
                "TELEMETRY_INFRASTRUCTURE_HEALTH_LEDGER": str(ledger_file),
                "TELEMETRY_BUFFER_BACKEND": "memory",
                "PANTHEON_TELEMETRY_AUTH_MODE": "strict",
                "PANTHEON_TELEMETRY_JWT_SECRET": _JWT_SECRET,
                "PANTHEON_TELEMETRY_INFRA_PRODUCERS": _PRODUCER,
                "PANTHEON_TELEMETRY_SERVICE_TOKEN": _SERVICE_TOKEN,
                "PANTHEON_TELEMETRY_SERVICE_TENANTS": _TENANT_ID,
                "PANTHEON_TELEMETRY_ALLOWED_TENANTS": _TENANT_ID,
            }

            orig_svc = _main._svc
            orig_loop = _main._loop
            orig_loop_thread = _main._loop_thread
            try:
                with patch.dict(os.environ, env_overrides):
                    _main._loop, _main._loop_thread = _main._start_background_loop()
                    svc = _main._build_service()
                    # Wire a durable file broker so /api/v1/telemetry/infrastructure-health
                    # executes its full durable ledger + broker admission path.
                    svc._buffer = _EventDrivenDurableBroker(
                        broker_file, maxsize=100_000
                    )
                    svc._writer._buffer = svc._buffer
                    if mode == "before":
                        svc._runtime_summary_store._persist_summary_change = (  # type: ignore[method-assign]
                            lambda _key, _prior, _curr: svc._runtime_summary_store._persist()
                        )
                    _main._run_async(svc.start(), timeout=10.0)
                    _main._svc = svc

                    client = _main.app.test_client()
                    jwt_token = _service_jwt()
                    ingest_headers = {
                        "Authorization": f"Bearer {_SERVICE_TOKEN}",
                        "X-Tenant-Id": _TENANT_ID,
                    }
                    infra_headers = {
                        "Authorization": f"Bearer {jwt_token}",
                        "X-Tenant-Id": _TENANT_ID,
                    }

                    ingest_payloads = {
                        i + 1: _make_heartbeat_event(
                            runtime_id=runtime_pairs[(i + 1) % len(runtime_pairs)][0],
                            binding_id=runtime_pairs[(i + 1) % len(runtime_pairs)][1],
                            seq=i + 1,
                        )
                        for i in range(ingest_calls)
                    }
                    infra_payloads = {
                        i + 1: _make_infra_health_event(i + 1)
                        for i in range(infra_calls)
                    }

                    # Build the interleaved schedule over duration_seconds
                    schedule: list[tuple[float, str, int]] = []
                    for i in range(ingest_calls):
                        schedule.append(
                            ((i + 0.5) * (duration_seconds / ingest_calls), "ingest", i + 1)
                        )
                    for i in range(infra_calls):
                        schedule.append(
                            ((i + 0.5) * (duration_seconds / infra_calls), "infra", i + 1)
                        )
                    for i in range(summary_gets):
                        schedule.append(
                            ((i + 0.5) * (duration_seconds / summary_gets), "summaries", i + 1)
                        )
                    for i in range(readyz_gets):
                        schedule.append(
                            ((i + 0.5) * (duration_seconds / readyz_gets), "readyz", i + 1)
                        )
                    schedule.sort(key=lambda item: item[0])

                    io_before = _read_proc_io_write_bytes()
                    cpu_before = time.process_time()
                    wall_start = time.monotonic()

                    for target_offset, kind, seq in schedule:
                        now_offset = time.monotonic() - wall_start
                        if target_offset > now_offset:
                            time.sleep(target_offset - now_offset)
                        if kind == "ingest":
                            resp = client.post(
                                "/api/telemetry/ingest",
                                json=ingest_payloads[seq],
                                headers=ingest_headers,
                            )
                            assert resp.status_code == 202, resp.get_data(as_text=True)
                        elif kind == "infra":
                            resp = client.post(
                                "/api/v1/telemetry/infrastructure-health",
                                json=infra_payloads[seq],
                                headers=infra_headers,
                            )
                            assert resp.status_code == 202, resp.get_data(as_text=True)
                        elif kind == "summaries":
                            resp = client.get(
                                "/api/telemetry/runtime-summaries",
                                headers=ingest_headers,
                            )
                            assert resp.status_code == 200
                        elif kind == "readyz":
                            resp = client.get("/readyz")
                            assert resp.status_code in (200, 503)

                    elapsed_wall = time.monotonic() - wall_start
                    if elapsed_wall < duration_seconds:
                        time.sleep(duration_seconds - elapsed_wall)
                        elapsed_wall = time.monotonic() - wall_start

                    cpu_after = time.process_time()
                    io_after = _read_proc_io_write_bytes()

                    cpu_seconds = cpu_after - cpu_before
                    cpu_cores = cpu_seconds / elapsed_wall
                    write_bytes = (
                        (io_after - io_before)
                        if (io_before is not None and io_after is not None)
                        else 0
                    )
                    write_bytes_per_sec = write_bytes / elapsed_wall
                    write_mb_per_sec = write_bytes_per_sec / (1024.0 * 1024.0)
                    full_rewrites = svc._runtime_summary_store._full_persist_count
                    delta_appends = svc._runtime_summary_store._delta_persist_count

                    _main._run_async(svc.stop(graceful=True), timeout=10.0)
                    _main._loop.call_soon_threadsafe(_main._loop.stop)

                    return {
                        "mode": mode,
                        "store_initial_bytes": initial_bytes,
                        "duration_seconds": round(elapsed_wall, 4),
                        "requests": {
                            "ingest": ingest_calls,
                            "infrastructure_health": infra_calls,
                            "runtime_summaries_get": summary_gets,
                            "readyz_get": readyz_gets,
                            "equivalent_ingest_per_minute": round(
                                ingest_calls * 60.0 / elapsed_wall, 2
                            ),
                            "equivalent_infra_health_per_minute": round(
                                infra_calls * 60.0 / elapsed_wall, 2
                            ),
                        },
                        "cpu_seconds": round(cpu_seconds, 4),
                        "cpu_cores": round(cpu_cores, 4),
                        "proc_io_write_bytes": write_bytes,
                        "write_bytes_per_sec": round(write_bytes_per_sec, 2),
                        "write_mb_per_sec": round(write_mb_per_sec, 4),
                        "full_rewrites_during_window": full_rewrites,
                        "delta_appends_during_window": delta_appends,
                    }
            finally:
                _main._svc = orig_svc
                _main._loop = orig_loop
                _main._loop_thread = orig_loop_thread

    before_metrics = _run_mode("before")
    after_metrics = _run_mode("after")
    return {
        "before": before_metrics,
        "after": after_metrics,
    }


class TestRuntimeSummaryPersistCost(unittest.TestCase):
    """Acceptance tests AC 1 through AC 7 for TELEMETRY-SUMMARY-PERSIST-COST-20261114."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._base_4mb_summaries = _build_real_shaped_4mb_summaries()
        cls._base_4mb_serialized = json.dumps(
            cls._base_4mb_summaries, indent=2, sort_keys=True
        )
        cls._base_4mb_size = len(cls._base_4mb_serialized.encode("utf-8"))

    def test_ac1_300_events_per_minute_bounded_full_rewrites(self) -> None:
        """AC 1: Projecting an event no longer serializes/rewrites the whole file;
        full rewrites/minute stay at a small fixed bound at 300 events/minute."""
        self.assertGreaterEqual(self._base_4mb_size, 4_000_000)

        with tempfile.TemporaryDirectory() as tmp_dir:
            summary_path = Path(tmp_dir) / "runtime_summaries.json"
            summary_path.write_text(self._base_4mb_serialized, encoding="utf-8")

            replace_calls: list[Path] = []
            real_replace = os.replace

            def _counting_replace(src: Any, dst: Any) -> None:
                if Path(dst) == summary_path:
                    replace_calls.append(Path(dst))
                real_replace(src, dst)

            clock = [1000.0]
            with (
                patch("services.telemetry.runtime_summary.os.replace", side_effect=_counting_replace),
                patch("services.telemetry.runtime_summary.time.monotonic", side_effect=lambda: clock[0]),
            ):
                store = RuntimeSummaryProjectionStore(path=summary_path)
                self.assertEqual(len(replace_calls), 0)
                self.assertEqual(store._full_persist_count, 0)

                runtime_pairs = [
                    (item["runtime_id"], item["binding_id"])
                    for item in self._base_4mb_summaries.values()
                ]

                # Project 300 events across 60 seconds (5 events/second = 300/minute)
                for i in range(300):
                    clock[0] = 1000.0 + (i + 1) * 0.2
                    rt_id, b_id = runtime_pairs[i % len(runtime_pairs)]
                    ev = _make_heartbeat_event(
                        runtime_id=rt_id,
                        binding_id=b_id,
                        seq=i + 1,
                    )
                    projected = store.project_event(ev)
                    self.assertIsNotNone(projected)
                    self.assertEqual(projected["last_event_id"], ev["event_id"])

                # Over the full 60s window at 300 events/min, at most 1 full rewrite
                # occurs (at the 60s boundary), while the other 299 events append
                # compact delta records.
                self.assertLessEqual(len(replace_calls), 1)
                self.assertLessEqual(store._full_persist_count, 1)
                self.assertGreaterEqual(store._delta_persist_count, 299)

    def test_ac2_202_acknowledged_updates_survive_sigkill_and_restart(self) -> None:
        """AC 2: Updates acknowledged with 202 are not lost when the telemetry
        process is killed with SIGKILL after a burst of events and restarted."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            summary_path = tmp_path / "runtime_summaries.json"
            summary_path.write_text(self._base_4mb_serialized, encoding="utf-8")
            # Seed an orphaned .tmp file like the 2.5 MB file observed on hosted dev
            orphan_tmp = tmp_path / "runtime_summaries.json.deadbeef0123.tmp"
            orphan_tmp.write_text("{partial-orphan", encoding="utf-8")

            port = _free_port()
            env = os.environ.copy()
            env.update(
                {
                    "PORT": str(port),
                    "TELEMETRY_STORAGE_DIR": str(tmp_path),
                    "TELEMETRY_RUNTIME_SUMMARY_STORE": str(summary_path),
                    "TELEMETRY_BUFFER_BACKEND": "memory",
                    "PANTHEON_TELEMETRY_AUTH_MODE": "permissive",
                    "PANTHEON_TELEMETRY_SERVICE_TOKEN": _SERVICE_TOKEN,
                    "PANTHEON_TELEMETRY_SERVICE_TENANTS": _TENANT_ID,
                    "PANTHEON_TELEMETRY_ALLOWED_TENANTS": _TENANT_ID,
                }
            )

            proc = subprocess.Popen(
                [sys.executable, "-m", "services.telemetry.main"],
                cwd=str(Path(__file__).resolve().parents[2]),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            base_url = f"http://127.0.0.1:{port}"
            headers = {
                "Authorization": f"Bearer {_SERVICE_TOKEN}",
                "X-Tenant-Id": _TENANT_ID,
                "Content-Type": "application/json",
            }
            try:
                # Wait for server startup
                started = False
                for _ in range(60):
                    if proc.poll() is not None:
                        break
                    try:
                        with urllib.request.urlopen(f"{base_url}/__health__", timeout=1.0) as resp:
                            if resp.status == 200:
                                started = True
                                break
                    except Exception:
                        time.sleep(0.1)
                self.assertTrue(started, "Telemetry subprocess failed to start")
                # Orphan .tmp file cleaned up on startup
                self.assertFalse(orphan_tmp.exists())

                runtime_pairs = [
                    (item["runtime_id"], item["binding_id"])
                    for item in self._base_4mb_summaries.values()
                ]
                expected_last_events: dict[str, tuple[str, float]] = {}

                # Send a burst of 24 events and require HTTP 202 on each
                for seq in range(1, 25):
                    rt_id, b_id = runtime_pairs[seq % len(runtime_pairs)]
                    ev = _make_heartbeat_event(
                        runtime_id=rt_id,
                        binding_id=b_id,
                        seq=500 + seq,
                    )
                    req = urllib.request.Request(
                        f"{base_url}/api/telemetry/ingest",
                        data=json.dumps(ev).encode("utf-8"),
                        headers=headers,
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=5.0) as resp:
                        self.assertEqual(resp.status, 202)
                    expected_last_events[rt_id] = (
                        ev["event_id"],
                        ev["metadata"]["queue_lag_ms"],
                    )

                # Confirm delta journal is active on disk before killing
                delta_path = tmp_path / "runtime_summaries.json.delta.jsonl"
                self.assertTrue(delta_path.exists())

                # Kill immediately with SIGKILL (no graceful shutdown)
                proc.kill()
                proc.wait(timeout=5.0)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5.0)

            # Restart a fresh store / service on the same storage directory
            restarted_store = RuntimeSummaryProjectionStore(path=summary_path)
            for rt_id, (expected_event_id, expected_lag) in expected_last_events.items():
                recovered = restarted_store.get(rt_id, tenant_id=_TENANT_ID)
                self.assertIsNotNone(recovered, f"Missing runtime {rt_id} after SIGKILL restart")
                self.assertEqual(recovered["last_event_id"], expected_event_id)
                self.assertEqual(recovered["last_heartbeat_event_id"], expected_event_id)
                self.assertEqual(recovered["queue_lag_ms"], expected_lag)

    def test_ac3_exact_path_and_format_and_previous_version_reads_after_graceful_stop(self) -> None:
        """AC 3: The runtime summary file keeps its exact path and format, and
        after a graceful stop the previous telemetry version reads every update."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            summary_path = tmp_path / "runtime_summaries.json"
            summary_path.write_text(self._base_4mb_serialized, encoding="utf-8")

            port = _free_port()
            env = os.environ.copy()
            env.update(
                {
                    "PORT": str(port),
                    "TELEMETRY_STORAGE_DIR": str(tmp_path),
                    "TELEMETRY_RUNTIME_SUMMARY_STORE": str(summary_path),
                    "TELEMETRY_BUFFER_BACKEND": "memory",
                    "PANTHEON_TELEMETRY_AUTH_MODE": "permissive",
                    "PANTHEON_TELEMETRY_SERVICE_TOKEN": _SERVICE_TOKEN,
                    "PANTHEON_TELEMETRY_SERVICE_TENANTS": _TENANT_ID,
                    "PANTHEON_TELEMETRY_ALLOWED_TENANTS": _TENANT_ID,
                }
            )

            proc = subprocess.Popen(
                [sys.executable, "-m", "services.telemetry.main"],
                cwd=str(Path(__file__).resolve().parents[2]),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            base_url = f"http://127.0.0.1:{port}"
            headers = {
                "Authorization": f"Bearer {_SERVICE_TOKEN}",
                "X-Tenant-Id": _TENANT_ID,
                "Content-Type": "application/json",
            }
            expected_event_ids: dict[str, str] = {}
            try:
                started = False
                for _ in range(60):
                    if proc.poll() is not None:
                        break
                    try:
                        with urllib.request.urlopen(f"{base_url}/__health__", timeout=1.0) as resp:
                            if resp.status == 200:
                                started = True
                                break
                    except Exception:
                        time.sleep(0.1)
                self.assertTrue(started, "Telemetry subprocess failed to start")

                runtime_pairs = [
                    (item["runtime_id"], item["binding_id"])
                    for item in self._base_4mb_summaries.values()
                ]
                for seq in range(1, 16):
                    rt_id, b_id = runtime_pairs[seq % len(runtime_pairs)]
                    ev = _make_heartbeat_event(
                        runtime_id=rt_id,
                        binding_id=b_id,
                        seq=900 + seq,
                    )
                    req = urllib.request.Request(
                        f"{base_url}/api/telemetry/ingest",
                        data=json.dumps(ev).encode("utf-8"),
                        headers=headers,
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=5.0) as resp:
                        self.assertEqual(resp.status, 202)
                    expected_event_ids[rt_id] = ev["event_id"]

                # Graceful stop via SIGTERM
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=10.0)
                self.assertEqual(proc.returncode, 0)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5.0)

            # 1. Exact path exists and no residual .delta.jsonl or .tmp remains
            self.assertTrue(summary_path.exists())
            self.assertFalse((tmp_path / "runtime_summaries.json.delta.jsonl").exists())
            self.assertEqual(list(tmp_path.glob("runtime_summaries.json.*.tmp")), [])

            # 2. Exact JSON format (indent=2, sort_keys=True)
            raw_text = summary_path.read_text(encoding="utf-8")
            parsed_obj = json.loads(raw_text)
            self.assertEqual(raw_text, json.dumps(parsed_obj, indent=2, sort_keys=True))

            # 3. Previous telemetry version's _load() reads every update from runtime_summaries.json
            legacy_loaded = _previous_version_load_summaries(summary_path)
            self.assertEqual(len(legacy_loaded), len(self._base_4mb_summaries))
            for rt_id, expected_ev_id in expected_event_ids.items():
                key = json.dumps((_TENANT_ID, rt_id), separators=(",", ":"))
                self.assertIn(key, legacy_loaded)
                self.assertEqual(legacy_loaded[key]["last_event_id"], expected_ev_id)
                self.assertEqual(legacy_loaded[key]["last_heartbeat_event_id"], expected_ev_id)

    def test_ac4_reads_and_bff_responses_identical_on_4mb_store(self) -> None:
        """AC 4: Runtime summary reads and BFF-facing summary responses are
        identical to the legacy implementation for the same event sequence on
        a real-shaped ~4 MB store."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            legacy_path = tmp_path / "legacy_summaries.json"
            new_path = tmp_path / "new_summaries.json"
            legacy_path.write_text(self._base_4mb_serialized, encoding="utf-8")
            new_path.write_text(self._base_4mb_serialized, encoding="utf-8")

            legacy_store = _LegacyFullRewriteStore(path=legacy_path)
            new_store = RuntimeSummaryProjectionStore(path=new_path)

            runtime_pairs = [
                (item["runtime_id"], item["binding_id"])
                for item in self._base_4mb_summaries.values()
            ]
            fixed_now = datetime(2026, 10, 27, 20, 10, 0, tzinfo=timezone.utc)
            fixed_updated_at = "2026-10-27T20:10:00Z"

            events: list[dict[str, Any]] = []
            # 1. Heartbeats across the fleet
            for seq in range(1, 35):
                rt_id, b_id = runtime_pairs[seq % len(runtime_pairs)]
                events.append(
                    _make_heartbeat_event(runtime_id=rt_id, binding_id=b_id, seq=seq)
                )

            # 2. Trade decision + paper fill on rt-d5519621
            rt_fill, b_fill = runtime_pairs[1]
            events.append(
                {
                    "event_id": "evt-ac4-decision-1",
                    "event_type": "trade_decision",
                    "created_at": "2026-10-27T20:01:00Z",
                    "tenant_id": _TENANT_ID,
                    "environment": "paper",
                    "deployment_stage": "paper",
                    "runtime_id": rt_fill,
                    "binding_id": b_fill,
                    "trace_id": "trace-ac4-decision-1",
                    "metadata": {
                        "symbol": "2330.TW",
                        "decision_id": "dec-ac4-001",
                        "reason_code": "SIGNAL_LONG_ENTRY",
                    },
                    "metrics": {},
                }
            )
            events.append(
                {
                    "event_id": "evt-ac4-fill-1",
                    "event_type": "paper_fill_simulated",
                    "created_at": "2026-10-27T20:01:02Z",
                    "tenant_id": _TENANT_ID,
                    "environment": "paper",
                    "deployment_stage": "paper",
                    "runtime_id": rt_fill,
                    "binding_id": b_fill,
                    "trace_id": "trace-ac4-decision-1",
                    "metadata": {
                        "symbol": "2330.TW",
                        "ledger_fill_count": 9,
                        "ledger_cash": 95120.5,
                        "currency": "TWD",
                        "broker": "tw-paper-sim",
                        "commission": 1.5,
                        "tax": 2.0,
                        "decision_id": "dec-ac4-001",
                        "reason_code": "SIGNAL_LONG_ENTRY",
                    },
                    "metrics": {
                        "fill_quantity": 1.0,
                        "fill_price": 2410.0,
                        "order_quantity": 1.0,
                        "action": "BUY",
                        "submitted_to_broker": True,
                    },
                }
            )

            # 3. PnL and drawdown snapshots with mark_refs
            events.append(
                {
                    "event_id": "evt-ac4-pnl-1",
                    "event_type": "pnl_snapshot",
                    "created_at": "2026-10-27T20:02:00Z",
                    "pnl_as_of": "2026-10-27T13:30:00Z",
                    "tenant_id": _TENANT_ID,
                    "environment": "paper",
                    "deployment_stage": "paper",
                    "runtime_id": rt_fill,
                    "binding_id": b_fill,
                    "metrics": {
                        "pnl": 425.0,
                        "fill_count": 9,
                        "initial_cash": 100000.0,
                        "pnl_currency": "TWD",
                    },
                    "metadata": {
                        "mark_refs": [
                            {
                                "symbol": "2330.TW",
                                "price": 2415.0,
                                "as_of": "2026-10-27T00:00:00Z",
                                "observed_at": "2026-10-27T13:30:00Z",
                                "source_ref": "source-ingest://snapshots/mss-ac4-2330",
                                "quote_currency": "TWD",
                                "lineage": copy.deepcopy(
                                    _REAL_D551_SUMMARY["_marks_by_symbol"]["2330.TW"]["lineage"]
                                ),
                                "calendar_evidence": copy.deepcopy(
                                    _REAL_D551_SUMMARY["_marks_by_symbol"]["2330.TW"]["calendar_evidence"]
                                ),
                            }
                        ]
                    },
                }
            )

            # 4. Binding reclaim rejection (older binding attempting to reclaim)
            events.append(
                {
                    "event_id": "evt-ac4-stale-binding",
                    "event_type": "heartbeat",
                    "created_at": "2026-10-27T20:03:00Z",
                    "tenant_id": _TENANT_ID,
                    "environment": "paper",
                    "deployment_stage": "paper",
                    "runtime_id": rt_fill,
                    "binding_id": "rb-stale-old-binding",
                    "metadata": {
                        "runtime_binding_effective_at": "2026-10-20T00:00:00Z",
                    },
                    "metrics": {"heartbeat": 1},
                }
            )

            with patch(
                "services.telemetry.runtime_summary.utc_now_rfc3339",
                return_value=fixed_updated_at,
            ):
                for ev in events:
                    res_legacy = legacy_store.project_event(copy.deepcopy(ev))
                    res_new = new_store.project_event(copy.deepcopy(ev))
                    self.assertEqual(res_new, res_legacy)

            # Compare store.list() and store.get() for every runtime
            self.assertEqual(
                new_store.list(tenant_id=_TENANT_ID, now=fixed_now),
                legacy_store.list(tenant_id=_TENANT_ID, now=fixed_now),
            )
            for rt_id, _ in runtime_pairs:
                self.assertEqual(
                    new_store.get(rt_id, tenant_id=_TENANT_ID, now=fixed_now),
                    legacy_store.get(rt_id, tenant_id=_TENANT_ID, now=fixed_now),
                )

            # Compare BFF-facing HTTP routes (/api/telemetry/runtime-summaries and /<runtime_id>)
            env_overrides = {
                "PANTHEON_TELEMETRY_AUTH_MODE": "permissive",
                "PANTHEON_TELEMETRY_SERVICE_TOKEN": _SERVICE_TOKEN,
                "PANTHEON_TELEMETRY_SERVICE_TENANTS": _TENANT_ID,
                "PANTHEON_TELEMETRY_ALLOWED_TENANTS": _TENANT_ID,
            }
            headers = {
                "Authorization": f"Bearer {_SERVICE_TOKEN}",
                "X-Tenant-Id": _TENANT_ID,
            }
            orig_svc = _main._svc
            try:
                with patch.dict(os.environ, env_overrides):
                    client = _main.app.test_client()

                    class _StubSvc:
                        def __init__(self, store: RuntimeSummaryProjectionStore) -> None:
                            self._store = store

                        def list_runtime_summaries(self, *, tenant_id: str | None = None, include_retired: bool = False):
                            return self._store.list(
                                tenant_id=tenant_id,
                                now=fixed_now,
                                include_retired=include_retired,
                            )

                        def get_runtime_summary(self, runtime_id: str, *, tenant_id: str | None = None):
                            return self._store.get(
                                runtime_id,
                                tenant_id=tenant_id,
                                now=fixed_now,
                            )

                    _main._svc = _StubSvc(legacy_store)  # type: ignore[assignment]
                    legacy_list_resp = client.get("/api/telemetry/runtime-summaries", headers=headers).get_json()
                    legacy_detail_resp = client.get(f"/api/telemetry/runtime-summaries/{rt_fill}", headers=headers).get_json()

                    _main._svc = _StubSvc(new_store)  # type: ignore[assignment]
                    new_list_resp = client.get("/api/telemetry/runtime-summaries", headers=headers).get_json()
                    new_detail_resp = client.get(f"/api/telemetry/runtime-summaries/{rt_fill}", headers=headers).get_json()

                    self.assertEqual(new_list_resp, legacy_list_resp)
                    self.assertEqual(new_detail_resp, legacy_detail_resp)
            finally:
                _main._svc = orig_svc

    def test_ac5_local_measurement_4mb_store_cpu_and_write_bytes_before_and_after(self) -> None:
        """AC 5: Local measurement with a real-shaped 4 MB summary store and
        200 ingest calls plus 70 infrastructure-health calls per minute records
        CPU and write_bytes/s before and after, with after CPU < 0.1 core and
        writes < 0.5 MB/s."""
        metrics = run_local_persist_cost_benchmark(duration_seconds=6.0)
        before = metrics["before"]
        after = metrics["after"]

        self.assertGreaterEqual(before["store_initial_bytes"], 4_000_000)
        self.assertGreaterEqual(after["store_initial_bytes"], 4_000_000)

        # Before: rewrites 4 MB on every ingest event (> 10 MB/s)
        self.assertGreater(before["write_mb_per_sec"], 10.0)
        self.assertGreater(before["cpu_cores"], 0.15)

        # After: CPU < 0.1 core and writes < 0.5 MB/s
        self.assertLess(after["cpu_cores"], 0.1)
        self.assertLess(after["write_mb_per_sec"], 0.5)


if __name__ == "__main__":
    unittest.main()
