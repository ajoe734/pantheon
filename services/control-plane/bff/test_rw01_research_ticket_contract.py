from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

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


@contextmanager
def _service_backed_client():
    tracked_env = {
        "PANTHEON_BFF_RESEARCH_TICKET_STORE": os.environ.get("PANTHEON_BFF_RESEARCH_TICKET_STORE"),
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ticket_store = root / "research_tickets.json"
        ticket_store.write_text(
            json.dumps(
                {
                    "rt-service-001": {
                        "ticket_id": "rt-service-001",
                        "title": "Service-backed ticket wins over local fallback",
                        "description": "Used to verify RW-01 reads the service store first.",
                        "status": "in_progress",
                        "priority": "high",
                        "owner": "persona-alpha",
                        "created_at": "2026-04-20T03:00:00Z",
                        "updated_at": "2026-04-20T05:30:00Z",
                        "closed_at": None,
                        "archived_at": None,
                        "lifecycle_history": [
                            {
                                "from_status": None,
                                "to_status": "open",
                                "transitioned_at": "2026-04-20T03:00:00Z",
                                "transitioned_by": "persona-alpha",
                            },
                            {
                                "from_status": "open",
                                "to_status": "in_progress",
                                "transitioned_at": "2026-04-20T05:30:00Z",
                                "transitioned_by": "test-operator",
                            }
                        ],
                        "linked_experiments": ["exp-service-001"],
                        "linked_artifacts": ["artifact-service-001"],
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        os.environ["PANTHEON_BFF_RESEARCH_TICKET_STORE"] = str(ticket_store)

        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)
        try:
            yield client, ticket_store
        finally:
            bff_main.read_store = original_store
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


@contextmanager
def _unavailable_client():
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store


def test_rw01_list_contract_returns_ticket_projection() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/research/tickets?status=in_progress,closed",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["page_info"]["total"] == 3
        assert payload["meta"]["surfaces"]["ticket_list"] == "degraded"
        assert [item["ticket_id"] for item in payload["data"]] == [
            "rt-20260419-007",
            "rt-20260415-001",
            "tkt-7a8b9c0d-1234-5678-abcd-ef0123456789",
        ]
        assert payload["data"][0]["allowedActions"] == {
            "canEdit": True,
            "canClose": True,
            "canArchive": False,
        }
        assert payload["data"][1]["allowedActions"] == {
            "canEdit": False,
            "canClose": False,
            "canArchive": True,
        }
        assert payload["data"][2]["allowedActions"] == {
            "canEdit": False,
            "canClose": False,
            "canArchive": True,
        }


def test_rw01_detail_contract_returns_lifecycle_and_links() -> None:
    with _service_backed_client() as (client, _ticket_store):
        response = client.get(
            "/api/v1/research/tickets/rt-service-001",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["ticket_id"] == "rt-service-001"
        assert payload["linked_experiments"] == ["exp-service-001"]
        assert payload["linked_artifacts"] == ["artifact-service-001"]
        assert payload["links"] == {
            "self": "/api/v1/research/tickets/rt-service-001",
            "workbench_detail": "/research/tickets/rt-service-001",
        }
        assert payload["lifecycle_history"][1]["to_status"] == "in_progress"
        assert payload["meta"]["surfaces"]["ticket_detail"] == "fresh"


def test_rw01_detail_does_not_fall_back_to_local_snapshot() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/research/tickets/rt-20260419-007",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 404, response.text


def test_rw01_create_and_patch_contract_follow_lifecycle() -> None:
    with _seeded_client() as client:
        create_response = client.post(
            "/api/v1/research/tickets",
            headers={"Authorization": OPERATOR_AUTH},
            json={
                "title": "Test cross-asset stress behavior",
                "description": "Validate whether stress propagation changes execution timing assumptions.",
                "priority": "critical",
                "owner": "persona-risk-chief",
            },
        )
        assert create_response.status_code == 200, create_response.text

        created = create_response.json()
        assert created["ticket_id"].startswith("rt-")
        assert created["status"] == "open"
        assert created["allowedActions"] == {
            "canEdit": True,
            "canClose": True,
            "canArchive": False,
        }

        patch_response = client.patch(
            f"/api/v1/research/tickets/{created['ticket_id']}",
            headers={"Authorization": OPERATOR_AUTH},
            json={
                "status": "closed",
            },
        )
        assert patch_response.status_code == 200, patch_response.text

        patched = patch_response.json()
        assert patched["status"] == "closed"
        assert patched["allowedActions"] == {
            "canEdit": False,
            "canClose": False,
            "canArchive": True,
        }


def test_rw01_patch_rejects_invalid_transition() -> None:
    with _seeded_client() as client:
        response = client.patch(
            "/api/v1/research/tickets/rt-20260418-003",
            headers={"Authorization": OPERATOR_AUTH},
            json={
                "status": "archived",
            },
        )
        assert response.status_code == 409, response.text
        payload = response.json()
        assert payload["error"]["code"] == "INVALID_STATE"
        assert payload["error"]["details"]["precondition_failed"] in {
            "allowedActions.canArchive",
            "status_transition",
        }


def test_rw01_service_backed_reads_override_seeded_snapshot() -> None:
    with _service_backed_client() as (client, _ticket_store):
        list_response = client.get(
            "/api/v1/research/tickets",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert list_response.status_code == 200, list_response.text

        payload = list_response.json()
        assert [item["ticket_id"] for item in payload["data"]] == ["rt-service-001"]
        assert payload["meta"]["surfaces"]["ticket_list"] == "fresh"

        detail_response = client.get(
            "/api/v1/research/tickets/rt-service-001",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()
        assert detail["linked_artifacts"] == ["artifact-service-001"]
        assert detail["meta"]["surfaces"]["ticket_detail"] == "fresh"


def test_rw01_list_reports_unavailable_without_service_or_snapshot_fallback() -> None:
    with _unavailable_client() as client:
        response = client.get(
            "/api/v1/research/tickets",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["data"] == []
        assert payload["page_info"] == {
            "next_page_token": None,
            "total": 0,
        }
        assert payload["meta"]["surfaces"]["ticket_list"] == "unavailable"


def test_rw01_create_and_patch_persist_to_service_store() -> None:
    with _service_backed_client() as (client, ticket_store):
        create_response = client.post(
            "/api/v1/research/tickets",
            headers={"Authorization": OPERATOR_AUTH},
            json={
                "title": "Persist to service store",
                "description": "RW-01 writes should land in the service-owned dataset.",
                "priority": "high",
                "owner": "persona-beta",
            },
        )
        assert create_response.status_code == 200, create_response.text
        created = create_response.json()

        patch_response = client.patch(
            f"/api/v1/research/tickets/{created['ticket_id']}",
            headers={"Authorization": OPERATOR_AUTH},
            json={"status": "closed"},
        )
        assert patch_response.status_code == 200, patch_response.text

        persisted = json.loads(ticket_store.read_text(encoding="utf-8"))
        assert "rt-20260419-007" not in persisted
        assert persisted[created["ticket_id"]]["status"] == "closed"
        assert persisted[created["ticket_id"]]["owner"] == "persona-beta"
        assert persisted[created["ticket_id"]]["lifecycle_history"][-1]["to_status"] == "closed"
