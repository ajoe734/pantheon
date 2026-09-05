"""Foreground regression suite for BFF-GOVERNANCE-CW-CONTRACT-CORRECTIVE-PREREQUISITE-001.

Exercises the *actual* production governance router/service/port wiring
(``services.control_plane.bff.governance.router``,
``services.control_plane.bff.governance.service.GovernanceService``, and
``services.control_plane.bff.ports.operations_consultation.DomainConsultationPort``)
against the published CW-01 / CW-03 / CW-04 BFF contracts.

Test doubles here provide only data/I/O (a real ``ConsultationStore`` on a
temp dir, and a typed fake ``ConsultationServiceClient`` recording the exact
payload handed to it) -- they never reimplement an endpoint, a projection, or
policy. Route/service/port composition is exercised through
``services.control_plane.bff.main`` (the real FastAPI app) for CW-01/CW-03,
and directly against ``GovernanceService``/``DomainConsultationPort`` for the
6x4 subtype/priority table and the CW-04 gate matrix.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("RANKING_STORE_DSN", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("RANKING_STORE_BOOTSTRAP", "0")
os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
os.environ.setdefault("PANTHEON_BFF_AUTH_MODE", "permissive")

from services.control_plane.bff import main as bff_main  # noqa: E402
from services.control_plane.bff.governance.service import GovernanceService  # noqa: E402
from services.control_plane.bff.models import redact_evidence_refs as _canonical_redact_evidence_refs  # noqa: E402
from services.control_plane.bff.ports.operations_consultation import (  # noqa: E402
    DomainConsultationPort,
    create_in_memory_operations_consultation_port,
    create_operations_consultation_port,
)
from services.control_plane.bff.ports.read_surface_ports import (  # noqa: E402
    create_read_surface_ports,
)
from services.consultation.models import (  # noqa: E402
    ActorRef,
    AuthorType,
    ConsultFinding,
    ConsultMemo,
    ConsultPriority,
    ConsultRequest,
    ConsultRequestStatus,
    ConsultRequestType,
    FindingSeverity,
    MemoStatus,
    MemoType,
    Recommendation,
)
from services.consultation.store import ConsultationStore  # noqa: E402


OPERATOR_AUTH = "Bearer test-operator:operator"
REVIEWER_AUTH = "Bearer test-reviewer:reviewer"

# The exact published CW-01 six-subtype / four-priority tables restored from
# the pre-extraction lineage (commit aba0cd0087f297dadfff5769d5a97f4bdc3215e8,
# services/control-plane/bff/read_store.py:88-105).
_EXPECTED_REQUEST_TYPE = {
    "pre_deployment": ConsultRequestType.STRATEGY_REVIEW,
    "risk_review": ConsultRequestType.EXECUTION_RISK,
    "macro_regime_shift": ConsultRequestType.STRATEGY_REVIEW,
    "incident_response": ConsultRequestType.INCIDENT,
    "policy_change": ConsultRequestType.PERSONA_POLICY,
    "general": ConsultRequestType.STRATEGY_REVIEW,
}
_EXPECTED_PRIORITY = {
    "low": ConsultPriority.LOW,
    "normal": ConsultPriority.NORMAL,
    "high": ConsultPriority.HIGH,
    "critical": ConsultPriority.URGENT,
}


class _FakeConsultationClient:
    """Typed I/O double for ConsultationServiceClient: records payloads only."""

    def __init__(self) -> None:
        self.created_payloads: List[Dict[str, Any]] = []
        self._requests: Dict[str, Dict[str, Any]] = {}

    def list_requests(self) -> List[Dict[str, Any]]:
        return list(self._requests.values())

    def list_handoffs(self) -> List[Dict[str, Any]]:
        return []

    def get_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        return self._requests.get(request_id)

    def create_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.created_payloads.append(dict(payload))
        request_id = f"cr-client-{len(self._requests) + 1}"
        record = {**payload, "request_id": request_id}
        self._requests[request_id] = record
        return record

    def cancel_request(self, request_id: str, *, actor_id: str, canceled_at: str) -> Optional[Dict[str, Any]]:
        record = self._requests.get(request_id)
        if record is None:
            return None
        record["status"] = "cancelled"
        record["canceled_at"] = canceled_at
        return record


def _seeded_consultation_client(app_module=bff_main) -> _FakeConsultationClient:
    return _FakeConsultationClient()


# ---------------------------------------------------------------------------
# CW-01: consult request contract, mapping restoration, fail-closed create
# ---------------------------------------------------------------------------


def test_cw01_domain_port_maps_full_six_by_four_table_client_branch() -> None:
    """Actual DomainConsultationPort, client branch: verifies canonical I/O payload."""
    client = _FakeConsultationClient()
    port = DomainConsultationPort(client=client)
    for consultation_type, expected_request_type in _EXPECTED_REQUEST_TYPE.items():
        for priority, expected_priority in _EXPECTED_PRIORITY.items():
            client.created_payloads.clear()
            detail = port.create_consult_request(
                from_persona_id="persona-alpha",
                target_type="persona",
                target_ref="persona-beta",
                task=f"{consultation_type}/{priority}",
                context_refs=[{"type": "artifact", "id": "art-1"}],
                priority=priority,
                consultation_type=consultation_type,
                actor_id="operator-1",
                created_at="2026-09-05T00:00:00Z",
            )
            assert len(client.created_payloads) == 1
            sent = client.created_payloads[0]
            assert sent["request_type"] == expected_request_type.value, (consultation_type, priority)
            assert sent["priority"] == expected_priority.value, (consultation_type, priority)
            # The public literal must survive verbatim in the response and metadata.
            assert detail["consultation_type"] == consultation_type
            assert detail["priority"] == priority
            assert sent["metadata"]["consultation_type"] == consultation_type
            assert sent["metadata"]["bff_priority"] == priority


def test_cw01_domain_port_maps_full_six_by_four_table_local_store_branch() -> None:
    """Actual DomainConsultationPort, local typed-store branch (no client configured)."""
    with tempfile.TemporaryDirectory() as tmp:
        for consultation_type, expected_request_type in _EXPECTED_REQUEST_TYPE.items():
            for priority, expected_priority in _EXPECTED_PRIORITY.items():
                port = DomainConsultationPort(data_dir=tmp)
                detail = port.create_consult_request(
                    from_persona_id="persona-alpha",
                    target_type="persona",
                    target_ref="persona-beta",
                    task=f"{consultation_type}/{priority}",
                    context_refs=[{"type": "artifact", "id": "art-1"}],
                    priority=priority,
                    consultation_type=consultation_type,
                    actor_id="operator-1",
                    created_at="2026-09-05T00:00:00Z",
                )
                store = ConsultationStore(tmp)
                stored = store.get_request(detail["request_id"])
                assert stored is not None
                assert stored.request_type == expected_request_type, (consultation_type, priority)
                assert stored.priority == expected_priority, (consultation_type, priority)
                # Public literal survives verbatim through the read-back projection.
                assert detail["consultation_type"] == consultation_type
                assert detail["priority"] == priority
                readback = port.get_consult_request(detail["request_id"])
                assert readback["consultation_type"] == consultation_type
                assert readback["priority"] == priority


def test_cw01_create_missing_provider_fails_closed_not_synthetic_success() -> None:
    """GovernanceService must not synthesize created+UUID+canCancel when the port has nothing."""

    class _NoneReturningStore:
        def create_consult_request(self, **_: Any) -> None:
            return None

    service = GovernanceService(_NoneReturningStore())
    with pytest.raises(RuntimeError):
        service.create_consult_request(
            {
                "from_persona_id": "persona-alpha",
                "target_type": "persona",
                "target_ref": "persona-beta",
                "task": "x",
                "context_refs": [],
                "priority": "normal",
                "consultation_type": "general",
            },
            identity=type("Identity", (), {"operator_id": "op-1", "roles": {"operator"}})(),
        )


@pytest.mark.parametrize(
    "field,payload_override",
    [
        ("consultation_type", {"consultation_type": "strategy_review"}),  # internal enum name, not public
        ("priority", {"priority": "urgent"}),  # internal enum name, not public "critical"
        ("context_refs", {"context_refs": [{"type": "bogus_type", "id": "1"}]}),
        ("context_refs", {"context_refs": [{"type": "artifact", "id": ""}]}),
        ("target_type", {"target_type": "not-a-target"}),
    ],
)
def test_cw01_create_rejects_invalid_published_fields(field: str, payload_override: Dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        payload = {
            "from_persona_id": "persona-alpha",
            "target_type": "persona",
            "target_ref": "persona-beta",
            "task": "x",
            "context_refs": [{"type": "artifact", "id": "art-1"}],
            "priority": "normal",
            "consultation_type": "general",
        }
        payload.update(payload_override)
        with TestClient(bff_main.app) as client:
            os.environ["PANTHEON_BFF_CONSULTATION_DATA_DIR"] = tmp
            response = client.post(
                "/api/v1/consult/requests", headers={"Authorization": OPERATOR_AUTH}, json=payload
            )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"]["precondition_failed"] == field


def test_cw01_create_list_detail_cancel_full_lifecycle_through_production_app() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["PANTHEON_BFF_CONSULTATION_DATA_DIR"] = tmp
        try:
            client = TestClient(bff_main.app)
            created = client.post(
                "/api/v1/consult/requests",
                headers={"Authorization": OPERATOR_AUTH},
                json={
                    "from_persona_id": "persona-alpha",
                    "target_type": "persona",
                    "target_ref": "persona-beta",
                    "task": "Should we proceed?",
                    "context_refs": [{"type": "deployment_plan", "id": "dp-1"}],
                    "priority": "critical",
                    "consultation_type": "risk_review",
                },
            )
            assert created.status_code == 200, created.text
            body = created.json()
            assert body["status"] == "created"
            assert body["request_to_session_status"] == "pending_session"
            assert body["allowedActions"] == {"canCancel": True}
            request_id = body["request_id"]

            detail = client.get(
                f"/api/v1/consult/requests/{request_id}", headers={"Authorization": OPERATOR_AUTH}
            )
            assert detail.status_code == 200, detail.text
            detail_body = detail.json()
            assert detail_body["priority"] == "critical"
            assert detail_body["consultation_type"] == "risk_review"
            assert detail_body["session_handoff"]["status"] == "pending_session"
            assert detail_body["links"]["self"] == f"/api/v1/consult/requests/{request_id}"
            assert "workbench_detail" in detail_body["links"]
            assert detail_body["meta"]["surfaces"]["consult_request_detail"]["status"] == "ok"
            assert "data" not in detail_body

            listing = client.get(
                "/api/v1/consult/requests", headers={"Authorization": OPERATOR_AUTH}
            )
            assert listing.status_code == 200, listing.text
            listing_body = listing.json()
            assert any(row["request_id"] == request_id for row in listing_body["data"])
            assert listing_body["meta"]["surfaces"]["consult_request_list"]["status"] == "ok"

            canceled = client.post(
                f"/api/v1/consult/requests/{request_id}/cancel",
                headers={"Authorization": OPERATOR_AUTH},
            )
            assert canceled.status_code == 200, canceled.text
            canceled_body = canceled.json()
            assert canceled_body["status"] == "canceled"
            assert canceled_body["allowedActions"] == {"canCancel": False}
            assert "data" not in canceled_body

            recanceled = client.post(
                f"/api/v1/consult/requests/{request_id}/cancel",
                headers={"Authorization": OPERATOR_AUTH},
            )
            assert recanceled.status_code == 409, recanceled.text
        finally:
            os.environ.pop("PANTHEON_BFF_CONSULTATION_DATA_DIR", None)


# ---------------------------------------------------------------------------
# CW-03: committee board GET/command convergence
# ---------------------------------------------------------------------------


def _seed_committee_request(data_dir: str) -> None:
    store = ConsultationStore(data_dir)
    request = ConsultRequest(
        request_id="cr-committee-001",
        request_type=ConsultRequestType.EXECUTION_RISK,
        requested_by=ActorRef(actor_type="operator", actor_id="operator-1"),
        from_persona_id="persona-alpha",
        target_type="deployment_plan",
        target_id="plan-1",
        task="Review committee handoff.",
        consultation_type="risk_review",
        priority="normal",
        status=ConsultRequestStatus.IN_PROGRESS,
        linked_session_id="cs-committee-001",
        request_to_session_status="session_running",
        trace_id="trace-committee-001",
        created_at="2026-09-05T00:00:00Z",
        metadata={
            "consultation": {
                "consultation_type": "risk_review",
                "requester_session_id": "cs-committee-001",
                "committee_session_ids": ["cm-committee-001"],
                "committee_ref": "committee-001",
                "quorum_state": "quorum_met",
                "consensus_state": "sponsor_required",
                "committee_started_at": "2026-09-05T00:01:00Z",
                "sponsor_session_id": "cm-committee-001",
                "sponsor_decision": None,
                "escalation_reason": {"trigger_rule": "risk_review"},
                "synthesis_summary": {"outcome": "pending", "evidence_refs": [], "dissent_refs": []},
                "committee_participants": [
                    {
                        "session_id": "cm-committee-001",
                        "persona_id": "p-sponsor",
                        "role": "sponsor",
                        "participant_status": "active",
                        "status": "active",
                    },
                ],
            }
        },
    )
    store.put_request(request)


def test_cw03_list_and_detail_share_committee_projection_owner() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _seed_committee_request(tmp)
        os.environ["PANTHEON_BFF_CONSULTATION_DATA_DIR"] = tmp
        try:
            client = TestClient(bff_main.app)
            listing = client.get(
                "/api/v1/committees?consensus_state=sponsor_required",
                headers={"Authorization": OPERATOR_AUTH},
            )
            assert listing.status_code == 200, listing.text
            listing_body = listing.json()
            assert listing_body["page_info"]["total"] == 1
            assert listing_body["data"][0]["committee_id"] == "committee-001"
            assert listing_body["meta"]["surfaces"]["committee_board"] == "ok"

            detail = client.get(
                "/api/v1/committees/committee-001", headers={"Authorization": OPERATOR_AUTH}
            )
            assert detail.status_code == 200, detail.text
            detail_body = detail.json()
            assert detail_body["allowedActions"] == {"canRecordSponsorDecision": True}
            assert detail_body["sponsor_assignment"]["persona_id"] == "p-sponsor"
            assert detail_body["meta"]["surfaces"]["committee_board"] == "ok"

            reviewer_detail = client.get(
                "/api/v1/committees/committee-001", headers={"Authorization": REVIEWER_AUTH}
            )
            assert reviewer_detail.json()["allowedActions"] == {"canRecordSponsorDecision": False}
        finally:
            os.environ.pop("PANTHEON_BFF_CONSULTATION_DATA_DIR", None)


def test_cw03_record_sponsor_decision_command_reuses_same_read_projection() -> None:
    """The command validator's authorization decision must equal the GET projection's.

    A GET-vs-command policy fork would let a client with a stale GET pass a
    command the current read projection would forbid.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _seed_committee_request(tmp)
        os.environ["PANTHEON_BFF_CONSULTATION_DATA_DIR"] = tmp
        try:
            client = TestClient(bff_main.app)
            detail = client.get(
                "/api/v1/committees/committee-001", headers={"Authorization": OPERATOR_AUTH}
            ).json()
            assert detail["allowedActions"]["canRecordSponsorDecision"] is True

            accepted = client.post(
                "/api/v1/operator/commands",
                headers={"Authorization": OPERATOR_AUTH, "X-Idempotency-Key": "idmp-cw03-accept"},
                json={
                    "command_type": "RecordSponsorDecision",
                    "committee_id": "committee-001",
                    "sponsor_decision": "approved",
                    "rationale_ref": "workspace://committee-rationales/committee-001/final",
                    "note": "approve",
                },
            )
            # Validation passes (202); the async execution write-path gap is a
            # separate, already-tracked DOMAIN-WRITERS concern, not this
            # corrective's scope.
            assert accepted.status_code == 202, accepted.text

            reviewer_reject = client.post(
                "/api/v1/operator/commands",
                headers={"Authorization": REVIEWER_AUTH, "X-Idempotency-Key": "idmp-cw03-reject"},
                json={
                    "command_type": "RecordSponsorDecision",
                    "committee_id": "committee-001",
                    "sponsor_decision": "approved",
                    "rationale_ref": "workspace://committee-rationales/committee-001/final",
                    "note": "approve",
                },
            )
            assert reviewer_reject.status_code == 403, reviewer_reject.text

            missing_committee = client.post(
                "/api/v1/operator/commands",
                headers={"Authorization": OPERATOR_AUTH, "X-Idempotency-Key": "idmp-cw03-missing"},
                json={
                    "command_type": "RecordSponsorDecision",
                    "committee_id": "committee-does-not-exist",
                    "sponsor_decision": "approved",
                    "rationale_ref": "workspace://committee-rationales/does-not-exist/final",
                    "note": "approve",
                },
            )
            assert missing_committee.status_code == 404, missing_committee.text
        finally:
            os.environ.pop("PANTHEON_BFF_CONSULTATION_DATA_DIR", None)


# ---------------------------------------------------------------------------
# CW-04: red-team memo projection and governance-review gate
# ---------------------------------------------------------------------------


def _seed_memo(
    data_dir: str,
    *,
    memo_id: str = "mem-001",
    status: MemoStatus = MemoStatus.PUBLISHED,
    target_type: str = "deployment_plan",
) -> None:
    store = ConsultationStore(data_dir)
    request = ConsultRequest(
        request_id=f"cr-{memo_id}",
        request_type=ConsultRequestType.EXECUTION_RISK,
        requested_by=ActorRef(actor_type="operator", actor_id="op-1"),
        from_persona_id="persona-alpha",
        target_type="deployment_plan",
        target_id="plan-1",
        task="review",
        consultation_type="risk_review",
        priority="normal",
        status=ConsultRequestStatus.IN_PROGRESS,
        linked_session_id=f"cs-{memo_id}",
        request_to_session_status="session_running",
        trace_id=f"trace-{memo_id}",
        created_at="2026-09-05T00:00:00Z",
    )
    store.put_request(request)
    store.put_memo(
        ConsultMemo(
            memo_id=memo_id,
            request_id=request.request_id,
            memo_type=MemoType.REDTEAM_REPORT,
            author_type=AuthorType.PERSONA,
            author_ref="p-risk-analyst",
            target_type=target_type,
            target_id="plan-1",
            summary="Deployment risk memo.",
            findings=[
                ConsultFinding(
                    severity=FindingSeverity.MEDIUM,
                    category="execution",
                    claim="c",
                    evidence_refs=["ev-1"],
                    recommendation="approve with conditions",
                )
            ],
            recommendation=Recommendation.APPROVE_WITH_CONDITIONS,
            status=status,
            trace_id=f"trace-{memo_id}",
            created_at="2026-09-05T00:05:00Z",
            published_at="2026-09-05T00:06:00Z" if status == MemoStatus.PUBLISHED else None,
        )
    )


def test_cw04_list_and_detail_projection_through_production_app() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _seed_memo(tmp)
        os.environ["PANTHEON_BFF_CONSULTATION_DATA_DIR"] = tmp
        try:
            client = TestClient(bff_main.app)
            listing = client.get("/api/v1/consult/memos", headers={"Authorization": OPERATOR_AUTH})
            assert listing.status_code == 200, listing.text
            listing_body = listing.json()
            assert listing_body["items"][0]["memo_type"] == "red_team"
            assert listing_body["meta"]["surfaces"]["redteam_memo"]["state"] == "ok"

            detail = client.get("/api/v1/consult/memos/mem-001", headers={"Authorization": REVIEWER_AUTH})
            assert detail.status_code == 200, detail.text
            detail_body = detail.json()
            assert detail_body["memo_type"] == "red_team"
            assert detail_body["lifecycle_state"] == "published"
            assert detail_body["recommendations"] == ["approve with conditions"]
            assert detail_body["allowedActions"] == {"canInitiateGovernanceReview": True}
            assert "data" not in detail_body

            operator_view = client.get(
                "/api/v1/consult/memos/mem-001", headers={"Authorization": OPERATOR_AUTH}
            ).json()
            # Operator lacks reviewer/approver/admin/governance_committee authority.
            assert operator_view["allowedActions"] == {"canInitiateGovernanceReview": False}
        finally:
            os.environ.pop("PANTHEON_BFF_CONSULTATION_DATA_DIR", None)


def test_cw04_governance_review_gate_truth_table() -> None:
    reviewer_identity = type("Identity", (), {"operator_id": "op-1", "roles": {"reviewer"}})()
    operator_identity = type("Identity", (), {"operator_id": "op-2", "roles": {"operator"}})()

    base_memo = {
        "memo_id": "mem-x",
        "lifecycle_state": "published",
        "status": "published",
        "governance_target": {"target_type": "deployment_plan", "target_id": "plan-1"},
        "active_governance_review_id": None,
        "suppressed": False,
        "withdrawn": False,
        "surface_state": "ok",
    }

    class _StaticStore:
        def __init__(self, memo: Dict[str, Any]) -> None:
            self._memo = memo

        def get_consult_memo(self, memo_id: str) -> Optional[Dict[str, Any]]:
            return self._memo if memo_id == self._memo["memo_id"] else None

        def dataset_source(self, dataset: str) -> str:
            return "typed_store"

    def _allowed(memo_override: Dict[str, Any], identity: Any) -> bool:
        memo = {**base_memo, **memo_override}
        service = GovernanceService(_StaticStore(memo))
        projection = service.consult_memo_projection(
            "mem-x", identity=identity, snapshot_at="2026-09-05T00:00:00Z"
        )
        return projection["allowedActions"]["canInitiateGovernanceReview"]

    # Positive case.
    assert _allowed({}, reviewer_identity) is True
    # Negative: draft lifecycle.
    assert _allowed({"lifecycle_state": "draft", "status": "draft"}, reviewer_identity) is False
    # Negative: no authority role.
    assert _allowed({}, operator_identity) is False
    # Negative: active review already in flight.
    assert _allowed({"active_governance_review_id": "gr-1"}, reviewer_identity) is False
    # Negative: suppressed.
    assert _allowed({"suppressed": True}, reviewer_identity) is False
    # Negative: withdrawn.
    assert _allowed({"withdrawn": True}, reviewer_identity) is False
    # Negative: unsupported target type.
    assert _allowed({"governance_target": {"target_type": "persona", "target_id": "p-1"}}, reviewer_identity) is False
    # Negative: no valid target at all.
    assert _allowed({"governance_target": {}}, reviewer_identity) is False
    # Negative: surface degraded/unavailable must force false even with authority.
    assert _allowed({"surface_state": "degraded"}, reviewer_identity) is False
    assert _allowed({"surface_state": "unavailable"}, reviewer_identity) is False


# ---------------------------------------------------------------------------
# Composition / wiring probe
# ---------------------------------------------------------------------------


def test_composition_committee_route_has_single_owner_and_no_reverse_import() -> None:
    """Production DI: the governance router resolves to the single GovernanceService
    owner backed by the composed read_store, and governance.service does not
    import main (no reverse dependency, no namespace-proxy shortcut)."""
    import inspect

    from services.control_plane.bff.governance import service as governance_service_module

    source = inspect.getsource(governance_service_module)
    assert "import main" not in source
    assert "from .. import main" not in source
    assert "from services.control_plane.bff import main" not in source

    # Route uniqueness: exactly one handler per CW01/03/04 path+method pair.
    # This FastAPI version defers `include_router` into lazy `_IncludedRouter`
    # wrappers, so route enumeration must recurse through `original_router`.
    def _iter_routes(routes: Any) -> Any:
        for route in routes:
            nested_router = getattr(route, "original_router", None)
            if nested_router is not None:
                yield from _iter_routes(nested_router.routes)
            else:
                yield route

    seen: Dict[tuple, int] = {}
    for route in _iter_routes(bff_main.app.router.routes):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if path is None or path not in {
            "/api/v1/consult/requests",
            "/api/v1/consult/requests/{request_id}",
            "/api/v1/consult/requests/{request_id}/cancel",
            "/api/v1/committees",
            "/api/v1/committees/{committee_id}",
            "/api/v1/consult/memos",
            "/api/v1/consult/memos/{memo_id}",
        }:
            continue
        for method in methods:
            key = (path, method)
            seen[key] = seen.get(key, 0) + 1
    assert seen, "expected CW01/03/04 routes to be mounted"
    duplicated = {key: count for key, count in seen.items() if count > 1}
    assert not duplicated, f"duplicate route registrations: {duplicated}"


# ---------------------------------------------------------------------------
# BFF-CW-READ-POLICY-CLOSURE-PREREQUISITE-001: read-port empty/missing truth,
# availability precedence, and redaction fail-closed regressions.
# ---------------------------------------------------------------------------


def test_read_surface_ports_committee_reads_do_not_fall_back_cross_domain() -> None:
    """ReadSurfacePorts.list_committees/get_committee must never substitute an
    unrelated domain's records (workflow templates, consult requests) for an
    authoritative empty/missing committee result."""
    ops_port = create_in_memory_operations_consultation_port(
        workflow_templates=[{"workflow_id": "wf-should-not-leak"}],
        consult_requests=[{"request_id": "shared-id", "status": "created"}],
    )
    ports = create_read_surface_ports(operations_consultation=ops_port)

    # No committee data seeded: an empty/None result is authoritative, not a
    # cue to fall back to a different domain's records.
    assert ports.list_committees() == []
    assert ports.get_committee("shared-id") is None
    assert ports.get_committee(None) is None


def test_read_surface_ports_committee_reads_delegate_to_real_committee_data() -> None:
    """When committee data does exist, ReadSurfacePorts must return it as-is
    (same board row shape as the domain port), not a workflow/consult-request
    substitute."""
    consult_sessions = [
        {
            "session_id": "cs-1",
            "session_type": "consult",
            "persona_id": "persona-alpha",
            "status": "active",
            "started_at": "2026-09-05T00:00:00Z",
            "request_id": "cr-1",
            "metadata": {
                "consultation": {
                    "committee_ref": "committee-42",
                    "committee_session_ids": ["cs-1"],
                    "quorum_state": "quorum_met",
                    "consensus_state": "sponsor_required",
                }
            },
        }
    ]
    ops_port = create_in_memory_operations_consultation_port(consult_sessions=consult_sessions)
    ports = create_read_surface_ports(operations_consultation=ops_port)

    rows = ports.list_committees()
    assert len(rows) == 1
    assert rows[0]["committee_id"] == "committee-42"

    detail = ports.get_committee("committee-42")
    assert detail is not None
    assert detail["committee_id"] == "committee-42"
    assert detail["quorum_state"] == "quorum_met"


def test_read_surface_ports_dataset_source_routes_consult_datasets_to_owning_port() -> None:
    """dataset_source for operations-consultation-owned datasets must reflect
    the actual client/store/missing truth of the owning port instead of a
    blanket 'typed_store' default that hides a genuinely unavailable backend."""
    for env_name in (
        "PANTHEON_BFF_CONSULTATION_DATA_DIR",
        "PANTHEON_CONSULTATION_DATA_DIR",
        "CONSULTATION_DATA_DIR",
    ):
        os.environ.pop(env_name, None)
    ops_port = create_operations_consultation_port()
    ports = create_read_surface_ports(operations_consultation=ops_port)

    assert ports.dataset_source("consult_requests") == "missing"
    assert ports.dataset_source("consult_memos") == "missing"

    with tempfile.TemporaryDirectory() as tmp:
        ops_port_with_store = create_operations_consultation_port(consultation_data_dir=tmp)
        ports_with_store = create_read_surface_ports(operations_consultation=ops_port_with_store)
        assert ports_with_store.dataset_source("consult_requests") == "service_store"
        assert ports_with_store.dataset_source("consult_memos") == "service_store"


def test_cw03_committee_surface_state_explicit_unavailable_dominates_dataset_ok() -> None:
    """A committee record's own explicit unavailable state must dominate an
    'ok' dataset source, not be silently overwritten into a healthy
    projection with a writable sponsor-decision CTA."""

    class _Store:
        def dataset_source(self, dataset: str) -> str:
            return "typed_store"

        def get_committee(self, committee_id: str) -> Optional[Dict[str, Any]]:
            return {
                "committee_id": "committee-x",
                "surface_state": "unavailable",
                "quorum_state": "quorum_met",
                "consensus_state": "sponsor_required",
                "sponsor_assignment": {"participant_id": "p-1"},
                "sponsor_decision": None,
            }

    identity = type("Identity", (), {"operator_id": "op-1", "roles": {"operator"}})()
    service = GovernanceService(_Store())
    projection = service.committee_projection(
        "committee-x", identity=identity, snapshot_at="2026-09-05T00:00:00Z"
    )
    assert projection is not None
    assert projection["meta"]["surfaces"]["committee_board"] == "unavailable"
    assert projection["allowedActions"] == {"canRecordSponsorDecision": False}


def test_cw04_capability_lookup_failure_fails_closed_not_passthrough() -> None:
    """A failed/absent capability lookup must fail closed through the
    canonical redactor (empty capability set), never fall back to an
    unredacted evidence passthrough."""
    reviewer_identity = type("Identity", (), {"operator_id": "op-1", "roles": {"reviewer"}})()
    memo = {
        "memo_id": "mem-y",
        "lifecycle_state": "published",
        "status": "published",
        "governance_target": {"target_type": "deployment_plan", "target_id": "plan-1"},
        "active_governance_review_id": None,
        "suppressed": False,
        "withdrawn": False,
        "surface_state": "ok",
        "evidence_refs": [{"ref_id": "ev-1", "evidence_type": "strategy"}],
    }

    class _StaticStore:
        def get_consult_memo(self, memo_id: str) -> Optional[Dict[str, Any]]:
            return memo if memo_id == memo["memo_id"] else None

        def dataset_source(self, dataset: str) -> str:
            return "typed_store"

    def _raising_capabilities(identity: Any) -> Any:
        raise RuntimeError("capability lookup failed")

    service = GovernanceService(
        _StaticStore(),
        redact_evidence_refs=_canonical_redact_evidence_refs,
        capabilities_for_identity=_raising_capabilities,
    )
    projection = service.consult_memo_projection(
        "mem-y", identity=reviewer_identity, snapshot_at="2026-09-05T00:00:00Z"
    )
    assert projection is not None
    assert projection["evidence_refs"][0]["redacted"] is True
    assert projection["evidence_refs"][0]["required_capability"] == "strategy.view"
    assert projection["meta"]["supporting_counts"]["redacted_evidence_count"] == 1


def test_cw04_default_redactor_fails_closed_without_wired_policy() -> None:
    """GovernanceService constructed without an injected redaction policy must
    withhold evidence by default, never silently disclose it."""
    memo = {
        "memo_id": "mem-z",
        "lifecycle_state": "published",
        "status": "published",
        "governance_target": {"target_type": "deployment_plan", "target_id": "plan-1"},
        "active_governance_review_id": None,
        "suppressed": False,
        "withdrawn": False,
        "surface_state": "ok",
        "evidence_refs": [{"ref_id": "ev-1", "evidence_type": "strategy"}],
    }

    class _StaticStore:
        def get_consult_memo(self, memo_id: str) -> Optional[Dict[str, Any]]:
            return memo if memo_id == memo["memo_id"] else None

        def dataset_source(self, dataset: str) -> str:
            return "typed_store"

    identity = type("Identity", (), {"operator_id": "op-1", "roles": {"reviewer"}})()
    service = GovernanceService(_StaticStore())
    projection = service.consult_memo_projection(
        "mem-z", identity=identity, snapshot_at="2026-09-05T00:00:00Z"
    )
    assert projection is not None
    assert projection["evidence_refs"] == [
        {"ref_id": "ev-1", "redacted": True, "reason": "redaction_policy_unavailable"}
    ]
    assert projection["meta"]["supporting_counts"]["redacted_evidence_count"] == 1


def test_cw04_authorized_capabilities_still_disclose_evidence() -> None:
    """Positive counterpart: an identity whose derived capabilities actually
    cover the evidence kind must still see it (redaction is capability-gated,
    not an unconditional black hole)."""
    reviewer_identity = type("Identity", (), {"operator_id": "op-1", "roles": {"reviewer"}})()
    memo = {
        "memo_id": "mem-w",
        "lifecycle_state": "published",
        "status": "published",
        "governance_target": {"target_type": "deployment_plan", "target_id": "plan-1"},
        "active_governance_review_id": None,
        "suppressed": False,
        "withdrawn": False,
        "surface_state": "ok",
        "evidence_refs": [{"ref_id": "ev-1", "evidence_type": "strategy"}],
    }

    class _StaticStore:
        def get_consult_memo(self, memo_id: str) -> Optional[Dict[str, Any]]:
            return memo if memo_id == memo["memo_id"] else None

        def dataset_source(self, dataset: str) -> str:
            return "typed_store"

    service = GovernanceService(
        _StaticStore(),
        redact_evidence_refs=_canonical_redact_evidence_refs,
        capabilities_for_identity=lambda identity: ["strategy.view"],
    )
    projection = service.consult_memo_projection(
        "mem-w", identity=reviewer_identity, snapshot_at="2026-09-05T00:00:00Z"
    )
    assert projection is not None
    assert projection["evidence_refs"] == [{"ref_id": "ev-1", "evidence_type": "strategy"}]
    assert projection["meta"]["supporting_counts"]["redacted_evidence_count"] == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
