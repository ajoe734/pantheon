"""Unit tests for OpenClawOpsClient.stream_assistant_provider (adapter SSE relay)."""
from __future__ import annotations

import json
from unittest import mock

from openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError


def _client():
    return OpenClawOpsClient(base_url="http://openclaw-gateway-adapter:8104")


def test_stream_yields_parsed_events_and_strips_done():
    sse = [
        b'data: {"type": "delta", "text": "\xe4\xbd\xa0"}\n',
        b"\n",
        b'data: {"type": "delta", "text": "\xe5\xa5\xbd"}\n',
        b'data: {"type": "done", "text": "\xe4\xbd\xa0\xe5\xa5\xbd", "transport": "responses_http"}\n',
        b"data: [DONE]\n",
        b'data: {"type": "delta", "text": "AFTER-DONE"}\n',
    ]

    class FakeResp:
        def __iter__(self):
            return iter(sse)

        def close(self):
            pass

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["op"] = req.headers.get("X-operator-id")
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResp()

    with mock.patch("urllib.request.urlopen", fake_urlopen):
        events = list(
            _client().stream_assistant_provider(
                mode="user",
                prompt="hi",
                context_pack={"k": "v"},
                operator_id="op-1",
                session_user="sess-9",
            )
        )

    assert captured["url"].endswith("/api/openclaw-adapter/assistant/providers/openclaw/invoke/stream")
    assert captured["op"] == "op-1"
    assert captured["body"]["metadata"]["session_user"] == "sess-9"
    types = [e["type"] for e in events]
    # stops at [DONE]; the post-DONE delta is never yielded
    assert types == ["delta", "delta", "done"]
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "你好"


def test_stream_raises_when_adapter_url_unset():
    client = OpenClawOpsClient(base_url="")
    try:
        list(client.stream_assistant_provider(mode="user", prompt="hi", context_pack={}, operator_id="op-1"))
        assert False, "expected OpenClawOpsClientError"
    except OpenClawOpsClientError as exc:
        assert exc.error_code == "OPENCLAW_ADAPTER_URL_NOT_CONFIGURED"
