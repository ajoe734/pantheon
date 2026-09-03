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
from test_normalized_route_uniqueness import (  # noqa: E402
    find_parameter_route_shadowing,
    scan_fastapi_routes,
)


def _matching_endpoints(method: str, path: str) -> list[str]:
    return [
        entry.handler_name
        for entry in scan_fastapi_routes(bff_main.app)
        if entry.raw_path == path and entry.method == method
    ]


def test_management_persona_league_has_single_registered_handler() -> None:
    assert _matching_endpoints("GET", "/bff/management/persona-league") == [
        "bff_management_persona_league",
    ]


def test_no_static_route_shadowed_by_earlier_param_route() -> None:
    """Assert that no static/literal route is shadowed by an earlier {param} route."""
    offenders = find_parameter_route_shadowing(bff_main.app)
    assert not offenders, "static routes shadowed by an earlier {param} route: " + str(offenders)


def test_route_resolution_has_no_hardcoded_allowlist() -> None:
    """Verify that the hard-coded duplicate allowlist (EXPECTED_WINNERS) is removed."""
    import test_route_resolution_no_shadowing as this_module
    module_dict = this_module.__dict__
    forbidden_tokens = ["EXPECTED_" + "WINNERS", "DUPLICATE_" + "ALLOWLIST", "ALLOWLIST"]
    for token in forbidden_tokens:
        assert token not in module_dict, f"Route resolution module must not contain allowlist token '{token}'"
