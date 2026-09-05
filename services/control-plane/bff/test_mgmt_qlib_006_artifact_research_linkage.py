"""MGMT-QLIB-006 contract tests for Management artifact / research linkage."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient


BFF_DIR = Path(__file__).resolve().parent
REPO_ROOT = BFF_DIR.parents[2]
LINKAGE_PACKET_PATH = (
    REPO_ROOT / "support" / "evidence" / "MGMT-QLIB-006" / "management_linkage_packet.json"
)


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort  # noqa: E402
from services.control_plane.bff.ports import create_read_surface_ports  # noqa: E402


HEADERS = {"Authorization": "Bearer op-mgmt-qlib:operator,reviewer"}


def _load_linkage_packet() -> dict:
    return json.loads(LINKAGE_PACKET_PATH.read_text(encoding="utf-8"))


def _seed_qlib_management_linkage() -> tuple[Any, dict]:
    packet = _load_linkage_packet()
    artifact_id = packet["artifact_id"]
    experiment_id = packet["experiment_id"]
    strategy_id = packet["strategy_id"]
    linkage = packet["research_linkage"]

    strategy_specs = {
        strategy_id: {
        "strategy_id": strategy_id,
        "current_spec_version_id": packet["strategy_spec_id"],
        "title": "TW Cross-Sectional Equity Alpha",
        "source_kind": "research",
        "lifecycle_state": "candidate",
        "updated_at": "2026-05-15T17:30:00Z",
        "versions": [
            {
                "spec_version_id": packet["strategy_spec_id"],
                "spec_version": "v1",
                "lifecycle_state": "candidate",
                "title": "Qlib TW Cross-Sectional Alpha StrategySpec",
                "source_kind": "research",
                "updated_at": "2026-05-15T17:30:00Z",
            }
        ],
        }
    }
    research_experiments = {
        experiment_id: {
            "experiment_id": experiment_id,
            "ticket_id": "rt-mgmt-qlib-006",
            "experiment_name": "MGMT-QLIB-006 Qlib admission linkage",
            "status": "completed",
            "queued_at": "2026-05-15T17:25:00Z",
            "started_at": "2026-05-15T17:26:00Z",
            "completed_at": "2026-05-15T17:30:00Z",
            "strategy_selector": {"strategy_id": strategy_id},
            "parameter_set": {"model_family": "lightgbm"},
            "run_config": {
                "backend": "qlib",
                "dataset_ref": linkage["dataset_manifest_id"],
                "execution_mode": "offline_admission_review",
            },
            "launch_context": {"task_id": "MGMT-QLIB-006"},
            "validation_warnings": [],
            "artifact_ids": [artifact_id],
            "failure": {"reason_code": None, "message": None},
        }
    }
    research_artifacts = {
        artifact_id: {
            "artifact_id": artifact_id,
            "lineage_id": packet["lineage_id"],
            "version": 1,
            "parent_artifact_id": None,
            "status": "pending",
            "name": "Qlib TW cross-sectional alpha draft model",
            "artifact_type": "model_artifact",
            "description": "Management-readable Qlib admission candidate linkage.",
            "produced_by_experiment_id": experiment_id,
            "linked_ticket_id": "rt-mgmt-qlib-006",
            "linked_strategy_id": strategy_id,
            "created_at": "2026-05-15T17:30:00Z",
            "sealed_at": None,
            "metrics": {
                "sharpe_ratio": None,
                "sortino_ratio": None,
                "max_drawdown": None,
                "annualized_return": None,
            },
            "parameters": {"model_family": "lightgbm", "framework": "qlib"},
            "linked_strategy_id": strategy_id,
            "research_linkage": linkage,
            "experiment_refs": [
                {
                    "experiment_id": experiment_id,
                    "backend": "qlib",
                    "route": f"/bff/research-experiments/{experiment_id}",
                }
            ],
            "metadata": {"research_linkage": linkage},
            "provenance": {
                "linked_experiment": {
                    "experiment_id": experiment_id,
                    "display_label": "MGMT-QLIB-006 Qlib admission linkage",
                },
                "linked_ticket": {
                    "ticket_id": "rt-mgmt-qlib-006",
                    "title": "Management artifact / research linkage",
                },
                "experiment_refs": [
                    {
                        "experiment_id": experiment_id,
                        "backend": "qlib",
                        "route": f"/bff/research-experiments/{experiment_id}",
                    }
                ],
            },
        }
    }
    rks_port = DefaultResearchKnowledgeSourcePort(
        strategy_specs_store=strategy_specs,
        research_experiments_store=research_experiments,
        research_artifacts_store=research_artifacts,
    )
    store = create_read_surface_ports(research_knowledge_source=rks_port)
    projected_detail = rks_port.get_research_artifact(artifact_id)
    assert projected_detail is not None
    projected_detail.update(
        {
            "linked_strategy_id": strategy_id,
            "research_linkage": linkage,
            "experiment_refs": research_artifacts[artifact_id]["experiment_refs"],
        }
    )
    projected_summary = {
        **rks_port.list_research_artifacts()[0],
        "linked_strategy_id": strategy_id,
        "research_linkage": linkage,
        "experiment_refs": research_artifacts[artifact_id]["experiment_refs"],
    }
    store.get_research_artifact = lambda requested_id: (
        json.loads(json.dumps(projected_detail))
        if requested_id == artifact_id
        else None
    )
    store.list_research_artifacts = lambda **_kwargs: [
        json.loads(json.dumps(projected_summary))
    ]
    return store, packet


@contextmanager
def _seeded_client() -> Iterator[tuple[TestClient, dict]]:
    original_store = bff_main.read_store
    store, packet = _seed_qlib_management_linkage()
    bff_main.read_store = store
    try:
        yield TestClient(bff_main.app), packet
    finally:
        bff_main.read_store = original_store


def _data(payload: dict) -> dict:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _assert_qlib_linkage(linkage: dict, packet: dict) -> None:
    assert linkage["framework"] == "qlib"
    assert linkage["strategy_id"] == packet["strategy_id"]
    assert linkage["strategy_spec_id"] == packet["strategy_spec_id"]
    assert linkage["dataset_manifest_id"].startswith("qlib-dataset-manifest:")
    assert "MGMT-QLIB-001" in linkage["source_task_ids"]
    assert "MGMT-QLIB-002" in linkage["source_task_ids"]
    assert "MGMT-QLIB-004" in linkage["source_task_ids"]
    assert "MGMT-QLIB-005" in linkage["pending_task_ids"]

    evidence_refs = {ref["ref_type"]: ref for ref in linkage["evidence_refs"]}
    assert evidence_refs["dataset_manifest"]["ref"] == (
        "support/evidence/MGMT-QLIB-001/dataset_manifest.json"
    )
    assert evidence_refs["strategy_spec_packet"]["ref"] == (
        "support/evidence/MGMT-QLIB-002/strategy_spec_packet.json"
    )

    artifact_ref_names = {ref["artifact_name"] for ref in linkage["artifact_refs"]}
    assert {"model_artifact", "evaluation_report", "candidate_packet"}.issubset(
        artifact_ref_names
    )

    safety = linkage["safety_assertions"]
    assert safety["registry_write_performed"] is False
    assert safety["registry_write_authority"] == "registry_service_only"
    assert safety["broker_session_opened"] is False
    assert safety["order_route"] == "none"
    assert safety["deployment_stage"] == "none"
    assert safety["live_capital_side_effects"] is False


def test_api_artifact_detail_exposes_qlib_research_linkage() -> None:
    with _seeded_client() as (client, packet):
        response = client.get(
            f"/api/v1/artifacts/{packet['artifact_id']}",
            headers=HEADERS,
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["artifact_id"] == packet["artifact_id"]
    assert payload["experiment_refs"][0]["backend"] == "qlib"
    _assert_qlib_linkage(payload["research_linkage"], packet)
    assert payload["research_linkage"]["management_routes"]["artifact_detail"] == (
        f"/bff/artifacts/{packet['artifact_id']}"
    )


def test_bff_artifact_and_strategy_routes_expose_qlib_research_linkage() -> None:
    with _seeded_client() as (client, packet):
        artifact = client.get(f"/bff/artifacts/{packet['artifact_id']}", headers=HEADERS)
        strategy_artifacts = client.get(
            f"/bff/strategies/{packet['strategy_id']}/artifacts",
            headers=HEADERS,
        )

    assert artifact.status_code == 200, artifact.text
    artifact_record = _data(artifact.json())
    assert artifact_record["artifact_id"] == packet["artifact_id"]
    _assert_qlib_linkage(artifact_record["research_linkage"], packet)

    assert strategy_artifacts.status_code == 200, strategy_artifacts.text
    items = strategy_artifacts.json()["items"]
    assert [item["artifact_id"] for item in items] == [packet["artifact_id"]]
    _assert_qlib_linkage(items[0]["research_linkage"], packet)
