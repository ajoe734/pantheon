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
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence, TextIO

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
LEGACY_BASELINE_SNAPSHOT_SCHEMA = "pantheon.lifecycle-legacy-baseline-snapshot.v1"


class _JsonStream:
    """Small stdlib-only streaming JSON reader for multi-GiB legacy bundles."""

    def __init__(self, handle: TextIO, *, chunk_size: int = 1024 * 1024) -> None:
        self.handle = handle
        self.chunk_size = chunk_size
        self.decoder = json.JSONDecoder()
        self.buffer = ""
        self.position = 0
        self.eof = False

    def _fill(self) -> bool:
        if self.position:
            self.buffer = self.buffer[self.position :]
            self.position = 0
        chunk = self.handle.read(self.chunk_size)
        if not chunk:
            self.eof = True
            return False
        self.buffer += chunk
        return True

    def _peek(self) -> str:
        while self.position >= len(self.buffer):
            if not self._fill():
                return ""
        return self.buffer[self.position]

    def skip_whitespace(self) -> None:
        while True:
            char = self._peek()
            if char and char.isspace():
                self.position += 1
                continue
            return

    def expect(self, expected: str) -> None:
        self.skip_whitespace()
        actual = self._peek()
        if actual != expected:
            raise ValueError(f"expected JSON token {expected!r}, found {actual!r}")
        self.position += 1

    def read_value(self) -> Any:
        self.skip_whitespace()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.position)
            except json.JSONDecodeError:
                if self.eof or not self._fill():
                    raise
                self.skip_whitespace()
                continue
            self.position = end
            return value

    def skip_value(self) -> None:
        """Skip one value without retaining a large object or array."""

        self.skip_whitespace()
        first = self._peek()
        if not first:
            raise ValueError("unexpected EOF while skipping JSON value")
        if first not in "[{":
            self.read_value()
            return
        stack = [first]
        self.position += 1
        in_string = False
        escaped = False
        while stack:
            char = self._peek()
            if not char:
                raise ValueError("unexpected EOF while skipping JSON value")
            self.position += 1
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "[{":
                stack.append(char)
            elif char == "}":
                if stack[-1] != "{":
                    raise ValueError("mismatched JSON object terminator")
                stack.pop()
            elif char == "]":
                if stack[-1] != "[":
                    raise ValueError("mismatched JSON array terminator")
                stack.pop()


def _seek_top_level_member(reader: _JsonStream, member: str) -> bool:
    reader.expect("{")
    reader.skip_whitespace()
    if reader._peek() == "}":
        reader.position += 1
        return False
    while True:
        key = reader.read_value()
        if not isinstance(key, str):
            raise ValueError("top-level JSON object key must be a string")
        reader.expect(":")
        if key == member:
            return True
        reader.skip_value()
        reader.skip_whitespace()
        token = reader._peek()
        if token == ",":
            reader.position += 1
            continue
        if token == "}":
            reader.position += 1
            return False
        raise ValueError(f"unexpected token after top-level member: {token!r}")


def read_legacy_bundle_member(path: Path, member: str) -> Any:
    """Read one bounded top-level member without loading preceding history."""

    with path.open("r", encoding="utf-8") as handle:
        reader = _JsonStream(handle)
        if not _seek_top_level_member(reader, member):
            raise ValueError(f"legacy bundle is missing top-level member {member!r}")
        return reader.read_value()


def validate_legacy_controller_binding(
    path: Path,
    *,
    expected_controller_id: str,
    expected_checkpoint: int,
    expected_deployment_sha: str | None = None,
) -> Mapping[str, Any]:
    """Fail closed unless the checksummed bundle's controller is admissible.

    The caller verifies the enclosing file checksum before calling this helper.
    Values are intentionally not coerced: a missing field, a stringified
    integer, or a truthy non-boolean must not become recovery authority.  The
    accepted identity and checkpoint are bound on every import and streaming
    parity run; the importer additionally binds the reviewed deployment SHA.
    """

    controller = read_legacy_bundle_member(path, "controller")
    if not isinstance(controller, Mapping):
        raise ValueError("legacy controller state controller must be an object")
    required_fields = {
        "controller_id",
        "checkpoint",
        "deployment_sha",
        "backlog",
        "quarantine_count",
        "accepted_live",
        "last_error",
    }
    missing_fields = sorted(required_fields.difference(controller))
    if missing_fields:
        raise ValueError(
            "legacy controller is missing required fields: "
            + ", ".join(missing_fields)
        )

    controller_id = controller["controller_id"]
    if not isinstance(controller_id, str) or not controller_id:
        raise ValueError("legacy controller identity must be a non-empty string")
    if controller_id != expected_controller_id:
        raise ValueError("legacy controller identity does not match the reviewed controller")

    checkpoint = controller["checkpoint"]
    if type(checkpoint) is not int or checkpoint <= 0:
        raise ValueError("legacy controller checkpoint must be a positive integer")
    if type(expected_checkpoint) is not int or expected_checkpoint <= 0:
        raise ValueError("reviewed legacy checkpoint must be a positive integer")
    if checkpoint != expected_checkpoint:
        raise ValueError("legacy controller checkpoint does not match the reviewed checkpoint")

    deployment_sha = controller["deployment_sha"]
    if not isinstance(deployment_sha, str) or not deployment_sha:
        raise ValueError("legacy controller deployment SHA must be a non-empty string")
    if expected_deployment_sha is not None:
        if not isinstance(expected_deployment_sha, str) or not expected_deployment_sha:
            raise ValueError("reviewed legacy controller deployment SHA is required")
        if deployment_sha != expected_deployment_sha:
            raise ValueError(
                "legacy controller deployment SHA does not match the reviewed deployment SHA"
            )

    backlog = controller["backlog"]
    if type(backlog) is not int or backlog != 0:
        raise ValueError("legacy controller backlog must be the integer zero")
    quarantine_count = controller["quarantine_count"]
    if type(quarantine_count) is not int or quarantine_count != 0:
        raise ValueError("legacy controller quarantine count must be the integer zero")
    if controller["accepted_live"] is not True:
        raise ValueError("legacy controller accepted_live must be exactly true")
    if controller["last_error"] is not None:
        raise ValueError("legacy controller last_error must be null")
    return controller


def iter_legacy_aggregates(path: Path) -> Iterator[tuple[str, BoundedAggregateState]]:
    """Yield one folded legacy aggregate at a time from controller state."""

    with path.open("r", encoding="utf-8") as handle:
        reader = _JsonStream(handle)
        if not _seek_top_level_member(reader, "aggregates"):
            raise ValueError("legacy controller state is missing aggregates")
        reader.expect("{")
        reader.skip_whitespace()
        if reader._peek() == "}":
            reader.position += 1
            return
        previous_key = ""
        while True:
            journey_id = reader.read_value()
            if not isinstance(journey_id, str) or not journey_id:
                raise ValueError("legacy aggregate key must be a non-empty string")
            if previous_key and journey_id <= previous_key:
                raise ValueError("legacy aggregate keys are not strictly sorted")
            reader.expect(":")
            payload = reader.read_value()
            if not isinstance(payload, Mapping):
                raise ValueError(f"legacy aggregate {journey_id!r} is not an object")
            aggregate = BoundedAggregateState.from_dict(payload)
            if aggregate.journey_id != journey_id:
                raise ValueError(f"legacy aggregate key mismatch for {journey_id!r}")
            yield journey_id, aggregate
            previous_key = journey_id
            reader.skip_whitespace()
            token = reader._peek()
            if token == ",":
                reader.position += 1
                continue
            if token == "}":
                reader.position += 1
                return
            raise ValueError(f"unexpected token after legacy aggregate: {token!r}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    owned_event_ids: "set[str] | None" = None,
) -> list[JourneyStageRow]:
    rows: list[JourneyStageRow] = []
    for journey_event in agg.journey_events:
        stage = journey_event.get("stage")
        if not stage:
            continue
        canonical_event_id = journey_event["canonical_event_id"]
        if owned_event_ids is not None and canonical_event_id not in owned_event_ids:
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
    owned_event_ids: "set[str] | None" = None,
) -> tuple[list[EventReceiptRow], list[IdentityLinkRow]]:
    by_canonical: dict[str, dict[str, Any]] = {}
    for journey_event in agg.journey_events:
        canonical_id = journey_event["canonical_event_id"]
        if owned_event_ids is not None and canonical_id not in owned_event_ids:
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
    accepted_event_ids: "set[str] | None" = None,
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
    owned_event_ids = (
        {entry["event"]["event_id"] for entry in reduced.entries}
        if accepted_event_ids is None
        else set(accepted_event_ids)
    )
    for agg in touched:
        receipts, identity_links = _receipts_and_identity_links(
            agg, projection_revision=projection_revision, owned_event_ids=owned_event_ids
        )
        mutation.receipts.extend(receipts)
        mutation.identity_links.extend(identity_links)
        mutation.stages.extend(
            _stage_rows_for_aggregate(
                agg, projection_revision=projection_revision, owned_event_ids=owned_event_ids
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
            # ``stage_batch`` reports a count but intentionally does not expose
            # accepted event IDs. Derive the bounded ownership set against only
            # the aggregates touched by this source window before staging. A
            # durable duplicate may share a journey and loop with a new event;
            # it must contribute to duplicate accounting and the source high
            # watermark, but it must not own a receipt, stage, aggregate, or
            # loop mutation in the transaction for the new event.
            accepted_event_ids: set[str] = set()
            for entry in reduced.entries:
                event_id = str(entry["event"]["event_id"])
                journey_id = str((entry.get("identity") or {}).get("journey_id") or "")
                current = self._materializer.aggregates.get(journey_id)
                if current is None or event_id not in current.event_fingerprints:
                    accepted_event_ids.add(event_id)
            staged, _affected, accepted, duplicates = self._materializer.stage_batch(
                reduced.entries
            )
            mutation = build_batch_mutation(
                staged,
                reduced,
                affected=_affected,
                accepted_event_ids=accepted_event_ids,
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


class LegacyBundleBackfillCoordinator:
    """Import an operator-accepted folded JSON baseline without full-file RAM.

    The legacy bundle is a projection, not reconstructed source telemetry. This
    path is therefore available only with an exact SHA-256 supplied by the
    operator evidence. It writes through the same transactional projection
    store, keeps a distinct migration controller, and seeds a non-live recovery
    cursor only after exact row-count and quarantine gates pass.
    """

    def __init__(
        self,
        store: ProjectionStore,
        *,
        controller_id: str,
        tenant_scope: str,
        environment_scope: str,
        controller_state_path: Path,
        expected_sha256: str,
        snapshot_path: Path,
        accepted_checkpoint: int | None = None,
        accepted_controller_deployment_sha: str = "",
        deployment_sha: str = "",
        batch_size: int = 100,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("legacy baseline batch_size must be positive")
        if len(expected_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in expected_sha256.lower()
        ):
            raise ValueError("legacy baseline expected_sha256 must be a full SHA-256")
        self.store = store
        self.controller_id = controller_id
        self.migration_controller_id = migration_controller_id(controller_id)
        self.tenant_scope = tenant_scope
        self.environment_scope = environment_scope
        self.controller_state_path = controller_state_path
        self.expected_sha256 = expected_sha256.lower()
        self.snapshot_path = snapshot_path
        self.accepted_checkpoint = accepted_checkpoint
        self.accepted_controller_deployment_sha = accepted_controller_deployment_sha
        self.deployment_sha = deployment_sha
        self.batch_size = batch_size

    def _read_snapshot(self) -> dict[str, Any]:
        if not self.snapshot_path.exists():
            return {}
        payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != LEGACY_BASELINE_SNAPSHOT_SCHEMA:
            raise ValueError("unsupported legacy baseline snapshot")
        if payload.get("source_sha256") != self.expected_sha256:
            raise ValueError("legacy baseline snapshot is bound to a different source checksum")
        return payload

    def _write_snapshot(self, payload: Mapping[str, Any]) -> None:
        rendered = {
            "schema_version": LEGACY_BASELINE_SNAPSHOT_SCHEMA,
            "source_sha256": self.expected_sha256,
            **dict(payload),
        }
        tmp = self.snapshot_path.with_name(self.snapshot_path.name + ".tmp")
        tmp.write_text(json.dumps(rendered, sort_keys=True), encoding="utf-8")
        tmp.replace(self.snapshot_path)

    def _accepted_controller(self) -> tuple[Mapping[str, Any], int]:
        if self.accepted_checkpoint is None:
            raise ValueError("reviewed legacy checkpoint is required")
        if not self.accepted_controller_deployment_sha:
            raise ValueError("reviewed legacy controller deployment SHA is required")
        controller = validate_legacy_controller_binding(
            self.controller_state_path,
            expected_controller_id=self.controller_id,
            expected_checkpoint=self.accepted_checkpoint,
            expected_deployment_sha=self.accepted_controller_deployment_sha,
        )
        return controller, self.accepted_checkpoint

    @staticmethod
    def _validate_aggregate(aggregate: BoundedAggregateState) -> None:
        if not aggregate.journey_events:
            raise ValueError(f"legacy aggregate {aggregate.journey_id!r} has no events")
        canonical_ids = {
            str(event.get("canonical_event_id") or "")
            for event in aggregate.journey_events
        }
        if "" in canonical_ids:
            raise ValueError(
                f"legacy aggregate {aggregate.journey_id!r} has a stage without canonical event ID"
            )
        if canonical_ids != set(aggregate.event_fingerprints):
            raise ValueError(
                f"legacy aggregate {aggregate.journey_id!r} fingerprint ownership mismatch"
            )
        if any(
            int(event.get("source_offset") or 0) <= 0
            for event in aggregate.journey_events
        ):
            raise ValueError(
                f"legacy aggregate {aggregate.journey_id!r} has a non-positive source offset"
            )

    def _apply_batch(
        self,
        aggregates: Sequence[BoundedAggregateState],
        *,
        checkpoint: int,
        projection_revision: int,
    ) -> dict[str, int]:
        mutation = BatchProjectionMutation(
            mode="backfill",
            status="backfilling",
            source_high_watermark=checkpoint,
            backlog_count=0,
            accepted_live=False,
            deployment_sha=self.deployment_sha,
        )
        counts = {"receipts": 0, "journeys": 0, "loop_runs": 0, "stages": 0}
        for aggregate in aggregates:
            self._validate_aggregate(aggregate)
            receipts, identity_links = _receipts_and_identity_links(
                aggregate, projection_revision=projection_revision
            )
            stages = _stage_rows_for_aggregate(
                aggregate, projection_revision=projection_revision
            )
            journey = _journey_row_for_aggregate(
                aggregate, projection_revision=projection_revision
            )
            loop_run = _loop_run_row_for_aggregate(
                aggregate, projection_revision=projection_revision
            )
            mutation.receipts.extend(receipts)
            mutation.identity_links.extend(identity_links)
            mutation.stages.extend(stages)
            if journey is not None:
                mutation.journeys.append(journey)
            if loop_run is not None:
                mutation.loop_runs.append(loop_run)
            counts["receipts"] += len(receipts)
            counts["journeys"] += int(journey is not None)
            counts["loop_runs"] += int(loop_run is not None)
            counts["stages"] += len(stages)
        self.store.execute_batch_transaction(
            self.migration_controller_id,
            self.tenant_scope,
            self.environment_scope,
            mutation,
        )
        return counts

    def run(self, *, max_batches: int | None = None) -> dict[str, Any]:
        actual_sha256 = sha256_file(self.controller_state_path)
        if actual_sha256 != self.expected_sha256:
            raise ValueError(
                "legacy controller-state checksum does not match the accepted evidence"
            )
        controller, checkpoint = self._accepted_controller()
        snapshot = self._read_snapshot()
        counts = {
            "aggregates": int(snapshot.get("aggregates") or 0),
            "receipts": int(snapshot.get("receipts") or 0),
            "journeys": int(snapshot.get("journeys") or 0),
            "loop_runs": int(snapshot.get("loop_runs") or 0),
            "stages": int(snapshot.get("stages") or 0),
            "batches": int(snapshot.get("batches") or 0),
        }
        last_journey_id = str(snapshot.get("last_journey_id") or "")

        if not bool(snapshot.get("import_complete")):
            pending: list[BoundedAggregateState] = []
            pending_last_id = ""
            batches_this_run = 0
            for journey_id, aggregate in iter_legacy_aggregates(
                self.controller_state_path
            ):
                if last_journey_id and journey_id <= last_journey_id:
                    continue
                if int(aggregate.last_ingested_seq or 0) > checkpoint:
                    raise ValueError(
                        f"legacy aggregate {journey_id!r} exceeds controller checkpoint"
                    )
                pending.append(aggregate)
                pending_last_id = journey_id
                if len(pending) < self.batch_size:
                    continue
                batch_counts = self._apply_batch(
                    pending,
                    checkpoint=checkpoint,
                    projection_revision=counts["batches"] + 1,
                )
                counts["aggregates"] += len(pending)
                counts["batches"] += 1
                for name, value in batch_counts.items():
                    counts[name] += value
                last_journey_id = pending_last_id
                self._write_snapshot(
                    {
                        **counts,
                        "checkpoint": checkpoint,
                        "last_journey_id": last_journey_id,
                        "import_complete": False,
                        "controller_deployment_sha": controller.get("deployment_sha"),
                    }
                )
                pending = []
                batches_this_run += 1
                if max_batches is not None and batches_this_run >= max_batches:
                    return {
                        **counts,
                        "checkpoint": checkpoint,
                        "source_sha256": actual_sha256,
                        "import_complete": False,
                        "live_controller_seeded": False,
                    }

            if pending:
                batch_counts = self._apply_batch(
                    pending,
                    checkpoint=checkpoint,
                    projection_revision=counts["batches"] + 1,
                )
                counts["aggregates"] += len(pending)
                counts["batches"] += 1
                for name, value in batch_counts.items():
                    counts[name] += value
                last_journey_id = pending_last_id
            if counts["aggregates"] <= 0 or counts["receipts"] <= 0:
                raise ValueError("legacy baseline contains no durable lifecycle aggregates")
            self._write_snapshot(
                {
                    **counts,
                    "checkpoint": checkpoint,
                    "last_journey_id": last_journey_id,
                    "import_complete": True,
                    "live_controller_seeded": False,
                    "controller_deployment_sha": controller.get("deployment_sha"),
                }
            )

        seeded = self.store.adopt_legacy_baseline(
            controller_id=self.controller_id,
            migration_controller_id=self.migration_controller_id,
            tenant_scope=self.tenant_scope,
            environment_scope=self.environment_scope,
            checkpoint_seq=checkpoint,
            deployment_sha=self.deployment_sha,
            expected_receipts=counts["receipts"],
            expected_journeys=counts["journeys"],
            expected_loop_runs=counts["loop_runs"],
        )
        self._write_snapshot(
            {
                **counts,
                "checkpoint": checkpoint,
                "last_journey_id": last_journey_id,
                "import_complete": True,
                "live_controller_seeded": True,
                "controller_deployment_sha": controller.get("deployment_sha"),
            }
        )
        return {
            **counts,
            "checkpoint": checkpoint,
            "source_sha256": actual_sha256,
            "import_complete": True,
            "live_controller_seeded": True,
            "live_controller_mode": seeded.mode,
            "live_controller_status": seeded.status,
            "accepted_live": seeded.accepted_live,
        }


# ---------------------------------------------------------------------------
# Deterministic old/new parity.
# ---------------------------------------------------------------------------


class StreamingMultisetDigest:
    """Order-independent, duplicate-sensitive digest for large row sets."""

    _MODULUS = 1 << 256

    def __init__(self) -> None:
        self.count = 0
        self._xor = 0
        self._sum = 0
        self._sum_squares = 0

    def update(self, row: Mapping[str, Any]) -> None:
        payload = json.dumps(
            dict(row), sort_keys=True, separators=(",", ":"), default=str
        )
        value = int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest(), "big")
        self.count += 1
        self._xor ^= value
        self._sum = (self._sum + value) % self._MODULUS
        self._sum_squares = (self._sum_squares + value * value) % self._MODULUS

    def hexdigest(self) -> str:
        summary = (
            f"{self.count}:{self._xor:064x}:{self._sum:064x}:"
            f"{self._sum_squares:064x}"
        )
        return hashlib.sha256(summary.encode("ascii")).hexdigest()


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
