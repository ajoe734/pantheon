"""
LOOP-AUTO-BFF-002: BFF downstream health monitor contract tests.

Covers:
- DownstreamHealthMonitor probe loop logic (unit)
- Telemetry emit on each probe result (sink mock)
- Incident open for sustained failure, idempotency on repeat failure
- Incident tracking cleared on recovery
- GET /bff/v5/downstream-health contract (BFF route)
- BFF degraded mode does not affect active runtimes
  (monitor errors are swallowed; BFF handles other routes normally)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import tempfile
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from services.control_plane.bff.downstream_health_monitor import (
    DownstreamHealthMonitor,
    DownstreamProbeResult,
    _DurableHealthStore,
    _probe_file_health,
    _probe_http,
    _post_json,
    _utc_now_rfc3339,
)

OPERATOR_TOKEN = "Bearer op-2:operator"


def _run(coro):
    """Run a coroutine in a new event loop (no pytest-asyncio needed)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# DownstreamProbeResult unit tests
# ---------------------------------------------------------------------------


def test_probe_result_to_dict_ok():
    r = DownstreamProbeResult(
        target_name="telemetry",
        ok=True,
        status_code=200,
        latency_ms=12.5,
        checked_at="2026-06-27T00:00:00Z",
    )
    d = r.to_dict()
    assert d["target_name"] == "telemetry"
    assert d["ok"] is True
    assert d["status_code"] == 200
    assert d["consecutive_failures"] == 0
    assert d["failure_reason"] is None


def test_probe_result_to_dict_failure():
    r = DownstreamProbeResult(
        target_name="incidents",
        ok=False,
        status_code=-1,
        latency_ms=3001.0,
        checked_at="2026-06-27T00:00:00Z",
        failure_reason="URLError: Connection refused",
        consecutive_failures=3,
    )
    d = r.to_dict()
    assert d["ok"] is False
    assert d["consecutive_failures"] == 3
    assert "Connection refused" in d["failure_reason"]


# ---------------------------------------------------------------------------
# DownstreamHealthMonitor unit tests
# ---------------------------------------------------------------------------


class TestDownstreamHealthMonitorState:
    def _make_monitor(self, **kwargs) -> DownstreamHealthMonitor:
        return DownstreamHealthMonitor(
            telemetry_url="http://telemetry:8080",
            incidents_url="http://incidents:8090",
            telemetry_service_jwt="test-service-jwt",
            tenant_id="tenant-test",
            probe_interval_seconds=9999,  # don't fire automatically in tests
            failure_threshold=3,
            **kwargs,
        )

    def test_get_state_empty_before_first_probe(self):
        monitor = self._make_monitor()
        with patch.object(monitor, "_resolve_target_registry", return_value={}):
            state = monitor.get_state()
            assert state["targets"] == {}
            assert state["overall_ok"] is None

    def test_get_state_with_probe_results(self):
        monitor = self._make_monitor()
        monitor._store.record_probe(DownstreamProbeResult(
            target_name="telemetry",
            ok=True,
            status_code=200,
            latency_ms=5.0,
            checked_at="2026-06-27T00:00:00Z",
        ))
        monitor._store.record_probe(DownstreamProbeResult(
            target_name="incidents",
            ok=False,
            status_code=-1,
            latency_ms=3001.0,
            checked_at="2026-06-27T00:00:00Z",
            failure_reason="Connection refused",
            consecutive_failures=2,
        ))
        state = monitor.get_state()
        assert state["overall_ok"] is False
        assert "telemetry" in state["targets"]
        assert "incidents" in state["targets"]
        assert state["targets"]["incidents"]["consecutive_failures"] == 2

    def test_get_degraded_targets(self):
        monitor = self._make_monitor()
        monitor._store.record_probe(DownstreamProbeResult(
            target_name="telemetry", ok=True, status_code=200, latency_ms=5.0, checked_at="2026-06-27T00:00:00Z"
        ))
        monitor._store.record_probe(DownstreamProbeResult(
            target_name="incidents", ok=False, status_code=-1, latency_ms=3001.0, checked_at="2026-06-27T00:00:00Z",
            failure_reason="timeout", consecutive_failures=1,
        ))
        degraded = monitor.get_degraded_targets()
        assert "incidents" in degraded
        assert "telemetry" not in degraded

    def test_resolve_targets_empty_when_no_env(self):
        monitor = self._make_monitor()
        # No env vars set, so targets should be empty
        targets = monitor._resolve_targets()
        # In test environment env vars are typically not set
        assert isinstance(targets, dict)

    def test_resolve_targets_reads_env_vars(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "http://tel:8080")
        monkeypatch.setenv("PANTHEON_INCIDENTS_API_URL", "http://inc:8090")
        monitor = self._make_monitor()
        targets = monitor._resolve_targets()
        assert "telemetry" in targets
        assert targets["telemetry"] == "http://tel:8080"
        assert "incidents" in targets
        assert targets["incidents"] == "http://inc:8090"


# ---------------------------------------------------------------------------
# Probe consecutive failure counting
# ---------------------------------------------------------------------------


class TestProbeConsecutiveFailures:
    def _make_monitor(self) -> DownstreamHealthMonitor:
        return DownstreamHealthMonitor(
            telemetry_url="",
            incidents_url="",
            probe_interval_seconds=9999,
            failure_threshold=3,
        )

    def _make_result(self, ok: bool, name: str = "telemetry", prev_failures: int = 0) -> DownstreamProbeResult:
        return DownstreamProbeResult(
            target_name=name,
            ok=ok,
            status_code=200 if ok else -1,
            latency_ms=5.0,
            checked_at="2026-06-27T00:00:00Z",
            failure_reason=None if ok else "timeout",
            consecutive_failures=0 if ok else prev_failures + 1,
        )

    def test_probe_one_ok_resets_consecutive_failures(self, monkeypatch):
        monitor = self._make_monitor()
        # Seed with previous failure
        monitor._state["telemetry"] = self._make_result(ok=False, prev_failures=4)

        with patch("services.control_plane.bff.downstream_health_monitor._probe_http", return_value=(True, 200, "")):
            result = _run(monitor._probe_one("telemetry", "http://tel:8080"))

        assert result.ok is True
        assert result.consecutive_failures == 0

    def test_probe_one_failure_increments_count(self, monkeypatch):
        monitor = self._make_monitor()
        monitor._state["telemetry"] = DownstreamProbeResult(
            target_name="telemetry", ok=False, status_code=-1, latency_ms=10.0,
            checked_at="2026-06-27T00:00:00Z", failure_reason="timeout", consecutive_failures=2,
        )

        with patch("services.control_plane.bff.downstream_health_monitor._probe_http", return_value=(False, -1, "timeout")):
            result = _run(monitor._probe_one("telemetry", "http://tel:8080"))

        assert result.ok is False
        assert result.consecutive_failures == 3

    def test_probe_one_first_failure_is_count_1(self, monkeypatch):
        monitor = self._make_monitor()
        # No previous state

        with patch("services.control_plane.bff.downstream_health_monitor._probe_http", return_value=(False, -1, "timeout")):
            result = _run(monitor._probe_one("telemetry", "http://tel:8080"))

        assert result.ok is False
        assert result.consecutive_failures == 1


# ---------------------------------------------------------------------------
# Telemetry emit tests
# ---------------------------------------------------------------------------


class TestTelemetryEmit:
    def _make_monitor(self) -> DownstreamHealthMonitor:
        return DownstreamHealthMonitor(
            telemetry_url="http://tel:8080",
            incidents_url="",
            telemetry_service_jwt="test-service-jwt",
            tenant_id="tenant-test",
            probe_interval_seconds=9999,
            failure_threshold=3,
        )

    def test_emit_telemetry_posts_to_ingest(self):
        monitor = self._make_monitor()
        result = DownstreamProbeResult(
            target_name="incidents",
            ok=False,
            status_code=-1,
            latency_ms=3001.0,
            checked_at="2026-06-27T00:00:00Z",
            failure_reason="timeout",
            consecutive_failures=1,
        )
        captured: List[Dict[str, Any]] = []

        def mock_post_json(url, body, timeout):
            captured.append({"url": url, "body": body})
            return True, 202

        with patch("services.control_plane.bff.downstream_health_monitor._post_json", side_effect=mock_post_json):
            monitor._emit_telemetry_sync(result)

        assert len(captured) == 1
        call = captured[0]
        assert call["url"] == "http://tel:8080/api/v1/telemetry/infrastructure-health"
        body = call["body"]
        assert body["event_type"] == "infrastructure_health"
        assert body["schema_version"] == "pantheon.infrastructure-health/1"
        assert body["tenant_id"] == "tenant-test"
        assert body["component"]["service_name"] == "incidents"
        assert body["observation"]["failure_count"] == 1
        assert body["observation"]["consecutive_failures"] == 1
        assert "binding_id" not in body
        assert "deployment_stage" not in body
        assert "execution_mode" not in body

    def test_emit_telemetry_ok_probe(self):
        monitor = self._make_monitor()
        result = DownstreamProbeResult(
            target_name="telemetry",
            ok=True,
            status_code=200,
            latency_ms=5.0,
            checked_at="2026-06-27T00:00:00Z",
        )
        captured: List[Dict[str, Any]] = []

        def mock_post_json(url, body, timeout):
            captured.append(body)
            return True, 202

        with patch("services.control_plane.bff.downstream_health_monitor._post_json", side_effect=mock_post_json):
            monitor._emit_telemetry_sync(result)

        assert len(captured) == 1
        assert captured[0]["health_status"] == "ok"
        assert captured[0]["observation"]["failure_count"] == 0

    def test_emit_telemetry_skipped_when_no_url(self):
        monitor = DownstreamHealthMonitor(
            telemetry_url="",
            incidents_url="",
            probe_interval_seconds=9999,
        )
        result = DownstreamProbeResult(
            target_name="telemetry", ok=True, status_code=200, latency_ms=5.0, checked_at="2026-06-27T00:00:00Z"
        )
        captured: List[Dict[str, Any]] = []

        def mock_post_json(url, body, timeout):
            captured.append(body)
            return True, 202

        with patch("services.control_plane.bff.downstream_health_monitor._post_json", side_effect=mock_post_json):
            monitor._emit_telemetry_sync(result)

        # No call made when no telemetry_url
        assert len(captured) == 0

    def test_emit_telemetry_handles_post_failure_gracefully(self):
        monitor = self._make_monitor()
        result = DownstreamProbeResult(
            target_name="runtime-manager", ok=False, status_code=-1, latency_ms=5.0,
            checked_at="2026-06-27T00:00:00Z", failure_reason="timeout",
        )

        def mock_post_json(url, body, timeout):
            return False, 500  # telemetry service is also down

        with patch("services.control_plane.bff.downstream_health_monitor._post_json", side_effect=mock_post_json):
            # Must not raise
            monitor._emit_telemetry_sync(result)


# ---------------------------------------------------------------------------
# Incident open / idempotency tests
# ---------------------------------------------------------------------------


class TestIncidentOpen:
    def _make_monitor(self) -> DownstreamHealthMonitor:
        return DownstreamHealthMonitor(
            telemetry_url="",
            incidents_url="http://inc:8090",
            tenant_id="tenant-test",
            probe_interval_seconds=9999,
            failure_threshold=3,
        )

    def test_open_incident_on_sustained_failure(self):
        monitor = self._make_monitor()
        result = DownstreamProbeResult(
            target_name="incidents",
            ok=False,
            status_code=-1,
            latency_ms=3001.0,
            checked_at="2026-06-27T00:00:00Z",
            failure_reason="timeout",
            consecutive_failures=3,
        )
        captured: List[Dict[str, Any]] = []

        def mock_post_json(url, body, timeout):
            captured.append({"url": url, "body": body})
            return True, 201

        with patch("services.control_plane.bff.downstream_health_monitor._post_json", side_effect=mock_post_json):
            inc_id = monitor._open_or_update_incident_sync(result)

        assert inc_id.startswith("infra-bff-")
        assert len(captured) == 1
        assert captured[0]["url"].endswith("/api/incidents/consume-infrastructure-health")
        body = captured[0]["body"]
        assert body["severity"] == "high"
        assert body["status"] == "open"
        assert "incidents" in body["title"]
        assert body["schema_version"] == "pantheon.infrastructure-incident/1"
        assert body["component"]["service_name"] == "incidents"
        assert "binding_id" not in body
        assert "deployment_stage" not in body

    def test_incident_idempotent_on_repeat_failure(self):
        monitor = self._make_monitor()
        result = DownstreamProbeResult(
            target_name="incidents", ok=False, status_code=-1, latency_ms=10.0,
            checked_at="2026-06-27T00:00:00Z", failure_reason="timeout", consecutive_failures=5,
        )

        with patch("services.control_plane.bff.downstream_health_monitor._post_json", return_value=(True, 201)):
            first_id = monitor._open_or_update_incident_sync(result)

        with patch("services.control_plane.bff.downstream_health_monitor._post_json") as mock_post:
            inc_id = monitor._open_or_update_incident_sync(result)

        assert inc_id == first_id
        mock_post.assert_not_called()

    def test_incident_409_is_dead_letter_not_idempotent_success(self):
        monitor = self._make_monitor()
        result = DownstreamProbeResult(
            target_name="telemetry", ok=False, status_code=-1, latency_ms=5.0,
            checked_at="2026-06-27T00:00:00Z", failure_reason="timeout", consecutive_failures=3,
        )

        def mock_post_json(url, body, timeout):
            return False, 409  # already exists

        with patch("services.control_plane.bff.downstream_health_monitor._post_json", side_effect=mock_post_json):
            inc_id = monitor._open_or_update_incident_sync(result)

        assert inc_id.startswith("infra-bff-")
        assert "telemetry" in monitor._open_incident_ids
        deliveries = monitor.list_delivery_records()
        incident_delivery = next(
            item for item in deliveries if item["channel"] == "incident_open"
        )
        assert incident_delivery["status"] == "dead_letter"
        assert monitor.get_state()["incidents"]["telemetry"]["status"] == "opening"

    def test_delivery_counts_use_summary_and_retention_prunes_history(self, tmp_path):
        store = _DurableHealthStore(str(tmp_path / "health.sqlite3"))
        store.queue_delivery(
            delivery_id="delivered-old",
            event_id="event-old",
            channel="telemetry",
            target_name="telemetry",
            url="http://telemetry/events",
            body={"event_id": "event-old"},
            dependency_ids=[],
            max_attempts=1,
        )
        with store._connect() as connection:
            connection.execute(
                """
                UPDATE delivery_outbox
                SET status='delivered', updated_at='2026-01-01T00:00:00Z'
                WHERE delivery_id='delivered-old'
                """
            )
            connection.execute(
                """
                INSERT INTO error_windows(
                    target_name, window_started_at, sample_count,
                    failure_count, latency_total_ms, last_status_code,
                    last_detail, last_observed_at
                ) VALUES (
                    'telemetry', '2026-01-01T00:00:00Z', 1, 1,
                    5.0, 500, 'old', '2026-01-01T00:00:00Z'
                )
                """
            )

        counts = store.delivery_counts()
        assert counts["delivered"] == 1
        pruned = store.prune_retained_history(
            delivery_history_seconds=60,
            window_history_seconds=60,
        )
        assert pruned["delivery_outbox"] == 1
        assert pruned["error_windows"] == 1
        assert store.delivery_counts()["delivered"] == 0

    def test_open_incident_skipped_when_no_incidents_url(self):
        monitor = DownstreamHealthMonitor(
            telemetry_url="",
            incidents_url="",
            probe_interval_seconds=9999,
        )
        result = DownstreamProbeResult(
            target_name="telemetry", ok=False, status_code=-1, latency_ms=5.0,
            checked_at="2026-06-27T00:00:00Z", failure_reason="timeout", consecutive_failures=5,
        )
        with patch("services.control_plane.bff.downstream_health_monitor._post_json") as mock_post:
            inc_id = monitor._open_or_update_incident_sync(result)

        mock_post.assert_not_called()
        assert inc_id is None

    def test_incident_not_opened_below_threshold(self):
        monitor = self._make_monitor()
        result = DownstreamProbeResult(
            target_name="telemetry", ok=False, status_code=-1, latency_ms=5.0,
            checked_at="2026-06-27T00:00:00Z", failure_reason="timeout", consecutive_failures=2,
        )
        captured: List[Dict[str, Any]] = []

        def mock_post_json(url, body, timeout):
            captured.append(body)
            return True, 201

        with patch("services.control_plane.bff.downstream_health_monitor._post_json", side_effect=mock_post_json):
            _run(monitor._handle_probe_result(result))

        assert "telemetry" not in monitor._open_incident_ids
        assert len(captured) == 0


# ---------------------------------------------------------------------------
# Recovery tracking tests
# ---------------------------------------------------------------------------


class TestRecoveryTracking:
    def test_recovery_without_incident_authority_keeps_open_tracking(self):
        monitor = DownstreamHealthMonitor(
            telemetry_url="",
            incidents_url="",
            probe_interval_seconds=9999,
            failure_threshold=3,
        )
        monitor._open_incident_ids["telemetry"] = "bff-downstream-telemetry-degraded"
        monitor._state["telemetry"] = DownstreamProbeResult(
            target_name="telemetry", ok=False, status_code=-1, latency_ms=10.0,
            checked_at="2026-06-27T00:00:00Z", consecutive_failures=3,
        )

        ok_result = DownstreamProbeResult(
            target_name="telemetry", ok=True, status_code=200, latency_ms=5.0,
            checked_at="2026-06-27T00:00:01Z",
        )

        with patch("services.control_plane.bff.downstream_health_monitor._post_json", return_value=(True, 202)):
            _run(monitor._handle_probe_result(ok_result))

        # No incident authority confirmed resolution, so the durable mapping
        # must not be falsely cleared.
        assert "telemetry" in monitor._open_incident_ids

    def test_recovery_calls_incident_resolve(self):
        monitor = DownstreamHealthMonitor(
            telemetry_url="http://tel:8080",
            incidents_url="http://inc:8090",
            telemetry_service_jwt="test-service-jwt",
            tenant_id="tenant-test",
            probe_interval_seconds=9999,
            failure_threshold=3,
        )
        failed = DownstreamProbeResult(
            target_name="telemetry", ok=False, status_code=-1, latency_ms=5.0,
            checked_at="2026-06-27T00:00:00Z", failure_reason="timeout",
            consecutive_failures=3,
        )
        with patch("services.control_plane.bff.downstream_health_monitor._post_json", return_value=(True, 201)):
            incident_id = monitor._open_or_update_incident_sync(failed)
        ok_result = DownstreamProbeResult(
            target_name="telemetry", ok=True, status_code=200, latency_ms=5.0,
            checked_at="2026-06-27T00:00:01Z",
        )
        captured: List[Dict[str, Any]] = []

        def mock_post_json(url, body, timeout):
            captured.append({"url": url, "body": body})
            return True, 200

        with patch("services.control_plane.bff.downstream_health_monitor._post_json", side_effect=mock_post_json):
            _run(monitor._handle_probe_result(ok_result))

        assert "telemetry" not in monitor._open_incident_ids
        resolve_calls = [c for c in captured if "/status" in c["url"]]
        assert len(resolve_calls) == 1
        assert resolve_calls[0]["body"]["status"] == "resolved"
        assert incident_id in resolve_calls[0]["url"]

    def test_probe_all_reconciles_orphaned_incidents_for_healthy_targets(self):
        from services.control_plane.bff.downstream_health_monitor import DownstreamTarget

        monitor = DownstreamHealthMonitor(
            telemetry_url="",
            incidents_url="http://inc:8090",
            tenant_id="tenant-test",
            probe_interval_seconds=60,
            failure_threshold=3,
        )
        monitor._resolve_target_registry = lambda: {  # type: ignore[assignment]
            "governance": DownstreamTarget(
                name="governance",
                base_url="http://governance:8080",
                health_path="/__health__",
            ),
            "deployment": DownstreamTarget(
                name="deployment",
                base_url="http://deployment:8080",
                health_path="/__health__",
            ),
        }
        captured: List[Dict[str, Any]] = []

        def mock_post_json(url, body, timeout):
            captured.append({"url": url, "body": body})
            return True, 200

        with (
            patch(
                "services.control_plane.bff.downstream_health_monitor._probe_http",
                return_value=(True, 200, ""),
            ),
            patch(
                "services.control_plane.bff.downstream_health_monitor._post_json",
                side_effect=mock_post_json,
            ),
        ):
            _run(monitor._probe_all())

        reconcile_calls = [
            c for c in captured if "/api/incidents/reconcile-infrastructure-health" in c["url"]
        ]
        assert len(reconcile_calls) == 1
        assert reconcile_calls[0]["body"]["healthy_components"] == ["deployment", "governance"]
        assert reconcile_calls[0]["body"]["referenced_incident_ids"] == []
        assert reconcile_calls[0]["body"]["resolution_reason"] == "orphan_recovered"

    def test_probe_all_sweeps_resolving_target_after_resolve_delivery_dead_letters(self):
        from services.control_plane.bff.downstream_health_monitor import DownstreamTarget

        monitor = DownstreamHealthMonitor(
            telemetry_url="",
            incidents_url="http://inc:8090",
            tenant_id="tenant-test",
            probe_interval_seconds=60,
            failure_threshold=1,
            delivery_max_attempts=5,
            delivery_backoff_seconds=0.1,
        )
        monitor._resolve_target_registry = lambda: {  # type: ignore[assignment]
            "capital": DownstreamTarget(
                name="capital",
                base_url="http://capital:8080",
                health_path="/__health__",
            ),
        }

        # 1. Open incident when capital is unhealthy and incidents service is reachable.
        with patch(
            "services.control_plane.bff.downstream_health_monitor._post_json",
            return_value=(True, 201),
        ):
            _run(
                monitor._handle_probe_result(
                    DownstreamProbeResult(
                        target_name="capital",
                        ok=False,
                        status_code=503,
                        latency_ms=10.0,
                        checked_at="2026-11-03T10:20:00Z",
                        failure_reason="HTTP 503",
                        consecutive_failures=1,
                    )
                )
            )
        inc_id = monitor._store.get_incident("capital")["incident_id"]
        assert monitor._store.get_incident("capital")["status"] == "open"

        # 2. Capital recovers while incidents service is blocked (network_error -> -1)
        # across all 5 attempts until incident_resolve enters dead_letter.
        with patch(
            "services.control_plane.bff.downstream_health_monitor._post_json",
            return_value=(False, -1),
        ):
            _run(
                monitor._handle_probe_result(
                    DownstreamProbeResult(
                        target_name="capital",
                        ok=True,
                        status_code=200,
                        latency_ms=3.0,
                        checked_at="2026-11-03T10:23:55Z",
                    )
                )
            )
            for _ in range(4):
                with monitor._store._connect() as conn:
                    conn.execute(
                        "UPDATE delivery_outbox SET next_attempt_at=0 WHERE channel='incident_resolve'"
                    )
                monitor._deliver_due_sync()

        resolve_row = next(
            r for r in monitor.list_delivery_records() if r["channel"] == "incident_resolve"
        )
        assert resolve_row["status"] == "dead_letter"
        assert resolve_row["attempt_count"] == 5
        assert resolve_row["last_error"] == "network_error"
        assert monitor._store.get_incident("capital")["status"] == "resolving"
        assert (
            monitor._store.has_active_incident_delivery(
                target_name="capital",
                incident_id=inc_id,
            )
            is False
        )

        # 3. Incidents service returns; next _probe_all() reconciles without listing
        # the dead-lettered incident in referenced_incident_ids and resolves the ledger row.
        reconcile_calls: List[Dict[str, Any]] = []

        def mock_post(url, body, timeout):
            if "/api/incidents/reconcile-infrastructure-health" in url:
                reconcile_calls.append({"url": url, "body": body})
            return True, 200

        with (
            patch(
                "services.control_plane.bff.downstream_health_monitor._probe_http",
                return_value=(True, 200, ""),
            ),
            patch(
                "services.control_plane.bff.downstream_health_monitor._post_json",
                side_effect=mock_post,
            ),
        ):
            _run(monitor._probe_all())

        assert len(reconcile_calls) == 1
        assert reconcile_calls[0]["body"]["healthy_components"] == ["capital"]
        assert reconcile_calls[0]["body"]["referenced_incident_ids"] == []
        assert reconcile_calls[0]["body"]["resolution_reason"] == "orphan_recovered"
        assert monitor._store.get_incident("capital")["status"] == "resolved"
        assert "capital" not in monitor._open_incident_ids


# ---------------------------------------------------------------------------
# BFF route contract tests
# ---------------------------------------------------------------------------


class TestBffDownstreamHealthRoute:
    def _make_client(self, monitor: Optional[DownstreamHealthMonitor] = None):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from services.control_plane.bff.control_loops.router import create_control_loops_router
        from services.control_plane.bff.control_loops.service import ControlLoopsService
        from services.foundation.health import register_fastapi_health_routes

        if monitor is None:
            monitor = DownstreamHealthMonitor(
                telemetry_url="",
                incidents_url="",
                probe_interval_seconds=9999,
            )
        service = ControlLoopsService(downstream_health_monitor=monitor)
        app = FastAPI()
        register_fastapi_health_routes(app, "operator-bff")

        @app.get("/health")
        def _compat_health():
            return {"status": "ok"}

        app.include_router(create_control_loops_router(service=service))
        return TestClient(app), monitor

    def test_downstream_health_route_returns_200(self):
        client, monitor = self._make_client()
        # Seed some state into the monitor
        monitor._store.record_probe(DownstreamProbeResult(
            target_name="telemetry",
            ok=True,
            status_code=200,
            latency_ms=5.0,
            checked_at="2026-06-27T00:00:00Z",
        ))
        response = client.get(
            "/bff/v5/downstream-health",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["read_model"] == "downstream_health"
        assert "data" in body
        assert "targets" in body["data"]
        assert "telemetry" in body["data"]["targets"]
        assert body["data"]["targets"]["telemetry"]["ok"] is True

    def test_downstream_health_route_empty_before_probes(self):
        client, monitor = self._make_client()
        with patch.object(monitor, "_resolve_target_registry", return_value={}):
            response = client.get(
                "/bff/v5/downstream-health",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["data"]["overall_ok"] is None
            assert body["data"]["targets"] == {}

    def test_downstream_health_route_shows_degraded_targets(self):
        client, monitor = self._make_client()
        monitor._store.record_probe(DownstreamProbeResult(
            target_name="incidents",
            ok=False,
            status_code=-1,
            latency_ms=3001.0,
            checked_at="2026-06-27T00:00:00Z",
            failure_reason="Connection refused",
            consecutive_failures=4,
        ))
        response = client.get(
            "/bff/v5/downstream-health",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["overall_ok"] is False
        assert body["data"]["targets"]["incidents"]["ok"] is False
        assert body["data"]["targets"]["incidents"]["consecutive_failures"] == 4

    def test_downstream_health_route_requires_auth(self):
        client, _ = self._make_client()
        response = client.get("/bff/v5/downstream-health")
        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Degraded mode isolation: monitor errors don't affect other BFF routes
# ---------------------------------------------------------------------------


class TestDegradedModeIsolation:
    def test_probe_loop_error_does_not_propagate(self):
        monitor = DownstreamHealthMonitor(
            telemetry_url="",
            incidents_url="",
            probe_interval_seconds=0.01,
            failure_threshold=3,
        )

        call_count = 0

        async def bad_probe_all():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("catastrophic probe failure")

        monitor._probe_all = bad_probe_all

        async def _run_monitor():
            await monitor.start()
            await asyncio.sleep(0.05)
            await monitor.stop()

        _run(_run_monitor())

        # The probe loop survived multiple error cycles
        assert call_count >= 1
        # BFF state is unaffected (still empty, no crash)
        with patch.object(monitor, "_resolve_target_registry", return_value={}):
            assert monitor.get_state()["targets"] == {}

    def test_bff_health_route_works_even_when_monitor_has_errors(self):
        """Verify other BFF routes continue to work when monitor state is degraded."""
        monitor = DownstreamHealthMonitor(
            telemetry_url="",
            incidents_url="",
            probe_interval_seconds=9999,
        )
        monitor._store.record_probe(DownstreamProbeResult(
            target_name="runtime-manager",
            ok=False,
            status_code=-1,
            latency_ms=9999.0,
            checked_at="2026-06-27T00:00:00Z",
            failure_reason="Connection refused",
            consecutive_failures=10,
        ))
        client, _ = TestBffDownstreamHealthRoute()._make_client(monitor)

        # BFF own health route still returns ok
        response = client.get("/health")
        assert response.status_code == 200

        # Downstream health route reflects degraded truth accurately
        dh_response = client.get(
            "/bff/v5/downstream-health",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert dh_response.status_code == 200
        assert dh_response.json()["data"]["overall_ok"] is False


# ---------------------------------------------------------------------------
# File worker health probes (L12-WORKER-HEALTH-NAMESPACE-WIRING-20261015)
# ---------------------------------------------------------------------------


class TestFileWorkerHealthProbes:
    def test_missing_health_file_returns_404(self, tmp_path):
        missing = tmp_path / "does-not-exist.json"
        ok, code, reason = _probe_file_health(str(missing), target_name="shadow-eval-scheduler")
        assert ok is False
        assert code == 404
        assert "missing" in reason

    def test_starting_status_rejected_with_500(self, tmp_path):
        f = tmp_path / "worker-health.json"
        f.write_text(json.dumps({"status": "starting", "ticks": 0, "ready": True}), encoding="utf-8")
        ok, code, reason = _probe_file_health(str(f), target_name="shadow-eval-scheduler")
        assert ok is False
        assert code == 500
        assert "status is not ok" in reason or "starting" in reason

    def test_ticks_zero_rejected_with_500(self, tmp_path):
        f = tmp_path / "worker-health.json"
        f.write_text(json.dumps({"status": "ok", "ticks": 0, "ready": True}), encoding="utf-8")
        ok, code, reason = _probe_file_health(str(f), target_name="deployment-outbox-consumer")
        assert ok is False
        assert code == 500
        assert "unready ticks" in reason or "ticks=0" in reason

    def test_legitimate_scheduler_sleep_beyond_180s_accepted(self, tmp_path):
        # Shadow scheduler interval is 3600s, max age 10800s.
        # An update 500s ago is well beyond 180s, but within max age.
        f = tmp_path / "shadow-health.json"
        payload = {
            "status": "ok",
            "ticks": 10,
            "ready": True,
            "worker_name": "policy-learning-shadow-eval-scheduler",
            "production_training": "fail_closed",
            "timestamp": "2026-10-15T10:00:00Z",
        }
        f.write_text(json.dumps(payload), encoding="utf-8")
        past_mtime = time.time() - 500
        os.utime(str(f), (past_mtime, past_mtime))

        ok, code, reason = _probe_file_health(str(f), target_name="shadow-eval-scheduler")
        assert ok is True
        assert code == 200
        assert reason == ""

    def test_stale_heartbeat_beyond_worker_max_age_returns_negative_one(self, tmp_path):
        # Shadow scheduler max age is 10800s.
        f = tmp_path / "shadow-health.json"
        payload = {
            "status": "ok",
            "ticks": 10,
            "ready": True,
            "worker_name": "policy-learning-shadow-eval-scheduler",
            "production_training": "fail_closed",
        }
        f.write_text(json.dumps(payload), encoding="utf-8")
        stale_mtime = time.time() - 11000
        os.utime(str(f), (stale_mtime, stale_mtime))

        ok, code, reason = _probe_file_health(str(f), target_name="shadow-eval-scheduler")
        assert ok is False
        assert code == -1
        assert "stale" in reason

    def test_paper_signal_producer_safety_markers_enforced(self, tmp_path):
        f = tmp_path / "paper-health.json"
        safe_payload = {
            "status": "ok",
            "ticks": 1,
            "worker_name": "paper-signal-producer",
            "execution_mode": "paper",
            "live_capital_enabled": False,
            "live_order_submission_enabled": False,
        }
        f.write_text(json.dumps(safe_payload), encoding="utf-8")
        ok, code, reason = _probe_file_health(str(f), target_name="paper-signal-producer")
        assert ok is True
        assert code == 200

        unsafe_payload = dict(safe_payload, live_capital_enabled=True)
        f.write_text(json.dumps(unsafe_payload), encoding="utf-8")
        ok, code, reason = _probe_file_health(str(f), target_name="paper-signal-producer")
        assert ok is False
        assert code == 500
        assert "safety marker mismatch" in reason or "paper execution markers invalid" in reason or "healthcheck failed" in reason

    def test_shadow_eval_scheduler_fail_closed_enforced(self, tmp_path):
        f = tmp_path / "shadow-health.json"
        f.write_text(json.dumps({
            "status": "ok",
            "ticks": 1,
            "worker_name": "policy-learning-shadow-eval-scheduler",
            "production_training": "open",
        }), encoding="utf-8")
        ok, code, reason = _probe_file_health(str(f), target_name="shadow-eval-scheduler")
        assert ok is False
        assert code == 500
        assert "fail_closed" in reason or "healthcheck failed" in reason

    def test_deployment_outbox_consumer_contracts_enforced(self, tmp_path):
        f = tmp_path / "consumer-health.json"
        err_payload = {
            "status": "ok",
            "ticks": 5,
            "consumer_name": "deployment-outbox-consumer",
            "last_success": "2026-10-15T10:00:00Z",
            "consecutive_errors": 1,
        }
        f.write_text(json.dumps(err_payload), encoding="utf-8")
        ok, code, reason = _probe_file_health(str(f), target_name="deployment-outbox-consumer")
        assert ok is False
        assert code == 500
        assert "consecutive_errors" in reason or "healthcheck failed" in reason

        ok_payload = dict(err_payload, consecutive_errors=0)
        f.write_text(json.dumps(ok_payload), encoding="utf-8")
        ok, code, reason = _probe_file_health(str(f), target_name="deployment-outbox-consumer")
        assert ok is True
        assert code == 200

    def test_reconciliation_worker_identity_enforced(self, tmp_path):
        f = tmp_path / "recon-health.json"
        f.write_text(json.dumps({"status": "ok", "ticks": 2, "worker_name": "wrong-name"}), encoding="utf-8")
        ok, code, reason = _probe_file_health(str(f), target_name="reconciliation-drift-consumer")
        assert ok is False
        assert code == 500
        assert "identity mismatch" in reason or "healthcheck failed" in reason

    def test_consultation_nested_functional_health_failure_enforced(self, tmp_path):
        f = tmp_path / "consultation-health.json"
        f.write_text(json.dumps({
            "status": "ok",
            "ticks": 1,
            "functional_health": {"ready": False, "reason": "db connection failed"},
        }), encoding="utf-8")
        ok, code, reason = _probe_file_health(str(f), target_name="consultation-workflow-executor")
        assert ok is False
        assert code == 500
        assert "functional_health.ready is false" in reason

    def test_consultation_missing_functional_health_rejected(self, tmp_path):
        f = tmp_path / "consultation-health.json"
        f.write_text(json.dumps({
            "status": "ok",
            "ticks": 1,
        }), encoding="utf-8")
        ok, code, reason = _probe_file_health(str(f), target_name="consultation-workflow-executor")
        assert ok is False
        assert code == 500
        assert "functional_health.ready is false" in reason

    def test_consultation_blocked_or_dead_letter_count_rejected(self, tmp_path):
        f = tmp_path / "consultation-health.json"
        f.write_text(json.dumps({
            "status": "ok",
            "ticks": 1,
            "functional_health": {"ready": True, "blocked_count": 2, "dead_letter_count": 0},
        }), encoding="utf-8")
        ok, code, reason = _probe_file_health(str(f), target_name="consultation-workflow-executor")
        assert ok is False
        assert code == 500
        assert "blocked_count=2" in reason

        f.write_text(json.dumps({
            "status": "ok",
            "ticks": 1,
            "functional_health": {"ready": True, "blocked_count": 0, "dead_letter_count": 1},
        }), encoding="utf-8")
        ok, code, reason = _probe_file_health(str(f), target_name="consultation-workflow-executor")
        assert ok is False
        assert code == 500
        assert "dead_letter_count=1" in reason

        f.write_text(json.dumps({
            "status": "ok",
            "ticks": 1,
            "functional_health": {"ready": True, "blocked_count": 0, "dead_letter_count": 0},
        }), encoding="utf-8")
        ok, code, reason = _probe_file_health(str(f), target_name="consultation-workflow-executor")
        assert ok is True
        assert code == 200

    def test_evolution_scheduler_identity_unforced(self, tmp_path):
        f = tmp_path / "evolution-health.json"
        f.write_text(json.dumps({"status": "ok", "ticks": 3, "scheduler": "evolution-daily-sweep"}), encoding="utf-8")
        ok, code, reason = _probe_file_health(str(f), target_name="evolution-daily-sweep-scheduler")
        assert ok is True
        assert code == 200


# ---------------------------------------------------------------------------
# PERSONA-FLEET-HEALTH-IO-20261030: durable I/O stays off the event loop
# ---------------------------------------------------------------------------


def _io_monitor(tmp_path, **kwargs) -> DownstreamHealthMonitor:
    return DownstreamHealthMonitor(
        telemetry_url="http://telemetry:8080",
        incidents_url="http://incidents:8090",
        telemetry_service_jwt="test-service-jwt",
        tenant_id="tenant-test",
        probe_interval_seconds=9999,
        failure_threshold=3,
        state_path=str(tmp_path / "health.sqlite3"),
        **kwargs,
    )


def _spy_connections(monkeypatch) -> List[Any]:
    import sqlite3

    from services.control_plane.bff import downstream_health_monitor as mod

    opened: List[Any] = []
    real_connect = sqlite3.connect

    def _connect(*args: Any, **kw: Any):
        connection = real_connect(*args, **kw)
        opened.append(connection)
        return connection

    monkeypatch.setattr(mod.sqlite3, "connect", _connect)
    return opened


def _is_closed(connection) -> bool:
    import sqlite3

    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


def test_recorded_outcome_uses_one_connection_and_closes_it(tmp_path, monkeypatch):
    monitor = _io_monitor(tmp_path)
    opened = _spy_connections(monkeypatch)
    monitor._resolve_target_registry = lambda: {"telemetry": object()}  # type: ignore[assignment]

    window = monitor.record_downstream_outcome(
        target_name="telemetry", ok=False, status_code=503, detail="HTTP 503"
    )
    # The request thread opens no connection; the monitor flush uses one.
    assert opened == []
    monitor.flush_outcomes_sync()

    assert len(opened) == 1
    assert all(_is_closed(c) for c in opened)
    # Both durable facts were written by that one transaction.
    probe = monitor._store.get_probe("telemetry")
    assert probe is not None and probe.ok is False and probe.consecutive_failures == 1
    assert monitor._store.append_error_sample(
        target_name="telemetry",
        window_started_at=_window_for(monitor),
        ok=True,
        latency_ms=0.0,
        status_code=200,
        detail=None,
        observed_at=_utc_now_rfc3339(),
    )["sample_count"] >= 2
    assert window is None or window["target_name"] == "telemetry"


def _window_for(monitor: DownstreamHealthMonitor) -> str:
    from services.control_plane.bff.downstream_health_monitor import _window_start

    return _window_start(_utc_now_rfc3339(), monitor._error_rate_window)


def test_store_connections_are_closed_deterministically(tmp_path, monkeypatch):
    monitor = _io_monitor(tmp_path)
    opened = _spy_connections(monkeypatch)

    monitor._store.list_probes()
    monitor._store.delivery_counts()
    monitor._store.claim_window(
        target_name="telemetry",
        probe_kind="interval_probe",
        window_started_at="2026-10-30T00:00:00Z",
        owner_id="a",
        lease_seconds=5,
    )
    with pytest.raises(RuntimeError):
        with monitor._store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            raise RuntimeError("boom")

    assert opened and all(_is_closed(c) for c in opened)
    # The failed transaction rolled back and released the writer lock.
    monitor._store.complete_window(
        target_name="telemetry",
        probe_kind="interval_probe",
        window_started_at="2026-10-30T00:00:00Z",
        owner_id="a",
    )
    row = monitor._store.claim_window(
        target_name="telemetry",
        probe_kind="interval_probe",
        window_started_at="2026-10-30T00:00:00Z",
        owner_id="a",
        lease_seconds=5,
    )
    assert row is False  # completed window is not claimable again


def test_synchronous_full_and_wal_are_preserved(tmp_path):
    monitor = _io_monitor(tmp_path)
    with monitor._store._connect() as connection:
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def _hold_writer(path: str, seconds: float):
    """Hold the SQLite writer lock from another connection for ``seconds``."""
    import sqlite3
    import threading

    holder = sqlite3.connect(
        path, isolation_level=None, timeout=10.0, check_same_thread=False
    )
    holder.execute("BEGIN IMMEDIATE")
    threading.Timer(seconds, lambda: (holder.rollback(), holder.close())).start()
    return holder


async def _max_loop_gap(duration: float) -> float:
    gap = 0.0
    end = time.monotonic() + duration
    last = time.monotonic()
    while time.monotonic() < end:
        await asyncio.sleep(0.01)
        now = time.monotonic()
        gap = max(gap, now - last)
        last = now
    return gap


def test_held_sqlite_writer_does_not_stall_event_loop_and_write_lands(tmp_path):
    monitor = _io_monitor(tmp_path)
    result = DownstreamProbeResult(
        target_name="telemetry",
        ok=False,
        status_code=503,
        latency_ms=5.0,
        checked_at=_utc_now_rfc3339(),
        failure_reason="HTTP 503",
        consecutive_failures=1,
    )

    async def scenario():
        _hold_writer(monitor.state_path, 1.0)
        handler = asyncio.ensure_future(monitor._handle_probe_result(result))
        gap = await _max_loop_gap(0.8)
        assert not handler.done()  # still waiting for the writer, loop is free
        await handler
        return gap

    gap = _run(scenario())

    assert gap < 0.4, f"event loop stalled {gap:.2f}s behind the SQLite writer"
    stored = monitor._store.get_probe("telemetry")
    assert stored is not None and stored.ok is False
    assert stored.consecutive_failures == 1


def test_probe_all_window_claims_and_truth_do_not_block_loop(tmp_path, monkeypatch):
    monitor = _io_monitor(tmp_path)
    monitor._resolve_target_registry = lambda: {  # type: ignore[assignment]
        "svc": __import__(
            "services.control_plane.bff.downstream_health_monitor",
            fromlist=["DownstreamTarget"],
        ).DownstreamTarget(name="svc", base_url="http://svc:1", health_path="/h")
    }

    async def _ok_probe(*_a: Any, **_k: Any):
        return True, 200, ""

    monkeypatch.setattr(
        "services.control_plane.bff.downstream_health_monitor._probe_http",
        lambda *a, **k: (True, 200, ""),
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)

    async def scenario():
        _hold_writer(monitor.state_path, 1.0)
        probe = asyncio.ensure_future(monitor._probe_all())
        gap = await _max_loop_gap(0.8)
        await probe
        return gap

    gap = _run(scenario())

    assert gap < 0.4, f"_probe_all stalled the event loop {gap:.2f}s"
    stored = monitor._store.get_probe("svc")
    assert stored is not None and stored.ok is True


def test_durable_write_failure_still_propagates(tmp_path, monkeypatch):
    monitor = _io_monitor(tmp_path)

    def _boom(_result):
        raise RuntimeError("disk full")

    monkeypatch.setattr(monitor._store, "record_probe", _boom)
    result = DownstreamProbeResult(
        target_name="telemetry",
        ok=True,
        status_code=200,
        latency_ms=1.0,
        checked_at=_utc_now_rfc3339(),
    )
    with pytest.raises(RuntimeError, match="disk full"):
        _run(monitor._handle_probe_result(result))


# --- BFF-HEALTH-OUTCOME-OFF-REQUEST-20261107 -------------------------------


def _reg_monitor(tmp_path, **kwargs) -> DownstreamHealthMonitor:
    monitor = _io_monitor(tmp_path, **kwargs)
    monitor._resolve_target_registry = lambda: {"telemetry": object()}  # type: ignore[assignment]
    return monitor


def _hold_write_lock(path: str, seconds: float):
    import sqlite3
    import threading

    locked = threading.Event()
    release = threading.Event()

    def _run() -> None:
        connection = sqlite3.connect(path, isolation_level=None)
        connection.execute("BEGIN IMMEDIATE")
        locked.set()
        release.wait(seconds)
        connection.rollback()
        connection.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert locked.wait(5)
    return thread, release


def test_request_thread_outcome_does_not_wait_on_health_write_lock(tmp_path):
    import time

    monitor = _reg_monitor(tmp_path)
    thread, release = _hold_write_lock(monitor.state_path, 3.0)
    try:
        started = time.perf_counter()
        for _ in range(10):
            monitor.record_downstream_outcome(
                target_name="telemetry", ok=True, status_code=200, latency_ms=5.0
            )
        per_call_ms = (time.perf_counter() - started) * 1000 / 10
    finally:
        release.set()
        thread.join(5)
    assert per_call_ms < 20


def test_flushed_outcomes_equal_sum_of_recorded_samples(tmp_path):
    monitor = _reg_monitor(tmp_path)
    ts = "2026-11-07T10:00:%02dZ"
    expected_latency = 0.0
    for i in range(7):
        ok = i % 3 != 0
        latency = 10.0 + i
        expected_latency += latency
        monitor.record_downstream_outcome(
            target_name="telemetry",
            ok=ok,
            status_code=200 if ok else 503,
            latency_ms=latency,
            detail=None if ok else "HTTP 503",
            observed_at=ts % i,
        )
    assert monitor._store.list_probes() == {}
    monitor.flush_outcomes_sync()
    window = monitor._store.append_error_sample(
        target_name="telemetry",
        window_started_at=_window_start_for(monitor, ts % 0),
        ok=True,
        latency_ms=0.0,
        status_code=200,
        detail=None,
        observed_at=ts % 9,
    )
    assert window["sample_count"] == 8
    assert window["failure_count"] == 3
    assert window["latency_total_ms"] == pytest.approx(expected_latency)
    probe = monitor._store.get_probe("telemetry")
    # Last recorded sample (i=6) was a failure.
    assert probe is not None and probe.ok is False and probe.consecutive_failures == 1


def _window_start_for(monitor: DownstreamHealthMonitor, ts: str) -> str:
    from services.control_plane.bff.downstream_health_monitor import _window_start

    return _window_start(ts, monitor._error_rate_window)


def test_failure_run_counts_consecutive_failures_across_flushes(tmp_path):
    monitor = _reg_monitor(tmp_path)
    for _ in range(3):
        monitor.record_downstream_outcome(
            target_name="telemetry", ok=False, status_code=503, detail="x"
        )
    monitor.flush_outcomes_sync()
    monitor.record_downstream_outcome(
        target_name="telemetry", ok=False, status_code=503, detail="x"
    )
    monitor.flush_outcomes_sync()
    assert monitor._store.get_probe("telemetry").consecutive_failures == 4


def test_threshold_crossing_emits_once_from_flush_never_asyncio_run(
    tmp_path, monkeypatch
):
    import asyncio
    import threading

    monitor = _reg_monitor(
        tmp_path,
        error_rate_window_seconds=300,
        error_rate_threshold=0.5,
        error_rate_min_samples=3,
    )
    emitted: List[Any] = []

    async def _emit(*, target_name, window):
        emitted.append((threading.current_thread().name, target_name, window))
        return True  # confirmed durable delivery intent

    monitor._emit_error_rate_window = _emit  # type: ignore[assignment]
    runs: List[Any] = []
    real_run = asyncio.run
    monkeypatch.setattr(asyncio, "run", lambda *a, **k: runs.append(a) or real_run(*a, **k))

    def _request_thread() -> None:
        for _ in range(4):
            monitor.record_downstream_outcome(
                target_name="telemetry",
                ok=False,
                status_code=503,
                detail="boom",
                observed_at="2026-11-07T10:00:01Z",
            )

    t = threading.Thread(target=_request_thread)
    t.start()
    t.join()
    assert runs == [] and emitted == []

    async def _drive() -> None:
        await monitor.flush_outcomes()
        monitor.record_downstream_outcome(
            target_name="telemetry",
            ok=False,
            status_code=503,
            observed_at="2026-11-07T10:00:02Z",
        )
        await monitor.flush_outcomes()

    real_run(_drive())
    assert runs == []
    assert len(emitted) == 1
    assert emitted[0][2]["sample_count"] == 4


def test_locked_flush_keeps_samples_and_pending_is_bounded(tmp_path):
    monitor = _reg_monitor(tmp_path, pending_outcome_max=5)
    for i in range(8):
        monitor.record_downstream_outcome(
            target_name="telemetry",
            ok=False,
            status_code=503,
            latency_ms=2.0,
            observed_at="2026-11-07T10:00:01Z",
        )
    stats = monitor.pending_outcome_stats()
    assert stats["pending_samples"] == 5 and stats["dropped_samples"] == 3

    monitor._store.flush_outcomes = lambda **kw: (_ for _ in ()).throw(  # type: ignore[assignment]
        __import__("sqlite3").OperationalError("database is locked")
    )
    assert monitor.flush_outcomes_sync() == []
    assert monitor.pending_outcome_stats()["pending_samples"] == 5
    assert monitor.pending_outcome_stats()["flush_failures"] == 1
    monitor.record_downstream_outcome(
        target_name="telemetry", ok=True, status_code=200,
        observed_at="2026-11-07T10:00:02Z",
    )
    # Still at the bound: the new sample is dropped and counted, not blocked.
    assert monitor.pending_outcome_stats()["pending_samples"] == 5
    assert monitor.pending_outcome_stats()["dropped_samples"] == 4

    del monitor._store.flush_outcomes
    monitor.flush_outcomes_sync()
    window = monitor._store.append_error_sample(
        target_name="telemetry",
        window_started_at=_window_start_for(monitor, "2026-11-07T10:00:01Z"),
        ok=True, latency_ms=0.0, status_code=200, detail=None,
        observed_at="2026-11-07T10:00:03Z",
    )
    assert window["sample_count"] == 6 and window["failure_count"] == 5
    del monitor._resolve_target_registry
    state = monitor.get_state()
    assert state["pending_outcomes"]["dropped_samples"] == 4


def test_inflight_samples_reserve_capacity_across_locked_retries(tmp_path):
    """Accepted + in-flight samples never exceed the bound while flushes fail."""
    import sqlite3

    monitor = _reg_monitor(tmp_path, pending_outcome_max=3)
    ts = "2026-11-07T10:00:01Z"

    def _record(n: int, ok: bool = False) -> None:
        for _ in range(n):
            monitor.record_downstream_outcome(
                target_name="telemetry", ok=ok, status_code=503,
                latency_ms=2.0, observed_at=ts,
            )

    def _locked_flush(**kw):
        # Requests keep arriving while the flush is blocked on the lock.
        _record(3)
        stats = monitor.pending_outcome_stats()
        assert stats["pending_samples"] <= 3
        raise sqlite3.OperationalError("database is locked")

    monitor._store.flush_outcomes = _locked_flush  # type: ignore[assignment]
    _record(3)
    for attempt in range(1, 4):
        assert monitor.flush_outcomes_sync() == []
        stats = monitor.pending_outcome_stats()
        assert stats["pending_samples"] <= 3
        assert stats["flush_failures"] == attempt
        assert stats["dropped_samples"] == 3 * attempt
    del monitor._store.flush_outcomes

    # Real writer lock held by another connection, then released.
    thread, release = _hold_write_lock(monitor._store.path, 30)
    monitor._store.flush_outcomes = lambda **kw: (_ for _ in ()).throw(  # type: ignore[assignment]
        sqlite3.OperationalError("database is locked")
    )
    assert monitor.flush_outcomes_sync() == []
    del monitor._store.flush_outcomes
    release.set()
    thread.join(5)

    monitor.flush_outcomes_sync()
    assert monitor.pending_outcome_stats()["pending_samples"] == 0
    window = monitor._store.append_error_sample(
        target_name="telemetry",
        window_started_at=_window_start_for(monitor, ts),
        ok=True, latency_ms=0.0, status_code=200, detail=None,
        observed_at="2026-11-07T10:00:03Z",
    )
    assert window["sample_count"] == 4 and window["failure_count"] == 3


def test_monitor_loop_persists_request_outcomes_within_interval(tmp_path):
    monitor = _reg_monitor(tmp_path)
    monitor._probe_interval = 0.05
    monitor._probe_all = AsyncMock()  # type: ignore[assignment]
    monitor._deliver_due_sync = lambda: None  # type: ignore[assignment]
    ts = "2026-11-07T10:00:01Z"

    result: Dict[str, Any] = {}

    async def _drive() -> None:
        await monitor.start()
        try:
            started = time.monotonic()
            for i in range(4):
                monitor.record_downstream_outcome(
                    target_name="telemetry", ok=i != 0, status_code=200 if i else 503,
                    latency_ms=10.0 + i, observed_at=ts,
                )
            deadline = started + 0.5
            while time.monotonic() < deadline:
                if monitor.pending_outcome_stats()["pending_samples"] == 0:
                    break
                await asyncio.sleep(0.005)
            result["elapsed"] = time.monotonic() - started
            # Read durable totals while the monitor loop is still running.
            result["window"] = monitor._store.append_error_sample(
                target_name="telemetry",
                window_started_at=_window_start_for(monitor, ts),
                ok=True, latency_ms=0.0, status_code=200, detail=None,
                observed_at="2026-11-07T10:00:03Z",
            )
        finally:
            await monitor.stop()

    asyncio.run(_drive())
    assert result["elapsed"] < 0.5
    window = result["window"]
    assert window["sample_count"] == 5 and window["failure_count"] == 1
    assert window["latency_total_ms"] == pytest.approx(10 + 11 + 12 + 13)


def test_error_window_retried_after_foreign_lease_expires(tmp_path):
    monitor = _reg_monitor(tmp_path)
    monitor._deliver_due_sync = lambda: 0  # type: ignore[assignment]
    queued: list = []
    monitor._queue_error_rate_window_sync = (  # type: ignore[assignment]
        lambda target, window: queued.append((target, window["window_started_at"]))
    )
    ts = "2026-11-07T10:00:01Z"
    wstart = _window_start_for(monitor, ts)
    assert monitor._store.claim_window(
        target_name="telemetry", probe_kind="error_rate",
        window_started_at=wstart, owner_id="other-instance", lease_seconds=1,
    )

    def _fail() -> None:
        for _ in range(monitor._error_rate_min_samples):
            monitor.record_downstream_outcome(
                target_name="telemetry", ok=False, status_code=503,
                latency_ms=5.0, observed_at=ts,
            )

    _fail()
    asyncio.run(monitor.flush_outcomes())
    assert queued == []  # foreign lease still held
    with monitor._store._connect() as c:
        c.execute("UPDATE probe_windows SET lease_until=? WHERE owner_id='other-instance'",
                  (time.time() - 1,))
    _fail()
    asyncio.run(monitor.flush_outcomes())
    assert queued == [("telemetry", wstart)]
    _fail()
    asyncio.run(monitor.flush_outcomes())
    assert len(queued) == 1  # confirmed delivery is deduplicated


def test_error_window_retried_after_transient_queue_failure(tmp_path):
    monitor = _reg_monitor(tmp_path)
    monitor._deliver_due_sync = lambda: 0  # type: ignore[assignment]
    calls: list = []

    def _queue(target, window):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database is locked")

    monitor._queue_error_rate_window_sync = _queue  # type: ignore[assignment]
    ts = "2026-11-07T10:00:01Z"
    for _ in range(2):
        for _ in range(monitor._error_rate_min_samples):
            monitor.record_downstream_outcome(
                target_name="telemetry", ok=False, status_code=503,
                latency_ms=5.0, observed_at=ts,
            )
        asyncio.run(monitor.flush_outcomes())
    assert len(calls) == 2


def test_stop_waits_for_inflight_writer_and_keeps_accounting(tmp_path):
    monitor = _reg_monitor(tmp_path)
    monitor._pending_max = 3
    ts = "2026-11-07T10:00:01Z"
    entered, release = threading.Event(), threading.Event()
    real = monitor._store.flush_outcomes

    def _slow(**kw):
        entered.set()
        release.wait(5)
        return real(**kw)

    monitor._store.flush_outcomes = _slow  # type: ignore[assignment]
    for _ in range(3):
        monitor.record_downstream_outcome(
            target_name="telemetry", ok=True, status_code=200,
            latency_ms=1.0, observed_at=ts,
        )

    async def _drive() -> None:
        task = asyncio.create_task(monitor.flush_outcomes())
        while not entered.is_set():
            await asyncio.sleep(0.005)
        task.cancel()
        threading.Timer(0.2, release.set).start()
        await monitor.stop()
        assert monitor.pending_outcome_stats()["pending_samples"] == 0

    asyncio.run(_drive())
    assert monitor.pending_outcome_stats()["dropped_samples"] == 0


def _real_shape_telemetry_body_json(event_id: str = "infra-health-00000000000000000000000000000000") -> str:
    """Return an 866-byte infrastructure_health telemetry event JSON matching hosted shape."""
    prefix = (
        '{"component":{"component_id":"telemetry-ingest-svc","component_kind":"http_service",'
        '"FailureBoundary":"edge_bff","health_url":"http://telemetry:8083/__health__"},'
        f'"event_id":"{event_id}","event_type":"infrastructure_health",'
        '"health":{"consecutive_failures":3,"error_rate":0.0,"failure_count":0,'
        '"failure_reason":"HTTP 503","healthy":false,"latency_ms":12.4,"probe_kind":"interval_probe",'
        '"sample_count":0,"status":"degraded","status_code":503,'
        '"window_started_at":"2026-09-02T00:00:00Z"},"incident_ref":null,'
        '"observed_at":"2026-09-02T00:00:05Z","producer":"control-plane-bff",'
        '"schema_version":"pantheon.infrastructure-health/1","tenant_id":"default",'
        '"detail":"'
    )
    suffix = '"}'
    pad_len = 866 - len(prefix) - len(suffix)
    body = prefix + ("x" * max(0, pad_len)) + suffix
    assert len(body) == 866
    return body


def test_prune_indexes_created_idempotently_at_startup(tmp_path):
    import sqlite3

    db_path = str(tmp_path / "legacy_no_prune_idx.sqlite3")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE delivery_outbox (
            delivery_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            target_name TEXT NOT NULL,
            url TEXT NOT NULL,
            body_json TEXT NOT NULL,
            dependency_ids_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL,
            max_attempts INTEGER NOT NULL,
            next_attempt_at REAL NOT NULL,
            claim_owner TEXT,
            claim_token TEXT,
            claim_until REAL,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX delivery_outbox_due_idx
            ON delivery_outbox(status, next_attempt_at, claim_until);
        CREATE INDEX delivery_outbox_event_idx
            ON delivery_outbox(event_id);
        """
    )
    conn.close()

    store = _DurableHealthStore(db_path)
    # Re-run startup initialization to prove idempotency.
    store._initialize()
    store2 = _DurableHealthStore(db_path)
    with store2._connect() as connection:
        index_names = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
    expected = {
        "delivery_outbox_due_idx",
        "delivery_outbox_event_idx",
        "delivery_outbox_prune_idx",
        "delivery_outbox_deps_idx",
        "probe_windows_completed_idx",
        "error_windows_observed_idx",
        "delivery_replay_audit_replayed_idx",
    }
    assert expected.issubset(index_names)


def test_prune_computes_protected_dependencies_in_sql_and_reads_bounded_rows(tmp_path):
    import sqlite3

    body_866 = _real_shape_telemetry_body_json()
    dep1_fmt = '["telemetry:infra-health-%032x"]'

    def _seed_and_measure_steps(db_file: str, row_count: int, batch_size: int = 100) -> int:
        store = _DurableHealthStore(db_file)
        conn = sqlite3.connect(db_file, isolation_level=None)
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("BEGIN")
        conn.execute(
            """
            WITH RECURSIVE seq(i) AS (
                VALUES(1)
                UNION ALL
                SELECT i + 1 FROM seq WHERE i < ?
            )
            INSERT INTO delivery_outbox(
                delivery_id, event_id, channel, target_name, url,
                body_json, dependency_ids_json, status, attempt_count,
                max_attempts, next_attempt_at, claim_owner, claim_token,
                claim_until, last_error, created_at, updated_at
            )
            SELECT
                CASE WHEN (i % 125) = 0 THEN printf('incident-open:infra-health-%032x', i)
                     ELSE printf('telemetry:infra-health-%032x', i) END,
                printf('infra-health-%032x', i),
                CASE WHEN (i % 125) = 0 THEN 'incident_open' ELSE 'telemetry' END,
                'telemetry',
                'http://telemetry:8083/api/v1/telemetry/infrastructure-health',
                ?,
                CASE WHEN (i % 125) = 0 THEN printf(?, i - 1) ELSE '[]' END,
                'dead_letter',
                5, 5, 0.0, NULL, NULL, NULL, 'HTTP 503',
                printf('2026-09-%02dT%02d:%02d:%02dZ', 1 + (i % 28), (i / 3600) % 24, (i / 60) % 60, i % 60),
                printf('2026-09-%02dT%02d:%02d:%02dZ', 1 + (i % 28), (i / 3600) % 24, (i / 60) % 60, i % 60)
            FROM seq
            """,
            (row_count, body_866, dep1_fmt),
        )
        conn.execute("COMMIT")
        conn.close()

        statements: List[str] = []
        vm_steps = 0
        orig_connect = store._connect

        import contextlib

        @contextlib.contextmanager
        def _instrumented_connect(busy_timeout_ms: int = 10000):
            nonlocal vm_steps
            with orig_connect(busy_timeout_ms) as c:
                c.set_trace_callback(statements.append)

                def _progress() -> int:
                    nonlocal vm_steps
                    vm_steps += 1
                    return 0

                c.set_progress_handler(_progress, 100)
                try:
                    yield c
                finally:
                    c.set_progress_handler(None, 0)
                    c.set_trace_callback(None)

        store._connect = _instrumented_connect  # type: ignore[method-assign]
        pruned = store.prune_retained_history(batch_size=batch_size)
        assert pruned["delivery_outbox"] == batch_size
        # Verify Python never selects dependency_ids_json from all non-delivered rows.
        assert not any(
            "WHERE status <> 'delivered'" in stmt and "SELECT dependency_ids_json" in stmt
            for stmt in statements
        )
        assert any("WITH RECURSIVE" in stmt for stmt in statements)
        return vm_steps

    steps_2k = _seed_and_measure_steps(str(tmp_path / "prune_2k.sqlite3"), 2000, batch_size=100)
    steps_20k = _seed_and_measure_steps(str(tmp_path / "prune_20k.sqlite3"), 20000, batch_size=100)
    # 10x more non-delivered rows must not cause O(N) VM work; bounded index scan stays nearly constant.
    assert steps_20k <= max(steps_2k * 2, steps_2k + 50), (steps_2k, steps_20k)


def test_dead_letter_and_quarantined_retention_and_replay_within_retention(tmp_path):
    from datetime import datetime, timezone
    from services.control_plane.bff.downstream_health_monitor import (
        _DELIVERY_HISTORY_RETENTION_SECONDS,
        _WINDOW_HISTORY_RETENTION_SECONDS,
    )

    assert _DELIVERY_HISTORY_RETENTION_SECONDS == 7 * 24 * 60 * 60
    assert _WINDOW_HISTORY_RETENTION_SECONDS == 24 * 60 * 60

    monitor = DownstreamHealthMonitor(
        telemetry_url="http://telemetry:8083",
        incidents_url="http://incidents:8084",
        probe_interval_seconds=9999,
        state_path=str(tmp_path / "dlq_retention.sqlite3"),
        telemetry_service_jwt="jwt-test-token",
        incident_service_token="inc-test-token",
    )
    store = monitor._store
    now_epoch = time.time()

    def _iso_ago(seconds: float) -> str:
        return (
            datetime.fromtimestamp(now_epoch - seconds, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    old_ts = _iso_ago(8 * 24 * 3600)  # 8 days ago (> 7d retention)
    recent_2d_ts = _iso_ago(2 * 24 * 3600)  # 2 days ago (< 7d retention)
    recent_1h_ts = _iso_ago(3600)  # 1 hour ago (< 6h delivery age & < 7d retention)
    old_win_ts = _iso_ago(25 * 3600)  # 25 hours ago (> 24h window retention)
    recent_win_ts = _iso_ago(2 * 3600)  # 2 hours ago (< 24h window retention)

    body_dict = json.loads(_real_shape_telemetry_body_json())
    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for idx in range(25):
            for status in ("dead_letter", "quarantined", "delivered"):
                did = f"{status}-old-{idx:02d}"
                ev_id = f"ev-{status}-old-{idx:02d}"
                b = dict(body_dict, event_id=ev_id)
                conn.execute(
                    """
                    INSERT INTO delivery_outbox(
                        delivery_id, event_id, channel, target_name, url,
                        body_json, dependency_ids_json, status, attempt_count,
                        max_attempts, next_attempt_at, claim_owner, claim_token,
                        claim_until, last_error, created_at, updated_at
                    ) VALUES (?, ?, 'telemetry', 'telemetry',
                              'http://telemetry:8083/api/v1/telemetry/infrastructure-health',
                              ?, '[]', ?, 5, 5, 0.0, NULL, NULL, NULL, 'err', ?, ?)
                    """,
                    (did, ev_id, json.dumps(b), status, old_ts, old_ts),
                )
        for status, ts, ev_id in (
            ("dead_letter", recent_1h_ts, "ev-dlq-1h"),
            ("dead_letter", recent_2d_ts, "ev-dlq-2d"),
            ("quarantined", recent_2d_ts, "ev-quar-2d"),
            ("delivered", recent_2d_ts, "ev-deliv-2d"),
        ):
            b = dict(body_dict, event_id=ev_id)
            conn.execute(
                """
                INSERT INTO delivery_outbox(
                    delivery_id, event_id, channel, target_name, url,
                    body_json, dependency_ids_json, status, attempt_count,
                    max_attempts, next_attempt_at, claim_owner, claim_token,
                    claim_until, last_error, created_at, updated_at
                ) VALUES (?, ?, 'telemetry', 'telemetry',
                          'http://telemetry:8083/api/v1/telemetry/infrastructure-health',
                          ?, '[]', ?, 5, 5, 0.0, NULL, NULL, NULL, 'HTTP 503', ?, ?)
                """,
                (f"telemetry:{ev_id}", ev_id, json.dumps(b), status, ts, ts),
            )
        conn.execute(
            """
            INSERT INTO probe_windows(
                target_name, probe_kind, window_started_at, owner_id, lease_until, completed_at
            ) VALUES
                ('telemetry', 'interval_probe', ?, 'inst-1', 0.0, ?),
                ('telemetry', 'interval_probe', ?, 'inst-1', 0.0, ?)
            """,
            (old_win_ts, old_win_ts, recent_win_ts, recent_win_ts),
        )
        conn.execute(
            """
            INSERT INTO error_windows(
                target_name, window_started_at, sample_count, failure_count,
                latency_total_ms, last_status_code, last_detail, last_observed_at
            ) VALUES
                ('telemetry', ?, 5, 5, 50.0, 503, 'old', ?),
                ('telemetry', ?, 5, 0, 10.0, 200, NULL, ?)
            """,
            (old_win_ts, old_win_ts, recent_win_ts, recent_win_ts),
        )
        conn.commit()

    # Bounded batch deletion: 75 old outbox rows (25 dead_letter + 25 quarantined + 25 delivered)
    b1 = store.prune_retained_history(batch_size=30)
    assert b1["delivery_outbox"] == 30
    assert b1["probe_windows"] == 1
    assert b1["error_windows"] == 1

    b2 = store.prune_retained_history(batch_size=30)
    assert b2["delivery_outbox"] == 30

    b3 = store.prune_retained_history(batch_size=30)
    assert b3["delivery_outbox"] == 15

    b4 = store.prune_retained_history(batch_size=30)
    assert b4["delivery_outbox"] == 0

    counts = store.delivery_counts()
    assert counts["dead_letter"] == 2
    assert counts["quarantined"] == 1
    assert counts["delivered"] == 1

    # Dead letters inside the 7-day retention can still be replayed and delivered.
    with patch(
        "services.control_plane.bff.downstream_health_monitor._post_json",
        return_value=(True, 200),
    ):
        replay_recent = monitor.replay_dead_letters(
            actor_id="op-1",
            approval_ref="apr-1",
            reason="replay recent dead letter inside age bound",
        )
        assert replay_recent["replayed"] == 1
        assert replay_recent["delivered_now"] == 1

        replay_2d = monitor.replay_dead_letters(
            actor_id="op-1",
            approval_ref="apr-2",
            reason="replay 2-day-old dead letter inside 7d retention by event_id",
            event_id="ev-dlq-2d",
        )
        assert replay_2d["replayed"] == 1
        assert replay_2d["delivered_now"] == 1

    final_counts = store.delivery_counts()
    assert final_counts["dead_letter"] == 0
    assert final_counts["delivered"] == 3


def test_prune_protects_dependencies_of_retained_rows_and_active_incidents(tmp_path):
    store = _DurableHealthStore(str(tmp_path / "protected_deps.sqlite3"))
    old_ts = "2026-09-02T00:00:00Z"
    recent_ts = _utc_now_rfc3339()
    body = json.loads(_real_shape_telemetry_body_json())

    def _insert_outbox(
        conn,
        delivery_id: str,
        event_id: str,
        channel: str,
        target_name: str,
        status: str,
        updated_at: str,
        deps: List[str],
    ) -> None:
        conn.execute(
            """
            INSERT INTO delivery_outbox(
                delivery_id, event_id, channel, target_name, url,
                body_json, dependency_ids_json, status, attempt_count,
                max_attempts, next_attempt_at, claim_owner, claim_token,
                claim_until, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'http://telemetry:8083/api/v1/telemetry/infrastructure-health',
                      ?, ?, ?, 1, 5, 0.0, NULL, NULL, NULL, NULL, ?, ?)
            """,
            (
                delivery_id,
                event_id,
                channel,
                target_name,
                json.dumps(dict(body, event_id=event_id)),
                json.dumps(deps),
                status,
                updated_at,
                updated_at,
            ),
        )

    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # 1. Dependency chain referenced by a retained pending row:
        #    pending-row -> dep-of-pending (old delivered) -> transitive-dep-of-pending (old dead_letter)
        _insert_outbox(conn, "transitive-dep-of-pending", "ev-trans", "telemetry", "t1", "dead_letter", old_ts, [])
        _insert_outbox(conn, "dep-of-pending", "ev-dep-pend", "incident_open", "t1", "delivered", old_ts, ["transitive-dep-of-pending"])
        _insert_outbox(conn, "retained-pending", "ev-pend", "incident_resolve", "t1", "pending", old_ts, ["dep-of-pending"])

        # 2. Old row referenced by a retained recent (< 7d) dead_letter row:
        _insert_outbox(conn, "dep-of-recent-dlq", "ev-dep-dlq", "telemetry", "t2", "delivered", old_ts, [])
        _insert_outbox(conn, "recent-dlq", "ev-rec-dlq", "incident_open", "t2", "dead_letter", recent_ts, ["dep-of-recent-dlq"])

        # 3. Old row referenced by a retained recent (< 7d) delivered row:
        _insert_outbox(conn, "dep-of-recent-delivered", "ev-dep-deliv", "telemetry", "t3", "quarantined", old_ts, [])
        _insert_outbox(conn, "recent-delivered", "ev-rec-deliv", "incident_open", "t3", "delivered", recent_ts, ["dep-of-recent-delivered"])

        # 4. Rows tied to active incidents in opening, open, and resolving states:
        _insert_outbox(conn, "telemetry:ev-opening", "ev-opening", "telemetry", "t-opening", "delivered", old_ts, [])
        _insert_outbox(conn, "incident-open:ev-opening", "ev-opening", "incident_open", "t-opening", "dead_letter", old_ts, ["telemetry:ev-opening"])
        conn.execute(
            """
            INSERT INTO incident_mappings(
                target_name, incident_id, status, opening_event_id,
                create_delivery_id, last_event_id, updated_at
            ) VALUES ('t-opening', 'inc-opening', 'opening', 'ev-opening', 'incident-open:ev-opening', 'ev-opening', ?)
            """,
            (old_ts,),
        )

        _insert_outbox(conn, "incident-open:ev-open", "ev-open", "incident_open", "t-open", "delivered", old_ts, [])
        conn.execute(
            """
            INSERT INTO incident_mappings(
                target_name, incident_id, status, opening_event_id,
                create_delivery_id, last_event_id, updated_at
            ) VALUES ('t-open', 'inc-open', 'open', 'ev-open', 'incident-open:ev-open', 'ev-open', ?)
            """,
            (old_ts,),
        )

        _insert_outbox(conn, "incident-open:ev-resolving", "ev-resolving-open", "incident_open", "t-resolving", "delivered", old_ts, [])
        _insert_outbox(conn, "incident-resolve:ev-resolving-last", "ev-resolving-last", "incident_resolve", "t-resolving", "dead_letter", old_ts, ["incident-open:ev-resolving"])
        conn.execute(
            """
            INSERT INTO incident_mappings(
                target_name, incident_id, status, opening_event_id,
                create_delivery_id, last_event_id, updated_at
            ) VALUES ('t-resolving', 'inc-resolving', 'resolving', 'ev-resolving-open', 'incident-open:ev-resolving', 'ev-resolving-last', ?)
            """,
            (old_ts,),
        )

        # 5. Unprotected old rows (including one tied to an already-resolved incident):
        _insert_outbox(conn, "incident-open:ev-resolved", "ev-resolved", "incident_open", "t-resolved", "delivered", old_ts, [])
        conn.execute(
            """
            INSERT INTO incident_mappings(
                target_name, incident_id, status, opening_event_id,
                create_delivery_id, last_event_id, updated_at
            ) VALUES ('t-resolved', 'inc-resolved', 'resolved', 'ev-resolved', 'incident-open:ev-resolved', 'ev-resolved', ?)
            """,
            (old_ts,),
        )
        _insert_outbox(conn, "unreferenced-old-dlq", "ev-unref-1", "telemetry", "t-unref", "dead_letter", old_ts, [])
        _insert_outbox(conn, "unreferenced-old-quar", "ev-unref-2", "telemetry", "t-unref", "quarantined", old_ts, [])
        conn.commit()

    pruned = store.prune_retained_history()
    assert pruned["delivery_outbox"] == 3

    remaining_ids = {row["delivery_id"] for row in store.list_deliveries()}
    expected_protected = {
        "transitive-dep-of-pending",
        "dep-of-pending",
        "retained-pending",
        "dep-of-recent-dlq",
        "recent-dlq",
        "dep-of-recent-delivered",
        "recent-delivered",
        "telemetry:ev-opening",
        "incident-open:ev-opening",
        "incident-open:ev-open",
        "incident-open:ev-resolving",
        "incident-resolve:ev-resolving-last",
    }
    assert expected_protected.issubset(remaining_ids)
    assert "incident-open:ev-resolved" not in remaining_ids
    assert "unreferenced-old-dlq" not in remaining_ids
    assert "unreferenced-old-quar" not in remaining_ids

    # Once active incidents transition to resolved, their old rows are eligible for prune.
    with store._connect() as conn:
        conn.execute("UPDATE incident_mappings SET status='resolved'")
    pruned_after_resolve = store.prune_retained_history()
    assert pruned_after_resolve["delivery_outbox"] == 5
    remaining_after_resolve = {row["delivery_id"] for row in store.list_deliveries()}
    for incident_row_id in (
        "telemetry:ev-opening",
        "incident-open:ev-opening",
        "incident-open:ev-open",
        "incident-open:ev-resolving",
        "incident-resolve:ev-resolving-last",
    ):
        assert incident_row_id not in remaining_after_resolve
    # Dependencies of retained rows remain protected.
    for dep_id in (
        "transitive-dep-of-pending",
        "dep-of-pending",
        "dep-of-recent-dlq",
        "dep-of-recent-delivered",
    ):
        assert dep_id in remaining_after_resolve


def test_get_state_accelerates_prune_cadence_while_backlog_batch_is_full(tmp_path):
    from services.control_plane.bff.downstream_health_monitor import _DELIVERY_PRUNE_BATCH_SIZE

    monitor = DownstreamHealthMonitor(
        telemetry_url="http://telemetry:8083",
        probe_interval_seconds=30.0,
        state_path=str(tmp_path / "cadence.sqlite3"),
    )
    with patch.object(
        monitor._store,
        "prune_retained_history",
        return_value={
            "delivery_outbox": _DELIVERY_PRUNE_BATCH_SIZE,
            "probe_windows": 0,
            "error_windows": 0,
            "delivery_replay_audit": 0,
        },
    ):
        monitor.get_state()
        # Next prune should be eligible after 30s (one probe interval) instead of 300s.
        assert 260.0 <= (time.time() - monitor._last_retention_prune_at) <= 275.0

    monitor._last_retention_prune_at = 0.0
    with patch.object(
        monitor._store,
        "prune_retained_history",
        return_value={
            "delivery_outbox": 10,
            "probe_windows": 0,
            "error_windows": 0,
            "delivery_replay_audit": 0,
        },
    ):
        monitor.get_state()
        assert (time.time() - monitor._last_retention_prune_at) < 5.0

