#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from contextlib import contextmanager

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from ports import create_in_memory_read_surface_ports


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
    original_store = bff_main.read_store
    evos = dict(_SEED_EVOLUTION_DECISIONS if evolution_decisions is None else evolution_decisions)
    apprs = dict(_SEED_APPROVAL_DECISIONS if approval_decisions is None else approval_decisions)
    bff_main.read_store = create_in_memory_read_surface_ports(
        lifecycle_telemetry_governance_kwargs={
            "evolution_decisions": evos,
        },
        ooda_management_kwargs={
            "approval_decisions": list(apprs.values()) if isinstance(apprs, dict) else list(apprs),
        },
    )
    client = TestClient(bff_main.app)
    try:
        yield client
    finally:
        bff_main.read_store = original_store


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
            "allowedActions",
            "meta",
        ):
            assert key in payload

        assert payload["decision_id"] == "evo-dec-88f3a2c1"
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
        assert payload["allowedActions"]["canReviewMutation"] is True
        assert payload["allowedActions"]["canApproveMutation"] is False
        assert payload["allowedActions"]["canExecuteMutation"] is False


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
        assert payload["allowedActions"]["canExecuteMutation"] is True
        assert payload["allowedActions"]["canApproveMutation"] is False
        assert payload["allowedActions"]["canReviewMutation"] is False


def test_mutation_review_returns_503_when_required_evidence_is_unavailable() -> None:
    apprs = dict(_SEED_APPROVAL_DECISIONS)
    apprs.pop("appr-dec-c5a9f11e", None)
    with _seeded_client(approval_decisions=apprs) as client:
        response = client.get(
            "/api/v1/operator/mutation-review/evo-dec-88f3a2c1",
            headers={"Authorization": APPROVER_AUTH},
        )
        assert response.status_code == 503, response.text

        payload = response.json()
        assert payload["error"]["message"] == "Mutation review evidence is unavailable"
        assert payload["surfaces"]["mutation_review"] == "unavailable"
