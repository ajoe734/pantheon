"""StrategyCommandAdapter must call the real Registry owner and verify a
durable readback, never fabricate a resulting_status.

Prior behavior (architecture-resumption-sa-sd.md §2): _execute_strategy_action
mapped action_id -> a static resulting_status and returned it as an
"authoritative_readback" with zero owner I/O. This suite proves the fix:
- update_params performs a real GET (base metadata) + PATCH (CAS) + GET
  (readback) sequence against the Registry owner and returns the verified
  readback, not the PATCH response body.
- submit_review/promote_paper/activate/pause/archive raise
  ActionUnavailableError naming the exact non-Registry owner from
  services.registry.command_contract, instead of returning a fabricated
  status — a caller can no longer mistake "the adapter didn't crash" for
  "the business action happened".
"""
from __future__ import annotations

import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional
from unittest.mock import patch

import pytest

from services.control_plane.bff.command_adapters.base import (
    ActionUnavailableError,
    http_request_json,
)
from services.control_plane.bff.command_adapters.strategy_adapter import StrategyCommandAdapter


@pytest.fixture
def adapter():
    return StrategyCommandAdapter()


@pytest.fixture(autouse=True)
def registry_url_env(monkeypatch):
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry-svc.internal")


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json")
def test_update_params_calls_real_registry_owner_and_returns_verified_readback(mock_http, adapter):
    mock_http.side_effect = [
        {"entry": {"registry_id": "reg-001", "metadata": {"note": "old"}}},  # GET base
        {"entry": {"registry_id": "reg-001", "metadata": {"note": "new"}}},  # PATCH response (ignored as truth)
        {"entry": {"registry_id": "reg-001", "metadata": {"note": "new"}}},  # GET readback
    ]

    result = adapter.execute(
        "cmd-strat-001",
        "StrategyAction",
        {
            "entity_type": "strategy",
            "strategy_id": "strat-alpha",
            "registry_id": "reg-001",
            "action_id": "update_params",
            "metadata": {"note": "new"},
        },
        auth_token="test-token",
    )

    assert result["status"] == "metadata_updated"
    # The authoritative_readback must be the *verified GET*, not the PATCH
    # request/response body echoed back as truth.
    assert result["authoritative_readback"]["metadata"] == {"note": "new"}
    assert result["entity_id"] == "strat-alpha"
    assert result["domain_receipt"]["registry_id"] == "reg-001"

    assert mock_http.call_count == 3
    get_base_call, patch_call, get_readback_call = mock_http.call_args_list
    assert get_base_call.kwargs["method"] == "GET"
    assert get_readback_call.kwargs["method"] == "GET"
    assert patch_call.kwargs["method"] == "PATCH"
    assert patch_call.kwargs["payload"]["expected_metadata"] == {"note": "old"}
    assert patch_call.kwargs["payload"]["metadata"] == {"note": "new"}
    assert patch_call.kwargs["payload"]["command_key"] == "cmd-strat-001"


def test_update_params_requires_registry_id(adapter):
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-strat-002",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "action_id": "update_params",
                "metadata": {"note": "new"},
            },
        )
    assert excinfo.value.error_code == "MISSING_REGISTRY_ID"


@pytest.mark.parametrize(
    "action_id,expected_owner",
    [
        ("submit_review", "governance_review"),
        ("promote_paper", "promotion"),
        ("activate", "runtime"),
        ("pause", "runtime"),
        ("archive", "runtime"),
    ],
)
def test_non_registry_actions_fail_explicitly_naming_the_real_owner(adapter, action_id, expected_owner):
    """These must never return a fabricated resulting_status again."""
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-strat-003",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "action_id": action_id,
            },
        )
    assert expected_owner in str(excinfo.value)
    assert excinfo.value.error_code == "OWNER_NOT_INTEGRATED"


def test_unrecognized_action_fails_explicitly(adapter):
    with pytest.raises(ActionUnavailableError):
        adapter.execute(
            "cmd-strat-004",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "action_id": "not_a_real_action",
            },
        )


class _CapturingHandler(BaseHTTPRequestHandler):
    """Records the received method/headers/body and replays a canned response.

    Configured per-test via class attributes so each request against a real
    socket proves what actually went out on the wire, not a mocked stand-in.
    """

    response_status = 200
    response_body: Dict[str, Any] = {"ok": True}
    received: Optional[Dict[str, Any]] = None

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(length) if length else b""
        type(self).received = {
            "method": self.command,
            "headers": dict(self.headers.items()),
            "body": json.loads(raw_body.decode("utf-8")) if raw_body else None,
        }
        payload = json.dumps(type(self).response_body).encode("utf-8")
        self.send_response(type(self).response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        self._handle()

    def do_POST(self):  # noqa: N802
        self._handle()

    def do_PATCH(self):  # noqa: N802
        self._handle()

    def do_DELETE(self):  # noqa: N802
        self._handle()

    def log_message(self, format, *args):  # noqa: A002 - silence test server logging
        return


class TestHttpRequestJsonMethodDispatch:
    """Real-HTTP regressions for command_adapters.base.http_request_json.

    Before the REGISTRY-STRATEGY-UNIFIED-CONTRACT-001 fix, any non-GET method
    (including PATCH, already used by capital_adapter.py before Strategy
    needed it) fell through to command_executor._post_json, which hardcodes
    method="POST" -- so a PATCH request against a route that only accepts
    PATCH silently went out as POST. These tests exercise a real socket
    instead of a mocked http_request_json/_post_json/_get_json call, so a
    regression of the method-dispatch fix shows up as a real wrong-method
    request rather than a satisfied mock expectation.
    """

    @pytest.fixture(autouse=True)
    def _server(self):
        _CapturingHandler.received = None
        _CapturingHandler.response_status = 200
        _CapturingHandler.response_body = {"ok": True}
        server = HTTPServer(("127.0.0.1", 0), _CapturingHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.port = server.server_port
        yield
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    def _url(self, path: str = "/api/registry/entries/reg-001/metadata") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def test_get_sends_real_get(self):
        body = http_request_json(self._url(), method="GET")
        assert body == {"ok": True}
        assert _CapturingHandler.received["method"] == "GET"

    def test_post_sends_real_post_with_payload(self):
        body = http_request_json(self._url(), method="POST", payload={"a": 1})
        assert body == {"ok": True}
        assert _CapturingHandler.received["method"] == "POST"
        assert _CapturingHandler.received["body"] == {"a": 1}

    def test_patch_sends_real_patch_not_post(self):
        """The exact regression: PATCH must not be silently sent as POST."""
        body = http_request_json(self._url(), method="PATCH", payload={"metadata": {"note": "x"}})
        assert body == {"ok": True}
        assert _CapturingHandler.received["method"] == "PATCH"
        assert _CapturingHandler.received["body"] == {"metadata": {"note": "x"}}

    def test_delete_sends_real_delete(self):
        http_request_json(self._url(), method="DELETE")
        assert _CapturingHandler.received["method"] == "DELETE"

    def test_auth_and_mfa_tokens_are_forwarded_as_headers(self):
        http_request_json(self._url(), method="PATCH", payload={}, auth_token="tok-123", mfa_token="mfa-456")
        headers = _CapturingHandler.received["headers"]
        assert headers.get("Authorization") == "Bearer tok-123"
        assert headers.get("X-Mfa-Token") == "mfa-456"

    def test_bearer_prefixed_auth_token_is_not_double_wrapped(self):
        http_request_json(self._url(), method="GET", auth_token="Bearer already-prefixed")
        headers = _CapturingHandler.received["headers"]
        assert headers.get("Authorization") == "Bearer already-prefixed"

    def test_owner_conflict_status_propagates_as_http_error(self):
        """A real 409 (CAS conflict) from the owner must raise, never be
        swallowed into a fabricated success body."""
        _CapturingHandler.response_status = 409
        _CapturingHandler.response_body = {"error": "conflict"}
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            http_request_json(self._url(), method="PATCH", payload={})
        assert excinfo.value.code == 409

    def test_owner_5xx_error_propagates_as_http_error(self):
        _CapturingHandler.response_status = 503
        _CapturingHandler.response_body = {"error": "unavailable"}
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            http_request_json(self._url(), method="GET")
        assert excinfo.value.code == 503

    def test_timeout_raises_when_owner_does_not_respond(self):
        """A method that falls through to the urllib fallback path (e.g.
        PATCH) must honor an explicit short timeout rather than hang."""
        import socket

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        stall_port = listener.getsockname()[1]

        def _accept_and_stall():
            try:
                conn, _ = listener.accept()
                threading.Event().wait(2)
                conn.close()
            except OSError:
                pass

        acceptor = threading.Thread(target=_accept_and_stall, daemon=True)
        acceptor.start()
        try:
            with pytest.raises(Exception):
                http_request_json(
                    f"http://127.0.0.1:{stall_port}/api/registry/entries/reg-001/metadata",
                    method="PATCH",
                    payload={},
                    timeout=1,
                )
        finally:
            listener.close()
            acceptor.join(timeout=5)
