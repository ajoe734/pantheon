"""Provision only tenant/persona-bound deny-all consultation agents.

The existing adapter owns the gateway connection, registry lock and workspace
root. This module never selects or rewrites a trading/default agent.
"""
from __future__ import annotations

import copy
import json
import os
import urllib.request
from typing import Any

from integrations.openclaw.persona_agent_sync import agent_registry_lock
from services.persona.consult_triggers import (
    CONSULT_PANEL_POLICY_ID, RISK_PERSONA_ID, RED_TEAM_PERSONA_ID,
)

ENSURE_PATH = "/api/openclaw-adapter/consultation/agents/ensure"


def _persona_record(tenant_id: str, persona_id: str) -> dict[str, Any]:
    from services.service_token_file import configured_service_token
    base = str(os.getenv("PANTHEON_PERSONA_API_URL") or "").rstrip("/")
    token = configured_service_token("PANTHEON_PERSONA_SERVICE_TOKEN") or configured_service_token("PERSONA_SERVICE_TOKEN")
    if not base or not token:
        raise RuntimeError("persona owner read is not configured")
    request = urllib.request.Request(base + "/api/personas/" + persona_id,
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant_id})
    with urllib.request.urlopen(request, timeout=10) as response:
        record = json.load(response)
    if record.get("tenant_id") != tenant_id:
        raise ValueError("participant persona tenant binding mismatch")
    if persona_id in {RISK_PERSONA_ID, RED_TEAM_PERSONA_ID}:
        metadata = record.get("metadata") or {}
        if (record.get("consult_policy_id") != CONSULT_PANEL_POLICY_ID
            or record.get("lifecycle_state") != "draft"
            or metadata.get("persona_class") != "consult_panel"
            or metadata.get("trading") is not False or metadata.get("consultable") is not True
            or record.get("tool_profile_id") or record.get("route_policy_id")):
            raise ValueError("panel persona bootstrap is incomplete or unsafe")
    return record


def ensure_consult_agent(provider: Any, *, tenant_id: str, persona_id: str) -> dict[str, Any]:
    # Import at call time to avoid a cycle with the route module. Admission is
    # deliberately reused after reconciliation and still reads live state.
    from consultation_provider import (
        _PERSONA_ID_PATTERN, _RESERVED_AGENT_IDS, CONSULT_AGENT_PREFIX,
        PanelAdmissionDenied, consult_agent_id, admit_panel_agent,
    )
    from main import _OPENCLAW_AGENT_WORKSPACE_ROOT, _PERSONA_OPINION_RUNTIME_POLICY
    persona = str(persona_id or "").strip()
    if (not tenant_id.strip() or not _PERSONA_ID_PATTERN.fullmatch(persona)
        or persona in _RESERVED_AGENT_IDS or persona == getattr(provider, "_agent_id", "main")
        or persona.startswith(CONSULT_AGENT_PREFIX)):
        raise PanelAdmissionDenied("participant persona_id is not an admissible persona identity")
    _persona_record(tenant_id, persona)
    agent_id = consult_agent_id(tenant_id, persona)
    workspace = str(_OPENCLAW_AGENT_WORKSPACE_ROOT / agent_id)
    with agent_registry_lock():
        snapshot = provider._gateway_call("config.get")
        if snapshot.get("valid") is not True or not snapshot.get("hash"):
            raise RuntimeError("gateway config snapshot is invalid or lacks a revision")
        config = snapshot.get("config") or {}
        entries = (config.get("agents") or {}).get("list", [])
        if (not isinstance(entries, list)
            or any(not isinstance(a, dict) or not isinstance(a.get("id"), str) or not a["id"] for a in entries)
            or len({a["id"] for a in entries}) != len(entries)):
            raise RuntimeError("gateway agents config is invalid")
        # Never let the first consultation agent become an implicit default.
        if not entries or not any(a.get("id") == "main" or a.get("default") is True for a in entries):
            raise RuntimeError("gateway default agent is not provisioned")
        matches = [a for a in entries if a.get("id") == agent_id]
        if len(matches) > 1 or any(a.get("default") for a in matches):
            raise RuntimeError("consultation agent identity collides with a default or duplicate agent")
        if any(a.get("id") != agent_id and a.get("workspace") == workspace for a in entries):
            raise RuntimeError("consultation workspace belongs to another agent")
        desired = {"id": agent_id, "workspace": workspace, **copy.deepcopy(_PERSONA_OPINION_RUNTIME_POLICY)}
        changed = not matches or any(matches[0].get(k) != v for k, v in desired.items())
        if changed:
            # Patch this id only. baseHash fences concurrent replicas/CLI writers;
            # no credential-bearing full config or default-agent patch is sent.
            provider._gateway_call("config.patch", {
                "baseHash": snapshot["hash"],
                "raw": json.dumps({"agents": {"list": [desired]}}),
                # Explicit shrink intent required by the pinned gateway when
                # repairing an agent that previously had tools/skills/memory.
                "replacePaths": ["agents.list[].tools.allow", "agents.list[].tools.deny",
                                 "agents.list[].skills", "agents.list[].memorySearch.sources"],
            })
        admitted = admit_panel_agent(provider, tenant_id=tenant_id, persona_id=persona)
        verified = provider._gateway_call("config.get")
        live_entries = ((verified.get("config") or {}).get("agents") or {}).get("list") or []
        bound = [a for a in live_entries if a.get("id") == agent_id]
        if verified.get("valid") is not True or len(bound) != 1 or bound[0].get("workspace") != workspace:
            raise RuntimeError("consultation agent workspace was not acknowledged")
    return {"status": "ready", "tenant_id": tenant_id, "persona_id": persona,
            "agent_id": admitted, "workspace_ref": workspace, "changed": changed}
