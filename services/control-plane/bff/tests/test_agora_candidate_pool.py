from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.control_plane.bff.agora.router import create_agora_router
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.models import ErrorCode
from services.control_plane.bff.personas.service import (
    _extract_identity,
    _require_read_role,
    _require_operator_role,
    _bff_error,
)
try:
    from agora.service import _AGORA_SIGNAL_WRITE_ROLES, _AGORA_BULK_FEEDBACK_ROLES
except ImportError:
    from services.control_plane.bff.agora.service import (
        _AGORA_SIGNAL_WRITE_ROLES,
        _AGORA_BULK_FEEDBACK_ROLES,
    )


def _require_agora_signal_write_role(identity: Any) -> None:
    if not _AGORA_SIGNAL_WRITE_ROLES.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Agora signal creation requires analyst-level role",
            "Operator does not hold the required analyst, operator, reviewer, approver, or admin role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with analyst-level Agora write access",
        )


def _require_agora_bulk_feedback_role(identity: Any) -> None:
    if not _AGORA_BULK_FEEDBACK_ROLES.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Agora feedback access requires analyst role",
            "Operator does not hold the required Agora feedback role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with analyst, operator, reviewer, approver, or admin role",
        )


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


REPO_ROOT = Path(__file__).resolve().parents[4]


_OPERATOR_AUTH = "Bearer agora-test-user:operator"
_POOL_SCHEMA = REPO_ROOT / "services/control-plane/specs/agora/candidate_pool.schema.json"
_SCORE_SCHEMA = REPO_ROOT / "services/control-plane/specs/agora/v5/candidate_score_result.schema.json"
_REVIEW_SCHEMA = REPO_ROOT / "services/control-plane/specs/agora/v5/candidate_member_review.schema.json"
_DISCUSSION_SCHEMA = REPO_ROOT / "services/control-plane/specs/agora/v5/candidate_discussion.schema.json"
_MONITORING_SCHEMA = REPO_ROOT / "services/control-plane/specs/agora/v5/candidate_monitoring_status.schema.json"
_RECIPE = (
    REPO_ROOT
    / "docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/"
    / "candidate_scoring_recipe.winner_branch.default.json"
)


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    router = create_agora_router(
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_write_role=_require_operator_role,
        require_operator_role=_require_operator_role,
        require_journal_write_role=_require_operator_role,
        require_agora_signal_write_role=_require_agora_signal_write_role,
        require_agora_bulk_feedback_role=_require_agora_bulk_feedback_role,
        bff_error=_bff_error,
        utc_now=_utc_now_rfc3339,
        read_surface=create_in_memory_read_surface_ports(),
        sync_servant_agent=lambda p: {},
    )
    app = FastAPI()

    @app.exception_handler(HTTPException)
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request, exc):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": "HTTP_ERROR", "message": str(exc.detail)}})

    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _headers(
    *,
    idempotency_key: str | None = None,
    if_match: str | None = None,
) -> dict[str, str]:
    headers = {"Authorization": _OPERATOR_AUTH, "X-Request-Id": "req-ag-be-cp-001"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    if if_match is not None:
        headers["If-Match"] = if_match
    return headers


def _schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(schema_path: Path, payload: dict) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = _schema(schema_path)
    if "2020-12" in str(schema.get("$schema")):
        validator = jsonschema.Draft202012Validator
    else:
        validator = jsonschema.Draft7Validator
    validator(schema, format_checker=jsonschema.FormatChecker()).validate(payload)


def _create_pool(client: TestClient, key: str) -> dict:
    response = client.post(
        "/bff/agora/candidate-pools",
        headers=_headers(idempotency_key=key),
        json={
            "operator_id": "agora-test-user",
            "filter": {
                "asset_classes": ["equity"],
                "strategy_families": ["winner_branch"],
                "lifecycle_states": ["candidate"],
                "persona_ids": ["persona-winner-branch"],
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _get_pool(client: TestClient, pool_id: str) -> dict:
    response = client.get(
        f"/bff/agora/candidate-pools/{pool_id}",
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _score_pool(client: TestClient, pool_id: str, etag: str, key: str) -> list[dict]:
    response = client.post(
        f"/bff/agora/candidate-pools/{pool_id}/score",
        headers=_headers(idempotency_key=key, if_match=etag),
        json={},
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "completed"

    score_response = client.get(
        f"/bff/agora/candidate-pools/{pool_id}/score",
        headers=_headers(),
    )
    assert score_response.status_code == 200, score_response.text
    return score_response.json()["items"]


def test_candidate_pool_create_and_score_follow_a2_recipe(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    created = _create_pool(client, "ag-be-cp-001-create-score")
    pool = created["data"]
    pool_id = pool["pool_id"]

    _validate(_POOL_SCHEMA, pool)
    assert pool["metadata"]["no_order_route_proof"] == "candidate_pool_bff_request_only_no_order_route"
    assert len(pool["candidates"]) == 2

    scores = _score_pool(client, pool_id, created["meta"]["etag"], "ag-be-cp-001-score")
    recipe = _schema(_RECIPE)
    recipe_component_ids = [
        component["component_id"]
        for component in [*recipe["positive_components"], *recipe["penalty_components"]]
    ]

    assert {score["candidate_id"] for score in scores} == {
        "candidate-winner-branch-priority",
        "candidate-winner-branch-research",
    }
    for score in scores:
        _validate(_SCORE_SCHEMA, score)
        assert [component["component_id"] for component in score["components"]] == recipe_component_ids
        assert {"raw_score", "penalty_score", "evidence_confidence", "effective_score"} <= set(score)

    priority = next(score for score in scores if score["candidate_id"] == "candidate-winner-branch-priority")
    positive_count = len(recipe["positive_components"])
    base_score = sum(component["contribution"] for component in priority["components"][:positive_count])
    penalty_score = sum(component["contribution"] for component in priority["components"][positive_count:])
    expected_raw = round(max(base_score - penalty_score, 0.0), 4)
    expected_effective = round(expected_raw * (0.60 + 0.40 * priority["evidence_confidence"]), 4)

    assert priority["raw_score"] == pytest.approx(expected_raw)
    assert priority["penalty_score"] == pytest.approx(round(penalty_score, 4))
    assert priority["effective_score"] == pytest.approx(expected_effective)
    assert priority["rank"] == 1

    research = next(score for score in scores if score["candidate_id"] == "candidate-winner-branch-research")
    assert research["band"] == "needs_research"
    assert any("data_quality below 0.50" in blocker for blocker in research["blockers"])


def test_reject_review_retains_candidate_as_negative_example(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    created = _create_pool(client, "ag-be-cp-001-create-reject")
    pool_id = created["data"]["pool_id"]
    scores = _score_pool(client, pool_id, created["meta"]["etag"], "ag-be-cp-001-score-reject")
    artifact_id = scores[0]["candidate_id"]
    etag = _get_pool(client, pool_id)["meta"]["etag"]

    review_payload = {
        "decision": "reject",
        "rationale": "Score drivers are not decision-relevant enough for this operator.",
        "reviewed_by": "agora-test-user",
        "negative_example_tags": ["low_decision_relevance"],
    }
    _validate(_REVIEW_SCHEMA, review_payload)
    response = client.post(
        f"/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/review",
        headers=_headers(idempotency_key="ag-be-cp-001-review-reject", if_match=etag),
        json=review_payload,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["candidate"]["lifecycle_state"] == "rejected"
    assert body["data"]["negative_example"] is True
    assert body["data"]["no_order_route_proof"] == "candidate_pool_bff_request_only_no_order_route"

    member_response = client.get(
        f"/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}",
        headers=_headers(),
    )
    assert member_response.status_code == 200, member_response.text
    member = member_response.json()["data"]
    assert member["candidate"]["lifecycle_state"] == "rejected"
    assert member["negative_examples"][0]["negative_example_tags"] == ["low_decision_relevance"]

    rejected = client.get(
        f"/bff/agora/candidate-pools/{pool_id}/members",
        headers=_headers(),
        params={"lifecycle_state": "rejected"},
    )
    assert rejected.status_code == 200, rejected.text
    assert [item["artifact_id"] for item in rejected.json()["items"]] == [artifact_id]


def test_monitoring_and_member_discussion_persist(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    created = _create_pool(client, "ag-be-cp-001-create-monitor")
    pool_id = created["data"]["pool_id"]
    scores = _score_pool(client, pool_id, created["meta"]["etag"], "ag-be-cp-001-score-monitor")
    artifact_id = scores[0]["candidate_id"]

    approve_response = client.post(
        f"/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/review",
        headers=_headers(
            idempotency_key="ag-be-cp-001-review-approve",
            if_match=_get_pool(client, pool_id)["meta"]["etag"],
        ),
        json={
            "decision": "approve_for_monitoring",
            "reviewed_by": "agora-test-user",
            "rationale": "A2 score and evidence support watchlist monitoring.",
        },
    )
    assert approve_response.status_code == 200, approve_response.text

    monitoring_response = client.post(
        f"/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor",
        headers=_headers(
            idempotency_key="ag-be-cp-001-monitor-add",
            if_match=_get_pool(client, pool_id)["meta"]["etag"],
        ),
        json={
            "monitoring_state": "active",
            "trigger_conditions": [
                {
                    "condition_type": "score_drop",
                    "threshold": 12,
                    "description": "Review if effective score falls by 12 points.",
                }
            ],
            "notes": "Watch for score decay before any Trading Room handoff.",
        },
    )
    assert monitoring_response.status_code == 201, monitoring_response.text
    monitoring = monitoring_response.json()["data"]
    _validate(_MONITORING_SCHEMA, monitoring)
    assert monitoring["artifact_id"] == artifact_id
    assert monitoring["monitoring_state"] == "active"

    monitoring_list = client.get(
        f"/bff/agora/candidate-pools/{pool_id}/monitoring",
        headers=_headers(),
    )
    assert monitoring_list.status_code == 200, monitoring_list.text
    assert monitoring_list.json()["items"][0]["artifact_id"] == artifact_id

    discussion_response = client.post(
        f"/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions",
        headers=_headers(idempotency_key="ag-be-cp-001-discussion-create"),
        json={
            "body": "Check whether the score decay trigger should be tighter after the next run.",
            "kind": "score_question",
            "tags": ["a2-score"],
        },
    )
    assert discussion_response.status_code == 201, discussion_response.text
    discussion = discussion_response.json()["data"]
    _validate(_DISCUSSION_SCHEMA, discussion)
    assert discussion["subject_type"] == "member"
    assert discussion["subject_id"] == artifact_id

    discussion_list = client.get(
        f"/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions",
        headers=_headers(),
    )
    assert discussion_list.status_code == 200, discussion_list.text
    assert discussion_list.json()["items"][0]["discussion_id"] == discussion["discussion_id"]
