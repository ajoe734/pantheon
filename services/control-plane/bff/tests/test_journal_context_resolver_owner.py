"""BFF-JOURNAL-CONTEXT-SEAM-CORRECTIVE-001: single-owner context resolver.

Proves ``resolve_agora_interaction_context_ref`` behaves identically to the
pre-migration inline main.py resolver for every kind it owns (decision_event,
journal_entry via trade episode, journal_entry via legacy DecisionJournal),
fails closed for the frontend-only kinds with no scoped owner yet, and reads
journal visibility through its own canonical filter -- not a second,
less-scoped private-record filter duplicated elsewhere.

Canonical interface per
``docs/operations/bff-test-migration-b05-journal-context-resolver-seam.md``
§ 4.1 (BFF-TEST-MIGRATION-B05-JOURNAL-CONTEXT-RESOLVER-SEAM-DECISION-001).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from services.control_plane.bff.agora.interaction.context_resolver import (
    filter_agora_private_records,
    is_agora_private_record_visible,
    resolve_agora_interaction_context_ref,
)
from services.control_plane.bff.models import OperatorIdentity


def _bff_error(status, code, message, reason=None, *, precondition_failed=None, **kwargs) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={
            "error": {
                "code": str(code),
                "message": message,
                "reason": reason,
                "details": {"precondition_failed": precondition_failed},
            }
        },
    )


class _Resolved:
    def __init__(self, tenant_id: str = "tenant-a", user_id: str = "alice") -> None:
        self.tenant_id = tenant_id
        self.user_id = user_id


class _FakeReadStore:
    def __init__(self, journal_entries: Optional[List[Dict[str, Any]]] = None) -> None:
        self._journal_entries = journal_entries or []
        self.list_calls: List[Dict[str, Any]] = []

    def list_decision_journal_entries(self, **kwargs: Any) -> List[Dict[str, Any]]:
        self.list_calls.append(kwargs)
        return list(self._journal_entries)


class _FakeTradingRoomStore:
    def __init__(self, events: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self._events = events or {}

    def get_decision_event(self, ref_id: str) -> Optional[Dict[str, Any]]:
        return self._events.get(ref_id)


def _identity(tenant: str = "tenant-a", user: str = "alice", roles=("operator",)) -> OperatorIdentity:
    return OperatorIdentity(
        operator_id=user,
        roles=list(roles),
        mfa_verified=True,
        claims={"tid": tenant, "sub": user},
    )


def _resolve(*, identity=None, read_store=None, trading_room_store=None,
             persona_directory_snapshot_fn=None, persona_record_tenant_id_fn=None,
             **kwargs):
    identity = identity or _identity()
    trading_room_store = trading_room_store or _FakeTradingRoomStore()
    return resolve_agora_interaction_context_ref(
        extract_identity=lambda authorization: identity,
        require_read_role=lambda ident: None,
        bff_error=_bff_error,
        read_store=read_store,
        trading_room_store_fn=lambda: trading_room_store,
        persona_directory_snapshot_fn=persona_directory_snapshot_fn,
        persona_record_tenant_id_fn=persona_record_tenant_id_fn,
        **kwargs,
    )


@pytest.mark.parametrize("kind", ["position", "performance_window", "human_inbox_item"])
def test_frontend_only_kinds_fail_closed_unavailable(kind):
    with pytest.raises(HTTPException) as ctx:
        _resolve(
            kind=kind, ref_id="anything", ref_version=None, resolved=_Resolved(),
            session={}, context_refs=[], authorization="Bearer t",
            source_route="/x", focused_object={"kind": "other", "id": "o"},
        )
    assert ctx.value.status_code == 503
    assert ctx.value.detail["error"]["details"]["precondition_failed"] == f"{kind}_scope_unavailable"


def test_unknown_kind_fails_closed_unavailable():
    with pytest.raises(HTTPException) as ctx:
        _resolve(
            kind="mystery", ref_id="x", ref_version=None, resolved=_Resolved(),
            session={}, context_refs=[], authorization="Bearer t",
            source_route="/x", focused_object={"kind": "other", "id": "o"},
        )
    assert ctx.value.status_code == 503
    assert ctx.value.detail["error"]["details"]["precondition_failed"] == "mystery_store_unavailable"


def test_decision_event_focused_on_itself_is_unavailable():
    with pytest.raises(HTTPException) as ctx:
        _resolve(
            trading_room_store=_FakeTradingRoomStore({"dec-1": {"strategy_id": "s1", "tenant_id": "tenant-a"}}),
            kind="decision_event", ref_id="dec-1", ref_version=None, resolved=_Resolved(),
            session={}, context_refs=[], authorization="Bearer t", source_route="/x",
            focused_object={"kind": "decision_event", "id": "dec-1"},
        )
    assert ctx.value.status_code == 503
    assert ctx.value.detail["error"]["details"]["precondition_failed"] == "decision_event_source_route_unavailable"


def test_decision_event_missing_returns_unverified_none_row():
    result = _resolve(
        trading_room_store=_FakeTradingRoomStore({}),
        kind="decision_event", ref_id="missing", ref_version=None, resolved=_Resolved(),
        session={}, context_refs=[], authorization="Bearer t", source_route="/x",
        focused_object={"kind": "other", "id": "o"},
    )
    assert result == {"row": None, "audience_verified": False}


def test_decision_event_strategy_and_version_match_verifies_audience():
    store = _FakeTradingRoomStore({"dec-1": {"strategy_id": "strategy-1", "strategy_spec_registry_id": "v1"}})
    result = _resolve(
        trading_room_store=store,
        kind="decision_event", ref_id="dec-1", ref_version=None, resolved=_Resolved(),
        session={"strategy_id": "strategy-1", "active_strategy_spec_registry_id": "v1"},
        context_refs=[], authorization="Bearer t", source_route="/x",
        focused_object={"kind": "other", "id": "o"},
    )
    assert result["row"]["strategy_id"] == "strategy-1"
    assert result["audience_verified"] is True


def test_decision_event_strategy_mismatch_is_not_audience_verified():
    store = _FakeTradingRoomStore({"dec-1": {"strategy_id": "strategy-other", "strategy_spec_registry_id": "v1"}})
    result = _resolve(
        trading_room_store=store,
        kind="decision_event", ref_id="dec-1", ref_version=None, resolved=_Resolved(),
        session={"strategy_id": "strategy-1", "active_strategy_spec_registry_id": "v1"},
        context_refs=[], authorization="Bearer t", source_route="/x",
        focused_object={"kind": "other", "id": "o"},
    )
    assert result["audience_verified"] is False


def test_journal_entry_legacy_decision_journal_is_never_audience_verified():
    """A legacy DecisionJournal row is returned for not-found/error parity, but
    intentionally never elevated to an audience-verified receipt."""
    store = _FakeReadStore(journal_entries=[
        {"id": "entry-1", "tenant_id": "tenant-a", "owner_user_id": "alice"},
    ])
    result = _resolve(
        read_store=store,
        kind="journal_entry", ref_id="entry-1", ref_version=None, resolved=_Resolved(),
        session={"workshop_id": "ws-1"}, context_refs=[], authorization="Bearer t",
        source_route="/agora/workshop", focused_object={"kind": "other", "id": "o"},
    )
    assert result["row"]["id"] == "entry-1"
    assert result["audience_verified"] is False


def test_journal_entry_legacy_lookup_reads_through_resolved_scope():
    store = _FakeReadStore(journal_entries=[])
    _resolve(
        read_store=store, identity=_identity(tenant="tenant-a", user="alice"),
        kind="journal_entry", ref_id="missing", ref_version=None,
        resolved=_Resolved(tenant_id="tenant-a", user_id="alice"),
        session={"workshop_id": "ws-1"}, context_refs=[], authorization="Bearer t",
        source_route="/agora/workshop", focused_object={"kind": "other", "id": "o"},
    )
    assert len(store.list_calls) == 1
    assert store.list_calls[0] == {"tenant_id": "tenant-a", "user_id": "alice"}


def test_journal_entry_legacy_lookup_falls_back_to_unscoped_read_on_type_error():
    """A read store that does not accept tenant/user kwargs (a narrow test
    double) is retried unscoped rather than crashing the resolution."""
    class _NarrowReadStore:
        def list_decision_journal_entries(self):
            return [{"id": "entry-1", "tenant_id": "tenant-a", "owner_user_id": "alice"}]

    result = _resolve(
        read_store=_NarrowReadStore(),
        kind="journal_entry", ref_id="entry-1", ref_version=None, resolved=_Resolved(),
        session={"workshop_id": "ws-1"}, context_refs=[], authorization="Bearer t",
        source_route="/agora/workshop", focused_object={"kind": "other", "id": "o"},
    )
    assert result["row"]["id"] == "entry-1"
    assert result["audience_verified"] is False


def test_journal_entry_legacy_lookup_excludes_other_tenant_rows():
    store = _FakeReadStore(journal_entries=[
        {"id": "entry-1", "tenant_id": "tenant-b", "owner_user_id": "alice"},
    ])
    result = _resolve(
        read_store=store,
        kind="journal_entry", ref_id="entry-1", ref_version=None, resolved=_Resolved(),
        session={"workshop_id": "ws-1"}, context_refs=[], authorization="Bearer t",
        source_route="/agora/workshop", focused_object={"kind": "other", "id": "o"},
    )
    assert result["row"] is None
    assert result["audience_verified"] is False


def test_journal_entry_missing_read_store_is_none_row_not_a_crash():
    result = _resolve(
        read_store=None,
        kind="journal_entry", ref_id="entry-1", ref_version=None, resolved=_Resolved(),
        session={"workshop_id": "ws-1"}, context_refs=[], authorization="Bearer t",
        source_route="/agora/workshop", focused_object={"kind": "other", "id": "o"},
    )
    assert result == {"row": None, "audience_verified": False}


def _trade_episode(**overrides):
    return {
        "trade_episode_id": "ep-1",
        "environment": "paper",
        "persona_id": "ready",
        "strategy_id": "strategy-1",
        "artifact_id": "artifact-7",
        "artifact_version": "artifact-build-42",
        "runtime_binding_id": "22222222-2222-4222-8222-222222222222",
        "capital_pool_id": "pool-7",
        "instrument_id": "SPY",
        "side": "long",
        "status": "reflected",
        "coverage": {
            "state": "complete",
            "missing_refs": [],
            "as_of": "2026-07-17T00:00:00Z",
            "source_system": "lean-telemetry",
        },
        **overrides,
    }


def test_journal_entry_trade_episode_canonical_route_verifies_audience(tmp_path):
    episode_path = tmp_path / "trade-episodes.json"
    episode_path.write_text(json.dumps([_trade_episode()]))
    with patch.dict(os.environ, {"PANTHEON_BFF_TRADE_EPISODES_STORE": str(episode_path)}):
        directory_lookup_calls = []

        class _Snapshot:
            records_by_id = {"ready": {"tenant_id": "tenant-a"}}

        def _snapshot(tenant_id):
            directory_lookup_calls.append(tenant_id)
            return _Snapshot()

        result = _resolve(
            persona_directory_snapshot_fn=_snapshot,
            persona_record_tenant_id_fn=lambda record: record.get("tenant_id"),
            kind="journal_entry", ref_id="ep-1", ref_version=None,
            resolved=_Resolved(tenant_id="tenant-a"),
            session={"strategy_id": "strategy-1", "active_strategy_spec_registry_id": "v1"},
            context_refs=[{"kind": "persona", "id": "ready"}],
            authorization="Bearer t",
            source_route="/management/personas/ready?tab=tradeJournal",
            focused_object={"kind": "journal_entry", "id": "ep-1"},
        )
        assert result["row"]["trade_episode_id"] == "ep-1"
        assert result["audience_verified"] is True
        assert directory_lookup_calls == ["tenant-a"]


def test_journal_entry_trade_episode_wrong_route_is_not_audience_verified(tmp_path):
    episode_path = tmp_path / "trade-episodes.json"
    episode_path.write_text(json.dumps([_trade_episode()]))
    with patch.dict(os.environ, {"PANTHEON_BFF_TRADE_EPISODES_STORE": str(episode_path)}):
        class _Snapshot:
            records_by_id = {"ready": {"tenant_id": "tenant-a"}}

        result = _resolve(
            persona_directory_snapshot_fn=lambda tenant_id: _Snapshot(),
            persona_record_tenant_id_fn=lambda record: record.get("tenant_id"),
            kind="journal_entry", ref_id="ep-1", ref_version=None,
            resolved=_Resolved(tenant_id="tenant-a"),
            session={"strategy_id": "strategy-1", "active_strategy_spec_registry_id": "v1"},
            context_refs=[{"kind": "persona", "id": "ready"}],
            authorization="Bearer t",
            source_route="/management/personas/ready?tab=overview",
            focused_object={"kind": "journal_entry", "id": "ep-1"},
        )
        assert result["row"]["trade_episode_id"] == "ep-1"
        assert result["audience_verified"] is False


def test_journal_entry_trade_episode_missing_persona_snapshot_dependency_stays_unverified(tmp_path):
    """Without an injected persona directory dependency the trade-episode
    branch cannot prove audience and must not silently default to verified."""
    episode_path = tmp_path / "trade-episodes.json"
    episode_path.write_text(json.dumps([_trade_episode()]))
    with patch.dict(os.environ, {"PANTHEON_BFF_TRADE_EPISODES_STORE": str(episode_path)}):
        result = _resolve(
            kind="journal_entry", ref_id="ep-1", ref_version=None,
            resolved=_Resolved(tenant_id="tenant-a"),
            session={"strategy_id": "strategy-1", "active_strategy_spec_registry_id": "v1"},
            context_refs=[{"kind": "persona", "id": "ready"}],
            authorization="Bearer t",
            source_route="/management/personas/ready?tab=tradeJournal",
            focused_object={"kind": "journal_entry", "id": "ep-1"},
        )
        assert result["row"]["trade_episode_id"] == "ep-1"
        assert result["audience_verified"] is False


def test_journal_entry_trade_episode_schema_invalid_is_not_audience_verified(tmp_path):
    episode = _trade_episode(coverage=None)
    episode_path = tmp_path / "trade-episodes.json"
    episode_path.write_text(json.dumps([episode]))
    with patch.dict(os.environ, {"PANTHEON_BFF_TRADE_EPISODES_STORE": str(episode_path)}):
        class _Snapshot:
            records_by_id = {"ready": {"tenant_id": "tenant-a"}}

        result = _resolve(
            persona_directory_snapshot_fn=lambda tenant_id: _Snapshot(),
            persona_record_tenant_id_fn=lambda record: record.get("tenant_id"),
            kind="journal_entry", ref_id="ep-1", ref_version=None,
            resolved=_Resolved(tenant_id="tenant-a"),
            session={"strategy_id": "strategy-1", "active_strategy_spec_registry_id": "v1"},
            context_refs=[{"kind": "persona", "id": "ready"}],
            authorization="Bearer t",
            source_route="/management/personas/ready?tab=tradeJournal",
            focused_object={"kind": "journal_entry", "id": "ep-1"},
        )
        assert result["audience_verified"] is False


def test_filter_agora_private_records_isolates_tenant_and_owner():
    identity = _identity(tenant="tenant-a", user="alice")
    records = [
        {"id": "e1", "tenant_id": "tenant-a", "user_id": "alice", "visibility": "private"},
        {"id": "e2", "tenant_id": "tenant-b", "user_id": "alice", "visibility": "private"},
        {"id": "e3", "tenant_id": "tenant-a", "user_id": "bob", "visibility": "private"},
        {"id": "e4", "tenant_id": "tenant-a", "user_id": "bob", "visibility": "public"},
        "invalid-non-dict",
        None,
    ]
    filtered = filter_agora_private_records(records, identity, tenant_id="tenant-a", user_id="alice")
    assert [r["id"] for r in filtered] == ["e1", "e4"]


def test_is_agora_private_record_visible_rejects_non_dict():
    identity = _identity()
    assert is_agora_private_record_visible("not-a-dict", identity, tenant_id="tenant-a", user_id="alice") is False
