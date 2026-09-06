"""Consistency checks for the frozen first-release capability contract.

architecture-resumption-sa-sd.md §3.2 requires the capability/API/DTO/action-
owner-state/retirement matrix to be frozen *and* reconciled with actual
callers — not a document nobody checks against the running code. This module
is that check: every capability's route must actually be mounted, every
STRATEGY_ACTIONS entry naming a registry_capability must resolve to a real
capability, and no unlisted mutating route should silently appear on the
mounted app (which would mean either this contract or the retirement claim
went stale).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from .command_contract import REGISTRY_CAPABILITIES, STRATEGY_ACTIONS, resolve_action
from .service import app

_CONTRACT_PATH = Path(__file__).parent / "first_release_contract.json"

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Health/docs/openapi are framework-provided, not registry capabilities.
_NON_CAPABILITY_PATHS = {"/health", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _mounted_routes() -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path:
            continue
        for method in methods - {"HEAD", "OPTIONS"}:
            routes.add((method, path))
    return routes


def _contract() -> dict:
    return json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))


def test_contract_file_is_valid_json_with_required_sections():
    contract = _contract()
    for key in ("write_authority", "capabilities", "action_owner_matrix", "bff_adapter_semantics", "retirement"):
        assert key in contract, f"first_release_contract.json missing required section: {key}"


def test_every_capability_route_is_actually_mounted():
    contract = _contract()
    mounted = _mounted_routes()
    for capability in contract["capabilities"]:
        method, path = capability["route"].split(" ", 1)
        assert (method, path) in mounted, (
            f"first_release_contract.json capability {capability['capability']!r} "
            f"names route {capability['route']!r} which is not mounted on the app"
        )


def test_every_registry_capability_constant_matches_a_contract_capability():
    contract_capability_ids = {c["capability"] for c in _contract()["capabilities"]}
    for capability_id, route in REGISTRY_CAPABILITIES.items():
        assert capability_id in contract_capability_ids, (
            f"command_contract.REGISTRY_CAPABILITIES has {capability_id!r} with no matching "
            "entry in first_release_contract.json"
        )
        method, path = route.split(" ", 1)
        assert (method, path) in _mounted_routes(), (
            f"command_contract.REGISTRY_CAPABILITIES[{capability_id!r}] names an unmounted route: {route!r}"
        )


def test_every_strategy_action_registry_capability_resolves():
    """Every STRATEGY_ACTIONS entry that claims a registry_capability must name
    a real capability in REGISTRY_CAPABILITIES — a typo here would silently
    make the BFF adapter dispatch to a nonexistent capability."""
    for action_id, spec in STRATEGY_ACTIONS.items():
        if spec.registry_capability is not None:
            assert spec.registry_capability in REGISTRY_CAPABILITIES, (
                f"STRATEGY_ACTIONS[{action_id!r}].registry_capability={spec.registry_capability!r} "
                "does not name a real REGISTRY_CAPABILITIES entry"
            )


def test_action_owner_matrix_json_matches_command_contract_python():
    """The frozen JSON snapshot must not drift from the executable Python
    matrix a caller actually resolves against at runtime."""
    json_entries = {
        entry["action_id"]: (entry["owner"], entry["registry_capability"])
        for entry in _contract()["action_owner_matrix"]["entries"]
    }
    python_entries = {
        action_id: (spec.owner.value, spec.registry_capability)
        for action_id, spec in STRATEGY_ACTIONS.items()
    }
    assert json_entries == python_entries


def test_resolve_action_raises_for_unrecognized_action():
    with pytest.raises(KeyError):
        resolve_action("not_a_real_action")


def test_resolve_action_is_case_and_whitespace_insensitive():
    spec = resolve_action("  Update_Params  ")
    assert spec.action_id == "update_params"


@pytest.mark.parametrize(
    "action_id",
    ["submit_review", "promote_paper", "activate", "pause", "archive"],
)
def test_non_registry_actions_declare_no_registry_capability(action_id):
    """These actions must never claim a Registry capability — that would be
    exactly the create-draft/register-spec/create-revision relabeling
    architecture-resumption-sa-sd.md §3.2 forbids."""
    spec = resolve_action(action_id)
    assert spec.registry_capability is None


def test_no_unlisted_mutating_routes_are_mounted():
    """Every mutating route on the mounted app must be named by some
    capability in the frozen contract (or be an explicitly out-of-scope,
    pre-existing route this task did not touch) — this is the retirement
    inventory's actual enforcement, not an rg-only inventory."""
    contract = _contract()
    contract_routes = {tuple(c["route"].split(" ", 1)) for c in contract["capabilities"]}
    # Pre-existing mutating routes this task's contract does not (yet) cover
    # explicitly — strategy-artifact and allocation-policy-artifact capability
    # families predate this task and are out of its declared scope; they are
    # listed here so a genuinely new unlisted route still fails this test.
    pre_existing_out_of_scope = {
        ("PUT", "/api/registry/entries/{registry_id}/deployment-summary"),
        ("POST", "/api/registry/strategy-specs/{registry_id}/advance"),
        ("POST", "/api/registry/strategy-artifacts"),
        ("POST", "/api/registry/strategy-artifacts/{registry_id}/mutate"),
        ("POST", "/api/registry/strategy-artifacts/{registry_id}/advance"),
        ("POST", "/api/registry/allocation-policy-artifacts"),
        ("POST", "/api/registry/allocation-policy-artifacts/{registry_id}/advance"),
    }
    allowed = contract_routes | pre_existing_out_of_scope

    for method, path in _mounted_routes():
        if method not in _MUTATING_METHODS or path in _NON_CAPABILITY_PATHS:
            continue
        assert (method, path) in allowed, (
            f"Mounted mutating route {method} {path} is not named by first_release_contract.json "
            "or the pre-existing out-of-scope allowlist in this test — freeze it in the contract "
            "before shipping."
        )
