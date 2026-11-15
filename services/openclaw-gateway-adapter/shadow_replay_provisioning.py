"""Provision the single deny-all agent used for harness shadow replay.

Follows consult_agent_provisioning: the adapter owns the gateway connection,
registry lock and workspace root; this module patches only its own agent id,
never a default/trading agent, and the policy it reports is read back live.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Mapping

from integrations.openclaw.persona_agent_sync import agent_registry_lock

SHADOW_REPLAY_AGENT_ID = "shadow-replay-deny-all"
ENSURE_PATH = "/api/openclaw-adapter/shadow-replay/agent/ensure"
POLICY_PATH = "/api/openclaw-adapter/shadow-replay/policy"


class ShadowReplayProvisioningError(RuntimeError):
    """Safe, actionable reason the replay agent cannot be ensured or probed."""


def replay_agent_id() -> str:
    return str(os.getenv("PANTHEON_SHADOW_REPLAY_AGENT_ID") or SHADOW_REPLAY_AGENT_ID).strip()


def _deny_all_violation(agent: Mapping[str, Any]) -> str | None:
    tools = agent.get("tools")
    if not isinstance(tools, Mapping) or tools.get("deny") != ["*"] or tools.get("allow") not in (None, []):
        return "tools"
    exec_cfg = tools.get("exec")
    if not (isinstance(exec_cfg, Mapping) and exec_cfg.get("security") == "deny" and exec_cfg.get("ask") == "always"):
        return "tools.exec"
    if agent.get("skills") != []:
        return "skills"
    memory = agent.get("memorySearch")
    if not (isinstance(memory, Mapping) and memory.get("enabled") is False and memory.get("sources") == []):
        return "memorySearch"
    if agent.get("contextInjection") != "never":
        return "contextInjection"
    return None


def _entries(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries = ((snapshot.get("config") or {}).get("agents") or {}).get("list", [])
    if (snapshot.get("valid") is not True or not isinstance(entries, list)
            or any(not isinstance(a, dict) or not isinstance(a.get("id"), str) or not a["id"] for a in entries)
            or len({a["id"] for a in entries}) != len(entries)):
        raise ShadowReplayProvisioningError("gateway agents config is invalid")
    return entries


def ensure_shadow_replay_agent(provider: Any, *, policy: Mapping[str, Any], workspace_root: Any,
                               agent_id: str | None = None) -> dict[str, Any]:
    agent_id = (agent_id or replay_agent_id()).strip()
    if not agent_id or agent_id in {"main", "default"} or agent_id == getattr(provider, "_agent_id", "main"):
        raise ShadowReplayProvisioningError("replay agent id must be a dedicated non-default identity")
    workspace = str(workspace_root / agent_id)
    with agent_registry_lock():
        snapshot = provider._gateway_call("config.get")
        if not snapshot.get("hash"):
            raise ShadowReplayProvisioningError("gateway config snapshot lacks a revision")
        entries = _entries(snapshot)
        if not any(a.get("id") == "main" or a.get("default") is True for a in entries):
            raise ShadowReplayProvisioningError("gateway default agent is not provisioned")
        matches = [a for a in entries if a["id"] == agent_id]
        if any(a.get("default") for a in matches):
            raise ShadowReplayProvisioningError("replay agent identity collides with a default agent")
        if any(a["id"] != agent_id and a.get("workspace") == workspace for a in entries):
            raise ShadowReplayProvisioningError("replay workspace belongs to another agent")
        desired = {"id": agent_id, "workspace": workspace, **copy.deepcopy(dict(policy))}
        changed = not matches or any(matches[0].get(k) != v for k, v in desired.items())
        if changed:
            provider._gateway_call("config.patch", {
                "baseHash": snapshot["hash"],
                "raw": json.dumps({"agents": {"list": [desired]}}),
                "replacePaths": ["agents.list[].tools.allow", "agents.list[].tools.deny",
                                 "agents.list[].skills", "agents.list[].memorySearch.sources"],
            })
        agent = shadow_replay_policy(provider, agent_id)
    return {"status": "ready", "agent_id": agent["agent_id"], "workspace_ref": workspace, "changed": changed}


def shadow_replay_policy(provider: Any, agent_id: str) -> dict[str, Any]:
    """Live probe: report tools/side effects from the gateway, never from a cached claim."""
    snapshot = provider._gateway_call("config.get")
    matches = [a for a in _entries(snapshot) if a["id"] == agent_id]
    if len(matches) != 1:
        raise ShadowReplayProvisioningError("replay agent is not provisioned")
    if not any(isinstance(a, dict) and a.get("id") == agent_id for a in provider.gateway_agents_list()):
        raise ShadowReplayProvisioningError("replay agent is not live in the gateway registry")
    violation = _deny_all_violation(matches[0])
    if violation:
        raise ShadowReplayProvisioningError("replay agent runtime policy is not deny-all: " + violation)
    return {"agent_id": agent_id, "tools": [], "side_effects": "none"}
