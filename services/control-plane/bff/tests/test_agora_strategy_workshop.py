"""Tests for AG-BE-SW-001 — workshop session/event persistence.

Covers:
- MemoryWorkshopStore: create/get/list sessions, events, completeness snapshots
- Event privacy rule: private content must NOT appear in stored events
- Router endpoints: list/create/get workshop, post message, list events, completeness
- Import and schema alignment checks
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# --------------------------------------------------------------------------- #
# MemoryWorkshopStore unit tests
# --------------------------------------------------------------------------- #

class TestMemoryWorkshopStoreSession:
    def _store(self):
        from agora.strategy_workshop import MemoryWorkshopStore
        return MemoryWorkshopStore()

    def test_create_and_get_session(self):
        store = self._store()
        s = store.create_session({
            "workshop_id": "ws-001",
            "tenant_id": "tenant-alpha",
            "user_id": "user-alpha",
        })
        assert s["workshop_id"] == "ws-001"
        assert s["status"] == "open"
        assert s["lock_version"] == 1
        assert "created_at" in s
        assert "updated_at" in s

        got = store.get_session("ws-001")
        assert got is not None
        assert got["workshop_id"] == "ws-001"

    def test_get_session_returns_none_for_unknown(self):
        store = self._store()
        assert store.get_session("does-not-exist") is None

    def test_list_sessions_user_scoped(self):
        store = self._store()
        store.create_session({"workshop_id": "ws-A", "tenant_id": "t1", "user_id": "u1"})
        store.create_session({"workshop_id": "ws-B", "tenant_id": "t1", "user_id": "u2"})
        store.create_session({"workshop_id": "ws-C", "tenant_id": "t1", "user_id": "u1"})

        sessions, cursor = store.list_sessions(user_id="u1", tenant_id="t1")
        ids = [s["workshop_id"] for s in sessions]
        assert "ws-A" in ids
        assert "ws-C" in ids
        assert "ws-B" not in ids

    def test_list_sessions_status_filter(self):
        store = self._store()
        store.create_session({"workshop_id": "ws-open", "tenant_id": "t1", "user_id": "u1", "status": "open"})
        store.create_session({"workshop_id": "ws-concluded", "tenant_id": "t1", "user_id": "u1", "status": "concluded"})

        open_sessions, _ = store.list_sessions(user_id="u1", tenant_id="t1", status="open")
        assert all(s["status"] == "open" for s in open_sessions)
        assert len(open_sessions) == 1

    def test_list_sessions_pagination(self):
        store = self._store()
        for i in range(5):
            store.create_session({"workshop_id": f"ws-{i:02d}", "tenant_id": "t1", "user_id": "u1"})

        page1, cursor = store.list_sessions(user_id="u1", tenant_id="t1", limit=3)
        assert len(page1) == 3
        # cursor is set when more results exist
        assert cursor is not None

    def test_session_fields_match_contract(self):
        """All fields from the contract persistence spec must be present."""
        store = self._store()
        s = store.create_session({
            "workshop_id": "ws-full",
            "tenant_id": "t1",
            "user_id": "u1",
            "servant_persona_id": "persona-1",
            "openclaw_session_id": "oc-session-1",
            "strategy_id": "strat-1",
            "active_strategy_spec_registry_id": "reg-draft-1",
            "selected_version_id": "ver-1",
        })
        for field in [
            "workshop_id", "tenant_id", "user_id", "servant_persona_id",
            "openclaw_session_id", "strategy_id", "active_strategy_spec_registry_id",
            "selected_version_id", "status", "lock_version", "created_at", "updated_at",
        ]:
            assert field in s, f"Missing field: {field}"


class TestMemoryWorkshopStoreEvent:
    def _store(self):
        from agora.strategy_workshop import MemoryWorkshopStore
        return MemoryWorkshopStore()

    def test_create_event_assigns_sequence(self):
        store = self._store()
        e1 = store.create_event({
            "workshop_id": "ws-1",
            "actor_type": "operator",
            "event_type": "message",
            "redacted_summary": "hello",
        })
        e2 = store.create_event({
            "workshop_id": "ws-1",
            "actor_type": "persona",
            "event_type": "message",
            "redacted_summary": "world",
        })
        assert e1["sequence_no"] == 1
        assert e2["sequence_no"] == 2

    def test_event_has_no_private_content(self):
        """Private content must NEVER be stored in the event — only ref + redacted."""
        store = self._store()
        event = store.create_event({
            "workshop_id": "ws-priv",
            "actor_type": "operator",
            "event_type": "message",
            "private_content_ref": "enc-store://ref-abc",
            "redacted_summary": "Operator discussed alpha exposure",
        })
        # Must not have raw private_content key
        assert "private_content" not in event
        # Must preserve the ref
        assert event["private_content_ref"] == "enc-store://ref-abc"
        assert event["redacted_summary"] == "Operator discussed alpha exposure"

    def test_event_fields_match_contract(self):
        store = self._store()
        e = store.create_event({
            "workshop_id": "ws-1",
            "actor_type": "operator",
            "event_type": "message",
            "private_content_ref": "ref-1",
            "redacted_summary": "summary",
            "payload_refs_json": ["attach-1"],
            "trace_id": "trace-xyz",
        })
        for field in [
            "event_id", "workshop_id", "sequence_no", "actor_type", "event_type",
            "private_content_ref", "redacted_summary", "payload_refs_json",
            "trace_id", "created_at",
        ]:
            assert field in e, f"Missing field: {field}"

    def test_list_events_empty_when_none(self):
        store = self._store()
        assert store.list_events("no-workshop") == []

    def test_list_events_after_sequence(self):
        store = self._store()
        for i in range(5):
            store.create_event({
                "workshop_id": "ws-seq",
                "actor_type": "operator",
                "event_type": "message",
            })
        after2 = store.list_events("ws-seq", after_sequence=2)
        assert all(e["sequence_no"] > 2 for e in after2)
        assert len(after2) == 3


class TestMemoryWorkshopStoreCompleteness:
    def _store(self):
        from agora.strategy_workshop import MemoryWorkshopStore
        return MemoryWorkshopStore()

    def test_create_and_get_snapshot(self):
        store = self._store()
        snap = store.create_completeness_snapshot({
            "workshop_id": "ws-c1",
            "strategy_version_id": "ver-1",
            "state_map_json": {"hypothesis": "complete"},
            "blocking_items_json": [],
            "next_question_json": {"question": "What is the target universe?"},
        })
        assert snap["snapshot_id"]
        assert snap["workshop_id"] == "ws-c1"
        assert snap["state_map_json"] == {"hypothesis": "complete"}

        latest = store.get_latest_completeness_snapshot("ws-c1")
        assert latest is not None
        assert latest["snapshot_id"] == snap["snapshot_id"]

    def test_get_latest_returns_most_recent(self):
        store = self._store()
        s1 = store.create_completeness_snapshot({
            "workshop_id": "ws-c2",
            "state_map_json": {"v": 1},
        })
        s2 = store.create_completeness_snapshot({
            "workshop_id": "ws-c2",
            "state_map_json": {"v": 2},
        })
        latest = store.get_latest_completeness_snapshot("ws-c2")
        assert latest["snapshot_id"] == s2["snapshot_id"]

    def test_get_latest_returns_none_when_absent(self):
        store = self._store()
        assert store.get_latest_completeness_snapshot("no-such-workshop") is None

    def test_snapshot_fields_match_contract(self):
        store = self._store()
        snap = store.create_completeness_snapshot({
            "workshop_id": "ws-fields",
            "strategy_version_id": "v1",
            "state_map_json": {},
            "blocking_items_json": [],
            "next_question_json": {},
        })
        for field in [
            "snapshot_id", "workshop_id", "strategy_version_id",
            "state_map_json", "blocking_items_json", "next_question_json", "created_at",
        ]:
            assert field in snap, f"Missing field: {field}"


# --------------------------------------------------------------------------- #
# Factory and import tests
# --------------------------------------------------------------------------- #

class TestWorkshopStoreFactory:
    def test_make_workshop_store_off_returns_memory(self):
        from agora.strategy_workshop import make_workshop_store, MemoryWorkshopStore
        store = make_workshop_store(backend="off")
        assert isinstance(store, MemoryWorkshopStore)

    def test_make_workshop_store_default_is_off(self, monkeypatch):
        monkeypatch.delenv("AGORA_WORKSHOP_STORE_BACKEND", raising=False)
        from agora.strategy_workshop import make_workshop_store, MemoryWorkshopStore
        store = make_workshop_store()
        assert isinstance(store, MemoryWorkshopStore)

    def test_make_workshop_store_postgres_requires_dsn(self, monkeypatch):
        monkeypatch.delenv("AGORA_WORKSHOP_STORE_DSN", raising=False)
        from agora.strategy_workshop import make_workshop_store
        with pytest.raises(RuntimeError, match="DSN"):
            make_workshop_store(backend="postgres")

    def test_make_workshop_store_unknown_raises(self):
        from agora.strategy_workshop import make_workshop_store
        with pytest.raises(RuntimeError, match="Unknown"):
            make_workshop_store(backend="sqlite")

    def test_module_importable(self):
        from agora.strategy_workshop import (
            MemoryWorkshopStore,
            PostgresWorkshopStore,
            make_workshop_store,
            BACKEND_ENV,
            DSN_ENV,
            SCHEMA_ENV,
            DEFAULT_SCHEMA,
        )
        assert DEFAULT_SCHEMA == "agora"
        assert BACKEND_ENV == "AGORA_WORKSHOP_STORE_BACKEND"


# --------------------------------------------------------------------------- #
# Router endpoint integration tests (in-memory store, permissive auth)
# --------------------------------------------------------------------------- #

_OPERATOR_AUTH = "Bearer agora-test-user:operator"


def _workshop_client(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    import main as bff_main
    return TestClient(bff_main.app, raise_server_exceptions=False)


def _get_current_etag(client, workshop_id: str) -> str:
    """Fetch the current ETag for a workshop (required before each POST /messages)."""
    resp = client.get(
        f"/bff/agora/workshops/{workshop_id}",
        headers={"Authorization": _OPERATOR_AUTH},
    )
    assert resp.status_code == 200, f"ETag fetch failed: {resp.text}"
    return resp.headers["etag"]


class TestWorkshopRouterEndpoints:
    def test_list_workshops_returns_envelope(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        resp = client.get(
            "/bff/agora/workshops",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
        assert body["meta"]["capability"] == "agora.workshop.v1"

    def test_list_workshops_requires_auth(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        resp = client.get("/bff/agora/workshops")
        assert resp.status_code in (401, 403)

    def test_create_workshop_returns_201(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-create-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": "Let's explore alpha decay on EURUSD"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "data" in body
        data = body["data"]
        assert "workshop_id" in data
        assert data["status"] == "open"

    def test_create_workshop_with_strategy_ref(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-create-002",
                "Content-Type": "application/json",
            },
            json={
                "initial_message": "Review this strategy",
                "strategy_spec_ref": "strat-draft-abc",
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data.get("strategy_id") == "strat-draft-abc" or data.get("active_strategy_spec_registry_id") == "strat-draft-abc"

    def test_get_workshop_returns_etag_in_meta(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        # Create a workshop first
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-get-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": "ETag test"},
        )
        assert create_resp.status_code == 201, create_resp.text
        workshop_id = create_resp.json()["data"]["workshop_id"]

        get_resp = client.get(
            f"/bff/agora/workshops/{workshop_id}",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        assert get_resp.status_code == 200, get_resp.text
        body = get_resp.json()
        assert "etag" in body["meta"]
        assert body["meta"]["etag"].startswith('W/"')

    def test_get_workshop_404_for_unknown(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        resp = client.get(
            "/bff/agora/workshops/no-such-workshop-xyz",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        assert resp.status_code == 404

    def test_post_message_creates_event(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-msg-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": "First message"},
        )
        workshop_id = create_resp.json()["data"]["workshop_id"]

        current_etag = _get_current_etag(client, workshop_id)
        msg_resp = client.post(
            f"/bff/agora/workshops/{workshop_id}/messages",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-msg-002",
                "If-Match": current_etag,
                "Content-Type": "application/json",
            },
            json={"content": "Follow-up message"},
        )
        assert msg_resp.status_code == 202, msg_resp.text
        body = msg_resp.json()
        assert "event_id" in body["data"]
        assert "sequence_no" in body["data"]

    def test_list_events_returns_ordered_events(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-ev-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": "Event list test"},
        )
        workshop_id = create_resp.json()["data"]["workshop_id"]

        # Post additional messages — fetch fresh ETag before each mutation.
        for i in range(2):
            current_etag = _get_current_etag(client, workshop_id)
            client.post(
                f"/bff/agora/workshops/{workshop_id}/messages",
                headers={
                    "Authorization": _OPERATOR_AUTH,
                    "Idempotency-Key": f"idem-ev-msg-{i}",
                    "If-Match": current_etag,
                    "Content-Type": "application/json",
                },
                json={"content": f"Message {i}"},
            )

        ev_resp = client.get(
            f"/bff/agora/workshops/{workshop_id}/events",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        assert ev_resp.status_code == 200, ev_resp.text
        events = ev_resp.json()["data"]
        assert isinstance(events, list)
        assert len(events) >= 1
        seqs = [e["sequence_no"] for e in events]
        assert seqs == sorted(seqs), "Events must be in sequence order"

    def test_events_do_not_contain_private_content(self, monkeypatch):
        """Privacy rule: no private content in event list response."""
        client = _workshop_client(monkeypatch)
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-priv-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": "Private alpha strategy discussion"},
        )
        workshop_id = create_resp.json()["data"]["workshop_id"]

        ev_resp = client.get(
            f"/bff/agora/workshops/{workshop_id}/events",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        for event in ev_resp.json()["data"]:
            assert "private_content" not in event, (
                "Event payload must not expose raw private_content"
            )

    def test_completeness_returns_none_when_no_snapshot(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-comp-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": "Completeness test"},
        )
        workshop_id = create_resp.json()["data"]["workshop_id"]

        comp_resp = client.get(
            f"/bff/agora/workshops/{workshop_id}/completeness",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        assert comp_resp.status_code == 200, comp_resp.text
        assert comp_resp.json()["data"] is None

    def test_deferred_versions_returns_501(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-ver-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": "Versions test"},
        )
        workshop_id = create_resp.json()["data"]["workshop_id"]

        resp = client.get(
            f"/bff/agora/workshops/{workshop_id}/versions",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        assert resp.status_code == 501

    def test_workshop_router_importable(self):
        from agora.strategy_workshop.router import create_strategy_workshop_router
        assert callable(create_strategy_workshop_router)

    def test_workshop_router_accepts_injected_store(self):
        """Router factory must accept an injected workshop_store for testability."""
        from agora.strategy_workshop import MemoryWorkshopStore
        from agora.strategy_workshop.router import create_strategy_workshop_router
        from fastapi import HTTPException

        store = MemoryWorkshopStore()
        router = create_strategy_workshop_router(
            extract_identity=lambda auth: {"sub": "test"},
            require_read_role=lambda identity: None,
            bff_error=lambda *a, **kw: HTTPException(status_code=a[0]),
            utc_now=lambda: "2026-06-21T00:00:00Z",
            workshop_store=store,
        )
        assert router is not None


# --------------------------------------------------------------------------- #
# Contract tests for concurrency (ETag/If-Match) and privacy (private_content_ref)
# AG-BE-SW-001 review-requested changes
# --------------------------------------------------------------------------- #

class TestWorkshopConcurrencyContract:
    """ETag must be in the HTTP response header with the correct format.
    Stale If-Match on mutations must return 409 RESOURCE_CONFLICT.
    """

    def test_get_workshop_returns_etag_response_header(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-cc-etag-hdr-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": "ETag header test"},
        )
        assert create_resp.status_code == 201, create_resp.text
        workshop_id = create_resp.json()["data"]["workshop_id"]

        get_resp = client.get(
            f"/bff/agora/workshops/{workshop_id}",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        assert get_resp.status_code == 200, get_resp.text
        # ETag MUST be in the HTTP response header, not just in meta
        assert "etag" in get_resp.headers, (
            "ETag must be set as an HTTP response header"
        )

    def test_get_workshop_etag_header_format(self, monkeypatch):
        """ETag format must be W/\"workshop:{id}:v{N}\" per contract §B."""
        client = _workshop_client(monkeypatch)
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-cc-etag-fmt-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": "ETag format test"},
        )
        assert create_resp.status_code == 201, create_resp.text
        workshop_id = create_resp.json()["data"]["workshop_id"]

        get_resp = client.get(
            f"/bff/agora/workshops/{workshop_id}",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        assert get_resp.status_code == 200, get_resp.text
        etag = get_resp.headers.get("etag", "")
        # Must match W/"workshop:{id}:v{N}"
        expected_prefix = f'W/"workshop:{workshop_id}:v'
        assert etag.startswith(expected_prefix), (
            f"ETag {etag!r} must start with {expected_prefix!r}"
        )
        assert etag.endswith('"'), f"ETag {etag!r} must end with closing quote"

    def test_post_message_with_stale_if_match_returns_409(self, monkeypatch):
        """Stale ETag in If-Match must be rejected with 409."""
        client = _workshop_client(monkeypatch)
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-cc-stale-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": "Stale ETag test"},
        )
        assert create_resp.status_code == 201, create_resp.text
        workshop_id = create_resp.json()["data"]["workshop_id"]

        stale_etag = f'W/"workshop:{workshop_id}:v0"'  # version 0 is always stale
        msg_resp = client.post(
            f"/bff/agora/workshops/{workshop_id}/messages",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-cc-stale-msg-001",
                "If-Match": stale_etag,
                "Content-Type": "application/json",
            },
            json={"content": "Should be rejected"},
        )
        assert msg_resp.status_code == 409, (
            f"Stale ETag must return 409, got {msg_resp.status_code}: {msg_resp.text}"
        )

    def test_post_message_with_matching_if_match_succeeds(self, monkeypatch):
        """Correct If-Match must allow the mutation through."""
        client = _workshop_client(monkeypatch)
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-cc-match-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": "Correct ETag test"},
        )
        assert create_resp.status_code == 201, create_resp.text
        workshop_id = create_resp.json()["data"]["workshop_id"]

        # Fetch current ETag from the GET response header
        get_resp = client.get(
            f"/bff/agora/workshops/{workshop_id}",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        current_etag = get_resp.headers["etag"]

        msg_resp = client.post(
            f"/bff/agora/workshops/{workshop_id}/messages",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-cc-match-msg-001",
                "If-Match": current_etag,
                "Content-Type": "application/json",
            },
            json={"content": "Should be accepted"},
        )
        assert msg_resp.status_code == 202, (
            f"Matching ETag must succeed, got {msg_resp.status_code}: {msg_resp.text}"
        )

    def test_lock_version_increments_after_message(self, monkeypatch):
        """GET ETag version number must increase after a successful mutation."""
        client = _workshop_client(monkeypatch)
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-cc-lock-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": "Lock version test"},
        )
        workshop_id = create_resp.json()["data"]["workshop_id"]

        etag_before = client.get(
            f"/bff/agora/workshops/{workshop_id}",
            headers={"Authorization": _OPERATOR_AUTH},
        ).headers["etag"]

        client.post(
            f"/bff/agora/workshops/{workshop_id}/messages",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-cc-lock-msg-001",
                "If-Match": etag_before,
                "Content-Type": "application/json",
            },
            json={"content": "Bump lock"},
        )

        etag_after = client.get(
            f"/bff/agora/workshops/{workshop_id}",
            headers={"Authorization": _OPERATOR_AUTH},
        ).headers["etag"]

        assert etag_before != etag_after, (
            "ETag must change after a successful mutation"
        )


class TestWorkshopPrivacyContract:
    """Events from message/create paths must carry private_content_ref.
    Raw message content must never appear as the redacted_summary.
    """

    def test_initial_event_has_private_content_ref(self, monkeypatch):
        """create_workshop must generate private_content_ref for the initial event."""
        client = _workshop_client(monkeypatch)
        raw_content = "Discuss alpha decay strategy with high information ratio"
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-priv-init-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": raw_content},
        )
        assert create_resp.status_code == 201, create_resp.text
        workshop_id = create_resp.json()["data"]["workshop_id"]

        ev_resp = client.get(
            f"/bff/agora/workshops/{workshop_id}/events",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        assert ev_resp.status_code == 200, ev_resp.text
        events = ev_resp.json()["data"]
        assert len(events) >= 1, "Initial event must exist"
        initial = events[0]

        assert initial.get("private_content_ref") is not None, (
            "Initial event must have private_content_ref (not None)"
        )
        # Raw content must NOT appear in redacted_summary
        redacted = initial.get("redacted_summary")
        assert redacted != raw_content, (
            "Raw initial_message must NOT be stored verbatim in redacted_summary"
        )

    def test_post_message_event_has_private_content_ref(self, monkeypatch):
        """POST /messages must generate private_content_ref; raw content must not appear."""
        client = _workshop_client(monkeypatch)
        raw_content = "Increase position sizing on mean-reversion signals"
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-priv-msg-create-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": "Workshop for privacy test"},
        )
        workshop_id = create_resp.json()["data"]["workshop_id"]

        current_etag = _get_current_etag(client, workshop_id)
        msg_resp = client.post(
            f"/bff/agora/workshops/{workshop_id}/messages",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-priv-msg-001",
                "If-Match": current_etag,
                "Content-Type": "application/json",
            },
            json={"content": raw_content},
        )
        assert msg_resp.status_code == 202, msg_resp.text

        ev_resp = client.get(
            f"/bff/agora/workshops/{workshop_id}/events",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        events = ev_resp.json()["data"]
        # Find the operator message event (last one is the posted message)
        operator_events = [e for e in events if e.get("actor_type") == "operator"]
        assert len(operator_events) >= 2, "At least 2 operator events expected"
        posted_event = operator_events[-1]

        assert posted_event.get("private_content_ref") is not None, (
            "Posted message event must have private_content_ref (not None)"
        )
        redacted = posted_event.get("redacted_summary")
        assert redacted != raw_content, (
            "Raw message content must NOT be stored verbatim in redacted_summary"
        )

    def test_event_list_never_exposes_private_content_key(self, monkeypatch):
        """Event list must not expose a 'private_content' key (raw content field)."""
        client = _workshop_client(monkeypatch)
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-priv-nokey-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": "Privacy no-key test"},
        )
        workshop_id = create_resp.json()["data"]["workshop_id"]

        ev_resp = client.get(
            f"/bff/agora/workshops/{workshop_id}/events",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        for event in ev_resp.json()["data"]:
            assert "private_content" not in event, (
                "Event payload must not expose raw private_content key"
            )


class TestMemoryWorkshopStoreConcurrencyHelpers:
    """Unit tests for the new store helpers added in AG-BE-SW-001 review pass."""

    def _store(self):
        from agora.strategy_workshop import MemoryWorkshopStore
        return MemoryWorkshopStore()

    def test_update_session_lock_version_increments(self):
        store = self._store()
        store.create_session({"workshop_id": "ws-lv-01", "tenant_id": "t1", "user_id": "u1"})
        v1 = store.update_session_lock_version("ws-lv-01")
        v2 = store.update_session_lock_version("ws-lv-01")
        assert v1 == 2
        assert v2 == 3
        session = store.get_session("ws-lv-01")
        assert session["lock_version"] == 3

    def test_update_session_lock_version_unknown_returns_1(self):
        store = self._store()
        result = store.update_session_lock_version("no-such-ws")
        assert result == 1

    def test_idempotency_key_first_call_returns_false(self):
        store = self._store()
        seen = store.check_and_record_idempotency_key("scope-a", "key-001")
        assert seen is False

    def test_idempotency_key_duplicate_call_returns_true(self):
        store = self._store()
        store.check_and_record_idempotency_key("scope-b", "key-002")
        seen = store.check_and_record_idempotency_key("scope-b", "key-002")
        assert seen is True

    def test_idempotency_key_different_scopes_do_not_collide(self):
        store = self._store()
        store.check_and_record_idempotency_key("scope-c:user1", "shared-key")
        seen = store.check_and_record_idempotency_key("scope-c:user2", "shared-key")
        assert seen is False, "Different scopes must not share idempotency key state"


# --------------------------------------------------------------------------- #
# Mandatory-header enforcement tests (AG-BE-SW-001 review-requested fixes)
# --------------------------------------------------------------------------- #

class TestWorkshopMandatoryHeaderEnforcement:
    """Verify that the router rejects requests with missing required headers."""

    def test_create_workshop_without_idempotency_key_returns_400(self, monkeypatch):
        """POST /workshops without Idempotency-Key must return 400."""
        client = _workshop_client(monkeypatch)
        resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Content-Type": "application/json",
                # Deliberately no Idempotency-Key
            },
            json={"initial_message": "Should be rejected"},
        )
        assert resp.status_code == 400, (
            f"Missing Idempotency-Key must return 400, got {resp.status_code}: {resp.text}"
        )

    def test_post_message_without_if_match_returns_428(self, monkeypatch):
        """POST /messages without If-Match must return 428 (Precondition Required)."""
        client = _workshop_client(monkeypatch)
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-enf-create-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": "Enforcement test"},
        )
        assert create_resp.status_code == 201, create_resp.text
        workshop_id = create_resp.json()["data"]["workshop_id"]

        resp = client.post(
            f"/bff/agora/workshops/{workshop_id}/messages",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-enf-msg-001",
                "Content-Type": "application/json",
                # Deliberately no If-Match
            },
            json={"content": "Should be rejected"},
        )
        assert resp.status_code == 428, (
            f"Missing If-Match must return 428, got {resp.status_code}: {resp.text}"
        )

    def test_post_message_without_idempotency_key_returns_400(self, monkeypatch):
        """POST /messages without Idempotency-Key must return 400."""
        client = _workshop_client(monkeypatch)
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-enf-create-002",
                "Content-Type": "application/json",
            },
            json={"initial_message": "Enforcement test 2"},
        )
        assert create_resp.status_code == 201, create_resp.text
        workshop_id = create_resp.json()["data"]["workshop_id"]
        current_etag = _get_current_etag(client, workshop_id)

        resp = client.post(
            f"/bff/agora/workshops/{workshop_id}/messages",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "If-Match": current_etag,
                "Content-Type": "application/json",
                # Deliberately no Idempotency-Key
            },
            json={"content": "Should be rejected"},
        )
        assert resp.status_code == 400, (
            f"Missing Idempotency-Key must return 400, got {resp.status_code}: {resp.text}"
        )
