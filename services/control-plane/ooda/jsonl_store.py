"""Append-only JSONL store for OODA loop packets.

The store deliberately accepts packet mappings instead of importing the packet
schema module. MGMT-OODA-001 owns the schema/model; this module owns durable
append/replay semantics that later API and BFF routes can reuse.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OODA_PACKET_STORE_PATH = ROOT / ".orchestrator" / "ooda" / "ooda_loop_packets.jsonl"

SCHEMA_VERSION = "ooda_loop_packet_record.v1"
PACKET_SNAPSHOT = "packet_snapshot"
STAGE_TRANSITION = "stage_transition"
SUPPORTED_RECORD_TYPES = {PACKET_SNAPSHOT, STAGE_TRANSITION}


class OodaJsonlStoreError(ValueError):
    """Raised when the OODA JSONL store cannot replay or accept a record."""


@dataclass(frozen=True)
class OodaPacketQuery:
    loop_type: str | None = None
    status: str | None = None
    environment: str | None = None
    capital_pool_id: str | None = None
    strategy_id: str | None = None
    persona_id: str | None = None
    runtime_binding_id: str | None = None
    deployment_plan_id: str | None = None
    evolution_decision_id: str | None = None
    limit: int | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _require_text(value: Any, field_name: str) -> str:
    if value in (None, ""):
        raise OodaJsonlStoreError(f"{field_name} is required")
    normalized = str(value).strip()
    if not normalized:
        raise OodaJsonlStoreError(f"{field_name} is required")
    return normalized


def _nested_text(packet: Mapping[str, Any], section: str, field_name: str) -> str | None:
    section_value = packet.get(section)
    if not isinstance(section_value, Mapping):
        return None
    value = section_value.get(field_name)
    if value in (None, ""):
        return None
    return str(value)


class OodaJsonlAppendStore:
    """Append/replay store for OODA packets and stage-transition records."""

    def __init__(self, path: str | Path = DEFAULT_OODA_PACKET_STORE_PATH) -> None:
        self.path = Path(path)
        self._records: list[dict[str, Any]] = []
        self._packets: dict[str, dict[str, Any]] = {}
        self._packet_sequences: dict[str, int] = {}
        self._stage_transitions: dict[str, list[dict[str, Any]]] = {}
        self.reload()

    def reload(self) -> None:
        self._records.clear()
        self._packets.clear()
        self._packet_sequences.clear()
        self._stage_transitions.clear()

        if not self.path.exists():
            return

        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            payload = line.strip()
            if not payload:
                continue
            try:
                record = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise OodaJsonlStoreError(f"Invalid OODA JSONL at {self.path}:{line_number}: {exc.msg}") from exc
            try:
                self._apply_record(record)
            except OodaJsonlStoreError as exc:
                raise OodaJsonlStoreError(f"Invalid OODA record at {self.path}:{line_number}: {exc}") from exc

    def append_packet(self, packet: Mapping[str, Any]) -> dict[str, Any]:
        """Append a complete packet snapshot and return the durable envelope."""

        packet_payload = _json_copy(packet)
        packet_id = _require_text(packet_payload.get("packet_id"), "packet.packet_id")
        record = self._build_record(
            record_type=PACKET_SNAPSHOT,
            packet_id=packet_id,
            payload=packet_payload,
        )
        self._append_record(record)
        return _json_copy(record)

    def append_stage_transition(
        self,
        packet_id: str,
        transition: Mapping[str, Any],
        *,
        packet: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append a stage transition, optionally with the resulting packet snapshot."""

        normalized_packet_id = _require_text(packet_id, "packet_id")
        transition_payload = _json_copy(transition)
        transition_payload.setdefault("packet_id", normalized_packet_id)
        transition_packet_id = _require_text(transition_payload.get("packet_id"), "transition.packet_id")
        if transition_packet_id != normalized_packet_id:
            raise OodaJsonlStoreError("transition.packet_id must match packet_id")

        payload: dict[str, Any] = {"transition": transition_payload}
        if packet is not None:
            packet_payload = _json_copy(packet)
            if _require_text(packet_payload.get("packet_id"), "packet.packet_id") != normalized_packet_id:
                raise OodaJsonlStoreError("packet.packet_id must match packet_id")
            payload["packet"] = packet_payload

        record = self._build_record(
            record_type=STAGE_TRANSITION,
            packet_id=normalized_packet_id,
            payload=payload,
        )
        self._append_record(record)
        return _json_copy(record)

    def get_packet(self, packet_id: str) -> dict[str, Any] | None:
        packet = self._packets.get(packet_id)
        return _json_copy(packet) if packet is not None else None

    def list_packets(self, query: OodaPacketQuery | None = None) -> list[dict[str, Any]]:
        query = query or OodaPacketQuery()
        packets = sorted(
            self._packets.values(),
            key=lambda packet: self._packet_sequences.get(str(packet.get("packet_id")), -1),
            reverse=True,
        )

        matched: list[dict[str, Any]] = []
        for packet in packets:
            if not self._matches(packet, query):
                continue
            matched.append(_json_copy(packet))
            if query.limit is not None and len(matched) >= query.limit:
                break
        return matched

    def get_stage_transitions(self, packet_id: str) -> list[dict[str, Any]]:
        return [_json_copy(item) for item in self._stage_transitions.get(packet_id, [])]

    def iter_records(self) -> Iterator[dict[str, Any]]:
        for record in self._records:
            yield _json_copy(record)

    def _append_record(self, record: Mapping[str, Any]) -> None:
        record_payload = _json_copy(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        self._apply_record(record_payload)

    def _build_record(self, *, record_type: str, packet_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "record_type": record_type,
            "record_id": f"ooda-rec-{uuid.uuid4()}",
            "packet_id": packet_id,
            "recorded_at": utc_now(),
            "payload": _json_copy(payload),
        }

    def _apply_record(self, record: Mapping[str, Any]) -> None:
        if record.get("schema_version") != SCHEMA_VERSION:
            raise OodaJsonlStoreError(f"schema_version must be {SCHEMA_VERSION}")
        record_type = _require_text(record.get("record_type"), "record_type")
        if record_type not in SUPPORTED_RECORD_TYPES:
            raise OodaJsonlStoreError(f"Unsupported record_type: {record_type}")
        packet_id = _require_text(record.get("packet_id"), "packet_id")
        _require_text(record.get("record_id"), "record_id")
        _require_text(record.get("recorded_at"), "recorded_at")

        payload = record.get("payload")
        if not isinstance(payload, Mapping):
            raise OodaJsonlStoreError("payload must be an object")

        record_payload = _json_copy(record)
        sequence = len(self._records)
        if record_type == PACKET_SNAPSHOT:
            packet = self._packet_from_snapshot(packet_id, payload)
            self._packets[packet_id] = packet
            self._packet_sequences[packet_id] = sequence
        elif record_type == STAGE_TRANSITION:
            packet = self._packet_from_transition(packet_id, payload)
            if packet is not None:
                self._packets[packet_id] = packet
                self._packet_sequences[packet_id] = sequence
            transition = payload.get("transition")
            if not isinstance(transition, Mapping):
                raise OodaJsonlStoreError("transition payload must include transition object")
            self._stage_transitions.setdefault(packet_id, []).append(_json_copy(transition))

        self._records.append(record_payload)

    def _packet_from_snapshot(self, packet_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        packet = _json_copy(payload)
        if _require_text(packet.get("packet_id"), "payload.packet_id") != packet_id:
            raise OodaJsonlStoreError("payload.packet_id must match record packet_id")
        return packet

    def _packet_from_transition(self, packet_id: str, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        packet = payload.get("packet")
        if packet is None:
            return None
        if not isinstance(packet, Mapping):
            raise OodaJsonlStoreError("transition packet must be an object")
        return self._packet_from_snapshot(packet_id, packet)

    def _matches(self, packet: Mapping[str, Any], query: OodaPacketQuery) -> bool:
        if query.loop_type and packet.get("loop_type") != query.loop_type:
            return False
        if query.status and packet.get("status") != query.status:
            return False
        if query.environment and packet.get("environment") != query.environment:
            return False
        if query.capital_pool_id and packet.get("capital_pool_id") != query.capital_pool_id:
            return False
        if query.strategy_id and packet.get("strategy_id") != query.strategy_id:
            return False
        if query.persona_id and query.persona_id not in (packet.get("persona_ids") or []):
            return False
        if query.runtime_binding_id and _nested_text(packet, "act", "runtime_binding_id") != query.runtime_binding_id:
            return False
        if query.deployment_plan_id and _nested_text(packet, "decide", "deployment_plan_id") != query.deployment_plan_id:
            return False
        if query.evolution_decision_id and _nested_text(packet, "decide", "evolution_decision_id") != query.evolution_decision_id:
            return False
        return True
