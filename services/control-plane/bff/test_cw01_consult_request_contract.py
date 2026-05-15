from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore
from services.consultation.store import ConsultationStore

OPERATOR_AUTH = "Bearer test-operator:operator"

_VALID_CREATE_PAYLOAD = {
    "from_persona_id": "persona-alpha",
    "target_type": "persona",
    "target_ref": "persona-beta",
    "task": "Review deployment risk for persona-beta before next canary window.",
    "context_refs": [
        {"type": "deployment_plan", "id": "plan-F-042"},
        {"type": "incident", "id": "inc-007"},
    ],
    "priority": "high",
    "consultation_type": "risk_review",
}


@contextmanager
def _seeded_client(*, allow_local_snapshot_fallback: bool = True):
    with tempfile.TemporaryDirectory() as td:
        cr_store_path = os.path.join(td, "consult_requests.json")
        original_store = bff_main.read_store
        original_cr_env = os.environ.get("PANTHEON_BFF_CONSULT_REQUEST_STORE")
        os.environ["PANTHEON_BFF_CONSULT_REQUEST_STORE"] = cr_store_path
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=allow_local_snapshot_fallback,
        )
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store
            if original_cr_env is None:
                os.environ.pop("PANTHEON_BFF_CONSULT_REQUEST_STORE", None)
            else:
                os.environ["PANTHEON_BFF_CONSULT_REQUEST_STORE"] = original_cr_env


def test_cw01_create_returns_required_fields() -> None:
    with _seeded_client() as client:
        resp = client.post(
            "/api/v1/consult/requests",
            json=_VALID_CREATE_PAYLOAD,
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert "request_id" in body
        assert body["status"] == "created"
        assert "created_at" in body
        assert body["linked_session_id"] is None
        assert body["request_to_session_status"] == "pending_session"
        assert body["allowedActions"]["canCancel"] is True


def test_cw01_list_returns_required_envelope() -> None:
    with _seeded_client() as client:
        client.post(
            "/api/v1/consult/requests",
            json=_VALID_CREATE_PAYLOAD,
            headers={"Authorization": OPERATOR_AUTH},
        )
        resp = client.get(
            "/api/v1/consult/requests",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert "data" in body
        assert "page_info" in body
        assert "next_page_token" in body["page_info"]
        assert "total" in body["page_info"]
        assert "meta" in body
        assert "snapshot_at" in body["meta"]
        assert "consult_request_list" in body["meta"]["surfaces"]

        row = body["data"][0]
        assert "request_id" in row
        assert "status" in row
        assert "from_persona_id" in row
        assert "target_type" in row
        assert "target_ref" in row
        assert "task_summary" in row
        assert "priority" in row
        assert "consultation_type" in row
        assert "created_at" in row
        assert "linked_session_id" in row
        assert "request_to_session_status" in row
        assert "allowedActions" in row
        assert "canCancel" in row["allowedActions"]


def test_cw01_detail_returns_required_fields() -> None:
    with _seeded_client() as client:
        create_resp = client.post(
            "/api/v1/consult/requests",
            json=_VALID_CREATE_PAYLOAD,
            headers={"Authorization": OPERATOR_AUTH},
        )
        request_id = create_resp.json()["request_id"]

        resp = client.get(
            f"/api/v1/consult/requests/{request_id}",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        for field in (
            "request_id", "status", "from_persona_id", "target_type",
            "target_ref", "task", "context_refs", "priority",
            "consultation_type", "created_at", "completed_at", "canceled_at",
            "linked_session_id", "request_to_session_status",
        ):
            assert field in body, f"missing field: {field}"

        assert "session_handoff" in body
        sh = body["session_handoff"]
        assert "status" in sh
        assert "linked_session_id" in sh
        assert "session_route_href" in sh
        assert "note" in sh

        assert "allowedActions" in body
        assert "canCancel" in body["allowedActions"]

        assert "links" in body
        assert "self" in body["links"]
        assert "workbench_detail" in body["links"]

        assert "meta" in body
        assert "consult_request_detail" in body["meta"]["surfaces"]


def test_cw01_cancel_sets_status_canceled_and_blocks_further_cancel() -> None:
    with _seeded_client() as client:
        create_resp = client.post(
            "/api/v1/consult/requests",
            json=_VALID_CREATE_PAYLOAD,
            headers={"Authorization": OPERATOR_AUTH},
        )
        request_id = create_resp.json()["request_id"]

        cancel_resp = client.post(
            f"/api/v1/consult/requests/{request_id}/cancel",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert cancel_resp.status_code == 200, cancel_resp.text
        body = cancel_resp.json()

        assert body["request_id"] == request_id
        assert body["status"] == "canceled"
        assert body["canceled_at"] is not None
        assert body["allowedActions"]["canCancel"] is False
        assert "request_to_session_status" in body

        second_cancel = client.post(
            f"/api/v1/consult/requests/{request_id}/cancel",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert second_cancel.status_code == 409, second_cancel.text


def test_cw01_running_request_disables_cancel_and_rejects_cancel_route() -> None:
    with _seeded_client() as client:
        create_resp = client.post(
            "/api/v1/consult/requests",
            json=_VALID_CREATE_PAYLOAD,
            headers={"Authorization": OPERATOR_AUTH},
        )
        request_id = create_resp.json()["request_id"]

        available, service_requests = bff_main.read_store._service.list_records("consult_requests")
        assert available is True
        request = next(record for record in service_requests if record["request_id"] == request_id)
        request["status"] = "running"
        request["linked_session_id"] = "cs-20260420-001"
        request["request_to_session_status"] = "session_running"
        request["session_handoff_note"] = "Persona Plane materialized the consultation session."
        bff_main.read_store._service.write_records("consult_requests", {request_id: request})

        list_resp = client.get(
            "/api/v1/consult/requests",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert list_resp.status_code == 200, list_resp.text
        row = list_resp.json()["data"][0]
        assert row["request_id"] == request_id
        assert row["allowedActions"]["canCancel"] is False

        detail_resp = client.get(
            f"/api/v1/consult/requests/{request_id}",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert detail_resp.status_code == 200, detail_resp.text
        detail = detail_resp.json()
        assert detail["request_to_session_status"] == "session_running"
        assert detail["allowedActions"]["canCancel"] is False

        cancel_resp = client.post(
            f"/api/v1/consult/requests/{request_id}/cancel",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert cancel_resp.status_code == 409, cancel_resp.text


def test_cw01_create_and_cancel_use_consultation_service_store_when_configured() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        tracked_env = {
            "PANTHEON_BFF_CONSULTATION_DATA_DIR": os.environ.get("PANTHEON_BFF_CONSULTATION_DATA_DIR"),
            "PANTHEON_BFF_CONSULT_REQUEST_STORE": os.environ.get("PANTHEON_BFF_CONSULT_REQUEST_STORE"),
        }
        os.environ["PANTHEON_BFF_CONSULTATION_DATA_DIR"] = td
        os.environ.pop("PANTHEON_BFF_CONSULT_REQUEST_STORE", None)
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        client = TestClient(bff_main.app)
        try:
            create_resp = client.post(
                "/api/v1/consult/requests",
                json=_VALID_CREATE_PAYLOAD,
                headers={"Authorization": OPERATOR_AUTH},
            )
            assert create_resp.status_code == 200, create_resp.text
            created = create_resp.json()
            request_id = created["request_id"]
            assert created["status"] == "created"
            assert created["request_to_session_status"] == "pending_session"

            service_store = ConsultationStore(td)
            service_request = service_store.get_request(request_id)
            assert service_request is not None
            assert service_request.request_id == request_id
            assert service_request.task == _VALID_CREATE_PAYLOAD["task"]
            assert service_request.metadata["bff_context_refs"] == _VALID_CREATE_PAYLOAD["context_refs"]

            list_resp = client.get(
                "/api/v1/consult/requests",
                headers={"Authorization": OPERATOR_AUTH},
            )
            assert list_resp.status_code == 200, list_resp.text
            list_body = list_resp.json()
            assert list_body["data"][0]["request_id"] == request_id
            assert list_body["meta"]["surfaces"]["consult_request_list"] == "fresh"

            cancel_resp = client.post(
                f"/api/v1/consult/requests/{request_id}/cancel",
                headers={"Authorization": OPERATOR_AUTH},
            )
            assert cancel_resp.status_code == 200, cancel_resp.text
            assert cancel_resp.json()["status"] == "canceled"

            replayed = ConsultationStore(td).get_request(request_id)
            assert replayed is not None
            assert replayed.status.value == "cancelled"
            assert replayed.request_to_session_status == "canceled_before_session"
            audit_actions = [
                event.action
                for event in ConsultationStore(td).list_audit_for_request(request_id)
            ]
            assert "request_created" in audit_actions
            assert "request_cancelled" in audit_actions
        finally:
            bff_main.read_store = original_store
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
