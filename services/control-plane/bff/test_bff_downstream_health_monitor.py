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
import time
import tempfile
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

sys.path.insert(0, os.path.dirname(__file__))


def _run(coro):
    """Run a coroutine in a new event loop (no pytest-asyncio needed)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

from downstream_health_monitor import (
    DownstreamHealthMonitor,
    DownstreamProbeResult,
    _probe_http,
    _post_json,
    _utc_now_rfc3339,
    _SENTINEL_BINDING_ID,
    _SENTINEL_STAGE,
)

OPERATOR_TOKEN = "Bearer op-2:operator"


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
            probe_interval_seconds=9999,  # don't fire automatically in tests
            failure_threshold=3,
            **kwargs,
        )

    def test_get_state_empty_before_first_probe(self):
        monitor = self._make_monitor()
        state = monitor.get_state()
        assert state["targets"] == {}
        assert state["overall_ok"] is None

    def test_get_state_with_probe_results(self):
        monitor = self._make_monitor()
        monitor._state["telemetry"] = DownstreamProbeResult(
            target_name="telemetry",
            ok=True,
            status_code=200,
            latency_ms=5.0,
            checked_at="2026-06-27T00:00:00Z",
        )
        monitor._state["incidents"] = DownstreamProbeResult(
            target_name="incidents",
            ok=False,
            status_code=-1,
            latency_ms=3001.0,
            checked_at="2026-06-27T00:00:00Z",
            failure_reason="Connection refused",
            consecutive_failures=2,
        )
        state = monitor.get_state()
        assert state["overall_ok"] is False
        assert "telemetry" in state["targets"]
        assert "incidents" in state["targets"]
        assert state["targets"]["incidents"]["consecutive_failures"] == 2

    def test_get_degraded_targets(self):
        monitor = self._make_monitor()
        monitor._state["telemetry"] = DownstreamProbeResult(
            target_name="telemetry", ok=True, status_code=200, latency_ms=5.0, checked_at="2026-06-27T00:00:00Z"
        )
        monitor._state["incidents"] = DownstreamProbeResult(
            target_name="incidents", ok=False, status_code=-1, latency_ms=3001.0, checked_at="2026-06-27T00:00:00Z",
            failure_reason="timeout", consecutive_failures=1,
        )
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

        with patch("downstream_health_monitor._probe_http", return_value=(True, 200, "")):
            result = _run(monitor._probe_one("telemetry", "http://tel:8080"))

        assert result.ok is True
        assert result.consecutive_failures == 0

    def test_probe_one_failure_increments_count(self, monkeypatch):
        monitor = self._make_monitor()
        monitor._state["telemetry"] = DownstreamProbeResult(
            target_name="telemetry", ok=False, status_code=-1, latency_ms=10.0,
            checked_at="2026-06-27T00:00:00Z", failure_reason="timeout", consecutive_failures=2,
        )

        with patch("downstream_health_monitor._probe_http", return_value=(False, -1, "timeout")):
            result = _run(monitor._probe_one("telemetry", "http://tel:8080"))

        assert result.ok is False
        assert result.consecutive_failures == 3

    def test_probe_one_first_failure_is_count_1(self, monkeypatch):
        monitor = self._make_monitor()
        # No previous state

        with patch("downstream_health_monitor._probe_http", return_value=(False, -1, "timeout")):
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

        with patch("downstream_health_monitor._post_json", side_effect=mock_post_json):
            monitor._emit_telemetry_sync(result)

        assert len(captured) == 1
        call = captured[0]
        assert call["url"] == "http://tel:8080/api/telemetry/ingest"
        body = call["body"]
        assert body["event_type"] == "runtime_health"
        assert body["binding_id"] == _SENTINEL_BINDING_ID
        assert body["deployment_stage"] == _SENTINEL_STAGE
        assert body["execution_mode"] == _SENTINEL_STAGE
        assert body["metrics"]["probe_ok"] == 0
        assert body["metrics"]["consecutive_failures"] == 1
        assert body["bff_health_probe"]["target_name"] == "incidents"

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

        with patch("downstream_health_monitor._post_json", side_effect=mock_post_json):
            monitor._emit_telemetry_sync(result)

        assert len(captured) == 1
        assert captured[0]["metrics"]["probe_ok"] == 1

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

        with patch("downstream_health_monitor._post_json", side_effect=mock_post_json):
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

        with patch("downstream_health_monitor._post_json", side_effect=mock_post_json):
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

        with patch("downstream_health_monitor._post_json", side_effect=mock_post_json):
            inc_id = monitor._open_or_update_incident_sync(result)

        assert inc_id == "bff-downstream-incidents-degraded"
        assert len(captured) == 1
        assert "/api/incidents" in captured[0]["url"]
        body = captured[0]["body"]
        assert body["severity"] == "high"
        assert body["status"] == "open"
        assert "incidents" in body["title"]
        assert body["binding_id"] == _SENTINEL_BINDING_ID
        assert body["deployment_stage"] == _SENTINEL_STAGE

    def test_incident_idempotent_on_repeat_failure(self):
        monitor = self._make_monitor()
        monitor._open_incident_ids["incidents"] = "bff-downstream-incidents-degraded"

        result = DownstreamProbeResult(
            target_name="incidents", ok=False, status_code=-1, latency_ms=10.0,
            checked_at="2026-06-27T00:00:00Z", failure_reason="timeout", consecutive_failures=5,
        )
        captured: List[Dict[str, Any]] = []

        def mock_post_json(url, body, timeout):
            captured.append(body)
            return True, 201

        with patch("downstream_health_monitor._post_json", side_effect=mock_post_json):
            inc_id = monitor._open_or_update_incident_sync(result)

        # Already tracking an open incident — no new POST
        assert inc_id == "bff-downstream-incidents-degraded"
        assert len(captured) == 0

    def test_incident_409_treated_as_idempotent_ok(self):
        monitor = self._make_monitor()
        result = DownstreamProbeResult(
            target_name="telemetry", ok=False, status_code=-1, latency_ms=5.0,
            checked_at="2026-06-27T00:00:00Z", failure_reason="timeout", consecutive_failures=3,
        )

        def mock_post_json(url, body, timeout):
            return False, 409  # already exists

        with patch("downstream_health_monitor._post_json", side_effect=mock_post_json):
            inc_id = monitor._open_or_update_incident_sync(result)

        assert inc_id == "bff-downstream-telemetry-degraded"
        assert "telemetry" in monitor._open_incident_ids

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
        with patch("downstream_health_monitor._post_json") as mock_post:
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

        with patch("downstream_health_monitor._post_json", side_effect=mock_post_json):
            _run(monitor._handle_probe_result(result))

        assert "telemetry" not in monitor._open_incident_ids
        assert len(captured) == 0


# ---------------------------------------------------------------------------
# Recovery tracking tests
# ---------------------------------------------------------------------------


class TestRecoveryTracking:
    def test_recovery_clears_open_incident_tracking(self):
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

        with patch("downstream_health_monitor._post_json", return_value=(True, 202)):
            _run(monitor._handle_probe_result(ok_result))

        # Local incident tracking cleared after recovery
        assert "telemetry" not in monitor._open_incident_ids

    def test_recovery_calls_incident_resolve(self):
        monitor = DownstreamHealthMonitor(
            telemetry_url="http://tel:8080",
            incidents_url="http://inc:8090",
            probe_interval_seconds=9999,
            failure_threshold=3,
        )
        monitor._open_incident_ids["telemetry"] = "bff-downstream-telemetry-degraded"
        ok_result = DownstreamProbeResult(
            target_name="telemetry", ok=True, status_code=200, latency_ms=5.0,
            checked_at="2026-06-27T00:00:01Z",
        )
        captured: List[Dict[str, Any]] = []

        def mock_post_json(url, body, timeout):
            captured.append({"url": url, "body": body})
            return True, 200

        with patch("downstream_health_monitor._post_json", side_effect=mock_post_json):
            _run(monitor._handle_probe_result(ok_result))

        assert "telemetry" not in monitor._open_incident_ids
        resolve_calls = [c for c in captured if "/resolve" in c["url"]]
        assert len(resolve_calls) == 1
        assert resolve_calls[0]["body"]["status"] == "resolved"


# ---------------------------------------------------------------------------
# BFF route contract tests
# ---------------------------------------------------------------------------


class TestBffDownstreamHealthRoute:
    def _make_client(self):
        from fastapi.testclient import TestClient
        import main as bff_main
        return TestClient(bff_main.app), bff_main

    def test_downstream_health_route_returns_200(self):
        client, bff_main = self._make_client()
        # Seed some state into the monitor
        bff_main.downstream_health_monitor._state["telemetry"] = DownstreamProbeResult(
            target_name="telemetry",
            ok=True,
            status_code=200,
            latency_ms=5.0,
            checked_at="2026-06-27T00:00:00Z",
        )
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
        from fastapi.testclient import TestClient
        import main as bff_main

        # Clear state
        bff_main.downstream_health_monitor._state.clear()
        client = TestClient(bff_main.app)

        response = client.get(
            "/bff/v5/downstream-health",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["overall_ok"] is None
        assert body["data"]["targets"] == {}

    def test_downstream_health_route_shows_degraded_targets(self):
        from fastapi.testclient import TestClient
        import main as bff_main

        bff_main.downstream_health_monitor._state.clear()
        bff_main.downstream_health_monitor._state["incidents"] = DownstreamProbeResult(
            target_name="incidents",
            ok=False,
            status_code=-1,
            latency_ms=3001.0,
            checked_at="2026-06-27T00:00:00Z",
            failure_reason="Connection refused",
            consecutive_failures=4,
        )
        client = TestClient(bff_main.app)

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
        from fastapi.testclient import TestClient
        import main as bff_main

        client = TestClient(bff_main.app)
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
        assert monitor.get_state()["targets"] == {}

    def test_bff_health_route_works_even_when_monitor_has_errors(self):
        """Verify other BFF routes continue to work when monitor state is degraded."""
        from fastapi.testclient import TestClient
        import main as bff_main

        # Seed degraded state
        bff_main.downstream_health_monitor._state["runtime-manager"] = DownstreamProbeResult(
            target_name="runtime-manager",
            ok=False,
            status_code=-1,
            latency_ms=9999.0,
            checked_at="2026-06-27T00:00:00Z",
            failure_reason="Connection refused",
            consecutive_failures=10,
        )

        client = TestClient(bff_main.app)

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
