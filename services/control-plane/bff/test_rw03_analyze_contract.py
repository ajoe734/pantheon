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


def test_rw03_list_contract_returns_backend_grouped_analysis_projection() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/research/analysis?ticket_id=rt-20260419-007&status=completed",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["page_info"]["total"] == 2
        assert [item["analysis_id"] for item in payload["data"]] == [
            "analysis-20260419-007-a",
            "analysis-20260418-004-b",
        ]
        assert payload["data"][0]["metric_group_refs"] == [
            "performance",
            "drawdown",
            "signal_quality",
        ]
        assert payload["data"][0]["summary"]["verdict"] == "candidate_update"
        assert payload["meta"]["surfaces"]["analysis_results"] in {"fresh", "stale", "degraded"}


def test_rw03_detail_contract_returns_metric_groups_and_comparative_summary() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/research/analysis/analysis-20260419-007-a",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["analysis_id"] == "analysis-20260419-007-a"
        assert payload["metric_groups"][0]["group_key"] == "performance"
        assert payload["metric_groups"][0]["metrics"][0]["metric_key"] == "annualized_return"
        assert payload["metric_groups"][1]["metrics"][0]["delta_display"] == "-1.4 pts"
        assert payload["comparative_summary"]["baseline_analysis_id"] == "analysis-20260418-004-b"
        assert payload["comparative_summary"]["comparisons"][0]["delta_highlights"][0]["direction"] == "improved"
        assert payload["links"] == {
            "self": "/api/v1/research/analysis/analysis-20260419-007-a",
            "workbench_detail": "/research/analyze/analysis-20260419-007-a",
            "linked_ticket_detail": "/research/tickets/rt-20260419-007",
            "linked_experiment_detail": "/research/experiments/exp-20260419-012",
        }
        assert payload["meta"]["surfaces"]["analysis_results"] in {"fresh", "stale", "degraded"}


def test_rw03_list_rejects_invalid_status_filter() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/research/analysis?status=archived",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 422, response.text
        payload = response.json()
        assert payload["detail"]["error"]["code"] == "INVALID_PARAMS"
        assert payload["detail"]["error"]["details"]["precondition_failed"] == "status"
