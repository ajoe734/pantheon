"""Deployable source-ingest service boundary.

The service supports both the initial bounded already-fetched record wrapper
and the baseline autonomous path: callers can persist a connector fetch
configuration, then trigger an ingest run by connector id. The wrapper applies
the governed ingest lifecycle, persisted watermarks, durable source/evidence
refs, DLQ routing, and audit replay contract from the source_ingestion library.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
try:
    from pydantic import BaseModel, ConfigDict, Field
except ImportError:  # pragma: no cover - compatibility with older pydantic.
    from pydantic import BaseModel, Field

    ConfigDict = None  # type: ignore[assignment]

from services.foundation import ActorRef, ActorType, DeadLetterQueue, DeadLetterReplayProcessor, SchemaRegistry
from services.foundation.health import register_fastapi_health_routes
from services.knowledge.evidence import EvidenceBundleBuilder, EvidenceItem, normalize_source_evidence, normalize_source_record
from services.knowledge.evidence.models import EvidenceValidationError

from .connectors import (
    AuthType,
    ConnectorMode,
    ConnectorStatus,
    SourceConnector,
    SourceEvidenceError,
    SourceRecord,
    SourceRecordStatus,
    SourceType,
    example_provider_catalog,
)
from .configured import ConfiguredConnectorFetcher, JsonlConfiguredConnectorStore, JsonlConnectorScheduleStore
from .ingest_manager import IngestManager
from .pg_store import build_source_evidence_repository
from .scheduler import IngestBatch, IngestionScheduler, JsonlIngestScheduleStore


def _resolve_data_dir() -> Path:
    data_dir = Path(os.getenv("SOURCE_INGEST_DATA_DIR", "/tmp/pantheon/source-ingest"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


DATA_DIR = _resolve_data_dir()
SCHEDULE_STORE_PATH = Path(os.getenv("SOURCE_INGEST_STORE_PATH", str(DATA_DIR / "ingest_schedule.jsonl")))
CONNECTOR_STORE_PATH = Path(os.getenv("SOURCE_INGEST_CONNECTOR_STORE_PATH", str(DATA_DIR / "connector_config.jsonl")))
SOURCE_EVIDENCE_STORE_PATH = Path(os.getenv("SOURCE_INGEST_EVIDENCE_STORE_PATH", str(DATA_DIR / "source_evidence.jsonl")))
DLQ_STORE_PATH = Path(os.getenv("SOURCE_INGEST_DLQ_PATH", str(DATA_DIR / "source_ingest_dlq.jsonl")))
AUDIT_STORE_PATH = Path(os.getenv("SOURCE_INGEST_AUDIT_PATH", str(DATA_DIR / "source_ingest_audit.jsonl")))
CONNECTOR_SCHEDULE_CONFIG_PATH = Path(os.getenv("SOURCE_INGEST_SCHEDULE_CONFIG_PATH", str(DATA_DIR / "connector_schedule.jsonl")))
MAX_RECORDS_PER_JOB = int(os.getenv("SOURCE_INGEST_MAX_RECORDS", "100"))

app = FastAPI(title="Pantheon Source Ingest Service", version="0.1.0")
manager = IngestManager()
store = JsonlIngestScheduleStore(SCHEDULE_STORE_PATH)
connector_store = JsonlConfiguredConnectorStore(CONNECTOR_STORE_PATH)
schedule_config_store = JsonlConnectorScheduleStore(CONNECTOR_SCHEDULE_CONFIG_PATH)
configured_fetcher = ConfiguredConnectorFetcher(connector_store)
evidence_repository = build_source_evidence_repository(SOURCE_EVIDENCE_STORE_PATH)
evidence_builder = EvidenceBundleBuilder(evidence_repository)
dead_letter_queue = DeadLetterQueue(DLQ_STORE_PATH)
dead_letter_queue.load_from_spill()
scheduler = IngestionScheduler(manager=manager, store=store, dead_letter_queue=dead_letter_queue)
replay_processor = DeadLetterReplayProcessor(schema_registry=SchemaRegistry())
register_fastapi_health_routes(
    app,
    "pantheon-source-ingest",
    metrics=lambda: {
        "run_count": len(store.list_runs()),
        "connector_count": len(connector_store.list_configs()),
        "source_record_count": len(evidence_repository.list_source_records()),
        "evidence_item_count": len(evidence_repository.list_evidence_items()),
        "dlq_count": len(dead_letter_queue.entries()),
    },
    details=lambda: {
        "store_path": str(SCHEDULE_STORE_PATH),
        "connector_store_path": str(CONNECTOR_STORE_PATH),
        "source_evidence_path": str(SOURCE_EVIDENCE_STORE_PATH),
        "dlq_path": str(DLQ_STORE_PATH),
        "audit_path": str(AUDIT_STORE_PATH),
    },
)


class StrictBaseModel(BaseModel):
    if ConfigDict is not None:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - compatibility with older pydantic.

        class Config:
            extra = "forbid"


class ConnectorBody(StrictBaseModel):
    connector_id: str
    source_type: SourceType
    provider: str
    license_scope: str
    auth_type: AuthType = AuthType.NONE
    secret_ref_id: str | None = None
    supported_modes: list[ConnectorMode] = Field(default_factory=lambda: [ConnectorMode.BATCH])
    status: ConnectorStatus = ConnectorStatus.ENABLED
    rate_limit_policy_ref: str | None = None
    auth_policy: dict[str, Any] | None = None
    rate_limit_policy: dict[str, Any] | None = None
    license_policy: dict[str, Any] | None = None
    source_metadata: dict[str, Any] | None = None
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
            auth_policy=self.auth_policy,
            rate_limit_policy=self.rate_limit_policy,
            license_policy=self.license_policy,
            source_metadata=self.source_metadata,
            metadata=self.metadata,
        )


class SourceRecordBody(StrictBaseModel):
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


class ConfiguredFetchRecordBody(StrictBaseModel):
    source_id: str
    title: str
    content_ref: str
    connector_id: str | None = None
    source_type: SourceType | None = None
    status: SourceRecordStatus = SourceRecordStatus.NORMALIZED
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = ""
    created_at: str | None = None

    def to_config(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_id": self.source_id,
            "title": self.title,
            "content_ref": self.content_ref,
            "status": self.status.value,
            "metadata": self.metadata,
            "trace_id": self.trace_id,
        }
        if self.connector_id:
            payload["connector_id"] = self.connector_id
        if self.source_type:
            payload["source_type"] = self.source_type.value
        if self.created_at:
            payload["created_at"] = self.created_at
        return payload


class ConfiguredFetchBody(StrictBaseModel):
    mode: Literal["static_records", "external_feed"] = "static_records"
    records: list[ConfiguredFetchRecordBody] = Field(default_factory=list)
    url: str | None = None
    allowed_url_prefixes: list[str] = Field(default_factory=list)
    timeout_seconds: float = 5.0
    max_bytes: int = 1_000_000
    max_records: int = 100
    default_access_scope: list[str] = Field(default_factory=lambda: ["public"])
    next_watermark: str | None = None
    fail_until_attempt: int = 0
    failure_reason: str = "configured connector fetch failed"

    def to_config(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "mode": self.mode,
            "records": [record.to_config() for record in self.records],
            "next_watermark": self.next_watermark,
            "fail_until_attempt": self.fail_until_attempt,
            "failure_reason": self.failure_reason,
        }
        if self.mode == "external_feed":
            payload.update(
                {
                    "url": self.url,
                    "allowed_url_prefixes": self.allowed_url_prefixes,
                    "timeout_seconds": self.timeout_seconds,
                    "max_bytes": self.max_bytes,
                    "max_records": self.max_records,
                    "default_access_scope": self.default_access_scope,
                }
            )
        return payload


class ConfigureConnectorRequest(StrictBaseModel):
    connector: ConnectorBody
    fetch: ConfiguredFetchBody


class TriggerIngestJobRequest(StrictBaseModel):
    connector: ConnectorBody | None = None
    connector_id: str | None = None
    trace_id: str
    trigger_type: str = "manual"
    records: list[SourceRecordBody] = Field(default_factory=list)
    next_watermark: str | None = None
    fetch: ConfiguredFetchBody | None = None


class ReplayDlqRequest(StrictBaseModel):
    tag: str = "retry_exhausted"
    entry_ids: list[str] = Field(default_factory=list)
    reason: str = "operator-approved source ingest DLQ replay"
    actor_id: str = "source-ingest-operator"


class SetScheduleRequest(StrictBaseModel):
    interval_seconds: int = 0
    enabled: bool = False


def _register_or_validate_connector(connector: SourceConnector) -> SourceConnector:
    existing = manager.get_connector(connector.connector_id)
    if existing is None:
        return manager.register_connector(connector)
    if existing.to_dict() != connector.to_dict():
        raise SourceEvidenceError(f"Connector already registered with different contract: {connector.connector_id}")
    return existing


def _assert_fetch_within_limit(fetch: ConfiguredFetchBody) -> None:
    if len(fetch.records) > MAX_RECORDS_PER_JOB:
        raise HTTPException(status_code=413, detail=f"fetch.records exceeds SOURCE_INGEST_MAX_RECORDS={MAX_RECORDS_PER_JOB}")
    if fetch.mode == "external_feed" and fetch.max_records > MAX_RECORDS_PER_JOB:
        raise HTTPException(
            status_code=413,
            detail=f"fetch.max_records exceeds SOURCE_INGEST_MAX_RECORDS={MAX_RECORDS_PER_JOB}",
        )


def _configure_connector(request: ConfigureConnectorRequest) -> dict[str, Any]:
    _assert_fetch_within_limit(request.fetch)
    connector = _register_or_validate_connector(request.connector.to_domain())
    config = connector_store.upsert_config(connector, request.fetch.to_config())
    return {
        "connector": config.connector.to_dict(),
        "fetch": dict(config.fetch),
        "state": connector_store.get_fetch_state(connector.connector_id),
        "updated_at": config.updated_at,
    }


def _fetch_policy_summary(fetch: dict[str, Any] | None) -> dict[str, Any]:
    if not fetch:
        return {
            "configured": False,
            "mode": None,
        }
    mode = str(fetch.get("mode") or "")
    summary: dict[str, Any] = {
        "configured": True,
        "mode": mode,
        "fail_until_attempt": int(fetch.get("fail_until_attempt") or 0),
    }
    if mode == "external_feed":
        parsed = urllib.parse.urlparse(str(fetch.get("url") or ""))
        summary.update(
            {
                "url_scheme": parsed.scheme or None,
                "url_host": parsed.netloc or None,
                "allowed_url_prefix_count": len(fetch.get("allowed_url_prefixes") or []),
                "timeout_seconds": fetch.get("timeout_seconds"),
                "max_bytes": fetch.get("max_bytes"),
                "max_records": fetch.get("max_records"),
                "default_access_scope": list(fetch.get("default_access_scope") or []),
            }
        )
    elif mode == "static_records":
        summary.update(
            {
                "records_count": len(fetch.get("records") or []),
                "next_watermark": fetch.get("next_watermark"),
            }
        )
    return summary


def _schedule_summary(connector_id: str) -> dict[str, Any]:
    schedule = schedule_config_store.get_schedule(connector_id)
    if schedule is None:
        return {
            "configured": False,
            "enabled": False,
            "interval_seconds": 0,
        }
    return {
        "configured": True,
        "enabled": schedule.enabled,
        "interval_seconds": schedule.interval_seconds,
        "updated_at": schedule.updated_at,
    }


def _connector_registry_entry(
    connector: SourceConnector,
    *,
    fetch: dict[str, Any] | None,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": "source_connector_registry_entry.v1",
        "connector_id": connector.connector_id,
        "provider": connector.provider,
        "source_type": connector.source_type.value,
        "status": connector.status.value,
        "supported_modes": [mode.value for mode in connector.supported_modes],
        "policy": connector.policy_summary(),
        "metadata": dict(connector.metadata),
        "fetch_policy": _fetch_policy_summary(fetch),
        "schedule": _schedule_summary(connector.connector_id),
        "state": state
        or {
            "connector_id": connector.connector_id,
            "attempts": 0,
            "successful_attempts": 0,
            "failed_attempts": 0,
            "last_error": None,
            "updated_at": None,
        },
    }


def _provider_example_payloads() -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for provider in example_provider_catalog():
        connector = provider.connector()
        payloads.append(
            {
                "connector": connector.to_dict(),
                "fetch_policy": _fetch_policy_summary(dict(provider.fetch_config())),
            }
        )
    return payloads


def _connector_for_job(request: TriggerIngestJobRequest) -> SourceConnector:
    if request.connector is not None:
        connector = _register_or_validate_connector(request.connector.to_domain())
        if request.connector_id and request.connector_id != connector.connector_id:
            raise SourceEvidenceError("connector_id must match connector.connector_id")
        if request.fetch is not None:
            _assert_fetch_within_limit(request.fetch)
            connector_store.upsert_config(connector, request.fetch.to_config())
        return connector

    connector_id = str(request.connector_id or "").strip()
    if not connector_id:
        raise SourceEvidenceError("connector or connector_id is required")
    config = connector_store.get_config(connector_id)
    if config is None:
        raise SourceEvidenceError(f"Connector fetch is not configured: {connector_id}")
    return _register_or_validate_connector(config.connector)


def _inline_fetch(records: tuple[SourceRecord, ...], next_watermark: str | None):
    return lambda _watermark: IngestBatch(records=records, next_watermark=next_watermark)


def _configured_fetch(connector_id: str):
    return lambda watermark: configured_fetcher.fetch_batch(connector_id, watermark)


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


def _stable_ref(prefix: str, value: str) -> str:
    digest = sha256(value.encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()[:48]
    return f"{prefix}-{slug}-{digest}" if slug else f"{prefix}-{digest}"


def _list_metadata(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, list):
        normalized = [str(item).strip() for item in value if str(item).strip()]
        return normalized or default
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return default


def _evidence_item_for_record(record: SourceRecord, run: Any) -> EvidenceItem:
    metadata = dict(record.metadata)
    item_id = str(metadata.get("evidence_item_id") or _stable_ref("evi", record.source_id))
    body = str(metadata.get("body") or metadata.get("excerpt") or record.title)
    citation_label = str(metadata.get("citation_label") or record.title or record.source_id)
    trace_refs = []
    for trace_ref in (record.trace_id, run.trace_id):
        if trace_ref and trace_ref not in trace_refs:
            trace_refs.append(trace_ref)
    return EvidenceItem(
        evidence_item_id=item_id,
        source_id=record.source_id,
        item_type=str(metadata.get("evidence_item_type") or "source_record"),
        content_ref=record.content_ref,
        citation_label=citation_label,
        body=body,
        event_time=metadata.get("event_time"),
        available_time=metadata.get("available_time"),
        confidence=float(metadata.get("confidence", 1.0)),
        access_scope=_list_metadata(metadata.get("access_scope"), ["public"]),
        trace_refs=trace_refs,
        metadata={**metadata, "source_ingest_run_id": run.ingest_run_id},
    )


def _persist_source_evidence_refs(result: Any) -> dict[str, Any]:
    source_records = [record for record in result.records if not record.is_rejected]
    if not source_records:
        return {
            "source_ids": [],
            "evidence_item_ids": [],
            "evidence_bundle_id": None,
            "knowledge_object_ids": [],
        }

    normalized_source_records_by_id: dict[str, SourceRecord] = {}
    source_by_evidence_item_id: dict[str, SourceRecord] = {}
    evidence_items_by_id: dict[str, EvidenceItem] = {}
    source_owner_by_dedupe_key: dict[str, SourceRecord] = {}
    connector = manager.get_connector(result.run.connector_id)
    connector_license_scope = connector.license_scope if connector else None
    for record in source_records:
        candidate_source = normalize_source_record(record, connector_license_scope=connector_license_scope)
        source_dedupe_key = str(candidate_source.metadata["source_dedupe_key"])
        source_owner = (
            evidence_repository.get_source_record_by_dedupe_key(source_dedupe_key)
            or source_owner_by_dedupe_key.get(source_dedupe_key)
            or candidate_source
        )
        source_owner_by_dedupe_key[source_dedupe_key] = source_owner
        normalized = normalize_source_evidence(
            source_record=record,
            evidence_item=_evidence_item_for_record(record, result.run),
            connector_license_scope=connector_license_scope,
            source_owner_id=source_owner.source_id,
        )
        normalized_source = source_owner if source_owner.source_id != record.source_id else normalized.source_record
        normalized_source_records_by_id[normalized_source.source_id] = normalized_source
        source_by_evidence_item_id[normalized.evidence_item.evidence_item_id] = normalized_source
        evidence_items_by_id[normalized.evidence_item.evidence_item_id] = normalized.evidence_item
    evidence_items = list(evidence_items_by_id.values())
    bundle = evidence_builder.build_bundle(
        source_records=list(normalized_source_records_by_id.values()),
        evidence_items=evidence_items,
        summary=f"Source ingest run {result.run.ingest_run_id} persisted {len(source_records)} source record(s).",
        created_by="source-ingest",
        evidence_bundle_id=_stable_ref("evbundle", result.run.ingest_run_id),
        metadata={
            "connector_id": result.run.connector_id,
            "ingest_run_id": result.run.ingest_run_id,
            "trigger_type": result.run.trigger_type,
            "normalization_schema": "source_evidence_normalization.v1",
        },
    )
    knowledge_object_ids: list[str] = []
    for item in evidence_items:
        record = source_by_evidence_item_id[item.evidence_item_id]
        metadata = dict(record.metadata)
        knowledge_object = evidence_builder.build_knowledge_object(
            knowledge_object_id=str(metadata.get("knowledge_object_id") or _stable_ref("ko", item.evidence_item_id)),
            source_record=record,
            evidence_item=item,
            evidence_bundle=bundle,
            title=record.title,
            text=item.body,
            access_scope=item.access_scope,
            keywords=_list_metadata(metadata.get("keywords"), []),
            metadata={
                "connector_id": record.connector_id,
                "ingest_run_id": result.run.ingest_run_id,
                "content_ref": record.content_ref,
                "source_dedupe_key": metadata.get("source_dedupe_key"),
                "evidence_dedupe_key": item.metadata.get("evidence_dedupe_key"),
                "evidence_owner_id": item.metadata.get("evidence_owner_id"),
            },
        )
        knowledge_object_ids.append(knowledge_object.knowledge_object_id)
    return {
        "source_ids": list(normalized_source_records_by_id),
        "evidence_item_ids": [item.evidence_item_id for item in evidence_items],
        "evidence_bundle_id": bundle.evidence_bundle_id,
        "knowledge_object_ids": knowledge_object_ids,
    }


def _result_payload(result: Any, evidence_refs: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "run": result.run.to_dict(),
        "watermark": result.watermark.to_dict() if result.watermark else None,
        "records": [record.to_dict() for record in result.records],
        "evidence_refs": evidence_refs
        or {
            "source_ids": [],
            "evidence_item_ids": [],
            "evidence_bundle_id": None,
            "knowledge_object_ids": [],
        },
        "dlq_entries": [entry.to_dict() for entry in result.dlq_entries],
        "audit_actions": [action.to_dict() for action in result.audit_actions],
    }


def _run_job(
    *,
    connector: SourceConnector,
    trace_id: str,
    trigger_type: str,
    fetch_batch: Any,
) -> tuple[Any, dict[str, Any]]:
    result = scheduler.run_once(
        connector_id=connector.connector_id,
        trace_id=trace_id,
        trigger_type=trigger_type,
        fetch_batch=fetch_batch,
    )
    _append_audit_actions(result.audit_actions)
    evidence_refs = _persist_source_evidence_refs(result)
    return result, evidence_refs


def _replay_source_event(event: Any) -> str:
    if event.event_type != "source_ingestion.scheduled_run_failed":
        raise SourceEvidenceError(f"Unsupported source-ingest DLQ replay event: {event.event_type}")
    connector_id = str(event.payload.get("connector_id") or "").strip()
    if not connector_id:
        raise SourceEvidenceError("DLQ replay event is missing connector_id")
    config = connector_store.get_config(connector_id)
    if config is None:
        raise SourceEvidenceError(f"Connector fetch is not configured: {connector_id}")
    connector = _register_or_validate_connector(config.connector)
    result, _evidence_refs = _run_job(
        connector=connector,
        trace_id=event.trace_id,
        trigger_type="dlq_replay",
        fetch_batch=_configured_fetch(connector.connector_id),
    )
    if result.run.status.value != "completed":
        raise SourceEvidenceError(f"DLQ replay did not complete ingest run: {result.run.status.value}")
    return f"source_ingest_run:{result.run.ingest_run_id}"


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "pantheon-source-ingest",
        "store_path": str(SCHEDULE_STORE_PATH),
        "connector_store_path": str(CONNECTOR_STORE_PATH),
        "source_evidence_path": str(SOURCE_EVIDENCE_STORE_PATH),
        "dlq_path": str(DLQ_STORE_PATH),
        "audit_path": str(AUDIT_STORE_PATH),
        "run_count": len(store.list_runs()),
        "connector_count": len(connector_store.list_configs()),
        "source_record_count": len(evidence_repository.list_source_records()),
        "evidence_item_count": len(evidence_repository.list_evidence_items()),
        "dlq_count": len(dead_letter_queue.entries()),
    }


@app.post("/api/source-ingest/connectors", status_code=201)
def configure_connector(request: ConfigureConnectorRequest) -> dict[str, Any]:
    try:
        return _configure_connector(request)
    except SourceEvidenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/source-ingest/connectors")
def list_connectors() -> dict[str, Any]:
    return {
        "connectors": [
            {
                "connector": config.connector.to_dict(),
                "fetch": dict(config.fetch),
                "state": connector_store.get_fetch_state(config.connector.connector_id),
                "updated_at": config.updated_at,
            }
            for config in connector_store.list_configs()
        ]
    }


@app.get("/api/source-ingest/registry")
def source_connector_registry() -> dict[str, Any]:
    configured_by_id = {config.connector.connector_id: config for config in connector_store.list_configs()}
    connector_ids = set(configured_by_id)
    connectors = list(manager.list_connectors())
    connector_ids.update(connector.connector_id for connector in connectors)

    entries: list[dict[str, Any]] = []
    for connector_id in sorted(connector_ids):
        config = configured_by_id.get(connector_id)
        connector = config.connector if config else manager.get_connector(connector_id)
        if connector is None:
            continue
        entries.append(
            _connector_registry_entry(
                connector,
                fetch=dict(config.fetch) if config else None,
                state=connector_store.get_fetch_state(connector_id) if config else None,
            )
        )
    return {
        "schema_version": "source_connector_registry.v1",
        "connectors": entries,
        "provider_examples": _provider_example_payloads(),
    }


@app.get("/api/source-ingest/connectors/{connector_id}")
def get_connector(connector_id: str) -> dict[str, Any]:
    config = connector_store.get_config(connector_id)
    if config is None:
        raise HTTPException(status_code=404, detail="connector config not found")
    return {
        "connector": config.connector.to_dict(),
        "fetch": dict(config.fetch),
        "state": connector_store.get_fetch_state(connector_id),
        "updated_at": config.updated_at,
    }


@app.post("/api/source-ingest/jobs", status_code=201)
def trigger_job(request: TriggerIngestJobRequest) -> dict[str, Any]:
    if len(request.records) > MAX_RECORDS_PER_JOB:
        raise HTTPException(status_code=413, detail=f"records exceeds SOURCE_INGEST_MAX_RECORDS={MAX_RECORDS_PER_JOB}")

    try:
        connector = _connector_for_job(request)
        if request.records:
            records = tuple(record.to_domain() for record in request.records)
            for record in records:
                if record.connector_id != connector.connector_id:
                    raise SourceEvidenceError("record connector_id must match job connector")
                if record.source_type != connector.source_type:
                    raise SourceEvidenceError("record source_type must match job connector")
            fetch_batch = _inline_fetch(records, request.next_watermark)
        else:
            fetch_batch = _configured_fetch(connector.connector_id)
        result, evidence_refs = _run_job(
            connector=connector,
            trace_id=request.trace_id,
            trigger_type=request.trigger_type,
            fetch_batch=fetch_batch,
        )
        return _result_payload(result, evidence_refs)
    except (EvidenceValidationError, SourceEvidenceError) as exc:
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


@app.get("/api/source-ingest/source-records")
def list_source_records() -> dict[str, Any]:
    return {"source_records": [record.to_dict() for record in evidence_repository.list_source_records()]}


@app.get("/api/source-ingest/source-records/{source_id}")
def get_source_record(source_id: str) -> dict[str, Any]:
    source = evidence_repository.get_source_record(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source record not found")
    return {"source_record": source.to_dict()}


@app.get("/api/source-ingest/evidence/items")
def list_evidence_items() -> dict[str, Any]:
    return {"items": [item.to_dict() for item in evidence_repository.list_evidence_items()]}


@app.get("/api/source-ingest/evidence/items/{evidence_item_id}")
def get_evidence_item(evidence_item_id: str) -> dict[str, Any]:
    item = evidence_repository.get_evidence_item(evidence_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="evidence item not found")
    return {"item": item.to_dict()}


@app.get("/api/source-ingest/evidence/bundles")
def list_evidence_bundles() -> dict[str, Any]:
    return {"bundles": [bundle.to_dict() for bundle in evidence_repository.list_bundles()]}


@app.get("/api/source-ingest/evidence/bundles/{evidence_bundle_id}")
def get_evidence_bundle(evidence_bundle_id: str) -> dict[str, Any]:
    bundle = evidence_repository.get_bundle(evidence_bundle_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="evidence bundle not found")
    return {"bundle": bundle.to_dict()}


@app.get("/api/source-ingest/evidence/knowledge-objects")
def list_knowledge_objects() -> dict[str, Any]:
    return {"knowledge_objects": [item.to_dict() for item in evidence_repository.list_knowledge_objects()]}


@app.get("/api/source-ingest/evidence/knowledge-objects/{knowledge_object_id}")
def get_knowledge_object(knowledge_object_id: str) -> dict[str, Any]:
    knowledge_object = evidence_repository.get_knowledge_object(knowledge_object_id)
    if knowledge_object is None:
        raise HTTPException(status_code=404, detail="knowledge object not found")
    return {"knowledge_object": knowledge_object.to_dict()}


@app.get("/api/source-ingest/dlq")
def list_dlq(
    status: Literal["pending", "replayed", "duplicate_skipped", "replay_failed", "schema_rejected"] | None = None,
) -> dict[str, Any]:
    return {"entries": [entry.to_dict() for entry in dead_letter_queue.entries(status=status)]}


@app.post("/api/source-ingest/dlq/replay")
def replay_dlq(request: ReplayDlqRequest) -> dict[str, Any]:
    entries = dead_letter_queue.pending_entries(tag_filter=request.tag or None)
    if request.entry_ids:
        requested = set(request.entry_ids)
        entries = [entry for entry in entries if entry.entry_id in requested]
    actor_ref = ActorRef(ActorType.SERVICE, request.actor_id, roles=("source_ingest_replay",))
    replay_result = replay_processor.replay(
        entries,
        actor_ref=actor_ref,
        environment=scheduler.environment,
        reason=request.reason,
        queue=dead_letter_queue,
        apply_fn=_replay_source_event,
    )
    _append_audit_actions(tuple(result.audit_action for result in replay_result.results))
    return replay_result.to_dict()


@app.put("/api/source-ingest/connectors/{connector_id}/schedule")
def set_connector_schedule(connector_id: str, request: SetScheduleRequest) -> dict[str, Any]:
    config = connector_store.get_config(connector_id)
    if config is None:
        raise HTTPException(status_code=404, detail="connector config not found")
    try:
        schedule = schedule_config_store.upsert_schedule(
            connector_id,
            interval_seconds=request.interval_seconds,
            enabled=request.enabled,
        )
        return {"schedule": schedule.to_dict()}
    except SourceEvidenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/source-ingest/connectors/{connector_id}/schedule")
def get_connector_schedule(connector_id: str) -> dict[str, Any]:
    schedule = schedule_config_store.get_schedule(connector_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="connector schedule not configured")
    return {"schedule": schedule.to_dict()}


@app.post("/api/source-ingest/run-scheduled")
def run_scheduled_connectors() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    schedules = schedule_config_store.list_schedules()
    ran: list[dict[str, Any]] = []
    skipped: list[str] = []
    failed: list[dict[str, Any]] = []

    for sched in schedules:
        if not sched.enabled or sched.interval_seconds <= 0:
            skipped.append(sched.connector_id)
            continue
        watermark = store.get_watermark(sched.connector_id)
        if watermark is not None:
            try:
                last_run = datetime.fromisoformat(watermark.updated_at.replace("Z", "+00:00"))
                elapsed = (now - last_run).total_seconds()
                if elapsed < sched.interval_seconds:
                    skipped.append(sched.connector_id)
                    continue
            except ValueError:
                pass
        config = connector_store.get_config(sched.connector_id)
        if config is None:
            failed.append({"connector_id": sched.connector_id, "error": "connector config not found"})
            continue
        try:
            connector = _register_or_validate_connector(config.connector)
            result, evidence_refs = _run_job(
                connector=connector,
                trace_id=f"scheduled-{sched.connector_id}-{int(now.timestamp())}",
                trigger_type="scheduled",
                fetch_batch=_configured_fetch(sched.connector_id),
            )
            ran.append({
                "connector_id": sched.connector_id,
                "run": result.run.to_dict(),
                "evidence_refs": evidence_refs,
            })
        except (EvidenceValidationError, SourceEvidenceError) as exc:
            failed.append({"connector_id": sched.connector_id, "error": str(exc)})

    return {
        "ran": ran,
        "skipped": skipped,
        "failed": failed,
        "summary": {
            "total_ran": len(ran),
            "total_skipped": len(skipped),
            "total_failed": len(failed),
        },
    }


@app.get("/api/source-ingest/audit")
def list_audit() -> dict[str, Any]:
    return {"actions": _load_audit_actions()}
