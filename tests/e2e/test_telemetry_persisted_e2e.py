"""DEVLOOP-L0-003: verify e2e telemetry persisted (not synthesized).

Three assertions proven here:

1. **Stats counter rises** — after injecting heartbeat, pnl_snapshot, and
   bracket_order_logged events into TelemetryIngestService, the stats
   ``service.total_ingested`` count increases by exactly the number of
   accepted events.  This proves the ingest path records what arrived, not
   a synthesised placeholder.

2. **Timestamps are pre-set, not request-time** — the events handed to the
   write_fn carry the exact ``created_at`` values from the original payloads.
   If the service were synthesising timestamps at request time, the recorded
   ``created_at`` would equal the current UTC second, not the hard-coded
   fixture date (2026-05-15T…).

3. **Census correctly gates on real vs synthesised telemetry** —
   ``devloop_census.build_census`` marks ``right_half_started=True`` only when
   source is non-fallback with trades>0 **and** loop_runs>0; it marks
   ``right_half_started=False`` for ``telemetry_summary_fallback`` or
   zero-trade records even when event count is positive.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
import types
import urllib.error
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.telemetry.ingest_svc import TelemetryIngestService
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BINDING_ID = "devloop-l0-003-binding-001"
_RUNTIME_ID = "devloop-l0-003-runtime-001"
_POOL_ID = "devloop-l0-003-pool-001"
_ARTIFACT_ID = "devloop-l0-003-artifact-001"
_PLAN_ID = "devloop-l0-003-plan-001"
_PCB_ID = "devloop-l0-003-pcb-001"

_PRESETTLED_TS = {
    "heartbeat": "2026-05-15T10:00:00Z",
    "pnl_snapshot": "2026-05-15T10:00:10Z",
    "bracket_order_logged": "2026-05-15T10:00:20Z",
}


class _StubBindingStore:
    def get_binding(self, binding_id: str) -> types.SimpleNamespace | None:
        if binding_id == _BINDING_ID:
            return types.SimpleNamespace(
                binding_id=_BINDING_ID,
                runtime_id=_RUNTIME_ID,
                capital_pool_id=_POOL_ID,
                artifact_id=_ARTIFACT_ID,
                artifact_version="1.0.0",
                plan_id=_PLAN_ID,
                persona_capital_binding_id=_PCB_ID,
                deployment_mode="paper",
                execution_mode="paper",
                effective_at="2026-05-15T09:00:00Z",
                retired_at=None,
            )
        return None


def _make_event(event_type: str, event_id: str, metrics: dict) -> dict:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "created_at": _PRESETTLED_TS[event_type],
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": _BINDING_ID,
        "runtime_id": _RUNTIME_ID,
        "capital_pool_id": _POOL_ID,
        "artifact_id": _ARTIFACT_ID,
        "artifact_version": "1.0.0",
        "plan_id": _PLAN_ID,
        "persona_capital_binding_id": _PCB_ID,
        "target": {"strategy_id": "devloop-l0-003-strategy"},
        "metrics": metrics,
        "metadata": {
            "engine_bridge_repo": "ajoe734/pantheon-lean.git",
            "engine_bridge_commit": "devloop-l0-003-commit",
        },
        "loop_run_id": "devloop-l0-003-loop-run-001",
    }


_EVENTS = [
    _make_event("heartbeat", "devloop-l0-003-hb-001", {"heartbeat": 1}),
    _make_event("pnl_snapshot", "devloop-l0-003-pnl-001", {"pnl": 42.0, "total_trades": 5}),
    _make_event(
        "bracket_order_logged",
        "devloop-l0-003-ord-001",
        {"action": "bracket_logged_only"},
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_ingest(events: list[dict]) -> tuple[dict, list[list[dict]]]:
    """Run the ingest service and return (stats, batches_written)."""
    written_batches: list[list[dict]] = []

    async def _recording_write_fn(batch: list[dict]) -> Any:
        written_batches.append([json.loads(json.dumps(e)) for e in batch])
        result = types.SimpleNamespace(written=len(batch), failed=0)
        return result

    svc = TelemetryIngestService(
        binding_store=_StubBindingStore(),
        runtime_summary_store=RuntimeSummaryProjectionStore(
            heartbeat_stale_after_seconds=10_000_000
        ),
        write_fn=_recording_write_fn,
        batch_size=10,
        batch_interval=0.02,
    )
    await svc.start()

    for event in events:
        await svc.ingest(event)

    await asyncio.sleep(0.15)

    stats = svc.stats()
    await svc.stop(graceful=True)

    return stats, written_batches


# ---------------------------------------------------------------------------
# Test 1: stats counter rises
# ---------------------------------------------------------------------------


def test_stats_counter_rises_by_number_of_accepted_events() -> None:
    """service.total_ingested equals len(accepted events) — counter is not static."""
    stats, _ = asyncio.run(_run_ingest(_EVENTS))

    service_stats = stats["service"]
    # All three events have valid binding_id and deployment_stage — expect all accepted.
    assert service_stats["total_ingested"] == len(_EVENTS), (
        f"expected total_ingested={len(_EVENTS)}, got {service_stats['total_ingested']}; "
        f"full stats={stats}"
    )
    # No events should be rejected under normal conditions.
    assert service_stats["total_rejected"] == 0, (
        f"unexpected rejections: {service_stats['total_rejected']}"
    )


# ---------------------------------------------------------------------------
# Test 2: written events carry pre-set timestamps (not request-time)
# ---------------------------------------------------------------------------


def test_written_events_carry_presettled_timestamps_not_request_time() -> None:
    """Events flushed to the write_fn must have the original created_at values.

    If the service were synthesising timestamps at read/request time, the
    ``created_at`` fields would differ from the fixture values.
    """
    _, written_batches = asyncio.run(_run_ingest(_EVENTS))

    assert written_batches, "write_fn must have been called at least once"

    all_written = [evt for batch in written_batches for evt in batch]
    written_by_id = {e["event_id"]: e for e in all_written}

    for original in _EVENTS:
        eid = original["event_id"]
        assert eid in written_by_id, f"event {eid} was not written to the write_fn"
        written_ts = written_by_id[eid].get("created_at")
        expected_ts = _PRESETTLED_TS[original["event_type"]]
        assert written_ts == expected_ts, (
            f"event {eid}: expected created_at={expected_ts!r}, "
            f"got {written_ts!r} — timestamp was synthesised"
        )

    # Confirm no written event has a timestamp that looks like the current
    # second (i.e. starts with today's date prefix 2026-06-).
    today_prefix = time.strftime("%Y-%m-%d", time.gmtime())
    for evt in all_written:
        ts = evt.get("created_at", "")
        assert not ts.startswith(today_prefix), (
            f"event {evt.get('event_id')!r} has a request-time timestamp "
            f"{ts!r} that matches today's date {today_prefix!r}"
        )


# ---------------------------------------------------------------------------
# Test 3: loop_run_id is non-empty in written events (loop-run > 0 evidence)
# ---------------------------------------------------------------------------


def test_written_events_carry_loop_run_id() -> None:
    """At least one written event must carry a non-empty loop_run_id.

    The presence of loop_run_id in the persisted payload is the ingest-side
    evidence that a loop run occurred (loop-run > 0).
    """
    _, written_batches = asyncio.run(_run_ingest(_EVENTS))
    all_written = [evt for batch in written_batches for evt in batch]

    loop_run_ids = [
        e.get("loop_run_id")
        for e in all_written
        if e.get("loop_run_id")
    ]
    assert loop_run_ids, (
        "No written event contained a non-empty loop_run_id; "
        "expected at least one event to carry loop_run_id indicating a loop run occurred"
    )


# ---------------------------------------------------------------------------
# Helpers: census module loader (mirrors scripts/test_devloop_census.py)
# ---------------------------------------------------------------------------


def _load_census_module():
    module_path = REPO_ROOT / "scripts" / "devloop_census.py"
    spec = importlib.util.spec_from_file_location("devloop_census", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, *, status: int = 200, body: dict[str, Any]) -> None:
        self.status = status
        self._body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self) -> bytes:
        return self._body


# ---------------------------------------------------------------------------
# Test 4: census marks right_half_started=True when source is postgres + trades>0
# ---------------------------------------------------------------------------


def test_census_real_telemetry_with_trades_marks_right_half_started(monkeypatch) -> None:
    """Census sees source=postgres + trades=5 + loop_run=1: right_half_started must be True."""
    census = _load_census_module()

    payloads = {
        "/api/v1/telemetry": {
            "data": [
                {
                    "id": "devloop-l0-003-pnl-001",
                    "event_type": "pnl_snapshot",
                    "created_at": _PRESETTLED_TS["pnl_snapshot"],
                    "metrics": {"pnl": 42.0, "total_trades": 5},
                }
            ],
            "meta": {
                "total": 1,
                "surfaces": {
                    "telemetry_events": {"status": "ok", "source": "postgres"}
                },
            },
        },
        "/bff/v5/loop-runs": {
            "items": [{"id": "devloop-l0-003-loop-run-001"}],
            "meta": {"total": 1},
        },
        "/bff/approvals": {"items": [], "count": 0},
        "/api/v1/evolution-decisions": {
            "items": [],
            "page_info": {"next_page_token": None},
        },
        "/bff/incidents": {"data": [], "page_info": {"total": 0}},
        "/api/v1/rollbacks": {
            "items": [],
            "meta": {"snapshot_at": "2026-06-14T00:00:00Z"},
        },
    }

    def fake_urlopen(request, timeout, context):
        path = request.full_url.removeprefix("https://bff.test")
        return _FakeResponse(body=payloads[path])

    monkeypatch.setattr(census.urllib.request, "urlopen", fake_urlopen)

    result = census.build_census(
        base_url="https://bff.test",
        token="op-dev:admin:mfa",
        timeout=3.0,
        insecure=True,
    )

    assert result["right_half_started"] is True, (
        f"expected right_half_started=True for postgres source + trades=5; "
        f"got surfaces={result['surfaces']['telemetry']}"
    )
    assert result["totals"]["trades"] == 5
    assert result["totals"]["loop_runs"] == 1
    assert result["hard_failure_count"] == 0


# ---------------------------------------------------------------------------
# Test 5: census rejects synthesised fallback as non-material
# ---------------------------------------------------------------------------


def test_census_synthesised_fallback_is_not_material(monkeypatch) -> None:
    """When source=telemetry_summary_fallback the census must NOT mark right_half_started."""
    census = _load_census_module()

    payloads = {
        "/api/v1/telemetry": {
            "data": [
                {
                    "id": "synth-001",
                    "metrics": {"total_trades": 0, "pnl": 0.0},
                }
            ],
            "meta": {
                "total": 1,
                "surfaces": {
                    "telemetry_events": {
                        "status": "degraded",
                        "source": "telemetry_summary_fallback",
                    }
                },
            },
        },
        "/bff/v5/loop-runs": {"data": [], "meta": {"total": 0}},
        "/bff/approvals": {"items": [], "count": 0},
        "/api/v1/evolution-decisions": {
            "items": [],
            "page_info": {"next_page_token": None},
        },
        "/bff/incidents": {"data": [], "page_info": {"total": 0}},
        "/api/v1/rollbacks": {
            "items": [],
            "meta": {"snapshot_at": "2026-06-14T00:00:00Z"},
        },
    }

    def fake_urlopen(request, timeout, context):
        path = request.full_url.removeprefix("https://bff.test")
        return _FakeResponse(body=payloads[path])

    monkeypatch.setattr(census.urllib.request, "urlopen", fake_urlopen)

    result = census.build_census(
        base_url="https://bff.test",
        token="op-dev:admin:mfa",
        timeout=3.0,
        insecure=True,
    )

    assert result["right_half_started"] is False, (
        "synthesised telemetry_summary_fallback must NOT set right_half_started"
    )
    assert result["totals"]["trades"] == 0
    assert result["surfaces"]["telemetry"]["material_reason"] == "synthesized summary fallback"
