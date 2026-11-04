"""BFF-DOWNSTREAM-MONITOR-RESTART-20261008: lifespan owns the Loop 12 monitor.

Commit c1894c3b0 removed the startup/shutdown hooks that ran the downstream
health monitor, and nothing failed: /bff/v5/downstream-health then reported
every target as ``not_yet_observed`` forever.  These tests drive the real
create_lifespan so removing the start or stop call fails a test.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import FastAPI

from services.control_plane.bff import downstream_health_monitor as monitor_module
from services.control_plane.bff.auth.service import ProviderReadinessCache
from services.control_plane.bff.control_loops.service import ControlLoopsService
from services.control_plane.bff.core.lifespan import create_lifespan


async def _ready_probe():
    return {"ready": True, "status": "ready"}


@pytest.fixture
def restore_registered_monitor():
    previous = monitor_module.get_downstream_health_monitor()
    yield
    monitor_module.set_downstream_health_monitor(previous)


def test_lifespan_starts_and_stops_the_registered_monitor(tmp_path, restore_registered_monitor):
    # Production builds its lifespan with create_lifespan() and no monitor
    # argument (main.py); the composition root's monitor registers itself on
    # construction, so this exercises the production resolution path.
    monitor = monitor_module.DownstreamHealthMonitor(
        probe_interval_seconds=3600,
        state_path=str(tmp_path / "downstream_health.sqlite3"),
    )
    lifespan = create_lifespan(ProviderReadinessCache(_ready_probe), prewarm_jwks=False)
    app = FastAPI()

    async def scenario():
        async with lifespan(app):
            assert app.state.downstream_health_monitor is monitor
            assert monitor._running is True
            assert monitor._task is not None and not monitor._task.done()
        assert monitor._running is False
        assert monitor._task is None

    asyncio.run(scenario())


class _HealthyHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        body = json.dumps({"status": "ok"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence test output
        return


def test_downstream_health_reports_targets_observed_within_two_intervals(
    tmp_path, monkeypatch, restore_registered_monitor
):
    # The target registry probes each base URL once, so each target needs its
    # own server.
    servers = [ThreadingHTTPServer(("127.0.0.1", 0), _HealthyHandler) for _ in range(2)]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    runtime_url, reconciler_url = (
        f"http://127.0.0.1:{server.server_address[1]}" for server in servers
    )
    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", runtime_url)
    monkeypatch.setenv("PANTHEON_PAPER_FLEET_RECONCILER_URL", reconciler_url)
    interval = 1.0
    monitor = monitor_module.DownstreamHealthMonitor(
        telemetry_url="",
        incidents_url="",
        probe_interval_seconds=interval,
        state_path=str(tmp_path / "downstream_health.sqlite3"),
    )
    service = ControlLoopsService(downstream_health_monitor=monitor)
    lifespan = create_lifespan(
        ProviderReadinessCache(_ready_probe),
        prewarm_jwks=False,
        downstream_health_monitor=monitor,
    )
    app = FastAPI()

    async def scenario():
        async with lifespan(app):
            started = time.monotonic()
            while True:
                targets = service.downstream_health()["data"]["targets"]
                observed = {
                    name: targets.get(name, {}).get("ok")
                    for name in ("runtime-manager", "paper-fleet-reconciler")
                }
                if all(value is True for value in observed.values()):
                    return time.monotonic() - started, observed
                if time.monotonic() - started > 2 * interval + 0.5:
                    return None, observed
                await asyncio.sleep(0.05)

    try:
        elapsed, observed = asyncio.run(scenario())
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
    assert elapsed is not None, f"targets not observed within two intervals: {observed}"
    assert elapsed <= 2 * interval + 0.5


def test_lifespan_monitor_reconciles_orphaned_infrastructure_incidents_within_one_interval(
    tmp_path, monkeypatch, restore_registered_monitor
):
    reconcile_requests: list[dict] = []

    class _IncidentsAndHealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            if self.path.endswith("/api/incidents/reconcile-infrastructure-health"):
                reconcile_requests.append(json.loads(raw))
            body = json.dumps({"resolved_count": 1, "resolution_reason": "orphan_recovered"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _IncidentsAndHealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    interval = 1.0
    monitor = monitor_module.DownstreamHealthMonitor(
        telemetry_url="",
        incidents_url=base_url,
        probe_interval_seconds=interval,
        state_path=str(tmp_path / "downstream_orphan_reconcile.sqlite3"),
    )
    monitor._resolve_target_registry = lambda: {  # type: ignore[assignment]
        "governance": monitor_module.DownstreamTarget(
            name="governance",
            base_url=base_url,
            health_path="/__health__",
        ),
    }
    lifespan = create_lifespan(
        ProviderReadinessCache(_ready_probe),
        prewarm_jwks=False,
        downstream_health_monitor=monitor,
    )
    app = FastAPI()

    async def scenario():
        async with lifespan(app):
            started = time.monotonic()
            while not reconcile_requests and (time.monotonic() - started) <= interval + 0.5:
                await asyncio.sleep(0.05)
            return time.monotonic() - started

    try:
        elapsed = asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()

    assert reconcile_requests, "Expected orphan reconciliation request within one probe interval"
    assert elapsed <= interval + 0.5
    assert reconcile_requests[0]["healthy_components"] == ["governance"]
    assert reconcile_requests[0]["resolution_reason"] == "orphan_recovered"


def test_lifespan_monitor_reconciles_dead_lettered_resolving_incident_within_one_interval(
    tmp_path, restore_registered_monitor
):
    reconcile_requests: list[dict] = []

    class _IncidentsAndHealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            if self.path.endswith("/api/incidents/reconcile-infrastructure-health"):
                reconcile_requests.append(json.loads(raw))
            body = json.dumps({"resolved_count": 1, "resolution_reason": "orphan_recovered"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _IncidentsAndHealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    interval = 1.0
    monitor = monitor_module.DownstreamHealthMonitor(
        telemetry_url="",
        incidents_url=base_url,
        probe_interval_seconds=interval,
        state_path=str(tmp_path / "downstream_dead_letter_reconcile.sqlite3"),
    )
    monitor._resolve_target_registry = lambda: {  # type: ignore[assignment]
        "capital": monitor_module.DownstreamTarget(
            name="capital",
            base_url=base_url,
            health_path="/__health__",
        ),
    }
    inc_id = "infra-bff-f15b59d579abe4c783d75af1"
    with monitor._store._connect() as conn:
        conn.execute(
            """
            INSERT INTO incident_mappings(
                target_name, incident_id, status, opening_event_id,
                create_delivery_id, last_event_id, updated_at
            ) VALUES (
                'capital', ?, 'resolving', 'evt-cap-open',
                'incident-open:evt-cap-open', 'evt-cap-resolve',
                '2026-11-03T10:23:55Z'
            )
            """,
            (inc_id,),
        )
        conn.execute(
            """
            INSERT INTO delivery_outbox(
                delivery_id, event_id, channel, target_name, url,
                body_json, dependency_ids_json, status, attempt_count,
                max_attempts, next_attempt_at, claim_owner, claim_token,
                claim_until, last_error, created_at, updated_at
            ) VALUES (
                'incident-resolve:evt-cap-resolve', 'evt-cap-resolve',
                'incident_resolve', 'capital', ?, ?, '[]',
                'dead_letter', 5, 5, 0, NULL, NULL, NULL,
                'network_error', '2026-11-03T10:23:55Z', '2026-11-03T10:24:25Z'
            )
            """,
            (
                f"{base_url}/api/incidents/{inc_id}/status",
                json.dumps({"status": "resolved", "source_event_id": "evt-cap-resolve"}),
            ),
        )

    lifespan = create_lifespan(
        ProviderReadinessCache(_ready_probe),
        prewarm_jwks=False,
        downstream_health_monitor=monitor,
    )
    app = FastAPI()

    async def scenario():
        async with lifespan(app):
            started = time.monotonic()
            while not reconcile_requests and (time.monotonic() - started) <= interval + 0.5:
                await asyncio.sleep(0.05)
            return time.monotonic() - started

    try:
        elapsed = asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()

    assert reconcile_requests, "Expected orphan reconciliation request within one probe interval"
    assert elapsed <= interval + 0.5
    assert reconcile_requests[0]["healthy_components"] == ["capital"]
    assert reconcile_requests[0]["referenced_incident_ids"] == []
    assert reconcile_requests[0]["resolution_reason"] == "orphan_recovered"
    assert monitor._store.get_incident("capital")["status"] == "resolved"
