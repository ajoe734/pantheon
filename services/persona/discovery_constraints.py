"""Discovery constraints of a persona: its route policy and capability snapshot, resolved from their owners.

HYPOTHESIS-CARD-GOVERNED-BINDING-20261125. The persona daily cycle (``services/control-plane/persona/main.py``
``_route_policy`` / ``_capability_snapshot``) and the hypothesis-card admission matcher
(``services/research/alpha_replication/card_production_wiring.py``) both score a StrategySpec against a persona with
the discovery scorer. Both resolve the persona's constraints with these two functions, so a persona whose
policy is referenced (``route_policy_id``) instead of inline is scored the same way on both paths, and never
without its constraints:

* an inline, non-empty value on the persona record wins;
* a referenced route policy is selected from the owner catalog by its own identity (``policy_id`` or ``id``),
  never by a persona-scoped lookup that may return a stale, different policy;
* a referenced policy or snapshot the owner cannot return raises ``route_policy_unavailable:<id>`` /
  ``capability_snapshot_unavailable:<id>`` (fail closed), never ``{}`` in its place.

The owner reads are passed in by the caller (the persona process reads its in-process owner surface; the
worker reads the same owners over HTTP), so this module performs no I/O of its own.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Optional

RoutePolicyCatalogRead = Callable[[], Optional[Iterable[Any]]]
CapabilitySnapshotRead = Callable[[], Optional[Mapping[str, Any]]]


def resolve_route_policy(persona: Mapping[str, Any], list_route_policies: RoutePolicyCatalogRead) -> dict[str, Any]:
    """The persona's route policy: inline, else the referenced one from the owner catalog, else ``{}``."""
    inline = persona.get("route_policy")
    if isinstance(inline, Mapping) and inline:
        return dict(inline)
    policy_id = str(persona.get("route_policy_id") or "").strip()
    if not policy_id:
        return {}
    for policy in list_route_policies() or []:
        if isinstance(policy, Mapping) and policy and policy_id in {
                str(policy.get("policy_id") or "").strip(), str(policy.get("id") or "").strip()} - {""}:
            return dict(policy)
    # referenced policy unreadable/mismatched: never score without (or with other) constraints
    raise RuntimeError(f"route_policy_unavailable:{policy_id}")


def resolve_capability_snapshot(persona: Mapping[str, Any],
                                snapshot_for_persona: CapabilitySnapshotRead) -> dict[str, Any]:
    """The persona's capability snapshot: inline, else the owner's snapshot for the persona.

    A persona that references a snapshot id the owner does not return fails closed; a persona with no
    snapshot reference and no owner snapshot is scored with ``{}`` (no capability constraint exists).
    """
    inline = persona.get("capability_snapshot")
    if isinstance(inline, Mapping) and inline:
        return dict(inline)
    snapshot = snapshot_for_persona()
    snapshot_id = str(persona.get("capability_snapshot_id") or "").strip()
    if not snapshot and snapshot_id:
        raise RuntimeError(f"capability_snapshot_unavailable:{snapshot_id}")
    return dict(snapshot or {})


__all__ = ["resolve_capability_snapshot", "resolve_route_policy"]
