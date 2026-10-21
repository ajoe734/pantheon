"""Request-scoped owner reads; no cross-request cache or BFF copy of domain records."""
from contextvars import ContextVar
from functools import wraps
from http.cookies import SimpleCookie
import inspect
import json
import os
import threading
import time
from typing import Any, Optional
import urllib.error
import urllib.request

authorization: ContextVar[Optional[str]] = ContextVar("owner_read_authorization", default=None)
selected_tenant: ContextVar[Optional[str]] = ContextVar("owner_read_selected_tenant", default=None)
request_read_cache: ContextVar[Optional[dict[Any, Any]]] = ContextVar("owner_read_request_cache", default=None)
management_read_deadline: ContextVar[Optional[float]] = ContextVar("management_read_deadline", default=None)


def remaining_management_read_budget(
    default: Optional[float] = None,
    *,
    reserve_seconds: float = 0.0,
) -> Optional[float]:
    """Return remaining seconds before the active management_read_deadline, if any."""
    deadline = management_read_deadline.get()
    if deadline is None:
        return default
    remaining = max(0.0, deadline - time.monotonic() - max(0.0, reserve_seconds))
    if default is None:
        return remaining
    return min(float(default), remaining)

_CACHE_LOCK = threading.Lock()
_PLAIN_DICT_LOCKS: dict[int, tuple[dict[Any, Any], dict[Any, threading.RLock]]] = {}


class _RequestReadCache(dict):
    def __init__(self) -> None:
        super().__init__()
        self._key_locks: dict[Any, threading.RLock] = {}


def _key_lock(cache: dict[Any, Any], key: Any) -> threading.RLock:
    with _CACHE_LOCK:
        locks = getattr(cache, "_key_locks", None)
        if not isinstance(locks, dict):
            entry = _PLAIN_DICT_LOCKS.get(id(cache))
            if entry is not None and entry[0] is cache:
                locks = entry[1]
            else:
                locks = {}
                try:
                    setattr(cache, "_key_locks", locks)
                except AttributeError:
                    if len(_PLAIN_DICT_LOCKS) >= 64:
                        _PLAIN_DICT_LOCKS.pop(next(iter(_PLAIN_DICT_LOCKS)))
                    _PLAIN_DICT_LOCKS[id(cache)] = (cache, locks)
        lock = locks.get(key)
        if lock is None:
            lock = threading.RLock()
            locks[key] = lock
        return lock


class request_read_scope:
    def __enter__(self):
        self._tok = request_read_cache.set(_RequestReadCache()) if request_read_cache.get() is None else None
        return self

    def __exit__(self, *exc):
        if self._tok is not None:
            request_read_cache.reset(self._tok)

    def __call__(self, fn):
        if inspect.iscoroutinefunction(fn):
            @wraps(fn)
            async def _aw(*a, **kw):
                with request_read_scope():
                    return await fn(*a, **kw)
            return _aw
        @wraps(fn)
        def _w(*a, **kw):
            with request_read_scope():
                return fn(*a, **kw)
        return _w


def cached_owner_call(key: Any, fn: Any) -> Any:
    cache = request_read_cache.get()
    if cache is None:
        return fn()
    if key not in cache:
        with _key_lock(cache, key):
            if key not in cache:
                try:
                    cache[key] = (True, fn())
                except Exception as exc:
                    cache[key] = (False, exc)
    ok, val = cache[key]
    if not ok:
        raise val
    return val


def evict_request_read_keys(*prefixes: str) -> None:
    cache = request_read_cache.get()
    if cache is None:
        return
    with _CACHE_LOCK:
        for k in [k for k in tuple(cache) if isinstance(k, tuple) and k and any(str(k[0]).startswith(p) or p in str(k[:2]) for p in prefixes)]:
            cache.pop(k, None)

from ..command_adapters.base import (
    ActionUnavailableError,
    bound_tenant,
    capital_url,
    deployment_url,
    evolution_url,
    get_base_url,
    http_request_json,
    record_downstream_outcome,
)
from ..governance import approval_owner


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
        cache_token = request_read_cache.set(_RequestReadCache())
        try:
            await self.app(scope, receive, send)
        finally:
            request_read_cache.reset(cache_token)
            selected_tenant.reset(tenant_token)
            authorization.reset(token)


def verified_caller_tenant(payload: Any = None, tenant_id: Optional[str] = None) -> str:
    auth = authorization.get()
    caller_token = auth.removeprefix("Bearer ").strip() if auth else None
    tid = tenant_id or selected_tenant.get()
    return bound_tenant(payload or {}, tenant_id=tid, auth_token=caller_token)


def read_records(url_builder, path, key=None):
    auth = authorization.get()
    if not auth:
        raise RuntimeError("Owner reads require the caller's authorization")
    token = auth.removeprefix("Bearer ")
    # No implicit selection: bound_tenant uses the verified primary/sole tenant or fails closed.
    tenant = selected_tenant.get()
    url = url_builder(path)

    def _fetch():
        body = http_request_json(url, auth_token=token, tenant_id=tenant)
        records = body.get(key) if key and isinstance(body, dict) else body
        if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
            raise RuntimeError(f"Invalid owner collection for {path}")
        return records

    return cached_owner_call(("http_records", path, url, key, token, tenant), _fetch)


def _evolution_http_get(url: str, service_token: str, tenant_id: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "X-Pantheon-Service": "control-plane-bff",
            "Authorization": f"Bearer {service_token}",
            "X-Tenant-Id": tenant_id,
        },
        method="GET",
    )
    timeout = int(os.getenv("PANTHEON_COMMAND_TIMEOUT_SECONDS", "30"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status_code = int(resp.status)
            body = json.loads(resp.read().decode("utf-8"))
            record_downstream_outcome(url, ok=True, status_code=status_code)
            return body
    except urllib.error.HTTPError as exc:
        record_downstream_outcome(url, ok=False, status_code=int(exc.code), detail=f"HTTP {exc.code}")
        if exc.code == 403:
            raise ActionUnavailableError(
                f"Evolution owner access forbidden for tenant {tenant_id}",
                downstream_status=403,
            ) from exc
        elif exc.code == 401:
            raise ActionUnavailableError(
                "Evolution owner authentication failed",
                downstream_status=403,
            ) from exc
        else:
            raise ActionUnavailableError(
                f"Evolution owner request failed with status {exc.code}",
                downstream_status=exc.code if exc.code in (403, 503) else 503,
            ) from exc
    except ActionUnavailableError:
        raise
    except Exception as exc:
        record_downstream_outcome(url, ok=False, status_code=-1, detail=str(exc))
        raise ActionUnavailableError(
            f"Evolution owner unavailable: {exc}",
            downstream_status=503,
        ) from exc


def read_evolution_records(url_builder, path, key=None):
    auth = authorization.get()
    if not auth:
        raise RuntimeError("Owner reads require the caller's authorization")
    caller_token = auth.removeprefix("Bearer ").strip()

    # If the target service URL is unconfigured, fail fast with URL configuration error
    url = url_builder(path)

    tenant = selected_tenant.get()
    caller_tenant = bound_tenant({}, tenant_id=tenant, auth_token=caller_token)

    service_token = os.getenv("EVOLUTION_AUTH_TOKEN", "").strip()
    if not service_token:
        raise ActionUnavailableError(
            "Evolution owner authentication is unconfigured (missing EVOLUTION_AUTH_TOKEN)",
            downstream_status=503,
        )

    def _fetch():
        body = _evolution_http_get(url, service_token, caller_tenant)
        records = body.get(key) if key and isinstance(body, dict) else body
        if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
            raise RuntimeError(f"Invalid owner collection for {path}")
        return records

    return cached_owner_call(("evo_records", path, url, key, caller_tenant, service_token), _fetch)


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
    return cached_owner_call(("approval_records", authorization.get(), selected_tenant.get()), lambda: approval_owner.list_decisions(authorization.get()))


def create_owner_domain_ports(persona_store=None, ranking_store=None):
    from ..ports.persona_capital_runtime import (
        CapitalPoolPort, DeploymentPlanPort, EvolutionProjectionPort,
        PersonaCapitalRuntimeDomainPort, PersonaFleetPort, RankingProjectionPort, RuntimePort,
    )
    from services.rankings.store import build_rankings_store

    def rankings():
        return cached_owner_call(("rankings_store", id(ranking_store), selected_tenant.get()), lambda: [r.to_dict() for r in (ranking_store or build_rankings_store()).list_rankings()])

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
            evolution_programs_reader=lambda: read_evolution_records(evolution_url, "/api/evolution/programs", "items"),
            evolution_decisions_reader=lambda: persona_store.list_evolution_decisions() if hasattr(persona_store, "list_evolution_decisions") and not os.getenv("PANTHEON_EVOLUTION_API_URL", "").strip() else read_evolution_records(evolution_url, "/api/evolution/proposals"),
        ),
    )
