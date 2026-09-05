from __future__ import annotations

import importlib.util
import json
import os
import sys
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import pytest


BFF_DIR = Path(__file__).resolve().parent

from services.control_plane.bff import main as bff_main
from services.control_plane.bff import loop_inventory as loop_inventory_model  # noqa: E402
from services.control_plane.bff.downstream_health_monitor import _probe_http  # noqa: E402


REPO_ROOT = BFF_DIR.parents[2]
TENANT_ID = "tenant-twelve-truth"
ENVIRONMENT = "dev"
HEADERS = {
    "Authorization": (
        f"Bearer loop-health-operator:operator,reviewer,admin:{TENANT_ID}"
    )
}

EXPECTED_IMPLEMENTED_CONTROLLERS: dict[str, str] = {
    "source_ingestion": "source-ingestion-controller",
    "strategy_distillation": "strategy-distillation-controller",
    "alpha_replication": "alpha-replication-controller",
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
    downstream_monitor: Optional[Any] = None,
) -> Iterator[TestClient]:
    scoped_records: List[Dict[str, Any]] = []
    for record in (loop_health_store or {}).values():
        scoped_record = deepcopy(record)
        scoped_record.setdefault("tenant_id", TENANT_ID)
        scoped_record.setdefault("environment", ENVIRONMENT)
        scoped_records.append(scoped_record)

    async def _fetch_controller_records(
        tenant_id: str,
        environment: str,
    ) -> tuple[bool, List[Dict[str, Any]]]:
        records = [
            deepcopy(record)
            for record in scoped_records
            if record["tenant_id"] == tenant_id
            and record["environment"] == environment
        ]
        return bool(records), records

    env_overrides = {
        "PANTHEON_BFF_AUTH_STUB": "true",
        "PANTHEON_ENV": ENVIRONMENT,
    }
    with (
        patch.dict(os.environ, env_overrides, clear=False),
        patch.object(
            bff_main.loop_truth,
            "fetch_controller_store_health_records",
            new=_fetch_controller_records,
        ),
        patch.object(
            bff_main,
            "downstream_health_monitor",
            downstream_monitor,
        ),
    ):
        yield TestClient(bff_main.app, raise_server_exceptions=False)


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
    expected_name = (
        controller_name
        or EXPECTED_IMPLEMENTED_CONTROLLERS.get(loop_id, f"{loop_id}-controller")
    )
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
        "desired_state_presence": {
            "present": True,
            "authoritative": True,
            "source": f"{loop_id}-desired-authority",
            "checked_at": heartbeat.isoformat(),
            "query": f"desired for {loop_id}",
        },
        "downstream_actual_state": {
            "status": "ready",
            "authoritative": True,
            "source": f"{loop_id}-terminal-store",
            "checked_at": heartbeat.isoformat(),
            "query": f"actual for {loop_id}",
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
        "evidence_basis": "controller_runtime",
        "evidence_bases": ["controller_runtime"],
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

        for loop in loops:
            loop_id = loop["loop_id"]

            # Owner declaration
            owner = loop["owner"]
            assert owner["authoritative_write_owner"], f"{loop_id} missing authoritative_write_owner"
            assert owner["service_or_doc"], f"{loop_id} missing service_or_doc"
            assert owner["domain"], f"{loop_id} missing domain"

            contract = loop["controller_contract"]
            if loop_id in EXPECTED_IMPLEMENTED_CONTROLLERS:
                expected_owner = EXPECTED_IMPLEMENTED_CONTROLLERS[loop_id]
                assert owner["current_controller_owner"] == expected_owner, (
                    f"{loop_id} current_controller_owner is {owner.get('current_controller_owner')!r}, "
                    f"expected {expected_owner!r}"
                )
                assert contract["status"] == "implemented", f"{loop_id} status is {contract.get('status')}"
                assert contract["controller_name"] == expected_owner, f"{loop_id} controller_name mismatch"
                assert contract["desired_state_query"], f"{loop_id} missing desired_state_query"
                assert contract["actual_state_query"], f"{loop_id} missing actual_state_query"
                assert contract["restart_behavior"], f"{loop_id} missing restart_behavior"
                assert contract["liveness_metric"] == "last_heartbeat_at", f"{loop_id} liveness_metric mismatch"
                assert contract["idempotency_key"], f"{loop_id} missing idempotency_key"
                assert contract["duplicate_event_policy"], f"{loop_id} missing duplicate_event_policy"
            else:
                assert owner["current_controller_owner"] is None, (
                    f"{loop_id} declared current_controller_owner without implemented controller"
                )
                assert contract["status"] == "not_implemented", f"{loop_id} status is {contract.get('status')}"
                assert contract["controller_name"] is None, f"{loop_id} controller_name must be null"
                assert contract["desired_state_query"] is None, f"{loop_id} desired_state_query must be null"
                assert contract["actual_state_query"] is None, f"{loop_id} actual_state_query must be null"
                assert contract["restart_behavior"] is None, f"{loop_id} restart_behavior must be null"
                assert contract["liveness_metric"] is None, f"{loop_id} liveness_metric must be null"

            # Static contract status ceiling (proven_live requires live runtime projection, not static catalog)
            assert loop["controller_contract"]["status"] in {"not_implemented", "implemented"}, (
                f"{loop_id} controller_contract status ceiling exceeded: {loop['controller_contract']['status']}"
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
        assert coverage["declared_controller_count"] == 3
        assert coverage["no_declared_controller_count"] == 10
        assert coverage["incomplete_contract_loop_ids"] == []

        for loop_id, expected_name in EXPECTED_IMPLEMENTED_CONTROLLERS.items():
            item = items[loop_id]
            declaration = item["controller_contract_declaration"]
            assert declaration["status"] == "implemented"
            assert declaration["controller_implemented"] is True
            assert declaration["contract_complete"] is True
            assert declaration["missing_contract_fields"] == []
            assert item["owner"]["current_controller_owner"] == expected_name

        for loop_id in [
            "persona_teaching",
            "agora_interaction_evidence",
            "human_imitation_shadow_evaluation",
            "consultation",
            "promotion_deployment",
            "capital_pool_execution",
            "telemetry_reconciliation",
            "evolution",
            "bff_health_monitoring",
        ]:
            item = items[loop_id]
            declaration = item["controller_contract_declaration"]
            assert declaration["status"] == "not_implemented"
            assert declaration["controller_implemented"] is False
            assert declaration["contract_complete"] is False
            assert item["owner"]["current_controller_owner"] is None


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

            if loop_id in EXPECTED_IMPLEMENTED_CONTROLLERS:
                assert item["controller"]["status"] == "implemented"
                assert item["controller_health"]["status"] == "unobserved"
                assert item["controller_health"]["current_record_accepted"] is False
                assert item["controller_health"]["rejection_reason"] is not None
            else:
                assert item["controller"]["status"] == "not_implemented"
                assert item["controller_health"]["status"] == "not_implemented"
                assert item["controller_health"]["current_record_accepted"] is False

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
            "source_ingestion",
            now=now,
            controller_name="wrong-rogue-controller",
        )
        store = {"source_ingestion": row}

        with _scoped_health_client(loop_health_store=store) as client:
            response = client.get("/bff/v5/loop-health/source_ingestion", headers=HEADERS)

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
            "alpha_replication",
            now=now,
            status="degraded",
        )
        store = {"alpha_replication": row}

        with _scoped_health_client(loop_health_store=store) as client:
            response = client.get("/bff/v5/loop-health/alpha_replication", headers=HEADERS)

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
            "source_ingestion",
            now=now,
            status="healthy",
            worker_health={
                "ready": False,
                "status": "degraded",
                "reason": "database replication lag > 30s",
            },
        )
        store = {"source_ingestion": row}

        with _scoped_health_client(loop_health_store=store) as client:
            response = client.get("/bff/v5/loop-health/source_ingestion", headers=HEADERS)

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
            "source_ingestion",
            now=now,
            evidence_refs=archive_refs,
        )
        store = {"source_ingestion": row}

        with _scoped_health_client(loop_health_store=store) as client:
            response = client.get("/bff/v5/loop-health/source_ingestion", headers=HEADERS)

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["controller_health"]["current_record_accepted"] is False
        assert data["controller_health"]["rejection_reason"] == (
            "task archive completion is reference-only, not runtime evidence"
        )
        assert data["live_status"]["is_live"] is False
        assert data["live_status"]["has_live_evidence"] is False


class TestAllTwelveProductLoopsRuntimeObservations:
    """Validate positive and negative runtime observation acceptance across all twelve loops."""

    def test_positive_runtime_observations_respect_catalog_controller_admission(self) -> None:
        conformance = _loop_conformance_module()
        now = datetime.now(timezone.utc)
        store = {
            loop_id: _build_valid_controller_row(loop_id, now=now)
            for loop_id in conformance.CANONICAL_LOOP_IDS
        }

        with _scoped_health_client(loop_health_store=store) as client:
            response = client.get("/bff/v5/loop-health", headers=HEADERS)

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["meta"]["coverage"]["canonical_loop_count"] == 12
        assert payload["meta"]["coverage"]["raw_health_record_count"] == 12
        assert payload["meta"]["coverage"]["controller_health_record_count"] == len(
            EXPECTED_IMPLEMENTED_CONTROLLERS
        )
        assert payload["meta"]["surfaces"]["loop_health"]["status"] == "degraded"

        items = {item["loop_id"]: item for item in payload["items"]}
        for loop_id in conformance.CANONICAL_LOOP_IDS:
            assert loop_id in items, f"{loop_id} missing from items"
            item = items[loop_id]
            health = item["controller_health"]
            live_status = item["live_status"]
            packet = item["evidence_packet"]
            maturity = item["runtime_maturity"]

            if loop_id in EXPECTED_IMPLEMENTED_CONTROLLERS:
                assert health["current_record_accepted"] is True, loop_id
                assert health["status"] == "healthy", loop_id
                assert health["rejection_reason"] is None, loop_id
                assert live_status["has_live_evidence"] is True, loop_id
                assert live_status["is_reconciled"] is True, loop_id
                assert live_status["operator_truth"]["accepted_as_live"] is True, loop_id
                assert live_status["operator_truth"]["degraded"] is False, loop_id
                assert maturity["state"] == "reconciled", loop_id
                assert packet["runtime_controller_record_qualified"] is True, loop_id
                assert packet["accepted_live_liveness"] is True, loop_id
            else:
                assert health["current_record_accepted"] is False, loop_id
                assert health["rejection_reason"] is not None, loop_id
                assert live_status["has_live_evidence"] is False, loop_id
                assert live_status["is_reconciled"] is False, loop_id
                assert packet["runtime_controller_record_qualified"] is False, loop_id
                assert packet["accepted_live_liveness"] is False, loop_id

    def test_all_twelve_loop_detail_endpoints_apply_catalog_admission(self) -> None:
        conformance = _loop_conformance_module()
        now = datetime.now(timezone.utc)
        for loop_id in conformance.CANONICAL_LOOP_IDS:
            row = _build_valid_controller_row(loop_id, now=now)
            store = {loop_id: row}
            with _scoped_health_client(loop_health_store=store) as client:
                response = client.get(f"/bff/v5/loop-health/{loop_id}", headers=HEADERS)

            assert response.status_code == 200, f"{loop_id} detail failed: {response.text}"
            data = response.json()["data"]
            expected = loop_id in EXPECTED_IMPLEMENTED_CONTROLLERS
            assert data["controller_health"]["current_record_accepted"] is expected, loop_id
            assert data["live_status"]["is_reconciled"] is expected, loop_id
            assert data["live_status"]["has_live_evidence"] is expected, loop_id
            if expected:
                assert data["controller_health"]["status"] == "healthy", loop_id

    def test_all_twelve_loops_reject_negative_stale_heartbeat(self) -> None:
        conformance = _loop_conformance_module()
        now = datetime.now(timezone.utc)
        stale_heartbeat = now - timedelta(hours=2)
        for loop_id in conformance.CANONICAL_LOOP_IDS:
            row = _build_valid_controller_row(
                loop_id,
                now=now,
                heartbeat_at=stale_heartbeat,
            )
            store = {loop_id: row}
            with _scoped_health_client(loop_health_store=store) as client:
                response = client.get(f"/bff/v5/loop-health/{loop_id}", headers=HEADERS)

            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert data["controller_health"]["freshness"]["current"] is False, loop_id
            assert data["controller_health"]["current_record_accepted"] is False, loop_id
            assert data["live_status"]["has_live_evidence"] is False, loop_id
            assert data["live_status"]["is_reconciled"] is False, loop_id

    def test_all_twelve_loops_reject_negative_degraded_worker_health(self) -> None:
        conformance = _loop_conformance_module()
        now = datetime.now(timezone.utc)
        for loop_id in conformance.CANONICAL_LOOP_IDS:
            row = _build_valid_controller_row(
                loop_id,
                now=now,
                worker_health={
                    "ready": False,
                    "status": "degraded",
                    "reason": f"{loop_id} worker worker_unready",
                },
            )
            store = {loop_id: row}
            with _scoped_health_client(loop_health_store=store) as client:
                response = client.get(f"/bff/v5/loop-health/{loop_id}", headers=HEADERS)

            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert data["controller_health"]["current_record_accepted"] is False, loop_id
            if loop_id in EXPECTED_IMPLEMENTED_CONTROLLERS:
                assert data["controller_health"]["rejection_reason"] == (
                    "worker functional health is degraded despite process readiness"
                ), loop_id
            assert data["live_status"]["has_live_evidence"] is False, loop_id
            assert data["live_status"]["is_reconciled"] is False, loop_id

    def test_all_twelve_loops_reject_negative_archive_only_refs(self) -> None:
        conformance = _loop_conformance_module()
        now = datetime.now(timezone.utc)
        for loop_id in conformance.CANONICAL_LOOP_IDS:
            row = _build_valid_controller_row(
                loop_id,
                now=now,
                evidence_refs=[f"ai-task-archive/tasks/LOOP-{loop_id}.json"],
            )
            store = {loop_id: row}
            with _scoped_health_client(loop_health_store=store) as client:
                response = client.get(f"/bff/v5/loop-health/{loop_id}", headers=HEADERS)

            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert data["controller_health"]["current_record_accepted"] is False, loop_id
            assert data["controller_health"]["rejection_reason"] == (
                "task archive completion is reference-only, not runtime evidence"
            ), loop_id
            assert data["live_status"]["has_live_evidence"] is False, loop_id
            assert data["live_status"]["is_reconciled"] is False, loop_id


class TestOverlayExclusion:
    """Validate that composite overlay controllers are excluded from canonical loop runtime acceptance."""

    def test_composite_overlay_per_persona_ooda_rejects_direct_controller_record(self) -> None:
        now = datetime.now(timezone.utc)
        row = _build_valid_controller_row("per_persona_ooda", now=now)
        store = {"per_persona_ooda": row}

        with _scoped_health_client(loop_health_store=store) as client:
            detail_response = client.get(
                "/bff/v5/loop-health/per_persona_ooda",
                headers=HEADERS,
            )
            list_response = client.get("/bff/v5/loop-health", headers=HEADERS)

        assert detail_response.status_code == 404, detail_response.text
        assert list_response.status_code == 200, list_response.text
        payload = list_response.json()
        assert "per_persona_ooda" not in {
            item["loop_id"] for item in payload["items"]
        }
        overlays = payload["meta"]["composite_overlay_inventory"]
        assert [item["loop_id"] for item in overlays] == ["per_persona_ooda"]


class TestBffDownstreamWorkerIsolation:
    """Validate that component probes cannot manufacture canonical loop truth."""

    def test_downstream_probe_failures_stay_on_downstream_health_surface(self) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        mock_monitor = MagicMock()
        mock_monitor.get_state.return_value = {
            "targets": {
                "deployment-outbox-consumer": {
                    "name": "deployment-outbox-consumer",
                    "ok": False,
                    "failure_reason": "database outbox deadletter threshold exceeded",
                    "checked_at": now_iso,
                },
                "paper-fleet-reconciler": {
                    "name": "paper-fleet-reconciler",
                    "ok": False,
                    "failure_reason": "portfolio drift exceeds guardrail limit",
                    "checked_at": now_iso,
                },
                "runtime-manager": {
                    "name": "runtime-manager",
                    "ok": False,
                    "failure_reason": "container runtime unreachable",
                    "checked_at": now_iso,
                },
            }
        }

        with _scoped_health_client(downstream_monitor=mock_monitor) as client:
            downstream_response = client.get(
                "/bff/v5/downstream-health",
                headers=HEADERS,
            )
            loop_response = client.get("/bff/v5/loop-health", headers=HEADERS)

        assert downstream_response.status_code == 200, downstream_response.text
        assert downstream_response.json()["data"] == mock_monitor.get_state.return_value

        assert loop_response.status_code == 200, loop_response.text
        loop_payload = loop_response.json()
        assert len(loop_payload["items"]) == 12
        serialized_loops = json.dumps(loop_payload["items"], sort_keys=True)
        for target in mock_monitor.get_state.return_value["targets"].values():
            assert target["failure_reason"] not in serialized_loops
        mock_monitor.publish_loop_12_controller_truth.assert_not_called()
