#!/usr/bin/env python3
"""Project real skills/tools/mcp-server/mcp-tool records into BFF read-surface stores.

Domain producers used (real data only — no fabrication):
  * services/control-plane/skills/skills.yaml — canonical skill registry
  * research-worker-gateway capabilities endpoint (optional; falls back to worker list from yaml)

Writes four files:
  <out_dir>/skills.json        keyed by skill_id
  <out_dir>/tools.json         keyed by tool_id
  <out_dir>/mcp_servers.json   keyed by server_id
  <out_dir>/mcp_tools.json     keyed by tool_id

Wire these via:
  PANTHEON_BFF_SKILLS_STORE      -> <out_dir>/skills.json
  PANTHEON_BFF_TOOLS_STORE       -> <out_dir>/tools.json
  PANTHEON_BFF_MCP_SERVERS_STORE -> <out_dir>/mcp_servers.json
  PANTHEON_BFF_MCP_TOOLS_STORE   -> <out_dir>/mcp_tools.json

Usage (run from repo root or inside the control-plane container):
    OUT_DIR=/data/bff RWG_URL=http://research-worker-gateway-svc:8103 \
        python3 scripts/project_skills_to_bff_surfaces.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.request
from datetime import datetime, timezone

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SKILLS_YAML = _REPO_ROOT / "services" / "control-plane" / "skills" / "skills.yaml"

_RESEARCH_WORKER_MCP_SERVER_ID = "mcp-server-research-worker-gateway"
_PROJECTED_AT = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_yaml(path: pathlib.Path):
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"  warn: failed to load {path}: {e}", file=sys.stderr)
        return None


def _get_json(url: str):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  warn: GET {url} failed: {e}", file=sys.stderr)
        return None


def _build_skills(skills_data) -> dict:
    skills = {}
    for entry in skills_data.get("skills", []):
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        skill_id = f"skill-cp-{name.replace('_', '-')}"
        skills[skill_id] = {
            "id": skill_id,
            "skill_id": skill_id,
            "name": name,
            "status": "active",
            "description": str(entry.get("description") or ""),
            "sandbox_enabled": True,
            "input_schema": {
                "type": "object",
                "properties": {"intent": {"type": "string"}},
            },
            "output_schema": {"type": "object"},
            "worker": entry.get("worker"),
            "triggers": entry.get("triggers") or [],
            "source": "control_plane_skills_registry",
            "created_at": _PROJECTED_AT,
            "updated_at": _PROJECTED_AT,
            "created_by": "project_skills_to_bff_surfaces",
        }
    return skills


def _build_tools(skills_data) -> dict:
    tools = {}
    for entry in skills_data.get("skills", []):
        worker = entry.get("worker")
        if not worker:
            continue
        tool_id = f"tool-cp-{worker.replace('_', '-')}"
        if tool_id in tools:
            continue
        tools[tool_id] = {
            "id": tool_id,
            "tool_id": tool_id,
            "name": f"{worker.capitalize()} Research Tool",
            "status": "active",
            "tool_class": "research_worker",
            "description": f"Research execution tool backed by {worker} worker.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "parameters": {"type": "object"},
                    "strategy_id": {"type": "string"},
                },
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "status": {"type": "string"},
                },
            },
            "mcp_sourced": False,
            "worker": worker,
            "source": "control_plane_skills_registry",
            "created_at": _PROJECTED_AT,
            "updated_at": _PROJECTED_AT,
            "created_by": "project_skills_to_bff_surfaces",
        }
    return tools


def _build_mcp_server(rwg_url: str) -> dict:
    capabilities = None
    if rwg_url:
        payload = _get_json(f"{rwg_url}/api/research-worker-gateway/capabilities")
        if isinstance(payload, dict):
            capabilities = payload.get("capabilities") or []

    server_id = _RESEARCH_WORKER_MCP_SERVER_ID
    return {
        server_id: {
            "id": server_id,
            "server_id": server_id,
            "name": "Research Worker Gateway",
            "status": "registered",
            "endpoint": rwg_url or "http://research-worker-gateway-svc:8103",
            "server_version": "1.0.0",
            "description": "MCP server bridging the Pantheon control plane to research worker backends (qlib, vectorbt, finrl, quantlib, stub).",
            "governance": {
                "paper_only": True,
                "live_execution_allowed": False,
            },
            "capabilities_reported": len(capabilities) if capabilities else None,
            "source": "research_worker_gateway",
            "created_at": _PROJECTED_AT,
            "updated_at": _PROJECTED_AT,
            "created_by": "project_skills_to_bff_surfaces",
        }
    }


def _build_mcp_tools(skills_data, rwg_url: str) -> dict:
    capabilities = None
    if rwg_url:
        payload = _get_json(f"{rwg_url}/api/research-worker-gateway/capabilities")
        if isinstance(payload, dict):
            capabilities = payload.get("capabilities") or []

    workers: list[str] = []
    if capabilities:
        for cap in capabilities:
            w = str(cap.get("worker") or "").strip()
            if w and w not in workers:
                workers.append(w)

    if not workers:
        for entry in skills_data.get("skills", []):
            w = entry.get("worker")
            if w and w not in workers:
                workers.append(w)

    mcp_tools = {}
    for worker in workers:
        tool_id = f"mcp-tool-rwg-{worker.replace('_', '-')}"
        registry_key = f"{_RESEARCH_WORKER_MCP_SERVER_ID}:{tool_id}"
        mcp_tools[registry_key] = {
            "id": tool_id,
            "tool_id": tool_id,
            "server_id": _RESEARCH_WORKER_MCP_SERVER_ID,
            "name": f"{worker.capitalize()} Worker Tool",
            "status": "imported",
            "tool_class": "research",
            "description": f"MCP tool wrapping the {worker} research worker backend.",
            "standalone_create_enabled": False,
            "descriptor": {
                "toolId": tool_id,
                "name": f"{worker.capitalize()} Worker Tool",
                "toolClass": "research",
                "actions": [
                    {
                        "actionId": f"invoke.{worker}",
                        "actionType": "invoke",
                        "riskLevel": "low",
                    }
                ],
            },
            "source": "research_worker_gateway",
            "imported_by": "project_skills_to_bff_surfaces",
            "imported_at": _PROJECTED_AT,
        }
    return mcp_tools


def project(rwg_url: str) -> tuple[dict, dict, dict, dict]:
    skills_data = _load_yaml(_SKILLS_YAML)
    if not skills_data:
        print("  error: could not load skills.yaml — aborting", file=sys.stderr)
        return {}, {}, {}, {}

    skills = _build_skills(skills_data)
    tools = _build_tools(skills_data)
    mcp_servers = _build_mcp_server(rwg_url)
    mcp_tools = _build_mcp_tools(skills_data, rwg_url)
    return skills, tools, mcp_servers, mcp_tools


def main() -> int:
    rwg_url = os.environ.get("RWG_URL", "").rstrip("/")
    out_dir = os.environ.get("OUT_DIR", "/data/bff")

    skills, tools, mcp_servers, mcp_tools = project(rwg_url)
    if not skills:
        return 1

    os.makedirs(out_dir, exist_ok=True)

    def _write(filename, data):
        path = os.path.join(out_dir, filename)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return path

    _write("skills.json", skills)
    _write("tools.json", tools)
    _write("mcp_servers.json", mcp_servers)
    _write("mcp_tools.json", mcp_tools)

    print(
        f"projected {len(skills)} skills + {len(tools)} tools + "
        f"{len(mcp_servers)} mcp-servers + {len(mcp_tools)} mcp-tools -> {out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
