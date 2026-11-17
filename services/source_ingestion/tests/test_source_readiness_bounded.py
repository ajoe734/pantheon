from __future__ import annotations

import importlib
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from services.source_ingestion.controller_state import ControllerState, ControllerStateStore


def _load_source_main(monkeypatch: Any, tmp_path: Path) -> Any:
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOURCE_INGEST_MAX_RECORDS", "3")
    monkeypatch.setenv("SEARCH_INGEST_NOTIFY_URL", "")
    sys.modules.pop("services.source_ingestion.main", None)
    return importlib.import_module("services.source_ingestion.main")


def _actual_readback(connector_count: int) -> dict[str, Any]:
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    connectors = [
        {
            "connector_id": f"connector-{index:03d}",
            "configured": True,
            "schedule": {"enabled": True, "interval_seconds": 60},
            "freshness": {
                "status": "fresh",
                "last_ingest_run_id": f"run-{index:03d}",
                "staleness_seconds": 1,
            },
            "latest_source_record": {
                "source_id": f"source-{index:03d}",
                "status": "normalized",
                "created_at": captured_at,
            },
            "source_health": {"status": "ok", "last_success_at": captured_at},
        }
        for index in range(connector_count)
    ]
    return {
        "schema_version": "source_ingest_controller_readback.v1",
        "captured_at": captured_at,
        "connector_count": connector_count,
        "source_record_count": connector_count,
        "dlq_count": 0,
        "pending_dlq_count": 0,
        "unresolved_dlq_count": 0,
        "dlq_status_counts": {
            "pending": 0,
            "replayed": 0,
            "duplicate_skipped": 0,
            "replay_failed": 0,
            "schema_rejected": 0,
        },
        "frontier_backlog": 0,
        "max_lag_seconds": 1,
        "connectors": connectors,
    }


def test_health_and_readiness_use_bounded_controller_summary_for_260_connectors(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    module = _load_source_main(monkeypatch, tmp_path)
    state = ControllerState(
        controller_id="source-controller:test",
        controller_name="source-ingestion-controller",
        environment="test",
        tenant_id="tenant-test",
        deployment={"git_sha": "test", "image_digest": "test"},
    )
    state.record_tick_started()
    state.record_success(
        desired_state={"sha256": "desired-sha", "persona_count": 260, "requirement_count": 260},
        reconcile={"connector_ids": [f"connector-{index:03d}" for index in range(260)]},
        schedule={
            "mode": "reconcile_only",
            "provider_egress_attempted": False,
            "summary": {"total_reconciled_connectors": 260, "total_provider_pulls": 0},
        },
        actual_readback=_actual_readback(260),
    )
    ControllerStateStore(module.runtime.CONTROLLER_STATE_PATH).save(state)

    # A readiness probe must not replay these journals, even when they are
    # large.  Any accidental use of a store fails this test immediately.
    calls: list[str] = []

    class ForbiddenStore:
        def __getattr__(self, name: str) -> Any:
            calls.append(name)
            raise AssertionError(f"readiness must not replay {name}")

    monkeypatch.setattr(module.runtime, "connector_store", ForbiddenStore())
    monkeypatch.setattr(module.runtime, "schedule_config_store", ForbiddenStore())
    monkeypatch.setattr(module.runtime, "store", ForbiddenStore())
    monkeypatch.setattr(module.runtime, "evidence_repository", ForbiddenStore())
    (tmp_path / "ingest_schedule.jsonl").write_bytes(b"{}\n" * 1_000_000)

    client = TestClient(module.app)
    started = time.monotonic()
    ready = client.get("/readyz")
    health = client.get("/health")
    elapsed_seconds = time.monotonic() - started

    assert ready.status_code == 200, ready.text
    assert health.status_code == 200, health.text
    assert elapsed_seconds < 1.0
    assert calls == []
    readiness = ready.json()["dependencies"]["source_freshness"]
    assert readiness["status"] == "ok"
    assert readiness["connector_inventory_count"] == 260
    assert readiness["scheduled_connector_count"] == 260
    assert readiness["provider_egress_attempted"] is False
    assert health.json()["connector_count"] == 260


def _saved_controller(module: Any, *, connectors: list[dict[str, Any]] | None = None) -> ControllerState:
    state = ControllerState(
        controller_id="source-controller:test",
        controller_name="source-ingestion-controller",
        environment="test",
        tenant_id="tenant-test",
        deployment={"git_sha": "test", "image_digest": "test"},
    )
    state.record_tick_started()
    readback = _actual_readback(3)
    if connectors is not None:
        readback["connectors"] = connectors
        readback["connector_count"] = len(connectors)
    state.record_success(
        desired_state={"sha256": "desired-sha", "persona_count": 3, "requirement_count": 3},
        reconcile={"connector_ids": ["a", "b", "c"]},
        schedule={"mode": "reconcile_only", "provider_egress_attempted": False, "summary": {}},
        actual_readback=readback,
    )
    return state


def _age_heartbeat(state: ControllerState, seconds: int) -> None:
    from datetime import timedelta

    state.heartbeat_at = (
        (datetime.now(timezone.utc) - timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def test_readyz_not_ready_when_controller_heartbeat_is_stale(tmp_path: Path, monkeypatch: Any) -> None:
    module = _load_source_main(monkeypatch, tmp_path)
    state = _saved_controller(module)
    _age_heartbeat(state, 20727)
    ControllerStateStore(module.runtime.CONTROLLER_STATE_PATH).save(state)

    client = TestClient(module.app)
    ready = client.get("/readyz")
    assert ready.status_code == 503, ready.text
    body = ready.json()
    assert body["ready"] is False
    freshness = body["dependencies"]["source_freshness"]
    assert freshness["controller_stale"] is True
    assert freshness["reason"] == "controller_stale"
    assert freshness["controller_heartbeat_age_seconds"] >= 20727
    # Liveness is process-only: a dead scheduler must not stop restarts.
    assert client.get("/livez").status_code == 200

    # The BFF downstream monitor's own parser sees source-ingest as not ready.
    import io
    import urllib.error
    from unittest import mock

    from services.control_plane.bff import downstream_health_monitor as dhm

    err = urllib.error.HTTPError("http://x/readyz", 503, "unavailable", {}, io.BytesIO(ready.content))
    with mock.patch.object(dhm.urllib.request, "urlopen", side_effect=err):
        ok, code, reason = dhm._probe_http("http://x/readyz", 1.0)
    assert (ok, code) == (False, 503)

    degraded_body = io.BytesIO(ready.content)
    degraded_body.status = 200  # type: ignore[attr-defined]
    degraded_body.__enter__ = lambda self=degraded_body: self  # type: ignore[method-assign]
    degraded_body.__exit__ = lambda *a: False  # type: ignore[method-assign]
    with mock.patch.object(dhm.urllib.request, "urlopen", return_value=degraded_body):
        ok, code, reason = dhm._probe_http("http://x/readyz", 1.0)
    assert ok is False and reason.startswith("functional_")


def test_readyz_ready_with_fresh_controller_and_stale_connectors(tmp_path: Path, monkeypatch: Any) -> None:
    module = _load_source_main(monkeypatch, tmp_path)
    connectors = [
        {
            "connector_id": f"c{i}",
            "schedule": {"enabled": True},
            "freshness": {"status": "stale"},
        }
        for i in range(21)
    ]
    ControllerStateStore(module.runtime.CONTROLLER_STATE_PATH).save(_saved_controller(module, connectors=connectors))

    ready = TestClient(module.app).get("/readyz")
    assert ready.status_code == 200, ready.text
    freshness = ready.json()["dependencies"]["source_freshness"]
    assert freshness["stale_connector_count"] == 21
    assert freshness["controller_stale"] is False


def test_readyz_does_not_block_first_start_without_controller_state(tmp_path: Path, monkeypatch: Any) -> None:
    module = _load_source_main(monkeypatch, tmp_path)
    ready = TestClient(module.app).get("/readyz")
    assert ready.status_code == 200, ready.text
    assert ready.json()["dependencies"]["source_freshness"]["reason"] == "controller_state_missing"
