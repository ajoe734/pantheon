"""Request-scoped owner reads; no cache or BFF copy of domain records."""
from contextvars import ContextVar
from http.cookies import SimpleCookie
from typing import Optional

from ..command_adapters.base import (
    capital_url, deployment_url, evolution_url, http_request_json, get_base_url,
)
from ..governance import approval_owner


authorization: ContextVar[Optional[str]] = ContextVar("owner_read_authorization", default=None)
selected_tenant: ContextVar[Optional[str]] = ContextVar("owner_read_selected_tenant", default=None)


class OwnerReadContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        caller_auth = headers.get(b"authorization", b"").decode().strip()
        if not caller_auth:
            cookie = SimpleCookie()
            try:
                cookie.load(headers.get(b"cookie", b"").decode())
                session = cookie.get("pantheon_session")
                if session and session.value:
                    caller_auth = f"Bearer {session.value}"
            except (UnicodeDecodeError, ValueError):
                caller_auth = ""
        token = authorization.set(caller_auth or None)
        tenant_token = selected_tenant.set(headers.get(b"x-tenant-id", b"").decode().strip() or None)
        try:
            await self.app(scope, receive, send)
        finally:
            selected_tenant.reset(tenant_token)
            authorization.reset(token)


def read_records(url_builder, path, key=None):
    auth = authorization.get()
    if not auth:
        raise RuntimeError("Owner reads require the caller's authorization")
    token = auth.removeprefix("Bearer ")
    # No implicit selection: bound_tenant uses the verified primary/sole tenant or fails closed.
    tenant = selected_tenant.get()
    body = http_request_json(url_builder(path), auth_token=token, tenant_id=tenant)
    records = body.get(key) if key and isinstance(body, dict) else body
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise RuntimeError(f"Invalid owner collection for {path}")
    return records


def telemetry_summaries():
    """Read the authenticated Telemetry owner projection in the current request context."""
    from ..command_adapters.base import get_base_url

    return read_records(
        lambda path: get_base_url("PANTHEON_TELEMETRY_API_URL", "PANTHEON_TELEMETRY_URL") + path,
        "/api/telemetry/runtime-summaries",
        "summaries",
    )


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

    def ranking_formulas():
        from ..personas.service import _pm12_quarter_formula_payload
        return [_pm12_quarter_formula_payload()]

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
            runtime_bindings_provider=lambda: read_records(
                lambda path: get_base_url("PANTHEON_RUNTIME_MANAGER_URL") + path,
                "/api/runtime-bindings", "bindings",
            ),
        ),
        ranking_port=RankingProjectionPort(
            rankings_reader=rankings,
            ranking_formulas_reader=ranking_formulas,
            rebalances_reader=lambda: read_records(capital_url, "/api/rebalances"),
            capital_allocations_reader=lambda: read_records(capital_url, "/api/allocations", "items"),
            containments_reader=lambda: read_records(capital_url, "/api/containments"),
        ),
        evolution_port=EvolutionProjectionPort(
            evolution_programs_reader=lambda: read_records(evolution_url, "/api/evolution/programs", "items"),
            evolution_decisions_reader=lambda: read_records(evolution_url, "/api/evolution/proposals"),
        ),
    )
