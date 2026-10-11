"""Unit tests for reconciliation-drift telemetry client.

Tests authenticated telemetry client operations:
- fetch_runtime_summaries
- append_lifecycle_event
- fetch_accepted_event
- verify_durable_event_order
"""

from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from telemetry_client import (
    TelemetryAuthError,
    TelemetryError,
    TelemetryUnavailable,
    append_lifecycle_event,
    fetch_accepted_event,
    fetch_runtime_summaries,
    verify_durable_event_order,
)


class _MockResponse:
    def __init__(self, body: str | bytes, status: int = 200):
        self._raw = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.code = status

    def read(self) -> bytes:
        return self._raw

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def _make_http_error(url: str, code: int, body: dict | str = "") -> urllib.error.HTTPError:
    raw = json.dumps(body).encode("utf-8") if isinstance(body, dict) else str(body).encode("utf-8")
    fp = io.BytesIO(raw)
    return urllib.error.HTTPError(url, code, f"HTTP {code}", hdrs=None, fp=fp)


class TestTelemetryClient(unittest.TestCase):
    def setUp(self):
        self.telemetry_url = "http://telemetry.local:8083"
        self.tenant_id = "tenant-dev"
        self._env_patch = mock.patch.dict("os.environ", {"PANTHEON_TENANT_ID": self.tenant_id})
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()

    def test_event_get_missing_tenant_fails_closed(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(TelemetryUnavailable) as ctx:
                fetch_accepted_event(self.telemetry_url, "e1")
            self.assertIn("tenant_id is required", str(ctx.exception))

    # --- fetch_runtime_summaries tests ---
    def test_fetch_runtime_summaries_missing_url(self):
        with self.assertRaises(TelemetryUnavailable):
            fetch_runtime_summaries("")

    @mock.patch("urllib.request.urlopen")
    def test_fetch_runtime_summaries_success(self, mock_urlopen):
        mock_urlopen.return_value = _MockResponse(json.dumps([{"binding_id": "b1"}]))
        summaries = fetch_runtime_summaries(self.telemetry_url)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["binding_id"], "b1")

    @mock.patch("urllib.request.urlopen")
    def test_fetch_runtime_summaries_dict_wrapper(self, mock_urlopen):
        mock_urlopen.return_value = _MockResponse(json.dumps({"summaries": [{"binding_id": "b2"}]}))
        summaries = fetch_runtime_summaries(self.telemetry_url)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["binding_id"], "b2")

    @mock.patch("urllib.request.urlopen")
    def test_fetch_runtime_summaries_auth_error(self, mock_urlopen):
        mock_urlopen.side_effect = _make_http_error(self.telemetry_url, 401, {"error": "unauthorized"})
        with self.assertRaises(TelemetryAuthError):
            fetch_runtime_summaries(self.telemetry_url)

    @mock.patch("urllib.request.urlopen")
    def test_fetch_runtime_summaries_server_error(self, mock_urlopen):
        mock_urlopen.side_effect = _make_http_error(self.telemetry_url, 500, {"error": "server error"})
        with self.assertRaises(TelemetryUnavailable):
            fetch_runtime_summaries(self.telemetry_url)

    # --- append_lifecycle_event tests ---
    def test_append_lifecycle_event_missing_url(self):
        res = append_lifecycle_event("", {"event_id": "e1"})
        self.assertEqual(res["status"], "retryable_error")
        self.assertTrue(res["retryable"])

    def test_append_lifecycle_event_timeout_zero(self):
        res = append_lifecycle_event(self.telemetry_url, {"event_id": "e1"}, timeout_seconds=0.0)
        self.assertEqual(res["status"], "retryable_error")
        self.assertEqual(res["http_status"], 504)

    def test_append_lifecycle_event_success(self):
        mock_opener = mock.MagicMock()
        mock_opener.return_value = _MockResponse(json.dumps({"status": "accepted"}), status=202)
        res = append_lifecycle_event(self.telemetry_url, {"event_id": "e1"}, urlopen=mock_opener)
        self.assertEqual(res["status"], "accepted")
        self.assertTrue(res["terminal"])

    def test_append_lifecycle_event_terminal_rejected(self):
        mock_opener = mock.MagicMock()
        mock_opener.side_effect = _make_http_error(self.telemetry_url, 400, {"error": "invalid"})
        res = append_lifecycle_event(self.telemetry_url, {"event_id": "e1"}, urlopen=mock_opener)
        self.assertEqual(res["status"], "terminal_rejected")
        self.assertTrue(res["terminal"])

    def test_append_lifecycle_event_retryable_error(self):
        mock_opener = mock.MagicMock()
        mock_opener.side_effect = _make_http_error(self.telemetry_url, 503, {"error": "overloaded"})
        res = append_lifecycle_event(self.telemetry_url, {"event_id": "e1"}, urlopen=mock_opener)
        self.assertEqual(res["status"], "retryable_error")
        self.assertTrue(res["retryable"])

    # --- fetch_accepted_event tests ---
    def test_fetch_accepted_event_missing_params(self):
        with self.assertRaises(TelemetryUnavailable):
            fetch_accepted_event("", "e1")
        with self.assertRaises(TelemetryUnavailable):
            fetch_accepted_event(self.telemetry_url, "")

    @mock.patch("urllib.request.urlopen")
    def test_fetch_accepted_event_found(self, mock_urlopen):
        mock_urlopen.return_value = _MockResponse(json.dumps({"event_id": "e1", "event_type": "lifecycle"}))
        event = fetch_accepted_event(self.telemetry_url, "e1")
        self.assertIsNotNone(event)
        self.assertEqual(event["event_id"], "e1")

    @mock.patch("urllib.request.urlopen")
    def test_fetch_accepted_event_not_found(self, mock_urlopen):
        mock_urlopen.side_effect = _make_http_error(self.telemetry_url, 404, {"error": "not found"})
        event = fetch_accepted_event(self.telemetry_url, "nonexistent")
        self.assertIsNone(event)

    @mock.patch("urllib.request.urlopen")
    def test_fetch_accepted_event_auth_error(self, mock_urlopen):
        mock_urlopen.side_effect = _make_http_error(self.telemetry_url, 403, {"error": "forbidden"})
        with self.assertRaises(TelemetryAuthError):
            fetch_accepted_event(self.telemetry_url, "e1")

    @mock.patch("urllib.request.urlopen")
    def test_fetch_accepted_event_unavailable(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")
        with self.assertRaises(TelemetryUnavailable):
            fetch_accepted_event(self.telemetry_url, "e1")

    # --- verify_durable_event_order tests ---
    def test_verify_durable_event_order_missing_params(self):
        ok, reason, pair = verify_durable_event_order("", accepted_event_id="e1", observed_event_id="e2")
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_parameter")

        ok, reason, pair = verify_durable_event_order(self.telemetry_url, accepted_event_id="", observed_event_id="e2")
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_parameter")

        ok, reason, pair = verify_durable_event_order(self.telemetry_url, accepted_event_id="e1", observed_event_id="")
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_parameter")

    @mock.patch("urllib.request.urlopen")
    def test_verify_durable_event_order_verified_200(self, mock_urlopen):
        mock_urlopen.return_value = _MockResponse(json.dumps({
            "status": "verified",
            "pair": {
                "accepted_event_id": "e1",
                "observed_event_id": "e2",
                "accepted_ingested_seq": 100,
                "observed_ingested_seq": 200,
            }
        }))
        ok, reason, pair = verify_durable_event_order(
            self.telemetry_url, accepted_event_id="e1", observed_event_id="e2"
        )
        self.assertTrue(ok)
        self.assertIsNone(reason)
        self.assertEqual(pair["accepted_ingested_seq"], 100)
        self.assertEqual(pair["observed_ingested_seq"], 200)

    @mock.patch("urllib.request.urlopen")
    def test_verify_durable_event_order_not_found_404(self, mock_urlopen):
        mock_urlopen.side_effect = _make_http_error(self.telemetry_url, 404, {"error": "not found"})
        ok, reason, pair = verify_durable_event_order(
            self.telemetry_url, accepted_event_id="e1", observed_event_id="e2"
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "event_not_found")
        self.assertEqual(pair, {})

    @mock.patch("urllib.request.urlopen")
    def test_verify_durable_event_order_conflict_409(self, mock_urlopen):
        mock_urlopen.side_effect = _make_http_error(
            self.telemetry_url, 409, {"status": "conflict", "error": {"reason": "out_of_order"}}
        )
        ok, reason, pair = verify_durable_event_order(
            self.telemetry_url, accepted_event_id="e2", observed_event_id="e1"
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "out_of_order")
        self.assertEqual(pair, {})

    @mock.patch("urllib.request.urlopen")
    def test_verify_durable_event_order_auth_error(self, mock_urlopen):
        mock_urlopen.side_effect = _make_http_error(self.telemetry_url, 401, {"error": "unauthorized"})
        with self.assertRaises(TelemetryAuthError):
            verify_durable_event_order(
                self.telemetry_url, accepted_event_id="e1", observed_event_id="e2"
            )

    @mock.patch("urllib.request.urlopen")
    def test_verify_durable_event_order_unavailable(self, mock_urlopen):
        mock_urlopen.side_effect = _make_http_error(self.telemetry_url, 503, {"error": "db offline"})
        with self.assertRaises(TelemetryUnavailable):
            verify_durable_event_order(
                self.telemetry_url, accepted_event_id="e1", observed_event_id="e2"
            )


if __name__ == "__main__":
    unittest.main()
