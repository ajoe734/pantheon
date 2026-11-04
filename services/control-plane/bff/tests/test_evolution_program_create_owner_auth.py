"""POST/PATCH /bff/evolution-programs bind the verified caller tenant and use the service credential.

Drives the real BFF route (real JWT identity, real role gates, the real
``EvolutionServiceProgramCommandPort`` and the real owner-read surface) against
the real ``services/evolution/main.py`` app running with
``EVOLUTION_AUTH_MODE=token``. The only seam is ``urllib.request.urlopen``,
which hands the headers the BFF actually emitted to the Evolution app without
rewriting them, so Evolution's own auth middleware decides every request.
"""
from __future__ import annotations

import io
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_tmp = tempfile.mkdtemp(prefix="evo_program_create_")
os.environ.setdefault("EVOLUTION_DATA_DIR", _tmp)
os.environ.setdefault("INCIDENT_DATA_DIR", _tmp)

from services.control_plane.bff.auth.policy import (  # noqa: E402
    bff_error,
    extract_identity_jwt,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.core.errors import register_error_handlers  # noqa: E402
from services.control_plane.bff.core.owner_reads import (  # noqa: E402
    OwnerReadContextMiddleware,
    create_owner_domain_ports,
)
from services.control_plane.bff.evolution.router import create_evolution_router  # noqa: E402
from services.control_plane.bff.ports.evolution_program_commands import (  # noqa: E402
    EvolutionServiceProgramCommandPort,
)
from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts  # noqa: E402
from services.evolution import main as evo_main  # noqa: E402
from services.evolution.main import app as evolution_app  # noqa: E402
from services.evolution.program_service import ProgramService  # noqa: E402
from services.evolution.program_store import JsonProgramStore  # noqa: E402
from services.runtime_auth_inbound import encode_jwt_hs256  # noqa: E402

SERVICE_TOKEN = "pantheon-local-evolution-service"
TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
JWT_ENV = {
    "PANTHEON_BFF_JWT_SECRET": "evo-program-create-signing-secret-0123456789",
    "PANTHEON_BFF_JWT_ISSUER": "evo-program-create-tests",
    "PANTHEON_BFF_JWT_AUDIENCE": "bff-operators",
    "PANTHEON_BFF_AUTH_MODE": "strict",
}


def _jwt(tenant=TENANT_A, roles=("operator",), sub="olivia"):
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


def _auth(tenant=TENANT_A, roles=("operator",), sub="olivia"):
    return {"Authorization": f"Bearer {_jwt(tenant, roles, sub)}"}


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


class Harness:
    def __init__(self, client, owner, seen, data_path):
        self.client = client
        self.owner = owner
        self.seen = seen
        self.data_path = data_path

    def writes(self):
        return [s for s in self.seen if s["method"] in ("POST", "PATCH")]


@pytest.fixture
def h(monkeypatch, tmp_path):
    for k, v in JWT_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("EVOLUTION_AUTH_MODE", "token")
    monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("EVOLUTION_AUTH_ALLOWED_TENANTS", f"{TENANT_A},{TENANT_B}")
    monkeypatch.setenv("PANTHEON_EVOLUTION_API_URL", "http://evolution-owner.local")
    # A tenant the caller is not (Evolution's configured default) must never be used.
    monkeypatch.setenv("EVOLUTION_DEFAULT_TENANT_ID", "pantheon-default")

    data_path = tmp_path / "programs.json"
    monkeypatch.setattr(
        evo_main.program_service, "_store", JsonProgramStore(data_path)
    )
    owner = TestClient(evolution_app)
    seen = []

    def transport(req, timeout=30):
        headers = {k: v for k, v in req.header_items()}
        path = "/" + req.full_url.split("://", 1)[1].split("/", 1)[1]
        seen.append({"method": req.get_method(), "path": path, "headers": headers, "body": req.data})
        resp = owner.request(req.get_method(), path, headers=headers, content=req.data)
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(req.full_url, resp.status_code, "err", {}, io.BytesIO(resp.content))
        return _Resp(resp.status_code, resp.content)

    monkeypatch.setattr(urllib.request, "urlopen", transport)

    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(OwnerReadContextMiddleware)
    app.include_router(
        create_evolution_router(
            read_surface=ReadSurfacePorts(persona_capital_runtime=create_owner_domain_ports()),
            extract_identity=extract_identity_jwt,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            program_commands=EvolutionServiceProgramCommandPort(),
        )
    )
    return Harness(TestClient(app), owner, seen, data_path)


def _create(h, name="Momentum research", *, tenant=TENANT_A, key=None, **extra):
    headers = _auth(tenant)
    if key:
        headers["Idempotency-Key"] = key
    return h.client.post("/bff/evolution-programs", json={"name": name, **extra}, headers=headers)


def test_create_binds_verified_tenant_and_service_credential(h):
    resp = _create(h, key="k-create-1")
    assert resp.status_code == 201, resp.text
    program = resp.json()
    assert program["status"] == "draft"
    assert program["tenant_id"] == TENANT_A
    assert program["created_by"] == "olivia"

    (write,) = h.writes()
    assert write["headers"]["X-tenant-id"] == TENANT_A
    assert write["headers"]["Authorization"] == f"Bearer {SERVICE_TOKEN}"
    assert _jwt() not in write["headers"]["Authorization"]
    assert write["headers"]["Idempotency-key"] == "k-create-1"

    read = h.client.get("/bff/evolution-programs", headers=_auth())
    assert read.status_code == 200, read.text
    items = read.json()["items"]
    assert [(p["program_id"], p["status"]) for p in items] == [(program["program_id"], "draft")]
    assert h.client.get(
        f"/bff/evolution-programs/{program['program_id']}", headers=_auth()
    ).json()["data"]["status"] == "draft"


def test_name_patch_binds_verified_tenant_and_service_credential(h):
    program = _create(h).json()
    resp = h.client.patch(
        f"/bff/evolution-programs/{program['program_id']}",
        json={"name": "Renamed", "revision": program["revision"]},
        headers=_auth(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["name"] == "Renamed"
    patch = [w for w in h.writes() if w["method"] == "PATCH"][0]
    assert patch["headers"]["X-tenant-id"] == TENANT_A
    assert patch["headers"]["Authorization"] == f"Bearer {SERVICE_TOKEN}"


def test_actor_comes_from_verified_identity_and_overrides_are_422(h):
    assert _create(h).status_code == 201
    body = h.writes()[0]["body"]
    assert b'"actor_id": "olivia"' in body and b"tenant" not in body

    before = len(h.writes())
    for field, value in (
        ("status", "active"),
        ("program_id", "evp-forced"),
        ("id", "evp-forced"),
        ("actor_id", "mallory"),
        ("tenant_id", TENANT_B),
    ):
        resp = _create(h, **{field: value})
        assert resp.status_code == 422, (field, resp.text)
    assert _create(h, name="  ").status_code == 422
    assert len(h.writes()) == before


def test_role_and_session_gates(h):
    viewer = h.client.post(
        "/bff/evolution-programs", json={"name": "x"}, headers=_auth(roles=("viewer",))
    )
    assert viewer.status_code == 403
    assert h.client.post("/bff/evolution-programs", json={"name": "x"}).status_code == 401
    assert h.writes() == []


def test_idempotency_replay_and_divergence(h):
    first = _create(h, "Same", key="idem-1")
    again = _create(h, "Same", key="idem-1")
    assert first.status_code == again.status_code == 201
    assert first.json()["program_id"] == again.json()["program_id"]
    diverged = _create(h, "Different", key="idem-1")
    assert diverged.status_code == 409, diverged.text
    listed = h.client.get("/bff/evolution-programs", headers=_auth()).json()["items"]
    assert len(listed) == 1


def test_tenants_never_see_each_others_programs(h):
    a = _create(h, "A program", tenant=TENANT_A).json()
    b = _create(h, "B program", tenant=TENANT_B).json()
    assert a["tenant_id"] == TENANT_A and b["tenant_id"] == TENANT_B
    list_a = h.client.get("/bff/evolution-programs", headers=_auth(TENANT_A)).json()["items"]
    list_b = h.client.get("/bff/evolution-programs", headers=_auth(TENANT_B)).json()["items"]
    assert [p["program_id"] for p in list_a] == [a["program_id"]]
    assert [p["program_id"] for p in list_b] == [b["program_id"]]
    assert h.client.get(f"/bff/evolution-programs/{b['program_id']}", headers=_auth(TENANT_A)).status_code == 404
    assert h.client.get(f"/bff/evolution-programs/{a['program_id']}", headers=_auth(TENANT_B)).status_code == 404


def test_created_program_stays_draft_without_decision_outbox_or_run(h):
    decisions_before = len(evo_main.store.list_all())
    outbox_before = len(evo_main.dispatch_outbox.store.list_all())
    program = _create(h).json()
    assert program["status"] == "draft"
    assert program["run_ids"] == [] and program["candidate_ids"] == []
    assert len(evo_main.store.list_all()) == decisions_before
    assert len(evo_main.dispatch_outbox.store.list_all()) == outbox_before
    # The only owner traffic is the program create: no proposal, dispatch or research call.
    assert {(s["method"], s["path"]) for s in h.seen} == {("POST", "/api/evolution/programs")}


def test_created_program_survives_restart(h):
    program = _create(h).json()
    reopened = ProgramService(JsonProgramStore(Path(h.data_path)))
    stored = reopened.get_program(tenant_id=TENANT_A, program_id=program["program_id"])
    assert stored is not None
    assert (stored["name"], stored["status"]) == (program["name"], "draft")


def test_missing_service_token_fails_closed_before_any_http_call(h, monkeypatch):
    monkeypatch.delenv("EVOLUTION_AUTH_TOKEN")
    resp = _create(h)
    assert resp.status_code == 503, resp.text
    assert "EVOLUTION_AUTH_TOKEN" in resp.text
    assert h.seen == []

