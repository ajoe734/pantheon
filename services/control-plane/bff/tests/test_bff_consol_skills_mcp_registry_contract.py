"""Contract tests: /bff/skills, /bff/tools, /bff/mcp-servers, /bff/mcp-tools.

Acceptance criteria:
  1. GET /bff/skills  -> 200, data envelope, count>0, surface status=ok when store is wired
  2. GET /bff/tools   -> 200, data envelope, count>0, surface status=ok when store is wired
  3. GET /bff/mcp-servers -> 200, data envelope, count>0, surface status=ok (bff_local_registry)
  4. GET /bff/mcp-tools   -> 200, data envelope, count>0, surface status=ok (bff_local_registry)
  5. 401 without auth on all four surfaces
  6. Store-wired surfaces report source=canonical and status=ok
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterator, List

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.models import utc_now
from services.control_plane.bff.tools_integrations.router import create_integrations_router
from services.control_plane.bff.tools_integrations.service import IntegrationsService

OPERATOR_AUTH = "Bearer consol-skills-op:operator"
HEADERS = {"Authorization": OPERATOR_AUTH}

_SKILLS_FIXTURE: Dict[str, Any] = {
    "skill-cp-run-backtest": {
        "id": "skill-cp-run-backtest",
        "skill_id": "skill-cp-run-backtest",
        "name": "run_backtest",
        "status": "active",
        "description": "Run a historical backtest for a given strategy",
        "sandbox_enabled": True,
        "source": "control_plane_skills_registry",
    },
    "skill-cp-run-portfolio-sim": {
        "id": "skill-cp-run-portfolio-sim",
        "skill_id": "skill-cp-run-portfolio-sim",
        "name": "run_portfolio_sim",
        "status": "active",
        "description": "Run a portfolio simulation / vectorized backtest",
        "sandbox_enabled": True,
        "source": "control_plane_skills_registry",
    },
}

_TOOLS_FIXTURE: Dict[str, Any] = {
    "tool-cp-qlib": {
        "id": "tool-cp-qlib",
        "tool_id": "tool-cp-qlib",
        "name": "Qlib Research Tool",
        "status": "active",
        "tool_class": "research_worker",
        "mcp_sourced": False,
        "source": "control_plane_skills_registry",
    },
}

_MCP_SERVERS_FIXTURE: Dict[str, Any] = {
    "mcp-server-research-worker-gateway": {
        "id": "mcp-server-research-worker-gateway",
        "server_id": "mcp-server-research-worker-gateway",
        "name": "Research Worker Gateway",
        "status": "registered",
        "endpoint": "http://research-worker-gateway-svc:8103",
        "governance": {"paper_only": True, "live_execution_allowed": False},
    },
}

_MCP_TOOLS_FIXTURE: Dict[str, Any] = {
    "mcp-server-research-worker-gateway:mcp-tool-rwg-qlib": {
        "id": "mcp-tool-rwg-qlib",
        "tool_id": "mcp-tool-rwg-qlib",
        "server_id": "mcp-server-research-worker-gateway",
        "name": "Qlib Worker Tool",
        "status": "imported",
        "tool_class": "research",
    },
}


class _ConsolSkillsReadStore:
    """Minimal read-store double exposing the four list_* methods that the
    real, extracted Tools & Integrations service (tools_integrations/service.py)
    reads from (see IntegrationsService.tool_fixture_records / skill_fixture_records
    / mcp_server_fixture_records / mcp_tool_fixture_records)."""

    def __init__(
        self,
        skills: Dict[str, Any],
        tools: Dict[str, Any],
        mcp_servers: Dict[str, Any],
        mcp_tools: Dict[str, Any],
    ) -> None:
        self._skills = list(skills.values()) if isinstance(skills, dict) else list(skills)
        self._tools = list(tools.values()) if isinstance(tools, dict) else list(tools)
        self._mcp_servers = list(mcp_servers.values()) if isinstance(mcp_servers, dict) else list(mcp_servers)
        self._mcp_tools = list(mcp_tools.values()) if isinstance(mcp_tools, dict) else list(mcp_tools)

    def list_skills(self) -> List[Dict[str, Any]]:
        return list(self._skills)

    def list_tools(self) -> List[Dict[str, Any]]:
        return list(self._tools)

    def list_mcp_servers(self) -> List[Dict[str, Any]]:
        return list(self._mcp_servers)

    def list_mcp_tools(self) -> List[Dict[str, Any]]:
        return list(self._mcp_tools)


@contextmanager
def _bff_with_stores(
    *,
    skills: Dict[str, Any],
    tools: Dict[str, Any],
    mcp_servers: Dict[str, Any],
    mcp_tools: Dict[str, Any],
) -> Iterator[TestClient]:
    """Mount the real, extracted Tools & Integrations router (production
    wiring: services.control_plane.bff.tools_integrations.router) on a fresh
    standalone FastAPI app, backed by an in-memory read-store double.

    A fresh IntegrationsService is built per call, so its mcp_server_registry /
    tool_registry / skill_registry / mcp_tool_registry all start empty --
    replacing the old bff_main global-registry backup/clear/restore dance.
    """
    read_store = _ConsolSkillsReadStore(skills, tools, mcp_servers, mcp_tools)
    service = IntegrationsService(read_store=read_store, bff_error_fn=bff_error, utc_now_fn=utc_now)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(
        create_integrations_router(
            service=service,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            require_mcp_tool_write_role=require_operator_role,
            require_openclaw_command_role=require_operator_role,
            bff_error=bff_error,
            utc_now_fn=utc_now,
        )
    )
    yield TestClient(app)


# ---------------------------------------------------------------------------
# /bff/skills
# ---------------------------------------------------------------------------

def test_skills_200_with_store_wired() -> None:
    """GET /bff/skills returns 200 with count>0 when store is wired."""
    with _bff_with_stores(
        skills=_SKILLS_FIXTURE, tools={}, mcp_servers={}, mcp_tools={}
    ) as client:
        resp = client.get("/bff/skills", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body
    assert "page_info" in body
    assert body["page_info"]["total"] >= 1


def test_skills_surface_status_ok_with_store() -> None:
    """GET /bff/skills surface.status=ok when skills store file is present."""
    with _bff_with_stores(
        skills=_SKILLS_FIXTURE, tools={}, mcp_servers={}, mcp_tools={}
    ) as client:
        resp = client.get("/bff/skills", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    meta = resp.json().get("meta", {})
    surfaces = meta.get("surfaces", {})
    skill_surface = surfaces.get("skill_list") or surfaces.get("skills") or {}
    assert skill_surface.get("status") == "ok", (
        f"Expected status=ok, got: {skill_surface}"
    )


def test_skills_each_entry_has_id_and_name() -> None:
    """Each skill record has skill_id and name."""
    with _bff_with_stores(
        skills=_SKILLS_FIXTURE, tools={}, mcp_servers={}, mcp_tools={}
    ) as client:
        resp = client.get("/bff/skills", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    for item in resp.json()["data"]:
        assert item.get("skill_id") or item.get("id"), f"Missing id in: {item}"
        assert item.get("name"), f"Missing name in: {item}"


def test_skills_source_not_missing_with_store() -> None:
    """GET /bff/skills surface.source != missing when store file is present."""
    with _bff_with_stores(
        skills=_SKILLS_FIXTURE, tools={}, mcp_servers={}, mcp_tools={}
    ) as client:
        resp = client.get("/bff/skills", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    meta = resp.json().get("meta", {})
    surfaces = meta.get("surfaces", {})
    skill_surface = surfaces.get("skill_list") or surfaces.get("skills") or {}
    assert skill_surface.get("source") not in (None, "missing"), (
        f"Expected non-missing source, got: {skill_surface}"
    )


def test_skills_401_no_auth() -> None:
    """GET /bff/skills returns 401 without authorization."""
    with _bff_with_stores(
        skills=_SKILLS_FIXTURE, tools={}, mcp_servers={}, mcp_tools={}
    ) as client:
        resp = client.get("/bff/skills")
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# /bff/tools
# ---------------------------------------------------------------------------

def test_tools_200_with_store_wired() -> None:
    """GET /bff/tools returns 200 with count>0 when store is wired."""
    with _bff_with_stores(
        skills={}, tools=_TOOLS_FIXTURE, mcp_servers={}, mcp_tools={}
    ) as client:
        resp = client.get("/bff/tools", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body
    assert "page_info" in body
    assert body["page_info"]["total"] >= 1


def test_tools_surface_status_ok_with_store() -> None:
    """GET /bff/tools surface.status=ok when tools store file is present."""
    with _bff_with_stores(
        skills={}, tools=_TOOLS_FIXTURE, mcp_servers={}, mcp_tools={}
    ) as client:
        resp = client.get("/bff/tools", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    meta = resp.json().get("meta", {})
    surfaces = meta.get("surfaces", {})
    tool_surface = surfaces.get("tool_list") or surfaces.get("tools") or {}
    assert tool_surface.get("status") == "ok", (
        f"Expected status=ok, got: {tool_surface}"
    )


def test_tools_each_entry_has_id_and_name() -> None:
    """Each tool record has tool_id and name."""
    with _bff_with_stores(
        skills={}, tools=_TOOLS_FIXTURE, mcp_servers={}, mcp_tools={}
    ) as client:
        resp = client.get("/bff/tools", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    for item in resp.json()["data"]:
        assert item.get("tool_id") or item.get("id"), f"Missing id in: {item}"
        assert item.get("name"), f"Missing name in: {item}"


def test_tools_401_no_auth() -> None:
    """GET /bff/tools returns 401 without authorization."""
    with _bff_with_stores(
        skills={}, tools=_TOOLS_FIXTURE, mcp_servers={}, mcp_tools={}
    ) as client:
        resp = client.get("/bff/tools")
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# /bff/mcp-servers
# ---------------------------------------------------------------------------

def test_mcp_servers_200_with_store() -> None:
    """GET /bff/mcp-servers returns 200 with count>0 when store is wired."""
    with _bff_with_stores(
        skills={}, tools={}, mcp_servers=_MCP_SERVERS_FIXTURE, mcp_tools={}
    ) as client:
        resp = client.get("/bff/mcp-servers", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    total = body.get("page_info", {}).get("total") or len(body.get("data") or [])
    assert total >= 1


def test_mcp_servers_surface_status_ok() -> None:
    """GET /bff/mcp-servers always reports surface status=ok (bff_local_registry)."""
    with _bff_with_stores(
        skills={}, tools={}, mcp_servers=_MCP_SERVERS_FIXTURE, mcp_tools={}
    ) as client:
        resp = client.get("/bff/mcp-servers", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    meta = resp.json().get("meta", {})
    surfaces = meta.get("surfaces", {})
    mcp_surface = surfaces.get("mcp_servers") or {}
    assert mcp_surface.get("status") == "ok", (
        f"Expected status=ok, got: {mcp_surface}"
    )


def test_mcp_servers_401_no_auth() -> None:
    """GET /bff/mcp-servers returns 401 without authorization."""
    with _bff_with_stores(
        skills={}, tools={}, mcp_servers=_MCP_SERVERS_FIXTURE, mcp_tools={}
    ) as client:
        resp = client.get("/bff/mcp-servers")
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# /bff/mcp-tools
# ---------------------------------------------------------------------------

def test_mcp_tools_200_with_store() -> None:
    """GET /bff/mcp-tools returns 200 with count>0 when store is wired."""
    with _bff_with_stores(
        skills={}, tools={}, mcp_servers={}, mcp_tools=_MCP_TOOLS_FIXTURE
    ) as client:
        resp = client.get("/bff/mcp-tools", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    total = body.get("page_info", {}).get("total") or len(body.get("data") or [])
    assert total >= 1


def test_mcp_tools_surface_status_ok() -> None:
    """GET /bff/mcp-tools always reports surface status=ok (bff_local_registry)."""
    with _bff_with_stores(
        skills={}, tools={}, mcp_servers={}, mcp_tools=_MCP_TOOLS_FIXTURE
    ) as client:
        resp = client.get("/bff/mcp-tools", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    meta = resp.json().get("meta", {})
    surfaces = meta.get("surfaces", {})
    mcp_surface = surfaces.get("mcp_tools") or {}
    assert mcp_surface.get("status") == "ok", (
        f"Expected status=ok, got: {mcp_surface}"
    )


def test_mcp_tools_401_no_auth() -> None:
    """GET /bff/mcp-tools returns 401 without authorization."""
    with _bff_with_stores(
        skills={}, tools={}, mcp_servers={}, mcp_tools=_MCP_TOOLS_FIXTURE
    ) as client:
        resp = client.get("/bff/mcp-tools")
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# Stub-dispatch safety invariant (dev safety posture)
# ---------------------------------------------------------------------------

def test_mcp_tools_no_live_execution_in_governance() -> None:
    """MCP server records declare live_execution_allowed=False (dev safety posture)."""
    with _bff_with_stores(
        skills={}, tools={}, mcp_servers=_MCP_SERVERS_FIXTURE, mcp_tools={}
    ) as client:
        resp = client.get("/bff/mcp-servers", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    for item in resp.json().get("data", []):
        governance = item.get("governance") or {}
        assert governance.get("live_execution_allowed") is False, (
            f"Expected live_execution_allowed=False in {item.get('server_id')}"
        )


def test_memory_only_tool_and_skill_registries_are_not_served_as_catalog_truth() -> None:
    """Process-local tool_registry and skill_registry entries do not appear in catalog reads."""
    read_store = _ConsolSkillsReadStore(_SKILLS_FIXTURE, _TOOLS_FIXTURE, {}, {})
    service = IntegrationsService(read_store=read_store, bff_error_fn=bff_error, utc_now_fn=utc_now)
    service.tool_registry["tool-memory-ghost"] = {
        "id": "tool-memory-ghost",
        "tool_id": "tool-memory-ghost",
        "name": "Memory Ghost Tool",
        "status": "draft",
    }
    service.skill_registry["skill-memory-ghost"] = {
        "id": "skill-memory-ghost",
        "skill_id": "skill-memory-ghost",
        "name": "Memory Ghost Skill",
        "status": "draft",
    }
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(
        create_integrations_router(
            service=service,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now_fn=utc_now,
        )
    )
    client = TestClient(app)

    tools_resp = client.get("/bff/tools", headers=HEADERS)
    assert tools_resp.status_code == 200
    assert [t["tool_id"] for t in tools_resp.json()["data"]] == ["tool-cp-qlib"]
    assert client.get("/bff/tools/tool-memory-ghost", headers=HEADERS).status_code == 404

    skills_resp = client.get("/bff/skills", headers=HEADERS)
    assert skills_resp.status_code == 200
    assert {s["skill_id"] for s in skills_resp.json()["data"]} == set(_SKILLS_FIXTURE.keys())
    assert client.get("/bff/skills/skill-memory-ghost", headers=HEADERS).status_code == 404


def test_domain_workflow_catalog_port_and_openclaw_adapter_owner_boundary() -> None:
    """DomainWorkflowCatalogPort and OpenClawOpsClient fail closed on writes and preserve empty vs missing surface status."""
    from services.control_plane.bff.ports import DomainWorkflowCatalogPort
    from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient

    openclaw_client = OpenClawOpsClient()
    for op in ("create_tool", "update_tool", "create_skill", "update_skill"):
        assert openclaw_client.supports_catalog_metadata_mutation(op) is False

    # 1. Unconfigured DomainWorkflowCatalogPort reports missing/unavailable and rejects writes with 503/403
    unconfigured_port = DomainWorkflowCatalogPort()
    unconfigured_service = IntegrationsService(
        read_store=unconfigured_port,
        openclaw_client=openclaw_client,
        bff_error_fn=bff_error,
        utc_now_fn=utc_now,
    )
    app_missing = FastAPI()
    register_error_handlers(app_missing)
    app_missing.include_router(
        create_integrations_router(
            service=unconfigured_service,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now_fn=utc_now,
        )
    )
    client_missing = TestClient(app_missing)

    tools_missing = client_missing.get("/bff/tools", headers=HEADERS).json()
    assert tools_missing["data"] == []
    assert tools_missing["meta"]["surfaces"]["tool_list"]["status"] == "unavailable"
    assert tools_missing["meta"]["surfaces"]["tool_list"]["source"] == "missing"

    skills_missing = client_missing.get("/bff/skills", headers=HEADERS).json()
    assert skills_missing["data"] == []
    assert skills_missing["meta"]["surfaces"]["skill_list"]["status"] == "unavailable"
    assert skills_missing["meta"]["surfaces"]["skill_list"]["source"] == "missing"

    viewer_headers = {"Authorization": "Bearer consol-viewer:viewer", "Idempotency-Key": "k-viewer-1"}
    assert client_missing.post("/bff/tools", json={"name": "t1"}, headers=viewer_headers).status_code == 403
    assert client_missing.post(
        "/bff/tools",
        json={"name": "t1"},
        headers={**HEADERS, "Idempotency-Key": "k-op-1"},
    ).status_code == 503
    assert client_missing.post(
        "/bff/skills",
        json={"name": "s1"},
        headers={**HEADERS, "Idempotency-Key": "k-op-2"},
    ).status_code == 503

    # 2. Configured empty DomainWorkflowCatalogPort reports ok/in_memory (actual empty vs unavailable)
    empty_port = DomainWorkflowCatalogPort(datasets={"tools": {}, "skills": {}})
    empty_service = IntegrationsService(
        read_store=empty_port,
        openclaw_client=openclaw_client,
        bff_error_fn=bff_error,
        utc_now_fn=utc_now,
    )
    app_empty = FastAPI()
    register_error_handlers(app_empty)
    app_empty.include_router(
        create_integrations_router(
            service=empty_service,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now_fn=utc_now,
        )
    )
    client_empty = TestClient(app_empty)

    tools_empty = client_empty.get("/bff/tools", headers=HEADERS).json()
    assert tools_empty["data"] == []
    assert tools_empty["meta"]["surfaces"]["tool_list"]["status"] == "ok"
    assert tools_empty["meta"]["surfaces"]["tool_list"]["source"] == "in_memory"

    skills_empty = client_empty.get("/bff/skills", headers=HEADERS).json()
    assert skills_empty["data"] == []
    assert skills_empty["meta"]["surfaces"]["skill_list"]["status"] == "ok"
    assert skills_empty["meta"]["surfaces"]["skill_list"]["source"] == "in_memory"

    # 3. Canonical allowed_tenants second-tenant reaches unconfigured owner 503 instead of false 403,
    #    while allowed-only claim with foreign X-Tenant-Id is rejected with 403 FORBIDDEN.
    multi_tenant_headers = {
        "Authorization": "Bearer consol-multi:operator:local-tenant-a,local-tenant-b",
        "X-Tenant-Id": "local-tenant-b",
        "Idempotency-Key": "k-multi-tenant-b",
    }
    second_tenant_resp = client_missing.post("/bff/tools", json={"name": "t-second"}, headers=multi_tenant_headers)
    assert second_tenant_resp.status_code == 503
    assert second_tenant_resp.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

    foreign_tenant_headers = {
        "Authorization": "Bearer consol-multi:operator:local-tenant-a,local-tenant-b",
        "X-Tenant-Id": "local-tenant-c",
        "Idempotency-Key": "k-foreign-tenant-c",
    }
    foreign_tenant_resp = client_missing.post("/bff/tools", json={"name": "t-foreign"}, headers=foreign_tenant_headers)
    assert foreign_tenant_resp.status_code == 403
    assert foreign_tenant_resp.json()["error"]["code"] == "FORBIDDEN"
    assert foreign_tenant_resp.json()["error"]["details"]["precondition_failed"] == "tenant_scope"


