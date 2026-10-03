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

    monkeypatch.setattr(WorkshopCanonicalOperations, "dispatch_research_run", dispatch)
    monkeypatch.setattr(WorkshopCanonicalOperations, "cancel_research_run", cancel_run)
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


def test_cancelled_and_historical_plans_remain_terminal_without_owner_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    active_plan = _create_plan(client, "ws-active-cancel", "active-cancel-create")
    active_id = active_plan["data"]["plan_id"]
    _approve_plan(client, active_id, active_plan["meta"]["etag"], "active-cancel-approve")
    active_approved = _get_plan(client, active_id)
    _dispatch_plan(client, active_id, active_approved["meta"]["etag"], "active-cancel-dispatch")
    active_readback = _get_plan(client, active_id)
    active_cancel = client.post(
        f"/bff/agora/research-plans/{active_id}/cancel",
        headers=_headers("active-cancel", active_readback["meta"]["etag"]),
    )
    assert active_cancel.status_code == 200, active_cancel.text
    assert _get_plan(client, active_id)["data"]["status"] == "cancelled"
    active_redispatch = client.post(
        f"/bff/agora/research-plans/{active_id}/runs",
        headers=_headers("active-redispatch", _get_plan(client, active_id)["meta"]["etag"]),
    )
    assert active_redispatch.status_code == 409

    history_plan = _create_plan(client, "ws-history-plan", "history-create")
    history_id = history_plan["data"]["plan_id"]
    _approve_plan(client, history_id, history_plan["meta"]["etag"], "history-approve")
    store = client.router.research_store
    stored_plan = store.get_plan(history_id)
    store.create_run({
        "run_id": "legacy-completed-run", "plan_id": history_id,
        "stage_id": "stage-prototype-backtest", "stage_type": "prototype_backtest",
        "execution_status": "succeeded", "outcome": "pass",
        "tenant_id": stored_plan["tenant_id"], "user_id": stored_plan["user_id"],
        "created_at": "2026-09-01T00:00:00Z",
        "artifact_refs": [], "evidence_refs": [],
    })
    projected = _get_plan(client, history_id)
    assert projected["data"]["status"] == "completed"
    assert projected["data"]["run_ids"] == ["legacy-completed-run"]
    listed = client.get(
        f"/bff/agora/research-plans/{history_id}/runs", headers=_headers()
    )
    assert listed.status_code == 200
    assert [run["run_id"] for run in listed.json()["items"]] == ["legacy-completed-run"]
    redispatch_history = client.post(
        f"/bff/agora/research-plans/{history_id}/runs",
        headers=_headers("history-redispatch", projected["meta"]["etag"]),
    )
    assert redispatch_history.status_code == 202
    assert redispatch_history.json()["data"]["run_id"] == "legacy-completed-run"

    mixed_res = client.post(
        "/bff/agora/workshops/ws-mixed-plan/research-plans",
        headers=_headers("mixed-create"),
        json={
            "spec_version": "1.0",
            "strategy_id": "strat-mixed",
            "strategy_spec_registry_id": "reg-mixed",
            "stages": [
                {
                    "stage_id": "stage-1",
                    "stage_type": "prototype_backtest",
                    "status": "ready",
                    "routing": {"backend_mode": "fixture", "fallback_policy": "explicit_fixture_only"},
                },
                {
                    "stage_id": "stage-2",
                    "stage_type": "prototype_backtest",
                    "status": "pending",
                    "dependencies": ["stage-1"],
                    "routing": {"backend_mode": "fixture", "fallback_policy": "explicit_fixture_only"},
                },
            ],
        },
    )
    assert mixed_res.status_code == 201
    mixed_id = mixed_res.json()["data"]["plan_id"]
    _approve_plan(client, mixed_id, mixed_res.json()["meta"]["etag"], "mixed-approve")
    stored_mixed = store.get_plan(mixed_id)
    store.create_run({
        "run_id": "legacy-s1-run",
        "plan_id": mixed_id,
        "stage_id": "stage-1",
        "stage_type": "prototype_backtest",
        "execution_status": "succeeded",
        "outcome": "pass",
        "tenant_id": stored_mixed["tenant_id"],
        "user_id": stored_mixed["user_id"],
        "created_at": "2026-09-01T00:00:00Z",
        "artifact_refs": [],
        "evidence_refs": [],
    })
    mixed_mid = _get_plan(client, mixed_id)
    assert mixed_mid["data"]["status"] == "running"
    assert mixed_mid["data"]["run_ids"] == ["legacy-s1-run"]

    mixed_s2 = client.post(
        f"/bff/agora/research-plans/{mixed_id}/runs",
        headers=_headers("mixed-s2-dispatch", mixed_mid["meta"]["etag"]),
    )
    assert mixed_s2.status_code == 202
    owner_s2_run_id = mixed_s2.json()["data"]["run_id"]
    client.owner_research_runs[owner_s2_run_id]["status"] = "completed"

    mixed_final = _get_plan(client, mixed_id)
    assert mixed_final["data"]["status"] == "completed"
    assert "legacy-s1-run" in mixed_final["data"]["run_ids"]
    assert owner_s2_run_id in mixed_final["data"]["run_ids"]

    mixed_listed = client.get(f"/bff/agora/research-plans/{mixed_id}/runs", headers=_headers())
    assert mixed_listed.status_code == 200
    listed_ids = [r["run_id"] for r in mixed_listed.json()["items"]]
    assert "legacy-s1-run" in listed_ids
    assert owner_s2_run_id in listed_ids


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
    client.owner_research_runs[run_id].update({
        "status": "completed", "correlation_id": correlation_id,
        "executor": executor, "provenance": "real",
    })

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

    # 8. Non-terminal owner run with matching receipt -> unavailable
    store.update_run(run_id, {"execution_status": "running"})
    client.owner_research_runs[run_id]["status"] = "running"
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

    # 9. Authentic valid receipt on terminal owner run -> resolves to 'real'
    store.update_run(run_id, {"execution_status": "succeeded", "provenance": "real"})
    client.owner_research_runs[run_id]["status"] = "completed"
    res9 = client.get(f"/bff/agora/research-runs/{run_id}", headers=_headers())
    assert res9.status_code == 200
    assert res9.json()["provenance"] == "real"

    # 10. Mismatched owner provenance vs receipt mode (run claims simulation, receipt claims real) -> unavailable
    store.update_run(run_id, {"provenance": "simulation"})
    client.owner_research_runs[run_id]["provenance"] = "simulation"
    res10 = client.get(f"/bff/agora/research-runs/{run_id}", headers=_headers())
    assert res10.status_code == 200
    assert res10.json()["provenance"] == "unavailable"


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
    store.create_run({
        "run_id": rid2,
        "plan_id": pid,
        "stage_id": "stage-prototype-backtest",
        "stage_type": "prototype_backtest",
        "execution_status": "running",
        "tenant_id": "pantheon-dev",
        "user_id": "agora-test-user",
        "created_at": "2026-10-03T00:00:00Z",
    })

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
    run1_local = store.get_run(rid1)
    assert run1_local["execution_status"] == "cancelled"
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
