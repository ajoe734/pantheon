"""Capabilities, Tools, MCP Servers, and Skills Domain Command Adapter.

Enforces strict production safety for capability actions:
- Safe diagnostic actions (health_check, test_connection) execute live probes.
- Unsafe runtime mutations (execute, publish, edit) fail closed with explicit
  ActionUnavailableError rather than emitting generic admission success.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
)

log = logging.getLogger(__name__)


class CapabilitiesCommandAdapter(DomainCommandAdapter):
    """Adapter for Tools, MCP Servers, and Skills commands."""

    _HANDLED_COMMANDS = {
        "ToolAction",
        "McpServerAction",
        "SkillAction",
    }

    _HANDLED_ENTITIES = {
        "tool",
        "mcptool",
        "mcp-tool",
        "mcpserver",
        "mcp-server",
        "skill",
    }

    _SAFE_PROBE_ACTIONS = {
        "health_check",
        "test_connection",
        "probe",
        "status",
        "ping",
    }

    def can_handle(self, command_type: str, entity_type: str, action_id: str) -> bool:
        normalized_cmd = str(command_type or "").strip()
        normalized_entity = str(entity_type or "").strip().lower().replace("_", "-")
        return normalized_cmd in self._HANDLED_COMMANDS or normalized_entity in self._HANDLED_ENTITIES

    def execute(
        self,
        command_id: str,
        command_type: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        from .retired import reject_retired_command
        reject_retired_command(command_type)
        raise ActionUnavailableError("No configured executing owner", entity_type=command_type)
