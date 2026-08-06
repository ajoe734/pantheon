"""Bounded incremental lifecycle materializer for Trade Journey and Loop Runs.

Maintains aggregate-keyed projections over incoming telemetry events without
retaining full historical event logs or performing full global rebuilds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from services.trade_journey.materializer import JourneyMaterializer, JourneyProjection


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

    def to_dict(self) -> dict[str, Any]:
        """Serialize aggregate state for persistent controller_state.json storage."""
        return {
            "journey_id": self.journey_id,
            "tenant_id": self.tenant_id,
            "environment": self.environment,
            "events_by_id": self.events_by_id,
            "event_fingerprints": self.event_fingerprints,
            "last_ingested_seq": self.last_ingested_seq,
            "last_sequence_no": self.last_sequence_no,
            "projection": self.projection.to_dict() if self.projection and hasattr(self.projection, "to_dict") else self.projection,
            "loop_record": self.loop_record,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BoundedAggregateState:
        """Restore aggregate state from persistent storage dict."""
        proj_data = data.get("projection")
        proj = None
        if isinstance(proj_data, dict):
            proj = JourneyProjection.from_dict(proj_data)
        elif isinstance(proj_data, JourneyProjection):
            proj = proj_data

        return cls(
            journey_id=data["journey_id"],
            tenant_id=data.get("tenant_id", ""),
            environment=data.get("environment", ""),
            events_by_id=dict(data.get("events_by_id") or {}),
            event_fingerprints=dict(data.get("event_fingerprints") or {}),
            last_ingested_seq=int(data.get("last_ingested_seq") or 0),
            last_sequence_no=int(data.get("last_sequence_no") or 0),
            projection=proj,
            loop_record=dict(data["loop_record"]) if data.get("loop_record") else None,
        )


class IncrementalLifecycleMaterializer:
    """Bounded materializer maintaining per-journey aggregate states.

    Only active or affected journeys are materialized during incremental processing.
    """

    def __init__(self, initial_state: Mapping[str, Any] | None = None) -> None:
        self.aggregates: dict[str, BoundedAggregateState] = {}
        if initial_state:
            self._restore_from_state(initial_state)

    def _restore_from_state(self, state: Mapping[str, Any]) -> None:
        # Load from persisted bounded aggregates if present
        stored_aggregates = state.get("aggregates")
        if isinstance(stored_aggregates, dict):
            for jid, agg_dict in stored_aggregates.items():
                if isinstance(agg_dict, dict):
                    self.aggregates[jid] = BoundedAggregateState.from_dict(agg_dict)
                elif isinstance(agg_dict, BoundedAggregateState):
                    self.aggregates[jid] = agg_dict
            return

        # Fallback for legacy state format containing canonical_events
        canonical_events = state.get("canonical_events") or {}
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

    def apply_batch(
        self,
        canonical_entries: Sequence[Mapping[str, Any]],
        *,
        controller: Mapping[str, Any],
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

        if entries:
            records = loop_record_builder_fn([entries], mat, controller)
            if records:
                agg.loop_record = records[0]

    def render_full_payloads(
        self,
        *,
        schema_version_journey: str,
        schema_version_loop: str,
        generation: int,
        controller: Mapping[str, Any],
        journey_events_fn: Any,
        loop_record_builder_fn: Any | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Render complete journey and loop payloads from all aggregate states."""
        all_journey_events: list[dict[str, Any]] = []
        all_loop_records: dict[str, dict[str, Any]] = {}

        # Sort journeys for deterministic order
        for journey_id in sorted(self.aggregates.keys()):
            agg = self.aggregates[journey_id]
            # Ensure aggregate has loop_record if needed
            if agg.loop_record is None and agg.events_by_id and loop_record_builder_fn:
                self._rematerialize_aggregate(
                    agg,
                    controller=controller,
                    journey_events_fn=journey_events_fn,
                    loop_record_builder_fn=loop_record_builder_fn,
                )
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

