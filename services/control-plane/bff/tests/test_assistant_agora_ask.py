"""Tests for ASST-BFF-001: provider-backed /bff/agora/ask flow.

Acceptance criteria covered:
1. Feature flag disabled (default): route returns 202 with provider.status=disabled.
2. Feature flag enabled, provider succeeds: 202, provider.status=completed, answer
   present, ask.message.delta and ask.message.completed in SSE buffer, user and
   assistant turns in transcript store.
3. Feature flag enabled, provider degraded (client error): 202, provider.status=
   degraded, deterministic fallback answer, SSE events emitted.
4. Feature flag enabled, provider degraded (adapter not configured): 202,
   provider.status=degraded, fallback answer.
5. Idempotency replay: second POST with same Idempotency-Key returns cached result
   without re-invoking the provider.
6. Transcript readback: turns recorded during ask are retrievable from transcript store.
"""
from __future__ import annotations

import os
import sys
import tempfile
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.control_plane.bff import main as bff_main
from openclaw_ops_client import OpenClawOpsClientError
from ports import create_in_memory_read_surface_ports

OPERATOR_HEADERS = {"Authorization": "Bearer op-ask:operator"}


def _fresh_client(td: str) -> TestClient:
    sessions_map: dict[str, dict] = {}
    store = create_in_memory_read_surface_ports()

    def get_agora_session(session_id: str | None) -> dict | None:
        return sessions_map.get(session_id or "")

    def create_agora_session(
        *,
        session_id: str,
        title: str,
        actor_id: str,
        payload: dict,
        created_at: str | None = None,
    ) -> dict:
        timestamp = created_at or "2026-08-29T00:00:00Z"
        session = {
            "id": session_id,
            "sessionId": session_id,
            "title": title,
            "mode": payload.get("mode") or payload.get("sessionType") or "quick_ask",
            "status": payload.get("status") or "active",
            "participants": list(payload.get("participants") or []),
            "contextRefs": list(payload.get("contextRefs") or payload.get("context_refs") or []),
            "messages": list(payload.get("messages") or []),
            "createdBy": actor_id,
            "createdAt": timestamp,
            "updatedAt": timestamp,
        }
        sessions_map[session_id] = session
        return session

    def list_agora_session_messages(session_id: str) -> list[dict] | None:
        session = get_agora_session(session_id)
        if session is None:
            return None
        return list(session.get("messages") or [])

    def append_agora_session_message(
        session_id: str,
        *,
        message_id: str,
        content: str,
        actor_id: str,
        payload: dict,
        created_at: str | None = None,
    ) -> dict | None:
        session = get_agora_session(session_id)
        if session is None:
            return None
        timestamp = created_at or "2026-08-29T00:00:00Z"
        message = {
            "id": message_id,
            "sessionId": session_id,
            "sender": payload.get("sender") or {"type": "operator", "id": actor_id},
            "role": payload.get("role") or "user",
            "content": content,
            "language": payload.get("language") or "zh-TW",
            "attachments": list(payload.get("attachments") or []),
            "citations": list(payload.get("citations") or []),
            "annotations": list(payload.get("annotations") or []),
            "createdAt": timestamp,
        }
        session.setdefault("messages", []).append(message)
        session["updatedAt"] = timestamp
        return message

    store.get_agora_session = get_agora_session
    store.create_agora_session = create_agora_session
    store.list_agora_session_messages = list_agora_session_messages
    store.append_agora_session_message = append_agora_session_message
    store.list_events_bff = lambda **kw: []
    store.list_persona_league = lambda **kw: []
    store.list_personas = lambda **kw: []
    store.list_strategy_summaries = lambda **kw: []
    store.list_strategy_specs = lambda **kw: []
    store.list_runtimes = lambda **kw: []
    store.list_runtime_instances = lambda **kw: []
    store.list_runtime_bindings = lambda **kw: []
    bff_main.read_store = store
    bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
    # Reset transcript store so turns don't bleed between tests
    from assistant.transcript_store import InMemorySessionStore, InMemoryTranscriptStore
    bff_main._ASSISTANT_SESSION_STORE = InMemorySessionStore()
    bff_main._ASSISTANT_TRANSCRIPT_STORE = InMemoryTranscriptStore()
    return TestClient(bff_main.app, raise_server_exceptions=True)


def _ask(client: TestClient, *, prompt: str = "What is the portfolio PnL?", ikey: str = "ask-ikey-001") -> Any:
    return client.post(
        "/bff/agora/ask",
        json={"prompt": prompt, "sessionId": f"sess-{ikey}"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": ikey},
    )


# ---------------------------------------------------------------------------
# AC#1 — feature flag disabled (default)
# ---------------------------------------------------------------------------


def test_ask_disabled_returns_202_with_disabled_provider_status() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            with patch.dict(os.environ, {"PANTHEON_ASSISTANT_ENABLED": "false"}):
                resp = _ask(client, ikey="ask-disabled-001")
            assert resp.status_code == 202, resp.text
            body = resp.json()
            assert body["status"] == "accepted"
            assert body["data"]["provider"]["status"] == "disabled"
            assert body["data"]["provider"]["answer"] is None
            assert body["meta"]["assistant"]["enabled"] is False
        finally:
            bff_main.read_store = original_store


# ---------------------------------------------------------------------------
# AC#2 — feature flag enabled, provider succeeds
# ---------------------------------------------------------------------------


def test_ask_enabled_provider_success_returns_answer_and_sse_events() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            mock_payload = {
                "status": "ok",
                "data": {
                    "provider": "codex_cli",
                    "mode": "user",
                    "status": "completed",
                    "output": "Portfolio PnL is +2.5% today.",
                    "redaction": {},
                },
            }
            mock_client = MagicMock()
            mock_client.configured = True
            mock_client.invoke_assistant.return_value = mock_payload

            with patch.dict(os.environ, {"PANTHEON_ASSISTANT_ENABLED": "true"}):
                with patch("main.OpenClawOpsClient", return_value=mock_client):
                    # Clear SSE buffer before call
                    bff_main._sse_buffers["ask"].clear()
                    resp = _ask(client, ikey="ask-success-001")

            assert resp.status_code == 202, resp.text
            body = resp.json()
            assert body["status"] == "accepted"
            assert body["data"]["provider"]["status"] == "completed"
            assert body["data"]["provider"]["answer"] == "Portfolio PnL is +2.5% today."
            assert body["meta"]["assistant"]["enabled"] is True
            assert body["meta"]["assistant"]["provider_status"] == "completed"

            # SSE buffer must contain delta and completed events
            sse_events = [evt for _, evt in bff_main._sse_buffers["ask"]]
            event_types = [e.get("type") for e in sse_events]
            assert "ask.message.delta" in event_types, f"ask.message.delta not in {event_types}"
            assert "ask.message.completed" in event_types, f"ask.message.completed not in {event_types}"

            delta_evt = next(e for e in sse_events if e.get("type") == "ask.message.delta")
            assert delta_evt["data"]["delta"] == "Portfolio PnL is +2.5% today."
            assert delta_evt["data"]["provider_status"] == "completed"

            completed_evt = next(e for e in sse_events if e.get("type") == "ask.message.completed")
            assert completed_evt["data"]["status"] == "completed"
        finally:
            bff_main.read_store = original_store


# ---------------------------------------------------------------------------
# AC#3 — feature flag enabled, provider raises OpenClawOpsClientError (degraded)
# ---------------------------------------------------------------------------


def test_ask_enabled_provider_client_error_returns_degraded_fallback() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            mock_client = MagicMock()
            mock_client.configured = True
            mock_client.invoke_assistant.side_effect = OpenClawOpsClientError(
                "Gateway adapter unreachable",
                status_code=503,
                error_code="OPENCLAW_ADAPTER_UNREACHABLE",
            )

            with patch.dict(os.environ, {"PANTHEON_ASSISTANT_ENABLED": "true"}):
                with patch("main.OpenClawOpsClient", return_value=mock_client):
                    bff_main._sse_buffers["ask"].clear()
                    resp = _ask(client, ikey="ask-degraded-001")

            assert resp.status_code == 202, resp.text
            body = resp.json()
            assert body["status"] == "accepted"
            assert body["data"]["provider"]["status"] == "degraded"
            # Fallback answer must be a non-empty string
            assert isinstance(body["data"]["provider"]["answer"], str)
            assert len(body["data"]["provider"]["answer"]) > 0

            sse_events = [evt for _, evt in bff_main._sse_buffers["ask"]]
            event_types = [e.get("type") for e in sse_events]
            assert "ask.message.delta" in event_types
            assert "ask.message.completed" in event_types

            completed_evt = next(e for e in sse_events if e.get("type") == "ask.message.completed")
            assert completed_evt["data"]["status"] == "degraded"
        finally:
            bff_main.read_store = original_store


# ---------------------------------------------------------------------------
# AC#4 — feature flag enabled, adapter not configured (degraded)
# ---------------------------------------------------------------------------


def test_ask_enabled_adapter_not_configured_returns_degraded() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            mock_client = MagicMock()
            mock_client.configured = False  # adapter URL not set

            with patch.dict(os.environ, {"PANTHEON_ASSISTANT_ENABLED": "true"}):
                with patch("main.OpenClawOpsClient", return_value=mock_client):
                    resp = _ask(client, ikey="ask-unconfigured-001")

            assert resp.status_code == 202, resp.text
            body = resp.json()
            assert body["data"]["provider"]["status"] == "degraded"
            assert isinstance(body["data"]["provider"]["answer"], str)
        finally:
            bff_main.read_store = original_store


# ---------------------------------------------------------------------------
# AC#5 — idempotency replay does not re-invoke the provider
# ---------------------------------------------------------------------------


def test_ask_idempotency_replay_returns_cached_without_reinvoking() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            invoke_count = {"n": 0}

            def _counted_invoke(**kwargs: Any) -> Dict[str, Any]:
                invoke_count["n"] += 1
                return {
                    "status": "ok",
                    "data": {
                        "provider": "codex_cli",
                        "mode": "user",
                        "status": "completed",
                        "output": "PnL is +1%.",
                        "redaction": {},
                    },
                }

            mock_client = MagicMock()
            mock_client.configured = True
            mock_client.invoke_assistant.side_effect = _counted_invoke

            headers = {**OPERATOR_HEADERS, "Idempotency-Key": "ask-replay-002"}
            payload = {"prompt": "PnL?", "sessionId": "sess-replay-002"}

            with patch.dict(os.environ, {"PANTHEON_ASSISTANT_ENABLED": "true"}):
                with patch("main.OpenClawOpsClient", return_value=mock_client):
                    resp1 = client.post("/bff/agora/ask", json=payload, headers=headers)
                    first_count = invoke_count["n"]
                    resp2 = client.post("/bff/agora/ask", json=payload, headers=headers)

            assert resp1.status_code == 202, resp1.text
            assert resp2.status_code == 202, resp2.text
            # Provider must not be re-invoked on replay
            assert invoke_count["n"] == first_count, "Provider re-invoked on idempotency replay"
            # Both responses must have the same session/message data
            assert resp1.json()["data"]["session"] == resp2.json()["data"]["session"]
        finally:
            bff_main.read_store = original_store


# ---------------------------------------------------------------------------
# AC#6 — transcript readback: turns stored after successful ask
# ---------------------------------------------------------------------------


def test_ask_enabled_transcript_has_user_and_assistant_turns() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            sid = "sess-transcript-001"
            mock_payload = {
                "status": "ok",
                "data": {
                    "provider": "codex_cli",
                    "mode": "user",
                    "status": "completed",
                    "output": "The answer is 42.",
                    "redaction": {},
                },
            }
            mock_client = MagicMock()
            mock_client.configured = True
            mock_client.invoke_assistant.return_value = mock_payload

            with patch.dict(os.environ, {"PANTHEON_ASSISTANT_ENABLED": "true"}):
                with patch("main.OpenClawOpsClient", return_value=mock_client):
                    resp = client.post(
                        "/bff/agora/ask",
                        json={"prompt": "What is the answer?", "sessionId": sid},
                        headers={**OPERATOR_HEADERS, "Idempotency-Key": "ask-transcript-001"},
                    )

            assert resp.status_code == 202, resp.text

            # Transcript store must have user and assistant turns
            transcript_store = bff_main._ASSISTANT_TRANSCRIPT_STORE
            assert transcript_store is not None, "Transcript store not initialised"
            turns = transcript_store.list_turns(sid)
            assert len(turns) == 2, f"Expected 2 turns, got {len(turns)}: {turns}"
            roles = [t.role.value for t in turns]
            assert "user" in roles, f"user turn missing: {roles}"
            assert "assistant" in roles, f"assistant turn missing: {roles}"
            user_turn = next(t for t in turns if t.role.value == "user")
            assert user_turn.content == "What is the answer?"
            asst_turn = next(t for t in turns if t.role.value == "assistant")
            assert asst_turn.content == "The answer is 42."
            # Both turns must carry context_pack_id for source readback
            assert user_turn.context_pack_id is not None, "user turn missing context_pack_id"
            assert asst_turn.context_pack_id is not None, "assistant turn missing context_pack_id"
            assert user_turn.context_pack_id == asst_turn.context_pack_id
            # Assistant turn must carry provider_run_id
            assert asst_turn.provider_run_id is not None, "assistant turn missing provider_run_id"
        finally:
            bff_main.read_store = original_store


# ---------------------------------------------------------------------------
# AC#6b — session lifecycle: assistant session created and context updated
# ---------------------------------------------------------------------------


def test_ask_enabled_session_lifecycle_created_and_context_updated() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            sid = "sess-lifecycle-001"
            mock_payload = {
                "status": "ok",
                "data": {
                    "provider": "codex_cli",
                    "mode": "user",
                    "status": "completed",
                    "output": "Lifecycle answer.",
                    "redaction": {},
                },
            }
            mock_client = MagicMock()
            mock_client.configured = True
            mock_client.invoke_assistant.return_value = mock_payload

            with patch.dict(os.environ, {"PANTHEON_ASSISTANT_ENABLED": "true"}):
                with patch("main.OpenClawOpsClient", return_value=mock_client):
                    resp = client.post(
                        "/bff/agora/ask",
                        json={"prompt": "lifecycle?", "sessionId": sid},
                        headers={**OPERATOR_HEADERS, "Idempotency-Key": "ask-lifecycle-001"},
                    )

            assert resp.status_code == 202, resp.text

            # Session store must contain a session for the agora session_id
            session_store = bff_main._ASSISTANT_SESSION_STORE
            assert session_store is not None, "Session store not initialised"
            from assistant.transcript_store import SessionNotFoundError
            try:
                session = session_store.get(sid)
            except SessionNotFoundError:
                pytest.fail(f"Assistant session not created for session_id={sid!r}")
            # Session context must be updated with context_pack_id and provider_run_id
            assert session.context_pack_id is not None, "session context_pack_id not updated"
            assert session.provider_run_id is not None, "session provider_run_id not updated"

            # invoke_assistant must have been called with a non-empty context_pack
            call_kwargs = mock_client.invoke_assistant.call_args
            assert call_kwargs is not None
            cp = call_kwargs.kwargs.get("context_pack") or {}
            assert isinstance(cp, dict), f"context_pack not a dict: {cp!r}"
            assert cp.get("context_pack_id") is not None, "context_pack_id missing from invoke_assistant payload"
        finally:
            bff_main.read_store = original_store


# ---------------------------------------------------------------------------
# Regression: existing command receipt and idempotency remain intact
# ---------------------------------------------------------------------------


def test_ask_command_id_present_in_response_meta() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = _ask(client, ikey="ask-cmd-001")
            assert resp.status_code == 202, resp.text
            body = resp.json()
            assert "command" in body["meta"]
            assert isinstance(body["meta"]["command"]["command"], str)
            assert isinstance(body["meta"]["command"]["commandId"], str)
            assert body["meta"]["idempotency"]["idempotencyKey"] == "ask-cmd-001"
            assert body["meta"]["idempotency"]["replayed"] is False
        finally:
            bff_main.read_store = original_store
