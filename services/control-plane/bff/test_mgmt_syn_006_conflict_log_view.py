"""MGMT-SYN-006 contract tests for the Management synthesis conflict log view."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from fastapi.testclient import TestClient


BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402


HEADERS = {"Authorization": "Bearer op-mgmt-syn:operator,reviewer,admin:mfa"}

PROOF_BUNDLE = {
    "allocation_policy_artifact": {
        "artifact_id": "alloc-policy-mgmt-syn-006-001",
        "capital_pool_id": "capital-pool-paper-001",
        "scope_ref": "paper",
        "sponsor_persona_id": "persona-tw-momentum",
        "synthesis_method": "weighted_fusion",
        "target_weights": {"2330.TW": 0.4, "0050.TW": 0.3},
        "constraints_bundle": {"environment": "paper"},
        "risk_budget": 0.35,
        "provenance_refs": [
            "pap-mgmt-syn-006-alpha",
            "pap-mgmt-syn-006-beta",
            "pap-mgmt-syn-006-gamma",
        ],
        "conflict_resolution_log_id": "conflict-log-mgmt-syn-006-001",
        "created_at": "2026-05-15T15:45:00Z",
    },
    "conflict_resolution_log": {
        "log_id": "conflict-log-mgmt-syn-006-001",
        "capital_pool_id": "capital-pool-paper-001",
        "scope_ref": "paper",
        "timestamp": "2026-05-15T15:45:00Z",
        "proposal_ids": [
            "pap-mgmt-syn-006-alpha",
            "pap-mgmt-syn-006-beta",
            "pap-mgmt-syn-006-gamma",
        ],
        "vetoed_proposals": [
            {
                "proposal_id": "pap-mgmt-syn-006-gamma",
                "persona_id": "persona-leverage-skeptic",
                "reason": "forbidden_strategy_family",
                "detail": "leveraged_short is forbidden for the paper pool",
            }
        ],
        "weighting_inputs": {
            "pap-mgmt-syn-006-alpha": 0.58,
            "pap-mgmt-syn-006-beta": 0.27,
        },
        "weighting_outputs": {
            "pap-mgmt-syn-006-alpha": 0.68,
            "pap-mgmt-syn-006-beta": 0.32,
        },
        "committee_ref": None,
        "sponsor_persona_id": "persona-tw-momentum",
        "rejected_reason": None,
        "synthesis_method": "weighted_fusion",
    },
    "governance_approval_packet": {
        "approval_decision_id": "approval-mgmt-syn-006-paper-001",
        "decision": "approved_for_paper_evidence",
        "decision_state": "decided",
        "can_proceed": True,
        "risk_level": "medium",
        "evidence_refs": [
            {
                "ref_id": "conflict-log-mgmt-syn-006-001",
                "ref_type": "conflict_resolution_log",
                "path": "support/evidence/MGMT-SYN-006/synthesis-proof.json#/conflict_resolution_log",
            }
        ],
    },
}

COMMITTEE_LOG = {
    "log_id": "conflict-log-mgmt-syn-006-committee",
    "capital_pool_id": "capital-pool-live-shadow",
    "scope_ref": "shadow",
    "timestamp": "2026-05-15T16:00:00Z",
    "proposal_ids": ["pap-committee-a", "pap-committee-b"],
    "vetoed_proposals": [],
    "weighting_inputs": {"pap-committee-a": 0.5, "pap-committee-b": 0.5},
    "weighting_outputs": {},
    "committee_ref": "committee-risk-001",
    "sponsor_persona_id": None,
    "rejected_reason": None,
    "synthesis_method": "committee_override",
}


@contextmanager
def _conflict_log_client(
    monkeypatch,
    *,
    payload: Optional[object] = None,
) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
        monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
        monkeypatch.delenv("PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED", raising=False)
        if payload is not None:
            store_path = Path(td) / "synthesis_conflict_logs.json"
            store_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            monkeypatch.setenv("PANTHEON_BFF_SYNTHESIS_CONFLICT_LOG_STORE", str(store_path))
        else:
            monkeypatch.delenv("PANTHEON_BFF_SYNTHESIS_CONFLICT_LOG_STORE", raising=False)
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        try:
            yield TestClient(bff_main.app, raise_server_exceptions=False)
        finally:
            bff_main.read_store = original_store


def test_conflict_log_list_and_detail_project_management_view(monkeypatch) -> None:
    with _conflict_log_client(monkeypatch, payload={"items": [PROOF_BUNDLE, COMMITTEE_LOG]}) as client:
        listed = client.get("/bff/synthesis/conflict-logs", headers=HEADERS)
        detail = client.get(
            "/bff/synthesis/conflict-logs/conflict-log-mgmt-syn-006-001",
            headers=HEADERS,
        )

    assert listed.status_code == 200, listed.text
    listed_payload = listed.json()
    assert listed_payload["page_info"]["total"] == 2
    assert [item["log_id"] for item in listed_payload["items"]] == [
        "conflict-log-mgmt-syn-006-committee",
        "conflict-log-mgmt-syn-006-001",
    ]
    assert listed_payload["meta"]["surfaces"]["synthesis_conflict_logs"]["source"] == "service_store"

    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["log_id"] == "conflict-log-mgmt-syn-006-001"
    assert data["allocation_policy_artifact_id"] == "alloc-policy-mgmt-syn-006-001"
    assert data["governance_approval_id"] == "approval-mgmt-syn-006-paper-001"
    assert data["resolution_state"] == "resolved_with_veto"
    assert data["view"]["summary"] == {
        "proposal_count": 3,
        "selected_count": 2,
        "veto_count": 1,
        "committee_required": False,
        "sponsor_persona_id": "persona-tw-momentum",
        "synthesis_method": "weighted_fusion",
        "capital_pool_id": "capital-pool-paper-001",
        "scope_ref": "paper",
    }
    veto_row = next(
        row for row in data["view"]["proposal_rows"] if row["proposal_id"] == "pap-mgmt-syn-006-gamma"
    )
    assert veto_row["state"] == "vetoed"
    assert veto_row["veto_reason"] == "forbidden_strategy_family"
    assert data["view"]["links"]["allocation_policy_artifact"] == {
        "id": "alloc-policy-mgmt-syn-006-001",
        "href": None,
    }


def test_conflict_log_filters_and_committee_state(monkeypatch) -> None:
    with _conflict_log_client(monkeypatch, payload=[PROOF_BUNDLE, COMMITTEE_LOG]) as client:
        by_proposal = client.get(
            "/bff/synthesis/conflict-logs?proposal_id=pap-mgmt-syn-006-gamma",
            headers=HEADERS,
        )
        by_committee = client.get(
            "/bff/synthesis/conflict-logs?committee_ref=committee-risk-001",
            headers=HEADERS,
        )

    assert by_proposal.status_code == 200, by_proposal.text
    assert [item["log_id"] for item in by_proposal.json()["items"]] == [
        "conflict-log-mgmt-syn-006-001"
    ]

    assert by_committee.status_code == 200, by_committee.text
    committee_item = by_committee.json()["items"][0]
    assert committee_item["log_id"] == "conflict-log-mgmt-syn-006-committee"
    assert committee_item["resolution_state"] == "committee_required"
    assert committee_item["view"]["governance"]["committee_ref"] == "committee-risk-001"


def test_conflict_log_unknown_id_and_missing_source(monkeypatch) -> None:
    with _conflict_log_client(monkeypatch, payload=[PROOF_BUNDLE]) as client:
        unknown = client.get("/bff/synthesis/conflict-logs/not-a-log", headers=HEADERS)

    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["detail"]["error"]["code"] == "OBJECT_NOT_FOUND"

    with _conflict_log_client(monkeypatch, payload=None) as client:
        listed = client.get("/bff/synthesis/conflict-logs", headers=HEADERS)
        detail = client.get("/bff/synthesis/conflict-logs/not-a-log", headers=HEADERS)

    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == []
    assert listed.json()["meta"]["surfaces"]["synthesis_conflict_logs"]["status"] == "unavailable"
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["status"] == "degraded"
    assert detail.json()["meta"]["surfaces"]["synthesis_conflict_log_detail"]["status"] == "unavailable"


def test_conflict_log_feature_flag_and_openapi_route_registration(monkeypatch) -> None:
    with _conflict_log_client(monkeypatch, payload=[PROOF_BUNDLE]) as client:
        monkeypatch.setenv("PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED", "false")
        disabled = client.get("/bff/synthesis/conflict-logs", headers=HEADERS)
        monkeypatch.setenv("PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED", "true")
        openapi = client.get("/openapi.json")

    assert disabled.status_code == 503, disabled.text
    assert disabled.json()["detail"]["error"]["code"] == "DOWNSTREAM_UNAVAILABLE"
    assert "/bff/synthesis/conflict-logs" in openapi.json()["paths"]
    assert "/bff/synthesis/conflict-logs/{log_id}" in openapi.json()["paths"]
