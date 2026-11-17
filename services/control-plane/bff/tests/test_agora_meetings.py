"""HTTP and contract tests for Agora participation in deliberative persona meetings.

Covers Task AGORA-MEETINGS-20261115 (SD-PERSONA-MEETINGS-20261115 §11.2 / §12.8, C1/C2):
1. A trader can convene a deliberative meeting from a Strategy Workshop session
   through a BFF route that forwards to the consultation owner; participants
   come from the existing Agora eligibility plus the trader's own private
   servant, and another user's servant is refused.
2. The committee read routes list gate panels and deliberative meetings with
   their rounds from the owner, and the BFF keeps no meeting store.
3. Proposals without a trading-persona sponsor become Agora candidate decisions
   on the existing approval path; nothing from Agora trades, moves capital, or
   writes canonical state.
4. HTTP tests cover convening, refusal, and reads with the consultation owner
   doubled at its HTTP edge.
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.candidate_decisions.models import canonical_sha256
from services.control_plane.bff.agora.candidate_decisions.router import (
    create_candidate_decision_router as create_base_candidate_decision_router,
)
from services.control_plane.bff.agora.governance.store import ProposalStore
from services.control_plane.bff.agora.interaction.candidate_decisions.router import (
    create_candidate_decision_router,
    read_downstream_status,
    route_meeting_candidate_decisions,
)
from services.control_plane.bff.agora.interaction.provider import authority_boundary
from services.control_plane.bff.agora.interaction.router import create_interaction_router
from services.control_plane.bff.agora.interaction.store import InteractionLifecycleStore
from services.control_plane.bff.agora.strategy_workshop.store import MemoryWorkshopStore
from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.core.app_factory import (
    build_bff_app,
    mount_agora_review_routers,
)
from services.runtime_auth_inbound import encode_jwt_hs256

SECRET = "agora-meetings-http-edge-test-secret"
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
NOW_ISO = "2026-11-16T12:00:00Z"


def _auth_headers(
    user: str = "alice",
    tenant: str = "tenant-a",
    *,
    roles: list[str] | None = None,
    idempotency_key: str | None = "idem-meeting-1",
) -> dict[str, str]:
    token = encode_jwt_hs256(
        {
            "sub": user,
            "roles": roles or ["operator"],
            "tenant_id": tenant,
            "allowed_tenants": [tenant],
            "exp": 4102444800,
        },
        secret=SECRET,
    )
    out = {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant}
    if idempotency_key is not None:
        out["Idempotency-Key"] = idempotency_key
    return out


class _FakeReadStore:
    def __init__(self) -> None:
        self.personas: list[dict[str, Any]] = [
            {
                "persona_id": "persona-inst-1",
                "display_name": "Institutional Momentum",
                "tenant_id": "tenant-a",
                "lifecycle_state": "paper_running",
                "status": "active",
                "environment_ceiling": "paper",
                "persona_version": "v1",
                "capability_snapshot_id": "cap-inst-1",
                "archetype": "momentum",
                "strategy_family": "trend",
            },
            {
                "persona_id": "persona-advisor-1",
                "display_name": "Agora Macro Advisor",
                "tenant_id": "tenant-a",
                "lifecycle_state": "consultable",
                "status": "active",
                "environment_ceiling": "paper",
                "persona_version": "v2",
                "capability_snapshot_id": "cap-advisor-1",
                "archetype": "agora_advisor",
                "strategy_family": "macro",
                "metadata": {"persona_class": "agora_advisor"},
            },
            {
                "persona_id": "agora-servant-alice",
                "display_name": "Alice Private Servant",
                "tenant_id": "tenant-a",
                "lifecycle_state": "active",
                "status": "active",
                "environment_ceiling": "paper",
                "persona_version": "v1",
                "capability_snapshot_id": "cap-servant-alice",
                "archetype": "agora_servant",
                "mandate": "user_private_agora_servant",
                "strategy_family": "agora_servant",
                "metadata": {
                    "persona_class": "agora_servant",
                    "owner_scope": "user_private",
                    "visibility_scope": "private",
                    "memory_scope": "private_user",
                    "agora_user_id": "alice",
                    "environment_ceiling": "paper",
                },
            },
            {
                "persona_id": "agora-servant-bob",
                "display_name": "Bob Private Servant",
                "tenant_id": "tenant-a",
                "lifecycle_state": "active",
                "status": "active",
                "environment_ceiling": "paper",
                "persona_version": "v1",
                "capability_snapshot_id": "cap-servant-bob",
                "archetype": "agora_servant",
                "mandate": "user_private_agora_servant",
                "strategy_family": "agora_servant",
                "metadata": {
                    "persona_class": "agora_servant",
                    "owner_scope": "user_private",
                    "visibility_scope": "private",
                    "memory_scope": "private_user",
                    "agora_user_id": "bob",
                    "environment_ceiling": "paper",
                },
            },
            {
                "persona_id": "persona-archived",
                "display_name": "Archived Persona",
                "tenant_id": "tenant-a",
                "lifecycle_state": "retired",
                "status": "archived",
                "environment_ceiling": "paper",
                "persona_version": "v1",
                "capability_snapshot_id": "cap-archived",
            },
            {
                "persona_id": "persona-foreign-tenant",
                "display_name": "Foreign Tenant Persona",
                "tenant_id": "tenant-b",
                "lifecycle_state": "paper_running",
                "status": "active",
                "environment_ceiling": "paper",
                "persona_version": "v1",
                "capability_snapshot_id": "cap-foreign",
            },
        ]
        self.snapshots: dict[str, dict[str, Any]] = {
            "cap-inst-1": {
                "snapshot_id": "cap-inst-1",
                "persona_id": "persona-inst-1",
                "capabilities": ["persona_opinion"],
            },
            "cap-advisor-1": {
                "snapshot_id": "cap-advisor-1",
                "persona_id": "persona-advisor-1",
                "capabilities": ["persona_opinion"],
            },
            "cap-servant-alice": {
                "snapshot_id": "cap-servant-alice",
                "persona_id": "agora-servant-alice",
                "capabilities": ["persona_opinion"],
            },
            "cap-servant-bob": {
                "snapshot_id": "cap-servant-bob",
                "persona_id": "agora-servant-bob",
                "capabilities": ["persona_opinion"],
            },
            "cap-foreign": {
                "snapshot_id": "cap-foreign",
                "persona_id": "persona-foreign-tenant",
                "capabilities": ["persona_opinion"],
            },
        }
        self.approvals: dict[str, dict[str, Any]] = {}
        self.approval_store_available = True

    def list_personas(self, include_market_persona_defaults: bool = True) -> list[dict[str, Any]]:
        return list(self.personas)

    def get_capability_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        return self.snapshots.get(snapshot_id)

    def get_capability_snapshot_for_persona(self, persona_id: str) -> dict[str, Any] | None:
        for snap in self.snapshots.values():
            if snap.get("persona_id") == persona_id:
                return snap
        return None

    def get_canonical_approval_decision_readback(self, decision_id: str) -> dict[str, Any]:
        if decision_id == "__candidate_readiness_probe__":
            return {
                "available": self.approval_store_available,
                "record": None,
                "source": "canonical.approval_decision",
            }
        return {
            "available": self.approval_store_available,
            "record": self.approvals.get(decision_id),
            "source": "canonical.approval_decision",
        }


@pytest.fixture
def agora_meetings_stack(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "")
    monkeypatch.delenv("PANTHEON_BFF_AUTH_STUB", raising=False)
    monkeypatch.delenv("PANTHEON_BFF_TENANT_ID", raising=False)
    monkeypatch.delenv("PANTHEON_BFF_DEFAULT_TENANT_ID", raising=False)
    monkeypatch.setenv("PANTHEON_CONSULTATION_API_URL", "http://consultation.test")
    monkeypatch.setenv("CONSULTATION_SERVICE_TOKEN", "test-only-consult-service-token")
    monkeypatch.setenv("AGORA_PROPOSAL_STORE_BACKEND", "off")
    monkeypatch.setenv("AGORA_INTERACTION_STORE_BACKEND", "off")
    monkeypatch.setenv("AGORA_CANDIDATE_DECISION_STORE_BACKEND", "off")

    read_store = _FakeReadStore()
    workshop_store = MemoryWorkshopStore()
    workshop_store.create_session({
        "workshop_id": "ws-alice-1",
        "tenant_id": "tenant-a",
        "user_id": "alice",
        "strategy_id": "strat-alpha",
        "active_strategy_spec_registry_id": "spec-v1",
        "selected_version_id": "ver-1",
        "status": "open",
    })
    workshop_store.create_session({
        "workshop_id": "ws-bob-1",
        "tenant_id": "tenant-a",
        "user_id": "bob",
        "strategy_id": "strat-beta",
        "active_strategy_spec_registry_id": "spec-v2",
        "selected_version_id": "ver-2",
        "status": "open",
    })

    proposal_store = ProposalStore(backend="off")
    interaction_store = InteractionLifecycleStore.from_governance_store(proposal_store)

    owner_state: dict[str, Any] = {
        "offline": False,
        "convene_error": None,
        "panels": [
            {
                "request_id": "panel-gate-1",
                "tenant_id": "tenant-a",
                "consultation_type": "persona_panel",
                "request_type": "persona_policy",
                "from_persona_id": "persona-inst-1",
                "target_type": "trial_admission",
                "target_id": "cand-1",
                "status": "published",
                "created_at": "2026-11-16T09:00:00Z",
            }
        ],
        "panel_transcripts": {
            "panel-gate-1": {
                "request_id": "panel-gate-1",
                "events": [
                    {
                        "event_id": "evt-panel-1",
                        "event_type": "persona_contribution",
                        "actor": {"actor_type": "persona", "actor_id": "persona-risk-officer"},
                        "content": {
                            "role": "risk_officer",
                            "recommendation": "approve",
                            "claim": "Drawdown bounds satisfied",
                            "evidence_refs": ["artifact://risk-check-1"],
                        },
                    }
                ],
            }
        },
        "meetings": {
            "mtg-delib-1": {
                "meeting_id": "mtg-delib-1",
                "meeting_type": "MT-3",
                "status": "closed",
                "outcome": "completed",
                "chair_persona_id": "persona-inst-1",
                "convened_by": {"kind": "agora_user", "id": "alice"},
                "subject_ref": "strat-alpha",
                "participants": [
                    {"persona_id": "persona-inst-1", "role": "chair", "origin": "institutional"},
                    {"persona_id": "persona-advisor-1", "role": "advisor", "origin": "agora_advisor"},
                    {
                        "persona_id": "agora-servant-alice",
                        "role": "servant",
                        "origin": "private_servant",
                        "agora_user_id": "alice",
                    },
                ],
                "round": 2,
                "created_at": "2026-11-16T10:00:00Z",
                "closed_at": "2026-11-16T10:06:00Z",
                "agenda": "Turn volatility regime findings into testable ideas",
                "case_file_items": [
                    {
                        "item_id": "cf-shared-1",
                        "kind": "research_finding",
                        "excerpt": "Intraday range expansion precedes trend continuation.",
                        "relevance_scope": "committee",
                    },
                    {
                        "item_id": "cf-private-1",
                        "kind": "private_memory",
                        "excerpt": "SECRET_PRIVATE_MEMORY_NEVER_RETURN",
                        "relevance_scope": "persona_private",
                    },
                ],
                "pre_read": [
                    {
                        "persona_id": "persona-inst-1",
                        "interpretation": "Breakout filter aligns with current trend book.",
                        "scope": "committee",
                    },
                    {
                        "persona_id": "agora-servant-alice",
                        "interpretation": "SECRET_SERVANT_PRIVATE_INTERPRETATION",
                        "memory_scope": "private_user",
                    },
                ],
                "rounds": [
                    {
                        "round": 1,
                        "positions": [
                            {
                                "label": "chair_trend",
                                "persona_id": "persona-inst-1",
                                "role": "chair",
                                "style": "trend",
                                "stance": "support",
                                "confidence": 0.84,
                                "citations": ["cf-shared-1"],
                                "changed": False,
                            },
                            {
                                "label": "advisor_macro",
                                "persona_id": "persona-advisor-1",
                                "role": "advisor",
                                "style": "macro",
                                "stance": "support",
                                "confidence": 0.78,
                                "citations": ["cf-shared-1"],
                                "changed": False,
                            },
                            {
                                "label": "servant_operator_guided",
                                "persona_id": "agora-servant-alice",
                                "role": "servant",
                                "style": "operator-guided",
                                "stance": "conditional",
                                "confidence": 0.71,
                                "citations": ["cf-shared-1"],
                                "changed": False,
                            },
                        ],
                    },
                    {
                        "round": 2,
                        "positions": [
                            {
                                "label": "chair_trend",
                                "persona_id": "persona-inst-1",
                                "role": "chair",
                                "style": "trend",
                                "stance": "support",
                                "confidence": 0.86,
                                "citations": ["cf-shared-1"],
                                "changed": False,
                            },
                            {
                                "label": "advisor_macro",
                                "persona_id": "persona-advisor-1",
                                "role": "advisor",
                                "style": "macro",
                                "stance": "support",
                                "confidence": 0.80,
                                "citations": ["cf-shared-1"],
                                "changed": False,
                            },
                            {
                                "label": "servant_operator_guided",
                                "persona_id": "agora-servant-alice",
                                "role": "servant",
                                "style": "operator-guided",
                                "stance": "support",
                                "confidence": 0.79,
                                "citations": ["cf-shared-1"],
                                "changed": True,
                            },
                        ],
                    },
                ],
                "synthesis": {
                    "proposals": [
                        {
                            "proposal_id": "prop-sponsored-1",
                            "meeting_id": "mtg-delib-1",
                            "kind": "research_demand",
                            "sponsor_persona_id": "persona-inst-1",
                            "payload": {
                                "title": "Sponsored volatility demand",
                                "proposed_value": {"window": 20},
                            },
                            "citations": ["cf-shared-1"],
                            "downstream": {
                                "target": "research_demand",
                                "id": "rd-101",
                                "status": "open",
                            },
                        },
                        {
                            "proposal_id": "prop-unsponsored-1",
                            "meeting_id": "mtg-delib-1",
                            "kind": "agora_candidate_decision",
                            "sponsor_persona_id": None,
                            "payload": {
                                "measure_type": "strategy_parameter_change",
                                "target": {
                                    "kind": "strategy",
                                    "id": "strat-alpha",
                                    "version": "spec-v1",
                                    "path": "entry.volatility_threshold",
                                },
                                "current_value": 1.5,
                                "proposed_value": 1.25,
                                "rationale": "Agora workshop meeting proposed tighter volatility entry threshold.",
                                "expected_benefit": "Capture earlier breakout continuation in paper validation.",
                                "adverse_scenarios": ["Higher false breakout rate in choppy sessions."],
                                "confidence": 0.79,
                            },
                            "citations": ["cf-shared-1"],
                        },
                    ],
                    "tally": {"support": 2.45, "oppose": 0.0},
                    "dissent_memo_id": "memo-dissent-1",
                    "outcome": "completed",
                },
                "writebacks": [
                    {"persona_id": "persona-inst-1", "entry_id": "pmem-1"},
                    {"persona_id": "agora-servant-alice", "entry_id": "pmem-2"},
                ],
                "usage": {"rounds": 2, "calls": 7},
            }
        },
        "catalogue": {
            "types": [
                {
                    "meeting_type": "MT-1",
                    "enabled": True,
                    "cadence": "weekly",
                    "roster_rule": "owner_peer_risk",
                    "budget": {"max_participants": 5, "max_rounds": 2},
                },
                {
                    "meeting_type": "MT-3",
                    "enabled": True,
                    "cadence": "weekly",
                    "roster_rule": "enrolled_trading_plus_red_team",
                    "budget": {"max_participants": 5, "max_rounds": 2},
                },
                {
                    "meeting_type": "MT-4",
                    "enabled": True,
                    "cadence": "event_driven",
                    "roster_rule": "attribution_subject_plus_risk_red_team",
                    "budget": {"max_participants": 5, "max_rounds": 2},
                },
            ],
            "schedule": [
                {
                    "persona_id": "persona-inst-1",
                    "meeting_type": "MT-1",
                    "last_run_at": "2026-11-10T00:00:00Z",
                    "next_due_at": "2026-11-17T00:00:00Z",
                }
            ],
            "refusals": [
                {
                    "persona_id": "persona-inst-1",
                    "meeting_type": "MT-2",
                    "reason": "insufficient_participants",
                    "at": "2026-11-15T08:00:00Z",
                }
            ],
        },
        "convene_by_key": {},
    }
    calls: list[dict[str, Any]] = []

    def _reply(status: int, payload: Any):
        raw = json.dumps(payload).encode("utf-8")
        if status >= 400:
            raise HTTPError("http://consultation.test", status, "owner error", {}, io.BytesIO(raw))
        res = io.BytesIO(raw)
        res.status = status
        return res

    def transport(req, timeout=10):
        u = urlsplit(req.full_url)
        assert u.hostname == "consultation.test", f"Unexpected host: {req.full_url}"
        headers = {k.lower(): v for k, v in req.header_items()}
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        calls.append({
            "method": req.method,
            "path": u.path,
            "query": parse_qs(u.query),
            "headers": headers,
            "body": body,
        })
        if owner_state["offline"]:
            raise URLError("consultation owner offline")
        assert headers.get("authorization") == "Bearer test-only-consult-service-token"
        assert headers.get("x-pantheon-tenant-id") == "tenant-a"

        if req.method == "POST" and u.path == "/api/consult/meetings":
            if owner_state["convene_error"] is not None:
                err_status, err_payload = owner_state["convene_error"]
                return _reply(err_status, err_payload)
            idem = headers.get("idempotency-key")
            assert idem, "Consultation convene requires Idempotency-Key header"
            if idem in owner_state["convene_by_key"]:
                return _reply(202, owner_state["convene_by_key"][idem])
            meeting_id = f"mtg-convened-{len(owner_state['convene_by_key']) + 1}"
            created = {"meeting_id": meeting_id, "status": "convened"}
            owner_state["convene_by_key"][idem] = created
            owner_state["meetings"][meeting_id] = {
                "meeting_id": meeting_id,
                "meeting_type": body["meeting_type"],
                "status": "convened",
                "outcome": None,
                "chair_persona_id": body["participants"][0],
                "convened_by": body["convened_by"],
                "subject_ref": body.get("subject_ref"),
                "participants": [
                    {
                        "persona_id": pid,
                        "role": "servant" if "servant" in pid else "participant",
                        "origin": (
                            "private_servant"
                            if "servant" in pid
                            else "agora_advisor"
                            if "advisor" in pid
                            else "institutional"
                        ),
                        **({"agora_user_id": body["convened_by"]["id"]} if "servant" in pid else {}),
                    }
                    for pid in (body.get("participants") or [])
                ],
                "round": 0,
                "created_at": NOW_ISO,
                "closed_at": None,
                "agenda": body["topic"],
                "case_file_items": [],
                "pre_read": [],
                "rounds": [],
                "synthesis": None,
                "writebacks": [],
                "usage": {"rounds": 0, "calls": 0},
            }
            return _reply(202, created)

        if req.method == "GET" and u.path == "/api/consult/requests":
            return _reply(200, owner_state["panels"])

        if req.method == "GET" and u.path.startswith("/api/consult/requests/"):
            rest = u.path.removeprefix("/api/consult/requests/")
            if rest.endswith("/transcript"):
                pid = rest.removesuffix("/transcript")
                if pid in owner_state["panel_transcripts"]:
                    return _reply(200, owner_state["panel_transcripts"][pid])
                return _reply(404, {"detail": "transcript not found"})
            for p in owner_state["panels"]:
                if p["request_id"] == rest:
                    return _reply(200, p)
            return _reply(404, {"detail": "request not found"})

        if req.method == "GET" and u.path == "/api/consult/meetings":
            items = [
                {
                    "meeting_id": m["meeting_id"],
                    "meeting_type": m["meeting_type"],
                    "status": m["status"],
                    "outcome": m.get("outcome"),
                    "chair_persona_id": m.get("chair_persona_id"),
                    "convened_by": m.get("convened_by"),
                    "participants": m.get("participants", []),
                    "round": m.get("round", len(m.get("rounds") or [])),
                    "created_at": m.get("created_at"),
                    "closed_at": m.get("closed_at"),
                }
                for m in owner_state["meetings"].values()
            ]
            return _reply(200, {"items": items, "next_cursor": None})

        if req.method == "GET" and u.path == "/api/consult/meetings/proposals":
            q = parse_qs(u.query)
            target_mid = (q.get("meeting_id") or [None])[0]
            proposals = []
            for m in owner_state["meetings"].values():
                if target_mid and m["meeting_id"] != target_mid:
                    continue
                syn = m.get("synthesis") or {}
                proposals.extend(syn.get("proposals") or [])
            return _reply(200, {"items": proposals})

        if req.method == "GET" and u.path == "/api/consult/meetings/catalogue":
            return _reply(200, owner_state["catalogue"])

        if req.method == "GET" and u.path.startswith("/api/consult/meetings/"):
            mid = u.path.removeprefix("/api/consult/meetings/")
            if mid in owner_state["meetings"]:
                return _reply(200, owner_state["meetings"][mid])
            return _reply(404, {"detail": "meeting not found"})

        raise AssertionError(f"Unhandled consultation request: {req.method} {u.path}")

    monkeypatch.setattr("urllib.request.urlopen", transport)

    app = build_bff_app()
    mount_agora_review_routers(
        app,
        extract_identity=extract_identity,
        require_read_role=require_read_role,
        require_write_role=require_operator_role,
        utc_now=lambda: NOW_ISO,
    )
    interaction_router = create_interaction_router(
        extract_identity=extract_identity,
        require_read_role=require_read_role,
        require_write_role=require_operator_role,
        bff_error=bff_error,
        utc_now=lambda: NOW_ISO,
        get_read_store=lambda: read_store,
        workshop_store=workshop_store,
        proposal_store=proposal_store,
        interaction_store=interaction_store,
    )
    interaction_router.candidate_service.clock = lambda: NOW
    interaction_router.candidate_service.validation_adapter.clock = lambda: NOW
    app.include_router(interaction_router)
    app.include_router(
        create_candidate_decision_router(
            service=interaction_router.candidate_service,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_write_role=require_operator_role,
            bff_error=bff_error,
            utc_now=lambda: NOW_ISO,
            get_read_store=lambda: read_store,
        )
    )
    return TestClient(app), calls, owner_state, read_store, interaction_router.candidate_service


def test_trader_convenes_deliberative_meeting_from_workshop_with_eligible_personas_and_own_servant(
    agora_meetings_stack,
) -> None:
    client, calls, _, _, _ = agora_meetings_stack
    resp = client.post(
        "/bff/agora/workshops/ws-alice-1/meetings",
        headers=_auth_headers("alice", idempotency_key="convene-alice-1"),
        json={
            "meeting_type": "MT-3",
            "topic": "Discuss breakout entry filter with my private servant",
        },
    )
    assert resp.status_code == 202, resp.text
    payload = resp.json()
    assert payload["meeting_id"] == "mtg-convened-1"
    assert payload["status"] == "convened"
    assert payload["execution_authority"] == "none"
    assert payload["environment_ceiling"] == "paper"

    data = payload["data"]
    assert data["convened_by"] == {"kind": "agora_user", "id": "alice"}
    assert data["subject_ref"] == "strat-alpha"
    assert data["authority"] == authority_boundary()
    # Includes eligible institutional & advisor personas plus Alice's own servant,
    # and never includes Bob's servant, archived personas, or foreign-tenant personas.
    assert "persona-inst-1" in data["participants"]
    assert "persona-advisor-1" in data["participants"]
    assert "agora-servant-alice" in data["participants"]
    assert "agora-servant-bob" not in data["participants"]
    assert "persona-archived" not in data["participants"]
    assert "persona-foreign-tenant" not in data["participants"]

    assert len(calls) == 1
    sent = calls[0]
    assert sent["method"] == "POST"
    assert sent["path"] == "/api/consult/meetings"
    assert sent["headers"]["idempotency-key"] == "convene-alice-1"
    assert sent["body"]["convened_by"] == {"kind": "agora_user", "id": "alice"}
    assert sent["body"]["meeting_type"] == "MT-3"
    assert "agora-servant-alice" in sent["body"]["participants"]
    assert "agora-servant-bob" not in sent["body"]["participants"]

    # Idempotent replay forwards the same Idempotency-Key and returns the same meeting_id
    replay = client.post(
        "/bff/agora/interactions/meetings",
        headers=_auth_headers("alice", idempotency_key="convene-alice-1"),
        json={
            "workshop_id": "ws-alice-1",
            "meeting_type": "MT-3",
            "topic": "Discuss breakout entry filter with my private servant",
        },
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["meeting_id"] == "mtg-convened-1"


def test_workshop_meeting_convening_refuses_another_users_servant_and_invalid_requests(
    agora_meetings_stack,
) -> None:
    client, calls, owner_state, read_store, _ = agora_meetings_stack

    # 1. Another user's private servant is refused with 403 before any owner call
    refused_other_servant = client.post(
        "/bff/agora/workshops/ws-alice-1/meetings",
        headers=_auth_headers("alice", idempotency_key="refuse-bob-servant"),
        json={
            "meeting_type": "MT-1",
            "topic": "Try to invite Bob's private servant",
            "participants": ["persona-inst-1", "agora-servant-bob"],
        },
    )
    assert refused_other_servant.status_code == 403, refused_other_servant.text
    assert "persona_user_mismatch" in refused_other_servant.text
    assert calls == []

    # 2. Another user's workshop session is refused with 403
    refused_other_workshop = client.post(
        "/bff/agora/workshops/ws-bob-1/meetings",
        headers=_auth_headers("alice", idempotency_key="refuse-bob-ws"),
        json={
            "meeting_type": "MT-1",
            "topic": "Try to convene from Bob's workshop",
        },
    )
    assert refused_other_workshop.status_code == 403, refused_other_workshop.text
    assert calls == []

    # 3. Spoofed convened_by identity is refused with 403
    refused_spoofed = client.post(
        "/bff/agora/workshops/ws-alice-1/meetings",
        headers=_auth_headers("alice", idempotency_key="refuse-spoof"),
        json={
            "meeting_type": "MT-1",
            "topic": "Try to spoof convened_by",
            "convened_by": {"kind": "operator", "id": "admin"},
        },
    )
    assert refused_spoofed.status_code == 403, refused_spoofed.text
    assert calls == []

    # 4. Non-existent private servant fails closed with servant_absent
    refused_missing_servant = client.post(
        "/bff/agora/workshops/ws-alice-1/meetings",
        headers=_auth_headers("alice", idempotency_key="refuse-missing-servant"),
        json={
            "meeting_type": "MT-1",
            "topic": "Missing servant",
            "participants": ["agora-servant-missing"],
        },
    )
    assert refused_missing_servant.status_code == 409, refused_missing_servant.text
    assert "servant_absent" in refused_missing_servant.text
    assert calls == []

    # 5. Ineligible (archived) participant is refused with 422
    refused_ineligible = client.post(
        "/bff/agora/workshops/ws-alice-1/meetings",
        headers=_auth_headers("alice", idempotency_key="refuse-archived"),
        json={
            "meeting_type": "MT-1",
            "topic": "Archived persona",
            "participants": ["persona-archived"],
        },
    )
    assert refused_ineligible.status_code == 422, refused_ineligible.text
    assert "participant_eligibility_failed" in refused_ineligible.text
    assert calls == []

    # 6. Gate panel type or live environment is refused with 422
    for invalid_body in (
        {"meeting_type": "persona_panel", "topic": "Not deliberative"},
        {"meeting_type": "trial_admission", "topic": "Gate panel type"},
        {"meeting_type": "MT-1", "topic": "Live forbidden", "environment": "live"},
    ):
        res = client.post(
            "/bff/agora/workshops/ws-alice-1/meetings",
            headers=_auth_headers("alice", idempotency_key="refuse-invalid"),
            json=invalid_body,
        )
        assert res.status_code == 422, res.text
    assert calls == []

    # 7. Missing Idempotency-Key is refused with 400
    no_idem = client.post(
        "/bff/agora/workshops/ws-alice-1/meetings",
        headers=_auth_headers("alice", idempotency_key=None),
        json={"meeting_type": "MT-1", "topic": "Missing idempotency"},
    )
    assert no_idem.status_code == 400, no_idem.text
    assert calls == []

    # 8. Consultation owner refusal reason is surfaced fail-closed
    owner_state["convene_error"] = (409, {"detail": {"reason": "insufficient_participants"}})
    owner_refused = client.post(
        "/bff/agora/workshops/ws-alice-1/meetings",
        headers=_auth_headers("alice", idempotency_key="owner-refusal-1"),
        json={"meeting_type": "MT-3", "topic": "Owner refuses due to roster"},
    )
    assert owner_refused.status_code == 409, owner_refused.text
    assert owner_refused.json()["reason"] == "insufficient_participants"

    # 9. Empty eligible roster fails closed with insufficient_participants
    read_store.personas.clear()
    empty_roster = client.post(
        "/bff/agora/workshops/ws-alice-1/meetings",
        headers=_auth_headers("alice", idempotency_key="empty-roster"),
        json={"meeting_type": "MT-1", "topic": "No eligible personas"},
    )
    assert empty_roster.status_code == 422, empty_roster.text
    assert "insufficient_participants" in empty_roster.text


def test_committee_reads_list_gate_panels_and_deliberative_meetings_with_rounds_and_keep_no_store(
    agora_meetings_stack,
) -> None:
    client, _, owner_state, _, _ = agora_meetings_stack

    # 1. Unified committee sessions list returns both gate panels and deliberative meetings with rounds
    sessions_resp = client.get(
        "/bff/agora/committee/sessions",
        headers=_auth_headers("alice"),
    )
    assert sessions_resp.status_code == 200, sessions_resp.text
    sessions = {item["session_id"]: item for item in sessions_resp.json()["data"]}
    assert set(sessions) == {"panel-gate-1", "mtg-delib-1"}

    gate = sessions["panel-gate-1"]
    assert gate["session_class"] == "gate_panel"
    assert gate["consultation_type"] == "persona_panel"
    assert len(gate["rounds"]) == 1
    assert gate["rounds"][0]["mode"] == "independent"

    delib = sessions["mtg-delib-1"]
    assert delib["session_class"] == "deliberative_meeting"
    assert delib["consultation_type"] == "persona_meeting"
    assert [r["round"] for r in delib["rounds"]] == [1, 2]
    assert delib["rounds"][1]["positions"][2]["changed"] is True
    assert delib["private_items_withheld"] == 2
    assert "SECRET_PRIVATE_MEMORY_NEVER_RETURN" not in sessions_resp.text
    assert "SECRET_SERVANT_PRIVATE_INTERPRETATION" not in sessions_resp.text

    # 2. Meeting detail route returns full rounds, synthesis, writebacks, and usage
    detail_resp = client.get(
        "/bff/agora/committee/meetings/mtg-delib-1",
        headers=_auth_headers("alice"),
    )
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()["data"]
    assert detail["meeting_id"] == "mtg-delib-1"
    assert len(detail["rounds"]) == 2
    assert detail["synthesis"]["tally"] == {"support": 2.45, "oppose": 0.0}
    assert len(detail["writebacks"]) == 2
    assert detail["execution_authority"] == "none"

    # 3. Proposals and catalogue read routes forward from consultation owner
    proposals_resp = client.get(
        "/bff/agora/committee/meetings/mtg-delib-1/proposals",
        headers=_auth_headers("alice"),
    )
    assert proposals_resp.status_code == 200, proposals_resp.text
    props = {p["proposal_id"]: p for p in proposals_resp.json()["data"]}
    assert props["prop-sponsored-1"]["approval_path"] == "governed_owner_entry_point"
    assert props["prop-unsponsored-1"]["approval_path"] == "agora_candidate_decision"
    assert props["prop-unsponsored-1"]["sponsor_persona_id"] is None

    catalogue_resp = client.get(
        "/bff/agora/committee/catalogue",
        headers=_auth_headers("alice"),
    )
    assert catalogue_resp.status_code == 200, catalogue_resp.text
    assert len(catalogue_resp.json()["data"]["types"]) == 3
    assert len(catalogue_resp.json()["data"]["refusals"]) == 1

    # 4. BFF keeps no meeting store: owner mutation is immediately reflected and outage fails closed
    owner_state["meetings"].pop("mtg-delib-1")
    after_remove = client.get(
        "/bff/agora/committee/meetings",
        headers=_auth_headers("alice"),
    )
    assert after_remove.status_code == 200, after_remove.text
    assert after_remove.json()["data"] == []
    assert after_remove.json()["reason"] == "no_persona_meetings"
    assert client.get(
        "/bff/agora/committee/meetings/mtg-delib-1",
        headers=_auth_headers("alice"),
    ).status_code == 404

    owner_state["offline"] = True
    outage_resp = client.get(
        "/bff/agora/committee/sessions",
        headers=_auth_headers("alice"),
    )
    assert outage_resp.status_code == 503
    assert outage_resp.json()["status"] == "unavailable"
    assert outage_resp.json()["fallback_used"] is False


def test_unsponsored_proposals_become_agora_candidate_decisions_on_existing_approval_path(
    agora_meetings_stack,
) -> None:
    client, _, owner_state, read_store, candidate_service = agora_meetings_stack

    # 1. A proposal WITH a trading-persona sponsor is refused on the Agora candidate path
    sponsored_res = client.post(
        "/bff/agora/meetings/mtg-delib-1/proposals/prop-sponsored-1/candidates",
        headers=_auth_headers("alice", idempotency_key="cand-sponsored-refuse"),
    )
    assert sponsored_res.status_code == 422, sponsored_res.text
    assert "trading-persona sponsor" in sponsored_res.text

    # 2. A proposal WITHOUT a trading-persona sponsor becomes an Agora candidate decision
    create_res = client.post(
        "/bff/agora/meetings/mtg-delib-1/proposals/prop-unsponsored-1/candidates",
        headers=_auth_headers("alice", idempotency_key="cand-unsponsored-1"),
    )
    assert create_res.status_code == 201, create_res.text
    initial_etag = create_res.headers["etag"]
    created_data = create_res.json()["data"]
    candidate = created_data["candidate"]
    assert candidate["proposal_id"] == "prop-unsponsored-1"
    assert candidate["meeting_id"] == "mtg-delib-1"
    assert candidate["sponsor_persona_id"] is None
    assert candidate["state"] == "draft"
    assert candidate["revision"] == 1
    assert candidate["proposed_value"] == 1.25
    assert candidate["execution_authority"] == "none"
    assert candidate["authority"] == authority_boundary()

    # Idempotent replay returns 200 with identical payload and ETag
    replay_res = client.post(
        "/bff/agora/meetings/mtg-delib-1/proposals/prop-unsponsored-1/candidates",
        headers=_auth_headers("alice", idempotency_key="cand-unsponsored-1"),
    )
    assert replay_res.status_code == 200, replay_res.text
    assert replay_res.headers["etag"] == initial_etag

    # 3. Walk the existing candidate-decision approval path:
    #    modify -> accept_for_review -> authoritative validation -> formal approval link
    modify_res = client.post(
        "/bff/agora/proposals/prop-unsponsored-1/candidate-decisions",
        headers={
            **_auth_headers("alice", idempotency_key="cand-modify-1"),
            "If-Match": initial_etag,
        },
        json={
            "action": "modify",
            "reason": "Refine threshold before validation",
            "expected_revision": 1,
            "expected_proposal_digest": candidate["proposal_digest"],
            "proposed_value": 1.20,
        },
    )
    assert modify_res.status_code == 200, modify_res.text
    rev2 = modify_res.json()["data"]["candidate"]
    rev2_etag = modify_res.headers["etag"]
    assert rev2["revision"] == 2
    assert rev2["proposed_value"] == 1.20
    assert rev2["state"] == "draft"

    accept_res = client.post(
        "/bff/agora/proposals/prop-unsponsored-1/candidate-decisions",
        headers={
            **_auth_headers("alice", idempotency_key="cand-accept-1"),
            "If-Match": rev2_etag,
        },
        json={
            "action": "accept_for_review",
            "reason": "Submit unsponsored meeting candidate for validation",
            "expected_revision": 2,
            "expected_proposal_digest": rev2["proposal_digest"],
        },
    )
    assert accept_res.status_code == 200, accept_res.text
    rev3 = accept_res.json()["data"]["candidate"]
    rev3_etag = accept_res.headers["etag"]
    assert rev3["revision"] == 3
    assert rev3["state"] == "review_requested"

    validate_res = client.post(
        "/bff/agora/proposals/prop-unsponsored-1/validations",
        headers={
            **_auth_headers("alice", idempotency_key="cand-validate-1"),
            "If-Match": rev3_etag,
        },
        json={
            "expected_revision": 3,
            "expected_proposal_digest": rev3["proposal_digest"],
        },
    )
    assert validate_res.status_code == 200, validate_res.text
    val_receipt = validate_res.json()["data"]["validation_receipts"][0]
    assert val_receipt["outcome"] == "passed"
    assert val_receipt["authority"] == "canonical_validation_service"

    readiness_res = client.get(
        "/bff/agora/proposals/prop-unsponsored-1/review-readiness",
        headers=_auth_headers("alice", idempotency_key=None),
    )
    assert readiness_res.status_code == 200, readiness_res.text
    assert readiness_res.json()["data"]["readiness"]["validation"]["current_passed"] is True
    assert readiness_res.json()["data"]["readiness"]["reviewer"]["can_link_formal_approval"] is True
    assert readiness_res.json()["data"]["execution_authority"] == "none"

    # Link a canonical formal rejection/approval receipt and verify zero execution or capital authority
    approval_payload = {
        "approval_decision_id": "approval-mtg-1",
        "authority": "canonical_approval_decision_store",
        "tenant_id": "tenant-a",
        "proposal_id": "prop-unsponsored-1",
        "revision": rev3["revision"],
        "proposal_digest": rev3["proposal_digest"],
        "validation_receipt_id": val_receipt["validation_receipt_id"],
        "validation_receipt_sha256": val_receipt["receipt_sha256"],
        "proposer_id": "alice",
        "reviewer_id": "risk-reviewer-1",
        "outcome": "rejected",
        "self_approval": False,
        "decided_at": NOW_ISO,
        "expires_at": (NOW + timedelta(days=1)).isoformat(),
        "execution_authority": "none",
    }
    approval_payload["receipt_sha256"] = canonical_sha256(approval_payload)
    read_store.approvals["approval-mtg-1"] = approval_payload

    link_res = client.post(
        "/bff/agora/proposals/prop-unsponsored-1/formal-approvals/approval-mtg-1:link",
        headers={
            **_auth_headers("alice", idempotency_key="cand-link-1"),
            "If-Match": validate_res.headers["etag"],
        },
        json={
            "expected_revision": rev3["revision"],
            "expected_proposal_digest": rev3["proposal_digest"],
        },
    )
    assert link_res.status_code == 200, link_res.text
    linked_data = link_res.json()["data"]
    assert len(linked_data["formal_approval_receipts"]) == 1
    assert linked_data["execution_authority"] == "none"
    assert linked_data["candidate"]["authority"] == authority_boundary()

    # 4. C2 output-routing helpers route unsponsored proposals and read back live status
    c2_results = route_meeting_candidate_decisions(
        owner_state["meetings"]["mtg-delib-1"],
        service=candidate_service,
        tenant_id="tenant-a",
        owner_user_id="alice",
        proposer_id="alice",
        read_store=read_store,
        idempotency_prefix="cand-unsponsored-1",
    )
    assert len(c2_results) == 1
    assert c2_results[0]["proposal_id"] == "prop-unsponsored-1"
    assert c2_results[0]["downstream_target"] == "agora_candidate_decision"
    assert c2_results[0]["downstream_id"] == "prop-unsponsored-1"
    assert c2_results[0]["execution_authority"] == "none"

    downstream = read_downstream_status(
        "prop-unsponsored-1",
        service=candidate_service,
        tenant_id="tenant-a",
        owner_user_id="alice",
    )
    assert downstream["downstream_id"] == "prop-unsponsored-1"
    assert downstream["status"] == "review_requested"
    assert downstream["execution_authority"] == "none"


def test_mt4_attribution_meeting_batch_candidate_route_and_participant_budget_cap(
    agora_meetings_stack,
) -> None:
    client, calls, owner_state, read_store, _ = agora_meetings_stack

    # Add 4 more institutional personas so total requested participants > 5
    for idx in range(2, 6):
        pid = f"persona-inst-{idx}"
        sid = f"cap-inst-{idx}"
        read_store.personas.append({
            "persona_id": pid,
            "display_name": f"Institutional {idx}",
            "tenant_id": "tenant-a",
            "lifecycle_state": "paper_running",
            "status": "active",
            "environment_ceiling": "paper",
            "persona_version": "v1",
            "capability_snapshot_id": sid,
        })
        read_store.snapshots[sid] = {
            "snapshot_id": sid,
            "persona_id": pid,
            "capabilities": ["persona_opinion"],
        }

    # Requesting 6 participants exceeds the M9 cap of 5
    over_budget = client.post(
        "/bff/agora/workshops/ws-alice-1/meetings",
        headers=_auth_headers("alice", idempotency_key="over-budget-6"),
        json={
            "meeting_type": "MT-4",
            "topic": "Attribution review with too many participants",
            "participants": [
                "persona-inst-1",
                "persona-inst-2",
                "persona-inst-3",
                "persona-inst-4",
                "persona-inst-5",
                "agora-servant-alice",
            ],
            "include_private_servant": False,
        },
    )
    assert over_budget.status_code == 422, over_budget.text
    assert "participant_budget_exceeded" in over_budget.text
    assert calls == []

    # Convening MT-4 with attribution_record_id within the 5-participant cap succeeds
    mt4_res = client.post(
        "/bff/agora/meetings",
        headers=_auth_headers("alice", idempotency_key="mt4-convene-1"),
        json={
            "workshop_id": "ws-alice-1",
            "meeting_type": "MT-4",
            "topic": "Performance attribution review for outlier episode",
            "attribution_record_id": "attr-outlier-42",
        },
    )
    assert mt4_res.status_code == 202, mt4_res.text
    assert len(mt4_res.json()["data"]["participants"]) == 5
    assert "agora-servant-alice" in mt4_res.json()["data"]["participants"]
    assert calls[-1]["body"]["attribution_record_id"] == "attr-outlier-42"

    # Batch route POST /bff/agora/meetings/{meeting_id}/candidate-decisions routes only unsponsored proposals
    batch_res = client.post(
        "/bff/agora/meetings/mtg-delib-1/candidate-decisions",
        headers=_auth_headers("alice", idempotency_key="batch-cand-1"),
    )
    assert batch_res.status_code == 201, batch_res.text
    items = batch_res.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["proposal_id"] == "prop-unsponsored-1"
    assert items[0]["downstream_target"] == "agora_candidate_decision"
    assert batch_res.json()["data"]["execution_authority"] == "none"

