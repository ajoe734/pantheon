from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.control_plane.bff.ports.research_knowledge_source import (
    DefaultResearchKnowledgeSourcePort,
)
from services.control_plane.bff.research.router import create_research_router
from services.control_plane.bff.research.service import ResearchRouterService


OPERATOR_AUTH = "Bearer test-operator:operator"


class _AnalysisPortDouble(DefaultResearchKnowledgeSourcePort):
    """Typed RW-03 double that preserves route-compatible fallback flags."""

    def __init__(self, records: dict[str, dict], *, source: str) -> None:
        super().__init__(research_analyses_store=records)
        self._source = source

    def dataset_source(self, dataset: str, **_: object) -> str:
        if dataset == "research_analyses":
            return self._source
        return super().dataset_source(dataset)

    def list_research_analyses(self, **kwargs: object) -> list[dict]:
        kwargs.pop("include_snapshot_fallback", None)
        kwargs.pop("include_local_fallback", None)
        return super().list_research_analyses(**kwargs)

    def get_research_analysis(self, analysis_id: str, **_: object) -> dict | None:
        return super().get_research_analysis(analysis_id)


def _extract_identity(authorization: Optional[str]) -> Optional[Any]:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer "):].strip()
    if ":" in token:
        op_id, roles_str = token.split(":", 1)
        roles = {r.strip() for r in roles_str.split(",") if r.strip()}
    else:
        op_id = token
        roles = {"operator", "viewer"}
    return SimpleNamespace(operator_id=op_id, roles=roles)


def _bff_error(
    status_code: int,
    code: Any,
    message: str,
    reason: Optional[str] = None,
    **kwargs: Any,
) -> HTTPException:
    error_dict = {
        "code": getattr(code, "value", str(code)),
        "message": message,
        "reason": reason or message,
        "details": kwargs if kwargs else {"reason": reason or message},
    }
    return HTTPException(status_code=status_code, detail={"error": error_dict})


def _create_test_app(port: _AnalysisPortDouble) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(HTTPException)
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request, exc):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})

    service = ResearchRouterService(
        port_getter=lambda: port,
        utc_now=lambda: "2026-04-20T03:10:00Z",
        snapshot_meta=lambda stamp, **kw: {"snapshot_at": stamp, **kw},
        page_slice=lambda items, token=None, size=20: (list(items[:size]), None),
    )

    router = create_research_router(
        read_surface=lambda: port,
        extract_identity=_extract_identity,
        require_read_role=lambda ident: None,
        require_operator_role=lambda ident: None,
        bff_error=_bff_error,
        utc_now=lambda: "2026-04-20T03:10:00Z",
        service=service,
    )
    app.include_router(router)
    return app


@contextmanager
def _seeded_client():
    port = _AnalysisPortDouble({}, source="local_snapshot")
    app = _create_test_app(port)
    client = TestClient(app, raise_server_exceptions=False)
    yield client


@contextmanager
def _service_backed_client():
    tracked_env = {
        "PANTHEON_BFF_RESEARCH_ANALYSIS_STORE": os.environ.get(
            "PANTHEON_BFF_RESEARCH_ANALYSIS_STORE"
        ),
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        analysis_store = root / "research_analyses.json"
        analysis_store.write_text(
            json.dumps(
                {
                    "analysis-service-001": {
                        "analysis_id": "analysis-service-001",
                        "ticket_id": "rt-service-001",
                        "experiment_id": "exp-service-001",
                        "status": "completed",
                        "run_at": "2026-04-20T03:10:00Z",
                        "completed_at": "2026-04-20T03:12:00Z",
                        "summary": {
                            "headline": "Service-backed analysis wins over local fallback",
                            "verdict": "hold",
                        },
                        "metric_groups": [
                            {
                                "group_key": "performance",
                                "label": "Performance",
                                "description": "Service-backed projection",
                                "metrics": [
                                    {
                                        "metric_key": "sharpe_ratio",
                                        "label": "Sharpe ratio",
                                        "value": 1.48,
                                        "unit": "ratio",
                                        "display_value": "1.48",
                                        "direction": "higher_is_better",
                                        "baseline_value": 1.32,
                                        "delta_value": 0.16,
                                        "delta_display": "+0.16",
                                    }
                                ],
                            }
                        ],
                        "comparative_summary": {
                            "baseline_analysis_id": "analysis-service-000",
                            "basis": "Service-backed baseline comparison.",
                            "comparisons": [
                                {
                                    "analysis_id": "analysis-service-000",
                                    "label": "Previous baseline",
                                    "delta_highlights": [
                                        {
                                            "metric_key": "sharpe_ratio",
                                            "direction": "improved",
                                            "delta_display": "+0.16",
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        os.environ["PANTHEON_BFF_RESEARCH_ANALYSIS_STORE"] = str(analysis_store)

        port = _AnalysisPortDouble(
            json.loads(analysis_store.read_text(encoding="utf-8")),
            source="service_client",
        )
        app = _create_test_app(port)
        client = TestClient(app, raise_server_exceptions=False)
        try:
            yield client
        finally:
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


@contextmanager
def _unavailable_client():
    port = _AnalysisPortDouble({}, source="missing")
    app = _create_test_app(port)
    client = TestClient(app, raise_server_exceptions=False)
    yield client


def test_rw03_list_contract_returns_backend_grouped_analysis_projection() -> None:
    with _service_backed_client() as client:
        response = client.get(
            "/api/v1/research/analysis?ticket_id=rt-service-001&status=completed",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["page_info"]["total"] == 1
        assert [item["analysis_id"] for item in payload["data"]] == ["analysis-service-001"]
        assert payload["data"][0]["metric_group_refs"] == ["performance"]
        assert payload["data"][0]["summary"]["verdict"] == "hold"
        assert payload["meta"]["surfaces"]["analysis_results"] == {
            "status": "ok",
            "source": "service_client",
        }


def test_rw03_detail_contract_returns_metric_groups_and_comparative_summary() -> None:
    with _service_backed_client() as client:
        response = client.get(
            "/api/v1/research/analysis/analysis-service-001",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["analysis_id"] == "analysis-service-001"
        assert payload["metric_groups"][0]["group_key"] == "performance"
        assert payload["metric_groups"][0]["metrics"][0]["metric_key"] == "sharpe_ratio"
        assert payload["metric_groups"][0]["metrics"][0]["delta_display"] == "+0.16"
        assert payload["comparative_summary"]["baseline_analysis_id"] == "analysis-service-000"
        assert payload["comparative_summary"]["comparisons"][0]["delta_highlights"][0]["direction"] == "improved"
        assert payload["links"] == {
            "self": "/api/v1/research/analysis/analysis-service-001",
            "workbench_detail": "/research/analyze/analysis-service-001",
            "linked_ticket_detail": "/research/tickets/rt-service-001",
            "linked_experiment_detail": "/research/experiments/exp-service-001",
        }
        assert payload["meta"]["surfaces"]["analysis_results"] == {
            "status": "ok",
            "source": "service_client",
        }


def test_rw03_list_rejects_invalid_status_filter() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/research/analysis?status=archived",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 422, response.text
        payload = response.json()
        assert payload["error"]["code"] == "VALIDATION_FAILED"
        assert payload["error"]["details"]["precondition_failed"] == "status"


def test_rw03_service_backed_reads_override_seeded_snapshot() -> None:
    with _service_backed_client() as client:
        list_response = client.get(
            "/api/v1/research/analysis",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert list_response.status_code == 200, list_response.text

        payload = list_response.json()
        assert [item["analysis_id"] for item in payload["data"]] == ["analysis-service-001"]
        assert payload["meta"]["surfaces"]["analysis_results"] == {
            "status": "ok",
            "source": "service_client",
        }
        assert payload["data"][0]["metric_group_refs"] == ["performance"]

        detail_response = client.get(
            "/api/v1/research/analysis/analysis-service-001",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert detail_response.status_code == 200, detail_response.text

        detail = detail_response.json()
        assert detail["summary"]["headline"] == "Service-backed analysis wins over local fallback"
        assert detail["comparative_summary"]["baseline_analysis_id"] == "analysis-service-000"
        assert detail["meta"]["surfaces"]["analysis_results"] == {
            "status": "ok",
            "source": "service_client",
        }


def test_rw03_detail_does_not_fall_back_to_local_snapshot() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/research/analysis/analysis-20260419-007-a",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 404, response.text


def test_rw03_list_reports_unavailable_without_service_or_snapshot_fallback() -> None:
    with _unavailable_client() as client:
        response = client.get(
            "/api/v1/research/analysis",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["data"] == []
        assert payload["page_info"] == {
            "next_page_token": None,
            "total": 0,
        }
        assert payload["meta"]["surfaces"]["analysis_results"]["status"] == "unavailable"
        assert payload["meta"]["surfaces"]["analysis_results"]["source"] == "missing"
