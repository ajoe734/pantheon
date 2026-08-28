"""Deployable source-ingest service composition root and entrypoint.

Assembles the Source Ingestion runtime, registers health and version probes,
and mounts the five route family routers.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from services.foundation import (
    ActorRef,
    ActorType,
    AuditAction,
    DeadLetterQueue,
    DeadLetterReplayProcessor,
)
from services.foundation.health import register_fastapi_health_routes
from services.knowledge.evidence import EvidenceBundleBuilder

from .api_models import (
    ActiveUniverseMemberBody,
    ActiveUniversePlanRequest,
    ActiveUniverseScheduleRequest,
    ApplyProposalRequest,
    ConfigureConnectorRequest,
    ConfiguredFetchBody,
    ConfiguredFetchRecordBody,
    ConnectorBody,
    CreateProposalRequest,
    GapReportRequest,
    LLMProposalRequest,
    PersonaSourceProvisioningRequest,
    ProposalRiskBody,
    ProposedSourceBody,
    ReplayDlqRequest,
    ReplayFrontierRequest,
    RunScheduledRequest,
    SetConnectorLifecycleRequest,
    SetScheduleRequest,
    SourceCommandActorBody,
    SourceCommandRequestBody,
    SourceRecordBody,
    SourceRecordIngestRequest,
    SourceUpdateRuleBody,
    StrictBaseModel,
    TriggerIngestJobRequest,
    UpsertHealthRequest,
    UpsertUsageRequest,
)
from .connectors import (
    AuthType,
    ConnectorMode,
    ConnectorStatus,
    SourceConnector,
    SourceRecord,
    SourceRecordStatus,
    SourceType,
)
from .configured import (
    ConfiguredConnectorFetcher,
    JsonlConfiguredConnectorStore,
    JsonlConnectorScheduleStore,
)
from .controller_auth import load_controller_token
from .distillation_worker import DistillationJobQueue
from .ingest_manager import IngestManager
from .market_data_storage import MarketDataStorageWriter
from .pg_store import build_source_evidence_repository
from .pipeline import (
    compact_bulk_market_record,
    notify_search_index_refresh,
)
from .registry.llm_proposal_adapter import LLMSourceProposalAdapter
from .registry.proposals import SourceChangeProposalStore
from .requirement_state import (
    LatestMarketSnapshotStore,
    RequirementSnapshotStore,
)
from .routers import (
    create_catalog_controller_router,
    create_ingest_operations_router,
    create_management_router,
    create_observability_router,
    create_proposals_router,
)
from .runtime import SourceIngestionRuntime, create_runtime
from .scheduler import IngestionScheduler, JsonlIngestScheduleStore
from .source_health import (
    SourceHealthStore,
    SourceUsageDailyStore,
)


import sys


def create_app(runtime: SourceIngestionRuntime | None = None) -> FastAPI:
    """Create and compose the FastAPI application instance with all routers."""
    if runtime is None:
        runtime = create_runtime(module=sys.modules.get(__name__))

    app = FastAPI(title="Pantheon Source Ingest Service", version="0.1.0")

    register_fastapi_health_routes(
        app,
        "pantheon-source-ingest",
        dependencies=lambda: {
            "source_search_posture": runtime.PRODUCTION_POSTURE.to_dict(),
            "source_freshness": runtime._source_freshness_readiness(),
        },
        metrics=lambda: runtime._source_runtime_metrics(),
        details=lambda: {
            "store_path": str(runtime.SCHEDULE_STORE_PATH),
            "connector_store_path": str(runtime.CONNECTOR_STORE_PATH),
            "source_evidence_path": str(runtime.SOURCE_EVIDENCE_STORE_PATH),
            "market_data_storage_root": str(runtime.MARKET_DATA_STORAGE_ROOT),
            "latest_market_snapshot_path": str(runtime.LATEST_MARKET_SNAPSHOT_PATH),
            "market_snapshot_max_closes": runtime.MARKET_SNAPSHOT_MAX_CLOSES,
            "dlq_path": str(runtime.DLQ_STORE_PATH),
            "audit_path": str(runtime.AUDIT_STORE_PATH),
            "scheduler_max_concurrency": runtime.SCHEDULER_MAX_CONCURRENCY,
            "frontier_max_attempts": runtime.FRONTIER_MAX_ATTEMPTS,
            "frontier_backoff_seconds": runtime.FRONTIER_BACKOFF_SECONDS,
            "source_search_posture": runtime.PRODUCTION_POSTURE.to_dict(),
        },
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        runtime_metrics = runtime._source_runtime_metrics()
        return {
            "status": "ok",
            "service": "pantheon-source-ingest",
            "store_path": str(runtime.SCHEDULE_STORE_PATH),
            "connector_store_path": str(runtime.CONNECTOR_STORE_PATH),
            "source_evidence_path": str(runtime.SOURCE_EVIDENCE_STORE_PATH),
            "market_data_storage_root": str(runtime.MARKET_DATA_STORAGE_ROOT),
            "latest_market_snapshot_path": str(runtime.LATEST_MARKET_SNAPSHOT_PATH),
            "dlq_path": str(runtime.DLQ_STORE_PATH),
            "audit_path": str(runtime.AUDIT_STORE_PATH),
            **runtime_metrics,
            "scheduler_max_concurrency": runtime.SCHEDULER_MAX_CONCURRENCY,
            "frontier_max_attempts": runtime.FRONTIER_MAX_ATTEMPTS,
            "frontier_backoff_seconds": runtime.FRONTIER_BACKOFF_SECONDS,
            "source_search_posture": runtime.PRODUCTION_POSTURE.to_dict(),
            "posture_alert_count": runtime.PRODUCTION_POSTURE.alert_count(),
        }

    app.include_router(create_ingest_operations_router(runtime))
    app.include_router(create_catalog_controller_router(runtime))
    app.include_router(create_proposals_router(runtime))
    app.include_router(create_observability_router(runtime))
    app.include_router(create_management_router(runtime))

    return app


# Default singleton runtime and application composition
runtime = create_runtime(module=sys.modules.get(__name__))
app = create_app(runtime)

# ---------------------------------------------------------------------------
# Module-level aliases and re-exports for test and backward compatibility
# ---------------------------------------------------------------------------
DATA_DIR = runtime.DATA_DIR
PROPOSAL_STORE_PATH = runtime.PROPOSAL_STORE_PATH
SCHEDULE_STORE_PATH = runtime.SCHEDULE_STORE_PATH
CONNECTOR_STORE_PATH = runtime.CONNECTOR_STORE_PATH
SOURCE_EVIDENCE_STORE_PATH = runtime.SOURCE_EVIDENCE_STORE_PATH
DLQ_STORE_PATH = runtime.DLQ_STORE_PATH
AUDIT_STORE_PATH = runtime.AUDIT_STORE_PATH
CONNECTOR_SCHEDULE_CONFIG_PATH = runtime.CONNECTOR_SCHEDULE_CONFIG_PATH
SOURCE_HEALTH_STORE_PATH = runtime.SOURCE_HEALTH_STORE_PATH
SOURCE_USAGE_STORE_PATH = runtime.SOURCE_USAGE_STORE_PATH
MARKET_DATA_STORAGE_ROOT = runtime.MARKET_DATA_STORAGE_ROOT
CONTROLLER_STATE_PATH = runtime.CONTROLLER_STATE_PATH
REQUIREMENT_STATE_PATH = runtime.REQUIREMENT_STATE_PATH
LATEST_MARKET_SNAPSHOT_PATH = runtime.LATEST_MARKET_SNAPSHOT_PATH
RECONCILE_TRANSACTION_LOCK_PATH = runtime.RECONCILE_TRANSACTION_LOCK_PATH
CONTROLLER_TOKEN_PATH = runtime.CONTROLLER_TOKEN_PATH
DISTILLATION_JOB_QUEUE_PATH = runtime.DISTILLATION_JOB_QUEUE_PATH
SOURCE_RECORD_SCHEMA_PATH = runtime.SOURCE_RECORD_SCHEMA_PATH
MAX_RECORDS_PER_JOB = runtime.MAX_RECORDS_PER_JOB
SCHEDULER_MAX_CONCURRENCY = runtime.SCHEDULER_MAX_CONCURRENCY
FRONTIER_MAX_ATTEMPTS = runtime.FRONTIER_MAX_ATTEMPTS
FRONTIER_BACKOFF_SECONDS = runtime.FRONTIER_BACKOFF_SECONDS
FRONTIER_RUNNING_TIMEOUT_SECONDS = runtime.FRONTIER_RUNNING_TIMEOUT_SECONDS
DEFAULT_STALE_THRESHOLD_SECONDS = runtime.DEFAULT_STALE_THRESHOLD_SECONDS
MARKET_SNAPSHOT_MAX_CLOSES = runtime.MARKET_SNAPSHOT_MAX_CLOSES
SOURCE_TIMESTAMP_FUTURE_TOLERANCE_SECONDS = runtime.SOURCE_TIMESTAMP_FUTURE_TOLERANCE_SECONDS
SEARCH_INGEST_NOTIFY_URL = runtime.SEARCH_INGEST_NOTIFY_URL
PRODUCTION_POSTURE = runtime.PRODUCTION_POSTURE

manager = runtime.manager
proposal_store = runtime.proposal_store
llm_proposal_adapter = runtime.llm_proposal_adapter
store = runtime.store
connector_store = runtime.connector_store
schedule_config_store = runtime.schedule_config_store
configured_fetcher = runtime.configured_fetcher
evidence_repository = runtime.evidence_repository
evidence_builder = runtime.evidence_builder
dead_letter_queue = runtime.dead_letter_queue
scheduler = runtime.scheduler
replay_processor = runtime.replay_processor
source_health_store = runtime.source_health_store
source_usage_store = runtime.source_usage_store
requirement_snapshot_store = runtime.requirement_snapshot_store
latest_market_snapshot_store = runtime.latest_market_snapshot_store
controller_token = runtime.controller_token
authoritative_reconcile_lock = runtime.authoritative_reconcile_lock
audit_store_lock = runtime.audit_store_lock
source_execution_lock = runtime.source_execution_lock
market_data_storage_writer = runtime.market_data_storage_writer
distillation_job_queue = runtime.distillation_job_queue
source_management_store = runtime.source_management_store
source_command_engine = runtime.source_command_engine


def _controller_readback_payload() -> dict[str, Any]:
    return runtime._default_controller_readback_payload()


def _source_freshness_readiness() -> dict[str, Any]:
    return runtime._default_source_freshness_readiness()


def _controller_connector_readbacks() -> list[dict[str, Any]]:
    return runtime._controller_connector_readbacks()


def _source_connector_entries() -> list[dict[str, Any]]:
    return runtime._source_connector_entries()


def _source_policy_registry_payload(entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return runtime._source_policy_registry_payload(entries)


def _compact_bulk_market_record(record: SourceRecord, storage_refs: dict[str, Any] | None) -> SourceRecord:
    return compact_bulk_market_record(record, storage_refs)


def _source_provisioning_reconciler() -> Any:
    return runtime._default_source_provisioning_reconciler()


def _desired_state_digest(personas: list[dict[str, Any]]) -> str:
    return runtime._desired_state_digest(personas)


def _append_audit_actions(actions: tuple[Any, ...]) -> None:
    runtime._default_append_audit_actions(actions)


def _notify_search_index_refresh(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return notify_search_index_refresh(*args, **kwargs)


def run_scheduled_connectors(
    request: RunScheduledRequest | None = None,
    authorization: str | None = None,
) -> dict[str, Any]:
    return runtime.run_scheduled_connectors(request, authorization=authorization)


def _run_scheduled_connectors(
    request: RunScheduledRequest | None = None,
    authorization: str | None = None,
) -> dict[str, Any]:
    return run_scheduled_connectors(request, authorization=authorization)


def reconcile_persona_source_provisioning(
    request: PersonaSourceProvisioningRequest,
    authorization: str | None = None,
) -> dict[str, Any]:
    return runtime.reconcile_persona_source_provisioning(request, authorization=authorization)


__all__ = [
    "app",
    "create_app",
    "runtime",
    "create_runtime",
    "SourceIngestionRuntime",
    "SourceChangeProposalStore",
    "LLMSourceProposalAdapter",
    "JsonlIngestScheduleStore",
    "JsonlConfiguredConnectorStore",
    "JsonlConnectorScheduleStore",
    "ConfiguredConnectorFetcher",
    "build_source_evidence_repository",
    "EvidenceBundleBuilder",
    "DeadLetterQueue",
    "DeadLetterReplayProcessor",
    "IngestManager",
    "IngestionScheduler",
    "SourceHealthStore",
    "SourceUsageDailyStore",
    "RequirementSnapshotStore",
    "LatestMarketSnapshotStore",
    "load_controller_token",
    "MarketDataStorageWriter",
    "DistillationJobQueue",
    "SourceConnector",
    "SourceRecord",
    "SourceRecordStatus",
    "ConnectorStatus",
    "SourceType",
    "StrictBaseModel",
    "ConnectorBody",
    "SourceRecordBody",
    "ConfiguredFetchRecordBody",
    "ConfiguredFetchBody",
    "ConfigureConnectorRequest",
    "TriggerIngestJobRequest",
    "SourceRecordIngestRequest",
    "ReplayDlqRequest",
    "SetScheduleRequest",
    "ActiveUniverseMemberBody",
    "SourceUpdateRuleBody",
    "ActiveUniversePlanRequest",
    "ActiveUniverseScheduleRequest",
    "SetConnectorLifecycleRequest",
    "RunScheduledRequest",
    "PersonaSourceProvisioningRequest",
    "ReplayFrontierRequest",
    "ProposedSourceBody",
    "ProposalRiskBody",
    "CreateProposalRequest",
    "LLMProposalRequest",
    "ApplyProposalRequest",
    "UpsertHealthRequest",
    "UpsertUsageRequest",
    "GapReportRequest",
    "SourceCommandActorBody",
    "SourceCommandRequestBody",
    "reconcile_persona_source_provisioning",
    "run_scheduled_connectors",
    "_run_scheduled_connectors",
]
