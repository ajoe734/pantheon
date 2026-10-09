"""Tests for autonomous and bounded ingestion scheduler, schedule admission, and operator stop preservation."""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _read_headers(tenant: str = "tenant-dev") -> dict[str, str]:
    from services.runtime_auth_inbound import encode_jwt_hs256

    token = encode_jwt_hs256(
        {"sub": "test-scheduler", "roles": ["operator"], "tenant_id": tenant, "exp": int(__import__("time").time()) + 600},
        secret=os.environ["PANTHEON_RUNTIME_JWT_SECRET"],
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def client():
    tempdir = tempfile.mkdtemp(prefix="source_ingest_sched_test_")
    env_backup = {
        "SOURCE_INGEST_DATA_DIR": os.environ.get("SOURCE_INGEST_DATA_DIR"),
        "SOURCE_INGEST_MAX_RECORDS": os.environ.get("SOURCE_INGEST_MAX_RECORDS"),
        "SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY": os.environ.get("SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY"),
        "SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS": os.environ.get("SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS"),
        "SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS": os.environ.get("SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS"),
        "PANTHEON_RUNTIME_JWT_SECRET": os.environ.get("PANTHEON_RUNTIME_JWT_SECRET"),
    }
    os.environ["SOURCE_INGEST_DATA_DIR"] = tempdir
    os.environ["SOURCE_INGEST_MAX_RECORDS"] = "20"
    os.environ["SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY"] = "1"
    os.environ["SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS"] = "2"
    os.environ["SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS"] = "300"
    os.environ["PANTHEON_RUNTIME_JWT_SECRET"] = "source-test-secret"

    sys.modules.pop("services.source_ingestion.main", None)
    module = importlib.import_module("services.source_ingestion.main")
    module = importlib.reload(module)

    from services.source_ingestion.controller_state import ControllerState, ControllerStateStore

    state = ControllerState(
        controller_id="ctrl-test-scheduler",
        controller_name="test-controller",
        environment="test",
        tenant_id="tenant-dev",
        deployment={},
    )
    ControllerStateStore(module.runtime.CONTROLLER_STATE_PATH).save(state)

    try:
        yield TestClient(module.app, headers=_read_headers()), Path(tempdir), module
    finally:
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_run_scheduled_exclusive_fails_closed_when_schedule_disabled(client) -> None:
    test_client, _, module = client
    conn_id = "conn-exclusive-disabled"
    headers = {"Authorization": f"Bearer {module.controller_token}"}

    # Configure connector
    configured = test_client.post(
        "/api/source-ingest/connectors",
        headers=headers,
        json={
            "connector": {
                "connector_id": conn_id,
                "source_type": "market",
                "provider": "Test provider",
                "license_scope": "internal",
                "metadata": {
                    "persona_source_reconciliation": {
                        "managed_by": "persona_source_provisioning_reconciler",
                    },
                },
            },
            "fetch": {"mode": "static_records", "records": []},
        },
    )
    assert configured.status_code == 201, configured.text

    # Set schedule to disabled
    sched_resp = test_client.put(
        f"/api/source-ingest/connectors/{conn_id}/schedule",
        headers=headers,
        json={"interval_seconds": 86400, "enabled": False},
    )
    assert sched_resp.status_code == 200, sched_resp.text
    assert sched_resp.json()["schedule"]["enabled"] is False

    # Exclusive trigger must fail closed reporting that the schedule is disabled
    run_resp = test_client.post(
        "/api/source-ingest/run-scheduled",
        headers=headers,
        json={"force_connector_ids": [conn_id], "exclusive_connector_ids": [conn_id]},
    )
    assert run_resp.status_code == 200, run_resp.text
    body = run_resp.json()
    assert body["summary"]["total_ran"] == 0
    assert body["summary"]["total_failed"] == 1
    assert len(body["failed"]) == 1
    assert body["failed"][0]["connector_id"] == conn_id
    assert "exclusively selected connector schedule is disabled" in body["failed"][0]["error"]


def test_operator_stop_preservation_blocks_schedule_enable(client) -> None:
    """Acceptance 1: explicit operator stops are preserved and cannot be overridden by schedule enable."""
    test_client, _, _ = client
    conn_id = "conn-operator-stopped"

    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": {
                "connector_id": conn_id,
                "source_type": "market",
                "provider": "Test provider",
                "license_scope": "internal",
            },
            "fetch": {"mode": "static_records", "records": []},
        },
    )
    assert configured.status_code == 201

    # Initially configure schedule as enabled
    init_sched = test_client.put(
        f"/api/source-ingest/connectors/{conn_id}/schedule",
        json={"interval_seconds": 60, "enabled": True},
    )
    assert init_sched.status_code == 200

    # Operator sets status to disabled via lifecycle
    lifecycle_resp = test_client.put(
        f"/api/source-ingest/connectors/{conn_id}/lifecycle",
        json={
            "status": "disabled",
            "reason": "operator emergency stop for risk review",
            "actor_id": "operator-gate",
        },
    )
    assert lifecycle_resp.status_code == 200
    assert lifecycle_resp.json()["connector"]["status"] == "disabled"

    # Attempting to enable schedule on operator-stopped connector fails closed with 400
    sched_put = test_client.put(
        f"/api/source-ingest/connectors/{conn_id}/schedule",
        json={"interval_seconds": 60, "enabled": True},
    )
    assert sched_put.status_code == 400
    assert "explicit operator stop is active" in sched_put.json()["detail"]

    # Triggering run-scheduled reports disabled by explicit operator stop
    run_resp = test_client.post(
        "/api/source-ingest/run-scheduled",
        json={"exclusive_connector_ids": [conn_id]},
    )
    assert run_resp.status_code == 200
    failed = run_resp.json()["failed"]
    assert len(failed) == 1
    assert "disabled by explicit operator stop" in failed[0]["error"]
