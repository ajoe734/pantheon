"""Request-scoped owner reads; no cache or BFF copy of domain records."""
from contextvars import ContextVar
from typing import Optional

from ..command_adapters.base import (
    capital_url, deployment_url, evolution_url, http_request_json, internal_url,
)
from ..governance import approval_owner


authorization: ContextVar[Optional[str]] = ContextVar("owner_read_authorization", default=None)


class OwnerReadContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        token = authorization.set(headers.get(b"authorization", b"").decode() or None)
        try:
            await self.app(scope, receive, send)
        finally:
            authorization.reset(token)


def read_records(url_builder, path, key=None):
    auth = authorization.get()
    if not auth:
        raise RuntimeError("Owner reads require the caller's authorization")
    body = http_request_json(url_builder(path), auth_token=auth.removeprefix("Bearer "))
    records = body.get(key) if key and isinstance(body, dict) else body
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise RuntimeError(f"Invalid owner collection for {path}")
    return records


def approval_records():
    if not authorization.get():
        raise RuntimeError("Governance reads require the caller's authorization")
    return approval_owner.list_decisions(authorization.get())


def create_owner_domain_ports(persona_store=None, ranking_store=None):
    from ..ports.persona_capital_runtime import (
        CapitalPoolPort, DeploymentPlanPort, EvolutionProjectionPort,
        PersonaCapitalRuntimeDomainPort, PersonaFleetPort, RankingProjectionPort, RuntimePort,
    )
    from services.rankings.store import build_rankings_store

    def rankings():
        store = ranking_store if ranking_store is not None else build_rankings_store()
        return [record.to_dict() for record in store.list_rankings()]

    return PersonaCapitalRuntimeDomainPort(
        persona_port=PersonaFleetPort(store=persona_store),
        capital_port=CapitalPoolPort(
            pools_provider=lambda: read_records(capital_url, "/api/capital-pools"),
            bindings_provider=lambda: read_records(capital_url, "/api/bindings"),
        ),
        deployment_port=DeploymentPlanPort(
            plans_provider=lambda: read_records(deployment_url, "/api/deployment/plans"),
        ),
        runtime_port=RuntimePort(
            runtime_bindings_provider=lambda: read_records(internal_url, "/api/runtime-bindings", "bindings"),
        ),
        ranking_port=RankingProjectionPort(
            rankings_reader=rankings,
            rebalances_reader=lambda: read_records(capital_url, "/api/rebalances"),
            capital_allocations_reader=lambda: read_records(capital_url, "/api/allocations", "items"),
            containments_reader=lambda: read_records(capital_url, "/api/containments"),
        ),
        evolution_port=EvolutionProjectionPort(
            evolution_programs_reader=lambda: read_records(evolution_url, "/api/evolution/programs", "items"),
            evolution_decisions_reader=lambda: read_records(evolution_url, "/api/evolution/proposals"),
        ),
    )
