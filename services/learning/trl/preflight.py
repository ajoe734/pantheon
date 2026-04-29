"""
TRL pre-activation preflight scaffold.

Checks all gate conditions for TRL DPO activation without executing DPO or
writing canonical state.  Returns a fail-closed report.

Gates:
  fb002_volume (REQUIRED)        - FB-002 store must have >=200 governed events.
  preference_pairs (REQUIRED)    - >=100 valid preference pairs must be present.
  imitation_artifact (INFO)      - LP-002 imitation artifact must be at
                                   artifact_state=approved.
  downstream_consumers (INFO)    - at least one of EV-001, LP-005, LP-001 must
                                   be registered.

REQUIRED gates set activation_allowed=False when BLOCKED.
INFORMATIONAL gates are reported without affecting activation_allowed; they
record GateState.UNKNOWN when no probe is supplied.

This module never imports the TRL adapter, never initiates DPO training, and
never writes to the registry or governance system.
"""
from __future__ import annotations

import dataclasses
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, Sequence

FB002_MIN_EVENTS: int = 200
PREFERENCE_PAIR_MIN: int = 100

_ACCEPTED_CONSUMERS: FrozenSet[str] = frozenset({"EV-001", "LP-005", "LP-001"})


class GateState(str, Enum):
    OPEN = "open"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclasses.dataclass(frozen=True)
class GateResult:
    name: str
    state: GateState
    detail: str
    required: bool


@dataclasses.dataclass(frozen=True)
class PreflightReport:
    gates: List[GateResult]
    activation_allowed: bool
    summary: str

    @classmethod
    def from_gates(cls, gates: List[GateResult]) -> "PreflightReport":
        required_blocked = [g for g in gates if g.required and g.state == GateState.BLOCKED]
        allowed = not required_blocked
        if allowed:
            summary = "All required gates open - TRL DPO activation unblocked."
        else:
            names = ", ".join(g.name for g in required_blocked)
            summary = f"Blocked by required gates: {names}"
        return cls(gates=list(gates), activation_allowed=allowed, summary=summary)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "activation_allowed": self.activation_allowed,
            "summary": self.summary,
            "gates": [dataclasses.asdict(gate) for gate in self.gates],
        }


def check_fb002_volume(events: Sequence[Any]) -> GateResult:
    """Gate fb002_volume (REQUIRED): FB-002 must have >=200 governed feedback events."""
    count = len(events)
    if count >= FB002_MIN_EVENTS:
        return GateResult(
            name="fb002_volume",
            state=GateState.OPEN,
            detail=f"FB-002 volume OK: {count} events (min {FB002_MIN_EVENTS})",
            required=True,
        )
    return GateResult(
        name="fb002_volume",
        state=GateState.BLOCKED,
        detail=f"FB-002 volume insufficient: {count} events, need >={FB002_MIN_EVENTS}",
        required=True,
    )


def check_preference_pairs(pairs: Sequence[Any]) -> GateResult:
    """Gate preference_pairs (REQUIRED): >=100 valid preference pairs must be present."""
    count = len(pairs)
    if count >= PREFERENCE_PAIR_MIN:
        return GateResult(
            name="preference_pairs",
            state=GateState.OPEN,
            detail=f"Preference pairs OK: {count} valid pairs (min {PREFERENCE_PAIR_MIN})",
            required=True,
        )
    return GateResult(
        name="preference_pairs",
        state=GateState.BLOCKED,
        detail=f"Preference pairs insufficient: {count} pairs, need >={PREFERENCE_PAIR_MIN}",
        required=True,
    )


def check_imitation_artifact(registry_probe: Optional[Dict[str, Any]] = None) -> GateResult:
    """Gate imitation_artifact (INFORMATIONAL): LP-002 artifact must be approved.

    Reports gate state without writing canonical state.
    Pass registry_probe=None to record UNKNOWN.
    """
    if registry_probe is None:
        return GateResult(
            name="imitation_artifact",
            state=GateState.UNKNOWN,
            detail="Imitation artifact registry probe not supplied - gate state unknown",
            required=False,
        )
    artifact_state = registry_probe.get("artifact_state", "")
    if artifact_state == "approved":
        return GateResult(
            name="imitation_artifact",
            state=GateState.OPEN,
            detail="Imitation LP-002 artifact present: artifact_state='approved'",
            required=False,
        )
    return GateResult(
        name="imitation_artifact",
        state=GateState.BLOCKED,
        detail=(
            f"Imitation LP-002 artifact not ready: artifact_state={artifact_state!r}; "
            "need 'approved'"
        ),
        required=False,
    )


def check_downstream_consumers(consumer_probe: Optional[Dict[str, Any]] = None) -> GateResult:
    """Gate downstream_consumers (INFORMATIONAL): at least one consumer must be registered.

    Reports gate state without writing canonical state.
    Pass consumer_probe=None to record UNKNOWN.
    """
    if consumer_probe is None:
        return GateResult(
            name="downstream_consumers",
            state=GateState.UNKNOWN,
            detail="Downstream consumer probe not supplied - gate state unknown",
            required=False,
        )
    registered = set(consumer_probe.get("registered_consumers", []))
    ready = registered & _ACCEPTED_CONSUMERS
    if ready:
        return GateResult(
            name="downstream_consumers",
            state=GateState.OPEN,
            detail=f"Downstream consumer readiness present: {sorted(ready)}",
            required=False,
        )
    return GateResult(
        name="downstream_consumers",
        state=GateState.BLOCKED,
        detail=f"No downstream consumer registered; need one of {sorted(_ACCEPTED_CONSUMERS)}",
        required=False,
    )


def run_trl_preflight(
    fb002_events: Sequence[Any],
    preference_pairs: Sequence[Any],
    imitation_registry_probe: Optional[Dict[str, Any]] = None,
    downstream_consumer_probe: Optional[Dict[str, Any]] = None,
) -> PreflightReport:
    """Run TRL pre-activation preflight.

    Checks all required and informational gates.  Never executes DPO, never
    writes to registry or governance.  Returns a fail-closed report.

    Args:
        fb002_events: Governed FB-002 feedback events from the feedback store.
        preference_pairs: Valid preference pairs constructable from fb002_events.
        imitation_registry_probe: Optional dict with 'artifact_state' from an
            LP-002 imitation registry lookup.  Pass None to record UNKNOWN.
        downstream_consumer_probe: Optional dict with 'registered_consumers'
            list.  Pass None to record UNKNOWN.

    Returns:
        PreflightReport with per-gate results and activation_allowed flag.
        activation_allowed is True only when all *required* gates are OPEN.
    """
    gates: List[GateResult] = [
        check_fb002_volume(fb002_events),
        check_preference_pairs(preference_pairs),
        check_imitation_artifact(imitation_registry_probe),
        check_downstream_consumers(downstream_consumer_probe),
    ]
    return PreflightReport.from_gates(gates)
