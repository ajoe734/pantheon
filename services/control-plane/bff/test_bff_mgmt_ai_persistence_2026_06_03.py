from __future__ import annotations

import base64
import json
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


def test_management_ai_attachment_store_uses_gcs_bucket_metadata(monkeypatch) -> None:
    image_bytes = b"\x89PNG\r\n\x1a\nstored-in-fake-gcs"
    uploads: dict[str, dict[str, object]] = {}

    class FakeBlob:
        def __init__(self, name: str) -> None:
            self.name = name

        def upload_from_string(self, content: bytes, content_type: str | None = None) -> None:
            uploads[self.name] = {"content": content, "content_type": content_type}

        def download_as_bytes(self) -> bytes:
            return uploads[self.name]["content"]  # type: ignore[return-value]

    class FakeBucket:
        def blob(self, name: str) -> FakeBlob:
            return FakeBlob(name)

    store = bff_main.ManagementAiAttachmentStore(
        storage_path="off",
        bucket_name="pantheon-test-attachments",
    )
    monkeypatch.setattr(store, "_gcs_bucket", lambda bucket_name=None: FakeBucket())

    metadata = store.store_inline_attachment(
        {
            "kind": "image",
            "mimeType": "image/png",
            "filename": "screen.png",
            "dataBase64": base64.b64encode(image_bytes).decode("ascii"),
        },
        session_id="mgmt-gcs-session",
        turn_id="turn-gcs",
    )

    assert metadata["storageUrl"].startswith("gs://pantheon-test-attachments/management-ai-attachments/")
    assert metadata["sizeBytes"] == len(image_bytes)
    assert "dataBase64" not in metadata
    object_name = metadata["objectName"]
    assert uploads[object_name]["content"] == image_bytes
    assert uploads[object_name]["content_type"] == "image/png"

    content, mime_type, filename = store.read(metadata["id"], metadata)
    assert content == image_bytes
    assert mime_type == "image/png"
    assert filename == "screen.png"

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


def test_postgres_store_uses_management_ai_database_url_alias(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakePostgresStore:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "assistant_conversation_store.PostgresAssistantConversationStore",
        FakePostgresStore,
    )

    store = AssistantConversationStore(
        env={
            "MANAGEMENT_AI_STORE_BACKEND": "postgres",
            "MANAGEMENT_AI_DATABASE_URL": "postgresql://management-ai@postgres/pantheon",
            "DATABASE_URL": "postgresql://shared-app@postgres/pantheon",
            "MANAGEMENT_AI_STORE_SCHEMA": "management_ai",
        }
    )

    assert store.backend == "postgres"
    assert captured["dsn"] == "postgresql://management-ai@postgres/pantheon"
    assert captured["schema"] == "management_ai"


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


def test_postgres_assistant_conversation_bootstrap_uses_valid_index_name(monkeypatch) -> None:
    executed: list[str] = []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, *args):
            executed.append(str(sql))
            return None

    store = PostgresAssistantConversationStore(
        dsn="postgresql://example.invalid/pantheon",
        schema="management_ai",
        surface="assistant_conversation",
        owner_service="operator-bff-test",
        bootstrap=False,
    )
    monkeypatch.setattr(store, "_connect", lambda: FakeConnection())

    store.bootstrap()

    create_index_sql = " ".join(
        next(sql for sql in executed if "CREATE INDEX IF NOT EXISTS" in sql).split()
    )
    assert (
        'CREATE INDEX IF NOT EXISTS "assistant_conversation_turns_session_created_idx" '
        'ON "management_ai"."assistant_conversation_turns"'
    ) in create_index_sql
    assert '"management_ai"."assistant_conversation_turns_session_created_idx"' not in create_index_sql


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


class _FakeProviderClient:
    def __init__(self, result: dict | None = None) -> None:
        self.result = result or {
            "status": "ok",
            "data": {
                "provider": "codex_cli",
                "status": "completed",
                "output": {"json_events": [{"final": "Provider inspected the attachment."}]},
            },
        }
        self.calls: list[dict] = []

    def invoke_assistant_provider(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


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


def test_attachment_storage_base64_proxy_url_and_size_rejections(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """
    ATTACH-006 acceptance: inline base64 attachments are decoded into the BFF
    object store, turn rows keep metadata/storageUrl only, conversation GET
    returns a proxy URL, and oversize payloads are rejected with 413.
    """
    from management_ai_store import ManagementAiConversationStore

    monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "deterministic")
    monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "false")
    monkeypatch.setenv("PANTHEON_MGMT_AI_ATTACH_MAX_BYTES", "32")
    monkeypatch.setenv("PANTHEON_MGMT_AI_ATTACH_MAX_REQUEST_BYTES", "48")

    store_path = tmp_path / "mgmt-ai-attachment-storage.json"
    image_bytes = b"\x89PNG\r\n\x1a\nok"
    encoded = base64.b64encode(image_bytes).decode("ascii")

    with _persist_client(tmp_path, store_path) as (client, store):
        resp = client.post(
            "/bff/management/nl/ask",
            json={
                "question": "Please inspect this screenshot.",
                "sessionId": "attach-006-session",
                "attachments": [
                    {
                        "kind": "image",
                        "mimeType": "image/png",
                        "filename": "screen.png",
                        "sizeBytes": 999999,
                        "dataBase64": encoded,
                    }
                ],
            },
            headers={**_OPERATOR_HEADERS, "Idempotency-Key": "attach-006-ok"},
        )
        assert resp.status_code == 202, resp.text

        stored_turns = store.list_turns("attach-006-session")
        stored_dump = json.dumps(stored_turns, ensure_ascii=False)
        assert encoded not in stored_dump
        assert "dataBase64" not in stored_dump
        stored_attachment = stored_turns[0]["attachments"][0]
        assert stored_attachment["storageUrl"].startswith("local://management-ai-attachments/")
        assert stored_attachment["sizeBytes"] == len(image_bytes)
        assert stored_attachment["mimeType"] == "image/png"

        reloaded = ManagementAiConversationStore(storage_path=str(store_path))
        reloaded_dump = json.dumps(reloaded.list_turns("attach-006-session"), ensure_ascii=False)
        assert encoded not in reloaded_dump
        assert "dataBase64" not in reloaded_dump

        conversation_resp = client.get(
            "/bff/management/ai/conversations/attach-006-session",
            headers=_OPERATOR_HEADERS,
        )
        assert conversation_resp.status_code == 200, conversation_resp.text
        conversation_dump = json.dumps(conversation_resp.json(), ensure_ascii=False)
        assert encoded not in conversation_dump
        assert "dataBase64" not in conversation_dump
        attachment = conversation_resp.json()["data"]["turns"][0]["attachments"][0]
        assert attachment["url"] == f"/bff/management/ai/attachments/{stored_attachment['id']}"
        assert "storageUrl" not in attachment

        attachment_resp = client.get(attachment["url"], headers=_OPERATOR_HEADERS)
        assert attachment_resp.status_code == 200, attachment_resp.text
        assert attachment_resp.content == image_bytes
        assert attachment_resp.headers["content-type"].startswith("image/png")

        too_large_resp = client.post(
            "/bff/management/nl/ask",
            json={
                "question": "This attachment is too large.",
                "sessionId": "attach-006-too-large",
                "attachments": [
                    {
                        "kind": "image",
                        "mimeType": "image/png",
                        "filename": "too-large.png",
                        "dataBase64": base64.b64encode(b"x" * 33).decode("ascii"),
                    }
                ],
            },
            headers={**_OPERATOR_HEADERS, "Idempotency-Key": "attach-006-too-large"},
        )
        assert too_large_resp.status_code == 413, too_large_resp.text
        too_large_body = too_large_resp.json()
        assert too_large_body["error"]["code"] == "REQUEST_TOO_LARGE"
        assert too_large_body["error"]["details"]["precondition_failed"] == "management_ai_attachment_size"

        total_too_large_resp = client.post(
            "/bff/management/nl/ask",
            json={
                "question": "These attachments are too large together.",
                "sessionId": "attach-006-total-too-large",
                "attachments": [
                    {
                        "kind": "image",
                        "mimeType": "image/png",
                        "filename": "a.png",
                        "dataBase64": base64.b64encode(b"a" * 25).decode("ascii"),
                    },
                    {
                        "kind": "image",
                        "mimeType": "image/png",
                        "filename": "b.png",
                        "dataBase64": base64.b64encode(b"b" * 25).decode("ascii"),
                    },
                ],
            },
            headers={**_OPERATOR_HEADERS, "Idempotency-Key": "attach-006-total-too-large"},
        )
        assert total_too_large_resp.status_code == 413, total_too_large_resp.text
        total_body = total_too_large_resp.json()
        assert total_body["error"]["code"] == "REQUEST_TOO_LARGE"
        assert (
            total_body["error"]["details"]["precondition_failed"]
            == "management_ai_attachment_total_size"
        )

        unsupported_mime_resp = client.post(
            "/bff/management/nl/ask",
            json={
                "question": "This attachment has a disallowed MIME type.",
                "sessionId": "attach-006-bad-mime",
                "attachments": [
                    {
                        "kind": "file",
                        "mimeType": "text/plain",
                        "filename": "note.txt",
                        "dataBase64": base64.b64encode(b"plain text").decode("ascii"),
                    }
                ],
            },
            headers={**_OPERATOR_HEADERS, "Idempotency-Key": "attach-006-bad-mime"},
        )
        assert unsupported_mime_resp.status_code == 422, unsupported_mime_resp.text
        unsupported_mime_body = unsupported_mime_resp.json()
        assert unsupported_mime_body["error"]["code"] == "VALIDATION_FAILED"
        assert (
            unsupported_mime_body["error"]["details"]["precondition_failed"]
            == "management_ai_attachment_mime_type"
        )


def test_multimodal_image_attachment_is_forwarded_to_codex_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """
    ATTACH-007 acceptance: after ATTACH-006 stores inline image bytes in the
    object store, provider invocation resolves those bytes into a multimodal
    image_url payload instead of forwarding DB metadata only.
    """
    fake = _FakeProviderClient()
    monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "codex_cli")
    monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
    monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)

    store_path = tmp_path / "mgmt-ai-attachment-provider.json"
    image_bytes = b"\x89PNG\r\n\x1a\nprovider-forward"
    encoded = base64.b64encode(image_bytes).decode("ascii")

    with _persist_client(tmp_path, store_path) as (client, store):
        resp = client.post(
            "/bff/management/nl/ask",
            json={
                "question": "What is visible in this screenshot?",
                "sessionId": "attach-007-codex-session",
                "attachments": [
                    {
                        "kind": "image",
                        "mimeType": "image/png",
                        "filename": "screen.png",
                        "dataBase64": encoded,
                    }
                ],
            },
            headers={**_OPERATOR_HEADERS, "Idempotency-Key": "attach-007-codex"},
        )

        assert resp.status_code == 202, resp.text
        assert resp.json()["data"]["answer"] == "Provider inspected the attachment."
        assert fake.calls, "provider must be invoked"
        call = fake.calls[0]
        assert call["metadata"]["attachments"][0]["url"].startswith("/bff/management/ai/attachments/")
        assert "storageUrl" not in call["metadata"]["attachments"][0]
        assert call["metadata"]["multimodal"]["attachment_count"] == 1
        assert call["metadata"]["multimodal"]["forwarded"] is True

        message = call["messages"][0]
        assert message["role"] == "user"
        image_part = next(part for part in message["content"] if part["type"] == "image_url")
        assert image_part["image_url"]["url"] == f"data:image/png;base64,{encoded}"
        assert image_part["source"] == "management_ai_attachment_store"
        assert call["attachments"][0]["attachmentId"] == image_part["attachmentId"]

        stored_dump = json.dumps(store.list_turns("attach-007-codex-session"), ensure_ascii=False)
        assert encoded not in stored_dump
        assert "dataBase64" not in stored_dump


def test_multimodal_attachment_falls_back_to_text_only_for_unsupported_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake = _FakeProviderClient(
        result={
            "provider": "claude",
            "status": "ok",
            "text": "Claude handled the text-only fallback.",
        }
    )
    monkeypatch.setenv("PANTHEON_ASSISTANT_PROVIDER", "claude_cli")
    monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
    monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)

    store_path = tmp_path / "mgmt-ai-attachment-provider-fallback.json"
    encoded = base64.b64encode(b"\x89PNG\r\n\x1a\nfallback").decode("ascii")

    with _persist_client(tmp_path, store_path) as (client, _store):
        resp = client.post(
            "/bff/management/nl/ask",
            json={
                "question": "Please inspect this screenshot.",
                "sessionId": "attach-007-claude-session",
                "attachments": [
                    {
                        "kind": "image",
                        "mimeType": "image/png",
                        "filename": "screen.png",
                        "dataBase64": encoded,
                    }
                ],
            },
            headers={**_OPERATOR_HEADERS, "Idempotency-Key": "attach-007-claude"},
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["data"]["answer"] == "Claude handled the text-only fallback."
    provider_status = body["data"]["providerStatus"]
    assert provider_status["reason"] == "multimodal_unsupported"
    assert provider_status["multimodal"]["forwarded"] is False
    assert provider_status["multimodal"]["fallback"] == "text_only"
    assert "messages" not in fake.calls[0]
    assert "attachments" not in fake.calls[0]


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
        first_body = r1.json()
        meta1 = first_body.get("meta", {})
        assert meta1.get("idempotency", {}).get("replayed") is False, (
            "First request with key must NOT be a replay"
        )

        r2 = client.post(
            "/bff/management/nl/ask",
            json={"question": idem_q, "sessionId": session_id},
            headers={**_OPERATOR_HEADERS, "Idempotency-Key": idem_key},
        )
        assert r2.status_code == 202, f"Idempotency replay failed: {r2.text}"
        assert r2.json() == first_body, "Replay must return the original stored response body verbatim"

        # Replay must not add extra turns: still 60 + 2 (from the idempotency ask) = 62.
        after_idem = store.list_turns(session_id)
        assert len(after_idem) == 62, (
            f"After idempotency replay: expected 62 turns (60 + 2 from idem ask), got {len(after_idem)}"
        )

        # Idempotency record must also be in the durable store (not only in-memory dict).
        idem_record = store.get_idempotency(idem_key)
        assert idem_record is not None, "Idempotency record must be written to durable store"
        assert isinstance(idem_record.get("request_hash"), str)
        assert idem_record.get("result") == first_body
