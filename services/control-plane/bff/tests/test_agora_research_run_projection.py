from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[4]


_OPERATOR_AUTH = "Bearer agora-test-user:operator"
_SCHEMA_PATH = (
    REPO_ROOT
    / "services/control-plane/specs/agora/v4/research_run_projection.schema.json"
)


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    try:
        from services.control_plane.bff.tests.test_agora_strategy_workshop import _workshop_client
    except ImportError:
        from test_agora_strategy_workshop import _workshop_client
    return _workshop_client(monkeypatch)


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


def test_route_get_research_run_provenance_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route-level tests verifying fail-closed schema/version/terminal/owner/correlation receipt validation."""
    client = _client(monkeypatch)
    store = getattr(client, "router", None) and getattr(client.router, "research_store", None) or getattr(client, "app_instance", None) and getattr(client.app_instance, "research_store", None)
    assert store is not None

    workshop_id = "ws-prov-route-test"
    created = _create_plan(client, workshop_id, "prov-route-create-1")
    plan_id = created["data"]["plan_id"]
    _approve_plan(client, plan_id, created["meta"]["etag"], "prov-route-approve-1")
    approved = _get_plan(client, plan_id)
    run_id = _dispatch_plan(client, plan_id, approved["meta"]["etag"], "prov-route-dispatch-1")

    # 1. Non-terminal run (queued) without receipt -> unavailable
    res1 = client.get(f"/bff/agora/research-runs/{run_id}", headers=_headers())
    assert res1.status_code == 200
    assert res1.json()["provenance"] == "unavailable"

    # Update run to succeeded with executor and correlation_id in store
    correlation_id = "corr-prov-route-1"
    executor = "qlib_executor"
    now = "2026-09-08T02:00:00Z"
    run_record = store.get_run(run_id)
    assert run_record is not None
    store.update_run(
        run_id,
        {
            "execution_status": "succeeded",
            "completed_at": now,
            "correlation_id": correlation_id,
            "executor": executor,
            "provenance": "real",
        },
    )

    # 2. Succeeded run claiming real but without receipt -> downgraded to simulation, NEVER real
    res2 = client.get(f"/bff/agora/research-runs/{run_id}", headers=_headers())
    assert res2.status_code == 200
    assert res2.json()["provenance"] != "real"
    assert res2.json()["provenance"] == "simulation"

    # 3. Wrong owner receipt -> unavailable
    store.record_execution_receipt({
        "receipt_id": f"rcpt-{run_id}",
        "run_id": run_id,
        "executor": "wrong_executor",
        "mode": "real",
        "correlation_id": correlation_id,
        "completed_at": now,
        "spec_version": "1.0",
    })
    res3 = client.get(f"/bff/agora/research-runs/{run_id}", headers=_headers())
    assert res3.status_code == 200
    assert res3.json()["provenance"] == "unavailable"

    # 4. Wrong correlation receipt -> unavailable
    store.record_execution_receipt({
        "receipt_id": f"rcpt-{run_id}",
        "run_id": run_id,
        "executor": executor,
        "mode": "real",
        "correlation_id": "wrong-correlation",
        "completed_at": now,
        "spec_version": "1.0",
    })
    res4 = client.get(f"/bff/agora/research-runs/{run_id}", headers=_headers())
    assert res4.status_code == 200
    assert res4.json()["provenance"] == "unavailable"

    # 5. Missing receipt_id -> unavailable
    store.record_execution_receipt({
        "receipt_id": "",
        "run_id": run_id,
        "executor": executor,
        "mode": "real",
        "correlation_id": correlation_id,
        "completed_at": now,
        "spec_version": "1.0",
    })
    res5 = client.get(f"/bff/agora/research-runs/{run_id}", headers=_headers())
    assert res5.status_code == 200
    assert res5.json()["provenance"] == "unavailable"

    # 6. Missing completed_at -> unavailable
    store.record_execution_receipt({
        "receipt_id": f"rcpt-{run_id}",
        "run_id": run_id,
        "executor": executor,
        "mode": "real",
        "correlation_id": correlation_id,
        "completed_at": "",
        "spec_version": "1.0",
    })
    res6 = client.get(f"/bff/agora/research-runs/{run_id}", headers=_headers())
    assert res6.status_code == 200
    assert res6.json()["provenance"] == "unavailable"

    # 7. Invalid spec_version -> unavailable
    store.record_execution_receipt({
        "receipt_id": f"rcpt-{run_id}",
        "run_id": run_id,
        "executor": executor,
        "mode": "real",
        "correlation_id": correlation_id,
        "completed_at": now,
        "spec_version": "2.0",
    })
    res7 = client.get(f"/bff/agora/research-runs/{run_id}", headers=_headers())
    assert res7.status_code == 200
    assert res7.json()["provenance"] == "unavailable"

    # 8. Non-terminal run with matching receipt -> unavailable
    store.update_run(run_id, {"execution_status": "running"})
    store.record_execution_receipt({
        "receipt_id": f"rcpt-{run_id}",
        "run_id": run_id,
        "executor": executor,
        "mode": "real",
        "correlation_id": correlation_id,
        "completed_at": now,
        "spec_version": "1.0",
    })
    res8 = client.get(f"/bff/agora/research-runs/{run_id}", headers=_headers())
    assert res8.status_code == 200
    assert res8.json()["provenance"] == "unavailable"

    # 9. Authentic valid receipt on terminal run -> resolves to 'real'
    store.update_run(run_id, {"execution_status": "succeeded", "provenance": "real"})
    res9 = client.get(f"/bff/agora/research-runs/{run_id}", headers=_headers())
    assert res9.status_code == 200
    assert res9.json()["provenance"] == "real"

    # 10. Mismatched run provenance vs receipt mode (run claims simulation, receipt claims real) -> unavailable
    store.update_run(run_id, {"provenance": "simulation"})
    res10 = client.get(f"/bff/agora/research-runs/{run_id}", headers=_headers())
    assert res10.status_code == 200
    assert res10.json()["provenance"] == "unavailable"
