#!/usr/bin/env python3
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from typing import Any, Optional

from fastapi import APIRouter, FastAPI, Header
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


APPROVER_AUTH = "Bearer test-approver:approver"
REVIEWER_AUTH = "Bearer test-reviewer:reviewer"

_DATA_PATH = Path(__file__).parent / "data" / "read_surfaces.json"
with open(_DATA_PATH, "r", encoding="utf-8") as _f:
    _RAW_DATA = json.load(_f)

_SEED_EVOLUTION_DECISIONS = dict(_RAW_DATA.get("evolution_decisions", {}))
_SEED_APPROVAL_DECISIONS = dict(_RAW_DATA.get("approval_decisions", {}))


def _create_ew05_router(evos: dict[str, Any], apprs: dict[str, Any]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/operator/mutation-review/{decision_id}")
    async def get_mutation_review(
        decision_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        dec = evos.get(decision_id)
        if not dec:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "RESOURCE_NOT_FOUND", "message": "Decision not found"}},
            )

        appr_id = dec.get("approval_decision_id")
        appr = apprs.get(appr_id) if appr_id else None
        if appr_id and not appr:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "DEPENDENCY_UNAVAILABLE",
                        "message": "Mutation review evidence is unavailable",
                    },
                    "surfaces": {"mutation_review": "unavailable"},
                    "meta": {"surfaces": {"mutation_review": "unavailable"}},
                },
            )

        roles = set()
        if authorization:
            token = authorization.replace("Bearer ", "").strip()
            if ":" in token:
                roles = set(token.split(":")[-1].split(","))
            else:
                roles = {token}

        decision_state = str(dec.get("decision_state") or dec.get("status") or "").lower()
        risk_level = str(dec.get("risk_level") or "").lower()

        is_approver = bool(roles & {"approver", "admin"})
        is_reviewer = bool(roles & {"reviewer", "admin"})
        is_operator = bool(roles & {"operator", "admin"})

        can_review = (decision_state == "proposed") and is_reviewer
        can_approve = (decision_state == "reviewed") and is_approver
        can_reject = (decision_state in {"proposed", "reviewed"}) and (is_approver or is_reviewer)
        can_execute = (decision_state == "approved") and is_operator

        allowed_actions = {
            "canReviewMutation": can_review,
            "canApproveMutation": can_approve,
            "canRejectMutation": can_reject,
            "canExecuteMutation": can_execute,
        }

        proposed_changes = dict(dec.get("proposed_changes") or {})
        if "target_stage" not in proposed_changes:
            proposed_changes["target_stage"] = dec.get("target_stage") or "canary"
        if "summary" not in proposed_changes:
            proposed_changes["summary"] = dec.get("rationale") or dec.get("notes") or ""

        risk_assessment = dict(dec.get("risk_assessment") or {})
        if "threshold_triggers" not in risk_assessment:
            triggers = []
            for s in dec.get("threshold_snapshots") or []:
                if isinstance(s, dict):
                    triggers.append({
                        "trigger_type": s.get("signal_type"),
                        "metric": s.get("metric_name"),
                        "observed_value": str(s.get("observed_value")),
                        "threshold_value": str(s.get("threshold_value")),
                        "threshold_source": s.get("policy_source"),
                    })
            risk_assessment["threshold_triggers"] = triggers

        payload = {
            "decision_id": decision_id,
            "target_type": dec.get("target_type") or "candidate_artifact",
            "target_id": dec.get("target_id") or dec.get("artifact_id"),
            "target_version": dec.get("target_version") or "v1.0.0",
            "action_type": dec.get("action_type") or "freeze_canary",
            "decision_state": decision_state,
            "risk_level": risk_level,
            "created_at": dec.get("created_at") or "2026-07-01T00:00:00Z",
            "approval_decision_id": appr_id,
            "proposed_changes": proposed_changes,
            "risk_assessment": risk_assessment,
            "required_approvals": dec.get("required_approvals") or [],
            "review_chain": dec.get("review_chain") or [],
            "evidence_refs": dec.get("evidence_refs") or [],
            "allowedActions": allowed_actions,
            "meta": {
                "snapshot_at": "2026-07-01T00:00:00Z",
                "surfaces": {"mutation_review": "fresh"},
            },
        }
        return payload

    return router


@contextmanager
def _seeded_client(
    *,
    evolution_decisions: dict | None = None,
    approval_decisions: dict | None = None,
):
    evos = dict(_SEED_EVOLUTION_DECISIONS if evolution_decisions is None else evolution_decisions)
    apprs = dict(_SEED_APPROVAL_DECISIONS if approval_decisions is None else approval_decisions)
    app = FastAPI()
    app.include_router(_create_ew05_router(evos, apprs))
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
