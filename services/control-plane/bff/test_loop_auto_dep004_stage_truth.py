from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


HEADERS = {"Authorization": "Bearer dep004-operator:operator"}
_TRACKED_ENV = (
    "PANTHEON_GOVERNANCE_DATA_DIR",
    "PANTHEON_RUNTIME_DATA_DIR",
    "PANTHEON_BFF_DEPLOYMENT_PLAN_STORE",
    "PANTHEON_BFF_DEPLOYMENT_SAGA_STORE",
    "PANTHEON_BFF_APPROVAL_DECISION_STORE",
    "PANTHEON_BFF_RUNTIME_BINDING_STORE",
    "PANTHEON_BFF_PAPER_RUNTIME_MONITORING_SESSION_STORE",
    "PANTHEON_BFF_TELEMETRY_SUMMARY_STORE",
    "PANTHEON_TELEMETRY_API_URL",
    "PANTHEON_PAPER_FLEET_RECONCILER_URL",
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@contextmanager
def _isolated_dep004_bff(
    *,
    include_monitoring: bool,
    include_second_plan: bool = False,
) -> Iterator[TestClient]:
    original_store = bff_main.read_store
    original_env = {key: os.environ.get(key) for key in _TRACKED_ENV}
    with tempfile.TemporaryDirectory(prefix="dep004_bff_") as td:
        root = Path(td)
        governance_dir = root / "governance"
        runtime_dir = root / "runtime"

        for key in _TRACKED_ENV:
            os.environ.pop(key, None)
        os.environ["PANTHEON_GOVERNANCE_DATA_DIR"] = str(governance_dir)
        os.environ["PANTHEON_RUNTIME_DATA_DIR"] = str(runtime_dir)

        deployment_plans = {
            "plan-dep004-001": {
                "plan_id": "plan-dep004-001",
                "approval_decision_id": "approval-dep004-001",
                "artifact_id": "artifact-dep004-001",
                "artifact_version": "v1.0.0",
                "strategy_id": "strategy-dep004",
                "capital_pool_id": "pool-dep004",
                "current_stage": "none",
                "target_stage": "paper",
                "transition_type": "activate",
                "runtime_action": "deploy_new_binding",
                "status": "approved",
                "created_at": "2026-06-27T01:00:00Z",
            }
        }
        if include_second_plan:
            deployment_plans["plan-dep004-002"] = {
                "plan_id": "plan-dep004-002",
                "approval_decision_id": "approval-dep004-002",
                "artifact_id": "artifact-dep004-002",
                "artifact_version": "v1.0.0",
                "strategy_id": "strategy-dep004",
                "capital_pool_id": "pool-dep004",
                "current_stage": "none",
                "target_stage": "paper",
                "transition_type": "activate",
                "runtime_action": "deploy_new_binding",
                "status": "approved",
                "created_at": "2026-06-27T01:02:00Z",
            }
        _write_json(governance_dir / "deployment_plans.json", deployment_plans)

        approval_decisions = {
            "approval-dep004-001": {
                "decision_id": "approval-dep004-001",
                "decision": "approved",
                "decision_state": "decided",
                "actor_id": "risk-committee",
                "risk_level": "medium",
                "decided_at": "2026-06-27T01:05:00Z",
            }
        }
        if include_second_plan:
            approval_decisions["approval-dep004-002"] = {
                "decision_id": "approval-dep004-002",
                "decision": "approved",
                "decision_state": "decided",
                "actor_id": "risk-committee",
                "risk_level": "medium",
                "decided_at": "2026-06-27T01:07:00Z",
            }
        _write_json(
            governance_dir / "approval_decisions.json",
            approval_decisions,
        )
        deployment_sagas = {
            "deployment-saga-dep004-001": {
                "saga_id": "deployment-saga-dep004-001",
                "plan_id": "plan-dep004-001",
                "approval_decision_id": "approval-dep004-001",
                "strategy_id": "strategy-dep004",
                "artifact_id": "artifact-dep004-001",
                "artifact_version": "v1.0.0",
                "capital_pool_id": "pool-dep004",
                "current_stage": "none",
                "target_stage": "paper",
                "runtime_action": "deploy_new_binding",
                "status": "awaiting_binding",
                "current_step": "binding_requested",
                "trace_id": "trace-dep004",
                "created_at": "2026-06-27T01:00:00Z",
                "updated_at": "2026-06-27T01:15:00Z",
                "last_sequence_no": 1,
                "history": [
                    {
                        "step": "binding_requested",
                        "status": "awaiting_binding",
                        "event_id": "deployment-saga-dep004-001-evt-0001",
                        "sequence_no": 1,
                        "emitted_at": "2026-06-27T01:00:00Z",
                    }
                ],
            }
        }
        if include_second_plan:
            deployment_sagas["deployment-saga-dep004-002"] = {
                "saga_id": "deployment-saga-dep004-002",
                "plan_id": "plan-dep004-002",
                "approval_decision_id": "approval-dep004-002",
                "strategy_id": "strategy-dep004",
                "artifact_id": "artifact-dep004-002",
                "artifact_version": "v1.0.0",
                "capital_pool_id": "pool-dep004",
                "current_stage": "none",
                "target_stage": "paper",
                "runtime_action": "deploy_new_binding",
                "status": "completed",
                "current_step": "completed",
                "trace_id": "trace-dep004-002",
                "created_at": "2026-06-27T01:02:00Z",
                "updated_at": "2026-06-27T01:12:00Z",
                "last_sequence_no": 2,
                "history": [
                    {
                        "step": "completed",
                        "status": "completed",
                        "event_id": "deployment-saga-dep004-002-evt-0002",
                        "sequence_no": 2,
                        "emitted_at": "2026-06-27T01:12:00Z",
                    }
                ],
            }
        _write_json(
            governance_dir / "deployment_sagas.json",
            {
                "sagas": deployment_sagas,
                "outbox": [
                    {
                        "owner_service": "deployment-orchestrator",
                        "event": {
                            "event_id": "deployment-saga-dep004-001-evt-0001",
                            "event_type": "runtime.binding.requested",
                            "aggregate_type": "deployment_saga",
                            "aggregate_id": "deployment-saga-dep004-001",
                            "sequence_no": 1,
                            "event_time": "2026-06-27T01:00:00Z",
                            "trace_id": "trace-dep004",
                            "payload": {"plan_id": "plan-dep004-001"},
                        },
                        "status": "dead_lettered",
                        "delivery_attempts": 3,
                        "last_error": "runtime-manager unavailable",
                        "blocked_reason": "runtime-manager unavailable",
                        "dlq_at": "2026-06-27T01:15:00Z",
                        "retry_policy": {
                            "consumer_name": "deployment-outbox-consumer",
                            "retryable": True,
                            "max_attempts": 3,
                            "retry_delay_seconds": 30,
                        },
                    }
                ],
                "inbox": [],
            },
        )
        _write_json(
            runtime_dir / "runtime_bindings.json",
            [
                {
                    "binding_id": "rb-dep004-001",
                    "runtime_binding_id": "rb-dep004-001",
                    "runtime_id": "runtime-dep004-001",
                    "capital_pool_id": "pool-dep004",
                    "artifact_id": "artifact-dep004-001",
                    "artifact_version": "v1.0.0",
                    "deployment_mode": "paper",
                    "effective_at": "2026-06-27T01:10:00Z",
                    "status": "active",
                    "plan_id": "plan-dep004-001",
                    "persona_capital_binding_id": "pcb-dep004-001",
                },
                *(
                    [
                        {
                            "binding_id": "rb-dep004-002",
                            "runtime_binding_id": "rb-dep004-002",
                            "runtime_id": "runtime-dep004-002",
                            "capital_pool_id": "pool-dep004",
                            "artifact_id": "artifact-dep004-002",
                            "artifact_version": "v1.0.0",
                            "deployment_mode": "paper",
                            "effective_at": "2026-06-27T01:12:00Z",
                            "status": "active",
                            "plan_id": "plan-dep004-002",
                            "persona_capital_binding_id": "pcb-dep004-002",
                        }
                    ]
                    if include_second_plan
                    else []
                ),
            ],
        )
        if include_monitoring:
            _write_json(
                runtime_dir / "paper_runtime_monitoring_sessions.json",
                [
                    {
                        "session_id": "prmon-dep004-001",
                        "binding_id": "rb-dep004-001",
                        "runtime_binding_id": "rb-dep004-001",
                        "runtime_id": "runtime-dep004-001",
                        "deployment_stage": "paper",
                        "status": "running",
                        "active": True,
                        "started_at": "2026-06-27T01:10:00Z",
                        "last_heartbeat_at": "2026-06-27T01:16:00Z",
                        "stale_after_seconds": 90,
                    }
                ],
            )

        bff_main.read_store = ReadSurfaceStore(
            str(root / "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def test_dep004_stage_truth_splits_blocked_saga_from_runtime_fleet() -> None:
    with _isolated_dep004_bff(include_monitoring=False) as client:
        response = client.get("/bff/deployments/plan-dep004-001", headers=HEADERS)
        list_response = client.get("/bff/deployments", headers=HEADERS)
        review_response = client.get(
            "/api/v1/operator/deployment-review/plan-dep004-001",
            headers=HEADERS,
        )

    assert response.status_code == 200, response.text
    assert list_response.status_code == 200, list_response.text
    assert review_response.status_code == 200, review_response.text

    payload = response.json()
    stage_truth = payload["data"]["stage_truth"]
    assert list(stage_truth) == ["approval", "plan", "saga", "binding", "runtime_fleet"]
    assert stage_truth["approval"]["status"] == "approved"
    assert stage_truth["plan"]["status"] == "approved"
    assert stage_truth["saga"]["status"] == "blocked"
    assert stage_truth["saga"]["failure"] is True
    assert stage_truth["saga"]["blocked_reason"] == "runtime-manager unavailable"
    assert stage_truth["binding"]["status"] == "active"
    assert stage_truth["runtime_fleet"]["status"] == "unavailable"
    assert stage_truth["runtime_fleet"]["failure"] is True
    assert "not inferred" in stage_truth["runtime_fleet"]["message"]

    surfaces = payload["meta"]["surfaces"]
    assert surfaces["approval_stage"]["source"] == "canonical"
    assert surfaces["plan_stage"]["source"] == "canonical"
    assert surfaces["saga_stage"]["status"] == "degraded"
    assert surfaces["binding_stage"]["source"] == "canonical"
    assert surfaces["runtime_fleet_stage"]["status"] == "unavailable"

    assert list_response.json()["items"][0]["stage_truth"]["saga"]["status"] == "blocked"
    assert (
        review_response.json()["data"]["stage_truth"]["runtime_fleet"]["status"]
        == "unavailable"
    )


def test_dep004_runtime_fleet_requires_runtime_evidence_to_be_active() -> None:
    with _isolated_dep004_bff(include_monitoring=True) as client:
        response = client.get("/bff/deployments/plan-dep004-001", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    runtime_fleet = payload["data"]["stage_truth"]["runtime_fleet"]
    assert runtime_fleet["status"] == "active"
    assert runtime_fleet["source_dataset"] == "paper_runtime_monitoring_sessions"
    assert runtime_fleet["source_id"] == "prmon-dep004-001"
    assert runtime_fleet["failure"] is False
    assert runtime_fleet["last_heartbeat_at"] == "2026-06-27T01:16:00Z"
    assert payload["meta"]["surfaces"]["runtime_fleet_stage"]["source"] == "canonical"


def test_dep004_deployment_list_stage_surfaces_aggregate_page_failures() -> None:
    with _isolated_dep004_bff(
        include_monitoring=True,
        include_second_plan=True,
    ) as client:
        response = client.get("/bff/deployments", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [item["plan_id"] for item in payload["items"]] == [
        "plan-dep004-001",
        "plan-dep004-002",
    ]
    assert payload["items"][0]["stage_truth"]["runtime_fleet"]["status"] == "active"
    assert payload["items"][1]["stage_truth"]["runtime_fleet"]["status"] == "unavailable"

    surfaces = payload["meta"]["surfaces"]
    assert surfaces["runtime_fleet_stage"]["status"] == "degraded"
    assert surfaces["deployment_stage_truth"]["status"] == "degraded"
