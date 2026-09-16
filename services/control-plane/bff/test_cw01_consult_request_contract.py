from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.ports.operations_consultation import DomainConsultationPort
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

_CONSULTATION_DATA_DIR_ENVS = (
    "PANTHEON_BFF_CONSULTATION_DATA_DIR",
    "PANTHEON_CONSULTATION_DATA_DIR",
    "CONSULTATION_DATA_DIR",
)


def _consultation_service_configured() -> bool:
    return any(os.environ.get(name, "").strip() for name in _CONSULTATION_DATA_DIR_ENVS)


def _parse_rfc3339(value: Any) -> datetime:
    if not value or not isinstance(value, str):
        return datetime.min
    cleaned = value.strip()
    if not cleaned:
        return datetime.min
    try:
        normalized = cleaned.replace("Z", "+00:00") if cleaned.endswith("Z") else cleaned
        return datetime.fromisoformat(normalized).replace(tzinfo=None)
    except (ValueError, TypeError):
        return datetime.min


def _can_cancel(req: Dict[str, Any]) -> bool:
    status = str(req.get("status") or "created").lower()
    if status in {"completed", "canceled", "cancelled"}:
        return False
    return not bool(req.get("linked_session_id"))


def _project_summary(req: Dict[str, Any]) -> Dict[str, Any]:
    task_full = str(req.get("task") or "")
    task_summary = task_full[:120] + ("…" if len(task_full) > 120 else "")
    return {
        "request_id": req.get("request_id"),
        "status": req.get("status") or "created",
        "from_persona_id": req.get("from_persona_id"),
        "target_type": req.get("target_type"),
        "target_ref": req.get("target_ref"),
        "task_summary": task_summary,
        "priority": req.get("priority"),
        "consultation_type": req.get("consultation_type"),
        "created_at": req.get("created_at"),
        "linked_session_id": req.get("linked_session_id"),
        "request_to_session_status": req.get("request_to_session_status", "pending_session"),
        "allowedActions": {"canCancel": _can_cancel(req)},
    }


def _project_detail(req: Dict[str, Any]) -> Dict[str, Any]:
    linked_session_id = req.get("linked_session_id")
    r2s_status = str(req.get("request_to_session_status") or "pending_session")
    session_route_href = f"/api/v1/consultations/{linked_session_id}" if linked_session_id else None
    return {
        "request_id": req.get("request_id"),
        "status": req.get("status") or "created",
        "from_persona_id": req.get("from_persona_id"),
        "target_type": req.get("target_type"),
        "target_ref": req.get("target_ref"),
        "task": req.get("task"),
        "context_refs": req.get("context_refs", []),
        "priority": req.get("priority"),
        "consultation_type": req.get("consultation_type"),
        "created_at": req.get("created_at"),
        "completed_at": req.get("completed_at"),
        "canceled_at": req.get("canceled_at"),
        "linked_session_id": linked_session_id,
        "request_to_session_status": r2s_status,
        "session_handoff": {
            "status": r2s_status,
            "linked_session_id": linked_session_id,
            "session_route_href": session_route_href,
            "note": req.get("session_handoff_note", ""),
        },
        "allowedActions": {"canCancel": _can_cancel(req)},
    }


class _LocalConsultRequestServiceAdapter:
    """Mimics the `_service` file-backed adapter used by direct-write test cases."""

    def __init__(self, outer: "_ConsultRequestReadStore") -> None:
        self._outer = outer

    def list_records(self, dataset: str):
        if dataset != "consult_requests":
            return False, []
        return True, list(self._outer._load_requests().values())

    def write_records(self, dataset: str, records: Dict[str, Dict[str, Any]]) -> bool:
        if dataset != "consult_requests":
            return False
        self._outer._write_requests(dict(records))
        return True


class _ConsultRequestReadStore:
    """CW-01 in-memory consult-request read/write double.

    Delegates to the real DomainConsultationPort when a consultation service
    data dir is configured (matching production behavior), and otherwise
    persists consult requests to a plain dict/JSON file keyed by
    PANTHEON_BFF_CONSULT_REQUEST_STORE, mirroring the retired
    the legacy BFF read surface's local fallback semantics.
    """

    def __init__(self, path: str, allow_local_snapshot_fallback: bool = True) -> None:
        self._path = path
        self._requests: Dict[str, Dict[str, Any]] = {}
        self._domain = DomainConsultationPort()
        self._service = _LocalConsultRequestServiceAdapter(self)

    def _consult_request_store_path(self) -> Optional[str]:
        raw = os.environ.get("PANTHEON_BFF_CONSULT_REQUEST_STORE", "").strip()
        return raw or None

    def _load_requests(self) -> Dict[str, Dict[str, Any]]:
        path = self._consult_request_store_path()
        if path:
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    return json.load(handle)
            except (FileNotFoundError, json.JSONDecodeError):
                return {}
        return self._requests

    def _write_requests(self, requests: Dict[str, Dict[str, Any]]) -> None:
        path = self._consult_request_store_path()
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(requests, handle)
        else:
            self._requests = requests

    def dataset_source(self, dataset: str) -> str:
        if dataset != "consult_requests":
            return "missing"
        if _consultation_service_configured():
            return "consultation_service_store"
        if self._consult_request_store_path():
            return "service_store"
        return "missing"

    def list_consult_requests(
        self,
        *,
        statuses: Optional[List[str]] = None,
        target_type: Optional[str] = None,
        consultation_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if _consultation_service_configured():
            return self._domain.list_consult_requests(
                statuses=statuses,
                target_type=target_type,
                consultation_type=consultation_type,
            )
        requests = list(self._load_requests().values())
        if statuses:
            requested = {s.strip().lower() for s in statuses if s.strip()}
            requests = [r for r in requests if str(r.get("status") or "").strip().lower() in requested]
        if target_type:
            requested_tt = target_type.strip().lower()
            requests = [r for r in requests if str(r.get("target_type") or "").strip().lower() == requested_tt]
        if consultation_type:
            requested_ct = consultation_type.strip().lower()
            requests = [
                r for r in requests if str(r.get("consultation_type") or "").strip().lower() == requested_ct
            ]
        requests.sort(key=lambda r: _parse_rfc3339(r.get("created_at")), reverse=True)
        return [_project_summary(r) for r in requests]

    def get_consult_request(self, request_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not request_id:
            return None
        if _consultation_service_configured():
            return self._domain.get_consult_request(request_id)
        req = self._load_requests().get(request_id)
        return _project_detail(req) if req else None

    def create_consult_request(
        self,
        *,
        from_persona_id: str,
        target_type: str,
        target_ref: str,
        task: str,
        context_refs: List[Dict[str, str]],
        priority: str,
        consultation_type: str,
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        if _consultation_service_configured():
            return self._domain.create_consult_request(
                from_persona_id=from_persona_id,
                target_type=target_type,
                target_ref=target_ref,
                task=task,
                context_refs=context_refs,
                priority=priority,
                consultation_type=consultation_type,
                actor_id=actor_id,
                created_at=created_at,
            )

        timestamp = created_at or datetime.utcnow().isoformat() + "Z"
        requests = self._load_requests()
        request_id = f"cr-{timestamp[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
        while request_id in requests:
            request_id = f"cr-{timestamp[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"

        req: Dict[str, Any] = {
            "request_id": request_id,
            "status": "created",
            "from_persona_id": from_persona_id,
            "target_type": target_type,
            "target_ref": target_ref,
            "task": task,
            "context_refs": context_refs,
            "priority": priority,
            "consultation_type": consultation_type,
            "created_at": timestamp,
            "completed_at": None,
            "canceled_at": None,
            "linked_session_id": None,
            "request_to_session_status": "pending_session",
            "session_handoff_note": "Request accepted; session creation is pending Persona Plane assignment.",
            "created_by": actor_id,
        }
        requests[request_id] = req
        self._write_requests(requests)
        return _project_detail(req)

    def cancel_consult_request(
        self,
        request_id: str,
        *,
        actor_id: str,
        canceled_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if _consultation_service_configured():
            return self._domain.cancel_consult_request(
                request_id,
                actor_id=actor_id,
                canceled_at=canceled_at,
            )

        requests = self._load_requests()
        req = requests.get(request_id)
        if req is None:
            return None
        if not _can_cancel(req):
            return None
        timestamp = canceled_at or datetime.utcnow().isoformat() + "Z"
        req["status"] = "canceled"
        req["canceled_at"] = timestamp
        req["request_to_session_status"] = "canceled_before_session"
        req["session_handoff_note"] = "Request canceled by operator."
        requests[request_id] = req
        self._write_requests(requests)
        return _project_detail(req)


@contextmanager
def _seeded_client(*, allow_local_snapshot_fallback: bool = True):
    with tempfile.TemporaryDirectory() as td:
        cr_store_path = os.path.join(td, "consult_requests.json")
        original_cr_env = os.environ.get("PANTHEON_BFF_CONSULT_REQUEST_STORE")
        os.environ["PANTHEON_BFF_CONSULT_REQUEST_STORE"] = cr_store_path
        store = _ConsultRequestReadStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=allow_local_snapshot_fallback,
        )
        app = FastAPI()
        app.include_router(create_governance_router(get_read_store=lambda: store))
        client = TestClient(app)
        client.store = store  # type: ignore[attr-defined]
        try:
            yield client
        finally:
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

        available, service_requests = client.store._service.list_records("consult_requests")
        assert available is True
        request = next(record for record in service_requests if record["request_id"] == request_id)
        request["status"] = "running"
        request["linked_session_id"] = "cs-20260420-001"
        request["request_to_session_status"] = "session_running"
        request["session_handoff_note"] = "Persona Plane materialized the consultation session."
        client.store._service.write_records("consult_requests", {request_id: request})

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
        tracked_env = {
            "PANTHEON_BFF_CONSULTATION_DATA_DIR": os.environ.get("PANTHEON_BFF_CONSULTATION_DATA_DIR"),
            "PANTHEON_BFF_CONSULT_REQUEST_STORE": os.environ.get("PANTHEON_BFF_CONSULT_REQUEST_STORE"),
        }
        os.environ["PANTHEON_BFF_CONSULTATION_DATA_DIR"] = td
        os.environ.pop("PANTHEON_BFF_CONSULT_REQUEST_STORE", None)
        store = _ConsultRequestReadStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        app = FastAPI()
        app.include_router(create_governance_router(get_read_store=lambda: store))
        client = TestClient(app)
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
            surface_state = list_body["meta"]["surfaces"]["consult_request_list"]
            assert surface_state == "fresh" or (isinstance(surface_state, dict) and surface_state.get("status") == "ok")

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
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
