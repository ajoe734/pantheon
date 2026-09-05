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


_OPERATOR_AUTH = "Bearer agora-test-user:operator"
_SCHEMA_PATH = (
    REPO_ROOT
    / "services/control-plane/specs/agora/v4/research_run_projection.schema.json"
)


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    return TestClient(bff_main.app, raise_server_exceptions=False)


def _headers(idempotency_key: str | None = None, if_match: str | None = None) -> dict[str, str]:
    headers = {"Authorization": _OPERATOR_AUTH}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    if if_match is not None:
        headers["If-Match"] = if_match
    return headers


def _create_plan(client: TestClient, workshop_id: str, idempotency_key: str) -> dict:
    response = client.post(
        f"/bff/agora/workshops/{workshop_id}/research-plans",
        headers=_headers(idempotency_key=idempotency_key),
        json={
            "spec_version": "1.0",
            "strategy_id": f"strategy-{workshop_id}",
            "strategy_spec_registry_id": f"registry-{workshop_id}-v1",
            "stages": [
                {
                    "stage_id": "stage-prototype-backtest",
                    "stage_type": "prototype_backtest",
                    "status": "ready",
                    "dependencies": [],
                    "routing": {
                        "backend_mode": "fixture",
                        "fallback_policy": "explicit_fixture_only",
                    },
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _approve_plan(client: TestClient, plan_id: str, etag: str, idempotency_key: str) -> None:
    response = client.post(
        f"/bff/agora/research-plans/{plan_id}/approve",
        headers=_headers(idempotency_key=idempotency_key, if_match=etag),
    )
    assert response.status_code == 200, response.text


def _get_plan(client: TestClient, plan_id: str) -> dict:
    response = client.get(
        f"/bff/agora/research-plans/{plan_id}",
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _dispatch_plan(client: TestClient, plan_id: str, etag: str, idempotency_key: str) -> str:
    response = client.post(
        f"/bff/agora/research-plans/{plan_id}/runs",
        headers=_headers(idempotency_key=idempotency_key, if_match=etag),
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]["run_id"]


def test_research_run_detail_returns_schema_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    workshop_id = "ws-ag-be-rs-002-projection"
    created = _create_plan(client, workshop_id, "ag-be-rs-002-create-projection")
    plan_id = created["data"]["plan_id"]

    _approve_plan(
        client,
        plan_id,
        created["meta"]["etag"],
        "ag-be-rs-002-approve-projection",
    )
    approved = _get_plan(client, plan_id)
    run_id = _dispatch_plan(
        client,
        plan_id,
        approved["meta"]["etag"],
        "ag-be-rs-002-dispatch-projection",
    )

    response = client.get(
        f"/bff/agora/research-runs/{run_id}",
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    run = response.json()
    assert "data" not in run
    assert run["run_id"] == run_id
    assert run["plan_id"] == plan_id
    assert run["workshop_id"] == workshop_id
    assert run["execution_status"] == "queued"
    assert run["outcome"] == "pending"
    assert run["progress"]["phase"] == "queued"
    assert run["progress"]["percent"] == 0
    assert run["backend"] == {
        "requested": "vectorbt",
        "effective": "vectorbt",
        "mode": "fixture",
    }
    assert run["metrics"] == []
    assert run["artifact_refs"] == []
    assert run["evidence_refs"] == []
    assert run["no_order_route_proof"] == "research_only_not_direct_action"

    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(_SCHEMA_PATH.read_text())
    jsonschema.Draft7Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    ).validate(run)


def test_research_run_list_artifacts_and_sse_are_canonical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agora.strategy_workshop.router import _workshop_sse_buffers

    _workshop_sse_buffers.clear()
    client = _client(monkeypatch)
    workshop_id = "ws-ag-be-rs-002-events"
    created = _create_plan(client, workshop_id, "ag-be-rs-002-create-events")
    plan_id = created["data"]["plan_id"]
    _approve_plan(client, plan_id, created["meta"]["etag"], "ag-be-rs-002-approve-events")
    approved = _get_plan(client, plan_id)
    run_id = _dispatch_plan(
        client,
        plan_id,
        approved["meta"]["etag"],
        "ag-be-rs-002-dispatch-events",
    )

    list_response = client.get(
        f"/bff/agora/research-plans/{plan_id}/runs",
        headers=_headers(),
    )
    assert list_response.status_code == 200, list_response.text
    listed = list_response.json()["items"]
    assert listed[0]["run_id"] == run_id
    assert listed[0]["artifact_refs"] == []
    assert listed[0]["evidence_refs"] == []

    artifact_response = client.get(
        f"/bff/agora/research-runs/{run_id}/artifacts",
        headers=_headers(),
    )
    assert artifact_response.status_code == 200, artifact_response.text
    assert artifact_response.json()["items"] == []

    event_types = [event["type"] for _, event in _workshop_sse_buffers[workshop_id]]
    assert event_types == [
        "research.plan.created",
        "research.plan.approved",
        "research.run.queued",
    ]
