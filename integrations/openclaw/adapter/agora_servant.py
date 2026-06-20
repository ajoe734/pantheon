"""Agora servant persona -> OpenClaw agent provisioning adapter."""
from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

from integrations.openclaw.persona_agent_sync import (
    CliRunner,
    SoulWriter,
    desired_agent_spec,
    sync_persona_agents,
)


class AgoraServantAgentSyncError(RuntimeError):
    """Raised when the OpenClaw servant agent cannot be reconciled."""


def ensure_agora_servant_agent(
    persona: Mapping[str, Any],
    *,
    runner: Optional[CliRunner] = None,
    soul_writer: Optional[SoulWriter] = None,
    sync_fn: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Create or update the OpenClaw agent for one user-private Agora servant.

    The underlying sync path is the existing persona-agent adapter: it creates a
    missing OpenClaw agent and refreshes identity/SOUL for an existing one.
    """
    spec = desired_agent_spec(persona)
    sync = sync_fn or sync_persona_agents
    report = sync([persona], runner=runner, soul_writer=soul_writer)
    report_dict = report.to_dict() if hasattr(report, "to_dict") else dict(report)
    failed = list(report_dict.get("failed") or [])
    if failed:
        first = failed[0]
        raise AgoraServantAgentSyncError(
            str(first.get("error") or "OpenClaw servant agent sync failed")
        )
    created = set(report_dict.get("created") or [])
    updated = set(report_dict.get("updated") or [])
    unchanged = set(report_dict.get("unchanged") or [])
    if spec.persona_id in created:
        status = "created"
    elif spec.persona_id in updated:
        status = "updated"
    elif spec.persona_id in unchanged:
        status = "unchanged"
    else:
        status = "unknown"
    return {
        "status": status,
        "agent_id": spec.persona_id,
        "model_id": f"openclaw/{spec.persona_id}",
        "model": spec.model,
        "workspace_ref": spec.workspace,
        "report": report_dict,
    }


__all__ = ["AgoraServantAgentSyncError", "ensure_agora_servant_agent"]
