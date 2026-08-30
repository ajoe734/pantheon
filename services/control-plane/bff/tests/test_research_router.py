"""Standalone contract tests for the prepared typed Research router."""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from research.router import create_research_router  # noqa: E402


class _Port:
    def __init__(self, *, source: str = "typed_store") -> None:
        self.source = source
        self.analyses: Dict[str, Dict[str, Any]] = {
            "analysis-1": {
                "analysis_id": "analysis-1",
                "ticket_id": "ticket-1",
                "experiment_id": "experiment-1",
                "status": "completed",
                "run_at": "2026-08-30T00:00:00Z",
                "summary": {"verdict": "hold"},
            }
        }
        self.artifacts: Dict[str, Dict[str, Any]] = {
            "artifact-1": {
                "artifact_id": "artifact-1",
                "status": "sealed",
                "allowedActions": {"canCompare": True},
            },
            "artifact-2": {
                "artifact_id": "artifact-2",
                "status": "superseded",
                "allowedActions": {"canCompare": True},
            },
        }

    def dataset_source(self, _dataset: str) -> str:
        return self.source

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, source: str, has_data: bool) -> Dict[str, Any]:
        if source == "missing" or not has_data:
            return {"status": "unavailable", "source": source, "snapshot_at": snapshot_at}
        return {"status": "ok", "source": source}

    def list_research_analyses(self, *, ticket_id=None, experiment_id=None, statuses=None, date_range=None) -> List[Dict[str, Any]]:
        records = list(self.analyses.values())
        if ticket_id:
            records = [item for item in records if item["ticket_id"] == ticket_id]
        if experiment_id:
            records = [item for item in records if item["experiment_id"] == experiment_id]
        if statuses:
            records = [item for item in records if item["status"] in statuses]
        return records

    def get_research_analysis(self, analysis_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.analyses.get(str(analysis_id))

    def list_research_artifacts(self, **_filters: Any) -> List[Dict[str, Any]]:
        return list(self.artifacts.values())

    def get_research_artifact(self, artifact_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.artifacts.get(str(artifact_id))

    def compare_research_artifacts(self, artifact_ids: List[str]) -> Dict[str, Any]:
        return {"artifacts": [self.artifacts[artifact_id] for artifact_id in artifact_ids], "comparisons": []}


def _bff_error(status_code, code, message, reason, **extra):
    return HTTPException(
        status_code=status_code,
        detail={"code": code.value, "message": message, "reason": reason, **extra},
    )


def _client(port: _Port) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_research_router(
            get_read_store=lambda: port,
            extract_identity=lambda _authorization: object(),
            require_read_role=lambda _identity: None,
            bff_error=_bff_error,
            utc_now=lambda: "2026-08-30T00:00:00Z",
            include_prepared_subrouters=False,
        )
    )
    return TestClient(app)


def test_typed_analysis_routes_use_durable_port_and_preserve_links() -> None:
    client = _client(_Port())

    listed = client.get("/api/v1/research/analyses", params={"status": "completed"})
    assert listed.status_code == 200
    assert listed.json()["data"][0]["links"]["self"] == "/api/v1/research/analyses/analysis-1"

    detail = client.get("/api/v1/research/analyses/analysis-1")
    assert detail.status_code == 200
    assert detail.json()["links"]["linked_experiment_detail"] == "/research/experiments/experiment-1"


def test_typed_analysis_validation_and_unavailable_surface_are_explicit() -> None:
    invalid = _client(_Port()).get("/api/v1/research/analyses", params={"status": "archived"})
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["precondition_failed"] == "status"

    unavailable = _client(_Port(source="missing")).get("/api/v1/research/analyses")
    assert unavailable.status_code == 200
    assert unavailable.json()["data"] == []
    assert unavailable.json()["meta"]["surfaces"]["analysis_results"]["status"] == "unavailable"

    compat = _client(_Port()).get("/api/v1/research/analysis/analysis-1")
    assert compat.status_code == 200
    assert compat.json()["links"]["self"] == "/api/v1/research/analysis/analysis-1"


def test_typed_artifact_and_bff_replacement_routes_are_backed_by_same_port() -> None:
    client = _client(_Port())

    detail = client.get("/api/v1/research/artifacts/artifact-1")
    assert detail.status_code == 200
    assert detail.json()["artifact_id"] == "artifact-1"

    bff_detail = client.get("/bff/artifacts/artifact-1")
    assert bff_detail.status_code == 200
    assert bff_detail.json()["data"]["artifact_id"] == "artifact-1"

    comparison = client.get("/api/v1/research/artifacts/compare", params={"artifact_ids": "artifact-1,artifact-2"})
    assert comparison.status_code == 200
    assert [item["artifact_id"] for item in comparison.json()["artifacts"]] == ["artifact-1", "artifact-2"]

    non_comparable_port = _Port()
    non_comparable_port.artifacts["artifact-1"]["allowedActions"] = {"canCompare": False}
    rejected = _client(non_comparable_port).get(
        "/api/v1/research/artifacts/compare",
        params={"artifact_ids": "artifact-1,artifact-2"},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "OPERATION_NOT_ALLOWED"
