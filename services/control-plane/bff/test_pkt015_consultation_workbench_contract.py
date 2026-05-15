from __future__ import annotations

import os
import sys
import json
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main


OPERATOR_TOKEN = "Bearer op-2:operator"
EXAMPLE_PATH = Path(__file__).resolve().parents[3] / "docs" / "examples" / "PKT-consultation-workbench.json"


def test_pkt015_consultation_workbench_returns_truthful_overview_payload() -> None:
    client = TestClient(bff_main.app)

    response = client.get(
        "/api/v1/workbench/consultation",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["workbench_id"] == "consultation-workbench"
    assert payload["route_href"] == "/consultation"
    assert payload["overall_status"] == "partial_ready"
    assert payload["packet_family"]["family_id"] == "CW-008"
    assert payload["packet_family"]["lovable_readiness"] == "partial_ready"
    assert payload["module_counts"] == {"total": 4, "ready": 4, "not_ready": 0}
    assert [module["module_id"] for module in payload["modules"]] == [
        "CW-01",
        "CW-02",
        "CW-03",
        "CW-04",
    ]
    assert payload["modules"][0]["status"] == "ready"
    assert payload["modules"][1]["status"] == "ready"
    assert payload["modules"][1]["live_routes"] == [
        "GET /api/v1/consultations/{session_id}/transcript"
    ]
    assert payload["modules"][2]["status"] == "ready"
    assert payload["modules"][2]["live_routes"] == [
        "GET /api/v1/committees",
        "GET /api/v1/committees/{committee_id}",
        "POST /api/v1/operator/commands (RecordSponsorDecision)",
    ]
    assert payload["modules"][3]["status"] == "ready"
    assert payload["modules"][3]["live_routes"] == [
        "GET /api/v1/consult/memos",
        "GET /api/v1/consult/memos/{memo_id}",
    ]
    assert payload["support_refs"][1]["value"] == "/api/v1/personas/{persona_id}/consultations"
    assert payload["meta"]["surfaces"]["overview"]["status"] == "ok"
    assert payload["meta"]["surfaces"]["packet_family"]["status"] == "ok"


def test_pkt015_consultation_workbench_example_matches_builder() -> None:
    expected = bff_main._build_consultation_workbench_overview("2026-04-22T00:00:00Z")
    example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    assert example == expected
