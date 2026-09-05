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

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import create_in_memory_read_surface_ports, create_read_surface_ports

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


class _ConsolSkillsTestStore:
    def __init__(self, skills: Dict[str, Any], tools: Dict[str, Any], mcp_servers: Dict[str, Any], mcp_tools: Dict[str, Any]) -> None:
        self.ports = create_in_memory_read_surface_ports(
            operations_consultation_kwargs={
                "skills": list(skills.values()) if isinstance(skills, dict) else list(skills),
                "tools": list(tools.values()) if isinstance(tools, dict) else list(tools),
                "mcp_servers": list(mcp_servers.values()) if isinstance(mcp_servers, dict) else list(mcp_servers),
                "mcp_tools": list(mcp_tools.values()) if isinstance(mcp_tools, dict) else list(mcp_tools),
            }
        )

    def dataset_source(self, dataset: str) -> str:
        if dataset in ("skills", "tools", "mcp_servers", "mcp_tools"):
            return "typed_store"
        return self.ports.dataset_source(dataset)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.ports, name)


@contextmanager
def _bff_with_stores(
    *,
    skills: Dict[str, Any],
    tools: Dict[str, Any],
    mcp_servers: Dict[str, Any],
    mcp_tools: Dict[str, Any],
) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        skills_path = os.path.join(td, "skills.json")
        tools_path = os.path.join(td, "tools.json")
        mcp_servers_path = os.path.join(td, "mcp_servers.json")
        mcp_tools_path = os.path.join(td, "mcp_tools.json")

        with open(skills_path, "w") as f:
            json.dump(skills, f)
        with open(tools_path, "w") as f:
            json.dump(tools, f)
        with open(mcp_servers_path, "w") as f:
            json.dump(mcp_servers, f)
        with open(mcp_tools_path, "w") as f:
            json.dump(mcp_tools, f)

        original_store = bff_main.read_store
        original_mcp_registry = dict(bff_main._MCP_SERVER_REGISTRY)
        original_tool_registry = dict(bff_main._TOOL_REGISTRY)
        original_skill_registry = dict(bff_main._SKILL_REGISTRY)
        original_mcp_tool_registry = dict(bff_main._MCP_TOOL_REGISTRY)
        env_backup = {}
        env_vars = {
            "PANTHEON_BFF_SKILLS_STORE": skills_path,
            "PANTHEON_BFF_TOOLS_STORE": tools_path,
            "PANTHEON_BFF_MCP_SERVERS_STORE": mcp_servers_path,
            "PANTHEON_BFF_MCP_TOOLS_STORE": mcp_tools_path,
        }
        for k, v in env_vars.items():
            env_backup[k] = os.environ.get(k)
            os.environ[k] = v
        try:
            bff_main.read_store = _ConsolSkillsTestStore(skills, tools, mcp_servers, mcp_tools)
            bff_main._MCP_SERVER_REGISTRY.clear()
            bff_main._TOOL_REGISTRY.clear()
            bff_main._SKILL_REGISTRY.clear()
            bff_main._MCP_TOOL_REGISTRY.clear()
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            bff_main._MCP_SERVER_REGISTRY.clear()
            bff_main._MCP_SERVER_REGISTRY.update(original_mcp_registry)
            bff_main._TOOL_REGISTRY.clear()
            bff_main._TOOL_REGISTRY.update(original_tool_registry)
            bff_main._SKILL_REGISTRY.clear()
            bff_main._SKILL_REGISTRY.update(original_skill_registry)
            bff_main._MCP_TOOL_REGISTRY.clear()
            bff_main._MCP_TOOL_REGISTRY.update(original_mcp_tool_registry)
            for k, v in env_backup.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


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
