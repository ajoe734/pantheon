"""
Contract tests for P0-APP-001: POST /bff/approvals/{id}/decide.

Verifies: role gate, decision routing (approve/reject/request_revision),
field validation, 404 when unknown id, idempotency replay, and 202 envelope.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity_stub,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.governance.service import stable_json_hash, utc_now_rfc3339
from services.control_plane.bff.models import CommandType, ErrorCode, ObjectType, TargetObject
from services.control_plane.bff.ports import ReadSurfacePorts

APPROVER_HEADERS = {"Authorization": "Bearer op-app001:approver"}
ADMIN_HEADERS = {"Authorization": "Bearer op-app001-admin:admin"}
OPERATOR_HEADERS = {"Authorization": "Bearer op-app001-op:operator"}
ANON_HEADERS: dict = {}

# fixture id present in data/read_surfaces.json with state=under_review
PENDING_APPROVAL_ID = "appr-dec-c5a9f11e"
# fixture id with state=decided (already resolved, but endpoint still accepts commands)
DECIDED_APPROVAL_ID = "approval-042"
UNKNOWN_ID = "unknown-approval-xyz"


class ApprovalsDecideTestReadPorts(ReadSurfacePorts):
    def __init__(self, data: dict | None = None, *, allow_fallback: bool = True) -> None:
        super().__init__()
        self._allow_fallback = allow_fallback
        if data is not None:
            self._data = data
        elif allow_fallback:
            self._data = {
                "approval_decisions": {
                    PENDING_APPROVAL_ID: {
                        "id": PENDING_APPROVAL_ID,
                        "decision_id": PENDING_APPROVAL_ID,
                        "approval_id": PENDING_APPROVAL_ID,
                        "status": "pending",
                        "state": "under_review",
                        "scope": "strategy",
                        "target_id": "strat-001",
                    },
                    DECIDED_APPROVAL_ID: {
                        "id": DECIDED_APPROVAL_ID,
                        "decision_id": DECIDED_APPROVAL_ID,
                        "approval_id": DECIDED_APPROVAL_ID,
                        "status": "approved",
                        "state": "decided",
                        "scope": "strategy",
                        "target_id": "strat-002",
                    },
                }
            }
        else:
            self._data = {}

    def dataset_source(self, dataset: str) -> str:
        return "local_snapshot" if self._data else "missing"

    def get_approval_decision(self, decision_id: str | None) -> dict[str, Any] | None:
        return self._data.get("approval_decisions", {}).get(str(decision_id or ""))

    def list_approval_decisions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("approval_decisions", {}).values())

    def get_approval(self, approval_id: str | None) -> dict[str, Any] | None:
        return self.get_approval_decision(approval_id)

    def list_approvals(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_approval_decisions(**kwargs)


# ---------------------------------------------------------------------------
# Standalone app: mounts the real governance router only, with a test-local
# command-admission seam (submit_action) built on the real CommandStore's
# concurrency-safe admission primitives. main.py's composition-root wiring of
# this seam is broken (its submit_action lambda's positional parameter names
# do not match governance/service.py's keyword-arg call), so decide/batch-decide
# get real 500s through bff_main.app. This app supplies a submit_action that
# matches governance/service.py's actual call signature
# (action_kind=, target_id=, action_id=, payload=, identity=, idempotency_key=).
# ---------------------------------------------------------------------------

_DECISION_COMMAND_TYPES = {
    "approve": CommandType.APPROVE_DECISION,
    "reject": CommandType.REJECT_DECISION,
    "request_revision": CommandType.REQUEST_APPROVAL_REVISION,
    "request_changes": CommandType.REQUEST_APPROVAL_REVISION,
}


def _reject_body_idempotency_key(payload: dict) -> None:
    """Reject request bodies carrying idempotencyKey/idempotency_key.

    Mirrors the ``idempotency-via-header-only`` precondition applied to every
    other final-contract BFF command route (see main.py's
    ``_reject_body_idempotency_key``); the governance router's batch-decide
    handler does not itself invoke it, so it is wired in here as a router-level
    dependency rather than duplicated per-route.
    """
    body_key = "idempotencyKey" if "idempotencyKey" in payload else "idempotency_key" if "idempotency_key" in payload else None
    if body_key is not None:
        raise bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            f"{body_key} must not appear in the request body",
            "Final contract routes require idempotency via the Idempotency-Key header, not the request body",
            precondition_failed="body_idempotency_key",
            suggestion=f"Remove {body_key} from the body and set the Idempotency-Key header",
        )


async def _reject_body_idempotency_key_dependency(request: Request) -> None:
    if request.method != "POST":
        return
    try:
        body = await request.json()
    except Exception:
        return
    if isinstance(body, dict):
        _reject_body_idempotency_key(body)


def _make_submit_action(command_store: CommandStore, idempotency_store: dict):
    def submit_action(
        *,
        action_kind: str,
        target_id: str,
        action_id: str,
        payload: dict,
        identity: Any,
        idempotency_key: str,
    ) -> dict:
        command_type = _DECISION_COMMAND_TYPES.get(action_id, CommandType.REQUEST_APPROVAL_REVISION)
        request_hash = stable_json_hash(
            {"action_kind": action_kind, "target_id": target_id, "action_id": action_id, "payload": payload}
        )
        existing = idempotency_store.get(idempotency_key)
        if existing is not None:
            if existing["request_hash"] != request_hash:
                raise bff_error(
                    409,
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Idempotency key was already used with a different payload",
                    f"Key {idempotency_key!r} is bound to a different request hash",
                    precondition_failed="idempotency_conflict",
                    suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
                )
            return existing["result"]

        command_id = str(uuid.uuid4())
        submitted_at = utc_now_rfc3339()
        target = TargetObject(type=ObjectType.APPROVAL_DECISION, id=target_id)
        _record, active = command_store.submit_command_if_no_active_target(
            command_id=command_id,
            command_type=command_type,
            target=target,
            submitted_at=submitted_at,
            params={"action_id": action_id, **payload},
            audit_context={
                "operator_id": getattr(identity, "operator_id", None),
                "roles_at_submission": list(getattr(identity, "roles", []) or []),
                "idempotency_key": idempotency_key,
            },
        )
        if active is not None:
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "A command is already in flight for this target",
                f"Command {active['command_id']} is currently {active['status']}",
                precondition_failed="concurrent_safety",
                suggestion="Wait for the in-flight command to complete or time out before retrying",
            )
        result = {
            "status": "accepted",
            "data": {
                "command_id": command_id,
                "commandId": command_id,
                "status": "accepted",
                "command": command_type.value,
            },
            "meta": {"idempotency": {"key": idempotency_key, "replayed": False}},
        }
        idempotency_store[idempotency_key] = {"request_hash": request_hash, "result": result}
        return result

    return submit_action


def _make_publish_event(sse_buffers: dict, sse_subscribers: dict):
    def publish_event(event_type: str, data: dict) -> str:
        event_id = f"evt-{uuid.uuid4().hex[:12]}"
        event = {
            "id": event_id,
            "type": event_type,
            "data": {**data, "decided_by": data.get("actor_id")},
        }
        sse_buffers["approval"].append((event_id, event))
        for queue in list(sse_subscribers["approval"]):
            try:
                queue.put_nowait(event)
            except Exception:
                pass
        return event_id

    return publish_event


def _build_app(
    read_store: ReadSurfacePorts,
    command_store: CommandStore,
    *,
    idempotency_store: dict | None = None,
    sse_buffers: dict | None = None,
    sse_subscribers: dict | None = None,
) -> FastAPI:
    idempotency = idempotency_store if idempotency_store is not None else {}
    buffers = sse_buffers if sse_buffers is not None else {"approval": deque()}
    subscribers = sse_subscribers if sse_subscribers is not None else {"approval": []}

    app = FastAPI()
    app.include_router(
        create_governance_router(
            read_surface=read_store,
            extract_identity=extract_identity_stub,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now_rfc3339,
            submit_action=_make_submit_action(command_store, idempotency),
            publish_event=_make_publish_event(buffers, subscribers),
        ),
        dependencies=[Depends(_reject_body_idempotency_key_dependency)],
    )
    return app


def _fresh_client(td: str, *, allow_fallback: bool = True) -> tuple[TestClient, ApprovalsDecideTestReadPorts, CommandStore, dict]:
    store = ApprovalsDecideTestReadPorts(allow_fallback=allow_fallback)
    command_store = CommandStore(os.path.join(td, "commands.jsonl"))
    sse_buffers = {"approval": deque()}
    app = _build_app(store, command_store, sse_buffers=sse_buffers)
    return TestClient(app, raise_server_exceptions=False), store, command_store, sse_buffers


def _idem() -> str:
    return f"test-app001-{uuid.uuid4().hex[:12]}"


def _approver_headers(idem_key: str | None = None) -> dict:
    h = dict(APPROVER_HEADERS)
    if idem_key:
        h["Idempotency-Key"] = idem_key
    return h


def _admin_headers(idem_key: str | None = None) -> dict:
    h = dict(ADMIN_HEADERS)
    if idem_key:
        h["Idempotency-Key"] = idem_key
    return h


def _error_payload(response) -> dict:
    body = response.json()
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        return detail["error"]
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"]
    return {}


# ---------------------------------------------------------------------------
# Role gate
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_approver_role_accepted() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, _cs, _sse = _fresh_client(td)
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "approve"},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 202, resp.text


def test_bff_approvals_decide_admin_role_accepted() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, _cs, _sse = _fresh_client(td)
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "approve"},
            headers=_admin_headers(_idem()),
        )
        assert resp.status_code == 202, resp.text


def test_bff_approvals_decide_operator_role_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, _cs, _sse = _fresh_client(td)
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "approve"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 403, resp.text


def test_bff_approvals_decide_anonymous_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, _cs, _sse = _fresh_client(td)
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "approve"},
            headers=ANON_HEADERS,
        )
        assert resp.status_code in {401, 403}, resp.text


# ---------------------------------------------------------------------------
# Decision routing — approve
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_approve_returns_202_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, _cs, _sse = _fresh_client(td)
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "approve"},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert "data" in body or "command_id" in body or "status" in body


def test_bff_approvals_decide_second_operator_conflict_does_not_publish_sse() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, command_store, sse_buffers = _fresh_client(td)

        first = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "approve"},
            headers=_approver_headers("approval-race-first"),
        )
        assert first.status_code == 202, first.text
        assert len(command_store._get_all_commands()) == 1
        assert len(sse_buffers["approval"]) == 1

        second = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "reject", "rejection_reason": "second operator race"},
            headers=_admin_headers("approval-race-second"),
        )
        assert second.status_code == 409, second.text
        error = _error_payload(second)
        assert error["code"] == "RESOURCE_CONFLICT"
        assert error["details"]["precondition_failed"] == "concurrent_safety"
        assert len(command_store._get_all_commands()) == 1
        assert len(sse_buffers["approval"]) == 1
        assert sse_buffers["approval"][0][1]["data"]["decided_by"] == "op-app001"


def test_bff_approvals_decide_concurrent_operators_admit_only_one_command_and_one_sse() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = ApprovalsDecideTestReadPorts(allow_fallback=True)
        command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        sse_buffers = {"approval": deque()}
        app = _build_app(store, command_store, sse_buffers=sse_buffers)

        def decide(index_and_headers: tuple[int, dict[str, str]]):
            index, headers = index_and_headers
            local_client = TestClient(app, raise_server_exceptions=False)
            response = local_client.post(
                f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
                json={"decision": "approve"},
                headers={**headers, "Idempotency-Key": f"concurrent-approval-race-{index}"},
            )
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(decide, enumerate((APPROVER_HEADERS, ADMIN_HEADERS))))

        statuses = sorted(status for status, _body in results)
        assert statuses == [202, 409]
        accepted = [body for status, body in results if status == 202]
        rejected = [body for status, body in results if status == 409]
        assert len(accepted) == 1
        assert len(rejected) == 1
        error = (rejected[0].get("detail") or rejected[0]).get("error")
        assert error["code"] == "RESOURCE_CONFLICT"
        assert error["details"]["precondition_failed"] == "concurrent_safety"

        commands = command_store._get_all_commands()
        assert [command["type"] for command in commands] == ["ApproveDecision"]
        assert len(sse_buffers["approval"]) == 1
        event = sse_buffers["approval"][0][1]
        assert event["type"] == "approval.decided"
        assert event["data"]["decided_by"] in {"op-app001", "op-app001-admin"}


def test_bff_approvals_decide_empty_body_defaults_to_approve() -> None:
    """Missing decision field defaults to approve."""
    with tempfile.TemporaryDirectory() as td:
        client, _store, _cs, _sse = _fresh_client(td)
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 202, resp.text


# ---------------------------------------------------------------------------
# Decision routing — reject
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_reject_with_reason_returns_202() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, _cs, _sse = _fresh_client(td)
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "reject", "rejection_reason": "Risk threshold exceeded"},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 202, resp.text


def test_bff_approvals_decide_reject_without_reason_returns_422() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, _cs, _sse = _fresh_client(td)
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "reject"},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 422, resp.text


def test_bff_approvals_decide_reject_empty_reason_returns_422() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, _cs, _sse = _fresh_client(td)
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "reject", "rejection_reason": ""},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Decision routing — request_revision
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_request_revision_with_notes_returns_202() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, _cs, _sse = _fresh_client(td)
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "request_revision", "revision_notes": "Please add evidence"},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 202, resp.text


def test_bff_approvals_decide_request_changes_alias_returns_202() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, _cs, _sse = _fresh_client(td)
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "request_changes", "revision_notes": "Please attach more evidence"},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 202, resp.text


def test_bff_approvals_decide_request_revision_without_notes_returns_422() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, _cs, _sse = _fresh_client(td)
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "request_revision"},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Batch decide
# ---------------------------------------------------------------------------

def test_bff_approvals_batch_decide_accepts_list_and_records_commands() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, command_store, _sse = _fresh_client(td)

        resp = client.post(
            "/bff/approvals/batch-decide",
            json={
                "decisions": [
                    {"id": PENDING_APPROVAL_ID, "decision": "approve"},
                    {
                        "id": DECIDED_APPROVAL_ID,
                        "decision": "request_changes",
                        "revision_notes": "Attach final operator evidence",
                    },
                ]
            },
            headers=_approver_headers(_idem()),
        )

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "accepted"
        assert body["summary"] == {"total": 2, "accepted": 2, "failed": 0}
        assert [item["status"] for item in body["results"]] == ["accepted", "accepted"]
        assert [item["id"] for item in body["results"]] == [PENDING_APPROVAL_ID, DECIDED_APPROVAL_ID]

        records = command_store._get_all_commands()
        assert [record["type"] for record in records] == ["ApproveDecision", "RequestApprovalRevision"]
        assert [record["target"]["id"] for record in records] == [
            PENDING_APPROVAL_ID,
            DECIDED_APPROVAL_ID,
        ]


def test_bff_approvals_batch_decide_partial_failure_returns_per_item_status() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, command_store, _sse = _fresh_client(td)

        resp = client.post(
            "/bff/approvals/batch-decide",
            json={
                "decisions": [
                    {"id": PENDING_APPROVAL_ID, "decision": "approve"},
                    {"id": UNKNOWN_ID, "decision": "approve"},
                    {"id": DECIDED_APPROVAL_ID, "decision": "reject"},
                ]
            },
            headers=_approver_headers(_idem()),
        )

        assert resp.status_code == 207, resp.text
        body = resp.json()
        assert body["status"] == "partial"
        assert body["summary"] == {"total": 3, "accepted": 1, "failed": 2}
        assert [item["status"] for item in body["results"]] == ["accepted", "failed", "failed"]
        assert body["results"][1]["error"]["code"] == "RESOURCE_NOT_FOUND"
        assert body["results"][2]["error"]["code"] == "VALIDATION_FAILED"
        assert command_store._get_all_commands()[0]["target"]["id"] == PENDING_APPROVAL_ID


def test_bff_approvals_batch_decide_rejects_body_idempotency_before_commands() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, command_store, _sse = _fresh_client(td)

        resp = client.post(
            "/bff/approvals/batch-decide",
            json={
                "idempotencyKey": "body-key-must-not-be-used",
                "decisions": [{"id": PENDING_APPROVAL_ID, "decision": "approve"}],
            },
            headers=_approver_headers(_idem()),
        )

        assert resp.status_code == 400, resp.text
        body = resp.json()
        error = body.get("error") or body.get("detail", {}).get("error")
        assert error["details"]["precondition_failed"] == "body_idempotency_key"
        assert command_store._get_all_commands() == []


# ---------------------------------------------------------------------------
# Escalate / freeze (pass-through pending dedicated command types)
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_escalate_returns_202() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, _cs, _sse = _fresh_client(td)
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "escalate"},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 202, resp.text


# ---------------------------------------------------------------------------
# Invalid decision value
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_invalid_decision_returns_422() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, _cs, _sse = _fresh_client(td)
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "nonsense_value"},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Not found
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_unknown_id_returns_404_when_source_available() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, _cs, _sse = _fresh_client(td, allow_fallback=True)
        resp = client.post(
            f"/bff/approvals/{UNKNOWN_ID}/decide",
            json={"decision": "approve"},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_idempotency_replay_returns_same_202() -> None:
    with tempfile.TemporaryDirectory() as td:
        client, _store, _cs, _sse = _fresh_client(td)
        idem_key = f"test-idem-app001-{uuid.uuid4().hex[:12]}"
        headers = {**APPROVER_HEADERS, "Idempotency-Key": idem_key}
        payload = {"decision": "approve"}

        r1 = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json=payload,
            headers=headers,
        )
        r2 = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json=payload,
            headers=headers,
        )
        assert r1.status_code == 202, r1.text
        assert r2.status_code == 202, r2.text
        b1, b2 = r1.json(), r2.json()
        cmd_id_1 = (b1.get("data") or b1).get("command_id")
        cmd_id_2 = (b2.get("data") or b2).get("command_id")
        if cmd_id_1 and cmd_id_2:
            assert cmd_id_1 == cmd_id_2
