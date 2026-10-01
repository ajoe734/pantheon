"""Regression coverage for BFF-GOVERNANCE-EVIDENCE-REDACTION-SWEEP-001.

Exercises the real ``create_governance_router`` composition (identical
wiring to ``core/app_factory.py``: canonical ``redact_evidence_refs`` from
``models.py`` plus ``capabilities_for_identity`` from ``auth/policy.py``)
against the 11 previously-discarded-identity handlers that were repaired to
apply fail-closed evidence-ref redaction: approval decisions (list/detail),
the governance approval queue, the governance audit trail, the management
governance ledger, the consultation session/participants/outcome/transcript
surfaces, ``/bff/approvals``, ``/bff/reviews/{review_id}/audit``, and the
``/bff/approvals/{approval_id}`` alias.
"""
from __future__ import annotations

import copy
import os
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.models import redact_evidence_refs, utc_now


LOW_CAPABILITY_TOKEN = "Bearer op-sweep-1:operator"
FULL_CAPABILITY_TOKEN = "Bearer admin-sweep-1:admin"
# The "reviewer" role lacks artifact.read and risk.incident.read (unlike
# "operator", which holds both) -- it is the low-capability identity that
# actually exercises redaction on consult-request context_refs.
CONTEXT_REF_LOW_CAPABILITY_TOKEN = "Bearer reviewer-sweep-1:reviewer"

_ALERT_REF = {"ref_id": "ref-alert-ev", "type": "alert"}
_METRIC_REF = {"ref_id": "ref-metric-ev", "type": "metric"}
_JOB_REF = {"ref_id": "ref-job-ev", "type": "job"}
_MIXED_REFS = [copy.deepcopy(_ALERT_REF), copy.deepcopy(_METRIC_REF), copy.deepcopy(_JOB_REF)]

_APPROVAL_1: Dict[str, Any] = {
    "id": "approval-1",
    "decision_id": "approval-1",
    "tenant_id": "tenant-sweep",
    "decision_type": "DeploymentPlan",
    "decision_state": "pending",
    "risk_level": "high",
    "evidence_refs": copy.deepcopy(_MIXED_REFS),
}
_APPROVAL_2: Dict[str, Any] = {
    "id": "approval-2",
    "decision_id": "approval-2",
    "tenant_id": "tenant-sweep",
    "decision_type": "StrategySpec",
    "decision_state": "approved",
    "outcome": "approved",
    "evidence_refs": [],
}
_APPROVAL_3_DECISION_ONLY: Dict[str, Any] = {
    "id": "approval-3",
    "decision_id": "approval-3",
    "tenant_id": "tenant-sweep",
    "decision_type": "DeploymentPlan",
    "decision_state": "approved",
    "outcome": "approved",
    "evidence_refs": copy.deepcopy(_MIXED_REFS),
}
_AUDIT_1: Dict[str, Any] = {
    "id": "audit-1",
    "action_type": "approval.reviewed",
    "target_type": "Review",
    "target_id": "review-1",
    "actor": "operator-1",
    "timestamp": "2026-08-30T12:00:00Z",
    "evidence_refs": copy.deepcopy(_MIXED_REFS),
}
_AUDIT_2: Dict[str, Any] = {
    "id": "audit-2",
    "action_type": "approval.override",
    "target_type": "ApprovalDecision",
    "target_id": "approval-1",
    "actor": "operator-1",
    "timestamp": "2026-08-30T12:05:00Z",
    "evidence_refs": [],
}
_SESSION_1: Dict[str, Any] = {
    "session_id": "session-1",
    "id": "session-1",
    "persona_id": "persona-1",
    "status": "completed",
    "metadata": {
        "consultation": {
            "evidence_refs": copy.deepcopy(_MIXED_REFS),
        }
    },
}
_TRANSCRIPT_1: Dict[str, Any] = {
    "transcript_id": "tr-session-1",
    "session_id": "session-1",
    "events": [
        {"event_id": "ev-1", "sequence_no": 1, "evidence_refs": copy.deepcopy(_MIXED_REFS)},
        {"event_id": "ev-2", "sequence_no": 2, "evidence_refs": []},
    ],
}
_CONSULT_REQUEST_1: Dict[str, Any] = {
    "request_id": "consult-req-1",
    "status": "draft",
    "from_persona_id": "persona-1",
    "target_type": "strategy",
    "target_ref": "strategy-1",
    "task": "Review pre-deployment risk posture",
    "context_refs": [
        {"type": "artifact", "id": "consult-artifact-1"},
        {"type": "incident", "id": "consult-incident-1"},
    ],
    "priority": "high",
    "consultation_type": "pre_deployment",
    "created_at": "2026-08-30T12:00:00Z",
    "completed_at": None,
    "canceled_at": None,
    "linked_session_id": None,
    "request_to_session_status": "pending_session",
    "session_handoff": {
        "status": "pending_session",
        "linked_session_id": None,
        "session_route_href": None,
        "note": "",
    },
    "allowedActions": {"canCancel": True},
}


class _SweepStore:
    """Minimal read-store double exposing only what the router/service calls."""

    def __init__(
        self,
        *,
        two_approval_pages: bool = False,
        include_decision_only_entry: bool = False,
    ) -> None:
        self._two_approval_pages = two_approval_pages
        self._include_decision_only_entry = include_decision_only_entry

    def dataset_source(self, dataset: str) -> str:
        return "service_store"

    # Approval decisions ------------------------------------------------
    def list_approval_decisions(self, **_: Any) -> List[Dict[str, Any]]:
        items = [copy.deepcopy(_APPROVAL_1), copy.deepcopy(_APPROVAL_2)]
        if self._include_decision_only_entry:
            items.append(copy.deepcopy(_APPROVAL_3_DECISION_ONLY))
        return items

    def get_approval_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        for item in (_APPROVAL_1, _APPROVAL_2):
            if item["decision_id"] == decision_id:
                return copy.deepcopy(item)
        return None

    def list_approval_queue_items(self, **_: Any) -> List[Dict[str, Any]]:
        return [copy.deepcopy(_APPROVAL_1), copy.deepcopy(_APPROVAL_2)]

    # Audit ---------------------------------------------------------------
    def list_governance_audit_events(self, **_: Any) -> List[Dict[str, Any]]:
        return [copy.deepcopy(_AUDIT_1), copy.deepcopy(_AUDIT_2)]

    # Consultations ---------------------------------------------------------
    def get_persona(self, persona_id: str) -> Optional[Dict[str, Any]]:
        return {"id": persona_id, "persona_id": persona_id} if persona_id == "persona-1" else None

    def list_consultations_for_persona(self, persona_id: str, **_: Any) -> Optional[List[Dict[str, Any]]]:
        if persona_id != "persona-1":
            return None
        return [copy.deepcopy(_SESSION_1)]

    def get_consultation(self, session_id: str) -> Optional[Dict[str, Any]]:
        return copy.deepcopy(_SESSION_1) if session_id == "session-1" else None

    def get_consultation_participants(self, session_id: str) -> Optional[List[Dict[str, Any]]]:
        if session_id != "session-1":
            return None
        return [copy.deepcopy(_SESSION_1)]

    def get_consultation_outcome(self, session_id: str) -> Optional[Dict[str, Any]]:
        if session_id != "session-1":
            return None
        return copy.deepcopy(_SESSION_1)

    def get_consult_transcript(self, session_id: str, **_: Any) -> Optional[Dict[str, Any]]:
        return copy.deepcopy(_TRANSCRIPT_1) if session_id == "session-1" else None

    def get_consult_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        return copy.deepcopy(_CONSULT_REQUEST_1) if request_id == "consult-req-1" else None


def _tenant_identity(*args: Any, **kwargs: Any) -> Any:
    identity = auth_policy.extract_identity(*args, **kwargs)
    identity.claims = {**(identity.claims or {}), "tenant_id": "tenant-sweep"}
    return identity


def _build_app(
    store: Optional[_SweepStore] = None,
    *,
    capabilities_for_identity: Any = None,
) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(
        create_governance_router(
            read_surface=store or _SweepStore(),
            extract_identity=_tenant_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=utc_now,
            redact_evidence_refs=redact_evidence_refs,
            capabilities_for_identity=capabilities_for_identity or auth_policy.capabilities_for_identity,
        )
    )
    return app


@contextmanager
def _stub_auth_env():
    tracked = {
        "PANTHEON_BFF_AUTH_STUB": os.environ.get("PANTHEON_BFF_AUTH_STUB"),
        "PANTHEON_BFF_AUTH_MODE": os.environ.get("PANTHEON_BFF_AUTH_MODE"),
    }
    os.environ["PANTHEON_BFF_AUTH_STUB"] = "1"
    os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"
    try:
        yield
    finally:
        for key, value in tracked.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _assert_redacted(ref: Dict[str, Any], *, ref_id: str, required_capability: str) -> None:
    assert ref["redacted"] is True
    assert ref["ref_id"] == ref_id
    assert ref["required_capability"] == required_capability
    assert ref["reason"] == "insufficient_capability"


def _assert_mixed_refs_redacted_for_low_capability(refs: List[Dict[str, Any]]) -> None:
    assert len(refs) == 3
    # operator role holds risk.alert.read: alert ref passes through unchanged
    assert refs[0] == _ALERT_REF
    # operator role lacks metric.read and job.read: both are withheld
    _assert_redacted(refs[1], ref_id="ref-metric-ev", required_capability="metric.read")
    _assert_redacted(refs[2], ref_id="ref-job-ev", required_capability="job.read")


# --- Approval decisions (list + detail) ------------------------------------


def test_approval_decisions_list_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/approval-decisions",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        by_id = {item["decision_id"]: item for item in payload["data"]}
        _assert_mixed_refs_redacted_for_low_capability(by_id["approval-1"]["evidence_refs"])
        assert by_id["approval-2"]["evidence_refs"] == []
        assert payload["meta"]["redacted_evidence_count"] == 2


def test_approval_decisions_list_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/approval-decisions",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        by_id = {item["decision_id"]: item for item in payload["data"]}
        assert by_id["approval-1"]["evidence_refs"] == _MIXED_REFS
        assert payload["meta"]["redacted_evidence_count"] == 0


def test_approval_decision_detail_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/approval-decisions/approval-1",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        _assert_mixed_refs_redacted_for_low_capability(payload["data"]["evidence_refs"])
        assert payload["meta"]["redacted_evidence_count"] == 2


def test_approval_decision_detail_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/approval-decisions/approval-1",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"]["evidence_refs"] == _MIXED_REFS
        assert payload["meta"]["redacted_evidence_count"] == 0


def test_approval_decision_detail_fails_closed_when_capabilities_unresolvable() -> None:
    def _boom(identity: Any) -> List[str]:
        raise RuntimeError("capability lookup unavailable")

    with _stub_auth_env():
        client = TestClient(_build_app(capabilities_for_identity=_boom))
        response = client.get(
            "/api/v1/approval-decisions/approval-1",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        refs = payload["data"]["evidence_refs"]
        assert len(refs) == 3
        assert all(ref["redacted"] is True for ref in refs)
        assert payload["meta"]["redacted_evidence_count"] == 3


# --- /bff/approvals/{approval_id} alias -------------------------------------


def test_bff_approval_detail_alias_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/bff/approvals/approval-1",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        _assert_mixed_refs_redacted_for_low_capability(payload["data"]["evidence_refs"])
        assert payload["meta"]["redacted_evidence_count"] == 2


def test_bff_approval_detail_alias_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/bff/approvals/approval-1",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"]["evidence_refs"] == _MIXED_REFS
        assert payload["meta"]["redacted_evidence_count"] == 0


# --- Governance approval queue (paginated) ----------------------------------


def test_governance_approval_queue_redacted_count_scoped_to_returned_page() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())

        page1 = client.get(
            "/api/v1/operator/governance/approval-queue",
            params={"page_size": 1},
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert page1.status_code == 200, page1.text
        page1_payload = page1.json()
        assert len(page1_payload["items"]) == 1
        assert page1_payload["items"][0]["decision_id"] == "approval-1"
        _assert_mixed_refs_redacted_for_low_capability(page1_payload["items"][0]["evidence_refs"])
        assert page1_payload["meta"]["redacted_evidence_count"] == 2

        next_token = page1_payload["page_info"]["next_page_token"]
        assert next_token
        page2 = client.get(
            "/api/v1/operator/governance/approval-queue",
            params={"page_size": 1, "page_token": next_token},
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        page2_payload = page2.json()
        assert page2_payload["items"][0]["decision_id"] == "approval-2"
        # page 2's item carries no evidence_refs, so nothing withheld on this page
        assert page2_payload["meta"]["redacted_evidence_count"] == 0


def test_governance_approval_queue_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/operator/governance/approval-queue",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        by_id = {item["decision_id"]: item for item in payload["items"]}
        assert by_id["approval-1"]["evidence_refs"] == _MIXED_REFS
        assert payload["meta"]["redacted_evidence_count"] == 0


# --- Governance audit trail (paginated) + /bff/reviews/{id}/audit ----------


def test_governance_audit_trail_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/operator/governance/audit",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        by_id = {item["id"]: item for item in payload["items"]}
        _assert_mixed_refs_redacted_for_low_capability(by_id["audit-1"]["evidence_refs"])
        assert by_id["audit-2"]["evidence_refs"] == []
        assert payload["meta"]["redacted_evidence_count"] == 2


def test_governance_audit_trail_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/operator/governance/audit",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        by_id = {item["id"]: item for item in payload["items"]}
        assert by_id["audit-1"]["evidence_refs"] == _MIXED_REFS
        assert payload["meta"]["redacted_evidence_count"] == 0


def test_bff_review_audit_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/bff/reviews/review-1/audit",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["events"]) == 1
        _assert_mixed_refs_redacted_for_low_capability(payload["events"][0]["evidence_refs"])
        assert payload["meta"]["redacted_evidence_count"] == 2


def test_bff_review_audit_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/bff/reviews/review-1/audit",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["events"][0]["evidence_refs"] == _MIXED_REFS
        assert payload["meta"]["redacted_evidence_count"] == 0


# --- /bff/approvals (pending-approvals compatibility surface) --------------


def test_bff_approvals_list_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/bff/approvals",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        pending = next(item for item in payload["items"] if item["decision_id"] == "approval-1")
        _assert_mixed_refs_redacted_for_low_capability(pending["evidence_refs"])
        assert payload["meta"]["redacted_evidence_count"] == 2


def test_bff_approvals_list_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/bff/approvals",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        pending = next(item for item in payload["items"] if item["decision_id"] == "approval-1")
        assert pending["evidence_refs"] == _MIXED_REFS
        assert payload["meta"]["redacted_evidence_count"] == 0


# --- Management governance ledger: all three evidence sources ---------------


def test_governance_ledger_redacts_all_three_evidence_sources_for_low_capability_identity() -> None:
    # The ledger dedupes approval entries by decision_id across the
    # approval_queue_items/approval_decisions datasets (first-seen wins), so
    # approval-1 surfaces once as an "approval" source_type entry sourced
    # from approval_queue_items; the audit(-override) sources each surface
    # their own entry. All three underlying datasets
    # (approval_queue_items, approval_decisions, governance_audit_events) feed the ledger's evidence redaction path.
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/bff/management/governance-ledger",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        items = payload["data"]["items"]

        approval_entry = next(
            item for item in items
            if item["source_type"] == "approval" and item["target_id"] == "approval-1"
        )
        assert approval_entry["source_dataset"] == "approval_queue_items"
        _assert_mixed_refs_redacted_for_low_capability(approval_entry["evidence_refs"])

        audit_approval_entry = next(
            item for item in items
            if item["source_dataset"] == "governance_audit_events" and item["source_type"] == "approval"
        )
        _assert_mixed_refs_redacted_for_low_capability(audit_approval_entry["evidence_refs"])

        override_entry = next(item for item in items if item["source_type"] == "override")
        assert override_entry["evidence_refs"] == []

        # 2 entries carry the 3-ref mixed fixture (approval-1,
        # audit-approval); 2 of each 3 refs are withheld (metric/job) = 4.
        assert payload["meta"]["redacted_evidence_count"] == 4


def test_governance_ledger_includes_decision_only_approval_entry() -> None:
    # approval-3 only exists in the approval_decisions dataset (never in
    # approval_queue_items), so it is not shadowed by the dedup keyed on
    # decision_id and must surface with source_dataset=approval_decisions.
    store = _SweepStore(include_decision_only_entry=True)

    with _stub_auth_env():
        low_client = TestClient(_build_app(store))
        low_response = low_client.get(
            "/bff/management/governance-ledger",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert low_response.status_code == 200, low_response.text
        low_items = low_response.json()["data"]["items"]
        decision_only_entry = next(
            item for item in low_items
            if item["source_type"] == "approval" and item["target_id"] == "approval-3"
        )
        assert decision_only_entry["source_dataset"] == "approval_decisions"
        _assert_mixed_refs_redacted_for_low_capability(decision_only_entry["evidence_refs"])

        full_client = TestClient(_build_app(store))
        full_response = full_client.get(
            "/bff/management/governance-ledger",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert full_response.status_code == 200, full_response.text
        full_items = full_response.json()["data"]["items"]
        decision_only_entry_full = next(
            item for item in full_items
            if item["source_type"] == "approval" and item["target_id"] == "approval-3"
        )
        assert decision_only_entry_full["source_dataset"] == "approval_decisions"
        assert decision_only_entry_full["evidence_refs"] == _MIXED_REFS


def test_governance_ledger_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/bff/management/governance-ledger",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        items = payload["data"]["items"]
        approval_entry = next(
            item for item in items
            if item["source_type"] == "approval" and item["target_id"] == "approval-1"
        )
        assert approval_entry["evidence_refs"] == _MIXED_REFS
        assert payload["meta"]["redacted_evidence_count"] == 0


def test_governance_ledger_fails_closed_when_capabilities_unresolvable() -> None:
    def _boom(identity: Any) -> List[str]:
        raise RuntimeError("capability lookup unavailable")

    with _stub_auth_env():
        client = TestClient(_build_app(capabilities_for_identity=_boom))
        response = client.get(
            "/bff/management/governance-ledger",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        # 2 entries carry the 3-ref mixed fixture; fail-closed withholds all
        # 3 mapped-kind refs on each = 6.
        assert payload["meta"]["redacted_evidence_count"] == 6


# --- Consultation session surfaces ------------------------------------------


def test_list_consultations_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/personas/persona-1/consultations",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        session = payload["data"][0]
        _assert_mixed_refs_redacted_for_low_capability(
            session["metadata"]["consultation"]["evidence_refs"]
        )
        assert payload["meta"]["supporting_counts"]["redacted_evidence_count"] == 2


def test_list_consultations_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/personas/persona-1/consultations",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        session = payload["data"][0]
        assert session["metadata"]["consultation"]["evidence_refs"] == _MIXED_REFS
        assert payload["meta"]["supporting_counts"]["redacted_evidence_count"] == 0


def test_get_consultation_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/consultations/session-1",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        _assert_mixed_refs_redacted_for_low_capability(
            payload["data"]["metadata"]["consultation"]["evidence_refs"]
        )
        assert payload["meta"]["supporting_counts"]["redacted_evidence_count"] == 2


def test_get_consultation_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/consultations/session-1",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"]["metadata"]["consultation"]["evidence_refs"] == _MIXED_REFS
        assert payload["meta"]["supporting_counts"]["redacted_evidence_count"] == 0


def test_get_consultation_participants_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/consultations/session-1/participants",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        participant = payload["data"][0]
        _assert_mixed_refs_redacted_for_low_capability(
            participant["metadata"]["consultation"]["evidence_refs"]
        )
        assert payload["meta"]["supporting_counts"]["redacted_evidence_count"] == 2


def test_get_consultation_participants_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/consultations/session-1/participants",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        participant = payload["data"][0]
        assert participant["metadata"]["consultation"]["evidence_refs"] == _MIXED_REFS
        assert payload["meta"]["supporting_counts"]["redacted_evidence_count"] == 0


def test_get_consultation_outcome_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/consultations/session-1/outcome",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        _assert_mixed_refs_redacted_for_low_capability(
            payload["data"]["metadata"]["consultation"]["evidence_refs"]
        )
        assert payload["meta"]["supporting_counts"]["redacted_evidence_count"] == 2


def test_get_consultation_outcome_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/consultations/session-1/outcome",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"]["metadata"]["consultation"]["evidence_refs"] == _MIXED_REFS
        assert payload["meta"]["supporting_counts"]["redacted_evidence_count"] == 0


def test_get_consultation_transcript_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/consultations/session-1/transcript",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        events = {event["event_id"]: event for event in payload["events"]}
        _assert_mixed_refs_redacted_for_low_capability(events["ev-1"]["evidence_refs"])
        assert events["ev-2"]["evidence_refs"] == []
        assert payload["meta"]["redacted_evidence_count"] == 2


def test_get_consultation_transcript_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/consultations/session-1/transcript",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        events = {event["event_id"]: event for event in payload["events"]}
        assert events["ev-1"]["evidence_refs"] == _MIXED_REFS
        assert payload["meta"]["redacted_evidence_count"] == 0


def test_consultation_participants_fail_closed_when_capabilities_unresolvable() -> None:
    def _boom(identity: Any) -> List[str]:
        raise RuntimeError("capability lookup unavailable")

    with _stub_auth_env():
        client = TestClient(_build_app(capabilities_for_identity=_boom))
        response = client.get(
            "/api/v1/consultations/session-1/participants",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        refs = payload["data"][0]["metadata"]["consultation"]["evidence_refs"]
        assert len(refs) == 3
        assert all(ref["redacted"] is True for ref in refs)
        assert payload["meta"]["supporting_counts"]["redacted_evidence_count"] == 3


# --- Consult request detail (context_refs) ----------------------------------


def test_get_consult_request_redacts_context_refs_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/consult/requests/consult-req-1",
            headers={"Authorization": CONTEXT_REF_LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        refs = payload["context_refs"]
        assert len(refs) == 2
        # "reviewer" role lacks both artifact.read and risk.incident.read.
        _assert_redacted(refs[0], ref_id="consult-artifact-1", required_capability="artifact.read")
        _assert_redacted(refs[1], ref_id="consult-incident-1", required_capability="risk.incident.read")
        assert payload["meta"]["redacted_evidence_count"] == 2


def test_get_consult_request_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app())
        response = client.get(
            "/api/v1/consult/requests/consult-req-1",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["context_refs"] == _CONSULT_REQUEST_1["context_refs"]
        assert payload["meta"]["redacted_evidence_count"] == 0


def test_get_consult_request_fails_closed_when_capabilities_unresolvable() -> None:
    def _boom(identity: Any) -> List[str]:
        raise RuntimeError("capability lookup unavailable")

    with _stub_auth_env():
        client = TestClient(_build_app(capabilities_for_identity=_boom))
        response = client.get(
            "/api/v1/consult/requests/consult-req-1",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        refs = payload["context_refs"]
        assert len(refs) == 2
        assert all(ref["redacted"] is True for ref in refs)
        assert payload["meta"]["redacted_evidence_count"] == 2
