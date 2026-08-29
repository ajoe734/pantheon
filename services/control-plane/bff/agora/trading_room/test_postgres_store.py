"""Focused selection and restart-persistence tests for the trading-room store."""
from __future__ import annotations

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from bff.agora.trading_room import store as store_module


def test_factory_defaults_to_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(store_module.BACKEND_ENV, raising=False)
    assert type(store_module.make_trading_room_store()) is store_module.TradingRoomStore


def test_factory_selects_postgres_without_logging_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class FakePostgresStore(store_module.TradingRoomStore):
        def __init__(self, *, dsn: str, schema: str) -> None:
            super().__init__()
            captured.update(dsn=dsn, schema=schema)

    monkeypatch.setattr(store_module, "PostgresTradingRoomStore", FakePostgresStore)
    result = store_module.make_trading_room_store(
        backend="postgres", dsn="postgresql://secret@example/pantheon", schema="agora_test"
    )
    assert isinstance(result, FakePostgresStore)
    assert captured == {
        "dsn": "postgresql://secret@example/pantheon",
        "schema": "agora_test",
    }


def test_postgres_store_survives_new_instance_and_preserves_proof() -> None:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    schema = f"agora_tr_{uuid.uuid4().hex[:12]}"
    first = store_module.PostgresTradingRoomStore(dsn=dsn, schema=schema)
    event = {
        "decision_event_id": f"evt-{uuid.uuid4().hex}",
        "event_kind": "strategy_signal",
        "state": "pending",
        "triggered_at": "2026-07-12T00:00:00Z",
        "no_order_route_proof": "agora_decision_support_only",
    }
    first.upsert_decision_event(event)

    restarted = store_module.PostgresTradingRoomStore(dsn=dsn, schema=schema)
    assert restarted.get_decision_event(event["decision_event_id"]) == event
    with pytest.raises(ValueError, match="no_order_route_proof"):
        restarted.upsert_decision_event({
            **event,
            "decision_event_id": f"evt-{uuid.uuid4().hex}",
            "no_order_route_proof": "unsafe",
        })


def test_trading_room_store_fresh_reader_invariants() -> None:
    store = store_module.TradingRoomStore()
    event_id = f"evt-{uuid.uuid4().hex}"
    intent_id = f"intent-{uuid.uuid4().hex}"
    handoff_id = f"ho-{uuid.uuid4().hex}"
    ws_id = f"ws-{uuid.uuid4().hex}"
    prop_id = f"prop-{uuid.uuid4().hex}"
    widget_prop_id = f"wprop-{uuid.uuid4().hex}"

    # 1. Decision event
    event = {
        "decision_event_id": event_id,
        "strategy_id": "strat-1",
        "event_kind": "strategy_signal",
        "state": "pending",
        "triggered_at": "2026-07-12T00:00:00Z",
        "no_order_route_proof": "agora_decision_support_only",
    }
    store.upsert_decision_event(event)
    assert store.get_decision_event(event_id) == event
    listed_events = store.list_decision_events(strategy_id="strat-1")
    assert any(e["decision_event_id"] == event_id for e in listed_events["items"])

    # 2. Trader decision
    store.record_trader_decision(event_id, {"decision": "approve", "reason": "good signal"})
    updated_event = store.get_decision_event(event_id)
    assert updated_event is not None
    assert updated_event["state"] == "decided"
    assert updated_event["decision_state"] == "approved_by_trader"

    # 3. Intent & Handoff
    intent = {
        "intent_id": intent_id,
        "strategy_id": "strat-1",
        "no_order_route_proof": "agora_intent_record_only",
    }
    store.upsert_intent(intent, state="draft")
    assert store.get_intent(intent_id) == intent
    assert store.get_intent_state(intent_id) == "draft"
    store.set_intent_state(intent_id, "active")
    assert store.get_intent_state(intent_id) == "active"

    handoff = {
        "handoff_id": handoff_id,
        "intent_id": intent_id,
        "state": "submitted",
        "no_order_route_proof": "agora_request_only_no_order_route",
    }
    store.upsert_handoff(handoff)
    assert store.get_handoff(handoff_id) == handoff
    assert store.list_handoffs_for_intent(intent_id) == [handoff]
    assert store.get_intent_state(intent_id) == "submitted"

    withdrawn = store.withdraw_intent(intent_id, withdrawn_at="2026-07-12T01:00:00Z")
    assert withdrawn is not None
    assert withdrawn["state"] == "withdrawn"
    assert store.get_handoff(handoff_id)["state"] == "withdrawn"

    # 4. Workspace proposals & Workspaces
    ws_proposal = {
        "proposalId": prop_id,
        "title": "Workspace Proposal 1",
    }
    store.upsert_workspace_proposal(
        ws_proposal,
        tenant_id="tenant-1",
        user_id="user-1",
        generation_meta={"model": "test"},
    )
    assert store.get_workspace_proposal_record(prop_id)["proposal"] == ws_proposal
    assert store.get_workspace_proposal_generation_meta(prop_id) == {"model": "test"}

    workspace = {
        "id": ws_id,
        "strategyId": "strat-1",
        "strategyVersion": "v1",
        "dashboardVersion": 1,
        "userId": "user-1",
        "views": [{"id": "v1", "widgets": ["w1"]}],
        "createdAt": "2026-07-12T00:00:00Z",
        "updatedAt": "2026-07-12T00:00:00Z",
    }
    store.upsert_workspace(workspace, tenant_id="tenant-1", user_id="user-1")
    assert store.get_workspace_record(ws_id)["workspace"] == workspace
    assert store.get_workspace_for_strategy(
        "strat-1", tenant_id="tenant-1", user_id="user-1"
    ) == workspace
    assert len(store.list_workspaces(tenant_id="tenant-1", user_id="user-1")) == 1

    # 5. Widget revision proposal
    w_proposal = {"id": widget_prop_id, "title": "Widget Prop"}
    store.upsert_widget_revision_proposal(w_proposal, tenant_id="tenant-1", user_id="user-1")
    assert store.get_widget_revision_proposal_record(widget_prop_id)["proposal"] == w_proposal

    # 6. Workspace versions
    v_rec = store.record_workspace_version(
        workspace,
        tenant_id="tenant-1",
        user_id="user-1",
        created_at="2026-07-12T00:00:00Z",
        change_summary="Initial version",
    )
    assert v_rec["dashboardVersion"] == 1
    assert len(store.list_workspace_version_records(ws_id, tenant_id="tenant-1", user_id="user-1")) == 1
    assert store.get_workspace_version_record(
        ws_id, v_rec["id"], tenant_id="tenant-1", user_id="user-1"
    ) == v_rec

    # 7. Idempotency keys
    assert store.check_and_record_idempotency_key("agora", "key-1") is False
    assert store.check_and_record_idempotency_key("agora", "key-1") is True


def test_postgres_store_all_entities_survive_restart() -> None:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    schema = f"agora_tr_all_{uuid.uuid4().hex[:12]}"
    first = store_module.PostgresTradingRoomStore(dsn=dsn, schema=schema)

    event_id = f"evt-{uuid.uuid4().hex}"
    intent_id = f"intent-{uuid.uuid4().hex}"
    handoff_id = f"ho-{uuid.uuid4().hex}"
    ws_id = f"ws-{uuid.uuid4().hex}"
    prop_id = f"prop-{uuid.uuid4().hex}"
    widget_prop_id = f"wprop-{uuid.uuid4().hex}"

    event = {
        "decision_event_id": event_id,
        "strategy_id": "strat-1",
        "event_kind": "strategy_signal",
        "state": "pending",
        "triggered_at": "2026-07-12T00:00:00Z",
        "no_order_route_proof": "agora_decision_support_only",
    }
    first.upsert_decision_event(event)
    first.record_trader_decision(event_id, {"decision": "approve", "reason": "good signal"})

    intent = {
        "intent_id": intent_id,
        "strategy_id": "strat-1",
        "no_order_route_proof": "agora_intent_record_only",
    }
    first.upsert_intent(intent, state="draft")

    handoff = {
        "handoff_id": handoff_id,
        "intent_id": intent_id,
        "state": "submitted",
        "no_order_route_proof": "agora_request_only_no_order_route",
    }
    first.upsert_handoff(handoff)

    ws_proposal = {"proposalId": prop_id, "title": "Workspace Proposal 1"}
    first.upsert_workspace_proposal(
        ws_proposal,
        tenant_id="tenant-1",
        user_id="user-1",
        generation_meta={"model": "test"},
    )

    workspace = {
        "id": ws_id,
        "strategyId": "strat-1",
        "strategyVersion": "v1",
        "dashboardVersion": 1,
        "userId": "user-1",
        "views": [{"id": "v1", "widgets": ["w1"]}],
        "createdAt": "2026-07-12T00:00:00Z",
        "updatedAt": "2026-07-12T00:00:00Z",
    }
    first.upsert_workspace(workspace, tenant_id="tenant-1", user_id="user-1")

    w_proposal = {"id": widget_prop_id, "title": "Widget Prop"}
    first.upsert_widget_revision_proposal(w_proposal, tenant_id="tenant-1", user_id="user-1")

    v_rec = first.record_workspace_version(
        workspace,
        tenant_id="tenant-1",
        user_id="user-1",
        created_at="2026-07-12T00:00:00Z",
        change_summary="Initial version",
    )

    assert first.check_and_record_idempotency_key("agora", "key-pg") is False

    # Restart store with fresh instance
    restarted = store_module.PostgresTradingRoomStore(dsn=dsn, schema=schema)
    res_event = restarted.get_decision_event(event_id)
    assert res_event is not None
    assert res_event["state"] == "decided"
    assert res_event["decision_state"] == "approved_by_trader"

    assert restarted.get_intent(intent_id) == intent
    assert restarted.get_handoff(handoff_id) == handoff
    assert restarted.get_workspace_proposal_record(prop_id)["proposal"] == ws_proposal
    assert restarted.get_workspace_record(ws_id)["workspace"] == workspace
    assert restarted.get_widget_revision_proposal_record(widget_prop_id)["proposal"] == w_proposal
    assert restarted.get_workspace_version_record(
        ws_id, v_rec["id"], tenant_id="tenant-1", user_id="user-1"
    ) == v_rec
    assert restarted.check_and_record_idempotency_key("agora", "key-pg") is True


def test_no_read_store_import_in_agora_stores() -> None:
    import ast
    agora_dir = os.path.dirname(os.path.dirname(__file__))
    store_files = []
    for root, _, files in os.walk(agora_dir):
        for f in files:
            if f == "store.py":
                store_files.append(os.path.join(root, f))

    assert len(store_files) >= 4, f"Found only {len(store_files)} store.py files"
    for store_path in store_files:
        with open(store_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=store_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "read_store" not in alias.name, f"{store_path} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "read_store" not in module, f"{store_path} imports from {module}"
                for alias in node.names:
                    assert alias.name != "ReadSurfaceStore", f"{store_path} imports ReadSurfaceStore"


def test_source_ingestion_reconcile_only_for_agora() -> None:
    import ast
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
    si_dir = os.path.join(repo_root, "services", "source_ingestion")
    if not os.path.exists(si_dir):
        pytest.skip("services/source_ingestion does not exist")

    # Source ingestion must not import or write to Agora stores
    for root, _, files in os.walk(si_dir):
        for f in files:
            if f.endswith(".py"):
                file_path = os.path.join(root, f)
                with open(file_path, "r", encoding="utf-8") as sf:
                    content = sf.read()
                assert "CandidateDecisionStore" not in content, f"{file_path} imports CandidateDecisionStore"
                assert "PostgresTradingRoomStore" not in content, f"{file_path} imports PostgresTradingRoomStore"
