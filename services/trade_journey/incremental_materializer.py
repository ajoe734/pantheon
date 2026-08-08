"""Bounded incremental lifecycle materializer for Trade Journey and Loop Runs.

The aggregate is the unit of work and the unit of state.  Each
:class:`BoundedAggregateState` keeps only *folded* read-model state for one
journey: the derived Trade Journey events, the per-event idempotency
fingerprints and source modes, folded loop-run counters, and the cached loop-run
record.  The raw canonical telemetry log is never retained.

Two consequences matter for the reducer contract:

* applying a poll batch rebuilds only the aggregates that batch touched, so the
  number of aggregates rematerialized and the number of canonical entries pushed
  through ``journey_events_fn`` are bounded by the batch and the aggregates it
  affects, not by total history;
* publishing concatenates already-materialized per-aggregate event lists and
  overlays a fixed-size controller stamp onto cached loop records, so a publish
  performs no re-derivation at all.

``stats`` exposes those counters so callers (and tests) can assert the bound
directly instead of inferring it from wall-clock behaviour.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from services.trade_journey.materializer import JourneyMaterializer, JourneyProjection


# Loop-run fields that describe the publishing controller rather than the
# aggregate.  They are stamped at render time so cached loop records never go
# stale when an unrelated poll advances the controller generation.
LOOP_RECORD_CONTROLLER_STAMP = (
    "last_projected_at",
    "controller_id",
    "controller_generation",
    "deployment_sha",
)

FILL_EVENT_TYPES = frozenset(
    {"paper_fill_simulated", "fill_received", "order_partially_filled", "order_filled"}
)
LOOP_TERMINAL_STATUSES = frozenset(
    {"completed", "completed_with_variance", "failed", "cancelled"}
)
LOOP_ACTIVE_STATUSES = frozenset({"open", "executing", "partially_filled"})


def _entry_sort_key(entry: Mapping[str, Any]) -> tuple[str, int, str, str]:
    identity = entry.get("identity") or {}
    event = entry.get("event") or {}
    return (
        str(identity.get("journey_id") or ""),
        int(entry.get("sequence_no") or 0),
        str(event.get("created_at") or ""),
        str(event.get("event_id") or ""),
    )


@dataclass
class BoundedAggregateState:
    """Folded read-model state for a single Trade Journey lifecycle.

    No raw canonical entry is kept.  ``journey_events`` is the materialized
    read-model slice this aggregate contributes to the published bundle.
    """

    journey_id: str
    tenant_id: str
    environment: str
    identity: dict[str, str] = field(default_factory=dict)
    event_fingerprints: dict[str, str] = field(default_factory=dict)
    event_modes: dict[str, str] = field(default_factory=dict)
    journey_events: list[dict[str, Any]] = field(default_factory=list)
    fill_event_count: int = 0
    position_event_count: int = 0
    reconciliation_event_count: int = 0
    last_ingested_seq: int = 0
    last_sequence_no: int = 0
    last_entry_key: list[Any] | None = None
    last_canonical_event_id: str | None = None
    last_source_offset: Any = None
    last_live_event_at: str | None = None
    loop_record: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize folded aggregate state for controller_state.json."""
        return {
            "journey_id": self.journey_id,
            "tenant_id": self.tenant_id,
            "environment": self.environment,
            "identity": self.identity,
            "event_fingerprints": self.event_fingerprints,
            "event_modes": self.event_modes,
            "journey_events": self.journey_events,
            "fill_event_count": self.fill_event_count,
            "position_event_count": self.position_event_count,
            "reconciliation_event_count": self.reconciliation_event_count,
            "last_ingested_seq": self.last_ingested_seq,
            "last_sequence_no": self.last_sequence_no,
            "last_entry_key": self.last_entry_key,
            "last_canonical_event_id": self.last_canonical_event_id,
            "last_source_offset": self.last_source_offset,
            "last_live_event_at": self.last_live_event_at,
            "loop_record": self.loop_record,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BoundedAggregateState:
        """Restore folded aggregate state written by :meth:`to_dict`."""
        return cls(
            journey_id=data["journey_id"],
            tenant_id=data.get("tenant_id", ""),
            environment=data.get("environment", ""),
            identity=dict(data.get("identity") or {}),
            event_fingerprints=dict(data.get("event_fingerprints") or {}),
            event_modes=dict(data.get("event_modes") or {}),
            journey_events=list(data.get("journey_events") or []),
            fill_event_count=int(data.get("fill_event_count") or 0),
            position_event_count=int(data.get("position_event_count") or 0),
            reconciliation_event_count=int(data.get("reconciliation_event_count") or 0),
            last_ingested_seq=int(data.get("last_ingested_seq") or 0),
            last_sequence_no=int(data.get("last_sequence_no") or 0),
            last_entry_key=list(data["last_entry_key"]) if data.get("last_entry_key") else None,
            last_canonical_event_id=data.get("last_canonical_event_id"),
            last_source_offset=data.get("last_source_offset"),
            last_live_event_at=data.get("last_live_event_at"),
            loop_record=dict(data["loop_record"]) if data.get("loop_record") else None,
        )

    @property
    def source_modes(self) -> list[str]:
        return sorted({str(mode) for mode in self.event_modes.values()})

    @property
    def accepted_live(self) -> bool:
        return "live" in set(self.event_modes.values())


class IncrementalLifecycleMaterializer:
    """Maintain folded per-journey aggregates and render bundles from them."""

    def __init__(
        self,
        initial_state: Mapping[str, Any] | None = None,
        *,
        journey_events_fn: Any = None,
    ) -> None:
        self.aggregates: dict[str, BoundedAggregateState] = {}
        self._journey_events_fn = journey_events_fn
        self.stats: dict[str, int] = {}
        self.reset_stats()
        if initial_state:
            self._restore_from_state(initial_state)

    # ------------------------------------------------------------------
    # instrumentation
    # ------------------------------------------------------------------

    def reset_stats(self) -> None:
        """Zero the per-poll bound counters.

        ``entries_derived`` counts canonical entries pushed through
        ``journey_events_fn``; ``aggregates_rematerialized`` counts per-aggregate
        :class:`JourneyMaterializer` rebuilds; ``aggregates_snapshotted`` counts
        aggregates deep-copied for staging.  All three must stay bounded by the
        batch and the aggregates it affects.
        """
        self.stats = {
            "entries_derived": 0,
            "aggregates_rematerialized": 0,
            "aggregates_snapshotted": 0,
        }

    def _events_fn(self, override: Any = None) -> Any:
        if override is not None:
            return override
        if self._journey_events_fn is None:
            from services.trade_journey.lifecycle_projector import LifecycleProjector

            self._journey_events_fn = LifecycleProjector._journey_events
        return self._journey_events_fn

    # ------------------------------------------------------------------
    # restore
    # ------------------------------------------------------------------

    def _restore_from_state(self, state: Mapping[str, Any]) -> None:
        stored_aggregates = state.get("aggregates")
        if isinstance(stored_aggregates, Mapping) and stored_aggregates:
            legacy_entries: dict[str, list[Mapping[str, Any]]] = {}
            for journey_id, agg_data in stored_aggregates.items():
                if isinstance(agg_data, BoundedAggregateState):
                    self.aggregates[journey_id] = agg_data
                    continue
                if not isinstance(agg_data, Mapping):
                    continue
                if "journey_events" in agg_data:
                    self.aggregates[journey_id] = BoundedAggregateState.from_dict(agg_data)
                    continue
                # Pre-fold aggregate layout that still carried raw entries.
                raw = agg_data.get("events_by_id") or {}
                if isinstance(raw, Mapping) and raw:
                    legacy_entries[journey_id] = list(raw.values())
            for journey_id, entries in legacy_entries.items():
                self._fold_entries(journey_id, entries)
            if self.aggregates:
                return

        # Legacy on-disk layout: one global all-history canonical event log.
        canonical_events = state.get("canonical_events") or {}
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for entry in canonical_events.values():
            journey_id = (entry.get("identity") or {}).get("journey_id")
            if not journey_id:
                continue
            grouped.setdefault(journey_id, []).append(entry)
        for journey_id, entries in grouped.items():
            self._fold_entries(journey_id, entries)

    def _fold_entries(self, journey_id: str, entries: Iterable[Mapping[str, Any]]) -> None:
        ordered = sorted(entries, key=_entry_sort_key)
        if not ordered:
            return
        identity = dict(ordered[0].get("identity") or {})
        agg = BoundedAggregateState(
            journey_id=journey_id,
            tenant_id=identity.get("tenant_id", ""),
            environment=identity.get("environment", ""),
            identity=identity,
        )
        events_fn = self._events_fn()
        for entry in ordered:
            self._absorb(agg, entry, events_fn)
        agg.journey_events.sort(key=JourneyMaterializer._sort_key)
        self._rematerialize_aggregate(agg)
        self.aggregates[journey_id] = agg

    # ------------------------------------------------------------------
    # batch application
    # ------------------------------------------------------------------

    def _stage(
        self,
        journey_id: str,
        staged: dict[str, BoundedAggregateState],
        *,
        identity: Mapping[str, str] | None = None,
    ) -> BoundedAggregateState | None:
        """Return the staged copy of ``journey_id``, creating it if needed.

        Only aggregates the batch touches are deep-copied, which is what keeps
        per-poll copying bounded by the affected aggregates.
        """
        if journey_id in staged:
            return staged[journey_id]
        existing = self.aggregates.get(journey_id)
        if existing is not None:
            staged[journey_id] = copy.deepcopy(existing)
            self.stats["aggregates_snapshotted"] += 1
            return staged[journey_id]
        if identity is None:
            return None
        staged[journey_id] = BoundedAggregateState(
            journey_id=journey_id,
            tenant_id=identity.get("tenant_id", ""),
            environment=identity.get("environment", ""),
            identity=dict(identity),
        )
        self.stats["aggregates_snapshotted"] += 1
        return staged[journey_id]

    def _absorb(
        self,
        agg: BoundedAggregateState,
        entry: Mapping[str, Any],
        events_fn: Any,
    ) -> None:
        """Fold one accepted canonical entry into the aggregate."""
        event = entry["event"]
        event_id = event["event_id"]
        source_mode = str(entry["source_mode"])
        agg.event_fingerprints[event_id] = entry["fingerprint"]
        agg.event_modes[event_id] = source_mode

        self.stats["entries_derived"] += 1
        agg.journey_events.extend(events_fn(entry))

        event_type = str(event.get("event_type") or "")
        if event_type in FILL_EVENT_TYPES:
            agg.fill_event_count += 1
        if "position_snapshot" in event_type:
            agg.position_event_count += 1
        if event_type.startswith("reconciliation_"):
            agg.reconciliation_event_count += 1

        agg.last_ingested_seq = max(agg.last_ingested_seq, int(entry.get("ingested_seq") or 0))
        agg.last_sequence_no = max(agg.last_sequence_no, int(entry.get("sequence_no") or 0))
        key = list(_entry_sort_key(entry)[1:])
        if agg.last_entry_key is None or key > list(agg.last_entry_key):
            agg.last_entry_key = key
            agg.last_canonical_event_id = event_id
            agg.last_source_offset = entry.get("ingested_seq")
        if source_mode == "live":
            created_at = str(event.get("created_at") or "")
            if created_at and (agg.last_live_event_at or "") < created_at:
                agg.last_live_event_at = created_at

    @staticmethod
    def _promote_to_live(agg: BoundedAggregateState, event_id: str) -> bool:
        """Re-deliver an already-known event under ``live`` mode.

        The cached read-model events for that canonical id are patched in place;
        the caller rematerializes the aggregate so the loop record follows.
        """
        if agg.event_modes.get(event_id) == "live":
            return False
        agg.event_modes[event_id] = "live"
        created_at = None
        for event in agg.journey_events:
            if event.get("canonical_event_id") != event_id:
                continue
            event["source"] = "canonical_telemetry_live"
            event["source_mode"] = "live"
            event["accepted_live"] = True
            created_at = created_at or event.get("occurred_at")
        if created_at and (agg.last_live_event_at or "") < str(created_at):
            agg.last_live_event_at = str(created_at)
        return True

    def stage_batch(
        self,
        canonical_entries: Sequence[Mapping[str, Any]],
        *,
        promotions: Mapping[str, Iterable[str]] | None = None,
        journey_events_fn: Any = None,
    ) -> tuple[dict[str, BoundedAggregateState], set[str], int, int]:
        """Fold a batch into deep copies of only the aggregates it touches.

        Nothing observable is mutated: the caller publishes from the staged view
        and calls :meth:`commit` only once the bundle and state have landed.

        Returns ``(staged, affected_journey_ids, accepted, duplicates)``.
        """
        events_fn = self._events_fn(journey_events_fn)
        staged: dict[str, BoundedAggregateState] = {}
        affected: set[str] = set()
        accepted = 0
        duplicates = 0

        batch_by_journey: dict[str, list[Mapping[str, Any]]] = {}
        for entry in canonical_entries:
            journey_id = (entry.get("identity") or {}).get("journey_id")
            if not journey_id:
                continue
            batch_by_journey.setdefault(journey_id, []).append(entry)

        journey_ids = sorted(set(batch_by_journey) | set(promotions or {}))
        for journey_id in journey_ids:
            entries = batch_by_journey.get(journey_id) or []
            identity = entries[0]["identity"] if entries else None
            agg = self._stage(journey_id, staged, identity=identity)
            if agg is None:
                continue
            changed = False
            for entry in sorted(entries, key=_entry_sort_key):
                event_id = entry["event"]["event_id"]
                fingerprint = entry["fingerprint"]
                known = agg.event_fingerprints.get(event_id)
                if known is not None:
                    if known != fingerprint:
                        from services.trade_journey.lifecycle_projector import (
                            ConflictingLifecycleEvent,
                        )

                        raise ConflictingLifecycleEvent(
                            f"conflicting canonical event_id: {event_id}"
                        )
                    duplicates += 1
                    continue
                self._absorb(agg, entry, events_fn)
                accepted += 1
                changed = True
            for event_id in (promotions.get(journey_id) or ()) if promotions else ():
                changed |= self._promote_to_live(agg, event_id)
            if changed:
                affected.add(journey_id)
                agg.journey_events.sort(key=JourneyMaterializer._sort_key)
                self._rematerialize_aggregate(agg)

        return staged, affected, accepted, duplicates

    def commit(self, staged: Mapping[str, BoundedAggregateState]) -> None:
        """Promote staged aggregates to live state after a successful publish."""
        self.aggregates.update(staged)

    def _rematerialize_aggregate(self, agg: BoundedAggregateState) -> None:
        """Rebuild one aggregate's projection and cached loop record."""
        self.stats["aggregates_rematerialized"] += 1
        materializer = JourneyMaterializer()
        materializer.rebuild(agg.journey_events)
        projection = materializer.get(
            agg.journey_id, tenant_id=agg.tenant_id, environment=agg.environment
        )
        agg.loop_record = self._loop_record(agg, projection)

    @staticmethod
    def _loop_record(
        agg: BoundedAggregateState, projection: JourneyProjection | None
    ) -> dict[str, Any] | None:
        """Build the aggregate-derived half of the loop record.

        The controller stamp is deliberately absent; it is applied at render
        time so an untouched aggregate still publishes a current stamp.
        """
        if projection is None:
            return None
        identity = dict(agg.identity)
        loop_run_id = identity.get("loop_run_id")
        if not loop_run_id:
            return None
        status = projection.snapshot.get("status") or "open"
        source_modes = agg.source_modes
        accepted_live = agg.accepted_live
        return {
            "id": loop_run_id,
            "loop_run_id": loop_run_id,
            "journey_id": agg.journey_id,
            "loop_type": "paper_execution",
            "status": "active" if status in LOOP_ACTIVE_STATUSES else status,
            "activePeriod": {
                "start": projection.snapshot.get("created_at"),
                "end": projection.snapshot.get("updated_at")
                if status in LOOP_TERMINAL_STATUSES
                else None,
            },
            **identity,
            "source": "canonical_telemetry_lifecycle_projector",
            "source_modes": source_modes,
            "accepted_live": accepted_live,
            "projection_mode": "live" if accepted_live else "+".join(source_modes),
            "canonical_event_count": len(agg.event_fingerprints),
            "fill_event_count": agg.fill_event_count,
            "position_event_count": agg.position_event_count,
            "reconciliation_event_count": agg.reconciliation_event_count,
            "last_canonical_event_id": agg.last_canonical_event_id,
            "last_source_offset": agg.last_source_offset,
        }

    # ------------------------------------------------------------------
    # read side
    # ------------------------------------------------------------------

    def merged_view(
        self, staged: Mapping[str, BoundedAggregateState] | None = None
    ) -> dict[str, BoundedAggregateState]:
        if not staged:
            return self.aggregates
        return {**self.aggregates, **staged}

    def serialize_aggregates(
        self,
        *,
        base: Mapping[str, Mapping[str, Any]] | None = None,
        staged: Mapping[str, BoundedAggregateState] | None = None,
    ) -> dict[str, Any]:
        """Serialize aggregates, re-serializing only the staged ones.

        ``base`` is the previously serialized mapping; untouched aggregates are
        carried over by reference rather than rebuilt.
        """
        if base is None:
            return {journey_id: agg.to_dict() for journey_id, agg in self.aggregates.items()}
        result = dict(base)
        for journey_id, agg in (staged or {}).items():
            result[journey_id] = agg.to_dict()
        return result

    def last_live_event_at(
        self, staged: Mapping[str, BoundedAggregateState] | None = None
    ) -> str | None:
        stamps = [
            agg.last_live_event_at
            for agg in self.merged_view(staged).values()
            if agg.last_live_event_at
        ]
        return max(stamps) if stamps else None

    def render_full_payloads(
        self,
        *,
        schema_version_journey: str,
        schema_version_loop: str,
        generation: int,
        controller: Mapping[str, Any],
        staged: Mapping[str, BoundedAggregateState] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Render both read models by concatenating cached per-aggregate slices.

        No canonical entry is re-derived and no aggregate is rebuilt here; the
        only per-publish work proportional to total history is the concatenation
        and ordering of already-materialized events, which is the payload itself.
        """
        aggregates = self.merged_view(staged)
        all_journey_events: list[dict[str, Any]] = []
        for journey_id in sorted(aggregates):
            all_journey_events.extend(aggregates[journey_id].journey_events)
        all_journey_events.sort(key=JourneyMaterializer._sort_key)

        stamp = {
            "last_projected_at": controller.get("last_projection_success_at"),
            "controller_id": controller.get("controller_id"),
            "controller_generation": controller.get("generation"),
            "deployment_sha": controller.get("deployment_sha"),
        }
        all_loop_records: dict[str, dict[str, Any]] = {}
        for journey_id in sorted(aggregates):
            record = aggregates[journey_id].loop_record
            if record:
                all_loop_records[record["id"]] = {**record, **stamp}

        envelope = {
            "projector_owned": True,
            "generation": generation,
            "projection_mode": controller.get("mode"),
            "accepted_live": bool(controller.get("accepted_live")),
            "controller": dict(controller),
        }
        journey_payload = {
            "schema_version": schema_version_journey,
            **envelope,
            "events": all_journey_events,
        }
        loop_payload = {
            "schema_version": schema_version_loop,
            **envelope,
            "records": all_loop_records,
        }
        return journey_payload, loop_payload
