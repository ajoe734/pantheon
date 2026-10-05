"""Current default Persona HTTP port must project actual served SESSION_STORE.
Offline local same-process session proof; NOT restart durability/provider acceptance.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import urllib.error
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from services.persona import write_owner
from services.persona.write_owner import CreatePersonaRequest, PersistentPersonaOwner, create_app
from services.runtime_auth_inbound import encode_jwt_hs256
from services.control_plane.bff.core import owner_reads
from services.control_plane.bff.ports.persona_write_owner import PersonaRegistryHttpWritePort
from services.control_plane.bff.ports.persona_training import PersonaRegistryReadsPort

_SECRET = "coordinator-isolated-trade-journal-test-only-secret"
_PATH = "/api/personas/p-alpha/trade-journal/ep-1/reflection:retry"


class _Provider:
    name = "isolated-data-only-control"
    model = "not-hosted"

    def __init__(self):
        self.calls = 0
        self.started = threading.Event()
        self.second_started = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.lock = threading.Lock()

    def reflect(self, *, facts, trigger):
        with self.lock:
            self.calls += 1
        return {
            "expected_vs_actual": {"thesis": "supported", "entry_quality": "good"},
            "attribution": "process",
            "counterfactuals": [{"alternative_action": "wait", "estimated_impact": "higher", "assumptions": "same"}],
            "lesson_candidates": [{"scope": "strategy", "proposed_change": "fine tune", "confidence": 0.8}],
            "mistakes": [],
            "what_worked": ["timing"],
            "unknowns": [],
            "followups": [],
        }


def auth(*, tenant="tenant-1"):
    claims = {"sub": "local-operator", "roles": ["operator"], "exp": 4102444800}
    if tenant is not None:
        claims["tenant_id"] = tenant
    return {"Authorization": "Bearer " + encode_jwt_hs256(claims, secret=_SECRET), "Idempotency-Key": "local-key"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")
    monkeypatch.setenv("PERSONA_JWT_SECRET", _SECRET)
    monkeypatch.setenv("PERSONA_JWT_ISSUER", "")
    monkeypatch.setenv("PERSONA_JWT_AUDIENCE", "")
    monkeypatch.setenv("PERSONA_STORE_PATH", str(tmp_path / "personas.json"))
    monkeypatch.setenv("PERSONA_CAPABILITY_STORE_PATH", str(tmp_path / "capability.json"))
    monkeypatch.setenv("PERSONA_TRAINING_TARGET_STORE_PATH", str(tmp_path / "training.json"))
    owner = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
    owner.create(CreatePersonaRequest(actor_id="local-operator", persona_id="p-alpha", name="Control", mandate="Paper only", tenant_id="tenant-1"))
    provider = _Provider()
    counts = {"fetch": 0, "read": 0}

    def fetch(episode, tenant, authorization):
        counts["fetch"] += 1
        return {"trade_episode_id": episode, "persona_id": "p-alpha", "tenant_id": "tenant-1", "status": "closed", "realized_pnl": 100.0}

    app = create_app(owner, reflection_provider=provider, telemetry_fetcher=fetch)
    return TestClient(app), owner, provider, counts, _PATH


@pytest.fixture
def served_parent(setup, monkeypatch):
    import uuid
    client, _, _, _, _ = setup
    response = client.post(_PATH, headers=auth(), json={"reason": "local shared-JSON parent scope control"})
    assert response.status_code == 202
    persona_dir = Path(write_owner.__file__).resolve().parents[1] / "control-plane/persona"
    for dep_name in ["persona_policy_resolver", "persona_registry"]:
        if dep_name not in sys.modules:
            dep_spec = importlib.util.spec_from_file_location(dep_name, persona_dir / f"{dep_name}.py")
            if dep_spec and dep_spec.loader:
                dep_mod = importlib.util.module_from_spec(dep_spec)
                sys.modules[dep_name] = dep_mod
                dep_spec.loader.exec_module(dep_mod)

    name = "coordinator_actual_persona_parent_" + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(name, persona_dir / "main.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

    owner = module.PERSONA_OWNER_API.state.persona_owner
    counts = {"private_get": 0, "private_list": 0}
    original = owner.get
    original_list = owner.list

    def counted(persona_id):
        counts["private_get"] += 1
        return original(persona_id)

    def counted_list(*args, **kwargs):
        counts["private_list"] += 1
        return original_list(*args, **kwargs)

    monkeypatch.setattr(owner, "get", counted)
    monkeypatch.setattr(owner, "list", counted_list)
    yield TestClient(module.app), counts
    sys.modules.pop(name, None)


class ActualResponse:
    def __init__(self, response):
        self.response = response
        self.status = response.status_code

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.response.content


def test_default_bff_port_projects_owned_actual_served_persona_session(served_parent):
    client, _ = served_parent
    route = next(r for r in client.app.routes if getattr(r, "path", None) == "/api/sessions" and "GET" in r.methods)
    actual = route.endpoint.__globals__
    session = actual["SessionPersona"](
        session_id="isolated-existing-owner-session",
        persona_id="p-alpha",
        session_type=actual["SessionType"].CONSULT.value,
        status=actual["SessionStatus"].ACTIVE.value,
        started_at=actual["utc_now"](),
        capability_snapshot_id="isolated-session-snapshot-ref",
        trace_id="isolated-session-trace",
        request_id="isolated-session-request",
    )
    actual["SESSION_STORE"].create(session)
    headers = auth()
    direct = client.get("/api/sessions", params={"persona_id": "p-alpha"}, headers=headers)
    assert direct.status_code == 200, direct.text
    assert {r["session_id"] for r in direct.json()} == {session.session_id}
    observed = []

    def transport(request, **_):
        u = urlsplit(request.full_url)
        response = client.get(u.path + ("?" + u.query if u.query else ""), headers=dict(request.header_items()))
        observed.append(response.status_code)
        if response.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, response.status_code, "actual served parent rejected", None, None)
        return ActualResponse(response)

    token = owner_reads.authorization.set(headers["Authorization"])
    tenant = owner_reads.selected_tenant.set("tenant-1")
    try:
        actual_default = PersonaRegistryHttpWritePort(base_url="http://127.0.0.1:1", opener=transport)
        rows = PersonaRegistryReadsPort(store=actual_default).list_persona_sessions("p-alpha")
        assert {r["session_id"] for r in rows} == {session.session_id}, f"actual_owner_status={direct.status_code}; default_owner_transport_calls={observed}"
    finally:
        owner_reads.selected_tenant.reset(tenant)
        owner_reads.authorization.reset(token)
