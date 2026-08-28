"""Ingest application pipeline service.

Consolidates job execution, record ingestion, scheduled runs, frontier replay,
DLQ resolution, evidence persistence, market data storage persistence, distillation
admission, health/usage updates, and search refresh notifications into one
canonical pipeline service.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from fastapi import HTTPException

from services.foundation import (
    ActorRef,
    ActorType,
    AuditAction,
    DeadLetterQueue,
    DeadLetterReplayProcessor,
    DeadLetterStatus,
    SchemaRegistry,
    TraceContext,
)
from services.knowledge.evidence import (
    EvidenceBundleBuilder,
    EvidenceItem,
    normalize_source_evidence,
    normalize_source_record,
)
from services.knowledge.evidence.models import EvidenceValidationError

from .connectors import (
    ConnectorStatus,
    IngestEvent,
    SourceConnector,
    SourceEvidenceError,
    SourceRecord,
    SourceRecordStatus,
)
from .configured import ConfiguredConnectorFetcher, JsonlConfiguredConnectorStore, JsonlConnectorScheduleStore
from .distillation_worker import DistillationJobQueue
from .external_sources import (
    external_source_bundle_metadata,
    validate_external_source_connector,
    validate_external_source_record,
)
from .ingest_manager import IngestManager
from .market_data_storage import MarketDataStorageWriter
from .requirement_state import LatestMarketSnapshotStore
from .scheduler import IngestBatch, IngestReceipt, IngestionScheduler, JsonlIngestScheduleStore
from .source_health import (
    SourceHealth,
    SourceHealthStatus,
    SourceHealthStore,
    SourceUsageDaily,
    SourceUsageDailyStore,
)
from .api_models import RunScheduledRequest, TriggerIngestJobRequest


def _parse_utc_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def stable_ref(prefix: str, value: str) -> str:
    digest = sha256(value.encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()[:48]
    return f"{prefix}-{slug}-{digest}" if slug else f"{prefix}-{digest}"


def list_metadata(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, list):
        normalized = [str(item).strip() for item in value if str(item).strip()]
        return normalized or default
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return default


def evidence_item_for_record(record: SourceRecord, run: Any) -> EvidenceItem:
    metadata = dict(record.metadata)
    item_id = str(metadata.get("evidence_item_id") or stable_ref("evi", record.source_id))
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
        access_scope=list_metadata(metadata.get("access_scope"), ["public"]),
        trace_refs=trace_refs,
        metadata={**metadata, "source_ingest_run_id": run.ingest_run_id},
    )


def market_raw_refs_for_record(record: SourceRecord, storage_refs: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not storage_refs:
        return []
    dataset = str(record.metadata.get("dataset") or record.metadata.get("source_dataset") or "")
    raw_refs = [dict(ref) for ref in storage_refs.get("raw_refs") or []]
    if not dataset:
        return raw_refs
    matched = [ref for ref in raw_refs if str(ref.get("dataset") or "") == dataset]
    return matched or raw_refs


def compact_bulk_market_record(record: SourceRecord, storage_refs: dict[str, Any] | None) -> SourceRecord:
    if record.source_type.value != "market":
        return record
    metadata = dict(record.metadata)
    removed = []
    for key in ("raw_row", "raw_rows", "raw_payload", "payload", "body"):
        if key in metadata:
            metadata.pop(key, None)
            removed.append(key)
    raw_refs = market_raw_refs_for_record(record, storage_refs)
    if raw_refs:
        metadata["raw_storage_refs"] = raw_refs
    if removed:
        metadata["bulk_payload_redacted_from_evidence"] = True
        metadata["bulk_payload_redacted_fields"] = removed
    return SourceRecord(
        source_id=record.source_id,
        connector_id=record.connector_id,
        source_type=record.source_type.value,
        title=record.title,
        content_ref=record.content_ref,
        status=record.status.value,
        metadata=metadata,
        trace_id=record.trace_id,
        created_at=record.created_at,
    )


def with_source_ingest_run(record: SourceRecord, ingest_run_id: str) -> SourceRecord:
    """Keep the producing run on the durable SourceRecord projection."""
    return SourceRecord(
        source_id=record.source_id,
        connector_id=record.connector_id,
        source_type=record.source_type.value,
        title=record.title,
        content_ref=record.content_ref,
        status=record.status.value,
        metadata={**record.metadata, "source_ingest_run_id": ingest_run_id},
        trace_id=record.trace_id,
        created_at=record.created_at,
    )


def admit_normalized_records_to_distillation(
    distillation_job_queue: DistillationJobQueue,
    normalized_source_records: Iterable[SourceRecord],
) -> dict[str, Any]:
    # Idempotent: same source_id + content digest resolves to the same job,
    # so a later controller catch_up pass over the same version is a no-op.
    # Never raises; catch_up remains the missed-event recovery path for
    # whatever admission could not enqueue here.
    admissions: dict[str, Any] = {}
    for source in normalized_source_records:
        if source.status != SourceRecordStatus.NORMALIZED:
            continue
        try:
            job = distillation_job_queue.enqueue_source_record(
                source,
                requested_by="source-ingest",
            )
        except Exception as exc:  # noqa: BLE001 - ingest completion must remain non-blocking.
            admissions[source.source_id] = {
                "status": "admission_failed",
                "error": str(exc)[:300],
            }
            continue
        admissions[source.source_id] = {
            "status": "admitted",
            "job_id": job.job_id,
            "source_digest": job.source_digest,
        }
    return admissions


def persist_source_evidence_refs(
    manager: IngestManager,
    evidence_repository: Any,
    evidence_builder: EvidenceBundleBuilder,
    distillation_job_queue: DistillationJobQueue,
    result: Any,
    storage_refs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_records = [
        with_source_ingest_run(
            compact_bulk_market_record(record, storage_refs),
            result.run.ingest_run_id,
        )
        for record in result.records
        if not record.is_rejected
    ]
    if not source_records:
        return {
            "source_ids": [],
            "evidence_item_ids": [],
            "evidence_bundle_id": None,
            "knowledge_object_ids": [],
            "distillation_admissions": {},
        }

    connector = manager.get_connector(result.run.connector_id)
    source_records = [
        validate_external_source_record(record, connector=connector)
        for record in source_records
    ]
    normalized_source_records_by_id: dict[str, SourceRecord] = {}
    source_by_evidence_item_id: dict[str, SourceRecord] = {}
    evidence_items_by_id: dict[str, EvidenceItem] = {}
    source_owner_by_dedupe_key: dict[str, SourceRecord] = {}
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
            evidence_item=evidence_item_for_record(record, result.run),
            connector_license_scope=connector_license_scope,
            source_owner_id=source_owner.source_id,
        )
        normalized_source = source_owner if source_owner.source_id != record.source_id else normalized.source_record
        normalized_source_records_by_id[normalized_source.source_id] = normalized_source
        source_by_evidence_item_id[normalized.evidence_item.evidence_item_id] = normalized_source
        evidence_items_by_id[normalized.evidence_item.evidence_item_id] = normalized.evidence_item
    evidence_items = list(evidence_items_by_id.values())
    bundle_metadata = {
        "connector_id": result.run.connector_id,
        "ingest_run_id": result.run.ingest_run_id,
        "trigger_type": result.run.trigger_type,
        "normalization_schema": "source_evidence_normalization.v1",
        **external_source_bundle_metadata(
            list(normalized_source_records_by_id.values()),
            evidence_items,
        ),
    }
    bundle = evidence_builder.build_bundle(
        source_records=list(normalized_source_records_by_id.values()),
        evidence_items=evidence_items,
        summary=f"Source ingest run {result.run.ingest_run_id} persisted {len(source_records)} source record(s).",
        created_by="source-ingest",
        evidence_bundle_id=stable_ref("evbundle", result.run.ingest_run_id),
        metadata=bundle_metadata,
    )
    distillation_admissions = admit_normalized_records_to_distillation(
        distillation_job_queue,
        normalized_source_records_by_id.values(),
    )
    knowledge_object_ids: list[str] = []
    for item in evidence_items:
        record = source_by_evidence_item_id[item.evidence_item_id]
        metadata = dict(record.metadata)
        knowledge_object = evidence_builder.build_knowledge_object(
            knowledge_object_id=str(metadata.get("knowledge_object_id") or stable_ref("ko", item.evidence_item_id)),
            source_record=record,
            evidence_item=item,
            evidence_bundle=bundle,
            title=record.title,
            text=item.body,
            access_scope=item.access_scope,
            keywords=list_metadata(metadata.get("keywords"), []),
            metadata={
                "connector_id": record.connector_id,
                "ingest_run_id": result.run.ingest_run_id,
                "content_ref": record.content_ref,
                "source_dedupe_key": metadata.get("source_dedupe_key"),
                "evidence_dedupe_key": item.metadata.get("evidence_dedupe_key"),
                "evidence_owner_id": item.metadata.get("evidence_owner_id"),
                "available_time": metadata.get("available_time"),
                "entitlement_tags": metadata.get("entitlement_tags"),
                "pit": metadata.get("pit"),
                "governance": metadata.get("governance"),
            },
        )
        knowledge_object_ids.append(knowledge_object.knowledge_object_id)
    return {
        "source_ids": list(normalized_source_records_by_id),
        "evidence_item_ids": [item.evidence_item_id for item in evidence_items],
        "evidence_bundle_id": bundle.evidence_bundle_id,
        "knowledge_object_ids": knowledge_object_ids,
        "distillation_admissions": distillation_admissions,
    }


def persist_market_data_storage_refs(
    manager: IngestManager,
    market_data_storage_writer: MarketDataStorageWriter,
    result: Any,
) -> dict[str, Any]:
    connector = manager.get_connector(result.run.connector_id)
    if connector is None:
        return {
            "schema_version": "market_data_storage_manifest.v1",
            "ingest_run_id": result.run.ingest_run_id,
            "raw_refs": [],
            "normalized_refs": [],
            "feature_refs": [],
            "summary": {"raw_ref_count": 0, "normalized_ref_count": 0, "feature_ref_count": 0, "normalized_row_count": 0},
        }
    return market_data_storage_writer.write_run(result=result, connector=connector).to_dict()


def persist_latest_market_snapshots(
    latest_market_snapshot_store: LatestMarketSnapshotStore,
    result: Any,
) -> dict[str, Any]:
    """Project completed normalized SourceRecords into the read-only paper API."""
    if result.run.status.value != "completed":
        return {
            "schema_version": "source_ingest_latest_market_snapshot_batch.v1",
            "ingest_run_id": result.run.ingest_run_id,
            "accepted_record_count": 0,
            "updated_snapshot_count": 0,
            "snapshots": [],
        }
    return latest_market_snapshot_store.append_normalized_records(
        result.records,
        ingest_run_id=result.run.ingest_run_id,
        observed_at=run_finished_at_iso(result.run),
    )


def run_finished_at_iso(run: Any) -> str:
    value = run.to_dict().get("finished_at") or run.to_dict().get("started_at")
    return str(value or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))


def run_date(run: Any) -> str:
    return run_finished_at_iso(run)[:10]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def record_ingest_usage(source_usage_store: SourceUsageDailyStore, connector_id: str, run: Any) -> None:
    date = run_date(run)
    existing = source_usage_store.get(date, connector_id)
    source_usage_store.upsert(
        SourceUsageDaily(
            date=date,
            source_id=connector_id,
            source_kind="data_source",
            ingest_run_count=(existing.ingest_run_count if existing else 0) + 1,
            query_count=existing.query_count if existing else 0,
            search_hit_count=existing.search_hit_count if existing else 0,
            persona_match_count=existing.persona_match_count if existing else 0,
            strategy_seed_yield_count=existing.strategy_seed_yield_count if existing else 0,
            strategy_promotion_count=existing.strategy_promotion_count if existing else 0,
            experiment_dependency_count=existing.experiment_dependency_count if existing else 0,
            active_strategy_dependency_count=existing.active_strategy_dependency_count if existing else 0,
            cost_estimate=existing.cost_estimate if existing else None,
        )
    )


def health_status_for_run(run: Any) -> str:
    if run.status.value == "completed":
        return SourceHealthStatus.DEGRADED.value if run.rejected_count else SourceHealthStatus.OK.value
    if run.status.value == "rejected":
        return SourceHealthStatus.DEGRADED.value
    return SourceHealthStatus.FAILED.value


def source_error_for_result(result: Any) -> str | None:
    if result.run.status.value == "completed":
        return None
    if result.dlq_entries:
        return str(result.dlq_entries[0].reason)
    event_messages = [event.message for event in result.run.events if event.message]
    return str(event_messages[-1]) if event_messages else f"source ingest run status={result.run.status.value}"


def provider_metadata_from_records(result: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for record in result.records:
        record_metadata = dict(record.metadata)
        for key in (
            "quota",
            "rate_limit",
            "provider_quota",
            "provider_rate_limit",
            "source_error",
            "readback_file_hash",
            "readback_timestamp",
        ):
            if key in record_metadata and key not in metadata:
                metadata[key] = record_metadata[key]
    return metadata


def source_health_outcome(result: Any) -> dict[str, Any]:
    """Project the scheduler's typed receipt truth onto SourceHealth."""
    if result.run.status.value == "completed":
        return {
            "schema_version": "source_health_outcome.v1",
            "classification": "success",
            "category": "success",
            "code": "completed",
            "retryable": False,
        }
    typed_failure = dict(result.typed_failure) if isinstance(result.typed_failure, Mapping) else {}
    category = str(typed_failure.get("category") or "unknown")
    classification = {
        "external_egress": "policy_denial",
        "credential": "credential_unavailable",
        "provider": "provider_failure",
    }.get(category, "failure")
    return {
        "schema_version": "source_health_outcome.v1",
        "classification": classification,
        "category": category,
        "code": str(typed_failure.get("code") or "source_ingest_failed"),
        "error_type": str(typed_failure.get("error_type") or "UnknownSourceFailure"),
        "retryable": bool(typed_failure.get("retryable", False)),
    }


def source_timestamp_state_for_result(
    result: Any,
    *,
    now: datetime | None = None,
    future_tolerance_seconds: int = 300,
) -> tuple[str | None, str]:
    timestamps: list[datetime] = []
    explicit_candidate_seen = False
    for record in result.records:
        metadata = dict(record.metadata)
        normalized_row = metadata.get("normalized_row")
        candidates: list[Any] = []
        if isinstance(normalized_row, Mapping):
            candidates.extend(
                normalized_row.get(key)
                for key in ("available_time", "as_of_time", "event_time", "timestamp", "date")
            )
        candidates.extend(
            metadata.get(key)
            for key in ("available_time", "as_of_time", "event_time", "source_timestamp", "date")
        )
        for candidate in candidates:
            if candidate in (None, ""):
                continue
            explicit_candidate_seen = True
            parsed = _parse_utc_datetime(candidate)
            if parsed is not None:
                timestamps.append(parsed)
                break
    if not timestamps:
        return None, "invalid" if explicit_candidate_seen else "missing"
    latest = max(timestamps).replace(microsecond=0)
    captured_at = now or datetime.now(timezone.utc)
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    captured_at = captured_at.astimezone(timezone.utc)
    status = (
        "future"
        if latest > captured_at + timedelta(seconds=future_tolerance_seconds)
        else "valid"
    )
    return latest.isoformat().replace("+00:00", "Z"), status


def source_timestamp_for_result(result: Any, *, future_tolerance_seconds: int = 300) -> str | None:
    return source_timestamp_state_for_result(result, future_tolerance_seconds=future_tolerance_seconds)[0]


def persist_ingest_receipt(
    store: JsonlIngestScheduleStore,
    result: Any,
    evidence_refs: Mapping[str, Any],
    *,
    status: str | None = None,
    typed_failure: Mapping[str, Any] | None = None,
    future_tolerance_seconds: int = 300,
) -> IngestReceipt:
    run = result.run.to_dict()
    storage_refs = evidence_refs.get("storage_refs")
    source_timestamp, source_timestamp_status = source_timestamp_state_for_result(
        result,
        future_tolerance_seconds=future_tolerance_seconds,
    )
    receipt = IngestReceipt(
        ingest_run_id=result.run.ingest_run_id,
        connector_id=result.run.connector_id,
        status=status or result.run.status.value,
        trigger_type=result.run.trigger_type,
        trace_id=result.run.trace_id,
        started_at=str(run["started_at"]),
        finished_at=run.get("finished_at"),
        raw_count=int(result.run.raw_count or 0),
        normalized_count=int(result.run.normalized_count or 0),
        rejected_count=int(result.run.rejected_count or 0),
        watermark=result.watermark.value if result.watermark else None,
        source_timestamp=source_timestamp,
        source_timestamp_status=source_timestamp_status,
        evidence_refs={key: value for key, value in evidence_refs.items() if key != "storage_refs"},
        storage_refs=dict(storage_refs) if isinstance(storage_refs, Mapping) else {},
        typed_failure=(
            dict(typed_failure)
            if typed_failure is not None
            else (dict(result.typed_failure) if result.typed_failure is not None else None)
        ),
    )
    return store.upsert_receipt(receipt)


def post_processing_typed_failure(exc: Exception, *, stage: str) -> dict[str, Any]:
    return {
        "schema_version": "source_ingest_typed_failure.v1",
        "category": "persistence",
        "code": "post_processing_failed",
        "error_type": type(exc).__name__,
        "retryable": isinstance(exc, OSError),
        "stage": stage,
    }


def search_refresh_event_summary(refresh: dict[str, Any]) -> dict[str, Any]:
    search_service = refresh.get("search_service") if isinstance(refresh.get("search_service"), dict) else {}
    return {
        "schema_version": "source_search_refresh_observed.v1",
        "status": refresh.get("status"),
        "configured": bool(refresh.get("configured")),
        "ingest_run_id": refresh.get("ingest_run_id"),
        "attempted_at": refresh.get("attempted_at"),
        "search_url": refresh.get("search_url"),
        "http_status": refresh.get("http_status"),
        "pipeline_run_id": search_service.get("pipeline_run_id"),
        "freshness_status": search_service.get("freshness_status"),
        "freshness_within_sla": search_service.get("freshness_within_sla"),
        "materialized": search_service.get("materialized"),
        "materialized_matches_completion": search_service.get("materialized_matches_completion"),
        "error": refresh.get("error"),
    }


def record_search_refresh_event(store: JsonlIngestScheduleStore, run: Any, refresh: dict[str, Any]) -> None:
    summary = search_refresh_event_summary(refresh)
    run.events.append(
        IngestEvent(
            event_type="SearchIndexRefreshObserved",
            ingest_run_id=run.ingest_run_id,
            status=run.status,
            trace_id=run.trace_id,
            message=json.dumps(summary, sort_keys=True, separators=(",", ":")),
        )
    )
    store.upsert_run(run)


def notify_search_index_refresh(
    search_ingest_notify_url: str,
    ingest_run_id: str,
    *,
    connector_id: str | None = None,
    source_type: str | None = None,
    trace_id: str | None = None,
    normalized_count: int = 0,
    evidence_refs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST source completion to search service and return a compact observable summary."""
    attempted_at = utc_now_iso()
    summary: dict[str, Any] = {
        "schema_version": "source_search_refresh_notification.v1",
        "ingest_run_id": ingest_run_id,
        "configured": bool(search_ingest_notify_url),
        "status": "not_configured",
        "attempted_at": attempted_at,
        "search_url": search_ingest_notify_url or None,
        "search_service": None,
    }
    if not search_ingest_notify_url:
        return summary
    evidence_refs = evidence_refs or {}
    try:
        payload = json.dumps(
            {
                "ingest_run_id": ingest_run_id,
                "connector_id": connector_id,
                "source_type": source_type,
                "trace_id": trace_id,
                "normalized_count": normalized_count,
                "source_ids": list(evidence_refs.get("source_ids") or []),
                "evidence_bundle_id": evidence_refs.get("evidence_bundle_id"),
                "knowledge_object_ids": list(evidence_refs.get("knowledge_object_ids") or []),
                "materialize": True,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{search_ingest_notify_url}/api/search/index/source-completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as response:  # noqa: S310
            body = response.read().decode("utf-8")
            search_payload = json.loads(body) if body else {}
        truth = search_payload.get("truth") if isinstance(search_payload, dict) else {}
        if not isinstance(truth, dict):
            truth = {}
        summary.update(
            {
                "status": "refreshed" if truth.get("index_refreshed") else "accepted",
                "http_status": response.getcode(),
                "search_service": {
                    "schema_version": "source_search_refresh_service_summary.v1",
                    "pipeline_run_id": truth.get("pipeline_run_id"),
                    "freshness_status": truth.get("freshness_status"),
                    "freshness_within_sla": truth.get("freshness_within_sla"),
                    "materialized": truth.get("materialized"),
                    "materialized_at": truth.get("materialized_at"),
                    "materialized_matches_completion": truth.get("materialized_matches_completion"),
                },
            }
        )
        return summary
    except Exception as exc:  # noqa: BLE001 - ingest completion must remain non-blocking.
        summary.update({"status": "notify_failed", "error": str(exc)[:300]})
        return summary


def result_error(result: Any) -> str:
    if result.dlq_entries:
        return str(result.dlq_entries[0].reason)
    return f"source ingest run ended with status={result.run.status.value}"


class IngestPipelineService:
    """Consolidated ingest execution and post-processing pipeline service."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    @property
    def manager(self) -> IngestManager:
        return self.runtime.manager

    @property
    def store(self) -> JsonlIngestScheduleStore:
        return self.runtime.store

    @property
    def connector_store(self) -> JsonlConfiguredConnectorStore:
        return self.runtime.connector_store

    @property
    def schedule_config_store(self) -> JsonlConnectorScheduleStore:
        return self.runtime.schedule_config_store

    @property
    def configured_fetcher(self) -> ConfiguredConnectorFetcher:
        return self.runtime.configured_fetcher

    @property
    def evidence_repository(self) -> Any:
        return self.runtime.evidence_repository

    @property
    def evidence_builder(self) -> EvidenceBundleBuilder:
        return self.runtime.evidence_builder

    @property
    def dead_letter_queue(self) -> DeadLetterQueue:
        return self.runtime.dead_letter_queue

    @property
    def scheduler(self) -> IngestionScheduler:
        return self.runtime.scheduler

    @property
    def replay_processor(self) -> DeadLetterReplayProcessor:
        return self.runtime.replay_processor

    @property
    def source_health_store(self) -> SourceHealthStore:
        return self.runtime.source_health_store

    @property
    def source_usage_store(self) -> SourceUsageDailyStore:
        return self.runtime.source_usage_store

    @property
    def latest_market_snapshot_store(self) -> LatestMarketSnapshotStore:
        return self.runtime.latest_market_snapshot_store

    @property
    def market_data_storage_writer(self) -> MarketDataStorageWriter:
        return self.runtime.market_data_storage_writer

    @property
    def distillation_job_queue(self) -> DistillationJobQueue:
        return self.runtime.distillation_job_queue

    def inline_fetch(self, records: tuple[SourceRecord, ...], next_watermark: str | None) -> Any:
        return lambda _watermark: IngestBatch(records=records, next_watermark=next_watermark)

    def configured_fetch(
        self,
        connector_id: str,
        *,
        trace_id: str = "",
        job_parameters: dict[str, Any] | None = None,
    ) -> Any:
        return lambda watermark: self.configured_fetcher.fetch_batch(
            connector_id,
            watermark,
            trace_id=trace_id,
            job_parameters=job_parameters,
        )

    def update_source_health_and_usage(
        self,
        *,
        connector: SourceConnector,
        result: Any,
        storage_refs: dict[str, Any],
    ) -> None:
        finished_at = run_finished_at_iso(result.run)
        existing = self.source_health_store.get(connector.connector_id)
        previous_failures = int((existing.metadata.get("failure_count") if existing else 0) or 0)
        status = health_status_for_run(result.run)
        source_error = source_error_for_result(result)
        failure_count = previous_failures + (0 if result.run.status.value == "completed" else 1)
        fetch_state = self.connector_store.get_fetch_state(connector.connector_id)
        config = self.connector_store.get_config(connector.connector_id)
        total_attempts = max(1, int(fetch_state.get("attempts") or 0))
        failed_attempts = int(fetch_state.get("failed_attempts") or 0)
        watermark = result.watermark.value if result.watermark else None
        health = SourceHealth(
            source_id=connector.connector_id,
            source_kind="data_source",
            status=status,
            last_success_at=finished_at if result.run.status.value == "completed" else (existing.last_success_at if existing else None),
            last_failure_at=finished_at if result.run.status.value != "completed" else (existing.last_failure_at if existing else None),
            latest_watermark=watermark,
            row_count_last_run=int(result.run.normalized_count or 0),
            rejected_count_last_run=int(result.run.rejected_count or 0),
            schema_hash=self.runtime._connector_schema_hash(connector, config.fetch if config else None),
            staleness_seconds=self.runtime._connector_freshness_summary(connector.connector_id).get("staleness_seconds"),
            error_rate_7d=min(1.0, failed_attempts / total_attempts),
            cost_estimate_30d=existing.cost_estimate_30d if existing else None,
            metadata={
                **(dict(existing.metadata) if existing else {}),
                "last_ingest_run_id": result.run.ingest_run_id,
                "last_run_status": result.run.status.value,
                "last_outcome": source_health_outcome(result),
                "source_error": source_error,
                "failure_count": failure_count,
                "storage_refs": storage_refs,
                **provider_metadata_from_records(result),
            },
        )
        self.source_health_store.upsert(health)
        record_ingest_usage(self.source_usage_store, connector.connector_id, result.run)

    def result_payload(
        self,
        result: Any,
        evidence_refs: dict[str, Any] | None = None,
        source_search_refresh: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        receipt = self.store.get_receipt(result.run.ingest_run_id)
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
            "storage_refs": (evidence_refs or {}).get("storage_refs"),
            "source_search_refresh": source_search_refresh,
            "dlq_entries": [entry.to_dict() for entry in result.dlq_entries],
            "audit_actions": [action.to_dict() for action in result.audit_actions],
            "frontier_id": getattr(result, "frontier_id", None),
            "receipt": receipt.to_dict() if receipt is not None else None,
        }

    def run_job(
        self,
        *,
        connector: SourceConnector,
        trace_id: str,
        trigger_type: str,
        fetch_batch: Any,
        frontier_id: str | None = None,
    ) -> tuple[Any, dict[str, Any], dict[str, Any] | None]:
        self.runtime._assert_connector_lifecycle_allows_run(connector)
        result = self.scheduler.run_once(
            connector_id=connector.connector_id,
            trace_id=trace_id,
            trigger_type=trigger_type,
            fetch_batch=fetch_batch,
            frontier_id=frontier_id,
        )
        evidence_refs: dict[str, Any] = {
            "source_ids": [],
            "evidence_item_ids": [],
            "evidence_bundle_id": None,
            "knowledge_object_ids": [],
            "storage_refs": {},
        }
        initial_receipt_status = "processing" if result.run.status.value == "completed" else result.run.status.value
        persist_ingest_receipt(
            self.store,
            result,
            evidence_refs,
            status=initial_receipt_status,
            future_tolerance_seconds=self.runtime.SOURCE_TIMESTAMP_FUTURE_TOLERANCE_SECONDS,
        )
        post_processing_stage = "audit"
        try:
            self.runtime._append_audit_actions(result.audit_actions)
            post_processing_stage = "market_storage"
            storage_refs = persist_market_data_storage_refs(self.manager, self.market_data_storage_writer, result)
            evidence_refs["storage_refs"] = storage_refs
            post_processing_stage = "latest_market_snapshot"
            market_snapshots = persist_latest_market_snapshots(self.latest_market_snapshot_store, result)
            evidence_refs["market_snapshots"] = market_snapshots
            post_processing_stage = "source_evidence"
            evidence_refs = persist_source_evidence_refs(
                self.manager,
                self.evidence_repository,
                self.evidence_builder,
                self.distillation_job_queue,
                result,
                storage_refs=storage_refs,
            )
            evidence_refs["storage_refs"] = storage_refs
            evidence_refs["market_snapshots"] = market_snapshots
            post_processing_stage = "source_health_usage"
            self.update_source_health_and_usage(connector=connector, result=result, storage_refs=storage_refs)
            post_processing_stage = "receipt_finalize"
            persist_ingest_receipt(
                self.store,
                result,
                evidence_refs,
                future_tolerance_seconds=self.runtime.SOURCE_TIMESTAMP_FUTURE_TOLERANCE_SECONDS,
            )
        except Exception as exc:
            persist_ingest_receipt(
                self.store,
                result,
                evidence_refs,
                status="failed",
                typed_failure=post_processing_typed_failure(exc, stage=post_processing_stage),
                future_tolerance_seconds=self.runtime.SOURCE_TIMESTAMP_FUTURE_TOLERANCE_SECONDS,
            )
            raise
        source_search_refresh: dict[str, Any] | None = None
        if result.run.status.value == "completed":
            source_search_refresh = notify_search_index_refresh(
                self.runtime.SEARCH_INGEST_NOTIFY_URL,
                result.run.ingest_run_id,
                connector_id=connector.connector_id,
                source_type=connector.source_type.value,
                trace_id=result.run.trace_id,
                normalized_count=result.run.normalized_count,
                evidence_refs=evidence_refs,
            )
            record_search_refresh_event(self.store, result.run, source_search_refresh)
        return result, evidence_refs, source_search_refresh

    def run_ingest_request(self, request: TriggerIngestJobRequest) -> dict[str, Any]:
        if len(request.records) > self.runtime.MAX_RECORDS_PER_JOB:
            raise HTTPException(
                status_code=413,
                detail=f"records exceeds SOURCE_INGEST_MAX_RECORDS={self.runtime.MAX_RECORDS_PER_JOB}",
            )

        try:
            connector = self.runtime._connector_for_job(request)
            if request.records:
                records = tuple(
                    validate_external_source_record(record.to_domain(), connector=connector)
                    for record in request.records
                )
                for record in records:
                    if record.connector_id != connector.connector_id:
                        raise SourceEvidenceError("record connector_id must match job connector")
                    if record.source_type != connector.source_type:
                        raise SourceEvidenceError("record source_type must match job connector")
                fetch_batch = self.inline_fetch(records, request.next_watermark)
            else:
                fetch_batch = self.configured_fetch(
                    connector.connector_id,
                    trace_id=request.trace_id,
                    job_parameters=request.job_parameters,
                )
            result, evidence_refs, source_search_refresh = self.run_job(
                connector=connector,
                trace_id=request.trace_id,
                trigger_type=request.trigger_type,
                fetch_batch=fetch_batch,
            )
            return self.result_payload(result, evidence_refs, source_search_refresh)
        except (EvidenceValidationError, SourceEvidenceError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def run_frontier_item(
        self,
        item: Any,
        *,
        resolve_correlated_dlq: bool = True,
    ) -> tuple[Any, dict[str, Any], Any, dict[str, Any] | None]:
        config = self.connector_store.get_config(item.connector_id)
        if config is None:
            updated = self.store.fail_frontier(
                item.frontier_id,
                error="connector config not found",
                backoff_seconds=self.runtime.FRONTIER_BACKOFF_SECONDS,
            )
            raise SourceEvidenceError(f"Connector fetch is not configured: {item.connector_id}; frontier={updated.status}")
        connector = self.runtime._register_or_validate_connector(config.connector)
        try:
            result, evidence_refs, source_search_refresh = self.run_job(
                connector=connector,
                trace_id=item.trace_id or f"frontier-{item.frontier_id}",
                trigger_type=item.trigger_type,
                fetch_batch=self.configured_fetch(
                    item.connector_id,
                    trace_id=item.trace_id or f"frontier-{item.frontier_id}",
                    job_parameters=dict(item.job_parameters),
                ),
                frontier_id=item.frontier_id,
            )
        except Exception as exc:
            updated = self.store.fail_frontier(
                item.frontier_id,
                error=str(exc),
                backoff_seconds=self.runtime.FRONTIER_BACKOFF_SECONDS,
            )
            raise SourceEvidenceError(f"frontier run failed before ingest result persisted: {updated.last_error}") from exc

        if result.run.status.value == "completed":
            updated = self.store.complete_frontier(item.frontier_id, ingest_run_id=result.run.ingest_run_id)
            if resolve_correlated_dlq:
                self.resolve_pending_dlq_for_recovery(
                    frontier_id=item.frontier_id,
                    connector_id=item.connector_id,
                    recovery_ingest_run_id=result.run.ingest_run_id,
                )
        else:
            updated = self.store.fail_frontier(
                item.frontier_id,
                error=result_error(result),
                backoff_seconds=self.runtime.FRONTIER_BACKOFF_SECONDS,
                ingest_run_id=result.run.ingest_run_id,
            )
        return result, evidence_refs, updated, source_search_refresh

    def unresolved_dead_letter_entries(self, *, tag_filter: str | None = None) -> list[Any]:
        entries: list[Any] = []
        for status in (
            DeadLetterStatus.PENDING,
            DeadLetterStatus.REPLAY_FAILED,
            DeadLetterStatus.SCHEMA_REJECTED,
        ):
            entries.extend(self.dead_letter_queue.entries(status=status, tag_filter=tag_filter))
        return entries

    def resolve_pending_dlq_for_recovery(
        self,
        *,
        frontier_id: str,
        connector_id: str,
        recovery_ingest_run_id: str,
    ) -> list[dict[str, Any]]:
        recovery_run = self.store.get_run(recovery_ingest_run_id)
        frontier = self.store.get_frontier(frontier_id)
        if (
            recovery_run is None
            or recovery_run.status.value != "completed"
            or recovery_run.connector_id != connector_id
            or frontier is None
            or frontier.connector_id != connector_id
            or frontier.status != "done"
            or frontier.ingest_run_id != recovery_ingest_run_id
        ):
            return []
        resolved: list[dict[str, Any]] = []
        for entry in self.unresolved_dead_letter_entries(tag_filter="retry_exhausted"):
            if entry.event.event_type != "source_ingestion.scheduled_run_failed":
                continue
            event_frontier_id = str(entry.event.payload.get("frontier_id") or "").strip()
            event_connector_id = str(entry.event.payload.get("connector_id") or "").strip()
            if event_frontier_id != frontier_id or event_connector_id != connector_id:
                continue
            failed_ingest_run_id = str(entry.event.payload.get("ingest_run_id") or "").strip()
            failed_run = self.store.get_run(failed_ingest_run_id)
            if (
                not failed_ingest_run_id
                or entry.event.aggregate_type != "source_ingest_run"
                or entry.event.aggregate_id != failed_ingest_run_id
                or failed_run is None
                or failed_run.connector_id != connector_id
                or failed_run.status.value != "failed"
            ):
                continue
            audit = AuditAction.record(
                actor_ref=self.scheduler.actor_ref,
                action_type="source_ingestion.scheduled_run.recovered",
                target_ref=f"dead_letter:{entry.entry_id}",
                environment=self.scheduler.environment,
                reason="durable crawl frontier retry completed the dead-lettered source run",
                trace=entry.event.trace,
                payload={
                    "entry_id": entry.entry_id,
                    "frontier_id": frontier_id,
                    "connector_id": connector_id,
                    "failed_ingest_run_id": failed_ingest_run_id,
                    "recovery_ingest_run_id": recovery_ingest_run_id,
                },
                before_state_ref=f"dead_letter:{entry.entry_id}:{entry.status.value}",
                after_state_ref=f"source_ingest_run:{recovery_ingest_run_id}:completed",
                metadata={
                    "dead_letter_entry_id": entry.entry_id,
                    "frontier_id": frontier_id,
                    "failed_ingest_run_id": failed_ingest_run_id,
                    "recovery_ingest_run_id": recovery_ingest_run_id,
                },
            )
            audit = replace(
                audit,
                action_id=(
                    "audit-source-recovery-"
                    + sha256(
                        f"{entry.entry_id}:{frontier_id}:{recovery_ingest_run_id}".encode("utf-8")
                    ).hexdigest()
                ),
            )
            updated = entry.with_replay_result(status=DeadLetterStatus.REPLAYED, audit_action=audit)
            self.runtime._append_audit_actions((audit,))
            self.dead_letter_queue.replace_entry(updated)
            resolved.append(
                {
                    "entry_id": entry.entry_id,
                    "frontier_id": frontier_id,
                    "recovery_ingest_run_id": recovery_ingest_run_id,
                    "status": updated.status.value,
                }
            )
        return resolved

    def resolve_pending_dlq_for_completed_frontiers(self) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        pending_frontier_ids = {
            str(entry.event.payload.get("frontier_id") or "").strip()
            for entry in self.unresolved_dead_letter_entries(tag_filter="retry_exhausted")
            if entry.event.event_type == "source_ingestion.scheduled_run_failed"
        }
        for event_frontier_id in sorted(pending_frontier_ids):
            if not event_frontier_id:
                continue
            frontier = self.store.get_frontier(event_frontier_id)
            if frontier is None or frontier.status != "done" or not frontier.ingest_run_id:
                continue
            resolved.extend(
                self.resolve_pending_dlq_for_recovery(
                    frontier_id=event_frontier_id,
                    connector_id=frontier.connector_id,
                    recovery_ingest_run_id=frontier.ingest_run_id,
                )
            )
        return resolved

    def replay_source_event(self, event: Any) -> str:
        if event.event_type != "source_ingestion.scheduled_run_failed":
            raise SourceEvidenceError(f"Unsupported source-ingest DLQ replay event: {event.event_type}")
        connector_id = str(event.payload.get("connector_id") or "").strip()
        if not connector_id:
            raise SourceEvidenceError("DLQ replay event is missing connector_id")
        frontier_id = str(event.payload.get("frontier_id") or "").strip()
        if frontier_id:
            self.store.replay_frontier(frontier_id, trace_id=event.trace_id)
            item = self.store.claim_frontier(frontier_id)
            result, _evidence_refs, updated, _source_search_refresh = self.run_frontier_item(item)
            if result.run.status.value != "completed":
                raise SourceEvidenceError(
                    f"DLQ replay did not complete frontier {frontier_id}: run={result.run.status.value} frontier={updated.status}"
                )
            return f"crawl_frontier:{frontier_id}:source_ingest_run:{result.run.ingest_run_id}"
        config = self.connector_store.get_config(connector_id)
        if config is None:
            raise SourceEvidenceError(f"Connector fetch is not configured: {connector_id}")
        connector = self.runtime._register_or_validate_connector(config.connector)
        result, _evidence_refs, _source_search_refresh = self.run_job(
            connector=connector,
            trace_id=event.trace_id,
            trigger_type="dlq_replay",
            fetch_batch=self.configured_fetch(connector.connector_id, trace_id=event.trace_id),
        )
        if result.run.status.value != "completed":
            raise SourceEvidenceError(f"DLQ replay did not complete ingest run: {result.run.status.value}")
        return f"source_ingest_run:{result.run.ingest_run_id}"

    def run_scheduled_connectors(self, request: RunScheduledRequest | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        now_iso = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        max_concurrency = (
            request.max_concurrency
            if request and request.max_concurrency is not None
            else self.runtime.SCHEDULER_MAX_CONCURRENCY
        )
        force_connector_ids = {
            str(connector_id).strip()
            for connector_id in (request.force_connector_ids if request else [])
            if str(connector_id).strip()
        }
        exclusive_connector_ids = {
            str(connector_id).strip()
            for connector_id in (request.exclusive_connector_ids if request else [])
            if str(connector_id).strip()
        }
        if exclusive_connector_ids and force_connector_ids - exclusive_connector_ids:
            raise HTTPException(
                status_code=400,
                detail="force_connector_ids must stay within exclusive_connector_ids",
            )
        force_connector_ids.update(exclusive_connector_ids)
        if max_concurrency < 1:
            raise HTTPException(status_code=400, detail="max_concurrency must be >= 1")
        if max_concurrency > self.runtime.SCHEDULER_MAX_CONCURRENCY:
            raise HTTPException(
                status_code=400,
                detail=f"max_concurrency exceeds SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY={self.runtime.SCHEDULER_MAX_CONCURRENCY}",
            )
        schedules = self.schedule_config_store.list_schedules()
        enqueued: list[dict[str, Any]] = []
        ran: list[dict[str, Any]] = []
        skipped: list[str] = []
        excluded: list[str] = []
        failed: list[dict[str, Any]] = []
        resolved_dlq = self.resolve_pending_dlq_for_completed_frontiers()
        recovered_frontier = self.store.recover_stale_running(
            timeout_seconds=self.runtime.FRONTIER_RUNNING_TIMEOUT_SECONDS,
            now=now_iso,
        )
        active_frontier_connector_ids = {
            item.connector_id
            for item in self.store.list_frontier()
            if item.status in {"queued", "running", "retry"}
        }
        configured_schedule_ids = {schedule.connector_id for schedule in schedules}
        for connector_id in sorted(exclusive_connector_ids - configured_schedule_ids):
            failed.append({"connector_id": connector_id, "error": "exclusively selected connector schedule not found"})
        for connector_id in sorted(force_connector_ids - configured_schedule_ids - exclusive_connector_ids):
            failed.append({"connector_id": connector_id, "error": "forced connector schedule not found"})

        for sched in schedules:
            if exclusive_connector_ids and sched.connector_id not in exclusive_connector_ids:
                excluded.append(sched.connector_id)
                continue
            if not sched.enabled or sched.interval_seconds <= 0:
                if sched.connector_id in exclusive_connector_ids:
                    failed.append({
                        "connector_id": sched.connector_id,
                        "error": "exclusively selected connector schedule is disabled",
                    })
                else:
                    skipped.append(sched.connector_id)
                continue
            if sched.connector_id in active_frontier_connector_ids:
                skipped.append(sched.connector_id)
                continue
            watermark = self.store.get_watermark(sched.connector_id)
            if watermark is not None and sched.connector_id not in force_connector_ids:
                try:
                    last_run = datetime.fromisoformat(watermark.updated_at.replace("Z", "+00:00"))
                    elapsed = (now - last_run).total_seconds()
                    if elapsed < sched.interval_seconds:
                        skipped.append(sched.connector_id)
                        continue
                except ValueError:
                    pass
            config = self.connector_store.get_config(sched.connector_id)
            if config is None:
                failed.append({"connector_id": sched.connector_id, "error": "connector config not found"})
                continue
            if config.connector.status == ConnectorStatus.DISABLED:
                if sched.connector_id in exclusive_connector_ids:
                    failed.append({
                        "connector_id": sched.connector_id,
                        "error": "exclusively selected connector is disabled",
                    })
                else:
                    skipped.append(sched.connector_id)
                continue
            try:
                self.runtime._register_or_validate_connector(config.connector)
                frontier = self.store.enqueue_frontier(
                    connector_id=sched.connector_id,
                    trace_id=f"scheduled-{sched.connector_id}-{int(now.timestamp())}",
                    trigger_type="scheduled",
                    max_attempts=self.runtime.FRONTIER_MAX_ATTEMPTS,
                    available_at=now_iso,
                )
                enqueued.append(frontier.to_dict())
                active_frontier_connector_ids.add(sched.connector_id)
            except (EvidenceValidationError, SourceEvidenceError) as exc:
                failed.append({"connector_id": sched.connector_id, "error": str(exc)})

        claimed = self.store.claim_due_frontier(
            limit=max_concurrency,
            now=now_iso,
            connector_ids=exclusive_connector_ids or None,
        )
        for frontier in claimed:
            try:
                result, evidence_refs, updated_frontier, source_search_refresh = self.run_frontier_item(frontier)
                payload = {
                    "connector_id": frontier.connector_id,
                    "frontier": updated_frontier.to_dict(),
                    "run": result.run.to_dict(),
                    "evidence_refs": evidence_refs,
                    "source_search_refresh": source_search_refresh,
                    "receipt": (
                        self.store.get_receipt(result.run.ingest_run_id).to_dict()
                        if self.store.get_receipt(result.run.ingest_run_id) is not None
                        else None
                    ),
                }
                if result.run.status.value != "completed":
                    failed.append({**payload, "error": result_error(result)})
                else:
                    ran.append(payload)
            except (EvidenceValidationError, SourceEvidenceError) as exc:
                latest = self.store.get_frontier(frontier.frontier_id)
                failed.append(
                    {
                        "connector_id": frontier.connector_id,
                        "frontier": latest.to_dict() if latest else frontier.to_dict(),
                        "error": str(exc),
                    }
                )

        if exclusive_connector_ids:
            accounted_connector_ids = {
                str(item.get("connector_id") or "")
                for item in [*ran, *failed]
                if isinstance(item, Mapping)
            }
            claimed_connector_ids = {item.connector_id for item in claimed}
            for connector_id in sorted(exclusive_connector_ids - accounted_connector_ids - claimed_connector_ids):
                failed.append({
                    "connector_id": connector_id,
                    "error": "exclusively selected connector was not runnable",
                })

        return {
            "enqueued": enqueued,
            "claimed": [item.to_dict() for item in claimed],
            "ran": ran,
            "skipped": skipped,
            "excluded": excluded,
            "failed": failed,
            "recovered_frontier": [item.to_dict() for item in recovered_frontier],
            "resolved_dlq": resolved_dlq,
            "summary": {
                "total_ran": len(ran),
                "total_skipped": len(skipped),
                "total_failed": len(failed),
                "total_enqueued": len(enqueued),
                "max_concurrency": max_concurrency,
                "forced_connector_count": len(force_connector_ids),
                "exclusive_connector_count": len(exclusive_connector_ids),
                "total_excluded": len(excluded),
                "recovered_frontier_count": len(recovered_frontier),
                "resolved_dlq_count": len(resolved_dlq),
            },
        }
