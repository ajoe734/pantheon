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
        provider = _make_provider()
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["agent_id"] = dict(req.header_items()).get("X-openclaw-agent-id")
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
        assert captured["body"]["model"] == "openai/gpt-5.5"
        assert result.output["agent_id"] == "persona-opinion-abcdef0123456789abcdef01"


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
            calls.append(json.loads(req.data.decode("utf-8")).get("model"))
            raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)

        with patch("urllib.request.urlopen", fake_urlopen):
            with pytest.raises(OpenClawProviderError):
                provider.invoke("hi", operator_id="op-1")
        assert len(calls) == 1
        assert calls[0] == "anthropic/claude-opus-4-8"
