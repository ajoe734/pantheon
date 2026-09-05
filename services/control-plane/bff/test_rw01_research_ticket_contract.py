from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import DefaultResearchKnowledgeSourcePort


OPERATOR_AUTH = "Bearer test-operator:operator"


_SEEDED_TICKETS = {
    "rt-20260419-007": {
        "ticket_id": "rt-20260419-007",
        "title": "Evaluate momentum factor decay in high-volatility regime",
        "description": "Assess momentum decay during sustained volatility spikes.",
        "status": "in_progress",
        "priority": "high",
        "owner": "persona-risk-chief",
        "created_at": "2026-04-19T17:10:00Z",
        "updated_at": "2026-04-19T18:30:00Z",
    },
    "rt-20260415-001": {
        "ticket_id": "rt-20260415-001",
        "title": "Validate signal quality on macro event windows",
        "description": "Validate macro-event exclusion windows.",
        "status": "closed",
        "priority": "normal",
        "owner": "persona-risk-chief",
        "created_at": "2026-04-15T09:00:00Z",
        "updated_at": "2026-04-18T12:00:00Z",
        "closed_at": "2026-04-18T12:00:00Z",
    },
    "tkt-7a8b9c0d-1234-5678-abcd-ef0123456789": {
        "ticket_id": "tkt-7a8b9c0d-1234-5678-abcd-ef0123456789",
        "title": "RW-Ticket: MOM-v3 slippage investigation (Apr 14)",
        "description": "Ticket aligned with the RW-01 example payload.",
        "status": "closed",
        "priority": "high",
        "owner": "op-001",
        "created_at": "2026-04-14T10:30:00Z",
        "updated_at": "2026-04-16T14:22:00Z",
        "closed_at": "2026-04-16T13:55:00Z",
    },
    "rt-20260418-003": {
        "ticket_id": "rt-20260418-003",
        "title": "Active ticket cannot be archived",
        "description": "Exercises the lifecycle transition guard.",
        "status": "open",
        "priority": "normal",
        "owner": "persona-alpha",
        "created_at": "2026-04-18T08:00:00Z",
        "updated_at": "2026-04-18T08:00:00Z",
    },
}


class _TicketPortDouble(DefaultResearchKnowledgeSourcePort):
    """Typed RW-01 double with explicit source and optional JSON persistence."""

    def __init__(
        self,
        records: dict[str, dict],
        *,
        source: str,
        persistence_path: Path | None = None,
    ) -> None:
        super().__init__(research_tickets_store=records)
        self._source = source
        self._persistence_path = persistence_path

    def dataset_source(self, dataset: str, **_: object) -> str:
        if dataset == "research_tickets":
            return self._source
        return super().dataset_source(dataset)

    def get_research_ticket(
        self,
        ticket_id: str,
        *,
        include_snapshot_fallback: bool = True,
        include_local_fallback: bool = True,
    ) -> dict | None:
        if self._source == "local_snapshot" and not (
            include_snapshot_fallback and include_local_fallback
        ):
            return None
        return super().get_research_ticket(ticket_id)

    def _persist(self) -> None:
        if self._persistence_path is not None:
            self._persistence_path.write_text(
                json.dumps(self._tickets, indent=2),
                encoding="utf-8",
            )

    def create_research_ticket(self, **kwargs: object) -> dict:
        ticket = super().create_research_ticket(**kwargs)
        self._persist()
        return ticket

    def patch_research_ticket(self, ticket_id: str, **kwargs: object) -> dict | None:
        ticket = super().patch_research_ticket(ticket_id, **kwargs)
        self._persist()
        return ticket


@contextmanager
def _seeded_client():
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = _TicketPortDouble(
            _SEEDED_TICKETS,
            source="local_snapshot",
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
        bff_main.read_store = _TicketPortDouble(
            json.loads(ticket_store.read_text(encoding="utf-8")),
            source="service_client",
            persistence_path=ticket_store,
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
        bff_main.read_store = _TicketPortDouble({}, source="missing")
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
        assert payload["error"]["code"] == "OPERATION_NOT_ALLOWED"
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
