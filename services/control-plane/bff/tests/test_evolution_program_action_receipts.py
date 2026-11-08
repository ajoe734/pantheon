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

import base64
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
from services.control_plane.bff.command_adapters.base import _token_tenant
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

def _tenant_token(tenant: str) -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"tenant_id": tenant}).encode()).decode().rstrip("=")
    return f"Bearer h.{claims}.s"


@pytest.fixture(autouse=True)
def _authenticated_caller(monkeypatch):
    """Production always forwards the caller's JWT; direct adapter calls get one for tenant-e2e."""
    original = EvolutionCommandAdapter.execute

    def execute(self, command_id, command_type, params, auth_token=None, mfa_token=None):
        if not _token_tenant(auth_token):
            auth_token = _tenant_token("tenant-e2e")
        return original(self, command_id, command_type, params, auth_token=auth_token, mfa_token=mfa_token)

    monkeypatch.setattr(EvolutionCommandAdapter, "execute", execute)



_BINDING = {
    "tenant_id": "tenant-e2e", "target_type": "evolution_proposal", "target_id": "evp",
    "target_version": "2", "proposal_id": "evp", "proposal_revision": 2,
    "proposal_content_digest": "d" * 64, "risk_level": "medium",
}


def _owner_with_binding(receipt):
    """Owner double: the approval-binding read answers with a binding, every
    other call answers with ``receipt``.  Submit really does read the binding
    and file a governance request first, so the double must serve both."""
    def request(url, **kwargs):
        if url.endswith("/approval-binding"):
            return dict(_BINDING)
        return receipt
    return request


@pytest.fixture(autouse=True)
def _governance_request_filed(monkeypatch):
    """Governance is a separate service; record what the BFF files with it."""
    from services.control_plane.bff.governance import approval_owner
    filed = []
    monkeypatch.setattr(approval_owner, "propose",
                        lambda authorization, payload, key: filed.append(dict(payload)) or {"id": key})
    return filed


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
            "services.control_plane.bff.command_adapters.evolution_adapter.evolution_owner_request",
            side_effect=_owner_with_binding(expected_backend_receipt),
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

            call_headers = mock_http.call_args[1].get("headers", {})
            assert call_headers.get("Idempotency-Key") == "idem-key-1"
            assert "X-Idempotency-Key" not in call_headers
            assert "X-Tenant-Id" in call_headers

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
            "services.control_plane.bff.command_adapters.evolution_adapter.evolution_owner_request",
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


def test_adapter_propagates_idempotency_and_tenant_headers() -> None:
    adapter = EvolutionCommandAdapter()
    program_id = "evp-header-test"

    with patch.dict(os.environ, {
        "PANTHEON_EVOLUTION_API_URL": "http://mock-evolution:8000",
        "EVOLUTION_AUTH_TOKEN": "backend-auth-token",
        "EVOLUTION_DEFAULT_TENANT_ID": "tenant-env-default",
    }):
        with patch(
            "services.control_plane.bff.command_adapters.evolution_adapter.evolution_owner_request",
            side_effect=_owner_with_binding({"receipt_id": "rcpt-hdr", "program_id": program_id, "status": "active"}),
        ) as mock_http:
            adapter.execute(
                command_id="cmd-hdr-1",
                command_type="EvolutionProgramAction",
                params={
                    "action_id": "submit_evolution_review",
                    "program_id": program_id,
                    "idempotency_key": "unique-idemp-12345",
                    "tenant_id": "tenant-custom-xyz",
                },
                auth_token=_tenant_token("tenant-custom-xyz"),
            )

            assert mock_http.called
            kwargs = mock_http.call_args[1]
            headers = kwargs.get("headers", {})
            assert headers.get("Idempotency-Key") == "unique-idemp-12345"
            assert "X-Idempotency-Key" not in headers
            assert headers.get("X-Tenant-Id") == "tenant-custom-xyz"
            assert kwargs.get("caller_token") == _tenant_token("tenant-custom-xyz")


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
            "services.control_plane.bff.command_adapters.evolution_adapter.evolution_owner_request",
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
            "services.control_plane.bff.command_adapters.evolution_adapter.evolution_owner_request",
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

    def submit_action(entity_type, entity_id, action_id, resolved_key, identity, payload, *, authorization=None):
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
            "services.control_plane.bff.command_adapters.evolution_adapter.evolution_owner_request",
            return_value=backend_receipt,
        ):
            resp = client.post(
                f"/bff/evolution-programs/evp-role-2/actions/{action_id}",
                json={"reason": "Role test"} if action_id == "reject_program" else {},
            )
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
            "services.control_plane.bff.command_adapters.evolution_adapter.evolution_owner_request",
            side_effect=_owner_with_binding(backend_receipt),
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

    from services.evolution.test_program_owner import GovernedProgramService

    store = JsonProgramStore(tmp_path / "programs.json")
    service = GovernedProgramService(store)
    backend_app = FastAPI()
    backend_app.include_router(
        create_program_router(
            service=service,
            current_tenant=lambda: "tenant-e2e",
            authorize_request_tenant=lambda t: t or "tenant-e2e",
        )
    )
    backend_client = TestClient(backend_app)

    def dispatch_to_backend(url: str, method: str = "POST", payload: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, **kwargs):
        parsed = urlparse(url)
        path = parsed.path
        req_headers = {"X-Tenant-Id": "tenant-e2e"}
        if headers:
            req_headers.update(headers)
        if method.upper() == "POST":
            resp = backend_client.post(path, json=payload, headers=req_headers)
        else:
            resp = backend_client.get(path, headers=req_headers)

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
            "services.control_plane.bff.command_adapters.evolution_adapter.evolution_owner_request",
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

            # Test 6: Idempotent replay end-to-end via real router with header transport
            p2_init, _ = service.create_program(tenant_id="tenant-e2e", actor_id="admin-1", name="Idempotency E2E Program")
            p2_id = p2_init["program_id"]
            service.execute_action(tenant_id="tenant-e2e", actor_id="admin-1", program_id=p2_id, action_id="submit_evolution_review")
            service.execute_action(tenant_id="tenant-e2e", actor_id="admin-1", actor_role="approver", program_id=p2_id, action_id="approve_program")

            # First pause with idempotency key
            receipt_pause1 = adapter.execute(
                command_id="cmd-pause-1",
                command_type="EvolutionProgramAction",
                params={
                    "action_id": "pause_program",
                    "program_id": p2_id,
                    "actor_id": "op-e2e",
                    "idempotency_key": "e2e-idem-key-999",
                },
            )
            assert receipt_pause1["status"] == "paused"
            assert receipt_pause1["idempotent_replay"] is False

            # Second pause with the SAME idempotency key (header transported to real router/store)
            # Without header transport, pause on an already-paused program would raise 409 conflict.
            receipt_pause2 = adapter.execute(
                command_id="cmd-pause-2",
                command_type="EvolutionProgramAction",
                params={
                    "action_id": "pause_program",
                    "program_id": p2_id,
                    "actor_id": "op-e2e",
                    "idempotency_key": "e2e-idem-key-999",
                },
            )
            assert receipt_pause2["status"] == "paused"
            assert receipt_pause2["idempotent_replay"] is True
            assert receipt_pause2["receipt_id"] == receipt_pause1["receipt_id"]

            # Test 7: Divergent replay detection end-to-end via real router
            # Same idempotency key with different payload triggers 409 conflict
            with pytest.raises(ActionUnavailableError) as exc_info:
                adapter.execute(
                    command_id="cmd-pause-divergent",
                    command_type="EvolutionProgramAction",
                    params={
                        "action_id": "pause_program",
                        "program_id": p2_id,
                        "actor_id": "different-actor-divergent",
                        "idempotency_key": "e2e-idem-key-999",
                    },
                )
            assert exc_info.value.downstream_status == 409


# ---------------------------------------------------------------------------
# Draft Metadata Carrier Normalization, Idempotency & Security Regression
# (BFF-EVO-DRAFT-ACTION-CARRIER-20261106)
# ---------------------------------------------------------------------------

_TEST_TENANT = "tenant-a"
_TEST_JWT_ENV = {
    "PANTHEON_BFF_JWT_SECRET": "draft-action-carrier-test-secret-0123456789",
    "PANTHEON_BFF_JWT_ISSUER": "pantheon-bff-draft-tests",
    "PANTHEON_BFF_JWT_AUDIENCE": "bff-operators",
    "PANTHEON_BFF_AUTH_MODE": "strict",
}


def _make_test_jwt(sub: str, tenant: str = _TEST_TENANT, roles=("operator",)) -> str:
    from services.runtime_auth_inbound import encode_jwt_hs256

    claims: Dict[str, Any] = {
        "sub": sub,
        "roles": list(roles),
        "exp": 4102444800,
        "iss": _TEST_JWT_ENV["PANTHEON_BFF_JWT_ISSUER"],
        "aud": _TEST_JWT_ENV["PANTHEON_BFF_JWT_AUDIENCE"],
    }
    if tenant:
        claims["tenant_id"] = tenant
    return "Bearer " + encode_jwt_hs256(claims, secret=_TEST_JWT_ENV["PANTHEON_BFF_JWT_SECRET"])


def _observed_ui_create_constraint_payload(
    program_id: str = "evp-c0ce26091e8b443495bfa61e3dac34ff",
    expression: str = "max_drawdown <= 0.10",
) -> Dict[str, Any]:
    return {
        "command": "EvolutionProgramAction",
        "target": {
            "type": "EvolutionProgram",
            "id": program_id,
        },
        "action": "create_constraint",
        "params": {
            "name": expression,
            "operator": "<=",
            "value": expression,
            "expression": expression,
            "scope": "global",
            "payload": {
                "name": expression,
                "operator": "<=",
                "value": expression,
                "expression": expression,
                "scope": "global",
            },
            "memo": expression,
            "reason": expression,
            "action_id": "create_constraint",
            "entity_type": "evolution-program",
            "entity_id": program_id,
            "audit_event": "evolution.create_constraint",
            "frontend_source_route": "/bff/v1/commands",
        },
        "audit_context": {
            "reason": expression,
        },
    }


def _mount_bff_commands_with_real_evolution_owner(tmp_path, monkeypatch, program_id: str):
    from fastapi import HTTPException
    from services.control_plane.bff.auth.policy import bff_error, extract_identity
    from services.control_plane.bff.command_adapters.router import create_command_adapters_router
    from services.control_plane.bff.command_adapters.service import CommandAdapterService
    from services.control_plane.bff.command_queue import CommandStore
    from services.control_plane.bff.core.errors import register_error_handlers
    from services.evolution.program_router import create_program_router
    from services.evolution.program_service import ProgramService
    from services.evolution.program_store import JsonProgramStore

    for k, v in _TEST_JWT_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PANTHEON_EVOLUTION_API_URL", "http://evolution-draft-owner.local")

    evo_store = JsonProgramStore(str(tmp_path / "draft_programs.json"))
    evo_service = ProgramService(store=evo_store)
    fixed_hex = program_id.removeprefix("evp-")
    with patch("services.evolution.program_service.uuid.uuid4", return_value=MagicMock(hex=fixed_hex)):
        created, _ = evo_service.create_program(
            tenant_id=_TEST_TENANT,
            actor_id="op-creator",
            name="Private Workspace Evolution Draft",
        )
    assert created["program_id"] == program_id
    assert created["status"] == "draft"

    active_request_tenant = [_TEST_TENANT]
    evo_app = FastAPI()
    evo_app.include_router(
        create_program_router(
            service=evo_service,
            current_tenant=lambda: active_request_tenant[0],
            authorize_request_tenant=lambda t: t or active_request_tenant[0],
        )
    )
    evo_client = TestClient(evo_app)
    owner_post_calls: list[Dict[str, Any]] = []

    def dispatch_to_owner(
        url: str,
        *,
        caller_token=None,
        method: str = "GET",
        payload=None,
        headers=None,
        timeout: float = 5.0,
        tenant_id=None,
    ) -> Dict[str, Any]:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        req_headers = dict(headers or {})
        if tenant_id and "X-Tenant-Id" not in req_headers:
            req_headers["X-Tenant-Id"] = tenant_id
        active_request_tenant[0] = str(req_headers.get("X-Tenant-Id") or tenant_id or _TEST_TENANT)
        if method.upper() == "POST":
            owner_post_calls.append({"path": parsed.path, "payload": payload, "headers": req_headers})
            resp = evo_client.post(parsed.path, json=payload or {}, headers=req_headers)
        else:
            resp = evo_client.get(parsed.path, headers=req_headers)
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(
                url=url,
                code=resp.status_code,
                msg=resp.reason_phrase,
                hdrs={},
                fp=MagicMock(read=lambda: resp.content),
            )
        return resp.json()

    monkeypatch.setattr(
        "services.control_plane.bff.command_adapters.evolution_adapter.evolution_owner_request",
        dispatch_to_owner,
    )

    class _ProgramReadSurface:
        def get_evolution_program(self, pid: str):
            prog = evo_store.get_program(_TEST_TENANT, pid)
            if prog is not None:
                return prog
            return evo_store.get_program("tenant-other", pid)

    def strict_identity(authorization=None, mfa_token=None):
        raw = str(authorization or "").strip()
        if not raw:
            raise bff_error(401, "UNAUTHORIZED", "Missing bearer token", "Provide Authorization header")
        return extract_identity(raw, mfa_token=mfa_token)

    cmd_store = CommandStore(str(tmp_path / "bff-commands.jsonl"))
    cmd_service = CommandAdapterService(
        command_store=cmd_store,
        read_surface=_ProgramReadSurface(),
        extract_identity=strict_identity,
        bff_error=bff_error,
    )
    bff_app = FastAPI()
    register_error_handlers(bff_app)
    bff_app.include_router(
        create_command_adapters_router(
            service=cmd_service,
            submit_command_admission=cmd_service.submit_command_admission,
        )
    )
    return TestClient(bff_app, raise_server_exceptions=False), cmd_store, evo_service, owner_post_calls


def test_observed_ui_create_constraint_and_draft_metadata_actions_e2e(tmp_path, monkeypatch) -> None:
    from services.control_plane.bff.command_adapters.contracts import (
        canonicalize_wrapped_payload,
        normalize_b5_command_payload,
        normalize_operator_command_payload,
    )

    program_id = "evp-c0ce26091e8b443495bfa61e3dac34ff"
    raw_ui_payload = _observed_ui_create_constraint_payload(program_id=program_id)

    # 1. Contract normalization accepts exact observed UI payload without 422 createconstraint failure
    canonical = canonicalize_wrapped_payload(raw_ui_payload)
    assert canonical["command"] == "EvolutionProgramAction"
    assert canonical["action"] == "create_constraint"
    assert canonical["params"]["action_id"] == "create_constraint"
    cmd = normalize_b5_command_payload(normalize_operator_command_payload(canonical))
    assert cmd.action == "create_constraint"
    assert cmd.params["action_id"] == "create_constraint"

    client, cmd_store, evo_service, owner_post_calls = _mount_bff_commands_with_real_evolution_owner(
        tmp_path, monkeypatch, program_id=program_id
    )
    op_token = _make_test_jwt(sub="op-draft-author", tenant=_TEST_TENANT, roles=("operator",))

    # 2. First submission persists constraint once on draft program and preserves draft status
    resp1 = client.post(
        "/bff/v1/commands",
        json=raw_ui_payload,
        headers={"Authorization": op_token, "Idempotency-Key": "ui-draft-constraint-idem-1"},
    )
    assert resp1.status_code == 202, resp1.text
    body1 = resp1.json()
    assert body1["meta"]["idempotency"]["replayed"] is False
    rec1 = cmd_store.get_command_by_idempotency_key("ui-draft-constraint-idem-1", operator_id="op-draft-author")
    assert rec1 is not None and rec1["status"] == "executed"
    assert len(owner_post_calls) == 1

    prog_after_1 = evo_service.get_program(tenant_id=_TEST_TENANT, program_id=program_id)
    assert prog_after_1["status"] == "draft"
    assert prog_after_1["revision"] == 2
    assert len(prog_after_1["constraints"]) == 1
    assert prog_after_1["constraints"][0]["name"] == "max_drawdown <= 0.10"
    assert prog_after_1["constraints"][0]["operator"] == "<="
    assert prog_after_1["constraints"][0]["value"] == "max_drawdown <= 0.10"
    assert prog_after_1["constraints"][0]["scope"] == "global"

    # 3. Same-key replay returns non-duplicate result without extra owner writes
    resp_replay = client.post(
        "/bff/v1/commands",
        json=raw_ui_payload,
        headers={"Authorization": op_token, "Idempotency-Key": "ui-draft-constraint-idem-1"},
    )
    assert resp_replay.status_code == 202, resp_replay.text
    body_replay = resp_replay.json()
    assert body_replay["meta"]["idempotency"]["replayed"] is True
    assert body_replay["data"]["command_id"] == body1["data"]["command_id"]
    assert len(owner_post_calls) == 1
    prog_after_replay = evo_service.get_program(tenant_id=_TEST_TENANT, program_id=program_id)
    assert len(prog_after_replay["constraints"]) == 1
    assert prog_after_replay["revision"] == 2

    # 4. Divergent payload with same idempotency key fails closed with 409
    divergent_payload = _observed_ui_create_constraint_payload(
        program_id=program_id,
        expression="max_drawdown <= 0.05",
    )
    resp_divergent = client.post(
        "/bff/v1/commands",
        json=divergent_payload,
        headers={"Authorization": op_token, "Idempotency-Key": "ui-draft-constraint-idem-1"},
    )
    assert resp_divergent.status_code == 409, resp_divergent.text
    assert len(owner_post_calls) == 1
    assert len(evo_service.get_program(tenant_id=_TEST_TENANT, program_id=program_id)["constraints"]) == 1

    # 5. create_fitness_formula and create_mutation_rule also dispatch cleanly on draft program
    formula_payload = {
        "command": "EvolutionProgramAction",
        "target": {"type": "EvolutionProgram", "id": program_id},
        "action": "create_fitness_formula",
        "params": {
            "name": "Sharpe Ratio Formula",
            "expression": "sharpe - 0.5 * max_drawdown",
            "metrics": [{"name": "sharpe", "weight": 1.0}],
            "payload": {
                "name": "Sharpe Ratio Formula",
                "expression": "sharpe - 0.5 * max_drawdown",
                "metrics": [{"name": "sharpe", "weight": 1.0}],
            },
            "action_id": "create_fitness_formula",
            "entity_type": "evolution-program",
            "entity_id": program_id,
        },
        "audit_context": {"reason": "Add draft fitness formula"},
    }
    resp_formula = client.post(
        "/bff/v1/commands",
        json=formula_payload,
        headers={"Authorization": op_token, "Idempotency-Key": "ui-draft-formula-idem-1"},
    )
    assert resp_formula.status_code == 202, resp_formula.text
    assert cmd_store.get_command_by_idempotency_key("ui-draft-formula-idem-1", operator_id="op-draft-author")["status"] == "executed"

    rule_payload = {
        "command": "EvolutionProgramAction",
        "target": {"type": "EvolutionProgram", "id": program_id},
        "action": "create_mutation_rule",
        "params": {
            "expression": "Jitter lookback window",
            "scope": "feature_window",
            "rate": "0.15",
            "risk": "low",
            "payload": {
                "expression": "Jitter lookback window",
                "scope": "feature_window",
                "rate": "0.15",
                "risk": "low",
            },
            "action_id": "create_mutation_rule",
            "entity_type": "evolution-program",
            "entity_id": program_id,
        },
        "audit_context": {"reason": "Add draft mutation rule"},
    }
    resp_rule = client.post(
        "/bff/v1/commands",
        json=rule_payload,
        headers={"Authorization": op_token, "Idempotency-Key": "ui-draft-rule-idem-1"},
    )
    assert resp_rule.status_code == 202, resp_rule.text
    assert cmd_store.get_command_by_idempotency_key("ui-draft-rule-idem-1", operator_id="op-draft-author")["status"] == "executed"

    prog_final = evo_service.get_program(tenant_id=_TEST_TENANT, program_id=program_id)
    assert prog_final["status"] == "draft"
    assert len(prog_final["constraints"]) == 1
    assert len(prog_final["fitness_formulas"]) == 1
    assert prog_final["fitness_formulas"][0]["expression"] == "sharpe - 0.5 * max_drawdown"
    assert len(prog_final["mutation_rules"]) == 1
    assert prog_final["mutation_rules"][0]["expression"] == "Jitter lookback window"
    assert prog_final["mutation_rules"][0]["scope"] == "feature_window"


def test_draft_metadata_action_rejects_unknown_conflicting_and_unauthorized_calls(tmp_path, monkeypatch) -> None:
    program_id = "evp-c0ce26091e8b443495bfa61e3dac34ff"
    client, cmd_store, evo_service, owner_post_calls = _mount_bff_commands_with_real_evolution_owner(
        tmp_path, monkeypatch, program_id=program_id
    )
    op_token = _make_test_jwt(sub="op-draft-author", tenant=_TEST_TENANT, roles=("operator",))
    viewer_token = _make_test_jwt(sub="viewer-user", tenant=_TEST_TENANT, roles=("viewer",))
    foreign_token = _make_test_jwt(sub="op-foreign", tenant="tenant-foreign", roles=("operator",))

    # 1. Unknown action carriers are rejected with 422 and zero owner writes
    for idx, bad_override in enumerate([
        {"action": "invent_magic_action", "params": {**_observed_ui_create_constraint_payload(program_id)["params"], "action_id": "invent_magic_action"}},
        {"action": "create_constraint", "params": {**_observed_ui_create_constraint_payload(program_id)["params"], "action_id": "invent_magic_action"}},
        {"action": "create_constraint", "params": {**_observed_ui_create_constraint_payload(program_id)["params"], "payload": {"action_id": "invent_magic_action"}}},
    ]):
        bad_payload = {**_observed_ui_create_constraint_payload(program_id), **bad_override}
        resp_bad = client.post(
            "/bff/v1/commands",
            json=bad_payload,
            headers={"Authorization": op_token, "Idempotency-Key": f"bad-unknown-{idx}"},
        )
        assert resp_bad.status_code == 422, resp_bad.text

    # 2. Conflicting action carriers are rejected with 422 and zero owner writes
    for idx, conflict_override in enumerate([
        {"action": "create_constraint", "params": {**_observed_ui_create_constraint_payload(program_id)["params"], "action_id": "pause_program"}},
        {"command": "PauseEvolutionProgram", "action": "create_constraint"},
        {"action": "create_constraint", "params": {**_observed_ui_create_constraint_payload(program_id)["params"], "payload": {"action_id": "create_mutation_rule"}}},
    ]):
        conflict_payload = {**_observed_ui_create_constraint_payload(program_id), **conflict_override}
        resp_conflict = client.post(
            "/bff/v1/commands",
            json=conflict_payload,
            headers={"Authorization": op_token, "Idempotency-Key": f"bad-conflict-{idx}"},
        )
        assert resp_conflict.status_code == 422, resp_conflict.text

    # 3. Missing/invalid auth (noauth) -> 401
    resp_noauth = client.post(
        "/bff/v1/commands",
        json=_observed_ui_create_constraint_payload(program_id),
        headers={"Idempotency-Key": "noauth-draft-1"},
    )
    assert resp_noauth.status_code == 401, resp_noauth.text

    resp_bad_jwt = client.post(
        "/bff/v1/commands",
        json=_observed_ui_create_constraint_payload(program_id),
        headers={"Authorization": "Bearer invalid.jwt.token", "Idempotency-Key": "badjwt-draft-1"},
    )
    assert resp_bad_jwt.status_code == 401, resp_bad_jwt.text

    # 4. Viewer role -> 403 at BFF admission and at adapter boundary
    resp_viewer = client.post(
        "/bff/v1/commands",
        json=_observed_ui_create_constraint_payload(program_id),
        headers={"Authorization": viewer_token, "Idempotency-Key": "viewer-draft-1"},
    )
    assert resp_viewer.status_code == 403, resp_viewer.text

    adapter = EvolutionCommandAdapter()
    with pytest.raises(ActionUnavailableError) as viewer_exc:
        adapter.execute(
            command_id="cmd-viewer-direct",
            command_type="EvolutionProgramAction",
            params={"action_id": "create_constraint", "program_id": program_id, "name": "x <= 1"},
            auth_token=viewer_token,
        )
    assert viewer_exc.value.downstream_status == 403
    assert viewer_exc.value.error_code == "FORBIDDEN"

    # 5. Tenant mismatch (top-level and nested payload.tenant_id, plus cross-tenant JWT) -> 403/404 with zero writes
    for idx, mismatch_params in enumerate([
        {"tenant_id": "tenant-foreign"},
        {"payload": {"name": "max_drawdown <= 0.10", "tenant_id": "tenant-foreign"}},
    ]):
        mismatch_payload = _observed_ui_create_constraint_payload(program_id)
        mismatch_payload["params"] = {**mismatch_payload["params"], **mismatch_params}
        resp_mismatch = client.post(
            "/bff/v1/commands",
            json=mismatch_payload,
            headers={"Authorization": op_token, "Idempotency-Key": f"tenant-mismatch-draft-{idx}"},
        )
        assert resp_mismatch.status_code == 403, resp_mismatch.text
        assert resp_mismatch.json()["error"]["code"] == "TENANT_MISMATCH"

    assert owner_post_calls == []
    assert cmd_store._get_all_commands() == []

    resp_foreign = client.post(
        "/bff/v1/commands",
        json=_observed_ui_create_constraint_payload(program_id),
        headers={"Authorization": foreign_token, "Idempotency-Key": "foreign-jwt-draft-1"},
    )
    if resp_foreign.status_code == 202:
        foreign_rec = cmd_store.get_command_by_idempotency_key("foreign-jwt-draft-1", operator_id="op-foreign")
        assert foreign_rec is not None and foreign_rec["status"] == "failed"
    else:
        assert resp_foreign.status_code in (403, 404), resp_foreign.text

    prog_untouched = evo_service.get_program(tenant_id=_TEST_TENANT, program_id=program_id)
    assert prog_untouched["revision"] == 1
    assert prog_untouched["constraints"] == []

