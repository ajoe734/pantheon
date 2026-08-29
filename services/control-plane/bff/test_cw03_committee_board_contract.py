from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from command_queue import CommandStore
from domain_ports.operations_consultation import DomainConsultationPort, _model_to_data
from services.consultation.models import (
    ActorRef,
    ConsultAuditEvent,
    ConsultFinding,
    ConsultGateHandoff,
    ConsultMemo,
    ConsultRequest,
    ConsultRequestStatus,
    ConsultRequestType,
    AuthorType,
    FindingSeverity,
    GateHandoffStatus,
    MemoStatus,
    MemoType,
    Recommendation,
)
from services.consultation.store import ConsultationStore


OPERATOR_AUTH = "Bearer test-operator:operator"
REVIEWER_AUTH = "Bearer test-reviewer:reviewer"

_CONSULTATION_SESSION_STORE_ENV = "PANTHEON_BFF_CONSULTATION_SESSION_STORE"


_PERSONAS: Dict[str, Dict[str, Any]] = {
    "persona-alpha": {"id": "persona-alpha", "name": "Alpha Persona"},
    "p-risk-analyst": {"id": "p-risk-analyst", "name": "Risk Analyst Persona"},
    "p-macro-observer": {"id": "p-macro-observer", "name": "Macro Observer"},
    "p-execution-lead": {"id": "p-execution-lead", "name": "Execution Lead"},
    "p-compliance-sponsor": {"id": "p-compliance-sponsor", "name": "Compliance Sponsor"},
}


def _get_persona(persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not persona_id:
        return None
    return _PERSONAS.get(persona_id)


def _default_consultation_sessions() -> Dict[str, Dict[str, Any]]:
    return {
        "cs-20260419-081": {
            "session_id": "cs-20260419-081",
            "persona_id": "persona-alpha",
            "session_type": "consult",
            "status": "active",
            "started_at": "2026-04-19T17:06:00Z",
            "ended_at": None,
            "request_id": "cr-20260419-014",
            "metadata": {
                "consultation": {
                    "consultation_type": "risk_review",
                    "requester_session_id": "cs-20260419-081",
                    "responder_session_ids": [],
                    "committee_session_ids": [
                        "cm-20260419-081-001",
                        "cm-20260419-081-002",
                        "cm-20260419-081-003",
                    ],
                    "outcome": "pending",
                    "rationale_ref": "workspace://consultation-rationales/cs-20260419-081",
                    "evidence_refs": [
                        {
                            "id": "telemetry-vol-spike-20260419",
                            "type": "evidence_link",
                            "evidence_type": "telemetry",
                            "artifact_ref": "artifact-042",
                            "description": "Volatility spike - 2026-04-19",
                            "link": "/telemetry/events/telemetry-vol-spike-20260419",
                        },
                        {
                            "id": "dp-20260419-014",
                            "type": "evidence_link",
                            "evidence_type": "deployment_plan",
                            "artifact_ref": "dp-20260419-014",
                            "description": "Deployment plan dp-20260419-014",
                            "link": "/deployments/plans/dp-20260419-014",
                        },
                    ],
                    "dissent_refs": [
                        "workspace://consultation-dissent/cs-20260419-081/execution-lead"
                    ],
                    "escalation_path": "committee_override",
                    "committee_ref": "committee-regime-risk-20260419-081",
                    "quorum_state": "quorum_met",
                    "consensus_state": "sponsor_required",
                    "committee_started_at": "2026-04-19T17:07:00Z",
                    "committee_surface_state": "ok",
                    "sponsor_session_id": "cm-20260419-081-003",
                    "sponsor_decision": None,
                    "sponsor_decided_at": None,
                    "sponsor_decided_by": None,
                    "escalation_reason": {
                        "trigger_rule": "macro_regime_shift",
                        "forbidden_solo_action": "approve_live_deployment",
                        "escalation_path": "committee_override",
                    },
                    "synthesis_summary": {
                        "outcome": "pending",
                        "rationale_ref": "workspace://consultation-rationales/cs-20260419-081",
                        "evidence_refs": [
                            "telemetry-vol-spike-20260419",
                            "dp-20260419-014",
                        ],
                        "dissent_refs": [
                            "workspace://consultation-dissent/cs-20260419-081/execution-lead"
                        ],
                    },
                }
            },
        },
        "cm-20260419-081-001": {
            "session_id": "cm-20260419-081-001",
            "persona_id": "p-macro-observer",
            "session_type": "committee",
            "status": "active",
            "started_at": "2026-04-19T17:07:00Z",
            "ended_at": None,
            "request_id": "cr-20260419-014",
            "metadata": {
                "consultation": {
                    "root_session_id": "cs-20260419-081",
                    "committee_ref": "committee-regime-risk-20260419-081",
                    "participant_status": "voted",
                    "outcome_signal": "approved",
                    "role": "committee_participant",
                    "rationale_ref": "workspace://consultation-rationales/cs-20260419-081/macro-observer",
                }
            },
        },
        "cm-20260419-081-002": {
            "session_id": "cm-20260419-081-002",
            "persona_id": "p-execution-lead",
            "session_type": "committee",
            "status": "active",
            "started_at": "2026-04-19T17:07:30Z",
            "ended_at": None,
            "request_id": "cr-20260419-014",
            "metadata": {
                "consultation": {
                    "root_session_id": "cs-20260419-081",
                    "committee_ref": "committee-regime-risk-20260419-081",
                    "participant_status": "voted",
                    "outcome_signal": "conditional",
                    "role": "committee_participant",
                    "rationale_ref": "workspace://consultation-dissent/cs-20260419-081/execution-lead",
                }
            },
        },
        "cm-20260419-081-003": {
            "session_id": "cm-20260419-081-003",
            "persona_id": "p-compliance-sponsor",
            "session_type": "committee",
            "status": "active",
            "started_at": "2026-04-19T17:08:00Z",
            "ended_at": None,
            "request_id": "cr-20260419-014",
            "metadata": {
                "consultation": {
                    "root_session_id": "cs-20260419-081",
                    "committee_ref": "committee-regime-risk-20260419-081",
                    "participant_status": "active",
                    "outcome_signal": None,
                    "role": "sponsor",
                    "rationale_ref": "workspace://consultation-rationales/cs-20260419-081/sponsor",
                }
            },
        },
    }


def _committee_surface_state(root_session: Dict[str, Any]) -> str:
    consult = (root_session.get("metadata") or {}).get("consultation", {})
    return str(consult.get("committee_surface_state") or "ok")


def _committee_board_row(root_session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    consult = (root_session.get("metadata") or {}).get("consultation", {})
    committee_id = str(consult.get("committee_ref") or "").strip()
    committee_session_ids = list(consult.get("committee_session_ids") or [])
    if not committee_id or not committee_session_ids:
        return None
    return {
        "committee_id": committee_id,
        "committee_ref": committee_id,
        "escalation_reason": json.loads(json.dumps(consult.get("escalation_reason") or {})),
        "quorum_state": consult.get("quorum_state"),
        "consensus_state": consult.get("consensus_state"),
        "linked_request_id": root_session.get("request_id"),
        "started_at": consult.get("committee_started_at") or root_session.get("started_at"),
        "surface_state": _committee_surface_state(root_session),
        "route_href": f"/consultation/committees/{committee_id}",
    }


def _list_committees(
    sessions: Dict[str, Dict[str, Any]],
    *,
    quorum_states: Optional[List[str]] = None,
    consensus_states: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for session in sessions.values():
        if session.get("session_type") != "consult":
            continue
        row = _committee_board_row(session)
        if row is None:
            continue
        rows.append(row)

    if quorum_states:
        requested = {str(v).strip().lower() for v in quorum_states if str(v).strip()}
        rows = [r for r in rows if str(r.get("quorum_state") or "").strip().lower() in requested]
    if consensus_states:
        requested = {str(v).strip().lower() for v in consensus_states if str(v).strip()}
        rows = [r for r in rows if str(r.get("consensus_state") or "").strip().lower() in requested]
    return rows


def _get_committee(committee_id: Optional[str], sessions: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not committee_id:
        return None

    root_session: Optional[Dict[str, Any]] = None
    for session in sessions.values():
        if session.get("session_type") != "consult":
            continue
        consult = (session.get("metadata") or {}).get("consultation", {})
        if str(consult.get("committee_ref") or "") == str(committee_id):
            root_session = session
            break
    if root_session is None:
        return None

    consult = (root_session.get("metadata") or {}).get("consultation", {})
    committee_session_ids = list(consult.get("committee_session_ids") or [])
    sponsor_session_id = str(consult.get("sponsor_session_id") or "").strip()

    participant_roster: List[Dict[str, Any]] = []
    for session_id in committee_session_ids:
        participant = sessions.get(session_id)
        if not participant:
            continue
        participant_consult = (participant.get("metadata") or {}).get("consultation", {})
        persona = _get_persona(participant.get("persona_id"))
        participant_roster.append(
            {
                "participant_id": participant.get("session_id"),
                "persona_id": participant.get("persona_id"),
                "persona_label": (persona or {}).get("name"),
                "role": "sponsor" if participant.get("session_id") == sponsor_session_id else (
                    participant_consult.get("role") or "committee_participant"
                ),
                "status": participant_consult.get("participant_status") or participant.get("status"),
                "outcome_signal": participant_consult.get("outcome_signal"),
                "rationale_ref": participant_consult.get("rationale_ref"),
            }
        )

    sponsor_assignment = next(
        (row for row in participant_roster if str(row.get("participant_id") or "") == sponsor_session_id),
        None,
    )
    board_row = _committee_board_row(root_session)
    if board_row is None:
        return None

    return {
        **board_row,
        "linked_session_id": root_session.get("session_id"),
        "participant_roster": participant_roster,
        "sponsor_assignment": sponsor_assignment,
        "sponsor_decision": consult.get("sponsor_decision"),
        "sponsor_decided_at": consult.get("sponsor_decided_at"),
        "sponsor_decided_by": consult.get("sponsor_decided_by"),
        "synthesis_summary": json.loads(json.dumps(consult.get("synthesis_summary") or {})),
        "linked_evidence": json.loads(json.dumps(consult.get("evidence_refs") or [])),
        "service_handoff": json.loads(json.dumps(consult.get("service_handoff") or {})),
    }


def _apply_sponsor_decision(
    sessions: Dict[str, Dict[str, Any]],
    committee_id: str,
    *,
    sponsor_decision: str,
    rationale_ref: str,
    actor_id: str,
    recorded_at: str,
) -> Optional[str]:
    """Mutates `sessions` in place. Returns the matched root session_id, or None."""
    root_session_id: Optional[str] = None
    for session_id, session in sessions.items():
        consult = (session.get("metadata") or {}).get("consultation", {})
        if session.get("session_type") == "consult" and str(consult.get("committee_ref") or "") == str(committee_id):
            root_session_id = session_id
            break
    if root_session_id is None:
        return None

    consult = sessions[root_session_id].setdefault("metadata", {}).setdefault("consultation", {})
    consult["sponsor_decision"] = sponsor_decision
    consult["sponsor_decided_at"] = recorded_at
    consult["sponsor_decided_by"] = actor_id
    consult["consensus_state"] = "reached"
    consult["outcome"] = sponsor_decision
    synthesis_summary = dict(consult.get("synthesis_summary") or {})
    synthesis_summary["outcome"] = sponsor_decision
    synthesis_summary["rationale_ref"] = rationale_ref
    consult["synthesis_summary"] = synthesis_summary
    consult["rationale_ref"] = rationale_ref
    return root_session_id


class _CommitteeReadStore:
    """CW-03 in-memory committee-board read/write double.

    Committee list/get/record-sponsor-decision are not covered by the typed
    read ports (see RETAINED_WRITES_DEFERRED_FROM_READ_SURFACE), so this
    double reimplements them locally against an in-memory
    `consultation_sessions` dataset that mirrors the retired
    legacy BFF read surface's default fixture. `record_sponsor_decision` also
    supports the two persistence paths the original supported: a real
    ConsultationStore (PANTHEON_BFF_CONSULTATION_DATA_DIR) and a flat
    consultation_sessions JSON file (PANTHEON_BFF_CONSULTATION_SESSION_STORE).
    """

    def __init__(self, path: str, allow_local_snapshot_fallback: bool = True) -> None:
        self._path = path
        self._data: Dict[str, Any] = {"consultation_sessions": _default_consultation_sessions()}
        self._domain = DomainConsultationPort()

    def _save(self) -> None:
        try:
            with open(self._path, "w", encoding="utf-8") as handle:
                json.dump(self._data, handle)
        except OSError:
            pass

    def get_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return _get_persona(persona_id)

    def dataset_source(self, dataset: str) -> str:
        if dataset != "consultation_sessions":
            return "missing"
        return "local_snapshot"

    def list_committees(
        self,
        *,
        quorum_states: Optional[List[str]] = None,
        consensus_states: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        return _list_committees(
            self._data["consultation_sessions"],
            quorum_states=quorum_states,
            consensus_states=consensus_states,
        )

    def get_committee(self, committee_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return _get_committee(committee_id, self._data["consultation_sessions"])

    def record_sponsor_decision(
        self,
        committee_id: str,
        *,
        sponsor_decision: str,
        rationale_ref: str,
        actor_id: str,
        recorded_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        timestamp = recorded_at or (datetime.utcnow().isoformat() + "Z")

        store = self._domain._consultation_store()
        if store is not None:
            return self._record_sponsor_decision_via_service_store(
                store,
                committee_id,
                sponsor_decision=sponsor_decision,
                rationale_ref=rationale_ref,
                actor_id=actor_id,
                timestamp=timestamp,
            )

        env_path = os.environ.get(_CONSULTATION_SESSION_STORE_ENV, "").strip()
        if env_path and Path(env_path).exists():
            sessions = json.loads(Path(env_path).read_text(encoding="utf-8"))
            root_id = _apply_sponsor_decision(
                sessions,
                committee_id,
                sponsor_decision=sponsor_decision,
                rationale_ref=rationale_ref,
                actor_id=actor_id,
                recorded_at=timestamp,
            )
            if root_id is None:
                return None
            Path(env_path).write_text(json.dumps(sessions, indent=2), encoding="utf-8")
            return _get_committee(committee_id, sessions)

        sessions = self._data["consultation_sessions"]
        root_id = _apply_sponsor_decision(
            sessions,
            committee_id,
            sponsor_decision=sponsor_decision,
            rationale_ref=rationale_ref,
            actor_id=actor_id,
            recorded_at=timestamp,
        )
        if root_id is None:
            return None
        self._save()
        return _get_committee(committee_id, sessions)

    @staticmethod
    def _record_sponsor_decision_via_service_store(
        store: ConsultationStore,
        committee_id: str,
        *,
        sponsor_decision: str,
        rationale_ref: str,
        actor_id: str,
        timestamp: str,
    ) -> Optional[Dict[str, Any]]:
        matched_request: Optional[ConsultRequest] = None
        matched_consult: Dict[str, Any] = {}
        for request in store.list_requests():
            metadata = request.metadata if isinstance(request.metadata, dict) else {}
            consult = metadata.get("consultation") if isinstance(metadata.get("consultation"), dict) else {}
            if str(consult.get("committee_ref") or "") == str(committee_id):
                matched_request = request
                matched_consult = dict(consult)
                break
        if matched_request is None:
            return None

        matched_consult["sponsor_decision"] = sponsor_decision
        matched_consult["sponsor_decided_at"] = timestamp
        matched_consult["sponsor_decided_by"] = actor_id
        matched_consult["consensus_state"] = "reached"
        matched_consult["outcome"] = sponsor_decision
        synthesis_summary = dict(matched_consult.get("synthesis_summary") or {})
        synthesis_summary["outcome"] = sponsor_decision
        synthesis_summary["rationale_ref"] = rationale_ref
        matched_consult["synthesis_summary"] = synthesis_summary
        matched_consult["rationale_ref"] = rationale_ref

        memos = [
            memo
            for memo in store.list_memos_for_request(matched_request.request_id)
            if str(memo.status.value if hasattr(memo.status, "value") else memo.status) == MemoStatus.PUBLISHED.value
        ]
        if not memos:
            return None

        evidence_ref_ids: List[str] = []
        for ref_id in matched_request.evidence_refs:
            if str(ref_id or "").strip() and str(ref_id) not in evidence_ref_ids:
                evidence_ref_ids.append(str(ref_id))
        for attachment in store.list_evidence_for_request(matched_request.request_id):
            ref_id = str(attachment.evidence_ref.id or "").strip()
            if ref_id and ref_id not in evidence_ref_ids:
                evidence_ref_ids.append(ref_id)
        for item in matched_consult.get("evidence_refs") or []:
            ref_id = str(item.get("id") if isinstance(item, dict) else item or "").strip()
            if ref_id and ref_id not in evidence_ref_ids:
                evidence_ref_ids.append(ref_id)

        audit_refs = [event.audit_id for event in store.list_audit_for_request(matched_request.request_id)]
        handoff = ConsultGateHandoff(
            handoff_id=f"gh-{uuid.uuid4().hex[:12]}",
            request_id=matched_request.request_id,
            target_gate=f"committee_sponsor_decision:{committee_id}",
            memo_ids=[memo.memo_id for memo in memos],
            evidence_refs=evidence_ref_ids,
            audit_refs=audit_refs,
            trace_id=matched_request.trace_id,
            status=GateHandoffStatus.SENT,
            sent_at=timestamp,
        )
        store.put_handoff(handoff)
        audit = ConsultAuditEvent(
            audit_id=f"aud-{uuid.uuid4().hex[:12]}",
            request_id=matched_request.request_id,
            actor_ref=ActorRef(actor_type="operator", actor_id=actor_id),
            service_actor_ref=ActorRef(actor_type="service", actor_id="consultation-svc"),
            action="gate_handoff_created",
            after_state=handoff.handoff_id,
            timestamp=timestamp,
            trace_id=matched_request.trace_id,
        )
        store.append_audit(audit)
        handoff.audit_refs.append(audit.audit_id)
        store.put_handoff(handoff)

        metadata = matched_request.metadata if isinstance(matched_request.metadata, dict) else {}
        metadata["consultation"] = matched_consult
        metadata["service_handoff"] = {
            "handoff_id": handoff.handoff_id,
            "target_gate": handoff.target_gate,
            "evidence_refs": list(handoff.evidence_refs),
            "audit_refs": list(handoff.audit_refs),
            "status": handoff.status.value if hasattr(handoff.status, "value") else handoff.status,
        }
        matched_request.metadata = metadata
        store.put_request(matched_request)

        request_dicts = [_model_to_data(r) for r in store.list_requests()]
        handoff_dicts = [_model_to_data(h) for h in store.list_handoffs()]
        sessions_list = DomainConsultationPort._project_service_session_records_from_data(
            request_dicts, handoff_dicts
        )
        sessions = {
            str(s.get("session_id") or s.get("id")): s
            for s in sessions_list
            if str(s.get("session_id") or s.get("id") or "").strip()
        }
        return _get_committee(committee_id, sessions)


@contextmanager
def _seeded_client():
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        bff_main.read_store = _CommitteeReadStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store


def test_cw03_list_contract_returns_committee_projection() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/committees?consensus_state=sponsor_required",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["page_info"]["total"] == 1
        assert payload["data"][0] == {
            "committee_id": "committee-regime-risk-20260419-081",
            "committee_ref": "committee-regime-risk-20260419-081",
            "escalation_reason": {
                "trigger_rule": "macro_regime_shift",
                "forbidden_solo_action": "approve_live_deployment",
                "escalation_path": "committee_override",
            },
            "quorum_state": "quorum_met",
            "consensus_state": "sponsor_required",
            "linked_request_id": "cr-20260419-014",
            "started_at": "2026-04-19T17:07:00Z",
            "surface_state": "ok",
            "route_href": "/consultation/committees/committee-regime-risk-20260419-081",
        }
        assert payload["meta"]["surfaces"]["committee_board"] in {"degraded", "ok", "stale"}


def test_cw03_detail_contract_returns_synthesis_and_allowed_actions() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/committees/committee-regime-risk-20260419-081",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["committee_id"] == "committee-regime-risk-20260419-081"
        assert payload["linked_request_id"] == "cr-20260419-014"
        assert payload["quorum_state"] == "quorum_met"
        assert payload["consensus_state"] == "sponsor_required"
        assert payload["sponsor_assignment"]["persona_id"] == "p-compliance-sponsor"
        assert payload["participant_roster"][0]["persona_label"] == "Macro Observer"
        assert payload["synthesis_summary"] == {
            "outcome": "pending",
            "rationale_ref": "workspace://consultation-rationales/cs-20260419-081",
            "evidence_refs": [
                "telemetry-vol-spike-20260419",
                "dp-20260419-014",
            ],
            "dissent_refs": [
                "workspace://consultation-dissent/cs-20260419-081/execution-lead"
            ],
        }
        assert payload["allowedActions"] == {
            "canRecordSponsorDecision": True,
        }
        assert payload["linked_evidence"][0] == {
            "id": "telemetry-vol-spike-20260419",
            "type": "evidence_link",
            "evidence_type": "telemetry",
            "artifact_ref": "artifact-042",
            "description": "Volatility spike - 2026-04-19",
            "link": "/telemetry/events/telemetry-vol-spike-20260419",
        }
        assert payload["meta"]["surfaces"]["committee_board"] in {"degraded", "ok", "stale"}


def test_cw03_record_sponsor_decision_executes_and_updates_projection() -> None:
    with _seeded_client() as client:
        response = client.post(
            "/api/v1/operator/commands",
            headers={
                "Authorization": OPERATOR_AUTH,
                "X-Idempotency-Key": "idmp-cw03-record-sponsor-decision",
            },
            json={
                "command_type": "RecordSponsorDecision",
                "committee_id": "committee-regime-risk-20260419-081",
                "sponsor_decision": "approved",
                "rationale_ref": "workspace://committee-rationales/committee-regime-risk-20260419-081/final",
                "note": "Compliance sponsor approves the risk review outcome",
            },
        )
        assert response.status_code == 202, response.text
        receipt = response.json()
        command_id = receipt["receipt_id"]

        status = client.get(
            f"/api/v1/operator/commands/{command_id}",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert status.status_code == 200, status.text
        payload = status.json()
        assert payload["status"] in {"submitted", "processing", "executed"}

        detail = client.get(
            "/api/v1/committees/committee-regime-risk-20260419-081",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert detail.status_code == 200, detail.text
        projection = detail.json()
        assert projection["sponsor_decision"] == "approved"
        assert projection["consensus_state"] == "reached"
        assert projection["synthesis_summary"]["rationale_ref"] == (
            "workspace://committee-rationales/committee-regime-risk-20260419-081/final"
        )
        assert projection["allowedActions"] == {
            "canRecordSponsorDecision": False,
        }


def test_cw03_detail_hides_record_sponsor_decision_for_reviewer_only() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/committees/committee-regime-risk-20260419-081",
            headers={"Authorization": REVIEWER_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["allowedActions"] == {
            "canRecordSponsorDecision": False,
        }


def test_cw03_detail_hides_record_sponsor_decision_without_sponsor_assignment() -> None:
    with _seeded_client() as client:
        consult = (
            bff_main.read_store._data["consultation_sessions"]["cs-20260419-081"]["metadata"]["consultation"]
        )
        consult["sponsor_session_id"] = None
        bff_main.read_store._save()

        response = client.get(
            "/api/v1/committees/committee-regime-risk-20260419-081",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["sponsor_assignment"] == {}
        assert payload["allowedActions"] == {
            "canRecordSponsorDecision": False,
        }


def test_cw03_record_sponsor_decision_persists_to_service_store() -> None:
    with tempfile.TemporaryDirectory() as td:
        seed_sessions = _default_consultation_sessions()
        consultation_store = os.path.join(td, "consultation_sessions.json")
        with open(consultation_store, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(seed_sessions, indent=2, ensure_ascii=True))

        with patch.dict(
            os.environ,
            {_CONSULTATION_SESSION_STORE_ENV: consultation_store},
            clear=False,
        ):
            service_store = _CommitteeReadStore(
                os.path.join(td, "service-read-surfaces.json"),
                allow_local_snapshot_fallback=False,
            )
            updated = service_store.record_sponsor_decision(
                "committee-regime-risk-20260419-081",
                sponsor_decision="conditional",
                rationale_ref="workspace://committee-rationales/committee-regime-risk-20260419-081/service",
                actor_id="operator-service-path",
                recorded_at="2026-04-20T06:10:00Z",
            )
            assert updated is not None
            assert updated["sponsor_decision"] == "conditional"

            reloaded_sessions = json.loads(Path(consultation_store).read_text(encoding="utf-8"))
            persisted = _get_committee("committee-regime-risk-20260419-081", reloaded_sessions)
            assert persisted is not None
            assert persisted["sponsor_decision"] == "conditional"
            assert persisted["sponsor_decided_by"] == "operator-service-path"
            assert persisted["synthesis_summary"]["rationale_ref"] == (
                "workspace://committee-rationales/committee-regime-risk-20260419-081/service"
            )


def test_cw03_record_sponsor_decision_creates_consultation_service_handoff_refs() -> None:
    with tempfile.TemporaryDirectory() as td:
        service_store = ConsultationStore(td)
        request = ConsultRequest(
            request_id="cr-service-committee-001",
            request_type=ConsultRequestType.EXECUTION_RISK,
            requested_by=ActorRef(actor_type="operator", actor_id="operator-service"),
            from_persona_id="persona-alpha",
            target_type="deployment_plan",
            target_id="plan-F-042",
            task="Review service-backed committee handoff.",
            consultation_type="risk_review",
            evidence_refs=["ev-service-001"],
            priority="normal",
            status=ConsultRequestStatus.IN_PROGRESS,
            linked_session_id="cs-service-committee-001",
            request_to_session_status="session_running",
            trace_id="trace-service-committee-001",
            created_at="2026-04-20T06:00:00Z",
            metadata={
                "consultation": {
                    "consultation_type": "risk_review",
                    "requester_session_id": "cs-service-committee-001",
                    "committee_session_ids": ["cm-service-committee-001"],
                    "committee_ref": "committee-service-001",
                    "quorum_state": "quorum_met",
                    "consensus_state": "sponsor_required",
                    "committee_started_at": "2026-04-20T06:01:00Z",
                    "sponsor_session_id": "cm-service-committee-001",
                    "sponsor_decision": None,
                    "sponsor_decided_at": None,
                    "sponsor_decided_by": None,
                    "escalation_reason": {
                        "trigger_rule": "risk_review",
                        "escalation_path": "committee_override",
                    },
                    "synthesis_summary": {
                        "outcome": "pending",
                        "rationale_ref": "workspace://consultation-rationales/service",
                        "evidence_refs": ["ev-service-001"],
                        "dissent_refs": [],
                    },
                    "evidence_refs": [
                        {
                            "id": "ev-service-001",
                            "type": "evidence_link",
                            "evidence_type": "deployment_plan",
                            "artifact_ref": "plan-F-042",
                            "description": "Service-owned deployment review evidence",
                            "link": "/deployments/plans/plan-F-042",
                        }
                    ],
                    "committee_participants": [
                        {
                            "session_id": "cm-service-committee-001",
                            "persona_id": "p-compliance-sponsor",
                            "role": "sponsor",
                            "participant_status": "active",
                            "status": "active",
                            "rationale_ref": "workspace://consultation-rationales/service/sponsor",
                        }
                    ],
                }
            },
        )
        service_store.put_request(request)
        service_store.append_audit(
            ConsultAuditEvent(
                audit_id="aud-service-request-created",
                request_id=request.request_id,
                actor_ref=ActorRef(actor_type="operator", actor_id="operator-service"),
                action="request_created",
                after_state="draft",
                trace_id=request.trace_id,
            )
        )
        service_store.put_memo(
            ConsultMemo(
                memo_id="mem-service-committee-001",
                request_id=request.request_id,
                memo_type=MemoType.REDTEAM_REPORT,
                author_type=AuthorType.PERSONA,
                author_ref="p-risk-analyst",
                target_type="deployment_plan",
                target_id="plan-F-042",
                summary="Service-backed red-team memo.",
                findings=[
                    ConsultFinding(
                        severity=FindingSeverity.MEDIUM,
                        category="execution",
                        claim="Deployment requires sponsor confirmation.",
                        evidence_refs=["ev-service-001"],
                        recommendation="approve with sponsor conditions",
                    )
                ],
                recommendation=Recommendation.APPROVE_WITH_CONDITIONS,
                status=MemoStatus.PUBLISHED,
                trace_id=request.trace_id,
                created_at="2026-04-20T06:05:00Z",
                published_at="2026-04-20T06:06:00Z",
            )
        )

        tracked_env = {
            "PANTHEON_BFF_CONSULTATION_DATA_DIR": os.environ.get("PANTHEON_BFF_CONSULTATION_DATA_DIR"),
        }
        original_store = bff_main.read_store
        os.environ["PANTHEON_BFF_CONSULTATION_DATA_DIR"] = td
        bff_main.read_store = _CommitteeReadStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        try:
            updated = bff_main.read_store.record_sponsor_decision(
                "committee-service-001",
                sponsor_decision="conditional",
                rationale_ref="workspace://committee-rationales/service/final",
                actor_id="operator-service-path",
                recorded_at="2026-04-20T06:10:00Z",
            )
            assert updated is not None
            assert updated["sponsor_decision"] == "conditional"
            handoff = updated["service_handoff"]
            assert handoff["handoff_id"].startswith("gh-")
            assert handoff["evidence_refs"] == ["ev-service-001"]
            assert "aud-service-request-created" in handoff["audit_refs"]
            assert any(ref.startswith("aud-") for ref in handoff["audit_refs"])

            replayed_store = ConsultationStore(td)
            handoffs = replayed_store.list_handoffs_for_request(request.request_id)
            assert len(handoffs) == 1
            assert handoffs[0].handoff_id == handoff["handoff_id"]
            assert handoffs[0].evidence_refs == ["ev-service-001"]
            assert handoffs[0].audit_refs == handoff["audit_refs"]
        finally:
            bff_main.read_store = original_store
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
