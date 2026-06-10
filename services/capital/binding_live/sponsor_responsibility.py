"""SponsorPersonaResponsibility schema model.

Implements the 2026-05-19 blueprint supplement sponsor-responsibility evidence
shape for capital binding live readiness. This module is schema and consistency
validation only; it never enables capital live writes or performs runtime/broker
side effects.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "SponsorPersonaResponsibility.v1"
RESPONSIBILITY_PACKET_VERSION = "2026-05-19.CBL-002"

RESPONSIBILITY_STATUSES = {"draft", "active", "revoked", "expired", "superseded"}
ACTIVE_RESPONSIBILITY_STATUS = "active"
LIVE_OWNER_ROLE = "live_owner"

REQUIRED_TOP_LEVEL_FIELDS = (
    "responsibility_id",
    "sponsor_persona_id",
    "binding_id",
    "live_owner",
    "escalation_chain",
)
REQUIRED_LIVE_OWNER_FIELDS = ("owner_id", "role", "binding_id")
REQUIRED_ESCALATION_FIELDS = ("level", "owner_id", "role", "trigger", "action")


class SponsorPersonaResponsibilityError(ValueError):
    """Raised when a SponsorPersonaResponsibility packet is invalid."""


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SponsorPersonaResponsibilityError(f"{field_name} must be an object")
    return copy.deepcopy(dict(value))


def _sequence(value: Any, field_name: str) -> list[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise SponsorPersonaResponsibilityError(f"{field_name} must be a list")
    return list(value)


def _required_text(value: Any, field_name: str) -> str:
    if value is None:
        raise SponsorPersonaResponsibilityError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise SponsorPersonaResponsibilityError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_status(value: Any, field_name: str) -> str:
    status = _required_text(value, field_name).lower()
    if status not in RESPONSIBILITY_STATUSES:
        raise SponsorPersonaResponsibilityError(
            f"{field_name} must be one of: {', '.join(sorted(RESPONSIBILITY_STATUSES))}"
        )
    return status


def _required_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SponsorPersonaResponsibilityError(f"{field_name} must be an integer")
    if value <= 0:
        raise SponsorPersonaResponsibilityError(f"{field_name} must be greater than 0")
    return value


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    return tuple(_required_text(item, f"{field_name}[]") for item in _sequence(value, field_name))


def _optional_string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    return _string_tuple(value, field_name)


def _compact(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if value is not None}


@dataclass(frozen=True)
class LiveOwnerResponsibility:
    owner_id: str
    role: str
    binding_id: str
    mandate_ref: str | None = None
    contact_ref: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOwnerResponsibility":
        payload = _mapping(data, "live_owner")
        owner = cls(
            owner_id=_required_text(payload.get("owner_id"), "live_owner.owner_id"),
            role=_required_text(payload.get("role"), "live_owner.role"),
            binding_id=_required_text(payload.get("binding_id"), "live_owner.binding_id"),
            mandate_ref=_optional_text(payload.get("mandate_ref")),
            contact_ref=_optional_text(payload.get("contact_ref")),
        )
        owner.validate_consistency()
        return owner

    def validate_consistency(self) -> None:
        if self.role != LIVE_OWNER_ROLE:
            raise SponsorPersonaResponsibilityError(
                f"live_owner.role must be {LIVE_OWNER_ROLE}"
            )

    def to_dict(self) -> dict[str, str]:
        return _compact(
            {
                "owner_id": self.owner_id,
                "role": self.role,
                "binding_id": self.binding_id,
                "mandate_ref": self.mandate_ref,
                "contact_ref": self.contact_ref,
            }
        )


@dataclass(frozen=True)
class EscalationStep:
    level: int
    owner_id: str
    role: str
    trigger: str
    action: str
    evidence_ref: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], index: int) -> "EscalationStep":
        field_name = f"escalation_chain[{index}]"
        payload = _mapping(data, field_name)
        return cls(
            level=_required_int(payload.get("level"), f"{field_name}.level"),
            owner_id=_required_text(payload.get("owner_id"), f"{field_name}.owner_id"),
            role=_required_text(payload.get("role"), f"{field_name}.role"),
            trigger=_required_text(payload.get("trigger"), f"{field_name}.trigger"),
            action=_required_text(payload.get("action"), f"{field_name}.action"),
            evidence_ref=_optional_text(payload.get("evidence_ref")),
        )

    def to_dict(self) -> dict[str, str | int]:
        return _compact(
            {
                "level": self.level,
                "owner_id": self.owner_id,
                "role": self.role,
                "trigger": self.trigger,
                "action": self.action,
                "evidence_ref": self.evidence_ref,
            }
        )


@dataclass(frozen=True)
class SponsorPersonaResponsibility:
    responsibility_id: str
    sponsor_persona_id: str
    binding_id: str
    live_owner: LiveOwnerResponsibility
    escalation_chain: tuple[EscalationStep, ...]
    capital_pool_id: str | None = None
    policy_refs: tuple[str, ...] = ()
    status: str = ACTIVE_RESPONSIBILITY_STATUS
    schema_version: str | None = SCHEMA_VERSION
    packet_version: str | None = RESPONSIBILITY_PACKET_VERSION

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SponsorPersonaResponsibility":
        payload = _mapping(data, "SponsorPersonaResponsibility")
        packet = cls(
            responsibility_id=_required_text(payload.get("responsibility_id"), "responsibility_id"),
            sponsor_persona_id=_required_text(
                payload.get("sponsor_persona_id"),
                "sponsor_persona_id",
            ),
            binding_id=_required_text(payload.get("binding_id"), "binding_id"),
            capital_pool_id=_optional_text(payload.get("capital_pool_id")),
            live_owner=LiveOwnerResponsibility.from_dict(payload.get("live_owner")),
            escalation_chain=tuple(
                EscalationStep.from_dict(item, index)
                for index, item in enumerate(_sequence(payload.get("escalation_chain"), "escalation_chain"))
            ),
            policy_refs=_optional_string_tuple(payload.get("policy_refs"), "policy_refs"),
            status=_required_status(payload.get("status", ACTIVE_RESPONSIBILITY_STATUS), "status"),
            schema_version=_optional_text(payload.get("schema_version")) or SCHEMA_VERSION,
            packet_version=_optional_text(payload.get("packet_version")) or RESPONSIBILITY_PACKET_VERSION,
        )
        packet.validate_consistency()
        return packet

    @classmethod
    def from_json_file(cls, path: str | Path) -> "SponsorPersonaResponsibility":
        with Path(path).open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, Mapping):
            raise SponsorPersonaResponsibilityError(
                "sponsor responsibility document must be a JSON object"
            )
        return cls.from_dict(data)

    def validate_consistency(self) -> None:
        if self.live_owner.binding_id != self.binding_id:
            raise SponsorPersonaResponsibilityError(
                "live_owner.binding_id must match binding_id"
            )

        if self.status == ACTIVE_RESPONSIBILITY_STATUS and not self.escalation_chain:
            raise SponsorPersonaResponsibilityError(
                "active sponsor responsibility requires a non-empty escalation_chain"
            )

        levels = [step.level for step in self.escalation_chain]
        expected_levels = list(range(1, len(levels) + 1))
        if levels != expected_levels:
            raise SponsorPersonaResponsibilityError(
                "escalation_chain.level values must be contiguous and start at 1"
            )

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            {
                "responsibility_id": self.responsibility_id,
                "sponsor_persona_id": self.sponsor_persona_id,
                "binding_id": self.binding_id,
                "capital_pool_id": self.capital_pool_id,
                "live_owner": self.live_owner.to_dict(),
                "escalation_chain": [step.to_dict() for step in self.escalation_chain],
                "policy_refs": list(self.policy_refs),
                "status": self.status,
                "schema_version": self.schema_version,
                "packet_version": self.packet_version,
            }
        )


def validate_sponsor_responsibility(
    packet: Mapping[str, Any] | SponsorPersonaResponsibility,
) -> SponsorPersonaResponsibility:
    """Validate sponsor responsibility evidence and return the normalized model."""

    if isinstance(packet, SponsorPersonaResponsibility):
        packet.live_owner.validate_consistency()
        packet.validate_consistency()
        return packet
    return SponsorPersonaResponsibility.from_dict(packet)
