"""Governed ingestion scheduling with replayable watermarks and DLQ routing."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import weakref
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, ClassVar, Iterable, Mapping
from uuid import uuid4

from services.external_egress import ExternalEgressBlocked
from services.foundation import (
    ActorRef,
    ActorType,
    AuditAction,
    DeadLetterEntry,
    DeadLetterQueue,
    EnvironmentName,
    EnvironmentScope,
    EventEnvelope,
    TraceContext,
)

from .connectors.base import IngestRun, IngestRunStatus, SourceEvidenceError, SourceRecord
from .connectors.collector_bundle import CollectorDelivery
from .ingest_manager import IngestManager
from .process_lock import exclusive_file_lock


@dataclass(frozen=True)
class SourceWatermark:
    connector_id: str
    source_type: str
    value: str | None
    updated_at: str
    last_ingest_run_id: str | None = None
    schema_version: str = "source_watermark.v1"

    def __post_init__(self) -> None:
        if not str(self.connector_id).strip():
            raise SourceEvidenceError("watermark connector_id is required")
        if not str(self.source_type).strip():
            raise SourceEvidenceError("watermark source_type is required")
        if not str(self.updated_at).strip():
            raise SourceEvidenceError("watermark updated_at is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "connector_id": self.connector_id,
            "source_type": self.source_type,
            "value": self.value,
            "updated_at": self.updated_at,
            "last_ingest_run_id": self.last_ingest_run_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceWatermark":
        return cls(
            connector_id=str(data["connector_id"]),
            source_type=str(data["source_type"]),
            value=data.get("value"),
            updated_at=str(data["updated_at"]),
            last_ingest_run_id=data.get("last_ingest_run_id"),
            schema_version=str(data.get("schema_version", "source_watermark.v1")),
        )


@dataclass(frozen=True)
class IngestBatch:
    records: tuple[SourceRecord, ...] | list[SourceRecord] = field(default_factory=tuple)
    next_watermark: str | None = None
    empty_ok: bool = False
    empty_reason: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    derived_records: tuple[SourceRecord, ...] = field(default_factory=tuple)
    collector_delivery: CollectorDelivery | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(self.records))
        object.__setattr__(self, "derived_records", tuple(self.derived_records))
        for record in (*self.records, *self.derived_records):
            if not isinstance(record, SourceRecord):
                raise SourceEvidenceError("ingest batch records must be SourceRecord instances")
        object.__setattr__(self, "empty_ok", bool(self.empty_ok))
        object.__setattr__(self, "metadata", dict(self.metadata))
        if self.empty_ok and not str(self.empty_reason or "").strip():
            raise SourceEvidenceError("ingest batch empty_reason is required when empty_ok is true")


@dataclass(frozen=True)
class CrawlFrontierItem:
    frontier_id: str
    connector_id: str
    status: str = "queued"
    trigger_type: str = "scheduled"
    trace_id: str | None = None
    attempts: int = 0
    max_attempts: int = 2
    available_at: str | None = None
    last_error: str | None = None
    ingest_run_id: str | None = None
    job_parameters: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    schema_version: str = "crawl_frontier_item.v1"

    VALID_STATUSES: ClassVar[set[str]] = {"queued", "running", "done", "failed", "retry"}

    def __post_init__(self) -> None:
        if not str(self.frontier_id).strip():
            raise SourceEvidenceError("frontier_id is required")
        if not str(self.connector_id).strip():
            raise SourceEvidenceError("frontier connector_id is required")
        if self.status not in self.VALID_STATUSES:
            raise SourceEvidenceError(f"frontier status must be one of: {', '.join(sorted(self.VALID_STATUSES))}")
        if self.attempts < 0:
            raise SourceEvidenceError("frontier attempts must be >= 0")
        if self.max_attempts < 1:
            raise SourceEvidenceError("frontier max_attempts must be >= 1")
        now = _utc_now()
        object.__setattr__(self, "created_at", self.created_at or now)
        object.__setattr__(self, "updated_at", self.updated_at or now)
        object.__setattr__(self, "job_parameters", dict(self.job_parameters))

    @classmethod
    def new(
        cls,
        *,
        connector_id: str,
        trigger_type: str = "scheduled",
        trace_id: str | None = None,
        max_attempts: int = 2,
        available_at: str | None = None,
        job_parameters: Mapping[str, Any] | None = None,
    ) -> "CrawlFrontierItem":
        return cls(
            frontier_id=f"frontier-{uuid4().hex[:12]}",
            connector_id=connector_id,
            trigger_type=trigger_type,
            trace_id=trace_id,
            max_attempts=max_attempts,
            available_at=available_at or _utc_now(),
            job_parameters=dict(job_parameters or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "frontier_id": self.frontier_id,
            "connector_id": self.connector_id,
            "status": self.status,
            "trigger_type": self.trigger_type,
            "trace_id": self.trace_id,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "available_at": self.available_at,
            "last_error": self.last_error,
            "ingest_run_id": self.ingest_run_id,
            "job_parameters": dict(self.job_parameters),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CrawlFrontierItem":
        return cls(
            frontier_id=str(data["frontier_id"]),
            connector_id=str(data["connector_id"]),
            status=str(data.get("status", "queued")),
            trigger_type=str(data.get("trigger_type", "scheduled")),
            trace_id=data.get("trace_id"),
            attempts=int(data.get("attempts", 0)),
            max_attempts=int(data.get("max_attempts", 2)),
            available_at=data.get("available_at"),
            last_error=data.get("last_error"),
            ingest_run_id=data.get("ingest_run_id"),
            job_parameters=dict(data.get("job_parameters") or {}),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            schema_version=str(data.get("schema_version", "crawl_frontier_item.v1")),
        )


@dataclass(frozen=True)
class ScheduledIngestResult:
    run: IngestRun
    watermark: SourceWatermark | None
    records: tuple[SourceRecord, ...]
    dlq_entries: tuple[DeadLetterEntry, ...] = field(default_factory=tuple)
    audit_actions: tuple[AuditAction, ...] = field(default_factory=tuple)
    frontier_id: str | None = None
    typed_failure: Mapping[str, Any] | None = None
    collector_delivery: CollectorDelivery | None = None


# Receipts reference updated market snapshots by identity; the bodies live in the
# latest market snapshot store. A body is dropped to keep a receipt line bounded.
SNAPSHOT_REF_SCHEMA_VERSION = "source_ingest_snapshot_ref.v1"
DEFAULT_SNAPSHOT_DATASET = "tw_price_daily"
JOURNAL_SLIM_MIN_LINE_BYTES = 16 * 1024
JOURNAL_WARN_BYTES = 256 * 1024 * 1024
# SOURCE-RECEIPT-REFS-ONLY: a receipt line holds counts and references only.
RECEIPT_REFS_SCHEMA_VERSION = "source_ingest_receipt_refs.v1"
RECEIPT_LINE_BUDGET_BYTES = 16 * 1024
JOURNAL_GROWTH_ALARM_BYTES_PER_HOUR = 5 * 1024 * 1024
DEFAULT_RECEIPT_RETENTION_DAYS = 30
RECEIPT_RETENTION_DAYS_ENV = "SOURCE_INGEST_RECEIPT_RETENTION_DAYS"
_ID_LIST_COUNT_KEYS = {
    "source_ids": "source_id_count",
    "evidence_item_ids": "evidence_item_id_count",
    "knowledge_object_ids": "knowledge_object_id_count",
}


def snapshot_ref(body: Mapping[str, Any]) -> dict[str, Any]:
    """Bounded reference to one updated snapshot: id, symbol, dataset, newest event time."""
    return {
        "snapshot_id": body.get("snapshot_id"),
        "symbol": body.get("symbol"),
        "dataset": body.get("dataset") or DEFAULT_SNAPSHOT_DATASET,
        "event_time": body.get("event_time"),
    }


def slim_market_snapshot_batch(batch: Any) -> Any:
    """Return ``batch`` with every snapshot body replaced by a reference.

    Idempotent; entries that already are references (no ``closes``) are kept. Counts
    and every other batch field are preserved. Non-mapping input is returned as is.
    """
    if not isinstance(batch, Mapping):
        return batch
    snapshots = batch.get("snapshots")
    if not isinstance(snapshots, list) or not any(
        isinstance(item, Mapping) and "closes" in item for item in snapshots
    ):
        return batch
    slim = dict(batch)
    slim["snapshots"] = [
        snapshot_ref(item) if isinstance(item, Mapping) and "closes" in item else item for item in snapshots
    ]
    return slim


def slim_receipt_payload(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], bool]:
    """Slim ``evidence_refs.market_snapshots`` of a receipt payload; keeps all other fields."""
    refs = payload.get("evidence_refs")
    if not isinstance(refs, Mapping):
        return payload, False
    batch = refs.get("market_snapshots")
    slim = slim_market_snapshot_batch(batch)
    if slim is batch:
        return payload, False
    return {**payload, "evidence_refs": {**refs, "market_snapshots": slim}}, True


def compact_receipt_evidence_refs(refs: Mapping[str, Any]) -> tuple[Mapping[str, Any], bool]:
    """Counts and references only: no per-record list survives in a receipt.

    ``source_ids`` / ``evidence_item_ids`` / ``knowledge_object_ids`` become counts (the
    evidence bundle id stays the reference to the items); ``distillation_admissions`` becomes
    a count and a status histogram; ``market_snapshots.snapshots`` becomes ``snapshot_count``
    plus one reference to the latest market snapshot store keyed by the ingest run id.
    Idempotent; every other field is kept.
    """
    out = dict(refs)
    changed = False
    for key, count_key in _ID_LIST_COUNT_KEYS.items():
        value = out.get(key)
        if isinstance(value, (list, tuple)):
            out[count_key] = len(value)
            del out[key]
            changed = True
    admissions = out.get("distillation_admissions")
    if isinstance(admissions, Mapping):
        histogram: dict[str, int] = {}
        for item in admissions.values():
            status = str(item.get("status") if isinstance(item, Mapping) else "unknown")
            histogram[status] = histogram.get(status, 0) + 1
        out["distillation_admission_count"] = len(admissions)
        out["distillation_admission_status_counts"] = dict(sorted(histogram.items()))
        del out["distillation_admissions"]
        changed = True
    batch = out.get("market_snapshots")
    if isinstance(batch, Mapping) and isinstance(batch.get("snapshots"), list):
        snapshots = batch["snapshots"]
        datasets = sorted({str(item.get("dataset") or DEFAULT_SNAPSHOT_DATASET)
                           for item in snapshots if isinstance(item, Mapping)})[:8]
        compact = {key: value for key, value in batch.items() if key != "snapshots"}
        compact["snapshot_count"] = len(snapshots)
        compact["snapshot_refs"] = {
            "schema_version": RECEIPT_REFS_SCHEMA_VERSION,
            "store": "latest_market_snapshots",
            "ingest_run_id": batch.get("ingest_run_id"),
            "datasets": datasets,
        }
        out["market_snapshots"] = compact
        changed = True
    return (out, True) if changed else (refs, False)


def compact_receipt_payload(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], bool]:
    """Receipt payload with counts and references only; idempotent; other fields untouched."""
    refs = payload.get("evidence_refs")
    if not isinstance(refs, Mapping):
        return payload, False
    compact, changed = compact_receipt_evidence_refs(refs)
    if not changed:
        return payload, False
    return {**payload, "evidence_refs": dict(compact)}, True


_TAIL_ID_RE = re.compile(rb'"record_id":"((?:[^"\\]|\\.)*)","record_type":"([a-z_]+)"')
_CREATED_AT_RE = re.compile(rb'"created_at":"([^"]+)"')


def receipt_retention_days(default: int = DEFAULT_RECEIPT_RETENTION_DAYS) -> int:
    raw = os.getenv(RECEIPT_RETENTION_DAYS_ENV)
    try:
        return max(0, int(raw)) if raw not in (None, "") else default
    except ValueError:
        return default


def _line_identity(line: bytes) -> tuple[str, str] | None:
    """(record_type, record_id) read from the tail of a sorted-key row without parsing it."""
    match = _TAIL_ID_RE.search(line[-400:])
    if match is None:
        return None
    return match.group(2).decode("ascii"), json.loads(b'"' + match.group(1) + b'"')


@dataclass
class JournalScan:
    """One streaming pass over a journal: what a rewrite must change. Holds ids only."""

    rows: int = 0
    bytes: int = 0
    oversize_receipt_lines: int = 0
    receipts: int = 0
    drop_ids: frozenset[str] = frozenset()
    pinned_ids: frozenset[str] = frozenset()

    @property
    def needs_rewrite(self) -> bool:
        return bool(self.oversize_receipt_lines or self.drop_ids)


def scan_journal(
    path: Path,
    *,
    retention_days: int,
    now: datetime | None = None,
    extra_pinned_run_ids: Iterable[str] = (),
) -> JournalScan:
    """Stream ``path`` once; decide which receipt rows are past retention and unpinned.

    Pinned receipts (never dropped): referenced by a watermark, by a frontier item, by a run
    that is not terminal, the newest run of each connector, or named in ``extra_pinned_run_ids``
    (lineage). Only small non-receipt rows are parsed; memory is bounded by the id sets.
    """
    scan = JournalScan()
    created: dict[str, str | None] = {}
    watermark_runs: dict[str, str | None] = {}
    frontier_runs: dict[str, str | None] = {}
    run_state: dict[str, tuple[str, str, str]] = {}
    try:
        handle = path.open("rb")
    except OSError:
        return scan
    with handle:
        while line := handle.readline():
            scan.rows += 1
            scan.bytes += len(line)
            ident = _line_identity(line) if line.endswith(b"\n") else None
            if ident is None:
                continue
            record_type, record_id = ident
            if record_type == "ingest_receipt":
                scan.receipts += 1
                if len(line) > RECEIPT_LINE_BUDGET_BYTES:
                    scan.oversize_receipt_lines += 1
                match = _CREATED_AT_RE.search(line, 0, 4096)
                created[record_id] = match.group(1).decode() if match else None
                continue
            if len(line) > 256 * 1024:
                continue
            try:
                payload = json.loads(line).get("payload") or {}
            except ValueError:
                continue
            if record_type == "source_watermark":
                watermark_runs[record_id] = payload.get("last_ingest_run_id")
            elif record_type == "crawl_frontier_item":
                frontier_runs[record_id] = payload.get("ingest_run_id")
            elif record_type == "ingest_run":
                run_state[record_id] = (
                    str(payload.get("status") or ""), str(payload.get("started_at") or ""),
                    str(payload.get("connector_id") or ""))
    pinned: set[str] = {str(v) for v in watermark_runs.values() if v}
    pinned.update(str(v) for v in frontier_runs.values() if v)
    pinned.update(str(v) for v in extra_pinned_run_ids if v)
    newest: dict[str, tuple[str, str]] = {}
    for run_id, (status, started, connector) in run_state.items():
        if status not in {"completed", "failed", "rejected"}:
            pinned.add(run_id)
        if connector and (connector not in newest or started >= newest[connector][0]):
            newest[connector] = (started, run_id)
    pinned.update(run_id for _, run_id in newest.values())
    scan.pinned_ids = frozenset(pinned)
    if retention_days > 0:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
        cutoff_text = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
        scan.drop_ids = frozenset(
            rid for rid, stamp in created.items()
            if stamp and stamp[:19] < cutoff_text and rid not in pinned
        )
    return scan


def rewrite_journal_line(line: bytes, drop_ids: frozenset[str]) -> bytes | None:
    """The row to write for ``line``: None drops it, receipts are compacted, others are kept as is."""
    if b'"record_type":"ingest_receipt"' not in line[-400:] or not line.endswith(b"\n"):
        return line
    ident = _line_identity(line)
    if ident is not None and ident[1] in drop_ids:
        return None
    if len(line) <= RECEIPT_LINE_BUDGET_BYTES:
        return line
    try:
        entry = json.loads(line.decode("utf-8"))
        payload = entry.get("payload")
        if not isinstance(payload, Mapping):
            return line
        new_payload, changed = compact_receipt_payload(payload)
    except (ValueError, AttributeError):
        return line
    if not changed:
        return line
    return (_entry_line({**entry, "payload": dict(new_payload)}) + "\n").encode("utf-8")


def _entry_line(entry: Mapping[str, Any]) -> str:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class IngestReceipt:
    """Durable, secret-free result receipt for one governed source run."""

    ingest_run_id: str
    connector_id: str
    status: str
    trigger_type: str
    trace_id: str
    started_at: str
    finished_at: str | None
    raw_count: int
    normalized_count: int
    rejected_count: int
    watermark: str | None
    source_timestamp: str | None
    source_timestamp_status: str = "unknown"
    evidence_refs: Mapping[str, Any] = field(default_factory=dict)
    storage_refs: Mapping[str, Any] = field(default_factory=dict)
    typed_failure: Mapping[str, Any] | None = None
    created_at: str = field(default_factory=lambda: _utc_now())
    schema_version: str = "source_ingest_receipt.v1"

    def __post_init__(self) -> None:
        for field_name in ("ingest_run_id", "connector_id", "status", "trigger_type", "trace_id", "started_at"):
            if not str(getattr(self, field_name) or "").strip():
                raise SourceEvidenceError(f"receipt {field_name} is required")
        for field_name in ("raw_count", "normalized_count", "rejected_count"):
            if int(getattr(self, field_name)) < 0:
                raise SourceEvidenceError(f"receipt {field_name} must be >= 0")
        if self.source_timestamp_status not in {"valid", "missing", "invalid", "future", "unknown"}:
            raise SourceEvidenceError("receipt source_timestamp_status is invalid")
        object.__setattr__(self, "evidence_refs", dict(self.evidence_refs))
        object.__setattr__(self, "storage_refs", dict(self.storage_refs))
        if self.typed_failure is not None:
            object.__setattr__(self, "typed_failure", dict(self.typed_failure))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ingest_run_id": self.ingest_run_id,
            "connector_id": self.connector_id,
            "status": self.status,
            "trigger_type": self.trigger_type,
            "trace_id": self.trace_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "raw_count": self.raw_count,
            "normalized_count": self.normalized_count,
            "rejected_count": self.rejected_count,
            "watermark": self.watermark,
            "source_timestamp": self.source_timestamp,
            "source_timestamp_status": self.source_timestamp_status,
            "evidence_refs": dict(self.evidence_refs),
            "storage_refs": dict(self.storage_refs),
            "typed_failure": dict(self.typed_failure) if self.typed_failure is not None else None,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IngestReceipt":
        return cls(
            ingest_run_id=str(data["ingest_run_id"]),
            connector_id=str(data["connector_id"]),
            status=str(data["status"]),
            trigger_type=str(data["trigger_type"]),
            trace_id=str(data["trace_id"]),
            started_at=str(data["started_at"]),
            finished_at=data.get("finished_at"),
            raw_count=int(data.get("raw_count") or 0),
            normalized_count=int(data.get("normalized_count") or 0),
            rejected_count=int(data.get("rejected_count") or 0),
            watermark=data.get("watermark"),
            source_timestamp=data.get("source_timestamp"),
            source_timestamp_status=str(
                data.get("source_timestamp_status")
                or ("valid" if data.get("source_timestamp") else "unknown")
            ),
            evidence_refs=dict(data.get("evidence_refs") or {}),
            storage_refs=dict(data.get("storage_refs") or {}),
            typed_failure=dict(data["typed_failure"]) if isinstance(data.get("typed_failure"), Mapping) else None,
            created_at=str(data.get("created_at") or _utc_now()),
            schema_version=str(data.get("schema_version") or "source_ingest_receipt.v1"),
        )


class JsonlIngestScheduleStore:
    """Append/replay store for ingest-run state and connector watermarks."""

    _live_by_path: ClassVar[dict[str, "weakref.ReferenceType[JsonlIngestScheduleStore]"]] = {}

    def __init__(
        self,
        path: str | Path,
        *,
        retention_days: int | None = None,
        pinned_run_ids: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self.path = Path(path)
        # Receipts older than this many days expire unless a run, watermark, frontier item or
        # lineage (``pinned_run_ids``) still references them; 0 keeps every receipt.
        self.retention_days = receipt_retention_days() if retention_days is None else max(0, int(retention_days))
        self._pinned_run_ids = pinned_run_ids
        self._last_retention_check = time.monotonic()
        self._growth_samples: deque[tuple[float, int]] = deque(maxlen=512)
        self._max_receipt_line = 0
        self._oversize_receipt_lines = 0
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._lock = threading.RLock()
        self._runs: dict[str, IngestRun] = {}
        self._watermarks: dict[str, SourceWatermark] = {}
        self._frontier: dict[str, CrawlFrontierItem] = {}
        self._receipts: dict[str, IngestReceipt] = {}
        self._read_key: tuple[int, int, int] | None = (-1, -1, -1)
        self._read_offset = 0
        self._read_line_no = 0
        self._last_slim: dict[str, Any] | None = None
        self.reload()
        JsonlIngestScheduleStore._live_by_path[str(self.path)] = weakref.ref(self)

    def reload(self) -> None:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._slim_journal_unlocked()
            self._reload_unlocked(recover_incomplete=True)

    def _slim_journal_unlocked(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Crash-safe in-place rewrite: receipts become counts and references; old ones expire.

        One streaming scan decides what changes (receipt rows over the line budget, receipt rows
        past ``retention_days`` that no run, watermark, frontier item or lineage pin references).
        Nothing to change: the journal is not touched. Otherwise the journal streams to a sibling
        temp file, fsyncs, then atomically replaces the journal. Every state on disk (old file,
        temp file, new file) is a readable journal; a leftover temp from a crash is discarded.
        Every non-receipt row is preserved byte for byte. A failed rewrite leaves the journal
        untouched (replay still compacts in memory).
        """
        tmp = self.path.with_name(self.path.name + ".slim.tmp")
        stats = {"rewritten": False, "rows": 0, "slimmed_rows": 0, "expired_rows": 0,
                 "bytes_before": 0, "bytes_after": 0}
        try:
            tmp.unlink()
        except OSError:
            pass
        try:
            size = self.path.stat().st_size
        except OSError:
            return stats
        if size == 0:
            return stats
        pins = tuple(self._pinned_run_ids() or ()) if self._pinned_run_ids else ()
        scan = scan_journal(self.path, retention_days=self.retention_days, now=now, extra_pinned_run_ids=pins)
        stats["bytes_before"] = size
        if not scan.needs_rewrite:
            return stats
        slimmed = expired = 0
        try:
            with self.path.open("rb") as src, tmp.open("wb") as dst:
                while line := src.readline():
                    out = rewrite_journal_line(line, scan.drop_ids)
                    if out is None:
                        expired += 1
                        continue
                    if out is not line:
                        slimmed += 1
                    dst.write(out)
                    stats["rows"] += 1
                dst.flush()
                os.fsync(dst.fileno())
            os.replace(tmp, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            return stats
        stats.update(rewritten=True, slimmed_rows=slimmed, expired_rows=expired,
                     bytes_after=self.path.stat().st_size)
        self._last_slim = stats
        self._growth_samples.clear()
        return stats

    def enforce_retention_unlocked(self, *, now: datetime | None = None) -> dict[str, Any]:
        stats = self._slim_journal_unlocked(now=now)
        self._last_retention_check = time.monotonic()
        if stats["rewritten"]:
            self._reload_unlocked(recover_incomplete=False)
        return stats

    def enforce_retention(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Apply receipt retention now (also done on start and once a day on the write path)."""
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            stats = self._slim_journal_unlocked(now=now)
            self._last_retention_check = time.monotonic()
            if stats["rewritten"]:
                self._reload_unlocked(recover_incomplete=False)
            return stats

    @classmethod
    def journal_stats_for_path(cls, path: str | Path) -> dict[str, Any]:
        """``journal_stats`` of the live store for ``path`` without touching a runtime store handle.

        Readiness probes must not replay or call stores; this reads one stat and counters.
        """
        live = cls._live_by_path.get(str(Path(path)))
        store = live() if live is not None else None
        if store is not None:
            return store.journal_stats()
        try:
            size = Path(path).stat().st_size
        except OSError:
            size = 0
        return {"journal_bytes": size, "journal_lines": None, "receipt_count": None, "run_count": None,
                "warn_bytes": JOURNAL_WARN_BYTES,
                "warning": "ingest_schedule_journal_large" if size >= JOURNAL_WARN_BYTES else None,
                "receipt_line_budget_bytes": RECEIPT_LINE_BUDGET_BYTES, "alarms": []}

    def journal_stats(self) -> dict[str, Any]:
        """Journal size and receipt count readback; no replay, no file scan."""
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0
        self._sample_growth(size)
        growth = self._growth_last_hour(size)
        alarms = []
        if self._oversize_receipt_lines:
            alarms.append("ingest_receipt_line_over_16kb")
        if growth > JOURNAL_GROWTH_ALARM_BYTES_PER_HOUR:
            alarms.append("ingest_schedule_journal_growth_over_5mb_per_hour")
        stats: dict[str, Any] = {
            "journal_bytes": size,
            "journal_lines": self._read_line_no,
            "receipt_count": len(self._receipts),
            "run_count": len(self._runs),
            "warn_bytes": JOURNAL_WARN_BYTES,
            "warning": "ingest_schedule_journal_large" if size >= JOURNAL_WARN_BYTES else None,
            "receipt_line_budget_bytes": RECEIPT_LINE_BUDGET_BYTES,
            "max_receipt_line_bytes": self._max_receipt_line,
            "receipt_lines_over_budget": self._oversize_receipt_lines,
            "growth_bytes_last_hour": growth,
            "growth_alarm_bytes_per_hour": JOURNAL_GROWTH_ALARM_BYTES_PER_HOUR,
            "retention_days": self.retention_days,
            "alarms": alarms,
        }
        if self._last_slim:
            stats["last_slim_rewrite"] = dict(self._last_slim)
        return stats

    def _sample_growth(self, size: int) -> None:
        now = time.monotonic()
        if not self._growth_samples or now - self._growth_samples[-1][0] >= 30:
            self._growth_samples.append((now, size))

    def _growth_last_hour(self, size: int) -> int:
        now = time.monotonic()
        window = [(t, b) for t, b in self._growth_samples if now - t <= 3600]
        return max(0, size - window[0][1]) if window else 0

    def _stat_key(self) -> tuple[int, int, int] | None:
        try:
            stat = self.path.stat()
            return (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        except OSError:
            return None

    def _reload_if_changed_unlocked(self) -> None:
        """Catch up under the transaction lock only when the journal changed."""
        if self._stat_key() != self._read_key:
            self._reload_unlocked(recover_incomplete=False)

    def _reload_unlocked(self, *, recover_incomplete: bool) -> None:
        key = self._stat_key()
        # Append-only growth of the same inode preserves the consumed prefix.
        # Explicit reload/startup, replacement, shrink, and same-size rewrites
        # must rebuild all four projections instead.
        if (
            recover_incomplete
            or key is None
            or self._read_key is None
            or key[0] != self._read_key[0]
            or key[1] < self._read_key[1]
            or (key != self._read_key and key[1] == self._read_key[1])
        ):
            self._runs = {}
            self._watermarks = {}
            self._frontier = {}
            self._receipts = {}
            self._read_offset = 0
            self._read_line_no = 0
            self._read_key = None
            self._max_receipt_line = 0
            self._oversize_receipt_lines = 0
        if key is not None:
            # Binary offsets remain correct even if a writer stops within a
            # multibyte character. Only newline-terminated rows are consumed.
            with self.path.open("rb") as handle:
                handle.seek(self._read_offset)
                while line := handle.readline():
                    if not line.endswith(b"\n"):
                        break
                    self._apply_line_unlocked(line, self._read_line_no + 1)
                    self._read_offset = handle.tell()
                    self._read_line_no += 1
        self._read_key = key
        if recover_incomplete:
            self._recover_incomplete_receipts()

    def _apply_line_unlocked(self, line: bytes, line_no: int) -> None:
        if not line.strip():
            return
        try:
            entry = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise SourceEvidenceError(f"Invalid ingest schedule JSONL at {self.path}:{line_no}: {exc.msg}") from exc
        record_type = str(entry.get("record_type") or "")
        payload = entry.get("payload")
        if not isinstance(payload, Mapping):
            raise SourceEvidenceError(f"Invalid ingest schedule payload at {self.path}:{line_no}")
        if record_type == "ingest_run":
            run = IngestRun.from_dict(payload)
            self._runs[run.ingest_run_id] = run
        elif record_type == "source_watermark":
            watermark = SourceWatermark.from_dict(payload)
            self._watermarks[watermark.connector_id] = watermark
        elif record_type == "crawl_frontier_item":
            item = CrawlFrontierItem.from_dict(payload)
            self._frontier[item.frontier_id] = item
        elif record_type == "ingest_receipt":
            self._note_receipt_line(len(line))
            payload, _ = compact_receipt_payload(payload)
            receipt = IngestReceipt.from_dict(payload)
            self._receipts[receipt.ingest_run_id] = receipt
        else:
            raise SourceEvidenceError(f"Unsupported ingest schedule record: {record_type or '<missing>'}")

    def _note_receipt_line(self, size: int) -> None:
        self._max_receipt_line = max(self._max_receipt_line, size)
        if size > RECEIPT_LINE_BUDGET_BYTES:
            self._oversize_receipt_lines += 1

    def _recover_incomplete_receipts(self) -> list[IngestReceipt]:
        """Durably terminalize receipts stranded by a post-processing crash.

        A ``processing`` receipt is written only after the scheduler has made
        the ingest run terminal.  Seeing that pair during replay therefore
        proves that post-processing did not publish its final receipt.  Append
        a secret-free typed failure before exposing the reloaded store so a
        restart cannot leave completed run truth paired with a permanently
        nonterminal receipt.
        """

        recovered: list[IngestReceipt] = []
        for ingest_run_id, receipt in list(self._receipts.items()):
            run = self._runs.get(ingest_run_id)
            if receipt.status != "processing" or run is None or run.status != IngestRunStatus.COMPLETED:
                continue
            run_payload = run.to_dict()
            terminal_receipt = IngestReceipt(
                ingest_run_id=receipt.ingest_run_id,
                connector_id=receipt.connector_id,
                status="failed",
                trigger_type=receipt.trigger_type,
                trace_id=receipt.trace_id,
                started_at=receipt.started_at,
                finished_at=run_payload.get("finished_at") or receipt.finished_at,
                raw_count=receipt.raw_count,
                normalized_count=receipt.normalized_count,
                rejected_count=receipt.rejected_count,
                watermark=receipt.watermark,
                source_timestamp=receipt.source_timestamp,
                source_timestamp_status=receipt.source_timestamp_status,
                evidence_refs=receipt.evidence_refs,
                storage_refs=receipt.storage_refs,
                typed_failure={
                    "schema_version": "source_ingest_typed_failure.v1",
                    "category": "persistence",
                    "code": "post_processing_interrupted",
                    "error_type": "IncompleteReceiptRecovered",
                    "retryable": True,
                    "stage": "restart_recovery",
                },
            )
            self._append("ingest_receipt", terminal_receipt.ingest_run_id, terminal_receipt.to_dict())
            self._receipts[ingest_run_id] = terminal_receipt
            recovered.append(terminal_receipt)
        return recovered

    def upsert_run(self, run: IngestRun) -> IngestRun:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            self._append("ingest_run", run.ingest_run_id, run.to_dict())
            self._runs[run.ingest_run_id] = run
            return run

    def update_watermark(self, watermark: SourceWatermark) -> SourceWatermark:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            self._append("source_watermark", watermark.connector_id, watermark.to_dict())
            self._watermarks[watermark.connector_id] = watermark
            return watermark

    def get_run(self, ingest_run_id: str) -> IngestRun | None:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            return self._runs.get(ingest_run_id)

    def get_job(self, ingest_run_id: str) -> tuple[IngestRun | None, IngestReceipt | None]:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            run = self._runs.get(ingest_run_id)
            if run is None:
                return None, None
            return run, self._receipts.get(ingest_run_id)

    def list_runs(self) -> list[IngestRun]:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            return list(self._runs.values())

    def list_runs_page(
        self,
        *,
        statuses: frozenset[str] | None = None,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[IngestRun], list[IngestReceipt], int]:
        """Bounded run listing: newest first, optional status filter, one journal replay.

        Returns ``(runs_page, receipts_for_those_runs, total_matching)`` so a
        caller never has to materialise every run and receipt.
        """
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            runs = list(self._runs.values())
            if statuses:
                runs = [run for run in runs if str(getattr(run.status, "value", run.status)).lower() in statuses]
            run_positions = {run_id: pos for pos, run_id in enumerate(self._runs)}
            runs.sort(
                key=lambda run: (str(run.started_at), run_positions[run.ingest_run_id]),
                reverse=True,
            )
            total = len(runs)
            page = runs[offset : offset + limit]
            receipts = [self._receipts[run.ingest_run_id] for run in page if run.ingest_run_id in self._receipts]
            return page, receipts, total

    def upsert_receipt(self, receipt: IngestReceipt) -> IngestReceipt:
        # The durable receipt carries references, never snapshot bodies; the caller
        # keeps its own full batch. Memory and journal hold the identical slim form.
        compact_refs, changed = compact_receipt_evidence_refs(receipt.evidence_refs)
        if changed:
            receipt = replace(receipt, evidence_refs=dict(compact_refs))
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            self._append("ingest_receipt", receipt.ingest_run_id, receipt.to_dict())
            self._receipts[receipt.ingest_run_id] = receipt
            if self.retention_days and time.monotonic() - self._last_retention_check > 86400:
                self.enforce_retention_unlocked()
            return receipt

    def get_receipt(self, ingest_run_id: str) -> IngestReceipt | None:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            return self._receipts.get(ingest_run_id)

    def _sort_receipts_unlocked(self, receipts: Iterable[IngestReceipt]) -> list[IngestReceipt]:
        """Order by timestamp; ties by durable run position, then receipt position, then id."""
        run_positions = {run_id: pos for pos, run_id in enumerate(self._runs)}
        receipt_positions = {run_id: pos for pos, run_id in enumerate(self._receipts)}

        def key(receipt: IngestReceipt) -> tuple[Any, ...]:
            ts = receipt.finished_at or receipt.started_at
            run_pos = run_positions.get(receipt.ingest_run_id)
            if run_pos is not None:
                return (ts, 0, run_pos, receipt.ingest_run_id)
            return (ts, 1, receipt_positions.get(receipt.ingest_run_id, 0), receipt.ingest_run_id)

        return sorted(receipts, key=key)

    def list_receipts(self, *, connector_id: str | None = None) -> list[IngestReceipt]:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            receipts = list(self._receipts.values())
            if connector_id is not None:
                receipts = [receipt for receipt in receipts if receipt.connector_id == connector_id]
            return self._sort_receipts_unlocked(receipts)

    def read_freshness_snapshot(self) -> dict[str, Any]:
        """Read all freshness inputs from one replay of the durable journal.

        Health aggregation is fleet-wide. Calling the point lookup methods for
        every connector replays the same JSONL file once per lookup and turns a
        linear read into connector-count multiplied by journal-size work. This
        snapshot keeps the existing cross-process refresh semantics while
        giving callers one immutable-by-convention view to aggregate.
        """

        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            receipts = self._sort_receipts_unlocked(self._receipts.values())
            return {
                "runs": tuple(self._runs.values()),
                "receipts": tuple(receipts),
                "watermarks": dict(self._watermarks),
            }

    def get_watermark(self, connector_id: str) -> SourceWatermark | None:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            return self._watermarks.get(connector_id)

    def enqueue_frontier(
        self,
        *,
        connector_id: str,
        trigger_type: str = "scheduled",
        trace_id: str | None = None,
        max_attempts: int = 2,
        available_at: str | None = None,
        job_parameters: Mapping[str, Any] | None = None,
    ) -> CrawlFrontierItem:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            for item in self._frontier.values():
                if (
                    item.connector_id == connector_id
                    and item.status in {"queued", "running", "retry"}
                    and dict(item.job_parameters) == dict(job_parameters or {})
                ):
                    return item
            item = CrawlFrontierItem.new(
                connector_id=connector_id,
                trigger_type=trigger_type,
                trace_id=trace_id,
                max_attempts=max_attempts,
                available_at=available_at,
                job_parameters=job_parameters,
            )
            self._append("crawl_frontier_item", item.frontier_id, item.to_dict())
            self._frontier[item.frontier_id] = item
            return item

    def list_frontier(self, status: str | None = None) -> list[CrawlFrontierItem]:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            return self._list_frontier_unlocked(status=status)

    def _list_frontier_unlocked(self, status: str | None = None) -> list[CrawlFrontierItem]:
        items = list(self._frontier.values())
        if status:
            items = [item for item in items if item.status == status]
        return sorted(items, key=lambda item: (item.available_at or item.created_at, item.frontier_id))

    def get_frontier(self, frontier_id: str) -> CrawlFrontierItem | None:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            return self._frontier.get(frontier_id)

    def claim_due_frontier(
        self,
        *,
        limit: int,
        now: str | None = None,
        connector_ids: Iterable[str] | None = None,
    ) -> list[CrawlFrontierItem]:
        if limit < 1:
            raise SourceEvidenceError("frontier claim limit must be >= 1")
        allowed_connector_ids = (
            None
            if connector_ids is None
            else {str(connector_id).strip() for connector_id in connector_ids if str(connector_id).strip()}
        )
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            claimed: list[CrawlFrontierItem] = []
            now_dt = _parse_utc(now or _utc_now())
            for item in self._list_frontier_unlocked():
                if len(claimed) >= limit:
                    break
                if item.status not in {"queued", "retry"}:
                    continue
                if allowed_connector_ids is not None and item.connector_id not in allowed_connector_ids:
                    continue
                if item.available_at and _parse_utc(item.available_at) > now_dt:
                    continue
                claimed_item = self._claimed_frontier(item)
                self._append("crawl_frontier_item", claimed_item.frontier_id, claimed_item.to_dict())
                self._frontier[item.frontier_id] = claimed_item
                claimed.append(claimed_item)
            return claimed

    def claim_frontier(self, frontier_id: str, *, now: str | None = None) -> CrawlFrontierItem:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            item = self._require_frontier(frontier_id)
            if item.status not in {"queued", "retry"}:
                raise SourceEvidenceError(f"frontier item is not claimable: {frontier_id} status={item.status}")
            now_dt = _parse_utc(now or _utc_now())
            if item.available_at and _parse_utc(item.available_at) > now_dt:
                raise SourceEvidenceError(f"frontier item is not yet available: {frontier_id}")
            claimed_item = self._claimed_frontier(item)
            self._append("crawl_frontier_item", claimed_item.frontier_id, claimed_item.to_dict())
            self._frontier[item.frontier_id] = claimed_item
            return claimed_item

    @staticmethod
    def _claimed_frontier(item: CrawlFrontierItem) -> CrawlFrontierItem:
        return CrawlFrontierItem(
            **{
                **item.to_dict(),
                "status": "running",
                "attempts": item.attempts + 1,
                "updated_at": _utc_now(),
            }
        )

    def complete_frontier(self, frontier_id: str, *, ingest_run_id: str) -> CrawlFrontierItem:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            item = self._require_frontier(frontier_id)
            updated = CrawlFrontierItem(
                **{
                    **item.to_dict(),
                    "status": "done",
                    "available_at": None,
                    "last_error": None,
                    "ingest_run_id": ingest_run_id,
                    "updated_at": _utc_now(),
                }
            )
            self._append("crawl_frontier_item", frontier_id, updated.to_dict())
            self._frontier[frontier_id] = updated
            return updated

    def replay_frontier(
        self,
        frontier_id: str,
        *,
        trace_id: str | None = None,
        trigger_type: str = "dlq_replay",
        available_at: str | None = None,
    ) -> CrawlFrontierItem:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            item = self._require_frontier(frontier_id)
            if item.status not in {"failed", "retry"}:
                raise SourceEvidenceError(f"frontier item is not replayable: {frontier_id} status={item.status}")
            updated = CrawlFrontierItem(
                **{
                    **item.to_dict(),
                    "status": "retry",
                    "trigger_type": trigger_type,
                    "trace_id": trace_id or item.trace_id,
                    "max_attempts": max(item.max_attempts, item.attempts + 1),
                    "available_at": available_at or _utc_now(),
                    "last_error": None,
                    "updated_at": _utc_now(),
                }
            )
            self._append("crawl_frontier_item", frontier_id, updated.to_dict())
            self._frontier[frontier_id] = updated
            return updated

    def fail_frontier(
        self,
        frontier_id: str,
        *,
        error: str,
        backoff_seconds: int,
        ingest_run_id: str | None = None,
    ) -> CrawlFrontierItem:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            item = self._require_frontier(frontier_id)
            terminal = item.attempts >= item.max_attempts
            updated = CrawlFrontierItem(
                **{
                    **item.to_dict(),
                    "status": "failed" if terminal else "retry",
                    "available_at": None if terminal else _utc_after(backoff_seconds),
                    "last_error": error,
                    "ingest_run_id": ingest_run_id or item.ingest_run_id,
                    "updated_at": _utc_now(),
                }
            )
            self._append("crawl_frontier_item", frontier_id, updated.to_dict())
            self._frontier[frontier_id] = updated
            return updated

    def recover_stale_running(
        self,
        *,
        timeout_seconds: int,
        now: str | None = None,
    ) -> list[CrawlFrontierItem]:
        """Durably fence crash-stranded running work before scheduling again."""

        if timeout_seconds < 1:
            raise SourceEvidenceError("frontier running timeout must be >= 1")
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_if_changed_unlocked()
            now_value = now or _utc_now()
            now_dt = _parse_utc(now_value)
            recovered: list[CrawlFrontierItem] = []
            for item in self._list_frontier_unlocked(status="running"):
                if (now_dt - _parse_utc(item.updated_at)).total_seconds() < timeout_seconds:
                    continue
                terminal = item.attempts >= item.max_attempts
                updated = CrawlFrontierItem(
                    **{
                        **item.to_dict(),
                        "status": "failed" if terminal else "retry",
                        "available_at": None if terminal else now_value,
                        "last_error": "stale running frontier recovered after worker restart",
                        "updated_at": now_value,
                    }
                )
                self._append("crawl_frontier_item", item.frontier_id, updated.to_dict())
                self._frontier[item.frontier_id] = updated
                recovered.append(updated)
            return recovered

    def _require_frontier(self, frontier_id: str) -> CrawlFrontierItem:
        item = self._frontier.get(frontier_id)
        if item is None:
            raise SourceEvidenceError(f"Unknown crawl frontier item: {frontier_id}")
        return item

    def _append(self, record_type: str, record_id: str, payload: Mapping[str, Any]) -> None:
        if self._read_key is not None and self._read_offset != self._read_key[1]:
            raise SourceEvidenceError(f"Incomplete ingest schedule JSONL tail at {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file_preexisted = self.path.exists()
        entry = {
            "schema_version": "ingest_schedule_store.v1",
            "record_type": record_type,
            "record_id": record_id,
            "payload": dict(payload),
        }
        row = json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
        if record_type == "ingest_receipt":
            self._note_receipt_line(len(row.encode("utf-8")))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(row)
            handle.flush()
            os.fsync(handle.fileno())
            stat = os.fstat(handle.fileno())
        if not file_preexisted:
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        # The caller applies this row to its projection before releasing the
        # same exclusive lock. A subsequent read need not deserialize it again.
        self._read_offset = stat.st_size
        self._read_line_no += 1
        self._sample_growth(stat.st_size)
        self._read_key = (stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_after(seconds: int) -> str:
    bounded_seconds = max(0, int(seconds))
    return (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=bounded_seconds)).isoformat().replace(
        "+00:00", "Z"
    )


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def typed_failure_for_exception(exc: Exception) -> dict[str, Any]:
    """Map an exception to a stable, secret-free failure classification."""

    if isinstance(exc, ExternalEgressBlocked):
        payload = exc.to_dict()
        return {
            "schema_version": "source_ingest_typed_failure.v1",
            "category": "external_egress",
            "code": payload["code"],
            "error_type": type(exc).__name__,
            "retryable": payload["code"] == "dns_resolution_failed",
            "egress_denial": payload,
        }
    error_type = type(exc).__name__
    lowered = error_type.lower()
    if "credential" in lowered or "auth" in lowered:
        category, code, retryable = "credential", "credential_unavailable", False
    elif "quota" in lowered or "ratelimit" in lowered:
        category, code, retryable = "provider", "provider_quota_or_rate_limit", True
    elif isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        category, code, retryable = "network", "network_transport_failure", True
    elif isinstance(exc, SourceEvidenceError):
        category, code, retryable = "validation", "source_evidence_rejected", False
    else:
        category, code, retryable = "provider", "provider_fetch_failed", True
    return {
        "schema_version": "source_ingest_typed_failure.v1",
        "category": category,
        "code": code,
        "error_type": error_type,
        "retryable": retryable,
    }


class IngestionScheduler:
    """Runs governed batch ingestion from persisted watermarks."""

    def __init__(
        self,
        *,
        manager: IngestManager,
        store: JsonlIngestScheduleStore,
        dead_letter_queue: DeadLetterQueue,
        environment: EnvironmentScope | None = None,
        actor_ref: ActorRef | None = None,
        max_attempts: int = 2,
    ) -> None:
        if max_attempts < 1:
            raise SourceEvidenceError("max_attempts must be >= 1")
        self.manager = manager
        self.store = store
        self.dead_letter_queue = dead_letter_queue
        self.environment = environment or EnvironmentScope(EnvironmentName.PAPER, timezone="UTC")
        self.actor_ref = actor_ref or ActorRef(ActorType.SERVICE, "source-ingestion-scheduler", roles=("source_ingest",))
        self.max_attempts = max_attempts

    def run_once(
        self,
        *,
        connector_id: str,
        fetch_batch: Callable[[str | None], IngestBatch | Iterable[SourceRecord]],
        trace_id: str,
        trigger_type: str = "scheduled",
        frontier_id: str | None = None,
        defer_commit: bool = False,
    ) -> ScheduledIngestResult:
        connector = self.manager.get_connector(connector_id)
        if connector is None:
            raise SourceEvidenceError(f"Unknown connector: {connector_id}")

        run = self.manager.start_ingest_run(
            connector_id=connector_id,
            trigger_type=trigger_type,
            trace_id=trace_id,
        )
        self.store.upsert_run(run)
        starting_watermark = self.store.get_watermark(connector_id)
        last_error: Exception | None = None
        last_typed_failure: Mapping[str, Any] | None = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                fetched = fetch_batch(starting_watermark.value if starting_watermark else None)
                batch = fetched if isinstance(fetched, IngestBatch) else IngestBatch(records=tuple(fetched))
                for record in batch.records:
                    if record.connector_id != connector_id:
                        raise SourceEvidenceError("fetched record connector_id must match job connector")
                    if record.source_type != connector.source_type:
                        raise SourceEvidenceError("fetched record source_type must match job connector")
                if batch.derived_records and not isinstance(batch.collector_delivery, CollectorDelivery):
                    raise SourceEvidenceError("derived records require a verified Collector delivery")
                if batch.collector_delivery is not None:
                    # The adapter emits derived rows first. Validate its in-process
                    # proof before composing the records for the existing writer.
                    batch.collector_delivery.validate((*batch.derived_records, *batch.records))
                    expected_dataset = {"macro": "tw_macro_series", "social": "tw_community_daily"}.get(
                        connector.source_type.value)
                    if any(r.connector_id != connector_id or r.source_type.value != "market"
                           or r.metadata.get("dataset") != expected_dataset for r in batch.derived_records):
                        raise SourceEvidenceError("derived record connector/type/dataset mismatch")
                delivery = batch.collector_delivery
                batch = replace(batch, records=(*batch.derived_records, *batch.records), derived_records=())
                run.raw_count = len(batch.records)
                run.normalized_count = sum(1 for record in batch.records if not record.is_rejected)
                run.rejected_count = sum(1 for record in batch.records if record.is_rejected)
                if run.raw_count == 0 and not batch.empty_ok:
                    raise SourceEvidenceError(
                        "scheduled ingest returned zero rows without explicit no-new-data marker"
                    )
                rejected_dlq_entries: list[DeadLetterEntry] = []
                rejected_audit_actions: list[AuditAction] = []
                for index, record in enumerate((record for record in batch.records if record.is_rejected), start=1):
                    dlq_entry, audit_action = self._dead_letter_rejected_record(
                        run=run,
                        record=record,
                        connector_id=connector_id,
                        trigger_type=trigger_type,
                        trace_id=trace_id,
                        starting_watermark=starting_watermark.value if starting_watermark else None,
                        sequence_no=index,
                    )
                    rejected_dlq_entries.append(dlq_entry)
                    rejected_audit_actions.append(audit_action)
                if run.status == IngestRunStatus.FETCHING:
                    run.transition(IngestRunStatus.NORMALIZING, message="Scheduled source records fetched")
                if run.raw_count > 0 and run.normalized_count == 0 and run.rejected_count == run.raw_count:
                    run.transition(IngestRunStatus.REJECTED, message="Scheduled source records rejected and routed to DLQ")
                    self.store.upsert_run(run)
                    return ScheduledIngestResult(
                        run=run,
                        watermark=starting_watermark,
                        records=batch.records,
                        dlq_entries=tuple(rejected_dlq_entries),
                        audit_actions=tuple(rejected_audit_actions),
                        frontier_id=frontier_id,
                        typed_failure={
                            "schema_version": "source_ingest_typed_failure.v1",
                            "category": "validation",
                            "code": "all_records_rejected",
                            "error_type": "SourceEvidenceRejected",
                            "retryable": False,
                        },
                    )
                if run.status == IngestRunStatus.NORMALIZING:
                    run.transition(IngestRunStatus.INDEXING, message="Scheduled source records normalized")
                run.transition(IngestRunStatus.COMPLETED, message="Scheduled source records ready for indexing")
                self.store.upsert_run(run)
                watermark = SourceWatermark(
                    connector_id=connector_id,
                    source_type=connector.source_type.value,
                    value=batch.next_watermark if batch.next_watermark is not None else (starting_watermark.value if starting_watermark else None),
                    updated_at=run.finished_at.isoformat().replace("+00:00", "Z")
                    if hasattr(run.finished_at, "isoformat")
                    else str(run.finished_at),
                    last_ingest_run_id=run.ingest_run_id,
                )
                if not defer_commit and not (delivery and any(
                        r.source_type != connector.source_type for r in batch.records)):
                    if delivery:
                        delivery.acknowledge()
                    self.store.update_watermark(watermark)
                return ScheduledIngestResult(
                    run=run,
                    watermark=watermark,
                    records=batch.records,
                    dlq_entries=tuple(rejected_dlq_entries),
                    audit_actions=tuple(rejected_audit_actions),
                    frontier_id=frontier_id,
                    collector_delivery=delivery,
                )
            except Exception as exc:  # noqa: BLE001 - scheduler must DLQ final governed failures.
                last_error = exc
                last_typed_failure = typed_failure_for_exception(exc)
                if attempt < self.max_attempts:
                    continue

        message = str(last_error or "scheduled ingest failed")
        self.manager.fail_run(run.ingest_run_id, message=message)
        self.store.upsert_run(run)
        trace = TraceContext(
            trace_id=trace_id,
            correlation_id=trace_id,
            environment=self.environment,
            actor_ref=self.actor_ref,
        )
        event = EventEnvelope.new(
            event_type="source_ingestion.scheduled_run_failed",
            aggregate_type="source_ingest_run",
            aggregate_id=run.ingest_run_id,
            sequence_no=1,
            trace=trace,
            producer_service="source-ingestion",
            payload={
                "ingest_run_id": run.ingest_run_id,
                "connector_id": connector_id,
                "source_type": connector.source_type.value,
                "trigger_type": trigger_type,
                "attempts": self.max_attempts,
                "error": message,
                "watermark": starting_watermark.value if starting_watermark else None,
                "frontier_id": frontier_id,
            },
        )
        audit_action = AuditAction.record(
            actor_ref=self.actor_ref,
            action_type="source_ingestion.scheduled_run.dead_lettered",
            target_ref=f"source_ingest_run:{run.ingest_run_id}",
            environment=self.environment,
            reason="scheduled ingest exhausted retries",
            trace=trace,
            payload=event.payload,
            metadata={
                "connector_id": connector_id,
                "attempts": self.max_attempts,
                "watermark": starting_watermark.value if starting_watermark else None,
                "frontier_id": frontier_id,
            },
        )
        dlq_entry = self.dead_letter_queue.reject(
            event,
            reason=message,
            tags=("source_ingestion", "scheduled_ingest", "retry_exhausted"),
            source_ref=f"source_ingest_run:{run.ingest_run_id}",
        )
        return ScheduledIngestResult(
            run=run,
            watermark=starting_watermark,
            records=(),
            dlq_entries=(dlq_entry,),
            audit_actions=(audit_action,),
            frontier_id=frontier_id,
            typed_failure=last_typed_failure,
        )

    def _dead_letter_rejected_record(
        self,
        *,
        run: IngestRun,
        record: SourceRecord,
        connector_id: str,
        trigger_type: str,
        trace_id: str,
        starting_watermark: str | None,
        sequence_no: int,
    ) -> tuple[DeadLetterEntry, AuditAction]:
        trace = TraceContext(
            trace_id=record.trace_id or trace_id,
            correlation_id=trace_id,
            environment=self.environment,
            actor_ref=self.actor_ref,
        )
        payload = {
            "ingest_run_id": run.ingest_run_id,
            "connector_id": connector_id,
            "source_id": record.source_id,
            "source_type": record.source_type.value,
            "trigger_type": trigger_type,
            "watermark": starting_watermark,
            "record_status": record.status.value,
            "metadata": dict(record.metadata),
        }
        event = EventEnvelope.new(
            event_type="source_ingestion.source_record_rejected",
            aggregate_type="source_record",
            aggregate_id=record.source_id,
            sequence_no=sequence_no,
            trace=trace,
            producer_service="source-ingestion",
            payload=payload,
        )
        audit_action = AuditAction.record(
            actor_ref=self.actor_ref,
            action_type="source_ingestion.source_record.dead_lettered",
            target_ref=f"source_record:{record.source_id}",
            environment=self.environment,
            reason="source record rejected during scheduled ingest",
            trace=trace,
            payload=payload,
            metadata={"connector_id": connector_id, "ingest_run_id": run.ingest_run_id},
        )
        dlq_entry = self.dead_letter_queue.reject(
            event,
            reason=str(record.metadata.get("reject_reason") or "source record rejected during scheduled ingest"),
            tags=("source_ingestion", "scheduled_ingest", "source_record_rejected"),
            source_ref=f"source_record:{record.source_id}",
        )
        return dlq_entry, audit_action
