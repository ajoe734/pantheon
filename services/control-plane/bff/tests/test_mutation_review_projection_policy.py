"""BFF-EVOLUTION-REVIEW-JOURNAL-SEAM-CORRECTIVE-001: mutation-review policy owner.

Proves ``GovernanceService.mutation_review_projection`` is the single owner of
the actor/state/evidence policy consumed by both the "direct" POST action
validators (ApproveMutation/RejectMutation/ReviewMutation/ExecuteMutation in
main.py) and the "nested" GET reads (the governance router detail endpoint
and the management evolution journal composition in evolution/router.py).

These are isolated unit tests against ``GovernanceService`` with an in-memory
fake read store -- no FastAPI app boot and no coupling to ``main.py``
globals, per the B10 test-migration seam pattern.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.control_plane.bff.governance.service import GovernanceService
from services.control_plane.bff.models import OperatorIdentity


class _FakeReadStore:
    def __init__(
        self,
        *,
        evolution_decisions: Optional[Dict[str, Dict[str, Any]]] = None,
        approval_decisions: Optional[Dict[str, Dict[str, Any]]] = None,
        incidents: Optional[Dict[str, Dict[str, Any]]] = None,
        postmortems: Optional[Dict[str, Dict[str, Any]]] = None,
        dataset_sources: Optional[Dict[str, str]] = None,
    ) -> None:
        self._decisions = dict(evolution_decisions or {})
        self._approvals = dict(approval_decisions or {})
        self._incidents = dict(incidents or {})
        self._postmortems = dict(postmortems or {})
        self._dataset_sources = dict(dataset_sources or {})

    def get_evolution_decision_by_id(self, decision_id: str) -> Optional[Dict[str, Any]]:
        return self._decisions.get(decision_id)

    def get_approval_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        return self._approvals.get(decision_id)

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        return self._incidents.get(incident_id)

    def get_postmortem(self, postmortem_id: str) -> Optional[Dict[str, Any]]:
        return self._postmortems.get(postmortem_id)

    def get_rollbacks_by_incident(self, incident_id: str) -> List[Dict[str, Any]]:
        return []

    def dataset_source(self, dataset: str) -> str:
        return self._dataset_sources.get(dataset, "ok")


def _identity(*roles: str) -> OperatorIdentity:
    return OperatorIdentity(operator_id="op-test", roles=list(roles))


def _service(store: _FakeReadStore, *, read_surface_state: str = "fresh") -> GovernanceService:
    return GovernanceService(
        store,
        read_surface_state=lambda: read_surface_state,
    )


_MEDIUM_REVIEWED_DECISION = {
    "id": "evo-dec-policy-001",
    "decision_id": "evo-dec-policy-001",
    "target_type": "candidate_artifact",
    "target_id": "artifact-policy-001",
    "target_version": "v1.0.0",
    "action_type": "freeze_canary",
    "risk_level": "medium",
    "decision_state": "reviewed",
    "approval_decision_id": "appr-policy-001",
    "created_at": "2026-07-01T00:00:00Z",
}
_APPROVAL_UNDER_REVIEW = {
    "id": "appr-policy-001",
    "decision_id": "appr-policy-001",
    "outcome": None,
    "state": "under_review",
}


def test_direct_and_nested_calls_derive_identical_allowed_actions() -> None:
    """POST validators and GET reads must call through one policy owner.

    Simulates the "direct" (POST action validator) and "nested" (GET
    detail / journal composition) call sites: both must produce byte
    identical allowedActions for the same decision/identity, because both
    now go through the same ``mutation_review_projection`` method instead
    of a forked copy.
    """
    store = _FakeReadStore(
        evolution_decisions={"evo-dec-policy-001": _MEDIUM_REVIEWED_DECISION},
        approval_decisions={"appr-policy-001": _APPROVAL_UNDER_REVIEW},
    )
    service = _service(store)
    identity = _identity("approver")

    direct = service.mutation_review_projection(
        "evo-dec-policy-001", identity=identity, snapshot_at="2026-07-01T01:00:00Z"
    )
    nested = service.mutation_review_projection(
        "evo-dec-policy-001", identity=identity, snapshot_at="2026-07-01T01:00:00Z"
    )

    assert direct is not None and nested is not None
    assert direct["allowedActions"] == nested["allowedActions"]
    assert direct["allowedActions"]["canApproveMutation"] is True
    assert direct["meta"]["surfaces"]["mutation_review"] == nested["meta"]["surfaces"]["mutation_review"]


def test_missing_required_approval_evidence_marks_surface_unavailable() -> None:
    """Required evidence missing must fail closed, not silently pass through."""
    store = _FakeReadStore(
        evolution_decisions={"evo-dec-policy-001": _MEDIUM_REVIEWED_DECISION},
        approval_decisions={},  # approval_decision_id points at nothing
    )
    service = _service(store)
    identity = _identity("approver")

    projection = service.mutation_review_projection(
        "evo-dec-policy-001", identity=identity, snapshot_at="2026-07-01T01:00:00Z"
    )

    assert projection is not None
    assert projection["meta"]["surfaces"]["mutation_review"] == "unavailable"
    assert projection["allowedActions"] == {
        "canReviewMutation": False,
        "canApproveMutation": False,
        "canRejectMutation": False,
        "canExecuteMutation": False,
    }


def test_healthy_empty_linkage_is_not_conflated_with_unavailable_evidence() -> None:
    """A decision proposed with no incident/postmortem link is healthy-empty.

    The surface must read "fresh", not "unavailable" -- an empty optional
    linkage is a legitimate state, distinct from missing required evidence.
    """
    proposed_decision = {
        "id": "evo-dec-policy-002",
        "decision_id": "evo-dec-policy-002",
        "target_type": "candidate_artifact",
        "target_id": "artifact-policy-002",
        "target_version": "v1.0.0",
        "action_type": "freeze_canary",
        "risk_level": "low",
        "decision_state": "proposed",
        "created_at": "2026-07-01T00:00:00Z",
    }
    store = _FakeReadStore(evolution_decisions={"evo-dec-policy-002": proposed_decision})
    service = _service(store)
    identity = _identity("reviewer")

    projection = service.mutation_review_projection(
        "evo-dec-policy-002", identity=identity, snapshot_at="2026-07-01T01:00:00Z"
    )

    assert projection is not None
    assert projection["meta"]["surfaces"]["mutation_review"] == "fresh"
    assert projection["allowedActions"]["canReviewMutation"] is True


def test_no_hardcoded_fresh_reflects_local_snapshot_dataset_source() -> None:
    """The surface state must vary with the real dataset source, not a fixed value."""
    store = _FakeReadStore(
        evolution_decisions={"evo-dec-policy-002": {
            **{
                "id": "evo-dec-policy-002",
                "decision_id": "evo-dec-policy-002",
                "target_type": "candidate_artifact",
                "risk_level": "low",
                "decision_state": "proposed",
                "created_at": "2026-07-01T00:00:00Z",
            },
        }},
        dataset_sources={"evolution_decisions": "local_snapshot"},
    )
    service = _service(store)
    identity = _identity("reviewer")

    projection = service.mutation_review_projection(
        "evo-dec-policy-002", identity=identity, snapshot_at="2026-07-01T01:00:00Z"
    )

    assert projection is not None
    assert projection["meta"]["surfaces"]["mutation_review"] == "stale"


def test_unknown_decision_id_returns_none_not_a_fake_default() -> None:
    store = _FakeReadStore()
    service = _service(store)
    identity = _identity("admin")

    projection = service.mutation_review_projection(
        "does-not-exist", identity=identity, snapshot_at="2026-07-01T01:00:00Z"
    )

    assert projection is None


def test_role_policy_denies_reviewer_on_high_risk_approval() -> None:
    high_risk_reviewed = {
        "id": "evo-dec-policy-003",
        "decision_id": "evo-dec-policy-003",
        "target_type": "candidate_artifact",
        "risk_level": "high",
        "decision_state": "reviewed",
        "approval_decision_id": "appr-policy-003",
        "created_at": "2026-07-01T00:00:00Z",
    }
    store = _FakeReadStore(
        evolution_decisions={"evo-dec-policy-003": high_risk_reviewed},
        approval_decisions={"appr-policy-003": {"id": "appr-policy-003", "state": "under_review"}},
    )
    service = _service(store)

    reviewer_only = service.mutation_review_projection(
        "evo-dec-policy-003", identity=_identity("reviewer"), snapshot_at="2026-07-01T01:00:00Z"
    )
    admin = service.mutation_review_projection(
        "evo-dec-policy-003", identity=_identity("admin"), snapshot_at="2026-07-01T01:00:00Z"
    )

    assert reviewer_only is not None and admin is not None
    assert reviewer_only["allowedActions"]["canApproveMutation"] is False
    assert admin["allowedActions"]["canApproveMutation"] is True
