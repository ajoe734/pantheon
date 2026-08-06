"""Bounded incremental lifecycle materializer for Trade Journey and Loop Runs.

Maintains aggregate-keyed projections over incoming telemetry events without
retaining full historical event logs or performing full global rebuilds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from services.trade_journey.materializer import JourneyMaterializer, JourneyProjection, STAGES


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass
class BoundedAggregateState:
    """Bounded, stateful representation of a single Trade Journey lifecycle."""
    journey_id: str
    tenant_id: str
    environment: str
    events_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    event_fingerprints: dict[str, str] = field(default_factory=dict)
    last_ingested_seq: int = 0
    last_sequence_no: int = 0
    projection: JourneyProjection | None = None
    loop_record: dict[str, Any] | None = None


class IncrementalLifecycleMaterializer:
    """Bounded materializer maintaining per-journey aggregate states.

    Only active or affected journeys are materialized during incremental processing.
    """

    def __init__(self, initial_state: Mapping[str, Any] | None = None) -> None:
        self.aggregates: dict[str, BoundedAggregateState] = {}
        self.reverse_index: dict[tuple[str, str, str, str], set[str]] = {}
        if initial_state:
            self._restore_from_state(initial_state)

    def _restore_from_state(self, state: Mapping[str, Any]) -> None:
        canonical_events = state.get("canonical_events") or {}
        # Group stored canonical events by journey_id
        for event_id, entry in canonical_events.items():
            identity = entry.get("identity") or {}
            journey_id = identity.get("journey_id")
            if not journey_id:
                continue
            agg = self.aggregates.setdefault(
                journey_id,
                BoundedAggregateState(
                    journey_id=journey_id,
                    tenant_id=identity.get("tenant_id", ""),
                    environment=identity.get("environment", ""),
                ),
            )
            agg.events_by_id[event_id] = entry
            agg.event_fingerprints[event_id] = entry.get("fingerprint", "")
            agg.last_ingested_seq = max(agg.last_ingested_seq, int(entry.get("ingested_seq") or 0))
            agg.last_sequence_no = max(agg.last_sequence_no, int(entry.get("sequence_no") or 0))

    def rematerialize_all(
        self,
        *,
        controller: Mapping[str, Any],
        journey_events_fn: Any,
        loop_record_builder_fn: Any,
    ) -> None:
        for agg in self.aggregates.values():
            self._rematerialize_aggregate(
                agg,
                controller=controller,
                journey_events_fn=journey_events_fn,
                loop_record_builder_fn=loop_record_builder_fn,
            )

    def apply_batch(
        self,
        canonical_entries: Sequence[Mapping[str, Any]],
        *,
        controller: Mapping[str, Any],
        stage_specs_fn: Any,
        journey_events_fn: Any,
        loop_record_builder_fn: Any,
    ) -> tuple[set[str], int, int]:
        """Apply a batch of canonical entries, updating only affected aggregates incrementally.

        Returns:
            (affected_journey_ids, accepted_count, duplicate_count)
        """
        affected_journeys: set[str] = set()
        accepted = 0
        duplicates = 0

        # First materialize existing state aggregates if not already materialized
        for agg in self.aggregates.values():
            if agg.projection is None and agg.events_by_id:
                self._rematerialize_aggregate(
                    agg,
                    controller=controller,
                    journey_events_fn=journey_events_fn,
                    loop_record_builder_fn=loop_record_builder_fn,
                )

        # Group batch entries by journey_id
        batch_by_journey: dict[str, list[Mapping[str, Any]]] = {}
        for entry in canonical_entries:
            identity = entry.get("identity") or {}
            journey_id = identity.get("journey_id")
            if not journey_id:
                continue
            batch_by_journey.setdefault(journey_id, []).append(entry)

        for journey_id, entries in batch_by_journey.items():
            first_identity = entries[0]["identity"]
            agg = self.aggregates.setdefault(
                journey_id,
                BoundedAggregateState(
                    journey_id=journey_id,
                    tenant_id=first_identity["tenant_id"],
                    environment=first_identity["environment"],
                ),
            )

            changed = False
            for entry in entries:
                event_id = entry["event"]["event_id"]
                fingerprint = entry["fingerprint"]
                if event_id in agg.events_by_id:
                    if agg.event_fingerprints.get(event_id) != fingerprint:
                        from services.trade_journey.lifecycle_projector import ConflictingLifecycleEvent
                        raise ConflictingLifecycleEvent(
                            f"conflicting canonical event_id: {event_id}"
                        )
                    duplicates += 1
                    continue

                agg.events_by_id[event_id] = dict(entry)
                agg.event_fingerprints[event_id] = fingerprint
                agg.last_ingested_seq = max(agg.last_ingested_seq, int(entry.get("ingested_seq") or 0))
                agg.last_sequence_no = max(agg.last_sequence_no, int(entry.get("sequence_no") or 0))
                accepted += 1
                changed = True

            if changed:
                affected_journeys.add(journey_id)
                self._rematerialize_aggregate(
                    agg,
                    controller=controller,
                    journey_events_fn=journey_events_fn,
                    loop_record_builder_fn=loop_record_builder_fn,
                )

        return affected_journeys, accepted, duplicates

    def _rematerialize_aggregate(
        self,
        agg: BoundedAggregateState,
        *,
        controller: Mapping[str, Any],
        journey_events_fn: Any,
        loop_record_builder_fn: Any,
    ) -> None:
        entries = list(agg.events_by_id.values())
        entries.sort(key=lambda e: (
            str((e.get("identity") or {}).get("journey_id") or ""),
            int(e.get("sequence_no") or 0),
            str((e.get("event") or {}).get("created_at") or ""),
            str((e.get("event") or {}).get("event_id") or ""),
        ))

        journey_events: list[dict[str, Any]] = []
        for entry in entries:
            journey_events.extend(journey_events_fn(entry))

        journey_events.sort(key=JourneyMaterializer._sort_key)
        mat = JourneyMaterializer()
        mat.rebuild(journey_events)
        agg.projection = mat.get(agg.journey_id, tenant_id=agg.tenant_id, environment=agg.environment)

        if entries and agg.projection:
            records = loop_record_builder_fn([entries], mat, controller)
            agg.loop_record = records[0] if records else None

    def render_full_payloads(
        self,
        *,
        schema_version_journey: str,
        schema_version_loop: str,
        generation: int,
        controller: Mapping[str, Any],
        journey_events_fn: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Render complete journey and loop payloads from all aggregate states."""
        all_journey_events: list[dict[str, Any]] = []
        all_loop_records: dict[str, dict[str, Any]] = {}

        # Sort journeys for deterministic order
        for journey_id in sorted(self.aggregates.keys()):
            agg = self.aggregates[journey_id]
            entries = list(agg.events_by_id.values())
            entries.sort(key=lambda e: (
                str((e.get("identity") or {}).get("journey_id") or ""),
                int(e.get("sequence_no") or 0),
                str((e.get("event") or {}).get("created_at") or ""),
                str((e.get("event") or {}).get("event_id") or ""),
            ))
            for entry in entries:
                all_journey_events.extend(journey_events_fn(entry))
            if agg.loop_record:
                all_loop_records[agg.loop_record["id"]] = agg.loop_record

        all_journey_events.sort(key=JourneyMaterializer._sort_key)

        journey_payload = {
            "schema_version": schema_version_journey,
            "projector_owned": True,
            "generation": generation,
            "projection_mode": controller.get("mode"),
            "accepted_live": bool(controller.get("accepted_live")),
            "controller": dict(controller),
            "events": all_journey_events,
        }
        loop_payload = {
            "schema_version": schema_version_loop,
            "projector_owned": True,
            "generation": generation,
            "projection_mode": controller.get("mode"),
            "accepted_live": bool(controller.get("accepted_live")),
            "controller": dict(controller),
            "records": all_loop_records,
        }
        return journey_payload, loop_payload
