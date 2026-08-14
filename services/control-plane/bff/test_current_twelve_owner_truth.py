from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import pytest


BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
import loop_inventory as loop_inventory_model  # noqa: E402
from downstream_health_monitor import _probe_http  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402


REPO_ROOT = BFF_DIR.parents[2]
TENANT_ID = "tenant-twelve-truth"
ENVIRONMENT = "dev"
HEADERS = {
    "Authorization": (
        f"Bearer loop-health-operator:operator,reviewer,admin:{TENANT_ID}"
    )
}

EXPECTED_CANONICAL_OWNERS: dict[str, str] = {
    "source_ingestion": "source-ingestion-controller",
    "strategy_distillation": "strategy-distillation-controller",
    "alpha_replication": "alpha-replication-controller",
    "persona_teaching": "persona-teaching-controller",
    "agora_interaction_evidence": "agora-interaction-controller",
    "human_imitation_shadow_evaluation": "policy-learning-controller",
    "consultation": "consultation-workflow-controller",
    "promotion_deployment": "deployment-saga-controller",
    "capital_pool_execution": "paper-fleet-reconciler",
    "telemetry_reconciliation": "telemetry-reconciliation-controller",
    "evolution": "evolution-decision-controller",
    "bff_health_monitoring": "bff-health-monitor",
}


def _loop_conformance_module():
    """Import the shared controller-record conformance contract."""
    path = REPO_ROOT / "services" / "loop-control" / "conformance.py"
    spec = importlib.util.spec_from_file_location("l12_health_conformance", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def _scoped_health_client(
    *,
    loop_health_store: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        snapshot_path = root / "read_surfaces.json"
        env_overrides: Dict[str, str] = {
            "PANTHEON_BFF_AUTH_STUB": "true",
            "PANTHEON_ENV": ENVIRONMENT,
        }
        if loop_health_store is not None:
            health_path = root / "loop_health.json"
            scoped_records = {}
            for k, v in loop_health_store.items():
                record = dict(v)
                record.setdefault("tenant_id", TENANT_ID)
                record.setdefault("environment", ENVIRONMENT)
                scoped_records[k] = record
            health_path.write_text(
                json.dumps(scoped_records),
                encoding="utf-8",
            )
            env_overrides["PANTHEON_BFF_LOOP_HEALTH_STORE"] = str(health_path)

        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            str(snapshot_path),
            allow_local_snapshot_fallback=False,
        )
        with patch.dict(os.environ, env_overrides, clear=False):
            try:
                yield TestClient(bff_main.app, raise_server_exceptions=False)
            finally:
                bff_main.read_store = original_store


def _build_valid_controller_row(
    loop_id: str,
    *,
    now: Optional[datetime] = None,
    controller_name: Optional[str] = None,
    heartbeat_at: Optional[datetime] = None,
    status: str = "healthy",
    evidence_refs: Optional[List[str]] = None,
    worker_health: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    timestamp = now or datetime.now(timezone.utc)
    heartbeat = heartbeat_at or timestamp
    expected_name = controller_name or EXPECTED_CANONICAL_OWNERS.get(loop_id, f"{loop_id}-controller")
    row: Dict[str, Any] = {
        "loop_id": loop_id,
        "tenant_id": TENANT_ID,
        "environment": ENVIRONMENT,
        "controller_id": f"{loop_id}-ctrl-1",
        "controller_name": expected_name,
        "deployment_sha": "0123456789abcdef0123456789abcdef01234567",
        "desired_state_query": f"desired for {loop_id}",
        "actual_state_query": f"actual for {loop_id}",
        "desired_state": {
            "present": True,
            "source": f"{loop_id}-desired-authority",
            "checked_at": heartbeat.isoformat(),
        },
        "downstream_actual_state": {
            "status": "ready",
            "source": f"{loop_id}-terminal-store",
            "checked_at": heartbeat.isoformat(),
        },
        "last_heartbeat_at": heartbeat.isoformat(),
        "last_tick_at": heartbeat.isoformat(),
        "last_success_at": heartbeat.isoformat(),
        "last_failure_at": None,
        "last_failure_reason": None,
        "last_repair_at": None,
        "last_repair_reason": None,
        "backlog": 0,
        "lag": 0,
        "dlq_count": 0,
        "evidence_refs": (
            evidence_refs
            if evidence_refs is not None
            else [f"docs/deployment/evidence/twelve-loop-gap/{loop_id}.json"]
        ),
        "truth_level": "reconciled_live_proof",
        "lease_token": f"{loop_id}-fence-1",
        "lease_expires_at": (heartbeat + timedelta(seconds=60)).isoformat(),
        "controller_status": status,
        "controller_health": {
            "controller_name": expected_name,
            "status": status,
            "last_heartbeat_at": heartbeat.isoformat(),
        },
    }
    if worker_health is not None:
        row["worker_health"] = worker_health
    return row


class TestTwelveOwnerCatalogContract:
    """Validate that all twelve canonical loops declare complete owner contracts."""

    def test_catalog_declares_all_twelve_canonical_owners(self) -> None:
        conformance = _loop_conformance_module()
        registry = loop_inventory_model._load_registry()
        loops = registry["loops"]
        canonical_ids = [loop["loop_id"] for loop in loops]

        assert len(canonical_ids) == 12
        assert set(canonical_ids) == set(conformance.CANONICAL_LOOP_IDS)
        assert canonical_ids == list(EXPECTED_CANONICAL_OWNERS)

        for loop in loops:
            loop_id = loop["loop_id"]
            expected_owner = EXPECTED_CANONICAL_OWNERS[loop_id]

            # Owner declaration
            owner = loop["owner"]
            assert owner["current_controller_owner"] == expected_owner, (
                f"{loop_id} current_controller_owner is {owner.get('current_controller_owner')!r}, "
                f"expected {expected_owner!r}"
            )
            assert owner["authoritative_write_owner"], f"{loop_id} missing authoritative_write_owner"
            assert owner["service_or_doc"], f"{loop_id} missing service_or_doc"

            # Controller contract completeness
            contract = loop["controller_contract"]
            assert contract["status"] == "implemented", f"{loop_id} status is {contract.get('status')}"
            assert contract["controller_name"] == expected_owner, f"{loop_id} controller_name mismatch"
            assert contract["desired_state_query"], f"{loop_id} missing desired_state_query"
            assert contract["actual_state_query"], f"{loop_id} missing actual_state_query"
            assert contract["restart_behavior"], f"{loop_id} missing restart_behavior"
            assert contract["liveness_metric"] == "last_heartbeat_at", f"{loop_id} liveness_metric mismatch"
            assert contract["idempotency_key"], f"{loop_id} missing idempotency_key"
            assert contract["duplicate_event_policy"], f"{loop_id} missing duplicate_event_policy"

            # Maturity bounds
            assert loop["maturity"]["current"] in {"api-only", "manual"}, (
                f"{loop_id} maturity ceiling exceeded: {loop['maturity']['current']}"
            )

    def test_composite_overlay_is_isolated_from_canonical_loops(self) -> None:
        registry = loop_inventory_model._load_registry()
        overlays = registry.get("composite_overlays", [])
        assert len(overlays) == 1
        overlay = overlays[0]
        assert overlay["loop_id"] == "per_persona_ooda"
        assert overlay["classification"] == "composite_overlay"
        assert overlay["controller_contract"]["status"] == "not_implemented"

    def test_loop_inventory_endpoint_publishes_all_twelve_canonical_contracts(self) -> None:
        with _scoped_health_client() as client:
            response = client.get("/bff/v5/loop-inventory", headers=HEADERS)

        assert response.status_code == 200, response.text
        payload = response.json()
        items = {item["loop_id"]: item for item in payload["items"]}
        assert len(items) == 13

        coverage = payload["meta"]["catalog"]["controller_contract_coverage"]
        assert coverage["declared_controller_count"] == 12
        assert coverage["no_declared_controller_count"] == 1
        assert coverage["incomplete_contract_loop_ids"] == []

        for loop_id, expected_name in EXPECTED_CANONICAL_OWNERS.items():
            item = items[loop_id]
            declaration = item["controller_contract_declaration"]
            assert declaration["status"] == "implemented"
            assert declaration["controller_implemented"] is True
            assert declaration["contract_complete"] is True
            assert declaration["missing_contract_fields"] == []
            assert item["owner"]["current_controller_owner"] == expected_name


class TestDegradedAndUnobservedProjection:
    """Validate that unobserved or degraded loops cannot be accepted live."""

    def test_unobserved_loops_project_as_unobserved_and_not_live(self) -> None:
        with _scoped_health_client() as client:
            response = client.get("/bff/v5/loop-health", headers=HEADERS)

        assert response.status_code == 200, response.text
        payload = response.json()

        for item in payload["items"]:
            loop_id = item["loop_id"]
            live_status = item["live_status"]
            assert live_status["is_live"] is False, f"{loop_id} claimed is_live without observation"
            assert live_status["is_reconciled"] is False, f"{loop_id} claimed is_reconciled"
            assert live_status["has_live_evidence"] is False, f"{loop_id} claimed has_live_evidence"

            if loop_id in EXPECTED_CANONICAL_OWNERS:
                assert item["controller"]["status"] == "implemented"
                assert item["controller_health"]["status"] == "unobserved"
                assert item["controller_health"]["current_record_accepted"] is False
                assert item["controller_health"]["rejection_reason"] is not None

    def test_stale_heartbeat_is_rejected_as_degraded(self) -> None:
        now = datetime.now(timezone.utc)
        stale_heartbeat = now - timedelta(seconds=1200)  # > 900s
        row = _build_valid_controller_row(
            "source_ingestion",
            now=now,
            heartbeat_at=stale_heartbeat,
        )
        store = {"source_ingestion": row}

        with _scoped_health_client(loop_health_store=store) as client:
            response = client.get("/bff/v5/loop-health/source_ingestion", headers=HEADERS)

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["controller_health"]["freshness"]["current"] is False
        assert data["controller_health"]["current_record_accepted"] is False
        assert data["live_status"]["is_live"] is False
        assert data["live_status"]["has_live_evidence"] is False

    def test_future_heartbeat_is_rejected_as_degraded(self) -> None:
        now = datetime.now(timezone.utc)
        future_heartbeat = now + timedelta(seconds=120)  # in future
        row = _build_valid_controller_row(
            "strategy_distillation",
            now=now,
            heartbeat_at=future_heartbeat,
        )
        store = {"strategy_distillation": row}

        with _scoped_health_client(loop_health_store=store) as client:
            response = client.get("/bff/v5/loop-health/strategy_distillation", headers=HEADERS)

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["controller_health"]["freshness"]["current"] is False
        assert data["controller_health"]["current_record_accepted"] is False
        assert data["live_status"]["is_live"] is False

    def test_controller_identity_mismatch_is_rejected(self) -> None:
        now = datetime.now(timezone.utc)
        row = _build_valid_controller_row(
            "consultation",
            now=now,
            controller_name="wrong-rogue-controller",
        )
        store = {"consultation": row}

        with _scoped_health_client(loop_health_store=store) as client:
            response = client.get("/bff/v5/loop-health/consultation", headers=HEADERS)

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["controller_health"]["current_record_accepted"] is False
        assert data["controller_health"]["rejection_reason"] == (
            "runtime controller identity does not match catalog contract"
        )
        assert data["live_status"]["is_live"] is False

    def test_degraded_reported_status_cannot_claim_live(self) -> None:
        now = datetime.now(timezone.utc)
        row = _build_valid_controller_row(
            "capital_pool_execution",
            now=now,
            status="degraded",
        )
        store = {"capital_pool_execution": row}

        with _scoped_health_client(loop_health_store=store) as client:
            response = client.get("/bff/v5/loop-health/capital_pool_execution", headers=HEADERS)

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["controller_health"]["current_record_accepted"] is False
        assert data["live_status"]["is_live"] is False


class TestWorkerFunctionalHealthOverridesProcessReadyz:
    """Validate that worker functional health strictly overrides process HTTP 200 readiness."""

    def test_downstream_health_probe_fails_when_worker_reports_unready(self) -> None:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "service": "source-ingestion",
            "ready": False,
            "status": "degraded",
            "reason": "connector lease expired",
        }).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_response):
            ok, status_code, failure_reason = _probe_http("http://test-service/readyz", 2.0)

        assert ok is False
        assert status_code == 200
        assert "functional_unready" in failure_reason or "functional_degraded" in failure_reason
        assert "connector lease expired" in failure_reason

    def test_downstream_health_probe_fails_when_worker_reports_degraded_status(self) -> None:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "status": "unhealthy",
            "failure_reason": "telemetry stream drift exceeded threshold",
        }).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_response):
            ok, status_code, failure_reason = _probe_http("http://test-service/__health__", 2.0)

        assert ok is False
        assert status_code == 200
        assert "functional_degraded" in failure_reason
        assert "telemetry stream drift exceeded threshold" in failure_reason

    def test_downstream_health_probe_succeeds_when_worker_is_functionally_healthy(self) -> None:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "status": "ok",
            "ready": True,
            "live": True,
        }).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_response):
            ok, status_code, failure_reason = _probe_http("http://test-service/health", 2.0)

        assert ok is True
        assert status_code == 200
        assert failure_reason == ""

    def test_loop_health_read_model_rejects_record_with_degraded_worker_health(self) -> None:
        now = datetime.now(timezone.utc)
        row = _build_valid_controller_row(
            "telemetry_reconciliation",
            now=now,
            status="healthy",
            worker_health={
                "ready": False,
                "status": "degraded",
                "reason": "database replication lag > 30s",
            },
        )
        store = {"telemetry_reconciliation": row}

        with _scoped_health_client(loop_health_store=store) as client:
            response = client.get("/bff/v5/loop-health/telemetry_reconciliation", headers=HEADERS)

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["controller_health"]["current_record_accepted"] is False
        assert data["controller_health"]["rejection_reason"] == (
            "worker functional health is degraded despite process readiness"
        )
        assert data["live_status"]["is_live"] is False


class TestTaskArchiveLivenessRejection:
    """Validate that task archive history is reference-only and cannot produce liveness."""

    def test_task_archive_only_refs_are_rejected_from_live_proof(self) -> None:
        now = datetime.now(timezone.utc)
        archive_refs = [
            "ai-task-archive/tasks/LOOP-AUTO-001.json",
            "ai-task-archive/tasks/L12-TRUTH-001.json",
        ]
        row = _build_valid_controller_row(
            "evolution",
            now=now,
            evidence_refs=archive_refs,
        )
        store = {"evolution": row}

        with _scoped_health_client(loop_health_store=store) as client:
            response = client.get("/bff/v5/loop-health/evolution", headers=HEADERS)

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["controller_health"]["current_record_accepted"] is False
        assert data["controller_health"]["rejection_reason"] == (
            "task archive completion is reference-only, not runtime evidence"
        )
        assert data["live_status"]["is_live"] is False
        assert data["live_status"]["has_live_evidence"] is False
