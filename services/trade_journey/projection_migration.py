"""LIFECYCLE-PROJ-MIGRATE-001: resumable backfill and old/new parity tooling.

Backfills the relational Trade Journey projection
(:mod:`services.trade_journey.projection_store`) from canonical
``telemetry_events`` rows and proves deterministic parity against the legacy
JSON read-model, without serving reads from the new store.

Design boundaries (see
``docs/04/pantheon_lifecycle_projector_incremental_redesign_2026-08-01/archive/
LIFECYCLE_PROJECTOR_INCREMENTAL_REDESIGN_PLAN_2026-08-01.md`` section 11):

* ``telemetry_events`` is read-only here; nothing in this module writes to it.
* Reduction reuses the same pure per-batch fold the live JSON projector uses
  (:class:`~services.trade_journey.incremental_materializer.
  IncrementalLifecycleMaterializer` and
  :class:`~services.trade_journey.materializer.JourneyMaterializer`), so the
  relational rows this module derives are parity-by-construction with the
  legacy bundle rather than a second hand-maintained mapping that could drift.
* The migration watermark is a distinct controller row
  (``<controller_id>-migrate``) in the same additive
  ``trade_journey_projection.controller`` table; :class:`BackfillCoordinator`
  never reads or advances the live controller identity and always runs in
  ``backfill`` mode, never ``live``.
* Nothing here flips a BFF read flag. Cutover is LIFECYCLE-PROJ-CUTOVER-001.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from services.trade_journey.incremental_materializer import (
    BoundedAggregateState,
    IncrementalLifecycleMaterializer,
)
from services.trade_journey.lifecycle_projector import (
    LIFECYCLE_EVENT_TYPES,
    InvalidLifecycleEvent,
    LifecycleProjector,
    _fingerprint,
)
from services.trade_journey.materializer import (
    IDENTIFIER_FIELDS,
    JourneyMaterializer,
    STAGES,
    TERMINAL_STATUSES,
)
from services.trade_journey.projection_store import (
    BatchProjectionMutation,
    EventReceiptRow,
    IdentityLinkRow,
    JourneyRow,
    JourneyStageRow,
    LoopRunRow,
    ProjectionStore,
    QuarantineRow,
)

MIGRATION_CONTROLLER_SUFFIX = "-migrate"
DEFAULT_BATCH_SIZE = 500


def migration_controller_id(controller_id: str) -> str:
    """Return the distinct durable-watermark identity for a migration job.

    Reuses the additive ``controller`` table instead of a new schema object,
    but is scoped to its own row so a migration run can never read or advance
    the live checkpoint.
    """
    if controller_id.endswith(MIGRATION_CONTROLLER_SUFFIX):
        raise ValueError("controller_id must be the live controller id, not already migration-scoped")
    return f"{controller_id}{MIGRATION_CONTROLLER_SUFFIX}"


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must be timezone-aware ISO-8601: {value!r}")
    return parsed.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Reduction: shape and validate one ordered window of source rows.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReducedBatch:
    """Pure result of folding one ordered window of source rows.

    ``entries`` and ``quarantine`` mirror exactly what
    ``LifecycleProjector.project_records`` derives internally before it ever
    touches a JSON bundle -- this is the bounded-shadow-oracle reuse the
    redesign plan requires instead of a second hand-written reducer.
    """

    entries: tuple[Mapping[str, Any], ...]
    quarantine: tuple[Mapping[str, Any], ...]
    ignored: int
    high_watermark: int


def reduce_source_rows(rows: Sequence[Mapping[str, Any]], *, mode: str = "backfill") -> ReducedBatch:
    """Validate and shape one ordered window of source rows.

    Duplicate/conflict detection against already-committed state is left to
    :meth:`IncrementalLifecycleMaterializer.stage_batch`, the same idempotency
    boundary the live projector uses.
    """
    if mode not in {"backfill", "recovery", "replay"}:
        raise ValueError(
            f"projection_migration only runs in shadow modes, not {mode!r}; "
            "live cutover is LIFECYCLE-PROJ-CUTOVER-001"
        )
    ordered = sorted(
        (dict(row) for row in rows),
        key=lambda row: (int(row.get("ingested_seq") or 0), str(row.get("event_id") or "")),
    )
    entries: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []
    ignored = 0
    high_watermark = 0
    for row in ordered:
        sequence = int(row.get("ingested_seq") or 0)
        high_watermark = max(high_watermark, sequence)
        event = LifecycleProjector._source_event(row)
        if event["event_type"] not in LIFECYCLE_EVENT_TYPES:
            ignored += 1
            continue
        fingerprint = _fingerprint(
            {
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "created_at": event["created_at"],
                "payload": event,
            }
        )
        try:
            LifecycleProjector._validate_fixture_event(event)
            identity = LifecycleProjector._identity(event)
            sequence_no = LifecycleProjector._sequence_no(event)
        except InvalidLifecycleEvent as exc:
            quarantine.append(
                {
                    "event_id": event["event_id"],
                    "ingested_seq": sequence,
                    "event_type": event["event_type"],
                    "created_at": event["created_at"],
                    "reason": str(exc),
                    "source_mode": mode,
                }
            )
            continue
        entries.append(
            {
                "fingerprint": fingerprint,
                "event": event,
                "identity": identity,
                "sequence_no": sequence_no,
                "ingested_seq": sequence,
                "ingested_at": str(row.get("ingested_at") or event["created_at"]),
                "source_mode": mode,
                "accepted_live": False,
            }
        )
    return ReducedBatch(tuple(entries), tuple(quarantine), ignored, high_watermark)


# ---------------------------------------------------------------------------
# Mapping: bounded aggregate -> relational projection rows.
# ---------------------------------------------------------------------------

_STAGE_CONTRACT_FIELD_ALLOWLIST = (
    "event_type", "source", "source_mode", "accepted_live", "quantity", "price",
    "causal_parent_id", "run_id", "loop_run_id", "signal_id", "strategy_id",
)


def _stage_rows_for_aggregate(
    agg: BoundedAggregateState,
    *,
    projection_revision: int,
    batch_event_ids: "set[str] | None" = None,
) -> list[JourneyStageRow]:
    rows: list[JourneyStageRow] = []
    for journey_event in agg.journey_events:
        stage = journey_event.get("stage")
        if not stage:
            continue
        canonical_event_id = journey_event["canonical_event_id"]
        if batch_event_ids is not None and canonical_event_id not in batch_event_ids:
            continue
        rows.append(
            JourneyStageRow(
                tenant_id=agg.tenant_id,
                environment=agg.environment,
                journey_id=agg.journey_id,
                source_event_id=canonical_event_id,
                stage_name=stage,
                stage_status=journey_event.get("stage_status", "unknown"),
                stage_ordinal=STAGES.index(stage) if stage in STAGES else len(STAGES),
                source_ingested_seq=int(journey_event.get("source_offset") or 0),
                event_sequence=int(journey_event.get("sequence_no") or 0),
                occurred_at=_parse_dt(journey_event["occurred_at"]),
                recorded_at=_parse_dt(journey_event.get("recorded_at")),
                contract_fields={
                    key: journey_event[key]
                    for key in _STAGE_CONTRACT_FIELD_ALLOWLIST
                    if key in journey_event
                },
                evidence_references=[],
                projection_revision=projection_revision,
                fingerprint=agg.event_fingerprints.get(canonical_event_id, ""),
            )
        )
    return rows


def _journey_row_for_aggregate(
    agg: BoundedAggregateState, *, projection_revision: int
) -> JourneyRow | None:
    if not agg.journey_events:
        return None
    materializer = JourneyMaterializer()
    materializer.rebuild(agg.journey_events)
    projection = materializer.get(agg.journey_id, tenant_id=agg.tenant_id, environment=agg.environment)
    if projection is None:
        return None
    snapshot = projection.snapshot
    ingested_seqs = [int(je.get("source_offset") or 0) for je in agg.journey_events]
    return JourneyRow(
        tenant_id=agg.tenant_id,
        environment=agg.environment,
        journey_id=agg.journey_id,
        status=snapshot["status"],
        stage_coverage=snapshot["stages"],
        is_terminal=snapshot["status"] in TERMINAL_STATUSES,
        first_occurred_at=_parse_dt(snapshot["created_at"]),
        last_occurred_at=_parse_dt(snapshot["updated_at"]),
        first_ingested_seq=min(ingested_seqs),
        last_ingested_seq=max(ingested_seqs),
        current_identity_summary=snapshot.get("identifiers", {}),
        evidence_summary={"diagnostics": projection.diagnostics},
        diagnostic_summary={"completeness": snapshot.get("completeness", {})},
        loop_run_id=agg.identity.get("loop_run_id", ""),
        projection_revision=projection_revision,
    )


def _loop_run_row_for_aggregate(
    agg: BoundedAggregateState, *, projection_revision: int
) -> LoopRunRow | None:
    record = agg.loop_record
    if not record:
        return None
    return LoopRunRow(
        tenant_id=agg.tenant_id,
        environment=agg.environment,
        loop_run_id=record["loop_run_id"],
        journey_id=agg.journey_id,
        status=record["status"],
        lifecycle_summary={
            "canonical_event_count": record["canonical_event_count"],
            "fill_event_count": record["fill_event_count"],
            "position_event_count": record["position_event_count"],
            "reconciliation_event_count": record["reconciliation_event_count"],
        },
        freshness_lineage={
            "source_modes": record["source_modes"],
            "accepted_live": record["accepted_live"],
            "projection_mode": record["projection_mode"],
            "last_canonical_event_id": record["last_canonical_event_id"],
            "last_source_offset": record["last_source_offset"],
        },
        contract_payload=record,
        projection_revision=projection_revision,
    )


def _receipts_and_identity_links(
    agg: BoundedAggregateState,
    *,
    projection_revision: int,
    batch_event_ids: "set[str] | None" = None,
) -> tuple[list[EventReceiptRow], list[IdentityLinkRow]]:
    by_canonical: dict[str, dict[str, Any]] = {}
    for journey_event in agg.journey_events:
        canonical_id = journey_event["canonical_event_id"]
        if batch_event_ids is not None and canonical_id not in batch_event_ids:
            continue
        by_canonical.setdefault(canonical_id, journey_event)
    receipts = [
        EventReceiptRow(
            event_id=canonical_id,
            ingested_seq=int(journey_event.get("source_offset") or 0),
            fingerprint=agg.event_fingerprints.get(canonical_id, ""),
            tenant_id=agg.tenant_id,
            environment=agg.environment,
            journey_id=agg.journey_id,
            loop_run_id=agg.identity.get("loop_run_id", ""),
            source_event_type=str(journey_event.get("event_type") or ""),
            created_at=_parse_dt(journey_event["occurred_at"]),
            disposition="applied",
            projection_revision=projection_revision,
        )
        for canonical_id, journey_event in by_canonical.items()
    ]

    occurrences: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for journey_event in agg.journey_events:
        for identifier_type in IDENTIFIER_FIELDS:
            value = journey_event.get(identifier_type)
            if isinstance(value, str) and value:
                occurrences.setdefault((identifier_type, value), []).append(journey_event)
    identity_links = []
    for (identifier_type, value), events in occurrences.items():
        ingested_seqs = [int(je.get("source_offset") or 0) for je in events]
        occurred_ats = sorted(str(je["occurred_at"]) for je in events)
        identity_links.append(
            IdentityLinkRow(
                tenant_id=agg.tenant_id,
                environment=agg.environment,
                identifier_type=identifier_type,
                identifier_value=value,
                journey_id=agg.journey_id,
                first_ingested_seq=min(ingested_seqs),
                last_ingested_seq=max(ingested_seqs),
                first_occurred_at=_parse_dt(occurred_ats[0]),
                last_occurred_at=_parse_dt(occurred_ats[-1]),
            )
        )
    return receipts, identity_links


def _quarantine_receipts_and_rows(
    quarantine_items: Sequence[Mapping[str, Any]], *, projection_revision: int
) -> tuple[list[EventReceiptRow], list[QuarantineRow]]:
    receipts: list[EventReceiptRow] = []
    rows: list[QuarantineRow] = []
    for item in quarantine_items:
        event_id = str(item["event_id"])
        ingested_seq = int(item["ingested_seq"])
        receipts.append(
            EventReceiptRow(
                event_id=event_id,
                ingested_seq=ingested_seq,
                fingerprint="",
                tenant_id="",
                environment="",
                journey_id="",
                loop_run_id="",
                source_event_type=str(item.get("event_type") or ""),
                created_at=_parse_dt(item.get("created_at")),
                disposition="quarantined",
                projection_revision=projection_revision,
            )
        )
        rows.append(
            QuarantineRow(
                event_id=event_id,
                ingested_seq=ingested_seq,
                reason_code="invalid_lifecycle_event",
                reason_detail=str(item.get("reason") or "")[:500],
                source_event_type=str(item.get("event_type") or ""),
            )
        )
    return receipts, rows


def build_batch_mutation(
    staged: Mapping[str, BoundedAggregateState],
    reduced: ReducedBatch,
    *,
    affected: "set[str] | None" = None,
    projection_revision: int,
    source_high_watermark: int,
    backlog_count: int,
    deployment_sha: str = "",
) -> BatchProjectionMutation:
    """Map one folded batch into the rows :class:`ProjectionStore` commits.

    ``mode`` is always ``backfill`` and ``accepted_live`` is always ``False``:
    this migration tool never manufactures live freshness (redesign plan
    section 5.1) and never runs against the live controller row.
    """
    mutation = BatchProjectionMutation(
        mode="backfill",
        source_high_watermark=source_high_watermark,
        backlog_count=backlog_count,
        accepted_live=False,
        deployment_sha=deployment_sha,
    )
    touched = staged.values() if affected is None else (
        agg for journey_id, agg in staged.items() if journey_id in affected
    )
    batch_event_ids = {entry["event"]["event_id"] for entry in reduced.entries}
    for agg in touched:
        receipts, identity_links = _receipts_and_identity_links(
            agg, projection_revision=projection_revision, batch_event_ids=batch_event_ids
        )
        mutation.receipts.extend(receipts)
        mutation.identity_links.extend(identity_links)
        mutation.stages.extend(
            _stage_rows_for_aggregate(
                agg, projection_revision=projection_revision, batch_event_ids=batch_event_ids
            )
        )
        journey_row = _journey_row_for_aggregate(agg, projection_revision=projection_revision)
        if journey_row is not None:
            mutation.journeys.append(journey_row)
        loop_row = _loop_run_row_for_aggregate(agg, projection_revision=projection_revision)
        if loop_row is not None:
            mutation.loop_runs.append(loop_row)
    quarantine_receipts, quarantine_rows = _quarantine_receipts_and_rows(
        reduced.quarantine, projection_revision=projection_revision
    )
    mutation.receipts.extend(quarantine_receipts)
    mutation.quarantines.extend(quarantine_rows)
    return mutation


# ---------------------------------------------------------------------------
# Resumable backfill coordinator.
# ---------------------------------------------------------------------------


class BackfillCoordinator:
    """Drive a resumable, bounded backfill of the relational projection.

    Durable progress lives in the migration-scoped controller row so another
    process (or the same process after a restart) can see where the job got
    to. A local snapshot of the in-flight bounded aggregates -- the same
    ``to_dict``/``from_dict`` shape :class:`IncrementalLifecycleMaterializer`
    already supports for the live projector's own restart path -- lets a
    restart resume without re-reading history from ``ingested_seq`` 0: only
    the journeys a still-unprocessed batch touches are ever rebuilt in
    memory, so restart cost stays bounded by the batch, not total history.
    Re-submitting an already-applied batch is additionally safe because
    :meth:`ProjectionStore.execute_batch_transaction` treats a repeated exact
    receipt as a no-op duplicate.
    """

    def __init__(
        self,
        store: ProjectionStore,
        *,
        controller_id: str,
        tenant_scope: str,
        environment_scope: str,
        fetch_batch: Callable[[int, int], Sequence[Mapping[str, Any]]],
        snapshot_path: Path,
        deployment_sha: str = "",
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self.store = store
        self.controller_id = controller_id
        self.migration_controller_id = migration_controller_id(controller_id)
        self.tenant_scope = tenant_scope
        self.environment_scope = environment_scope
        self.fetch_batch = fetch_batch
        self.snapshot_path = snapshot_path
        self.deployment_sha = deployment_sha
        self.batch_size = batch_size
        self._materializer = IncrementalLifecycleMaterializer(initial_state=self._read_snapshot())

    def _read_snapshot(self) -> Mapping[str, Any] | None:
        if not self.snapshot_path.exists():
            return None
        return json.loads(self.snapshot_path.read_text(encoding="utf-8"))

    def _write_snapshot(self, checkpoint: int) -> None:
        payload = {"checkpoint": checkpoint, "aggregates": self._materializer.serialize_aggregates()}
        tmp = self.snapshot_path.with_name(self.snapshot_path.name + ".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True, default=str), encoding="utf-8")
        tmp.replace(self.snapshot_path)

    def checkpoint(self) -> int:
        """The durable resume position: local snapshot if present, else the
        migration controller row's checkpoint, else zero (fresh start)."""
        snapshot = self._read_snapshot()
        if snapshot and "checkpoint" in snapshot:
            return int(snapshot["checkpoint"])
        controller = self.store.get_controller_state(
            self.migration_controller_id, self.tenant_scope, self.environment_scope
        )
        return int(controller.checkpoint_seq) if controller else 0

    def run(self, *, max_batches: int | None = None) -> dict[str, Any]:
        """Fetch and apply batches until the source backlog is empty.

        Returns a bounded summary, not a growing per-row log.
        """
        totals = {"batches": 0, "accepted": 0, "duplicates": 0, "quarantined": 0, "ignored": 0}
        checkpoint = self.checkpoint()
        while max_batches is None or totals["batches"] < max_batches:
            rows = list(self.fetch_batch(checkpoint, self.batch_size))
            if not rows:
                break
            reduced = reduce_source_rows(rows, mode="backfill")
            staged, _affected, accepted, duplicates = self._materializer.stage_batch(reduced.entries)
            if accepted == 0 and duplicates > 0:
                # Batch contained only duplicate events already staged in materializer
                checkpoint = reduced.high_watermark
                self._write_snapshot(checkpoint)
                totals["duplicates"] += duplicates
                totals["quarantined"] += len(reduced.quarantine)
                totals["ignored"] += reduced.ignored
                continue

            mutation = build_batch_mutation(
                staged,
                reduced,
                affected=_affected,
                projection_revision=totals["batches"] + 1,
                source_high_watermark=reduced.high_watermark,
                backlog_count=0,
                deployment_sha=self.deployment_sha,
            )
            self.store.execute_batch_transaction(
                self.migration_controller_id, self.tenant_scope, self.environment_scope, mutation
            )
            self._materializer.commit(staged)
            checkpoint = reduced.high_watermark
            self._write_snapshot(checkpoint)
            totals["batches"] += 1
            totals["accepted"] += accepted
            totals["duplicates"] += duplicates
            totals["quarantined"] += len(reduced.quarantine)
            totals["ignored"] += reduced.ignored
        totals["checkpoint"] = checkpoint
        return totals


# ---------------------------------------------------------------------------
# Deterministic old/new parity.
# ---------------------------------------------------------------------------


def stable_hash(rows: Iterable[Mapping[str, Any]], *, key_fields: Sequence[str]) -> str:
    """A stable scoped hash over a bounded row collection.

    Rows are sorted by ``key_fields`` before hashing so delivery/query order
    can never change the result -- only content can.
    """
    ordered = sorted((dict(row) for row in rows), key=lambda row: tuple(str(row.get(f, "")) for f in key_fields))
    payload = json.dumps(ordered, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ParityCategoryResult:
    category: str
    legacy_hash: str
    new_hash: str
    match: bool
    classification: str | None = None


def compare_category(
    category: str,
    legacy_rows: Iterable[Mapping[str, Any]],
    new_rows: Iterable[Mapping[str, Any]],
    *,
    key_fields: Sequence[str],
    classification: str | None = None,
) -> ParityCategoryResult:
    """Compare one category's stable hash; an unmatched category is only
    ever "explained" by an explicit ``classification`` the caller supplies."""
    legacy_hash = stable_hash(legacy_rows, key_fields=key_fields)
    new_hash = stable_hash(new_rows, key_fields=key_fields)
    match = legacy_hash == new_hash
    return ParityCategoryResult(category, legacy_hash, new_hash, match, None if match else classification)


def summarize_parity(results: Sequence[ParityCategoryResult]) -> dict[str, Any]:
    mismatches = [result for result in results if not result.match]
    unexplained = [result for result in mismatches if not result.classification]
    return {
        "categories": {
            result.category: {
                "match": result.match,
                "legacy_hash": result.legacy_hash,
                "new_hash": result.new_hash,
                "classification": result.classification,
            }
            for result in results
        },
        "mismatch_count": len(mismatches),
        "unexplained_mismatch_count": len(unexplained),
    }


# ---------------------------------------------------------------------------
# Row-shape adapters: legacy JSON bundle <-> relational rows.
#
# Both sides are reduced to the same bounded, order-independent dict shape so
# ``stable_hash`` can compare them; this is where a documented, intended
# contract change (e.g. a renamed field) is isolated to one adapter function
# instead of leaking into the comparison itself.
# ---------------------------------------------------------------------------


def legacy_stage_rows(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "tenant_id": event.get("tenant_id"),
            "environment": event.get("environment"),
            "journey_id": event.get("journey_id"),
            "source_event_id": event.get("canonical_event_id"),
            "stage_name": event.get("stage"),
            "stage_status": event.get("stage_status"),
        }
        for event in events
        if event.get("stage")
    ]


def _field(row: Any, name: str) -> Any:
    return getattr(row, name) if hasattr(row, name) else row.get(name)


def projection_stage_rows(rows: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {
            "tenant_id": _field(row, "tenant_id"),
            "environment": _field(row, "environment"),
            "journey_id": _field(row, "journey_id"),
            "source_event_id": _field(row, "source_event_id"),
            "stage_name": _field(row, "stage_name"),
            "stage_status": _field(row, "stage_status"),
        }
        for row in rows
    ]


def legacy_journey_rows(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    events = list(events)
    materializer = JourneyMaterializer()
    if events:
        materializer.rebuild(events)
    rows = []
    seen: set[tuple[str, str, str]] = set()
    for event in events:
        key = (str(event.get("tenant_id")), str(event.get("environment")), str(event.get("journey_id")))
        if key in seen:
            continue
        seen.add(key)
        projection = materializer.get(key[2], tenant_id=key[0], environment=key[1])
        if projection is None:
            continue
        rows.append(
            {
                "tenant_id": key[0],
                "environment": key[1],
                "journey_id": key[2],
                "status": projection.snapshot["status"],
                "is_terminal": projection.snapshot["status"] in TERMINAL_STATUSES,
            }
        )
    return rows


def projection_journey_rows(rows: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {
            "tenant_id": _field(row, "tenant_id"),
            "environment": _field(row, "environment"),
            "journey_id": _field(row, "journey_id"),
            "status": _field(row, "status"),
            "is_terminal": bool(_field(row, "is_terminal")),
        }
        for row in rows
    ]


def legacy_loop_rows(records: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "tenant_id": record.get("tenant_id"),
            "environment": record.get("environment"),
            "loop_run_id": record.get("loop_run_id", loop_run_id),
            "status": record.get("status"),
            "canonical_event_count": record.get("canonical_event_count"),
        }
        for loop_run_id, record in records.items()
    ]


def projection_loop_rows(rows: Iterable[Any]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        summary = _field(row, "lifecycle_summary") or {}
        result.append(
            {
                "tenant_id": _field(row, "tenant_id"),
                "environment": _field(row, "environment"),
                "loop_run_id": _field(row, "loop_run_id"),
                "status": _field(row, "status"),
                "canonical_event_count": summary.get("canonical_event_count")
                if isinstance(summary, Mapping)
                else None,
            }
        )
    return result


def legacy_identity_rows(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    seen: set[tuple[Any, ...]] = set()
    for event in events:
        for identifier_type in IDENTIFIER_FIELDS:
            value = event.get(identifier_type)
            if not (isinstance(value, str) and value):
                continue
            key = (event.get("tenant_id"), event.get("environment"), identifier_type, value, event.get("journey_id"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "tenant_id": event.get("tenant_id"),
                    "environment": event.get("environment"),
                    "identifier_type": identifier_type,
                    "identifier_value": value,
                    "journey_id": event.get("journey_id"),
                }
            )
    return rows


def projection_identity_rows(rows: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {
            "tenant_id": _field(row, "tenant_id"),
            "environment": _field(row, "environment"),
            "identifier_type": _field(row, "identifier_type"),
            "identifier_value": _field(row, "identifier_value"),
            "journey_id": _field(row, "journey_id"),
        }
        for row in rows
    ]


def legacy_quarantine_rows(quarantine: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"event_id": item.get("event_id"), "ingested_seq": item.get("ingested_seq")}
        for item in quarantine
    ]


def projection_quarantine_rows(rows: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {"event_id": _field(row, "event_id"), "ingested_seq": _field(row, "ingested_seq")}
        for row in rows
    ]
