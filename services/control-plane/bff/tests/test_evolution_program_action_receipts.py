"""Tests for Evolution program lifecycle action dispatch and receipt readback (U8B).

Verifies:
- All 12 canonical lifecycle actions dispatched via EvolutionCommandAdapter
  to the Evolution service backend.
- build_domain_receipt structure: command_id, entity_type, entity_id, action_id,
  status, authoritative_readback, idempotent_replay, live_capital_side_effects: False.
- Role-gating in create_evolution_programs_router:
  * approver/admin required for approve_program, retire_program, freeze_generation,
    promote_candidate_paper, promote_candidate_live, approve_mutation, reject_mutation (403 otherwise).
  * operator sufficient for submit_evolution_review, pause_program, resume_program,
    complete_program, stop.
- Idempotency key forwarding and replay detection.
- Error propagation: 409 conflict, 422 validation, 503 unavailability.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.command_adapters.base import ActionUnavailableError
from services.control_plane.bff.command_adapters.evolution_adapter import (
    _CMD_TO_ACTION_ID,
    EvolutionCommandAdapter,
)
from services.control_plane.bff.evolution.router import (
    _APPROVER_PROGRAM_ACTIONS,
    create_evolution_programs_router,
)


_CANONICAL_ACTIONS = [
    "submit_evolution_review",
    "approve_program",
    "pause_program",
    "resume_program",
    "complete_program",
    "retire_program",
    "stop",
    "freeze_generation",
    "promote_candidate_paper",
    "promote_candidate_live",
    "approve_mutation",
    "reject_mutation",
]


class _FakeIdentity:
    def __init__(self, operator_id: str = "op-1", roles: Optional[list] = None, tenant_id: str = "tenant-a") -> None:
        self.operator_id = operator_id
        self.roles = roles or ["operator"]
        self.claims = {"tenant_id": tenant_id}


class _FakeReadStore:
    def __init__(self) -> None:
        self._programs: Dict[str, Dict[str, Any]] = {}

    def seed(self, program: Dict[str, Any]) -> None:
        self._programs[program["program_id"]] = program

    def list_evolution_programs(self):
        return list(self._programs.values())

    def get_evolution_program(self, program_id: Optional[str]):
        return self._programs.get(program_id) if program_id else None

    def list_evolution_program_runs(self, program_id: str):
        return []

    def list_evolution_program_candidates(self, program_id: str):
        return []


# ---------------------------------------------------------------------------
# Direct EvolutionCommandAdapter Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action_id", _CANONICAL_ACTIONS)
def test_adapter_dispatches_all_canonical_actions(action_id: str) -> None:
    adapter = EvolutionCommandAdapter()
    program_id = "evp-test-001"
    command_id = f"cmd-{action_id}"

    expected_backend_receipt = {
        "receipt_id": f"rcpt-{action_id}",
        "program_id": program_id,
        "action_id": action_id,
        "status": "active" if action_id == "approve_program" else "under_review",
        "program_status": "active" if action_id == "approve_program" else "under_review",
        "idempotent_replay": False,
        "details": {"action": action_id},
        "program": {
            "program_id": program_id,
            "status": "active" if action_id == "approve_program" else "under_review",
            "revision": 2,
        },
    }

    with patch.dict(os.environ, {"PANTHEON_EVOLUTION_API_URL": "http://mock-evolution:8000"}):
        with patch(
            "services.control_plane.bff.command_adapters.evolution_adapter.http_request_json",
            return_value=expected_backend_receipt,
        ) as mock_http:
            receipt = adapter.execute(
                command_id=command_id,
                command_type="EvolutionProgramAction",
                params={
                    "action_id": action_id,
                    "program_id": program_id,
                    "actor_id": "op-test",
                    "actor_role": "approver",
                    "idempotency_key": "idem-key-1",
                    "payload": {"param": "val"},
                },
                auth_token="Bearer mock-token",
            )

            assert mock_http.called
            call_url = mock_http.call_args[0][0]
            assert f"/api/evolution/programs/{program_id}/actions/{action_id}" in call_url

            call_payload = mock_http.call_args[1].get("payload", {})
            assert call_payload.get("actor_id") == "op-test"
            assert call_payload.get("actor_role") == "approver"
            assert call_payload.get("idempotency_key") == "idem-key-1"
            assert call_payload.get("payload") == {"param": "val"}

    # Validate build_domain_receipt structure
    assert receipt["command_id"] == command_id
    assert receipt["entity_type"] == "EvolutionProgram"
    assert receipt["entity_id"] == program_id
    assert receipt["action_id"] == action_id
    assert receipt["idempotent_replay"] is False
    assert receipt["live_capital_side_effects"] is False
    assert receipt["evolution_program_id"] == program_id
    assert receipt["receipt_id"] == f"rcpt-{action_id}"
    assert receipt["authoritative_readback"]["program_id"] == program_id
    assert receipt["authoritative_readback"]["action_id"] == action_id
    assert receipt["authoritative_readback"]["receipt_id"] == f"rcpt-{action_id}"


def test_adapter_handles_idempotent_replay() -> None:
    adapter = EvolutionCommandAdapter()
    program_id = "evp-test-replay"

    replayed_receipt = {
        "receipt_id": "rcpt-original",
        "program_id": program_id,
        "action_id": "pause_program",
        "status": "paused",
        "program_status": "paused",
        "idempotent_replay": True,
        "details": {"note": "Paused"},
        "program": {"program_id": program_id, "status": "paused", "revision": 3},
    }

    with patch.dict(os.environ, {"PANTHEON_EVOLUTION_API_URL": "http://mock-evolution:8000"}):
        with patch(
            "services.control_plane.bff.command_adapters.evolution_adapter.http_request_json",
            return_value=replayed_receipt,
        ):
            receipt = adapter.execute(
                command_id="cmd-replay-1",
                command_type="EvolutionProgramAction",
                params={
                    "action_id": "pause_program",
                    "program_id": program_id,
                    "idempotency_key": "same-key",
                },
            )

            assert receipt["idempotent_replay"] is True
            assert receipt["status"] == "paused"
            assert receipt["live_capital_side_effects"] is False


def test_adapter_propagates_409_conflict() -> None:
    adapter = EvolutionCommandAdapter()
    program_id = "evp-conflict"

    error_body = json.dumps({"detail": "Cannot pause program in status 'draft'"}).encode("utf-8")
    http_err = urllib.error.HTTPError(
        url="http://mock-evolution:8000",
        code=409,
        msg="Conflict",
        hdrs={},
        fp=MagicMock(read=lambda: error_body),
    )

    with patch.dict(os.environ, {"PANTHEON_EVOLUTION_API_URL": "http://mock-evolution:8000"}):
        with patch(
            "services.control_plane.bff.command_adapters.evolution_adapter.http_request_json",
            side_effect=http_err,
        ):
            with pytest.raises(ActionUnavailableError) as exc_info:
                adapter.execute(
                    command_id="cmd-err",
                    command_type="EvolutionProgramAction",
                    params={"action_id": "pause_program", "program_id": program_id},
                )
            assert exc_info.value.downstream_status == 409
            assert "Cannot pause program in status 'draft'" in str(exc_info.value)
            assert exc_info.value.entity_type == "EvolutionProgram"


def test_adapter_propagates_503_on_connection_failure() -> None:
    adapter = EvolutionCommandAdapter()
    program_id = "evp-down"

    with patch.dict(os.environ, {"PANTHEON_EVOLUTION_API_URL": "http://mock-evolution:8000"}):
        with patch(
            "services.control_plane.bff.command_adapters.evolution_adapter.http_request_json",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            with pytest.raises(ActionUnavailableError) as exc_info:
                adapter.execute(
                    command_id="cmd-down",
                    command_type="EvolutionProgramAction",
                    params={"action_id": "pause_program", "program_id": program_id},
                )
            assert exc_info.value.downstream_status == 503
            assert exc_info.value.retryable is True


# ---------------------------------------------------------------------------
# Router-Level Role Gating and Action Endpoint Tests
# ---------------------------------------------------------------------------

def _build_test_app(read_store: _FakeReadStore, identity_factory=None):
    adapter = EvolutionCommandAdapter()

    def submit_action(entity_type, entity_id, action_id, resolved_key, identity, payload):
        actor_id = getattr(identity, "operator_id", "op-1")
        roles = getattr(identity, "roles", ["operator"])
        actor_role = "approver" if ("approver" in roles or "admin" in roles) else "operator"
        return adapter.execute(
            command_id=f"cmd-{action_id}",
            command_type="EvolutionProgramAction",
            params={
                "action_id": action_id,
                "program_id": entity_id,
                "actor_id": actor_id,
                "actor_role": actor_role,
                "idempotency_key": resolved_key,
                "payload": payload,
            },
        )

    router = create_evolution_programs_router(
        get_read_store=lambda: read_store,
        extract_identity=identity_factory or (lambda auth: _FakeIdentity(roles=["operator"])),
        submit_program_action=submit_action,
    )
    app = FastAPI()

    @app.exception_handler(ActionUnavailableError)
    async def _handle_unavailable(request, exc: ActionUnavailableError):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=exc.downstream_status,
            content={"code": exc.error_code, "message": str(exc), "action_id": exc.action_id},
        )

    app.include_router(router)
    return app


@pytest.mark.parametrize("action_id", sorted(_APPROVER_PROGRAM_ACTIONS))
def test_router_blocks_operator_from_approver_actions(action_id: str) -> None:
    read_store = _FakeReadStore()
    read_store.seed({"program_id": "evp-role-1", "name": "Role Test", "status": "active"})
    # Identity has only "operator" role
    app = _build_test_app(read_store, identity_factory=lambda auth: _FakeIdentity(roles=["operator"]))
    client = TestClient(app)

    resp = client.post(f"/bff/evolution-programs/evp-role-1/actions/{action_id}", json={})
    assert resp.status_code == 403
    assert "approver" in resp.json().get("detail", {}).get("message", "").lower()


@pytest.mark.parametrize("action_id", sorted(_APPROVER_PROGRAM_ACTIONS))
def test_router_allows_approver_role_for_approver_actions(action_id: str) -> None:
    read_store = _FakeReadStore()
    read_store.seed({"program_id": "evp-role-2", "name": "Role Test", "status": "active"})
    # Identity has "operator" and "approver" roles
    app = _build_test_app(read_store, identity_factory=lambda auth: _FakeIdentity(roles=["operator", "approver"]))
    client = TestClient(app)

    backend_receipt = {
        "receipt_id": f"rcpt-{action_id}",
        "program_id": "evp-role-2",
        "action_id": action_id,
        "status": "active",
        "program_status": "active",
        "idempotent_replay": False,
        "details": {},
        "program": {"program_id": "evp-role-2", "status": "active", "revision": 2},
    }

    with patch.dict(os.environ, {"PANTHEON_EVOLUTION_API_URL": "http://mock-evolution:8000"}):
        with patch(
            "services.control_plane.bff.command_adapters.evolution_adapter.http_request_json",
            return_value=backend_receipt,
        ):
            resp = client.post(f"/bff/evolution-programs/evp-role-2/actions/{action_id}", json={})
            assert resp.status_code == 202
            data = resp.json()
            assert data["entity_id"] == "evp-role-2"
            assert data["action_id"] == action_id
            assert data["live_capital_side_effects"] is False


def test_router_allows_operator_for_non_approver_actions() -> None:
    read_store = _FakeReadStore()
    read_store.seed({"program_id": "evp-op-1", "name": "Op Test", "status": "draft"})
    app = _build_test_app(read_store, identity_factory=lambda auth: _FakeIdentity(roles=["operator"]))
    client = TestClient(app)

    backend_receipt = {
        "receipt_id": "rcpt-review-1",
        "program_id": "evp-op-1",
        "action_id": "submit_evolution_review",
        "status": "under_review",
        "program_status": "under_review",
        "idempotent_replay": False,
        "details": {},
        "program": {"program_id": "evp-op-1", "status": "under_review", "revision": 2},
    }

    with patch.dict(os.environ, {"PANTHEON_EVOLUTION_API_URL": "http://mock-evolution:8000"}):
        with patch(
            "services.control_plane.bff.command_adapters.evolution_adapter.http_request_json",
            return_value=backend_receipt,
        ):
            resp = client.post(
                "/bff/evolution-programs/evp-op-1/actions/submit_evolution_review",
                headers={"Idempotency-Key": "key-rev-1"},
                json={"note": "Ready for review"},
            )
            assert resp.status_code == 202
            data = resp.json()
            assert data["entity_id"] == "evp-op-1"
            assert data["action_id"] == "submit_evolution_review"


def test_router_returns_404_for_nonexistent_program() -> None:
    read_store = _FakeReadStore()
    app = _build_test_app(read_store)
    client = TestClient(app)

    resp = client.post("/bff/evolution-programs/evp-missing/actions/pause_program", json={})
    assert resp.status_code == 404


def test_adapter_end_to_end_with_real_program_router(tmp_path) -> None:
    from urllib.parse import urlparse
    from services.evolution.program_store import JsonProgramStore
    from services.evolution.program_service import ProgramService
    from services.evolution.program_router import create_program_router

    store = JsonProgramStore(tmp_path / "programs.json")
    service = ProgramService(store)
    backend_app = FastAPI()
    backend_app.include_router(
        create_program_router(
            service=service,
            current_tenant=lambda: "tenant-e2e",
            authorize_request_tenant=lambda t: t or "tenant-e2e",
        )
    )
    backend_client = TestClient(backend_app)

    def dispatch_to_backend(url: str, method: str = "POST", payload: Optional[Dict[str, Any]] = None, **kwargs):
        parsed = urlparse(url)
        path = parsed.path
        if method.upper() == "POST":
            resp = backend_client.post(path, json=payload, headers={"X-Tenant-Id": "tenant-e2e"})
        else:
            resp = backend_client.get(path, headers={"X-Tenant-Id": "tenant-e2e"})

        if resp.status_code >= 400:
            err_detail = resp.json().get("detail", resp.text) if resp.headers.get("content-type", "").startswith("application/json") else resp.text
            error_body = json.dumps({"detail": err_detail}).encode("utf-8")
            raise urllib.error.HTTPError(
                url=url,
                code=resp.status_code,
                msg=getattr(resp, "reason_phrase", "Error"),
                hdrs={},
                fp=MagicMock(read=lambda: error_body),
            )
        return resp.json()

    adapter = EvolutionCommandAdapter()

    # 1. Create a program and approve it to active
    p_init, _ = service.create_program(tenant_id="tenant-e2e", actor_id="admin-1", name="E2E Program")
    pid = p_init["program_id"]
    service.execute_action(tenant_id="tenant-e2e", actor_id="admin-1", program_id=pid, action_id="submit_evolution_review")
    service.execute_action(tenant_id="tenant-e2e", actor_id="admin-1", actor_role="approver", program_id=pid, action_id="approve_program")

    with patch.dict(os.environ, {"PANTHEON_EVOLUTION_API_URL": "http://evolution-backend"}):
        with patch(
            "services.control_plane.bff.command_adapters.evolution_adapter.http_request_json",
            side_effect=dispatch_to_backend,
        ):
            # Test 1: promote_candidate_paper through adapter to real router (flat fields parsed cleanly)
            receipt_paper = adapter.execute(
                command_id="cmd-promote-paper",
                command_type="EvolutionProgramAction",
                params={
                    "action_id": "promote_candidate_paper",
                    "program_id": pid,
                    "actor_id": "approver-e2e",
                    "actor_role": "approver",
                    "candidate_id": "cand-e2e-001",
                    "run_id": "run-e2e-001",
                    "artifact_id": "art-e2e-001",
                    "artifact_version": "v1.0.0",
                },
            )
            assert receipt_paper["status"] == "active"
            assert receipt_paper["live_capital_side_effects"] is False
            details_paper = receipt_paper["domain_receipt"]["details"]
            assert details_paper["candidate_id"] == "cand-e2e-001"
            assert details_paper["run_id"] == "run-e2e-001"
            assert details_paper["artifact_id"] == "art-e2e-001"
            assert details_paper["stage"] == "paper"

            # Test 2: approve_mutation through adapter to real router (real mutation_id preserved!)
            receipt_mutation = adapter.execute(
                command_id="cmd-approve-mut",
                command_type="EvolutionProgramAction",
                params={
                    "action_id": "approve_mutation",
                    "program_id": pid,
                    "actor_id": "approver-e2e",
                    "actor_role": "approver",
                    "mutation_id": "mut-real-explicit-12345",
                },
            )
            assert receipt_mutation["status"] == "active"
            details_mut = receipt_mutation["domain_receipt"]["details"]
            assert details_mut["mutation_id"] == "mut-real-explicit-12345"
            assert details_mut["decision"] == "approved"

            # Test 3: approve_mutation without mutation_id fails with 422 validation error
            with pytest.raises(ActionUnavailableError) as exc_info:
                adapter.execute(
                    command_id="cmd-approve-mut-missing",
                    command_type="EvolutionProgramAction",
                    params={
                        "action_id": "approve_mutation",
                        "program_id": pid,
                        "actor_id": "approver-e2e",
                        "actor_role": "approver",
                    },
                )
            assert exc_info.value.downstream_status == 422
            assert "mutation_id is required" in str(exc_info.value)

            # Test 4: stop transitions to stopped
            receipt_stop = adapter.execute(
                command_id="cmd-stop-e2e",
                command_type="EvolutionProgramAction",
                params={
                    "action_id": "stop",
                    "program_id": pid,
                    "actor_id": "op-e2e",
                },
            )
            assert receipt_stop["status"] == "stopped"

            # Test 5: resume_program on stopped program fails with 409 conflict
            with pytest.raises(ActionUnavailableError) as exc_info:
                adapter.execute(
                    command_id="cmd-resume-stopped",
                    command_type="EvolutionProgramAction",
                    params={
                        "action_id": "resume_program",
                        "program_id": pid,
                        "actor_id": "op-e2e",
                    },
                )
            assert exc_info.value.downstream_status == 409
            assert "cannot resume stopped program" in str(exc_info.value).lower()
