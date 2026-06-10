"""Multi-persona sponsor lineage for EP5 proof packets.

This module is a pure bridge from the MPO-003 multi-persona evidence packet to
the EP5 PromotionReadinessPacket extension surface. It does not read files or
run synthesis; callers pass the already-produced MPO packet plus an optional
portable evidence ref.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "EP5PersonaLineage.v1"


class PersonaLineageError(ValueError):
    """Raised when a multi-persona packet cannot produce EP5 lineage."""


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PersonaLineageError(f"{field_name} must be an object")
    return copy.deepcopy(dict(value))


def _optional_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    return _mapping(value, field_name)


def _required_text(value: Any, field_name: str) -> str:
    if value is None:
        raise PersonaLineageError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise PersonaLineageError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _sequence(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PersonaLineageError(f"{field_name} must be a list")
    return list(value)


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    items = tuple(_required_text(item, f"{field_name}[]") for item in _sequence(value, field_name))
    return items


def _compact(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if value is not None}


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _base_ref(packet_id: str, source_packet_ref: str | None) -> str:
    return source_packet_ref or f"packet:{packet_id}"


def _fragment_ref(base: str, pointer: str) -> str:
    return f"{base}#{pointer}"


@dataclass(frozen=True)
class EP5PersonaLineage:
    """Sponsor lineage embedded into EP5 canary readiness packets."""

    source_packet_id: str
    sponsor_persona_id: str
    conflict_resolution_log_id: str
    synthesized_memo_refs: tuple[str, ...]
    conflict_log_ref: str
    source_task_id: str | None = None
    source_packet_ref: str | None = None
    allocation_artifact_id: str | None = None
    capital_pool_id: str | None = None
    synthesis_method: str | None = None
    proposal_ids: tuple[str, ...] = ()
    source_conflict_resolution_log_id: str | None = None
    classified_conflict_refs: tuple[str, ...] = ()
    classified_conflict_types: tuple[str, ...] = ()
    has_open_conflicts: bool = False
    open_conflict_ids: tuple[str, ...] = ()
    strategy_spec_refs: tuple[str, ...] = ()
    acceptance_gates: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EP5PersonaLineage":
        payload = _mapping(data, "persona_lineage")
        return cls(
            source_packet_id=_required_text(payload.get("source_packet_id"), "source_packet_id"),
            source_task_id=_optional_text(payload.get("source_task_id")),
            source_packet_ref=_optional_text(payload.get("source_packet_ref")),
            sponsor_persona_id=_required_text(payload.get("sponsor_persona_id"), "sponsor_persona_id"),
            allocation_artifact_id=_optional_text(payload.get("allocation_artifact_id")),
            capital_pool_id=_optional_text(payload.get("capital_pool_id")),
            synthesis_method=_optional_text(payload.get("synthesis_method")),
            proposal_ids=_string_tuple(payload.get("proposal_ids"), "proposal_ids"),
            conflict_resolution_log_id=_required_text(
                payload.get("conflict_resolution_log_id"),
                "conflict_resolution_log_id",
            ),
            source_conflict_resolution_log_id=_optional_text(payload.get("source_conflict_resolution_log_id")),
            conflict_log_ref=_required_text(payload.get("conflict_log_ref"), "conflict_log_ref"),
            classified_conflict_refs=_string_tuple(
                payload.get("classified_conflict_refs"),
                "classified_conflict_refs",
            ),
            classified_conflict_types=_string_tuple(
                payload.get("classified_conflict_types"),
                "classified_conflict_types",
            ),
            synthesized_memo_refs=_string_tuple(payload.get("synthesized_memo_refs"), "synthesized_memo_refs"),
            has_open_conflicts=_bool(payload.get("has_open_conflicts")),
            open_conflict_ids=_string_tuple(payload.get("open_conflict_ids"), "open_conflict_ids"),
            strategy_spec_refs=_string_tuple(payload.get("strategy_spec_refs"), "strategy_spec_refs"),
            acceptance_gates=_optional_mapping(payload.get("acceptance_gates"), "acceptance_gates"),
            schema_version=_optional_text(payload.get("schema_version")) or SCHEMA_VERSION,
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            {
                "schema_version": self.schema_version,
                "source_packet_id": self.source_packet_id,
                "source_task_id": self.source_task_id,
                "source_packet_ref": self.source_packet_ref,
                "sponsor_persona_id": self.sponsor_persona_id,
                "allocation_artifact_id": self.allocation_artifact_id,
                "capital_pool_id": self.capital_pool_id,
                "synthesis_method": self.synthesis_method,
                "proposal_ids": list(self.proposal_ids),
                "conflict_resolution_log_id": self.conflict_resolution_log_id,
                "source_conflict_resolution_log_id": self.source_conflict_resolution_log_id,
                "conflict_log_ref": self.conflict_log_ref,
                "classified_conflict_refs": list(self.classified_conflict_refs),
                "classified_conflict_types": list(self.classified_conflict_types),
                "synthesized_memo_refs": list(self.synthesized_memo_refs),
                "has_open_conflicts": self.has_open_conflicts,
                "open_conflict_ids": list(self.open_conflict_ids),
                "strategy_spec_refs": list(self.strategy_spec_refs),
                "acceptance_gates": copy.deepcopy(self.acceptance_gates) if self.acceptance_gates else None,
            }
        )


def build_ep5_persona_lineage(
    multi_persona_packet: Mapping[str, Any],
    *,
    source_packet_ref: str | None = None,
) -> EP5PersonaLineage:
    """Extract EP5 sponsor lineage from a MPO-003 full evidence packet."""

    packet = _mapping(multi_persona_packet, "multi_persona_packet")
    if _optional_text(packet.get("schema_version")) == SCHEMA_VERSION:
        lineage = EP5PersonaLineage.from_dict(packet)
        if source_packet_ref and lineage.source_packet_ref != source_packet_ref:
            return EP5PersonaLineage.from_dict({**lineage.to_dict(), "source_packet_ref": source_packet_ref})
        return lineage

    packet_id = _required_text(packet.get("packet_id"), "packet_id")
    source_task_id = _optional_text(packet.get("task_id"))
    base_ref = _base_ref(packet_id, source_packet_ref)

    synthesis = _mapping(packet.get("synthesis"), "synthesis")
    sponsor_resolution = _mapping(packet.get("sponsor_resolution"), "sponsor_resolution")
    conflict_log = _mapping(
        sponsor_resolution.get("conflict_resolution_log"),
        "sponsor_resolution.conflict_resolution_log",
    )

    sponsor_persona_id = _required_text(
        sponsor_resolution.get("sponsor_persona_id") or synthesis.get("sponsor_persona_id"),
        "sponsor_persona_id",
    )
    synthesis_sponsor = _optional_text(synthesis.get("sponsor_persona_id"))
    if synthesis_sponsor and synthesis_sponsor != sponsor_persona_id:
        raise PersonaLineageError("synthesis.sponsor_persona_id must match sponsor_resolution.sponsor_persona_id")

    conflict_log_sponsor = _optional_text(conflict_log.get("sponsor_persona_id"))
    if conflict_log_sponsor and conflict_log_sponsor != sponsor_persona_id:
        raise PersonaLineageError(
            "sponsor_resolution.conflict_resolution_log.sponsor_persona_id must match sponsor_persona_id"
        )

    conflict_resolution_log_id = _required_text(
        conflict_log.get("log_id") or synthesis.get("mgmt_syn_conflict_log_id"),
        "conflict_resolution_log_id",
    )
    synthesis_conflict_log_id = _optional_text(synthesis.get("mgmt_syn_conflict_log_id"))
    if synthesis_conflict_log_id and synthesis_conflict_log_id != conflict_resolution_log_id:
        raise PersonaLineageError("synthesis.mgmt_syn_conflict_log_id must match conflict_resolution_log.log_id")

    proposal_ids = (
        _string_tuple(sponsor_resolution.get("proposal_ids"), "sponsor_resolution.proposal_ids")
        or _string_tuple(conflict_log.get("proposal_ids"), "conflict_resolution_log.proposal_ids")
        or _string_tuple(synthesis.get("provenance_refs"), "synthesis.provenance_refs")
    )
    if not proposal_ids:
        raise PersonaLineageError("persona lineage requires at least one proposal id")

    governance_memo_refs = _explicit_memo_refs(packet)
    if not governance_memo_refs:
        memo = _optional_text(packet.get("governance_memo"))
        if not memo:
            raise PersonaLineageError("governance_memo or governance_memo_refs is required")
        governance_memo_refs = (_fragment_ref(base_ref, "/governance_memo"),)

    classified_conflicts = _sequence(
        conflict_log.get("classified_conflicts"),
        "conflict_resolution_log.classified_conflicts",
    )
    classified_conflict_refs = tuple(
        _fragment_ref(base_ref, f"/sponsor_resolution/conflict_resolution_log/classified_conflicts/{index}")
        for index, _ in enumerate(classified_conflicts)
    )
    classified_conflict_types = tuple(
        sorted(
            {
                _required_text(
                    _mapping(conflict, "classified_conflicts[]").get("conflict_type"),
                    "classified_conflicts[].conflict_type",
                )
                for conflict in classified_conflicts
            }
        )
    )

    open_conflicts = _sequence(conflict_log.get("open_conflicts", []), "conflict_resolution_log.open_conflicts")
    open_conflict_ids = tuple(
        _required_text(_mapping(conflict, "open_conflicts[]").get("conflict_id"), "open_conflicts[].conflict_id")
        for conflict in open_conflicts
    )
    has_open_conflicts = _bool(sponsor_resolution.get("has_open_conflicts")) or bool(open_conflict_ids)

    return EP5PersonaLineage(
        source_packet_id=packet_id,
        source_task_id=source_task_id,
        source_packet_ref=source_packet_ref,
        sponsor_persona_id=sponsor_persona_id,
        allocation_artifact_id=_optional_text(synthesis.get("artifact_id") or conflict_log.get("artifact_id")),
        capital_pool_id=_optional_text(synthesis.get("capital_pool_id") or conflict_log.get("capital_pool_id")),
        synthesis_method=_optional_text(synthesis.get("synthesis_method") or conflict_log.get("synthesis_method")),
        proposal_ids=proposal_ids,
        conflict_resolution_log_id=conflict_resolution_log_id,
        source_conflict_resolution_log_id=_optional_text(conflict_log.get("source_conflict_resolution_log_id")),
        conflict_log_ref=_fragment_ref(base_ref, "/sponsor_resolution/conflict_resolution_log"),
        classified_conflict_refs=classified_conflict_refs,
        classified_conflict_types=classified_conflict_types,
        synthesized_memo_refs=governance_memo_refs,
        has_open_conflicts=has_open_conflicts,
        open_conflict_ids=open_conflict_ids,
        strategy_spec_refs=_string_tuple(packet.get("strategy_spec_pool"), "strategy_spec_pool"),
        acceptance_gates=_optional_mapping(packet.get("acceptance_gates"), "acceptance_gates"),
    )


def normalize_ep5_persona_lineage(
    value: EP5PersonaLineage | Mapping[str, Any],
    *,
    source_packet_ref: str | None = None,
) -> EP5PersonaLineage:
    """Normalize an EP5 lineage dict or MPO packet into ``EP5PersonaLineage``."""

    if isinstance(value, EP5PersonaLineage):
        if source_packet_ref and value.source_packet_ref != source_packet_ref:
            return EP5PersonaLineage.from_dict({**value.to_dict(), "source_packet_ref": source_packet_ref})
        return value
    if not isinstance(value, Mapping):
        raise PersonaLineageError("persona lineage source must be an object")
    if _optional_text(value.get("schema_version")) == SCHEMA_VERSION:
        lineage = EP5PersonaLineage.from_dict(value)
        if source_packet_ref and lineage.source_packet_ref != source_packet_ref:
            return EP5PersonaLineage.from_dict({**lineage.to_dict(), "source_packet_ref": source_packet_ref})
        return lineage
    return build_ep5_persona_lineage(value, source_packet_ref=source_packet_ref)


def _explicit_memo_refs(packet: Mapping[str, Any]) -> tuple[str, ...]:
    for key in ("governance_memo_refs", "synthesized_memo_refs", "memo_refs"):
        refs = _string_tuple(packet.get(key), key)
        if refs:
            return refs
    return ()


__all__ = [
    "EP5PersonaLineage",
    "PersonaLineageError",
    "build_ep5_persona_lineage",
    "normalize_ep5_persona_lineage",
]
