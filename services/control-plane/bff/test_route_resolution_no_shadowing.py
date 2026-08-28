"""Regression & Architecture Gate: Route resolution must not let a generic alias or parameterized route shadow a dedicated handler.

ACG-01-013:
- Hard-coded duplicate allowlist (EXPECTED_WINNERS) has been removed.
- Asserts that no static route is shadowed by an earlier parameterized route across the FastAPI app.
- Preserves single registration assertions for dedicated handlers.
"""
from __future__ import annotations

import sys
from pathlib import Path

BFF_DIR = Path(__file__).resolve().parent
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
from starlette.routing import Route  # noqa: E402


def _matching_endpoints(method: str, path: str) -> list[str]:
    return [
        route.endpoint.__name__
        for route in bff_main.app.routes
        if isinstance(route, Route) and route.path == path and method in (route.methods or ())
    ]


def test_management_persona_league_has_single_registered_handler() -> None:
    assert _matching_endpoints("GET", "/bff/management/persona-league") == [
        "bff_management_persona_league",
    ]


def test_no_static_route_shadowed_by_earlier_param_route() -> None:
    """Assert that no static/literal route is shadowed by an earlier {param} route."""
    routes = [
        (r.path, tuple(r.methods or ()), r.endpoint.__name__, idx, r.path_regex)
        for idx, r in enumerate(bff_main.app.routes)
        if isinstance(r, Route) and getattr(r, "path", None)
    ]
    offenders = []
    for path, methods, ep, idx, _rx in routes:
        if "{" in path:
            continue
        for path2, methods2, ep2, idx2, rx2 in routes:
            if idx2 >= idx:
                break
            if "{" not in path2:
                continue
            if methods and methods2 and not (set(methods) & set(methods2) - {"HEAD", "OPTIONS"}):
                continue
            if rx2.match(path):
                offenders.append(f"{path} ({ep}) shadowed by earlier {path2} ({ep2})")
                break
    assert not offenders, "static routes shadowed by an earlier {param} route: " + "; ".join(offenders)


def test_route_resolution_has_no_hardcoded_allowlist() -> None:
    """Verify that the hard-coded duplicate allowlist (EXPECTED_WINNERS) is removed."""
    import test_route_resolution_no_shadowing as this_module
    module_dict = this_module.__dict__
    forbidden_tokens = ["EXPECTED_" + "WINNERS", "DUPLICATE_" + "ALLOWLIST", "ALLOWLIST"]
    for token in forbidden_tokens:
        assert token not in module_dict, f"Route resolution module must not contain allowlist token '{token}'"
