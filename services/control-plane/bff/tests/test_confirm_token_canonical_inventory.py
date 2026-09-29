"""Inventory-wide mounted regression for admission-canonicalized confirm tokens.

DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001: mounted admission rewrites
``params.confirm_token`` to durable ``params.confirm_token_id``
(``canonicalize_validated_precondition_evidence``). Every executor/adapter
reachable from ``POST /bff/v1/commands`` must consume that canonical value
through ``command_adapters.base.canonical_confirm_token``. This module
iterates the whole inventory (direct command and RuntimeAction wrapper, before
and after a ``CommandStore`` restart) so a missed reader fails here, plus a
static guard that no raw ``confirm_token`` reads remain.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff import command_executor
from services.control_plane.bff.action_catalog import get_catalog_entry
from services.control_plane.bff.auth.policy import extract_identity_jwt
from services.control_plane.bff.command_adapters import create_command_adapters_router
from services.control_plane.bff.command_adapters import runtime_adapter
from services.control_plane.bff.command_adapters.service import CommandAdapterService, process_command
from services.control_plane.bff.command_queue import CommandStore
from services.runtime_auth_inbound import encode_jwt_hs256

_BFF_DIR = Path(__file__).resolve().parents[1]

# (direct command, RuntimeAction action_id or None when there is no wrapper spelling)
INVENTORY = [
    ("StartRuntime", "start"),
    ("RestartPaperRuntime", "RestartPaperRuntime"),
    ("RestartTelemetryBridge", "RestartTelemetryBridge"),
    ("StartPaperMonitoringSession", "StartPaperMonitoringSession"),
    ("ProbeTelemetryIngest", "ProbeTelemetryIngest"),
    ("AdvanceLifecycle", None),
]
CASES = [(cmd, wrap, wrapped) for cmd, wrap in INVENTORY for wrapped in (False, True) if not (wrapped and wrap is None)]


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("command,wrap_action,wrapped", CASES)
def test_mounted_confirm_token_reaches_every_executor(tmp_path, monkeypatch, command, wrap_action, wrapped, restart):
    secret = "test-confirm-token-inventory-secret"
    aud = "confirm-token-inventory"
    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", "http://confirm-token-inventory.invalid")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", aud)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", aud)
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")

    now = int(time.time())
    roles = ["operator", "runtime_operator", "live_owner_approver"]

    def jwt(actor: str) -> str:
        return encode_jwt_hs256(
            {"sub": actor, "roles": roles, "iss": aud, "aud": aud, "iat": now - 10, "exp": now + 300, "tenant_id": "tenant-a"},
            secret=secret,
        )

    actor = "inventory-actor"
    entry = get_catalog_entry(command)
    is_persona = command == "AdvanceLifecycle"
    target_type = "Persona" if is_persona else "Runtime"
    target_id = "persona-inventory" if is_persona else "rt-inventory"
    binding = {
        "runtime_id": target_id,
        "binding_id": "bind-inventory",
        "deployment_mode": "paper",
        "status": "active",
        "metadata": {"tenant_id": "tenant-a"},
    }
    calls: List[Dict[str, Any]] = []

    def fake_http(url, payload=None, **kwargs):
        if payload is not None:
            kwargs["payload"] = payload
        calls.append({"url": url, "payload": kwargs.get("payload")})
        return {"status": "accepted", "audit_id": "audit-inventory"}

    read_store_stub = SimpleNamespace(
        get_approval_decision=lambda _: {
            "outcome": "approved",
            "command": command,
            "target": {"type": target_type, "id": target_id},
        },
        get_runtime_binding_by_runtime_id=lambda rid: dict(binding) if rid == target_id else None,
        get_runtime_binding=lambda bid: dict(binding) if bid == "bind-inventory" else None,
    )
    command_path = str(tmp_path / "commands.jsonl")
    store = CommandStore(command_path)
    service = CommandAdapterService(
        command_store=store,
        extract_identity=extract_identity_jwt,
        check_read_surface_state=lambda: None,
        process_command_task=lambda command_id: None,
        get_read_store=lambda: read_store_stub,
    )
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=service))
    from services.control_plane.bff.control_loops.router import create_control_loops_router

    app.include_router(
        create_control_loops_router(
            extract_identity=extract_identity_jwt,
            submit_final_command_admission=service.submit_command_admission,
            submit_sem_command=service.sem_command_response,
        )
    )
    client = TestClient(app)
    monkeypatch.setattr(command_executor, "_post_json", fake_http)
    monkeypatch.setattr(runtime_adapter, "http_request_json", fake_http)
    monkeypatch.setattr(runtime_adapter, "_get_read_store", lambda: read_store_stub)

    token_id = f"inventory-token-{command}-{wrapped}-{restart}"
    issued = client.post(
        "/bff/confirm-tokens",
        headers={"Authorization": "Bearer " + jwt(actor), "Idempotency-Key": "issue-" + token_id},
        json={
            "tokenId": token_id,
            "ttlSeconds": 300,
            "command": command,
            "target_type": target_type,
            "target_id": target_id,
            "operator_id": actor,
        },
    )
    assert issued.status_code == 201, issued.text

    params: Dict[str, Any] = {"target_state": "paper_owner"}
    if is_persona:
        params["persona_id"] = target_id
    else:
        params["runtime_id"] = target_id
    if getattr(entry, "requires_approval", False):
        params["approval_decision_id"] = "approval-inventory"
    if getattr(entry, "requires_two_man", False):
        signature_id = "tms-inventory-" + token_id
        for signer in (actor, "second-inventory-operator"):
            signed = client.post(
                f"/bff/v5/interventions/{signature_id}/two-man-sign",
                headers={"Authorization": "Bearer " + jwt(signer), "Idempotency-Key": f"sign-{signature_id}-{signer}"},
                json={
                    "twoManSignatureId": signature_id,
                    "command": command,
                    "target": {"type": target_type, "id": target_id},
                    "reason": "inventory evidence",
                },
            )
            assert signed.status_code == 202, signed.text
        params["two_man_signature_id"] = signature_id
    if wrapped:
        params["action_id"] = wrap_action

    submitted_type = "RuntimeAction" if wrapped else command
    response = client.post(
        "/bff/v1/commands",
        headers={
            "Authorization": "Bearer " + jwt(actor),
            "Idempotency-Key": "cmd-" + token_id,
            "X-Confirm-Token": token_id,
        },
        json={
            "command": submitted_type,
            "target": {"type": target_type, "id": target_id},
            "params": params,
            "audit_context": {"reason": "confirm-token inventory regression"},
        },
    )
    rows = [row for row in store._get_all_commands() if row["type"] == submitted_type]
    if restart:
        store = CommandStore(command_path)
    for row in rows:
        asyncio.run(process_command(row["command_id"], command_store=store))
    final = store.get_command(rows[0]["command_id"]) if rows else {}
    evidence = {"status_code": response.status_code, "calls": calls, "status": final.get("status"), "error": final.get("error")}
    assert response.status_code == 202, evidence
    assert final.get("status") == "executed", evidence
    assert len(calls) == 1, evidence
    assert calls[0]["payload"].get("confirm_token") == token_id, evidence


def test_no_raw_confirm_token_param_reads_remain_in_executors_and_adapters():
    pattern = re.compile(r"""params(?:\.get\(|\[)\s*["']confirm_token["']""")
    offenders = []
    files = [_BFF_DIR / "command_executor.py", *sorted((_BFF_DIR / "command_adapters").glob("*.py"))]
    for path in files:
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line) and "canonical_confirm_token" not in line:
                # base.canonical_confirm_token is the single sanctioned reader.
                if path.name == "base.py":
                    continue
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, offenders
