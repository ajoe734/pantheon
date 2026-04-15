#!/usr/bin/env python3
"""
Deployment Service HTTP smoke test.

Runs against a live server. Set DEPLOYMENT_URL to override the default.
"""
from __future__ import annotations

import os
import sys
import uuid

import httpx

BASE = os.getenv("DEPLOYMENT_URL", "http://localhost:8006")
TIMEOUT = 10


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


def run() -> None:
    client = httpx.Client(base_url=BASE, timeout=TIMEOUT)
    suffix = uuid.uuid4().hex[:6]
    payload = _payload(suffix)

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


if __name__ == "__main__":
    try:
        run()
    except AssertionError as exc:
        print(f"\n✗ Smoke test failed: {exc}", file=sys.stderr)
        sys.exit(1)
