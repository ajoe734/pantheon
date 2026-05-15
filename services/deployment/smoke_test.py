#!/usr/bin/env python3
"""
Deployment Service HTTP smoke test.

Runs against a live server. Set DEPLOYMENT_URL to override the default.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx

BASE = os.getenv("DEPLOYMENT_URL", "").strip()
TIMEOUT = 10
REPO_ROOT = Path(__file__).resolve().parents[2]


def _payload(plan_suffix: str) -> dict:
    return {
        "plan_id": f"plan-smoke-{plan_suffix}",
        "approval_decision_id": "approval-001",
        "registry_entry": {
            "registry_id": f"reg-strat-smoke-{plan_suffix}",
            "artifact_type": "model_artifact",
            "strategy_id": "strat-smoke",
            "version": "1.2.0",
            "artifact_state": "approved",
            "checksum": "sha256:smoke",
            "approved_at": "2026-04-15T10:00:00Z",
            "lineage": {"source_run_ids": ["smoke-run-001"]},
            "deployment_summary": {"current_stage": "none"},
        },
        "approval_decision": {
            "decision_id": "approval-001",
            "target_id": f"reg-strat-smoke-{plan_suffix}",
            "target_version": "1.2.0",
            "decision_state": "decided",
            "decision": "approved",
            "capital_pool_id": "pool-001",
            "persona_id": "persona-ops",
        },
        "capital_pool_id": "pool-001",
        "sponsor_persona_id": "persona-ops",
        "target_stage": "paper",
        "rollback": {
            "target_artifact_id": "reg-strat-smoke-prev",
            "target_version": "1.1.0",
            "action_type": "replace",
        },
    }


def _build_client():
    if BASE:
        return httpx.Client(base_url=BASE, timeout=TIMEOUT)
    try:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from fastapi.testclient import TestClient
        from services.deployment.service import app
    except ImportError:
        return httpx.Client(base_url="http://localhost:8006", timeout=TIMEOUT)
    return TestClient(app)


def run() -> None:
    client = _build_client()
    suffix = uuid.uuid4().hex[:6]
    payload = _payload(suffix)
    try:
        response = client.get("/health")
        assert response.status_code == 200, response.text
        print("✓ /health")

        response = client.post("/api/deployment/plans/validate", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["ok"] is True
        print("✓ POST /api/deployment/plans/validate")

        response = client.post("/api/deployment/plans", json=payload)
        assert response.status_code == 201, response.text
        plan_id = response.json()["plan_id"]
        print(f"✓ POST /api/deployment/plans → {plan_id}")

        response = client.get(f"/api/deployment/plans/{plan_id}")
        assert response.status_code == 200, response.text
        assert response.json()["target_stage"] == "paper"
        print(f"✓ GET /api/deployment/plans/{plan_id}")

        response = client.post(
            f"/api/deployment/plans/{plan_id}/dispatch",
            json={
                "trace_id": f"trace-{suffix}",
                "workflow_id": "pantheon.deploy",
                "registry_entry": payload["registry_entry"],
            },
        )
        assert response.status_code == 200, response.text
        dispatch = response.json()
        saga_id = dispatch["deployment_saga"]["saga"]["saga_id"]
        seq1_event_id = dispatch["deployment_saga"]["outbox_event"]["event"]["event_id"]
        assert dispatch["consistency_contract"] == "DEP-002"
        assert dispatch["deployment_saga"]["outbox_event"]["event"]["sequence_no"] == 1
        print(f"✓ POST /api/deployment/plans/{plan_id}/dispatch → {saga_id}")

        response = client.post(
            f"/api/deployment/sagas/{saga_id}/binding-created",
            json={"binding_id": f"binding-{suffix}", "runtime_id": f"runtime-{suffix}"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["event"]["sequence_no"] == 2

        response = client.post(
            f"/api/deployment/sagas/{saga_id}/runtime-active",
            json={"binding_id": f"binding-{suffix}", "runtime_id": f"runtime-{suffix}"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["event"]["sequence_no"] == 3
        print(f"✓ POST /api/deployment/sagas/{saga_id}/binding-created + /runtime-active")

        response = client.post(
            f"/api/deployment/outbox/{seq1_event_id}/consume",
            json={"consumer_name": "runtime-projector"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "applied"

        response = client.post(
            f"/api/deployment/outbox/{seq1_event_id}/consume",
            json={"consumer_name": "runtime-projector"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "duplicate"
        print(f"✓ POST /api/deployment/outbox/{seq1_event_id}/consume replay dedupe")

        response = client.post(f"/api/deployment/plans/{plan_id}/status", json={"status": "executing"})
        assert response.status_code == 200, response.text
        response = client.post(f"/api/deployment/plans/{plan_id}/status", json={"status": "executed"})
        assert response.status_code == 200, response.text
        print(f"✓ POST /api/deployment/plans/{plan_id}/status")

        response = client.get("/api/deployment/strategies/strat-smoke/read-model")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["current_stage"] == "paper"
        assert body["latest_plan_id"] == plan_id
        print("✓ GET /api/deployment/strategies/{strategy_id}/read-model")

        print("\n✓ All smoke tests passed")
    finally:
        client.close()


if __name__ == "__main__":
    try:
        run()
    except AssertionError as exc:
        print(f"\n✗ Smoke test failed: {exc}", file=sys.stderr)
        sys.exit(1)
