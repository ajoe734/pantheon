"""BFF Domain Command Adapters package.

Provides domain-specific command execution and routing to real backend domain owners
for Management and Operator actions.
"""
from __future__ import annotations

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
    build_domain_receipt,
    capital_url,
    deployment_url,
    evolution_url,
    get_base_url,
    governance_approval_url,
    governance_url,
    http_request_json,
    internal_url,
    runtime_repair_url,
    utc_now,
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
from .registry import dispatch_domain_command, find_adapter
from .service import CommandAdapterService
from .router import create_action_command_router, create_command_adapters_router

__all__ = [
    "ActionUnavailableError",
    "CommandAdapterService",
    "create_action_command_router",
    "create_command_adapters_router",
    "DomainCommandAdapter",
    "CapitalCommandAdapter",
    "RuntimeCommandAdapter",
    "DeploymentCommandAdapter",
    "PersonaCommandAdapter",
    "GovernanceCommandAdapter",
    "IncidentCommandAdapter",
    "EvolutionCommandAdapter",
    "StrategyCommandAdapter",
    "CapabilitiesCommandAdapter",
    "AgoraCommandAdapter",
    "AuditCommandAdapter",
    "build_domain_receipt",
    "capital_url",
    "deployment_url",
    "dispatch_domain_command",
    "evolution_url",
    "find_adapter",
    "get_base_url",
    "governance_approval_url",
    "governance_url",
    "http_request_json",
    "internal_url",
    "runtime_repair_url",
    "utc_now",
]
