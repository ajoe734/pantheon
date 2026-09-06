"""StrategyCommandAdapter must call the real Registry owner and verify a
durable readback, never fabricate a resulting_status.

Prior behavior (architecture-resumption-sa-sd.md §2): _execute_strategy_action
mapped action_id -> a static resulting_status and returned it as an
"authoritative_readback" with zero owner I/O. A later fix made update_params
perform real owner I/O but still had two defects (reviewer finding 6):
it silently replaced the caller's ``expected_metadata`` CAS precondition
with a freshly-fetched GET (defeating CAS), and it discarded the actual PATCH
response in favor of a separate re-GET that could return stale/unrelated/
empty data and still be reported as a "metadata_updated" success.

This suite proves the current, corrected contract:
- update_params performs exactly one HTTP call — a PATCH carrying the
  caller's own ``expected_metadata`` unchanged — and builds its receipt from
  that PATCH response (entry snapshot + ``X-Idempotent-Replay`` header), not
  from a second GET.
- A PATCH response with no confirmable entry payload raises explicitly
  instead of manufacturing a false success.
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


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_preserves_callers_precondition_and_uses_patch_response_as_truth(mock_http, adapter):
    """The caller's own expected_metadata (its real CAS precondition) must
    reach the PATCH unchanged — never silently refreshed to "latest" via an
    extra GET first — and the receipt must reflect the actual PATCH response,
    not a separate re-GET."""
    mock_http.return_value = (
        200,
        {"X-Idempotent-Replay": "false"},
        {
            "entry": {
                "registry_id": "reg-001",
                "metadata": {"note": "new"},
                "updated_at": "2026-09-06T00:00:00Z",
                "checksum": "sha256:abc",
            }
        },
    )

    result = adapter.execute(
        "cmd-strat-001",
        "StrategyAction",
        {
            "entity_type": "strategy",
            "strategy_id": "strat-alpha",
            "registry_id": "reg-001",
            "action_id": "update_params",
            "expected_metadata": {"note": "old"},
            "metadata": {"note": "new"},
        },
        auth_token="test-token",
    )

    assert result["status"] == "metadata_updated"
    # The authoritative_readback is the entry from the PATCH response itself.
    assert result["authoritative_readback"]["metadata"] == {"note": "new"}
    assert result["entity_id"] == "strat-alpha"
    assert result["domain_receipt"]["registry_id"] == "reg-001"
    assert result["domain_receipt"]["checksum"] == "sha256:abc"
    assert result["idempotent_replay"] is False

    # Exactly one HTTP call: the PATCH itself, carrying the caller's own
    # expected_metadata unchanged — no preceding "refresh to latest" GET.
    assert mock_http.call_count == 1
    patch_call = mock_http.call_args_list[0]
    assert patch_call.kwargs["method"] == "PATCH"
    assert patch_call.kwargs["payload"]["expected_metadata"] == {"note": "old"}
    assert patch_call.kwargs["payload"]["metadata"] == {"note": "new"}
    assert patch_call.kwargs["payload"]["command_key"] == "cmd-strat-001"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_idempotent_replay_header_is_surfaced(mock_http, adapter):
    mock_http.return_value = (
        200,
        {"X-Idempotent-Replay": "true"},
        {"entry": {"registry_id": "reg-001", "metadata": {"note": "v1"}, "updated_at": "t1"}},
    )

    result = adapter.execute(
        "cmd-strat-005",
        "StrategyAction",
        {
            "entity_type": "strategy",
            "strategy_id": "strat-alpha",
            "registry_id": "reg-001",
            "action_id": "update_params",
            "expected_metadata": None,
            "metadata": {"note": "v1"},
        },
    )
    assert result["idempotent_replay"] is True


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_ambiguous_patch_response_never_fabricates_success(mock_http, adapter):
    """A 200 response with no confirmable entry payload (e.g. an empty body)
    must never be reported as metadata_updated — that would be exactly the
    "wrong-version/empty GET manufactures a fake success" defect."""
    mock_http.return_value = (200, {}, {})

    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-strat-006",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": None,
                "metadata": {"note": "v1"},
            },
        )
    assert excinfo.value.error_code == "AMBIGUOUS_REGISTRY_RESPONSE"


def test_update_params_requires_registry_id(adapter):
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-strat-002",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "action_id": "update_params",
                "expected_metadata": None,
                "metadata": {"note": "new"},
            },
        )
    assert excinfo.value.error_code == "MISSING_REGISTRY_ID"


def test_update_params_requires_expected_metadata(adapter):
    """Omitting expected_metadata entirely must fail explicitly rather than
    the adapter silently treating it as None or fetching a fresh base."""
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-strat-007",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "metadata": {"note": "new"},
            },
        )
    assert excinfo.value.error_code == "MISSING_EXPECTED_METADATA"


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


class TestUpdateParamsOverRealSocket:
    """End-to-end regression for update_params against a real HTTP server
    standing in for the Registry owner — no mocking of http_request_json* —
    so the CAS-precondition and receipt-fidelity fixes are proven against
    what actually goes out on (and comes back over) the wire, not a mock
    expectation that could silently drift from the real contract.
    """

    @pytest.fixture(autouse=True)
    def _server(self, monkeypatch):
        _CapturingHandler.received = None
        _CapturingHandler.response_status = 200
        _CapturingHandler.response_body = {"ok": True}
        server = HTTPServer(("127.0.0.1", 0), _CapturingHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", f"http://127.0.0.1:{server.server_port}")
        yield
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    def test_callers_base_precondition_is_sent_unchanged_over_the_wire(self, adapter):
        _CapturingHandler.response_body = {
            "entry": {
                "registry_id": "reg-001",
                "metadata": {"note": "new"},
                "updated_at": "2026-09-06T00:00:00Z",
                "checksum": "sha256:real",
            }
        }
        adapter.execute(
            "cmd-real-001",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "callers-own-base"},
                "metadata": {"note": "new"},
            },
        )
        assert _CapturingHandler.received["method"] == "PATCH"
        assert _CapturingHandler.received["body"]["expected_metadata"] == {"note": "callers-own-base"}

    def test_correct_patch_result_produces_receipt_bound_to_that_exact_response(self, adapter):
        _CapturingHandler.response_body = {
            "entry": {
                "registry_id": "reg-001",
                "metadata": {"note": "new"},
                "updated_at": "2026-09-06T01:23:45Z",
                "checksum": "sha256:exact-version",
            }
        }
        result = adapter.execute(
            "cmd-real-002",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": None,
                "metadata": {"note": "new"},
            },
        )
        assert result["status"] == "metadata_updated"
        assert result["domain_receipt"]["checksum"] == "sha256:exact-version"
        assert result["domain_receipt"]["commit_time"] == "2026-09-06T01:23:45Z"
        assert result["domain_receipt"]["correlation_id"] == "cmd-real-002"
        assert result["authoritative_readback"]["updated_at"] == "2026-09-06T01:23:45Z"

    def test_empty_response_body_cannot_manufacture_a_fake_success(self, adapter):
        """A 200 with no entry payload (e.g. an unrelated/empty GET-shaped
        body) must not be reported as metadata_updated."""
        _CapturingHandler.response_body = {}
        with pytest.raises(ActionUnavailableError) as excinfo:
            adapter.execute(
                "cmd-real-003",
                "StrategyAction",
                {
                    "entity_type": "strategy",
                    "strategy_id": "strat-alpha",
                    "registry_id": "reg-001",
                    "action_id": "update_params",
                    "expected_metadata": None,
                    "metadata": {"note": "new"},
                },
            )
        assert excinfo.value.error_code == "AMBIGUOUS_REGISTRY_RESPONSE"
