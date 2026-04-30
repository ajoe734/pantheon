"""Smoke tests for the OpenClawLifecycleClient HTTP shim.

These tests stub urllib so the client logic is exercised without a running
service. They verify request shape, header propagation, and error mapping —
not transport behavior.
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

_ADAPTER_DIR = os.path.dirname(__file__)
if _ADAPTER_DIR not in sys.path:
    sys.path.insert(0, _ADAPTER_DIR)

import lifecycle_client as lc  # noqa: E402


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self._status = status
        self.length = len(body)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:  # noqa: D401
        return None

    def getcode(self) -> int:
        return self._status

    def read(self) -> bytes:
        return self._body


class _Recorder:
    def __init__(self, response_body: Dict[str, Any] | str = "", status: int = 200, raise_http: Optional[urllib.error.HTTPError] = None, raise_url: Optional[urllib.error.URLError] = None) -> None:
        if isinstance(response_body, dict):
            self._body = json.dumps(response_body).encode("utf-8")
        else:
            self._body = response_body.encode("utf-8")
        self._status = status
        self.raise_http = raise_http
        self.raise_url = raise_url
        self.calls: List[Tuple[str, str, Dict[str, str], Optional[bytes]]] = []

    def __call__(self, request: Any, timeout: Optional[float] = None) -> _FakeResponse:
        self.calls.append((request.get_method(), request.full_url, dict(request.headers), request.data))
        if self.raise_http:
            raise self.raise_http
        if self.raise_url:
            raise self.raise_url
        return _FakeResponse(self._body, self._status)


class TestLifecycleClient(unittest.TestCase):
    def setUp(self) -> None:
        self.client = lc.OpenClawLifecycleClient(base_url="http://adapter.test", timeout_seconds=1.0)

    def test_create_session_sends_headers_and_returns_record(self) -> None:
        recorder = _Recorder({"status": "ok", "replayed": False, "session": {"session_id": "s-1", "state": "active"}}, status=201)
        with patch.object(lc.urllib.request, "urlopen", recorder):
            record, replayed = self.client.create_session(
                agent_id="a-1",
                session_type="interactive",
                operator_id="alice",
                idempotency_key="k-1",
                context_bundle={"k": "v"},
            )
        self.assertFalse(replayed)
        self.assertEqual(record["session_id"], "s-1")
        method, url, headers, body = recorder.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "http://adapter.test/api/openclaw-adapter/lifecycle/sessions")
        # urllib lowercases headers.
        self.assertEqual(headers["X-operator-id"], "alice")
        self.assertEqual(headers["X-idempotency-key"], "k-1")
        self.assertIn("application/json", headers["Content-type"])
        payload = json.loads(body)
        self.assertEqual(payload["agent_id"], "a-1")
        self.assertEqual(payload["context_bundle"], {"k": "v"})

    def test_get_session(self) -> None:
        recorder = _Recorder({"status": "ok", "session": {"session_id": "s-1", "state": "active"}})
        with patch.object(lc.urllib.request, "urlopen", recorder):
            record = self.client.get_session("s-1")
        self.assertEqual(record["session_id"], "s-1")

    def test_list_sessions_with_filters(self) -> None:
        recorder = _Recorder({"status": "ok", "sessions": [{"session_id": "s-1"}]})
        with patch.object(lc.urllib.request, "urlopen", recorder):
            records = self.client.list_sessions(operator_id="alice", state="active")
        self.assertEqual(records, [{"session_id": "s-1"}])
        _, url, _, _ = recorder.calls[0]
        self.assertIn("operator_id=alice", url)
        self.assertIn("state=active", url)

    def test_cancel_session(self) -> None:
        recorder = _Recorder({"status": "ok", "session": {"session_id": "s-1", "state": "canceled"}})
        with patch.object(lc.urllib.request, "urlopen", recorder):
            record = self.client.cancel_session("s-1", operator_id="alice")
        self.assertEqual(record["state"], "canceled")
        _, url, headers, _ = recorder.calls[0]
        self.assertTrue(url.endswith("/api/openclaw-adapter/lifecycle/sessions/s-1/cancel"))
        self.assertEqual(headers["X-operator-id"], "alice")

    def test_get_audit(self) -> None:
        recorder = _Recorder({"status": "ok", "session_id": "s-1", "audit_log": [{"action": "create_requested"}]})
        with patch.object(lc.urllib.request, "urlopen", recorder):
            audit = self.client.get_audit("s-1")
        self.assertEqual(audit, [{"action": "create_requested"}])

    def test_unconfigured_base_url_raises(self) -> None:
        client = lc.OpenClawLifecycleClient(base_url="", timeout_seconds=1.0)
        with self.assertRaises(lc.LifecycleClientError) as ctx:
            client.get_session("s-1")
        self.assertEqual(ctx.exception.error_code, "LIFECYCLE_CLIENT_NOT_CONFIGURED")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_http_error_maps_to_lifecycle_error(self) -> None:
        body = json.dumps({"error_code": "LIFECYCLE_NOT_FOUND", "message": "missing"}).encode("utf-8")
        http_error = urllib.error.HTTPError(
            url="http://adapter.test/x",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=io.BytesIO(body),
        )
        recorder = _Recorder("", raise_http=http_error)
        with patch.object(lc.urllib.request, "urlopen", recorder):
            with self.assertRaises(lc.LifecycleClientError) as ctx:
                self.client.get_session("missing")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.error_code, "LIFECYCLE_NOT_FOUND")

    def test_url_error_maps_to_unreachable(self) -> None:
        url_error = urllib.error.URLError("connection refused")
        recorder = _Recorder("", raise_url=url_error)
        with patch.object(lc.urllib.request, "urlopen", recorder):
            with self.assertRaises(lc.LifecycleClientError) as ctx:
                self.client.get_session("any")
        self.assertEqual(ctx.exception.error_code, "LIFECYCLE_CLIENT_UNREACHABLE")
        self.assertEqual(ctx.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
