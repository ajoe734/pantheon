"""Persona, Capital, Deployment, Runtime, Ranking, and Evolution domain ports.

Re-exports typed domain ports, protocols, and factory functions for Persona,
Capital, Deployment, Runtime, Ranking, and Evolution reads.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

try:
    from domain_ports.persona_capital_runtime import (
        CapitalPoolPort,
        DeploymentPlanPort,
        EvolutionProjectionPort,
        PersonaCapitalRuntimeDomainPort,
        PersonaFleetPort,
        PERSONA_OPERATIONAL_LIFECYCLE_STATES,
        RankingProjectionPort,
        RuntimePort,
    )
except ImportError:
    from services.control_plane.bff.domain_ports.persona_capital_runtime import (  # type: ignore[no-redef]
        CapitalPoolPort,
        DeploymentPlanPort,
        EvolutionProjectionPort,
        PersonaCapitalRuntimeDomainPort,
        PersonaFleetPort,
        PERSONA_OPERATIONAL_LIFECYCLE_STATES,
        RankingProjectionPort,
        RuntimePort,
    )

CompositePersonaCapitalRuntimePort = PersonaCapitalRuntimeDomainPort
InMemoryPersonaCapitalRuntimePort = PersonaCapitalRuntimeDomainPort


def create_persona_capital_runtime_port(
    *,
    persona_port: Optional[PersonaFleetPort] = None,
    capital_port: Optional[CapitalPoolPort] = None,
    deployment_port: Optional[DeploymentPlanPort] = None,
    runtime_port: Optional[RuntimePort] = None,
    ranking_port: Optional[RankingProjectionPort] = None,
    evolution_port: Optional[EvolutionProjectionPort] = None,
) -> PersonaCapitalRuntimeDomainPort:
    """Create a consolidated PersonaCapitalRuntimeDomainPort."""
    return PersonaCapitalRuntimeDomainPort(
        persona_port=persona_port,
        capital_port=capital_port,
        deployment_port=deployment_port,
        runtime_port=runtime_port,
        ranking_port=ranking_port,
        evolution_port=evolution_port,
    )


def create_in_memory_persona_capital_runtime_port(
    *,
    personas: Optional[List[Dict[str, Any]]] = None,
    capital_pools: Optional[List[Dict[str, Any]]] = None,
    bindings: Optional[List[Dict[str, Any]]] = None,
    deployment_plans: Optional[List[Dict[str, Any]]] = None,
    runtime_bindings: Optional[List[Dict[str, Any]]] = None,
    rankings: Optional[List[Dict[str, Any]]] = None,
    ranking_formulas: Optional[List[Dict[str, Any]]] = None,
    persona_league: Optional[List[Dict[str, Any]]] = None,
    rebalances: Optional[List[Dict[str, Any]]] = None,
    capital_allocations: Optional[List[Dict[str, Any]]] = None,
    containments: Optional[List[Dict[str, Any]]] = None,
    evolution_programs: Optional[List[Dict[str, Any]]] = None,
    evolution_decisions: Optional[List[Dict[str, Any]]] = None,
    **kwargs: Any,
) -> PersonaCapitalRuntimeDomainPort:
    """Create an in-memory PersonaCapitalRuntimeDomainPort for testing."""
    persona_p = PersonaFleetPort(records_provider=lambda: list(personas or []))
    capital_p = CapitalPoolPort(
        pools_provider=lambda: list(capital_pools or []),
        bindings_provider=lambda: list(bindings or []),
    )
    deploy_p = DeploymentPlanPort(plans_provider=lambda: list(deployment_plans or []))
    runtime_p = RuntimePort(runtime_bindings_provider=lambda: list(runtime_bindings or []))
    ranking_p = RankingProjectionPort(
        rankings_reader=lambda: list(rankings or []),
        ranking_formulas_reader=lambda: list(ranking_formulas or []),
        persona_league_reader=lambda: list(persona_league or []),
        rebalances_reader=lambda: list(rebalances or []),
        capital_allocations_reader=lambda: list(capital_allocations or []),
        containments_reader=lambda: list(containments or []),
    )
    evolution_p = EvolutionProjectionPort(
        evolution_programs_reader=lambda: list(evolution_programs or []),
        evolution_decisions_reader=lambda: list(evolution_decisions or []),
    )
    return PersonaCapitalRuntimeDomainPort(
        persona_port=persona_p,
        capital_port=capital_p,
        deployment_port=deploy_p,
        runtime_port=runtime_p,
        ranking_port=ranking_p,
        evolution_port=evolution_p,
    )


__all__ = [
    "PersonaFleetPort",
    "CapitalPoolPort",
    "DeploymentPlanPort",
    "RuntimePort",
    "RankingProjectionPort",
    "EvolutionProjectionPort",
    "PersonaCapitalRuntimeDomainPort",
    "CompositePersonaCapitalRuntimePort",
    "InMemoryPersonaCapitalRuntimePort",
    "create_persona_capital_runtime_port",
    "create_in_memory_persona_capital_runtime_port",
    "PERSONA_OPERATIONAL_LIFECYCLE_STATES",
]
