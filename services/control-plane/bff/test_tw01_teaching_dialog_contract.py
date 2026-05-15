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
def _seeded_client(
    *,
    allow_local_snapshot_fallback: bool = True,
    service_backed_teaching_sessions: bool = False,
):
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_teaching_store = os.environ.get("PANTHEON_BFF_TEACHING_SESSION_STORE")
        if service_backed_teaching_sessions:
            os.environ["PANTHEON_BFF_TEACHING_SESSION_STORE"] = os.path.join(
                td,
                "teaching_sessions.json",
            )
        else:
            os.environ.pop("PANTHEON_BFF_TEACHING_SESSION_STORE", None)
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=allow_local_snapshot_fallback,
        )
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store
            if original_teaching_store is None:
                os.environ.pop("PANTHEON_BFF_TEACHING_SESSION_STORE", None)
            else:
                os.environ["PANTHEON_BFF_TEACHING_SESSION_STORE"] = original_teaching_store


def test_tw01_create_detail_and_message_routes_follow_contract() -> None:
    with _seeded_client() as client:
        create_response = client.post(
            "/api/v1/trainer/sessions",
            json={
                "persona_id": "persona-alpha",
                "session_type": "trainer",
                "objective": "Coach the first five minutes after macro surprise releases.",
                "context_refs": [
                    {"type": "research_ticket", "id": "rt-20260419-007"},
                    {"type": "memory_entry", "id": "mem-8f3c6d45-7d61-4c61-a0c6-3c5e8d1740f1"},
                ],
            },
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert create_response.status_code == 200, create_response.text

        created = create_response.json()
        assert created["persona_id"] == "persona-alpha"
        assert created["session_type"] == "trainer"
        assert created["status"] == "active"
        assert created["allowedActions"] == {"canSendMessage": True}
        assert created["links"] == {
            "self": f"/api/v1/trainer/sessions/{created['session_id']}",
            "workbench_detail": f"/trainer/sessions/{created['session_id']}",
        }

        detail_response = client.get(
            f"/api/v1/trainer/sessions/{created['session_id']}",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert detail_response.status_code == 200, detail_response.text

        detail = detail_response.json()
        assert detail["session_id"] == created["session_id"]
        assert detail["context_refs"] == [
            {"type": "research_ticket", "id": "rt-20260419-007"},
            {"type": "memory_entry", "id": "mem-8f3c6d45-7d61-4c61-a0c6-3c5e8d1740f1"},
        ]
        assert detail["session_summary"] == {
            "message_count": 0,
            "last_event_at": None,
            "latest_outcome_signal": None,
        }
        assert detail["events"] == []
        assert detail["allowedActions"] == {"canSendMessage": True}
        assert detail["meta"]["surfaces"]["trainer_dialog"] == "degraded"

        message_response = client.post(
            f"/api/v1/trainer/sessions/{created['session_id']}/message",
            json={
                "message_body": (
                    "Delay reversal unless spread quality degrades with the event shock."
                )
            },
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert message_response.status_code == 200, message_response.text

        appended = message_response.json()
        assert appended["session_id"] == created["session_id"]
        assert appended["status"] == "active"
        assert appended["event"] == {
            "event_id": appended["event"]["event_id"],
            "session_id": created["session_id"],
            "actor": "operator",
            "message_body": "Delay reversal unless spread quality degrades with the event shock.",
            "emitted_at": appended["accepted_at"],
            "sequence_number": 1,
            "outcome_signal": None,
        }
        assert appended["session_summary"] == {
            "message_count": 1,
            "last_event_at": appended["accepted_at"],
            "latest_outcome_signal": None,
        }
        assert appended["allowedActions"] == {"canSendMessage": True}

        refreshed_detail = client.get(
            f"/api/v1/trainer/sessions/{created['session_id']}",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert refreshed_detail.status_code == 200, refreshed_detail.text
        refreshed_payload = refreshed_detail.json()
        assert [event["sequence_number"] for event in refreshed_payload["events"]] == [1]
        assert refreshed_payload["events"][0] == appended["event"]


def test_tw01_list_route_respects_backend_filters_pagination_and_surface_meta() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/sessions",
            params={
                "persona_id": "persona-alpha",
                "status": "active",
                "page_size": 1,
            },
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["page_info"] == {
            "next_page_token": None,
            "total": 1,
        }
        assert payload["data"] == [
            {
                "session_id": "trn-20260419-001",
                "persona_id": "persona-alpha",
                "session_type": "trainer",
                "objective": (
                    "Tighten event-window response and reduce premature signal reversals "
                    "during macro surprise sessions."
                ),
                "status": "active",
                "started_at": "2026-04-19T19:30:00Z",
                "ended_at": None,
                "message_count": 3,
                "last_event_at": "2026-04-19T19:37:40Z",
                "latest_outcome_signal": "candidate-adjustment-ready",
                "actor_context": {
                    "persona_display_name": "Alpha Persona",
                    "persona_role_context": "systematic momentum coach",
                },
                "allowedActions": {"canSendMessage": True},
                "links": {
                    "workbench_detail": "/trainer/sessions/trn-20260419-001",
                },
            }
        ]
        assert payload["meta"]["surfaces"]["trainer_dialog"] == "degraded"


def test_tw01_message_route_rejects_non_active_sessions() -> None:
    with _seeded_client() as client:
        response = client.post(
            "/api/v1/trainer/sessions/trn-20260418-003/message",
            json={"message_body": "This should not be accepted."},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 409, response.text

        payload = response.json()
        assert payload["detail"]["error"]["code"] == "INVALID_STATE"
        assert payload["detail"]["error"]["details"]["precondition_failed"] == "status"


def test_tw01_create_and_message_persist_when_service_store_is_configured() -> None:
    with _seeded_client(service_backed_teaching_sessions=True) as client:
        create_response = client.post(
            "/api/v1/trainer/sessions",
            json={
                "persona_id": "persona-alpha",
                "session_type": "trainer",
                "objective": "Validate service-backed persistence for trainer dialog writes.",
                "context_refs": [],
            },
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert create_response.status_code == 200, create_response.text
        session_id = create_response.json()["session_id"]

        list_response = client.get(
            "/api/v1/trainer/sessions",
            params={"persona_id": "persona-alpha"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert list_response.status_code == 200, list_response.text
        assert any(
            item["session_id"] == session_id
            for item in list_response.json()["data"]
        )

        message_response = client.post(
            f"/api/v1/trainer/sessions/{session_id}/message",
            json={"message_body": "Persist this through the configured service store."},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert message_response.status_code == 200, message_response.text

        detail_response = client.get(
            f"/api/v1/trainer/sessions/{session_id}",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()
        assert detail["session_summary"]["message_count"] == 1
        assert detail["events"][0]["message_body"] == (
            "Persist this through the configured service store."
        )


def test_tw01_list_route_returns_unavailable_surface_when_store_missing() -> None:
    with _seeded_client(allow_local_snapshot_fallback=False) as client:
        original_get_persona = bff_main.read_store.get_persona
        original_list_sessions = bff_main.read_store.list_trainer_sessions
        original_dataset_source = bff_main.read_store.dataset_source
        bff_main.read_store.get_persona = lambda persona_id: {"id": persona_id}
        bff_main.read_store.list_trainer_sessions = lambda **_: None
        bff_main.read_store.dataset_source = lambda dataset: "missing" if dataset == "teaching_sessions" else original_dataset_source(dataset)
        try:
            response = client.get(
                "/api/v1/trainer/sessions",
                params={"persona_id": "persona-alpha"},
                headers={"Authorization": OPERATOR_AUTH},
            )
        finally:
            bff_main.read_store.get_persona = original_get_persona
            bff_main.read_store.list_trainer_sessions = original_list_sessions
            bff_main.read_store.dataset_source = original_dataset_source

        assert response.status_code == 200, response.text
        assert response.json() == {
            "data": [],
            "page_info": {
                "next_page_token": None,
                "total": 0,
            },
            "meta": {
                "snapshot_at": response.json()["meta"]["snapshot_at"],
                "surfaces": {
                    "trainer_dialog": "unavailable",
                },
            },
        }
