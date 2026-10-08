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


# ---------------------------------------------------------------------------
# BFF-DOWNSTREAM-MONITOR-RESTART-20261008: queue safety before lifespan start.
# Each class below reproduces one hazard that made restarting the monitor
# unsafe after c1894c3b0 removed it from the BFF startup hooks.
# ---------------------------------------------------------------------------

import importlib
import sqlite3
import threading
import types
from datetime import datetime, timedelta, timezone


def _safety_monitor(tmp_path, **overrides):
    kwargs = dict(
        telemetry_url="http://tel:8080",
        incidents_url="http://inc:8090",
        telemetry_service_jwt="jwt-token",
        tenant_id="tenant-test",
        probe_interval_seconds=60,
        failure_threshold=2,
        state_path=str(tmp_path / "downstream_health.sqlite3"),
    )
    kwargs.update(overrides)
    return DownstreamHealthMonitor(**kwargs)


def _rfc3339(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _set_rows(monitor, delivery_ids, **columns):
    assignments = ", ".join(f"{name}=?" for name in columns)
    with sqlite3.connect(monitor.state_path) as connection:
        connection.executemany(
            f"UPDATE delivery_outbox SET {assignments} WHERE delivery_id=?",
            [(*columns.values(), delivery_id) for delivery_id in delivery_ids],
        )


def _age_rows(monitor, delivery_ids, *, seconds):
    # Never-attempted rows keep updated_at equal to created_at.
    created = _rfc3339(datetime.now(timezone.utc) - timedelta(seconds=seconds))
    _set_rows(monitor, delivery_ids, created_at=created, updated_at=created, next_attempt_at=0)


def _delivery(monitor, delivery_id):
    rows = [
        item
        for item in monitor.list_delivery_records()
        if item["delivery_id"] == delivery_id
    ]
    return rows[0] if rows else None


def _queue_telemetry(monitor, event_id, *, dependencies=()):
    return monitor._store.queue_delivery(
        delivery_id=f"telemetry:{event_id}",
        event_id=event_id,
        channel="telemetry",
        target_name="svc-a",
        url="http://tel:8080/api/v1/telemetry/infrastructure-health",
        body={"event_id": event_id},
        dependency_ids=list(dependencies),
        max_attempts=5,
    )


class TestDeliveryAgeBound:
    """Rows older than the delivery age bound are quarantined, not delivered as current."""

    def test_stale_pending_row_is_quarantined_instead_of_delivered(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PANTHEON_BFF_HEALTH_DELIVERY_MAX_AGE_SECONDS", "3600")
        monitor = _safety_monitor(tmp_path)
        _queue_telemetry(monitor, "stale-event")
        _age_rows(monitor, ["telemetry:stale-event"], seconds=2 * 86400)

        posted: List[str] = []
        with patch(
            "downstream_health_monitor._post_json",
            side_effect=lambda url, body, timeout: posted.append(body["event_id"]) or (True, 201),
        ):
            monitor._deliver_due_sync()

        assert posted == []
        assert _delivery(monitor, "telemetry:stale-event")["status"] == "quarantined"
        delivery = monitor.get_state()["delivery"]
        assert delivery["quarantined"] == 1
        assert delivery["backlog"] == 0

    def test_fresh_row_is_still_delivered(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PANTHEON_BFF_HEALTH_DELIVERY_MAX_AGE_SECONDS", "3600")
        monitor = _safety_monitor(tmp_path)
        _queue_telemetry(monitor, "fresh-event")
        with patch("downstream_health_monitor._post_json", return_value=(True, 201)):
            monitor._deliver_due_sync()
        assert _delivery(monitor, "telemetry:fresh-event")["status"] == "delivered"

    def test_quarantined_incident_open_releases_the_mapping_for_a_new_incident(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("PANTHEON_BFF_HEALTH_DELIVERY_MAX_AGE_SECONDS", "3600")
        monitor = _safety_monitor(tmp_path)
        old_failure = DownstreamProbeResult(
            target_name="svc-a",
            ok=False,
            status_code=503,
            latency_ms=10.0,
            checked_at="2026-09-04T12:00:00Z",
            failure_reason="HTTP 503",
            consecutive_failures=2,
        )
        with patch("downstream_health_monitor._post_json", return_value=(False, 503)):
            _run(monitor._handle_probe_result(old_failure))
        old_incident = monitor._store.get_incident("svc-a")
        stale_ids = [
            item["delivery_id"]
            for item in monitor.list_delivery_records()
            if item["status"] != "delivered"
        ]
        assert stale_ids
        _age_rows(monitor, stale_ids, seconds=30 * 86400)

        posted: List[Dict[str, Any]] = []

        def record(url, body, timeout):
            posted.append({"url": url, "body": body})
            return True, 201

        new_failure = DownstreamProbeResult(
            target_name="svc-a",
            ok=False,
            status_code=503,
            latency_ms=10.0,
            checked_at=_rfc3339(datetime.now(timezone.utc)),
            failure_reason="HTTP 503",
            consecutive_failures=3,
        )
        with patch("downstream_health_monitor._post_json", side_effect=record):
            monitor._deliver_due_sync()
            _run(monitor._handle_probe_result(new_failure))

        stale_event = str(old_incident["opening_event_id"])
        assert all(
            item["body"].get("event_id") != stale_event
            and item["body"].get("source_event_id") != stale_event
            for item in posted
        )
        new_incident = monitor._store.get_incident("svc-a")
        assert new_incident["incident_id"] != old_incident["incident_id"]
        assert any(
            item["body"].get("incident_id") == new_incident["incident_id"]
            for item in posted
        )


class TestDeliveryHeadOfLine:
    """A blocked oldest claim window must not starve newer due rows."""

    def test_blocked_oldest_window_does_not_starve_newer_rows(self, tmp_path):
        monitor = _safety_monitor(tmp_path)
        _queue_telemetry(monitor, "dead-dependency")
        _set_rows(monitor, ["telemetry:dead-dependency"], status="dead_letter")
        older = _rfc3339(datetime.now(timezone.utc) - timedelta(seconds=120))
        with sqlite3.connect(monitor.state_path) as connection:
            connection.executemany(
                """
                INSERT INTO delivery_outbox(
                    delivery_id, event_id, channel, target_name, url,
                    body_json, dependency_ids_json, status, attempt_count,
                    max_attempts, next_attempt_at, claim_owner, claim_token,
                    claim_until, last_error, created_at, updated_at
                ) VALUES (?, ?, 'incident_open', 'svc-a', 'http://inc:8090/x',
                          ?, ?, 'pending', 0, 5, 0, NULL, NULL, NULL, NULL, ?, ?)
                """,
                [
                    (
                        f"incident-open:blocked-{index:04d}",
                        f"blocked-{index:04d}",
                        json.dumps({"incident_id": f"blocked-{index:04d}"}),
                        json.dumps(["telemetry:dead-dependency"]),
                        older,
                        older,
                    )
                    for index in range(450)
                ],
            )
        _queue_telemetry(monitor, "newer-event")

        posted: List[str] = []
        with patch(
            "downstream_health_monitor._post_json",
            side_effect=lambda url, body, timeout: posted.append(
                body.get("event_id") or body.get("incident_id")
            ) or (True, 201),
        ):
            monitor._deliver_due_sync()

        assert posted == ["newer-event"]
        assert _delivery(monitor, "telemetry:newer-event")["status"] == "delivered"
        assert _delivery(monitor, "incident-open:blocked-0000")["status"] == "pending"


class TestIncidentResolveTransition:
    """At most one incident_resolve per mapping state transition."""

    def test_repeated_healthy_probes_do_not_requeue_resolve(self, tmp_path):
        monitor = _safety_monitor(tmp_path)
        failure = DownstreamProbeResult(
            target_name="svc-a",
            ok=False,
            status_code=503,
            latency_ms=10.0,
            checked_at="2026-10-08T12:00:00Z",
            failure_reason="HTTP 503",
            consecutive_failures=2,
        )
        with patch("downstream_health_monitor._post_json", return_value=(True, 201)):
            _run(monitor._handle_probe_result(failure))
        assert monitor._store.get_incident("svc-a")["status"] == "open"

        def incidents_unavailable(url, body, timeout):
            return (False, 503) if url.endswith("/status") else (True, 201)

        with patch("downstream_health_monitor._post_json", side_effect=incidents_unavailable):
            for minute in (1, 2, 3):
                _run(
                    monitor._handle_probe_result(
                        DownstreamProbeResult(
                            target_name="svc-a",
                            ok=True,
                            status_code=200,
                            latency_ms=5.0,
                            checked_at=f"2026-10-08T12:0{minute}:00Z",
                        )
                    )
                )

        resolves = [
            item
            for item in monitor.list_delivery_records()
            if item["channel"] == "incident_resolve"
        ]
        assert len(resolves) == 1
        assert monitor._store.get_incident("svc-a")["status"] == "resolving"


class _FakeLoopControllerWriter:
    calls: List[Any] = []
    fail_with: Any = None

    def __init__(self, **kwargs):
        type(self).calls.append(("init", kwargs))

    async def record_heartbeat(self, **kwargs):
        type(self).calls.append(("heartbeat", kwargs))
        if type(self).fail_with is not None:
            raise type(self).fail_with


def _install_fake_loop_control(monkeypatch, *, fail_with=None):
    _FakeLoopControllerWriter.calls = []
    _FakeLoopControllerWriter.fail_with = fail_with
    fake_module = types.SimpleNamespace(LoopControllerWriter=_FakeLoopControllerWriter)
    real_import = importlib.import_module

    def import_module(name, *args, **kwargs):
        if name == "services.loop-control":
            return fake_module
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", import_module)


class TestLoop12ControllerTruthPublication:
    """Loop 12 truth uses an explicit dependency and reports failures."""

    def _publish(self, monitor):
        async def scenario():
            monitor.publish_loop_12_controller_truth()
            for _ in range(20):
                await asyncio.sleep(0.01)

        _run(scenario())

    def test_publication_does_not_depend_on_preimported_asyncpg(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db:5432/pantheon")
        monkeypatch.delitem(sys.modules, "asyncpg", raising=False)
        _install_fake_loop_control(monkeypatch)
        monitor = _safety_monitor(tmp_path)

        self._publish(monitor)

        assert [call[0] for call in _FakeLoopControllerWriter.calls] == ["init", "heartbeat"]
        truth = monitor.get_state()["loop_12_controller_truth"]
        assert truth["status"] == "published"
        assert truth["error"] is None

    def test_publication_failure_is_reported_not_skipped(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db:5432/pantheon")
        _install_fake_loop_control(monkeypatch, fail_with=RuntimeError("controller store down"))
        monitor = _safety_monitor(tmp_path)

        self._publish(monitor)

        truth = monitor.get_state()["loop_12_controller_truth"]
        assert truth["status"] == "failed"
        assert "controller store down" in truth["error"]
        assert "user:pass" not in json.dumps(truth)

    def test_missing_database_url_is_reported_as_unconfigured(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monitor = _safety_monitor(tmp_path)
        self._publish(monitor)
        assert monitor.get_state()["loop_12_controller_truth"]["status"] == "unconfigured"


class TestDeadLetterReplayAgeBound:
    """Replay without explicit ids refuses rows older than the age bound."""

    def test_replay_without_event_id_skips_rows_older_than_age_bound(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PANTHEON_BFF_HEALTH_DELIVERY_MAX_AGE_SECONDS", "3600")
        monitor = _safety_monitor(tmp_path)
        _queue_telemetry(monitor, "old-dead")
        _queue_telemetry(monitor, "fresh-dead")
        _set_rows(monitor, ["telemetry:old-dead", "telemetry:fresh-dead"], status="dead_letter")
        _age_rows(monitor, ["telemetry:old-dead"], seconds=2 * 86400)
        _set_rows(monitor, ["telemetry:old-dead"], status="dead_letter")

        with patch("downstream_health_monitor._post_json", return_value=(True, 201)):
            result = monitor.replay_dead_letters(
                actor_id="operator-1", approval_ref="apr-1", reason="drain"
            )

        assert result["replayed"] == 1
        assert _delivery(monitor, "telemetry:fresh-dead")["status"] == "delivered"
        assert _delivery(monitor, "telemetry:old-dead")["status"] == "dead_letter"

    def test_replay_with_explicit_event_id_can_target_an_old_row(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PANTHEON_BFF_HEALTH_DELIVERY_MAX_AGE_SECONDS", "3600")
        monitor = _safety_monitor(tmp_path)
        _queue_telemetry(monitor, "old-dead")
        _age_rows(monitor, ["telemetry:old-dead"], seconds=2 * 86400)
        _set_rows(monitor, ["telemetry:old-dead"], status="dead_letter")

        with patch("downstream_health_monitor._post_json", return_value=(True, 201)):
            result = monitor.replay_dead_letters(
                actor_id="operator-1",
                approval_ref="apr-1",
                reason="explicit redrive",
                event_id="old-dead",
            )

        assert result["replayed"] == 1
        assert _delivery(monitor, "telemetry:old-dead")["status"] == "delivered"


class TestReplayRouteDoesNotBlockEventLoop:
    """The replay route runs the synchronous delivery drain off the event loop."""

    def test_replay_runs_in_a_worker_thread(self):
        from control_loops.service import ControlLoopsService

        loop_thread: Dict[str, int] = {}
        replay_thread: Dict[str, int] = {}

        class _Monitor:
            def replay_dead_letters(self, **kwargs):
                replay_thread["ident"] = threading.get_ident()
                return {"replayed": 0, "delivered_now": 0, **kwargs}

        service = ControlLoopsService(downstream_health_monitor=_Monitor())
        identity = types.SimpleNamespace(mfa_verified=True, operator_id="operator-1")

        async def scenario():
            loop_thread["ident"] = threading.get_ident()
            return await service.replay_downstream_health_dead_letters(
                identity=identity,
                payload={"approval_ref": "apr-1", "reason": "drain backlog"},
            )

        result = _run(scenario())
        assert result["data"]["approval_ref"] == "apr-1"
        assert replay_thread["ident"] != loop_thread["ident"]


class TestTelemetryTokenFileAuthority:
    """The telemetry principal is read from its issuer-written file and fails closed."""

    def _monitor(self):
        return DownstreamHealthMonitor(
            telemetry_url="http://tel:8080",
            incidents_url="http://inc:8090",
            tenant_id="tenant-dev",
            probe_interval_seconds=9999,
        )

    def _headers(self, monitor):
        return monitor._headers_for_delivery(
            {"event_id": "evt-1", "channel": "telemetry"}
        )

    def _write(self, path, value, mode=0o600):
        path.write_text(value)
        os.chmod(path, mode)

    def test_reads_rotating_file_at_delivery_time(self, tmp_path, monkeypatch):
        token_file = tmp_path / "PANTHEON_BFF_HEALTH_TELEMETRY_JWT"
        self._write(token_file, "first.jwt.value")
        monkeypatch.setenv("PANTHEON_BFF_HEALTH_TELEMETRY_JWT_FILE", str(token_file))
        monkeypatch.delenv("PANTHEON_BFF_HEALTH_TELEMETRY_JWT", raising=False)
        monitor = self._monitor()
        assert self._headers(monitor)["Authorization"] == "Bearer first.jwt.value"
        self._write(token_file, "second.jwt.value")
        assert self._headers(monitor)["Authorization"] == "Bearer second.jwt.value"

    @pytest.mark.parametrize("state", ["absent", "revoked", "empty", "malformed", "unsafe"])
    def test_absent_revoked_or_malformed_file_fails_closed(
        self, tmp_path, monkeypatch, state
    ):
        token_file = tmp_path / "PANTHEON_BFF_HEALTH_TELEMETRY_JWT"
        if state == "empty":
            self._write(token_file, "")
        elif state == "malformed":
            self._write(token_file, "has whitespace inside")
        elif state == "unsafe":
            self._write(token_file, "a.b.c", mode=0o644)
        elif state == "revoked":
            self._write(token_file, "a.b.c")
            token_file.unlink()
        monkeypatch.setenv("PANTHEON_BFF_HEALTH_TELEMETRY_JWT_FILE", str(token_file))
        # A stale env credential must never be used instead of the configured file.
        monkeypatch.setenv("PANTHEON_BFF_HEALTH_TELEMETRY_JWT", "stale.env.token")
        monkeypatch.setenv("PANTHEON_TELEMETRY_INFRA_SERVICE_JWT", "stale.infra.token")
        with pytest.raises(RuntimeError) as failure:
            self._headers(self._monitor())
        assert "stale" not in str(failure.value)
