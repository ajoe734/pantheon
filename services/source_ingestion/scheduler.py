"""Governed ingestion scheduling with replayable watermarks and DLQ routing."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(self.records))
        for record in self.records:
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

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._lock = threading.RLock()
        self._runs: dict[str, IngestRun] = {}
        self._watermarks: dict[str, SourceWatermark] = {}
        self._frontier: dict[str, CrawlFrontierItem] = {}
        self._receipts: dict[str, IngestReceipt] = {}
        self.reload()

    def reload(self) -> None:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked(recover_incomplete=True)

    def _reload_unlocked(self, *, recover_incomplete: bool) -> None:
        self._runs = {}
        self._watermarks = {}
        self._frontier = {}
        self._receipts = {}
        if not self.path.exists():
            return
        for line_no, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
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
                receipt = IngestReceipt.from_dict(payload)
                self._receipts[receipt.ingest_run_id] = receipt
            else:
                raise SourceEvidenceError(f"Unsupported ingest schedule record: {record_type or '<missing>'}")
        if recover_incomplete:
            self._recover_incomplete_receipts()

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
            self._reload_unlocked(recover_incomplete=False)
            self._append("ingest_run", run.ingest_run_id, run.to_dict())
            self._runs[run.ingest_run_id] = run
            return run

    def update_watermark(self, watermark: SourceWatermark) -> SourceWatermark:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked(recover_incomplete=False)
            self._append("source_watermark", watermark.connector_id, watermark.to_dict())
            self._watermarks[watermark.connector_id] = watermark
            return watermark

    def get_run(self, ingest_run_id: str) -> IngestRun | None:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked(recover_incomplete=False)
            return self._runs.get(ingest_run_id)

    def list_runs(self) -> list[IngestRun]:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked(recover_incomplete=False)
            return list(self._runs.values())

    def upsert_receipt(self, receipt: IngestReceipt) -> IngestReceipt:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked(recover_incomplete=False)
            self._append("ingest_receipt", receipt.ingest_run_id, receipt.to_dict())
            self._receipts[receipt.ingest_run_id] = receipt
            return receipt

    def get_receipt(self, ingest_run_id: str) -> IngestReceipt | None:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked(recover_incomplete=False)
            return self._receipts.get(ingest_run_id)

    def list_receipts(self, *, connector_id: str | None = None) -> list[IngestReceipt]:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked(recover_incomplete=False)
            receipts = list(self._receipts.values())
            if connector_id is not None:
                receipts = [receipt for receipt in receipts if receipt.connector_id == connector_id]
            return sorted(
                receipts,
                key=lambda receipt: (receipt.finished_at or receipt.started_at, receipt.ingest_run_id),
            )

    def get_watermark(self, connector_id: str) -> SourceWatermark | None:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked(recover_incomplete=False)
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
            self._reload_unlocked(recover_incomplete=False)
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
            self._reload_unlocked(recover_incomplete=False)
            return self._list_frontier_unlocked(status=status)

    def _list_frontier_unlocked(self, status: str | None = None) -> list[CrawlFrontierItem]:
        items = list(self._frontier.values())
        if status:
            items = [item for item in items if item.status == status]
        return sorted(items, key=lambda item: (item.available_at or item.created_at, item.frontier_id))

    def get_frontier(self, frontier_id: str) -> CrawlFrontierItem | None:
        with exclusive_file_lock(self._lock_path, self._lock):
            self._reload_unlocked(recover_incomplete=False)
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
            self._reload_unlocked(recover_incomplete=False)
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
            self._reload_unlocked(recover_incomplete=False)
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
            self._reload_unlocked(recover_incomplete=False)
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
            self._reload_unlocked(recover_incomplete=False)
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
            self._reload_unlocked(recover_incomplete=False)
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
            self._reload_unlocked(recover_incomplete=False)
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file_preexisted = self.path.exists()
        entry = {
            "schema_version": "ingest_schedule_store.v1",
            "record_type": record_type,
            "record_id": record_id,
            "payload": dict(payload),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if not file_preexisted:
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)


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
                self.store.update_watermark(watermark)
                return ScheduledIngestResult(
                    run=run,
                    watermark=watermark,
                    records=batch.records,
                    dlq_entries=tuple(rejected_dlq_entries),
                    audit_actions=tuple(rejected_audit_actions),
                    frontier_id=frontier_id,
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
