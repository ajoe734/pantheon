#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


APPROVER_AUTH = "Bearer test-approver:approver"
REVIEWER_AUTH = "Bearer test-reviewer:reviewer"


@contextmanager
def _seeded_client():
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
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


def test_mutation_review_returns_503_when_required_evidence_is_unavailable() -> None:
    with _seeded_client() as client:
        bff_main.read_store._data["approval_decisions"].pop("appr-dec-c5a9f11e", None)
        bff_main.read_store._save()

        response = client.get(
            "/api/v1/operator/mutation-review/evo-dec-88f3a2c1",
            headers={"Authorization": APPROVER_AUTH},
        )
        assert response.status_code == 503, response.text

        payload = response.json()
        assert payload == {
            "error": "evidence_unavailable",
            "meta": {
                "surfaces": {
                    "mutation_review": "unavailable",
                }
            },
        }
