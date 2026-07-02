"""Regression: route resolution must not let a generic alias/stub shadow a
dedicated handler.

Verification campaign 2026-06-14, round 9, finding F9. Four (method, path) pairs
have multiple registered handlers; Starlette serves the first match, so the
dedicated handler must register before any generic alias. If a future reorder
let a generic stub win, the endpoint would silently change behavior. This test
locks the intended winner for each duplicated route, and asserts no static route
is shadowed by an earlier parameterized route (the incidents/stream class of
bug, round 2).
"""
from __future__ import annotations

import sys
from pathlib import Path

BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
from starlette.routing import Route  # noqa: E402


# Intended winner for each known duplicately-registered (method, path).
EXPECTED_WINNERS = {
    ("GET", "/bff/events/stream"): "stream_bff_events",
    ("GET", "/bff/management/persona-fleet"): "bff_management_persona_fleet",
    ("GET", "/bff/management/persona-league"): "bff_management_persona_league",
    ("POST", "/bff/ranking-formulas"): "bff_create_ranking_formula_facade",
}


def _first_endpoint(method: str, path: str):
    for route in bff_main.app.routes:
        if isinstance(route, Route) and route.path_regex.match(path) and method in (route.methods or ()):
            return route.endpoint.__name__
    return None


def _matching_endpoints(method: str, path: str) -> list[str]:
    return [
        route.endpoint.__name__
        for route in bff_main.app.routes
        if isinstance(route, Route) and route.path == path and method in (route.methods or ())
    ]


def test_duplicate_routes_resolve_to_intended_handler() -> None:
    for (method, path), expected in EXPECTED_WINNERS.items():
        got = _first_endpoint(method, path)
        assert got == expected, f"{method} {path} resolved to {got}, expected {expected} (generic alias is shadowing)"


def test_management_persona_league_has_single_registered_handler() -> None:
    assert _matching_endpoints("GET", "/bff/management/persona-league") == [
        "bff_management_persona_league",
    ]


def test_no_static_route_shadowed_by_earlier_param_route() -> None:
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
            if methods and methods2 and not (set(methods) & set(methods2)):
                continue
            if rx2.match(path):
                offenders.append(f"{path} ({ep}) shadowed by earlier {path2} ({ep2})")
                break
    assert not offenders, "static routes shadowed by an earlier {param} route: " + "; ".join(offenders)
