from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


OPERATOR_AUTH = "Bearer test-operator:operator"


@contextmanager
def _seeded_client():
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store


def test_rw05_list_contract_returns_artifact_registry_projection() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/artifacts?ticket_id=tkt_5432&page_size=20",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["total_count"] == 3
        assert [item["artifact_id"] for item in payload["artifacts"]] == [
            "art_2024_abc123",
            "art_2024_abc122",
            "art_2024_abc121",
        ]
        assert payload["artifacts"][0]["is_current_version"] is True
        assert payload["artifacts"][1]["is_current_version"] is False
        assert payload["artifacts"][0]["allowedActions"] == {"canCompare": True}
        assert payload["artifacts"][1]["allowedActions"] == {"canCompare": True}
        assert payload["meta"]["surfaces"]["artifact_list"] in {"ok", "degraded"}


def test_rw05_list_contract_returns_non_comparable_authority_for_pending_artifacts() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/artifacts?status=pending&page_size=20",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["total_count"] == 1
        assert payload["artifacts"][0]["artifact_id"] == "art_2024_pending01"
        assert payload["artifacts"][0]["allowedActions"] == {"canCompare": False}


def test_rw05_detail_contract_returns_version_chain_and_allowed_actions() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/artifacts/art_2024_abc123",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["artifact_id"] == "art_2024_abc123"
        assert payload["parent_artifact_id"] == "art_2024_abc122"
        assert [item["version"] for item in payload["version_chain"]] == [1, 2, 3]
        assert payload["provenance"]["linked_ticket"]["ticket_id"] == "tkt_5432"
        assert payload["allowedActions"] == {
            "canCompare": True,
            "canViewDetail": True,
        }
        assert payload["meta"]["surfaces"]["artifact_detail"] in {"ok", "degraded"}


def test_rw05_compare_contract_returns_backend_composed_diff() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/artifacts/compare?artifact_ids=art_2024_abc121,art_2024_abc123",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert len(payload["artifacts"]) == 2
        sharpe_pair = next(
            pair for pair in payload["field_pairs"]
            if pair["field_key"] == "metrics.sharpe_ratio"
        )
        assert sharpe_pair["group"] == "performance"
        assert sharpe_pair["change_label"] == "improved"
        assert sharpe_pair["delta_direction"] == "up"
        assert payload["change_summary"]["total_fields_compared"] >= 10
        assert payload["provenance_pairs"][1]["linked_experiment"]["experiment_id"] == "exp_9876"
        assert payload["meta"]["surfaces"]["artifact_compare"] in {"ok", "degraded"}


def test_rw05_compare_rejects_non_comparable_artifacts() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/artifacts/compare?artifact_ids=art_2024_abc123,art_2024_pending01",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 422, response.text

        payload = response.json()
        assert payload["error"]["code"] == "INVALID_STATE"
        assert payload["non_comparable_artifacts"] == [
            {
                "artifact_id": "art_2024_pending01",
                "status": "pending",
                "reason": "Only sealed and superseded artifacts may be compared.",
            }
        ]


def test_rw05_compare_rejects_invalid_cardinality() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/artifacts/compare?artifact_ids=art_2024_abc123",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["error"]["code"] == "INVALID_PARAMS"
