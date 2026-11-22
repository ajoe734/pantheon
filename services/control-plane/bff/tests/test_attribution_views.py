"""Tests for BFF attribution reads (outliers, records, shadow) with owners doubled at their edges."""
from __future__ import annotations

import io
import json
import time
from typing import Any, Dict, List, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.attribution.router import create_attribution_router
from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.core.app_factory import (
    build_bff_app,
    compose_bff_app,
    get_canonical_bff_route_set,
)
from services.control_plane.bff.models import utc_now
from services.control_plane.bff.personas.routes.common import _management_read_timeout_seconds
from services.control_plane.bff.tests.bff_compose_stand_ins import resolve_with_stand_ins
from services.runtime_auth_inbound import encode_jwt_hs256

SECRET = "attribution-views-test-jwt-secret-only"
TOKEN = "test-only-evaluation-service-token"


def headers(sub: str, roles: List[str], tenant: str = "tenant-a", **claims: Any) -> Dict[str, str]:
    c = {"sub": sub, "roles": roles, "tenant_id": tenant, "exp": 4102444800, **claims}
    return {"Authorization": f"Bearer {encode_jwt_hs256(c, secret=SECRET)}"}


def record(rid: str, kind: str = "trader", subject: str = "trader-alice", direction: str = "underperform", meeting: str = "mtg-004"):
    return {
        "record_id": rid,
        "subject_kind": kind,
        "subject_id": subject,
        "window": {"start": "2026-11-02", "end": "2026-11-09"},
        "direction": direction,
        "metrics": {"excess_return": -0.031},
        "decomposition": {"selection": -0.01, "entry_timing": -0.02, "sizing": 0.0, "exit": 0.004, "costs": -0.005},
        "sample_size": 42,
        "luck_interval": {"low": -0.05, "high": 0.01, "level": 0.9},
        "regime_ref": "regime-7",
        "meeting_id": meeting,
        "created_at": "2026-11-10T00:00:00Z",
    }


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "")
    monkeypatch.delenv("PANTHEON_BFF_AUTH_STUB", raising=False)
    monkeypatch.setenv("PANTHEON_EVALUATION_API_URL", "http://evaluation.test")
    monkeypatch.setenv("EVALUATION_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("PANTHEON_CONSULTATION_API_URL", "http://consultation.test")
    monkeypatch.setenv("CONSULTATION_SERVICE_TOKEN", "consult-token")

    state: Dict[str, Any] = {
        "offline": False,
        "records": [
            record("rec-1"),
            record("rec-2", subject="trader-bob", direction="outstanding"),
            record("rec-3", kind="persona", subject="persona-alpha", direction="disagreement"),
        ],
        "shadow": {
            "dec-1": {
                "decision_id": "dec-1",
                "arms": [
                    {"arm": "trader", "settlement_source": "pantheon_paper_proxy", "outcome": {"pnl": -0.01}, "settled_at": "2026-11-12T00:00:00Z", "unavailable_reason": None},
                    {"arm": "base_strategy", "settlement_source": "market_data_replay", "outcome": {"pnl": 0.01}, "settled_at": "2026-11-12T00:00:00Z", "unavailable_reason": None},
                    {"arm": "private_servant", "settlement_source": None, "outcome": None, "settled_at": None, "unavailable_reason": "no_suggestion"},
                    {"arm": "institutional_view", "settlement_source": "market_data_replay", "outcome": {"pnl": 0.02}, "settled_at": "2026-11-12T00:00:00Z", "unavailable_reason": None},
                ],
            }
        },
        "locks": {
            ("dec-1", "trader-alice"): {
                "decision": "enter", "trading_room_verdict": "approve", "execution_observation_kind": "pantheon_paper_proxy",
                "execution_display_label": "交易員決策（紙上代理）", "execution_verified": False, "decision_time": "2026-11-11T00:00:00Z",
                "trader_identity": {"user_id": "trader-alice"}, "account_id": "acct-alice-01",
                "arms": {
                    "base_strategy": {"available": True, "strategy_id": "s1"},
                    "private_servant": {"available": False, "unavailable_reason": "no_servant_suggestion_on_decision_event"},
                    "institutional_view": {"available": True, "suggested_action": "hold"},
                },
            }
        },
        "lock_offline": False,
    }
    calls: List[Any] = []

    def transport(req, timeout=5):
        u = urlsplit(req.full_url)
        q = parse_qs(u.query)
        hdrs = {k.lower(): v for k, v in req.header_items()}
        calls.append((req.method, u.hostname, u.path, q, hdrs))
        if state["offline"]:
            raise URLError("simulated outage")

        def ok(payload):
            r = io.BytesIO(json.dumps(payload).encode())
            r.status = 200
            return r

        def nf():
            raise HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b'{"detail":"nf"}'))

        if u.hostname == "consultation.test":
            return ok({"meeting_id": "mtg-004", "meeting_type": "MT-4", "status": "closed", "outcome": "REVIEW",
                       "hypothesis_validation": {"status": "pending", "hypotheses": 2}})
        assert u.hostname == "evaluation.test", req.full_url
        assert hdrs.get("authorization") == f"Bearer {TOKEN}"
        assert req.method == "GET"
        if u.path == "/api/attribution/records":
            items = list(state["records"])
            if "subject_kind" in q:
                items = [r for r in items if r["subject_kind"] == q["subject_kind"][0]]
            if "direction" in q:
                items = [r for r in items if r["direction"] == q["direction"][0]]
            return ok({"items": items, "next_cursor": None})
        if u.path.startswith("/api/attribution/records/"):
            rid = u.path.rsplit("/", 1)[-1]
            for r in state["records"]:
                if r["record_id"] == rid:
                    return ok(r)
            nf()
        if u.path.startswith("/api/attribution/shadow/"):
            did = u.path.rsplit("/", 1)[-1]
            if did in state["shadow"]:
                return ok(state["shadow"][did])
            nf()
        raise AssertionError(u.path)

    monkeypatch.setattr("urllib.request.urlopen", transport)

    def lock_reader(decision_id, tenant_id, user_id, roles):
        if state["lock_offline"]:
            raise RuntimeError("agora_owner_unavailable")
        if "operator" in roles:
            return next((v for (d, _u), v in state["locks"].items() if d == decision_id), None)
        return state["locks"].get((decision_id, user_id))

    app = build_bff_app()
    app.include_router(
        create_attribution_router(
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now,
            lock_reader=lock_reader,
        )
    )
    return TestClient(app), calls, state


OP = lambda: headers("op-alice", ["operator"])  # noqa: E731
TRADER = lambda sub="trader-alice": headers(sub, ["trader"], agora_user_id=sub)  # noqa: E731


def test_operator_lists_outliers_with_decomposition_luck_meeting_and_hypothesis(stack):
    client, calls, _ = stack
    r = client.get("/bff/management/attribution/records?subject_kind=trader&direction=underperform", headers=OP())
    assert r.status_code == 200, r.text
    body = r.json()
    assert [i["record_id"] for i in body["items"]] == ["rec-1"]
    item = body["items"][0]
    assert item["decomposition"]["entry_timing"] == -0.02
    assert item["luck_interval"]["level"] == 0.9
    assert item["subject_id"] == "trader-alice"
    assert item["meeting"]["meeting_id"] == "mtg-004" and item["meeting"]["meeting_type"] == "MT-4"
    assert item["hypothesis_validation"]["status"] == "unavailable"
    owner = [c for c in calls if c[1] == "evaluation.test"]
    assert len(owner) == 1 and owner[0][3]["subject_kind"] == ["trader"]
    assert client.get("/bff/management/attribution/outliers", headers=OP()).json()["items"]


def test_record_detail_enriched_with_meeting_hypothesis_status(stack):
    client, _, _ = stack
    r = client.get("/bff/management/attribution/records/rec-1", headers=OP())
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["meeting"]["status"] == "closed"
    assert d["hypothesis_validation"] == {"status": "pending", "hypotheses": 2}


def test_trader_sees_only_own_records_and_unknown_is_404(stack):
    client, _, _ = stack
    items = client.get("/bff/management/attribution/records", headers=TRADER()).json()["items"]
    assert [i["record_id"] for i in items] == ["rec-1"]
    assert client.get("/bff/management/attribution/records/rec-1", headers=TRADER()).status_code == 200
    assert client.get("/bff/management/attribution/records/rec-2", headers=TRADER()).status_code == 404
    assert client.get("/bff/management/attribution/records/rec-3", headers=TRADER()).status_code == 404
    assert client.get("/bff/management/attribution/records/nope", headers=OP()).status_code == 404


def test_invalid_filters_rejected(stack):
    client, _, _ = stack
    assert client.get("/bff/management/attribution/records?direction=sideways", headers=OP()).status_code == 422
    assert client.get("/bff/management/attribution/records?subject_kind=alien", headers=OP()).status_code == 422


def test_owner_unavailable_is_explicit(stack):
    client, _, state = stack
    state["offline"] = True
    for route in ("/bff/management/attribution/records", "/bff/management/attribution/records/rec-1"):
        r = client.get(route, headers=OP())
        assert r.status_code == 200
        assert r.json()["status"] == "unavailable" and r.json()["reason"] == "attribution_owner_unavailable"


def test_unconfigured_owner_is_unavailable(stack, monkeypatch):
    client, _, _ = stack
    monkeypatch.delenv("PANTHEON_EVALUATION_API_URL")
    monkeypatch.delenv("EVALUATION_URL", raising=False)
    r = client.get("/bff/management/attribution/records", headers=OP())
    assert r.json()["reason"] == "attribution_owner_unconfigured"


def test_shadow_shows_four_arms_with_settlement_sources(stack):
    client, _, _ = stack
    r = client.get("/bff/management/attribution/shadow/dec-1", headers=OP())
    assert r.status_code == 200, r.text
    body = r.json()
    arms = {a["arm"]: a for a in body["data"]["arms"]}
    assert set(arms) == {"trader", "base_strategy", "private_servant", "institutional_view"}
    assert arms["trader"]["settlement_source"] == "pantheon_paper_proxy"
    assert arms["trader"]["decision_detail"]["execution_display_label"] == "交易員決策（紙上代理）"
    assert arms["private_servant"]["unavailable_reason"] == "no_suggestion"
    assert arms["base_strategy"]["decision_detail"]["strategy_id"] == "s1"
    assert body["data"]["trader_identity"]["user_id"] == "trader-alice"
    assert body["status"] == "ok"


def test_shadow_trader_own_only_and_unknown_404(stack):
    client, _, _ = stack
    assert client.get("/bff/management/attribution/shadow/dec-1", headers=TRADER()).status_code == 200
    assert client.get("/bff/management/attribution/shadow/dec-1", headers=TRADER("trader-bob")).status_code == 404
    assert client.get("/bff/management/attribution/shadow/dec-x", headers=OP()).status_code == 404


def test_shadow_degrades_explicitly_when_one_owner_down(stack):
    client, _, state = stack
    state["offline"] = True
    body = client.get("/bff/management/attribution/shadow/dec-1", headers=OP()).json()
    assert body["status"] == "degraded" and body["meta"]["sources"]["attribution"] == "attribution_owner_unavailable"
    assert all(a["unavailable_reason"] == "attribution_owner_unavailable" for a in body["data"]["arms"])
    state["offline"] = False
    state["lock_offline"] = True
    body = client.get("/bff/management/attribution/shadow/dec-1", headers=OP()).json()
    assert body["status"] == "degraded" and body["meta"]["sources"]["agora"] == "agora_owner_unavailable"
    assert body["data"]["arms"][0]["settlement_source"] == "pantheon_paper_proxy"


def test_reads_complete_within_management_gate_budget(stack):
    client, calls, state = stack
    state["records"] = [record(f"rec-{i}") for i in range(200)]
    budget = _management_read_timeout_seconds()
    for route in (
        "/bff/management/attribution/records?limit=200",
        "/bff/management/attribution/records/rec-1",
        "/bff/management/attribution/shadow/dec-1",
    ):
        t0 = time.perf_counter()
        assert client.get(route, headers=OP()).status_code == 200
        assert time.perf_counter() - t0 < budget, route


def test_app_factory_mounts_attribution_routes():
    route_set = get_canonical_bff_route_set(compose_bff_app(dependency_resolver=resolve_with_stand_ins))
    for expected in (
        ("GET", "/bff/management/attribution/records"),
        ("GET", "/bff/management/attribution/outliers"),
        ("GET", "/bff/management/attribution/records/{record_id}"),
        ("GET", "/bff/management/attribution/shadow/{decision_id}"),
    ):
        assert expected in route_set, expected


def test_composed_bff_shares_single_agora_decision_lock_owner_between_trading_room_and_shadow(
    monkeypatch: pytest.MonkeyPatch,
):
    from services.agora import service as agora_service_mod
    from services.agora.service import AgoraWriteService
    from services.agora.shadow_mode import build_decision_lock
    from services.control_plane.bff.agora.trading_room.service import get_active_trading_room_service

    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "")
    monkeypatch.delenv("PANTHEON_BFF_AUTH_STUB", raising=False)
    monkeypatch.setenv("PANTHEON_EVALUATION_API_URL", "http://evaluation.test")
    monkeypatch.setenv("EVALUATION_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("AGORA_STORE_DSN", "postgresql://test/agora")

    created_tables: List[Any] = []
    owner_builds: List[AgoraWriteService] = []
    state: Dict[str, Any] = {
        "evaluation_offline": False,
        "agora_store_offline": False,
        "shadow_by_tenant": {
            ("tenant-a", "dec-1"): {
                "decision_id": "dec-1",
                "arms": [
                    {
                        "arm": "trader",
                        "settlement_source": "pantheon_paper_proxy",
                        "outcome": {"pnl": -0.01},
                        "settled_at": "2026-11-12T00:00:00Z",
                        "unavailable_reason": None,
                    },
                    {
                        "arm": "base_strategy",
                        "settlement_source": "market_data_replay",
                        "outcome": {"pnl": 0.01},
                        "settled_at": "2026-11-12T00:00:00Z",
                        "unavailable_reason": None,
                    },
                    {
                        "arm": "private_servant",
                        "settlement_source": None,
                        "outcome": None,
                        "settled_at": None,
                        "unavailable_reason": "no_suggestion",
                    },
                    {
                        "arm": "institutional_view",
                        "settlement_source": "market_data_replay",
                        "outcome": {"pnl": 0.02},
                        "settled_at": "2026-11-12T00:00:00Z",
                        "unavailable_reason": None,
                    },
                ],
            }
        },
    }

    class _MemoryTable:
        def __init__(self, **kw: Any) -> None:
            self.kw = kw
            self.rows: Dict[str, Dict[str, Any]] = {}
            created_tables.append(self)

        def put(self, record_id: str, payload: Mapping[str, Any]) -> None:
            self.rows[record_id] = dict(payload)

        def insert_if_absent(self, record_id: str, payload: Mapping[str, Any], **_kw: Any):
            if record_id in self.rows:
                return False, dict(self.rows[record_id])
            self.rows[record_id] = dict(payload)
            return True, dict(payload)

        def get(self, record_id: str):
            if state["agora_store_offline"]:
                raise RuntimeError("agora_store_refused")
            row = self.rows.get(record_id)
            return dict(row) if row is not None else None

        def list_all(self, **_kw: Any):
            if state["agora_store_offline"]:
                raise RuntimeError("agora_store_refused")
            return [dict(r) for r in self.rows.values()]

    monkeypatch.setattr("services.agora.store.PostgresJsonOwnerStore", _MemoryTable)
    real_build = agora_service_mod.build_agora_write_service

    def tracked_build(*args: Any, **kwargs: Any) -> AgoraWriteService:
        built = real_build(*args, **kwargs)
        owner_builds.append(built)
        return built

    monkeypatch.setattr(agora_service_mod, "build_agora_write_service", tracked_build)

    def transport(req, timeout=5):
        u = urlsplit(req.full_url)
        hdrs = {k.lower(): v for k, v in req.header_items()}
        assert u.hostname == "evaluation.test", req.full_url
        assert hdrs.get("authorization") == f"Bearer {TOKEN}"
        if state["evaluation_offline"]:
            raise URLError("evaluation offline")
        if u.path.startswith("/api/attribution/shadow/"):
            did = u.path.rsplit("/", 1)[-1]
            tenant = hdrs.get("x-pantheon-tenant-id", "")
            payload = state["shadow_by_tenant"].get((tenant, did))
            if payload is not None:
                r = io.BytesIO(json.dumps(payload).encode())
                r.status = 200
                return r
            raise HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b'{"detail":"nf"}'))
        raise AssertionError(u.path)

    monkeypatch.setattr("urllib.request.urlopen", transport)

    # Compose through the real production entrypoint with no explicit agora_owner override.
    app = compose_bff_app(dependency_resolver=resolve_with_stand_ins)
    client = TestClient(app)

    assert len(owner_builds) == 1, "Expected a single shared AgoraWriteService owner instance"
    shared_owner = owner_builds[0]
    assert isinstance(shared_owner, AgoraWriteService)
    assert app.state.agora_router.decision_lock_owner is shared_owner
    assert app.state.decision_lock_owner is shared_owner
    assert get_active_trading_room_service().decision_lock_owner is shared_owner

    op_a = headers("op-alice", ["operator"], tenant="tenant-a")
    op_b = headers("op-bob", ["operator"], tenant="tenant-b")
    trader_alice_a = headers("trader-alice", ["viewer", "trader"], tenant="tenant-a", agora_user_id="trader-alice")
    trader_bob_a = headers("trader-bob", ["viewer", "trader"], tenant="tenant-a", agora_user_id="trader-bob")
    trader_alice_b = headers("trader-alice", ["viewer", "trader"], tenant="tenant-b", agora_user_id="trader-alice")

    # 1. Genuinely unknown lock with no attribution resolves to 404 on BOTH paths.
    tr_unknown = client.get("/bff/agora/trading-room/decision-events/dec-unknown/decision-lock", headers=op_a)
    sh_unknown_op = client.get("/bff/management/attribution/shadow/dec-unknown", headers=op_a)
    sh_unknown_tr = client.get("/bff/management/attribution/shadow/dec-unknown", headers=trader_alice_a)
    assert tr_unknown.status_code == 404
    assert tr_unknown.json()["error"]["details"]["reason"] == "decision_lock_not_found"
    assert sh_unknown_op.status_code == 404
    assert sh_unknown_op.json()["error"]["details"]["reason"] == "decision_not_found"
    assert sh_unknown_tr.status_code == 404

    # 2. Record a decision lock in the single shared Agora owner.
    event = {
        "decision_event_id": "dec-1",
        "tenant_id": "tenant-a",
        "user_id": "trader-alice",
        "event_kind": "entry",
        "strategy_id": "s1",
        "strategy_version_id": "sv-1",
        "suggested_action": "enter",
        "data_cutoff": "2026-11-11T00:00:00Z",
        "institutional_view": {"persona_id": "inst-1", "stance": "hold"},
    }
    lock = build_decision_lock(
        human_decision_id=shared_owner.store.decision_lock_id("tenant-a", "dec-1"),
        decision_record_id="rec-lock-1",
        event=event,
        decision_event_id="dec-1",
        tenant_id="tenant-a",
        user_id="trader-alice",
        body_decision="approve",
        rationale="進場理由",
        modifications=None,
        decision_time="2026-11-11T00:01:00Z",
        execution_observation_kind=None,
        account_id="acct-alice-01",
        broker_ids=["BRK-01"],
        actor_roles=["trader"],
    )
    shared_owner.create_decision_lock(lock, ["trader"])

    # Same-tenant authorized trader reads own lock on both native Trading Room and Management Shadow.
    tr_alice = client.get("/bff/agora/trading-room/decision-events/dec-1/decision-lock", headers=trader_alice_a)
    assert tr_alice.status_code == 200, tr_alice.text
    assert tr_alice.json()["data"]["user_id"] == "trader-alice"
    assert tr_alice.json()["data"]["account_id"] == "acct-alice-01"

    sh_alice = client.get("/bff/management/attribution/shadow/dec-1", headers=trader_alice_a)
    assert sh_alice.status_code == 200, sh_alice.text
    sh_alice_body = sh_alice.json()
    assert sh_alice_body["status"] == "ok"
    assert sh_alice_body["meta"]["sources"] == {"attribution": "ok", "agora": "ok"}
    assert sh_alice_body["data"]["account_id"] == "acct-alice-01"
    assert "trader_identity" not in sh_alice_body["data"]

    # Same-tenant authorized operator reads lock and intended operator fields on both paths.
    tr_op = client.get("/bff/agora/trading-room/decision-events/dec-1/decision-lock", headers=op_a)
    assert tr_op.status_code == 200, tr_op.text
    assert tr_op.json()["data"]["human_decision_id"] == lock["human_decision_id"]

    sh_op = client.get("/bff/management/attribution/shadow/dec-1", headers=op_a)
    assert sh_op.status_code == 200, sh_op.text
    sh_op_body = sh_op.json()
    assert sh_op_body["status"] == "ok"
    assert sh_op_body["meta"]["sources"] == {"attribution": "ok", "agora": "ok"}
    assert sh_op_body["data"]["trader_identity"]["user_id"] == "trader-alice"
    assert sh_op_body["data"]["account_id"] == "acct-alice-01"
    arms_by_name = {a["arm"]: a for a in sh_op_body["data"]["arms"]}
    assert arms_by_name["trader"]["decision_detail"]["decision"] == "enter"
    assert arms_by_name["trader"]["settlement_source"] == "pantheon_paper_proxy"
    assert arms_by_name["base_strategy"]["decision_detail"]["strategy_id"] == "s1"

    # Same-tenant other trader is denied (404) on both native Trading Room and Management Shadow.
    assert client.get("/bff/agora/trading-room/decision-events/dec-1/decision-lock", headers=trader_bob_a).status_code == 404
    assert client.get("/bff/management/attribution/shadow/dec-1", headers=trader_bob_a).status_code == 404

    # Cross-tenant trader and cross-tenant operator are denied (404) on both paths.
    assert client.get("/bff/agora/trading-room/decision-events/dec-1/decision-lock", headers=trader_alice_b).status_code == 404
    assert client.get("/bff/management/attribution/shadow/dec-1", headers=trader_alice_b).status_code == 404
    assert client.get("/bff/agora/trading-room/decision-events/dec-1/decision-lock", headers=op_b).status_code == 404
    assert client.get("/bff/management/attribution/shadow/dec-1", headers=op_b).status_code == 404

    # 3. Real source refusal / store unavailability stays distinguishable from missing data (404).
    state["agora_store_offline"] = True
    sh_degraded = client.get("/bff/management/attribution/shadow/dec-1", headers=op_a)
    assert sh_degraded.status_code == 200
    sh_degraded_body = sh_degraded.json()
    assert sh_degraded_body["status"] == "degraded"
    assert sh_degraded_body["meta"]["sources"] == {"attribution": "ok", "agora": "agora_owner_unavailable"}
    assert sh_degraded_body["data"]["arms"][0]["settlement_source"] == "pantheon_paper_proxy"
    state["agora_store_offline"] = False

    # 4. Unconfigured Agora DSN creates no default substitute owner.
    monkeypatch.delenv("AGORA_STORE_DSN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    unconfigured_app = compose_bff_app(dependency_resolver=resolve_with_stand_ins)
    assert unconfigured_app.state.decision_lock_owner is None
    unconfigured_client = TestClient(unconfigured_app)
    assert (
        unconfigured_client.get(
            "/bff/agora/trading-room/decision-events/dec-1/decision-lock", headers=op_a
        ).status_code
        == 503
    )
    unconfigured_shadow = unconfigured_client.get("/bff/management/attribution/shadow/dec-1", headers=op_a).json()
    assert unconfigured_shadow["status"] == "degraded"
    assert unconfigured_shadow["meta"]["sources"]["agora"] == "agora_owner_unavailable"
