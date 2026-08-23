"""Deployable source-ingest service boundary.

The service supports both the initial bounded already-fetched record wrapper
and the baseline autonomous path: callers can persist a connector fetch
configuration, then trigger an ingest run by connector id. The wrapper applies
the governed ingest lifecycle, persisted watermarks, durable source/evidence
refs, DLQ routing, and audit replay contract from the source_ingestion library.
"""

from __future__ import annotations

import fcntl
import json
import hmac
import os
import re
import threading
import urllib.parse
import urllib.request
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from fastapi import FastAPI, Header, HTTPException
try:
    from pydantic import BaseModel, ConfigDict, Field
except ImportError:  # pragma: no cover - compatibility with older pydantic.
    from pydantic import BaseModel, Field

    ConfigDict = None  # type: ignore[assignment]

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
from services.foundation.health import register_fastapi_health_routes
from services.knowledge.evidence import EvidenceBundleBuilder, EvidenceItem, normalize_source_evidence, normalize_source_record
from services.knowledge.evidence.models import EvidenceValidationError
from services.source_search_posture import require_source_search_posture

from .active_universe import (
    DEFAULT_SOURCE_UPDATE_RULES,
    ActiveUniverseMember,
    SourceUpdateRule,
    UniverseTier,
    build_active_universe_job_fanout,
    build_active_universe_update_plan,
)
from .registry.proposals import (
    ProposalStatus,
    ProposalType,
    ProposedSourceInfo,
    SourceChangeProposal,
    SourceChangeProposalError,
    SourceChangeProposalStore,
    SourceKind,
)
from .registry.llm_proposal_adapter import LLMSourceProposalAdapter
from .connectors import (
    AuthType,
    ConnectorMode,
    ConnectorStatus,
    IngestEvent,
    SourceConnector,
    SourceEvidenceError,
    SourceRecord,
    SourceRecordStatus,
    SourceType,
    example_provider_catalog,
)
from .configured import ConfiguredConnectorFetcher, JsonlConfiguredConnectorStore, JsonlConnectorScheduleStore
from .distillation_worker import DistillationJobQueue
from .external_sources import (
    external_source_bundle_metadata,
    validate_external_source_connector,
    validate_external_source_record,
)
from .financial_source_catalog import financial_data_source_catalog_payload
from .ingest_manager import IngestManager
from .market_data_storage import MarketDataStorageWriter
from .pg_store import build_source_evidence_repository
from .policy_registry import crawler_policy_for_connector, policy_registry_payload
from .controller_state import ControllerStateError, ControllerStateStore, read_controller_state
from .controller_auth import load_controller_token
from .persona_source_reconciler import RECONCILIATION_METADATA_KEY, SourceProvisioningReconciler
from .process_lock import exclusive_file_lock
from .requirement_state import (
    LatestMarketSnapshotStore,
    MarketSnapshotStateError,
    RequirementSnapshotStore,
    RequirementStateError,
)
from .scheduler import IngestBatch, IngestReceipt, IngestionScheduler, JsonlIngestScheduleStore
from .source_health import (
    SourceHealth,
    SourceHealthError,
    SourceHealthStatus,
    SourceHealthStore,
    SourceUsageDaily,
    SourceUsageDailyStore,
)
from .retirement_engine import RecommendationType, RetirementRecommendation, compute_recommendations
from .connector_coverage_matrix import build_coverage_matrix, build_source_alerts
from .gap_report import generate_market_data_gap_report, render_gap_report_markdown


def _resolve_data_dir() -> Path:
    data_dir = Path(os.getenv("SOURCE_INGEST_DATA_DIR", "/tmp/pantheon/source-ingest"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


DATA_DIR = _resolve_data_dir()
PROPOSAL_STORE_PATH = Path(os.getenv("SOURCE_INGEST_PROPOSAL_STORE_PATH", str(DATA_DIR / "source_change_proposals.jsonl")))
SCHEDULE_STORE_PATH = Path(os.getenv("SOURCE_INGEST_STORE_PATH", str(DATA_DIR / "ingest_schedule.jsonl")))
CONNECTOR_STORE_PATH = Path(os.getenv("SOURCE_INGEST_CONNECTOR_STORE_PATH", str(DATA_DIR / "connector_config.jsonl")))
SOURCE_EVIDENCE_STORE_PATH = Path(os.getenv("SOURCE_INGEST_EVIDENCE_STORE_PATH", str(DATA_DIR / "source_evidence.jsonl")))
DLQ_STORE_PATH = Path(os.getenv("SOURCE_INGEST_DLQ_PATH", str(DATA_DIR / "source_ingest_dlq.jsonl")))
AUDIT_STORE_PATH = Path(os.getenv("SOURCE_INGEST_AUDIT_PATH", str(DATA_DIR / "source_ingest_audit.jsonl")))
CONNECTOR_SCHEDULE_CONFIG_PATH = Path(os.getenv("SOURCE_INGEST_SCHEDULE_CONFIG_PATH", str(DATA_DIR / "connector_schedule.jsonl")))
SOURCE_HEALTH_STORE_PATH = Path(os.getenv("SOURCE_INGEST_HEALTH_STORE_PATH", str(DATA_DIR / "source_health.jsonl")))
SOURCE_USAGE_STORE_PATH = Path(os.getenv("SOURCE_INGEST_USAGE_STORE_PATH", str(DATA_DIR / "source_usage_daily.jsonl")))
MARKET_DATA_STORAGE_ROOT = Path(os.getenv("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT", str(DATA_DIR / "market_data_store")))
CONTROLLER_STATE_PATH = Path(
    os.getenv("SOURCE_INGEST_CONTROLLER_STATE_PATH", str(DATA_DIR / "controller_state.json"))
)
REQUIREMENT_STATE_PATH = Path(
    os.getenv("SOURCE_INGEST_REQUIREMENT_STATE_PATH", str(DATA_DIR / "requirement_snapshots.jsonl"))
)
LATEST_MARKET_SNAPSHOT_PATH = Path(
    os.getenv(
        "SOURCE_INGEST_LATEST_MARKET_SNAPSHOT_PATH",
        str(DATA_DIR / "latest_market_snapshots.jsonl"),
    )
)
RECONCILE_TRANSACTION_LOCK_PATH = Path(
    os.getenv(
        "SOURCE_INGEST_RECONCILE_TRANSACTION_LOCK_PATH",
        str(DATA_DIR / "persona_source_reconcile.lock"),
    )
)
CONTROLLER_TOKEN_PATH = Path(
    os.getenv("SOURCE_INGEST_CONTROLLER_TOKEN_FILE", str(DATA_DIR / "controller_token"))
)
DISTILLATION_JOB_QUEUE_PATH = Path(
    os.getenv("DISTILLATION_JOB_QUEUE_PATH", str(DATA_DIR / "distillation_job_queue.sqlite3"))
)
SOURCE_RECORD_SCHEMA_PATH = Path(__file__).with_name("source_record.schema.json")
MAX_RECORDS_PER_JOB = int(os.getenv("SOURCE_INGEST_MAX_RECORDS", "100"))
SCHEDULER_MAX_CONCURRENCY = max(1, int(os.getenv("SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY", "2")))
FRONTIER_MAX_ATTEMPTS = max(1, int(os.getenv("SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS", "2")))
FRONTIER_BACKOFF_SECONDS = max(0, int(os.getenv("SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS", "60")))
FRONTIER_RUNNING_TIMEOUT_SECONDS = max(
    1,
    int(os.getenv("SOURCE_INGEST_FRONTIER_RUNNING_TIMEOUT_SECONDS", "300")),
)
DEFAULT_STALE_THRESHOLD_SECONDS = max(
    1,
    int(os.getenv("SOURCE_INGEST_DEFAULT_STALE_THRESHOLD_SECONDS", "86400")),
)
MARKET_SNAPSHOT_MAX_CLOSES = max(
    2,
    int(os.getenv("SOURCE_INGEST_MARKET_SNAPSHOT_MAX_CLOSES", "60")),
)
SOURCE_TIMESTAMP_FUTURE_TOLERANCE_SECONDS = max(
    0,
    int(os.getenv("SOURCE_INGEST_FUTURE_TIMESTAMP_TOLERANCE_SECONDS", "300")),
)
# Optional: when set, notify search service after successful ingest runs (fire-and-forget).
SEARCH_INGEST_NOTIFY_URL = os.getenv("SEARCH_INGEST_NOTIFY_URL", "").rstrip("/")
PRODUCTION_POSTURE = require_source_search_posture("source-ingest")

app = FastAPI(title="Pantheon Source Ingest Service", version="0.1.0")
manager = IngestManager()
proposal_store = SourceChangeProposalStore.from_jsonl(PROPOSAL_STORE_PATH)
llm_proposal_adapter = LLMSourceProposalAdapter(proposal_store)
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
source_health_store = SourceHealthStore.from_jsonl(SOURCE_HEALTH_STORE_PATH)
source_usage_store = SourceUsageDailyStore.from_jsonl(SOURCE_USAGE_STORE_PATH)
requirement_snapshot_store = RequirementSnapshotStore(REQUIREMENT_STATE_PATH)
latest_market_snapshot_store = LatestMarketSnapshotStore(
    LATEST_MARKET_SNAPSHOT_PATH,
    max_closes=MARKET_SNAPSHOT_MAX_CLOSES,
)
controller_token = load_controller_token(token_path=CONTROLLER_TOKEN_PATH, create=True)
authoritative_reconcile_lock = threading.RLock()
audit_store_lock = threading.RLock()
source_execution_lock = threading.RLock()
market_data_storage_writer = MarketDataStorageWriter(MARKET_DATA_STORAGE_ROOT)
# Shared with the strategy-distillation controller via DISTILLATION_JOB_QUEUE_PATH:
# admitting a normalized SourceRecord commit here lets the controller's run_pending
# pick it up immediately instead of waiting for its next catch_up poll.
distillation_job_queue = DistillationJobQueue(DISTILLATION_JOB_QUEUE_PATH)
register_fastapi_health_routes(
    app,
    "pantheon-source-ingest",
    dependencies=lambda: {
        "source_search_posture": PRODUCTION_POSTURE.to_dict(),
        "source_freshness": _source_freshness_readiness(),
    },
    metrics=lambda: _source_runtime_metrics(),
    details=lambda: {
        "store_path": str(SCHEDULE_STORE_PATH),
        "connector_store_path": str(CONNECTOR_STORE_PATH),
        "source_evidence_path": str(SOURCE_EVIDENCE_STORE_PATH),
        "market_data_storage_root": str(MARKET_DATA_STORAGE_ROOT),
        "latest_market_snapshot_path": str(LATEST_MARKET_SNAPSHOT_PATH),
        "market_snapshot_max_closes": MARKET_SNAPSHOT_MAX_CLOSES,
        "dlq_path": str(DLQ_STORE_PATH),
        "audit_path": str(AUDIT_STORE_PATH),
        "scheduler_max_concurrency": SCHEDULER_MAX_CONCURRENCY,
        "frontier_max_attempts": FRONTIER_MAX_ATTEMPTS,
        "frontier_backoff_seconds": FRONTIER_BACKOFF_SECONDS,
        "source_search_posture": PRODUCTION_POSTURE.to_dict(),
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
    mode: Literal["static_records", "external_feed", "provider_owned_adapter"] = "static_records"
    records: list[ConfiguredFetchRecordBody] = Field(default_factory=list)
    url: str | None = None
    allowed_url_prefixes: list[str] = Field(default_factory=list)
    timeout_seconds: float = 5.0
    max_bytes: int = 1_000_000
    max_records: int = 100
    default_access_scope: list[str] = Field(default_factory=lambda: ["public"])
    respect_robots_txt: bool = True
    network_scope: Literal["external", "internal_service"] = "external"
    adapter: str | None = None
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    request: dict[str, Any] = Field(default_factory=dict)
    allow_empty: bool = False
    empty_reason: str = ""
    next_watermark: str | None = None
    fail_until_attempt: int = 0
    failure_reason: str = "configured connector fetch failed"

    def to_config(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "mode": self.mode,
            "records": [record.to_config() for record in self.records],
            "next_watermark": self.next_watermark,
            "allow_empty": self.allow_empty,
            "empty_reason": self.empty_reason,
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
                    "respect_robots_txt": self.respect_robots_txt,
                    "network_scope": self.network_scope,
                }
            )
        if self.mode == "provider_owned_adapter":
            payload.update(
                {
                    "adapter": self.adapter,
                    "adapter_config": self.adapter_config,
                    "request": self.request,
                    "max_records": self.max_records,
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
    job_parameters: dict[str, Any] = Field(default_factory=dict)


class SourceRecordIngestRequest(StrictBaseModel):
    connector: ConnectorBody | None = None
    connector_id: str | None = None
    trace_id: str
    trigger_type: str = "manual"
    records: list[SourceRecordBody] = Field(default_factory=list)
    next_watermark: str | None = None


class ReplayDlqRequest(StrictBaseModel):
    tag: str = "retry_exhausted"
    entry_ids: list[str] = Field(default_factory=list)
    reason: str = "operator-approved source ingest DLQ replay"
    actor_id: str = "source-ingest-operator"


class SetScheduleRequest(StrictBaseModel):
    interval_seconds: int = 0
    enabled: bool = False


class ActiveUniverseMemberBody(StrictBaseModel):
    symbol: str
    tier: UniverseTier
    market: str = "TW"
    venue: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_domain(self) -> ActiveUniverseMember:
        return ActiveUniverseMember(
            symbol=self.symbol,
            tier=self.tier.value,
            market=self.market,
            venue=self.venue,
            reason=self.reason,
            metadata=self.metadata,
        )


class SourceUpdateRuleBody(StrictBaseModel):
    connector_id: str
    dataset: str
    eligible_tiers: list[UniverseTier]
    cadence: str
    market: str = "TW"
    priority: int = 100
    max_symbols_per_run: int | None = None
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_domain(self) -> SourceUpdateRule:
        return SourceUpdateRule(
            connector_id=self.connector_id,
            dataset=self.dataset,
            eligible_tiers=[tier.value for tier in self.eligible_tiers],
            cadence=self.cadence,
            market=self.market,
            priority=self.priority,
            max_symbols_per_run=self.max_symbols_per_run,
            reason=self.reason,
            metadata=self.metadata,
        )


class ActiveUniversePlanRequest(StrictBaseModel):
    members: list[ActiveUniverseMemberBody]
    rules: list[SourceUpdateRuleBody] = Field(default_factory=list)


class ActiveUniverseScheduleRequest(StrictBaseModel):
    members: list[ActiveUniverseMemberBody]
    rules: list[SourceUpdateRuleBody] = Field(default_factory=list)
    run_date: str
    default_max_symbols_per_job: int = 50
    enqueue: bool = True
    trace_id: str | None = None


class SetConnectorLifecycleRequest(StrictBaseModel):
    status: ConnectorStatus
    reason: str
    actor_id: str = "source-ingest-operator"
    trace_id: str | None = None


class RunScheduledRequest(StrictBaseModel):
    max_concurrency: int | None = None
    force_connector_ids: list[str] = Field(default_factory=list)
    exclusive_connector_ids: list[str] = Field(default_factory=list)


class PersonaSourceProvisioningRequest(StrictBaseModel):
    persona: dict[str, Any] | None = None
    personas: list[dict[str, Any]] | None = None
    dry_run: bool = False
    authoritative_snapshot: bool = False
    desired_state_sha256: str | None = None
    source_authority: str | None = None


class ReplayFrontierRequest(StrictBaseModel):
    trace_id: str | None = None


def _require_controller_authorization(authorization: str | None, *, operation: str) -> None:
    scheme, _, presented = str(authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not presented:
        raise HTTPException(
            status_code=401,
            detail=f"controller service authorization is required for {operation}",
        )
    if not hmac.compare_digest(presented, controller_token):
        raise HTTPException(
            status_code=403,
            detail=f"controller service authorization is invalid for {operation}",
        )


def _is_controller_owned(connector: SourceConnector | None) -> bool:
    if connector is None:
        return False
    marker = connector.metadata.get(RECONCILIATION_METADATA_KEY)
    return bool(
        isinstance(marker, Mapping)
        and marker.get("managed_by") == "persona_source_provisioning_reconciler"
    )


def _fence_managed_connector_mutation(
    connector_id: str,
    authorization: str | None,
    *,
    operation: str,
    proposed_connector: SourceConnector | None = None,
) -> None:
    existing = connector_store.get_config(connector_id)
    if _is_controller_owned(existing.connector if existing is not None else None) or _is_controller_owned(
        proposed_connector
    ):
        _require_controller_authorization(authorization, operation=operation)


def _register_or_validate_connector(connector: SourceConnector) -> SourceConnector:
    connector = validate_external_source_connector(connector)
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


def _source_record_schema() -> dict[str, Any]:
    return json.loads(SOURCE_RECORD_SCHEMA_PATH.read_text(encoding="utf-8"))


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
        "allow_empty": bool(fetch.get("allow_empty", False)),
        "empty_reason": fetch.get("empty_reason"),
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
                "respect_robots_txt": bool(fetch.get("respect_robots_txt", True)),
            }
        )
    elif mode == "static_records":
        summary.update(
            {
                "records_count": len(fetch.get("records") or []),
                "next_watermark": fetch.get("next_watermark"),
            }
        )
    elif mode == "provider_owned_adapter":
        summary.update(
            {
                "adapter": fetch.get("adapter"),
                "adapter_config_keys": sorted((fetch.get("adapter_config") or {}).keys()),
                "request_keys": sorted((fetch.get("request") or {}).keys()),
                "max_records": fetch.get("max_records"),
                "next_watermark": fetch.get("next_watermark"),
            }
        )
    return summary


def _schedule_summary(connector_id: str) -> dict[str, Any]:
    return _schedule_summary_from_config(
        schedule_config_store.get_schedule(connector_id)
    )


def _schedule_summary_from_config(schedule: Any | None) -> dict[str, Any]:
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


def _run_status_value(run: Any) -> str:
    status = getattr(run, "status", "")
    return status.value if hasattr(status, "value") else str(status)


def _run_effective_at(run: Any) -> datetime:
    return (
        _parse_utc_datetime(getattr(run, "finished_at", None))
        or _parse_utc_datetime(getattr(run, "started_at", None))
        or datetime.min.replace(tzinfo=timezone.utc)
    )


def _connector_stale_threshold_seconds(
    connector_metadata: Mapping[str, Any],
    schedule: Any | None,
) -> int:
    metadata = dict(connector_metadata)
    configured = metadata.get("stale_threshold_seconds") or metadata.get("freshness_sla_seconds")
    if configured not in (None, ""):
        try:
            return max(1, int(configured))
        except (TypeError, ValueError):
            pass
    cadence = int(schedule.interval_seconds) if schedule is not None else 0
    return max(DEFAULT_STALE_THRESHOLD_SECONDS, cadence * 2)


def _connector_freshness_summary_from_snapshot(
    connector_id: str,
    *,
    connector_metadata: Mapping[str, Any],
    schedule: Any | None,
    watermark: Any | None,
    runs: list[Any] | tuple[Any, ...],
    receipts: list[IngestReceipt] | tuple[IngestReceipt, ...],
    now: datetime,
) -> dict[str, Any]:
    latest_run = max(runs, key=_run_effective_at) if runs else None
    latest_receipt = receipts[-1] if receipts else None
    latest_success_receipt = next(
        (receipt for receipt in reversed(receipts) if receipt.status == "completed"),
        None,
    )
    last_failed_receipt = next(
        (receipt for receipt in reversed(receipts) if receipt.typed_failure is not None),
        None,
    )

    schedule_enabled = bool(schedule and schedule.enabled and schedule.interval_seconds > 0)
    last_success_at = (
        latest_success_receipt.finished_at
        if latest_success_receipt is not None
        else (watermark.updated_at if watermark is not None and not receipts else None)
    )
    last_success_dt = _parse_utc_datetime(last_success_at)
    source_timestamp = latest_success_receipt.source_timestamp if latest_success_receipt is not None else None
    source_timestamp_dt = _parse_utc_datetime(source_timestamp)
    source_timestamp_status = (
        latest_success_receipt.source_timestamp_status
        if latest_success_receipt is not None
        else "missing"
    )
    if source_timestamp:
        if source_timestamp_dt is None:
            source_timestamp_status = "invalid"
        elif source_timestamp_dt > now + timedelta(seconds=SOURCE_TIMESTAMP_FUTURE_TOLERANCE_SECONDS):
            source_timestamp_status = "future"
        elif source_timestamp_status in {"unknown", "missing", "invalid", "future"}:
            source_timestamp_status = "valid"
    latest_run_at = _run_effective_at(latest_run) if latest_run else None
    latest_run_status = _run_status_value(latest_run) if latest_run else None
    next_due_at: str | None = None
    seconds_until_due: int | None = None
    staleness_seconds: int | None = None
    age_seconds = (
        max(0, int((now - source_timestamp_dt).total_seconds()))
        if source_timestamp_dt is not None and source_timestamp_status == "valid"
        else None
    )
    stale_threshold_seconds = _connector_stale_threshold_seconds(
        connector_metadata,
        schedule,
    )
    stale = source_timestamp_status != "valid" or (
        age_seconds is not None and age_seconds > stale_threshold_seconds
    )
    is_due = False

    if schedule is None:
        status = "unscheduled"
    elif not schedule_enabled:
        status = "disabled"
    elif last_success_dt is None:
        status = "never_ingested"
        is_due = True
    else:
        due_at = last_success_dt + timedelta(seconds=schedule.interval_seconds)
        next_due_at = due_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        staleness_seconds = max(0, int((now - last_success_dt).total_seconds()))
        seconds_until_due = int((due_at - now).total_seconds())
        is_due = seconds_until_due <= 0
        status = "stale" if stale else ("due" if is_due else "fresh")
        seconds_until_due = max(0, seconds_until_due)

    if (
        latest_run is not None
        and latest_run_status in {"failed", "rejected"}
        and (last_success_dt is None or latest_run_at >= last_success_dt)
    ):
        status = "stale" if stale else "degraded"

    if (
        latest_receipt is not None
        and latest_receipt.status != "completed"
        and (
            last_success_dt is None
            or (
                _parse_utc_datetime(latest_receipt.finished_at or latest_receipt.started_at) is not None
                and _parse_utc_datetime(latest_receipt.finished_at or latest_receipt.started_at) >= last_success_dt
            )
        )
    ):
        status = "stale" if stale else "degraded"

    latest_run_payload = None
    if latest_run is not None:
        latest_run_payload = {
            "ingest_run_id": latest_run.ingest_run_id,
            "status": latest_run_status,
            "trigger_type": latest_run.trigger_type,
            "finished_at": latest_run.to_dict().get("finished_at"),
            "raw_count": latest_run.raw_count,
            "normalized_count": latest_run.normalized_count,
            "rejected_count": latest_run.rejected_count,
        }

    return {
        "schema_version": "source_connector_freshness.v2",
        "status": status,
        "stale": stale,
        "is_due": is_due,
        "schedule_enabled": schedule_enabled,
        "last_success_at": last_success_at,
        "source_timestamp": source_timestamp,
        "source_timestamp_status": source_timestamp_status,
        "age_seconds": age_seconds,
        "stale_threshold_seconds": stale_threshold_seconds,
        "last_failure_at": last_failed_receipt.finished_at if last_failed_receipt is not None else None,
        "last_typed_failure": (
            dict(last_failed_receipt.typed_failure)
            if last_failed_receipt is not None and last_failed_receipt.typed_failure is not None
            else None
        ),
        "last_watermark": watermark.value if watermark else None,
        "last_ingest_run_id": watermark.last_ingest_run_id if watermark else None,
        "latest_run": latest_run_payload,
        "latest_receipt": latest_receipt.to_dict() if latest_receipt is not None else None,
        "staleness_seconds": staleness_seconds,
        "next_due_at": next_due_at,
        "next_run_at": next_due_at,
        "seconds_until_due": seconds_until_due,
    }


def _connector_freshness_summary(connector_id: str) -> dict[str, Any]:
    config = connector_store.get_config(connector_id)
    schedule = schedule_config_store.get_schedule(connector_id)
    snapshot = store.read_freshness_snapshot()
    runs = tuple(
        run
        for run in snapshot["runs"]
        if getattr(run, "connector_id", None) == connector_id
    )
    receipts = tuple(
        receipt
        for receipt in snapshot["receipts"]
        if receipt.connector_id == connector_id
    )
    return _connector_freshness_summary_from_snapshot(
        connector_id,
        connector_metadata=(config.connector.metadata if config is not None else {}),
        schedule=schedule,
        watermark=snapshot["watermarks"].get(connector_id),
        runs=runs,
        receipts=receipts,
        now=datetime.now(timezone.utc),
    )


_SOURCE_READINESS_MAX_STATE_BYTES = max(
    1,
    int(os.getenv("SOURCE_INGEST_READINESS_MAX_STATE_BYTES", str(1024 * 1024))),
)
_SOURCE_READINESS_MAX_CONTROLLER_AGE_SECONDS = max(
    1,
    int(os.getenv("SOURCE_INGEST_READINESS_MAX_CONTROLLER_AGE_SECONDS", "300")),
)


def _source_freshness_readiness() -> dict[str, Any]:
    """Return a fixed-cost readiness projection from the controller snapshot.

    Health probes must not replay source, schedule, connector, or evidence
    journals.  The controller is the sole writer of this bounded local restart
    snapshot; it is also the only component allowed to reconcile connectors.
    """

    try:
        state_size_bytes = CONTROLLER_STATE_PATH.stat().st_size
    except FileNotFoundError:
        return {
            "status": "not_observed",
            "data_ready": False,
            "scheduled_connector_count": 0,
            "stale_connector_count": 0,
            "degraded_connector_count": 0,
            "reason": "controller_state_missing",
        }
    except OSError as exc:
        return {
            "status": "degraded_data",
            "data_ready": False,
            "scheduled_connector_count": 0,
            "stale_connector_count": 0,
            "degraded_connector_count": 0,
            "reason": f"controller_state_stat_failed:{type(exc).__name__}",
        }
    if state_size_bytes > _SOURCE_READINESS_MAX_STATE_BYTES:
        return {
            "status": "degraded_data",
            "data_ready": False,
            "scheduled_connector_count": 0,
            "stale_connector_count": 0,
            "degraded_connector_count": 0,
            "state_size_bytes": state_size_bytes,
            "reason": "controller_state_exceeds_readiness_budget",
        }
    try:
        state = ControllerStateStore(CONTROLLER_STATE_PATH).load()
    except ControllerStateError as exc:
        return {
            "status": "degraded_data",
            "data_ready": False,
            "scheduled_connector_count": 0,
            "stale_connector_count": 0,
            "degraded_connector_count": 0,
            "state_size_bytes": state_size_bytes,
            "reason": f"controller_state_invalid:{exc}",
        }
    if state is None:
        return {
            "status": "not_observed",
            "data_ready": False,
            "scheduled_connector_count": 0,
            "stale_connector_count": 0,
            "degraded_connector_count": 0,
            "reason": "controller_state_missing",
        }

    actual = dict(state.actual_readback)
    terminal_inventory = actual.get("terminal_connectors")
    terminal_connectors = (
        terminal_inventory.get("items")
        if isinstance(terminal_inventory, Mapping) and isinstance(terminal_inventory.get("items"), list)
        else []
    )
    scheduled = [
        item
        for item in terminal_connectors
        if isinstance(item, Mapping) and bool(dict(item.get("schedule") or {}).get("enabled"))
    ]
    stale = [
        item
        for item in scheduled
        if str(dict(item.get("freshness") or {}).get("status") or "") == "stale"
    ]
    degraded = [
        item
        for item in scheduled
        if str(dict(item.get("freshness") or {}).get("status") or "") in {"degraded", "never_ingested"}
    ]
    heartbeat = _parse_utc_datetime(state.heartbeat_at)
    heartbeat_age_seconds = (
        max(0, int((datetime.now(timezone.utc) - heartbeat).total_seconds()))
        if heartbeat is not None
        else None
    )
    controller_stale = (
        heartbeat_age_seconds is None
        or heartbeat_age_seconds > _SOURCE_READINESS_MAX_CONTROLLER_AGE_SECONDS
    )
    return {
        # Data staleness is visible but does not make the API process unready;
        # the reconcile-only controller must be able to repair it.
        "status": "stale" if stale or controller_stale else ("degraded_data" if degraded else "ok"),
        "data_ready": not stale and not degraded and not controller_stale,
        "scheduled_connector_count": len(scheduled),
        "stale_connector_count": len(stale),
        "degraded_connector_count": len(degraded),
        "controller_state_size_bytes": state_size_bytes,
        "controller_heartbeat_age_seconds": heartbeat_age_seconds,
        "connector_inventory_count": (
            terminal_inventory.get("count") if isinstance(terminal_inventory, Mapping) else 0
        ),
        "connector_inventory_truncated": bool(
            terminal_inventory.get("truncated") if isinstance(terminal_inventory, Mapping) else False
        ),
        "provider_egress_attempted": bool(state.schedule.get("provider_egress_attempted")),
    }


def _source_runtime_metrics() -> dict[str, Any]:
    readiness = _source_freshness_readiness()
    return {
        "controller_state_size_bytes": readiness.get("controller_state_size_bytes", 0),
        "connector_count": readiness.get("connector_inventory_count", 0),
        "scheduled_connector_count": readiness.get("scheduled_connector_count", 0),
        "stale_connector_count": readiness.get("stale_connector_count", 0),
        "degraded_connector_count": readiness.get("degraded_connector_count", 0),
        "posture_alert_count": PRODUCTION_POSTURE.alert_count(),
    }


def _connector_schema_hash(connector: SourceConnector, fetch: dict[str, Any] | None) -> str:
    metadata_hash = connector.metadata.get("schema_hash")
    if metadata_hash not in (None, "", [], {}):
        return str(metadata_hash)
    payload = {
        "connector_id": connector.connector_id,
        "source_type": connector.source_type.value,
        "provider": connector.provider,
        "license_scope": connector.license_scope,
        "metadata": dict(connector.metadata),
        "fetch_policy": _fetch_policy_summary(fetch),
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return sha256(body).hexdigest()[:16]


def _expected_rows(connector: SourceConnector, fetch: dict[str, Any] | None) -> int | None:
    metadata = dict(connector.metadata)
    for key in ("expected_rows_per_run", "expected_rows"):
        value = metadata.get(key)
        if value not in (None, "", [], {}):
            return int(value)
    if fetch:
        if fetch.get("mode") == "static_records":
            return len(fetch.get("records") or [])
        if fetch.get("mode") == "external_feed" and fetch.get("max_records") not in (None, ""):
            return int(fetch["max_records"])
    return None


def _connector_health_metrics(
    connector: SourceConnector,
    *,
    fetch: dict[str, Any] | None,
    state: dict[str, Any],
    freshness: dict[str, Any],
) -> dict[str, Any]:
    latest_run = freshness.get("latest_run") if isinstance(freshness.get("latest_run"), dict) else None
    row_count = latest_run.get("normalized_count") if latest_run else None
    source_error = state.get("last_error")
    if not source_error and latest_run and latest_run.get("status") in {"failed", "rejected"}:
        source_error = f"latest ingest run {latest_run['status']}"
    return {
        "schema_version": "source_connector_health_metrics.v1",
        "last_success_at": freshness.get("last_success_at"),
        "row_count": row_count,
        "expected_rows": _expected_rows(connector, fetch),
        "watermark": freshness.get("last_watermark"),
        "schema_hash": _connector_schema_hash(connector, fetch),
        "staleness_seconds": freshness.get("staleness_seconds"),
        "source_error": source_error,
    }


def _connector_registry_entry(
    connector: SourceConnector,
    *,
    fetch: dict[str, Any] | None,
    state: dict[str, Any] | None,
    schedule: dict[str, Any],
    freshness: dict[str, Any],
) -> dict[str, Any]:
    state_payload = state or {
        "connector_id": connector.connector_id,
        "attempts": 0,
        "successful_attempts": 0,
        "failed_attempts": 0,
        "last_error": None,
        "updated_at": None,
    }
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
        "schedule": schedule,
        "freshness": freshness,
        "health_metrics": _connector_health_metrics(
            connector,
            fetch=fetch,
            state=state_payload,
            freshness=freshness,
        ),
        "state": state_payload,
        "crawler_policy": crawler_policy_for_connector(
            connector,
            fetch=fetch,
            schedule=schedule,
            freshness=freshness,
            state=state_payload,
        ),
    }


def _source_connector_entries() -> list[dict[str, Any]]:
    # This is a fleet-wide projection. Point lookups replay the JSONL stores
    # for cross-process consistency, so performing them once per connector
    # makes persistent deployments connector-count multiplied by journal-size
    # work. Read each durable store once and build every registry row from the
    # same immutable-by-convention snapshots.
    configs, fetch_states = connector_store.read_snapshot()
    configured_by_id = {config.connector.connector_id: config for config in configs}
    schedules = {
        schedule.connector_id: schedule
        for schedule in schedule_config_store.list_schedules()
    }
    freshness_snapshot = store.read_freshness_snapshot()
    runs_by_connector: dict[str, list[Any]] = {}
    for run in freshness_snapshot["runs"]:
        connector_id = str(getattr(run, "connector_id", ""))
        runs_by_connector.setdefault(connector_id, []).append(run)
    receipts_by_connector: dict[str, list[IngestReceipt]] = {}
    for receipt in freshness_snapshot["receipts"]:
        receipts_by_connector.setdefault(receipt.connector_id, []).append(receipt)
    observed_at = datetime.now(timezone.utc)

    connector_ids = set(configured_by_id)
    connectors = list(manager.list_connectors())
    connector_ids.update(connector.connector_id for connector in connectors)

    entries: list[dict[str, Any]] = []
    for connector_id in sorted(connector_ids):
        config = configured_by_id.get(connector_id)
        connector = config.connector if config else manager.get_connector(connector_id)
        if connector is None:
            continue
        schedule_config = schedules.get(connector_id)
        entries.append(
            _connector_registry_entry(
                connector,
                fetch=dict(config.fetch) if config else None,
                state=fetch_states.get(connector_id) if config else None,
                schedule=_schedule_summary_from_config(schedule_config),
                freshness=_connector_freshness_summary_from_snapshot(
                    connector_id,
                    connector_metadata=connector.metadata,
                    schedule=schedule_config,
                    watermark=freshness_snapshot["watermarks"].get(connector_id),
                    runs=runs_by_connector.get(connector_id, ()),
                    receipts=receipts_by_connector.get(connector_id, ()),
                    now=observed_at,
                ),
            )
        )
    return entries


def _source_policy_registry_payload(
    entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if entries is None:
        entries = _source_connector_entries()
    connector_policies = [dict(entry["crawler_policy"]) for entry in entries]
    return policy_registry_payload(
        connector_policies,
        max_records_per_job=MAX_RECORDS_PER_JOB,
        scheduler_max_concurrency=SCHEDULER_MAX_CONCURRENCY,
        frontier_max_attempts=FRONTIER_MAX_ATTEMPTS,
        search_ingest_notify_url=SEARCH_INGEST_NOTIFY_URL,
        posture=PRODUCTION_POSTURE.to_dict(),
    )


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


def _source_provisioning_reconciler() -> SourceProvisioningReconciler:
    return SourceProvisioningReconciler(
        manager=manager,
        connector_store=connector_store,
        schedule_store=schedule_config_store,
    )


def _desired_state_digest(personas: list[dict[str, Any]]) -> str:
    body = json.dumps(
        personas,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(body).hexdigest()


def _requirement_bindings(results: list[Any]) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for result in results:
        for action in result.actions:
            if action.connector_id and action.status in {"satisfied", "mutated"}:
                existing = bindings.get(action.idempotency_key)
                if existing is not None and existing != action.connector_id:
                    raise SourceEvidenceError(
                        f"requirement binding conflict for {action.idempotency_key}: {existing} != {action.connector_id}"
                    )
                bindings[action.idempotency_key] = action.connector_id
    return bindings


def _provisioning_summary(results: list[Any]) -> dict[str, int]:
    summary = {
        "persona_count": len(results),
        "total": 0,
        "satisfied": 0,
        "mutated": 0,
        "skipped": 0,
        "conflicts": 0,
        "unsupported": 0,
    }
    for result in results:
        for key, value in result.summary.items():
            summary[key] = int(summary.get(key, 0)) + int(value)
    return summary


def _retire_removed_requirement_bindings(
    *,
    current_bindings: Mapping[str, str],
) -> list[dict[str, Any]]:
    retained_connector_ids = set(current_bindings.values())
    removed_connector_ids = sorted(
        config.connector.connector_id
        for config in connector_store.list_configs()
        if (
            config.connector.connector_id not in retained_connector_ids
            and isinstance(config.connector.metadata.get(RECONCILIATION_METADATA_KEY), Mapping)
            and config.connector.metadata[RECONCILIATION_METADATA_KEY].get("managed_by")
            == "persona_source_provisioning_reconciler"
        )
    )
    actions: list[dict[str, Any]] = []
    for connector_id in removed_connector_ids:
        config = connector_store.get_config(connector_id)
        if config is None:
            actions.append({"connector_id": connector_id, "action": "already_absent"})
            continue
        marker = config.connector.metadata.get(RECONCILIATION_METADATA_KEY)
        owner = str(marker.get("managed_by") or "") if isinstance(marker, Mapping) else ""
        if owner != "persona_source_provisioning_reconciler":
            actions.append({"connector_id": connector_id, "action": "retained_operator_owned"})
            continue
        schedule = schedule_config_store.get_schedule(connector_id)
        already_retired = (
            config.connector.status == ConnectorStatus.DISABLED
            and isinstance(marker, Mapping)
            and marker.get("retired_by_authoritative_snapshot") is True
            and (schedule is None or not schedule.enabled)
        )
        if already_retired:
            actions.append({"connector_id": connector_id, "action": "already_disabled_controller_owned"})
            continue
        connector_payload = config.connector.to_dict()
        connector_payload["status"] = ConnectorStatus.DISABLED.value
        metadata = dict(connector_payload.get("metadata") or {})
        reconciliation = dict(metadata.get(RECONCILIATION_METADATA_KEY) or {})
        reconciliation["retired_by_authoritative_snapshot"] = True
        metadata[RECONCILIATION_METADATA_KEY] = reconciliation
        connector_payload["metadata"] = metadata
        retired_connector = SourceConnector.from_dict(connector_payload)
        connector_store.upsert_config(retired_connector, config.fetch)
        manager.upsert_connector(retired_connector)
        if schedule is not None and schedule.enabled:
            schedule_config_store.upsert_schedule(
                connector_id,
                interval_seconds=schedule.interval_seconds,
                enabled=False,
            )
        actions.append({"connector_id": connector_id, "action": "disabled_controller_owned"})
    return actions


def _source_record_readback(record: SourceRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    payload = record.to_dict()
    metadata = dict(record.metadata)
    provenance_keys = (
        "provider",
        "dataset",
        "source_dataset",
        "venue",
        "market",
        "event_time",
        "available_time",
        "api_endpoint",
        "access_scope",
        "license_scope",
        "schema_hash",
        "source_ingest_run_id",
    )
    return {
        "source_id": payload["source_id"],
        "connector_id": payload["connector_id"],
        "source_type": payload["source_type"],
        "title": payload["title"],
        "content_ref": payload["content_ref"],
        "status": payload["status"],
        "trace_id": payload["trace_id"],
        "created_at": payload["created_at"],
        "provenance": {key: metadata[key] for key in provenance_keys if key in metadata},
    }


def _latest_source_record_by_connector() -> dict[str, SourceRecord]:
    latest: dict[str, SourceRecord] = {}
    for record in evidence_repository.list_source_records():
        current = latest.get(record.connector_id)
        if current is None or str(record.to_dict()["created_at"]) > str(current.to_dict()["created_at"]):
            latest[record.connector_id] = record
    return latest


def _controller_connector_readbacks() -> list[dict[str, Any]]:
    # Controller readback is fleet-wide. Point reads replay their JSONL stores
    # for cross-process consistency, so doing them once per connector turns the
    # endpoint into connector-count multiplied by journal-size work.
    configs, fetch_states = connector_store.read_snapshot()
    schedules = {
        schedule.connector_id: schedule
        for schedule in schedule_config_store.list_schedules()
    }
    snapshot = store.read_freshness_snapshot()
    runs_by_connector: dict[str, list[Any]] = {}
    for run in snapshot["runs"]:
        connector_id = str(getattr(run, "connector_id", ""))
        runs_by_connector.setdefault(connector_id, []).append(run)
    receipts_by_connector: dict[str, list[IngestReceipt]] = {}
    for receipt in snapshot["receipts"]:
        receipts_by_connector.setdefault(receipt.connector_id, []).append(receipt)
    observed_at = datetime.now(timezone.utc)
    latest_by_connector = _latest_source_record_by_connector()
    readbacks: list[dict[str, Any]] = []
    for config in sorted(configs, key=lambda item: item.connector.connector_id):
        connector = config.connector
        connector_id = connector.connector_id
        schedule = schedules.get(connector_id)
        freshness = _connector_freshness_summary_from_snapshot(
            connector_id,
            connector_metadata=connector.metadata,
            schedule=schedule,
            watermark=snapshot["watermarks"].get(connector_id),
            runs=runs_by_connector.get(connector_id, ()),
            receipts=receipts_by_connector.get(connector_id, ()),
            now=observed_at,
        )
        health = source_health_store.get(connector_id)
        health_payload = health.to_dict() if health is not None else None
        if health_payload is not None:
            health_payload["staleness_seconds"] = freshness.get("staleness_seconds")
        reconciliation = connector.metadata.get(RECONCILIATION_METADATA_KEY)
        if not isinstance(reconciliation, Mapping):
            reconciliation = {}
        desired_state = reconciliation.get("desired_state")
        readbacks.append(
            {
                "connector_id": connector_id,
                "configured": True,
                "connector": connector.to_dict(),
                "desired_state": dict(desired_state) if isinstance(desired_state, Mapping) else {},
                "desired_state_sha256": reconciliation.get("desired_state_sha256"),
                "schedule": schedule.to_dict() if schedule is not None else None,
                "fetch_state": fetch_states[connector_id],
                "freshness": freshness,
                "latest_source_record": _source_record_readback(latest_by_connector.get(connector_id)),
                "source_health": health_payload,
            }
        )
    return readbacks


def _controller_readback_payload() -> dict[str, Any]:
    connector_readbacks = _controller_connector_readbacks()
    frontier = store.list_frontier()
    frontier_backlog = sum(1 for item in frontier if item.status in {"queued", "retry", "running"})
    staleness_values = [
        int(item["freshness"]["staleness_seconds"])
        for item in connector_readbacks
        if item.get("freshness", {}).get("staleness_seconds") is not None
    ]
    controller_state = read_controller_state(CONTROLLER_STATE_PATH)
    requirement_snapshot = requirement_snapshot_store.latest
    dlq_entries = dead_letter_queue.entries()
    dlq_status_counts = {
        status.value: sum(1 for entry in dlq_entries if entry.status == status)
        for status in DeadLetterStatus
    }
    unresolved_dlq_count = sum(
        dlq_status_counts[status.value]
        for status in (
            DeadLetterStatus.PENDING,
            DeadLetterStatus.REPLAY_FAILED,
            DeadLetterStatus.SCHEMA_REJECTED,
        )
    )
    return {
        "schema_version": "source_ingest_controller_readback.v1",
        "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "controller_state": controller_state,
        "controller_state_path": str(CONTROLLER_STATE_PATH),
        "requirement_snapshot": requirement_snapshot.to_dict() if requirement_snapshot is not None else None,
        "connector_count": len(connector_readbacks),
        "source_record_count": len(evidence_repository.list_source_records()),
        "dlq_count": len(dlq_entries),
        "pending_dlq_count": dlq_status_counts[DeadLetterStatus.PENDING.value],
        "unresolved_dlq_count": unresolved_dlq_count,
        "dlq_status_counts": dlq_status_counts,
        "frontier_backlog": frontier_backlog,
        "max_lag_seconds": max(staleness_values, default=0),
        "connectors": connector_readbacks,
    }


def _persona_source_provisioning_payload(request: PersonaSourceProvisioningRequest) -> dict[str, Any]:
    if request.authoritative_snapshot:
        if request.personas is None or request.persona is not None:
            raise SourceEvidenceError(
                "authoritative source reconciliation requires an explicit canonical personas array"
            )
        if not str(request.desired_state_sha256 or "").strip():
            raise SourceEvidenceError(
                "authoritative source reconciliation requires desired_state_sha256 from the desired-state authority"
            )
    personas = list(request.personas or [])
    if request.persona is not None:
        personas.insert(0, request.persona)
    if not personas and not request.authoritative_snapshot:
        raise SourceEvidenceError("persona or personas is required")
    desired_state_sha256 = _desired_state_digest(personas)
    if request.desired_state_sha256 and request.desired_state_sha256 != desired_state_sha256:
        raise SourceEvidenceError("desired_state_sha256 does not match canonical persona snapshot")
    pre_readback = _controller_readback_payload()
    reconciler = _source_provisioning_reconciler()
    retirement_actions: list[dict[str, Any]] = []
    accepted_snapshot = None
    if request.authoritative_snapshot and not request.dry_run:
        # Phase 1 validates the complete desired snapshot and computes stable
        # bindings without changing actual state.  Persisting that admitted
        # intent before convergence makes a crash retryable instead of leaving
        # mutated connectors governed by an older authoritative snapshot.
        planned_results = list(reconciler.reconcile_personas(personas, dry_run=True))
        planned_summary = _provisioning_summary(planned_results)
        planned_bindings = _requirement_bindings(planned_results)
        if planned_summary["conflicts"] or planned_summary["unsupported"]:
            results = planned_results
            summary = planned_summary
            bindings = planned_bindings
        else:
            accepted_snapshot = requirement_snapshot_store.append(
                desired_state_sha256=desired_state_sha256,
                bindings=planned_bindings,
                persona_count=len(personas),
                authority=str(request.source_authority or "api://persona-source-provisioning"),
                authoritative=True,
            )
            results = list(reconciler.reconcile_personas(personas, dry_run=False))
            summary = _provisioning_summary(results)
            bindings = _requirement_bindings(results)
            if summary["conflicts"] or summary["unsupported"] or bindings != planned_bindings:
                raise SourceEvidenceError(
                    "actual source convergence contradicted the admitted authoritative requirement snapshot"
                )
            retirement_actions = _retire_removed_requirement_bindings(
                current_bindings=bindings,
            )
    else:
        results = list(reconciler.reconcile_personas(personas, dry_run=request.dry_run))
        summary = _provisioning_summary(results)
        bindings = _requirement_bindings(results)
    return {
        "schema_version": "persona_source_provisioning_response.v1",
        "controller": "persona_source_provisioning_reconciler",
        "dry_run": request.dry_run,
        "authoritative_snapshot": request.authoritative_snapshot,
        "desired_state_sha256": desired_state_sha256,
        "source_authority": request.source_authority,
        "summary": summary,
        "results": [result.to_dict() for result in results],
        "bindings": bindings,
        "retirement_actions": retirement_actions,
        "accepted_requirement_snapshot": accepted_snapshot.to_dict() if accepted_snapshot is not None else None,
        "pre_readback": pre_readback,
        "post_readback": _controller_readback_payload(),
    }


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


def _assert_connector_lifecycle_allows_run(connector: SourceConnector) -> None:
    if connector.status == ConnectorStatus.DISABLED:
        raise SourceEvidenceError(f"Connector lifecycle status disabled rejects ingest runs: {connector.connector_id}")


def _inline_fetch(records: tuple[SourceRecord, ...], next_watermark: str | None):
    return lambda _watermark: IngestBatch(records=records, next_watermark=next_watermark)


def _configured_fetch(
    connector_id: str,
    *,
    trace_id: str = "",
    job_parameters: dict[str, Any] | None = None,
):
    return lambda watermark: configured_fetcher.fetch_batch(
        connector_id,
        watermark,
        trace_id=trace_id,
        job_parameters=job_parameters,
    )


def _append_audit_actions(actions: tuple[Any, ...]) -> None:
    if not actions:
        return
    payloads = [action.to_dict() for action in actions]
    with audit_store_lock:
        AUDIT_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_preexisted = AUDIT_STORE_PATH.exists()
        wrote = False
        with AUDIT_STORE_PATH.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                existing_ids = {
                    str(payload.get("action_id"))
                    for line in handle
                    if line.strip()
                    for payload in (json.loads(line),)
                    if isinstance(payload, Mapping) and payload.get("action_id")
                }
                handle.seek(0, os.SEEK_END)
                for payload in payloads:
                    action_id = str(payload.get("action_id") or "").strip()
                    if action_id in existing_ids:
                        continue
                    handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
                    existing_ids.add(action_id)
                    wrote = True
                if wrote:
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        if wrote and not file_preexisted:
            directory_fd = os.open(AUDIT_STORE_PATH.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)


def _load_audit_actions() -> list[dict[str, Any]]:
    with audit_store_lock:
        if not AUDIT_STORE_PATH.exists():
            return []
        actions: list[dict[str, Any]] = []
        with AUDIT_STORE_PATH.open("r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                for line in handle:
                    if line.strip():
                        actions.append(json.loads(line))
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
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


def _market_raw_refs_for_record(record: SourceRecord, storage_refs: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not storage_refs:
        return []
    dataset = str(record.metadata.get("dataset") or record.metadata.get("source_dataset") or "")
    raw_refs = [dict(ref) for ref in storage_refs.get("raw_refs") or []]
    if not dataset:
        return raw_refs
    matched = [ref for ref in raw_refs if str(ref.get("dataset") or "") == dataset]
    return matched or raw_refs


def _compact_bulk_market_record(record: SourceRecord, storage_refs: dict[str, Any] | None) -> SourceRecord:
    if record.source_type.value != "market":
        return record
    metadata = dict(record.metadata)
    removed = []
    for key in ("raw_row", "raw_rows", "raw_payload", "payload", "body"):
        if key in metadata:
            metadata.pop(key, None)
            removed.append(key)
    raw_refs = _market_raw_refs_for_record(record, storage_refs)
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


def _with_source_ingest_run(record: SourceRecord, ingest_run_id: str) -> SourceRecord:
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


def _admit_normalized_records_to_distillation(
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


def _persist_source_evidence_refs(result: Any, storage_refs: dict[str, Any] | None = None) -> dict[str, Any]:
    source_records = [
        _with_source_ingest_run(
            _compact_bulk_market_record(record, storage_refs),
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
            evidence_item=_evidence_item_for_record(record, result.run),
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
        evidence_bundle_id=_stable_ref("evbundle", result.run.ingest_run_id),
        metadata=bundle_metadata,
    )
    distillation_admissions = _admit_normalized_records_to_distillation(
        normalized_source_records_by_id.values()
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


def _persist_market_data_storage_refs(result: Any) -> dict[str, Any]:
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


def _persist_latest_market_snapshots(result: Any) -> dict[str, Any]:
    """Project completed normalized SourceRecords into the read-only paper API.

    This executes only as part of the already-authoritative ingest completion
    path.  The corresponding GET endpoint only reads this stored projection;
    it cannot call a provider or start the scheduler.
    """

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
        observed_at=_run_finished_at_iso(result.run),
    )


def _run_finished_at_iso(run: Any) -> str:
    value = run.to_dict().get("finished_at") or run.to_dict().get("started_at")
    return str(value or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))


def _run_date(run: Any) -> str:
    return _run_finished_at_iso(run)[:10]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _record_ingest_usage(connector_id: str, run: Any) -> None:
    date = _run_date(run)
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


def _health_status_for_run(run: Any) -> str:
    if run.status.value == "completed":
        return SourceHealthStatus.DEGRADED.value if run.rejected_count else SourceHealthStatus.OK.value
    if run.status.value == "rejected":
        return SourceHealthStatus.DEGRADED.value
    return SourceHealthStatus.FAILED.value


def _source_error_for_result(result: Any) -> str | None:
    if result.run.status.value == "completed":
        return None
    if result.dlq_entries:
        return str(result.dlq_entries[0].reason)
    event_messages = [event.message for event in result.run.events if event.message]
    return str(event_messages[-1]) if event_messages else f"source ingest run status={result.run.status.value}"


def _provider_metadata_from_records(result: Any) -> dict[str, Any]:
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


def _source_health_outcome(result: Any) -> dict[str, Any]:
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


def _source_timestamp_state_for_result(
    result: Any,
    *,
    now: datetime | None = None,
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
        if latest > captured_at + timedelta(seconds=SOURCE_TIMESTAMP_FUTURE_TOLERANCE_SECONDS)
        else "valid"
    )
    return latest.isoformat().replace("+00:00", "Z"), status


def _source_timestamp_for_result(result: Any) -> str | None:
    return _source_timestamp_state_for_result(result)[0]


def _persist_ingest_receipt(
    result: Any,
    evidence_refs: Mapping[str, Any],
    *,
    status: str | None = None,
    typed_failure: Mapping[str, Any] | None = None,
) -> IngestReceipt:
    run = result.run.to_dict()
    storage_refs = evidence_refs.get("storage_refs")
    source_timestamp, source_timestamp_status = _source_timestamp_state_for_result(result)
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


def _post_processing_typed_failure(exc: Exception, *, stage: str) -> dict[str, Any]:
    return {
        "schema_version": "source_ingest_typed_failure.v1",
        "category": "persistence",
        "code": "post_processing_failed",
        "error_type": type(exc).__name__,
        "retryable": isinstance(exc, OSError),
        "stage": stage,
    }


def _update_source_health_and_usage(
    *,
    connector: SourceConnector,
    result: Any,
    storage_refs: dict[str, Any],
) -> None:
    finished_at = _run_finished_at_iso(result.run)
    existing = source_health_store.get(connector.connector_id)
    previous_failures = int((existing.metadata.get("failure_count") if existing else 0) or 0)
    status = _health_status_for_run(result.run)
    source_error = _source_error_for_result(result)
    failure_count = previous_failures + (0 if result.run.status.value == "completed" else 1)
    fetch_state = connector_store.get_fetch_state(connector.connector_id)
    config = connector_store.get_config(connector.connector_id)
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
        schema_hash=_connector_schema_hash(connector, config.fetch if config else None),
        staleness_seconds=_connector_freshness_summary(connector.connector_id).get("staleness_seconds"),
        error_rate_7d=min(1.0, failed_attempts / total_attempts),
        cost_estimate_30d=existing.cost_estimate_30d if existing else None,
        metadata={
            **(dict(existing.metadata) if existing else {}),
            "last_ingest_run_id": result.run.ingest_run_id,
            "last_run_status": result.run.status.value,
            "last_outcome": _source_health_outcome(result),
            "source_error": source_error,
            "failure_count": failure_count,
            "storage_refs": storage_refs,
            **_provider_metadata_from_records(result),
        },
    )
    source_health_store.upsert(health)
    _record_ingest_usage(connector.connector_id, result.run)


def _result_payload(
    result: Any,
    evidence_refs: dict[str, Any] | None = None,
    source_search_refresh: dict[str, Any] | None = None,
) -> dict[str, Any]:
    receipt = store.get_receipt(result.run.ingest_run_id)
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


def _run_job(
    *,
    connector: SourceConnector,
    trace_id: str,
    trigger_type: str,
    fetch_batch: Any,
    frontier_id: str | None = None,
) -> tuple[Any, dict[str, Any], dict[str, Any] | None]:
    _assert_connector_lifecycle_allows_run(connector)
    result = scheduler.run_once(
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
    _persist_ingest_receipt(result, evidence_refs, status=initial_receipt_status)
    post_processing_stage = "audit"
    try:
        _append_audit_actions(result.audit_actions)
        post_processing_stage = "market_storage"
        storage_refs = _persist_market_data_storage_refs(result)
        evidence_refs["storage_refs"] = storage_refs
        post_processing_stage = "latest_market_snapshot"
        market_snapshots = _persist_latest_market_snapshots(result)
        evidence_refs["market_snapshots"] = market_snapshots
        post_processing_stage = "source_evidence"
        evidence_refs = _persist_source_evidence_refs(result, storage_refs=storage_refs)
        evidence_refs["storage_refs"] = storage_refs
        evidence_refs["market_snapshots"] = market_snapshots
        post_processing_stage = "source_health_usage"
        _update_source_health_and_usage(connector=connector, result=result, storage_refs=storage_refs)
        post_processing_stage = "receipt_finalize"
        _persist_ingest_receipt(result, evidence_refs)
    except Exception as exc:
        _persist_ingest_receipt(
            result,
            evidence_refs,
            status="failed",
            typed_failure=_post_processing_typed_failure(exc, stage=post_processing_stage),
        )
        raise
    source_search_refresh: dict[str, Any] | None = None
    if result.run.status.value == "completed":
        source_search_refresh = _notify_search_index_refresh(
            result.run.ingest_run_id,
            connector_id=connector.connector_id,
            source_type=connector.source_type.value,
            trace_id=result.run.trace_id,
            normalized_count=result.run.normalized_count,
            evidence_refs=evidence_refs,
        )
        _record_search_refresh_event(result.run, source_search_refresh)
    return result, evidence_refs, source_search_refresh


def _run_ingest_request(request: TriggerIngestJobRequest) -> dict[str, Any]:
    if len(request.records) > MAX_RECORDS_PER_JOB:
        raise HTTPException(status_code=413, detail=f"records exceeds SOURCE_INGEST_MAX_RECORDS={MAX_RECORDS_PER_JOB}")

    try:
        connector = _connector_for_job(request)
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
            fetch_batch = _inline_fetch(records, request.next_watermark)
        else:
            fetch_batch = _configured_fetch(
                connector.connector_id,
                trace_id=request.trace_id,
                job_parameters=request.job_parameters,
            )
        result, evidence_refs, source_search_refresh = _run_job(
            connector=connector,
            trace_id=request.trace_id,
            trigger_type=request.trigger_type,
            fetch_batch=fetch_batch,
        )
        return _result_payload(result, evidence_refs, source_search_refresh)
    except (EvidenceValidationError, SourceEvidenceError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _search_refresh_event_summary(refresh: dict[str, Any]) -> dict[str, Any]:
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


def _record_search_refresh_event(run: Any, refresh: dict[str, Any]) -> None:
    summary = _search_refresh_event_summary(refresh)
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


def _notify_search_index_refresh(
    ingest_run_id: str,
    *,
    connector_id: str | None = None,
    source_type: str | None = None,
    trace_id: str | None = None,
    normalized_count: int = 0,
    evidence_refs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST source completion to search service and return a compact observable summary."""
    attempted_at = _utc_now_iso()
    summary: dict[str, Any] = {
        "schema_version": "source_search_refresh_notification.v1",
        "ingest_run_id": ingest_run_id,
        "configured": bool(SEARCH_INGEST_NOTIFY_URL),
        "status": "not_configured",
        "attempted_at": attempted_at,
        "search_url": SEARCH_INGEST_NOTIFY_URL or None,
        "search_service": None,
    }
    if not SEARCH_INGEST_NOTIFY_URL:
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
            f"{SEARCH_INGEST_NOTIFY_URL}/api/search/index/source-completions",
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


def _result_error(result: Any) -> str:
    if result.dlq_entries:
        return str(result.dlq_entries[0].reason)
    return f"source ingest run ended with status={result.run.status.value}"


def _run_frontier_item(
    item: Any,
    *,
    resolve_correlated_dlq: bool = True,
) -> tuple[Any, dict[str, Any], Any, dict[str, Any] | None]:
    config = connector_store.get_config(item.connector_id)
    if config is None:
        updated = store.fail_frontier(
            item.frontier_id,
            error="connector config not found",
            backoff_seconds=FRONTIER_BACKOFF_SECONDS,
        )
        raise SourceEvidenceError(f"Connector fetch is not configured: {item.connector_id}; frontier={updated.status}")
    connector = _register_or_validate_connector(config.connector)
    try:
        result, evidence_refs, source_search_refresh = _run_job(
            connector=connector,
            trace_id=item.trace_id or f"frontier-{item.frontier_id}",
            trigger_type=item.trigger_type,
            fetch_batch=_configured_fetch(
                item.connector_id,
                trace_id=item.trace_id or f"frontier-{item.frontier_id}",
                job_parameters=dict(item.job_parameters),
            ),
            frontier_id=item.frontier_id,
        )
    except Exception as exc:
        updated = store.fail_frontier(
            item.frontier_id,
            error=str(exc),
            backoff_seconds=FRONTIER_BACKOFF_SECONDS,
        )
        raise SourceEvidenceError(f"frontier run failed before ingest result persisted: {updated.last_error}") from exc

    if result.run.status.value == "completed":
        # Publish the durable completed frontier, then resolve correlated DLQ
        # entries.  A crash between the two writes leaves pending_dlq_count
        # non-zero, so terminal admission fails closed and the next scheduled
        # tick's repair sweep completes the saga.
        updated = store.complete_frontier(item.frontier_id, ingest_run_id=result.run.ingest_run_id)
        if resolve_correlated_dlq:
            _resolve_pending_dlq_for_recovery(
                frontier_id=item.frontier_id,
                connector_id=item.connector_id,
                recovery_ingest_run_id=result.run.ingest_run_id,
            )
    else:
        updated = store.fail_frontier(
            item.frontier_id,
            error=_result_error(result),
            backoff_seconds=FRONTIER_BACKOFF_SECONDS,
            ingest_run_id=result.run.ingest_run_id,
        )
    return result, evidence_refs, updated, source_search_refresh


def _unresolved_dead_letter_entries(*, tag_filter: str | None = None) -> list[Any]:
    entries: list[Any] = []
    for status in (
        DeadLetterStatus.PENDING,
        DeadLetterStatus.REPLAY_FAILED,
        DeadLetterStatus.SCHEMA_REJECTED,
    ):
        entries.extend(dead_letter_queue.entries(status=status, tag_filter=tag_filter))
    return entries


def _resolve_pending_dlq_for_recovery(
    *,
    frontier_id: str,
    connector_id: str,
    recovery_ingest_run_id: str,
) -> list[dict[str, Any]]:
    """Durably close correlated pending DLQ entries after a recovered run."""

    recovery_run = store.get_run(recovery_ingest_run_id)
    frontier = store.get_frontier(frontier_id)
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
    for entry in _unresolved_dead_letter_entries(tag_filter="retry_exhausted"):
        if entry.event.event_type != "source_ingestion.scheduled_run_failed":
            continue
        event_frontier_id = str(entry.event.payload.get("frontier_id") or "").strip()
        event_connector_id = str(entry.event.payload.get("connector_id") or "").strip()
        if event_frontier_id != frontier_id or event_connector_id != connector_id:
            continue
        failed_ingest_run_id = str(entry.event.payload.get("ingest_run_id") or "").strip()
        failed_run = store.get_run(failed_ingest_run_id)
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
            actor_ref=scheduler.actor_ref,
            action_type="source_ingestion.scheduled_run.recovered",
            target_ref=f"dead_letter:{entry.entry_id}",
            environment=scheduler.environment,
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
        # Audit is part of the terminal recovery fact.  Make it durable first;
        # a crash then leaves the entry pending (fail closed), while the stable
        # action id makes the retry idempotent.
        _append_audit_actions((audit,))
        dead_letter_queue.replace_entry(updated)
        resolved.append(
            {
                "entry_id": entry.entry_id,
                "frontier_id": frontier_id,
                "recovery_ingest_run_id": recovery_ingest_run_id,
                "status": updated.status.value,
            }
        )
    return resolved


def _resolve_pending_dlq_for_completed_frontiers() -> list[dict[str, Any]]:
    """Repair a crash after frontier completion but before DLQ resolution.

    The scheduler performs bounded attempts inside each frontier attempt.  An
    inner exhaustion creates a DLQ entry while the durable frontier may still
    be retryable.  Once a later frontier attempt completes, retaining that
    entry as pending would contradict terminal controller truth.  This repair
    is idempotent and is also run at the start of every scheduled tick so a
    crash after frontier completion but before DLQ replacement converges on
    restart.
    """

    resolved: list[dict[str, Any]] = []
    pending_frontier_ids = {
        str(entry.event.payload.get("frontier_id") or "").strip()
        for entry in _unresolved_dead_letter_entries(tag_filter="retry_exhausted")
        if entry.event.event_type == "source_ingestion.scheduled_run_failed"
    }
    for event_frontier_id in sorted(pending_frontier_ids):
        if not event_frontier_id:
            continue
        frontier = store.get_frontier(event_frontier_id)
        if frontier is None or frontier.status != "done" or not frontier.ingest_run_id:
            continue
        resolved.extend(
            _resolve_pending_dlq_for_recovery(
                frontier_id=event_frontier_id,
                connector_id=frontier.connector_id,
                recovery_ingest_run_id=frontier.ingest_run_id,
            )
        )
    return resolved


def _replay_source_event(event: Any) -> str:
    if event.event_type != "source_ingestion.scheduled_run_failed":
        raise SourceEvidenceError(f"Unsupported source-ingest DLQ replay event: {event.event_type}")
    connector_id = str(event.payload.get("connector_id") or "").strip()
    if not connector_id:
        raise SourceEvidenceError("DLQ replay event is missing connector_id")
    frontier_id = str(event.payload.get("frontier_id") or "").strip()
    if frontier_id:
        store.replay_frontier(frontier_id, trace_id=event.trace_id)
        item = store.claim_frontier(frontier_id)
        result, _evidence_refs, updated, _source_search_refresh = _run_frontier_item(item)
        if result.run.status.value != "completed":
            raise SourceEvidenceError(
                f"DLQ replay did not complete frontier {frontier_id}: run={result.run.status.value} frontier={updated.status}"
            )
        return f"crawl_frontier:{frontier_id}:source_ingest_run:{result.run.ingest_run_id}"
    config = connector_store.get_config(connector_id)
    if config is None:
        raise SourceEvidenceError(f"Connector fetch is not configured: {connector_id}")
    connector = _register_or_validate_connector(config.connector)
    result, _evidence_refs, _source_search_refresh = _run_job(
        connector=connector,
        trace_id=event.trace_id,
        trigger_type="dlq_replay",
        fetch_batch=_configured_fetch(connector.connector_id, trace_id=event.trace_id),
    )
    if result.run.status.value != "completed":
        raise SourceEvidenceError(f"DLQ replay did not complete ingest run: {result.run.status.value}")
    return f"source_ingest_run:{result.run.ingest_run_id}"


def _record_connector_lifecycle_audit(
    *,
    connector_id: str,
    previous_status: str,
    next_status: str,
    reason: str,
    actor_id: str,
    trace_id: str | None,
) -> dict[str, Any]:
    actor = ActorRef(ActorType.SERVICE, actor_id or "source-ingest-operator", roles=("source_ingest_operator",))
    trace = TraceContext(
        trace_id=trace_id or f"trace-source-ingest-lifecycle-{connector_id}",
        correlation_id=trace_id or f"trace-source-ingest-lifecycle-{connector_id}",
        environment=scheduler.environment,
        actor_ref=actor,
    )
    payload = {
        "connector_id": connector_id,
        "previous_status": previous_status,
        "next_status": next_status,
        "reason": reason,
    }
    action = AuditAction.record(
        actor_ref=actor,
        action_type="source_ingestion.connector_lifecycle.updated",
        target_ref=f"source_connector:{connector_id}",
        environment=scheduler.environment,
        reason=reason,
        trace=trace,
        payload=payload,
        before_state_ref=f"source_connector:{connector_id}:status:{previous_status}",
        after_state_ref=f"source_connector:{connector_id}:status:{next_status}",
        metadata={"connector_id": connector_id, "previous_status": previous_status, "next_status": next_status},
    )
    _append_audit_actions((action,))
    return action.to_dict()


def _set_connector_lifecycle(connector_id: str, request: SetConnectorLifecycleRequest) -> dict[str, Any]:
    config = connector_store.get_config(connector_id)
    if config is None:
        raise HTTPException(status_code=404, detail="connector config not found")
    reason = str(request.reason or "").strip()
    if not reason:
        raise SourceEvidenceError("lifecycle reason is required")
    previous = config.connector
    previous_status = previous.status.value
    next_status = request.status.value
    payload = previous.to_dict()
    payload["status"] = next_status
    metadata = dict(payload.get("metadata") or {})
    metadata["lifecycle"] = {
        "status": next_status,
        "previous_status": previous_status,
        "reason": reason,
        "actor_id": request.actor_id,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    payload["metadata"] = metadata
    updated_connector = validate_external_source_connector(SourceConnector.from_dict(payload))
    manager.upsert_connector(updated_connector)
    stored = connector_store.upsert_config(updated_connector, config.fetch)
    audit = _record_connector_lifecycle_audit(
        connector_id=connector_id,
        previous_status=previous_status,
        next_status=next_status,
        reason=reason,
        actor_id=request.actor_id,
        trace_id=request.trace_id,
    )
    return {
        "connector": stored.connector.to_dict(),
        "fetch": dict(stored.fetch),
        "state": connector_store.get_fetch_state(connector_id),
        "lifecycle": metadata["lifecycle"],
        "audit_action": audit,
    }


@app.get("/health")
def health() -> dict[str, Any]:
    runtime_metrics = _source_runtime_metrics()
    return {
        "status": "ok",
        "service": "pantheon-source-ingest",
        "store_path": str(SCHEDULE_STORE_PATH),
        "connector_store_path": str(CONNECTOR_STORE_PATH),
        "source_evidence_path": str(SOURCE_EVIDENCE_STORE_PATH),
        "market_data_storage_root": str(MARKET_DATA_STORAGE_ROOT),
        "latest_market_snapshot_path": str(LATEST_MARKET_SNAPSHOT_PATH),
        "dlq_path": str(DLQ_STORE_PATH),
        "audit_path": str(AUDIT_STORE_PATH),
        **runtime_metrics,
        "scheduler_max_concurrency": SCHEDULER_MAX_CONCURRENCY,
        "frontier_max_attempts": FRONTIER_MAX_ATTEMPTS,
        "frontier_backoff_seconds": FRONTIER_BACKOFF_SECONDS,
        "source_search_posture": PRODUCTION_POSTURE.to_dict(),
        "posture_alert_count": PRODUCTION_POSTURE.alert_count(),
    }


@app.get("/api/source-ingest/snapshots/latest")
def get_latest_market_snapshot(symbol: str) -> dict[str, Any]:
    """Return the one Source-owned, already-stored normalized snapshot.

    This is intentionally a read-only projection lookup.  It does not invoke
    a connector, call any provider, or schedule ingestion work.
    """

    try:
        snapshot = latest_market_snapshot_store.get(symbol)
    except (MarketSnapshotStateError, RequirementStateError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "market_snapshot_store_unavailable",
                "symbol": str(symbol or "").strip(),
                "detail": str(exc),
            },
        ) from exc
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "market_snapshot_not_found",
                "symbol": str(symbol or "").strip().upper(),
            },
        )
    return snapshot.to_public_dict()


@app.post("/api/source-ingest/connectors", status_code=201)
def configure_connector(
    request: ConfigureConnectorRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        with authoritative_reconcile_lock:
            proposed = request.connector.to_domain()
            _fence_managed_connector_mutation(
                proposed.connector_id,
                authorization,
                operation="controller-owned connector configuration",
                proposed_connector=proposed,
            )
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
    entries = _source_connector_entries()
    financial_catalog = financial_data_source_catalog_payload()
    return {
        "schema_version": "source_connector_registry.v1",
        "connectors": entries,
        "provider_examples": _provider_example_payloads(),
        "policy_registry": _source_policy_registry_payload(entries),
        "financial_data_source_catalog": financial_catalog,
        "active_universe_policy": financial_catalog["active_universe_policy"],
    }


@app.post("/api/source-ingest/persona-source-provisioning/reconcile")
def reconcile_persona_source_provisioning(
    request: PersonaSourceProvisioningRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        if not request.dry_run:
            _require_controller_authorization(
                authorization,
                operation="source reconciliation mutation",
            )
            with exclusive_file_lock(
                RECONCILE_TRANSACTION_LOCK_PATH,
                authoritative_reconcile_lock,
            ):
                requirement_snapshot_store.reload()
                connector_store.reload()
                schedule_config_store.reload()
                return _persona_source_provisioning_payload(request)
        return _persona_source_provisioning_payload(request)
    except (SourceEvidenceError, RequirementStateError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/source-ingest/controller/readback")
def source_ingest_controller_readback() -> dict[str, Any]:
    """Return authoritative connector/schedule/record/health actual state."""

    try:
        with authoritative_reconcile_lock:
            return _controller_readback_payload()
    except ControllerStateError as exc:
        raise HTTPException(status_code=503, detail=f"controller state is invalid: {exc}") from exc


@app.get("/api/source-ingest/policy-registry")
def source_policy_registry() -> dict[str, Any]:
    return _source_policy_registry_payload()


@app.get("/api/source-ingest/data-sources/financial-catalog")
def financial_data_source_catalog() -> dict[str, Any]:
    return financial_data_source_catalog_payload()


@app.get("/api/source-ingest/active-universe/policy")
def active_universe_policy() -> dict[str, Any]:
    return financial_data_source_catalog_payload()["active_universe_policy"]


@app.post("/api/source-ingest/active-universe/plan")
def active_universe_plan(request: ActiveUniversePlanRequest) -> dict[str, Any]:
    try:
        rules = [rule.to_domain() for rule in request.rules] if request.rules else DEFAULT_SOURCE_UPDATE_RULES
        return build_active_universe_update_plan(
            [member.to_domain() for member in request.members],
            rules=rules,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/source-ingest/active-universe/schedule")
def active_universe_schedule(
    request: ActiveUniverseScheduleRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        rules = [rule.to_domain() for rule in request.rules] if request.rules else DEFAULT_SOURCE_UPDATE_RULES
        fanout = build_active_universe_job_fanout(
            [member.to_domain() for member in request.members],
            rules=rules,
            run_date=request.run_date,
            default_max_symbols_per_job=request.default_max_symbols_per_job,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    enqueued: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = list(fanout["skipped"])
    if request.enqueue:
        with authoritative_reconcile_lock, source_execution_lock:
            managed_job_ids = {
                str(job["connector_id"])
                for job in fanout["jobs"]
                if (
                    (config := connector_store.get_config(str(job["connector_id"]))) is not None
                    and _is_controller_owned(config.connector)
                )
            }
            if managed_job_ids:
                _require_controller_authorization(
                    authorization,
                    operation="controller-owned active-universe scheduling",
                )
            now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            for job in fanout["jobs"]:
                connector_id = str(job["connector_id"])
                config = connector_store.get_config(connector_id)
                if config is None:
                    skipped.append({**job, "reason": "connector-config-missing"})
                    continue
                if config.connector.status == ConnectorStatus.DISABLED:
                    skipped.append({**job, "reason": "connector-disabled"})
                    continue
                frontier = store.enqueue_frontier(
                    connector_id=connector_id,
                    trace_id=request.trace_id or f"active-universe-{connector_id}-{request.run_date}",
                    trigger_type="active_universe_scheduled",
                    max_attempts=FRONTIER_MAX_ATTEMPTS,
                    available_at=now_iso,
                    job_parameters=job,
                )
                enqueued.append(frontier.to_dict())

    return {
        **fanout,
        "enqueued": enqueued,
        "skipped": skipped,
        "summary": {
            **fanout["summary"],
            "enqueued_count": len(enqueued),
            "skipped_count": len(skipped),
        },
    }


@app.get("/api/source-ingest/schemas/source-record")
def source_record_schema() -> dict[str, Any]:
    return _source_record_schema()


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


@app.put("/api/source-ingest/connectors/{connector_id}/lifecycle")
def set_connector_lifecycle(
    connector_id: str,
    request: SetConnectorLifecycleRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        with authoritative_reconcile_lock:
            _fence_managed_connector_mutation(
                connector_id,
                authorization,
                operation="controller-owned connector lifecycle mutation",
            )
            return _set_connector_lifecycle(connector_id, request)
    except SourceEvidenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/source-ingest/jobs", status_code=201)
def trigger_job(
    request: TriggerIngestJobRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    with authoritative_reconcile_lock:
        connector_id = request.connector.connector_id if request.connector is not None else str(request.connector_id or "")
        proposed = request.connector.to_domain() if request.connector is not None else None
        _fence_managed_connector_mutation(
            connector_id,
            authorization,
            operation="controller-owned connector ingest mutation",
            proposed_connector=proposed,
        )
        return _run_ingest_request(request)


@app.post("/api/source-ingest/source-records", status_code=201)
def ingest_source_records(
    request: SourceRecordIngestRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if not request.records:
        raise HTTPException(status_code=400, detail="records is required for SourceRecord ingest")
    with authoritative_reconcile_lock:
        connector_id = request.connector.connector_id if request.connector is not None else str(request.connector_id or "")
        proposed = request.connector.to_domain() if request.connector is not None else None
        _fence_managed_connector_mutation(
            connector_id,
            authorization,
            operation="controller-owned connector source-record mutation",
            proposed_connector=proposed,
        )
        return _run_ingest_request(
            TriggerIngestJobRequest(
                connector=request.connector,
                connector_id=request.connector_id,
                trace_id=request.trace_id,
                trigger_type=request.trigger_type,
                records=request.records,
                next_watermark=request.next_watermark,
            )
        )


@app.get("/api/source-ingest/jobs")
def list_jobs() -> dict[str, Any]:
    return {
        "runs": [run.to_dict() for run in store.list_runs()],
        "receipts": [receipt.to_dict() for receipt in store.list_receipts()],
    }


@app.get("/api/source-ingest/jobs/{ingest_run_id}")
def get_job(ingest_run_id: str) -> dict[str, Any]:
    run = store.get_run(ingest_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ingest run not found")
    receipt = store.get_receipt(ingest_run_id)
    return {
        "run": run.to_dict(),
        "receipt": receipt.to_dict() if receipt is not None else None,
    }


@app.get("/api/source-ingest/receipts")
def list_ingest_receipts(connector_id: str | None = None) -> dict[str, Any]:
    return {"receipts": [receipt.to_dict() for receipt in store.list_receipts(connector_id=connector_id)]}


@app.get("/api/source-ingest/receipts/{ingest_run_id}")
def get_ingest_receipt(ingest_run_id: str) -> dict[str, Any]:
    receipt = store.get_receipt(ingest_run_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="ingest receipt not found")
    return {"receipt": receipt.to_dict()}


@app.get("/api/source-ingest/watermarks/{connector_id}")
def get_watermark(connector_id: str) -> dict[str, Any]:
    watermark = store.get_watermark(connector_id)
    if watermark is None:
        raise HTTPException(status_code=404, detail="source watermark not found")
    return {"watermark": watermark.to_dict()}


@app.get("/api/source-ingest/frontier")
def list_crawl_frontier(
    status: Literal["queued", "running", "done", "failed", "retry"] | None = None,
) -> dict[str, Any]:
    return {"frontier": [item.to_dict() for item in store.list_frontier(status=status)]}


@app.post("/api/source-ingest/frontier/{frontier_id}/replay")
def replay_frontier(frontier_id: str, request: ReplayFrontierRequest | None = None) -> dict[str, Any]:
    with source_execution_lock:
        try:
            trace_id = request.trace_id if request and request.trace_id else f"frontier-replay-{frontier_id}"
            store.replay_frontier(frontier_id, trace_id=trace_id)
            item = store.claim_frontier(frontier_id)
            result, evidence_refs, frontier, source_search_refresh = _run_frontier_item(item)
            return {**_result_payload(result, evidence_refs, source_search_refresh), "frontier": frontier.to_dict()}
        except (EvidenceValidationError, SourceEvidenceError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    entries = dead_letter_queue.entries(status=status)
    all_entries = dead_letter_queue.entries()
    status_counts = {
        item.value: sum(1 for entry in all_entries if entry.status == item)
        for item in DeadLetterStatus
    }
    unresolved_count = sum(
        status_counts[item.value]
        for item in (
            DeadLetterStatus.PENDING,
            DeadLetterStatus.REPLAY_FAILED,
            DeadLetterStatus.SCHEMA_REJECTED,
        )
    )
    return {
        "entries": [entry.to_dict() for entry in entries],
        "entry_count": len(entries),
        "pending_count": status_counts[DeadLetterStatus.PENDING.value],
        "unresolved_count": unresolved_count,
        "status_counts": status_counts,
    }


@app.post("/api/source-ingest/dlq/replay")
def replay_dlq(request: ReplayDlqRequest) -> dict[str, Any]:
    with source_execution_lock:
        before_entries = {
            entry.entry_id: entry
            for entry in _unresolved_dead_letter_entries(tag_filter=request.tag or None)
        }
        entries = list(before_entries.values())
        requested: set[str] = set()
        if request.entry_ids:
            requested = set(request.entry_ids)
            entries = [entry for entry in entries if entry.entry_id in requested]
        unique_entries: list[Any] = []
        seen_frontiers: set[tuple[str, str]] = set()
        for entry in entries:
            frontier_id = str(entry.event.payload.get("frontier_id") or "").strip()
            connector_id = str(entry.event.payload.get("connector_id") or "").strip()
            if frontier_id:
                correlation = (frontier_id, connector_id)
                if correlation in seen_frontiers:
                    continue
                seen_frontiers.add(correlation)
            unique_entries.append(entry)
        actor_ref = ActorRef(ActorType.SERVICE, request.actor_id, roles=("source_ingest_replay",))
        replay_result = replay_processor.replay(
            unique_entries,
            actor_ref=actor_ref,
            environment=scheduler.environment,
            reason=request.reason,
            queue=dead_letter_queue,
            apply_fn=_replay_source_event,
            before_replace_fn=lambda result: _append_audit_actions((result.audit_action,)),
        )
        selected_entry_ids = {entry.entry_id for entry in unique_entries}
        after_entries = {entry.entry_id: entry for entry in dead_letter_queue.entries()}
        correlated_resolutions = []
        for entry_id, before in sorted(before_entries.items()):
            after = after_entries.get(entry_id)
            if after is None or (
                after.status == before.status
                and after.replay_attempts == before.replay_attempts
            ):
                continue
            correlated_resolutions.append(
                {
                    "entry_id": entry_id,
                    "previous_status": before.status.value,
                    "status": after.status.value,
                    "previous_replay_attempts": before.replay_attempts,
                    "replay_attempts": after.replay_attempts,
                    "explicitly_requested": entry_id in requested if request.entry_ids else False,
                    "selected_for_execution": entry_id in selected_entry_ids,
                }
            )
        payload = replay_result.to_dict()
        payload["selected_entry_ids"] = sorted(selected_entry_ids)
        payload["correlated_resolutions"] = correlated_resolutions
        payload["summary"]["correlated_resolution_count"] = len(correlated_resolutions)
        return payload


@app.put("/api/source-ingest/connectors/{connector_id}/schedule")
def set_connector_schedule(
    connector_id: str,
    request: SetScheduleRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    config = connector_store.get_config(connector_id)
    if config is None:
        raise HTTPException(status_code=404, detail="connector config not found")
    try:
        with authoritative_reconcile_lock:
            _fence_managed_connector_mutation(
                connector_id,
                authorization,
                operation="controller-owned connector schedule mutation",
            )
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
def run_scheduled_connectors(
    request: RunScheduledRequest | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    with source_execution_lock:
        if any(_is_controller_owned(config.connector) for config in connector_store.list_configs()):
            _require_controller_authorization(
                authorization,
                operation="controller-owned scheduled source execution",
            )
        return _run_scheduled_connectors(request)


def _run_scheduled_connectors(request: RunScheduledRequest | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    now_iso = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    max_concurrency = request.max_concurrency if request and request.max_concurrency is not None else SCHEDULER_MAX_CONCURRENCY
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
    if max_concurrency > SCHEDULER_MAX_CONCURRENCY:
        raise HTTPException(
            status_code=400,
            detail=f"max_concurrency exceeds SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY={SCHEDULER_MAX_CONCURRENCY}",
        )
    schedules = schedule_config_store.list_schedules()
    enqueued: list[dict[str, Any]] = []
    ran: list[dict[str, Any]] = []
    skipped: list[str] = []
    excluded: list[str] = []
    failed: list[dict[str, Any]] = []
    resolved_dlq = _resolve_pending_dlq_for_completed_frontiers()
    recovered_frontier = store.recover_stale_running(
        timeout_seconds=FRONTIER_RUNNING_TIMEOUT_SECONDS,
        now=now_iso,
    )
    active_frontier_connector_ids = {
        item.connector_id
        for item in store.list_frontier()
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
        watermark = store.get_watermark(sched.connector_id)
        if watermark is not None and sched.connector_id not in force_connector_ids:
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
            _register_or_validate_connector(config.connector)
            frontier = store.enqueue_frontier(
                connector_id=sched.connector_id,
                trace_id=f"scheduled-{sched.connector_id}-{int(now.timestamp())}",
                trigger_type="scheduled",
                max_attempts=FRONTIER_MAX_ATTEMPTS,
                available_at=now_iso,
            )
            enqueued.append(frontier.to_dict())
            active_frontier_connector_ids.add(sched.connector_id)
        except (EvidenceValidationError, SourceEvidenceError) as exc:
            failed.append({"connector_id": sched.connector_id, "error": str(exc)})

    claimed = store.claim_due_frontier(
        limit=max_concurrency,
        now=now_iso,
        connector_ids=exclusive_connector_ids or None,
    )
    for frontier in claimed:
        try:
            result, evidence_refs, updated_frontier, source_search_refresh = _run_frontier_item(frontier)
            payload = {
                "connector_id": frontier.connector_id,
                "frontier": updated_frontier.to_dict(),
                "run": result.run.to_dict(),
                "evidence_refs": evidence_refs,
                "source_search_refresh": source_search_refresh,
                "receipt": (
                    store.get_receipt(result.run.ingest_run_id).to_dict()
                    if store.get_receipt(result.run.ingest_run_id) is not None
                    else None
                ),
            }
            if result.run.status.value != "completed":
                failed.append({**payload, "error": _result_error(result)})
            else:
                ran.append(payload)
        except (EvidenceValidationError, SourceEvidenceError) as exc:
            latest = store.get_frontier(frontier.frontier_id)
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


@app.get("/api/source-ingest/audit")
def list_audit() -> dict[str, Any]:
    return {"actions": _load_audit_actions()}


# ---------------------------------------------------------------------------
# LLM Source-Change Proposal routes (DATASTRAT-PROPOSAL-006)
# ---------------------------------------------------------------------------
# Design invariant: LLM agents may only create draft proposals through the
# /api/source-change-proposals POST endpoint.  All approval and apply
# transitions require an explicit operator-gated action endpoint.
# ---------------------------------------------------------------------------

class ProposedSourceBody(StrictBaseModel):
    source_id: str
    source_kind: str
    provider: str
    source_class: str
    license_scope: str
    allowed_use: list[str]
    homepage_url: str | None = None
    docs_url: str | None = None
    entitlement_required: bool = False
    entitlement_tags: list[str] = Field(default_factory=list)
    expected_datasets: list[str] = Field(default_factory=list)
    update_frequency: str | None = None
    cost_notes: str | None = None


class ProposalRiskBody(StrictBaseModel):
    risk_type: str
    severity: str
    note: str


class CreateProposalRequest(StrictBaseModel):
    proposal_type: str
    source_kind: str
    rationale: str
    proposed_by: dict[str, Any]
    target_source_id: str | None = None
    proposed_source: ProposedSourceBody | None = None
    expected_value: dict[str, Any] = Field(default_factory=dict)
    risks: list[ProposalRiskBody] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMProposalRequest(StrictBaseModel):
    """LLM-originated proposal — always creates a draft via the adapter."""
    proposal_type: str
    source_kind: str
    rationale: str
    agent_id: str
    trace_id: str | None = None
    target_source_id: str | None = None
    proposed_source: ProposedSourceBody | None = None
    expected_value: dict[str, Any] = Field(default_factory=dict)
    risks: list[ProposalRiskBody] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _proposal_to_response(proposal: SourceChangeProposal) -> dict[str, Any]:
    return proposal.to_dict()


@app.get("/api/source-change-proposals")
def list_proposals(
    status: str | None = None,
    proposal_type: str | None = None,
    source_kind: str | None = None,
) -> dict[str, Any]:
    try:
        proposals = proposal_store.list(
            status=status if status else None,
            proposal_type=proposal_type if proposal_type else None,
            source_kind=source_kind if source_kind else None,
        )
    except (SourceChangeProposalError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"proposals": [_proposal_to_response(p) for p in proposals]}


@app.post("/api/source-change-proposals", status_code=201)
def create_proposal(request: CreateProposalRequest) -> dict[str, Any]:
    """Create a new source-change proposal (draft only).

    Operator or LLM callers that need to enforce the draft-only restriction
    should use /api/source-change-proposals/llm-draft instead.
    """
    try:
        proposed_source = None
        if request.proposed_source is not None:
            proposed_source = ProposedSourceInfo.from_dict(request.proposed_source.model_dump())
        from .registry.proposals import ProposalRisk
        risks = [ProposalRisk(risk_type=r.risk_type, severity=r.severity, note=r.note)
                 for r in request.risks]
        proposal = SourceChangeProposal(
            proposal_id=f"prop-{re.sub(r'[^a-z0-9]', '-', request.rationale[:20].lower())}-{sha256(request.rationale.encode()).hexdigest()[:8]}",
            proposal_type=request.proposal_type,
            source_kind=request.source_kind,
            rationale=request.rationale,
            proposed_by=request.proposed_by,
            status=ProposalStatus.DRAFT.value,
            target_source_id=request.target_source_id,
            proposed_source=proposed_source,
            expected_value=request.expected_value,
            risks=risks,
            evidence_refs=request.evidence_refs,
            metadata=request.metadata,
        )
        created = proposal_store.create_draft(proposal)
        return _proposal_to_response(created)
    except SourceChangeProposalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/source-change-proposals/llm-draft", status_code=201)
def create_llm_draft_proposal(request: LLMProposalRequest) -> dict[str, Any]:
    """LLM-originated proposal.  The adapter enforces draft-only status."""
    try:
        pt = ProposalType(request.proposal_type)
        sk = SourceKind(request.source_kind)
        proposed_source_data = request.proposed_source.model_dump() if request.proposed_source else None
        risks_data = [r.model_dump() for r in request.risks]
        if pt == ProposalType.ADD_DATA_SOURCE:
            proposal = llm_proposal_adapter.propose_add_data_source(
                agent_id=request.agent_id,
                proposed_source=proposed_source_data or {},
                rationale=request.rationale,
                trace_id=request.trace_id,
                expected_value=request.expected_value or None,
                risks=risks_data or None,
                evidence_refs=request.evidence_refs or None,
                metadata=request.metadata or None,
            )
        elif pt == ProposalType.ADD_STRATEGY_SEED_SOURCE:
            proposal = llm_proposal_adapter.propose_add_strategy_seed_source(
                agent_id=request.agent_id,
                proposed_source=proposed_source_data or {},
                rationale=request.rationale,
                trace_id=request.trace_id,
                expected_value=request.expected_value or None,
                risks=risks_data or None,
                evidence_refs=request.evidence_refs or None,
                metadata=request.metadata or None,
            )
        elif pt == ProposalType.DISABLE_SOURCE:
            proposal = llm_proposal_adapter.propose_disable_source(
                agent_id=request.agent_id,
                target_source_id=request.target_source_id or "",
                source_kind=sk.value,
                rationale=request.rationale,
                trace_id=request.trace_id,
                risks=risks_data or None,
                evidence_refs=request.evidence_refs or None,
                metadata=request.metadata or None,
            )
        elif pt == ProposalType.RETIRE_SOURCE:
            proposal = llm_proposal_adapter.propose_retire_source(
                agent_id=request.agent_id,
                target_source_id=request.target_source_id or "",
                source_kind=sk.value,
                rationale=request.rationale,
                trace_id=request.trace_id,
                risks=risks_data or None,
                evidence_refs=request.evidence_refs or None,
                metadata=request.metadata or None,
            )
        elif pt == ProposalType.REPLACE_SOURCE:
            replacement_id = str(request.metadata.get("replacement_source_id") or "")
            proposal = llm_proposal_adapter.propose_replace_source(
                agent_id=request.agent_id,
                target_source_id=request.target_source_id or "",
                source_kind=sk.value,
                rationale=request.rationale,
                replacement_source_id=replacement_id,
                trace_id=request.trace_id,
                risks=risks_data or None,
                evidence_refs=request.evidence_refs or None,
                metadata={k: v for k, v in request.metadata.items() if k != "replacement_source_id"},
            )
        elif pt == ProposalType.CHANGE_SCHEDULE:
            schedule_change = dict(request.metadata.get("schedule_change") or {})
            proposal = llm_proposal_adapter.propose_change_schedule(
                agent_id=request.agent_id,
                target_source_id=request.target_source_id or "",
                source_kind=sk.value,
                rationale=request.rationale,
                schedule_change=schedule_change,
                trace_id=request.trace_id,
                risks=risks_data or None,
                evidence_refs=request.evidence_refs or None,
                metadata={k: v for k, v in request.metadata.items() if k != "schedule_change"},
            )
        elif pt == ProposalType.REQUEST_VENDOR_QUOTE:
            proposal = llm_proposal_adapter.propose_request_vendor_quote(
                agent_id=request.agent_id,
                source_kind=sk.value,
                rationale=request.rationale,
                proposed_source=proposed_source_data,
                trace_id=request.trace_id,
                evidence_refs=request.evidence_refs or None,
                metadata=request.metadata or None,
            )
        else:
            raise SourceChangeProposalError(f"Unsupported proposal_type for LLM draft: {pt.value}")
        return _proposal_to_response(proposal)
    except SourceChangeProposalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/source-change-proposals/{proposal_id}")
def get_proposal(proposal_id: str) -> dict[str, Any]:
    proposal = proposal_store.get(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return _proposal_to_response(proposal)


@app.post("/api/source-change-proposals/{proposal_id}/actions/submit")
def submit_proposal(proposal_id: str) -> dict[str, Any]:
    try:
        return _proposal_to_response(proposal_store.submit(proposal_id))
    except SourceChangeProposalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/source-change-proposals/{proposal_id}/actions/approve")
def approve_proposal(proposal_id: str) -> dict[str, Any]:
    try:
        return _proposal_to_response(proposal_store.approve(proposal_id))
    except SourceChangeProposalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/source-change-proposals/{proposal_id}/actions/reject")
def reject_proposal(proposal_id: str) -> dict[str, Any]:
    try:
        return _proposal_to_response(proposal_store.reject(proposal_id))
    except SourceChangeProposalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ApplyProposalRequest(StrictBaseModel):
    change_ref: str | None = None


@app.post("/api/source-change-proposals/{proposal_id}/actions/apply")
def apply_proposal(proposal_id: str, request: ApplyProposalRequest | None = None) -> dict[str, Any]:
    try:
        change_ref = request.change_ref if request else None
        return _proposal_to_response(proposal_store.apply(proposal_id, change_ref=change_ref))
    except SourceChangeProposalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/source-change-proposals/{proposal_id}/actions/retire")
def retire_proposal(proposal_id: str) -> dict[str, Any]:
    try:
        return _proposal_to_response(proposal_store.retire(proposal_id))
    except SourceChangeProposalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Source health and usage routes (DATASTRAT-USAGE-007)
# ---------------------------------------------------------------------------


class UpsertHealthRequest(StrictBaseModel):
    source_id: str
    source_kind: str
    status: str = "ok"
    last_success_at: str | None = None
    last_failure_at: str | None = None
    latest_watermark: str | None = None
    row_count_last_run: int = 0
    rejected_count_last_run: int = 0
    schema_hash: str | None = None
    staleness_seconds: int | None = None
    error_rate_7d: float = 0.0
    cost_estimate_30d: float | None = None
    metadata: dict[str, Any] = {}


class UpsertUsageRequest(StrictBaseModel):
    date: str
    source_id: str
    source_kind: str
    ingest_run_count: int = 0
    query_count: int = 0
    search_hit_count: int = 0
    persona_match_count: int = 0
    strategy_seed_yield_count: int = 0
    strategy_promotion_count: int = 0
    experiment_dependency_count: int = 0
    active_strategy_dependency_count: int = 0
    cost_estimate: float | None = None


@app.get("/api/source-ingest/health")
def list_source_health(source_kind: str | None = None) -> dict[str, Any]:
    """List source health records, optionally filtered by source_kind."""
    records = source_health_store.list(source_kind=source_kind)
    return {
        "health_records": [r.to_dict() for r in records],
        "count": len(records),
    }


@app.get("/api/source-ingest/health/{source_id}")
def get_source_health(source_id: str) -> dict[str, Any]:
    health = source_health_store.get(source_id)
    if health is None:
        raise HTTPException(status_code=404, detail=f"No health record for source: {source_id}")
    return health.to_dict()


@app.put("/api/source-ingest/health/{source_id}", status_code=200)
def upsert_source_health(source_id: str, request: UpsertHealthRequest) -> dict[str, Any]:
    if request.source_id != source_id:
        raise HTTPException(status_code=400, detail="source_id in body must match path parameter")
    try:
        health = SourceHealth.from_dict(request.model_dump())
        source_health_store.upsert(health)
        return health.to_dict()
    except SourceHealthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/source-ingest/usage")
def list_source_usage(
    source_id: str | None = None,
    source_kind: str | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    """List daily usage records, optionally filtered."""
    records = source_usage_store.list(source_id=source_id, source_kind=source_kind, date=date)
    return {
        "usage_records": [r.to_dict() for r in records],
        "count": len(records),
    }


@app.post("/api/source-ingest/usage", status_code=201)
def upsert_source_usage(request: UpsertUsageRequest) -> dict[str, Any]:
    """Upsert a daily usage record for a source."""
    try:
        record = SourceUsageDaily.from_dict(request.model_dump())
        source_usage_store.upsert(record)
        return record.to_dict()
    except SourceHealthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/source-ingest/health/{source_id}/usage-aggregate")
def get_source_usage_aggregate(source_id: str, days: int = 30) -> dict[str, Any]:
    """Aggregate usage for one source over the most recent N days."""
    return source_usage_store.aggregate_for_source(source_id, days=max(1, min(days, 365)))


@app.get("/api/source-ingest/retirement-recommendations")
def list_retirement_recommendations(
    source_kind: str | None = None,
    low_usage_threshold: int = 5,
    high_failure_threshold: float = 0.5,
    high_cost_threshold_30d: float = 1000.0,
    low_yield_threshold: int = 1,
    observation_window_days: int = 30,
) -> dict[str, Any]:
    """Compute retirement recommendations for all tracked sources.

    Recommendations are pure computations over stored health and usage data.
    They do not mutate any state. To act on a recommendation, create a
    SourceChangeProposal through /api/source-change-proposals.
    """
    health_records = source_health_store.list(source_kind=source_kind)
    recommendations = compute_recommendations(
        health_records,
        lambda sid: source_usage_store.aggregate_for_source(sid),
        low_usage_threshold=low_usage_threshold,
        high_failure_threshold=high_failure_threshold,
        high_cost_threshold_30d=high_cost_threshold_30d,
        low_yield_threshold=low_yield_threshold,
        observation_window_seconds=observation_window_days * 86400,
    )
    return {
        "recommendations": [r.to_dict() for r in recommendations],
        "count": len(recommendations),
        "summary": {
            rt.value: sum(1 for r in recommendations if r.recommendation == rt)
            for rt in RecommendationType
        },
    }


@app.get("/api/source-ingest/health-usage-snapshot")
def get_health_usage_snapshot() -> dict[str, Any]:
    """Composite snapshot of source health, usage aggregates, and retirement recommendations.

    Designed to be consumed by the BFF ops surface without requiring
    multiple round-trips. Returns health records enriched with their
    30-day usage aggregate and computed recommendation.
    """
    health_records = source_health_store.list()
    recommendations = compute_recommendations(
        health_records,
        lambda sid: source_usage_store.aggregate_for_source(sid),
    )
    rec_map = {r.source_id: r for r in recommendations}

    enriched: list[dict[str, Any]] = []
    for health in health_records:
        rec = rec_map.get(health.source_id)
        usage = source_usage_store.aggregate_for_source(health.source_id)
        enriched.append({
            "health": health.to_dict(),
            "usage_aggregate_30d": usage,
            "recommendation": rec.to_dict() if rec else None,
        })

    return {
        "source_count": len(enriched),
        "sources": enriched,
        "recommendation_summary": {
            rt.value: sum(1 for r in recommendations if r.recommendation == rt)
            for rt in RecommendationType
        },
    }


# ---------------------------------------------------------------------------
# Ops acceptance: coverage matrix, alerts, and gap report
# (DATASTRAT-MARKETDATA-OPS-ACCEPT-010)
# ---------------------------------------------------------------------------


@app.get("/api/source-ingest/coverage-matrix")
def source_coverage_matrix() -> dict[str, Any]:
    """Coverage matrix: planned financial-catalog connectors vs configured runtime connectors.

    Returns per-connector coverage classification (missing / configured /
    disabled / credential_unavailable / unhealthy / healthy) so the ops
    acceptance dashboard can show rollout completeness at a glance.
    """
    catalog = financial_data_source_catalog_payload()
    catalog_entries = catalog["entries"]

    configured_by_id = {config.connector.connector_id: config for config in connector_store.list_configs()}
    configured_ids = set(configured_by_id)

    health_by_id: dict[str, Any] = {h.source_id: h.to_dict() for h in source_health_store.list()}
    lifecycle_by_id: dict[str, str] = {
        cid: configured_by_id[cid].connector.status.value
        for cid in configured_ids
    }

    return build_coverage_matrix(
        catalog_entries=catalog_entries,
        configured_connector_ids=configured_ids,
        health_by_connector_id=health_by_id,
        lifecycle_by_connector_id=lifecycle_by_id,
    )


@app.get("/api/source-ingest/alerts")
def list_source_alerts(include_missing: bool = True) -> dict[str, Any]:
    """List sources that require operator attention.

    Includes:
    - Configured sources with health status != ok (stale/degraded/failed).
    - Credential-unavailable sources (labelled separately so they are not
      treated as hard failures when keys are not yet installed).
    - When include_missing=true (default), catalog-planned connectors that
      are not yet configured in the runtime.

    This endpoint is the canonical alert surface for the source health
    dashboard. Operators should resolve or acknowledge each alert.
    """
    health_records = [h.to_dict() for h in source_health_store.list()]
    configured_ids = {config.connector.connector_id for config in connector_store.list_configs()}
    catalog_entries = financial_data_source_catalog_payload()["entries"]

    alerts = build_source_alerts(
        catalog_entries=catalog_entries,
        configured_connector_ids=configured_ids,
        health_records=health_records,
        include_missing=include_missing,
    )

    summary: dict[str, int] = {}
    for alert in alerts:
        at = str(alert.get("alert_type") or "unknown")
        summary[at] = summary.get(at, 0) + 1

    return {
        "alert_count": len(alerts),
        "alerts": alerts,
        "summary": summary,
    }


class GapReportRequest(StrictBaseModel):
    members: list[ActiveUniverseMemberBody]
    rules: list[SourceUpdateRuleBody] = Field(default_factory=list)
    run_date: str
    default_max_symbols_per_job: int = 50
    render_markdown: bool = False


@app.post("/api/source-ingest/gap-report")
def generate_gap_report(request: GapReportRequest) -> dict[str, Any]:
    """Generate a market-data gap report for the given active-universe members and date.

    Classifies each expected ingest job as credential / quota / provider_stale /
    schema / parse / not-in-universe based on the source health records stored
    in this service. A job is considered a gap when the connector is not healthy
    or the latest watermark does not reach the requested run_date.

    Set render_markdown=true to include a human-readable markdown summary in the
    response. The machine-readable JSON report is always returned.
    """
    try:
        rules = [rule.to_domain() for rule in request.rules] if request.rules else DEFAULT_SOURCE_UPDATE_RULES
        health_records = source_health_store.list()
        report = generate_market_data_gap_report(
            members=[member.to_domain() for member in request.members],
            rules=rules,
            health_records=health_records,
            run_date=request.run_date,
            default_max_symbols_per_job=request.default_max_symbols_per_job,
        )
        payload: dict[str, Any] = {"report": report}
        if request.render_markdown:
            payload["markdown"] = render_gap_report_markdown(report)
        return payload
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
