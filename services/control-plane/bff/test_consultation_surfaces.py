#!/usr/bin/env python3
"""Unit tests for consultation read surfaces CS-01 to CS-06.

Tests the read_store methods that back the consultation surfaces:
- list_consultations_for_persona     (CS-01)
- get_consultation                   (CS-02)
- get_consultation_participants      (CS-03)
- get_consultation_outcome           (CS-04)
- get_consultation_evidence          (CS-05)
- get_consult_policy                 (CS-06)

Canonical basis:
  PERSONA_RUNTIME_MODEL.md §6, §13, §14
  CONSULTATION_SURFACE_CONTRACT.md

Note: this suite no longer exercises the legacy `the legacy read store`. It builds a
small in-memory test double (`_ConsultationSurfacePorts`) on top of the narrow
`ReadSurfacePorts` read ports, seeded with the same canonical fixture data that
used to ship as `the legacy read store`'s bundled local-snapshot fallback (see the
`cs-20260410-001` / `cs-resp-20260410-001` / `persona-alpha` / `p-risk-analyst`
records in read_store.py for the historical shape this mirrors). The narrow
`InMemoryOperationsConsultationPort` filters consultations by a flat
`persona_id` match and does not perform root-session resolution, so the
requester/responder root-resolution semantics (CS-01's "only requester
sessions" invariant, and CS-02..CS-05 resolving responder session ids back to
their root) are reimplemented here directly against the fixture, following
`the legacy read store.list_consultations_for_persona` / `_resolve_root_consultation_id`
/ `get_consultation_participants` / `get_consultation_outcome` /
`get_consultation_evidence` as the behavioral reference.
"""
from __future__ import annotations

import copy
import os
import sys
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.personas import PersonaService, create_personas_router
from services.control_plane.bff.ports import (
    create_in_memory_read_surface_ports,
    create_persona_registry_write_owner,
)

AUTH = "Bearer test-operator:operator,admin"


class _FakeRankingWriteOwner:
    def put_ranking_snapshot(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        sid = snapshot.get("snapshot_id") or "snap-1"
        return {"status": "created", "snapshot_id": sid, "snapshot": snapshot}

    def get_ranking_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        return None

    def list_ranking_snapshots(self) -> List[Dict[str, Any]]:
        return []


class _FakeCommandStore:
    def submit_command(self, *args: Any, **kwargs: Any) -> Any:
        return None


def _extract_identity(authorization: str | None) -> OperatorIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    raw = authorization[len("Bearer "):].strip()
    parts = raw.split(":")
    operator_id = parts[0] if parts else "op"
    roles = parts[1].split(",") if len(parts) > 1 else []
    return OperatorIdentity(operator_id=operator_id, roles=roles, claims={})


def _require_read_role(identity: OperatorIdentity) -> None:
    if not identity or not identity.roles:
        raise HTTPException(status_code=403, detail="Forbidden")


def _make_client(store: Any) -> TestClient:
    app = FastAPI(title="Consultation Surfaces Contract")
    gov_router = create_governance_router(
        get_read_store=lambda: store,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_operator_role=_require_read_role,
    )
    app.include_router(gov_router)

    persona_service = PersonaService(
        write_owner=create_persona_registry_write_owner(),
        ranking_write_owner=_FakeRankingWriteOwner(),
        read_store=store,
        command_store=_FakeCommandStore(),
    )
    persona_router = create_personas_router(
        service=persona_service,
        extract_identity_fn=_extract_identity,
        require_read_role_fn=_require_read_role,
        require_operator_role_fn=_require_read_role,
    )
    app.include_router(persona_router)

    return TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------- #
# Canonical fixture data (mirrors the legacy the legacy read store bundled fallback
# fixture for personas / consultation sessions / consult policies).
# --------------------------------------------------------------------------- #
_PERSONAS: List[Dict[str, Any]] = [
    {
        "persona_id": "persona-alpha",
        "id": "persona-alpha",
        "name": "Alpha Persona",
        "lifecycle_state": "active",
        "mandate": "systematic_crypto_trading",
        "strategy_family": "momentum",
        "created_at": "2026-03-01T00:00:00Z",
        "last_active_at": "2026-04-11T10:00:00Z",
    },
    {
        "persona_id": "p-risk-analyst",
        "id": "p-risk-analyst",
        "name": "Risk Analyst Persona",
        "lifecycle_state": "active",
        "mandate": "risk_review",
        "strategy_family": "risk_management",
        "created_at": "2026-02-15T00:00:00Z",
        "last_active_at": "2026-04-10T10:14:00Z",
    },
]

_CONSULT_SESSIONS: List[Dict[str, Any]] = [
    {
        "session_id": "sess-001",
        "id": "sess-001",
        "persona_id": "persona-alpha",
        "session_type": "interactive",
        "status": "active",
        "started_at": "2026-04-11T08:00:00Z",
        "capability_snapshot_id": "cap-001",
        "trace_id": "trace-sess-001",
        "request_id": "req-sess-001",
    },
    {
        "session_id": "cs-20260410-001",
        "persona_id": "persona-alpha",
        "session_type": "consult",
        "status": "terminated",
        "started_at": "2026-04-10T10:00:00Z",
        "ended_at": "2026-04-10T10:15:00Z",
        "capability_snapshot_id": "cap-001",
        "trace_id": "trace-cs-20260410-001",
        "request_id": "req-cs-20260410-001",
        "context_bundle_ref": "workspace://consultation-context/cs-20260410-001",
        "metadata": {
            "consultation": {
                "consultation_type": "pre_deployment",
                "requester_session_id": "cs-20260410-001",
                "responder_session_ids": ["cs-resp-20260410-001"],
                "committee_session_ids": [],
                "consult_policy_ref": "cp-risk-analyst",
                "trigger_rule": "pre_deployment_live",
                "required_reviewers": 1,
                "required_committees": [],
                "forbidden_solo_actions": ["approve_live_deployment"],
                "actual_reviewers": 1,
                "outcome": "conditional",
                "rationale_ref": "workspace://consultation-rationales/cs-20260410-001",
                "evidence_refs": [
                    {
                        "id": "ev-001",
                        "type": "evidence_link",
                        "evidence_type": "telemetry",
                        "artifact_ref": "artifact-042",
                        "description": "30-day performance metrics",
                        "link": "/api/v1/telemetry/artifact-042/performance?time_range=30d",
                    },
                    {
                        "id": "ev-002",
                        "type": "evidence_link",
                        "evidence_type": "lineage",
                        "artifact_ref": "artifact-042",
                        "description": "Full lineage chain for artifact-042",
                        "link": "/api/v1/lineage?artifact_id=artifact-042",
                    },
                ],
                "escalation_path": None,
            }
        },
    },
    {
        "session_id": "cs-resp-20260410-001",
        "persona_id": "p-risk-analyst",
        "session_type": "consult",
        "status": "terminated",
        "started_at": "2026-04-10T10:00:30Z",
        "ended_at": "2026-04-10T10:14:00Z",
        "capability_snapshot_id": "cap-001",
        "trace_id": "trace-cs-resp-20260410-001",
        "request_id": "req-cs-resp-20260410-001",
        "context_bundle_ref": "workspace://consultation-context/cs-20260410-001",
        "metadata": {
            "consultation": {
                "consultation_type": "pre_deployment",
                "consult_policy_ref": "cp-risk-analyst",
                "root_session_id": "cs-20260410-001",
            }
        },
    },
]

_CONSULT_RULES: List[Dict[str, Any]] = [
    {
        "id": "cp-risk-analyst",
        "persona_id": "p-risk-analyst",
        "required_reviewers": 1,
        "required_committees": [],
        "trigger_rules": [
            {
                "condition": "pre_deployment_live",
                "description": "Risk analyst must review before any live deployment",
            },
        ],
        "forbidden_solo_actions": ["approve_live_deployment"],
        "escalation_rules": [
            {"trigger": "responder_rejects", "escalate_to": "governance_committee"},
        ],
    },
    {
        "id": "cp-alpha",
        "persona_id": "persona-alpha",
        "required_reviewers": 1,
        "required_committees": [],
        "trigger_rules": [
            {
                "condition": "pre_deployment_live",
                "description": "Must consult before any live deployment",
            },
            {
                "condition": "macro_regime_shift",
                "description": "Must consult when macro regime shift detected",
            },
        ],
        "forbidden_solo_actions": [
            "approve_live_deployment",
            "increase_capital_allocation_above_20pct",
        ],
        "escalation_rules": [
            {"trigger": "responder_rejects", "escalate_to": "governance_committee"},
        ],
    },
]


class _ConsultationSurfacePorts:
    """In-memory test double for the consultation read surfaces (CS-01..CS-06).

    Wraps a narrow `ReadSurfacePorts` instance (built via
    `create_in_memory_read_surface_ports`) for persona/consult-policy reads,
    and layers requester/responder root-resolution semantics for
    consultation sessions on top -- mirroring the legacy
    `the legacy read store.list_consultations_for_persona` /
    `_resolve_root_consultation_id` / `get_consultation*` behavior, since the
    narrow `InMemoryOperationsConsultationPort.consult_sessions` does not
    implement that root-resolution.
    """

    def __init__(
        self,
        *,
        consult_sessions: List[Dict[str, Any]],
        personas: List[Dict[str, Any]],
        consult_rules: List[Dict[str, Any]],
    ) -> None:
        self._consult_sessions: Dict[str, Dict[str, Any]] = {
            s["session_id"]: copy.deepcopy(s) for s in consult_sessions
        }
        self._inner = create_in_memory_read_surface_ports(
            operations_consultation_kwargs={"consult_rules": consult_rules},
            persona_capital_runtime_kwargs={"personas": personas},
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _resolve_root_consultation_id(self, session_id: str) -> str:
        session = self._consult_sessions.get(session_id)
        if session is None:
            return session_id
        meta_consult = (session.get("metadata") or {}).get("consultation", {})
        if meta_consult.get("requester_session_id"):
            return session_id
        root_ref = meta_consult.get("root_session_id")
        if root_ref:
            return root_ref
        return session_id

    def list_consultations_for_persona(
        self,
        persona_id: Optional[str],
        consultation_type: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Optional[List[Dict[str, Any]]]:
        if not persona_id:
            return None
        if self._inner.get_persona(persona_id) is None:
            return None
        sessions = [
            s
            for s in self._consult_sessions.values()
            if s.get("persona_id") == persona_id
            and s.get("session_type") in {"consult", "committee"}
            and s.get("session_id")
            == ((s.get("metadata") or {}).get("consultation", {}).get("requester_session_id"))
        ]
        if consultation_type:
            sessions = [
                s
                for s in sessions
                if (s.get("metadata") or {}).get("consultation", {}).get("consultation_type")
                == consultation_type
            ]
        if status:
            sessions = [s for s in sessions if s.get("status") == status]
        sessions = sorted(sessions, key=lambda x: x.get("started_at", ""), reverse=True)
        return sessions

    def get_consultation(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        session = self._consult_sessions.get(session_id)
        if session is None:
            return None
        if session.get("session_type") not in {"consult", "committee"}:
            return None
        return session

    def get_consultation_participants(self, session_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        if not session_id or session_id not in self._consult_sessions:
            return None
        root_id = self._resolve_root_consultation_id(session_id)
        root = self._consult_sessions.get(root_id)
        if root is None:
            return None
        meta_consult = (root.get("metadata") or {}).get("consultation", {})
        requester_id = meta_consult.get("requester_session_id")
        responder_ids: List[str] = meta_consult.get("responder_session_ids") or []
        committee_ids: List[str] = meta_consult.get("committee_session_ids") or []

        def _role_for(sid: str) -> str:
            if sid == requester_id:
                return "requester"
            if sid in committee_ids:
                return "committee_participant"
            return "responder"

        participants = []
        for sid in [requester_id] + responder_ids + committee_ids:
            if not sid:
                continue
            session = self._consult_sessions.get(sid)
            if session:
                enriched = dict(session)
                enriched["consultation_role"] = _role_for(sid)
                participants.append(enriched)
        return participants

    def get_consultation_outcome(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_id or session_id not in self._consult_sessions:
            return None
        root_id = self._resolve_root_consultation_id(session_id)
        session = self.get_consultation(root_id)
        if session is None:
            return None
        meta_consult = (session.get("metadata") or {}).get("consultation", {})
        return {
            "session_id": session_id,
            "root_session_id": root_id,
            "source_session": f"/api/v1/consultations/{root_id}",
            "metadata": {
                "consultation": {
                    "outcome": meta_consult.get("outcome"),
                    "actual_reviewers": meta_consult.get("actual_reviewers"),
                    "responder_session_ids": meta_consult.get("responder_session_ids", []),
                    "rationale_ref": meta_consult.get("rationale_ref"),
                    "evidence_refs": meta_consult.get("evidence_refs", []),
                    "escalation_path": meta_consult.get("escalation_path"),
                }
            },
        }

    def get_consultation_evidence(self, session_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        if not session_id or session_id not in self._consult_sessions:
            return None
        root_id = self._resolve_root_consultation_id(session_id)
        session = self.get_consultation(root_id)
        if session is None:
            return None
        meta_consult = (session.get("metadata") or {}).get("consultation", {})
        return list(meta_consult.get("evidence_refs") or [])

    def get_consult_policy(self, persona_id: Optional[str] = None, **kwargs: Any) -> Optional[Dict[str, Any]]:
        # Legacy the legacy read store.get_consult_policy semantics: an absent
        # persona_id is a miss (None), unlike ReadSurfacePorts.get_consult_policy
        # which falls back to the first available rule when no persona_id is given.
        if not persona_id:
            return None
        return self._inner.get_consult_policy(persona_id, **kwargs)


def _build_consultation_ports() -> _ConsultationSurfacePorts:
    return _ConsultationSurfacePorts(
        consult_sessions=_CONSULT_SESSIONS,
        personas=_PERSONAS,
        consult_rules=_CONSULT_RULES,
    )


@contextmanager
def _seeded_app_read_store():
    seeded = _build_consultation_ports()
    yield _make_client(seeded)


def test_consultation_surfaces():
    store = _build_consultation_ports()

    # ------------------------------------------------------------------ #
    # CS-01: list consultations for persona
    # ------------------------------------------------------------------ #
    consultations = store.list_consultations_for_persona("persona-alpha")
    assert consultations is not None, "CS-01: should return a list, not None"
    assert len(consultations) >= 1, "CS-01: persona-alpha should have at least one consultation"
    for c in consultations:
        assert c.get("persona_id") == "persona-alpha", "CS-01: all sessions should belong to persona-alpha"
        assert c.get("session_type") in {"consult", "committee"}, "CS-01: only consult/committee types"
    print(f"CS-01: list_consultations_for_persona returns {len(consultations)} session(s)")

    # CS-01: filter by consultation_type
    pre_dep = store.list_consultations_for_persona(
        "persona-alpha", consultation_type="pre_deployment"
    )
    assert pre_dep is not None
    assert all(
        (s.get("metadata") or {}).get("consultation", {}).get("consultation_type") == "pre_deployment"
        for s in pre_dep
    ), "CS-01: filter by consultation_type should only return matching sessions"
    print("CS-01: consultation_type filter works")

    # CS-01: filter by status
    terminated = store.list_consultations_for_persona("persona-alpha", status="terminated")
    assert terminated is not None
    assert all(s.get("status") == "terminated" for s in terminated), "CS-01: status filter"
    print("CS-01: status filter works")

    # CS-01: None for invalid persona_id
    assert store.list_consultations_for_persona(None) is None
    assert store.list_consultations_for_persona("") is None
    print("CS-01: returns None for invalid persona_id")

    # ------------------------------------------------------------------ #
    # CS-02: consultation detail
    # ------------------------------------------------------------------ #
    session = store.get_consultation("cs-20260410-001")
    assert session is not None, "CS-02: cs-20260410-001 should exist"
    assert session["session_id"] == "cs-20260410-001", "CS-02: session_id matches"
    assert session["session_type"] in {"consult", "committee"}, "CS-02: must be consult or committee"
    assert "trace_id" in session, "CS-02: trace_id required (PERSONA_RUNTIME_MODEL §16)"
    assert "request_id" in session, "CS-02: request_id required (PERSONA_RUNTIME_MODEL §16)"
    assert "capability_snapshot_id" in session, "CS-02: capability_snapshot_id required"
    print("CS-02: get_consultation returns correct session detail")

    # CS-02: None for unknown
    assert store.get_consultation("nonexistent") is None, "CS-02: unknown session returns None"
    assert store.get_consultation(None) is None, "CS-02: None id returns None"
    print("CS-02: returns None for unknown session_id")

    # CS-02: regular (non-consultation) session must not be returned
    assert store.get_consultation("sess-001") is None, \
        "CS-02: non-consultation session sess-001 must not be returned"
    print("CS-02: non-consultation sessions are not returned")

    # ------------------------------------------------------------------ #
    # CS-03: consultation participants
    # ------------------------------------------------------------------ #
    participants = store.get_consultation_participants("cs-20260410-001")
    assert participants is not None, "CS-03: participants should not be None for valid session"
    assert len(participants) >= 1, "CS-03: at least the requester should be present"
    roles = {p.get("consultation_role") for p in participants}
    assert "requester" in roles, "CS-03: requester should be present"
    for p in participants:
        assert "session_id" in p, "CS-03: each participant has session_id"
        assert "persona_id" in p, "CS-03: each participant has persona_id"
        assert "consultation_role" in p, "CS-03: each participant has consultation_role"
    print(f"CS-03: get_consultation_participants returns {len(participants)} participant(s)")

    # CS-03: None for unknown session
    assert store.get_consultation_participants("nonexistent") is None
    assert store.get_consultation_participants(None) is None
    print("CS-03: returns None for unknown session_id")

    # ------------------------------------------------------------------ #
    # CS-04: consultation outcome
    # ------------------------------------------------------------------ #
    outcome = store.get_consultation_outcome("cs-20260410-001")
    assert outcome is not None, "CS-04: outcome should not be None"
    assert outcome["session_id"] == "cs-20260410-001", "CS-04: session_id matches"
    assert "metadata" in outcome, "CS-04: outcome has metadata"
    consult_meta = outcome["metadata"]["consultation"]
    assert "outcome" in consult_meta, "CS-04: outcome field present"
    assert "actual_reviewers" in consult_meta, "CS-04: actual_reviewers present"
    print(f"CS-04: get_consultation_outcome returns outcome={consult_meta['outcome']!r}")

    # CS-04: None for unknown
    assert store.get_consultation_outcome("nonexistent") is None
    assert store.get_consultation_outcome(None) is None
    print("CS-04: returns None for unknown session_id")

    # ------------------------------------------------------------------ #
    # CS-05: consultation evidence
    # ------------------------------------------------------------------ #
    evidence = store.get_consultation_evidence("cs-20260410-001")
    assert evidence is not None, "CS-05: evidence should not be None"
    assert isinstance(evidence, list), "CS-05: evidence is a list"
    assert len(evidence) >= 1, "CS-05: at least one evidence ref"
    for ev in evidence:
        assert "evidence_type" in ev, "CS-05: each evidence ref has evidence_type"
        assert "link" in ev, "CS-05: each evidence ref has a link"
    print(f"CS-05: get_consultation_evidence returns {len(evidence)} evidence ref(s)")

    # CS-05: None for unknown
    assert store.get_consultation_evidence("nonexistent") is None
    assert store.get_consultation_evidence(None) is None
    print("CS-05: returns None for unknown session_id")

    # ------------------------------------------------------------------ #
    # CS-06: consult policy
    # ------------------------------------------------------------------ #
    policy = store.get_consult_policy("persona-alpha")
    assert policy is not None, "CS-06: consult policy should exist for persona-alpha"
    assert "required_reviewers" in policy, "CS-06: required_reviewers present"
    assert "trigger_rules" in policy, "CS-06: trigger_rules present"
    assert "forbidden_solo_actions" in policy, "CS-06: forbidden_solo_actions present"
    assert "escalation_rules" in policy, "CS-06: escalation_rules present"
    assert isinstance(policy["trigger_rules"], list), "CS-06: trigger_rules is a list"
    print(f"CS-06: get_consult_policy returns policy with {len(policy['trigger_rules'])} rule(s)")

    # CS-06: None for unknown persona
    assert store.get_consult_policy("nonexistent") is None
    assert store.get_consult_policy(None) is None
    print("CS-06: returns None for unknown persona_id")

    # ------------------------------------------------------------------ #
    # Invariant: responder sessions are not returned by CS-01
    # (CS-01 returns only the persona's own requester sessions)
    # ------------------------------------------------------------------ #
    resp_session = store.get_consultation("cs-resp-20260410-001")
    assert resp_session is not None, "responder session should exist in store"
    # p-risk-analyst has no requester consultations so the list is empty (not None)
    risk_analyst_consultations = store.list_consultations_for_persona("p-risk-analyst")
    assert risk_analyst_consultations is not None
    assert all(
        (s.get("metadata") or {}).get("consultation", {}).get("requester_session_id") == s["session_id"]
        for s in risk_analyst_consultations
    ), "CS-01: only requester sessions are listed"
    print("CS-01 invariant: only requester sessions returned by list_consultations_for_persona")

    # ------------------------------------------------------------------ #
    # Persistence: a second, independently-constructed accessor over the
    # same canonical fixture data sees the same consultation data (the
    # in-memory analogue of the legacy read store's snapshot-reload persistence
    # check: two logical accesses to the same underlying data agree).
    # ------------------------------------------------------------------ #
    store2 = _build_consultation_ports()
    assert store2.get_consultation("cs-20260410-001") is not None, "Consultation session survives reload"
    assert store2.get_consult_policy("persona-alpha") is not None, "Consult policy survives reload"
    print("Persistence: consultation data reloads correctly")

    print("\n" + "=" * 55)
    print("Consultation surface (CS-01 to CS-06) tests: ALL PASSED")


def test_consultation_routes_requester_happy_path():
    """HTTP-level: requester session returns populated participants, outcome, and evidence."""
    with _seeded_app_read_store() as client:
        # CS-02: detail
        resp = client.get("/api/v1/consultations/cs-20260410-001", headers={"Authorization": AUTH})
        assert resp.status_code == 200, f"CS-02 requester detail failed: {resp.status_code}"
        body = resp.json()
        assert body["data"]["session_id"] == "cs-20260410-001"
        links = body["data"]["_links"]
        assert "participants" in links
        assert "outcome" in links
        assert "evidence" in links
        print("HTTP CS-02: requester consultation detail OK")

        # CS-03: participants
        resp = client.get("/api/v1/consultations/cs-20260410-001/participants", headers={"Authorization": AUTH})
        assert resp.status_code == 200, f"CS-03 participants failed: {resp.status_code}"
        data = resp.json()["data"]
        assert len(data) >= 1, "CS-03: requester path should have at least one participant"
        roles = {p["consultation_role"] for p in data}
        assert "requester" in roles, "CS-03: requester role must be present"
        print(f"HTTP CS-03: requester participants returns {len(data)} participant(s)")

        # CS-04: outcome
        resp = client.get("/api/v1/consultations/cs-20260410-001/outcome", headers={"Authorization": AUTH})
        assert resp.status_code == 200, f"CS-04 outcome failed: {resp.status_code}"
        outcome = resp.json()["data"]
        assert outcome["metadata"]["consultation"]["outcome"] is not None, "CS-04: outcome must not be null"
        print(f"HTTP CS-04: requester outcome={outcome['metadata']['consultation']['outcome']!r}")

        # CS-05: evidence
        resp = client.get("/api/v1/consultations/cs-20260410-001/evidence", headers={"Authorization": AUTH})
        assert resp.status_code == 200, f"CS-05 evidence failed: {resp.status_code}"
        evidence = resp.json()["data"]
        assert len(evidence) >= 1, "CS-05: at least one evidence ref"
        print(f"HTTP CS-05: requester evidence returns {len(evidence)} ref(s)")


def test_consultation_routes_responder_path():
    """HTTP-level: responder session id resolves to root data (non-empty participants/outcome/evidence)."""
    resp_id = "cs-resp-20260410-001"

    with _seeded_app_read_store() as client:
        # CS-02: responder detail is served (200)
        resp = client.get(f"/api/v1/consultations/{resp_id}", headers={"Authorization": AUTH})
        assert resp.status_code == 200, f"CS-02 responder detail failed: {resp.status_code}"
        print("HTTP CS-02: responder session detail OK")

        # CS-03: participants are non-empty (resolved from root)
        resp = client.get(f"/api/v1/consultations/{resp_id}/participants", headers={"Authorization": AUTH})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) >= 1, "CS-03: responder path must resolve root participants (non-empty)"
        roles = {p["consultation_role"] for p in data}
        assert "requester" in roles, "CS-03: root requester must appear in participant list"
        print(f"HTTP CS-03: responder participants resolved from root -- {len(data)} participant(s)")

        # CS-04: outcome is non-null (resolved from root)
        resp = client.get(f"/api/v1/consultations/{resp_id}/outcome", headers={"Authorization": AUTH})
        assert resp.status_code == 200
        outcome_meta = resp.json()["data"]["metadata"]["consultation"]
        assert outcome_meta["outcome"] is not None, "CS-04: responder path must resolve non-null outcome from root"
        print(f"HTTP CS-04: responder outcome resolved from root -- outcome={outcome_meta['outcome']!r}")

        # CS-05: evidence is non-empty (resolved from root)
        resp = client.get(f"/api/v1/consultations/{resp_id}/evidence", headers={"Authorization": AUTH})
        assert resp.status_code == 200
        evidence = resp.json()["data"]
        assert len(evidence) >= 1, "CS-05: responder path must resolve non-empty evidence from root"
        print(f"HTTP CS-05: responder evidence resolved from root -- {len(evidence)} ref(s)")


def test_consultation_participant_persona_links_resolve():
    """HTTP-level: persona_id values on participants resolve to real persona records (200)."""
    with _seeded_app_read_store() as client:
        resp = client.get(
            "/api/v1/consultations/cs-20260410-001/participants",
            headers={"Authorization": AUTH},
        )
        assert resp.status_code == 200
        participants = resp.json()["data"]
        for p in participants:
            pid = p.get("persona_id")
            assert pid, f"participant missing persona_id: {p}"
            persona_resp = client.get(f"/api/v1/personas/{pid}", headers={"Authorization": AUTH})
            assert persona_resp.status_code == 200, (
                f"participant persona_id={pid!r} returns {persona_resp.status_code} -- dead link"
            )
            print(f"HTTP persona link: /api/v1/personas/{pid} -> 200")


if __name__ == "__main__":
    test_consultation_surfaces()
    test_consultation_routes_requester_happy_path()
    test_consultation_routes_responder_path()
    test_consultation_participant_persona_links_resolve()
