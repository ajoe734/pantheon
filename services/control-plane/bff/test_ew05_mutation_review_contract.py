#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from pathlib import Path

from typing import Any, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


APPROVER_AUTH = "Bearer test-approver:approver"
REVIEWER_AUTH = "Bearer test-reviewer:reviewer"

_DATA_PATH = Path(__file__).parent / "data" / "read_surfaces.json"
with open(_DATA_PATH, "r", encoding="utf-8") as _f:
    _RAW_DATA = json.load(_f)

_SEED_EVOLUTION_DECISIONS = dict(_RAW_DATA.get("evolution_decisions", {}))
_SEED_APPROVAL_DECISIONS = dict(_RAW_DATA.get("approval_decisions", {}))


@contextmanager
def _seeded_client(
    *,
    evolution_decisions: dict | None = None,
    approval_decisions: dict | None = None,
):
    evos = dict(_SEED_EVOLUTION_DECISIONS if evolution_decisions is None else evolution_decisions)
    apprs = dict(_SEED_APPROVAL_DECISIONS if approval_decisions is None else approval_decisions)
    ports = create_in_memory_read_surface_ports(
        lifecycle_telemetry_governance_kwargs={
            "evolution_decisions": evos,
        },
        ooda_management_kwargs={
            "approval_decisions": list(apprs.values()) if isinstance(apprs, dict) else list(apprs),
        },
    )
    app = FastAPI()
    app.include_router(create_governance_router(read_surface=ports))
    client = TestClient(app)
    yield client


def test_mutation_review_projection_contract() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/operator/mutation-review/evo-dec-88f3a2c1",
            headers={"Authorization": APPROVER_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        for key in (
            "decision_id",
            "target_type",
            "target_id",
            "target_version",
            "action_type",
            "decision_state",
            "risk_level",
            "created_at",
            "approval_decision_id",
            "proposed_changes",
            "risk_assessment",
            "required_approvals",
            "review_chain",
            "evidence_refs",
            "meta",
        ):
            assert key in payload

        assert payload["decision_id"] == "evo-dec-88f3a2c1"
        if "allowedActions" in payload:
            assert payload["allowedActions"]["canApproveMutation"] is True
            assert payload["allowedActions"]["canRejectMutation"] is True
            # Seed decision is already "reviewed" — review/execute are gated to
            # "proposed"/"approved" respectively, so neither is allowed here.
            assert payload["allowedActions"]["canReviewMutation"] is False
            assert payload["allowedActions"]["canExecuteMutation"] is False
        assert payload["meta"]["surfaces"]["mutation_review"] in {"fresh", "stale"}
        assert payload["proposed_changes"]["target_stage"] == "canary"
        assert len(payload["risk_assessment"]["threshold_triggers"]) == 2


def test_mutation_review_reviewer_visibility_contract() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/operator/mutation-review/evo-dec-88f3a2c1",
            headers={"Authorization": REVIEWER_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        if "allowedActions" in payload:
            assert payload["allowedActions"]["canApproveMutation"] is False
            assert payload["allowedActions"]["canRejectMutation"] is True
        assert payload["meta"]["surfaces"]["mutation_review"] in {"fresh", "stale"}


def test_mutation_review_review_action_allowed_when_proposed() -> None:
    evos = {
        **_SEED_EVOLUTION_DECISIONS,
        "evo-dec-proposed-001": {
            "id": "evo-dec-proposed-001",
            "decision_id": "evo-dec-proposed-001",
            "target_type": "candidate_artifact",
            "target_id": "artifact-proposed-001",
            "target_version": "v1.0.0",
            "action_type": "freeze_canary",
            "risk_level": "medium",
            "status": "proposed",
            "decision_state": "proposed",
            "created_at": "2026-07-01T00:00:00Z",
            "rationale": "Initial threshold breach triage.",
        },
    }
    with _seeded_client(evolution_decisions=evos) as client:
        response = client.get(
            "/api/v1/operator/mutation-review/evo-dec-proposed-001",
            headers={"Authorization": REVIEWER_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        if "allowedActions" in payload:
            assert payload["allowedActions"]["canReviewMutation"] is True
            assert payload["allowedActions"]["canApproveMutation"] is False
            assert payload["allowedActions"]["canExecuteMutation"] is False
        assert payload["decision_state"] == "proposed"


def test_mutation_review_execute_action_allowed_when_approved() -> None:
    apprs = {
        **_SEED_APPROVAL_DECISIONS,
        "appr-dec-approved-001": {
            "id": "appr-dec-approved-001",
            "decision_id": "appr-dec-approved-001",
            "outcome": "approved",
            "state": "approved",
        },
    }
    evos = {
        **_SEED_EVOLUTION_DECISIONS,
        "evo-dec-approved-001": {
            "id": "evo-dec-approved-001",
            "decision_id": "evo-dec-approved-001",
            "target_type": "candidate_artifact",
            "target_id": "artifact-approved-001",
            "target_version": "v1.0.0",
            "action_type": "freeze_canary",
            "risk_level": "medium",
            "status": "approved",
            "decision_state": "approved",
            "approval_decision_id": "appr-dec-approved-001",
            "created_at": "2026-07-01T00:00:00Z",
            "rationale": "Ready for execution.",
        },
    }
    with _seeded_client(evolution_decisions=evos, approval_decisions=apprs) as client:
        response = client.get(
            "/api/v1/operator/mutation-review/evo-dec-approved-001",
            headers={"Authorization": "Bearer test-operator:operator"},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        if "allowedActions" in payload:
            assert payload["allowedActions"]["canExecuteMutation"] is True
            assert payload["allowedActions"]["canApproveMutation"] is False
            assert payload["allowedActions"]["canReviewMutation"] is False
        assert payload["decision_state"] == "approved"


def test_mutation_review_returns_503_when_required_evidence_is_unavailable() -> None:
    evos = copy.deepcopy(_SEED_EVOLUTION_DECISIONS)
    evos["evo-dec-88f3a2c1"]["target_id"] = None
    evos["evo-dec-88f3a2c1"]["artifact_id"] = None
    with _seeded_client(evolution_decisions=evos) as client:
        response = client.get(
            "/api/v1/operator/mutation-review/evo-dec-88f3a2c1",
            headers={"Authorization": APPROVER_AUTH},
        )
        assert response.status_code == 503, response.text

        payload = response.json()
        error = payload.get("error") or (payload.get("detail", {}).get("error") if isinstance(payload.get("detail"), dict) else {})
        assert error.get("code") == "DEPENDENCY_UNAVAILABLE"
        assert "Mutation review evidence is incomplete" in (error.get("message") or "")
