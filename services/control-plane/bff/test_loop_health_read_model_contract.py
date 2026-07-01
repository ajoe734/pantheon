from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional
from unittest.mock import patch

from fastapi.testclient import TestClient


BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
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
    assert len(payload["items"]) == 12
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

    source_loop = next(item for item in payload["items"] if item["loop_id"] == "source_ingestion")
    assert source_loop["current_maturity"] == "api-only"
    assert source_loop["target_maturity"] == "reconciled"
    assert source_loop["controller_health"]["status"] == "not_implemented"
    assert source_loop["last_success"] is None
    assert source_loop["last_failure"] is None

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
    assert data["controller_health"]["status"] == "healthy"
    assert data["controller_health"]["source"] == "service_store"
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
    assert data["controller_health"]["source"] == "local_snapshot"
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


def test_loop_health_service_store_live_truth_is_separate_from_registry(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    loop_health_store = {
        "bff_health_monitoring": {
            "loop_id": "bff_health_monitoring",
            "truth_level": "reconciled_live_proof",
            "truth_status": "present",
            "truth_note": "BFF health controller reconciled desired and actual state.",
            "controller_health": {
                "status": "healthy",
                "controller_name": "bff-health-reconciler",
                "last_heartbeat_at": "2026-06-27T06:20:00Z",
            },
            "evidence_packet": {
                "packet_id": "packet-bff-health-live-001",
                "refs": ["docs/deployment/evidence/loop-auto-bff-003/live-truth.json"],
            },
        }
    }
    with _loop_health_client(loop_health_store=loop_health_store) as client:
        response = client.get("/bff/v5/loop-health/bff_health_monitoring", headers=HEADERS)

    assert response.status_code == 200, response.text
    packet = response.json()["data"]["evidence_packet"]
    assert packet["highest_truth_level"] == "reconciled_live_proof"
    assert packet["accepted_live_liveness"] is True
    assert packet["operator_truth"]["truth_level"] == "reconciled_live_proof"
    assert packet["operator_truth"]["label"] == "Reconciled live truth"
    assert packet["operator_truth"]["source_type"] == "live_truth"
    assert packet["operator_truth"]["accepted_as_live"] is True
    assert packet["operator_truth"]["degraded"] is False
    assert _truth_source(packet, "registry_metadata")["source_type"] == "registry"
    assert _truth_source(packet, "reconciled_live_proof")["source_type"] == "live_truth"


def test_loop_health_detail_unknown_id_is_404(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _loop_health_client() as client:
        response = client.get("/bff/v5/loop-health/not-a-loop", headers=HEADERS)

    assert response.status_code == 404, response.text
