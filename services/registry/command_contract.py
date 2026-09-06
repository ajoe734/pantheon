"""Canonical Strategy action -> owner mapping — architecture-resumption-sa-sd.md §3.2.

Registry owns StrategySpec content, immutable versions, RegistryEntry
identities and artifact-state. It does NOT own review-submission,
paper-promotion, activation, pause, or archive business-lifecycle decisions —
those are governance/promotion/runtime concerns. This module is the single
source of truth both the BFF Strategy command adapter and
test_first_release_contract.py check the mounted API surface against, so a
caller never has to guess (or fabricate) which service a given action
belongs to.

Never relabel one business action as another: submit_review is not draft
creation, promote_paper is not spec registration, activate is not revision
creation. An action whose owner is a service this contract does not yet name
must fail explicitly (ActionUnavailableError-shaped), not silently succeed.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ActionOwner(str, Enum):
    """Which service is the lawful authority for a given Strategy action."""

    REGISTRY = "registry"
    GOVERNANCE_REVIEW = "governance_review"
    PROMOTION = "promotion"
    RUNTIME = "runtime"


@dataclass(frozen=True)
class StrategyActionSpec:
    action_id: str
    owner: ActionOwner
    registry_capability: str | None
    """The Registry HTTP capability this action actually invokes, or None if
    Registry has no part in it at all."""
    description: str


# Registry-owned capabilities: the machine-checkable positive list from §3.2.
# test_first_release_contract.py asserts every entry here resolves to a real
# mounted route in services/registry/service.py.
REGISTRY_CAPABILITIES: dict[str, str] = {
    "create_draft": "POST /api/registry/entries",
    "update_metadata_cas": "PATCH /api/registry/entries/{registry_id}/metadata",
    "register_strategy_spec": "POST /api/registry/strategy-specs",
    "create_next_revision": "POST /api/registry/strategy-specs",
    "advance_artifact_state": "POST /api/registry/entries/{registry_id}/advance",
}

# Read paths a caller uses for the exact-version GET/list proof required
# alongside each write capability above; not "capabilities" in their own
# right, so not tracked as separate first_release_contract.json entries.
REGISTRY_READ_PATHS: dict[str, str] = {
    "get_exact_version": "GET /api/registry/entries/{registry_id}",
    "list_versions": "GET /api/registry/strategies/{strategy_id}/entries",
}

# The canonical Strategy action -> owner matrix a BFF command adapter (or any
# other caller) must resolve against before dispatching. Actions not listed
# here are unrecognized and must fail explicitly, not silently succeed.
STRATEGY_ACTIONS: dict[str, StrategyActionSpec] = {
    "update_params": StrategyActionSpec(
        action_id="update_params",
        owner=ActionOwner.REGISTRY,
        registry_capability="update_metadata_cas",
        description=(
            "Allowed operator metadata update on the existing entry. Registry-owned: "
            "a real CAS-guarded PATCH against the entry's metadata field, verified by "
            "a durable owner GET readback."
        ),
    ),
    "submit_review": StrategyActionSpec(
        action_id="submit_review",
        owner=ActionOwner.GOVERNANCE_REVIEW,
        registry_capability=None,
        description=(
            "Review submission is a governance-review lifecycle decision, not Registry "
            "draft creation. Registry has no submit_review capability; this action must "
            "route to the governance-review owner once that integration exists."
        ),
    ),
    "promote_paper": StrategyActionSpec(
        action_id="promote_paper",
        owner=ActionOwner.PROMOTION,
        registry_capability=None,
        description=(
            "Paper promotion is a promotion/deployment-plan decision, not Registry spec "
            "registration. Registry has no promote_paper capability; this action must "
            "route to the promotion owner once that integration exists."
        ),
    ),
    "activate": StrategyActionSpec(
        action_id="activate",
        owner=ActionOwner.RUNTIME,
        registry_capability=None,
        description=(
            "Activation is a runtime/RuntimeBinding decision, not Registry revision "
            "creation. Registry has no activate capability; this action must route to "
            "the runtime owner once that integration exists."
        ),
    ),
    "pause": StrategyActionSpec(
        action_id="pause",
        owner=ActionOwner.RUNTIME,
        registry_capability=None,
        description="Runtime lifecycle decision; Registry has no pause capability.",
    ),
    "archive": StrategyActionSpec(
        action_id="archive",
        owner=ActionOwner.RUNTIME,
        registry_capability=None,
        description="Runtime lifecycle decision; Registry has no archive capability.",
    ),
}


def resolve_action(action_id: str) -> StrategyActionSpec:
    """Resolve a Strategy action to its lawful owner and (if any) Registry capability.

    Raises ``KeyError`` for an action this contract does not recognize at
    all — callers must not guess a default owner for an unlisted action.
    """
    return STRATEGY_ACTIONS[action_id.strip().lower()]
