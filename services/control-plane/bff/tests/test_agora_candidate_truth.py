"""AG-CAND-TRUTH-001-BE — candidate member truth projection contract tests.

Every candidate field rendered by Trading Room (rationale, concerns,
next_event, evidence, details) must be traceable to a durable record of the
same candidate with provenance/as-of, or explicitly typed unavailable.
Covers provenance traceability, typed missing fields, cross-tenant denial,
viewer evidence redaction, stable pagination, and explicit score semantics.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BFF_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(BFF_DIR))

from services.control_plane.bff import main as bff_main
import agora.research.router as research_router  # noqa: E402


_OPERATOR_AUTH = "Bearer agora-truth-user:operator"
_VIEWER_AUTH = "Bearer agora-truth-viewer:viewer"
_OTHER_OPERATOR_AUTH = "Bearer agora-truth-other:operator"
_TRUTH_SCHEMA = (
    REPO_ROOT
    / "services/control-plane/specs/agora/v13/candidate_member_truth_projection.schema.json"
)

_FIELD_NAMES = ("rationale", "concerns", "next_event", "evidence", "details")
_DEFAULT_REGISTRY_CANDIDATES = research_router._default_registry_candidates


def _enable_governed_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Attach persisted evidence refs to the test registry candidates."""
    def candidates_with_evidence(now: str) -> list[dict]:
        candidates = _DEFAULT_REGISTRY_CANDIDATES(now)
        for candidate in candidates:
            artifact_id = candidate["artifact_id"]
            metrics = candidate["_metrics"]
            metrics["evidence_refs"] = {
                component_id: [f"artifact://evidence/{artifact_id}/{component_id}"]
                for component_id in metrics["components"]
            }
        return candidates

    monkeypatch.setattr(
        research_router,
        "_default_registry_candidates",
        candidates_with_evidence,
    )


def _assert_private_score_explanations_absent(
    projection: dict,
    *,
    score_key: str,
) -> None:
    score = projection[score_key]
    assert score is not None
    assert all("explanation" not in component for component in score["components"])

    rationale = projection["fields"]["rationale"]
    if (
        rationale["availability"] == "available"
        and rationale["value"]["kind"] == "score_component_attribution"
    ):
        assert all(
            component["explanation"] is None
            for component in rationale["value"]["top_components"]
        )

    concerns = projection["fields"]["concerns"]
    if concerns["availability"] == "available":
        assert all(
            component["explanation"] is None
            for component in concerns["value"]["penalty_components"]
        )


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    return TestClient(bff_main.app, raise_server_exceptions=False)


def _headers(
    auth: str = _OPERATOR_AUTH,
    *,
    idempotency_key: str | None = None,
    if_match: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, str]:
    headers = {"Authorization": auth, "X-Request-Id": "req-ag-cand-truth-001"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    if if_match is not None:
        headers["If-Match"] = if_match
    if tenant_id is not None:
        headers["X-Tenant-Id"] = tenant_id
    return headers


def _truth_validator(name: str):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(_TRUTH_SCHEMA.read_text(encoding="utf-8"))
    return jsonschema.Draft7Validator(
        schema["definitions"][name],
        resolver=jsonschema.RefResolver.from_schema(schema),
        format_checker=jsonschema.FormatChecker(),
    )


def _validate_truth(name: str, payload: dict) -> None:
    errors = list(_truth_validator(name).iter_errors(payload))
    assert errors == [], errors


def _create_pool(client: TestClient, key: str, *, auth: str = _OPERATOR_AUTH, operator_id: str) -> dict:
    response = client.post(
        "/bff/agora/candidate-pools",
        headers=_headers(auth, idempotency_key=key),
        json={"operator_id": operator_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _score_pool(client: TestClient, pool_id: str, etag: str, key: str, *, auth: str = _OPERATOR_AUTH) -> None:
    response = client.post(
        f"/bff/agora/candidate-pools/{pool_id}/score",
        headers=_headers(auth, idempotency_key=key, if_match=etag),
        json={},
    )
    assert response.status_code == 202, response.text


def _list_members(client: TestClient, pool_id: str, *, auth: str = _OPERATOR_AUTH, **params) -> dict:
    response = client.get(
        f"/bff/agora/candidate-pools/{pool_id}/members",
        headers=_headers(auth),
        params=params,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _get_member(client: TestClient, pool_id: str, artifact_id: str, *, auth: str = _OPERATOR_AUTH) -> dict:
    response = client.get(
        f"/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}",
        headers=_headers(auth),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _pool_etag(client: TestClient, pool_id: str, *, auth: str = _OPERATOR_AUTH) -> str:
    response = client.get(f"/bff/agora/candidate-pools/{pool_id}", headers=_headers(auth))
    assert response.status_code == 200, response.text
    return response.json()["meta"]["etag"]


def test_every_available_field_traces_to_the_requested_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)
    created = _create_pool(client, "ag-cand-truth-trace-create", operator_id="agora-truth-user")
    pool_id = created["data"]["pool_id"]
    _score_pool(client, pool_id, created["meta"]["etag"], "ag-cand-truth-trace-score")

    body = _list_members(client, pool_id)
    assert len(body["items"]) == 2
    for item in body["items"]:
        artifact_id = item["artifact_id"]
        _validate_truth("CandidateMemberTruthProjection", {
            "artifact_id": artifact_id,
            "fields": item["fields"],
            "as_of": item["as_of"],
            "score_semantics": item["score_semantics"],
        })
        for field_name in _FIELD_NAMES:
            state = item["fields"][field_name]
            if state["availability"] != "available":
                continue
            source_ref = state["provenance"]["source_ref"]
            assert artifact_id in source_ref, (field_name, source_ref)
            assert state["provenance"]["as_of"], field_name

    # Rationale/evidence content must come from each candidate's own score
    # result, not another candidate's text: the two default candidates have
    # distinct component values, so their projections must differ.
    first, second = body["items"]
    assert first["fields"]["rationale"]["value"] != second["fields"]["rationale"]["value"]
    assert first["fields"]["evidence"] == {
        "availability": "unavailable",
        "reason": "no_governed_source",
    }
    assert second["fields"]["evidence"] == {
        "availability": "unavailable",
        "reason": "no_governed_source",
    }


def test_unscored_pool_returns_typed_unavailable_fields_not_static_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)
    created = _create_pool(client, "ag-cand-truth-unscored-create", operator_id="agora-truth-user")
    pool_id = created["data"]["pool_id"]

    body = _list_members(client, pool_id)
    assert body["meta"]["freshness"]["last_score_run_at"] is None
    for item in body["items"]:
        assert item["fields"]["rationale"] == {
            "availability": "unavailable", "reason": "score_not_run",
        }
        assert item["fields"]["concerns"] == {
            "availability": "unavailable", "reason": "score_not_run",
        }
        assert item["fields"]["evidence"] == {
            "availability": "unavailable", "reason": "score_not_run",
        }
        assert item["fields"]["next_event"] == {
            "availability": "unavailable", "reason": "no_governed_source",
        }
        details = item["fields"]["details"]
        assert details["availability"] == "available"
        assert details["provenance"]["source_type"] == "candidate_pool_member"
        assert item["score_semantics"]["effective_score"]["availability"] == "unavailable"
        assert item["score_semantics"]["effective_score"]["reason"] == "score_not_run"


def test_next_event_requires_a_governed_monitoring_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)
    created = _create_pool(client, "ag-cand-truth-nextevent-create", operator_id="agora-truth-user")
    pool_id = created["data"]["pool_id"]
    _score_pool(client, pool_id, created["meta"]["etag"], "ag-cand-truth-nextevent-score")

    artifact_id = _list_members(client, pool_id)["items"][0]["artifact_id"]
    approve = client.post(
        f"/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/review",
        headers=_headers(
            idempotency_key="ag-cand-truth-nextevent-approve",
            if_match=_pool_etag(client, pool_id),
        ),
        json={
            "decision": "approve_for_monitoring",
            "reviewed_by": "agora-truth-user",
            "rationale": "Score evidence supports monitoring for this candidate.",
        },
    )
    assert approve.status_code == 200, approve.text
    member_mutated_at = approve.json()["meta"]["snapshot_at"]

    monitor = client.post(
        f"/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor",
        headers=_headers(
            idempotency_key="ag-cand-truth-nextevent-monitor",
            if_match=_pool_etag(client, pool_id),
        ),
        json={
            "monitoring_state": "active",
            "review_due_at": "2026-08-01T00:00:00Z",
            "trigger_conditions": [
                {"condition_type": "score_drop", "threshold": 10}
            ],
        },
    )
    assert monitor.status_code == 201, monitor.text

    member = _get_member(client, pool_id, artifact_id)["data"]
    assert member["fields"]["details"]["provenance"]["as_of"] == member_mutated_at
    listed_member = next(
        item for item in _list_members(client, pool_id)["items"]
        if item["artifact_id"] == artifact_id
    )
    assert listed_member["fields"]["details"]["provenance"]["as_of"] == member_mutated_at
    next_event = member["fields"]["next_event"]
    assert next_event["availability"] == "available"
    assert next_event["value"]["kind"] == "monitoring_schedule"
    assert next_event["value"]["review_due_at"] == "2026-08-01T00:00:00Z"
    assert next_event["provenance"]["source_type"] == "candidate_monitoring"
    assert artifact_id in next_event["provenance"]["source_ref"]

    # The other, unmonitored candidate must stay typed-unavailable.
    other = next(
        item for item in _list_members(client, pool_id)["items"]
        if item["artifact_id"] != artifact_id
    )
    assert other["fields"]["next_event"]["reason"] == "no_governed_source"

    # Post-review the rationale must switch to the operator's recorded review.
    rationale = member["fields"]["rationale"]
    assert rationale["value"]["kind"] == "operator_review_rationale"
    assert rationale["value"]["rationale"].startswith("Score evidence supports")
    assert rationale["provenance"]["source_type"] == "candidate_review"


def test_cross_tenant_and_cross_user_reads_are_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)
    created = _create_pool(client, "ag-cand-truth-tenant-create", operator_id="agora-truth-user")
    pool_id = created["data"]["pool_id"]

    cross_tenant = client.get(
        f"/bff/agora/candidate-pools/{pool_id}/members",
        headers=_headers(tenant_id="tenant-not-allowed"),
    )
    assert cross_tenant.status_code == 403, cross_tenant.text

    cross_user = client.get(
        f"/bff/agora/candidate-pools/{pool_id}/members",
        headers=_headers(_OTHER_OPERATOR_AUTH),
    )
    assert cross_user.status_code == 403, cross_user.text


def test_evidence_summaries_are_redacted_by_role_and_in_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_governed_evidence(monkeypatch)
    client = _client(monkeypatch)

    # Operator-owned pool: detail summaries visible, list summaries redacted.
    created = _create_pool(client, "ag-cand-truth-redact-op-create", operator_id="agora-truth-user")
    pool_id = created["data"]["pool_id"]
    _score_pool(client, pool_id, created["meta"]["etag"], "ag-cand-truth-redact-op-score")

    score_list = client.get(
        f"/bff/agora/candidate-pools/{pool_id}/score",
        headers=_headers(),
    )
    assert score_list.status_code == 200, score_list.text
    assert all(
        "explanation" not in component
        for score in score_list.json()["items"]
        for component in score["components"]
    )

    list_item = _list_members(client, pool_id)["items"][0]
    _assert_private_score_explanations_absent(
        list_item,
        score_key="current_score",
    )
    for evidence_item in list_item["fields"]["evidence"]["value"]["items"]:
        assert evidence_item["summary"] is None
        assert evidence_item["summary_redacted"] is True
        assert evidence_item["redaction_reason"] == "list_response"

    detail = _get_member(client, pool_id, list_item["artifact_id"])["data"]
    detail_items = detail["fields"]["evidence"]["value"]["items"]
    assert detail_items, "scored candidate must expose evidence refs"
    for evidence_item in detail_items:
        assert evidence_item["summary_redacted"] is False
        assert evidence_item["summary"]
    assert all(component["explanation"] for component in detail["score"]["components"])
    assert all(
        component["explanation"]
        for component in detail["fields"]["rationale"]["value"]["top_components"]
    )
    assert all(
        component["explanation"]
        for component in detail["fields"]["concerns"]["value"]["penalty_components"]
    )

    # Viewer-owned pool: even the detail read keeps summaries redacted.
    viewer_created = _create_pool(
        client, "ag-cand-truth-redact-viewer-create",
        auth=_VIEWER_AUTH, operator_id="agora-truth-viewer",
    )
    viewer_pool_id = viewer_created["data"]["pool_id"]
    _score_pool(
        client, viewer_pool_id, viewer_created["meta"]["etag"],
        "ag-cand-truth-redact-viewer-score", auth=_VIEWER_AUTH,
    )
    viewer_item = _list_members(client, viewer_pool_id, auth=_VIEWER_AUTH)["items"][0]
    _assert_private_score_explanations_absent(
        viewer_item,
        score_key="current_score",
    )
    viewer_detail = _get_member(
        client, viewer_pool_id, viewer_item["artifact_id"], auth=_VIEWER_AUTH,
    )["data"]
    _assert_private_score_explanations_absent(viewer_detail, score_key="score")
    for evidence_item in viewer_detail["fields"]["evidence"]["value"]["items"]:
        assert evidence_item["summary"] is None
        assert evidence_item["summary_redacted"] is True
        assert evidence_item["redaction_reason"] == "viewer_role"


def test_generated_recipe_requirements_are_not_durable_evidence_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)
    created = _create_pool(
        client,
        "ag-cand-truth-no-evidence-create",
        operator_id="agora-truth-user",
    )
    pool_id = created["data"]["pool_id"]
    _score_pool(
        client,
        pool_id,
        created["meta"]["etag"],
        "ag-cand-truth-no-evidence-score",
    )

    for item in _list_members(client, pool_id)["items"]:
        assert item["fields"]["evidence"] == {
            "availability": "unavailable",
            "reason": "no_governed_source",
        }
        assert all(
            component["evidence_refs"] == []
            for component in item["current_score"]["components"]
        )


def test_details_provenance_uses_persisted_member_mutation_timestamp() -> None:
    projection = research_router._member_truth_projection(
        pool={
            "pool_id": "cpool-mutation",
            "snapshot_at": "2026-07-22T00:00:00Z",
        },
        member={
            "artifact_id": "artifact-mutation",
            "strategy_ref": "strategy://winner-branch/default",
            "lifecycle_state": "approved",
            "created_at": "2026-07-22T00:00:00Z",
            "_updated_at": "2026-07-22T02:00:00Z",
        },
        score=None,
        reviews=[],
        monitoring=None,
        recipe={"penalty_components": []},
        evidence_summary_mode="list_response",
        operator_grade=True,
    )

    details = projection["fields"]["details"]
    assert details["value"]["lifecycle_state"] == "approved"
    assert details["provenance"]["as_of"] == "2026-07-22T02:00:00Z"
    assert projection["as_of"] == "2026-07-22T02:00:00Z"


def test_member_pagination_is_stable_and_freshness_observable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)
    created = _create_pool(client, "ag-cand-truth-page-create", operator_id="agora-truth-user")
    pool_id = created["data"]["pool_id"]
    _score_pool(client, pool_id, created["meta"]["etag"], "ag-cand-truth-page-score")

    first_page = _list_members(client, pool_id, page_size=1)
    _validate_truth("CandidateMemberPageInfo", first_page["page_info"])
    _validate_truth("CandidateMemberListFreshness", first_page["meta"]["freshness"])
    assert first_page["page_info"]["total"] == 2
    assert first_page["page_info"]["has_more"] is True
    assert first_page["page_info"]["order_by"] == "created_at,artifact_id"
    assert first_page["meta"]["freshness"]["pool_snapshot_at"]
    assert first_page["meta"]["freshness"]["last_score_run_at"]
    token = first_page["page_info"]["next_page_token"]
    assert token

    second_page = _list_members(client, pool_id, page_size=1, page_token=token)
    assert second_page["page_info"]["has_more"] is False
    assert second_page["page_info"]["next_page_token"] is None
    ids = [first_page["items"][0]["artifact_id"], second_page["items"][0]["artifact_id"]]
    assert len(set(ids)) == 2

    # Same query twice → identical stable order.
    repeat = _list_members(client, pool_id, page_size=1)
    assert repeat["items"][0]["artifact_id"] == ids[0]

    invalid = client.get(
        f"/bff/agora/candidate-pools/{pool_id}/members",
        headers=_headers(),
        params={"page_token": "not-a-token"},
    )
    assert invalid.status_code == 422, invalid.text


def test_sharpe_derived_score_semantics_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)
    created = _create_pool(client, "ag-cand-truth-sharpe-create", operator_id="agora-truth-user")
    pool_id = created["data"]["pool_id"]
    _score_pool(client, pool_id, created["meta"]["etag"], "ag-cand-truth-sharpe-score")

    for item in _list_members(client, pool_id)["items"]:
        semantics = item["score_semantics"]
        _validate_truth("CandidateScoreSemantics", semantics)
        sharpe = semantics["sharpe_summary"]
        assert sharpe["kind"] == "sharpe_ratio"
        assert sharpe["is_confidence_score"] is False
        assert sharpe["transformation"] == "sharpe_ratio_from_producing_research_run"
        assert sharpe["source_ref"] == item["run_ref"]
        effective = semantics["effective_score"]
        assert effective["kind"] == "recipe_weighted_score"
        assert effective["is_confidence_score"] is False
        assert effective["recipe_id"] == item["current_score"]["recipe_id"]
        assert item["artifact_id"] in effective["source_ref"]
