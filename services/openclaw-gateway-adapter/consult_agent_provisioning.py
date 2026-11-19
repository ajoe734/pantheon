"""Provision only tenant/persona-bound deny-all consultation agents.

The existing adapter owns the gateway connection, registry lock and workspace
root. This module never selects or rewrites a trading/default agent.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any

from integrations.openclaw.persona_agent_sync import agent_registry_lock
from services.persona.consult_triggers import (
    CONSULT_PANEL_POLICY_ID, RISK_PERSONA_ID, RED_TEAM_PERSONA_ID,
)

ENSURE_PATH = "/api/openclaw-adapter/consultation/agents/ensure"
# The executor gives each ensure call CONSULTATION_WORKFLOW_EXECUTOR_TIMEOUT_SECONDS
# (30 s). Every gateway call is a ~6 s CLI spawn on hosted, so a first-time
# provisioning (get, patch, get, agents.list) cannot fit. The route therefore
# waits only this long, then answers the idempotent ``in_progress`` outcome while
# the single-flight provisioning keeps running; a later attempt finds it ready.
ENSURE_WAIT_ENV = "CONSULT_AGENT_ENSURE_WAIT_SECONDS"
DEFAULT_ENSURE_WAIT_SECONDS = 20.0
IN_PROGRESS_RETRY_SECONDS = 15

_log = logging.getLogger(__name__)
_flight_lock = threading.Lock()
_flights: dict[tuple[str, str], Future] = {}
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="consult-agent-ensure")


class ConsultAgentProvisioningInProgress(RuntimeError):
    """Provisioning outlived the caller's wait; it continues and is idempotent."""

    def __init__(self, retry_after_seconds: int = IN_PROGRESS_RETRY_SECONDS) -> None:
        super().__init__("consultation agent provisioning is in progress")
        self.retry_after_seconds = retry_after_seconds


class ConsultAgentProvisioningError(RuntimeError):
    """Safe, actionable dependency/policy reason for the blocked envelope."""


def _persona_record(tenant_id: str, persona_id: str) -> dict[str, Any]:
    from services.service_token_file import configured_service_token
    base = str(os.getenv("PANTHEON_PERSONA_API_URL") or "").rstrip("/")
    token = configured_service_token("PANTHEON_PERSONA_SERVICE_TOKEN") or configured_service_token("PERSONA_SERVICE_TOKEN")
    if not base or not token:
        raise ConsultAgentProvisioningError("persona owner read is not configured")
    request = urllib.request.Request(base + "/api/personas/" + persona_id,
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant_id})
    with urllib.request.urlopen(request, timeout=10) as response:
        record = json.load(response)
    if record.get("tenant_id") != tenant_id:
        raise ConsultAgentProvisioningError("participant persona tenant binding mismatch")
    if persona_id in {RISK_PERSONA_ID, RED_TEAM_PERSONA_ID}:
        metadata = record.get("metadata") or {}
        if (record.get("consult_policy_id") != CONSULT_PANEL_POLICY_ID
            or record.get("lifecycle_state") != "draft"
            or metadata.get("persona_class") != "consult_panel"
            or metadata.get("trading") is not False or metadata.get("consultable") is not True
            or record.get("tool_profile_id") or record.get("route_policy_id")):
            raise ConsultAgentProvisioningError("panel persona bootstrap is incomplete or unsafe")
    return record


def _wait_seconds() -> float:
    try:
        return max(0.0, float(os.getenv(ENSURE_WAIT_ENV) or DEFAULT_ENSURE_WAIT_SECONDS))
    except ValueError:
        return DEFAULT_ENSURE_WAIT_SECONDS


def _inspect(snapshot: Any, agent_id: str, workspace: str):
    """Validate a config snapshot; return (matches, desired, changed)."""
    from main import _PERSONA_OPINION_RUNTIME_POLICY
    if snapshot.get("valid") is not True or not snapshot.get("hash"):
        raise ConsultAgentProvisioningError("gateway config snapshot is invalid or lacks a revision")
    config = snapshot.get("config") or {}
    entries = (config.get("agents") or {}).get("list", [])
    if (not isinstance(entries, list)
        or any(not isinstance(a, dict) or not isinstance(a.get("id"), str) or not a["id"] for a in entries)
        or len({a["id"] for a in entries}) != len(entries)):
        raise ConsultAgentProvisioningError("gateway agents config is invalid")
    # Never let the first consultation agent become an implicit default.
    if not entries or not any(a.get("id") == "main" or a.get("default") is True for a in entries):
        raise ConsultAgentProvisioningError("gateway default agent is not provisioned")
    matches = [a for a in entries if a.get("id") == agent_id]
    if len(matches) > 1 or any(a.get("default") for a in matches):
        raise ConsultAgentProvisioningError("consultation agent identity collides with a default or duplicate agent")
    if any(a.get("id") != agent_id and a.get("workspace") == workspace for a in entries):
        raise ConsultAgentProvisioningError("consultation workspace belongs to another agent")
    desired = {"id": agent_id, "workspace": workspace, **copy.deepcopy(_PERSONA_OPINION_RUNTIME_POLICY)}
    changed = not matches or any(matches[0].get(k) != v for k, v in desired.items())
    return matches, desired, changed


class _SnapshotProvider:
    """Serve one already-verified config.get so admission does not spawn another CLI."""

    def __init__(self, provider: Any, snapshot: Any) -> None:
        self._provider = provider
        self._snapshot = snapshot
        self._agent_id = getattr(provider, "_agent_id", "main")
        self.gateway_agents_list = provider.gateway_agents_list

    def _gateway_call(self, method: str, params: Any = None):
        if method == "config.get" and params is None:
            return self._snapshot
        return self._provider._gateway_call(method, params)


def _provision_locked(provider: Any, tenant_id: str, persona: str, agent_id: str, workspace: str) -> dict[str, Any]:
    from consultation_provider import admit_panel_agent
    with agent_registry_lock():
        snapshot = provider._gateway_call("config.get")
        _, desired, changed = _inspect(snapshot, agent_id, workspace)
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
            verified = provider._gateway_call("config.get")
        else:
            verified = snapshot  # live config already holds this spec: no write, no re-read
        live_entries = ((verified.get("config") or {}).get("agents") or {}).get("list") or []
        bound = [a for a in live_entries if a.get("id") == agent_id]
        if verified.get("valid") is not True or len(bound) != 1 or bound[0].get("workspace") != workspace:
            raise ConsultAgentProvisioningError("consultation agent workspace was not acknowledged")
        admitted = admit_panel_agent(_SnapshotProvider(provider, verified),
                                     tenant_id=tenant_id, persona_id=persona)
    return {"status": "ready", "tenant_id": tenant_id, "persona_id": persona,
            "agent_id": admitted, "workspace_ref": workspace, "changed": changed}


def ensure_consult_agent(provider: Any, *, tenant_id: str, persona_id: str) -> dict[str, Any]:
    # Import at call time to avoid a cycle with the route module.
    from consultation_provider import (
        _PERSONA_ID_PATTERN, _RESERVED_AGENT_IDS, CONSULT_AGENT_PREFIX,
        PanelAdmissionDenied, consult_agent_id, _deny_all_policy_violation,
    )
    from main import _OPENCLAW_AGENT_WORKSPACE_ROOT
    persona = str(persona_id or "").strip()
    if (not tenant_id.strip() or not _PERSONA_ID_PATTERN.fullmatch(persona)
        or persona in _RESERVED_AGENT_IDS or persona == getattr(provider, "_agent_id", "main")
        or persona.startswith(CONSULT_AGENT_PREFIX)):
        raise PanelAdmissionDenied("participant persona_id is not an admissible persona identity")
    _persona_record(tenant_id, persona)
    agent_id = consult_agent_id(tenant_id, persona)
    workspace = str(_OPENCLAW_AGENT_WORKSPACE_ROOT / agent_id)
    # Fast path: one lock-free read. A live spec that already matches is ready and
    # never waits behind an unrelated writer of the agent registry lock.
    snapshot = provider._gateway_call("config.get")
    matches, _, changed = _inspect(snapshot, agent_id, workspace)
    if not changed and _deny_all_policy_violation(matches[0]) is None:
        return {"status": "ready", "tenant_id": tenant_id, "persona_id": persona,
                "agent_id": agent_id, "workspace_ref": workspace, "changed": False}
    key = (tenant_id, persona)
    with _flight_lock:
        flight = _flights.get(key)
        if flight is None:
            flight = _pool.submit(_provision_locked, provider, tenant_id, persona, agent_id, workspace)
            _flights[key] = flight

            def _done(done: Future, key=key) -> None:
                with _flight_lock:
                    if _flights.get(key) is done:
                        del _flights[key]
                if done.exception() is not None:
                    _log.warning("consult agent provisioning failed after caller wait: %s", type(done.exception()).__name__)
            flight.add_done_callback(_done)
    try:
        return flight.result(timeout=_wait_seconds())
    except FutureTimeout:
        raise ConsultAgentProvisioningInProgress() from None
