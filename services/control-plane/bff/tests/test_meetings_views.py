"""Tests for Management & Agora BFF meetings views and operator convene forward.

Covers AC1, AC2, AC3, AC5 of BFF-MEETINGS-VIEWS-R2-20261116 with the
consultation owner doubled at its HTTP edge (`urllib.request.urlopen`).
"""
from __future__ import annotations

import io
import json
import time
from typing import Any, Dict, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

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
from services.control_plane.bff.meetings.router import create_meetings_router
from services.control_plane.bff.models import utc_now
from services.control_plane.bff.personas.routes.common import _management_read_timeout_seconds
from services.control_plane.bff.tests.bff_compose_stand_ins import resolve_with_stand_ins
from services.runtime_auth_inbound import encode_jwt_hs256

SECRET = "meetings-views-r2-test-jwt-secret-only"
SERVICE_TOKEN = "test-only-consultation-service-token"


def make_headers(
    sub: str = "op-alice",
    *,
    tenant: str = "tenant-a",
    roles: List[str] | None = None,
    idempotency_key: str | None = None,
    extra_claims: Dict[str, Any] | None = None,
) -> Dict[str, str]:
    claims: Dict[str, Any] = {
        "sub": sub,
        "roles": roles if roles is not None else ["operator"],
        "tenant_id": tenant,
        "exp": 4102444800,
    }
    if extra_claims:
        claims.update(extra_claims)
    token = encode_jwt_hs256(claims, secret=SECRET)
    hdrs = {"Authorization": f"Bearer {token}"}
    if idempotency_key is not None:
        hdrs["Idempotency-Key"] = idempotency_key
    return hdrs


def _sample_meeting_summary(
    idx: int = 1,
    *,
    status: str = "closed",
    meeting_type: str = "MT-1",
    outcome: str = "PROPOSALS_ISSUED",
    chair: str = "persona-alpha",
    trader_id: str = "trader-alice",
) -> Dict[str, Any]:
    meeting_id = f"mtg-{idx:03d}"
    return {
        "meeting_id": meeting_id,
        "meeting_type": meeting_type,
        "topic": f"Weekly pool evolution review #{idx}",
        "status": status,
        "outcome": outcome,
        "chair_persona_id": chair,
        "convened_by": {"kind": "agora_user", "id": trader_id, "agora_user_id": trader_id},
        "subject_ref": {"kind": "trader", "id": trader_id, "account_id": "acct-alice-01"},
        "participants": [
            {"persona_id": chair, "role": "chair", "origin": "institutional"},
            {"persona_id": "persona-risk-officer", "role": "risk", "origin": "institutional"},
            {"persona_id": "advisor-macro-1", "role": "participant", "origin": "agora_advisor"},
            {
                "persona_id": "servant-alice",
                "role": "participant",
                "origin": "private_servant",
                "agora_user_id": trader_id,
                "trader_id": trader_id,
                "account_id": "acct-alice-01",
                "broker_ids": ["brk-ib-01"],
            },
        ],
        "round": 2,
        "created_at": "2026-11-16T10:00:00Z",
        "closed_at": "2026-11-16T10:05:00Z" if status == "closed" else None,
    }


def _sample_meeting_detail(
    meeting_id: str = "mtg-001",
    *,
    trader_id: str = "trader-alice",
) -> Dict[str, Any]:
    base = _sample_meeting_summary(1, status="closed", trader_id=trader_id)
    base["meeting_id"] = meeting_id
    base["status"] = "closed"
    base["meeting_type"] = "MT-4"
    base["attribution_record_id"] = "attr-001"
    base["attribution_record"] = {
        "record_id": "attr-001",
        "subject_kind": "trader",
        "subject_id": trader_id,
        "agora_user_id": trader_id,
        "account_id": "acct-alice-01",
        "broker_ids": ["brk-ib-01"],
        "direction": "outstanding",
        "decomposition": {"selection": 0.04, "entry_timing": 0.02, "sizing": 0.01, "exit": 0.01, "costs": -0.002},
        "sample_size": 18,
        "luck_interval": [0.01, 0.06],
    }
    base["agenda"] = ["Review outlier attribution attr-001", "Formulate testable causal hypothesis"]
    base["case_file_items"] = [
        {
            "item_id": "cf-shared-1",
            "kind": "pool_health",
            "relevance_scope": "committee",
            "source_owner": "evaluation",
            "excerpt": "Shared pool Sharpe 1.62 over 20d window",
        },
        {
            "item_id": "cf-private-leak-1",
            "kind": "memory",
            "relevance_scope": "persona_private",
            "memory_layer": "persona_private",
            "source_owner": "memory",
            "excerpt": "NEVER_RETURN_PRIVATE_MEMORY_EXCERPT_001",
        },
        {
            "item_id": "cf-agora-lock-1",
            "kind": "agora_decision_lock",
            "relevance_scope": "committee",
            "source_owner": "agora",
            "agora_user_id": trader_id,
            "trader_id": trader_id,
            "account_id": "acct-alice-01",
            "broker_ids": ["brk-ib-01"],
            "raw_text": "Trader Alice entered early stop-loss on high VIX day",
            "excerpt": "Decision lock dl-99",
        },
    ]
    base["pre_read"] = {
        "interpretations": [
            {
                "persona_id": "persona-alpha",
                "relevance_scope": "committee",
                "text": "Committee-visible interpretation of drawdown control.",
                "private_memory": "NEVER_RETURN_PRIVATE_FIELD_002",
            },
            {
                "persona_id": "persona-secret",
                "relevance_scope": "persona_private",
                "text": "NEVER_RETURN_PRIVATE_INTERPRETATION_003",
            },
        ]
    }
    base["rounds"] = [
        {
            "round": 1,
            "positions": [
                {
                    "label": "participant:chair:momentum",
                    "persona_id": "persona-alpha",
                    "role": "chair",
                    "style": "momentum",
                    "stance": "support_hypothesis",
                    "confidence": 0.82,
                    "citations": ["cf-shared-1", "cf-agora-lock-1"],
                    "changed": False,
                    "private_memory": "NEVER_RETURN_PRIVATE_ROUND_004",
                },
                {
                    "label": "participant:red_team:contrarian",
                    "persona_id": "persona-red-team",
                    "role": "red_team",
                    "style": "contrarian",
                    "stance": "argue_small_sample",
                    "confidence": 0.65,
                    "citations": ["cf-shared-1"],
                    "changed": False,
                },
            ],
        },
        {
            "round": 2,
            "positions": [
                {
                    "label": "participant:chair:momentum",
                    "persona_id": "persona-alpha",
                    "role": "chair",
                    "style": "momentum",
                    "stance": "support_with_regime_control",
                    "confidence": 0.88,
                    "citations": ["cf-shared-1", "cf-agora-lock-1"],
                    "changed": True,
                },
                {
                    "label": "participant:red_team:contrarian",
                    "persona_id": "persona-red-team",
                    "role": "red_team",
                    "style": "contrarian",
                    "stance": "dissent_recorded",
                    "confidence": 0.60,
                    "citations": ["cf-shared-1"],
                    "changed": False,
                },
            ],
        },
    ]
    base["synthesis"] = {
        "proposals": [
            {
                "proposal_id": "prop-001",
                "meeting_id": meeting_id,
                "kind": "research_demand",
                "sponsor_persona_id": "persona-alpha",
                "downstream": {"target": "research_demand", "id": "rd-101", "status": "pending"},
            }
        ],
        "tally": {"support": 0.88, "dissent": 0.60, "binding": False},
        "dissent_memo_id": "memo-dissent-001",
        "outcome": "PROPOSALS_ISSUED",
    }
    base["writebacks"] = [
        {
            "persona_id": "persona-alpha",
            "entry_id": "pmem-wb-001",
            "content": "NEVER_RETURN_WRITEBACK_PRIVATE_TEXT_005",
            "private_memory": "NEVER_RETURN_WRITEBACK_PRIVATE_TEXT_006",
        },
        {
            "persona_id": "persona-red-team",
            "entry_id": "pmem-wb-002",
        },
    ]
    base["usage"] = {"participants": 4, "rounds": 2, "calls": 10}
    return base


@pytest.fixture
def meetings_stack(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "")
    monkeypatch.delenv("PANTHEON_BFF_AUTH_STUB", raising=False)
    monkeypatch.setenv("CONSULTATION_AUTH_REQUIRED", "true")
    monkeypatch.setenv("CONSULTATION_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("PANTHEON_CONSULTATION_API_URL", "http://consultation.test")

    state: Dict[str, Any] = {
        "offline": False,
        "status_override": {},
        "body_override": {},
        "meetings": [
            _sample_meeting_summary(1, status="open", meeting_type="MT-1", outcome="IN_PROGRESS", chair="persona-alpha"),
            _sample_meeting_summary(2, status="closed", meeting_type="MT-4", outcome="REVIEW", chair="persona-beta"),
        ],
        "details": {
            "mtg-001": _sample_meeting_detail("mtg-001", trader_id="trader-alice"),
            "mtg-002": _sample_meeting_detail("mtg-002", trader_id="trader-alice"),
        },
        "proposals": [
            {
                "proposal_id": "prop-001",
                "meeting_id": "mtg-001",
                "kind": "research_demand",
                "sponsor_persona_id": "persona-alpha",
                "downstream": {"target": "research_demand", "id": "rd-101", "status": "pending"},
            },
            {
                "proposal_id": "prop-002",
                "meeting_id": "mtg-002",
                "kind": "governance_approval",
                "sponsor_persona_id": "persona-beta",
                "downstream": {"target": "governance_approval", "id": "apv-202", "status": "pending_operator"},
            },
        ],
        "catalogue": {
            "types": [
                {
                    "meeting_type": "MT-1",
                    "enabled": True,
                    "cadence": "weekly",
                    "roster_rule": "owner_plus_peer_plus_risk",
                    "budget": {"max_participants": 5, "max_rounds": 2, "weekly_limit": 3},
                },
                {
                    "meeting_type": "MT-4",
                    "enabled": True,
                    "cadence": "event_attribution",
                    "roster_rule": "subject_plus_counterparty_plus_risk_plus_red_team",
                    "budget": {"max_participants": 5, "max_rounds": 2, "weekly_limit": 3},
                },
            ],
            "schedule": [
                {
                    "persona_id": "persona-alpha",
                    "meeting_type": "MT-1",
                    "last_run_at": "2026-11-09T10:00:00Z",
                    "next_due_at": "2026-11-16T10:00:00Z",
                }
            ],
            "refusals": [
                {
                    "persona_id": "persona-alpha",
                    "meeting_type": "MT-3",
                    "reason": "insufficient_participants",
                    "at": "2026-11-16T09:00:00Z",
                }
            ],
        },
        "convened_by_key": {},
    }
    calls: List[Tuple[str, str, Dict[str, List[str]], Dict[str, str], Any]] = []

    def transport(req, timeout=5):
        u = urlsplit(req.full_url)
        assert u.hostname == "consultation.test", f"Unexpected host: {req.full_url}"
        query = parse_qs(u.query)
        hdrs = {k.lower(): v for k, v in req.header_items()}
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        calls.append((req.method, u.path, query, hdrs, body))

        if state["offline"]:
            raise URLError("simulated consultation owner outage")
        if u.path in state["status_override"]:
            code = state["status_override"][u.path]
            err_body = json.dumps(state["body_override"].get(u.path, {"detail": f"simulated {code}"})).encode("utf-8")
            raise HTTPError(req.full_url, code, "override", {}, io.BytesIO(err_body))
        if u.path in state["body_override"]:
            payload = state["body_override"][u.path]
            res = io.BytesIO(json.dumps(payload).encode("utf-8"))
            res.status = 200
            return res

        assert hdrs.get("authorization") == f"Bearer {SERVICE_TOKEN}"
        assert hdrs.get("x-pantheon-tenant-id") == "tenant-a"

        if req.method == "GET" and u.path == "/api/consult/meetings":
            items = list(state["meetings"])
            if "status" in query:
                items = [m for m in items if m.get("status") == query["status"][0]]
            if "type" in query:
                items = [m for m in items if m.get("meeting_type") == query["type"][0]]
            if "persona_id" in query:
                pid = query["persona_id"][0]
                items = [
                    m
                    for m in items
                    if m.get("chair_persona_id") == pid
                    or any(p.get("persona_id") == pid for p in m.get("participants", []))
                ]
            limit = int(query["limit"][0]) if "limit" in query else len(items)
            payload = {"items": items[:limit], "next_cursor": None}
            res = io.BytesIO(json.dumps(payload).encode("utf-8"))
            res.status = 200
            return res

        if req.method == "GET" and u.path == "/api/consult/meetings/proposals":
            items = list(state["proposals"])
            if "status" in query:
                items = [
                    p
                    for p in items
                    if (p.get("downstream") or {}).get("status") == query["status"][0]
                ]
            if "persona_id" in query:
                items = [p for p in items if p.get("sponsor_persona_id") == query["persona_id"][0]]
            if "meeting_id" in query:
                items = [p for p in items if p.get("meeting_id") == query["meeting_id"][0]]
            res = io.BytesIO(json.dumps({"items": items}).encode("utf-8"))
            res.status = 200
            return res

        if req.method == "GET" and u.path == "/api/consult/meetings/catalogue":
            res = io.BytesIO(json.dumps(state["catalogue"]).encode("utf-8"))
            res.status = 200
            return res

        if req.method == "GET" and u.path.startswith("/api/consult/meetings/"):
            meeting_id = u.path.rsplit("/", 1)[-1]
            if meeting_id not in state["details"]:
                raise HTTPError(
                    req.full_url,
                    404,
                    "Not Found",
                    {},
                    io.BytesIO(json.dumps({"detail": "meeting not found"}).encode("utf-8")),
                )
            res = io.BytesIO(json.dumps(state["details"][meeting_id]).encode("utf-8"))
            res.status = 200
            return res

        if req.method == "POST" and u.path == "/api/consult/meetings":
            idem = hdrs.get("idempotency-key")
            assert idem, "Idempotency-Key header must be forwarded to consultation owner"
            if idem in state["convened_by_key"]:
                created = state["convened_by_key"][idem]
            else:
                created = {
                    "meeting_id": f"mtg-conv-{len(state['convened_by_key']) + 1:03d}",
                    "status": "convened",
                }
                state["convened_by_key"][idem] = created
            res = io.BytesIO(json.dumps(created).encode("utf-8"))
            res.status = 202
            return res

        if req.method == "POST" and u.path.startswith("/api/consult/meetings/") and u.path.endswith("/review"):
            meeting_id = u.path[: -len("/review")].rsplit("/", 1)[-1]
            if meeting_id not in state["details"]:
                raise HTTPError(
                    req.full_url,
                    404,
                    "Not Found",
                    {},
                    io.BytesIO(json.dumps({"detail": "Persona meeting not found"}).encode("utf-8")),
                )
            detail = state["details"][meeting_id]
            synth = detail.get("synthesis") or {}
            outcome = str(detail.get("outcome") or synth.get("outcome") or "").upper()
            if str(detail.get("status") or "").lower() not in {"closed", "completed"} or outcome != "REVIEW":
                raise HTTPError(
                    req.full_url,
                    409,
                    "Conflict",
                    {},
                    io.BytesIO(
                        json.dumps({"detail": "meeting_not_in_review", "reason": "meeting_not_in_review"}).encode("utf-8")
                    ),
                )
            note = str((body or {}).get("note") or "").strip()
            reviewed_by = str((body or {}).get("reviewed_by") or "").strip()
            existing_rev = detail.get("operator_review")
            if isinstance(existing_rev, dict) and existing_rev.get("status") == "reviewed":
                if existing_rev.get("reviewed_by") == reviewed_by and existing_rev.get("note") == note:
                    replayed_detail = {**detail, "replayed": True}
                    res = io.BytesIO(json.dumps(replayed_detail).encode("utf-8"))
                    res.status = 200
                    return res
                raise HTTPError(
                    req.full_url,
                    409,
                    "Conflict",
                    {},
                    io.BytesIO(
                        json.dumps(
                            {"detail": "meeting_already_reviewed", "reason": "meeting_already_reviewed"}
                        ).encode("utf-8")
                    ),
                )
            cause = str(synth.get("unreadable_reason") or detail.get("synthesis_unreadable_reason") or "unknown")
            detail["operator_review"] = {
                "status": "reviewed",
                "disposition": "closed_no_proposal",
                "note": note,
                "reviewed_by": reviewed_by,
                "reviewed_at": "2026-11-21T12:15:00Z",
                "cause": cause,
                "unreadable_reason": cause,
            }
            out_detail = {**detail, "replayed": False}
            res = io.BytesIO(json.dumps(out_detail).encode("utf-8"))
            res.status = 200
            return res

        raise AssertionError(f"Unhandled request {req.method} {u.path}")

    monkeypatch.setattr("urllib.request.urlopen", transport)

    app = build_bff_app()
    app.include_router(
        create_meetings_router(
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now,
        )
    )
    return TestClient(app), calls, state


def test_meetings_center_lists_proposals_catalogue_and_detail_forward_to_owner(meetings_stack):
    client, calls, state = meetings_stack

    # 1. List with filters (status, type, persona_id) for meetings center & persona tab
    resp = client.get(
        "/bff/management/meetings?status=open&type=MT-1&persona_id=persona-alpha",
        headers=make_headers("op-alice", roles=["operator"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert len(body["data"]["items"]) == 1
    assert body["data"]["items"][0]["meeting_id"] == "mtg-001"
    assert calls[-1][1] == "/api/consult/meetings"
    assert calls[-1][2] == {
        "status": ["open"],
        "type": ["MT-1"],
        "persona_id": ["persona-alpha"],
        "limit": ["50"],
    }

    # Alias route /bff/meetings also forwards identically
    alias_resp = client.get("/bff/meetings", headers=make_headers("op-alice"))
    assert alias_resp.status_code == 200
    assert len(alias_resp.json()["data"]["items"]) == 2

    # 2. Proposals list with downstream status & href
    prop_resp = client.get(
        "/bff/management/meetings/proposals?persona_id=persona-alpha&meeting_id=mtg-001",
        headers=make_headers("op-alice"),
    )
    assert prop_resp.status_code == 200, prop_resp.text
    prop_items = prop_resp.json()["data"]["items"]
    assert len(prop_items) == 1
    assert prop_items[0]["proposal_id"] == "prop-001"
    assert prop_items[0]["downstream"]["target"] == "research_demand"
    assert prop_items[0]["downstream"]["status"] == "pending"
    assert prop_items[0]["downstream"]["href"] == "/management/experiments"
    assert prop_items[0]["meeting_href"] == "/management/meetings/mtg-001"

    # 3. Catalogue with types, last/next due runs, and refusals
    cat_resp = client.get("/bff/management/meetings/catalogue", headers=make_headers("op-alice"))
    assert cat_resp.status_code == 200, cat_resp.text
    cat_data = cat_resp.json()["data"]
    assert [t["meeting_type"] for t in cat_data["types"]] == ["MT-1", "MT-4"]
    assert cat_data["schedule"][0]["next_due_at"] == "2026-11-16T10:00:00Z"
    assert cat_data["refusals"][0]["reason"] == "insufficient_participants"

    # 4. Unknown meeting_id returns honest 404
    not_found = client.get("/bff/management/meetings/mtg-unknown-999", headers=make_headers("op-alice"))
    assert not_found.status_code == 404, not_found.text
    assert not_found.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_unavailable_owner_returns_explicit_unavailable_state(meetings_stack, monkeypatch):
    client, _calls, state = meetings_stack
    state["offline"] = True

    for path, surface_key in (
        ("/bff/management/meetings", "meetings"),
        ("/bff/management/meetings/proposals", "meeting_proposals"),
        ("/bff/management/meetings/catalogue", "meeting_catalogue"),
        ("/bff/management/meetings/mtg-001", "meeting_detail"),
    ):
        resp = client.get(path, headers=make_headers("op-alice"))
        assert resp.status_code == 200, (path, resp.text)
        payload = resp.json()
        assert payload["status"] == "unavailable"
        assert payload["data"]["status"] == "unavailable"
        assert payload["meta"]["status"] == "unavailable"
        assert payload["meta"]["surfaces"][surface_key]["status"] == "unavailable"
        assert payload["reason"] == "consultation_owner_unavailable"
        assert payload["fallback_used"] is False

    # Unconfigured URL also reports consultation_owner_unconfigured
    state["offline"] = False
    monkeypatch.delenv("PANTHEON_CONSULTATION_API_URL", raising=False)
    monkeypatch.delenv("PANTHEON_CONSULTATION_SERVICE_URL", raising=False)
    unconf = client.get("/bff/management/meetings", headers=make_headers("op-alice")).json()
    assert unconf["status"] == "unavailable"
    assert unconf["reason"] == "consultation_owner_unconfigured"


def test_private_memory_never_returned_and_trader_identity_and_label_visibility(meetings_stack):
    client, _calls, _state = meetings_stack

    # 1. Operator view:
    # - Private memory is NEVER returned
    # - Trader identity & Agora records ARE shown
    # - Anonymised round labels ARE revealed
    op_resp = client.get(
        "/bff/management/meetings/mtg-001",
        headers=make_headers("op-alice", roles=["operator"]),
    )
    assert op_resp.status_code == 200, op_resp.text
    op_text = op_resp.text
    for forbidden in (
        "NEVER_RETURN_PRIVATE_MEMORY_EXCERPT_001",
        "NEVER_RETURN_PRIVATE_FIELD_002",
        "NEVER_RETURN_PRIVATE_INTERPRETATION_003",
        "NEVER_RETURN_PRIVATE_ROUND_004",
        "NEVER_RETURN_WRITEBACK_PRIVATE_TEXT_005",
        "NEVER_RETURN_WRITEBACK_PRIVATE_TEXT_006",
    ):
        assert forbidden not in op_text

    op_data = op_resp.json()["data"]
    assert op_data["private_items_withheld"] >= 2
    assert [i["item_id"] for i in op_data["case_file_items"]] == ["cf-shared-1", "cf-agora-lock-1"]
    agora_item = next(i for i in op_data["case_file_items"] if i["item_id"] == "cf-agora-lock-1")
    assert agora_item["agora_user_id"] == "trader-alice"
    assert agora_item["account_id"] == "acct-alice-01"
    assert agora_item["raw_text"] == "Trader Alice entered early stop-loss on high VIX day"
    assert op_data["attribution_record"]["subject_id"] == "trader-alice"
    assert op_data["anonymised_labels_revealed"] is True
    assert op_data["label_reveal"] == {
        "participant:chair:momentum": "persona-alpha",
        "participant:red_team:contrarian": "persona-red-team",
    }
    assert op_data["rounds"][1]["positions"][0]["persona_id"] == "persona-alpha"
    assert op_data["writebacks"] == [
        {"persona_id": "persona-alpha", "entry_id": "pmem-wb-001"},
        {"persona_id": "persona-red-team", "entry_id": "pmem-wb-002"},
    ]

    # 2. Owning trader ("trader-alice") view:
    # - Private memory is NEVER returned
    # - Own trader identity & Agora records ARE shown
    # - Anonymised round labels are NOT revealed (only to operators)
    alice_resp = client.get(
        "/bff/meetings/mtg-001",
        headers=make_headers(
            "trader-alice",
            roles=["trader"],
            extra_claims={"agora_user_id": "trader-alice"},
        ),
    )
    assert alice_resp.status_code == 200, alice_resp.text
    alice_data = alice_resp.json()["data"]
    assert "NEVER_RETURN_PRIVATE" not in alice_resp.text
    assert [i["item_id"] for i in alice_data["case_file_items"]] == ["cf-shared-1", "cf-agora-lock-1"]
    assert alice_data["attribution_record"]["agora_user_id"] == "trader-alice"
    servant_p = next(p for p in alice_data["participants"] if p["persona_id"] == "servant-alice")
    assert servant_p["agora_user_id"] == "trader-alice"
    # Anonymised labels must NOT be revealed to trader
    assert alice_data["anonymised_labels_revealed"] is False
    assert alice_data["label_reveal"] is None
    for rnd in alice_data["rounds"]:
        for pos in rnd["positions"]:
            assert "persona_id" not in pos
            assert "revealed_persona_id" not in pos

    # 3. Another trader ("trader-bob") or non-operator viewer:
    # - Cannot see trader-alice's identity or Agora records
    # - Cannot see anonymised label reveal
    bob_resp = client.get(
        "/bff/meetings/mtg-001",
        headers=make_headers(
            "trader-bob",
            roles=["trader"],
            extra_claims={"agora_user_id": "trader-bob"},
        ),
    )
    assert bob_resp.status_code == 200, bob_resp.text
    bob_data = bob_resp.json()["data"]
    assert "trader-alice" not in bob_resp.text
    assert "acct-alice-01" not in bob_resp.text
    assert [i["item_id"] for i in bob_data["case_file_items"]] == ["cf-shared-1"]
    assert bob_data["attribution_record"] is None
    assert bob_data["anonymised_labels_revealed"] is False
    assert bob_data["label_reveal"] is None


def test_operator_convene_route_write_gated_and_forwards_idempotency_key(meetings_stack):
    client, calls, _state = meetings_stack

    payload = {
        "meeting_type": "MT-3",
        "topic": "Evaluate cross-persona volatility breakout candidates",
        "participants": ["persona-alpha", "persona-beta", "persona-red-team"],
        "evidence_refs": ["hyp-card-01"],
    }

    # 1. Unauthenticated -> 401
    assert client.post("/bff/management/meetings", json=payload).status_code == 401

    # 2. Viewer or trader (non-operator) -> 403
    for non_op_role in (["viewer"], ["trader"]):
        denied = client.post(
            "/bff/management/meetings",
            headers=make_headers("user-1", roles=non_op_role, idempotency_key="idem-deny"),
            json=payload,
        )
        assert denied.status_code == 403, denied.text

    # 3. Missing Idempotency-Key -> 400
    no_idem = client.post(
        "/bff/management/meetings",
        headers=make_headers("op-alice", roles=["operator"]),
        json=payload,
    )
    assert no_idem.status_code == 400, no_idem.text

    # 4. Spoofed convened_by operator id -> 403
    spoofed = client.post(
        "/bff/management/meetings",
        headers=make_headers("op-alice", roles=["operator"], idempotency_key="idem-spoof"),
        json={**payload, "convened_by": {"kind": "operator", "id": "op-other"}},
    )
    assert spoofed.status_code == 403, spoofed.text

    # 5. Valid operator convene forwards to consultation owner with Idempotency-Key
    calls.clear()
    ok = client.post(
        "/bff/management/meetings",
        headers=make_headers("op-alice", roles=["operator"], idempotency_key="idem-convene-001"),
        json=payload,
    )
    assert ok.status_code == 202, ok.text
    ok_body = ok.json()
    assert ok_body["meeting_id"] == "mtg-conv-001"
    assert ok_body["data"]["status"] == "convened"
    assert ok_body["data"]["convened_by"] == {"kind": "operator", "id": "op-alice"}
    assert len(calls) == 1
    method, path, _query, sent_hdrs, sent_body = calls[0]
    assert method == "POST"
    assert path == "/api/consult/meetings"
    assert sent_hdrs["idempotency-key"] == "idem-convene-001"
    assert sent_body["convened_by"] == {"kind": "operator", "id": "op-alice"}
    assert sent_body["meeting_type"] == "MT-3"

    # Replay with same Idempotency-Key returns same meeting_id
    replay = client.post(
        "/bff/meetings/convene",
        headers=make_headers("op-alice", roles=["operator"], idempotency_key="idem-convene-001"),
        json=payload,
    )
    assert replay.status_code == 202
    assert replay.json()["meeting_id"] == "mtg-conv-001"


def test_200_meetings_reads_complete_within_management_gate_budget(meetings_stack):
    client, calls, state = meetings_stack
    state["meetings"] = [
        _sample_meeting_summary(i, status="closed" if i % 2 == 0 else "open")
        for i in range(1, 201)
    ]
    state["proposals"] = [
        {
            "proposal_id": f"prop-{i:03d}",
            "meeting_id": f"mtg-{i:03d}",
            "kind": "research_demand",
            "sponsor_persona_id": "persona-alpha",
            "downstream": {"target": "research_demand", "id": f"rd-{i:03d}", "status": "pending"},
        }
        for i in range(1, 201)
    ]

    budget = _management_read_timeout_seconds()
    hdrs = make_headers("op-alice", roles=["operator"])

    for route in (
        "/bff/management/meetings?limit=200",
        "/bff/management/meetings/proposals",
        "/bff/management/meetings/catalogue",
        "/bff/management/meetings/mtg-001",
    ):
        calls.clear()
        t0 = time.perf_counter()
        resp = client.get(route, headers=hdrs)
        elapsed = time.perf_counter() - t0
        assert resp.status_code == 200, (route, resp.text)
        assert elapsed < budget, f"{route} took {elapsed:.3f}s >= budget {budget:.3f}s"
        assert len(calls) == 1, f"{route} must make exactly 1 owner HTTP call, made {len(calls)}"


def test_operator_review_route_enforces_role_tenant_validation_idempotency_and_failure_distinction(
    meetings_stack,
):
    client, calls, state = meetings_stack

    # Configure mtg-002 as a closed REVIEW meeting with legacy null unreadable_reason
    state["details"]["mtg-002"]["status"] = "closed"
    state["details"]["mtg-002"]["outcome"] = "REVIEW"
    state["details"]["mtg-002"]["synthesis"] = {
        "proposals": [],
        "tally": {"voting": False, "binding": False, "by_stance": {"request_more_research": 2}},
        "dissent_memo_id": "memo-dissent-002",
        "outcome": "REVIEW",
        "unreadable_reason": None,
        "summary": "Synthesis empty or unreadable; recorded as REVIEW with no proposal.",
    }

    # 1. Unauthenticated -> 401
    assert (
        client.post(
            "/bff/management/meetings/mtg-002/review",
            json={"note": "Unauthenticated attempt"},
        ).status_code
        == 401
    )

    # 2. Disallowed roles (viewer, trader) -> 403
    for non_op_role in (["viewer"], ["trader"]):
        denied = client.post(
            "/bff/management/meetings/mtg-002/review",
            headers=make_headers("user-1", roles=non_op_role),
            json={"note": "Role denied attempt"},
        )
        assert denied.status_code == 403, denied.text

    # 3. Cross-tenant header mismatch -> 403
    cross_tenant_hdrs = {
        **make_headers("op-alice", tenant="tenant-a", roles=["operator"]),
        "X-Tenant-Id": "tenant-b",
    }
    assert (
        client.post(
            "/bff/management/meetings/mtg-002/review",
            headers=cross_tenant_hdrs,
            json={"note": "Cross-tenant attempt"},
        ).status_code
        == 403
    )

    # 4. Empty note or oversize note (>2000 chars) -> 422
    assert (
        client.post(
            "/bff/management/meetings/mtg-002/review",
            headers=make_headers("op-alice", roles=["operator"]),
            json={"note": "   "},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/bff/management/meetings/mtg-002/review",
            headers=make_headers("op-alice", roles=["operator"]),
            json={"note": "n" * 2001},
        ).status_code
        == 422
    )

    # 5. Non-REVIEW meeting (mtg-001 has PROPOSALS_ISSUED) -> 409
    non_review = client.post(
        "/bff/management/meetings/mtg-001/review",
        headers=make_headers("op-alice", roles=["operator"]),
        json={"note": "Trying to review a non-REVIEW meeting"},
    )
    assert non_review.status_code == 409, non_review.text
    assert non_review.json()["error"]["code"] == "RESOURCE_CONFLICT"

    # 6. Unknown meeting_id -> 404
    not_found = client.post(
        "/bff/management/meetings/mtg-unknown-404/review",
        headers=make_headers("op-alice", roles=["operator"]),
        json={"note": "Missing meeting"},
    )
    assert not_found.status_code == 404, not_found.text

    # 7. Failed write when consultation owner is offline -> 503 (distinguishes failed writes from success)
    state["offline"] = True
    failed_write = client.post(
        "/bff/management/meetings/mtg-002/review",
        headers=make_headers("op-alice", roles=["operator"]),
        json={"note": "Attempt during outage"},
    )
    assert failed_write.status_code == 503
    state["offline"] = False

    # 8. Valid operator review succeeds, persists operator_review, and preserves original synthesis & 0 proposals
    calls.clear()
    note = "Two participants requested more research twice; closed review with zero proposals."
    ok = client.post(
        "/bff/management/meetings/mtg-002/review",
        headers=make_headers("op-alice", roles=["operator"], idempotency_key="idem-rev-002"),
        json={"note": note},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["replayed"] is False
    assert body["operator_review"] == {
        "status": "reviewed",
        "disposition": "closed_no_proposal",
        "note": note,
        "reviewed_by": "op-alice",
        "reviewed_at": "2026-11-21T12:15:00Z",
        "cause": "unknown",
        "unreadable_reason": "unknown",
    }
    assert body["data"]["operator_review"] == body["operator_review"]
    assert body["data"]["outcome"] == "REVIEW"
    assert body["data"]["synthesis"]["outcome"] == "REVIEW"
    assert body["data"]["synthesis"]["proposals"] == []
    assert body["data"]["synthesis"]["unreadable_reason"] == "unknown"
    assert "cf-shared-1" in body["data"]["evidence_refs"]

    # 9. Fresh GET detail returns the persisted operator_review
    detail_get = client.get(
        "/bff/management/meetings/mtg-002",
        headers=make_headers("op-alice", roles=["operator"]),
    )
    assert detail_get.status_code == 200
    assert detail_get.json()["data"]["operator_review"] == body["operator_review"]

    # 10. Repeated identical submission returns the same disposition (replayed=True)
    replay = client.post(
        "/bff/management/meetings/mtg-002/review",
        headers=make_headers("op-alice", roles=["operator"]),
        json={"note": note},
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["operator_review"] == body["operator_review"]

    # 11. Concurrent/conflicting submission by another operator returns 409 without overwriting op-alice
    conflict = client.post(
        "/bff/management/meetings/mtg-002/review",
        headers=make_headers("op-bob", roles=["operator"]),
        json={"note": "Other operator trying to overwrite"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "RESOURCE_CONFLICT"


def test_app_factory_mounts_meetings_routes():
    app = compose_bff_app(dependency_resolver=resolve_with_stand_ins)
    route_set = get_canonical_bff_route_set(app)
    for expected in (
        ("GET", "/bff/management/meetings"),
        ("GET", "/bff/management/meetings/proposals"),
        ("GET", "/bff/management/meetings/catalogue"),
        ("GET", "/bff/management/meetings/{meeting_id}"),
        ("POST", "/bff/management/meetings"),
        ("POST", "/bff/management/meetings/{meeting_id}/review"),
        ("GET", "/bff/meetings"),
        ("POST", "/bff/meetings"),
        ("POST", "/bff/meetings/{meeting_id}/review"),
    ):
        assert expected in route_set, f"Missing mounted route {expected}"
