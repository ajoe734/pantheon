"""Mounted signed-JWT regressions for the P1 acceptance-3 rejection.

Independent review proved the generic ``POST /bff/v1/commands`` admission
path promoted a caller-controlled ``params.tenant_id`` into owner authority
whenever the signed JWT tenant claim was absent or ambiguous. These tests
port the reviewer's reproducer plus the sibling capital/research write paths
flagged in the same corrective, through real mounted routers with real
``extract_identity_jwt`` signed-JWT extraction -- no mocked identity.
"""

import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.runtime_auth_inbound import encode_jwt_hs256
from services.control_plane.bff.auth.policy import (
    extract_identity_jwt,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.command_adapters.router import create_command_adapters_router
from services.control_plane.bff.command_adapters.service import CommandAdapterService, process_command
from services.control_plane.bff.command_adapters.experiment_adapter import ExperimentCommandAdapter
from services.control_plane.bff import command_executor
from services.control_plane.bff.capital.router import create_capital_router
from services.control_plane.bff.capital.service import DefaultCapitalAuthority
from services.control_plane.bff.research.router import create_research_router
from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort
from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts
from services.research.write_owner import ResearchWriteOwner

from .test_domain_durability_corrective_p1 import AtomicIO, CASStore, TICKET_BODY

_SECRET = "isolated-generic-tenant-forgery-secret"


@pytest.fixture
def signed_headers(monkeypatch):
    for name, value in {
        "PANTHEON_BFF_AUTH_MODE": "strict",
        "PANTHEON_BFF_JWT_SECRET": _SECRET,
        "PANTHEON_BFF_JWT_ISSUER": "review",
        "PANTHEON_BFF_JWT_AUDIENCE": "review",
        "PANTHEON_BFF_MFA_REQUIRED": "false",
    }.items():
        monkeypatch.setenv(name, value)

    def make(shape, key, sub="shared-actor"):
        if shape == "valid":
            scope = {"tenant_id": "tenant-a"}
        elif shape == "ambiguous":
            scope = {"tenant_id": "tenant-b", "tid": "tenant-c"}
        else:
            scope = {}
        now = int(time.time())
        token = encode_jwt_hs256(
            {
                "sub": sub,
                "roles": ["operator"],
                "iss": "review",
                "aud": "review",
                "iat": now - 10,
                "exp": now + 300,
                **scope,
            },
            secret=_SECRET,
        )
        return {"Authorization": "Bearer " + token, "Idempotency-Key": key}

    return make


# --- 1. Generic POST /bff/v1/commands ExperimentAction cancel forgery -----


@pytest.mark.parametrize("shape", ["absent", "ambiguous"])
@pytest.mark.parametrize("restart", [False, True])
def test_generic_admission_cannot_promote_body_tenant(tmp_path, monkeypatch, signed_headers, shape, restart):
    tickets, experiments = CASStore(), CASStore()

    def owner():
        return ResearchWriteOwner(tickets_store=tickets, experiments_store=experiments, notes_store=AtomicIO())

    created = owner().create_research_experiment(
        ticket_id="",
        experiment_name="isolated tenant victim",
        strategy_selector={},
        parameter_set={},
        run_config={},
        launch_context={},
        tenant_id="tenant-a",
        actor_id="victim",
    )
    eid = created["experiment_id"]
    path = str(tmp_path / "commands.jsonl")
    store = CommandStore(path)
    service = CommandAdapterService(
        command_store=store,
        extract_identity=extract_identity_jwt,
        check_read_surface_state=lambda: None,
        process_command_task=lambda command_id: None,
    )
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=service))
    response = TestClient(app, raise_server_exceptions=False).post(
        "/bff/v1/commands",
        headers=signed_headers(shape, "generic-forged-tenant"),
        json={
            "command": "ExperimentAction",
            "target": {"type": "Experiment", "id": eid},
            "params": {"action_id": "cancel", "tenant_id": "tenant-a"},
            "audit_context": {"reason": "isolated negative authorization review"},
        },
    )
    assert response.status_code in (400, 403, 404, 422), response.text
    assert store._get_all_commands() == [], store._get_all_commands()
    assert experiments.get(eid)["status"] == "queued", experiments.get(eid)

    if restart:
        store = CommandStore(path)
        assert store._get_all_commands() == []

    adapter = ExperimentCommandAdapter(research_write_owner_factory=owner)
    calls = []

    def spy(command_id, command_type, params, **kwargs):
        calls.append((command_id, command_type, dict(params)))
        return adapter.execute(command_id, command_type, params, **kwargs)

    monkeypatch.setattr(command_executor, "dispatch_domain_command", spy)
    assert calls == []
    assert experiments.get(eid)["status"] == "queued"


# --- 2. Capital pool create: absent/ambiguous tenant, with/without forged --
# --- body tenant_id -----------------------------------------------------


@pytest.mark.parametrize("shape", ["absent", "ambiguous"])
@pytest.mark.parametrize("restart", [False, True])
def test_capital_pool_create_rejects_forged_body_tenant(tmp_path, signed_headers, shape, restart):
    # An unresolved caller tenant with NO body tenant is a legitimate
    # untenanted write (documented single-tenant/stub-auth deployment
    # mode -- see capital/service.py::_trusted_tenant_id and the many
    # PANTHEON_BFF_AUTH_STUB-backed contract tests that rely on it). Only
    # a caller-supplied body tenant that cannot be verified against the
    # authenticated identity must be rejected.
    calls = []
    executor = SimpleNamespace(
        create_capital_pool=lambda payload: (
            calls.append(dict(payload)),
            {"id": payload.get("pool_id", "pool-x"), "name": payload["name"], "status": "created"},
        )[1]
    )

    def make_client():
        store = CommandStore(str(tmp_path / "commands.jsonl"))
        app = FastAPI()
        app.include_router(
            create_capital_router(
                get_read_store=lambda: SimpleNamespace(command_store=store),
                get_capital_authority=lambda: DefaultCapitalAuthority(command_store=store, command_executor=executor),
                extract_identity=extract_identity_jwt,
                require_operator_role=require_operator_role,
            )
        )
        return TestClient(app, raise_server_exceptions=False), store

    client, store = make_client()
    payload = {"name": "isolated-forged-pool", "tenant_id": "tenant-a"}

    if restart:
        client, store = make_client()

    response = client.post("/bff/capital-pools", headers=signed_headers(shape, "forged-pool-key"), json=payload)
    assert response.status_code in (400, 403, 422), response.text
    assert calls == [], calls


@pytest.mark.parametrize("restart", [False, True])
def test_capital_pool_create_replay_isolated_by_authenticated_tenant(tmp_path, signed_headers, restart):
    calls = []
    executor = SimpleNamespace(
        create_capital_pool=lambda payload: (
            calls.append(dict(payload)),
            {"id": "pool-a", "name": payload["name"], "status": "created"},
        )[1]
    )

    def make_client():
        store = CommandStore(str(tmp_path / "commands.jsonl"))
        app = FastAPI()
        app.include_router(
            create_capital_router(
                get_read_store=lambda: SimpleNamespace(command_store=store),
                get_capital_authority=lambda: DefaultCapitalAuthority(command_store=store, command_executor=executor),
                extract_identity=extract_identity_jwt,
                require_operator_role=require_operator_role,
            )
        )
        return TestClient(app, raise_server_exceptions=False), store

    client, store = make_client()
    payload = {"name": "isolated-review", "tenant_id": "tenant-a"}
    first = client.post("/bff/capital-pools", headers=signed_headers("valid", "shared-key"), json=payload)
    assert first.status_code == 201, first.text
    assert len(calls) == 1

    if restart:
        client, store = make_client()

    for shape in ("absent", "ambiguous"):
        second = client.post("/bff/capital-pools", headers=signed_headers(shape, "shared-key"), json=payload)
        assert second.status_code in (400, 403, 404, 409, 422), second.text
    assert len(calls) == 1, calls


# --- 3. Experiment launch + retry: absent/ambiguous tenant, with/without --
# --- forged launch_context tenant ----------------------------------------


def _make_research_client(tickets, experiments):
    owner = ResearchWriteOwner(tickets_store=tickets, experiments_store=experiments, notes_store=AtomicIO())
    port = DefaultResearchKnowledgeSourcePort(research_write_owner=owner)
    app = FastAPI()
    app.include_router(
        create_research_router(
            read_surface=ReadSurfacePorts(research_knowledge_source=port),
            extract_identity=extract_identity_jwt,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=lambda s, c, m, *a, **kw: HTTPException(s, detail=m),
            utc_now=lambda: "2026-09-28T00:00:00Z",
        )
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("shape", ["absent", "ambiguous"])
@pytest.mark.parametrize("forged_launch_tenant", [None, "tenant-a"])
@pytest.mark.parametrize("restart", [False, True])
def test_experiment_launch_rejects_unresolved_tenant(monkeypatch, signed_headers, shape, forged_launch_tenant, restart):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", _SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "review")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "review")
    monkeypatch.setenv("PANTHEON_BFF_MFA_REQUIRED", "false")

    tickets, experiments = CASStore(), CASStore()
    client = _make_research_client(tickets, experiments)
    issued = client.post("/api/v1/research/tickets", headers=signed_headers("valid", "ticket-a"), json=TICKET_BODY)
    assert issued.status_code == 200, issued.text
    tid = issued.json()["ticket_id"]

    if restart:
        client = _make_research_client(tickets, experiments)

    launch = {
        "ticket_id": tid,
        "experiment_name": "isolated-forged-launch",
        "strategy_selector": {},
        "parameter_set": {},
        "run_config": {
            "dataset_ref": "isolated",
            "time_range": {"start_at": "2026-01-01", "end_at": "2026-01-02"},
            "execution_mode": "simulation",
            "requested_by": "review",
        },
    }
    if forged_launch_tenant:
        launch["launch_context"] = {"tenant_id": forged_launch_tenant}

    response = client.post("/api/v1/experiments/launch", headers=signed_headers(shape, "forged-launch"), json=launch)
    assert response.status_code in (400, 403, 422), response.text
    assert experiments.list_all() == [], experiments.list_all()


@pytest.mark.parametrize("shape", ["absent", "ambiguous"])
@pytest.mark.parametrize("restart", [False, True])
def test_experiment_retry_via_generic_command_rejects_unresolved_tenant(
    tmp_path, monkeypatch, signed_headers, shape, restart
):
    tickets, experiments = CASStore(), CASStore()

    def owner():
        return ResearchWriteOwner(tickets_store=tickets, experiments_store=experiments, notes_store=AtomicIO())

    created = owner().create_research_experiment(
        ticket_id="",
        experiment_name="isolated retry victim",
        strategy_selector={},
        parameter_set={},
        run_config={},
        launch_context={},
        tenant_id="tenant-a",
        actor_id="victim",
    )
    eid = created["experiment_id"]
    experiments.put(eid, {**experiments.get(eid), "status": "failed"})

    path = str(tmp_path / "commands.jsonl")
    store = CommandStore(path)
    service = CommandAdapterService(
        command_store=store,
        extract_identity=extract_identity_jwt,
        check_read_surface_state=lambda: None,
        process_command_task=lambda command_id: None,
    )
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=service))
    client = TestClient(app, raise_server_exceptions=False)

    if restart:
        store = CommandStore(path)

    response = client.post(
        "/bff/v1/commands",
        headers=signed_headers(shape, "forged-retry", sub="attacker"),
        json={
            "command": "ExperimentAction",
            "target": {"type": "Experiment", "id": eid},
            "params": {"action_id": "retry", "tenant_id": "tenant-a"},
            "audit_context": {"reason": "isolated negative authorization review"},
        },
    )
    assert response.status_code in (400, 403, 404, 422), response.text
    assert store._get_all_commands() == [], store._get_all_commands()
    row = experiments.get(eid)
    assert row["status"] == "failed", row
    assert row.get("tenant_id") == "tenant-a", row

    adapter = ExperimentCommandAdapter(research_write_owner_factory=owner)
    calls = []

    def spy(command_id, command_type, params, **kwargs):
        calls.append((command_id, command_type, dict(params)))
        return adapter.execute(command_id, command_type, params, **kwargs)

    monkeypatch.setattr(command_executor, "dispatch_domain_command", spy)
    assert calls == []
