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
