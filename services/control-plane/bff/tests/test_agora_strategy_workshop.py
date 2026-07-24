"""Tests for AG-BE-SW-001 — workshop session/event persistence.

Covers:
- MemoryWorkshopStore: create/get/list sessions, events, completeness snapshots
- Event privacy rule: private content must NOT appear in stored events
- Router endpoints: list/create/get workshop, post message, list events, completeness
- Import and schema alignment checks
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_REPO_ROOT = Path(__file__).resolve().parents[4]
_AGORA_SCHEMA_ROOT = _REPO_ROOT / "services" / "control-plane" / "specs" / "agora"


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


class TestMemoryWorkshopStoreReadinessAndCards:
    def _store(self):
        from agora.strategy_workshop import MemoryWorkshopStore
        return MemoryWorkshopStore()

    def test_create_and_get_readiness_assessment(self):
        store = self._store()
        assessment = store.create_readiness_assessment({
            "spec_version": "1.0",
            "workshop_id": "ws-ready-1",
            "strategy_id": "strat-1",
            "workshop_version_id": "wv-1",
            "strategy_spec_registry_id": "registry-1",
            "gates": [
                {"gate": "preliminary_research", "state": "ready", "requirements": []},
                {"gate": "full_validation", "state": "conditional", "requirements": []},
                {"gate": "trading_room", "state": "blocked", "requirements": []},
            ],
            "highest_ready_gate": "preliminary_research",
            "assessed_at": "2026-07-05T00:00:00Z",
        })

        assert assessment["assessment_id"].startswith("ready_")
        assert assessment["assessment_version"] == 1
        latest = store.get_latest_readiness_assessment("ws-ready-1")
        assert latest is not None
        assert latest["assessment_id"] == assessment["assessment_id"]

    def test_readiness_assessment_versions_increment(self):
        store = self._store()
        base = {
            "spec_version": "1.0",
            "workshop_id": "ws-ready-2",
            "strategy_id": "strat-2",
            "workshop_version_id": "wv-2",
            "strategy_spec_registry_id": "registry-2",
            "gates": [],
            "assessed_at": "2026-07-05T00:00:00Z",
        }
        first = store.create_readiness_assessment(base)
        second = store.create_readiness_assessment(base)

        assert first["assessment_version"] == 1
        assert second["assessment_version"] == 2
        assert store.get_latest_readiness_assessment("ws-ready-2")["assessment_version"] == 2

    def test_record_and_list_workshop_cards(self):
        store = self._store()
        card = store.record_workshop_card({
            "card_id": "card-ready-1",
            "card_type": "readiness_gate",
            "workshop_id": "ws-cards-1",
            "sequence_no": 7,
            "status": "completed",
            "title": "Readiness",
            "payload": {"gates": [], "assessed_at": "2026-07-05T00:00:00Z"},
            "created_at": "2026-07-05T00:00:00Z",
        })

        assert card["sequence_no"] == 7
        cards = store.list_workshop_cards("ws-cards-1")
        assert [item["card_id"] for item in cards] == ["card-ready-1"]
        assert store.list_workshop_cards("ws-cards-1", after_sequence=7) == []


# --------------------------------------------------------------------------- #
# Factory and import tests
# --------------------------------------------------------------------------- #

class TestWorkshopStoreFactory:
    def test_make_workshop_store_logs_postgres_backend_without_dsn(self, monkeypatch, caplog):
        from agora.strategy_workshop import store as store_module

        class FakePostgresStore:
            def __init__(self, *, dsn, schema):
                self.dsn = dsn
                self.schema = schema

        monkeypatch.setattr(store_module, "PostgresWorkshopStore", FakePostgresStore)
        with caplog.at_level(logging.INFO, logger=store_module.__name__):
            result = store_module.make_workshop_store(
                backend="postgres",
                dsn="postgresql://secret-user:secret-password@postgres/pantheon",
                schema="agora",
            )

        assert isinstance(result, FakePostgresStore)
        assert "backend=postgres" in caplog.text
        assert "store=FakePostgresStore" in caplog.text
        assert "schema=agora" in caplog.text
        assert "secret-user" not in caplog.text
        assert "secret-password" not in caplog.text

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

    def test_postgres_create_event_replays_equivalent_and_rejects_collision(self):
        dsn = os.getenv("TEST_DATABASE_URL")
        if not dsn:
            pytest.skip("TEST_DATABASE_URL is not set")
        from agora.strategy_workshop import PostgresWorkshopStore

        store = PostgresWorkshopStore(dsn=dsn, schema=f"agora_ws_{uuid.uuid4().hex[:12]}")
        workshop_id = f"ws-{uuid.uuid4().hex}"
        store.create_session({
            "workshop_id": workshop_id, "tenant_id": "tenant-a", "user_id": "user-a",
        })
        event = {
            "event_id": f"evt-{uuid.uuid4().hex}", "workshop_id": workshop_id,
            "actor_type": "operator", "event_type": "opinion_requested",
            "private_content_ref": "private://one", "redacted_summary": "safe",
            "payload_refs_json": {"interaction_id": "int-1"}, "trace_id": "trace-1",
        }
        first = store.create_event(event)
        replay = store.create_event(event)
        assert replay["sequence_no"] == first["sequence_no"]
        assert len(store.list_events(workshop_id)) == 1
        with pytest.raises(ValueError, match="different payload"):
            store.create_event({**event, "redacted_summary": "different"})


# --------------------------------------------------------------------------- #
# Router endpoint integration tests (in-memory store, permissive auth)
# --------------------------------------------------------------------------- #

_OPERATOR_AUTH = "Bearer agora-test-user:operator"


def _workshop_client(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    from agora.strategy_workshop.operations import WorkshopCanonicalOperations

    def get_strategy_spec(_self, registry_id):
        return {
            "entry": {
                "registry_id": registry_id,
                "strategy_id": f"strategy-family-for-{registry_id}",
                "version": "1.0.0",
                "artifact_state": "draft",
                "lineage": {"source_run_ids": ["test-source"]},
                "metadata": {
                    "strategy_spec": {
                        "spec_version": "1.0",
                        "strategy_id": f"strategy-family-for-{registry_id}",
                    },
                },
            },
            "deployment_stage": "none",
        }

    monkeypatch.setattr(
        WorkshopCanonicalOperations,
        "get_strategy_spec",
        get_strategy_spec,
    )
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


def _create_workshop(client, idem_key: str, *, strategy_ref: str | None = None) -> str:
    payload = {"initial_message": "Private winner-branch strategy description"}
    if strategy_ref:
        payload["strategy_spec_ref"] = strategy_ref
    resp = client.post(
        "/bff/agora/workshops",
        headers={
            "Authorization": _OPERATOR_AUTH,
            "Idempotency-Key": idem_key,
            "Content-Type": "application/json",
        },
        json=payload,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["workshop_id"]


def _ready_state_map() -> dict:
    return {
        "data_pit": "confirmed",
        "exit_invalidation": "confirmed",
        "entry_signal": "confirmed",
        "risk_constraints": "confirmed",
        "position_sizing": "confirmed",
        "universe_rule": "confirmed",
        "liquidity": "confirmed",
    }


def _validate_schema(schema_name: str, payload: dict) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((_AGORA_SCHEMA_ROOT / schema_name).read_text(encoding="utf-8"))
    jsonschema.Draft7Validator(schema, format_checker=jsonschema.FormatChecker()).validate(payload)


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
        assert data["strategy_id"] == "strategy-family-for-strat-draft-abc"
        assert data["active_strategy_spec_registry_id"] == "strat-draft-abc"
        assert data["strategy_id"] != data["active_strategy_spec_registry_id"]

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

    def test_post_winner_branch_completeness_blocks_projection(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        workshop_id = _create_workshop(
            client,
            "idem-comp-winner-branch-create-001",
            strategy_ref="strat-winner-branch-001",
        )
        etag_before = _get_current_etag(client, workshop_id)

        # 12 blocks, all confirmed except sizing_leverage (missing) and monitoring_update (missing)
        winner_state_map = {
            "market_scope": "confirmed",
            "insider_branch_mapping": "confirmed",
            "winner_branch_scoring": "confirmed",
            "migration_reverse_flow": "confirmed",
            "event_lead": "confirmed",
            "signal_formation": "confirmed",
            "entry_holding": "confirmed",
            "add_reduce_exit": "confirmed",
            "sizing_leverage": "missing",
            "cost_liquidity_capacity": "confirmed",
            "validation_backtest_refutation": "confirmed",
            "monitoring_update": "missing",
        }

        create_snapshot = client.post(
            f"/bff/agora/workshops/{workshop_id}/completeness",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-comp-winner-branch-post-001",
                "If-Match": etag_before,
                "Content-Type": "application/json",
            },
            json={
                "strategy_version_id": "wv-winner-branch-001",
                "state_map_json": winner_state_map,
                "blocking_items_json": [],
                "next_question_json": {},
            },
        )

        assert create_snapshot.status_code == 201, create_snapshot.text
        payload = create_snapshot.json()["data"]

        # Since sizing_leverage is weak, risk_constraints / position_sizing will be weak, which blocks validation
        assert payload["readiness"]["highest_ready_gate"] == "preliminary_research"

        # Check that completeness card generated dimension updates mapped correctly
        cards = client.get(
            f"/bff/agora/workshops/{workshop_id}/cards",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        assert cards.status_code == 200
        comp_card = next(c for c in cards.json()["data"] if c["card_type"] == "completeness_update")
        dim_updates = {u["dimension"]: u for u in comp_card["payload"]["dimension_updates"]}

        # 7 generic dimensions projected from the 12 blocks:
        # hypothesis (event_lead, signal_formation) -> complete
        assert dim_updates["hypothesis"]["current_grade"] == "complete"
        # risk_constraints (sizing_leverage) -> missing (missing -> missing)
        assert dim_updates["risk_constraints"]["current_grade"] == "missing"
        # governance (monitoring_update) -> missing
        assert dim_updates["governance"]["current_grade"] == "missing"

        client = _workshop_client(monkeypatch)
        workshop_id = _create_workshop(
            client,
            "idem-comp-materialize-create-001",
            strategy_ref="strat-live-materialize-001",
        )
        etag_before = _get_current_etag(client, workshop_id)

        create_snapshot = client.post(
            f"/bff/agora/workshops/{workshop_id}/completeness",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-comp-materialize-post-001",
                "If-Match": etag_before,
                "Content-Type": "application/json",
            },
            json={
                "strategy_version_id": "wv-live-materialize-001",
                "state_map_json": _ready_state_map(),
                "blocking_items_json": [],
                "next_question_json": {},
            },
        )

        assert create_snapshot.status_code == 201, create_snapshot.text
        assert "etag" in create_snapshot.headers
        assert create_snapshot.headers["etag"] != etag_before
        payload = create_snapshot.json()["data"]
        assert payload["snapshot"]["strategy_version_id"] == "wv-live-materialize-001"
        assert payload["readiness"]["highest_ready_gate"] == "trading_room"

        completeness = client.get(
            f"/bff/agora/workshops/{workshop_id}/completeness",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        readiness = client.get(
            f"/bff/agora/workshops/{workshop_id}/readiness",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        cards = client.get(
            f"/bff/agora/workshops/{workshop_id}/cards",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        trading_room = client.get(
            "/bff/agora/trading-room",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        strategy_detail = client.get(
            "/bff/agora/trading-room/strategies/"
            "strategy-family-for-strat-live-materialize-001",
            headers={"Authorization": _OPERATOR_AUTH},
        )

        assert completeness.status_code == 200, completeness.text
        assert completeness.json()["data"]["state_map_json"]["entry_signal"] == "confirmed"
        assert readiness.status_code == 200, readiness.text
        assert readiness.json()["data"]["highest_ready_gate"] == "trading_room"
        assert cards.status_code == 200, cards.text
        card_types = [card["card_type"] for card in cards.json()["data"]]
        assert "completeness_update" in card_types
        assert "readiness_gate" in card_types
        assert trading_room.status_code == 200, trading_room.text
        strategies = trading_room.json()["strategies"]
        assert [item["strategy_id"] for item in strategies] == [
            "strategy-family-for-strat-live-materialize-001"
        ]
        assert strategy_detail.status_code == 200, strategy_detail.text
        assert strategy_detail.json()["data"]["strategy_version"] == "wv-live-materialize-001"

    def test_post_completeness_requires_if_match_and_idempotency_key(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        workshop_id = _create_workshop(
            client,
            "idem-comp-requires-create-001",
            strategy_ref="strat-live-materialize-002",
        )
        etag = _get_current_etag(client, workshop_id)

        missing_if_match = client.post(
            f"/bff/agora/workshops/{workshop_id}/completeness",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-comp-missing-if",
                "Content-Type": "application/json",
            },
            json={"state_map_json": _ready_state_map()},
        )
        missing_idem = client.post(
            f"/bff/agora/workshops/{workshop_id}/completeness",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "If-Match": etag,
                "Content-Type": "application/json",
            },
            json={"state_map_json": _ready_state_map()},
        )

        assert missing_if_match.status_code == 428, missing_if_match.text
        assert missing_idem.status_code == 400, missing_idem.text

    def test_cards_returns_live_projection_for_existing_workshop(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        workshop_id = _create_workshop(client, "idem-cards-live-001")

        cards_resp = client.get(
            f"/bff/agora/workshops/{workshop_id}/cards",
            headers={"Authorization": _OPERATOR_AUTH},
        )

        assert cards_resp.status_code == 200, cards_resp.text
        cards = cards_resp.json()["data"]
        assert [card["card_type"] for card in cards] == [
            "user_strategy_description",
            "readiness_gate",
        ]
        assert "Private winner-branch strategy description" not in json.dumps(cards)
        _validate_schema("v4/workshop_card.schema.json", cards[0])

    def test_readiness_returns_assessment_for_existing_workshop(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        workshop_id = _create_workshop(client, "idem-readiness-live-001", strategy_ref="strat-live-1")

        readiness_resp = client.get(
            f"/bff/agora/workshops/{workshop_id}/readiness",
            headers={"Authorization": _OPERATOR_AUTH},
        )

        assert readiness_resp.status_code == 200, readiness_resp.text
        readiness = readiness_resp.json()["data"]
        assert readiness["workshop_id"] == workshop_id
        assert readiness["strategy_id"] == "strategy-family-for-strat-live-1"
        assert {gate["gate"] for gate in readiness["gates"]} == {
            "preliminary_research",
            "full_validation",
            "trading_room",
        }
        _validate_schema("v4/strategy_readiness.schema.json", readiness)

    def test_reassess_persists_fresh_readiness_and_bumps_etag(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        workshop_id = _create_workshop(client, "idem-reassess-create-001", strategy_ref="strat-live-2")
        etag_before = _get_current_etag(client, workshop_id)

        reassess_resp = client.post(
            f"/bff/agora/workshops/{workshop_id}/readiness/reassess",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-reassess-post-001",
                "If-Match": etag_before,
                "Content-Type": "application/json",
            },
            json={},
        )

        assert reassess_resp.status_code == 202, reassess_resp.text
        readiness = reassess_resp.json()["data"]
        assert readiness["assessment_version"] == 1
        assert "etag" in reassess_resp.headers
        assert reassess_resp.headers["etag"] != etag_before

        get_resp = client.get(
            f"/bff/agora/workshops/{workshop_id}/readiness",
            headers={"Authorization": _OPERATOR_AUTH},
        )
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["data"]["assessment_id"] == readiness["assessment_id"]

    def test_reassess_requires_if_match_and_idempotency_key(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        workshop_id = _create_workshop(client, "idem-reassess-create-002")
        etag = _get_current_etag(client, workshop_id)

        missing_if_match = client.post(
            f"/bff/agora/workshops/{workshop_id}/readiness/reassess",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-reassess-missing-if",
                "Content-Type": "application/json",
            },
            json={},
        )
        assert missing_if_match.status_code == 428, missing_if_match.text

        missing_idem = client.post(
            f"/bff/agora/workshops/{workshop_id}/readiness/reassess",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "If-Match": etag,
                "Content-Type": "application/json",
            },
            json={},
        )
        assert missing_idem.status_code == 400, missing_idem.text

    def test_readiness_and_cards_unknown_workshop_return_404(self, monkeypatch):
        client = _workshop_client(monkeypatch)
        for suffix in ("readiness", "cards"):
            resp = client.get(
                f"/bff/agora/workshops/no-such-workshop-live/{suffix}",
                headers={"Authorization": _OPERATOR_AUTH},
            )
            assert resp.status_code == 404, resp.text

    def test_readiness_cross_tenant_returns_403_not_internal_error(self, monkeypatch):
        monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
        monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha,tenant-beta")
        client = _workshop_client(monkeypatch)
        workshop_id = _create_workshop(client, "idem-cross-tenant-readiness-001")

        resp = client.get(
            f"/bff/agora/workshops/{workshop_id}/readiness",
            headers={"Authorization": _OPERATOR_AUTH, "X-Tenant-Id": "tenant-beta"},
        )

        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "FORBIDDEN"

    def test_versions_route_is_live_and_returns_authoritative_empty_list(self, monkeypatch):
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
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["versions"] == []
        assert resp.headers["etag"].startswith('W/"workshop:')

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
            require_write_role=lambda identity: None,
            bff_error=lambda *a, **kw: HTTPException(status_code=a[0]),
            utc_now=lambda: "2026-06-21T00:00:00Z",
            workshop_store=store,
        )
        assert router is not None

    def test_readiness_route_accepts_injected_dict_identity(self):
        from agora.strategy_workshop import MemoryWorkshopStore
        from agora.strategy_workshop.router import create_strategy_workshop_router
        from fastapi import FastAPI, HTTPException

        store = MemoryWorkshopStore()
        store.create_session({
            "workshop_id": "ws-dict-identity",
            "tenant_id": "tenant-alpha",
            "user_id": "user-alpha",
            "strategy_id": "strat-dict-1",
            "active_strategy_spec_registry_id": "registry-dict-1",
        })
        store.create_event({
            "workshop_id": "ws-dict-identity",
            "actor_type": "operator",
            "event_type": "message",
            "private_content_ref": "priv://dict-identity/event-1",
            "redacted_summary": "Winner branch strategy captured.",
        })
        store.create_completeness_snapshot({
            "workshop_id": "ws-dict-identity",
            "strategy_version_id": "wv-dict-1",
            "state_map_json": {
                "data_pit": "confirmed",
                "exit_invalidation": "confirmed",
                "entry_signal": "confirmed",
                "risk_constraints": "confirmed",
                "position_sizing": "confirmed",
                "universe_rule": "confirmed",
                "liquidity": "confirmed",
            },
            "blocking_items_json": [],
            "next_question_json": {},
        })
        identity = {
            "operator_id": "user-alpha",
            "roles": ["operator"],
            "claims": {
                "tenant_id": "tenant-alpha",
                "allowed_tenants": ["tenant-alpha"],
            },
        }
        app = FastAPI()
        app.include_router(create_strategy_workshop_router(
            extract_identity=lambda auth: identity,
            require_read_role=lambda current: None,
            require_write_role=lambda current: None,
            bff_error=lambda status_code, *args, **kwargs: HTTPException(status_code=status_code),
            utc_now=lambda: "2026-07-05T00:00:00Z",
            workshop_store=store,
        ))
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get(
            "/bff/agora/workshops/ws-dict-identity/readiness",
            headers={"Authorization": "Bearer ignored"},
        )

        assert resp.status_code == 200, resp.text
        readiness = resp.json()["data"]
        assert readiness["highest_ready_gate"] == "trading_room"
        assert readiness["workshop_version_id"] == "wv-dict-1"

    def test_cards_route_accepts_injected_operator_identity(self):
        from agora.strategy_workshop import MemoryWorkshopStore
        from agora.strategy_workshop.router import create_strategy_workshop_router
        from fastapi import FastAPI, HTTPException
        from models import OperatorIdentity

        store = MemoryWorkshopStore()
        store.create_session({
            "workshop_id": "ws-operator-identity",
            "tenant_id": "tenant-alpha",
            "user_id": "user-alpha",
        })
        store.create_event({
            "workshop_id": "ws-operator-identity",
            "actor_type": "operator",
            "event_type": "message",
            "private_content_ref": "priv://operator-identity/event-1",
        })
        identity = OperatorIdentity(
            operator_id="user-alpha",
            roles=["operator"],
            claims={
                "tenant_id": "tenant-alpha",
                "allowed_tenants": ["tenant-alpha"],
            },
        )
        app = FastAPI()
        app.include_router(create_strategy_workshop_router(
            extract_identity=lambda auth: identity,
            require_read_role=lambda current: None,
            require_write_role=lambda current: None,
            bff_error=lambda status_code, *args, **kwargs: HTTPException(status_code=status_code),
            utc_now=lambda: "2026-07-05T00:00:00Z",
            workshop_store=store,
        ))
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get(
            "/bff/agora/workshops/ws-operator-identity/cards",
            headers={"Authorization": "Bearer ignored"},
        )

        assert resp.status_code == 200, resp.text
        assert {card["card_type"] for card in resp.json()["data"]} == {
            "user_strategy_description",
            "readiness_gate",
        }


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

    def test_stale_if_match_discards_private_content_orphan(self, monkeypatch):
        from agora.strategy_workshop import MemoryWorkshopStore
        from agora.strategy_workshop.router import create_strategy_workshop_router
        from fastapi import FastAPI, HTTPException
        from privacy.private_content_models import PrivateContentAccessDenied
        from privacy.private_content_store import EphemeralKeyProvider, MemoryPrivateContentStore

        workshop_store = MemoryWorkshopStore()
        workshop_store.create_session({
            "workshop_id": "ws-cas-orphan", "tenant_id": "tenant-alpha",
            "user_id": "user-alpha",
        })
        private_store = MemoryPrivateContentStore(key_provider=EphemeralKeyProvider())
        written = []
        original_put = private_store.put

        def recording_put(**kwargs):
            descriptor = original_put(**kwargs)
            written.append(descriptor)
            return descriptor

        private_store.put = recording_put
        identity = {"operator_id": "user-alpha", "roles": ["operator"],
                    "claims": {"tenant_id": "tenant-alpha"}}
        app = FastAPI()
        app.include_router(create_strategy_workshop_router(
            extract_identity=lambda auth: identity,
            require_read_role=lambda current: None,
            require_write_role=lambda current: None,
            bff_error=lambda status_code, *args, **kwargs: HTTPException(status_code=status_code),
            utc_now=lambda: "2026-07-12T00:00:00Z",
            workshop_store=workshop_store,
            private_content_store=private_store,
        ))
        response = TestClient(app, raise_server_exceptions=False).post(
            "/bff/agora/workshops/ws-cas-orphan/messages",
            headers={"Authorization": "Bearer ignored", "Idempotency-Key": "cas-fail",
                     "If-Match": 'W/"workshop:ws-cas-orphan:v0"'},
            json={"content": "must not survive"},
        )
        assert response.status_code == 409
        assert len(written) == 1
        with pytest.raises(PrivateContentAccessDenied):
            private_store.get_for_owner(
                private_content_ref=written[0].private_content_ref,
                tenant_id="tenant-alpha", owner_user_id="user-alpha",
                purpose="orphan-regression", request_id="req-orphan",
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

    def test_post_message_409_includes_current_etag_and_latest_href(self, monkeypatch):
        """409 response body must include current_etag and latest_href in details."""
        client = _workshop_client(monkeypatch)
        create_resp = client.post(
            "/bff/agora/workshops",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-cc-409-detail-001",
                "Content-Type": "application/json",
            },
            json={"initial_message": "409 detail test"},
        )
        assert create_resp.status_code == 201, create_resp.text
        workshop_id = create_resp.json()["data"]["workshop_id"]

        stale_etag = f'W/"workshop:{workshop_id}:v0"'
        resp = client.post(
            f"/bff/agora/workshops/{workshop_id}/messages",
            headers={
                "Authorization": _OPERATOR_AUTH,
                "Idempotency-Key": "idem-cc-409-detail-msg-001",
                "If-Match": stale_etag,
                "Content-Type": "application/json",
            },
            json={"content": "Should be rejected"},
        )
        assert resp.status_code == 409, resp.text
        body = resp.json()
        # details must carry current_etag and latest_href for client recovery
        details = body.get("details") or body.get("error", {}).get("details", {})
        if not isinstance(details, dict):
            details = {}
        assert "current_etag" in details, (
            f"409 response must include current_etag; got details={details}"
        )
        assert "latest_href" in details, (
            f"409 response must include latest_href; got details={details}"
        )


class TestWorkshopPrivacyContract:
    """Events from message/create paths must carry private_content_ref.
    Raw message content must never appear as the redacted_summary.
    """

    def test_create_private_store_failure_leaves_no_partial_session(self):
        from agora.strategy_workshop import MemoryWorkshopStore
        from agora.strategy_workshop.router import create_strategy_workshop_router
        from fastapi import FastAPI, HTTPException

        class FailingPrivateStore:
            def put(self, **kwargs):
                raise RuntimeError("private store unavailable")

        workshop_store = MemoryWorkshopStore()
        identity = {"operator_id": "user-alpha", "roles": ["operator"],
                    "claims": {"tenant_id": "tenant-alpha"}}
        app = FastAPI()
        app.include_router(create_strategy_workshop_router(
            extract_identity=lambda auth: identity,
            require_read_role=lambda current: None,
            require_write_role=lambda current: None,
            bff_error=lambda status_code, *args, **kwargs: HTTPException(status_code=status_code),
            utc_now=lambda: "2026-07-12T00:00:00Z",
            workshop_store=workshop_store,
            private_content_store=FailingPrivateStore(),
        ))
        response = TestClient(app, raise_server_exceptions=False).post(
            "/bff/agora/workshops",
            headers={"Authorization": "Bearer ignored", "Idempotency-Key": "create-fail"},
            json={"initial_message": "must not create a session"},
        )
        assert response.status_code == 500
        sessions, _ = workshop_store.list_sessions(
            user_id="user-alpha", tenant_id="tenant-alpha"
        )
        assert sessions == []

    def test_create_event_failure_discards_private_object_and_session(self):
        from agora.strategy_workshop import MemoryWorkshopStore
        from agora.strategy_workshop.router import create_strategy_workshop_router
        from fastapi import FastAPI, HTTPException
        from privacy.private_content_models import PrivateContentAccessDenied
        from privacy.private_content_store import EphemeralKeyProvider, MemoryPrivateContentStore

        class EventFailingStore(MemoryWorkshopStore):
            def create_event(self, event):
                raise RuntimeError("event write failed")

        workshop_store = EventFailingStore()
        private_store = MemoryPrivateContentStore(key_provider=EphemeralKeyProvider())
        written = []
        original_put = private_store.put

        def recording_put(**kwargs):
            descriptor = original_put(**kwargs)
            written.append(descriptor)
            return descriptor

        private_store.put = recording_put
        identity = {"operator_id": "user-alpha", "roles": ["operator"],
                    "claims": {"tenant_id": "tenant-alpha"}}
        app = FastAPI()
        app.include_router(create_strategy_workshop_router(
            extract_identity=lambda auth: identity,
            require_read_role=lambda current: None,
            require_write_role=lambda current: None,
            bff_error=lambda status_code, *args, **kwargs: HTTPException(status_code=status_code),
            utc_now=lambda: "2026-07-12T00:00:00Z",
            workshop_store=workshop_store,
            private_content_store=private_store,
        ))
        response = TestClient(app, raise_server_exceptions=False).post(
            "/bff/agora/workshops",
            headers={"Authorization": "Bearer ignored", "Idempotency-Key": "event-fail"},
            json={"initial_message": "must be hard discarded"},
        )
        assert response.status_code == 500
        sessions, _ = workshop_store.list_sessions(
            user_id="user-alpha", tenant_id="tenant-alpha"
        )
        assert sessions == []
        assert len(written) == 1
        with pytest.raises(PrivateContentAccessDenied):
            private_store.get_for_owner(
                private_content_ref=written[0].private_content_ref,
                tenant_id="tenant-alpha", owner_user_id="user-alpha",
                purpose="orphan-regression", request_id="req-create-orphan",
            )

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

    def test_append_event_cas_success(self):
        """CAS succeeds when expected_lock_version matches current."""
        store = self._store()
        store.create_session({"workshop_id": "ws-cas-1", "tenant_id": "t1", "user_id": "u1"})

        ev, new_ver = store.append_event_cas("ws-cas-1", 1, {
            "actor_type": "operator",
            "event_type": "message",
            "private_content_ref": "ref-cas-1",
        })
        assert ev is not None
        assert ev["sequence_no"] == 1
        assert ev["private_content_ref"] == "ref-cas-1"
        assert new_ver == 2

        session = store.get_session("ws-cas-1")
        assert session["lock_version"] == 2

    def test_append_event_cas_conflict_returns_current_version(self):
        """CAS fails when expected_lock_version doesn't match; returns actual current version."""
        store = self._store()
        store.create_session({"workshop_id": "ws-cas-2", "tenant_id": "t1", "user_id": "u1"})

        ev, current_ver = store.append_event_cas("ws-cas-2", 99, {
            "actor_type": "operator",
            "event_type": "message",
        })
        assert ev is None
        assert current_ver == 1

        # No event must have been created and lock_version must be unchanged.
        assert store.list_events("ws-cas-2") == []
        assert store.get_session("ws-cas-2")["lock_version"] == 1

    def test_append_event_cas_sequential_same_version_only_first_wins(self):
        """Two sequential CAS ops with the same expected version: only the first succeeds."""
        store = self._store()
        store.create_session({"workshop_id": "ws-cas-3", "tenant_id": "t1", "user_id": "u1"})

        ev1, new_ver1 = store.append_event_cas("ws-cas-3", 1, {
            "actor_type": "operator", "event_type": "message",
        })
        ev2, current_ver2 = store.append_event_cas("ws-cas-3", 1, {
            "actor_type": "operator", "event_type": "message",
        })

        assert ev1 is not None
        assert new_ver1 == 2
        assert ev2 is None
        assert current_ver2 == 2

        assert len(store.list_events("ws-cas-3")) == 1

    def test_append_event_cas_not_found_returns_none_none(self):
        """CAS on a non-existent workshop returns (None, None)."""
        store = self._store()
        ev, ver = store.append_event_cas("no-such-ws", 1, {
            "actor_type": "operator", "event_type": "message",
        })
        assert ev is None
        assert ver is None


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


# --------------------------------------------------------------------------- #
# Public Registry + Governance + Workshop exact-identity contract
# --------------------------------------------------------------------------- #

_PUBLIC_TENANT_ID = "tenant-workshop-contract"
_PUBLIC_USER_ID = "operator-workshop-contract"
_PUBLIC_APPROVER_ID = "reviewer-workshop-contract"


def _public_strategy_spec(strategy_id: str) -> dict:
    return {
        "spec_version": "1.0",
        "strategy_id": strategy_id,
        "title": "Public API workshop contract strategy",
        "hypothesis": "A bounded momentum effect is suitable for research.",
        "objective": "Validate the candidate without deployment authority.",
        "lifecycle_state": "draft",
        "market_scope": {
            "symbols": ["RESEARCH_UNIVERSE"],
            "frequency": "1d",
        },
        "data_dependencies": [
            {"ref": "dataset:workshop-contract", "kind": "dataset"},
        ],
        "execution_profile": {
            "signal_schema_version": "1.0",
            "quantity_type": "PERCENT_PORTFOLIO",
            "execution_mode_hint": "research",
        },
        "evaluation_plan": {"metrics": ["sharpe_ratio"]},
        "governance": {
            "approval_required": True,
            "policy_id": "research-only-v1",
        },
        "provenance": {
            "source_kind": "manual",
            "created_at": "2026-07-24T00:00:00Z",
        },
    }


class _PublicApiCanonicalOperations:
    """Use real Registry/Governance HTTP APIs for Workshop canonical reads."""

    def __init__(self, registry_client, governance_client):
        self.registry_client = registry_client
        self.governance_client = governance_client

    @staticmethod
    def _require_success(response, authority: str) -> dict:
        from agora.strategy_workshop.operations import CanonicalOperationError

        if response.status_code >= 400:
            raise CanonicalOperationError(
                authority,
                f"public API returned HTTP {response.status_code}",
                status_code=response.status_code,
                retryable=response.status_code >= 500,
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise CanonicalOperationError(
                authority,
                "public API returned a non-object payload",
            )
        return payload

    def get_strategy_spec(self, registry_id: str) -> dict:
        return self._require_success(
            self.registry_client.get(
                f"/api/registry/strategy-specs/{registry_id}"
            ),
            "strategy_registry",
        )

    def create_strategy_spec(self, payload: dict) -> dict:
        self._require_success(
            self.registry_client.post(
                "/api/registry/strategy-specs",
                json=payload,
            ),
            "strategy_registry",
        )
        return self.get_strategy_spec(str(payload["registry_id"]))

    def get_approval_decision(self, decision_id: str) -> dict:
        from read_store import ReadSurfaceStore

        raw = self._require_success(
            self.governance_client.get(
                f"/api/governance/approvals/{decision_id}"
            ),
            "approval_decision_store",
        )
        return ReadSurfaceStore._project_canonical_approval_decision(raw)

    def dispatch_research_run(
        self,
        *,
        task_payload: dict,
        run_payload: dict,
        resume=None,
    ) -> dict:
        assert task_payload["constraints"]["no_live_capital"] is True
        assert run_payload["dispatch_mode"] == "handoff_only"
        assert resume is None
        return {
            "task": {
                "task_id": "research-task-public-contract",
                "status": "accepted",
            },
            "run": {
                "run_id": "research-run-public-contract",
                "task_id": "research-task-public-contract",
                "status": "queued",
            },
        }

    def open_consultation(
        self,
        *,
        request_id: str,
        payload: dict,
        resume: bool = False,
    ) -> dict:
        assert payload["target_type"] == "strategy_workshop"
        assert resume is False
        return {
            "request_id": request_id,
            "target_id": payload["target_id"],
            "status": "submitted",
        }

    def cancel_consultation(self, *_args, **_kwargs) -> None:
        raise AssertionError("Successful public contract flow must not compensate")


def _public_contract_bff_error(
    status_code,
    code,
    message,
    reason,
    precondition_failed=None,
    suggestion=None,
    details_extra=None,
    **_kwargs,
):
    from fastapi import HTTPException

    details = {"reason": reason}
    if precondition_failed is not None:
        details["precondition_failed"] = precondition_failed
    if suggestion is not None:
        details["suggestion"] = suggestion
    details.update(details_extra or {})
    return HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": getattr(code, "value", str(code)),
                "message": message,
                "details": details,
            }
        },
    )


def _public_workshop_client(workshop_store, canonical_operations):
    from fastapi import FastAPI
    from agora.strategy_workshop.router import create_strategy_workshop_router

    identity = {
        "operator_id": _PUBLIC_USER_ID,
        "roles": ["operator"],
        "token_kind": "test",
        "mfa_verified": True,
        "claims": {
            "tenant_id": _PUBLIC_TENANT_ID,
            "allowed_tenants": [_PUBLIC_TENANT_ID],
            "user_id": _PUBLIC_USER_ID,
        },
    }
    app = FastAPI()
    app.include_router(
        create_strategy_workshop_router(
            extract_identity=lambda _authorization, **_kwargs: identity,
            require_read_role=lambda _identity: None,
            require_write_role=lambda _identity: None,
            bff_error=_public_contract_bff_error,
            utc_now=lambda: "2026-07-24T00:00:00Z",
            workshop_store=workshop_store,
            canonical_operations=canonical_operations,
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def _public_command_headers(key: str, etag: str) -> dict:
    return {
        "Authorization": "Bearer workshop-contract",
        "X-Tenant-Id": _PUBLIC_TENANT_ID,
        "X-MFA-Token": "mfa-workshop-contract",
        "If-Match": etag,
        "Idempotency-Key": key,
        "X-Request-Id": f"request-{key}",
    }


class _ApprovalGateCanonicalOperations:
    """Expose one projected approval while detecting downstream side effects."""

    def __init__(self, approval: dict):
        self.approval = dict(approval)
        self.research_dispatches = 0
        self.registry_reads = 0

    def get_approval_decision(self, decision_id: str) -> dict:
        assert decision_id == self.approval["decision_id"]
        return dict(self.approval)

    def dispatch_research_run(self, **_kwargs) -> dict:
        self.research_dispatches += 1
        raise AssertionError("invalid approval must not dispatch research")

    def get_strategy_spec(self, _registry_id: str) -> dict:
        self.registry_reads += 1
        raise AssertionError("invalid approval must not begin conclusion readback")


@pytest.mark.parametrize(
    ("state", "outcome", "reason"),
    [
        pytest.param(None, "approved", "APPROVAL_NOT_DECIDED", id="missing-state"),
        pytest.param(
            "under_review",
            "approved",
            "APPROVAL_NOT_DECIDED",
            id="non-decided-state",
        ),
        pytest.param(
            "approved",
            "approved",
            "APPROVAL_NOT_DECIDED",
            id="approved-state-alias",
        ),
        pytest.param(
            "completed",
            "approved",
            "APPROVAL_NOT_DECIDED",
            id="completed-state-alias",
        ),
        pytest.param(
            "decided",
            "accepted",
            "APPROVAL_NOT_APPROVED",
            id="accepted-outcome-alias",
        ),
        pytest.param(
            "decided",
            "approve",
            "APPROVAL_NOT_APPROVED",
            id="approve-outcome-alias",
        ),
    ],
)
def test_public_workshop_operations_fail_closed_on_noncanonical_approval(
    state,
    outcome,
    reason,
):
    """Research and conclude reject invalid approvals before durable effects."""

    from agora.strategy_workshop import MemoryWorkshopStore

    workshop_id = "ws-approval-gate-contract"
    version_id = "wsv-approval-gate-contract"
    registry_id = "registry-approval-gate-contract"
    approval_id = "approval-gate-contract"
    store = MemoryWorkshopStore()
    store.create_session(
        {
            "workshop_id": workshop_id,
            "tenant_id": _PUBLIC_TENANT_ID,
            "user_id": _PUBLIC_USER_ID,
            "strategy_id": "strategy-approval-gate-contract",
            "active_strategy_spec_registry_id": registry_id,
            "active_workshop_version_id": version_id,
            "selected_version_id": version_id,
            "status": "in_review",
        }
    )
    store.ensure_current_version_link(
        workshop_id=workshop_id,
        strategy_id="strategy-approval-gate-contract",
        strategy_spec_registry_id=registry_id,
        document_sha256="a" * 64,
    )
    approval = {
        "decision_id": approval_id,
        "outcome": outcome,
        "tenant_id": _PUBLIC_TENANT_ID,
        "owner_user_id": _PUBLIC_USER_ID,
        "target_type": "strategy_workshop",
        "target_id": workshop_id,
        "target_version": version_id,
        "reviewer": _PUBLIC_APPROVER_ID,
    }
    if state is not None:
        approval["state"] = state
    canonical = _ApprovalGateCanonicalOperations(approval)
    client = _public_workshop_client(store, canonical)
    etag = f'W/"workshop:{workshop_id}:v1"'
    session_before = store.get_session(workshop_id)
    events_before = store.list_events(workshop_id)

    research_key = f"research-invalid-approval-{state}-{outcome}"
    research = client.post(
        f"/bff/agora/workshops/{workshop_id}/research-runs",
        headers=_public_command_headers(research_key, etag),
        json={
            "research_context": "Must not dispatch without canonical approval.",
            "strategy_version_ref": version_id,
            "parameters": {"environment": "research"},
            "approval_decision_id": approval_id,
            "adapter": "handoff_only",
            "requested_mode": "handoff_only",
            "dispatch_mode": "handoff_only",
        },
    )
    assert research.status_code == 409, research.text
    assert research.json()["detail"]["error"]["details"]["reason"] == reason
    assert canonical.research_dispatches == 0
    assert (
        store.get_command_receipt(
            workshop_id=workshop_id,
            tenant_id=_PUBLIC_TENANT_ID,
            user_id=_PUBLIC_USER_ID,
            operation="dispatch_research",
            idempotency_key=research_key,
        )
        is None
    )

    conclude_key = f"conclude-invalid-approval-{state}-{outcome}"
    concluded = client.post(
        f"/bff/agora/workshops/{workshop_id}/conclude",
        headers=_public_command_headers(conclude_key, etag),
        json={
            "final_version_id": version_id,
            "conclusion_notes": "Must remain in review.",
            "approval_decision_id": approval_id,
        },
    )
    assert concluded.status_code == 409, concluded.text
    assert concluded.json()["detail"]["error"]["details"]["reason"] == reason
    assert canonical.registry_reads == 0
    assert (
        store.get_command_receipt(
            workshop_id=workshop_id,
            tenant_id=_PUBLIC_TENANT_ID,
            user_id=_PUBLIC_USER_ID,
            operation="conclude",
            idempotency_key=conclude_key,
        )
        is None
    )
    assert store.get_session(workshop_id) == session_before
    assert store.list_events(workshop_id) == events_before


def test_public_exact_identity_approval_flow_survives_restart(
    tmp_path,
    monkeypatch,
):
    """Real public APIs compose when Registry and strategy identities differ."""

    from agora.strategy_workshop import MemoryWorkshopStore
    from services.governance import main as governance_main
    from services.registry.service import app as registry_app
    from services.registry.storage import reset_store
    from services.research.strategy_spec.patching import compute_document_sha256

    ApprovalDecisionStore = governance_main.ApprovalDecisionStore
    reset_store()
    governance_path = tmp_path / "approval_decisions.json"
    monkeypatch.setattr(
        governance_main,
        "store",
        ApprovalDecisionStore(str(governance_path)),
    )
    registry_client = TestClient(registry_app)
    governance_client = TestClient(governance_main.app)

    strategy_id = "strategy-public-workshop-contract"
    registry_id = "registry-public-workshop-contract"
    strategy_spec = _public_strategy_spec(strategy_id)
    registered = registry_client.post(
        "/api/registry/strategy-specs",
        json={
            "registry_id": registry_id,
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "source_seed_id": "seed-public-workshop-contract",
            "metadata": {
                "tenant_id": _PUBLIC_TENANT_ID,
                "owner_user_id": _PUBLIC_USER_ID,
            },
            "strategy_spec": strategy_spec,
        },
    )
    assert registered.status_code == 200, registered.text
    assert registered.json()["entry"]["registry_id"] == registry_id
    assert registered.json()["entry"]["strategy_id"] == strategy_id
    assert registry_id != strategy_id

    workshop_store = MemoryWorkshopStore()
    canonical = _PublicApiCanonicalOperations(
        registry_client,
        governance_client,
    )
    workshop_client = _public_workshop_client(workshop_store, canonical)
    created = workshop_client.post(
        "/bff/agora/workshops",
        headers={
            "Authorization": "Bearer workshop-contract",
            "X-Tenant-Id": _PUBLIC_TENANT_ID,
            "Idempotency-Key": "create-public-workshop-contract",
        },
        json={
            "initial_message": "Validate this Registry-owned strategy.",
            "strategy_spec_ref": registry_id,
        },
    )
    assert created.status_code == 201, created.text
    workshop = created.json()["data"]
    workshop_id = workshop["workshop_id"]
    assert workshop["strategy_id"] == strategy_id
    assert workshop["active_strategy_spec_registry_id"] == registry_id

    listed = workshop_client.get(
        f"/bff/agora/workshops/{workshop_id}/versions",
        headers={
            "Authorization": "Bearer workshop-contract",
            "X-Tenant-Id": _PUBLIC_TENANT_ID,
        },
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["data"]["versions"]) == 1

    version_created = workshop_client.post(
        f"/bff/agora/workshops/{workshop_id}/versions",
        headers=_public_command_headers(
            "create-version-public-workshop-contract",
            listed.headers["etag"],
        ),
        json={
            "patch": [
                {
                    "op": "replace",
                    "path": "/title",
                    "value": "Selected public API workshop candidate",
                }
            ],
            "base_document_sha256": compute_document_sha256(strategy_spec),
            "reason": "Exercise distinct Registry and strategy identities",
        },
    )
    assert version_created.status_code == 201, version_created.text
    version = version_created.json()["data"]["resource"]["version"]
    version_id = version["workshop_version_id"]
    assert version["strategy_id"] == strategy_id
    assert version["strategy_spec_registry_id"] != strategy_id

    selected = workshop_client.post(
        f"/bff/agora/workshops/{workshop_id}/versions/{version_id}/select",
        headers=_public_command_headers(
            "select-version-public-workshop-contract",
            version_created.headers["etag"],
        ),
    )
    assert selected.status_code == 200, selected.text

    consulted = workshop_client.post(
        f"/bff/agora/workshops/{workshop_id}/consultations",
        headers=_public_command_headers(
            "consult-public-workshop-contract",
            selected.headers["etag"],
        ),
        json={
            "consultation_type": "committee",
            "subject": "Review the bounded research candidate",
            "context_refs": [f"registry:{registry_id}"],
        },
    )
    assert consulted.status_code == 201, consulted.text

    approval_id = "approval-public-workshop-contract"
    proposed = governance_client.post(
        "/api/governance/approvals",
        json={
            "decision_id": approval_id,
            "target_type": "strategy_workshop",
            "target_id": workshop_id,
            "target_version": version_id,
            "risk_level": "low",
            "tenant_id": _PUBLIC_TENANT_ID,
            "owner_user_id": _PUBLIC_USER_ID,
        },
    )
    assert proposed.status_code == 201, proposed.text
    reviewed = governance_client.post(
        f"/api/governance/approvals/{approval_id}/review",
        json={
            "actor_role": "governance_reviewer",
            "actor_id": _PUBLIC_APPROVER_ID,
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    decided = governance_client.post(
        f"/api/governance/approvals/{approval_id}/decide",
        json={
            "actor_role": "governance_reviewer",
            "actor_id": _PUBLIC_APPROVER_ID,
            "outcome": "approved",
            "rationale": "Approve research-only Workshop operations.",
        },
    )
    assert decided.status_code == 200, decided.text

    # Simulate Governance and BFF process restarts. Governance reconstructs
    # its owner store from disk; Workshop reconstructs its router over the
    # durable aggregate store and canonical public API clients.
    monkeypatch.setattr(
        governance_main,
        "store",
        ApprovalDecisionStore(str(governance_path)),
    )
    restarted_governance_client = TestClient(governance_main.app)
    restarted_approval = restarted_governance_client.get(
        f"/api/governance/approvals/{approval_id}"
    )
    assert restarted_approval.status_code == 200, restarted_approval.text
    assert restarted_approval.json()["target_type"] == "strategy_workshop"
    assert restarted_approval.json()["target_id"] == workshop_id
    assert restarted_approval.json()["target_version"] == version_id

    restarted_canonical = _PublicApiCanonicalOperations(
        registry_client,
        restarted_governance_client,
    )
    restarted_workshop_client = _public_workshop_client(
        workshop_store,
        restarted_canonical,
    )
    restarted_readback = restarted_workshop_client.get(
        f"/bff/agora/workshops/{workshop_id}",
        headers={
            "Authorization": "Bearer workshop-contract",
            "X-Tenant-Id": _PUBLIC_TENANT_ID,
        },
    )
    assert restarted_readback.status_code == 200, restarted_readback.text
    assert restarted_readback.json()["data"]["strategy_id"] == strategy_id
    assert (
        restarted_readback.json()["data"]["active_strategy_spec_registry_id"]
        == version["strategy_spec_registry_id"]
    )

    research = restarted_workshop_client.post(
        f"/bff/agora/workshops/{workshop_id}/research-runs",
        headers=_public_command_headers(
            "research-public-workshop-contract",
            restarted_readback.headers["etag"],
        ),
        json={
            "research_context": "Validate without live execution.",
            "strategy_version_ref": version_id,
            "parameters": {"environment": "research"},
            "approval_decision_id": approval_id,
            "adapter": "handoff_only",
            "requested_mode": "handoff_only",
            "dispatch_mode": "handoff_only",
        },
    )
    assert research.status_code == 202, research.text

    concluded = restarted_workshop_client.post(
        f"/bff/agora/workshops/{workshop_id}/conclude",
        headers=_public_command_headers(
            "conclude-public-workshop-contract",
            research.headers["etag"],
        ),
        json={
            "final_version_id": version_id,
            "conclusion_notes": "Approved as research-only.",
            "approval_decision_id": approval_id,
        },
    )
    assert concluded.status_code == 200, concluded.text
    resource = concluded.json()["data"]["resource"]
    assert resource["workshop"]["status"] == "concluded"
    assert resource["workshop"]["strategy_id"] == strategy_id
    assert resource["two_person_proof"]["approved_by"] == _PUBLIC_APPROVER_ID
    assert resource["two_person_proof"]["distinct_actors"] is True

    final_restart_client = _public_workshop_client(
        workshop_store,
        restarted_canonical,
    )
    final_readback = final_restart_client.get(
        f"/bff/agora/workshops/{workshop_id}",
        headers={
            "Authorization": "Bearer workshop-contract",
            "X-Tenant-Id": _PUBLIC_TENANT_ID,
        },
    )
    assert final_readback.status_code == 200, final_readback.text
    assert final_readback.json()["data"]["status"] == "concluded"
    assert final_readback.json()["data"]["final_workshop_version_id"] == version_id
