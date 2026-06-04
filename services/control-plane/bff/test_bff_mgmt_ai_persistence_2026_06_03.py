from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from assistant_conversation_store import AssistantConversationStore, PostgresAssistantConversationStore
import main as bff_main


OPERATOR_HEADERS = {"Authorization": "Bearer operator-alpha:operator"}


def _management_ai_route_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha,tenant-beta")
    bff_main._MGMT_NL_IDEMPOTENCY.clear()
    bff_main._MGMT_AI_AUDIT_EVENTS.clear()
    bff_main._sse_buffers["ask"].clear()
    bff_main._MGMT_AI_CONVERSATION_STORE = bff_main.ManagementAiConversationStore(
        storage_path="off",
        attachment_store=bff_main.ManagementAiAttachmentStore(storage_path="off"),
    )
    return TestClient(bff_main.app, raise_server_exceptions=False)

# ---------------------------------------------------------------------------
# Handler-level imports (used by the persist_turns handler tests below).
# These are imported lazily inside functions so the store-only tests above
# do not require a fully-configured BFF environment.
# ---------------------------------------------------------------------------
_BFF_DIR = os.path.dirname(os.path.abspath(__file__))
if _BFF_DIR not in sys.path:
    sys.path.insert(0, _BFF_DIR)


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


def test_bff_management_ai_read_conversations_store_backed_404_scope_and_full_turns(monkeypatch) -> None:
    client = _management_ai_route_client(monkeypatch)
    store = bff_main._MGMT_AI_CONVERSATION_STORE
    store.upsert_session(
        session_id="mgmt-store-backed-session",
        owner_id="operator-alpha",
        tenant_id="tenant-alpha",
        now="2026-06-03T00:00:00Z",
        title="Store backed readback",
    )
    for idx in reversed(range(60)):
        store.append_turn(
            turn_id=f"turn-{idx:02d}",
            session_id="mgmt-store-backed-session",
            role="assistant" if idx % 2 else "user",
            text=f"Persisted turn {idx:02d}",
            created_at=f"2026-06-03T00:00:{idx:02d}Z",
            trace_id=f"trace-{idx:02d}",
            attachments=[
                {
                    "id": f"att-{idx:02d}",
                    "kind": "image",
                    "mimeType": "image/png",
                    "filename": f"screen-{idx:02d}.png",
                    "sizeBytes": idx + 1,
                    "storageUrl": f"local://att-{idx:02d}",
                }
            ],
            provider_status={"provider": "codex_cli", "status": "completed", "idx": idx},
        )
    store.upsert_session(
        session_id="mgmt-owned-foreign-tenant-session",
        owner_id="operator-alpha",
        tenant_id="tenant-beta",
        now="2026-06-03T00:00:00Z",
        title="Owner visible outside default tenant",
    )
    store.append_turn(
        turn_id="owned-foreign-tenant-turn",
        session_id="mgmt-owned-foreign-tenant-session",
        role="user",
        text="Owner can still read this session.",
        created_at="2026-06-03T00:00:01Z",
    )
    store.upsert_session(
        session_id="mgmt-tenant-beta-session",
        owner_id="operator-beta",
        tenant_id="tenant-beta",
        now="2026-06-03T00:00:00Z",
        title="Tenant beta readback",
    )
    store.append_turn(
        turn_id="tenant-beta-turn",
        session_id="mgmt-tenant-beta-session",
        role="user",
        text="Tenant beta question",
        created_at="2026-06-03T00:00:01Z",
    )

    found = client.get(
        "/bff/management/ai/conversations/mgmt-store-backed-session",
        headers=OPERATOR_HEADERS,
    )
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["data"]["sessionId"] == "mgmt-store-backed-session"
    assert body["data"]["localOnly"] is False
    assert body["data"]["missingInStore"] is False
    turns = body["data"]["turns"]
    assert len(turns) == 60
    assert [turn["id"] for turn in turns[:3]] == ["turn-00", "turn-01", "turn-02"]
    assert [turn["createdAt"] for turn in turns[:3]] == [
        "2026-06-03T00:00:00Z",
        "2026-06-03T00:00:01Z",
        "2026-06-03T00:00:02Z",
    ]
    assert turns[17]["turn_id"] == "turn-17"
    assert turns[17]["id"] == "turn-17"
    assert turns[-1]["text"] == "Persisted turn 59"
    assert turns[-1]["providerStatus"] == {"provider": "codex_cli", "status": "completed", "idx": 59}
    assert turns[-1]["provider_status"] == turns[-1]["providerStatus"]
    assert turns[-1]["attachments"] == [
        {
            "id": "att-59",
            "attachmentId": "att-59",
            "attachment_id": "att-59",
            "kind": "image",
            "mimeType": "image/png",
            "mime_type": "image/png",
            "filename": "screen-59.png",
            "sizeBytes": 60,
            "size_bytes": 60,
            "url": "/bff/management/ai/attachments/att-59",
        }
    ]
    assert turns[-1]["created_at"] == turns[-1]["createdAt"]
    assert body["meta"]["surfaces"]["management_ai_conversation"] == {
        "status": "ok",
        "source": "management_ai_store",
        "reason": None,
    }

    missing = client.get(
        "/bff/management/ai/conversations/mgmt-missing-session",
        headers=OPERATOR_HEADERS,
    )
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["details"]["precondition_failed"] == "management_ai_session"

    owner_visible = client.get(
        "/bff/management/ai/conversations/mgmt-owned-foreign-tenant-session",
        headers=OPERATOR_HEADERS,
    )
    assert owner_visible.status_code == 200, owner_visible.text
    assert owner_visible.json()["data"]["turns"][0]["turn_id"] == "owned-foreign-tenant-turn"

    hidden = client.get(
        "/bff/management/ai/conversations/mgmt-tenant-beta-session",
        headers=OPERATOR_HEADERS,
    )
    assert hidden.status_code == 404, hidden.text
    assert hidden.json()["error"]["details"]["precondition_failed"] == "management_ai_session"

    tenant_visible = client.get(
        "/bff/management/ai/conversations/mgmt-tenant-beta-session",
        headers={**OPERATOR_HEADERS, "X-Tenant-Id": "tenant-beta"},
    )
    assert tenant_visible.status_code == 200, tenant_visible.text
    assert tenant_visible.json()["data"]["turns"][0]["text"] == "Tenant beta question"


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


# ---------------------------------------------------------------------------
# Handler-level persistence tests (MGMT-AI-PERSIST-P0-WRITE-002)
# These tests drive POST /bff/management/nl/ask via the FastAPI TestClient and
# verify that the handler wires up AssistantConversationStore correctly:
# - user turn persisted BEFORE provider call with full (non-truncated) text
# - assistant turn persisted AFTER provider call with full answer text
# - session created on first ask; reused on subsequent asks with same sessionId
# - idempotency records written to the durable store (not just in-memory dict)
# - 30 asks yield exactly 60 stored turns; restart durability confirmed
# ---------------------------------------------------------------------------

_OPERATOR_HEADERS = {"Authorization": "Bearer op-persist-write-002:operator"}


@contextmanager
def _persist_client(tmp_path: Path, store_path: Path) -> Iterator[object]:
    """
    Yield a TestClient wired to a file-backed ManagementAiConversationStore.
    Restores bff_main state on exit so tests are isolated.
    """
    import main as bff_main
    from management_ai_store import ManagementAiConversationStore, ManagementAiAttachmentStore
    from read_store import ReadSurfaceStore
    from fastapi.testclient import TestClient

    saved_store = bff_main._MGMT_AI_CONVERSATION_STORE
    saved_read_store = bff_main.read_store
    saved_idem = dict(bff_main._MGMT_NL_IDEMPOTENCY)

    store = ManagementAiConversationStore(
        storage_path=str(store_path),
        attachment_store=ManagementAiAttachmentStore(storage_path=str(tmp_path / "attachments")),
    )
    bff_main._MGMT_AI_CONVERSATION_STORE = store
    bff_main._MGMT_NL_IDEMPOTENCY.clear()
    bff_main._MGMT_AI_AUDIT_EVENTS.clear()
    bff_main.read_store = ReadSurfaceStore(
        str(tmp_path / "read_surfaces.json"),
        allow_local_snapshot_fallback=True,
    )
    try:
        yield TestClient(bff_main.app), store
    finally:
        bff_main._MGMT_AI_CONVERSATION_STORE = saved_store
        bff_main.read_store = saved_read_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        bff_main._MGMT_NL_IDEMPOTENCY.update(saved_idem)


def test_persist_turns(tmp_path: Path) -> None:
    """
    WRITE-002 acceptance: POST /bff/management/nl/ask persists user + assistant
    turns durably via AssistantConversationStore.

    Covers:
    - New session creation (owner_id + tenant_id populated)
    - Session reuse when the same sessionId is supplied on subsequent asks
    - 30 asks yield exactly 60 stored turns (no duplicates)
    - User turn text stored at FULL length (not truncated at 400 chars)
    - Restart durability: turns survive creating a new store from the same file
    - Idempotency replay does not create duplicate turns
    """
    import main as bff_main
    from management_ai_store import ManagementAiConversationStore
    from assistant_conversation_store import AssistantConversationStore

    store_path = tmp_path / "mgmt-ai-persist-write-002.json"
    # Text longer than the 400-char _management_ai_summary_value cap used in audit events.
    # _agora_required_text strips whitespace so store this stripped form as the expected value.
    long_question = ("What is the portfolio status? " + ("detail " * 60)).strip()
    assert len(long_question) > 400, "pre-condition: test question must exceed 400 chars"

    session_id = "write-002-persist-session"

    with _persist_client(tmp_path, store_path) as (client, store):
        # ---------------------------------------------------------------
        # First ask: new session must be created and user + assistant turns
        # must be persisted with the full (non-truncated) question text.
        # ---------------------------------------------------------------
        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": long_question, "sessionId": session_id},
            headers={**_OPERATOR_HEADERS, "Idempotency-Key": "write-002-ask-000"},
        )
        assert resp.status_code == 202, f"First ask failed: {resp.text}"
        body = resp.json()
        assert body["data"]["sessionId"] == session_id
        assert body["data"]["session_id"] == session_id
        assert body["data"]["message_id"], "message_id must be present"
        assert body["data"]["answer"], "answer must be non-empty"

        session = store.get_session(session_id)
        assert session is not None, "Session must be created in store after first ask"
        owner_id = session.get("ownerId") or session.get("owner_id")
        assert owner_id, "Session must have owner_id populated"

        turns = store.list_turns(session_id)
        assert len(turns) == 2, f"Expected 2 turns after first ask, got {len(turns)}"
        user_turn = turns[0]
        asst_turn = turns[1]
        assert user_turn["role"] == "user", f"First turn must be user, got {user_turn['role']!r}"
        assert user_turn["text"] == long_question, (
            "User turn text must NOT be truncated: "
            f"stored={len(user_turn['text'])} chars, expected={len(long_question)}"
        )
        assert asst_turn["role"] == "assistant", f"Second turn must be assistant, got {asst_turn['role']!r}"
        assert asst_turn["text"], "Assistant turn must have non-empty answer text"

        # ---------------------------------------------------------------
        # 29 more asks with the same session_id (30 total) to verify session
        # reuse and accumulation: 30 × 2 = 60 stored turns.
        # ---------------------------------------------------------------
        short_q = "Follow-up question?"
        for i in range(1, 30):
            r = client.post(
                "/bff/management/nl/ask",
                json={"question": short_q, "sessionId": session_id},
                headers={**_OPERATOR_HEADERS, "Idempotency-Key": f"write-002-ask-{i:03d}"},
            )
            assert r.status_code == 202, f"Ask #{i + 1} failed: {r.text}"
            assert r.json()["data"]["session_id"] == session_id, f"Ask #{i + 1} session_id mismatch"

        all_turns = store.list_turns(session_id)
        assert len(all_turns) == 60, (
            f"Expected 60 turns (30 user + 30 assistant) after 30 asks, got {len(all_turns)}"
        )
        user_turns = [t for t in all_turns if t["role"] == "user"]
        asst_turns = [t for t in all_turns if t["role"] == "assistant"]
        assert len(user_turns) == 30, f"Expected 30 user turns, got {len(user_turns)}"
        assert len(asst_turns) == 30, f"Expected 30 assistant turns, got {len(asst_turns)}"

        # First user turn still has full long text after session reuse.
        assert all_turns[0]["text"] == long_question

        # ---------------------------------------------------------------
        # Restart durability: a new store instance pointing at the same file
        # must see all 60 turns.
        # ---------------------------------------------------------------
        reloaded = ManagementAiConversationStore(storage_path=str(store_path))
        reloaded_turns = reloaded.list_turns(session_id)
        assert len(reloaded_turns) == 60, (
            f"After reload: expected 60 turns, got {len(reloaded_turns)}"
        )
        assert reloaded_turns[0]["text"] == long_question, (
            "First user turn text must survive store reload untruncated"
        )

        # ---------------------------------------------------------------
        # Idempotency replay must not create duplicate turns.
        # ---------------------------------------------------------------
        idem_key = "write-002-idem-key-replay"
        idem_q = "idempotency test question"

        r1 = client.post(
            "/bff/management/nl/ask",
            json={"question": idem_q, "sessionId": session_id},
            headers={**_OPERATOR_HEADERS, "Idempotency-Key": idem_key},
        )
        assert r1.status_code == 202, f"Idempotency first ask failed: {r1.text}"
        meta1 = r1.json().get("meta", {})
        assert meta1.get("idempotency", {}).get("replayed") is False, (
            "First request with key must NOT be a replay"
        )

        r2 = client.post(
            "/bff/management/nl/ask",
            json={"question": idem_q, "sessionId": session_id},
            headers={**_OPERATOR_HEADERS, "Idempotency-Key": idem_key},
        )
        assert r2.status_code == 202, f"Idempotency replay failed: {r2.text}"
        meta2 = r2.json().get("meta", {})
        assert meta2.get("idempotency", {}).get("replayed") is True, (
            f"Second request with same key must be replayed, meta={meta2}"
        )

        # Replay must not add extra turns: still 60 + 2 (from the idempotency ask) = 62.
        after_idem = store.list_turns(session_id)
        assert len(after_idem) == 62, (
            f"After idempotency replay: expected 62 turns (60 + 2 from idem ask), got {len(after_idem)}"
        )

        # Idempotency record must also be in the durable store (not only in-memory dict).
        idem_record = store.get_idempotency(idem_key)
        assert idem_record is not None, "Idempotency record must be written to durable store"
        assert isinstance(idem_record.get("request_hash"), str)
