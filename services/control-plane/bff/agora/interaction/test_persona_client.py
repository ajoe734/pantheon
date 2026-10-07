"""Tests for the typed canonical Persona discovery client in agora.interaction."""
from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import unittest
from typing import Any

from services.control_plane.bff.agora.interaction import persona_client
from services.control_plane.bff.agora.interaction.persona_client import PersonaReadPort, build_canonical_persona_client
from services.control_plane.bff.ports.persona_write_owner import PersonaWriteOwnerUnavailable


class _MockPersonaServer(http.server.BaseHTTPRequestHandler):
    personas = [
        {
            "persona_id": "persona-alpha",
            "name": "Alpha Trader",
            "lifecycle_state": "active",
            "mandate": "momentum",
            "strategy_family": "trend",
        }
    ]
    snapshots = {
        "snap-001": {
            "snapshot_id": "snap-001",
            "persona_id": "persona-alpha",
            "capabilities": ["research", "backtest"],
        }
    }

    def do_GET(self) -> None:
        if self.path.startswith("/api/personas/persona-alpha/capability-snapshot"):
            body = json.dumps(self.snapshots["snap-001"]).encode("utf-8")
            status = 200
        elif self.path.startswith("/api/capability-snapshots/snap-001"):
            body = json.dumps(self.snapshots["snap-001"]).encode("utf-8")
            status = 200
        elif self.path.startswith("/api/personas"):
            body = json.dumps(self.personas).encode("utf-8")
            status = 200
        else:
            body = b'{"detail": "Not found"}'
            status = 404

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


class TestPersonaClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._server = http.server.HTTPServer(("127.0.0.1", 0), _MockPersonaServer)
        cls._port = cls._server.server_port
        cls._thread = threading.Thread(target=cls._server.serve_forever, daemon=True)
        cls._thread.start()
        cls._persona_url = f"http://127.0.0.1:{cls._port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._server.shutdown()
        cls._server.server_close()

    def test_canonical_client_conforms_to_persona_read_port(self) -> None:
        client = build_canonical_persona_client()
        self.assertTrue(isinstance(client, PersonaReadPort))
        self.assertTrue(callable(getattr(client, "list_personas", None)))
        self.assertTrue(callable(getattr(client, "get_capability_snapshot", None)))

    def test_unconfigured_persona_dependency_reports_surface_unavailable(self) -> None:
        orig = os.environ.copy()
        try:
            for key in ("PERSONA_URL", "PANTHEON_PERSONA_URL", "PANTHEON_PERSONA_API_URL"):
                os.environ.pop(key, None)
            client = build_canonical_persona_client()
            diag = getattr(client, "get_surface_status", lambda: {})()
            pcr = diag.get("persona_capital_runtime") or {}
            persona_status = pcr.get("persona") or {}
            self.assertEqual(persona_status.get("status"), "unavailable")
            self.assertEqual(persona_status.get("source"), "missing")
        finally:
            os.environ.clear()
            os.environ.update(orig)

    def test_unreachable_persona_dependency_reports_surface_unavailable(self) -> None:
        orig = os.environ.copy()
        try:
            os.environ["PERSONA_URL"] = "http://127.0.0.1:59998"
            client = build_canonical_persona_client()
            diag = getattr(client, "get_surface_status", lambda: {})()
            pcr = diag.get("persona_capital_runtime") or {}
            persona_status = pcr.get("persona") or {}
            self.assertEqual(persona_status.get("status"), "unavailable")
            self.assertEqual(persona_status.get("source"), "unavailable")
        finally:
            os.environ.clear()
            os.environ.update(orig)

    def test_available_persona_dependency_reports_surface_ok_and_returns_data(self) -> None:
        orig = os.environ.copy()
        try:
            os.environ["PERSONA_URL"] = self._persona_url
            client = build_canonical_persona_client()
            diag = getattr(client, "get_surface_status", lambda: {})()
            pcr = diag.get("persona_capital_runtime") or {}
            persona_status = pcr.get("persona") or {}
            self.assertEqual(persona_status.get("status"), "ok")
            self.assertEqual(persona_status.get("source"), "store")

            personas = client.list_personas()
            self.assertIsInstance(personas, list)
            self.assertEqual(len(personas), 1)
            self.assertEqual(personas[0]["persona_id"], "persona-alpha")

            snapshot = client.get_capability_snapshot("snap-001")
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot["snapshot_id"], "snap-001")
        finally:
            os.environ.clear()
            os.environ.update(orig)

    def test_construction_failure_is_propagated(self) -> None:
        original = persona_client.create_persona_registry_write_owner
        try:
            persona_client.create_persona_registry_write_owner = lambda *a, **kw: (_ for _ in ()).throw(
                RuntimeError("injected construction failure")
            )
            with self.assertRaisesRegex(RuntimeError, "injected construction failure"):
                build_canonical_persona_client()
        finally:
            persona_client.create_persona_registry_write_owner = original


if __name__ == "__main__":
    unittest.main()
