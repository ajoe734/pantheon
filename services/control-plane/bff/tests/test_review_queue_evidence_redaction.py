"""Regression coverage for BFF-REVIEW-QUEUE-REDACTION-REPAIR-001.

Exercises the real ``create_governance_router`` composition (identical
wiring to ``core/app_factory.py``: canonical ``redact_evidence_refs`` from
``models.py`` plus ``capabilities_for_identity`` from ``auth/policy.py``)
against both a low-capability and a full-capability identity, proving that
``review_summary.evidence_refs`` on the review-queue read surfaces is
redacted fail-closed for callers who lack the required capability and
passed through untouched for callers who hold it.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.models import redact_evidence_refs, utc_now


LOW_CAPABILITY_TOKEN = "Bearer op-review-1:operator"
FULL_CAPABILITY_TOKEN = "Bearer admin-review-1:admin"

_REVIEW_ITEM: Dict[str, Any] = {
    "item_id": "gov-review-redaction-001",
    "item_type": "DeploymentPlan",
    "risk_level": "medium",
    "status": "pending",
    "submitted_at": "2026-09-24T10:00:00Z",
    "submitted_by": "orchestrator",
    "governance_outcome": "pending",
    "allowedActions": {
        "canReview": True,
        "canForwardToApproval": False,
        "canRequestChanges": True,
        "canEscalate": False,
    },
    "review_summary": {
        "risk_assessment": "Regression fixture for review-queue evidence redaction",
        "evidence_refs": [
            {"ref_id": "ref-alert-ev", "type": "alert"},
            {"ref_id": "ref-metric-ev", "type": "metric"},
            {"ref_id": "ref-job-ev", "type": "job"},
        ],
        "linked_approval_decision_id": None,
    },
}


class _ReviewQueueStore:
    """Minimal read-store double exposing only what the router calls."""

    def list_governance_review_queue_items(self, **_: Any) -> List[Dict[str, Any]]:
        import copy

        return [copy.deepcopy(_REVIEW_ITEM)]

    def dataset_source(self, dataset: str) -> str:
        return "service_store" if dataset == "governance_review_queue_items" else "missing"


def _build_app(store: _ReviewQueueStore) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(
        create_governance_router(
            read_surface=store,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=utc_now,
            redact_evidence_refs=redact_evidence_refs,
            capabilities_for_identity=auth_policy.capabilities_for_identity,
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


def test_operator_governance_review_queue_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app(_ReviewQueueStore()))
        response = client.get(
            "/api/v1/operator/governance/review-queue",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()

        assert len(payload["items"]) == 1
        ev_refs = payload["items"][0]["review_summary"]["evidence_refs"]
        assert len(ev_refs) == 3

        # operator role holds risk.alert.read: alert ref passes through unchanged
        assert ev_refs[0] == {"ref_id": "ref-alert-ev", "type": "alert"}
        # operator role lacks metric.read and job.read: both are withheld
        _assert_redacted(ev_refs[1], ref_id="ref-metric-ev", required_capability="metric.read")
        _assert_redacted(ev_refs[2], ref_id="ref-job-ev", required_capability="job.read")

        assert payload["meta"]["redacted_evidence_count"] == 2


def test_operator_governance_review_queue_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app(_ReviewQueueStore()))
        response = client.get(
            "/api/v1/operator/governance/review-queue",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()

        assert len(payload["items"]) == 1
        ev_refs = payload["items"][0]["review_summary"]["evidence_refs"]
        assert ev_refs == _REVIEW_ITEM["review_summary"]["evidence_refs"]
        assert payload["meta"]["redacted_evidence_count"] == 0


def test_bff_reviews_compat_surface_redacts_for_low_capability_identity() -> None:
    """/bff/reviews is a compatibility alias over the same GovernanceService.list_review_queue
    data; it must not become a bypass around the review-queue redaction fix."""
    with _stub_auth_env():
        client = TestClient(_build_app(_ReviewQueueStore()))
        response = client.get(
            "/bff/reviews",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()

        ev_refs = payload["items"][0]["review_summary"]["evidence_refs"]
        _assert_redacted(ev_refs[1], ref_id="ref-metric-ev", required_capability="metric.read")
        _assert_redacted(ev_refs[2], ref_id="ref-job-ev", required_capability="job.read")
        assert payload["meta"]["redacted_evidence_count"] == 2


def test_bff_reviews_compat_surface_passes_through_for_full_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app(_ReviewQueueStore()))
        response = client.get(
            "/bff/reviews",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()

        ev_refs = payload["items"][0]["review_summary"]["evidence_refs"]
        assert ev_refs == _REVIEW_ITEM["review_summary"]["evidence_refs"]
        assert payload["meta"]["redacted_evidence_count"] == 0


def test_bff_review_detail_redacts_for_low_capability_identity() -> None:
    with _stub_auth_env():
        client = TestClient(_build_app(_ReviewQueueStore()))
        response = client.get(
            f"/bff/reviews/{_REVIEW_ITEM['item_id']}",
            headers={"Authorization": LOW_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()

        ev_refs = payload["data"]["review_summary"]["evidence_refs"]
        _assert_redacted(ev_refs[1], ref_id="ref-metric-ev", required_capability="metric.read")
        _assert_redacted(ev_refs[2], ref_id="ref-job-ev", required_capability="job.read")
        assert payload["meta"]["redacted_evidence_count"] == 2


def test_review_queue_redaction_fails_closed_when_capabilities_unresolvable() -> None:
    """If capability resolution raises, redaction must withhold every ref rather
    than default to open disclosure."""

    def _boom(identity: Any) -> List[str]:
        raise RuntimeError("capability lookup unavailable")

    with _stub_auth_env():
        app = FastAPI()
        register_error_handlers(app)
        app.include_router(
            create_governance_router(
                read_surface=_ReviewQueueStore(),
                extract_identity=auth_policy.extract_identity,
                require_read_role=auth_policy.require_read_role,
                require_operator_role=auth_policy.require_operator_role,
                bff_error=auth_policy.bff_error,
                utc_now=utc_now,
                redact_evidence_refs=redact_evidence_refs,
                capabilities_for_identity=_boom,
            )
        )
        client = TestClient(app)
        response = client.get(
            "/api/v1/operator/governance/review-queue",
            headers={"Authorization": FULL_CAPABILITY_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()

        ev_refs = payload["items"][0]["review_summary"]["evidence_refs"]
        assert len(ev_refs) == 3
        assert all(ref["redacted"] is True for ref in ev_refs)
        assert payload["meta"]["redacted_evidence_count"] == 3
