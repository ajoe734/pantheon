"""
OODA packet stage transition validation.

This module owns the small, deterministic state machine for OodaLoopPacket
status advancement. It is intentionally independent from the packet schema and
store so schema, append-store, and BFF work can call it without import cycles.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, MutableMapping, Sequence


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class OodaTransitionError(ValueError):
    """Raised when an OODA status transition violates the stage graph."""


class OodaStatus(str, Enum):
    OPEN = "open"
    OBSERVING = "observing"
    ORIENTED = "oriented"
    DECIDED = "decided"
    ACTED = "acted"
    EVOLVING = "evolving"
    CLOSED = "closed"
    FAILED = "failed"


class OodaStageEvent(str, Enum):
    OBSERVE = "observe"
    ORIENT = "orient"
    DECIDE = "decide"
    ACT = "act"
    LEARN = "learn"
    CLOSE = "close"
    FAIL = "fail"


TERMINAL_STATUSES = {OodaStatus.CLOSED, OodaStatus.FAILED}

ALLOWED_STATUS_TRANSITIONS: dict[OodaStatus, set[OodaStatus]] = {
    OodaStatus.OPEN: {OodaStatus.OBSERVING, OodaStatus.FAILED},
    OodaStatus.OBSERVING: {OodaStatus.ORIENTED, OodaStatus.FAILED},
    OodaStatus.ORIENTED: {OodaStatus.DECIDED, OodaStatus.FAILED},
    OodaStatus.DECIDED: {OodaStatus.ACTED, OodaStatus.FAILED},
    OodaStatus.ACTED: {OodaStatus.EVOLVING, OodaStatus.CLOSED, OodaStatus.FAILED},
    OodaStatus.EVOLVING: {OodaStatus.CLOSED, OodaStatus.FAILED},
    OodaStatus.CLOSED: set(),
    OodaStatus.FAILED: set(),
}

EVENT_TARGET_STATUS: dict[OodaStageEvent, OodaStatus] = {
    OodaStageEvent.OBSERVE: OodaStatus.OBSERVING,
    OodaStageEvent.ORIENT: OodaStatus.ORIENTED,
    OodaStageEvent.DECIDE: OodaStatus.DECIDED,
    OodaStageEvent.ACT: OodaStatus.ACTED,
    OodaStageEvent.LEARN: OodaStatus.EVOLVING,
    OodaStageEvent.CLOSE: OodaStatus.CLOSED,
    OodaStageEvent.FAIL: OodaStatus.FAILED,
}

IDEMPOTENT_EVENT_STATUSES: dict[OodaStageEvent, set[OodaStatus]] = {
    OodaStageEvent.OBSERVE: {OodaStatus.OBSERVING},
    OodaStageEvent.ORIENT: {OodaStatus.ORIENTED},
    OodaStageEvent.DECIDE: {OodaStatus.DECIDED},
    OodaStageEvent.ACT: {OodaStatus.ACTED},
    OodaStageEvent.LEARN: {OodaStatus.EVOLVING},
}

NON_LIVE_ENVIRONMENTS = {"dev", "paper", "sandbox", "canary"}


@dataclass(frozen=True)
class OodaStageTransition:
    from_status: OodaStatus
    to_status: OodaStatus
    event: OodaStageEvent | None = None
    advanced: bool = True
    transitioned_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "from_status": self.from_status.value,
            "to_status": self.to_status.value,
            "advanced": self.advanced,
        }
        if self.event is not None:
            payload["event"] = self.event.value
        if self.transitioned_at is not None:
            payload["transitioned_at"] = self.transitioned_at
        return payload


def _coerce_status(value: OodaStatus | str) -> OodaStatus:
    try:
        return value if isinstance(value, OodaStatus) else OodaStatus(str(value))
    except ValueError as exc:
        allowed = ", ".join(status.value for status in OodaStatus)
        raise OodaTransitionError(f"Unknown OODA status '{value}'. Allowed: {allowed}") from exc


def _coerce_event(value: OodaStageEvent | str) -> OodaStageEvent:
    try:
        return value if isinstance(value, OodaStageEvent) else OodaStageEvent(str(value))
    except ValueError as exc:
        allowed = ", ".join(event.value for event in OodaStageEvent)
        raise OodaTransitionError(f"Unknown OODA stage event '{value}'. Allowed: {allowed}") from exc


def validate_status_transition(
    current_status: OodaStatus | str,
    next_status: OodaStatus | str,
    *,
    allow_same_status: bool = False,
) -> OodaStageTransition:
    current = _coerce_status(current_status)
    target = _coerce_status(next_status)

    if current == target:
        if allow_same_status and current not in TERMINAL_STATUSES:
            return OodaStageTransition(current, target, advanced=False)
        raise OodaTransitionError(f"OODA status transition cannot be a no-op: {current.value}")

    if current in TERMINAL_STATUSES:
        raise OodaTransitionError(f"OODA packet is terminal at {current.value}; no further transitions are allowed")

    allowed = ALLOWED_STATUS_TRANSITIONS[current]
    if target not in allowed:
        allowed_text = ", ".join(sorted(status.value for status in allowed)) or "none"
        raise OodaTransitionError(
            f"Forbidden OODA status transition: {current.value} -> {target.value}; "
            f"allowed next status: {allowed_text}"
        )

    return OodaStageTransition(current, target)


def transition_for_event(
    current_status: OodaStatus | str,
    event: OodaStageEvent | str,
    *,
    transitioned_at: str | None = None,
) -> OodaStageTransition:
    current = _coerce_status(current_status)
    stage_event = _coerce_event(event)
    target = EVENT_TARGET_STATUS[stage_event]

    if current in TERMINAL_STATUSES:
        raise OodaTransitionError(f"OODA packet is terminal at {current.value}; event {stage_event.value} is rejected")

    if current == target:
        if current in IDEMPOTENT_EVENT_STATUSES.get(stage_event, set()):
            return OodaStageTransition(
                from_status=current,
                to_status=target,
                event=stage_event,
                advanced=False,
                transitioned_at=transitioned_at,
            )
        raise OodaTransitionError(f"OODA event {stage_event.value} cannot be a no-op at {current.value}")

    transition = validate_status_transition(current, target)
    return OodaStageTransition(
        from_status=transition.from_status,
        to_status=transition.to_status,
        event=stage_event,
        advanced=transition.advanced,
        transitioned_at=transitioned_at,
    )


def apply_stage_event(
    packet: MutableMapping[str, Any],
    event: OodaStageEvent | str,
    *,
    transitioned_at: str | None = None,
) -> OodaStageTransition:
    if not isinstance(packet, MutableMapping):
        raise OodaTransitionError("OODA packet must be a mutable mapping")

    timestamp = transitioned_at or utc_now()
    transition = transition_for_event(packet.get("status", OodaStatus.OPEN.value), event, transitioned_at=timestamp)
    packet["status"] = transition.to_status.value
    packet["updated_at"] = timestamp
    if transition.to_status in TERMINAL_STATUSES:
        packet["closed_at"] = packet.get("closed_at") or timestamp
    return transition


def validate_packet_stage_invariants(packet: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        status = _coerce_status(packet.get("status", OodaStatus.OPEN.value))
    except OodaTransitionError as exc:
        errors.append(str(exc))
        status = None

    if status == OodaStatus.CLOSED and not packet.get("closed_at"):
        errors.append("closed OODA packets must include closed_at")

    if status == OodaStatus.CLOSED:
        for bundle_key in ("observe", "orient", "decide", "act"):
            bundle = packet.get(bundle_key)
            if not isinstance(bundle, Mapping):
                errors.append(f"closed OODA packets must include {bundle_key} bundle")
                continue
            has_evidence = any(
                v not in (None, False, [], "", {})
                for v in bundle.values()
            )
            if not has_evidence:
                errors.append(
                    f"closed OODA packets must include at least one evidence ref in the {bundle_key} bundle"
                )

    environment = str(packet.get("environment") or "").strip().lower()
    act_payload = packet.get("act") if isinstance(packet.get("act"), Mapping) else {}
    live_side_effects = bool(act_payload.get("live_capital_side_effects")) if isinstance(act_payload, Mapping) else False
    if environment in NON_LIVE_ENVIRONMENTS and live_side_effects:
        errors.append(
            "act.live_capital_side_effects must be false in dev, paper, sandbox, and canary environments"
        )

    return errors


def assert_packet_stage_invariants(packet: Mapping[str, Any]) -> None:
    errors = validate_packet_stage_invariants(packet)
    if errors:
        raise OodaTransitionError("; ".join(errors))


def assert_complete_replay_path(transitions: Sequence[Mapping[str, Any] | OodaStageTransition]) -> None:
    current = OodaStatus.OPEN
    for entry in transitions:
        if isinstance(entry, OodaStageTransition):
            target = entry.to_status
        else:
            target = _coerce_status(entry.get("to_status", ""))
        transition = validate_status_transition(current, target)
        current = transition.to_status
    if current != OodaStatus.CLOSED:
        raise OodaTransitionError(f"OODA replay path must end at closed, got {current.value}")
