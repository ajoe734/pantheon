"""Runtime module and composition factory for Source Ingestion.

Constructs existing managers, stores, writers, and engines once and provides
narrow operations and dependencies for routers and entrypoints.
"""

from __future__ import annotations

import atexit
import fcntl
import hmac
import json
import os
import re
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping
from .tw_close_reconcile import COLLECTOR_PRICE_HISTORY_SOURCE_IDS

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
from .chip_publication_schedule import (
    FINMIND_FUNDAMENTAL_PUBLICATION_DATASETS,
    finmind_fundamental_publication_fresh,
)
from .configured import ConfiguredConnector, ConfiguredConnectorFetcher, JsonlConfiguredConnectorStore, JsonlConnectorScheduleStore
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
from .pin_source import PinSource
from .security_listing import HOSTED_STATE_FILENAME, HostedListing, set_hosted_listing
from .policy_registry import crawler_policy_for_connector, policy_registry_payload
from .controller_state import ControllerStateError, ControllerStateStore, read_controller_state
from .controller_auth import load_controller_token
from .persona_source_reconciler import (
    RECONCILIATION_METADATA_KEY,
    UNDEFINED_BY_RELEASE_ACTIONS,
    SourceProvisioningReconciler,
)
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
from .pipeline import IngestDistillationJobQueue, IngestPipelineService
from .snapshot_universe import SnapshotUniverse
from .strategy_seed_store import StrategySpecSeedStore


def _ref_count(refs: Mapping[str, Any], listed_key: str, count_key: str) -> int:
    """Count from the receipt's count field (refs-only form) or its embedded list (older rows)."""
    value = refs.get(listed_key)
    if value:
        return len(value)
    return int(refs.get(count_key) or 0)


def _receipt_summary(receipt: IngestReceipt) -> dict[str, Any]:
    """Fixed-field read projection; full evidence stays on the receipt endpoint.

    Read attributes directly so projecting freshness never serializes or copies
    snapshot batches, admission maps, or storage manifests from the receipt.
    """
    refs = receipt.evidence_refs
    snapshots = refs.get("market_snapshots")
    snapshots = snapshots if isinstance(snapshots, Mapping) else {}
    return {
        "schema_version": "source_ingest_receipt_summary.v1",
        "ingest_run_id": receipt.ingest_run_id,
        "connector_id": receipt.connector_id,
        "status": receipt.status,
        "trigger_type": receipt.trigger_type,
        "trace_id": receipt.trace_id,
        "started_at": receipt.started_at,
        "finished_at": receipt.finished_at,
        "created_at": receipt.created_at,
        "source_timestamp": receipt.source_timestamp,
        "source_timestamp_status": receipt.source_timestamp_status,
        "raw_count": receipt.raw_count,
        "normalized_count": receipt.normalized_count,
        "rejected_count": receipt.rejected_count,
        "watermark": receipt.watermark,
        "evidence_refs": {
            "evidence_bundle_id": refs.get("evidence_bundle_id"),
            "source_id_count": _ref_count(refs, "source_ids", "source_id_count"),
            "evidence_item_id_count": _ref_count(refs, "evidence_item_ids", "evidence_item_id_count"),
            "knowledge_object_id_count": _ref_count(refs, "knowledge_object_ids", "knowledge_object_id_count"),
            "distillation_admission_count": _ref_count(
                refs, "distillation_admissions", "distillation_admission_count"),
            "market_snapshots": {
                "accepted_record_count": snapshots.get("accepted_record_count", 0),
                "updated_snapshot_count": snapshots.get("updated_snapshot_count", 0),
                "snapshot_count": _ref_count(snapshots, "snapshots", "snapshot_count"),
            },
        },
    }


def _ordered_run_receipts(receipts: Sequence[IngestReceipt], runs: Sequence[Any]) -> list[IngestReceipt]:
    """Resolve second-resolution timestamp ties by durable run insertion order."""
    positions = {run.ingest_run_id: index for index, run in enumerate(runs)}
    return sorted(receipts, key=lambda receipt: (
        receipt.finished_at or receipt.started_at,
        positions.get(receipt.ingest_run_id, -1),
        receipt.ingest_run_id,
    ))


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


def _active_contract_symbols() -> set[str]:
    raw = os.getenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "")
    symbols: set[str] = set()
    for item in raw.split(","):
        clean = item.strip().upper()
        if not clean:
            continue
        symbols.add(clean)
        if "." in clean:
            base, _ = clean.rsplit(".", 1)
            symbols.add(base)
            try:
                from .connectors.taiwan_official import canonical_taiwan_equity_symbol

                symbols.add(canonical_taiwan_equity_symbol(clean))
            except Exception:
                pass
    return symbols


class SourceIngestionRuntime:
    """Explicit runtime holding stores, managers, locks, and configuration."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.DATA_DIR = data_dir or _resolve_data_dir()
        self.PROPOSAL_STORE_PATH = Path(
            os.getenv("SOURCE_INGEST_PROPOSAL_STORE_PATH", str(self.DATA_DIR / "source_change_proposals.jsonl"))
        )
        self.SCHEDULE_STORE_PATH = Path(
            os.getenv("SOURCE_INGEST_STORE_PATH", str(self.DATA_DIR / "ingest_schedule.jsonl"))
        )
        self.CONNECTOR_STORE_PATH = Path(
            os.getenv("SOURCE_INGEST_CONNECTOR_STORE_PATH", str(self.DATA_DIR / "connector_config.jsonl"))
        )
        self.SOURCE_EVIDENCE_STORE_PATH = Path(
            os.getenv("SOURCE_INGEST_EVIDENCE_STORE_PATH", str(self.DATA_DIR / "source_evidence.jsonl"))
        )
        self.DLQ_STORE_PATH = Path(
            os.getenv("SOURCE_INGEST_DLQ_PATH", str(self.DATA_DIR / "source_ingest_dlq.jsonl"))
        )
        self.AUDIT_STORE_PATH = Path(
            os.getenv("SOURCE_INGEST_AUDIT_PATH", str(self.DATA_DIR / "source_ingest_audit.jsonl"))
        )
        self.CONNECTOR_SCHEDULE_CONFIG_PATH = Path(
            os.getenv("SOURCE_INGEST_SCHEDULE_CONFIG_PATH", str(self.DATA_DIR / "connector_schedule.jsonl"))
        )
        self.SOURCE_HEALTH_STORE_PATH = Path(
            os.getenv("SOURCE_INGEST_HEALTH_STORE_PATH", str(self.DATA_DIR / "source_health.jsonl"))
        )
        self.SOURCE_USAGE_STORE_PATH = Path(
            os.getenv("SOURCE_INGEST_USAGE_STORE_PATH", str(self.DATA_DIR / "source_usage_daily.jsonl"))
        )
        self.MARKET_DATA_STORAGE_ROOT = Path(
            os.getenv("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT", str(self.DATA_DIR / "market_data_store"))
        )
        self.CONTROLLER_STATE_PATH = Path(
            os.getenv("SOURCE_INGEST_CONTROLLER_STATE_PATH", str(self.DATA_DIR / "controller_state.json"))
        )
        self.REQUIREMENT_STATE_PATH = Path(
            os.getenv("SOURCE_INGEST_REQUIREMENT_STATE_PATH", str(self.DATA_DIR / "requirement_snapshots.jsonl"))
        )
        self.LATEST_MARKET_SNAPSHOT_PATH = Path(
            os.getenv(
                "SOURCE_INGEST_LATEST_MARKET_SNAPSHOT_PATH",
                str(self.DATA_DIR / "latest_market_snapshots.jsonl"),
            )
        )
        self.RECONCILE_TRANSACTION_LOCK_PATH = Path(
            os.getenv(
                "SOURCE_INGEST_RECONCILE_TRANSACTION_LOCK_PATH",
                str(self.DATA_DIR / "persona_source_reconcile.lock"),
            )
        )
        self.CONTROLLER_TOKEN_PATH = Path(
            os.getenv("SOURCE_INGEST_CONTROLLER_TOKEN_FILE", str(self.DATA_DIR / "controller_token"))
        )
        self.DISTILLATION_JOB_QUEUE_PATH = Path(
            os.getenv("DISTILLATION_JOB_QUEUE_PATH", str(self.DATA_DIR / "distillation_job_queue.sqlite3"))
        )
        self.SOURCE_RECORD_SCHEMA_PATH = Path(__file__).with_name("source_record.schema.json")
        self.MAX_RECORDS_PER_JOB = int(os.getenv("SOURCE_INGEST_MAX_RECORDS", "100"))
        self.SCHEDULER_MAX_CONCURRENCY = max(1, int(os.getenv("SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY", "2")))
        self.FRONTIER_MAX_ATTEMPTS = max(1, int(os.getenv("SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS", "2")))
        self.FRONTIER_BACKOFF_SECONDS = max(0, int(os.getenv("SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS", "60")))
        self.FRONTIER_RUNNING_TIMEOUT_SECONDS = max(
            1,
            int(os.getenv("SOURCE_INGEST_FRONTIER_RUNNING_TIMEOUT_SECONDS", "300")),
        )
        self.DEFAULT_STALE_THRESHOLD_SECONDS = max(
            1,
            int(os.getenv("SOURCE_INGEST_DEFAULT_STALE_THRESHOLD_SECONDS", "86400")),
        )
        self.MARKET_SNAPSHOT_MAX_CLOSES = max(
            2,
            int(os.getenv("SOURCE_INGEST_MARKET_SNAPSHOT_MAX_CLOSES", "60")),
        )
        self.SOURCE_TIMESTAMP_FUTURE_TOLERANCE_SECONDS = max(
            0,
            int(os.getenv("SOURCE_INGEST_FUTURE_TIMESTAMP_TOLERANCE_SECONDS", "300")),
        )
        self.SEARCH_INGEST_NOTIFY_URL = os.getenv("SEARCH_INGEST_NOTIFY_URL", "").rstrip("/")
        self.PRODUCTION_POSTURE = require_source_search_posture("source-ingest")
        self._SOURCE_READINESS_MAX_STATE_BYTES = max(
            1,
            int(os.getenv("SOURCE_INGEST_READINESS_MAX_STATE_BYTES", str(1024 * 1024))),
        )
        self._SOURCE_READINESS_MAX_CONTROLLER_AGE_SECONDS = max(
            1,
            int(os.getenv("SOURCE_INGEST_READINESS_MAX_CONTROLLER_AGE_SECONDS", "300")),
        )

        self.manager = IngestManager()
        self.proposal_store = SourceChangeProposalStore.from_jsonl(self.PROPOSAL_STORE_PATH)
        self.llm_proposal_adapter = LLMSourceProposalAdapter(self.proposal_store)
        # Receipts that a gate proof or paper evidence references are kept past retention; when the evidence
        # cannot be read (or SOURCE_INGEST_PIN_EVIDENCE_PATHS is unset) retention drops nothing and health says why.
        self.pin_source = PinSource.from_env(run_files_root=self.MARKET_DATA_STORAGE_ROOT)
        self.store = JsonlIngestScheduleStore(self.SCHEDULE_STORE_PATH, pinned_run_ids=self.pin_source.run_ids)
        self.connector_store = JsonlConfiguredConnectorStore(self.CONNECTOR_STORE_PATH)
        self.schedule_config_store = JsonlConnectorScheduleStore(self.CONNECTOR_SCHEDULE_CONFIG_PATH)
        self.configured_fetcher = ConfiguredConnectorFetcher(self.connector_store)
        self.evidence_repository = build_source_evidence_repository(self.SOURCE_EVIDENCE_STORE_PATH)
        self.evidence_builder = EvidenceBundleBuilder(self.evidence_repository)
        self.dead_letter_queue = DeadLetterQueue(self.DLQ_STORE_PATH)
        self.dead_letter_queue.load_from_spill()
        self.scheduler = IngestionScheduler(manager=self.manager, store=self.store, dead_letter_queue=self.dead_letter_queue)
        self.replay_processor = DeadLetterReplayProcessor(schema_registry=SchemaRegistry())
        self.source_health_store = SourceHealthStore.from_jsonl(self.SOURCE_HEALTH_STORE_PATH)
        self.source_usage_store = SourceUsageDailyStore.from_jsonl(self.SOURCE_USAGE_STORE_PATH)
        self.requirement_snapshot_store = RequirementSnapshotStore(self.REQUIREMENT_STATE_PATH)
        self.latest_market_snapshot_store = LatestMarketSnapshotStore(
            self.LATEST_MARKET_SNAPSHOT_PATH,
            max_closes=self.MARKET_SNAPSHOT_MAX_CLOSES,
            # MARKET_SNAPSHOT_STORE (journal default | dual | sqlite) is read by the store itself.
            # In sqlite mode the old-format export is rewritten at most once per interval (design 4.2 S2).
            export_min_interval_seconds=float(os.getenv("MARKET_SNAPSHOT_EXPORT_INTERVAL_SECONDS", "3600")),
        )
        self.snapshot_universe = SnapshotUniverse(
            self.latest_market_snapshot_store,
            state_path=self.LATEST_MARKET_SNAPSHOT_PATH.with_name(self.LATEST_MARKET_SNAPSHOT_PATH.name + ".universe.json"),
            storage_root=self.MARKET_DATA_STORAGE_ROOT,
            connector_market=self._connector_market_for_snapshot,
        )
        # The first due universe sweep runs here, before the app can answer /readyz, not inside the first
        # snapshot lookup (SOURCE-INGEST-READY-COVERS-SNAPSHOT-LOOKUP-20261118).
        self.snapshot_lookup_ready = False
        self.snapshot_warm_start_count = 0
        try:
            self._warm_snapshot_lookup()
        finally:
            self.snapshot_lookup_ready = True
        # The hourly sweep runs on this one background thread, never inside a lookup.
        self.snapshot_universe.start_background_sweeps()
        atexit.register(self.snapshot_universe.stop_background_sweeps)
        self.controller_token = load_controller_token(token_path=self.CONTROLLER_TOKEN_PATH, create=True)
        self.authoritative_reconcile_lock = threading.RLock()
        self.audit_store_lock = threading.RLock()
        self.source_execution_lock = threading.RLock()
        # The dated security listing: the collector host's state, read through the bundle transport (no provider
        # egress from hosted), kept as one file. Mode: SOURCE_INGEST_SECURITY_LISTING_MODE off|shadow|enforce.
        self.hosted_listing = HostedListing(self.DATA_DIR / HOSTED_STATE_FILENAME, transport=self._listing_transport)
        set_hosted_listing(self.hosted_listing)
        self.market_data_storage_writer = MarketDataStorageWriter(
            self.MARKET_DATA_STORAGE_ROOT, listing_provider=self.hosted_listing
        )
        self.distillation_job_queue = IngestDistillationJobQueue(self.DISTILLATION_JOB_QUEUE_PATH)
        self.source_management_store = build_source_management_store(self.DATA_DIR)
        self._STRATEGY_SEED_STORE_PATH = Path(
            (os.getenv("STRATEGY_SPEC_SEED_STORE_PATH") or os.getenv("STRATEGY_SEED_STORE_PATH"))
            if data_dir is None and (os.getenv("STRATEGY_SPEC_SEED_STORE_PATH") or os.getenv("STRATEGY_SEED_STORE_PATH"))
            else str(self.DATA_DIR / "distill_seeds.jsonl")
        )
        self.strategy_seed_store = StrategySpecSeedStore(path=self._STRATEGY_SEED_STORE_PATH)
        self.source_command_engine = SourceCommandEngine(
            store=self.source_management_store,
            connector_store=self.connector_store,
            schedule_config_store=self.schedule_config_store,
            evidence_builder=self.evidence_builder,
        )

        self._registry_cache_lock = threading.RLock()
        self._cached_freshness_key: Any | None = None
        self._cached_freshness_group: dict[str, Any] | None = None
        self._cached_provider_examples: list[dict[str, Any]] | None = None
        self._evidence_pg_initialized_repo_id: int | None = None
        self._evidence_pg_last_append_id: int = 0
        self._evidence_pg_last_count: int = 0
        self._latest_record_cache_key: Any | None = None
        self._latest_record_cache_value: dict[str, SourceRecord] = {}

        self._import_guard_lock = threading.RLock()
        self._last_import_input_digest: str | None = None
        self._last_import_report: Any | None = None

        self.pipeline = IngestPipelineService(self)
        self.import_configured_connectors_if_changed()

    def import_configured_connectors_if_changed(self, *, force: bool = False) -> Any | None:
        """Guarded startup/change import of configured connectors into SourceManagementStore (SD-SRCM § 10.1)."""
        if not force and os.getenv("SOURCE_MANAGEMENT_STARTUP_IMPORT_ENABLED", "1").strip().lower() in ("0", "false", "no"):
            return self._last_import_report
        with self._import_guard_lock:
            try:
                configs, _fetch_states = self.connector_store.read_snapshot()
            except Exception:
                return self._last_import_report
            if not configs and not force:
                self._last_import_input_digest = "empty"
                return self._last_import_report
            try:
                schedules = self.schedule_config_store.list_schedules()
            except Exception:
                schedules = []
            try:
                managed_count = len(self.source_management_store.list_instances())
            except Exception:
                managed_count = 0

            digest_material = {
                "connectors": [
                    (c.connector.connector_id, c.connector.status.value, c.updated_at)
                    for c in sorted(configs, key=lambda item: item.connector.connector_id)
                ],
                "schedules": [
                    (s.connector_id, s.interval_seconds, s.enabled, s.updated_at)
                    for s in sorted(schedules, key=lambda item: item.connector_id)
                ],
                "managed_count": managed_count,
            }
            digest = sha256(json.dumps(digest_material, sort_keys=True).encode("utf-8")).hexdigest()
            if not force and self._last_import_input_digest == digest and managed_count >= len(configs):
                return self._last_import_report

            from .source_management_import import import_configured_connectors

            report = import_configured_connectors(
                management_store=self.source_management_store,
                connector_store=self.connector_store,
                schedule_config_store=self.schedule_config_store,
                schedule_store=self.store,
                runtime=self,
            )
            try:
                post_managed_count = len(self.source_management_store.list_instances())
            except Exception:
                post_managed_count = managed_count
            digest_material["managed_count"] = post_managed_count
            self._last_import_input_digest = sha256(
                json.dumps(digest_material, sort_keys=True).encode("utf-8")
            ).hexdigest()
            self._last_import_report = report
            return report

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
    def _connector_market_for_snapshot(self, connector_id: str) -> str | None:
        connector = self.manager.get_connector(connector_id)
        meta = getattr(connector, "metadata", None)
        if isinstance(meta, dict) and meta.get("market"):
            return str(meta["market"]).strip()
        return None

    def _is_controller_owned(self, connector: SourceConnector | None) -> bool:
        if connector is None:
            return False
        marker = connector.metadata.get(RECONCILIATION_METADATA_KEY)
        return bool(
            isinstance(marker, Mapping)
            and marker.get("managed_by") == "persona_source_provisioning_reconciler"
        )

    def _is_operator_stopped(self, connector: SourceConnector | None) -> bool:
        if connector is None:
            return False
        metadata = dict(connector.metadata or {})
        if metadata.get("operator_stop"):
            return True
        if connector.status == ConnectorStatus.DISABLED:
            reconciliation = metadata.get(RECONCILIATION_METADATA_KEY)
            if isinstance(reconciliation, Mapping) and reconciliation.get("retired_by_authoritative_snapshot") is True:
                return False
            return True
        return False

    def _fence_managed_connector_mutation(
        self,
        connector_id: str,
        authorization: str | None,
        *,
        operation: str,
        proposed_connector: SourceConnector | None = None,
    ) -> None:
        existing = self.connector_store.get_config(connector_id)
        if (
            self._is_controller_owned(existing.connector if existing is not None else None)
            or self._is_controller_owned(proposed_connector)
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
        latest_record: Any | None = None,
        _preordered_receipts: bool = False,
        _precomputed_latest_run: Any | None = None,
    ) -> dict[str, Any]:
        from .pipeline import _parse_utc_datetime

        # Run/receipt timestamps have second precision. Choosing the first run
        # or breaking receipt ties with a random UUID can pair current health
        # with an older bundle's run, failing the controller's real readback.
        if _precomputed_latest_run is not None:
            latest_run = _precomputed_latest_run
        else:
            latest_run = max(reversed(runs), key=self._run_effective_at) if runs else None
        if not _preordered_receipts:
            receipts = _ordered_run_receipts(receipts, runs)
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
        is_taiwan = connector_id == "tw-twse-tpex-official-market"
        if is_taiwan and source_timestamp_dt is not None and source_timestamp_status == "valid":
            source_id_str = str(getattr(latest_record, "source_id", "") if latest_record is not None else "")
            if latest_record is None or not source_id_str.startswith("tw-official:"):
                stale = True
            else:
                lineage = {
                    "connector_ids": [connector_id],
                    "source_ids": [source_id_str],
                }
                cal_ev = None
                if latest_record is not None and hasattr(latest_record, "metadata") and isinstance(latest_record.metadata, Mapping):
                    cal_ev = latest_record.metadata.get("calendar_evidence")
                if cal_ev is None and latest_success_receipt is not None and hasattr(latest_success_receipt, "metadata") and isinstance(latest_success_receipt.metadata, Mapping):
                    cal_ev = latest_success_receipt.metadata.get("calendar_evidence")

                from services.execution.market_snapshot_admission import (
                    evaluate_taiwan_market_freshness,
                )

                tw_ok, _tw_reason, _tw_detail = evaluate_taiwan_market_freshness(
                    event_time_dt=source_timestamp_dt,
                    now_dt=now,
                    refresh_receipt_dt=last_success_dt,
                    lineage=lineage,
                    max_refresh_age_seconds=stale_threshold_seconds,
                    calendar_evidence=cal_ev,
                )
                stale = not tw_ok
        elif connector_id in FINMIND_FUNDAMENTAL_PUBLICATION_DATASETS:
            stale = source_timestamp_status != "valid" or (
                source_timestamp_dt is not None
                and not finmind_fundamental_publication_fresh(
                    FINMIND_FUNDAMENTAL_PUBLICATION_DATASETS[connector_id],
                    source_timestamp_dt,
                    now,
                )
            )
        else:
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
            "latest_receipt": _receipt_summary(latest_receipt) if latest_receipt is not None else None,
            "staleness_seconds": staleness_seconds,
            "next_due_at": next_due_at,
            "next_run_at": next_due_at,
            "seconds_until_due": seconds_until_due,
        }

    def _grouped_freshness_snapshot(self) -> dict[str, Any]:
        store = self.store
        if (
            "read_freshness_snapshot" not in getattr(store, "__dict__", {})
            and hasattr(store, "_reload_if_changed_unlocked")
            and hasattr(store, "_lock_path")
            and hasattr(store, "_lock")
        ):
            from .process_lock import exclusive_file_lock

            with exclusive_file_lock(store._lock_path, store._lock):
                store._reload_if_changed_unlocked()
                last_run_obj = next(reversed(store._runs.values()), None) if store._runs else None
                last_rcp_obj = next(reversed(store._receipts.values()), None) if store._receipts else None
                wm_sig = tuple((k, id(v)) for k, v in store._watermarks.items())
                cache_key: Any = (
                    id(store),
                    getattr(store, "_read_key", None),
                    getattr(store, "_read_offset", 0),
                    len(store._runs),
                    len(store._receipts),
                    id(last_run_obj),
                    id(last_rcp_obj),
                    wm_sig,
                )
                with self._registry_cache_lock:
                    if self._cached_freshness_key == cache_key and self._cached_freshness_group is not None:
                        return self._cached_freshness_group
                runs_tuple = tuple(store._runs.values())
                receipts_tuple = tuple(store._sort_receipts_unlocked(store._receipts.values()))
                watermarks_dict = dict(store._watermarks)
        else:
            snapshot = store.read_freshness_snapshot()
            runs_tuple = tuple(snapshot.get("runs", ()))
            receipts_tuple = tuple(snapshot.get("receipts", ()))
            watermarks_dict = dict(snapshot.get("watermarks", {}))
            cache_key = None

        runs_by_connector: dict[str, list[Any]] = {}
        for run in runs_tuple:
            cid = str(getattr(run, "connector_id", ""))
            runs_by_connector.setdefault(cid, []).append(run)
        receipts_by_connector: dict[str, list[IngestReceipt]] = {}
        for receipt in receipts_tuple:
            receipts_by_connector.setdefault(receipt.connector_id, []).append(receipt)

        ordered_receipts_by_connector: dict[str, tuple[IngestReceipt, ...]] = {}
        latest_completed_run_id_by_connector: dict[str, str] = {}
        all_cids = set(runs_by_connector) | set(receipts_by_connector)
        for cid in all_cids:
            c_runs = runs_by_connector.get(cid, ())
            c_recs = receipts_by_connector.get(cid, ())
            ordered = tuple(_ordered_run_receipts(c_recs, c_runs)) if c_recs else ()
            ordered_receipts_by_connector[cid] = ordered
            latest_completed = next((r for r in reversed(ordered) if r.status == "completed"), None)
            if latest_completed is not None and latest_completed.ingest_run_id:
                latest_completed_run_id_by_connector[cid] = str(latest_completed.ingest_run_id)

        latest_run_by_connector: dict[str, Any] = {
            cid: max(reversed(c_runs), key=self._run_effective_at)
            for cid, c_runs in runs_by_connector.items()
            if c_runs
        }

        grouped = {
            "runs_by_connector": {cid: tuple(c_runs) for cid, c_runs in runs_by_connector.items()},
            "receipts_by_connector": ordered_receipts_by_connector,
            "ordered_receipts_by_connector": ordered_receipts_by_connector,
            "latest_run_by_connector": latest_run_by_connector,
            "latest_completed_run_id_by_connector": latest_completed_run_id_by_connector,
            "watermarks": watermarks_dict,
        }
        if cache_key is not None:
            with self._registry_cache_lock:
                self._cached_freshness_key = cache_key
                self._cached_freshness_group = grouped
        return grouped

    def _connector_freshness_summary(self, connector_id: str) -> dict[str, Any]:
        config = self.connector_store.get_config(connector_id)
        schedule = self.schedule_config_store.get_schedule(connector_id)
        grouped = self._grouped_freshness_snapshot()
        runs = grouped["runs_by_connector"].get(connector_id, ())
        receipts = grouped["ordered_receipts_by_connector"].get(connector_id, ())
        latest_run = grouped["latest_run_by_connector"].get(connector_id)
        if connector_id == "tw-twse-tpex-official-market":
            latest_record = self._latest_source_record_by_connector(
                receipts_by_connector=grouped["ordered_receipts_by_connector"],
                runs_by_connector=grouped["runs_by_connector"],
                connector_ids=(connector_id,),
            ).get(connector_id)
        else:
            latest_record = None
        return self._connector_freshness_summary_from_snapshot(
            connector_id,
            connector_metadata=(config.connector.metadata if config is not None else {}),
            schedule=schedule,
            watermark=grouped["watermarks"].get(connector_id),
            runs=runs,
            receipts=receipts,
            now=datetime.now(timezone.utc),
            latest_record=latest_record,
            _preordered_receipts=True,
            _precomputed_latest_run=latest_run,
        )

    def _warm_snapshot_lookup(self) -> None:
        """Run the first due universe sweep once, before readiness, instead of inside the first lookup.

        ``SnapshotUniverse._last_sweep`` starts at 0, so the first ``snapshot_for_request`` after a start found
        the sweep due and ran it (withdraw_symbols -> journal compaction) in its ``finally`` under the universe
        lock: about 20 s on a hosted-shaped journal.  A sweep that cannot run is left for the hourly one.
        """
        universe = self.snapshot_universe
        self.snapshot_warm_start_count += 1
        with universe._lock:
            universe._last_sweep = time.monotonic()
            if not universe.ready:
                return
            try:
                universe.sweep()
            except (MarketSnapshotStateError, OSError):
                pass

    def _source_freshness_readiness(self) -> dict[str, Any]:
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
            "controller_stale": controller_stale,
            "reason": "controller_stale" if controller_stale else None,
            "controller_max_age_seconds": self._SOURCE_READINESS_MAX_CONTROLLER_AGE_SECONDS,
            "scheduled_connector_count": len(scheduled),
            "stale_connector_count": len(stale),
            "degraded_connector_count": len(degraded),
            # Items the last reconcile left alone because a newer release wrote them (RC1 WP1.5).
            "skipped_count": int(state.reconcile.get("skipped_count") or 0),
            "skipped_ids": list(state.reconcile.get("skipped_ids") or [])[:50],
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

    @staticmethod
    def _listing_transport() -> Any:
        """The bundle transport the collector pull already uses; None while no collector token is provisioned."""
        from .connectors.collector_bundle import COLLECTOR_BUNDLE_TOKEN_ENV, HttpBundleTransport, collector_host

        if not os.environ.get(COLLECTOR_BUNDLE_TOKEN_ENV, "").strip():
            return None
        return HttpBundleTransport(host=collector_host(), timeout_seconds=20.0)

    def _source_runtime_metrics(self) -> dict[str, Any]:
        readiness = self._source_freshness_readiness()
        return {
            "controller_state_size_bytes": readiness.get("controller_state_size_bytes", 0),
            "connector_count": readiness.get("connector_inventory_count", 0),
            "scheduled_connector_count": readiness.get("scheduled_connector_count", 0),
            "stale_connector_count": readiness.get("stale_connector_count", 0),
            "degraded_connector_count": readiness.get("degraded_connector_count", 0),
            "skipped_count": readiness.get("skipped_count", 0),
            "skipped_ids": readiness.get("skipped_ids", []),
            "posture_alert_count": self.PRODUCTION_POSTURE.alert_count(),
            "latest_market_snapshot_journal": self.latest_market_snapshot_store.journal_stats(),
            "ingest_schedule_journal": JsonlIngestScheduleStore.journal_stats_for_path(self.SCHEDULE_STORE_PATH),
            "security_listing": self.hosted_listing.health(),
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
        grouped_freshness = self._grouped_freshness_snapshot()
        runs_by_connector = grouped_freshness["runs_by_connector"]
        receipts_by_connector = grouped_freshness["ordered_receipts_by_connector"]
        latest_run_by_connector = grouped_freshness["latest_run_by_connector"]
        watermarks = grouped_freshness["watermarks"]
        observed_at = datetime.now(timezone.utc)

        connector_ids = set(configured_by_id)
        connectors = list(self.manager.list_connectors())
        connector_ids.update(connector.connector_id for connector in connectors)

        if "tw-twse-tpex-official-market" in connector_ids:
            latest_by_connector = self._latest_source_record_by_connector(
                receipts_by_connector=receipts_by_connector,
                runs_by_connector=runs_by_connector,
                connector_ids=("tw-twse-tpex-official-market",),
            )
        else:
            latest_by_connector = {}

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
                        watermark=watermarks.get(connector_id),
                        runs=runs_by_connector.get(connector_id, ()),
                        receipts=receipts_by_connector.get(connector_id, ()),
                        now=observed_at,
                        latest_record=latest_by_connector.get(connector_id),
                        _preordered_receipts=True,
                        _precomputed_latest_run=latest_run_by_connector.get(connector_id),
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
        if self._cached_provider_examples is not None:
            return self._cached_provider_examples
        payloads: list[dict[str, Any]] = []
        for provider in example_provider_catalog():
            connector = provider.connector()
            payloads.append(
                {
                    "connector": connector.to_dict(),
                    "fetch_policy": self._fetch_policy_summary(dict(provider.fetch_config())),
                }
            )
        self._cached_provider_examples = payloads
        return payloads

    def _source_provisioning_reconciler(self) -> SourceProvisioningReconciler:
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

    def _skipped_requirements(self, results: list[Any]) -> list[dict[str, str]]:
        return [
            {
                "kind": "requirement",
                "id": action.idempotency_key,
                "reason": str(action.details.get("reason") or action.connector_action),
            }
            for result in results
            for action in result.actions
            if action.connector_action in UNDEFINED_BY_RELEASE_ACTIONS
        ]

    def _undefined_stored_records(self, reconciler: SourceProvisioningReconciler) -> list[dict[str, str]]:
        """Stored connector and schedule records this release cannot interpret (read-only)."""

        items: list[dict[str, str]] = []
        for config in sorted(self.connector_store.list_configs(), key=lambda item: item.connector.connector_id):
            reason = reconciler.undefined_reason(config)
            if reason is None:
                continue
            connector_id = config.connector.connector_id
            items.append({"kind": "connector_record", "id": connector_id, "reason": reason})
            if self.schedule_config_store.get_schedule(connector_id) is not None:
                items.append(
                    {"kind": "schedule_record", "id": connector_id, "reason": "connector-not-defined-by-this-release"}
                )
        return items

    @staticmethod
    def _dedupe_skipped(items: list[dict[str, str]]) -> list[dict[str, str]]:
        seen: set[tuple[str, str]] = set()
        unique: list[dict[str, str]] = []
        for item in items:
            key = (item["kind"], item["id"])
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    def _uninterpreted_bindings(
        self, planned_bindings: Mapping[str, str], reconciler: SourceProvisioningReconciler
    ) -> dict[str, str]:
        """Previously accepted bindings whose connector record this release cannot interpret."""

        previous = self.requirement_snapshot_store.latest
        if previous is None:
            return {}
        carried: dict[str, str] = {}
        for requirement_id, connector_id in previous.bindings.items():
            if requirement_id in planned_bindings:
                continue
            config = self.connector_store.get_config(connector_id)
            if config is not None and reconciler.undefined_reason(config) is not None:
                carried[requirement_id] = connector_id
        return carried

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
        reconciler: SourceProvisioningReconciler | None = None,
        skipped: list[dict[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        reconciler = reconciler or self._source_provisioning_reconciler()
        skipped = skipped if skipped is not None else []
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
            undefined = reconciler.undefined_reason(config)
            if undefined is not None:
                # Written by a newer release: this release cannot interpret it, so it must not
                # disable it, rewrite it or fail the tick over it. Stored bytes stay untouched.
                actions.append({"connector_id": connector_id, "action": "left_alone_not_defined_by_release"})
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
            "symbol",
            "event_time",
            "available_time",
            "api_endpoint",
            "access_scope",
            "license_scope",
            "schema_hash",
            "source_ingest_run_id",
            "calendar_evidence",
            "tenant_id",
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
            "metadata": metadata,
            "provenance": {key: metadata[key] for key in provenance_keys if key in metadata},
        }

    def collector_market_record_from_partition(self, source_id: str, tenant_id: str) -> SourceRecord | None:
        """Resolve a snapshot's row lineage without storing per-row evidence.

        Only completed owner receipts authorize a partition read. Tenant and
        native Collector identity are checked on the row before returning it.
        """
        if not source_id.startswith("collector:"):
            return None
        native_source = source_id.split(":", 2)[1]
        day_match = re.search(r"\d{4}-\d{2}-\d{2}", source_id)
        owned_partitions = {
            str(Path(ref["uri"]).resolve())
            for summary in self.evidence_repository.list_source_records(tenant_id=tenant_id)
            if summary.metadata.get("collector_market_bundle")
            and (summary.metadata.get("collector") or {}).get("source_id") == native_source
            for ref in summary.metadata.get("normalized_partition_refs", ())
            if not day_match or ref.get("date") == day_match.group()
        }
        if not owned_partitions:
            return None
        seen: set[str] = set()
        for receipt in reversed(self.store.read_freshness_snapshot()["receipts"]):
            if receipt.status != "completed":
                continue
            for ref in receipt.storage_refs.get("normalized_refs", ()):
                if day_match and ref.get("date") != day_match.group():
                    continue
                uri = str(ref["uri"])
                if uri in seen or str(Path(uri).parent.resolve()) not in owned_partitions:
                    continue
                seen.add(uri)
                with Path(uri).open() as handle:
                    for line in handle:
                        row = json.loads(line)
                        metadata = row.get("metadata") or {}
                        if (row.get("source_id") != source_id or metadata.get("tenant_id") != tenant_id
                                or (metadata.get("collector") or {}).get("source_id") != native_source):
                            continue
                        return SourceRecord.from_dict({
                            **row, "source_type": "market", "status": "normalized",
                            "title": source_id.removeprefix(f"collector:{native_source}:").rsplit(":", 1)[0],
                            "trace_id": metadata.get("trace_id"), "created_at": metadata.get("ingest_time"),
                            "metadata": {**metadata, "source_ingest_run_id": receipt.ingest_run_id,
                                         "source_record_projection": "normalized_partition"},
                        })
        return None

    @staticmethod
    def _record_run_receipt_readback(
        record: SourceRecord | None, receipts: Sequence[IngestReceipt]
    ) -> dict[str, Any] | None:
        """Completed receipt of the very ingest run that produced ``record`` (None when unpaired)."""
        if record is None:
            return None
        meta = record.metadata or {}
        run_id = str(meta.get("source_ingest_run_id") or meta.get("ingest_run_id") or "")
        if not run_id:
            return None
        for receipt in reversed(tuple(receipts)):
            if receipt.status == "completed" and str(receipt.ingest_run_id) == run_id and receipt.finished_at:
                return {
                    "ingest_run_id": run_id,
                    "connector_id": receipt.connector_id,
                    "status": receipt.status,
                    "finished_at": receipt.finished_at,
                }
        return None

    def _sync_and_list_source_records(self) -> Sequence[SourceRecord]:
        from services.knowledge.evidence import InMemoryEvidenceRepository
        from .pg_store import PostgresSourceEvidenceRepository

        repo = self.evidence_repository
        cls_method = getattr(type(repo), "list_source_records", None)
        if (
            "list_source_records" in getattr(repo, "__dict__", {})
            or not hasattr(repo, "_source_records")
            or cls_method not in (
                InMemoryEvidenceRepository.list_source_records,
                PostgresSourceEvidenceRepository.list_source_records,
            )
        ):
            return tuple(repo.list_source_records())

        if cls_method is PostgresSourceEvidenceRepository.list_source_records and hasattr(repo, "_get_connection"):
            rel_name = getattr(repo, "table", "")
            try:
                with repo._get_connection() as conn:
                    meta_row = conn.execute(
                        f"SELECT COALESCE(MAX(append_id), 0), COUNT(*) FROM {rel_name} WHERE record_type = %s",
                        ("source_record",),
                    ).fetchone()
                    if isinstance(meta_row, (tuple, list)) and len(meta_row) >= 2:
                        max_append_id = int(meta_row[0])
                        db_count = int(meta_row[1])
                    elif isinstance(meta_row, Mapping) and len(meta_row) >= 2:
                        vals = list(meta_row.values())
                        max_append_id = int(vals[0])
                        db_count = int(vals[1])
                    else:
                        raise ValueError("unexpected metadata row format")

                    if self._evidence_pg_initialized_repo_id != id(repo):
                        if len(repo._source_records) == db_count:
                            self._evidence_pg_last_append_id = max_append_id
                            self._evidence_pg_last_count = db_count
                            self._evidence_pg_initialized_repo_id = id(repo)
                        else:
                            rows = conn.execute(
                                f"SELECT append_id, payload FROM {rel_name} WHERE record_type = %s ORDER BY append_id ASC",
                                ("source_record",),
                            ).fetchall()
                            for row in rows:
                                payload = row[1] if isinstance(row, (tuple, list)) else row.get("payload")
                                if isinstance(payload, str):
                                    payload = json.loads(payload)
                                if isinstance(payload, dict):
                                    rec = SourceRecord.from_dict(payload)
                                    repo._source_records[rec.source_id] = rec
                                    if hasattr(repo, "_source_records_by_tenant"):
                                        repo._source_records_by_tenant[(rec.tenant_id, rec.source_id)] = rec
                            self._evidence_pg_last_append_id = max_append_id
                            self._evidence_pg_last_count = db_count
                            self._evidence_pg_initialized_repo_id = id(repo)
                    elif max_append_id > self._evidence_pg_last_append_id:
                        rows = conn.execute(
                            f"SELECT append_id, payload FROM {rel_name} WHERE record_type = %s AND append_id > %s ORDER BY append_id ASC",
                            ("source_record", self._evidence_pg_last_append_id),
                        ).fetchall()
                        for row in rows:
                            payload = row[1] if isinstance(row, (tuple, list)) else row.get("payload")
                            if isinstance(payload, str):
                                payload = json.loads(payload)
                            if isinstance(payload, dict):
                                rec = SourceRecord.from_dict(payload)
                                repo._source_records[rec.source_id] = rec
                                if hasattr(repo, "_source_records_by_tenant"):
                                    repo._source_records_by_tenant[(rec.tenant_id, rec.source_id)] = rec
                        self._evidence_pg_last_append_id = max_append_id
                        self._evidence_pg_last_count = db_count
                    elif db_count != self._evidence_pg_last_count:
                        rows = conn.execute(
                            f"SELECT append_id, payload FROM {rel_name} WHERE record_type = %s ORDER BY append_id ASC",
                            ("source_record",),
                        ).fetchall()
                        repo._source_records.clear()
                        for row in rows:
                            payload = row[1] if isinstance(row, (tuple, list)) else row.get("payload")
                            if isinstance(payload, str):
                                payload = json.loads(payload)
                            if isinstance(payload, dict):
                                rec = SourceRecord.from_dict(payload)
                                repo._source_records[rec.source_id] = rec
                                if hasattr(repo, "_source_records_by_tenant"):
                                    repo._source_records_by_tenant[(rec.tenant_id, rec.source_id)] = rec
                        self._evidence_pg_last_append_id = max_append_id
                        self._evidence_pg_last_count = db_count
            except Exception:
                return tuple(repo.list_source_records())

        return tuple(repo._source_records.values())

    def _latest_source_record_by_connector(
        self,
        receipts_by_connector: Mapping[str, Sequence[IngestReceipt]] | None = None,
        runs_by_connector: Mapping[str, Sequence[Any]] | None = None,
        *,
        connector_ids: Iterable[str] | None = None,
    ) -> dict[str, SourceRecord]:
        target_cids = frozenset(str(c) for c in connector_ids) if connector_ids is not None else None
        if target_cids is not None and not target_cids:
            return {}
        active_symbols = _active_contract_symbols()
        latest_run_by_connector: dict[str, str] = {}
        if receipts_by_connector is None:
            try:
                grouped = self._grouped_freshness_snapshot()
                latest_run_by_connector = dict(grouped["latest_completed_run_id_by_connector"])
            except Exception:
                latest_run_by_connector = {}
        else:
            iter_cids = (
                (cid for cid in receipts_by_connector if cid in target_cids)
                if target_cids is not None
                else receipts_by_connector.keys()
            )
            for cid in iter_cids:
                recs = receipts_by_connector[cid]
                ordered = _ordered_run_receipts(recs, (runs_by_connector or {}).get(cid, ()))
                latest_completed = next((r for r in reversed(ordered) if r.status == "completed"), None)
                if latest_completed and latest_completed.ingest_run_id:
                    latest_run_by_connector[cid] = str(latest_completed.ingest_run_id)

        records = self._sync_and_list_source_records()
        last_rec_obj = records[-1] if records else None
        relevant_runs_key = tuple(
            sorted(
                (cid, run_id)
                for cid, run_id in latest_run_by_connector.items()
                if target_cids is None or cid in target_cids
            )
        )
        cache_key = (
            id(self.evidence_repository),
            len(records),
            id(last_rec_obj),
            relevant_runs_key,
            frozenset(active_symbols),
            target_cids,
        )
        with self._registry_cache_lock:
            if self._latest_record_cache_key == cache_key:
                return dict(self._latest_record_cache_value)

        latest: dict[str, tuple[tuple[Any, ...], SourceRecord]] = {}
        for record in records:
            cid = record.connector_id
            if target_cids is not None and cid not in target_cids:
                continue
            # Historical sweeps grow this population on every tick. Ranking only
            # reads scalar metadata; avoid allocating serialized record and
            # metadata mappings for every stored row in each readback.
            rec_meta = record.metadata or {}
            norm_row = rec_meta.get("normalized_row") if isinstance(rec_meta.get("normalized_row"), dict) else {}
            rec_run_id = str(rec_meta.get("source_ingest_run_id") or rec_meta.get("ingest_run_id") or "")
            target_run_id = latest_run_by_connector.get(cid, "")
            is_accepted_run = 1 if (target_run_id and rec_run_id == target_run_id) else 0

            dataset = str(rec_meta.get("dataset") or norm_row.get("dataset") or "")
            is_price = 1 if dataset == "tw_price_daily" or dataset.endswith("_price_daily") else 0

            symbol = str(rec_meta.get("symbol") or norm_row.get("symbol") or "").strip().upper()
            is_active_symbol = 1 if (active_symbols and symbol in active_symbols) else 0

            rec_avail = str(
                rec_meta.get("available_time")
                or rec_meta.get("date")
                or norm_row.get("available_time")
                or norm_row.get("date")
                or ""
            )
            rec_created = (
                record.created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                if isinstance(record.created_at, datetime) else str(record.created_at or "")
            )
            source_id = str(record.source_id or "")

            if cid in COLLECTOR_PRICE_HISTORY_SOURCE_IDS:
                # The collector backfills oldest-first after a same-day close is admitted, so the
                # latest completed run can be an older session; newest admitted session wins.
                # Rank by the validated trade date / event time, never available_time: the collector
                # sets available_time to fetched_at, so a later backfill fetch would otherwise displace
                # a newer close.
                rec_session = str(
                    rec_meta.get("trade_date") or norm_row.get("trade_date") or str(rec_meta.get("event_time") or "")[:10]
                )[:10]
                rec_key = (is_price, rec_session, is_accepted_run, is_active_symbol, rec_avail, rec_created, source_id)
            else:
                rec_key = (is_accepted_run, is_price, is_active_symbol, rec_avail, rec_created, source_id)
            if cid not in latest or rec_key > latest[cid][0]:
                latest[cid] = (rec_key, record)

        result = {cid: item[1] for cid, item in latest.items()}
        with self._registry_cache_lock:
            self._latest_record_cache_key = cache_key
            self._latest_record_cache_value = dict(result)
        return result

    def _collector_offerings(self, configs: Sequence[ConfiguredConnector]) -> dict[str, dict[str, Any]]:
        from .connectors.collector_bundle import (
            COLLECTOR_BUNDLE_ADAPTER_TOKEN, CollectorBundleAdapter, HttpBundleTransport,
            collector_host, operation_deadline, source_offering,
        )
        documents: dict[str, Any] = {}
        offerings = {}
        reconciler = self._source_provisioning_reconciler()
        for config in configs:
            undefined = reconciler.undefined_reason(config)
            if undefined is not None:
                # A record a newer release wrote: not offered by this release, so the scheduler
                # skips it instead of failing the pull stage on an adapter it does not define.
                offerings[config.connector.connector_id] = {
                    "offered": False,
                    "reason": f"not-defined-by-this-release: {undefined}",
                }
                continue
            fetch = getattr(config, "fetch", None) or {}
            if fetch.get("mode") != "provider_owned_adapter" or fetch.get("adapter") != COLLECTOR_BUNDLE_ADAPTER_TOKEN:
                continue
            spec = fetch.get("adapter_config") or {}
            host = str(spec.get("host") or collector_host())
            if host not in documents:
                # One bounded status read per host for this snapshot, never per source.
                with operation_deadline(2.0):
                    try:
                        documents[host] = CollectorBundleAdapter(
                            connector_id=config.connector.connector_id, source_id=str(spec.get("source_id") or ""),
                            transport=HttpBundleTransport(host=host, timeout_seconds=2.0),
                        ).read_status()
                    except SourceEvidenceError:
                        documents[host] = None  # unavailable/malformed capability is never invented
            offerings[config.connector.connector_id] = source_offering(documents[host], str(spec.get("source_id") or ""))
        return offerings

    def _controller_connector_readbacks(self) -> list[dict[str, Any]]:
        configs, fetch_states = self.connector_store.read_snapshot()
        offerings = self._collector_offerings(configs)
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
        latest_by_connector = self._latest_source_record_by_connector(
            receipts_by_connector=receipts_by_connector, runs_by_connector=runs_by_connector)
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
                latest_record=latest_by_connector.get(connector_id),
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
                    "latest_source_record_receipt": self._record_run_receipt_readback(
                        latest_by_connector.get(connector_id), receipts_by_connector.get(connector_id, ())
                    ),
                    "source_health": health_payload,
                    **({"collector_offering": offerings[connector_id]} if connector_id in offerings else {}),
                }
            )
        return readbacks

    def _controller_readback_payload(self) -> dict[str, Any]:
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
        dlq_status_counts_by_connector: dict[str, dict[str, int]] = {}
        for entry in dlq_entries:
            connector_id = entry.event.payload.get("connector_id")
            # Preserve unattributed entries in the totals. The terminal validator
            # rejects this bucket (and unknown connector IDs) for scoped admission.
            if not isinstance(connector_id, str) or not connector_id.strip():
                connector_id = ""
            counts = dlq_status_counts_by_connector.setdefault(
                connector_id, {status.value: 0 for status in DeadLetterStatus}
            )
            counts[entry.status.value] += 1
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
            "skipped_count": int(((controller_state or {}).get("reconcile") or {}).get("skipped_count") or 0),
            "skipped_ids": list(((controller_state or {}).get("reconcile") or {}).get("skipped_ids") or [])[:50],
            "controller_state_path": str(self.CONTROLLER_STATE_PATH),
            "requirement_snapshot": requirement_snapshot.to_dict() if requirement_snapshot is not None else None,
            "connector_count": len(connector_readbacks),
            "source_record_count": len(self._sync_and_list_source_records()),
            "dlq_count": len(dlq_entries),
            "pending_dlq_count": dlq_status_counts[DeadLetterStatus.PENDING.value],
            "unresolved_dlq_count": unresolved_dlq_count,
            "dlq_status_counts": dlq_status_counts,
            "dlq_status_counts_by_connector": dict(sorted(dlq_status_counts_by_connector.items())),
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
        skipped_items: list[dict[str, str]] = []
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
                uninterpreted = self._uninterpreted_bindings(planned_bindings, reconciler)
                for requirement_id, connector_id in sorted(uninterpreted.items()):
                    skipped_items.append(
                        {
                            "kind": "binding",
                            "id": requirement_id,
                            "reason": f"binding-kept-uninterpreted: connector {connector_id} is not defined by this release",
                        }
                    )
                accepted_snapshot = self.requirement_snapshot_store.append(
                    desired_state_sha256=desired_state_sha256,
                    # Bindings this release cannot interpret are carried forward, never dropped.
                    bindings={**planned_bindings, **uninterpreted},
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
                    current_bindings={**bindings, **uninterpreted},
                    reconciler=reconciler,
                    skipped=skipped_items,
                )
        else:
            results = list(reconciler.reconcile_personas(personas, dry_run=request.dry_run))
            summary = self._provisioning_summary(results)
            bindings = self._requirement_bindings(results)
        skipped_items = self._dedupe_skipped(
            [*self._skipped_requirements(results), *skipped_items, *self._undefined_stored_records(reconciler)]
        )
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
            "skipped": skipped_items,
            "skipped_count": len(skipped_items),
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


def create_runtime(data_dir: Path | None = None) -> SourceIngestionRuntime:
    return SourceIngestionRuntime(data_dir=data_dir)
