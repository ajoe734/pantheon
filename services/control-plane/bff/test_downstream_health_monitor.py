"""L12-MFC-R4-BFF-HEALTH-001: Typed exact BFF health targets and durable incident receipts test suite.

Verifies:
1. invalid target config fails fast (malformed JSON, bad scheme, bad target name, bad health_path)
2. exact health path used (no fallback probing; explicit health_path / env overrides used)
3. failure creates telemetry then incident (outbox dependency ordering & receipts)
4. recovery read back (recovery probe clears open incident tracking & updates read model)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from downstream_health_monitor import (
    DownstreamHealthMonitor,
    DownstreamProbeResult,
    DownstreamTarget,
)


def _run(coro):
    """Run a coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestInvalidTargetConfigFailsFast:
    """Acceptance criterion 1: invalid target config fails fast."""

    def test_downstream_target_validates_name(self):
        with pytest.raises(ValueError, match="invalid health target name"):
            DownstreamTarget(name="INVALID NAME!", base_url="http://localhost:8080")

    def test_downstream_target_validates_base_url(self):
        with pytest.raises(ValueError, match="must use http\\(s\\)"):
            DownstreamTarget(name="test-target", base_url="ftp://localhost:8080")

    def test_downstream_target_validates_health_path(self):
        with pytest.raises(ValueError, match="health_path must start with '/'"):
            DownstreamTarget(name="test-target", base_url="http://localhost:8080", health_path="health")

    def test_downstream_target_validates_component_kind(self):
        with pytest.raises(ValueError, match="component_kind cannot be empty"):
            DownstreamTarget(name="test-target", base_url="http://localhost:8080", component_kind="")

    def test_configured_target_json_malformed_json_fails_fast(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_BFF_HEALTH_TARGETS_JSON", "{invalid json")
        monitor = DownstreamHealthMonitor(probe_interval_seconds=9999)
        with pytest.raises(ValueError, match="must be valid JSON"):
            monitor._configured_target_json()

    def test_configured_target_json_non_dict_non_list_fails_fast(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_BFF_HEALTH_TARGETS_JSON", "12345")
        monitor = DownstreamHealthMonitor(probe_interval_seconds=9999)
        with pytest.raises(ValueError, match="must be an object or list"):
            monitor._configured_target_json()

    def test_configured_target_json_list_with_non_dict_fails_fast(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_BFF_HEALTH_TARGETS_JSON", '["invalid"]')
        monitor = DownstreamHealthMonitor(probe_interval_seconds=9999)
        with pytest.raises(ValueError, match="list entries must be objects"):
            monitor._configured_target_json()

    def test_configured_target_json_invalid_entry_name_fails_fast(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_BFF_HEALTH_TARGETS_JSON", '{"BAD NAME": "http://localhost:8000"}')
        monitor = DownstreamHealthMonitor(probe_interval_seconds=9999)
        with pytest.raises(ValueError, match="invalid health target name"):
            monitor._configured_target_json()

    def test_configured_target_json_invalid_entry_url_fails_fast(self, monkeypatch):
        monkeypatch.setenv(
            "PANTHEON_BFF_HEALTH_TARGETS_JSON",
            json.dumps({"my-svc": {"url": "gopher://localhost:8000"}}),
        )
        monitor = DownstreamHealthMonitor(probe_interval_seconds=9999)
        with pytest.raises(ValueError, match="must use http\\(s\\)"):
            monitor._configured_target_json()

    def test_configured_target_json_invalid_health_path_fails_fast(self, monkeypatch):
        monkeypatch.setenv(
            "PANTHEON_BFF_HEALTH_TARGETS_JSON",
            json.dumps({"my-svc": {"url": "http://localhost:8000", "health_path": "no_leading_slash"}}),
        )
        monitor = DownstreamHealthMonitor(probe_interval_seconds=9999)
        with pytest.raises(ValueError, match="health_path must start with '/'"):
            monitor._configured_target_json()


class TestExactHealthPathUsed:
    """Acceptance criterion 2: exact health path used (no guessing or fallback probing)."""

    def test_exact_health_url_construction(self):
        target1 = DownstreamTarget(name="svc-a", base_url="http://svc-a:8080", health_path="/health")
        assert target1.health_url == "http://svc-a:8080/health"

        target2 = DownstreamTarget(name="svc-b", base_url="https://svc-b:8443/", health_path="/api/v1/health")
        assert target2.health_url == "https://svc-b:8443/api/v1/health"

    def test_configured_target_json_preserves_custom_health_path(self, monkeypatch):
        monkeypatch.setenv(
            "PANTHEON_BFF_HEALTH_TARGETS_JSON",
            json.dumps({"custom-svc": {"url": "http://custom:9000", "health_path": "/healthz"}}),
        )
        monitor = DownstreamHealthMonitor(probe_interval_seconds=9999)
        targets = monitor._resolve_target_registry()
        assert "custom-svc" in targets
        assert targets["custom-svc"].health_path == "/healthz"
        assert targets["custom-svc"].health_url == "http://custom:9000/healthz"

    def test_env_specific_health_path_override(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", "http://rt:8080")
        monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL_HEALTH_PATH", "/health")
        monitor = DownstreamHealthMonitor(probe_interval_seconds=9999)
        targets = monitor._resolve_target_registry()
        assert "runtime-manager" in targets
        assert targets["runtime-manager"].health_path == "/health"
        assert targets["runtime-manager"].health_url == "http://rt:8080/health"

    def test_probe_one_hits_exact_health_url(self):
        monitor = DownstreamHealthMonitor(probe_interval_seconds=9999)
        probe_urls: List[str] = []

        def mock_probe_http(url, timeout):
            probe_urls.append(url)
            return True, 200, ""

        with patch("downstream_health_monitor._probe_http", side_effect=mock_probe_http):
            res = _run(monitor._probe_one("my-svc", "http://my-svc:8080", health_path="/custom/health"))

        assert res.ok is True
        assert len(probe_urls) == 1
        # Exact path used without any probing fallback
        assert probe_urls[0] == "http://my-svc:8080/custom/health"


class TestFailureCreatesTelemetryThenIncident:
    """Acceptance criterion 3: failure creates telemetry then incident outbox delivery."""

    def test_failure_queues_telemetry_then_incident(self):
        monitor = DownstreamHealthMonitor(
            telemetry_url="http://tel:8080",
            incidents_url="http://inc:8090",
            telemetry_service_jwt="jwt-token",
            tenant_id="tenant-test",
            probe_interval_seconds=9999,
            failure_threshold=2,
        )
        failed_res = DownstreamProbeResult(
            target_name="telemetry",
            ok=False,
            status_code=503,
            latency_ms=10.0,
            checked_at="2026-08-13T12:00:00Z",
            failure_reason="HTTP 503",
            consecutive_failures=2,
        )

        delivery_log: List[Dict[str, Any]] = []

        def mock_post_json(url, body, timeout):
            delivery_log.append({"url": url, "body": body})
            return True, 201

        with patch("downstream_health_monitor._post_json", side_effect=mock_post_json):
            _run(monitor._handle_probe_result(failed_res))

        assert len(delivery_log) == 2
        # First delivery MUST be telemetry
        assert "/api/v1/telemetry/infrastructure-health" in delivery_log[0]["url"]
        assert delivery_log[0]["body"]["event_type"] == "infrastructure_health"
        # Second delivery MUST be incident open
        assert "/api/incidents/consume-infrastructure-health" in delivery_log[1]["url"]
        assert delivery_log[1]["body"]["status"] == "open"

        # Shared source event ID check
        tel_event_id = delivery_log[0]["body"]["event_id"]
        inc_source_event_id = delivery_log[1]["body"]["source_event_id"]
        assert tel_event_id == inc_source_event_id


class TestRecoveryReadBack:
    """Acceptance criterion 4: recovery read back updates telemetry, incident, and read model."""

    def test_recovery_clears_incident_and_updates_state(self):
        monitor = DownstreamHealthMonitor(
            telemetry_url="http://tel:8080",
            incidents_url="http://inc:8090",
            telemetry_service_jwt="jwt-token",
            tenant_id="tenant-test",
            probe_interval_seconds=9999,
            failure_threshold=2,
        )

        # 1. Trigger failure to open incident
        failed_res = DownstreamProbeResult(
            target_name="telemetry",
            ok=False,
            status_code=503,
            latency_ms=10.0,
            checked_at="2026-08-13T12:00:00Z",
            failure_reason="HTTP 503",
            consecutive_failures=2,
        )
        with patch("downstream_health_monitor._post_json", return_value=(True, 201)):
            _run(monitor._handle_probe_result(failed_res))

        assert "telemetry" in monitor._open_incident_ids

        # 2. Trigger recovery
        ok_res = DownstreamProbeResult(
            target_name="telemetry",
            ok=True,
            status_code=200,
            latency_ms=5.0,
            checked_at="2026-08-13T12:01:00Z",
        )
        post_calls: List[Dict[str, Any]] = []

        def mock_post_json(url, body, timeout):
            post_calls.append({"url": url, "body": body})
            return True, 200

        with patch("downstream_health_monitor._post_json", side_effect=mock_post_json):
            _run(monitor._handle_probe_result(ok_res))

        # 3. Verify open incident is cleared after resolve delivery
        assert "telemetry" not in monitor._open_incident_ids

        # 4. Verify readback model
        state = monitor.get_state()
        assert state["targets"]["telemetry"]["ok"] is True
        assert state["targets"]["telemetry"]["consecutive_failures"] == 0
        assert state["overall_ok"] is True
        assert state["incidents"]["telemetry"]["status"] == "resolved"
