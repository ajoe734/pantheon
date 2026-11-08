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

    def test_reconcile_orphaned_incidents_posts_healthy_components_and_resolves_stuck_resolving(
        self, tmp_path
    ):
        monitor = _safety_monitor(tmp_path)
        monitor._store.record_probe(
            DownstreamProbeResult(
                target_name="governance",
                ok=True,
                status_code=200,
                latency_ms=4.0,
                checked_at="2026-11-03T01:00:00Z",
                consecutive_failures=0,
            )
        )
        monitor._store.record_probe(
            DownstreamProbeResult(
                target_name="telemetry",
                ok=False,
                status_code=503,
                latency_ms=12.0,
                checked_at="2026-11-03T01:00:00Z",
                consecutive_failures=3,
            )
        )
        with monitor._store._connect() as connection:
            connection.execute(
                """
                INSERT INTO incident_mappings(
                    target_name, incident_id, status, opening_event_id,
                    create_delivery_id, last_event_id, updated_at
                ) VALUES
                ('governance', 'infra-bff-2b8541b81f8d5796101ad57e', 'resolving',
                 'evt-gov-open', 'incident-open:evt-gov-open',
                 'evt-gov-resolve', '2026-10-27T01:36:00Z'),
                ('telemetry', 'infra-bff-telemetry-active', 'open',
                 'evt-tel-open', 'incident-open:evt-tel-open',
                 'evt-tel-open', '2026-11-03T01:00:00Z')
                """
            )

        captured: List[Dict[str, Any]] = []

        def mock_post(url, body, timeout):
            captured.append({"url": url, "body": body})
            return True, 200

        with patch("downstream_health_monitor._post_json", side_effect=mock_post):
            ok = monitor.reconcile_orphaned_incidents_sync(
                observed_at="2026-11-03T01:01:00Z"
            )

        assert ok is True
        assert len(captured) == 1
        assert captured[0]["url"].endswith("/api/incidents/reconcile-infrastructure-health")
        assert captured[0]["body"]["healthy_components"] == ["governance"]
        assert captured[0]["body"]["referenced_incident_ids"] == ["infra-bff-telemetry-active"]
        assert captured[0]["body"]["resolution_reason"] == "orphan_recovered"
        assert monitor._store.get_incident("governance")["status"] == "resolved"
        assert monitor._store.get_incident("telemetry")["status"] == "open"

    @pytest.mark.parametrize("channel", ["incident_open", "incident_resolve"])
    @pytest.mark.parametrize(
        ("status", "expected_active"),
        [
            ("pending", True),
            ("retry", True),
            ("claimed", True),
            ("dead_letter", False),
            ("delivered", False),
            ("quarantined", False),
        ],
    )
    def test_has_active_incident_delivery_excludes_dead_letter_and_keeps_inflight_statuses(
        self, tmp_path, channel, status, expected_active
    ):
        monitor = _safety_monitor(tmp_path)
        inc_id = "infra-bff-f15b59d579abe4c783d75af1"
        delivery_id = f"{channel}:evt-cap-1"
        url = (
            "http://inc:8090/api/incidents/consume-infrastructure-health"
            if channel == "incident_open"
            else f"http://inc:8090/api/incidents/{inc_id}/status"
        )
        body = (
            {"incident_id": inc_id, "status": "open"}
            if channel == "incident_open"
            else {"status": "resolved", "source_event_id": "evt-cap-1"}
        )
        monitor._store.queue_delivery(
            delivery_id=delivery_id,
            event_id="evt-cap-1",
            channel=channel,
            target_name="capital",
            url=url,
            body=body,
            dependency_ids=[],
            max_attempts=5,
        )
        _set_rows(monitor, [delivery_id], status=status)

        assert (
            monitor._store.has_active_incident_delivery(
                target_name="capital",
                incident_id=inc_id,
            )
            is expected_active
        )

    def test_reconcile_resolves_healthy_target_when_resolve_delivery_is_dead_letter_and_keeps_active_or_unhealthy(
        self, tmp_path
    ):
        monitor = _safety_monitor(tmp_path)
        for name, ok, failures in (
            ("capital", True, 0),
            ("pending-svc", True, 0),
            ("retry-svc", True, 0),
            ("claimed-svc", True, 0),
            ("unhealthy-resolving", False, 3),
            ("unhealthy-open", False, 4),
        ):
            monitor._store.record_probe(
                DownstreamProbeResult(
                    target_name=name,
                    ok=ok,
                    status_code=200 if ok else 503,
                    latency_ms=4.0,
                    checked_at="2026-11-04T00:00:00Z",
                    consecutive_failures=failures,
                )
            )

        with monitor._store._connect() as connection:
            connection.execute(
                """
                INSERT INTO incident_mappings(
                    target_name, incident_id, status, opening_event_id,
                    create_delivery_id, last_event_id, updated_at
                ) VALUES
                ('capital', 'infra-bff-f15b59d579abe4c783d75af1', 'resolving',
                 'evt-cap-open', 'incident-open:evt-cap-open',
                 'evt-cap-resolve', '2026-11-03T10:23:55Z'),
                ('pending-svc', 'infra-bff-pending-001', 'resolving',
                 'evt-pend-open', 'incident-open:evt-pend-open',
                 'evt-pend-resolve', '2026-11-04T00:00:00Z'),
                ('retry-svc', 'infra-bff-retry-001', 'resolving',
                 'evt-retry-open', 'incident-open:evt-retry-open',
                 'evt-retry-resolve', '2026-11-04T00:00:00Z'),
                ('claimed-svc', 'infra-bff-claimed-001', 'resolving',
                 'evt-claim-open', 'incident-open:evt-claim-open',
                 'evt-claim-resolve', '2026-11-04T00:00:00Z'),
                ('unhealthy-resolving', 'infra-bff-unhealthy-resolving-001', 'resolving',
                 'evt-unh-open', 'incident-open:evt-unh-open',
                 'evt-unh-resolve', '2026-11-03T10:23:55Z'),
                ('unhealthy-open', 'infra-bff-unhealthy-open-001', 'open',
                 'evt-unh2-open', 'incident-open:evt-unh2-open',
                 'evt-unh2-open', '2026-11-03T10:23:55Z')
                """
            )

        outbox_rows = [
            ("capital", "infra-bff-f15b59d579abe4c783d75af1", "incident_resolve", "dead_letter"),
            ("pending-svc", "infra-bff-pending-001", "incident_resolve", "pending"),
            ("retry-svc", "infra-bff-retry-001", "incident_resolve", "retry"),
            ("claimed-svc", "infra-bff-claimed-001", "incident_resolve", "claimed"),
            ("unhealthy-resolving", "infra-bff-unhealthy-resolving-001", "incident_resolve", "dead_letter"),
            ("unhealthy-open", "infra-bff-unhealthy-open-001", "incident_open", "dead_letter"),
        ]
        for target_name, inc_id, channel, status in outbox_rows:
            did = f"{channel}:{target_name}"
            monitor._store.queue_delivery(
                delivery_id=did,
                event_id=f"evt-{target_name}",
                channel=channel,
                target_name=target_name,
                url=f"http://inc:8090/api/incidents/{inc_id}/status",
                body={"incident_id": inc_id, "status": "resolved"},
                dependency_ids=[],
                max_attempts=5,
            )
            _set_rows(
                monitor,
                [did],
                status=status,
                attempt_count=5 if status == "dead_letter" else 1,
                last_error="network_error" if status == "dead_letter" else None,
            )

        captured: List[Dict[str, Any]] = []

        def mock_post(url, body, timeout):
            captured.append({"url": url, "body": body})
            return True, 200

        with patch("downstream_health_monitor._post_json", side_effect=mock_post):
            ok = monitor.reconcile_orphaned_incidents_sync(
                observed_at="2026-11-04T00:01:00Z"
            )

        assert ok is True
        assert len(captured) == 1
        body = captured[0]["body"]
        assert body["healthy_components"] == [
            "capital",
            "claimed-svc",
            "pending-svc",
            "retry-svc",
        ]
        assert "infra-bff-f15b59d579abe4c783d75af1" not in body["referenced_incident_ids"]
        assert set(body["referenced_incident_ids"]) == {
            "infra-bff-claimed-001",
            "infra-bff-pending-001",
            "infra-bff-retry-001",
            "infra-bff-unhealthy-open-001",
        }
        assert monitor._store.get_incident("capital")["status"] == "resolved"
        assert monitor._store.get_incident("pending-svc")["status"] == "resolving"
        assert monitor._store.get_incident("retry-svc")["status"] == "resolving"
        assert monitor._store.get_incident("claimed-svc")["status"] == "resolving"
        assert monitor._store.get_incident("unhealthy-resolving")["status"] == "resolving"
        assert monitor._store.get_incident("unhealthy-open")["status"] == "open"


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
            tenant_id = "tenant-a"

            def replay_dead_letters(self, **kwargs):
                replay_thread["ident"] = threading.get_ident()
                return {"replayed": 0, "delivered_now": 0, **kwargs}

        service = ControlLoopsService(downstream_health_monitor=_Monitor())
        identity = types.SimpleNamespace(
            mfa_verified=True, operator_id="operator-1", claims={"tenant_id": "tenant-a"}
        )

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


# ---------------------------------------------------------------------------
# BFF-INFRA-HEALTH-SCHEMA-KIND-20261017: events must satisfy the telemetry schema
# ---------------------------------------------------------------------------
import re as _re
from pathlib import Path as _Path

_REPO_ROOT = _Path(__file__).resolve().parents[3]


def _infra_event_validator():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (_REPO_ROOT / "services/telemetry/telemetry_event.schema.json").read_text()
    )
    sub = dict(schema["definitions"]["InfrastructureHealthEvent"])
    sub["definitions"] = schema["definitions"]
    return jsonschema.Draft7Validator(sub, format_checker=jsonschema.FormatChecker())


def _compose_targets():
    text = (_REPO_ROOT / "docker-compose.yml").read_text()
    match = _re.search(
        r"PANTHEON_BFF_HEALTH_TARGETS_JSON:\s*>-\s*\n\s*(\[.*\])\s*\n", text
    )
    assert match, "compose PANTHEON_BFF_HEALTH_TARGETS_JSON not found"
    return json.loads(match.group(1))


def _event_for(monitor, name, kind, base_url, health_path="/readyz"):
    target = DownstreamTarget(
        name=name, base_url=base_url, health_path=health_path, component_kind=kind
    )
    monitor._resolve_target_registry = lambda: {name: target}
    result = DownstreamProbeResult(
        target_name=name,
        ok=False,
        status_code=503,
        latency_ms=12.0,
        checked_at="2026-10-17T09:00:00Z",
        failure_reason="HTTP 503",
        consecutive_failures=2,
        window_started_at="2026-10-17T09:00:00Z",
    )
    return monitor._event_for_result(result, incident_ref=None)


def test_events_for_every_compose_kind_and_file_worker_match_telemetry_schema(tmp_path):
    validator = _infra_event_validator()
    monitor = _safety_monitor(tmp_path)
    entries = _compose_targets()
    assert len({e["component_kind"] for e in entries}) > 5
    cases = [
        (e["name"], e["component_kind"], e["url"], e["health_path"]) for e in entries
    ]
    cases.append(("file-worker-x", "file_worker", "file:///tmp/hb.json", "/tmp/hb.json"))
    for name, kind, url, path in cases:
        event = _event_for(monitor, name, kind, url, path)
        errors = [e.message for e in validator.iter_errors(event)]
        assert not errors, f"{name} ({kind}): {errors}"
        # Configured label survives for operators/incident consumers.
        assert event["metadata"]["configured_component_kind"] == kind
        assert event["component"]["component_kind"]


def test_file_worker_maps_to_scheduler_and_http_kinds_to_http_service(tmp_path):
    monitor = _safety_monitor(tmp_path)
    worker = _event_for(monitor, "w", "file_worker", "file:///tmp/hb", "/tmp/hb")
    assert worker["component"]["component_kind"] == "scheduler"
    http = _event_for(monitor, "a", "runtime-owner-api", "http://a:1")
    assert http["component"]["component_kind"] == "http_service"


class TestReplayAdmissionByEnvironment:
    class _Monitor:
        tenant_id = "tenant-a"

        def __init__(self):
            self.calls = []

        def replay_dead_letters(self, **kwargs):
            self.calls.append(kwargs)
            return {"replayed": 0, **kwargs}

    def _call(self, env, claims, mfa, payload=None):
        from control_loops.service import ControlLoopsService

        monitor = self._Monitor()
        service = ControlLoopsService(
            downstream_health_monitor=monitor, deployed_environment=env
        )
        identity = types.SimpleNamespace(
            mfa_verified=mfa, operator_id="op-1", claims=claims
        )
        body = (
            {"approval_ref": "apr-1", "reason": "drain"} if payload is None else payload
        )
        try:
            result = _run(
                service.replay_downstream_health_dead_letters(identity=identity, payload=body)
            )
        except Exception as exc:  # noqa: BLE001
            return monitor, exc
        return monitor, result

    @staticmethod
    def _assert_denied(monitor, err, status, precondition):
        from fastapi import HTTPException

        assert isinstance(err, HTTPException), repr(err)
        assert err.status_code == status
        assert precondition in json.dumps(err.detail, default=str)
        assert not monitor.calls

    def test_dev_matching_tenant_without_mfa_succeeds(self):
        monitor, result = self._call("dev", {"tenant_id": "tenant-a"}, False)
        assert result["data"]["approval_ref"] == "apr-1"
        assert len(monitor.calls) == 1 and monitor.calls[0]["actor_id"] == "op-1"
        assert monitor.calls[0]["reason"] == "drain"

    def test_dev_wrong_or_missing_tenant_denied_even_with_mfa(self):
        for claims in ({"tenant_id": "tenant-b"}, {}):
            monitor, err = self._call("dev", claims, True)
            self._assert_denied(monitor, err, 403, "tenant_scope")

    def test_non_dev_still_requires_mfa(self):
        monitor, err = self._call("prod", {"tenant_id": "tenant-a"}, False)
        self._assert_denied(monitor, err, 403, "mfa_verified")

    def test_dev_missing_approval_and_invalid_channel_rejected(self):
        claims = {"tenant_id": "tenant-a"}
        for body in (
            {},
            {"reason": "x"},
            {"approval_ref": "a"},
            {"approval_ref": " ", "reason": "x"},
        ):
            monitor, err = self._call("dev", claims, False, body)
            self._assert_denied(monitor, err, 422, "approval_ref")
        monitor, err = self._call(
            "dev", claims, False, {"approval_ref": "a", "reason": "x", "channel": "bogus"}
        )
        self._assert_denied(monitor, err, 422, "channel")


class TestLoop12SuccessTruthfulness:
    def _record(self, tmp_path, state):
        monitor = _safety_monitor(tmp_path)
        return monitor.publish_loop_12_controller_truth(state=state)

    def test_all_healthy_targets_record_success(self, tmp_path):
        rec = self._record(tmp_path, {"overall_ok": True, "targets": {"a": {"ok": True}}})
        assert rec["last_success_at"] and rec["last_tick_at"]

    def test_failing_target_never_records_success(self, tmp_path):
        rec = self._record(tmp_path, {"overall_ok": False, "targets": {"a": {"ok": False}}})
        assert rec["last_success_at"] is None and rec["last_tick_at"] is None
        assert rec["last_heartbeat_at"]

    def test_no_targets_never_records_success(self, tmp_path):
        rec = self._record(tmp_path, {"overall_ok": True, "targets": {}})
        assert rec["last_success_at"] is None


class TestLoop12RealWriterCapture:
    """Monitor -> real LoopControllerWriter.record_heartbeat -> _write_status with a capture store."""

    @staticmethod
    def _persist(targets):
        Writer = importlib.import_module("services.loop-control.writer").LoopControllerWriter

        class CaptureStore:
            record = None

            async def upsert_record(self, record):
                self.record = record

        sink = CaptureStore()
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://capture-only.invalid/never"}):
            writer = Writer("postgresql://capture-only.invalid/never")
            writer.store = sink
            monitor = object.__new__(DownstreamHealthMonitor)
            monitor._tenant_id = "t"
            monitor._instance_id = "i"
            monitor._loop_12_truth = {}
            monitor._loop_12_truth_task = None
            monitor._loop_12_writer_for = lambda dsn: writer
            monitor.publish_loop_12_controller_truth(
                state={"overall_ok": all(t["ok"] for t in targets.values()), "targets": targets}
            )
        return sink.record

    def test_healthy_persists_success_and_tick(self):
        rec = self._persist({"a": {"ok": True}})
        assert rec["last_success_at"] and rec["last_tick_at"] and rec["lease_token"]

    def test_failing_target_persists_no_success(self):
        rec = self._persist({"a": {"ok": False}})
        assert "last_success_at" not in rec and "last_tick_at" not in rec
        assert rec["last_heartbeat_at"] and rec["lease_token"]

    def test_no_targets_persists_no_success(self):
        rec = self._persist({})
        assert rec["desired_state"]["present"] is False
        assert "last_success_at" not in rec and "last_tick_at" not in rec
        assert rec["last_heartbeat_at"] and rec["lease_token"]


class TestBoundedHealthPruneAtHostedScale:
    """AC 1 & AC 5: >=500k real-shape outbox rows & 872,489 dead-letter backlog drain under 500ms."""

    @staticmethod
    def _real_body_866() -> str:
        prefix = (
            '{"component":{"component_id":"telemetry-ingest-svc","component_kind":"http_service",'
            '"FailureBoundary":"edge_bff","health_url":"http://telemetry:8083/__health__"},'
            '"event_id":"infra-health-00000000000000000000000000000000","event_type":"infrastructure_health",'
            '"health":{"consecutive_failures":3,"error_rate":0.0,"failure_count":0,'
            '"failure_reason":"HTTP 503","healthy":false,"latency_ms":12.4,"probe_kind":"interval_probe",'
            '"sample_count":0,"status":"degraded","status_code":503,'
            '"window_started_at":"2026-09-02T00:00:00Z"},"incident_ref":null,'
            '"observed_at":"2026-09-02T00:00:05Z","producer":"control-plane-bff",'
            '"schema_version":"pantheon.infrastructure-health/1","tenant_id":"default",'
            '"detail":"'
        )
        suffix = '"}'
        body = prefix + ("x" * (866 - len(prefix) - len(suffix))) + suffix
        assert len(body) == 866
        return body

    def test_500k_plus_outbox_and_870k_dead_letter_backlog_drain_under_500ms(self, tmp_path):
        import sqlite3
        import threading
        import time
        from downstream_health_monitor import _DurableHealthStore

        db_path = str(tmp_path / "hosted_scale_health.sqlite3")
        store = _DurableHealthStore(db_path)
        body_866 = self._real_body_866()
        dep1_fmt = '["telemetry:infra-health-%032x"]'
        dep2_fmt = '["incident-open:infra-health-%032x", "telemetry:infra-health-%032x"]'

        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-131072")
        conn.executescript(
            """
            DROP TRIGGER IF EXISTS delivery_outbox_count_insert;
            DROP INDEX IF EXISTS delivery_outbox_due_idx;
            DROP INDEX IF EXISTS delivery_outbox_event_idx;
            DROP INDEX IF EXISTS delivery_outbox_prune_idx;
            DROP INDEX IF EXISTS delivery_outbox_deps_idx;
            """
        )
        # Seed 872,489 old dead_letter rows + 10,477 old quarantined rows + 12 pending rows
        # (882,978 non-delivered rows with exact 2,167,626 bytes of dependency_ids_json:
        # 7,030 * 59 + 8 * 122 + 875,940 * 2 = 2,167,626 bytes).
        conn.execute("BEGIN")
        conn.execute(
            """
            WITH RECURSIVE seq(i) AS (
                VALUES(1)
                UNION ALL
                SELECT i + 1 FROM seq WHERE i < 882978
            )
            INSERT INTO delivery_outbox(
                delivery_id, event_id, channel, target_name, url,
                body_json, dependency_ids_json, status, attempt_count,
                max_attempts, next_attempt_at, claim_owner, claim_token,
                claim_until, last_error, created_at, updated_at
            )
            SELECT
                CASE WHEN i <= 7030 THEN printf('incident-open:infra-health-%032x', i)
                     WHEN i <= 7038 THEN printf('incident-resolve:infra-health-%032x', i)
                     ELSE printf('telemetry:infra-health-%032x', i) END,
                printf('infra-health-%032x', i),
                CASE WHEN i <= 7030 THEN 'incident_open'
                     WHEN i <= 7038 THEN 'incident_resolve'
                     ELSE 'telemetry' END,
                'telemetry',
                'http://telemetry:8083/api/v1/telemetry/infrastructure-health',
                ?,
                CASE WHEN i <= 7030 THEN printf(?, i)
                     WHEN i <= 7038 THEN printf(?, i, i)
                     ELSE '[]' END,
                CASE WHEN i <= 872489 THEN 'dead_letter'
                     WHEN i <= 882966 THEN 'quarantined'
                     ELSE 'pending' END,
                5, 5, 0.0, NULL, NULL, NULL, 'HTTP 503',
                printf('2026-09-%02dT%02d:%02d:%02dZ', 1 + ((i / 31600) % 28), (i / 3600) % 24, (i / 60) % 60, i % 60),
                printf('2026-09-%02dT%02d:%02d:%02dZ', 1 + ((i / 31600) % 28), (i / 3600) % 24, (i / 60) % 60, i % 60)
            FROM seq
            """,
            (body_866, dep1_fmt, dep2_fmt),
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS delivery_outbox_due_idx
                ON delivery_outbox(status, next_attempt_at, claim_until);
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS delivery_outbox_event_idx
                ON delivery_outbox(event_id);
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS delivery_outbox_prune_idx
                ON delivery_outbox(updated_at)
                WHERE status IN ('delivered', 'dead_letter', 'quarantined');
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS delivery_outbox_deps_idx
                ON delivery_outbox(status, updated_at, delivery_id, dependency_ids_json)
                WHERE dependency_ids_json <> '[]';
            """
        )
        conn.execute("COMMIT")
        store._initialize()

        non_delivered_count = conn.execute(
            "SELECT SUM(count) FROM delivery_status_counts WHERE status <> 'delivered'"
        ).fetchone()[0]
        non_empty_row = conn.execute(
            "SELECT COUNT(*), SUM(LENGTH(dependency_ids_json)) "
            "FROM delivery_outbox INDEXED BY delivery_outbox_deps_idx "
            "WHERE dependency_ids_json <> '[]' AND status <> 'delivered'"
        ).fetchone()
        assert non_delivered_count == 882978
        assert non_empty_row[1] + (non_delivered_count - non_empty_row[0]) * 2 == 2167626

        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        for p in (db_path, db_path + "-wal", db_path + "-shm"):
            if os.path.exists(p):
                fd = os.open(p, os.O_RDONLY)
                os.fsync(fd)
                os.close(fd)

        # Measure concurrent record_outcome writer lock wait during the initial prune burst
        writer_waits_ms: List[float] = []
        stop_writer = threading.Event()

        def _concurrent_writer() -> None:
            idx = 0
            while not stop_writer.is_set():
                idx += 1
                probe = DownstreamProbeResult(
                    target_name="telemetry",
                    ok=True,
                    status_code=200,
                    latency_ms=4.0,
                    checked_at="2026-11-07T15:13:21Z",
                )
                t_w0 = time.perf_counter()
                store.record_outcome(
                    target_name="telemetry",
                    window_started_at="2026-11-07T15:13:00Z",
                    ok=True,
                    latency_ms=4.0,
                    status_code=200,
                    detail=None,
                    observed_at="2026-11-07T15:13:21Z",
                    probe=probe,
                )
                writer_waits_ms.append((time.perf_counter() - t_w0) * 1000.0)
                time.sleep(0.02)

        prune_holds_ms: List[float] = []
        total_pruned = 0
        writer_thread = threading.Thread(target=_concurrent_writer, daemon=True)
        writer_thread.start()
        try:
            for _ in range(10):
                t0 = time.perf_counter()
                pruned = store.prune_retained_history()
                prune_holds_ms.append((time.perf_counter() - t0) * 1000.0)
                total_pruned += pruned["delivery_outbox"]
                time.sleep(0.05)
        finally:
            stop_writer.set()
            writer_thread.join(timeout=5.0)

        assert writer_waits_ms, "concurrent writer must execute during prune"
        assert max(writer_waits_ms) < 500.0, f"concurrent writer waited {max(writer_waits_ms):.1f}ms >= 500ms"

        # Continue draining the remaining backlog to completion
        while True:
            t0 = time.perf_counter()
            pruned = store.prune_retained_history()
            hold_ms = (time.perf_counter() - t0) * 1000.0
            prune_holds_ms.append(hold_ms)
            total_pruned += pruned["delivery_outbox"]
            if pruned["delivery_outbox"] == 0:
                break

        assert total_pruned == 872489 + 10477
        assert max(prune_holds_ms) < 500.0, f"max prune hold {max(prune_holds_ms):.1f}ms >= 500ms"
        final_counts = store.delivery_counts()
        assert final_counts["dead_letter"] == 0
        assert final_counts["quarantined"] == 0
        assert final_counts["pending"] == 12

