"""Deployable source-ingest service boundary.

The service intentionally keeps external fetching out of process for this
first deployable slice. Callers submit a bounded batch of already-fetched
records; the wrapper applies the governed ingest lifecycle, persisted
watermarks, DLQ routing, and audit replay contract from the source_ingestion
library.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from services.foundation import DeadLetterQueue

from .connectors import (
    AuthType,
    ConnectorMode,
    ConnectorStatus,
    SourceConnector,
    SourceEvidenceError,
    SourceRecord,
    SourceRecordStatus,
    SourceType,
)
from .ingest_manager import IngestManager
from .scheduler import IngestBatch, IngestionScheduler, JsonlIngestScheduleStore


def _resolve_data_dir() -> Path:
    data_dir = Path(os.getenv("SOURCE_INGEST_DATA_DIR", "/tmp/pantheon/source-ingest"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


DATA_DIR = _resolve_data_dir()
SCHEDULE_STORE_PATH = Path(os.getenv("SOURCE_INGEST_STORE_PATH", str(DATA_DIR / "ingest_schedule.jsonl")))
DLQ_STORE_PATH = Path(os.getenv("SOURCE_INGEST_DLQ_PATH", str(DATA_DIR / "source_ingest_dlq.jsonl")))
AUDIT_STORE_PATH = Path(os.getenv("SOURCE_INGEST_AUDIT_PATH", str(DATA_DIR / "source_ingest_audit.jsonl")))
MAX_RECORDS_PER_JOB = int(os.getenv("SOURCE_INGEST_MAX_RECORDS", "100"))

app = FastAPI(title="Pantheon Source Ingest Service", version="0.1.0")
manager = IngestManager()
store = JsonlIngestScheduleStore(SCHEDULE_STORE_PATH)
dead_letter_queue = DeadLetterQueue(DLQ_STORE_PATH)
dead_letter_queue.load_from_spill()
scheduler = IngestionScheduler(manager=manager, store=store, dead_letter_queue=dead_letter_queue)


class ConnectorBody(BaseModel):
    connector_id: str
    source_type: SourceType
    provider: str
    license_scope: str
    auth_type: AuthType = AuthType.NONE
    secret_ref_id: str | None = None
    supported_modes: list[ConnectorMode] = Field(default_factory=lambda: [ConnectorMode.BATCH])
    status: ConnectorStatus = ConnectorStatus.ENABLED
    rate_limit_policy_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_domain(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type=self.source_type.value,
            provider=self.provider,
            license_scope=self.license_scope,
            auth_type=self.auth_type.value,
            secret_ref_id=self.secret_ref_id,
            supported_modes=[mode.value for mode in self.supported_modes],
            status=self.status.value,
            rate_limit_policy_ref=self.rate_limit_policy_ref,
            metadata=self.metadata,
        )


class SourceRecordBody(BaseModel):
    source_id: str
    connector_id: str
    source_type: SourceType
    title: str
    content_ref: str
    status: SourceRecordStatus = SourceRecordStatus.NORMALIZED
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = ""

    def to_domain(self) -> SourceRecord:
        return SourceRecord(
            source_id=self.source_id,
            connector_id=self.connector_id,
            source_type=self.source_type.value,
            title=self.title,
            content_ref=self.content_ref,
            status=self.status.value,
            metadata=self.metadata,
            trace_id=self.trace_id,
        )


class TriggerIngestJobRequest(BaseModel):
    connector: ConnectorBody
    trace_id: str
    trigger_type: str = "manual"
    records: list[SourceRecordBody] = Field(default_factory=list)
    next_watermark: str | None = None


def _register_or_validate_connector(connector: SourceConnector) -> SourceConnector:
    existing = manager.get_connector(connector.connector_id)
    if existing is None:
        return manager.register_connector(connector)
    if existing.to_dict() != connector.to_dict():
        raise SourceEvidenceError(f"Connector already registered with different contract: {connector.connector_id}")
    return existing


def _append_audit_actions(actions: tuple[Any, ...]) -> None:
    if not actions:
        return
    AUDIT_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_STORE_PATH.open("a", encoding="utf-8") as handle:
        for action in actions:
            handle.write(json.dumps(action.to_dict(), sort_keys=True, separators=(",", ":")) + "\n")


def _load_audit_actions() -> list[dict[str, Any]]:
    if not AUDIT_STORE_PATH.exists():
        return []
    actions: list[dict[str, Any]] = []
    for line in AUDIT_STORE_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            actions.append(json.loads(line))
    return actions


def _result_payload(result: Any) -> dict[str, Any]:
    return {
        "run": result.run.to_dict(),
        "watermark": result.watermark.to_dict() if result.watermark else None,
        "records": [record.to_dict() for record in result.records],
        "dlq_entries": [entry.to_dict() for entry in result.dlq_entries],
        "audit_actions": [action.to_dict() for action in result.audit_actions],
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "pantheon-source-ingest",
        "store_path": str(SCHEDULE_STORE_PATH),
        "dlq_path": str(DLQ_STORE_PATH),
        "audit_path": str(AUDIT_STORE_PATH),
        "run_count": len(store.list_runs()),
        "dlq_count": len(dead_letter_queue.entries()),
    }


@app.post("/api/source-ingest/jobs", status_code=201)
def trigger_job(request: TriggerIngestJobRequest) -> dict[str, Any]:
    if len(request.records) > MAX_RECORDS_PER_JOB:
        raise HTTPException(status_code=413, detail=f"records exceeds SOURCE_INGEST_MAX_RECORDS={MAX_RECORDS_PER_JOB}")

    try:
        connector = _register_or_validate_connector(request.connector.to_domain())
        records = tuple(record.to_domain() for record in request.records)
        for record in records:
            if record.connector_id != connector.connector_id:
                raise SourceEvidenceError("record connector_id must match job connector")
            if record.source_type != connector.source_type:
                raise SourceEvidenceError("record source_type must match job connector")
        result = scheduler.run_once(
            connector_id=connector.connector_id,
            trace_id=request.trace_id,
            trigger_type=request.trigger_type,
            fetch_batch=lambda _watermark: IngestBatch(records=records, next_watermark=request.next_watermark),
        )
        _append_audit_actions(result.audit_actions)
        return _result_payload(result)
    except SourceEvidenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/source-ingest/jobs")
def list_jobs() -> dict[str, Any]:
    return {"runs": [run.to_dict() for run in store.list_runs()]}


@app.get("/api/source-ingest/jobs/{ingest_run_id}")
def get_job(ingest_run_id: str) -> dict[str, Any]:
    run = store.get_run(ingest_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ingest run not found")
    return {"run": run.to_dict()}


@app.get("/api/source-ingest/watermarks/{connector_id}")
def get_watermark(connector_id: str) -> dict[str, Any]:
    watermark = store.get_watermark(connector_id)
    if watermark is None:
        raise HTTPException(status_code=404, detail="source watermark not found")
    return {"watermark": watermark.to_dict()}


@app.get("/api/source-ingest/dlq")
def list_dlq(
    status: Literal["pending", "replayed", "duplicate_skipped", "replay_failed", "schema_rejected"] | None = None,
) -> dict[str, Any]:
    return {"entries": [entry.to_dict() for entry in dead_letter_queue.entries(status=status)]}


@app.get("/api/source-ingest/audit")
def list_audit() -> dict[str, Any]:
    return {"actions": _load_audit_actions()}
