"""BFF Evolution owner writes use the service credential + verified tenant.

Drives the real ``POST /bff/v1/commands`` admission stack and the real command
executor with an operator JWT against the real Evolution app running in
``EVOLUTION_AUTH_MODE=token``. The only seam is ``urllib.request.urlopen``,
which forwards the headers the BFF actually emitted to the Evolution app
without rewriting them, so the real Evolution auth middleware runs.
"""
from __future__ import annotations

import io
import os
import tempfile
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_tmp = tempfile.mkdtemp(prefix="evo_ws_auth_")
os.environ.setdefault("EVOLUTION_DATA_DIR", _tmp)
os.environ.setdefault("INCIDENT_DATA_DIR", _tmp)

from services.control_plane.bff import command_executor  # noqa: E402
from services.control_plane.bff.auth.policy import (  # noqa: E402
    bff_error,
    extract_identity_jwt,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.command_adapters.router import create_command_adapters_router  # noqa: E402
from services.control_plane.bff.command_adapters.service import (  # noqa: E402
    CommandAdapterService,
    pop_command_auth_context,
)
from services.control_plane.bff.command_queue import CommandStore  # noqa: E402
from services.control_plane.bff.core.errors import register_error_handlers  # noqa: E402
from services.control_plane.bff.models import CommandType, utc_now  # noqa: E402
from services.control_plane.bff.tests.conftest import ApprovalDecisionReadSurface  # noqa: E402
from services.evolution import main as evo_main  # noqa: E402
from services.evolution.main import app as evolution_app  # noqa: E402
from services.runtime_auth_inbound import encode_jwt_hs256  # noqa: E402

SERVICE_TOKEN = "pantheon-local-evolution-service"
TENANT = "tenant-dev"
JWT_ENV = {
    "PANTHEON_BFF_JWT_SECRET": "evo-write-auth-signing-secret-0123456789",
    "PANTHEON_BFF_JWT_ISSUER": "evo-write-auth-tests",
    "PANTHEON_BFF_JWT_AUDIENCE": "bff-operators",
    "PANTHEON_BFF_AUTH_MODE": "strict",
}
MEDIUM_FREEZE = dict(
    target_type="candidate_artifact",
    target_version="v1",
    action_type="freeze",
    rationale="drift incident inc-drift-ws",
    created_by_id="evolution-controller",
    target_stage="paper",
    tenant_id=TENANT,
    threshold_snapshots=[
        {
            "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.3",
            "signal_type": "feature_drift",
            "metric_name": "population_stability_index",
            "comparator": "gt",
            "observed_value": 0.31,
            "threshold_value": 0.30,
        }
    ],
)


def _jwt(tenant=TENANT, roles=("operator", "risk_owner"), sub="rita"):
    claims = {
        "sub": sub,
        "roles": list(roles),
        "exp": 4102444800,
        "iss": JWT_ENV["PANTHEON_BFF_JWT_ISSUER"],
        "aud": JWT_ENV["PANTHEON_BFF_JWT_AUDIENCE"],
    }
    if tenant:
        claims["tenant_id"] = tenant
    return encode_jwt_hs256(claims, secret=JWT_ENV["PANTHEON_BFF_JWT_SECRET"])


class _Resp:
    def __init__(self, status, content):
        self.status = status
        self._io = io.BytesIO(content)

    def read(self):
        return self._io.read()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k, v in JWT_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("EVOLUTION_AUTH_MODE", "token")
    monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("EVOLUTION_AUTH_ALLOWED_TENANTS", TENANT)
    monkeypatch.setenv("PANTHEON_EVOLUTION_API_URL", "http://evolution-owner.local")
    monkeypatch.setattr(
        evo_main, "store", evo_main.EvolutionDecisionStore(storage_path=str(tmp_path / "decisions.json"))
    )
    owner = TestClient(evolution_app)
    seen = []

    def transport(req, timeout=30):
        headers = {k: v for k, v in req.header_items()}
        path = "/" + req.full_url.split("://", 1)[1].split("/", 1)[1]
        seen.append({"method": req.get_method(), "path": path, "headers": headers})
        body = req.data
        resp = owner.request(req.get_method(), path, headers=headers, content=body)
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(req.full_url, resp.status_code, "err", {}, io.BytesIO(resp.content))
        return _Resp(resp.status_code, resp.content)

    monkeypatch.setattr(urllib.request, "urlopen", transport)

    service_headers = {"Authorization": f"Bearer {SERVICE_TOKEN}", "X-Tenant-Id": TENANT}
    did = f"evo-ws-{uuid.uuid4().hex[:8]}"
    r = owner.post(
        "/api/evolution/proposals",
        headers=service_headers,
        json={**MEDIUM_FREEZE, "decision_id": did, "target_id": f"artifact-{did}"},
    )
    assert r.status_code == 201, r.text

    commands = CommandStore(str(tmp_path / "commands.jsonl"))
    read_store = ApprovalDecisionReadSurface()
    read_store.seed_approval_decision(
        {
            "id": "apv-ws-1",
            "target_type": "evolution_execute",
            "target_id": did,
            "decision": "approved",
            "decision_state": "decided",
        }
    )

    def process(command_id):
        record = commands.get_command(command_id)
        runtime_auth = pop_command_auth_context(command_id)
        audit = record.get("audit") or {}
        status, result, error = command_executor.execute_command_with_status(
            command_id,
            CommandType(record["type"]),
            dict(record["params"]),
            auth_token=runtime_auth.get("auth_token") or audit.get("auth_token"),
            mfa_token=runtime_auth.get("mfa_token") or audit.get("mfa_token"),
        )
        commands.update_status(command_id, status, result=result, error=error)

    service = CommandAdapterService(
        command_store=lambda: commands,
        read_surface=lambda: read_store,
        extract_identity=extract_identity_jwt,
        require_operator_role=require_operator_role,
        require_read_role=require_read_role,
        bff_error=bff_error,
        utc_now_fn=utc_now,
        validators={},
        process_command_task=process,
    )
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_command_adapters_router(service=service))
    client = TestClient(app)
    yield client, owner, did, seen, commands


def _post(client, token, command, did, params, key):
    return client.post(
        "/bff/v1/commands",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key, "X-Tenant-Id": TENANT},
        json={
            "command": command,
            "target": {"type": "EvolutionDecision", "id": did},
            "params": params,
            "audit_context": {"reason": "evolution owner write service auth"},
        },
    )


def _run(commands, response):
    assert response.status_code == 202, response.text
    cid = response.json()["data"]["command_id"]
    return cid, commands.get_command(cid)


def test_review_then_approve_reaches_evolution_with_service_credential(env):
    client, owner, did, seen, commands = env
    token = _jwt()
    r = _post(client, token, "ReviewMutation", did,
              {"decision_id": did, "approval_decision_id": "apv-ws-1", "actor_role": "risk_owner"}, "k-review")
    _, rec = _run(commands, r)
    assert rec["status"] == "executed", rec.get("error")
    r = _post(client, token, "ApproveEvolutionDecision", did,
              {"evolution_decision_id": did, "approval_action": "approve",
               "approval_decision_id": "apv-ws-1", "actor_role": "risk_owner"}, "k-approve")
    _, rec = _run(commands, r)
    assert rec["status"] == "executed", rec.get("error")

    final = owner.get(f"/api/evolution/proposals/{did}",
                      headers={"Authorization": f"Bearer {SERVICE_TOKEN}", "X-Tenant-Id": TENANT}).json()
    assert final["decision_state"] == "approved"
    chain = {s["step_type"]: s for s in final["review_chain"]}
    for step in ("reviewed", "approved"):
        assert chain[step]["actor_id"] == "rita"
        assert chain[step]["actor_role"] == "risk_owner"

    writes = [c for c in seen if c["method"] == "POST"]
    assert len(writes) == 2
    for call in writes:
        auth = {k.lower(): v for k, v in call["headers"].items()}
        assert auth["authorization"] == f"Bearer {SERVICE_TOKEN}"
        assert auth["x-tenant-id"] == TENANT
        assert token not in auth["authorization"]


def test_missing_service_token_fails_closed_without_http(env, monkeypatch):
    client, _owner, did, seen, commands = env
    monkeypatch.delenv("EVOLUTION_AUTH_TOKEN")
    r = _post(client, _jwt(), "ReviewMutation", did,
              {"decision_id": did, "approval_decision_id": "apv-ws-1", "actor_role": "risk_owner"}, "k-nosvc")
    _, rec = _run(commands, r)
    assert rec["status"] == "failed"
    assert "EVOLUTION_AUTH_TOKEN" in rec["error"]["message"]
    assert not [c for c in seen if c["method"] == "POST"]


def test_payload_tenant_mismatch_and_unverified_tenant_never_reach_evolution(env):
    client, _owner, did, seen, commands = env
    with pytest.raises(command_executor.ActionUnavailableError) as exc:
        command_executor._execute_execute_mutation(
            "c", {"decision_id": did, "tenant_id": "other-tenant"}, auth_token=_jwt()
        )
    assert exc.value.detail["error"]["code"] == "TENANT_MISMATCH"
    with pytest.raises(command_executor.ActionUnavailableError):
        command_executor._execute_review_mutation(
            "c", {"decision_id": did, "approval_decision_id": "a"}, auth_token=_jwt(tenant=None)
        )
    assert not [c for c in seen if c["method"] == "POST"]


def test_foreign_actor_and_unheld_role_still_403(env):
    client, _owner, did, seen, _commands = env
    HTTPException = command_executor.ActionUnavailableError

    with pytest.raises(HTTPException) as exc:
        command_executor._execute_review_mutation(
            "c", {"decision_id": did, "approval_decision_id": "a", "actor_id": "someone-else"},
            auth_token=_jwt(),
        )
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        command_executor._execute_review_mutation(
            "c", {"decision_id": did, "approval_decision_id": "a", "actor_role": "governance_committee"},
            auth_token=_jwt(roles=("operator", "approver")),
        )
    assert exc.value.status_code == 403
    assert not seen


def test_replay_of_failed_command_returns_original_command_and_failure(env, monkeypatch):
    client, _owner, did, _seen, commands = env
    monkeypatch.delenv("EVOLUTION_AUTH_TOKEN")
    params = {"decision_id": did, "approval_decision_id": "apv-ws-1", "actor_role": "risk_owner"}
    token = _jwt()
    cid, rec = _run(commands, _post(client, token, "ReviewMutation", did, params, "k-replay"))
    assert rec["status"] == "failed"
    replay = _post(client, token, "ReviewMutation", did, params, "k-replay")
    body = replay.json()
    assert replay.status_code != 400, replay.text
    details = body["error"]["details"]
    assert details["command_id"] == cid
    assert details["failure"]["message"] == rec["error"]["message"]
    assert details["reason"] != "VALUE_ERROR"
