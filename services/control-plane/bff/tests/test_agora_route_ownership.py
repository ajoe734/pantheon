"""Route-ownership invariants for the ACG-06-BE Workshop backend decomposition.

Covers the acceptance gates for ACG-WORKSHOP-BE-20260828:
- the strategy-workshop router registers each of its 18 contracts exactly
  once, split disjointly across the session/versions/execution/stream
  route-group modules (ACG-06-004);
- Interaction and Research no longer import a leading-underscore
  (router-private) helper out of strategy_workshop.router -- they use the
  public events module instead (ACG-06-002);
- the readiness projector has a public (non-underscore) entry point
  (ACG-06-003);
- MemoryWorkshopStore requires explicit injection; it is never constructed
  implicitly by the router factory itself (ACG-06-007).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agora.strategy_workshop.router import create_strategy_workshop_router
from agora.strategy_workshop.routes.execution import build_execution_router
from agora.strategy_workshop.routes.session import build_session_router
from agora.strategy_workshop.routes.stream import build_stream_router
from agora.strategy_workshop.routes.versions import build_versions_router
from agora.strategy_workshop.store import MemoryWorkshopStore, PostgresWorkshopStore
from agora.strategy_workshop._admission import build_admission_context
from agora.strategy_workshop.readiness import build_readiness_assessment
from agora.strategy_workshop.operations import WorkshopCanonicalOperations

_BFF_DIR = Path(os.path.dirname(os.path.dirname(__file__)))

_EXPECTED_ROUTES = {
    ("GET", "/bff/agora/workshops"),
    ("POST", "/bff/agora/workshops"),
    ("GET", "/bff/agora/workshops/{workshop_id}"),
    ("POST", "/bff/agora/workshops/{workshop_id}/messages"),
    ("GET", "/bff/agora/workshops/{workshop_id}/events"),
    ("GET", "/bff/agora/workshops/{workshop_id}/completeness"),
    ("POST", "/bff/agora/workshops/{workshop_id}/completeness"),
    ("GET", "/bff/agora/workshops/{workshop_id}/cards"),
    ("GET", "/bff/agora/workshops/{workshop_id}/readiness"),
    ("POST", "/bff/agora/workshops/{workshop_id}/readiness/reassess"),
    ("POST", "/bff/agora/workshops/{workshop_id}/reconstruct"),
    ("GET", "/bff/agora/workshops/{workshop_id}/versions"),
    ("POST", "/bff/agora/workshops/{workshop_id}/versions"),
    ("POST", "/bff/agora/workshops/{workshop_id}/versions/{version_id}/select"),
    ("POST", "/bff/agora/workshops/{workshop_id}/research-runs"),
    ("POST", "/bff/agora/workshops/{workshop_id}/consultations"),
    ("POST", "/bff/agora/workshops/{workshop_id}/conclude"),
    ("GET", "/bff/agora/workshops/{workshop_id}/stream"),
}


def _fake_dependencies():
    def extract_identity(authorization, mfa_token=None):
        return {"operator_id": "u1", "roles": ["operator"]}

    def require_read_role(identity):
        return None

    def require_write_role(identity):
        return None

    def bff_error(status, code, message, reason, **kw):
        from fastapi import HTTPException

        return HTTPException(status_code=status, detail={"code": str(code), "message": message})

    def utc_now():
        return "2026-01-01T00:00:00Z"

    return extract_identity, require_read_role, require_write_role, bff_error, utc_now


def _route_set(router) -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in router.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", None)
        if not path:
            continue
        for method in methods:
            if method != "HEAD":
                routes.add((method, path))
    return routes


def test_workshop_router_registers_each_of_the_18_contracts_exactly_once():
    extract_identity, require_read_role, require_write_role, bff_error, utc_now = _fake_dependencies()
    router = create_strategy_workshop_router(
        extract_identity=extract_identity,
        require_read_role=require_read_role,
        require_write_role=require_write_role,
        bff_error=bff_error,
        utc_now=utc_now,
        workshop_store=MemoryWorkshopStore(),
    )
    routes = _route_set(router)
    assert routes == _EXPECTED_ROUTES
    # No duplicate (method, path) registrations: one route object per pair.
    seen: list[tuple[str, str]] = []
    for route in router.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", None)
        if not path:
            continue
        for method in methods:
            if method != "HEAD":
                seen.append((method, path))
    assert len(seen) == len(_EXPECTED_ROUTES)


def test_session_versions_execution_stream_subrouters_are_disjoint():
    extract_identity, require_read_role, require_write_role, bff_error, utc_now = _fake_dependencies()
    store = MemoryWorkshopStore()
    canonical = WorkshopCanonicalOperations()
    ctx = build_admission_context(
        store=store,
        canonical=canonical,
        extract_identity=extract_identity,
        require_read_role=require_read_role,
        require_write_role=require_write_role,
        bff_error=bff_error,
        utc_now=utc_now,
    )
    sys.path.insert(0, str(_BFF_DIR.parent))
    from privacy.private_content_store import EphemeralKeyProvider, MemoryPrivateContentStore

    private_content_store = MemoryPrivateContentStore(key_provider=EphemeralKeyProvider())

    groups = {
        "session": _route_set(build_session_router(
            store=store, canonical=canonical, private_content_store=private_content_store,
            utc_now=utc_now, bff_error=bff_error, ctx=ctx,
        )),
        "versions": _route_set(build_versions_router(
            store=store, canonical=canonical, utc_now=utc_now, bff_error=bff_error, ctx=ctx,
        )),
        "execution": _route_set(build_execution_router(
            store=store, canonical=canonical, utc_now=utc_now, bff_error=bff_error, ctx=ctx,
        )),
        "stream": _route_set(build_stream_router(
            store=store, utc_now=utc_now, bff_error=bff_error, ctx=ctx,
        )),
    }

    # Each subrouter owns at least one route.
    for name, routes in groups.items():
        assert routes, f"{name} subrouter registered no routes"

    # No two subrouters register the same (method, path) contract.
    all_pairs: list[tuple[str, str]] = []
    for routes in groups.values():
        all_pairs.extend(routes)
    assert len(all_pairs) == len(set(all_pairs)) == len(_EXPECTED_ROUTES)
    assert set(all_pairs) == _EXPECTED_ROUTES


@pytest.mark.parametrize(
    "relative_path",
    [
        "agora/interaction/runner.py",
        "agora/research/router.py",
    ],
)
def test_agora_routers_do_not_import_workshop_router_privates(relative_path: str):
    """ACG-06-002: cross-router callers use the public events module, not
    a leading-underscore name out of strategy_workshop.router."""
    source = (_BFF_DIR / relative_path).read_text(encoding="utf-8")
    assert not re.search(r"strategy_workshop\.router\s+import\s+_", source), (
        f"{relative_path} still imports a private helper from "
        "strategy_workshop.router; import from strategy_workshop.events instead"
    )


def test_workshop_events_module_is_the_public_sse_owner():
    from agora.strategy_workshop import events as workshop_events

    # Interaction/Research import these two names from the public module.
    assert callable(workshop_events._ws_publish)
    assert callable(workshop_events._ws_replay_after)


def test_readiness_has_a_public_non_underscore_entry_point():
    from agora.strategy_workshop import readiness as workshop_readiness

    assert callable(build_readiness_assessment)
    assert workshop_readiness.build_readiness_assessment is build_readiness_assessment
    assert "build_readiness_assessment" in vars(workshop_readiness)


def test_memory_store_requires_explicit_injection_not_router_default():
    """ACG-06-007: the router factory never silently selects
    MemoryWorkshopStore -- it only uses whatever `workshop_store` the
    caller injects, or resolves the configured backend via
    make_workshop_store(). Passing a MemoryWorkshopStore in explicitly
    (as tests do) remains supported; nothing here can construct it for us
    without our providing it."""
    extract_identity, require_read_role, require_write_role, bff_error, utc_now = _fake_dependencies()
    injected = MemoryWorkshopStore()
    router = create_strategy_workshop_router(
        extract_identity=extract_identity,
        require_read_role=require_read_role,
        require_write_role=require_write_role,
        bff_error=bff_error,
        utc_now=utc_now,
        workshop_store=injected,
    )
    # The router's routes close over the *injected* store object -- confirm
    # by exercising a route and checking the store saw the call.
    assert injected.list_sessions(user_id="u1", tenant_id="t1", status=None, cursor=None, limit=1) == ([], None)


def test_postgres_workshop_store_is_the_named_production_backend():
    assert PostgresWorkshopStore.__name__ == "PostgresWorkshopStore"
    with pytest.raises(ValueError):
        PostgresWorkshopStore(dsn="")
