"""RetirePaperPersonaCase command: admission, dry run, and adapter execution.

Functional evidence only: the provisioning store and coordinator are replaced
by in-process fakes.  Nothing here exercises a hosted environment.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.command_adapters import (
    CommandAdapterService,
    create_command_adapters_router,
    dispatch_domain_command,
)
from services.control_plane.bff.command_adapters import persona_adapter
from services.control_plane.bff.command_executor import execute_command_with_status
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import CommandStatus, CommandType, OperatorIdentity
from services.control_plane.bff.personas import service as persona_service

TENANT = "tenant-retire"
PERSONA = "persona-be19a1fac471247f2b2a"
TARGET = {"type": "Persona", "id": PERSONA}
HEADERS = {"Authorization": "Bearer op-test:operator:mfa"}


def _identity(*_args, **_kwargs) -> OperatorIdentity:
    return OperatorIdentity(
        operator_id="op-test", roles=["operator"], mfa_verified=True,
        claims={"tenant_id": TENANT, "sub": "op-test"}, token_kind="structured",
    )


class FakeRecord:
    def __init__(self, key: str, state: str = "paper_running", references: Optional[Dict[str, Any]] = None):
        self.idempotency_key = key
        self.state = state
        self.references = references or {}
        self.tenant_id = TENANT


class FakeStore:
    def __init__(self, record: Optional[FakeRecord]):
        self.record = record

    def get_by_persona(self, tenant_id: str, persona_id: str):
        assert tenant_id == TENANT
        return self.record


@pytest.fixture
def client_and_store(tmp_path):
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    svc = CommandAdapterService(command_store=store, read_surface=None, extract_identity=_identity)
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=svc, submit_command_admission=svc.submit_command_admission))
    return TestClient(app), store


def _issue_token(client, token="ct-retire-1"):
    issued = client.post(
        "/bff/confirm-tokens", headers={**HEADERS, "Idempotency-Key": f"issue-{token}"},
        json={"tokenId": token, "command": "RetirePaperPersonaCase", "target": TARGET,
              "reason": "retire learning persona", "ttlSeconds": 300},
    )
    assert issued.status_code == 201, issued.text
    return token


def _body(**overrides):
    body = {
        "command": "PersonaAction", "action": "retire", "target": TARGET, "params": {},
        "audit_context": {"reason": "negative share history cutover"},
    }
    body.update(overrides)
    return body


def _post(client, body, *, key="retire-key-1", token: Optional[str] = None):
    headers = {**HEADERS, "Idempotency-Key": key}
    if token:
        headers["X-Confirm-Token"] = token
    return client.post("/bff/v1/commands", headers=headers, json=body)


# ---- admission -------------------------------------------------------------

@pytest.mark.parametrize("verb", ["retire", "retire_case", "RetirePaperPersonaCase"])
def test_verbs_are_admitted_as_canonical_command(client_and_store, verb):
    client, store = client_and_store
    token = _issue_token(client)
    response = _post(client, _body(action=verb), token=token)
    assert response.status_code == 202, response.text
    records = [r for r in store._get_all_commands() if r["type"] == "RetirePaperPersonaCase"]
    assert len(records) == 1
    assert records[0]["target"] == TARGET


def test_confirmation_token_is_required(client_and_store):
    client, store = client_and_store
    response = _post(client, _body())
    assert response.status_code == 428, response.text
    assert [r for r in store._get_all_commands() if r["type"] == "RetirePaperPersonaCase"] == []


def test_audit_reason_is_required(client_and_store):
    client, _store = client_and_store
    token = _issue_token(client)
    with pytest.raises(Exception) as refused:
        _post(client, _body(audit_context={"reason": " "}), token=token)
    assert "reason is required" in str(refused.value)


def test_idempotency_key_is_required(client_and_store):
    client, _store = client_and_store
    token = _issue_token(client)
    response = client.post("/bff/v1/commands", headers={**HEADERS, "X-Confirm-Token": token}, json=_body())
    assert response.status_code in (400, 422), response.text


def test_non_persona_target_is_refused(client_and_store):
    client, _store = client_and_store
    token = _issue_token(client)
    response = _post(client, _body(target={"type": "Runtime", "id": "rt-1"}), token=token)
    assert response.status_code == 422, response.text


def test_catalog_gates_the_command_like_other_destructive_actions():
    from services.control_plane.bff.action_catalog import get_catalog_entry

    entry = get_catalog_entry("RetirePaperPersonaCase")
    assert entry.requires_confirm_token is True
    assert entry.idempotency_required is True
    assert list(entry.required_roles) == ["operator"]
    assert entry.entity_type == "Persona"


# ---- execution -------------------------------------------------------------

@pytest.fixture
def owner(monkeypatch):
    calls: List[Dict[str, Any]] = []
    state = SimpleNamespace(plan={
        "persona_id": PERSONA, "state": "paper_running", "release_case": False,
        "retirement_status": None, "planned_steps": list(persona_service.PERSONA_CASE_RETIREMENT_STEP_ORDER),
        "confirmed_steps": [],
    }, summary=None)

    def retire(tenant_id, persona_id, *, reason="x"):
        calls.append({"tenant_id": tenant_id, "persona_id": persona_id, "reason": reason})
        return state.summary

    monkeypatch.setattr(persona_adapter, "extract_identity_jwt", lambda *_a, **_k: _identity())
    monkeypatch.setattr(persona_service, "plan_persona_provisioning_case_retirement",
                        lambda tenant_id, persona_id: state.plan)
    monkeypatch.setattr(persona_service, "retire_persona_provisioning_case", retire)
    state.calls = calls
    return state


def _execute(params=None):
    return execute_command_with_status(
        "cmd-1", CommandType.RETIRE_PAPER_PERSONA_CASE,
        {"persona_id": PERSONA, "entity_type": "Persona", "action_id": "RetirePaperPersonaCase",
         "reason": "negative share history cutover", **(params or {})},
        auth_token="tok",
    )


def test_retired_summary_is_returned_in_receipt(owner):
    owner.summary = {"persona_id": PERSONA, "state": "retired", "retirement": {"status": "retired"}}
    status, result, error = _execute()
    assert status == CommandStatus.EXECUTED, error
    assert owner.calls == [{"tenant_id": TENANT, "persona_id": PERSONA, "reason": "negative share history cutover"}]
    assert result["domain_receipt"]["state"] == "retired"
    assert result["domain_receipt"]["retirement"]["status"] == "retired"
    assert result["entity_id"] == PERSONA


def test_pending_summary_carries_step_and_reason(owner):
    owner.summary = {"persona_id": PERSONA, "state": "paper_running", "retirement": {
        "status": "pending", "pending_step": "runtime_bindings", "pending_reason": "binding still active"}}
    status, result, _error = _execute()
    assert status == CommandStatus.EXECUTED
    retirement = result["domain_receipt"]["retirement"]
    assert retirement["status"] == "pending"
    assert retirement["pending_step"] == "runtime_bindings"
    assert retirement["pending_reason"] == "binding still active"


def test_missing_ledger_is_404_without_owner_write(owner):
    owner.plan = None
    status, result, error = _execute()
    assert status == CommandStatus.FAILED
    assert result is None
    assert error["code"] == "persona_case_not_found"
    assert error["downstream_status"] == 404
    assert owner.calls == []


def test_ledger_vanishing_before_retire_is_404(owner):
    owner.summary = None  # plan found a ledger but retire_case saw none
    status, _result, error = _execute()
    assert status == CommandStatus.FAILED
    assert error["code"] == "persona_case_not_found"
    assert error["downstream_status"] == 404


def test_release_case_is_refused_without_owner_write(owner):
    owner.plan = {**owner.plan, "release_case": True}
    status, _result, error = _execute()
    assert status == CommandStatus.FAILED
    assert error["code"] == "release_case_use_release_retire"
    assert owner.calls == []


def test_tenant_comes_from_authenticated_claims_only(owner):
    owner.summary = {"persona_id": PERSONA, "state": "retired", "retirement": {"status": "retired"}}
    _execute({"tenant_id": "tenant-spoofed", "tenant": "tenant-spoofed"})
    assert owner.calls[0]["tenant_id"] == TENANT


# ---- plan / release detection ----------------------------------------------

@pytest.mark.parametrize("key,release", [("dev-paper-release-20261101", True), ("persona-learning-2330", False)])
def test_plan_flags_release_cases_by_idempotency_key(monkeypatch, key, release):
    record = FakeRecord(key, references={"retirement": {
        "status": "pending", "first_evaluation_schedule": {"receipt": {}, "confirmed_at": "t"}}})
    monkeypatch.setattr(persona_service, "_persona_provisioning_store", lambda: FakeStore(record))
    plan = persona_service.plan_persona_provisioning_case_retirement(TENANT, PERSONA)
    assert plan["release_case"] is release
    assert plan["confirmed_steps"] == ["first_evaluation_schedule"]
    assert plan["planned_steps"] == list(persona_service.PERSONA_CASE_RETIREMENT_STEP_ORDER[1:])
    assert plan["retirement_status"] == "pending"


def test_plan_is_none_without_ledger(monkeypatch):
    monkeypatch.setattr(persona_service, "_persona_provisioning_store", lambda: FakeStore(None))
    assert persona_service.plan_persona_provisioning_case_retirement(TENANT, PERSONA) is None


def test_step_order_matches_the_coordinator_source():
    source = (Path(persona_service.__file__).parent.parent / "persona_provisioning_coordinator.py").read_text()
    start = source.index("def retire_case(")
    block = source[start:source.index("def _retire_schedule", start)]
    names = re.findall(r'^\s+\("([a-z_]+)", lambda r:', block, flags=re.M)
    assert tuple(names) == persona_service.PERSONA_CASE_RETIREMENT_STEP_ORDER


# ---- dry run ---------------------------------------------------------------

def _dry_run_client(tmp_path, monkeypatch, plan):
    monkeypatch.setattr(persona_service, "plan_persona_provisioning_case_retirement", lambda t, p: plan)
    coordinator_calls: List[Any] = []
    monkeypatch.setattr(persona_service, "retire_persona_provisioning_case",
                        lambda *a, **k: coordinator_calls.append((a, k)))
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    svc = CommandAdapterService(command_store=store, read_surface=None, extract_identity=_identity)
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=svc, submit_command_admission=svc.submit_command_admission))
    return TestClient(app), store, coordinator_calls


def test_dry_run_returns_plan_without_coordinator_call(tmp_path, monkeypatch):
    plan = {"persona_id": PERSONA, "state": "paper_running", "release_case": False,
            "retirement_status": None, "confirmed_steps": [],
            "planned_steps": list(persona_service.PERSONA_CASE_RETIREMENT_STEP_ORDER)}
    client, store, coordinator_calls = _dry_run_client(tmp_path, monkeypatch, plan)
    token = _issue_token(client)
    response = _post(client, _body(dryRun=True), token=token)
    assert response.status_code == 202, response.text
    meta = response.json()["meta"]
    assert meta["dryRun"] is True
    assert meta["plan"]["planned_steps"] == list(persona_service.PERSONA_CASE_RETIREMENT_STEP_ORDER)
    assert coordinator_calls == []
    assert [r for r in store._get_all_commands() if r["type"] == "RetirePaperPersonaCase"] == []


def test_dry_run_without_ledger_is_404_and_without_coordinator_call(tmp_path, monkeypatch):
    client, _store, coordinator_calls = _dry_run_client(tmp_path, monkeypatch, None)
    token = _issue_token(client)
    response = _post(client, _body(dryRun=True), token=token)
    assert response.status_code == 404, response.text
    assert "persona_case_not_found" in response.text
    assert coordinator_calls == []


def test_dry_run_refuses_release_case(tmp_path, monkeypatch):
    plan = {"persona_id": PERSONA, "release_case": True, "planned_steps": [], "confirmed_steps": []}
    client, _store, coordinator_calls = _dry_run_client(tmp_path, monkeypatch, plan)
    token = _issue_token(client)
    response = _post(client, _body(dryRun=True), token=token)
    assert response.status_code == 409, response.text
    assert "release_case_use_release_retire" in response.text
    assert coordinator_calls == []


# ---- deprecated route and contract -----------------------------------------

def test_deprecated_action_route_still_deprecates_retire():
    from services.control_plane.bff.personas.routes import lifecycle
    source = Path(lifecycle.__file__).read_text()
    allowed = re.search(
        r'action_id\.lower\(\)\.replace\("_", ""\)\.replace\("-", ""\) not in \{\s*(.*?)\s*\}',
        source, flags=re.S,
    ).group(1)
    assert "retire" not in allowed
    assert '"advancelifecycle", "promote", "promotecandidate", "demote"' in allowed


def test_contract_documents_the_command():
    doc = (Path(persona_service.__file__).parent.parent / "BFF_API_CONTRACT.md").read_text()
    assert "RetirePaperPersonaCase" in doc
    assert "persona_case_not_found" in doc
    assert "release_case_use_release_retire" in doc
