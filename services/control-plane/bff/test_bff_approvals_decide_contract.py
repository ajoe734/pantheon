"""Contract tests for P0-APP-001: POST /bff/approvals/{id}/decide.

Verifies: role gate, decision routing (approve/reject/request_revision),
field validation, 404 when unknown id, idempotency replay, and 202 envelope.

These tests exercise the real composition root (``bff_main.app``), not a
test-local shadow app. The governance router's ``submit_action`` binding
used to be wired with a broken lambda whose positional parameter names did
not match ``GovernanceService.submit_governance_action``'s keyword-arg call
(and omitted ``command_type`` entirely), so every governance command route
returned a real 500 through ``bff_main.app`` from 2026-09-01 onward. That
binding now delegates to ``CommandAdapterService.submit_governance_action``,
the single product owner of the action_kind/action_id -> ObjectType/
CommandType mapping (see ``command_adapters/service.py``).
"""
from __future__ import annotations

import os
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import patch

os.environ.setdefault("RANKING_STORE_DSN", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("RANKING_STORE_BOOTSTRAP", "0")
os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
os.environ.setdefault("PANTHEON_BFF_AUTH_MODE", "permissive")

from fastapi.testclient import TestClient
from services.control_plane.bff import main as bff_main
from services.control_plane.bff.command_queue import CommandStore

APPROVER_HEADERS = {"Authorization": "Bearer op-app001:approver"}
ADMIN_HEADERS = {"Authorization": "Bearer op-app001-admin:admin"}
OPERATOR_HEADERS = {"Authorization": "Bearer op-app001-op:operator"}
ANON_HEADERS: dict = {}

PENDING_APPROVAL_ID = "appr-dec-c5a9f11e"
DECIDED_APPROVAL_ID = "approval-042"
UNKNOWN_ID = "unknown-approval-xyz"

_APPROVAL_DECISIONS: dict[str, dict[str, Any]] = {
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


def _fixture_get_approval_decision(decision_id: str | None) -> dict[str, Any] | None:
    return _APPROVAL_DECISIONS.get(str(decision_id or ""))


def _fixture_list_approval_decisions(**_kwargs: Any) -> list[dict[str, Any]]:
    return list(_APPROVAL_DECISIONS.values())


@contextmanager
def _client() -> Iterator[TestClient]:
    """Real composition root, with a private on-disk CommandStore and a
    fixture-backed approval-decisions read surface per test.

    The governance router captures ``app_deps.read_surface`` once at import
    time as a fixed object reference (not a re-resolved lookup), so the
    approval-decision read methods are patched directly on that instance
    rather than swapping the module-global ``bff_main.read_store`` (which the
    governance router never re-reads). The command admission seam
    (``CommandAdapterService.command_store``) does read the module-global
    ``bff_main.command_store`` dynamically on every call, so swapping that
    name isolates each test's command records without needing a second app.
    """
    original_command_store = bff_main.command_store
    read_surface = bff_main.app_deps.read_surface
    original_dataset_source = read_surface.dataset_source

    def _fixture_dataset_source(dataset: str) -> str:
        if dataset == "approval_decisions":
            return "local_snapshot"
        return original_dataset_source(dataset)

    with tempfile.TemporaryDirectory(prefix="gov-approval-contract-") as command_dir, patch.object(
        read_surface, "get_approval_decision", side_effect=_fixture_get_approval_decision
    ), patch.object(
        read_surface, "list_approval_decisions", side_effect=_fixture_list_approval_decisions
    ), patch.object(
        read_surface, "dataset_source", side_effect=_fixture_dataset_source
    ):
        bff_main.command_store = CommandStore(os.path.join(command_dir, "commands.jsonl"))
        try:
            yield TestClient(bff_main.app, raise_server_exceptions=False)
        finally:
            bff_main.command_store = original_command_store


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
    with _client() as client:
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "approve"},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 202, resp.text


def test_bff_approvals_decide_admin_role_accepted() -> None:
    with _client() as client:
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "approve"},
            headers=_admin_headers(_idem()),
        )
        assert resp.status_code == 202, resp.text


def test_bff_approvals_decide_operator_role_rejected() -> None:
    with _client() as client:
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "approve"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": _idem()},
        )
        assert resp.status_code == 403, resp.text


def test_bff_approvals_decide_anonymous_rejected() -> None:
    with _client() as client:
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
    with _client() as client:
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "approve"},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert "data" in body or "command_id" in body or "status" in body


def test_bff_approvals_decide_second_operator_conflict_does_not_publish_sse() -> None:
    with _client() as client:
        commands = bff_main.command_store

        first = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "approve"},
            headers=_approver_headers("approval-race-first"),
        )
        assert first.status_code == 202, first.text
        assert len(commands._get_all_commands()) == 1

        second = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "reject", "rejection_reason": "second operator race"},
            headers=_admin_headers("approval-race-second"),
        )
        assert second.status_code == 409, second.text
        error = _error_payload(second)
        assert error["code"] == "RESOURCE_CONFLICT"
        assert error["details"]["precondition_failed"] == "concurrent_safety"
        assert len(commands._get_all_commands()) == 1


def test_bff_approvals_decide_concurrent_operators_admit_only_one_command() -> None:
    with _client() as client:
        commands = bff_main.command_store

        def decide(index_and_headers: tuple[int, dict[str, str]]):
            index, headers = index_and_headers
            local_client = TestClient(bff_main.app, raise_server_exceptions=False)
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

        records = commands._get_all_commands()
        assert [record["type"] for record in records] == ["ApproveDecision"]


def test_bff_approvals_decide_empty_body_defaults_to_approve() -> None:
    """Missing decision field defaults to approve."""
    with _client() as client:
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
    with _client() as client:
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "reject", "rejection_reason": "Risk threshold exceeded"},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 202, resp.text


def test_bff_approvals_decide_reject_without_reason_returns_422() -> None:
    with _client() as client:
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "reject"},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 422, resp.text


def test_bff_approvals_decide_reject_empty_reason_returns_422() -> None:
    with _client() as client:
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
    with _client() as client:
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "request_revision", "revision_notes": "Please add evidence"},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 202, resp.text


def test_bff_approvals_decide_request_changes_alias_returns_202() -> None:
    with _client() as client:
        resp = client.post(
            f"/bff/approvals/{PENDING_APPROVAL_ID}/decide",
            json={"decision": "request_changes", "revision_notes": "Please attach more evidence"},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 202, resp.text


def test_bff_approvals_decide_request_revision_without_notes_returns_422() -> None:
    with _client() as client:
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
    with _client() as client:
        commands = bff_main.command_store

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

        records = commands._get_all_commands()
        assert [record["type"] for record in records] == ["ApproveDecision", "RequestApprovalRevision"]
        assert [record["target"]["id"] for record in records] == [
            PENDING_APPROVAL_ID,
            DECIDED_APPROVAL_ID,
        ]


def test_bff_approvals_batch_decide_partial_failure_returns_per_item_status() -> None:
    with _client() as client:
        commands = bff_main.command_store

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
        assert commands._get_all_commands()[0]["target"]["id"] == PENDING_APPROVAL_ID


def test_bff_approvals_batch_decide_rejects_body_idempotency_before_commands() -> None:
    with _client() as client:
        commands = bff_main.command_store

        resp = client.post(
            "/bff/approvals/batch-decide",
            json={
                "idempotencyKey": "body-key-must-not-be-used",
                "decisions": [{"id": PENDING_APPROVAL_ID, "decision": "approve"}],
            },
            headers=_approver_headers(_idem()),
        )

        assert resp.status_code == 400, resp.text
        error = _error_payload(resp)
        assert error["details"]["precondition_failed"] == "body_idempotency_key"
        assert commands._get_all_commands() == []


# ---------------------------------------------------------------------------
# Escalate / freeze (pass-through pending dedicated command types)
# ---------------------------------------------------------------------------

def test_bff_approvals_decide_escalate_returns_202() -> None:
    with _client() as client:
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
    with _client() as client:
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
    with _client() as client:
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
    with _client() as client:
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


# ---------------------------------------------------------------------------
# Reviews (POST /bff/reviews, POST /bff/reviews/{id}/actions/{action_id})
# ---------------------------------------------------------------------------

def test_bff_create_review_returns_202() -> None:
    with _client() as client:
        resp = client.post(
            "/bff/reviews",
            json={"review_id": f"review-{uuid.uuid4().hex[:8]}", "item_type": "strategy"},
            headers=_approver_headers(_idem()),
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        data = body.get("data") if isinstance(body, dict) else None
        assert (data or body).get("command", "").lower().find("review") != -1 or "command_id" in (data or body)


def test_bff_review_action_returns_202() -> None:
    with _client() as client:
        review_id = f"review-{uuid.uuid4().hex[:8]}"
        create_resp = client.post(
            "/bff/reviews",
            json={"review_id": review_id},
            headers=_approver_headers(_idem()),
        )
        assert create_resp.status_code == 202, create_resp.text

        action_resp = client.post(
            f"/bff/reviews/{review_id}/actions/approve",
            json={},
            headers=_approver_headers(_idem()),
        )
        assert action_resp.status_code == 202, action_resp.text
