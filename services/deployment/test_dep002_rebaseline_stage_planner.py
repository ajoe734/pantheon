"""
DEP-002-RB rebaseline coverage for the deployable stage planner check API.

The endpoint intentionally checks paper / canary / live / frozen planner rules
without requiring a registry entry or ApprovalDecision payload.
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def dep002_client(tmp_path):
    governance_dir = tmp_path / "governance"
    governance_dir.mkdir(parents=True, exist_ok=True)
    runtime_binding_store = tmp_path / "runtime_bindings.json"

    env_backup = {
        "CAPITAL_DATA_DIR": os.environ.get("CAPITAL_DATA_DIR"),
        "DEPLOYMENT_DATA_DIR": os.environ.get("DEPLOYMENT_DATA_DIR"),
        "PANTHEON_GOVERNANCE_DATA_DIR": os.environ.get("PANTHEON_GOVERNANCE_DATA_DIR"),
        "PANTHEON_RUNTIME_BINDING_STORE_PATH": os.environ.get("PANTHEON_RUNTIME_BINDING_STORE_PATH"),
        "PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH": os.environ.get(
            "PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH"
        ),
    }
    os.environ["CAPITAL_DATA_DIR"] = str(governance_dir)
    os.environ["DEPLOYMENT_DATA_DIR"] = str(governance_dir)
    os.environ["PANTHEON_GOVERNANCE_DATA_DIR"] = str(governance_dir)
    os.environ["PANTHEON_RUNTIME_BINDING_STORE_PATH"] = str(runtime_binding_store)
    os.environ.pop("PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH", None)

    sys.modules.pop("services.deployment.service", None)
    module = importlib.import_module("services.deployment.service")
    module = importlib.reload(module)

    try:
        yield TestClient(module.app)
    finally:
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.mark.parametrize(
    (
        "current_stage",
        "target_stage",
        "rollback_action",
        "transition_type",
        "runtime_action",
        "rollback_required",
        "scale",
    ),
    [
        ("none", "paper", "replace", "activate", "deploy_new_binding", True, (0.0, 100.0)),
        ("paper", "canary", "replace", "promote", "replace_binding", True, (5.0, 25.0)),
        ("canary", "live", "replace", "promote", "replace_binding", True, (100.0, 100.0)),
        ("live", "frozen", None, "freeze", "freeze_binding", False, (0.0, 0.0)),
        ("frozen", "canary", "replace", "resume", "resume_binding", True, (5.0, 25.0)),
        ("live", "paper", "pause_then_replace", "rollback", "pause_then_replace", True, (0.0, 100.0)),
    ],
)
def test_stage_planner_check_reports_allowed_transition_policy(
    dep002_client,
    current_stage: str,
    target_stage: str,
    rollback_action: str | None,
    transition_type: str,
    runtime_action: str,
    rollback_required: bool,
    scale: tuple[float, float],
):
    payload = {
        "current_stage": current_stage,
        "target_stage": target_stage,
    }
    if rollback_action is not None:
        payload["rollback_action"] = rollback_action

    response = dep002_client.post("/api/deployment/stage-planner/check", json=payload)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["ruleset"] == "DEP-002-RB-stage-planner-v1"
    assert body["current_stage"] == current_stage
    assert body["target_stage"] == target_stage
    assert body["transition_type"] == transition_type
    assert body["runtime_action"] == runtime_action
    assert body["rollback_required"] is rollback_required
    assert body["default_scale"]["capital_scale_pct"] == scale[0]
    assert body["default_scale"]["gross_scale_pct"] == scale[1]
    assert body["effective_scale"] == body["default_scale"]
    assert body["errors"] == []


def test_stage_planner_check_rejects_skipped_promotion(dep002_client):
    response = dep002_client.post(
        "/api/deployment/stage-planner/check",
        json={
            "current_stage": "paper",
            "target_stage": "live",
            "rollback_action": "replace",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["transition_type"] is None
    assert any("Forbidden transition: paper -> live" in error for error in body["errors"])


def test_stage_planner_check_rejects_missing_active_stage_rollback(dep002_client):
    response = dep002_client.post(
        "/api/deployment/stage-planner/check",
        json={
            "current_stage": "none",
            "target_stage": "paper",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["transition_type"] == "activate"
    assert body["runtime_action"] == "deploy_new_binding"
    assert body["rollback_required"] is True
    assert any("rollback is required for target_stage 'paper'" in error for error in body["errors"])


def test_stage_planner_check_rejects_canary_scale_above_policy(dep002_client):
    response = dep002_client.post(
        "/api/deployment/stage-planner/check",
        json={
            "current_stage": "paper",
            "target_stage": "canary",
            "rollback_action": "replace",
            "scale": {
                "capital_scale_pct": 10.0,
                "gross_scale_pct": 30.0,
                "ramp_schedule": ["manual-review"],
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["transition_type"] == "promote"
    assert body["runtime_action"] == "replace_binding"
    assert body["default_scale"]["capital_scale_pct"] == 5.0
    assert body["effective_scale"]["capital_scale_pct"] == 10.0
    assert any("canary target_stage requires 0 < capital_scale_pct <= 5" in error for error in body["errors"])
    assert any("canary target_stage requires 0 < gross_scale_pct <= 25" in error for error in body["errors"])
