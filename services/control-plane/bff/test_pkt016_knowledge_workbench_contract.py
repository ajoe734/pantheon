from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.research.router import create_research_router
from services.control_plane.bff.research.routes.knowledge import _build_knowledge_workbench_overview
from services.control_plane.bff.research.routes.common import ResearchRouteContext


OPERATOR_TOKEN = "Bearer op-2:operator"
EXAMPLE_PATH = Path(__file__).resolve().parents[3] / "docs" / "examples" / "PKT-knowledge-workbench.json"


def _bff_error(status_code: int, code: Any, message: str, reason: Optional[str] = None, **kwargs: Any) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": getattr(code, "value", str(code)), "message": message, "reason": reason or message, **kwargs}},
    )


def test_pkt016_knowledge_workbench_returns_truthful_overview_payload() -> None:
    app = FastAPI()
    router = create_research_router(
        get_read_store=lambda: None,
        extract_identity=lambda _: SimpleNamespace(operator_id="op-2", roles={"operator"}),
        require_read_role=lambda _: None,
        require_operator_role=lambda _: None,
        bff_error=_bff_error,
        utc_now=lambda: "2026-04-22T00:00:00Z",
    )
    app.include_router(router)
    client = TestClient(app)

    response = client.get(
        "/api/v1/workbench/knowledge",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["workbench_id"] == "knowledge-workbench"
    assert payload["route_href"] == "/knowledge"
    assert payload["overall_status"] == "overview_ready"
    assert payload["packet_family"]["family_id"] == "KW-006"
    assert payload["module_counts"] == {"total": 5, "ready": 5, "not_ready": 0}
    assert [module["module_id"] for module in payload["modules"]] == [
        "KW-01",
        "KW-02",
        "KW-03",
        "KW-04",
        "KW-05",
    ]
    assert payload["modules"][0]["status"] == "ready"
    assert payload["modules"][0]["missing_contracts"] == []
    assert payload["modules"][1]["status"] == "ready"
    assert payload["modules"][2]["status"] == "ready"
    assert payload["modules"][3]["status"] == "ready"
    assert payload["modules"][4]["status"] == "ready"
    assert payload["modules"][4]["live_routes"][0] == "GET /api/v1/knowledge/strategy-specs"
    assert payload["support_refs"][3]["value"] == "/memory/retrieve"
    assert payload["meta"]["surfaces"]["overview"]["status"] == "ok"
    assert payload["meta"]["surfaces"]["packet_family"]["status"] == "ok"


def test_pkt016_knowledge_workbench_example_matches_builder() -> None:
    ctx = ResearchRouteContext(
        get_read_store=lambda: None,
        extract_identity=lambda _: None,
        require_read_role=lambda _: None,
        bff_error=_bff_error,
        utc_now=lambda: "2026-04-22T00:00:00Z",
    )
    expected = _build_knowledge_workbench_overview(ctx, "2026-04-22T00:00:00Z")
    example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    assert example == expected
