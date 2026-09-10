"""Regression: the static /api/v1/incidents/stream SSE route must not be
shadowed by the parameterized /api/v1/incidents/{incident_id} route.

FastAPI/Starlette match routes in registration order. If {incident_id} is
registered first, a request to /api/v1/incidents/stream binds incident_id=
"stream" and returns 404 "Incident stream does not exist", leaving the SSE
stream endpoint dead (verification campaign 2026-06-14, round 2, finding F3).
"""
from __future__ import annotations

from services.control_plane.bff.incidents.router import create_incident_router


def _iter_routes(routes):
    for r in routes:
        if hasattr(r, "original_router"):
            yield from _iter_routes(r.original_router.routes)
        elif hasattr(r, "routes"):
            yield from _iter_routes(r.routes)
        else:
            yield r


def _first_matching_endpoint(routes, path: str):
    for route in _iter_routes(routes):
        regex = getattr(route, "path_regex", None)
        if regex is not None and regex.match(path):
            return route
    return None


def test_incidents_stream_route_not_shadowed() -> None:
    router = create_incident_router()
    route = _first_matching_endpoint(router.routes, "/api/v1/incidents/stream")
    assert route is not None, "/api/v1/incidents/stream did not match any route"
    assert route.endpoint.__name__ == "stream_incident_events", (
        "/api/v1/incidents/stream is shadowed by "
        f"{route.endpoint.__name__} (expected stream_incident_events)"
    )
    assert getattr(route, "path", None) == "/api/v1/incidents/stream"


def test_incident_detail_route_still_resolves() -> None:
    # Ensure the parameterized route still works for real ids.
    router = create_incident_router()
    route = _first_matching_endpoint(router.routes, "/api/v1/incidents/INC-123")
    assert route is not None
    assert route.endpoint.__name__ == "get_incident"
