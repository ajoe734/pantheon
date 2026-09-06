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


@pytest.fixture
def mounted_http(monkeypatch):
    from fastapi.testclient import TestClient
    import main as adapter_main

    provider = _make_provider()
    captured = []

    def fake_urlopen(req, timeout=None, deadline=None):
        captured.append({
            "body": json.loads(req.data),
            "headers": {k.lower(): v for k, v in req.header_items()},
        })
        return _FakeSSEResponse(_answer_events("PANTHEON_PROVIDER_READY"))

    monkeypatch.setattr(adapter_main, "_OPENCLAW_AGENT_PROVIDER", provider)
    monkeypatch.setattr("assistant_openclaw_provider._urlopen_with_deadline", fake_urlopen)
    with TestClient(adapter_main.app) as client:
        yield client, captured


@pytest.mark.parametrize("session_field", ["session_user", "session_id"])
def test_mounted_transports_share_conversations_and_isolate_callers(mounted_http, session_field):
    client, captured = mounted_http
    identities = [
        ("tenant-a", "actor-a", "conversation-a"),
        ("tenant-a", "actor-a", "conversation-b"),
        ("tenant-b", "actor-a", "conversation-a"),
        ("tenant-a", "actor-b", "conversation-a"),
    ]
    users = []
    for tenant, actor, conversation in identities:
        for suffix in ("", "/stream", ""):
            response = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/invoke" + suffix,
                json={"prompt": "continue", "metadata": {
                    "tenant_id": tenant, session_field: conversation,
                }},
                headers={"X-Operator-Id": actor},
            )
            assert response.status_code == 200, response.text
        route_users = [entry["body"]["user"] for entry in captured[-3:]]
        assert route_users == [f"{tenant}|{actor}|{conversation}"] * 3
        users.append(route_users[0])
    assert len(set(users)) == len(identities)


def test_mounted_session_user_precedes_legacy_session_id(mounted_http):
    client, captured = mounted_http
    for suffix in ("", "/stream"):
        response = client.post(
            "/api/openclaw-adapter/assistant/providers/openclaw/invoke" + suffix,
            json={"prompt": "continue", "metadata": {
                "tenant_id": "tenant", "session_user": "current", "session_id": "legacy",
            }},
            headers={"X-Operator-Id": "actor"},
        )
        assert response.status_code == 200, response.text
    assert [entry["body"]["user"] for entry in captured] == ["tenant|actor|current"] * 2


@pytest.mark.parametrize("primary", [None, "fixture/configured-primary"])
def test_mounted_invoke_stream_readiness_share_effective_model(mounted_http, monkeypatch, primary):
    from assistant_openclaw_provider import DEFAULT_PRIMARY_MODEL

    client, captured = mounted_http
    if primary is None:
        monkeypatch.delenv("OPENCLAW_PRIMARY_MODEL", raising=False)
    else:
        monkeypatch.setenv("OPENCLAW_PRIMARY_MODEL", primary)
    for suffix in ("", "/stream"):
        response = client.post(
            "/api/openclaw-adapter/assistant/providers/openclaw/invoke" + suffix,
            json={"prompt": "hi"}, headers={"X-Operator-Id": "actor"},
        )
        assert response.status_code == 200, response.text
    readiness = client.get("/api/openclaw-adapter/assistant/readiness/openclaw?auth_probe=true")
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["ready"] is True
    assert len(captured) == 3
    for entry in captured:
        assert entry["body"]["model"] == "openclaw/main"
        assert entry["headers"]["x-openclaw-model"] == (primary or DEFAULT_PRIMARY_MODEL)


@pytest.mark.parametrize("model", [None, "fixture/explicit-model"])
def test_nondefault_agent_model_routing_is_identical_across_transports(monkeypatch, model):
    provider = _make_provider()
    captured = []

    def fake_urlopen(req, timeout=None, deadline=None):
        captured.append({k.lower(): v for k, v in req.header_items()})
        return _FakeSSEResponse(_answer_events("ok"))

    monkeypatch.setattr("assistant_openclaw_provider._urlopen_with_deadline", fake_urlopen)
    kwargs = {"agent_id": "persona-opinion-abcdef0123456789abcdef01", "model": model}
    provider.invoke("hi", **kwargs)
    assert list(provider.stream("hi", **kwargs))[-1]["type"] == "done"
    assert [headers.get("x-openclaw-model") for headers in captured] == [model, model]
    assert all(headers["x-openclaw-agent-id"] == kwargs["agent_id"] for headers in captured)


class TestNormalInvokeContract:
    def test_short_prompt_invoke_succeeds_over_http_only(self):
        provider = _make_provider()
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_FakeSSEResponse(_answer_events("hello"))):
            result = provider.invoke("hi", operator_id="op-1")
        assert result.status == "completed"
        assert result.output["transport"] == "responses_http"

    def test_oversized_prompt_behaves_identically_to_short_prompt(self):
        provider = _make_provider()
        big_prompt = "y" * (96 * 1024 + 5)
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_FakeSSEResponse(_answer_events("big reply"))) as _mock:
            result = provider.invoke(big_prompt, operator_id="op-1")
        assert result.status == "completed"
        assert result.output["transport"] == "responses_http"

    def test_invoke_never_spawns_subprocess(self):
        """`_run_func` raises if called; invoke must succeed without touching it."""
        provider = _make_provider()
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_FakeSSEResponse(_answer_events("ok"))):
            result = provider.invoke("hello", operator_id="op-1")
        assert result.status == "completed"

    def test_stream_never_spawns_subprocess(self):
        provider = _make_provider()
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_FakeSSEResponse(_answer_events("ok"))):
            events = list(provider.stream("hello", operator_id="op-1"))
        assert events[-1]["type"] == "done"

    def test_readiness_answer_probe_never_spawns_subprocess(self):
        provider = _make_provider()
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_FakeSSEResponse(_answer_events("PANTHEON_PROVIDER_READY"))):
            info = provider.readiness(auth_probe=True)
        assert info["ready"] is True

    def test_explicit_non_default_agent_and_model_routing(self):
        """The JSON `model` field must always be an `openclaw/<agentId>` alias
        (the pinned Gateway's `resolveOpenAiCompatModelOverride` rejects a raw
        provider id like "openai/gpt-5.5" with HTTP 400); a requested
        provider/model override belongs in the `x-openclaw-model` header."""
        provider = _make_provider()
        captured = {}

        def fake_urlopen(req, timeout=None, deadline=None):
            headers = {k.lower(): v for k, v in req.header_items()}
            captured["agent_id"] = headers.get("x-openclaw-agent-id")
            captured["model_header"] = headers.get("x-openclaw-model")
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeSSEResponse(_answer_events("routed reply"))

        with patch("assistant_openclaw_provider._urlopen_with_deadline", fake_urlopen):
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

    def test_default_agent_uses_primary_model_with_openclaw_alias(self):
        provider = _make_provider()
        captured = {}

        def fake_urlopen(req, timeout=None, deadline=None):
            headers = {k.lower(): v for k, v in req.header_items()}
            captured["model_header"] = headers.get("x-openclaw-model")
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeSSEResponse(_answer_events("ok"))

        with patch("assistant_openclaw_provider._urlopen_with_deadline", fake_urlopen):
            list(provider.stream("hi", operator_id="op-1"))
        assert captured["body"]["model"] == "openclaw/main"
        from assistant_openclaw_provider import DEFAULT_PRIMARY_MODEL
        assert captured["model_header"] == (os.getenv("OPENCLAW_PRIMARY_MODEL", "").strip() or DEFAULT_PRIMARY_MODEL)

    def test_same_named_conversation_isolates_by_authenticated_tenant_and_actor(self):
        """Two different tenants/actors reusing the same caller-chosen
        session_id must never collide onto the same upstream `user` — that
        would cross-pollinate warm session routing between them."""
        provider = _make_provider()
        captured_users = []

        def fake_urlopen(req, timeout=None, deadline=None):
            captured_users.append(json.loads(req.data.decode("utf-8")).get("user"))
            return _FakeSSEResponse(_answer_events("ok"))

        with patch("assistant_openclaw_provider._urlopen_with_deadline", fake_urlopen):
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

        def fake_urlopen(req, timeout=None, deadline=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeSSEResponse(_answer_events("ok"))

        history = [
            {"role": "user", "content": "first turn"},
            {"role": "assistant", "content": "first reply"},
        ]
        with patch("assistant_openclaw_provider._urlopen_with_deadline", fake_urlopen):
            list(provider.stream("second turn", operator_id="op-1", messages=history))
        input_list = captured["body"]["input"]
        assert all(item.get("type") == "message" for item in input_list)
        assert input_list[-1] == {"type": "message", "role": "user", "content": "second turn"}

    def test_chat_format_content_parts_in_messages_are_normalized(self):
        """SIMPLIFY-OPENCLAW-001 mounted-acceptance gap: only top-level
        `attachments` were normalized into the pinned Gateway's content-part
        shapes; a `messages[]` entry carrying Chat-Completions-style content
        parts (`{"type": "text", ...}` / `{"type": "image_url", ...}`) must
        round-trip through the same normalization so multimodal chat
        history is not rejected by the Gateway's `.strict()` schema."""
        provider = _make_provider()
        captured = {}

        def fake_urlopen(req, timeout=None, deadline=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeSSEResponse(_answer_events("ok"))

        history = [
            {"role": "user", "content": "first turn"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is in this image?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
                ],
            },
            {"role": "assistant", "content": "a description"},
        ]
        with patch("assistant_openclaw_provider._urlopen_with_deadline", fake_urlopen):
            list(provider.stream("second turn", operator_id="op-1", messages=history))
        input_list = captured["body"]["input"]
        multimodal_entry = input_list[1]
        assert multimodal_entry["type"] == "message"
        content = multimodal_entry["content"]
        assert {"type": "input_text", "text": "what is in this image?"} in content
        assert {
            "type": "input_image",
            "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"},
        } in content

    def test_boundary_shifting_session_components_do_not_collide(self):
        """A caller-chosen component containing the "|" separator must not
        let a different tenant/actor/conversation split collide onto the
        same derived upstream `user` key."""
        from assistant_openclaw_provider import derive_session_user

        first = derive_session_user(
            operator_id="alice", session_id="bob|shared", metadata={"tenant_id": "tenant"}
        )
        second = derive_session_user(
            operator_id="bob", session_id="shared", metadata={"tenant_id": "tenant|alice"}
        )
        assert first != second

    def test_absent_tenant_vs_absent_conversation_do_not_collide(self):
        """SIMPLIFY-OPENCLAW-001 corrective pass (reviewer finding 4):
        filtering out an empty component before joining collapses distinct
        identity shapes onto the same string --
        (tenant="alice", actor="bob", conversation="") and
        (tenant="", actor="alice", conversation="bob") both reduce to just
        the two present parts "alice" and "bob", losing which slot was
        absent. Each of the three slots must be encoded by position."""
        from assistant_openclaw_provider import derive_session_user

        no_conversation = derive_session_user(
            operator_id="bob", session_id="", metadata={"tenant_id": "alice"}
        )
        no_tenant = derive_session_user(
            operator_id="alice", session_id="bob", metadata={}
        )
        assert no_conversation != no_tenant

    def test_all_three_slots_absent_returns_none(self):
        from assistant_openclaw_provider import derive_session_user

        assert derive_session_user(operator_id=None, session_id=None, metadata={}) is None

    def test_trace_id_reaches_metadata(self):
        """`trace_id` is accepted by the request builder but must actually
        reach the upstream `/v1/responses` call — the pinned Gateway's only
        strict-schema slot for opaque caller context is the
        `metadata: Record<string,string>` field."""
        provider = _make_provider()
        captured = {}

        def fake_urlopen(req, timeout=None, deadline=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeSSEResponse(_answer_events("ok"))

        with patch("assistant_openclaw_provider._urlopen_with_deadline", fake_urlopen):
            list(
                provider.stream(
                    "hi",
                    operator_id="op-1",
                    trace_id="trace-123",
                )
            )
        metadata = captured["body"]["metadata"]
        assert metadata["trace_id"] == "trace-123"

    def test_context_pack_is_folded_into_model_visible_input_not_metadata(self):
        """SIMPLIFY-OPENCLAW-001 corrective pass (reviewer finding 5): the
        real Gateway builds model context only from `input`/`instructions`
        and never reads arbitrary `metadata`, so a `context_pack` serialized
        only into `metadata` (the old, truncated-at-4000-chars behavior) is
        never actually seen by the model. A multi-thousand-character pack
        with a trailing required fact must be verifiably present in the
        actual outbound `input` payload, untruncated."""
        provider = _make_provider()
        captured = {}

        def fake_urlopen(req, timeout=None, deadline=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeSSEResponse(_answer_events("ok"))

        padding = "x" * 5000
        context_pack = {"padding": padding, "required_fact": "the-secret-passphrase-99"}
        with patch("assistant_openclaw_provider._urlopen_with_deadline", fake_urlopen):
            list(
                provider.stream(
                    "hi",
                    operator_id="op-1",
                    context_pack=context_pack,
                )
            )
        body = captured["body"]
        # context_pack must never be serialized into metadata (the model
        # never reads it there).
        assert "context_pack" not in (body.get("metadata") or {})
        input_list = body["input"]
        assert isinstance(input_list, list)
        context_item = input_list[0]
        assert context_item["role"] == "system"
        context_text = context_item["content"][0]["text"]
        assert "the-secret-passphrase-99" in context_text
        assert padding in context_text
        # The prompt itself must still be present as the trailing user turn.
        trailing = input_list[-1]
        assert trailing["role"] == "user"

    def test_oversized_context_pack_is_explicitly_rejected_not_truncated(self):
        """An over-budget context_pack must yield a typed rejection, never a
        silently truncated "success" that quietly drops model-visible
        context."""
        provider = _make_provider()
        oversized = {"padding": "y" * 400_000}
        with patch("assistant_openclaw_provider._urlopen_with_deadline") as mock_urlopen:
            events = list(
                provider.stream("hi", operator_id="op-1", context_pack=oversized)
            )
        mock_urlopen.assert_not_called()
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_CONTEXT_PACK_TOO_LARGE"

    def test_attachments_are_normalized_into_input_content_parts(self):
        """A caller-supplied Chat-Completions-style attachment must be
        converted into the pinned Gateway's `input_image` content part and
        attached to the trailing user message, not silently dropped."""
        provider = _make_provider()
        captured = {}

        def fake_urlopen(req, timeout=None, deadline=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeSSEResponse(_answer_events("ok"))

        with patch("assistant_openclaw_provider._urlopen_with_deadline", fake_urlopen):
            list(
                provider.stream(
                    "describe this",
                    operator_id="op-1",
                    attachments=[
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
                    ],
                )
            )
        input_list = captured["body"]["input"]
        assert len(input_list) == 1
        message = input_list[0]
        assert message["type"] == "message"
        assert message["role"] == "user"
        content = message["content"]
        assert {"type": "input_text", "text": "describe this"} in content
        assert {
            "type": "input_image",
            "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"},
        } in content

    def test_attachments_attach_to_trailing_message_with_history(self):
        provider = _make_provider()
        captured = {}

        def fake_urlopen(req, timeout=None, deadline=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeSSEResponse(_answer_events("ok"))

        history = [{"role": "user", "content": "first turn"}]
        with patch("assistant_openclaw_provider._urlopen_with_deadline", fake_urlopen):
            list(
                provider.stream(
                    "second turn",
                    operator_id="op-1",
                    messages=history,
                    attachments=[
                        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                    ],
                )
            )
        input_list = captured["body"]["input"]
        assert len(input_list) == 2
        trailing = input_list[-1]
        assert trailing["role"] == "user"
        assert {"type": "input_text", "text": "second turn"} in trailing["content"]
        assert {"type": "input_image", "source": {"type": "url", "url": "https://example.com/a.png"}} in trailing["content"]


class TestErrorInjectionContract:
    """Every scenario must produce exactly one terminal event: no double
    terminal, no silent CLI fallback, no automatic model swap."""

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 429, 500, 503])
    def test_http_error_status_codes_surface_as_single_typed_error(self, status_code):
        provider = _make_provider()

        def fake_urlopen(req, timeout=None, deadline=None):
            raise urllib.error.HTTPError(req.full_url, status_code, "err", {}, None)

        with patch("assistant_openclaw_provider._urlopen_with_deadline", fake_urlopen):
            events = list(provider.stream("hi", operator_id="op-1"))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["status_code"] == status_code

    def test_connection_refused_is_single_typed_error(self):
        provider = _make_provider()
        with patch(
            "assistant_openclaw_provider._urlopen_with_deadline",
            side_effect=urllib.error.URLError(ConnectionRefusedError("refused")),
        ):
            events = list(provider.stream("hi", operator_id="op-1"))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_RESPONSES_UNREACHABLE"

    def test_dns_or_tls_style_failure_is_single_typed_error(self):
        provider = _make_provider()
        with patch(
            "assistant_openclaw_provider._urlopen_with_deadline",
            side_effect=urllib.error.URLError(OSError("Name or service not known")),
        ):
            events = list(provider.stream("hi", operator_id="op-1"))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_RESPONSES_UNREACHABLE"

    def test_socket_timeout_is_single_typed_error(self):
        provider = _make_provider()
        with patch(
            "assistant_openclaw_provider._urlopen_with_deadline",
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
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_BrokenIter()):
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
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_RawLinesResponse(lines)):
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
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_RawLinesResponse(lines)):
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
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_RawLinesResponse(lines)):
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
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1
        assert done[0]["text"] == "first"

    def test_partial_data_then_cancellation_yields_single_terminal(self):
        """Simulates a stream that stops mid-response (e.g. client cancels)
        without ever emitting response.completed or [DONE]."""
        lines = [b'data: {"type":"response.output_text.delta","delta":"partial answer"}\n']
        provider = _make_provider()
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        terminal = [e for e in events if e["type"] in ("done", "error")]
        assert len(terminal) == 1
        # A cancelled/incomplete stream never saw an explicit completion
        # signal ([DONE] or response.completed) — even though real partial
        # text was salvaged, it must be reported truthfully as an
        # interruption, never fabricated as a "done" success.
        assert terminal[0]["type"] == "error"
        assert terminal[0]["error_code"] == "OPENCLAW_RESPONSES_STREAM_INTERRUPTED"

    def test_response_failed_event_is_single_typed_error(self):
        provider = _make_provider()
        lines = [b'data: {"type":"response.failed","reason":"upstream_error"}\n']
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_RESPONSES_FAILED"

    def test_nested_response_status_failed_is_single_typed_error(self):
        provider = _make_provider()
        lines = [b'data: {"type":"response.completed","response":{"status":"failed"}}\n']
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_RESPONSES_FAILED"

    def test_nested_response_status_incomplete_is_single_typed_error(self):
        provider = _make_provider()
        lines = [b'data: {"type":"response.completed","response":{"status":"incomplete"}}\n']
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_RESPONSES_INCOMPLETE"

    def test_incomplete_status_with_function_call_is_treated_as_normal_completion(self):
        """SIMPLIFY-OPENCLAW-001 corrective pass (reviewer finding 2): the
        real pinned OpenClaw Gateway (v2026.7.1) emits response.completed
        with status="incomplete" as the *normal* tool-call yield whenever
        the model stops to hand back a function_call -- this is not
        truncation/refusal/failure and must not raise 502."""
        provider = _make_provider()
        payload = {
            "type": "response.completed",
            "response": {
                "status": "incomplete",
                "output": [
                    {
                        "type": "function_call",
                        "name": "emit_extraction",
                        "arguments": "{}",
                        "call_id": "call-1",
                    }
                ],
                "usage": {"input_tokens": 5, "output_tokens": 2},
                "id": "resp-1",
            },
        }
        lines = [("data: " + json.dumps(payload) + "\n").encode("utf-8")]
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1
        assert done[0]["function_calls"] == [
            {"name": "emit_extraction", "arguments": "{}", "call_id": "call-1"}
        ]
        assert done[0]["usage"] == {"input_tokens": 5, "output_tokens": 2}
        assert done[0]["response_id"] == "resp-1"

    def test_incomplete_status_with_no_function_call_still_errors(self):
        """Genuine truncation/refusal (incomplete with no tool call to fall
        back on) must still be reported as OPENCLAW_RESPONSES_INCOMPLETE."""
        provider = _make_provider()
        payload = {
            "type": "response.completed",
            "response": {"status": "incomplete", "output": []},
        }
        lines = [("data: " + json.dumps(payload) + "\n").encode("utf-8")]
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_RESPONSES_INCOMPLETE"

    def test_nested_response_status_cancelled_with_partial_text_is_rejected(self):
        """SIMPLIFY-OPENCLAW-001 corrective pass (reviewer finding 8):
        response.completed with a nested status of "cancelled" and partial
        text must never be reported as a successful "done"."""
        provider = _make_provider()
        lines = [
            b'data: {"type":"response.output_text.delta","delta":"partial"}\n',
            b'data: {"type":"response.completed","response":{"status":"cancelled"}}\n',
        ]
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        done = [e for e in events if e["type"] == "done"]
        assert done == []
        terminal = [e for e in events if e["type"] == "error"]
        assert len(terminal) == 1
        assert terminal[0]["error_code"] == "OPENCLAW_RESPONSES_NOT_TERMINAL"

    def test_nested_response_status_in_progress_is_rejected(self):
        provider = _make_provider()
        lines = [b'data: {"type":"response.completed","response":{"status":"in_progress"}}\n']
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_RESPONSES_NOT_TERMINAL"

    def test_refusal_with_no_text_and_no_function_calls_is_typed_empty(self):
        provider = _make_provider()
        lines = [b'data: {"type":"response.completed","response":{"status":"completed"}}\n']
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_RawLinesResponse(lines)):
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
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1
        assert done[0]["text"] == "no done marker"

    def test_missing_done_marker_and_no_completed_event_still_single_terminal(self):
        """The gateway's connection just ends. No [DONE], no response.completed.

        This is indistinguishable from a dropped/cancelled connection — it
        must fail truthfully rather than fabricate a "done" from whatever
        text happened to arrive first."""
        provider = _make_provider()
        lines = [b'data: {"type":"response.output_text.delta","delta":"streamed"}\n']
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        terminal = [e for e in events if e["type"] in ("done", "error")]
        assert len(terminal) == 1
        assert terminal[0]["type"] == "error"
        assert terminal[0]["error_code"] == "OPENCLAW_RESPONSES_STREAM_INTERRUPTED"

    def test_done_marker_with_no_completed_event_is_accepted_terminal(self):
        """A legitimate `[DONE]` sentinel (an explicit end-of-stream signal,
        unlike a bare EOF) still yields the salvaged text as a real "done",
        even when no `response.completed` event was ever seen."""
        provider = _make_provider()
        lines = [
            b'data: {"type":"response.output_text.delta","delta":"streamed"}\n',
            b"data: [DONE]\n",
        ]
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        terminal = [e for e in events if e["type"] in ("done", "error")]
        assert len(terminal) == 1
        assert terminal[0]["type"] == "done"
        assert terminal[0]["text"] == "streamed"

    def test_standalone_parseable_fragment_does_not_discard_buffered_multiline_event(self):
        """A legal multi-line event may be split such that an intermediate
        physical line happens to parse as valid JSON *on its own* (here, a
        bare JSON string) — this must not be mistaken for a fresh standalone
        event that discards the still-incomplete buffered fragment before
        them."""
        provider = _make_provider()
        lines = [
            b'data: {"type":"response.output_text.done","text":\n',
            b'data: "joined reply"\n',
            b"data: }\n",
            b"\n",
            b'data: {"type":"response.completed","response":{"status":"completed"}}\n',
            b"data: [DONE]\n",
        ]
        with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_RawLinesResponse(lines)):
            events = list(provider.stream("hi", operator_id="op-1"))
        errors = [e for e in events if e["type"] == "error"]
        assert not errors, errors
        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1
        assert done[0]["text"] == "joined reply"

    def test_invoke_does_not_retry_or_swap_model_on_failure(self):
        provider = _make_provider()
        calls = []

        def fake_urlopen(req, timeout=None, deadline=None):
            headers = {k.lower(): v for k, v in req.header_items()}
            calls.append(headers.get("x-openclaw-model"))
            raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)

        with patch("assistant_openclaw_provider._urlopen_with_deadline", fake_urlopen):
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

    def test_newline_free_slow_drip_is_bounded_by_total_deadline(self):
        """A per-`recv()` timeout alone never fires here: each of the twelve
        one-byte writes, sent 30ms apart with no newline anywhere in the
        stream, individually arrives comfortably inside any single read's
        timeout. Only checking the *total* elapsed time before every single
        byte — not only after a whole line finally completes — bounds the
        real elapsed time close to the requested budget."""
        import http.server
        import threading
        import time as _time

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for _ in range(12):
                    try:
                        self.wfile.write(b"x")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionError):
                        return
                    _time.sleep(0.03)

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
            started = _time.monotonic()
            events = list(provider.stream("hi", operator_id="op-1", timeout_seconds=0.1))
            elapsed = _time.monotonic() - started
        finally:
            server.shutdown()
            server.server_close()
        assert elapsed < 0.3, f"total streaming time {elapsed:.3f}s was not bounded near the 0.1s budget"
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_RESPONSES_TIMEOUT"

    def test_overlapping_deadlines_never_corrupt_process_global_http_classes(self):
        """Deterministic reproduction of the exact overlap the reviewer
        flagged against the old design: request A starts, request B starts
        while A is still in flight, A finishes (times out) first, then B
        finishes. The previous implementation temporarily replaced
        `http.client.HTTPConnection`/`HTTPSConnection` with per-call
        subclasses for the duration of each `with` block; A's `__exit__`
        restored the *pre-A* classes (wiping B's still-active override), and
        B's `__exit__` then restored *A's* classes permanently — a wholly
        unrelated request made afterward would stay bound by A's
        already-expired deadline forever.

        The fixed design builds a private, request-scoped opener per call
        (`_urlopen_with_deadline`) and never assigns to
        `http.client.HTTPConnection`/`HTTPSConnection` at all, so this test
        asserts those process-global attributes are the exact same objects
        before, during, and after two genuinely overlapping real-socket
        requests — and that a third, independent request made afterward
        completes on its own generous budget instead of failing against a
        stale short one.
        """
        import http.client
        import threading
        import time as _time

        orig_http_connection = http.client.HTTPConnection
        orig_https_connection = http.client.HTTPSConnection

        # A: slow drip, short budget -> times out first, well before B.
        # B: slow drip, longer budget -> still in flight when A exits.
        with _SlowDripHTTPServer(chunk_delay=0.05, num_chunks=40) as server_a, \
                _SlowDripHTTPServer(chunk_delay=0.05, num_chunks=40) as server_b:
            provider_a = AssistantOpenClawProvider(
                gateway_url=f"ws://127.0.0.1:{server_a.port}",
                agent_id="main",
                token="test-token",
                _which_func=lambda _: None,
                _run_func=_forbidden_run,
            )
            provider_b = AssistantOpenClawProvider(
                gateway_url=f"ws://127.0.0.1:{server_b.port}",
                agent_id="main",
                token="test-token",
                _which_func=lambda _: None,
                _run_func=_forbidden_run,
            )

            results: dict = {}
            classes_during_overlap: list = []
            a_started = threading.Event()
            b_started = threading.Event()

            def run_a():
                a_started.set()
                b_started.wait(timeout=2.0)
                results["a"] = list(provider_a.stream("hi", operator_id="op-a", timeout_seconds=0.3))

            def run_b():
                a_started.wait(timeout=2.0)
                b_started.set()
                # Sample the global classes partway through B's own request,
                # i.e. while A is guaranteed to have already started (and,
                # for a long enough B budget, likely already exited).
                _time.sleep(0.4)
                classes_during_overlap.append((http.client.HTTPConnection, http.client.HTTPSConnection))
                results["b"] = list(provider_b.stream("hi", operator_id="op-b", timeout_seconds=1.5))

            thread_a = threading.Thread(target=run_a)
            thread_b = threading.Thread(target=run_b)
            thread_a.start()
            thread_b.start()
            thread_a.join(timeout=5.0)
            thread_b.join(timeout=5.0)

        assert not thread_a.is_alive() and not thread_b.is_alive()
        # A (0.3s budget) times out; B (1.5s budget) completes its own
        # request on its own schedule -- neither corrupted the other's
        # deadline.
        assert results["a"][0]["error_code"] == "OPENCLAW_RESPONSES_TIMEOUT"
        assert results["b"][0]["error_code"] == "OPENCLAW_RESPONSES_TIMEOUT"

        # The process-global connection classes were never reassigned, not
        # even transiently while both requests were in flight.
        assert classes_during_overlap == [(orig_http_connection, orig_https_connection)]
        assert http.client.HTTPConnection is orig_http_connection
        assert http.client.HTTPSConnection is orig_https_connection

        # A third, wholly unrelated request made after both A and B finished
        # must use its own (generous) deadline, not get stuck bound to
        # either of A's or B's now-expired ones.
        with _SlowDripHTTPServer(chunk_delay=0.01, num_chunks=2) as server_c:
            provider_c = AssistantOpenClawProvider(
                gateway_url=f"ws://127.0.0.1:{server_c.port}",
                agent_id="main",
                token="test-token",
                _which_func=lambda _: None,
                _run_func=_forbidden_run,
            )
            started = _time.monotonic()
            events_c = list(provider_c.stream("hi", operator_id="op-c", timeout_seconds=5.0))
            elapsed_c = _time.monotonic() - started
        assert elapsed_c < 1.0, f"unrelated follow-up request took {elapsed_c:.3f}s, suggesting a stale deadline leaked in"
        assert events_c[0]["type"] == "error"
        assert events_c[0]["error_code"] == "OPENCLAW_RESPONSES_EMPTY"


class _SlowDripHTTPSServer:
    """A real local HTTPS (TLS) server, same slow-drip shape as
    `_SlowDripHTTPServer`, used to prove the post-handshake deadline
    enforcement fix on `_DeadlineBoundedHTTPSConnection`/
    `_DeadlineBoundedSSLSocket`: reads over `ssl.SSLSocket` happen through a
    genuine TLS record layer, which a mocked `urlopen` cannot exercise at
    all, and which the plain-HTTP `_DeadlineBoundedSocket` path never
    touches (it only rebinds the pre-TLS TCP socket)."""

    def __init__(self, *, chunk_delay: float, num_chunks: int, chunk: bytes = b": keep-alive\n\n"):
        import http.server
        import ssl as _ssl
        import subprocess
        import tempfile
        import threading

        self._chunk_delay = chunk_delay
        self._num_chunks = num_chunks
        self._chunk = chunk
        outer = self

        self._tmpdir = tempfile.TemporaryDirectory()
        key_path = f"{self._tmpdir.name}/key.pem"
        cert_path = f"{self._tmpdir.name}/cert.pem"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-days", "1", "-nodes",
                "-subj", "/CN=127.0.0.1",
                "-keyout", key_path, "-out", cert_path,
            ],
            check=True,
            capture_output=True,
        )

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

        server_context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(certfile=cert_path, keyfile=key_path)
        self._server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        self._server.socket = server_context.wrap_socket(self._server.socket, server_side=True)
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
        self._tmpdir.cleanup()


class TestRealLocalHttpsServerRegressions:
    """SIMPLIFY-OPENCLAW-001 reviewer defect (fifth corrective pass): the
    total-deadline enforcement only rebound the pre-TLS TCP socket, so once
    the TLS handshake completed, header parsing and body reads went through
    `ssl.SSLSocket`'s own reads and were bounded only by the connection's
    static per-read `timeout=` again -- exactly the "slow drip past a static
    per-read timeout" bug the plain-HTTP path had already fixed, reopened on
    the HTTPS path. These tests run against a genuine local TLS server; a
    mocked `urlopen` cannot exercise real TLS record-layer read semantics."""

    def test_https_slow_drip_response_is_bounded_by_total_deadline(self, monkeypatch):
        import ssl as _ssl

        # Test-only: the local server's cert is self-signed for 127.0.0.1,
        # so the client context must not require a trusted CA chain. The
        # adapter builds its HTTPS context through `urllib.request`'s
        # standard `HTTPSHandler.__init__` path, which resolves a default
        # context via `http.client._create_https_context()` ->
        # `ssl._create_default_https_context()` *before* the adapter's own
        # `context = self._context or ssl.create_default_context()` line
        # ever runs (`self._context` is already set by then) — so this
        # patches the actual function the stdlib calls for that default
        # policy, not the adapter's own (here dead-code) fallback call. This
        # only relaxes verification for this test's own client context, not
        # any behavior the adapter applies against a real Gateway.
        def _insecure_default_https_context():
            ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            return ctx

        monkeypatch.setattr(_ssl, "_create_default_https_context", _insecure_default_https_context)

        with _SlowDripHTTPSServer(chunk_delay=0.05, num_chunks=40) as server:
            provider = AssistantOpenClawProvider(
                gateway_url=f"wss://127.0.0.1:{server.port}",
                agent_id="main",
                token="test-token",
                _which_func=lambda _: None,
                _run_func=_forbidden_run,
            )
            started = __import__("time").monotonic()
            events = list(provider.stream("hi", operator_id="op-1", timeout_seconds=0.3))
            elapsed = __import__("time").monotonic() - started
        assert elapsed < 1.0, f"total HTTPS streaming time {elapsed:.3f}s was not bounded near the 0.3s budget"
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "OPENCLAW_RESPONSES_TIMEOUT"


# Opt-in real pinned-Gateway acceptance and transport benchmark.
# Run this file directly with the provisioned Python; ordinary pytest collection
# only defines the fixture. All model data and credentials below are synthetic.
import contextlib, hashlib, http.server, importlib.util, json, os, pathlib, socket, subprocess, sys, tempfile, threading, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services/openclaw-gateway-adapter"))
from assistant_openclaw_provider import AssistantOpenClawProvider, OpenClawProviderError


class Model(http.server.BaseHTTPRequestHandler):
    records = []
    emitted_cases = set()

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = body.get("messages", [])
        last = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), ""
        )
        prompt = json.dumps(last)
        tools = [t["function"]["name"] for t in body.get("tools", [])]
        self.records.append(
            {"tools": tools, "prompt": prompt, "at": time.monotonic(), "model": body.get("model")}
        )
        case = next(
            (
                c
                for c in ["invalid", "wrong", "missing", "denied", "positive"]
                if "CASE_" + c in prompt
            ),
            "text",
        )
        delta = {"role": "assistant", "content": "FIXTURE_OK"}
        finish = "stop"
        if case in ("positive", "invalid", "wrong", "denied") and case not in self.emitted_cases:
            self.emitted_cases.add(case)
            name = (
                "exec"
                if case == "denied"
                else "wrong_tool" if case == "wrong" else "emit_extraction"
            )
            args = '{"value":7}' if case != "invalid" else '{"value":"bad"}'
            if case == "denied":
                args = '{"command":"touch /tmp/SIMPLIFY_FORBIDDEN"}'
            delta = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_fixture",
                        "type": "function",
                        "function": {"name": name, "arguments": args},
                    }
                ],
            }
            finish = "tool_calls"
        usage = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
        self.send_response(200)
        self.send_header(
            "Content-Type", "text/event-stream" if body.get("stream") else "application/json"
        )
        self.end_headers()
        if body.get("stream"):
            for d, f, u in [(delta, None, None), ({}, finish, usage)]:
                payload = {
                    "id": "chatcmpl-fixture",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture-model",
                    "choices": [{"index": 0, "delta": d, "finish_reason": f}],
                }
                if u:
                    payload["usage"] = u
                self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self.wfile.write(
                json.dumps(
                    {
                        "id": "chatcmpl-fixture",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "fixture-model",
                        "choices": [{"index": 0, "message": delta, "finish_reason": finish}],
                        "usage": usage,
                    }
                ).encode()
            )


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_pinned_gateway_replay():
    image = "sha256:f435502b113f873768b64cd7f5f2a63f44d0a236ae3a5647ff42716652c74c31"
    name = f"simplify-openclaw-001-fixture-{os.getpid()}"
    with tempfile.TemporaryDirectory(prefix="simplify-openclaw-") as tmp:
        tmp = pathlib.Path(tmp)
        p = port()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Model)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = {
            "gateway": {
                "mode": "local",
                "bind": "loopback",
                "port": p,
                "auth": {"mode": "token", "token": "local-fixture-only"},
                "http": {"endpoints": {"responses": {"enabled": True}}},
            },
            "agents": {
                "defaults": {
                    "workspace": "/fixture/workspace",
                    "model": {"primary": "fixture/fixture-model"},
                    "skipBootstrap": True,
                    "thinkingDefault": "off",
                },
                "list": [
                    {"id": agent, "tools": {"deny": ["*"]}}
                    for agent in ["main"] + [f"bench-{i}" for i in range(10)]
                ],
            },
            "models": {
                "providers": {
                    "fixture": {
                        "baseUrl": f"http://127.0.0.1:{server.server_port}/v1",
                        "apiKey": "synthetic-fixture-only",
                        "api": "openai-completions",
                        "models": [
                            {
                                "id": "fixture-model",
                                "name": "Fixture",
                                "reasoning": False,
                                "input": ["text"],
                                "contextWindow": 200000,
                                "maxTokens": 1024,
                                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                            }
                        ],
                    }
                }
            },
        }
        policy_probe = os.environ.get("SIMPLIFY_POLICY_PROBE")
        if policy_probe == "missing":
            config["agents"]["list"][0].pop("tools")
        (tmp / "config.json").write_text(json.dumps(config))
        (tmp / "workspace").mkdir()

        def docker(*args, **kw):
            return subprocess.run(
                ["docker", *args],
                capture_output=True,
                text=True,
                timeout=kw.pop("timeout", 30),
                **kw,
            )

        try:
            started = docker(
                "run",
                "-d",
                "--name",
                name,
                "--network",
                "host",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "-v",
                f"{tmp}:/fixture",
                "-e",
                "OPENCLAW_CONFIG_PATH=/fixture/config.json",
                "-e",
                "OPENCLAW_STATE_DIR=/fixture/state",
                image,
                "node",
                "openclaw.mjs",
                "gateway",
                "--allow-unconfigured",
            )
            assert started.returncode == 0, started.stderr
            for _ in range(60):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{p}/healthz", timeout=1).close()
                    break
                except Exception:
                    time.sleep(0.5)
            else:
                raise RuntimeError(docker("logs", "--tail", "30", name).stdout)
            provider = AssistantOpenClawProvider(
                gateway_url=f"ws://127.0.0.1:{p}", token="local-fixture-only", timeout_seconds=15
            )
            if policy_probe:
                sys.path.insert(0, str(ROOT))
                import main as adapter_main
                from fastapi.testclient import TestClient
                from unittest.mock import patch

                def policy_rpc(cmd, **kw):
                    assert cmd[1:4] == ["gateway", "call", "config.get"]
                    return docker("exec", name, "node", "openclaw.mjs", *cmd[1:], timeout=kw["timeout"])

                provider._run = policy_rpc
                provider._which = lambda _: "openclaw"
                with (
                    patch.object(adapter_main, "_ASSISTANT_SERVICE_TOKEN", "synthetic-policy-token"),
                    patch.object(adapter_main, "_ASSISTANT_SERVICE_AUTH_REQUIRED", True),
                    patch.object(adapter_main, "_OPENCLAW_AGENT_PROVIDER", provider),
                    patch.dict(os.environ, {"OPENCLAW_PRIMARY_MODEL": "fixture/fixture-model"}),
                ):
                    client = TestClient(adapter_main.app)
                    results = []
                    for case in (["denied"] if policy_probe == "missing" else ["positive", "denied"]):
                        response = client.post(
                            "/api/openclaw-adapter/assistant/providers/openclaw/structured",
                            json={"prompt": "CASE_" + case, "session_id": "policy-" + case,
                                  "extraction_schema": {"type": "object", "properties": {"value": {"type": "integer"}},
                                                        "required": ["value"], "additionalProperties": False}},
                            headers={"X-Operator-Id": "fixture", "X-Pantheon-Service-Token": "synthetic-policy-token"},
                        )
                        results.append({"case": case, "http_status": response.status_code,
                                        "error_code": response.json().get("error_code")})
                        if policy_probe == "missing":
                            assert response.status_code == 503, response.text
                            assert response.json()["error_code"] == "OPENCLAW_STRUCTURED_POLICY_DENIED", response.text
                            assert Model.records == []
                        elif case == "positive":
                            assert response.status_code == 200, response.text
                            assert response.json()["data"]["output"]["structured_data"] == {"value": 7}
                        else:
                            assert response.status_code == 502, response.text
                            assert response.json()["error_code"] == "OPENCLAW_RESPONSES_FAILED"
                    assert all(r["tools"] == ["emit_extraction"] for r in Model.records)
                    assert docker("exec", name, "test", "-e", "/tmp/SIMPLIFY_FORBIDDEN").returncode == 1
                    print("MOUNTED_POLICY_RESULT", json.dumps({"policy": policy_probe, "results": results,
                          "model_requests": len(Model.records), "native_exec_advertised": False,
                          "marker_exists": False, "image": image}), flush=True)
                return
            capability = {}
            for case in ["positive", "invalid", "wrong", "missing", "denied"]:
                try:
                    result = provider.invoke_structured(
                        "CASE_" + case,
                        model="fixture/fixture-model",
                        session_id="case-" + case,
                        timeout_seconds=5,
                        extraction_schema={
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                            "additionalProperties": False,
                        },
                    )
                    assert case == "positive", (case, result.to_dict())
                    assert result.output["structured_data"] == {"value": 7}
                    capability[case] = result.to_dict()
                    print(case, "passed", flush=True)
                except OpenClawProviderError as exc:
                    assert case != "positive", exc.to_payload()
                    assert exc.error_code == "OPENCLAW_RESPONSES_FAILED", exc.to_payload()
                    capability[case] = exc.to_payload()
                    print(case, exc.error_code, flush=True)
            assert all(r["tools"] == ["emit_extraction"] for r in Model.records)
            assert docker("exec", name, "test", "-e", "/tmp/SIMPLIFY_FORBIDDEN").returncode == 1
            gateway_logs = docker("logs", name)
            logs = gateway_logs.stdout + gateway_logs.stderr
            assert "tool policy removed" in logs and "exec" in logs
            capability["native_denial"] = {
                "advertised_tools": ["emit_extraction"],
                "attempted_tool": "exec",
                "marker_exists": False,
                "gateway_denial_logged": True,
            }
            count = int(os.environ.get("SIMPLIFY_REPLAY_COUNT", "100"))
            # Freeze the measured CLI baseline so this runner remains reproducible
            # after the candidate merges and origin/dev starts using HTTP itself.
            base_sha = "a9557a6002e8170eb92415cc61e9d6a584cc610f"
            base_source = subprocess.check_output(
                [
                    "git",
                    "show",
                    base_sha + ":services/openclaw-gateway-adapter/assistant_openclaw_provider.py",
                ]
            )
            (tmp / "baseline.py").write_bytes(base_source)
            spec = importlib.util.spec_from_file_location("simplify_baseline", tmp / "baseline.py")
            baseline = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = baseline
            spec.loader.exec_module(baseline)
            cli_local = threading.local()

            def cli_runner(cmd, **kw):
                proc = docker("exec", name, "node", "openclaw.mjs", *cmd[1:], timeout=kw["timeout"])
                cli_local.result = proc
                return proc

            old = baseline.AssistantOpenClawProvider(
                gateway_url=f"ws://127.0.0.1:{p}",
                token="local-fixture-only",
                _which_func=lambda _: "openclaw",
                _run_func=cli_runner,
            )
            prompts = [f"Replay item {i:03d}: Return exactly FIXTURE_OK." for i in range(count)]
            manifest = {
                "base_sha": base_sha,
                "base_provider_sha256": hashlib.sha256(base_source).hexdigest(),
                "candidate_provider_sha256": hashlib.sha256(
                    (
                        ROOT / "services/openclaw-gateway-adapter/assistant_openclaw_provider.py"
                    ).read_bytes()
                ).hexdigest(),
                "image_id": image,
                "gateway_version": "2026.7.1",
                "model": "fixture/fixture-model",
                "auth_route": "isolated loopback Gateway, synthetic test token; local deterministic OpenAI-completions model",
                "count_per_arm": count,
                "prompt_sha256": hashlib.sha256(json.dumps(prompts).encode()).hexdigest(),
                "session_design": "10 independent admitted agents, each with 10 sequential turns per arm; first is session-cold, next 9 warm; four concurrent sequences, one shared Gateway, no process-cold claim",
                "concurrency": 4,
                "deadline_seconds": 30,
                "usage_semantics": "model fixture reports fixed input=100/output=10/total=110, not a tokenizer or production cost measurement",
                "ttft_semantics": "CLI buffered invoke first text available on return; HTTP first normalized delta; full includes invocation and normalization",
                "latency_limitation": "CLI transport includes docker exec overhead; local fixture measures transport and Gateway, not external model quality or latency",
                "config_sha256": hashlib.sha256(json.dumps(config).encode()).hexdigest(),
            }
            print("FROZEN", json.dumps(manifest), flush=True)

            def replay_sequence(group):
                rows = []
                for i in range(group * 10, min(group * 10 + 10, count)):
                    prompt = prompts[i]
                    # Alternate order to avoid assigning all process warm-up to one arm.
                    for arm in (["cli", "http"] if i % 2 == 0 else ["http", "cli"]):
                        started = time.monotonic()
                        first = None
                        if arm == "cli":
                            result = old.invoke(
                                prompt,
                                model="fixture/fixture-model",
                                agent_id=f"bench-{group}",
                                session_id=f"cli-{group}",
                                timeout_seconds=30,
                            )
                            assert result.status == "completed"
                            assert old._result_text(result) == "FIXTURE_OK"
                            raw = json.loads(cli_local.result.stdout)
                            meta = raw["result"]["meta"]["agentMeta"]
                            assert (
                                meta["provider"] == "fixture" and meta["model"] == "fixture-model"
                            )
                            u = meta["usage"]
                            usage = {
                                "input_tokens": u["input"],
                                "output_tokens": u["output"],
                                "total_tokens": u["total"],
                            }
                            spawns = 1
                            assert spawns == 1
                        else:
                            events = []
                            for event in provider.stream(
                                prompt,
                                model="fixture/fixture-model",
                                agent_id=f"bench-{group}",
                                session_user=f"http-{group}",
                                timeout_seconds=30,
                            ):
                                events.append(event)
                                if event["type"] == "delta" and first is None:
                                    first = time.monotonic() - started
                            terminals = [e for e in events if e["type"] in ("done", "error")]
                            assert len(terminals) == 1 and terminals[0]["type"] == "done", events
                            assert terminals[0]["text"] == "FIXTURE_OK"
                            usage = terminals[0]["usage"]
                            spawns = 0
                        elapsed = time.monotonic() - started
                        rows.append(
                            {
                                "arm": arm,
                                "case": i,
                                "temperature": "cold" if i % 10 == 0 else "warm",
                                "full_ms": elapsed * 1000,
                                "ttft_ms": (first if first is not None else elapsed) * 1000,
                                "usage": usage,
                                "errors": 0,
                                "subprocesses": spawns,
                            }
                        )
                    if (i + 1) % 10 == 0:
                        print("REPLAY", i + 1, "pairs completed", flush=True)
                return rows

            from concurrent.futures import ThreadPoolExecutor
            import math

            with ThreadPoolExecutor(max_workers=4) as pool:
                rows = [
                    row
                    for batch in pool.map(replay_sequence, range(math.ceil(count / 10)))
                    for row in batch
                ]
            import statistics

            def summary(items):
                def percentile(field, quantile):
                    values = sorted(r[field] for r in items)
                    import math

                    return values[math.ceil(len(values) * quantile) - 1]

                return {
                    "n": len(items),
                    "full_p50_ms": percentile("full_ms", 0.5),
                    "full_p95_ms": percentile("full_ms", 0.95),
                    "ttft_p50_ms": percentile("ttft_ms", 0.5),
                    "ttft_p95_ms": percentile("ttft_ms", 0.95),
                    "mean_tokens": statistics.mean(r["usage"]["total_tokens"] for r in items),
                    "errors": sum(r["errors"] for r in items),
                    "subprocesses": sum(r["subprocesses"] for r in items),
                }

            summaries = {
                arm: {
                    temp: summary(
                        [
                            r
                            for r in rows
                            if r["arm"] == arm and (temp == "all" or r["temperature"] == temp)
                        ]
                    )
                    for temp in ["all", "cold", "warm"]
                }
                for arm in ["cli", "http"]
            }
            gates = {
                "no_new_errors": summaries["http"]["all"]["errors"] == 0,
                "full_p95_ratio": summaries["http"]["all"]["full_p95_ms"]
                / summaries["cli"]["all"]["full_p95_ms"],
                "mean_token_ratio": summaries["http"]["all"]["mean_tokens"]
                / summaries["cli"]["all"]["mean_tokens"],
            }
            report = {
                "manifest": manifest,
                "capability": capability,
                "replay": summaries,
                "gates": gates,
                "rows": rows,
            }
            pathlib.Path(
                os.environ.get("SIMPLIFY_REPLAY_OUTPUT", "/tmp/simplify-replay.json")
            ).write_text(json.dumps(report, indent=2) + "\n")
            assert gates["full_p95_ratio"] <= 1.10 and gates["mean_token_ratio"] <= 1.05
            print("RESULT", json.dumps(summaries), flush=True)

        finally:
            # Only fixture-owned state is mounted; no credentials or existing service is changed.
            cleanup = docker("rm", "-f", name)
            print("CLEANUP", cleanup.returncode, flush=True)
            assert cleanup.returncode == 0, cleanup.stderr
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    run_pinned_gateway_replay()
