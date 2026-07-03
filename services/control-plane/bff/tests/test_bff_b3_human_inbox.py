"""
BFF-B3-003: contract tests for GET /bff/management/human-inbox.

The route is a read-only Management aggregate. It composes pending human
approval queue rows with v5 intervention rows, then exposes a detail route for
the composed inbox item identity.
"""
from __future__ import annotations

import os
import sys
import tempfile
from urllib.parse import quote

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore

OPERATOR_HEADERS = {"Authorization": "Bearer op-b3-human:operator"}


def _fresh_client(td: str) -> TestClient:
    bff_main.read_store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=True,
    )
    return TestClient(bff_main.app)


def _seed_intervention() -> None:
    bff_main._V5_INTERVENTIONS_STORE.append(
        {
            "intervention_id": "intv-human-001",
            "kind": "hiq_sentinel",
            "status": "pending",
            "target_type": "Runtime",
            "target_id": "runtime-human-001",
            "triggered_at": "2026-05-23T06:00:00Z",
            "triggered_by": "sentinel",
            "description": "Sentinel detected a risk breach requiring operator action.",
            "correlation_id": "corr-human-001",
        }
    )


def test_human_inbox_composes_approvals_and_interventions() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_interventions = list(bff_main._V5_INTERVENTIONS_STORE)
        try:
            client = _fresh_client(td)
            bff_main._V5_INTERVENTIONS_STORE.clear()
            _seed_intervention()

            resp = client.get("/bff/management/human-inbox", headers=OPERATOR_HEADERS)

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["data"] == body["items"]
            assert body["summary"]["approval_count"] >= 1
            assert body["summary"]["intervention_count"] >= 1
            assert body["meta"]["surfaces"]["human_inbox"]["source"] == "bff_composed"
            assert "approval_queue" in body["meta"]["surfaces"]
            assert body["meta"]["surfaces"]["v5_interventions"]["source"] in {
                "bff_local_registry",
                "local_snapshot",
            }

            approval = next(item for item in body["items"] if item["source_type"] == "approval")
            assert approval["id"].startswith("approval:")
            assert approval["decision_context"]["risk_summary"]
            assert "canApprove" in approval["allowedActions"]

            intervention = next(item for item in body["items"] if item["source_type"] == "intervention")
            assert intervention["id"] == "intervention:intv-human-001"
            assert intervention["priority"] == "critical"
            assert intervention["allowedActions"]["canRemediate"] is True
            assert intervention["remediation_context"]["correlation_id"] == "corr-human-001"
        finally:
            bff_main.read_store = original_store
            bff_main._V5_INTERVENTIONS_STORE.clear()
            bff_main._V5_INTERVENTIONS_STORE.extend(original_interventions)


def test_human_inbox_supports_filters_pagination_and_detail() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_interventions = list(bff_main._V5_INTERVENTIONS_STORE)
        try:
            client = _fresh_client(td)
            bff_main._V5_INTERVENTIONS_STORE.clear()
            _seed_intervention()

            list_resp = client.get(
                "/bff/management/human-inbox?source_type=intervention&status=pending&page_size=1",
                headers=OPERATOR_HEADERS,
            )
            assert list_resp.status_code == 200, list_resp.text
            list_body = list_resp.json()
            assert list_body["page_info"]["page_size"] == 1
            assert list_body["page_info"]["total"] >= 1
            assert len(list_body["items"]) == 1

            detail_resp = client.get(
                "/bff/management/human-inbox/intervention:intv-human-001",
                headers=OPERATOR_HEADERS,
            )
            assert detail_resp.status_code == 200, detail_resp.text
            detail_body = detail_resp.json()
            assert detail_body["data"]["intervention_id"] == "intv-human-001"
            assert detail_body["data"]["bff_detail_path"] == "/bff/v5/interventions/intv-human-001"
        finally:
            bff_main.read_store = original_store
            bff_main._V5_INTERVENTIONS_STORE.clear()
            bff_main._V5_INTERVENTIONS_STORE.extend(original_interventions)


def test_human_inbox_projects_persona_paper_to_canary_review_items() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_interventions = list(bff_main._V5_INTERVENTIONS_STORE)
        try:
            client = _fresh_client(td)
            bff_main._V5_INTERVENTIONS_STORE.clear()
            store = bff_main.read_store
            store._data["personas"] = {
                "persona-promotion-review": {
                    "id": "persona-promotion-review",
                    "persona_id": "persona-promotion-review",
                    "name": "Promotion Review Persona",
                    "lifecycle_state": "deployed",
                    "status": "deployed",
                    "updated_at": "2026-07-02T00:00:00Z",
                    "canonicalWriteAuthority": "persona_registry_service",
                    "persistenceMode": "bff_local_dev_store",
                    "metadata": {
                        "owner": "test",
                        "capital_pool_id": "pool-promotion-review",
                        "current_work": "Paper runtime passed stability window; canary review required.",
                    },
                },
            }
            store._data["persona_bindings"] = {
                "binding-promotion-review": {
                    "binding_id": "binding-promotion-review",
                    "persona_id": "persona-promotion-review",
                    "capital_pool_id": "pool-promotion-review",
                    "status": "active",
                    "validity": "active",
                    "allowed_deployment_scope": "paper",
                },
            }
            store._data["runtime_bindings"] = {
                "runtime-promotion-review": {
                    "runtime_binding_id": "rb-promotion-review",
                    "runtime_id": "runtime-promotion-review",
                    "capital_pool_id": "pool-promotion-review",
                    "deployment_stage": "paper",
                    "status": "active",
                },
            }
            store._data["persona_league"] = {
                "persona-promotion-review": {
                    "persona_id": "persona-promotion-review",
                    "deployment_stage": "paper",
                    "capital_pool_id": "pool-promotion-review",
                    "runtime_id": "runtime-promotion-review",
                    "league_score": 82.0,
                    "governance_required": True,
                    "recommendation": "promote_canary_review",
                    "status": "paper_running",
                },
            }
            store._local_overlay_write_datasets.add("persona_bindings")
            store._local_overlay_write_datasets.add("persona_league")
            store._local_overlay_write_datasets.add("runtime_bindings")

            list_resp = client.get(
                "/bff/management/human-inbox?source_type=readiness_blocker",
                headers=OPERATOR_HEADERS,
            )

            assert list_resp.status_code == 200, list_resp.text
            items = {item["id"]: item for item in list_resp.json()["items"]}
            gate_id = "readiness_blocker:persona:persona-promotion-review"
            item = items[gate_id]
            assert item["review_type"] == "promotion_to_canary"
            assert item["source_type"] == "readiness_blocker"
            assert item["research_context"]["state"] == "paper_running"
            assert item["research_context"]["required_human_review"] == "promotion_to_canary"
            assert item["canProceed"] is False
            assert item["canDecide"] is False
            assert item["links"]["manageHref"] == "/management/persona-fleet?persona=persona-promotion-review"
            assert list_resp.json()["summary"]["readiness_blocker_count"] >= 1

            detail_resp = client.get(
                f"/bff/management/human-inbox/{quote(gate_id, safe='')}",
                headers=OPERATOR_HEADERS,
            )

            assert detail_resp.status_code == 200, detail_resp.text
            detail = detail_resp.json()["data"]
            assert detail["id"] == gate_id
            assert detail["target"] == {"type": "Persona", "id": "persona-promotion-review"}
            assert detail["research_context"]["competition_track"] == "paper_challenger"
        finally:
            bff_main.read_store = original_store
            bff_main._V5_INTERVENTIONS_STORE.clear()
            bff_main._V5_INTERVENTIONS_STORE.extend(original_interventions)


def test_human_inbox_detail_unknown_returns_404() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/human-inbox/missing-item", headers=OPERATOR_HEADERS)

            assert resp.status_code == 404, resp.text
            assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
        finally:
            bff_main.read_store = original_store


def test_human_inbox_requires_authentication() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/human-inbox")

            assert resp.status_code == 401, resp.text
            assert resp.json()["error"]["code"] == "AUTH_REQUIRED"
        finally:
            bff_main.read_store = original_store
