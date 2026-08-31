"""Domain Command Adapter Registry and Dispatcher.

Central registry that inspects command types, entity types, and action IDs,
and routes execution to the appropriate authoritative domain adapter.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
)
from .capital_adapter import CapitalCommandAdapter
from .runtime_adapter import RuntimeCommandAdapter
from .deployment_adapter import DeploymentCommandAdapter
from .persona_adapter import PersonaCommandAdapter
from .governance_adapter import GovernanceCommandAdapter
from .incident_adapter import IncidentCommandAdapter
from .evolution_adapter import EvolutionCommandAdapter
from .strategy_adapter import StrategyCommandAdapter
from .capabilities_adapter import CapabilitiesCommandAdapter
from .agora_adapter import AgoraCommandAdapter
from .audit_adapter import AuditCommandAdapter

log = logging.getLogger(__name__)

_DEFAULT_ADAPTERS: List[DomainCommandAdapter] = [
    CapitalCommandAdapter(),
    RuntimeCommandAdapter(),
    DeploymentCommandAdapter(),
    PersonaCommandAdapter(),
    GovernanceCommandAdapter(),
    IncidentCommandAdapter(),
    EvolutionCommandAdapter(),
    StrategyCommandAdapter(),
    CapabilitiesCommandAdapter(),
    AgoraCommandAdapter(),
    AuditCommandAdapter(),
]


def find_adapter(command_type: Any, entity_type: Any = "", action_id: Any = "") -> Optional[DomainCommandAdapter]:
    """Find a domain adapter capable of handling the command/entity/action."""
    clean_cmd = command_type.value if hasattr(command_type, "value") else str(command_type or "").strip()
    clean_entity = entity_type.value if hasattr(entity_type, "value") else str(entity_type or "").strip()
    clean_action = action_id.value if hasattr(action_id, "value") else str(action_id or "").strip()

    for adapter in _DEFAULT_ADAPTERS:
        if adapter.can_handle(clean_cmd, clean_entity, clean_action):
            return adapter
    return None


def dispatch_domain_command(
    command_id: str,
    command_type: Any,
    params: Dict[str, Any],
    auth_token: Optional[str] = None,
    mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch a command or action to its authoritative domain owner.

    Raises ActionUnavailableError if no domain owner exists for the action.
    """
    cmd_name = command_type.value if hasattr(command_type, "value") else str(command_type)
    entity_type = str(params.get("entity_type") or "").strip()
    action_id = str(params.get("action_id") or "").strip()

    adapter = find_adapter(cmd_name, entity_type, action_id)
    if adapter is None:
        raise ActionUnavailableError(
            f"No domain owner available for command_type={cmd_name!r}, entity_type={entity_type!r}, action_id={action_id!r}.",
            action_id=action_id or cmd_name,
            entity_type=entity_type,
            error_code="DOMAIN_OWNER_NOT_FOUND",
            suggestion="Submit a supported domain action or verify entity_type in the action catalog.",
        )

    return adapter.execute(
        command_id=command_id,
        command_type=cmd_name,
        params=params,
        auth_token=auth_token,
        mfa_token=mfa_token,
    )
