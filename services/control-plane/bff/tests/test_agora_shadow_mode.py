"""Shadow mode (design A4): immutable decision locks through the production
Trading Room decision route, writing into the real Agora owner (AgoraWriteService
+ AgoraStore). Only the Postgres JSON table underneath AgoraStore is replaced by
an in-memory table (no Postgres in CI); its insert_if_absent semantics match.
"""
from __future__ import annotations

import uuid
from unittest import mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.agora.service import AgoraWriteService
from services.agora.shadow_mode import (
    ACTUAL_FILL_KINDS,
    DISPLAY_LABELS,
    verify_checksum,
)
from services.agora.store import AgoraStore
from services.agora.write_authority import AgoraWriteForbiddenError
from services.control_plane.bff.agora.trading_room.router import create_trading_room_router
from services.control_plane.bff.agora.trading_room.store import make_trading_room_store


class _MemoryTable:
    def __init__(self, **_kw) -> None:
        self.rows: dict = {}

    def put(self, record_id, payload):
        self.rows[record_id] = dict(payload)

    def insert_if_absent(self, record_id, payload, **_kw):
        if record_id in self.rows:
            return False, dict(self.rows[record_id])
        self.rows[record_id] = dict(payload)
        return True, dict(payload)

    def get(self, record_id):
        row = self.rows.get(record_id)
        return dict(row) if row is not None else None

    def list_all(self, **_kw):
        return [dict(r) for r in self.rows.values()]


def _owner() -> AgoraWriteService:
    from services.control_plane.bff.agora.dataset_extraction.extractor import AgoraDatasetStore

    with mock.patch("services.agora.store.PostgresJsonOwnerStore", _MemoryTable):
        return AgoraWriteService(
            store=AgoraStore(dsn="postgresql://test/none", bootstrap=False),
            dataset_store=AgoraDatasetStore(backend="memory"),
        )


def _event(event_id: str, **extra) -> dict:
    base = {
        "spec_version": "1.0",
        "decision_event_id": event_id,
        "tenant_id": "tenant-001",
        "user_id": "user-001",
        "event_kind": "entry",
        "origin": "strategy_signal",
        "strategy_id": "strat-001",
        "strategy_spec_registry_id": "reg-001",
        "subject": {"symbol": "2330.TW"},
        "state": "pending_review",
        "triggered_at": "2026-11-16T01:00:00Z",
        "confidence": {"value": 0.7, "basis": "model", "calibration_state": "calibrated", "sample_size": 100},
        "probability": {"target_outcome": "up", "horizon": "5d", "value": 0.6, "ci_lower": 0.5, "ci_upper": 0.7},
        "expected_value": {"horizon": "5d", "unit": "pct_return", "gross": 0.03, "cost": 0.001, "net": 0.029, "downside": -0.02},
        "rationale": [{"claim": "signal", "confidence": 0.7, "evidence_refs": []}],
        "risk_notes": [],
        "evidence_refs": [],
        "invalidation": {"conditions": ["x"], "current_state": "valid", "last_checked_at": "2026-11-16T00:55:00Z"},
        "suggested_action": "enter",
        "no_order_route_proof": "agora_decision_support_only",
    }
    base.update(extra)
    return base


def _hdr(key: str) -> dict:
    return {"Authorization": "Bearer t", "If-Match": "*", "Idempotency-Key": key, "X-Request-Id": f"req-{key}"}


def _app(store, owner, identity: dict) -> TestClient:
    def bff_error(status_code, code, message, reason, **kw):
        return HTTPException(
            status_code=status_code,
            detail={"code": getattr(code, "value", code), "message": message, "reason": reason, **kw},
        )

    def extract_identity(auth, session_cookie=None):
        return dict(identity)

    app = FastAPI()
    app.include_router(
        create_trading_room_router(
            extract_identity=extract_identity,
            require_read_role=lambda _i: None,
            require_write_role=lambda _i: None,
            bff_error=bff_error,
            utc_now=lambda: "2026-11-16T02:00:00Z",
            trading_room_store=store,
            decision_lock_owner=owner,
        )
    )
    return TestClient(app)


TRADER = {"user_id": "user-001", "tenant_id": "tenant-001", "roles": ["trader"]}
OPERATOR = {"user_id": "op-9", "tenant_id": "tenant-001", "roles": ["operator"]}
OTHER_TRADER = {"user_id": "user-002", "tenant_id": "tenant-001", "roles": ["trader"]}
URL = "/bff/agora/trading-room/decision-events/{}"


@pytest.fixture
def world():
    store = make_trading_room_store()
    owner = _owner()
    return store, owner


def _decide(client, event_id, body, key="k1"):
    return client.post(URL.format(event_id) + "/decisions", headers=_hdr(key), json=body)


def test_every_decision_writes_one_lock_with_all_arms(world):
    store, owner = world
    store.upsert_decision_event(_event(
        "evt-1",
        data_cutoff="2026-11-16T00:59:00Z",
        strategy_version_id="sv-7",
        servant_suggestion={"action": "enter", "note": "servant raw text"},
        institutional_view={"persona_id": "inst-1", "stance": "enter"},
    ))
    client = _app(store, owner, TRADER)
    resp = _decide(client, "evt-1", {
        "decision": "approve", "rationale": "我的原始想法",
        "account_id": "acct-77", "broker_ids": ["BRK-1", "BRK-2"],
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["execution_observation_kind"] == "pantheon_paper_proxy"

    lock = client.get(URL.format("evt-1") + "/decision-lock").json()["data"]
    assert lock["human_decision_id"] == data["human_decision_id"]
    assert lock["decision_time"] == "2026-11-16T02:00:00Z"
    assert lock["available_data_cutoff"] == "2026-11-16T00:59:00Z"
    assert lock["decision"] == "enter"
    assert lock["user_id"] == "user-001" and lock["tenant_id"] == "tenant-001"
    assert lock["account_id"] == "acct-77" and lock["broker_ids"] == ["BRK-1", "BRK-2"]
    assert lock["raw_rationale"] == "我的原始想法"
    arms = lock["arms"]
    assert arms["base_strategy"]["strategy_version_id"] == "sv-7"
    assert arms["private_servant"]["available"] is True
    assert arms["private_servant"]["note"] == "servant raw text"
    assert arms["institutional_view"]["persona_id"] == "inst-1"
    assert verify_checksum(lock)
    assert len(owner.store.list_decision_locks()) == 1


def test_second_write_for_same_decision_is_rejected_and_lock_unchanged(world):
    store, owner = world
    store.upsert_decision_event(_event("evt-2"))
    client = _app(store, owner, TRADER)
    assert _decide(client, "evt-2", {"decision": "reject", "rationale": "first"}).status_code == 201
    before = owner.store.list_decision_locks()[0]
    # Direct owner write (same decision) is rejected too.
    from services.agora.shadow_mode import DecisionLockExistsError

    with pytest.raises(DecisionLockExistsError):
        owner.create_decision_lock(dict(before), ["trader"])
    # Production route replay: event already decided -> 409, no second lock.
    again = _decide(client, "evt-2", {"decision": "approve", "rationale": "second"}, key="k2")
    assert again.status_code == 409
    after = owner.store.list_decision_locks()
    assert len(after) == 1 and after[0]["raw_rationale"] == "first"


def test_missing_arms_recorded_unavailable_with_reason_nothing_fabricated(world):
    store, owner = world
    store.upsert_decision_event(_event("evt-3"))
    client = _app(store, owner, TRADER)
    assert _decide(client, "evt-3", {"decision": "approve"}).status_code == 201
    lock = owner.store.list_decision_locks()[0]
    servant = lock["arms"]["private_servant"]
    inst = lock["arms"]["institutional_view"]
    assert servant == {"available": False, "unavailable_reason": "no_servant_suggestion_on_decision_event"}
    assert inst == {"available": False, "unavailable_reason": "no_institutional_view_on_decision_event"}
    assert lock["arms"]["base_strategy"]["strategy_version_id"] is None
    assert lock["arms"]["base_strategy"]["strategy_version_unavailable_reason"] == "event_has_no_strategy_version"
    assert lock["available_data_cutoff"] is None
    assert lock["data_cutoff_unavailable_reason"] == "event_has_no_data_cutoff"
    assert lock["outcome"]["available"] is False
    assert lock["account_id"] is None and lock["account_unavailable_reason"] == "account_not_supplied"


def test_paper_proxy_never_labelled_actual_fill_and_no_order_side_effect(world):
    store, owner = world
    store.upsert_decision_event(_event("evt-4"))
    client = _app(store, owner, TRADER)
    assert _decide(client, "evt-4", {"decision": "approve"}).status_code == 201
    lock = owner.store.list_decision_locks()[0]
    assert lock["execution_observation_kind"] == "pantheon_paper_proxy"
    assert lock["execution_observation_kind"] not in ACTUAL_FILL_KINDS
    assert lock["actual_fill_claimed"] is False and lock["fill_refs"] == []
    assert "實際成交" not in lock["execution_display_label"].replace("實際成交（已驗證）", "")
    assert lock["execution_display_label"] == DISPLAY_LABELS["pantheon_paper_proxy"]
    assert lock["no_order_route_proof"] == "agora_decision_lock_record_only"


@pytest.mark.parametrize("verdict,kind", [("reject", "declared_no_trade"), ("defer", "declared_no_trade")])
def test_no_trade_verdicts_label_declared_no_trade(world, verdict, kind):
    store, owner = world
    store.upsert_decision_event(_event("evt-5"))
    client = _app(store, owner, TRADER)
    assert _decide(client, "evt-5", {"decision": verdict}).status_code == 201
    assert owner.store.list_decision_locks()[0]["execution_observation_kind"] == kind


def test_broker_fill_kinds_cannot_be_declared_through_decision_route(world):
    store, owner = world
    store.upsert_decision_event(_event("evt-6"))
    client = _app(store, owner, TRADER)
    resp = _decide(client, "evt-6", {"decision": "approve", "execution_observation_kind": "verified_broker_fill"})
    assert resp.status_code == 422
    assert owner.store.list_decision_locks() == []
    # decision was not recorded either
    assert store.get_decision_event("evt-6")["state"] == "pending_review"


def test_reads_operator_sees_any_trader_sees_only_own(world):
    store, owner = world
    store.upsert_decision_event(_event("evt-7"))
    assert _decide(_app(store, owner, TRADER), "evt-7", {"decision": "approve"}).status_code == 201
    lock_url = URL.format("evt-7") + "/decision-lock"
    assert _app(store, owner, TRADER).get(lock_url).status_code == 200
    assert _app(store, owner, OPERATOR).get(lock_url).status_code == 200
    assert _app(store, owner, OTHER_TRADER).get(lock_url).status_code == 404
    assert _app(store, owner, {**OPERATOR, "tenant_id": "tenant-xyz"}).get(lock_url).status_code == 404


def test_unconfigured_owner_fails_closed_and_records_no_decision(world):
    store, _ = world
    store.upsert_decision_event(_event("evt-8"))
    client = _app(store, None, TRADER)
    resp = _decide(client, "evt-8", {"decision": "approve"})
    assert resp.status_code == 503
    assert store.get_decision_event("evt-8")["state"] == "pending_review"


def test_owner_write_authority_denies_roles_outside_matrix(world):
    store, owner = world
    store.upsert_decision_event(_event("evt-9"))
    client = _app(store, owner, {**TRADER, "roles": ["viewer"]})
    assert _decide(client, "evt-9", {"decision": "approve"}).status_code == 403
    assert owner.store.list_decision_locks() == []
    with pytest.raises(AgoraWriteForbiddenError):
        owner.create_decision_lock({"human_decision_id": "x"}, ["viewer"])


def test_decision_route_emits_four_learning_evidence_kinds_with_snapshot_one_per_lock():
    from services.control_plane.bff.agora.dataset_extraction.extractor import AgoraDatasetStore
    from services.control_plane.bff.agora.dataset_extraction.models import DatasetKind, InteractionKind

    store = make_trading_room_store()
    ds_store = AgoraDatasetStore(backend="memory")
    with mock.patch("services.agora.store.PostgresJsonOwnerStore", _MemoryTable):
        owner = AgoraWriteService(
            store=AgoraStore(dsn="postgresql://test/none", bootstrap=False),
            dataset_store=ds_store,
        )
    client = _app(store, owner, TRADER)

    verdicts = [
        ("evt-ev-1", {"decision": "approve", "rationale": "確認進場"}, InteractionKind.DECISION, "enter"),
        ("evt-ev-2", {"decision": "reject", "rationale": "流動性不足拒絕"}, InteractionKind.REJECTION, "reject"),
        ("evt-ev-3", {"decision": "defer", "rationale": "延後至尾盤再看"}, InteractionKind.DEFERRAL, "defer"),
        (
            "evt-ev-4",
            {"decision": "modify", "rationale": "手動減半部位", "modifications": {"action": "reduce", "size_hint": "small"}},
            InteractionKind.INTERVENTION,
            "reduce",
        ),
    ]

    for idx, (event_id, payload, expected_kind, expected_action) in enumerate(verdicts):
        store.upsert_decision_event(
            _event(
                event_id,
                data_cutoff="2026-11-27T00:59:00Z",
                strategy_version_id="sv-11",
                servant_suggestion={"action": "enter", "persona_id": "servant-user-001"},
            )
        )
        resp = _decide(client, event_id, payload, key=f"k-ev-{idx}")
        assert resp.status_code == 201, resp.text
        human_decision_id = resp.json()["data"]["human_decision_id"]

        lock = client.get(URL.format(event_id) + "/decision-lock").json()["data"]
        assert lock["evidence_kind"] == expected_kind.value
        assert lock["decision_time_snapshot"]["symbol"] == "2330.TW"
        assert lock["decision_time_snapshot"]["available_data_cutoff"] == "2026-11-27T00:59:00Z"

        evidence_rec = ds_store.get(
            f"ev-lock-{human_decision_id}",
            tenant_id="tenant-001",
            user_id="user-001",
        )
        assert evidence_rec is not None
        assert evidence_rec.dataset_kind == DatasetKind.LEARN
        assert evidence_rec.interaction_kind == expected_kind
        assert evidence_rec.retention_days == 30
        assert evidence_rec.content["action"] == expected_action
        assert evidence_rec.content["decision_time_snapshot"]["symbol"] == "2330.TW"

    learn_items = ds_store.list_by_dataset(DatasetKind.LEARN, tenant_id="tenant-001", user_id="user-001")
    assert len(learn_items) == 4
    handoffs = ds_store.list_handoffs_for_tenant(tenant_id="tenant-001", pending_only=True)
    assert len(handoffs) == 4
    assert {h["interaction_kind"] for h in handoffs} == {"decision", "rejection", "deferral", "intervention"}


def test_decision_succeeds_when_dataset_store_raises_on_evidence_emission():
    from services.control_plane.bff.agora.dataset_extraction.extractor import IdempotencyConflictError

    store = make_trading_room_store()
    failing_ds_store = mock.MagicMock()
    failing_ds_store.add_to_inbox.side_effect = RuntimeError("Postgres dataset store connection refused")
    with mock.patch("services.agora.store.PostgresJsonOwnerStore", _MemoryTable):
        owner = AgoraWriteService(
            store=AgoraStore(dsn="postgresql://test/none", bootstrap=False),
            dataset_store=failing_ds_store,
        )
    client = _app(store, owner, TRADER)

    store.upsert_decision_event(_event("evt-fail-ds-1", data_cutoff="2026-11-27T00:59:00Z"))
    resp = _decide(client, "evt-fail-ds-1", {"decision": "approve", "rationale": "Proceed even if dataset store is down"})
    assert resp.status_code == 201, resp.text
    assert store.get_decision_event("evt-fail-ds-1")["state"] == "decided"
    lock = client.get(URL.format("evt-fail-ds-1") + "/decision-lock").json()["data"]
    assert lock["evidence_kind"] == "decision"

    # Also test IdempotencyConflictError from dataset store does not block decision lock creation
    failing_ds_store.add_to_inbox.side_effect = IdempotencyConflictError("Conflicting durable evidence digest")
    store.upsert_decision_event(_event("evt-fail-ds-2", data_cutoff="2026-11-27T00:59:00Z"))
    resp2 = _decide(client, "evt-fail-ds-2", {"decision": "reject", "rationale": "Reject despite conflict"}, key="k-fail-2")
    assert resp2.status_code == 201, resp2.text
    assert store.get_decision_event("evt-fail-ds-2")["state"] == "decided"


def test_journal_write_path_emits_four_learning_evidence_kinds_and_survives_dataset_store_error(tmp_path):
    from services.control_plane.bff.agora.dataset_extraction.extractor import AgoraDatasetStore
    from services.control_plane.bff.agora.dataset_extraction.models import DatasetKind, InteractionKind
    from services.control_plane.bff.agora.service import AgoraService
    from services.control_plane.bff.governance.decision_journal_write_owner import (
        build_decision_journal_write_owner,
    )
    from services.control_plane.bff.models import OperatorIdentity

    ds_store = AgoraDatasetStore(backend="memory")
    journal_owner = build_decision_journal_write_owner(
        data_dir=str(tmp_path / "journal"),
    )
    journal_owner.dataset_store = ds_store
    agora_svc = AgoraService(journal_write_owner=journal_owner)
    agora_svc.dataset_store = ds_store
    identity = OperatorIdentity(
        operator_id="user-001",
        roles=["operator"],
        claims={"tenant_id": "tenant-001"},
    )

    cases = [
        (
            "jrnl-k1",
            {
                "id": "dje-ev-1",
                "title": "Confirm entry 2330.TW",
                "body": "Confirmed entry at open",
                "decision": "approve",
                "symbol": "2330.TW",
                "strategy_id": "strat-001",
                "data_cutoff": "2026-11-27T01:00:00Z",
                "visibility": "private",
            },
            InteractionKind.DECISION,
        ),
        (
            "jrnl-k2",
            {
                "id": "dje-ev-2",
                "title": "Reject signal 2330.TW",
                "body": "Rejected due to spread",
                "decision": "reject",
                "symbol": "2330.TW",
                "strategy_id": "strat-001",
                "data_cutoff": "2026-11-27T01:05:00Z",
                "visibility": "private",
            },
            InteractionKind.REJECTION,
        ),
        (
            "jrnl-k3",
            {
                "id": "dje-ev-3",
                "title": "Defer entry 2317.TW",
                "body": "Defer until afternoon session",
                "decision": "defer",
                "symbol": "2317.TW",
                "strategy_id": "strat-001",
                "data_cutoff": "2026-11-27T01:10:00Z",
                "visibility": "private",
            },
            InteractionKind.DEFERRAL,
        ),
        (
            "jrnl-k4",
            {
                "id": "dje-ev-4",
                "title": "Manual trim 2330.TW",
                "body": "Intervened to halve position size",
                "modifications": {"action": "reduce", "size_hint": "small"},
                "tags": ["intervention"],
                "symbol": "2330.TW",
                "strategy_id": "strat-001",
                "data_cutoff": "2026-11-27T01:15:00Z",
                "visibility": "private",
            },
            InteractionKind.INTERVENTION,
        ),
    ]

    for idem_key, payload, expected_kind in cases:
        res = agora_svc.create_journal_entry(
            payload=payload,
            identity=identity,
            idempotency_key=idem_key,
            tenant_id="tenant-001",
            user_id="user-001",
        )
        entry_id = res["data"]["id"]
        rec = ds_store.get(f"ev-journal-{entry_id}", tenant_id="tenant-001", user_id="user-001")
        assert rec is not None
        assert rec.dataset_kind == DatasetKind.LEARN
        assert rec.interaction_kind == expected_kind
        assert rec.content["decision_time_snapshot"]["strategy_id"] == "strat-001"

    assert len(ds_store.list_by_dataset(DatasetKind.LEARN, tenant_id="tenant-001", user_id="user-001")) == 4

    # Plain journal note without decision signal produces no learn evidence
    res_plain = agora_svc.create_journal_entry(
        payload={
            "id": "dje-plain-note",
            "title": "Lunch",
            "body": "Market felt slow, read about semis.",
            "visibility": "private",
        },
        identity=identity,
        idempotency_key="jrnl-k-plain",
        tenant_id="tenant-001",
        user_id="user-001",
    )
    assert res_plain["data"]["id"] == "dje-plain-note"
    assert ds_store.get("ev-journal-dje-plain-note", tenant_id="tenant-001", user_id="user-001") is None
    assert len(ds_store.list_by_dataset(DatasetKind.LEARN, tenant_id="tenant-001", user_id="user-001")) == 4

    # Verify dataset store failure does not block journal creation even when decision signal is present
    broken_ds = mock.MagicMock()
    broken_ds.add_to_inbox.side_effect = RuntimeError("dataset store down")
    journal_owner.dataset_store = broken_ds
    agora_svc.dataset_store = broken_ds
    res_ok = agora_svc.create_journal_entry(
        payload={
            "id": "dje-ev-5",
            "title": "Still succeeds",
            "body": "Journal write succeeds",
            "decision": "approve",
            "visibility": "private",
        },
        identity=identity,
        idempotency_key="jrnl-k5",
        tenant_id="tenant-001",
        user_id="user-001",
    )
    assert res_ok["data"]["id"] == "dje-ev-5"
