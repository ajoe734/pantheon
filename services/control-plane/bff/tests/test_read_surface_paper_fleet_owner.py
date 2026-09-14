"""Tests for ReadSurfacePorts authoritative paper fleet reconciler querying.

Validates that list_authoritative_paper_runtime_monitoring_sessions and
list_paper_runtime_monitoring_sessions query PANTHEON_PAPER_FLEET_RECONCILER_URL
(GET /api/fleet/state -> monitoring_sessions) instead of delegating to drift reports,
preserve authoritative unavailable (raising) vs valid empty ([]), validate response
shape with finite timeout, and support test injection.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List
import unittest
from unittest.mock import patch

BFF_DIR = Path(__file__).resolve().parent.parent
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))

from ports import (
    ReadSurfacePorts,
    create_read_surface_ports,
    create_in_memory_read_surface_ports,
)


class FakeHTTPResponse:
    def __init__(self, body: str | bytes, status: int = 200) -> None:
        self.status = status
        self.status_code = status
        if isinstance(body, str):
            self._data = body.encode("utf-8")
        else:
            self._data = body

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def read(self) -> bytes:
        return self._data


class TestReadSurfacePaperFleetOwner(unittest.TestCase):
    def test_queries_authoritative_reconciler_url(self) -> None:
        captured_requests = []

        def transport(req: Any, timeout: float = 10.0) -> FakeHTTPResponse:
            captured_requests.append((req.full_url, req.headers.get("Accept"), timeout))
            return FakeHTTPResponse(
                json.dumps(
                    {
                        "reconciler": "paper_fleet_reconciler",
                        "monitoring_sessions": [
                            {"id": "s-1", "runtime_id": "rt-1", "binding_id": "b-1", "active": True},
                            {"id": "s-2", "runtime_id": "rt-2", "binding_id": "b-2", "active": False},
                        ],
                    }
                )
            )

        ports = create_read_surface_ports(
            paper_fleet_reconciler_url="http://fleet-reconciler.internal:9000",
            paper_fleet_transport=transport,
        )

        sessions = ports.list_authoritative_paper_runtime_monitoring_sessions()
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["id"], "s-1")
        self.assertEqual(sessions[1]["id"], "s-2")
        self.assertEqual(len(captured_requests), 1)
        self.assertEqual(captured_requests[0][0], "http://fleet-reconciler.internal:9000/api/fleet/state")
        self.assertEqual(captured_requests[0][1], "application/json")
        self.assertEqual(captured_requests[0][2], 10.0)

    def test_never_delegates_to_drift_reports(self) -> None:
        drift_calls = []

        class TrackingLTG:
            def list_paper_live_drift_reports(self) -> List[Dict[str, Any]]:
                drift_calls.append("called")
                return [{"drift": "ignored"}]

        def transport(req: Any, timeout: float = 10.0) -> FakeHTTPResponse:
            return FakeHTTPResponse(json.dumps({"monitoring_sessions": []}))

        ports = create_read_surface_ports(
            lifecycle_telemetry_governance=TrackingLTG(),
            paper_fleet_reconciler_url="http://fleet-reconciler.internal:9000",
            paper_fleet_transport=transport,
        )

        sessions = ports.list_paper_runtime_monitoring_sessions()
        self.assertEqual(sessions, [])
        self.assertEqual(drift_calls, [])

    def test_valid_empty_monitoring_sessions_returns_empty_list(self) -> None:
        def transport(req: Any, timeout: float = 10.0) -> FakeHTTPResponse:
            return FakeHTTPResponse(
                json.dumps({"reconciler": "paper_fleet_reconciler", "monitoring_sessions": []})
            )

        ports = create_read_surface_ports(
            paper_fleet_reconciler_url="http://fleet-reconciler.internal:9000",
            paper_fleet_transport=transport,
        )

        sessions = ports.list_authoritative_paper_runtime_monitoring_sessions()
        self.assertEqual(sessions, [])

    def test_unconfigured_url_raises_authoritative_unavailable(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            ports = create_read_surface_ports()
            with self.assertRaises(RuntimeError) as ctx:
                ports.list_authoritative_paper_runtime_monitoring_sessions()
            self.assertIn("PANTHEON_PAPER_FLEET_RECONCILER_URL is not configured", str(ctx.exception))

    def test_env_var_url_used_when_not_passed_explicitly(self) -> None:
        captured_urls = []

        def transport(req: Any, timeout: float = 10.0) -> FakeHTTPResponse:
            captured_urls.append(req.full_url)
            return FakeHTTPResponse(json.dumps({"monitoring_sessions": []}))

        with patch.dict(os.environ, {"PANTHEON_PAPER_FLEET_RECONCILER_URL": "http://env-reconciler:8080"}):
            ports = create_read_surface_ports(paper_fleet_transport=transport)
            ports.list_authoritative_paper_runtime_monitoring_sessions()
            self.assertEqual(captured_urls, ["http://env-reconciler:8080/api/fleet/state"])

    def test_http_error_propagates_as_runtime_error(self) -> None:
        def transport(req: Any, timeout: float = 10.0) -> FakeHTTPResponse:
            return FakeHTTPResponse("Service Unavailable", status=503)

        ports = create_read_surface_ports(
            paper_fleet_reconciler_url="http://fleet-reconciler.internal:9000",
            paper_fleet_transport=transport,
        )

        with self.assertRaises(RuntimeError) as ctx:
            ports.list_authoritative_paper_runtime_monitoring_sessions()
        self.assertIn("HTTP 503", str(ctx.exception))

    def test_response_shape_validation_non_dict_payload(self) -> None:
        def transport(req: Any, timeout: float = 10.0) -> FakeHTTPResponse:
            return FakeHTTPResponse(json.dumps(["not", "a", "dict"]))

        ports = create_read_surface_ports(
            paper_fleet_reconciler_url="http://fleet-reconciler.internal:9000",
            paper_fleet_transport=transport,
        )

        with self.assertRaises(RuntimeError) as ctx:
            ports.list_authoritative_paper_runtime_monitoring_sessions()
        self.assertIn("must be a JSON object", str(ctx.exception))

    def test_response_shape_validation_missing_monitoring_sessions_list(self) -> None:
        def transport(req: Any, timeout: float = 10.0) -> FakeHTTPResponse:
            return FakeHTTPResponse(json.dumps({"reconciler": "paper_fleet_reconciler"}))

        ports = create_read_surface_ports(
            paper_fleet_reconciler_url="http://fleet-reconciler.internal:9000",
            paper_fleet_transport=transport,
        )

        with self.assertRaises(RuntimeError) as ctx:
            ports.list_authoritative_paper_runtime_monitoring_sessions()
        self.assertIn("missing 'monitoring_sessions' list", str(ctx.exception))

    def test_injected_provider_precedence_and_validation(self) -> None:
        injected_data = [{"id": "s-injected", "runtime_id": "rt-injected"}]
        ports = create_read_surface_ports(
            paper_runtime_monitoring_sessions_provider=lambda: injected_data,
        )

        self.assertEqual(
            ports.list_authoritative_paper_runtime_monitoring_sessions(),
            injected_data,
        )

        bad_ports = create_read_surface_ports(
            paper_runtime_monitoring_sessions_provider=lambda: "not a list",  # type: ignore
        )
        with self.assertRaises(RuntimeError) as ctx:
            bad_ports.list_authoritative_paper_runtime_monitoring_sessions()
        self.assertIn("must return a list", str(ctx.exception))

    def test_get_paper_runtime_monitoring_session_lookup(self) -> None:
        sessions = [
            {"id": "sess-alpha", "runtime_id": "rt-alpha", "binding_id": "b-alpha"},
            {"session_id": "sess-beta", "runtime_id": "rt-beta", "binding_id": "b-beta"},
        ]
        ports = create_read_surface_ports(
            paper_runtime_monitoring_sessions_provider=lambda: sessions,
        )

        self.assertEqual(
            ports.get_paper_runtime_monitoring_session(session_id="sess-alpha")["id"],
            "sess-alpha",
        )
        self.assertEqual(
            ports.get_paper_runtime_monitoring_session(runtime_id="rt-beta")["session_id"],
            "sess-beta",
        )
        self.assertEqual(
            ports.get_paper_runtime_monitoring_session(binding_id="b-alpha")["id"],
            "sess-alpha",
        )
        self.assertIsNone(ports.get_paper_runtime_monitoring_session(session_id="missing"))
        self.assertIsNone(ports.get_paper_runtime_monitoring_session())

    def test_create_in_memory_read_surface_ports_defaults_to_empty_provider(self) -> None:
        ports = create_in_memory_read_surface_ports()
        self.assertEqual(ports.list_authoritative_paper_runtime_monitoring_sessions(), [])
        self.assertEqual(ports.list_paper_runtime_monitoring_sessions(), [])
