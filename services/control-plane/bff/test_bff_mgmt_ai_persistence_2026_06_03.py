from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from assistant_conversation_store import AssistantConversationStore, PostgresAssistantConversationStore


def _append_60_turns(store: object, *, session_id: str) -> str:
    full_text = "full-text-" + ("x" * 900)
    for idx in range(60):
        text = full_text if idx == 59 else f"turn-{idx:02d}"
        store.append_turn(
            turn_id=f"turn-{idx:02d}",
            session_id=session_id,
            role="assistant" if idx % 2 else "user",
            text=text,
            created_at=f"2026-06-03T00:00:{idx:02d}Z",
            trace_id=f"trace-{idx:02d}",
            attachments=[{"id": f"att-{idx:02d}", "storageUrl": f"local://att-{idx:02d}"}],
            provider_status={"status": "completed", "idx": idx},
            ui_snapshot={"route": "/management"},
            ui_actions=[{"kind": "navigate", "idx": idx}],
            assistant_metadata={"context_pack_id": f"ctx-{idx:02d}"},
        )
    return full_text


def _assert_store_roundtrip(store: object, *, session_id: str) -> str:
    assert store.get_session("missing-session") is None
    assert store.list_turns("missing-session") == []

    created = store.create_session(
        session_id=session_id,
        owner_id="operator-alpha",
        tenant_id="tenant-alpha",
        now="2026-06-03T00:00:00Z",
        title="Management AI persistence",
    )
    assert created["sessionId"] == session_id
    assert created["ownerId"] == "operator-alpha"

    full_text = _append_60_turns(store, session_id=session_id)
    turns = store.list_turns(session_id)
    assert len(turns) == 60
    assert [turn["turn_id"] for turn in turns[:3]] == ["turn-00", "turn-01", "turn-02"]
    assert turns[-1]["text"] == full_text
    assert turns[-1]["providerStatus"] == {"status": "completed", "idx": 59}
    assert turns[-1]["uiSnapshot"] == {"route": "/management"}
    assert turns[-1]["assistantMetadata"]["context_pack_id"] == "ctx-59"

    touched = store.touch_session(session_id, now="2026-06-03T00:02:00Z")
    assert touched is not None
    assert touched["updatedAt"] == "2026-06-03T00:02:00Z"

    store.put_idempotency(
        "idem-key-1",
        request_hash="hash-one",
        result={"status": "accepted", "data": {"sessionId": session_id}},
    )
    idem = store.get_idempotency("idem-key-1")
    assert idem is not None
    assert idem["request_hash"] == "hash-one"
    assert idem["result"]["data"]["sessionId"] == session_id
    return full_text


def test_json_assistant_conversation_store_persists_restart_roundtrip(tmp_path: Path) -> None:
    store_path = tmp_path / "management-ai-conversations.json"
    session_id = "mgmt-json-session"
    store = AssistantConversationStore(backend="json", storage_path=str(store_path))
    full_text = _assert_store_roundtrip(store, session_id=session_id)

    reloaded = AssistantConversationStore(backend="json", storage_path=str(store_path))
    assert reloaded.get_session(session_id)["tenantId"] == "tenant-alpha"
    reloaded_turns = reloaded.list_turns(session_id)
    assert len(reloaded_turns) == 60
    assert reloaded_turns[-1]["text"] == full_text
    assert reloaded.get_idempotency("idem-key-1")["request_hash"] == "hash-one"


def test_json_assistant_conversation_store_can_run_in_memory() -> None:
    store = AssistantConversationStore(backend="memory")
    session_id = "mgmt-memory-session"
    store.create_session(
        session_id=session_id,
        owner_id="operator-alpha",
        tenant_id=None,
        now="2026-06-03T00:00:00Z",
        title=None,
    )
    store.append_turn(
        turn_id="turn-memory",
        session_id=session_id,
        role="user",
        text="in memory",
        created_at="2026-06-03T00:00:01Z",
    )
    assert store.list_turns(session_id)[0]["text"] == "in memory"


def test_postgres_assistant_conversation_store_roundtrip_when_database_available() -> None:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set; skipping Postgres AssistantConversationStore roundtrip")

    surface = f"assistant_conversation_test_{uuid.uuid4().hex[:10]}"
    session_id = f"mgmt-pg-session-{uuid.uuid4().hex[:8]}"
    store = PostgresAssistantConversationStore(
        dsn=dsn,
        schema="management_ai_test",
        surface=surface,
        owner_service="operator-bff-test",
    )
    full_text = _assert_store_roundtrip(store, session_id=session_id)

    reloaded = PostgresAssistantConversationStore(
        dsn=dsn,
        schema="management_ai_test",
        surface=surface,
        owner_service="operator-bff-test",
        bootstrap=False,
    )
    assert reloaded.get_session(session_id)["ownerId"] == "operator-alpha"
    assert len(reloaded.list_turns(session_id)) == 60
    assert reloaded.list_turns(session_id)[-1]["text"] == full_text
    assert reloaded.get_idempotency("idem-key-1")["result"]["status"] == "accepted"
