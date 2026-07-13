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


HEADERS = {"Authorization": "Bearer loop-health-operator:operator,reviewer,admin:mfa"}


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
            snapshot_path.write_text(json.dumps(snapshot_payload), encoding="utf-8")
        env_overrides: Dict[str, str] = {}
        if loop_health_store is not None:
            health_path = root / "loop_health.json"
            health_path.write_text(json.dumps(loop_health_store), encoding="utf-8")
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
