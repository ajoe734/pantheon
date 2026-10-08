"""Tests for autonomous and bounded ingestion scheduler, schedule admission, and operator stop preservation."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.source_ingestion.market_snapshot import (
    LatestMarketSnapshot,
    LatestMarketSnapshotStore,
    MarketSnapshotPoint,
)


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


def _simulation_connector(connector_id: str = "dev-paper-us-equity-simulation", market: str = "US"):
    return {
        "connector_id": connector_id,
        "source_type": "market",
        "provider": "Explicit controlled simulation",
        "license_scope": "internal",
        "metadata": {
            "dev_only": True,
            "is_real": False,
            "market": market,
            "provenance": "simulation",
            "symbols": ["SPY"],
            "persona_source_reconciliation": {
                "managed_by": "persona_source_provisioning_reconciler",
            },
        },
    }


def test_run_scheduled_exclusive_fails_closed_when_schedule_disabled(client) -> None:
    test_client, _, module = client
    conn_id = "dev-paper-us-equity-simulation"
    headers = {"Authorization": f"Bearer {module.controller_token}"}

    # Configure connector
    configured = test_client.post(
        "/api/source-ingest/connectors",
        headers=headers,
        json={
            "connector": _simulation_connector(connector_id=conn_id),
            "fetch": {
                "mode": "provider_owned_adapter",
                "adapter": "DevPaperUsEquitySimulationAdapter.records_from_now",
                "adapter_config": {"symbols": ["SPY"]},
                "request": {"symbols": ["SPY"]},
            },
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


def test_bounded_temporary_refresh_and_restoration_with_marketless_snapshot(client) -> None:
    """Acceptance 2 & 4 & 5: prove stored marketless SPY snapshot becomes legitimately market-bearing

    upon bounded temporary schedule admission, and that schedule is restored to disabled on completion.
    """
    test_client, data_dir, module = client
    conn_id = "dev-paper-us-equity-simulation"

    # Pre-seed a marketless snapshot for SPY matching production dev VM (mss-008c3b5fa7f0563691f3be83)
    snapshot_path = data_dir / "latest_market_snapshots.jsonl"
    store = LatestMarketSnapshotStore(snapshot_path)
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    points = [
        MarketSnapshotPoint(
            event_time=f"2026-10-0{i}T00:00:00Z",
            close=515.0 + i,
            source_id=f"src-legacy-{i}",
            connector_id="conn-dev-product-legacy",
            content_ref=f"simulation://legacy/SPY/2026-10-0{i}",
            ingest_run_id=f"run-legacy-{i}",
            market=None,
        )
        for i in range(1, 7)
    ]
    initial_snapshot = LatestMarketSnapshot(
        symbol="SPY",
        points=points,
        observed_at=now_iso,
    )
    assert initial_snapshot.market is None
    snapshot_state = initial_snapshot.to_dict()
    state_json = json.dumps(snapshot_state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    envelope = {
        "state": snapshot_state,
        "checksum_algorithm": "sha256",
        "checksum": hashlib.sha256(state_json.encode("utf-8")).hexdigest(),
    }
    with snapshot_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    module.latest_market_snapshot_store.reload()

    # Verify initial snapshot readback has no market
    snap_readback = test_client.get("/api/source-ingest/snapshots/latest?symbol=SPY")
    assert snap_readback.status_code == 200, snap_readback.text
    assert snap_readback.json().get("market") is None

    headers = {"Authorization": f"Bearer {module.controller_token}"}

    # Register dev-paper-us-equity-simulation connector with market='US' in metadata
    configured = test_client.post(
        "/api/source-ingest/connectors",
        headers=headers,
        json={
            "connector": _simulation_connector(connector_id=conn_id, market="US"),
            "fetch": {
                "mode": "provider_owned_adapter",
                "adapter": "DevPaperUsEquitySimulationAdapter.records_from_now",
                "adapter_config": {"symbols": ["SPY"]},
                "request": {"symbols": ["SPY"]},
            },
        },
    )
    assert configured.status_code == 201, configured.text

    # Prior state: schedule is disabled
    sched_resp = test_client.put(
        f"/api/source-ingest/connectors/{conn_id}/schedule",
        headers=headers,
        json={"interval_seconds": 86400, "enabled": False},
    )
    assert sched_resp.status_code == 200, sched_resp.text
    assert sched_resp.json()["schedule"]["enabled"] is False

    # Exclusive trigger without admission fails closed
    fail_resp = test_client.post(
        "/api/source-ingest/run-scheduled",
        headers=headers,
        json={"force_connector_ids": [conn_id], "exclusive_connector_ids": [conn_id]},
    )
    assert fail_resp.json()["summary"]["total_ran"] == 0
    assert "schedule is disabled" in fail_resp.json()["failed"][0]["error"]

    # 1. Staged lawful admission: temporarily enable schedule
    adm_resp = test_client.put(
        f"/api/source-ingest/connectors/{conn_id}/schedule",
        headers=headers,
        json={"interval_seconds": 86400, "enabled": True},
    )
    assert adm_resp.status_code == 200
    assert adm_resp.json()["schedule"]["enabled"] is True

    # 2. Trigger run-scheduled under temporary admission
    refresh_resp = test_client.post(
        "/api/source-ingest/run-scheduled",
        headers=headers,
        json={"force_connector_ids": [conn_id], "exclusive_connector_ids": [conn_id]},
    )
    assert refresh_resp.status_code == 200, refresh_resp.text
    ref_body = refresh_resp.json()
    assert ref_body["summary"]["total_ran"] == 1
    assert ref_body["summary"]["total_failed"] == 0

    # 3. Restoration: restore prior disabled schedule state
    restore_resp = test_client.put(
        f"/api/source-ingest/connectors/{conn_id}/schedule",
        headers=headers,
        json={"interval_seconds": 86400, "enabled": False},
    )
    assert restore_resp.status_code == 200
    assert restore_resp.json()["schedule"]["enabled"] is False

    # 4. Verify snapshot is now legitimately market-bearing ('US') without lexical guess
    fresh_snap = test_client.get("/api/source-ingest/snapshots/latest?symbol=SPY")
    assert fresh_snap.status_code == 200, fresh_snap.text
    fresh_data = fresh_snap.json()
    assert fresh_data["market"] == "US"
    assert fresh_data["symbol"] == "SPY"
    assert len(fresh_data["closes"]) >= 2

    # 5. Verify subsequent exclusive run-scheduled refuses again due to restored disabled state
    after_resp = test_client.post(
        "/api/source-ingest/run-scheduled",
        headers=headers,
        json={"force_connector_ids": [conn_id], "exclusive_connector_ids": [conn_id]},
    )
    assert after_resp.json()["summary"]["total_ran"] == 0
    assert "schedule is disabled" in after_resp.json()["failed"][0]["error"]


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
