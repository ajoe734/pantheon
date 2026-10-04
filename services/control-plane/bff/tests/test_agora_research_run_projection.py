from __future__ import annotations

import json
import sys
from copy import deepcopy
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
    from services.control_plane.bff.agora.strategy_workshop.operations import WorkshopCanonicalOperations

    monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://research-owner.test")
    records: dict[str, dict] = {}

    def dispatch(_self, *, task_payload, run_payload, resume=None):
        task_id = "owner-task-" + str(len(records) + 1)
        run_id = "owner-run-" + str(len(records) + 1)
        params = run_payload.get("parameters") or {}
        stage = params.get("stage") or {}
        run = {
            "run_id": run_id,
            "task_id": task_id,
            "status": "queued",
            "execution_status": "queued",
            "outcome": "pending",
            "stage_id": stage.get("stage_id"),
            "adapter": run_payload.get("adapter"),
            "input_refs": run_payload.get("input_refs") or [],
            "tenant_id": run_payload.get("tenant_id"),
            "user_id": run_payload.get("user_id"),
            "created_at": "2026-10-03T00:00:00Z",
            "artifact_refs": [],
            "evidence_refs": [],
            "parameters": params,
        }
        records[run_id] = run
        return {"task": {"task_id": task_id}, "run": run}

    def cancel_run(_self, run_id, **_kwargs):
        if run_id not in records:
            from services.control_plane.bff.agora.strategy_workshop.operations import CanonicalOperationError
            raise CanonicalOperationError("research_orchestrator", "not found", status_code=404)
        run = records[run_id]
        if run.get("status") in ("completed", "rejected"):
            from services.control_plane.bff.agora.strategy_workshop.operations import CanonicalOperationError
            raise CanonicalOperationError("research_orchestrator", "conflict", status_code=409)
        run["status"] = "canceled"
        run["execution_status"] = "canceled"
        return run

    def cancel_task(_self, task_id, **_kwargs):
        return {"task_id": task_id, "status": "canceled"}

    monkeypatch.setattr(WorkshopCanonicalOperations, "dispatch_research_run", dispatch)
    monkeypatch.setattr(WorkshopCanonicalOperations, "cancel_research_run", cancel_run)
    monkeypatch.setattr(WorkshopCanonicalOperations, "cancel_research_task", cancel_task)
    monkeypatch.setattr(WorkshopCanonicalOperations, "list_research_runs", lambda _self, **_kwargs: list(records.values()))
    monkeypatch.setattr(WorkshopCanonicalOperations, "get_research_run", lambda _self, run_id: records[run_id])
    monkeypatch.setattr(WorkshopCanonicalOperations, "get_research_artifacts", lambda _self, _run_id: [])
    client = _workshop_client(monkeypatch)
    client.owner_research_runs = records
    return client


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


def test_cancel_before_dispatch_and_active_cancel_are_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    created = _create_plan(client, "ws-terminal-plan", "terminal-create")
    plan_id = created["data"]["plan_id"]
    _approve_plan(client, plan_id, created["meta"]["etag"], "terminal-approve")
    approved = _get_plan(client, plan_id)
    cancelled = client.post(
        f"/bff/agora/research-plans/{plan_id}/cancel",
        headers=_headers("terminal-cancel", approved["meta"]["etag"]),
    )
    assert cancelled.status_code == 200, cancelled.text
    assert _get_plan(client, plan_id)["data"]["status"] == "cancelled"
    redispatch = client.post(
        f"/bff/agora/research-plans/{plan_id}/runs",
        headers=_headers("terminal-redispatch", _get_plan(client, plan_id)["meta"]["etag"]),
    )
    assert redispatch.status_code == 409

    active = _create_plan(client, "ws-active-cancel", "active-cancel-create")
    active_id = active["data"]["plan_id"]
    _approve_plan(client, active_id, active["meta"]["etag"], "active-cancel-approve")
    _dispatch_plan(client, active_id, _get_plan(client, active_id)["meta"]["etag"], "active-cancel-dispatch")
    readback = _get_plan(client, active_id)
    response = client.post(
        f"/bff/agora/research-plans/{active_id}/cancel",
        headers=_headers("active-cancel", readback["meta"]["etag"]),
    )
    assert response.status_code == 200, response.text
    assert _get_plan(client, active_id)["data"]["status"] == "cancelled"

    completed = _create_plan(client, "ws-completed-replay", "completed-create")
    completed_id = completed["data"]["plan_id"]
    _approve_plan(client, completed_id, completed["meta"]["etag"], "completed-approve")
    first_run = _dispatch_plan(client, completed_id, _get_plan(client, completed_id)["meta"]["etag"], "completed-dispatch")
    client.owner_research_runs[first_run]["status"] = "completed"
    projected = _get_plan(client, completed_id)
    assert projected["data"]["status"] == "completed"
    replay = client.post(
        f"/bff/agora/research-plans/{completed_id}/runs",
        headers=_headers("completed-replay", projected["meta"]["etag"]),
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["data"]["run_id"] == first_run


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


def test_route_get_research_run_provenance_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    created = _create_plan(client, "ws-prov-route-test", "prov-route-create")
    plan_id = created["data"]["plan_id"]
    _approve_plan(client, plan_id, created["meta"]["etag"], "prov-route-approve")
    run_id = _dispatch_plan(client, plan_id, _get_plan(client, plan_id)["meta"]["etag"], "prov-route-dispatch")
    owner_run = client.owner_research_runs[run_id]
    url = f"/bff/agora/research-runs/{run_id}"

    response = client.get(url, headers=_headers())
    assert response.status_code == 200
    assert response.json()["provenance"] == "unavailable"

    now, correlation, executor = "2026-09-08T02:00:00Z", owner_run["parameters"]["correlation_id"], "qlib_executor"
    owner_run.update({"status": "completed", "correlation_id": correlation, "executor": executor, "provenance": "real"})
    owner_run["receipt"] = {
        "receipt_id": f"rcpt-{run_id}", "run_id": run_id, "executor": executor,
        "mode": "real", "correlation_id": correlation, "completed_at": now, "spec_version": "1.0",
    }
    assert client.get(url, headers=_headers()).json()["provenance"] == "real"
    owner_run["receipt"]["executor"] = "wrong_executor"
    assert client.get(url, headers=_headers()).json()["provenance"] == "unavailable"
    owner_run["receipt"]["executor"] = executor
    owner_run["receipt"]["correlation_id"] = "wrong-correlation"
    assert client.get(url, headers=_headers()).json()["provenance"] == "unavailable"
    owner_run["receipt"].update({"correlation_id": correlation, "spec_version": "2.0"})
    assert client.get(url, headers=_headers()).json()["provenance"] == "unavailable"
    owner_run["status"] = "running"
    owner_run["receipt"]["spec_version"] = "1.0"
    assert client.get(url, headers=_headers()).json()["provenance"] == "unavailable"


def test_workshop_preserves_research_owner_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.control_plane.bff.research.client import ResearchServiceClient, ResearchCommandError
    from services.control_plane.bff.agora.strategy_workshop.operations import (
        WorkshopCanonicalOperations, CanonicalOperationError,
    )

    ops = WorkshopCanonicalOperations(research_base_url="http://research-owner.test")
    for status_code, expected_retryable in [
        (400, False),
        (404, False),
        (409, False),
        (503, True),
    ]:
        def mock_call(*args, **kwargs):
            raise ResearchCommandError(f"error with status {status_code}", status_code=status_code)

        monkeypatch.setattr(ResearchServiceClient, "_call", mock_call)
        with pytest.raises(CanonicalOperationError) as exc_info:
            ops.cancel_research_run(f"run-{status_code}")
        assert exc_info.value.status_code == status_code
        assert exc_info.value.retryable is expected_retryable
        assert f"error with status {status_code}" in str(exc_info.value)
        assert exc_info.value.authority == "research_orchestrator"


def test_mounted_cancel_owner_failure_preserves_nonterminal_and_allows_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.control_plane.bff.agora.strategy_workshop.operations import (
        WorkshopCanonicalOperations, CanonicalOperationError,
    )
    client = _client(monkeypatch)
    workshop_id = "ws-cancel-owner-fail"
    created = _create_plan(client, workshop_id, "cancel-fail-create")
    pid = created["data"]["plan_id"]
    _approve_plan(client, pid, created["meta"]["etag"], "cancel-fail-approve")
    approved = _get_plan(client, pid)
    rid = _dispatch_plan(client, pid, approved["meta"]["etag"], "cancel-fail-dispatch")

    should_fail = True

    def flakey_cancel(_self, run_id, **kwargs):
        if should_fail:
            raise CanonicalOperationError("research_orchestrator", "owner unavailable", status_code=503)
        client.owner_research_runs[run_id]["status"] = "canceled"
        return client.owner_research_runs[run_id]

    monkeypatch.setattr(WorkshopCanonicalOperations, "cancel_research_run", flakey_cancel)

    # 1. Attempt cancel while owner is unavailable
    fail_res = client.post(
        f"/bff/agora/research-plans/{pid}/cancel",
        headers=_headers("cancel-attempt-1", _get_plan(client, pid)["meta"]["etag"]),
    )
    assert fail_res.status_code == 503
    readback = _get_plan(client, pid)
    assert readback["data"]["status"] == "running"
    assert client.owner_research_runs[rid]["status"] == "queued"

    # 2. Owner recovers; retry cancel succeeds
    should_fail = False
    retry_res = client.post(
        f"/bff/agora/research-plans/{pid}/cancel",
        headers=_headers("cancel-attempt-2", readback["meta"]["etag"]),
    )
    assert retry_res.status_code == 200
    final_readback = _get_plan(client, pid)
    assert final_readback["data"]["status"] == "cancelled"
    assert client.owner_research_runs[rid]["status"] == "canceled"

    # 3. Redispatch is rejected
    redispatch = client.post(
        f"/bff/agora/research-plans/{pid}/runs",
        headers=_headers("cancel-redispatch", final_readback["meta"]["etag"]),
    )
    assert redispatch.status_code == 409


def test_mounted_cancel_unconfigured_owner_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    workshop_id = "ws-cancel-unconfigured"
    created = _create_plan(client, workshop_id, "cancel-unconf-create")
    pid = created["data"]["plan_id"]
    _approve_plan(client, pid, created["meta"]["etag"], "cancel-unconf-approve")
    approved = _get_plan(client, pid)
    rid = _dispatch_plan(client, pid, approved["meta"]["etag"], "cancel-unconf-dispatch")

    monkeypatch.delenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", raising=False)
    monkeypatch.delenv("RESEARCH_ORCHESTRATOR_URL", raising=False)
    monkeypatch.delenv("RESEARCH_ORCHESTRATOR_API_URL", raising=False)

    res = client.post(
        f"/bff/agora/research-plans/{pid}/cancel",
        headers=_headers("cancel-unconf-req", _get_plan(client, pid)["meta"]["etag"]),
    )
    assert res.status_code == 503
    assert res.json()["error"]["code"] in ("DEPENDENCY_UNAVAILABLE", "UPSTREAM_UNAVAILABLE")
    readback = _get_plan(client, pid)
    assert readback["data"]["status"] == "running"
    assert client.owner_research_runs[rid]["status"] == "queued"


def test_mounted_cancel_unavailable_owner_projection_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.control_plane.bff.agora.strategy_workshop.operations import (
        WorkshopCanonicalOperations, CanonicalOperationError,
    )
    client = _client(monkeypatch)
    workshop_id = "ws-cancel-unavail-proj"
    created = _create_plan(client, workshop_id, "cancel-proj-create")
    pid = created["data"]["plan_id"]
    _approve_plan(client, pid, created["meta"]["etag"], "cancel-proj-approve")
    approved = _get_plan(client, pid)
    _dispatch_plan(client, pid, approved["meta"]["etag"], "cancel-proj-dispatch")

    def broken_list(_self, **_kwargs):
        raise CanonicalOperationError("research_orchestrator", "owner unreachable", status_code=503)

    monkeypatch.setattr(WorkshopCanonicalOperations, "list_research_runs", broken_list)

    res = client.post(
        f"/bff/agora/research-plans/{pid}/cancel",
        headers=_headers("cancel-proj-req", _get_plan(client, pid)["meta"]["etag"]),
    )
    assert res.status_code == 503
    assert res.json()["error"]["code"] in ("DEPENDENCY_UNAVAILABLE", "UPSTREAM_UNAVAILABLE")


def test_mounted_cancel_partial_failure_preserves_nonterminal_retryable_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.control_plane.bff.agora.strategy_workshop.operations import (
        WorkshopCanonicalOperations, CanonicalOperationError,
    )
    client = _client(monkeypatch)
    store = getattr(client, "router", None) and getattr(client.router, "research_store", None) or getattr(client, "app_instance", None) and getattr(client.app_instance, "research_store", None)
    workshop_id = "ws-cancel-partial"
    created = _create_plan(client, workshop_id, "cancel-partial-create")
    pid = created["data"]["plan_id"]
    _approve_plan(client, pid, created["meta"]["etag"], "cancel-partial-approve")
    approved = _get_plan(client, pid)
    rid1 = _dispatch_plan(client, pid, approved["meta"]["etag"], "cancel-partial-dispatch-1")

    # Inject second run into store and owner
    rid2 = "owner-run-2"
    client.owner_research_runs[rid2] = {
        "run_id": rid2,
        "task_id": "owner-task-2",
        "status": "running",
        "execution_status": "running",
        "outcome": "pending",
        "stage_id": "stage-prototype-backtest",
        "input_refs": [{"type": "research_plan", "id": pid}],
        "created_at": "2026-10-03T00:00:00Z",
    }


    # When cancelling, rid1 succeeds but rid2 fails
    fail_rid2 = True

    def partial_cancel(_self, run_id, **kwargs):
        if run_id == rid2 and fail_rid2:
            raise CanonicalOperationError("research_orchestrator", "run 2 timeout", status_code=504)
        client.owner_research_runs[run_id]["status"] = "canceled"
        return client.owner_research_runs[run_id]

    monkeypatch.setattr(WorkshopCanonicalOperations, "cancel_research_run", partial_cancel)

    readback_before = _get_plan(client, pid)
    res = client.post(
        f"/bff/agora/research-plans/{pid}/cancel",
        headers=_headers("cancel-partial-req-1", readback_before["meta"]["etag"]),
    )
    assert res.status_code == 504
    # Plan remains nonterminal (running)
    readback_mid = _get_plan(client, pid)
    assert readback_mid["data"]["status"] == "running"
    # Run 1 was cancelled locally and on owner
    assert client.owner_research_runs[rid1]["status"] == "canceled"
    # Run 2 is still running
    assert client.owner_research_runs[rid2]["status"] == "running"

    # Now owner recovers on rid2, retry succeeds
    fail_rid2 = False
    retry_res = client.post(
        f"/bff/agora/research-plans/{pid}/cancel",
        headers=_headers("cancel-partial-req-2", readback_mid["meta"]["etag"]),
    )
    assert retry_res.status_code == 200
    readback_after = _get_plan(client, pid)
    assert readback_after["data"]["status"] == "cancelled"
    assert client.owner_research_runs[rid2]["status"] == "canceled"


def _setup_two_stages(monkeypatch: pytest.MonkeyPatch, tag: str):
    client = _client(monkeypatch)
    created = _create_plan(client, f"ws-review-{tag}", f"{tag}-create")
    pid = created["data"]["plan_id"]
    store = getattr(client, "router", None) and getattr(client.router, "research_store", None) or getattr(client, "app_instance", None) and getattr(client.app_instance, "research_store", None)
    plan = store.get_plan(pid)
    stages = deepcopy(plan["stages"])
    stages.append({**deepcopy(stages[0]), "stage_id": "stage-second", "dependencies": [stages[0]["stage_id"]]})
    store.update_plan(pid, {"stages": stages})
    _approve_plan(client, pid, created["meta"]["etag"], f"{tag}-approve")
    rid = _dispatch_plan(client, pid, _get_plan(client, pid)["meta"]["etag"], f"{tag}-dispatch")
    root = client.owner_research_runs[rid]
    root["status"] = "completed"
    child_id = "owner-run-2"
    client.owner_research_runs[child_id] = {
        **deepcopy(root),
        "run_id": child_id,
        "stage_id": "stage-second",
        "status": "running",
        "execution_status": "running",
        "input_refs": [{"type": "research_plan", "id": pid}, {"type": "stage", "id": "stage-second"}],
    }
    return client, store, pid, rid, child_id


def test_cancel_after_root_completed_uses_owner_status(monkeypatch: pytest.MonkeyPatch) -> None:
    client, store, pid, rid, child_id = _setup_two_stages(monkeypatch, "stale-root")
    response = client.post(
        f"/bff/agora/research-plans/{pid}/cancel",
        headers=_headers("stale-root-cancel", _get_plan(client, pid)["meta"]["etag"]),
    )
    assert response.status_code == 200, response.text
    assert client.owner_research_runs[child_id]["status"] == "canceled"


def test_other_operator_cannot_miss_owner_only_successor(monkeypatch: pytest.MonkeyPatch) -> None:
    client, store, pid, rid, child_id = _setup_two_stages(monkeypatch, "other-operator")
    client.owner_research_runs[rid]["status"] = "completed"
    headers = _headers("other-operator-cancel", _get_plan(client, pid)["meta"]["etag"])
    headers["Authorization"] = "Bearer second-operator:operator"
    response = client.post(f"/bff/agora/research-plans/{pid}/cancel", headers=headers)
    assert response.status_code == 200, response.text
    assert client.owner_research_runs[child_id]["status"] == "canceled", (
        "BFF returned cancellation success while owner-only successor stayed running",
        response.text,
        client.owner_research_runs[child_id]["status"],
        store.get_plan(pid)["status"],
    )


def test_cancel_reconciles_terminal_race_when_owner_returns_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    client, store, pid, rid, child_id = _setup_two_stages(monkeypatch, "race-conflict")
    from services.control_plane.bff.agora.strategy_workshop.operations import (
        WorkshopCanonicalOperations,
        CanonicalOperationError,
    )

    def race_cancel(_self, run_id, **kwargs):
        if run_id == child_id:
            client.owner_research_runs[child_id]["status"] = "completed"
            raise CanonicalOperationError(
                "research_orchestrator",
                "terminal research run in status 'completed' cannot be canceled",
                status_code=409,
            )
        client.owner_research_runs[run_id]["status"] = "canceled"
        return client.owner_research_runs[run_id]

    monkeypatch.setattr(WorkshopCanonicalOperations, "cancel_research_run", race_cancel)
    response = client.post(
        f"/bff/agora/research-plans/{pid}/cancel",
        headers=_headers("race-conflict-cancel", _get_plan(client, pid)["meta"]["etag"]),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "cancelled"


def test_list_includes_owner_only_successor_and_authoritative_root_status(monkeypatch: pytest.MonkeyPatch) -> None:
    client, store, pid, root_id, child_id = _setup_two_stages(monkeypatch, "review-list")
    response = client.get(f"/bff/agora/research-plans/{pid}/runs", headers=_headers())
    assert response.status_code == 200, response.text
    rows = {r["run_id"]: r for r in response.json()["items"]}
    assert child_id in rows, (response.json(), list(client.owner_research_runs))
    assert rows[root_id]["execution_status"] == "succeeded"


def test_cancel_fences_successor_created_during_terminal_race(monkeypatch: pytest.MonkeyPatch) -> None:
    client, store, pid, root_id, child_id = _setup_two_stages(monkeypatch, "review-race")
    plan = store.get_plan(pid)
    stages = deepcopy(plan["stages"])
    stages.append({**deepcopy(stages[-1]), "stage_id": "stage-third", "dependencies": ["stage-second"]})
    store.update_plan(pid, {"stages": stages})
    third_id = "owner-run-3"

    def race_cancel(_self, run_id, **kwargs):
        record = client.owner_research_runs[run_id]
        if run_id == child_id:
            record["status"] = "completed"
            client.owner_research_runs[third_id] = {
                **deepcopy(record),
                "run_id": third_id,
                "stage_id": "stage-third",
                "status": "running",
                "execution_status": "running",
                "input_refs": [{"type": "research_plan", "id": pid}, {"type": "stage", "id": "stage-third"}],
            }
            from services.control_plane.bff.agora.strategy_workshop.operations import CanonicalOperationError
            raise CanonicalOperationError(
                "research_orchestrator",
                "terminal research run in status 'completed' cannot be canceled",
                status_code=409,
            )
        record["status"] = "canceled"
        return record

    from services.control_plane.bff.agora.strategy_workshop.operations import WorkshopCanonicalOperations
    monkeypatch.setattr(WorkshopCanonicalOperations, "cancel_research_run", race_cancel)
    response = client.post(
        f"/bff/agora/research-plans/{pid}/cancel",
        headers=_headers("review-race-cancel", _get_plan(client, pid)["meta"]["etag"]),
    )
    assert response.status_code == 200, response.text
    assert client.owner_research_runs[third_id]["status"] == "canceled", (
        response.json(),
        client.owner_research_runs[third_id]["status"],
        store.get_plan(pid)["status"],
    )


def test_cancel_must_not_acknowledge_success_with_new_running_successor(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    created = _create_plan(client, "ws-cancel-progress-race", "race-create")
    pid = created["data"]["plan_id"]
    store = getattr(client, "router", None) and getattr(client.router, "research_store", None) or getattr(client, "app_instance", None) and getattr(client.app_instance, "research_store", None)
    plan = store.get_plan(pid)
    stages = deepcopy(plan["stages"])
    stages.append({**deepcopy(stages[0]), "stage_id": "successor", "dependencies": [stages[0]["stage_id"]]})
    store.update_plan(pid, {"stages": stages})
    _approve_plan(client, pid, created["meta"]["etag"], "race-approve")
    rid = _dispatch_plan(client, pid, _get_plan(client, pid)["meta"]["etag"], "race-dispatch")
    client.owner_research_runs[rid]["status"] = "running"
    calls = []

    def concurrent_progress_then_cancel(_self, run_id, **kwargs):
        calls.append(run_id)
        if run_id == rid:
            root = client.owner_research_runs[rid]
            root["status"] = "completed"
            root["execution_status"] = "completed"
            client.owner_research_runs["owner-successor"] = {
                **deepcopy(root),
                "run_id": "owner-successor",
                "stage_id": "successor",
                "status": "running",
                "execution_status": "running",
                "input_refs": [{"type": "research_plan", "id": pid}, {"type": "stage", "id": "successor"}],
            }
            from services.control_plane.bff.agora.strategy_workshop.operations import CanonicalOperationError
            raise CanonicalOperationError(
                "research",
                "terminal research run in status 'completed' cannot be canceled",
                status_code=409,
            )
        run = client.owner_research_runs[run_id]
        run["status"] = "canceled"
        return run

    from services.control_plane.bff.agora.strategy_workshop.operations import WorkshopCanonicalOperations
    monkeypatch.setattr(WorkshopCanonicalOperations, "cancel_research_run", concurrent_progress_then_cancel)
    response = client.post(
        f"/bff/agora/research-plans/{pid}/cancel",
        headers=_headers("race-cancel", _get_plan(client, pid)["meta"]["etag"]),
    )
    active = [r["run_id"] for r in client.owner_research_runs.values() if r["status"] == "running"]
    assert not (response.status_code == 200 and active), {
        "http_status": response.status_code,
        "active_owner_runs": active,
        "owner_cancel_calls": calls,
        "stored_plan_status": store.get_plan(pid)["status"],
    }


def test_cancel_owner_disappears_after_initial_read(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.control_plane.bff.agora.strategy_workshop.operations import (
        WorkshopCanonicalOperations,
        CanonicalOperationError,
    )
    client = _client(monkeypatch)
    created = _create_plan(client, "ws-review-late-outage", "review-create")
    pid = created["data"]["plan_id"]
    _approve_plan(client, pid, created["meta"]["etag"], "review-approve")
    _dispatch_plan(client, pid, _get_plan(client, pid)["meta"]["etag"], "review-dispatch")
    etag = _get_plan(client, pid)["meta"]["etag"]
    calls = []

    def list_runs(_self, **kwargs):
        calls.append("list")
        if len(calls) > 1:
            raise CanonicalOperationError("research_orchestrator", "owner unavailable", status_code=503)
        return list(client.owner_research_runs.values())

    def cancel_task(_self, task_id, **kwargs):
        raise CanonicalOperationError("research_orchestrator", "owner unavailable", status_code=503)

    monkeypatch.setattr(WorkshopCanonicalOperations, "list_research_runs", list_runs)
    monkeypatch.setattr(WorkshopCanonicalOperations, "cancel_research_task", cancel_task)
    response = client.post(
        f"/bff/agora/research-plans/{pid}/cancel",
        headers=_headers("review-cancel", etag),
    )
    assert response.status_code == 503, "Unavailable cancellation/readback must not commit terminal local success"
    store = getattr(client, "router", None) and getattr(client.router, "research_store", None) or getattr(client, "app_instance", None) and getattr(client.app_instance, "research_store", None)
    assert store.get_plan(pid)["status"] != "cancelled", "Plan must not be cancelled locally when owner fails"


def test_bff_accepts_supported_synthesis_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    from services.control_plane.bff.agora.research.service import AgoraResearchService
    from services.control_plane.bff.agora.research.store import MemoryResearchPlanStore
    from services.control_plane.bff.agora.strategy_workshop.operations import WorkshopCanonicalOperations

    monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://research-owner.invalid")
    monkeypatch.setattr(WorkshopCanonicalOperations, "list_research_runs", lambda *a, **k: [])
    sent = Mock(return_value={"task": {"task_id": "t"}, "run": {"run_id": "r", "status": "queued"}})
    monkeypatch.setattr(WorkshopCanonicalOperations, "dispatch_research_run", sent)
    store = MemoryResearchPlanStore()
    store.create_plan({
        "plan_id": "p",
        "status": "draft",
        "tenant_id": "a",
        "user_id": "u",
        "lock_version": 1,
        "approval": {"state": "approved", "decided_by": "u"},
        "stages": [{
            "stage_id": "s",
            "stage_type": "evidence_synthesis",
            "status": "ready",
            "routing": {"backend_mode": "real"},
            "artifact_refs": [{"artifact_id": "input"}],
        }],
    })
    service = AgoraResearchService(store=store)
    service.approve_plan("p", scope=SimpleNamespace(tenant_id="a", user_id="u", roles=["operator"]))
    service.dispatch_plan("p", scope=SimpleNamespace(tenant_id="a", user_id="u", roles=["operator"]))
    assert sent.call_count == 1
