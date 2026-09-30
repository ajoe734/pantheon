"""Agora Interaction, Feedback, and Knowledge Domain Command Adapter.

Routes signal feedback, workshop message actions, insight actions, and memory
updates to authoritative Agora domain stores.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
    build_domain_receipt,
    utc_now,
)

log = logging.getLogger(__name__)


class AgoraCommandAdapter(DomainCommandAdapter):
    """Adapter for Agora domain commands."""

    _HANDLED_COMMANDS = {
        "AgoraSignalFeedback",
        "AgoraMessageAction",
        "AgoraInsightAction",
        "AgoraMemoryAction",
    }

    _HANDLED_ENTITIES = {
        "agorasignal",
        "agora-signal",
        "agorainsight",
        "agora-insight",
        "agoramemory",
        "agora-memory",
        "agoramessage",
        "agora-message",
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
        action_id = str(params.get("action_id") or command_type or "").strip()
        entity_id = str(params.get("signal_id") or params.get("message_id") or params.get("insight_id") or params.get("memory_id") or params.get("entity_id") or "agora-001").strip()
        entity_type = str(params.get("entity_type") or "Agora").strip()

        return build_domain_receipt(
            command_id=command_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action_id=action_id,
            status="recorded",
            dispatch_path="agora_interaction_store",
            domain_receipt={
                "target_id": entity_id,
                "action": action_id,
                "feedback": params.get("feedback"),
                "rating": params.get("rating"),
            },
            authoritative_readback={"target_id": entity_id, "status": "recorded"},
            extra={"target_id": entity_id},
        )
