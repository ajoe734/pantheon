"""HTTP-mounted contract regression tests for the unified OpenClaw transport.

SIMPLIFY-OPENCLAW-001: ordinary agent turns (`invoke()`, `stream()`,
`readiness()`'s answer-probe) go through a single HTTP request builder
hitting the Gateway `POST /v1/responses` endpoint. This module asserts:

  * a normal invoke works entirely through mocked HTTP
  * invoke/stream/readiness never spawn a subprocess for ordinary turns
  * a >96KiB prompt behaves identically to a short one (no transport
    branch on prompt length)
  * explicit non-default agent + model routing reaches the payload/headers
  * error injection (HTTP 4xx/5xx, connection failures, malformed/partial
    SSE, response.failed/refusal/incomplete, missing [DONE], mid-stream
    cancellation) always yields exactly one terminal event and never
    silently falls back to CLI or swaps models.
"""
from __future__ import annotations

import json
import socket
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

ADAPTER_DIR = Path(__file__).resolve().parents[1]
if str(ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTER_DIR))

from assistant_openclaw_provider import (  # noqa: E402
    AssistantOpenClawProvider,
    OpenClawProviderError,
)


def _sse_bytes(events: list) -> list:
    lines = [("data: " + json.dumps(evt) + "\n").encode("utf-8") for evt in events]
    lines.append(b"data: [DONE]\n")
    return lines


class _FakeSSEResponse:
    def __init__(self, events: list) -> None:
        self._lines = _sse_bytes(events)

    def __iter__(self):
        return iter(self._lines)

    def close(self) -> None:
        pass


class _RawLinesResponse:
    """Fake response that yields raw pre-encoded byte lines (no [DONE] appended)."""

    def __init__(self, lines: list) -> None:
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)

    def close(self) -> None:
        pass


def _answer_events(text: str) -> list:
    return [
        {"type": "response.output_text.done", "text": text},
        {"type": "response.completed", "response": {"status": "completed"}},
    ]


def _forbidden_run(*_args, **_kwargs):
    raise AssertionError("must not spawn subprocess for ordinary turn")


def _make_provider(*, gateway_url: str = "ws://openclaw-gateway:18789", token: str = "test-token") -> AssistantOpenClawProvider:
    return AssistantOpenClawProvider(
        gateway_url=gateway_url,
        agent_id="main",
        token=token,
        _which_func=lambda _: None,  # CLI binary absent must not matter for ordinary turns
        _run_func=_forbidden_run,
    )


class TestNormalInvokeContract:
    def test_short_prompt_invoke_succeeds_over_http_only(self):
        provider = _make_provider()
        with patch("urllib.request.urlopen", return_value=_FakeSSEResponse(_answer_events("hello"))):
            result = provider.invoke("hi", operator_id="op-1")
        assert result.status == "completed"
        assert result.output["transport"] == "responses_http"

    def test_oversized_prompt_behaves_identically_to_short_prompt(self):
        provider = _make_provider()
        big_prompt = "y" * (96 * 1024 + 5)
        with patch("urllib.request.urlopen", return_value=_FakeSSEResponse(_answer_events("big reply"))) as _mock:
            result = provider.invoke(big_prompt, operator_id="op-1")
        assert result.status == "completed"
        assert result.output["transport"] == "responses_http"

    def test_invoke_never_spawns_subprocess(self):
        """`_run_func` raises if called; invoke must succeed without touching it."""
        provider = _make_provider()
        with patch("urllib.request.urlopen", return_value=_FakeSSEResponse(_answer_events("ok"))):
            result = provider.invoke("hello", operator_id="op-1")
        assert result.status == "completed"

    def test_stream_never_spawns_subprocess(self):
        provider = _make_provider()
        with patch("urllib.request.urlopen", return_value=_FakeSSEResponse(_answer_events("ok"))):
            events = list(provider.stream("hello", operator_id="op-1"))
        assert events[-1]["type"] == "done"

    def test_readiness_answer_probe_never_spawns_subprocess(self):
        provider = _make_provider()
        with patch("urllib.request.urlopen", return_value=_FakeSSEResponse(_answer_events("PANTHEON_PROVIDER_READY"))):
            info = provider.readiness(auth_probe=True)
        assert info["ready"] is True

    def test_explicit_non_default_agent_and_model_routing(self):
        """The JSON `model` field must always be an `openclaw/<agentId>` alias
        (the pinned Gateway's `resolveOpenAiCompatModelOverride` rejects a raw
        provider id like "openai/gpt-5.5" with HTTP 400); a requested
        provider/model override belongs in the `x-openclaw-model` header."""
        provider = _make_provider()
        captured = {}

        def fake_urlopen(req, timeout=None):
            headers = {k.lower(): v for k, v in req.header_items()}
            captured["agent_id"] = headers.get("x-openclaw-agent-id")
            captured["model_header"] = headers.get("x-openclaw-model")
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeSSEResponse(_answer_events("routed reply"))

        with patch("urllib.request.urlopen", fake_urlopen):
            result = provider.invoke(
                "route this",
                agent_id="persona-opinion-abcdef0123456789abcdef01",
                model="openai/gpt-5.5",
                operator_id="op-1",
            )
        assert result.status == "completed"
        assert captured["agent_id"] == "persona-opinion-abcdef0123456789abcdef01"
        assert captured["body"]["model"] == "openclaw/persona-opinion-abcdef0123456789abcdef01"
        assert captured["model_header"] == "openai/gpt-5.5"
        assert result.output["agent_id"] == "persona-opinion-abcdef0123456789abcdef01"

    def test_default_agent_no_model_override_sends_openclaw_alias_only(self):
        provider = _make_provider()
        captured = {}

        def fake_urlopen(req, timeout=None):
            headers = {k.lower(): v for k, v in req.header_items()}
            captured["model_header"] = headers.get("x-openclaw-model")
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeSSEResponse(_answer_events("ok"))

        with patch("urllib.request.urlopen", fake_urlopen):
            list(provider.stream("hi", operator_id="op-1"))
        assert captured["body"]["model"] == "openclaw/main"
        assert captured["model_header"] is None

    def test_same_named_conversation_isolates_by_authenticated_tenant_and_actor(self):
        """Two different tenants/actors reusing the same caller-chosen
        session_id must never collide onto the same upstream `user` — that
        would cross-pollinate warm session routing between them."""
        provider = _make_provider()
        captured_users = []

        def fake_urlopen(req, timeout=None):
            captured_users.append(json.loads(req.data.decode("utf-8")).get("user"))
            return _FakeSSEResponse(_answer_events("ok"))

        with patch("urllib.request.urlopen", fake_urlopen):
            provider.invoke(
                "hi",
                operator_id="alice",
                metadata={"tenant_id": "tenant-a"},
                session_id="shared",
            )
            provider.invoke(
                "hi",
                operator_id="bob",
                metadata={"tenant_id": "tenant-b"},
                session_id="shared",
            )
        assert len(captured_users) == 2
        assert captured_users[0] != captured_users[1]

    def test_multiturn_messages_are_normalized_to_message_items(self):
        """Pinned `MessageItemSchema` is `.strict()` and requires
        `type: "message"` — a plain `{"role", "content"}` dict is rejected."""
        provider = _make_provider()
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeSSEResponse(_answer_events("ok"))

        history = [
            {"role": "user", "content": "first turn"},
            {"role": "assistant", "content": "first reply"},
        ]
        with patch("urllib.request.urlopen", fake_urlopen):
            list(provider.stream("second turn", operator_id="op-1", messages=history))
        input_list = captured["body"]["input"]
        assert all(item.get("type") == "message" for item in input_list)
        assert input_list[-1] == {"type": "message", "role": "user", "content": "second turn"}


class TestErrorInjectionContract:
    """Every scenario must produce exactly one terminal event: no double
    terminal, no silent CLI fallback, no automatic model swap."""

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 429, 500, 503])
    def test_http_error_status_codes_surface_as_single_typed_error(self, status_code):
        provider = _make_provider()

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, status_code, "err", {}, None)

        with patch("urllib.request.urlopen", fake_urlopen):
            events = list(provider.stream("hi", operator_id="op-1"))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["status_code"] == status_code

    def test_connection_refused_is_single_typed_error(self):
        provider = _make_provider()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError(ConnectionRefusedError("refused")),
        ):
            events = list(provider.stream("hi", operator_id="op-1"))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_RESPONSES_UNREACHABLE"

    def test_dns_or_tls_style_failure_is_single_typed_error(self):
        provider = _make_provider()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError(OSError("Name or service not known")),
        ):
            events = list(provider.stream("hi", operator_id="op-1"))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_RESPONSES_UNREACHABLE"

    def test_socket_timeout_is_single_typed_error(self):
        provider = _make_provider()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError(socket.timeout("timed out")),
        ):
            events = list(provider.stream("hi", operator_id="op-1"))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_RESPONSES_TIMEOUT"

    def test_server_disconnect_mid_stream_is_single_typed_error(self):
        class _BrokenIter:
            def __iter__(self):
                def gen():
                    yield b'data: {"type":"response.output_text.delta","delta":"partial"}\n'
                    raise ConnectionError("server disconnected")

                return gen()

            def close(self):
                pass

        provider = _make_provider()
        with patch("urllib.request.urlopen", return_value=_BrokenIter()):
            events = list(provider.stream("hi", operator_id="op-1"))
        terminal = [e for e in events if e["type"] in ("done", "error")]
        assert len(terminal) == 1
        assert terminal[0]["type"] == "error"
        assert terminal[0]["error_code"] == "OPENCLAW_RESPONSES_STREAM_INTERRUPTED"

    def test_malformed_sse_lines_are_skipped_not_fatal(self):
        provider = _make_provider()
        lines = [
            b"not a data line at all\n",
            b"data: {not valid json\n",
            b"data: " + json.dumps({"type": "response.output_text.done", "text": "salvaged"}).encode() + b"\n",
            b'data: {"type":"response.completed","response":{"status":"completed"}}\n',
            b"data: [DONE]\n",
        ]
        with patch("urllib.request.urlopen", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1
        assert done[0]["text"] == "salvaged"

    def test_multiline_sse_with_blank_lines_between_events(self):
        provider = _make_provider()
        lines = [
            b'data: {"type":"response.output_text.delta","delta":"a"}\n',
            b"\n",
            b'data: {"type":"response.output_text.delta","delta":"b"}\n',
            b"\n",
            b'data: {"type":"response.completed","response":{"status":"completed"}}\n',
            b"data: [DONE]\n",
        ]
        with patch("urllib.request.urlopen", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1
        assert done[0]["text"] == "ab"

    def test_legal_multiline_single_event_split_across_data_lines(self):
        """Per SSE spec, consecutive `data:` lines with no blank line between
        them belong to the SAME event and must be joined with "\\n" before
        parsing — a single JSON object legally split across two physical
        `data:` lines must not be treated as two malformed fragments and
        silently dropped (which previously surfaced as OPENCLAW_RESPONSES_EMPTY)."""
        provider = _make_provider()
        lines = [
            b'data: {"type":"response.output_text.done",\n',
            b'data: "text":"joined reply"}\n',
            b"\n",
            b'data: {"type":"response.completed","response":{"status":"completed"}}\n',
            b"data: [DONE]\n",
        ]
        with patch("urllib.request.urlopen", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        errors = [e for e in events if e["type"] == "error"]
        assert not errors, errors
        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1
        assert done[0]["text"] == "joined reply"

    def test_duplicate_response_completed_yields_only_one_done_event(self):
        """A second `response.completed` (e.g. a duplicated upstream frame)
        must never re-emit a second "done" — the first terminal event ends
        the stream read."""
        provider = _make_provider()
        lines = [
            b'data: {"type":"response.output_text.done","text":"first"}\n',
            b'data: {"type":"response.completed","response":{"status":"completed"}}\n',
            b'data: {"type":"response.completed","response":{"status":"completed"}}\n',
            b"data: [DONE]\n",
        ]
        with patch("urllib.request.urlopen", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1
        assert done[0]["text"] == "first"

    def test_partial_data_then_cancellation_yields_single_terminal(self):
        """Simulates a stream that stops mid-response (e.g. client cancels)
        without ever emitting response.completed or [DONE]."""
        lines = [b'data: {"type":"response.output_text.delta","delta":"partial answer"}\n']
        provider = _make_provider()
        with patch("urllib.request.urlopen", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        terminal = [e for e in events if e["type"] in ("done", "error")]
        assert len(terminal) == 1
        # A cancelled/incomplete stream with real partial text is reported as
        # a real (non-fabricated) done using the salvaged partial text.
        assert terminal[0]["type"] == "done"
        assert terminal[0]["text"] == "partial answer"

    def test_response_failed_event_is_single_typed_error(self):
        provider = _make_provider()
        lines = [b'data: {"type":"response.failed","reason":"upstream_error"}\n']
        with patch("urllib.request.urlopen", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_RESPONSES_FAILED"

    def test_nested_response_status_failed_is_single_typed_error(self):
        provider = _make_provider()
        lines = [b'data: {"type":"response.completed","response":{"status":"failed"}}\n']
        with patch("urllib.request.urlopen", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_RESPONSES_FAILED"

    def test_nested_response_status_incomplete_is_single_typed_error(self):
        provider = _make_provider()
        lines = [b'data: {"type":"response.completed","response":{"status":"incomplete"}}\n']
        with patch("urllib.request.urlopen", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_RESPONSES_INCOMPLETE"

    def test_refusal_with_no_text_and_no_function_calls_is_typed_empty(self):
        provider = _make_provider()
        lines = [b'data: {"type":"response.completed","response":{"status":"completed"}}\n']
        with patch("urllib.request.urlopen", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_RESPONSES_EMPTY"

    def test_missing_done_marker_but_completed_still_terminates_once(self):
        provider = _make_provider()
        lines = [
            b'data: {"type":"response.output_text.done","text":"no done marker"}\n',
            b'data: {"type":"response.completed","response":{"status":"completed"}}\n',
        ]
        with patch("urllib.request.urlopen", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1
        assert done[0]["text"] == "no done marker"

    def test_missing_done_marker_and_no_completed_event_still_single_terminal(self):
        """The gateway's connection just ends. No [DONE], no response.completed."""
        provider = _make_provider()
        lines = [b'data: {"type":"response.output_text.delta","delta":"streamed"}\n']
        with patch("urllib.request.urlopen", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        terminal = [e for e in events if e["type"] in ("done", "error")]
        assert len(terminal) == 1
        assert terminal[0]["type"] == "done"
        assert terminal[0]["text"] == "streamed"

    def test_invoke_does_not_retry_or_swap_model_on_failure(self):
        provider = _make_provider()
        calls = []

        def fake_urlopen(req, timeout=None):
            headers = {k.lower(): v for k, v in req.header_items()}
            calls.append(headers.get("x-openclaw-model"))
            raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)

        with patch("urllib.request.urlopen", fake_urlopen):
            with pytest.raises(OpenClawProviderError):
                provider.invoke("hi", operator_id="op-1")
        assert len(calls) == 1
        assert calls[0] == "anthropic/claude-opus-4-8"


class _SlowDripHTTPServer:
    """A real local HTTP server (no mocking of `urllib.request.urlopen`) that
    trickles response bytes slower than any single per-read socket timeout,
    so `time.sleep`/socket-level timeouts alone would never bound the total
    request duration. Used to reproduce, over a genuine socket, the "slow
    drip" total-deadline bug: a per-chunk delay just under the per-read
    timeout that accumulates far past the intended total budget."""

    def __init__(self, *, chunk_delay: float, num_chunks: int, chunk: bytes = b": keep-alive\n\n"):
        import http.server
        import threading

        self._chunk_delay = chunk_delay
        self._num_chunks = num_chunks
        self._chunk = chunk
        outer = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):  # noqa: D401 - silence test server logging
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                import time as _time

                for _ in range(outer._num_chunks):
                    try:
                        self.wfile.write(outer._chunk)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionError):
                        return
                    _time.sleep(outer._chunk_delay)

        self._server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._server.shutdown()
        self._server.server_close()


class TestRealLocalHttpServerRegressions:
    """These reproduce bugs found only against a genuine socket — a mocked
    `urllib.request.urlopen` iterator does not exhibit real partial-chunk /
    slow-drip / mid-stream-close socket semantics."""

    def test_slow_drip_response_is_bounded_by_total_deadline_not_per_read_timeout(self):
        """A per-read timeout alone never fires here (each chunk arrives
        comfortably inside it); only a total-deadline check bounds the real
        elapsed time close to the requested budget."""
        with _SlowDripHTTPServer(chunk_delay=0.05, num_chunks=40) as server:
            provider = AssistantOpenClawProvider(
                gateway_url=f"ws://127.0.0.1:{server.port}",
                agent_id="main",
                token="test-token",
                _which_func=lambda _: None,
                _run_func=_forbidden_run,
            )
            started = __import__("time").monotonic()
            events = list(provider.stream("hi", operator_id="op-1", timeout_seconds=0.3))
            elapsed = __import__("time").monotonic() - started
        assert elapsed < 1.0, f"total streaming time {elapsed:.3f}s was not bounded near the 0.3s budget"
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_RESPONSES_TIMEOUT"

    def test_real_partial_eof_mid_event_yields_single_truthful_terminal(self):
        """The server sends a real partial SSE frame and then closes the
        connection before completing the event — must never be reported as a
        fabricated "completed" success; exactly one terminal event fires."""
        import http.server
        import threading

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"type":"response.output_text.delta","delta":"partial')
                self.wfile.flush()
                # Close mid-frame: no closing quote/brace, no [DONE], no
                # response.completed — a genuine truncated connection.

        server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            provider = AssistantOpenClawProvider(
                gateway_url=f"ws://127.0.0.1:{server.server_address[1]}",
                agent_id="main",
                token="test-token",
                _which_func=lambda _: None,
                _run_func=_forbidden_run,
            )
            events = list(provider.stream("hi", operator_id="op-1", timeout_seconds=5.0))
        finally:
            server.shutdown()
            server.server_close()
        terminal = [e for e in events if e["type"] in ("done", "error")]
        assert len(terminal) == 1
        # A truncated line never parses as JSON, so no partial text was
        # salvaged; the stream ends with no assistant text at all — reported
        # truthfully as empty, never fabricated as "completed".
        assert terminal[0]["type"] == "error"
        assert terminal[0]["error_code"] == "OPENCLAW_RESPONSES_EMPTY"
