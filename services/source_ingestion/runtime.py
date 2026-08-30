"""Runtime module and composition factory for Source Ingestion.

Constructs existing managers, stores, writers, and engines once and provides
narrow operations and dependencies for routers and entrypoints.
"""

from __future__ import annotations

import fcntl
import hmac
import json
import os
import re
import threading
import urllib.parse
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
from services.knowledge.evidence import EvidenceBundleBuilder, normalize_source_record
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
from .connector_definitions import (
    DEPLOYED_CONNECTOR_DEFINITIONS,
    get_connector_definition,
    list_connector_definitions,
)
from .source_management_models import (
    SourceManagementCommand,
    SourceManagementReceipt,
    SourceCanaryResult,
    CommandType,
    ReceiptStatus,
)
from .source_management_store import (
    SourceManagementStore,
    build_source_management_store,
    SourceInstanceNotFoundError,
    StaleRevisionError,
    IdempotencyConflictError,
    DuplicateInstanceError,
    SourceManagementContractError,
)
from .source_management_commands import (
    SourceCommandEngine,
    CommandPreconditionError,
    AdapterNotSupportedError,
)
from .api_models import (
    ConfigureConnectorRequest,
    ConfiguredFetchBody,
    PersonaSourceProvisioningRequest,
    RunScheduledRequest,
    SetConnectorLifecycleRequest,
    TriggerIngestJobRequest,
)
from .pipeline import IngestPipelineService


def _resolve_data_dir() -> Path:
    data_dir = Path(os.getenv("SOURCE_INGEST_DATA_DIR", "/tmp/pantheon/source-ingest"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _frontier_backlog_readback(frontier: Iterable[Any]) -> tuple[int, dict[str, int]]:
    backlog_by_connector: dict[str, int] = {}
    for item in frontier:
        if item.status not in {"queued", "retry", "running"}:
            continue
        backlog_by_connector[item.connector_id] = backlog_by_connector.get(item.connector_id, 0) + 1
    ordered = dict(sorted(backlog_by_connector.items()))
    return sum(ordered.values()), ordered


class SourceIngestionRuntime:
    """Explicit runtime holding stores, managers, locks, and configuration."""

    def __init__(self, data_dir: Path | None = None, module: Any = None) -> None:
        self._module = module
        self._DATA_DIR = data_dir or _resolve_data_dir()
        self._PROPOSAL_STORE_PATH = Path(
            os.getenv("SOURCE_INGEST_PROPOSAL_STORE_PATH", str(self._DATA_DIR / "source_change_proposals.jsonl"))
        )
        self._SCHEDULE_STORE_PATH = Path(
            os.getenv("SOURCE_INGEST_STORE_PATH", str(self._DATA_DIR / "ingest_schedule.jsonl"))
        )
        self._CONNECTOR_STORE_PATH = Path(
            os.getenv("SOURCE_INGEST_CONNECTOR_STORE_PATH", str(self._DATA_DIR / "connector_config.jsonl"))
        )
        self._SOURCE_EVIDENCE_STORE_PATH = Path(
            os.getenv("SOURCE_INGEST_EVIDENCE_STORE_PATH", str(self._DATA_DIR / "source_evidence.jsonl"))
        )
        self._DLQ_STORE_PATH = Path(
            os.getenv("SOURCE_INGEST_DLQ_PATH", str(self._DATA_DIR / "source_ingest_dlq.jsonl"))
        )
        self._AUDIT_STORE_PATH = Path(
            os.getenv("SOURCE_INGEST_AUDIT_PATH", str(self._DATA_DIR / "source_ingest_audit.jsonl"))
        )
        self._CONNECTOR_SCHEDULE_CONFIG_PATH = Path(
            os.getenv("SOURCE_INGEST_SCHEDULE_CONFIG_PATH", str(self._DATA_DIR / "connector_schedule.jsonl"))
        )
        self._SOURCE_HEALTH_STORE_PATH = Path(
            os.getenv("SOURCE_INGEST_HEALTH_STORE_PATH", str(self._DATA_DIR / "source_health.jsonl"))
        )
        self._SOURCE_USAGE_STORE_PATH = Path(
            os.getenv("SOURCE_INGEST_USAGE_STORE_PATH", str(self._DATA_DIR / "source_usage_daily.jsonl"))
        )
        self._MARKET_DATA_STORAGE_ROOT = Path(
            os.getenv("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT", str(self._DATA_DIR / "market_data_store"))
        )
        self._CONTROLLER_STATE_PATH = Path(
            os.getenv("SOURCE_INGEST_CONTROLLER_STATE_PATH", str(self._DATA_DIR / "controller_state.json"))
        )
        self._REQUIREMENT_STATE_PATH = Path(
            os.getenv("SOURCE_INGEST_REQUIREMENT_STATE_PATH", str(self._DATA_DIR / "requirement_snapshots.jsonl"))
        )
        self._LATEST_MARKET_SNAPSHOT_PATH = Path(
            os.getenv(
                "SOURCE_INGEST_LATEST_MARKET_SNAPSHOT_PATH",
                str(self._DATA_DIR / "latest_market_snapshots.jsonl"),
            )
        )
        self._RECONCILE_TRANSACTION_LOCK_PATH = Path(
            os.getenv(
                "SOURCE_INGEST_RECONCILE_TRANSACTION_LOCK_PATH",
                str(self._DATA_DIR / "persona_source_reconcile.lock"),
            )
        )
        self._CONTROLLER_TOKEN_PATH = Path(
            os.getenv("SOURCE_INGEST_CONTROLLER_TOKEN_FILE", str(self._DATA_DIR / "controller_token"))
        )
        self._DISTILLATION_JOB_QUEUE_PATH = Path(
            os.getenv("DISTILLATION_JOB_QUEUE_PATH", str(self._DATA_DIR / "distillation_job_queue.sqlite3"))
        )
        self._SOURCE_RECORD_SCHEMA_PATH = Path(__file__).with_name("source_record.schema.json")
        self._MAX_RECORDS_PER_JOB = int(os.getenv("SOURCE_INGEST_MAX_RECORDS", "100"))
        self._SCHEDULER_MAX_CONCURRENCY = max(1, int(os.getenv("SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY", "2")))
        self._FRONTIER_MAX_ATTEMPTS = max(1, int(os.getenv("SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS", "2")))
        self._FRONTIER_BACKOFF_SECONDS = max(0, int(os.getenv("SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS", "60")))
        self._FRONTIER_RUNNING_TIMEOUT_SECONDS = max(
            1,
            int(os.getenv("SOURCE_INGEST_FRONTIER_RUNNING_TIMEOUT_SECONDS", "300")),
        )
        self._DEFAULT_STALE_THRESHOLD_SECONDS = max(
            1,
            int(os.getenv("SOURCE_INGEST_DEFAULT_STALE_THRESHOLD_SECONDS", "86400")),
        )
        self._MARKET_SNAPSHOT_MAX_CLOSES = max(
            2,
            int(os.getenv("SOURCE_INGEST_MARKET_SNAPSHOT_MAX_CLOSES", "60")),
        )
        self._SOURCE_TIMESTAMP_FUTURE_TOLERANCE_SECONDS = max(
            0,
            int(os.getenv("SOURCE_INGEST_FUTURE_TIMESTAMP_TOLERANCE_SECONDS", "300")),
        )
        self._SEARCH_INGEST_NOTIFY_URL = os.getenv("SEARCH_INGEST_NOTIFY_URL", "").rstrip("/")
        self._PRODUCTION_POSTURE = require_source_search_posture("source-ingest")
        self._SOURCE_READINESS_MAX_STATE_BYTES = max(
            1,
            int(os.getenv("SOURCE_INGEST_READINESS_MAX_STATE_BYTES", str(1024 * 1024))),
        )
        self._SOURCE_READINESS_MAX_CONTROLLER_AGE_SECONDS = max(
            1,
            int(os.getenv("SOURCE_INGEST_READINESS_MAX_CONTROLLER_AGE_SECONDS", "300")),
        )

        self._manager = IngestManager()
        self._proposal_store = SourceChangeProposalStore.from_jsonl(self._PROPOSAL_STORE_PATH)
        self._llm_proposal_adapter = LLMSourceProposalAdapter(self._proposal_store)
        self._store = JsonlIngestScheduleStore(self._SCHEDULE_STORE_PATH)
        self._connector_store = JsonlConfiguredConnectorStore(self._CONNECTOR_STORE_PATH)
        self._schedule_config_store = JsonlConnectorScheduleStore(self._CONNECTOR_SCHEDULE_CONFIG_PATH)
        self._configured_fetcher = ConfiguredConnectorFetcher(self._connector_store)
        self._evidence_repository = build_source_evidence_repository(self._SOURCE_EVIDENCE_STORE_PATH)
        self._evidence_builder = EvidenceBundleBuilder(self._evidence_repository)
        self._dead_letter_queue = DeadLetterQueue(self._DLQ_STORE_PATH)
        self._dead_letter_queue.load_from_spill()
        self._scheduler = IngestionScheduler(manager=self._manager, store=self._store, dead_letter_queue=self._dead_letter_queue)
        self._replay_processor = DeadLetterReplayProcessor(schema_registry=SchemaRegistry())
        self._source_health_store = SourceHealthStore.from_jsonl(self._SOURCE_HEALTH_STORE_PATH)
        self._source_usage_store = SourceUsageDailyStore.from_jsonl(self._SOURCE_USAGE_STORE_PATH)
        self._requirement_snapshot_store = RequirementSnapshotStore(self._REQUIREMENT_STATE_PATH)
        self._latest_market_snapshot_store = LatestMarketSnapshotStore(
            self._LATEST_MARKET_SNAPSHOT_PATH,
            max_closes=self._MARKET_SNAPSHOT_MAX_CLOSES,
        )
        self._controller_token = load_controller_token(token_path=self._CONTROLLER_TOKEN_PATH, create=True)
        self._authoritative_reconcile_lock = threading.RLock()
        self._audit_store_lock = threading.RLock()
        self._source_execution_lock = threading.RLock()
        self._market_data_storage_writer = MarketDataStorageWriter(self._MARKET_DATA_STORAGE_ROOT)
        self._distillation_job_queue = DistillationJobQueue(self._DISTILLATION_JOB_QUEUE_PATH)
        self._source_management_store = build_source_management_store(self._DATA_DIR)
        self._source_command_engine = SourceCommandEngine(
            store=self._source_management_store,
            connector_store=self._connector_store,
            schedule_config_store=self._schedule_config_store,
            evidence_builder=self._evidence_builder,
        )

        self.pipeline = IngestPipelineService(self)

    def _active_attr(self, name: str, default: Any) -> Any:
        if getattr(self, "_module", None) is not None:
            val = self._module.__dict__.get(name)
            if val is not None:
                return val
        import sys
        main_mod = sys.modules.get("services.source_ingestion.main")
        if main_mod is not None:
            val = main_mod.__dict__.get(name)
            if val is not None:
                return val
        return default

    @property
    def DATA_DIR(self) -> Path:
        return self._active_attr("DATA_DIR", self._DATA_DIR)

    @property
    def store(self) -> JsonlIngestScheduleStore:
        return self._active_attr("store", self._store)

    @property
    def connector_store(self) -> JsonlConfiguredConnectorStore:
        return self._active_attr("connector_store", self._connector_store)

    @property
    def schedule_config_store(self) -> JsonlConnectorScheduleStore:
        return self._active_attr("schedule_config_store", self._schedule_config_store)

    @property
    def evidence_repository(self) -> Any:
        return self._active_attr("evidence_repository", self._evidence_repository)

    @property
    def evidence_builder(self) -> EvidenceBundleBuilder:
        return self._active_attr("evidence_builder", self._evidence_builder)

    @property
    def manager(self) -> IngestManager:
        return self._active_attr("manager", self._manager)

    @property
    def scheduler(self) -> IngestionScheduler:
        return self._active_attr("scheduler", self._scheduler)

    @property
    def dead_letter_queue(self) -> DeadLetterQueue:
        return self._active_attr("dead_letter_queue", self._dead_letter_queue)

    @property
    def replay_processor(self) -> DeadLetterReplayProcessor:
        return self._active_attr("replay_processor", self._replay_processor)

    @property
    def source_health_store(self) -> SourceHealthStore:
        return self._active_attr("source_health_store", self._source_health_store)

    @property
    def source_usage_store(self) -> SourceUsageDailyStore:
        return self._active_attr("source_usage_store", self._source_usage_store)

    @property
    def requirement_snapshot_store(self) -> RequirementSnapshotStore:
        return self._active_attr("requirement_snapshot_store", self._requirement_snapshot_store)

    @property
    def latest_market_snapshot_store(self) -> LatestMarketSnapshotStore:
        return self._active_attr("latest_market_snapshot_store", self._latest_market_snapshot_store)

    @property
    def authoritative_reconcile_lock(self) -> threading.RLock:
        return self._active_attr("authoritative_reconcile_lock", self._authoritative_reconcile_lock)

    @property
    def audit_store_lock(self) -> threading.RLock:
        return self._active_attr("audit_store_lock", self._audit_store_lock)

    @property
    def source_execution_lock(self) -> threading.RLock:
        return self._active_attr("source_execution_lock", self._source_execution_lock)

    @property
    def market_data_storage_writer(self) -> MarketDataStorageWriter:
        return self._active_attr("market_data_storage_writer", self._market_data_storage_writer)

    @property
    def distillation_job_queue(self) -> DistillationJobQueue:
        return self._active_attr("distillation_job_queue", self._distillation_job_queue)

    @property
    def source_management_store(self) -> SourceManagementStore:
        return self._active_attr("source_management_store", self._source_management_store)

    @property
    def source_command_engine(self) -> SourceCommandEngine:
        return self._active_attr("source_command_engine", self._source_command_engine)

    @property
    def configured_fetcher(self) -> ConfiguredConnectorFetcher:
        return self._active_attr("configured_fetcher", self._configured_fetcher)

    @property
    def proposal_store(self) -> SourceChangeProposalStore:
        return self._active_attr("proposal_store", self._proposal_store)

    @property
    def llm_proposal_adapter(self) -> LLMSourceProposalAdapter:
        return self._active_attr("llm_proposal_adapter", self._llm_proposal_adapter)

    @property
    def controller_token(self) -> str:
        return self._active_attr("controller_token", self._controller_token)

    @property
    def PROPOSAL_STORE_PATH(self) -> Path:
        return Path(self._active_attr("PROPOSAL_STORE_PATH", self._PROPOSAL_STORE_PATH))

    @property
    def SCHEDULE_STORE_PATH(self) -> Path:
        return Path(self._active_attr("SCHEDULE_STORE_PATH", self._SCHEDULE_STORE_PATH))

    @property
    def CONNECTOR_STORE_PATH(self) -> Path:
        return Path(self._active_attr("CONNECTOR_STORE_PATH", self._CONNECTOR_STORE_PATH))

    @property
    def SOURCE_EVIDENCE_STORE_PATH(self) -> Path:
        return Path(self._active_attr("SOURCE_EVIDENCE_STORE_PATH", self._SOURCE_EVIDENCE_STORE_PATH))

    @property
    def DLQ_STORE_PATH(self) -> Path:
        return Path(self._active_attr("DLQ_STORE_PATH", self._DLQ_STORE_PATH))

    @property
    def AUDIT_STORE_PATH(self) -> Path:
        return Path(self._active_attr("AUDIT_STORE_PATH", self._AUDIT_STORE_PATH))

    @property
    def CONNECTOR_SCHEDULE_CONFIG_PATH(self) -> Path:
        return Path(self._active_attr("CONNECTOR_SCHEDULE_CONFIG_PATH", self._CONNECTOR_SCHEDULE_CONFIG_PATH))

    @property
    def SOURCE_HEALTH_STORE_PATH(self) -> Path:
        return Path(self._active_attr("SOURCE_HEALTH_STORE_PATH", self._SOURCE_HEALTH_STORE_PATH))

    @property
    def SOURCE_USAGE_STORE_PATH(self) -> Path:
        return Path(self._active_attr("SOURCE_USAGE_STORE_PATH", self._SOURCE_USAGE_STORE_PATH))

    @property
    def MARKET_DATA_STORAGE_ROOT(self) -> Path:
        return Path(self._active_attr("MARKET_DATA_STORAGE_ROOT", self._MARKET_DATA_STORAGE_ROOT))

    @property
    def CONTROLLER_STATE_PATH(self) -> Path:
        return Path(self._active_attr("CONTROLLER_STATE_PATH", self._CONTROLLER_STATE_PATH))

    @property
    def REQUIREMENT_STATE_PATH(self) -> Path:
        return Path(self._active_attr("REQUIREMENT_STATE_PATH", self._REQUIREMENT_STATE_PATH))

    @property
    def LATEST_MARKET_SNAPSHOT_PATH(self) -> Path:
        return Path(self._active_attr("LATEST_MARKET_SNAPSHOT_PATH", self._LATEST_MARKET_SNAPSHOT_PATH))

    @property
    def RECONCILE_TRANSACTION_LOCK_PATH(self) -> Path:
        return Path(self._active_attr("RECONCILE_TRANSACTION_LOCK_PATH", self._RECONCILE_TRANSACTION_LOCK_PATH))

    @property
    def CONTROLLER_TOKEN_PATH(self) -> Path:
        return Path(self._active_attr("CONTROLLER_TOKEN_PATH", self._CONTROLLER_TOKEN_PATH))

    @property
    def DISTILLATION_JOB_QUEUE_PATH(self) -> Path:
        return Path(self._active_attr("DISTILLATION_JOB_QUEUE_PATH", self._DISTILLATION_JOB_QUEUE_PATH))

    @property
    def SOURCE_RECORD_SCHEMA_PATH(self) -> Path:
        return Path(self._active_attr("SOURCE_RECORD_SCHEMA_PATH", self._SOURCE_RECORD_SCHEMA_PATH))

    @property
    def MAX_RECORDS_PER_JOB(self) -> int:
        return int(self._active_attr("MAX_RECORDS_PER_JOB", self._MAX_RECORDS_PER_JOB))

    @property
    def SCHEDULER_MAX_CONCURRENCY(self) -> int:
        return int(self._active_attr("SCHEDULER_MAX_CONCURRENCY", self._SCHEDULER_MAX_CONCURRENCY))

    @property
    def FRONTIER_MAX_ATTEMPTS(self) -> int:
        return int(self._active_attr("FRONTIER_MAX_ATTEMPTS", self._FRONTIER_MAX_ATTEMPTS))

    @property
    def FRONTIER_BACKOFF_SECONDS(self) -> int:
        return int(self._active_attr("FRONTIER_BACKOFF_SECONDS", self._FRONTIER_BACKOFF_SECONDS))

    @property
    def FRONTIER_RUNNING_TIMEOUT_SECONDS(self) -> int:
        return int(self._active_attr("FRONTIER_RUNNING_TIMEOUT_SECONDS", self._FRONTIER_RUNNING_TIMEOUT_SECONDS))

    @property
    def DEFAULT_STALE_THRESHOLD_SECONDS(self) -> int:
        return int(self._active_attr("DEFAULT_STALE_THRESHOLD_SECONDS", self._DEFAULT_STALE_THRESHOLD_SECONDS))

    @property
    def MARKET_SNAPSHOT_MAX_CLOSES(self) -> int:
        return int(self._active_attr("MARKET_SNAPSHOT_MAX_CLOSES", self._MARKET_SNAPSHOT_MAX_CLOSES))

    @property
    def SOURCE_TIMESTAMP_FUTURE_TOLERANCE_SECONDS(self) -> int:
        return int(self._active_attr("SOURCE_TIMESTAMP_FUTURE_TOLERANCE_SECONDS", self._SOURCE_TIMESTAMP_FUTURE_TOLERANCE_SECONDS))

    @property
    def SEARCH_INGEST_NOTIFY_URL(self) -> str:
        return str(self._active_attr("SEARCH_INGEST_NOTIFY_URL", self._SEARCH_INGEST_NOTIFY_URL))

    @property
    def PRODUCTION_POSTURE(self) -> Any:
        return self._active_attr("PRODUCTION_POSTURE", self._PRODUCTION_POSTURE)

    # -----------------------------------------------------------------------
    # Authorization & Mutation Guards
    # -----------------------------------------------------------------------

    def _require_controller_authorization(self, authorization: str | None, *, operation: str) -> None:
        scheme, _, presented = str(authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not presented:
            raise HTTPException(
                status_code=401,
                detail=f"controller service authorization is required for {operation}",
            )
        current_token = load_controller_token(token_path=self.CONTROLLER_TOKEN_PATH, create=True)
        if not (
            hmac.compare_digest(presented, current_token)
            or hmac.compare_digest(presented, self.controller_token)
        ):
            raise HTTPException(
                status_code=403,
                detail=f"controller service authorization is invalid for {operation}",
            )

    def _require_service_authorization(self, authorization: str | None, *, operation: str) -> None:
        scheme, _, presented = str(authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not presented:
            raise HTTPException(
                status_code=401,
                detail=f"service authorization is required for {operation}",
            )
        current_controller_token = load_controller_token(token_path=self.CONTROLLER_TOKEN_PATH, create=True)
        service_token = os.getenv("SOURCE_INGEST_SERVICE_TOKEN", current_controller_token)
        if not (
            hmac.compare_digest(presented, current_controller_token)
            or hmac.compare_digest(presented, service_token)
            or hmac.compare_digest(presented, self.controller_token)
        ):
            raise HTTPException(
                status_code=403,
                detail=f"service authorization is invalid for {operation}",
            )

    def _is_controller_owned(self, connector: SourceConnector | None) -> bool:
        if connector is None:
            return False
        marker = connector.metadata.get(RECONCILIATION_METADATA_KEY)
        return bool(
            isinstance(marker, Mapping)
            and marker.get("managed_by") == "persona_source_provisioning_reconciler"
        )

    def _fence_managed_connector_mutation(
        self,
        connector_id: str,
        authorization: str | None,
        *,
        operation: str,
        proposed_connector: SourceConnector | None = None,
    ) -> None:
        existing = self.connector_store.get_config(connector_id)
        if self._is_controller_owned(existing.connector if existing is not None else None) or self._is_controller_owned(
            proposed_connector
        ):
            self._require_controller_authorization(authorization, operation=operation)

    def _register_or_validate_connector(self, connector: SourceConnector) -> SourceConnector:
        connector = validate_external_source_connector(connector)
        existing = self.manager.get_connector(connector.connector_id)
        if existing is None:
            return self.manager.register_connector(connector)
        if existing.to_dict() != connector.to_dict():
            raise SourceEvidenceError(f"Connector already registered with different contract: {connector.connector_id}")
        return existing

    def _assert_fetch_within_limit(self, fetch: ConfiguredFetchBody) -> None:
        if len(fetch.records) > self.MAX_RECORDS_PER_JOB:
            raise HTTPException(status_code=413, detail=f"fetch.records exceeds SOURCE_INGEST_MAX_RECORDS={self.MAX_RECORDS_PER_JOB}")
        if fetch.mode == "external_feed" and fetch.max_records > self.MAX_RECORDS_PER_JOB:
            raise HTTPException(
                status_code=413,
                detail=f"fetch.max_records exceeds SOURCE_INGEST_MAX_RECORDS={self.MAX_RECORDS_PER_JOB}",
            )

    def _configure_connector(self, request: ConfigureConnectorRequest) -> dict[str, Any]:
        self._assert_fetch_within_limit(request.fetch)
        connector = self._register_or_validate_connector(request.connector.to_domain())
        config = self.connector_store.upsert_config(connector, request.fetch.to_config())
        return {
            "connector": config.connector.to_dict(),
            "fetch": dict(config.fetch),
            "state": self.connector_store.get_fetch_state(connector.connector_id),
            "updated_at": config.updated_at,
        }

    def _source_record_schema(self) -> dict[str, Any]:
        return json.loads(self.SOURCE_RECORD_SCHEMA_PATH.read_text(encoding="utf-8"))

    # -----------------------------------------------------------------------
    # Summary & Metrics
    # -----------------------------------------------------------------------

    def _fetch_policy_summary(self, fetch: dict[str, Any] | None) -> dict[str, Any]:
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

    def _schedule_summary(self, connector_id: str) -> dict[str, Any]:
        return self._schedule_summary_from_config(
            self.schedule_config_store.get_schedule(connector_id)
        )

    def _schedule_summary_from_config(self, schedule: Any | None) -> dict[str, Any]:
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

    def _run_status_value(self, run: Any) -> str:
        status = getattr(run, "status", "")
        return status.value if hasattr(status, "value") else str(status)

    def _run_effective_at(self, run: Any) -> datetime:
        from .pipeline import _parse_utc_datetime

        return (
            _parse_utc_datetime(getattr(run, "finished_at", None))
            or _parse_utc_datetime(getattr(run, "started_at", None))
            or datetime.min.replace(tzinfo=timezone.utc)
        )

    def _connector_stale_threshold_seconds(
        self,
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
        return max(self.DEFAULT_STALE_THRESHOLD_SECONDS, cadence * 2)

    def _connector_freshness_summary_from_snapshot(
        self,
        connector_id: str,
        *,
        connector_metadata: Mapping[str, Any],
        schedule: Any | None,
        watermark: Any | None,
        runs: list[Any] | tuple[Any, ...],
        receipts: list[IngestReceipt] | tuple[IngestReceipt, ...],
        now: datetime,
    ) -> dict[str, Any]:
        from .pipeline import _parse_utc_datetime

        latest_run = max(runs, key=self._run_effective_at) if runs else None
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
            elif source_timestamp_dt > now + timedelta(seconds=self.SOURCE_TIMESTAMP_FUTURE_TOLERANCE_SECONDS):
                source_timestamp_status = "future"
            elif source_timestamp_status in {"unknown", "missing", "invalid", "future"}:
                source_timestamp_status = "valid"
        latest_run_at = self._run_effective_at(latest_run) if latest_run else None
        latest_run_status = self._run_status_value(latest_run) if latest_run else None
        next_due_at: str | None = None
        seconds_until_due: int | None = None
        staleness_seconds: int | None = None
        age_seconds = (
            max(0, int((now - source_timestamp_dt).total_seconds()))
            if source_timestamp_dt is not None and source_timestamp_status == "valid"
            else None
        )
        stale_threshold_seconds = self._connector_stale_threshold_seconds(
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

    def _connector_freshness_summary(self, connector_id: str) -> dict[str, Any]:
        config = self.connector_store.get_config(connector_id)
        schedule = self.schedule_config_store.get_schedule(connector_id)
        snapshot = self.store.read_freshness_snapshot()
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
        return self._connector_freshness_summary_from_snapshot(
            connector_id,
            connector_metadata=(config.connector.metadata if config is not None else {}),
            schedule=schedule,
            watermark=snapshot["watermarks"].get(connector_id),
            runs=runs,
            receipts=receipts,
            now=datetime.now(timezone.utc),
        )

    def _source_freshness_readiness(self) -> dict[str, Any]:
        if getattr(self, "_module", None) is not None:
            fn = self._module.__dict__.get("_source_freshness_readiness")
            if callable(fn) and getattr(fn, "__code__", None) is not None and fn.__code__ is not self._default_source_freshness_readiness.__code__:
                return fn()
        import sys
        main_mod = sys.modules.get("services.source_ingestion.main")
        if main_mod is not None:
            fn = main_mod.__dict__.get("_source_freshness_readiness")
            if callable(fn) and getattr(fn, "__code__", None) is not None and fn.__code__ is not self._default_source_freshness_readiness.__code__:
                return fn()
        return self._default_source_freshness_readiness()

    def _default_source_freshness_readiness(self) -> dict[str, Any]:
        """Return a fixed-cost readiness projection from the controller snapshot."""
        from .pipeline import _parse_utc_datetime

        try:
            state_size_bytes = self.CONTROLLER_STATE_PATH.stat().st_size
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
        if state_size_bytes > self._SOURCE_READINESS_MAX_STATE_BYTES:
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
            state = ControllerStateStore(self.CONTROLLER_STATE_PATH).load()
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
            or heartbeat_age_seconds > self._SOURCE_READINESS_MAX_CONTROLLER_AGE_SECONDS
        )
        return {
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

    def _source_runtime_metrics(self) -> dict[str, Any]:
        readiness = self._source_freshness_readiness()
        return {
            "controller_state_size_bytes": readiness.get("controller_state_size_bytes", 0),
            "connector_count": readiness.get("connector_inventory_count", 0),
            "scheduled_connector_count": readiness.get("scheduled_connector_count", 0),
            "stale_connector_count": readiness.get("stale_connector_count", 0),
            "degraded_connector_count": readiness.get("degraded_connector_count", 0),
            "posture_alert_count": self.PRODUCTION_POSTURE.alert_count(),
        }

    def _connector_schema_hash(self, connector: SourceConnector, fetch: dict[str, Any] | None) -> str:
        metadata_hash = connector.metadata.get("schema_hash")
        if metadata_hash not in (None, "", [], {}):
            return str(metadata_hash)
        payload = {
            "connector_id": connector.connector_id,
            "source_type": connector.source_type.value,
            "provider": connector.provider,
            "license_scope": connector.license_scope,
            "metadata": dict(connector.metadata),
            "fetch_policy": self._fetch_policy_summary(fetch),
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return sha256(body).hexdigest()[:16]

    def _expected_rows(self, connector: SourceConnector, fetch: dict[str, Any] | None) -> int | None:
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
        self,
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
            "expected_rows": self._expected_rows(connector, fetch),
            "watermark": freshness.get("last_watermark"),
            "schema_hash": self._connector_schema_hash(connector, fetch),
            "staleness_seconds": freshness.get("staleness_seconds"),
            "source_error": source_error,
        }

    def _connector_registry_entry(
        self,
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
            "fetch_policy": self._fetch_policy_summary(fetch),
            "schedule": schedule,
            "freshness": freshness,
            "health_metrics": self._connector_health_metrics(
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

    def _source_connector_entries(self) -> list[dict[str, Any]]:
        configs, fetch_states = self.connector_store.read_snapshot()
        configured_by_id = {config.connector.connector_id: config for config in configs}
        schedules = {
            schedule.connector_id: schedule
            for schedule in self.schedule_config_store.list_schedules()
        }
        freshness_snapshot = self.store.read_freshness_snapshot()
        runs_by_connector: dict[str, list[Any]] = {}
        for run in freshness_snapshot["runs"]:
            connector_id = str(getattr(run, "connector_id", ""))
            runs_by_connector.setdefault(connector_id, []).append(run)
        receipts_by_connector: dict[str, list[IngestReceipt]] = {}
        for receipt in freshness_snapshot["receipts"]:
            receipts_by_connector.setdefault(receipt.connector_id, []).append(receipt)
        observed_at = datetime.now(timezone.utc)

        connector_ids = set(configured_by_id)
        connectors = list(self.manager.list_connectors())
        connector_ids.update(connector.connector_id for connector in connectors)

        entries: list[dict[str, Any]] = []
        for connector_id in sorted(connector_ids):
            config = configured_by_id.get(connector_id)
            connector = config.connector if config else self.manager.get_connector(connector_id)
            if connector is None:
                continue
            schedule_config = schedules.get(connector_id)
            entries.append(
                self._connector_registry_entry(
                    connector,
                    fetch=dict(config.fetch) if config else None,
                    state=fetch_states.get(connector_id) if config else None,
                    schedule=self._schedule_summary_from_config(schedule_config),
                    freshness=self._connector_freshness_summary_from_snapshot(
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
        self,
        entries: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if entries is None:
            entries = self._source_connector_entries()
        connector_policies = [dict(entry["crawler_policy"]) for entry in entries]
        return policy_registry_payload(
            connector_policies,
            max_records_per_job=self.MAX_RECORDS_PER_JOB,
            scheduler_max_concurrency=self.SCHEDULER_MAX_CONCURRENCY,
            frontier_max_attempts=self.FRONTIER_MAX_ATTEMPTS,
            search_ingest_notify_url=self.SEARCH_INGEST_NOTIFY_URL,
            posture=self.PRODUCTION_POSTURE.to_dict(),
        )

    def _provider_example_payloads(self) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for provider in example_provider_catalog():
            connector = provider.connector()
            payloads.append(
                {
                    "connector": connector.to_dict(),
                    "fetch_policy": self._fetch_policy_summary(dict(provider.fetch_config())),
                }
            )
        return payloads

    def _source_provisioning_reconciler(self) -> SourceProvisioningReconciler:
        if getattr(self, "_module", None) is not None:
            fn = self._module.__dict__.get("_source_provisioning_reconciler")
            if callable(fn) and getattr(fn, "__code__", None) is not None and fn.__code__ is not self._default_source_provisioning_reconciler.__code__:
                return fn()
        import sys
        main_mod = sys.modules.get("services.source_ingestion.main")
        if main_mod is not None:
            fn = main_mod.__dict__.get("_source_provisioning_reconciler")
            if callable(fn) and getattr(fn, "__code__", None) is not None and fn.__code__ is not self._default_source_provisioning_reconciler.__code__:
                return fn()
        return self._default_source_provisioning_reconciler()

    def _default_source_provisioning_reconciler(self) -> SourceProvisioningReconciler:
        return SourceProvisioningReconciler(
            manager=self.manager,
            connector_store=self.connector_store,
            schedule_store=self.schedule_config_store,
        )

    def _desired_state_digest(self, personas: list[dict[str, Any]]) -> str:
        body = json.dumps(
            personas,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(body).hexdigest()

    def _requirement_bindings(self, results: list[Any]) -> dict[str, str]:
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

    def _provisioning_summary(self, results: list[Any]) -> dict[str, int]:
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
        self,
        *,
        current_bindings: Mapping[str, str],
    ) -> list[dict[str, Any]]:
        retained_connector_ids = set(current_bindings.values())
        removed_connector_ids = sorted(
            config.connector.connector_id
            for config in self.connector_store.list_configs()
            if (
                config.connector.connector_id not in retained_connector_ids
                and isinstance(config.connector.metadata.get(RECONCILIATION_METADATA_KEY), Mapping)
                and config.connector.metadata[RECONCILIATION_METADATA_KEY].get("managed_by")
                == "persona_source_provisioning_reconciler"
            )
        )
        actions: list[dict[str, Any]] = []
        for connector_id in removed_connector_ids:
            config = self.connector_store.get_config(connector_id)
            if config is None:
                actions.append({"connector_id": connector_id, "action": "already_absent"})
                continue
            marker = config.connector.metadata.get(RECONCILIATION_METADATA_KEY)
            owner = str(marker.get("managed_by") or "") if isinstance(marker, Mapping) else ""
            if owner != "persona_source_provisioning_reconciler":
                actions.append({"connector_id": connector_id, "action": "retained_operator_owned"})
                continue
            schedule = self.schedule_config_store.get_schedule(connector_id)
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
            self.connector_store.upsert_config(retired_connector, config.fetch)
            self.manager.upsert_connector(retired_connector)
            if schedule is not None and schedule.enabled:
                self.schedule_config_store.upsert_schedule(
                    connector_id,
                    interval_seconds=schedule.interval_seconds,
                    enabled=False,
                )
            actions.append({"connector_id": connector_id, "action": "disabled_controller_owned"})
        return actions

    def _source_record_readback(self, record: SourceRecord | None) -> dict[str, Any] | None:
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

    def _latest_source_record_by_connector(self) -> dict[str, SourceRecord]:
        latest: dict[str, SourceRecord] = {}
        for record in self.evidence_repository.list_source_records():
            current = latest.get(record.connector_id)
            if current is None or str(record.to_dict()["created_at"]) > str(current.to_dict()["created_at"]):
                latest[record.connector_id] = record
        return latest

    def _controller_connector_readbacks(self) -> list[dict[str, Any]]:
        configs, fetch_states = self.connector_store.read_snapshot()
        schedules = {
            schedule.connector_id: schedule
            for schedule in self.schedule_config_store.list_schedules()
        }
        snapshot = self.store.read_freshness_snapshot()
        runs_by_connector: dict[str, list[Any]] = {}
        for run in snapshot["runs"]:
            connector_id = str(getattr(run, "connector_id", ""))
            runs_by_connector.setdefault(connector_id, []).append(run)
        receipts_by_connector: dict[str, list[IngestReceipt]] = {}
        for receipt in snapshot["receipts"]:
            receipts_by_connector.setdefault(receipt.connector_id, []).append(receipt)
        observed_at = datetime.now(timezone.utc)
        latest_by_connector = self._latest_source_record_by_connector()
        readbacks: list[dict[str, Any]] = []
        for config in sorted(configs, key=lambda item: item.connector.connector_id):
            connector = config.connector
            connector_id = connector.connector_id
            schedule = schedules.get(connector_id)
            freshness = self._connector_freshness_summary_from_snapshot(
                connector_id,
                connector_metadata=connector.metadata,
                schedule=schedule,
                watermark=snapshot["watermarks"].get(connector_id),
                runs=runs_by_connector.get(connector_id, ()),
                receipts=receipts_by_connector.get(connector_id, ()),
                now=observed_at,
            )
            health = self.source_health_store.get(connector_id)
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
                    "latest_source_record": self._source_record_readback(latest_by_connector.get(connector_id)),
                    "source_health": health_payload,
                }
            )
        return readbacks

    def _controller_readback_payload(self) -> dict[str, Any]:
        if getattr(self, "_module", None) is not None:
            fn = self._module.__dict__.get("_controller_readback_payload")
            if callable(fn) and getattr(fn, "__code__", None) is not None and fn.__code__ is not self._default_controller_readback_payload.__code__:
                return fn()
        import sys
        main_mod = sys.modules.get("services.source_ingestion.main")
        if main_mod is not None:
            fn = main_mod.__dict__.get("_controller_readback_payload")
            if callable(fn) and getattr(fn, "__code__", None) is not None and fn.__code__ is not self._default_controller_readback_payload.__code__:
                return fn()
        return self._default_controller_readback_payload()

    def _default_controller_readback_payload(self) -> dict[str, Any]:
        connector_readbacks = self._controller_connector_readbacks()
        frontier = self.store.list_frontier()
        frontier_backlog, frontier_backlog_by_connector = _frontier_backlog_readback(frontier)
        staleness_values = [
            int(item["freshness"]["staleness_seconds"])
            for item in connector_readbacks
            if item.get("freshness", {}).get("staleness_seconds") is not None
        ]
        controller_state = read_controller_state(self.CONTROLLER_STATE_PATH)
        requirement_snapshot = self.requirement_snapshot_store.latest
        dlq_entries = self.dead_letter_queue.entries()
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
            "controller_state_path": str(self.CONTROLLER_STATE_PATH),
            "requirement_snapshot": requirement_snapshot.to_dict() if requirement_snapshot is not None else None,
            "connector_count": len(connector_readbacks),
            "source_record_count": len(self.evidence_repository.list_source_records()),
            "dlq_count": len(dlq_entries),
            "pending_dlq_count": dlq_status_counts[DeadLetterStatus.PENDING.value],
            "unresolved_dlq_count": unresolved_dlq_count,
            "dlq_status_counts": dlq_status_counts,
            "frontier_backlog": frontier_backlog,
            "frontier_backlog_by_connector": dict(sorted(frontier_backlog_by_connector.items())),
            "max_lag_seconds": max(staleness_values, default=0),
            "connectors": connector_readbacks,
        }

    def _persona_source_provisioning_payload(self, request: PersonaSourceProvisioningRequest) -> dict[str, Any]:
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
        desired_state_sha256 = self._desired_state_digest(personas)
        if request.desired_state_sha256 and request.desired_state_sha256 != desired_state_sha256:
            raise SourceEvidenceError("desired_state_sha256 does not match canonical persona snapshot")
        pre_readback = self._controller_readback_payload()
        reconciler = self._source_provisioning_reconciler()
        retirement_actions: list[dict[str, Any]] = []
        accepted_snapshot = None
        if request.authoritative_snapshot and not request.dry_run:
            planned_results = list(reconciler.reconcile_personas(personas, dry_run=True))
            planned_summary = self._provisioning_summary(planned_results)
            planned_bindings = self._requirement_bindings(planned_results)
            if planned_summary["conflicts"] or planned_summary["unsupported"]:
                results = planned_results
                summary = planned_summary
                bindings = planned_bindings
            else:
                accepted_snapshot = self.requirement_snapshot_store.append(
                    desired_state_sha256=desired_state_sha256,
                    bindings=planned_bindings,
                    persona_count=len(personas),
                    authority=str(request.source_authority or "api://persona-source-provisioning"),
                    authoritative=True,
                )
                results = list(reconciler.reconcile_personas(personas, dry_run=False))
                summary = self._provisioning_summary(results)
                bindings = self._requirement_bindings(results)
                if summary["conflicts"] or summary["unsupported"] or bindings != planned_bindings:
                    raise SourceEvidenceError(
                        "actual source convergence contradicted the admitted authoritative requirement snapshot"
                    )
                retirement_actions = self._retire_removed_requirement_bindings(
                    current_bindings=bindings,
                )
        else:
            results = list(reconciler.reconcile_personas(personas, dry_run=request.dry_run))
            summary = self._provisioning_summary(results)
            bindings = self._requirement_bindings(results)
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
            "post_readback": self._controller_readback_payload(),
        }

    def _connector_for_job(self, request: TriggerIngestJobRequest) -> SourceConnector:
        if request.connector is not None:
            connector = self._register_or_validate_connector(request.connector.to_domain())
            if request.connector_id and request.connector_id != connector.connector_id:
                raise SourceEvidenceError("connector_id must match connector.connector_id")
            if request.fetch is not None:
                self._assert_fetch_within_limit(request.fetch)
                self.connector_store.upsert_config(connector, request.fetch.to_config())
            return connector

        connector_id = str(request.connector_id or "").strip()
        if not connector_id:
            raise SourceEvidenceError("connector or connector_id is required")
        config = self.connector_store.get_config(connector_id)
        if config is None:
            raise SourceEvidenceError(f"Connector fetch is not configured: {connector_id}")
        return self._register_or_validate_connector(config.connector)

    def _assert_connector_lifecycle_allows_run(self, connector: SourceConnector) -> None:
        if connector.status == ConnectorStatus.DISABLED:
            raise SourceEvidenceError(f"Connector lifecycle status disabled rejects ingest runs: {connector.connector_id}")

    def _append_audit_actions(self, actions: tuple[Any, ...]) -> None:
        if getattr(self, "_module", None) is not None:
            fn = self._module.__dict__.get("_append_audit_actions")
            if callable(fn) and getattr(fn, "__code__", None) is not None and fn.__code__ is not self._default_append_audit_actions.__code__:
                return fn(actions)
        import sys
        main_mod = sys.modules.get("services.source_ingestion.main")
        if main_mod is not None:
            fn = main_mod.__dict__.get("_append_audit_actions")
            if callable(fn) and getattr(fn, "__code__", None) is not None and fn.__code__ is not self._default_append_audit_actions.__code__:
                return fn(actions)
        return self._default_append_audit_actions(actions)

    def _default_append_audit_actions(self, actions: tuple[Any, ...]) -> None:
        if not actions:
            return
        payloads = [action.to_dict() for action in actions]
        with self.audit_store_lock:
            self.AUDIT_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            file_preexisted = self.AUDIT_STORE_PATH.exists()
            wrote = False
            with self.AUDIT_STORE_PATH.open("a+", encoding="utf-8") as handle:
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
                directory_fd = os.open(self.AUDIT_STORE_PATH.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)

    def _load_audit_actions(self) -> list[dict[str, Any]]:
        with self.audit_store_lock:
            if not self.AUDIT_STORE_PATH.exists():
                return []
            actions: list[dict[str, Any]] = []
            with self.AUDIT_STORE_PATH.open("r", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
                try:
                    for line in handle:
                        if line.strip():
                            actions.append(json.loads(line))
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return actions

    def _record_connector_lifecycle_audit(
        self,
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
            environment=self.scheduler.environment,
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
            environment=self.scheduler.environment,
            reason=reason,
            trace=trace,
            payload=payload,
            before_state_ref=f"source_connector:{connector_id}:status:{previous_status}",
            after_state_ref=f"source_connector:{connector_id}:status:{next_status}",
            metadata={"connector_id": connector_id, "previous_status": previous_status, "next_status": next_status},
        )
        self._append_audit_actions((action,))
        return action.to_dict()

    def _set_connector_lifecycle(self, connector_id: str, request: SetConnectorLifecycleRequest) -> dict[str, Any]:
        config = self.connector_store.get_config(connector_id)
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
        self.manager.upsert_connector(updated_connector)
        stored = self.connector_store.upsert_config(updated_connector, config.fetch)
        audit = self._record_connector_lifecycle_audit(
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
            "state": self.connector_store.get_fetch_state(connector_id),
            "lifecycle": metadata["lifecycle"],
            "audit_action": audit,
        }

    def _proposal_to_response(self, proposal: SourceChangeProposal) -> dict[str, Any]:
        return proposal.to_dict()

    def run_scheduled_connectors(
        self,
        request: RunScheduledRequest | None = None,
        authorization: str | None = None,
    ) -> dict[str, Any]:
        with self.source_execution_lock:
            if any(self._is_controller_owned(config.connector) for config in self.connector_store.list_configs()):
                self._require_controller_authorization(
                    authorization,
                    operation="controller-owned scheduled source execution",
                )
            return self.pipeline.run_scheduled_connectors(request)

    def reconcile_persona_source_provisioning(
        self,
        request: PersonaSourceProvisioningRequest,
        authorization: str | None = None,
    ) -> dict[str, Any]:
        if not request.dry_run:
            self._require_controller_authorization(
                authorization,
                operation="source reconciliation mutation",
            )
            with exclusive_file_lock(
                self.RECONCILE_TRANSACTION_LOCK_PATH,
                self.authoritative_reconcile_lock,
            ):
                self.requirement_snapshot_store.reload()
                self.connector_store.reload()
                self.schedule_config_store.reload()
                return self._persona_source_provisioning_payload(request)
        return self._persona_source_provisioning_payload(request)


def create_runtime(data_dir: Path | None = None, module: Any = None) -> SourceIngestionRuntime:
    return SourceIngestionRuntime(data_dir=data_dir, module=module)
