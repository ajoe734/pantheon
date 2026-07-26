from __future__ import annotations

import json
import os
import sys
import tempfile
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional
from unittest.mock import patch

from fastapi.testclient import TestClient


BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
import loop_inventory as loop_inventory_model  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402
from services.runtime_auth_inbound import encode_jwt_hs256  # noqa: E402


TENANT_ID = "tenant-loop-health"
ENVIRONMENT = "dev"
HEADERS = {
    "Authorization": (
        "Bearer loop-health-operator:operator,reviewer,admin:"
        f"{TENANT_ID}"
    )
}


def _scope_loop_health_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    scoped = deepcopy(payload)
    records = scoped.get("loop_health")
    if isinstance(records, dict):
        for record in records.values():
            if isinstance(record, dict):
                record.setdefault("tenant_id", TENANT_ID)
                record.setdefault("environment", ENVIRONMENT)
    return scoped


def _scope_loop_health_records(
    records: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    scoped = deepcopy(records)
    for record in scoped.values():
        if isinstance(record, dict):
            record.setdefault("tenant_id", TENANT_ID)
            record.setdefault("environment", ENVIRONMENT)
    return scoped


@contextmanager
def _loop_health_client(
    *,
    loop_health_store: Optional[Dict[str, Dict[str, Any]]] = None,
    snapshot_payload: Optional[Dict[str, Any]] = None,
    allow_snapshot_fallback: bool = False,
) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        snapshot_path = root / "read_surfaces.json"
        if snapshot_payload is not None:
            snapshot_path.write_text(
                json.dumps(_scope_loop_health_payload(snapshot_payload)),
                encoding="utf-8",
            )
        env_overrides: Dict[str, str] = {}
        if loop_health_store is not None:
            health_path = root / "loop_health.json"
            health_path.write_text(
                json.dumps(_scope_loop_health_records(loop_health_store)),
                encoding="utf-8",
            )
            env_overrides["PANTHEON_BFF_LOOP_HEALTH_STORE"] = str(health_path)

        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            str(snapshot_path),
            allow_local_snapshot_fallback=allow_snapshot_fallback,
        )
        with patch.dict(os.environ, env_overrides, clear=False):
            try:
                yield TestClient(bff_main.app, raise_server_exceptions=False)
            finally:
                bff_main.read_store = original_store


def _truth_source(packet: Dict[str, Any], truth_level: str) -> Dict[str, Any]:
    return next(
        item
        for item in packet["truth_sources"]
        if item["truth_level"] == truth_level
    )


def test_loop_health_registry_only_lists_all_loops_without_live_claim(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _loop_health_client() as client:
        response = client.get("/bff/v5/loop-health", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["items"]) == 13
    assert payload["meta"]["surfaces"]["loop_health"]["status"] == "degraded"
    assert payload["meta"]["surfaces"]["loop_health"]["truth_level"] == "registry_metadata"
    assert payload["meta"]["surfaces"]["loop_health_snapshots"]["source"] == "missing"
    assert payload["meta"]["truth_labels"]["seed_fixture"]["label"] == "Seed / fixture"
    assert payload["meta"]["truth_labels"]["snapshot_fallback"]["source_type"] == "snapshot"
    assert payload["meta"]["truth_labels"]["registry_metadata"]["source_type"] == "registry"
    assert payload["meta"]["truth_labels"]["scheduled_tick"]["label"] == "Scheduled tick"
    assert payload["meta"]["truth_labels"]["reconciled_live_proof"]["source_type"] == "live_truth"
    assert payload["meta"]["truth_source_policy"]["non_live_source_types"] == [
        "seed_fixture",
        "snapshot",
        "registry",
        "scheduled",
    ]
    assert payload["meta"]["coverage"] == {
        "loop_count": 13,
        "canonical_loop_count": 12,
        "composite_overlay_count": 1,
        "inventory_entry_count": 13,
        "controller_health_record_count": 0,
        "raw_health_record_count": 0,
        "controller_health_records_available": False,
        "accepted_controller_health_records_available": False,
    }

    source_loop = next(item for item in payload["items"] if item["loop_id"] == "source_ingestion")
    assert source_loop["current_maturity"] == "api-only"
    assert source_loop["target_maturity"] == "reconciled"
    assert source_loop["controller_health"]["status"] == "not_implemented"
    assert source_loop["last_success"] is None
    assert source_loop["last_failure"] is None

    ooda_overlay = next(item for item in payload["items"] if item["loop_id"] == "per_persona_ooda")
    assert ooda_overlay["classification"] == "composite_overlay"
    assert ooda_overlay["controller_health"]["current_record_accepted"] is False
    assert ooda_overlay["live_status"]["is_live"] is False

    packet = source_loop["evidence_packet"]
    assert packet["highest_truth_level"] == "registry_metadata"
    assert packet["accepted_live_liveness"] is False
    assert packet["can_claim_reconciled"] is False
    assert packet["operator_truth"]["truth_level"] == "registry_metadata"
    assert packet["operator_truth"]["label"] == "Registry metadata"
    assert packet["operator_truth"]["source_type"] == "registry"
    assert packet["operator_truth"]["accepted_as_live"] is False
    assert packet["operator_truth"]["degraded"] is True
    assert _truth_source(packet, "seed_fixture")["source_type"] == "seed_fixture"
    assert _truth_source(packet, "seed_fixture")["label"] == "Seed / fixture"
    assert _truth_source(packet, "seed_fixture")["operator_visibility"] == "not_live_proof"
    assert _truth_source(packet, "snapshot_fallback")["source_type"] == "snapshot"
    assert _truth_source(packet, "snapshot_fallback")["status"] == "missing"
    assert _truth_source(packet, "registry_metadata")["status"] == "present"
    assert _truth_source(packet, "scheduled_tick")["source_type"] == "scheduled"
    assert _truth_source(packet, "reconciled_live_proof")["source_type"] == "live_truth"


def test_loop_health_service_store_overlays_controller_health_and_events(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    loop_health_store = {
        "source_ingestion": {
            "loop_id": "source_ingestion",
            "truth_level": "scheduled_tick",
            "controller_health": {
                "status": "healthy",
                "controller_name": "source-provisioning-health-probe",
                "last_heartbeat_at": "2026-06-27T06:00:00Z",
            },
            "last_success": {
                "at": "2026-06-27T06:01:00Z",
                "summary": "Observed source scheduler probe tick.",
                "evidence_refs": ["probe-run-001"],
            },
            "last_failure": {
                "at": "2026-06-27T05:55:00Z",
                "reason": "connector timeout",
                "evidence_refs": ["probe-run-000"],
            },
            "downstream_actual_state": {
                "status": "ok",
                "source": "source-health-probe",
                "checked_at": "2026-06-27T06:01:00Z",
            },
            "evidence_packet": {
                "packet_id": "packet-source-ingestion-health-001",
                "refs": ["docs/deployment/evidence/source-health/probe-run-001.json"],
            },
        }
    }
    with _loop_health_client(loop_health_store=loop_health_store) as client:
        response = client.get("/bff/v5/loop-health/source_ingestion", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]
    assert data["controller_health"]["status"] == "not_implemented"
    assert data["controller_health"]["source"] == "registry_metadata"
    assert data["controller_health"]["reported_status"] == "healthy"
    assert data["controller_health"]["reported_source"] == "service_store"
    assert data["controller_health"]["current_record_accepted"] is False
    assert data["last_success"]["at"] == "2026-06-27T06:01:00Z"
    assert data["last_failure"]["reason"] == "connector timeout"
    assert data["downstream_actual_state"]["status"] == "ok"
    assert data["evidence_packet"]["packet_id"] == "packet-source-ingestion-health-001"
    assert data["evidence_packet"]["highest_truth_level"] == "scheduled_tick"
    assert data["evidence_packet"]["accepted_live_liveness"] is False
    assert data["evidence_packet"]["operator_truth"]["truth_level"] == "scheduled_tick"
    assert data["evidence_packet"]["operator_truth"]["label"] == "Scheduled tick"
    assert data["evidence_packet"]["operator_truth"]["source_type"] == "scheduled"
    assert data["evidence_packet"]["operator_truth"]["is_live_truth"] is False
    assert _truth_source(data["evidence_packet"], "scheduled_tick")["status"] == "present"


def test_loop_health_local_snapshot_is_labeled_snapshot_not_live(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    snapshot_payload = {
        "loop_health": {
            "bff_health_monitoring": {
                "loop_id": "bff_health_monitoring",
                "truth_level": "reconciled_live_proof",
                "controller_status": "observed",
                "last_success_at": "2026-06-27T06:10:00Z",
            }
        }
    }
    with _loop_health_client(
        snapshot_payload=snapshot_payload,
        allow_snapshot_fallback=True,
    ) as client:
        response = client.get("/bff/v5/loop-health/bff_health_monitoring", headers=HEADERS)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    packet = data["evidence_packet"]
    assert data["controller_health"]["source"] == "registry_metadata"
    assert data["controller_health"]["reported_source"] == "local_snapshot"
    assert data["controller_health"]["current_record_accepted"] is False
    assert _truth_source(packet, "snapshot_fallback")["status"] == "present"
    assert _truth_source(packet, "snapshot_fallback")["source_type"] == "snapshot"
    assert packet["highest_truth_level"] == "reconciled_live_proof"
    assert packet["operator_truth"]["truth_level"] == "snapshot_fallback"
    assert packet["operator_truth"]["label"] == "Snapshot fallback"
    assert packet["operator_truth"]["source_type"] == "snapshot"
    assert packet["operator_truth"]["highest_available_truth_level"] == "reconciled_live_proof"
    assert packet["accepted_live_liveness"] is False
    assert packet["can_claim_reconciled"] is False
    assert data["live_status"]["has_live_evidence"] is False
    assert data["live_status"]["operator_truth"]["accepted_as_live"] is False


def test_loop_health_archive_completion_cannot_create_controller_liveness(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    registry = deepcopy(loop_inventory_model._load_registry())
    source_loop = next(
        loop for loop in registry["loops"] if loop["loop_id"] == "source_ingestion"
    )
    source_loop["maturity"]["current"] = "proven-live"
    source_loop["controller_contract"].update(
        {
            "status": "proven_live",
            "controller_name": "source-ingestion-controller",
            "desired_state_query": "configured source requirements",
            "actual_state_query": "current source schedules",
            "restart_behavior": "resume from durable cursor",
            "liveness_metric": "last_heartbeat_at",
        }
    )
    source_loop["evidence_profile"]["reconciled_live_proof"]["status"] = "present"
    source_loop["evidence_profile"]["proven_live_evidence"]["status"] = "present"
    monkeypatch.setattr(loop_inventory_model, "_load_registry", lambda: registry)
    current_heartbeat = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    loop_health_store = {
        "source_ingestion": {
            "loop_id": "source_ingestion",
            "truth_level": "proven_live_evidence",
            "truth_status": "present",
            "evidence_basis": "task_archive_completion",
            "controller_health": {
                "status": "healthy",
                "controller_name": "source-ingestion-controller",
                "last_heartbeat_at": current_heartbeat,
            },
            "evidence_packet": {
                "packet_id": "archive-loop-auto-bff-004",
                "refs": ["ai-task-archive/tasks/LOOP-AUTO-BFF-004.json"],
            },
        }
    }
    with _loop_health_client(loop_health_store=loop_health_store) as client:
        response = client.get("/bff/v5/loop-health/source_ingestion", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]
    packet = data["evidence_packet"]
    assert packet["highest_truth_level"] == "proven_live_evidence"
    assert packet["runtime_record_evidence_basis"] == "task_archive_completion"
    assert packet["runtime_controller_record_qualified"] is False
    assert packet["archived_task_completion_accepted"] is False
    assert packet["accepted_live_liveness"] is False
    assert packet["can_claim_reconciled"] is False
    assert packet["can_claim_proven_live"] is False
    assert data["controller_health"]["reported_status"] == "healthy"
    assert data["controller_health"]["status"] == "unobserved"
    assert data["controller_health"]["current_record_accepted"] is False
    assert data["live_status"]["is_reconciled"] is False
    assert data["live_status"]["is_live"] is False
    assert payload["meta"]["surfaces"]["loop_health"]["status"] == "degraded"
    assert payload["meta"]["coverage"]["controller_health_record_count"] == 0
    assert payload["meta"]["coverage"]["raw_health_record_count"] == 1


def test_loop_health_rejects_conflicting_or_archive_only_runtime_provenance(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    registry = deepcopy(loop_inventory_model._load_registry())
    source_loop = next(
        loop for loop in registry["loops"] if loop["loop_id"] == "source_ingestion"
    )
    source_loop["maturity"]["current"] = "proven-live"
    source_loop["controller_contract"].update(
        {
            "status": "proven_live",
            "controller_name": "source-ingestion-controller",
            "desired_state_query": "configured source requirements",
            "actual_state_query": "current source schedules",
            "restart_behavior": "resume from durable cursor",
            "liveness_metric": "last_heartbeat_at",
        }
    )
    source_loop["evidence_profile"]["reconciled_live_proof"]["status"] = "present"
    source_loop["evidence_profile"]["proven_live_evidence"]["status"] = "present"
    monkeypatch.setattr(loop_inventory_model, "_load_registry", lambda: registry)
    current_heartbeat = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    archive_ref = "ai-task-archive/tasks/LOOP-AUTO-BFF-004.json"
    runtime_ref = "docs/deployment/evidence/source-ingestion/current-controller.json"
    cases = [
        (
            "top-runtime-nested-archive",
            "controller_runtime",
            "task_archive_completion",
            [archive_ref],
            False,
            "record declares conflicting evidence provenance",
        ),
        (
            "top-archive-nested-runtime",
            "task_archive_completion",
            "controller_runtime",
            [runtime_ref],
            False,
            "record declares conflicting evidence provenance",
        ),
        (
            "accepted-basis-archive-only-ref",
            "controller_runtime",
            "controller_runtime",
            [archive_ref],
            False,
            "task archive completion is reference-only, not runtime evidence",
        ),
        (
            "accepted-basis-with-runtime-and-archive-refs",
            "controller_runtime",
            "controller_runtime",
            [archive_ref, runtime_ref],
            True,
            None,
        ),
    ]

    for case_id, top_basis, packet_basis, refs, accepted, rejection_reason in cases:
        loop_health_store = {
            "source_ingestion": {
                "loop_id": "source_ingestion",
                "truth_level": "proven_live_evidence",
                "truth_status": "present",
                "evidence_basis": top_basis,
                "controller_health": {
                    "status": "healthy",
                    "controller_name": "source-ingestion-controller",
                    "last_heartbeat_at": current_heartbeat,
                },
                "evidence_packet": {
                    "packet_id": case_id,
                    "provenance_type": packet_basis,
                    "refs": refs,
                },
            }
        }
        with _loop_health_client(loop_health_store=loop_health_store) as client:
            response = client.get(
                "/bff/v5/loop-health/source_ingestion",
                headers=HEADERS,
            )

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        packet = data["evidence_packet"]
        assert packet["runtime_controller_record_qualified"] is accepted, case_id
        assert packet["accepted_live_liveness"] is accepted, case_id
        assert packet["can_claim_reconciled"] is accepted, case_id
        assert packet["can_claim_proven_live"] is accepted, case_id
        assert data["controller_health"]["current_record_accepted"] is accepted, case_id
        assert data["controller_health"]["rejection_reason"] == rejection_reason, case_id
        assert data["live_status"]["is_reconciled"] is accepted, case_id
        assert data["live_status"]["is_live"] is accepted, case_id


def test_loop_health_cannot_combine_lower_live_truth_with_higher_registry_claim(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    registry = deepcopy(loop_inventory_model._load_registry())
    source_loop = next(
        loop for loop in registry["loops"] if loop["loop_id"] == "source_ingestion"
    )
    source_loop["maturity"]["current"] = "proven-live"
    source_loop["controller_contract"].update(
        {
            "status": "proven_live",
            "controller_name": "source-ingestion-controller",
            "desired_state_query": "configured source requirements",
            "actual_state_query": "current source schedules",
            "restart_behavior": "resume from durable cursor",
            "liveness_metric": "last_heartbeat_at",
        }
    )
    source_loop["evidence_profile"]["reconciled_live_proof"]["status"] = "present"
    source_loop["evidence_profile"]["proven_live_evidence"]["status"] = "present"
    monkeypatch.setattr(loop_inventory_model, "_load_registry", lambda: registry)
    current_heartbeat = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    loop_health_store = {
        "source_ingestion": {
            "loop_id": "source_ingestion",
            "truth_level": "reconciled_live_proof",
            "truth_status": "present",
            "evidence_basis": "controller_runtime",
            "controller_health": {
                "status": "healthy",
                "controller_name": "source-ingestion-controller",
                "last_heartbeat_at": current_heartbeat,
            },
            "evidence_packet": {
                "packet_id": "source-ingestion-reconciled-only",
                "refs": ["docs/deployment/evidence/source-ingestion/reconciled.json"],
            },
        }
    }
    with _loop_health_client(loop_health_store=loop_health_store) as client:
        response = client.get("/bff/v5/loop-health/source_ingestion", headers=HEADERS)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    packet = data["evidence_packet"]
    assert packet["highest_truth_level"] == "proven_live_evidence"
    assert packet["operator_truth"]["truth_level"] == "reconciled_live_proof"
    assert packet["operator_truth"]["accepted_as_live"] is True
    assert packet["accepted_live_liveness"] is True
    assert packet["can_claim_reconciled"] is True
    assert packet["can_claim_proven_live"] is False
    assert data["controller_health"]["current_record_accepted"] is True
    assert data["live_status"]["is_reconciled"] is True
    assert data["live_status"]["is_live"] is False


def test_loop_health_accepts_current_controller_runtime_only_when_catalog_admits_it(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    registry = deepcopy(loop_inventory_model._load_registry())
    bff_loop = next(
        loop for loop in registry["loops"] if loop["loop_id"] == "bff_health_monitoring"
    )
    bff_loop["maturity"]["current"] = "reconciled"
    bff_loop["controller_contract"].update(
        {
            "status": "implemented",
            "controller_name": "bff-health-reconciler",
            "desired_state_query": "required downstream probes",
            "actual_state_query": "current downstream health",
            "restart_behavior": "resume durable probe schedule",
            "liveness_metric": "last_heartbeat_at",
        }
    )
    bff_loop["evidence_profile"]["reconciled_live_proof"]["status"] = "present"
    monkeypatch.setattr(loop_inventory_model, "_load_registry", lambda: registry)
    current_heartbeat = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    loop_health_store = {
        "bff_health_monitoring": {
            "loop_id": "bff_health_monitoring",
            "truth_level": "reconciled_live_proof",
            "truth_status": "present",
            "evidence_basis": "controller_runtime",
            "truth_note": "BFF health controller reconciled desired and actual state.",
            "controller_health": {
                "status": "healthy",
                "controller_name": "bff-health-reconciler",
                "last_heartbeat_at": current_heartbeat,
            },
            "evidence_packet": {
                "packet_id": "packet-bff-health-live-001",
                "refs": ["docs/deployment/evidence/bff-health/current-controller.json"],
            },
        }
    }
    with _loop_health_client(loop_health_store=loop_health_store) as client:
        response = client.get("/bff/v5/loop-health/bff_health_monitoring", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]
    packet = data["evidence_packet"]
    assert packet["highest_truth_level"] == "reconciled_live_proof"
    assert packet["runtime_record_evidence_basis"] == "controller_runtime"
    assert packet["runtime_controller_record_qualified"] is True
    assert packet["accepted_live_liveness"] is True
    assert packet["operator_truth"]["truth_level"] == "reconciled_live_proof"
    assert packet["operator_truth"]["label"] == "Reconciled live truth"
    assert packet["operator_truth"]["source_type"] == "live_truth"
    assert packet["operator_truth"]["accepted_as_live"] is True
    assert packet["operator_truth"]["degraded"] is False
    assert data["controller_health"]["status"] == "healthy"
    assert data["controller_health"]["current_record_accepted"] is True
    assert data["live_status"]["is_reconciled"] is True
    assert data["live_status"]["is_live"] is False
    assert payload["meta"]["surfaces"]["loop_health"]["status"] == "ok"
    assert payload["meta"]["coverage"]["controller_health_record_count"] == 1
    assert _truth_source(packet, "registry_metadata")["source_type"] == "registry"
    assert _truth_source(packet, "reconciled_live_proof")["source_type"] == "live_truth"

    stale_store = deepcopy(loop_health_store)
    stale_store["bff_health_monitoring"]["controller_health"]["last_heartbeat_at"] = (
        "2000-01-01T00:00:00Z"
    )
    with _loop_health_client(loop_health_store=stale_store) as client:
        stale_response = client.get(
            "/bff/v5/loop-health/bff_health_monitoring",
            headers=HEADERS,
        )

    assert stale_response.status_code == 200, stale_response.text
    stale_data = stale_response.json()["data"]
    assert stale_data["controller_health"]["freshness"]["current"] is False
    assert stale_data["controller_health"]["current_record_accepted"] is False
    assert stale_data["evidence_packet"]["accepted_live_liveness"] is False
    assert stale_data["live_status"]["is_reconciled"] is False


def test_loop_health_rejects_runtime_record_from_wrong_controller_identity(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    registry = deepcopy(loop_inventory_model._load_registry())
    bff_loop = next(
        loop for loop in registry["loops"] if loop["loop_id"] == "bff_health_monitoring"
    )
    bff_loop["maturity"]["current"] = "reconciled"
    bff_loop["controller_contract"].update(
        {
            "status": "implemented",
            "controller_name": "expected-bff-health-controller",
            "desired_state_query": "required downstream probes",
            "actual_state_query": "current downstream health",
            "restart_behavior": "resume durable probe schedule",
            "liveness_metric": "last_heartbeat_at",
        }
    )
    bff_loop["evidence_profile"]["reconciled_live_proof"]["status"] = "present"
    monkeypatch.setattr(loop_inventory_model, "_load_registry", lambda: registry)
    current_heartbeat = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    loop_health_store = {
        "bff_health_monitoring": {
            "loop_id": "bff_health_monitoring",
            "truth_level": "reconciled_live_proof",
            "truth_status": "present",
            "evidence_basis": "controller_runtime",
            "controller_health": {
                "status": "healthy",
                "controller_name": "unexpected-controller",
                "last_heartbeat_at": current_heartbeat,
            },
            "evidence_packet": {
                "packet_id": "packet-wrong-controller",
                "refs": ["docs/deployment/evidence/bff-health/wrong-controller.json"],
            },
        }
    }
    with _loop_health_client(loop_health_store=loop_health_store) as client:
        response = client.get("/bff/v5/loop-health/bff_health_monitoring", headers=HEADERS)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    packet = data["evidence_packet"]
    assert packet["runtime_controller_record_qualified"] is False
    assert packet["accepted_live_liveness"] is False
    assert packet["can_claim_reconciled"] is False
    assert data["controller_health"]["current_record_accepted"] is False
    assert data["controller_health"]["rejection_reason"] == (
        "runtime controller identity does not match catalog contract"
    )
    assert data["live_status"]["is_reconciled"] is False


def test_loop_health_detail_unknown_id_is_404(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _loop_health_client() as client:
        response = client.get("/bff/v5/loop-health/not-a-loop", headers=HEADERS)

    assert response.status_code == 404, response.text


def test_loop_health_db_store_exercise(monkeypatch) -> None:
    from datetime import timedelta
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://dummy_user:dummy_pass@localhost:5432/dummy_db")

    # Mock asyncpg to prevent ModuleNotFoundError when importing services.loop-control
    import sys
    from unittest.mock import MagicMock
    mock_asyncpg = MagicMock()
    monkeypatch.setitem(sys.modules, "asyncpg", mock_asyncpg)

    # Now import LoopControllerStore and mock its list_records
    import importlib
    loop_control = importlib.import_module("services.loop-control")
    LoopControllerStore = loop_control.LoopControllerStore

    async def mock_list_records(self, tenant_id, env):
        now = datetime.now(timezone.utc)
        return [
            {
                "loop_id": "source_ingestion",
                "tenant_id": tenant_id,
                "environment": env,
                "controller_id": "ctrl-1",
                "controller_name": "SourceIngester",
                "deployment_sha": "sha-123",
                "desired_state_query": "SELECT *",
                "actual_state_query": "SELECT *",
                "desired_state": {
                    "present": True,
                    "source": "domain-desired-store",
                    "checked_at": now,
                },
                "downstream_actual_state": {
                    "status": "ready",
                    "source": "domain-terminal-store",
                    "checked_at": now,
                },
                "last_heartbeat_at": now,
                "last_tick_at": now,
                "last_success_at": now,
                "truth_level": "reconciled_live_proof",
                "lease_token": "db-store-fence-1",
                "lease_expires_at": now + timedelta(seconds=60),
                "evidence_refs": ["ref-1"],
                "payload": {}
            }
        ]

    monkeypatch.setattr(LoopControllerStore, "list_records", mock_list_records)
    with _loop_health_client() as client:
        response = client.get("/bff/v5/loop-health", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()

    # Assert that it successfully loaded from database and did not fall back
    assert payload["meta"]["surfaces"]["loop_health_snapshots"]["source"] == "controller_store"


def test_loop_health_db_store_merge_with_file_store(monkeypatch) -> None:
    from datetime import timedelta
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://dummy_user:dummy_pass@localhost:5432/dummy_db")

    # Mock asyncpg to prevent ModuleNotFoundError
    import sys
    from unittest.mock import MagicMock
    mock_asyncpg = MagicMock()
    monkeypatch.setitem(sys.modules, "asyncpg", mock_asyncpg)

    import importlib
    loop_control = importlib.import_module("services.loop-control")
    LoopControllerStore = loop_control.LoopControllerStore

    async def mock_list_records(self, tenant_id, env):
        now = datetime.now(timezone.utc)
        return [
            {
                "loop_id": "source_ingestion",
                "tenant_id": tenant_id,
                "environment": env,
                "controller_id": "ctrl-1",
                "controller_name": "SourceIngester",
                "deployment_sha": "sha-123",
                "desired_state_query": "SELECT *",
                "actual_state_query": "SELECT *",
                "desired_state": {
                    "present": True,
                    "source": "domain-desired-store",
                    "checked_at": now,
                },
                "downstream_actual_state": {
                    "status": "ready",
                    "source": "domain-terminal-store",
                    "checked_at": now,
                },
                "last_heartbeat_at": now,
                "last_tick_at": now,
                "last_success_at": now,
                "truth_level": "reconciled_live_proof",
                "lease_token": "db-store-fence-2",
                "lease_expires_at": now + timedelta(seconds=60),
                "evidence_refs": ["ref-1"],
                "payload": {}
            }
        ]

    monkeypatch.setattr(LoopControllerStore, "list_records", mock_list_records)

    loop_health_store = {
        "bff_health_monitoring": {
            "loop_id": "bff_health_monitoring",
            "truth_level": "scheduled_tick",
            "controller_health": {
                "status": "healthy",
                "controller_name": "bff-health-reconciler",
                "last_heartbeat_at": "2026-06-27T06:00:00Z",
            },
            "evidence_packet": {
                "packet_id": "packet-bff-health-001",
                "refs": ["ref-file-store"],
            },
        }
    }

    with _loop_health_client(loop_health_store=loop_health_store) as client:
        response = client.get("/bff/v5/loop-health", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    items = payload["items"]

    source_ingestion = next(item for item in items if item["loop_id"] == "source_ingestion")
    bff_health = next(item for item in items if item["loop_id"] == "bff_health_monitoring")

    assert source_ingestion["truth_source"]["source"] == "controller_store"
    assert source_ingestion["desired_state_presence"]["authoritative"] is True
    assert source_ingestion["desired_state_presence"]["present"] is True
    assert source_ingestion["downstream_actual_state"]["authoritative"] is True
    assert source_ingestion["downstream_actual_state"]["status"] == "ready"
    assert bff_health["truth_source"]["source"] == "service_store"
    assert bff_health["evidence_packet"]["packet_id"] == "packet-bff-health-001"


def test_loop_health_database_lookup_uses_authenticated_tenant_and_environment(
    monkeypatch,
) -> None:
    secret = "loop-health-tenant-scope-secret"
    issuer = "pantheon-loop-health-test"
    audience = "bff-operators"
    now = int(datetime.now(timezone.utc).timestamp())
    token = encode_jwt_hs256(
        {
            "sub": "loop-health-tenant-viewer",
            "roles": ["viewer"],
            "tenant_id": TENANT_ID,
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + 300,
        },
        secret=secret,
    )
    strict_headers = {"Authorization": f"Bearer {token}"}

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", issuer)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", audience)
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")
    monkeypatch.delenv("PANTHEON_BFF_JWKS_URI", raising=False)
    monkeypatch.delenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://dummy_user:dummy_pass@localhost:5432/dummy_db",
    )
    monkeypatch.setenv("PANTHEON_ENV", ENVIRONMENT)

    import importlib
    import sys
    from unittest.mock import MagicMock

    monkeypatch.setitem(sys.modules, "asyncpg", MagicMock())
    loop_control = importlib.import_module("services.loop-control")
    calls = []

    async def mock_list_records(self, tenant_id, environment):
        calls.append((tenant_id, environment))
        return [
            {
                "loop_id": "source_ingestion",
                "tenant_id": "tenant-other",
                "environment": environment,
            }
        ]

    monkeypatch.setattr(
        loop_control.LoopControllerStore,
        "list_records",
        mock_list_records,
    )
    with _loop_health_client() as client:
        response = client.get("/bff/v5/loop-health", headers=strict_headers)
        denied = client.get(
            "/bff/v5/loop-health",
            headers={**strict_headers, "X-Tenant-Id": "tenant-other"},
        )
        denied_environment = client.get(
            "/bff/v5/loop-health?environment=prod",
            headers=strict_headers,
        )

    assert response.status_code == 200, response.text
    assert response.json()["meta"]["scope"] == {
        "tenant_id": TENANT_ID,
        "environment": ENVIRONMENT,
        "source": "authenticated_identity_and_deployment_scope",
    }
    assert response.json()["meta"]["coverage"]["raw_health_record_count"] == 0
    assert calls == [(TENANT_ID, ENVIRONMENT)]
    assert denied.status_code == 403
    assert denied.json()["error"]["details"]["precondition_failed"] == "tenant_scope"
    assert denied_environment.status_code == 403
    assert (
        denied_environment.json()["error"]["details"]["precondition_failed"]
        == "environment_scope"
    )
