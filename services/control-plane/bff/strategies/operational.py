"""Operational truth for Strategy reads: persona states, operationalStatus, rules.

This module is the only place that derives ``operationalStatus`` and
``personaStates``. Persona state is never recomputed here: the caller hands in
the Persona directory read and the Persona route's own projection function, so
the Strategy pages and ``GET /bff/personas`` agree by construction.

Read-only: nothing in this module writes a strategy, artifact or persona.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional

log = logging.getLogger(__name__)

STATUS_RUNNING = "running"
STATUS_IDLE = "idle"
STATUS_RETIRED = "retired"
STATUS_UNBOUND = "unbound"
STATUS_UNKNOWN = "unknown"

OPERATIONAL_STATUSES = (
    STATUS_RUNNING,
    STATUS_IDLE,
    STATUS_RETIRED,
    STATUS_UNBOUND,
    STATUS_UNKNOWN,
)

PERSONA_STATE_MISSING = "missing"
PERSONA_STATE_UNKNOWN = "unknown"

ROLE_TRADING = "trading"
ROLE_ROLLBACK_BASELINE = "rollback_baseline"


@dataclass(frozen=True)
class PersonaStateLookup:
    """One request's view of Persona states, read from the directory once."""

    available: bool
    states_by_id: Mapping[str, str] = field(default_factory=dict)
    operational_states: FrozenSet[str] = frozenset()
    failure: Optional[str] = None

    def persona_states(self, persona_ids: Iterable[str]) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        for persona_id in persona_ids:
            if not self.available:
                state = PERSONA_STATE_UNKNOWN
            else:
                state = self.states_by_id.get(persona_id, PERSONA_STATE_MISSING)
            rows.append({"personaId": persona_id, "state": state})
        return rows

    def operational_status(self, persona_ids: Iterable[str]) -> str:
        ids = list(persona_ids)
        if not self.available:
            return STATUS_UNKNOWN
        if not ids:
            return STATUS_UNBOUND
        states = [self.states_by_id.get(pid, PERSONA_STATE_MISSING) for pid in ids]
        if any(state in self.operational_states for state in states):
            return STATUS_RUNNING
        if all(state == STATUS_RETIRED for state in states):
            return STATUS_RETIRED
        return STATUS_IDLE

    def surface(self) -> Dict[str, Any]:
        if self.available:
            return {"status": "ok", "source": "persona_directory"}
        return {
            "status": "unavailable",
            "source": "persona_directory",
            "reason": self.failure or "persona directory read failed",
        }


def unavailable_lookup(reason: str) -> PersonaStateLookup:
    return PersonaStateLookup(available=False, failure=reason)


def read_persona_state_lookup(
    *,
    directory_snapshot: Optional[Callable[..., Any]],
    projected_state: Optional[Callable[[Mapping[str, Any]], str]],
    operational_states: Optional[Iterable[str]],
    tenant_id: str,
    snapshot_at: str,
) -> PersonaStateLookup:
    """Read the Persona directory once and project each record's state.

    ``directory_snapshot`` and ``projected_state`` are the same callables the
    Persona list route uses. Any failure yields an unavailable lookup so the
    caller reports ``unknown`` instead of hiding rows.
    """
    if directory_snapshot is None or projected_state is None or operational_states is None:
        return unavailable_lookup("persona directory is not wired for strategy reads")
    try:
        directory = directory_snapshot(tenant_id, snapshot_at=snapshot_at)
        states = {
            str(persona_id): str(projected_state(record))
            for persona_id, record in directory.records_by_id.items()
        }
    except Exception as exc:  # noqa: BLE001 - any owner failure degrades, never hides rows
        log.warning("strategy persona state read failed: %s", exc)
        return unavailable_lookup(f"persona directory read failed: {type(exc).__name__}")
    return PersonaStateLookup(
        available=True,
        states_by_id=states,
        operational_states=frozenset(str(s) for s in operational_states),
    )


def summary_persona_ids(row: Mapping[str, Any]) -> List[str]:
    raw = row.get("persona_ids")
    if not isinstance(raw, list):
        raw = row.get("personaIds")
    if not isinstance(raw, list):
        return []
    return [str(p) for p in raw if str(p).strip()]


def operational_counts(statuses: Iterable[str]) -> Dict[str, int]:
    counts = {name: 0 for name in OPERATIONAL_STATUSES}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    return counts


def _clean_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def project_strategy_rules(
    entries: Any,
    *,
    strategy_id: str,
) -> List[Dict[str, Any]]:
    """One rule per approved execution_bundle of ``strategy_id``.

    ``entries`` is the registry owner response of
    ``GET /api/registry/strategies/{strategy_id}/strategy-artifacts``: a list
    of RegistryEntryView (``{"entry": {...}}``) or bare entries. Role is
    ``rollback_baseline`` only when the registry id is named in
    ``lineage.parent_registry_ids`` of another approved bundle of the same
    strategy. No registry id is ever derived by rewriting another id.
    """
    approved: List[Dict[str, Any]] = []
    for item in entries if isinstance(entries, list) else []:
        entry = item.get("entry") if isinstance(item, dict) and isinstance(item.get("entry"), dict) else item
        if not isinstance(entry, dict):
            continue
        if entry.get("artifact_type") not in (None, "execution_bundle"):
            continue
        if str(entry.get("artifact_state") or "").strip().lower() != "approved":
            continue
        if str(entry.get("strategy_id") or "").strip() != strategy_id:
            continue
        registry_id = str(entry.get("registry_id") or "").strip()
        if not registry_id:
            continue
        approved.append(entry)

    parent_ids_by_bundle: Dict[str, List[str]] = {}
    for entry in approved:
        lineage = _clean_dict(entry.get("lineage"))
        parents = lineage.get("parent_registry_ids")
        parent_ids_by_bundle[str(entry["registry_id"])] = (
            [str(p) for p in parents if str(p).strip()] if isinstance(parents, list) else []
        )

    rules: List[Dict[str, Any]] = []
    for entry in approved:
        registry_id = str(entry["registry_id"])
        is_parent_of_other = any(
            registry_id in parents
            for other_id, parents in parent_ids_by_bundle.items()
            if other_id != registry_id
        )
        metadata = _clean_dict(entry.get("metadata"))
        artifact = _clean_dict(metadata.get("strategy_artifact"))
        mutation = _clean_dict(artifact.get("mutation_surface"))
        controls = mutation.get("controls")
        immutable = mutation.get("immutable_parameters")
        rules.append(
            {
                "registryId": registry_id,
                "version": entry.get("version"),
                "role": ROLE_ROLLBACK_BASELINE if is_parent_of_other else ROLE_TRADING,
                "strategyLogic": _clean_dict(artifact.get("strategy_logic")),
                "parameters": _clean_dict(artifact.get("parameters")),
                "adjustableControls": list(controls) if isinstance(controls, list) else [],
                "immutableParameters": list(immutable) if isinstance(immutable, list) else [],
            }
        )
    return rules
