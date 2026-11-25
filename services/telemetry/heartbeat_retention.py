"""Runtime heartbeat, infrastructure_health, runtime_state_snapshot, and 14-day telemetry_events retention.

TELEMETRY-HEARTBEAT-RETENTION-20261117, TELEMETRY-INFRA-HEALTH-RETENTION-20261117 &
TELEMETRY-EVENTS-RETENTION-14D-20261125:
- Keeps raw ``event_type = 'heartbeat'`` rows in ``public.telemetry_events``
  for ``PANTHEON_TELEMETRY_HEARTBEAT_RAW_RETENTION_DAYS`` (default 3 days).
- Summarises each ``(runtime_id, hour_bucket)`` in UTC into
  ``public.telemetry_heartbeat_hourly_summaries`` (storing ``heartbeat_count``,
  ``first_heartbeat_at``, ``last_heartbeat_at``, ``largest_gap_seconds``, and
  ``last_connectivity_status``) BEFORE any raw heartbeat row of that hour is
  deleted. Summary rows are permanent and never deleted.
- Keeps ``event_type = 'infrastructure_health'`` rows in ``public.telemetry_events``
  for ``PANTHEON_TELEMETRY_INFRA_HEALTH_RETENTION_DAYS`` (default 7 days), and
  protects any ``infrastructure_health`` event ID referenced by an IncidentCase
  or Postmortem so ``GET /api/telemetry/events/<event_id>`` and
  ``GET /api/telemetry/lineage/events/<event_id>/trace`` stay resolvable for as
  long as that incident or postmortem can be validated.
- Prunes unreferenced non-permanent ``public.telemetry_events`` rows older than
  ``PANTHEON_TELEMETRY_EVENTS_RETENTION_DAYS`` (default 14 days per 2026-11-25
  operator decision) in bounded batches while preserving consumer watermarks,
  incident/postmortem references, latest per-runtime snapshot anchors, distinct
  quarter-equity valuation observations, and canonical fill/governance/deploy/
  decision/episode rows required by ``CanonicalFillHistoryReader``,
  ``RuntimeSummaryProjectionStore``, or ``TradeEpisodeProjectionStore``.
- Never deletes a row whose ``ingested_seq`` has not yet been passed by every
  consumer reading ``public.telemetry_events`` by ``ingested_seq``.
- Verifies that no registered reader requires unreferenced raw rows older than
  its configured retention window; if any reader does, refuses to delete and
  raises ``HeartbeatRetentionReaderBlocker`` naming that reader.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

from .ingest_svc import CanonicalFillHistoryReader
from .runtime_summary import RuntimeSummaryProjectionStore
from .trade_episode_projection import TradeEpisodeProjectionStore

log = logging.getLogger(__name__)

CANONICAL_TELEMETRY_TABLE = "public.telemetry_events"
HEARTBEAT_SUMMARY_TABLE = "public.telemetry_heartbeat_hourly_summaries"
HEARTBEAT_EVENT_TYPE = "heartbeat"
INFRA_HEALTH_EVENT_TYPE = "infrastructure_health"
STATE_SNAPSHOT_EVENT_TYPE = "runtime_state_snapshot"

DEFAULT_RAW_RETENTION_DAYS = 3.0
DEFAULT_INFRA_HEALTH_RETENTION_DAYS = 7.0
# PAPER-RUNTIME-STATE-SNAPSHOT-CHANGE-DETECTION-20261118: raw runtime_state_snapshot
# rows older than this are deleted, except the latest row per runtime and rows
# whose performance_telemetry.status is a quarter-equity "unknown" observation
# (the runtime summary keeps those as history). The oldest row per runtime
# is kept too: the summary's binding first-event time derives from it.
DEFAULT_STATE_SNAPSHOT_RETENTION_DAYS = 3.0
STATE_SNAPSHOT_KEEP_STATUSES = ("marks_unavailable", "invalid_ledger", "invalid_drawdown_series")
# TELEMETRY-EVENTS-RETENTION-14D-20261125: operator decision (2026-11-25) sets
# a 14-day retention window on unreferenced non-permanent public.telemetry_events
# rows while preserving consumer watermarks, incident/postmortem references,
# per-runtime snapshot/valuation anchors, and canonical fill/governance/deploy/
# decision/episode rows.
DEFAULT_TELEMETRY_EVENTS_RETENTION_DAYS = 14.0
DEFAULT_BATCH_SIZE = 5000
DEFAULT_MAX_BATCHES_PER_RUN = 20
DEFAULT_INTERVAL_SECONDS = 300.0
DEFAULT_STATEMENT_TIMEOUT_MS = 15000

DEFAULT_INCIDENT_CASES_TABLE = "incident.incident_cases"
DEFAULT_POSTMORTEMS_TABLE = "incident.postmortems"

RAW_RETENTION_DAYS_ENV = "PANTHEON_TELEMETRY_HEARTBEAT_RAW_RETENTION_DAYS"
INFRA_HEALTH_RETENTION_DAYS_ENV = "PANTHEON_TELEMETRY_INFRA_HEALTH_RETENTION_DAYS"
STATE_SNAPSHOT_RETENTION_DAYS_ENV = "PANTHEON_TELEMETRY_STATE_SNAPSHOT_RETENTION_DAYS"
TELEMETRY_EVENTS_RETENTION_DAYS_ENV = "PANTHEON_TELEMETRY_EVENTS_RETENTION_DAYS"
RETENTION_ENABLED_ENV = "PANTHEON_TELEMETRY_HEARTBEAT_RETENTION_ENABLED"
RETENTION_BATCH_SIZE_ENV = "PANTHEON_TELEMETRY_HEARTBEAT_RETENTION_BATCH_SIZE"
RETENTION_MAX_BATCHES_ENV = "PANTHEON_TELEMETRY_HEARTBEAT_RETENTION_MAX_BATCHES_PER_RUN"
RETENTION_INTERVAL_SECONDS_ENV = "PANTHEON_TELEMETRY_HEARTBEAT_RETENTION_INTERVAL_SECONDS"
RETENTION_STATEMENT_TIMEOUT_MS_ENV = "PANTHEON_TELEMETRY_HEARTBEAT_RETENTION_STATEMENT_TIMEOUT_MS"

DEDICATED_RETENTION_EVENT_TYPES: tuple[str, ...] = (
    HEARTBEAT_EVENT_TYPE,
    INFRA_HEALTH_EVENT_TYPE,
    STATE_SNAPSHOT_EVENT_TYPE,
)

PERMANENT_TELEMETRY_EVENT_TYPES: tuple[str, ...] = (
    "deploy_started",
    "deploy_completed",
    "runtime_retired",
    "rollback_started",
    "rollback_completed",
    "pause_triggered",
    "liquidate_triggered",
    "governance_decision",
    "approval_action",
    "manual_override",
    "kill_switch_action",
    "persona_instrument_counterfactual",
    "trade_episode.opened",
    "trade_episode.updated",
    "trade_episode.closed",
    "trade_episode.unresolved",
    "trade_reflection.requested",
    "trade_reflection.completed",
    "trade_reflection.failed",
    "trade_lesson.proposed",
    "trade_lesson.reviewed",
    "trade_lesson.merged",
    "trade_lesson.quarantined",
)

CANONICAL_FILL_AND_REJECTION_EVENT_TYPES: tuple[str, ...] = (
    "paper_fill_simulated",
    "paper_futures_settled",
    "order_filled",
    "fill_received",
    "fill_observation",
    "order_partially_filled",
    "order_rejection",
    "order_rejection_simulated",
    "order_canceled",
    "order_cancelled",
)

SNAPSHOT_ANCHOR_EVENT_TYPES: tuple[str, ...] = (
    "pnl_snapshot",
    "drawdown_snapshot",
    "position_snapshot",
    "position_snapshot_received",
    "broker_position_snapshot",
)

LIFECYCLE_IDENTITY_EVENT_TYPES: tuple[str, ...] = (
    "signal_generation",
    "trade_decision",
    "risk_evaluation",
    "paper_order_simulated",
    "order_submitted",
    "order_accepted",
    "order_partially_filled",
    "paper_fill_simulated",
    "fill_received",
    "order_filled",
    "order_rejection",
    "order_rejection_simulated",
    "order_canceled",
    "order_cancelled",
    "position_snapshot",
    "position_snapshot_received",
    "broker_position_snapshot",
    "reconciliation_completed",
    "reconciliation_failed",
)


CREATE_HEARTBEAT_SUMMARY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS telemetry_heartbeat_hourly_summaries (
    runtime_id               TEXT             NOT NULL,
    hour_bucket              TIMESTAMPTZ      NOT NULL,
    heartbeat_count          BIGINT           NOT NULL CHECK (heartbeat_count > 0),
    first_heartbeat_at       TIMESTAMPTZ      NOT NULL,
    last_heartbeat_at        TIMESTAMPTZ      NOT NULL,
    largest_gap_seconds      DOUBLE PRECISION NOT NULL DEFAULT 0.0 CHECK (largest_gap_seconds >= 0.0),
    last_connectivity_status TEXT             NOT NULL,
    first_event_id           TEXT,
    last_event_id            TEXT,
    min_ingested_seq         BIGINT,
    max_ingested_seq         BIGINT,
    summarized_at            TIMESTAMPTZ      NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (runtime_id, hour_bucket),
    CHECK (first_heartbeat_at <= last_heartbeat_at)
);
"""

CREATE_HEARTBEAT_SUMMARY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_telemetry_heartbeat_hourly_summaries_hour_bucket
    ON telemetry_heartbeat_hourly_summaries (hour_bucket DESC);
"""


# ---------------------------------------------------------------------------
# Canonical inventories for AC-3 (ingested_seq consumers) and AC-4 (heartbeat readers)
# ---------------------------------------------------------------------------

INGESTED_SEQ_CONSUMERS: tuple[dict[str, Any], ...] = (
    {
        "consumer_id": "trade_journey_lifecycle_projector",
        "module": "services/trade_journey/lifecycle_projector.py",
        "symbol": "RelationalLifecycleProjector / PostgresLifecycleSource",
        "checkpoint_location": "PostgreSQL table trade_journey_projection.controller.checkpoint_seq (keyed by controller_id='canonical-lifecycle-projector', tenant_scope='*', environment_scope='*')",
        "reads_heartbeat_rows": True,
        "heartbeat_disposition": "Classified as ignored (not in LIFECYCLE_EVENT_TYPES); never creates or updates journeys or loop_runs.",
        "query_pattern": "SELECT ingested_seq, ingested_at, event_id, event_type, created_at, payload FROM telemetry_events WHERE ingested_seq > $1 ORDER BY ingested_seq ASC LIMIT $2",
        "gap_tolerance_mechanism": (
            "fetch_after uses strict inequality (ingested_seq > checkpoint) and records last_frontier_seq under _source_writer_fence; "
            "ProjectionStore.apply_batch_mutation and record_poll advance target_checkpoint_seq = max(target_checkpoint_seq, proven_frontier) across sequence holes."
        ),
        "stalls_on_ingested_seq_gaps": False,
    },
    {
        "consumer_id": "trade_journey_incremental_materializer",
        "module": "services/trade_journey/incremental_materializer.py",
        "symbol": "IncrementalLifecycleMaterializer",
        "checkpoint_location": "Per-aggregate last_ingested_seq persisted in trade_journey_projection.journeys.last_ingested_seq and trade_journey_projection.loop_runs.last_ingested_seq; governed by trade_journey_projection.controller.checkpoint_seq",
        "reads_heartbeat_rows": False,
        "heartbeat_disposition": "Only folds LIFECYCLE_EVENT_TYPES; heartbeat rows never enter aggregate materialization.",
        "query_pattern": "In-memory folding of rows supplied in monotonic ingested_seq order by RelationalLifecycleProjector",
        "gap_tolerance_mechanism": "Updates agg.last_ingested_seq = max(agg.last_ingested_seq, int(entry.get('ingested_seq') or 0)); never requires contiguous seq + 1.",
        "stalls_on_ingested_seq_gaps": False,
    },
    {
        "consumer_id": "trade_journey_projection_store",
        "module": "services/trade_journey/projection_store.py",
        "symbol": "ProjectionStore",
        "checkpoint_location": "PostgreSQL table trade_journey_projection.controller (columns checkpoint_seq, source_high_watermark, backlog_count)",
        "reads_heartbeat_rows": False,
        "heartbeat_disposition": "Persists ignored event_receipts only for live batches; retention never deletes rows with ingested_seq > checkpoint_seq.",
        "query_pattern": "WITH RECURSIVE contiguous(seq) ... plus proven_frontier = mutation.source_frontier_seq",
        "gap_tolerance_mechanism": "When proven_frontier >= curr_checkpoint_seq, target_checkpoint_seq = max(target_checkpoint_seq, proven_frontier), advancing cleanly across deleted heartbeat sequence gaps with backlog_count = max(0, next_source_high_watermark - target_checkpoint_seq).",
        "stalls_on_ingested_seq_gaps": False,
    },
    {
        "consumer_id": "telemetry_runtime_summary",
        "module": "services/telemetry/runtime_summary.py",
        "symbol": "RuntimeSummaryProjectionStore",
        "checkpoint_location": "Persisted JSON projection TELEMETRY_RUNTIME_SUMMARY_STORE (runtime_summaries.json + runtime_summaries.json.delta.jsonl)",
        "reads_heartbeat_rows": True,
        "heartbeat_disposition": "Folds latest heartbeat liveness, connectivity_status, member_virtual_books, and monotonic ledger_fill_count.",
        "query_pattern": "In-process project_event on ingest; startup backfill sorts lifecycle events by (created_at, sequence_no, ingested_seq)",
        "gap_tolerance_mechanism": "Uses ingested_seq only as a sort tie-breaker for _LIFECYCLE_IDENTITY_EVENT_TYPES; never steps by contiguous ingested_seq.",
        "stalls_on_ingested_seq_gaps": False,
    },
    {
        "consumer_id": "telemetry_ingest_service",
        "module": "services/telemetry/ingest_svc.py",
        "symbol": "TelemetryIngestService / build_postgres_write_fn / build_postgres_event_reader / CanonicalFillHistoryReader",
        "checkpoint_location": "PostgreSQL sequence public.telemetry_events_ingested_seq_seq for writes; dead_letter.jsonl and infrastructure_health_admissions.jsonl for ingest recovery; stateless readers",
        "reads_heartbeat_rows": True,
        "heartbeat_disposition": "Writes new heartbeats with nextval('telemetry_events_ingested_seq_seq'); binding reader returns most recent events (ORDER BY created_at DESC, ingested_seq DESC LIMIT 50).",
        "query_pattern": "ORDER BY created_at DESC, ingested_seq DESC LIMIT $n and seq_a < seq_o pair order checks",
        "gap_tolerance_mechanism": "Postgres sequence nextval is monotonic and unaffected by row deletion; read queries use ORDER BY ... DESC or strict inequality (< / >).",
        "stalls_on_ingested_seq_gaps": False,
    },
    {
        "consumer_id": "bff_trade_journey_projection_store",
        "module": "services/control-plane/bff/trade_journey_projection_store.py",
        "symbol": "PostgresTradeJourneyProjectionStore",
        "checkpoint_location": "Reads trade_journey_projection.controller.checkpoint_seq and stateless keyset pagination tokens over trade_journey_projection.journey_stages.source_ingested_seq",
        "reads_heartbeat_rows": False,
        "heartbeat_disposition": "Queries derived trade_journey_projection tables only (never public.telemetry_events heartbeat rows).",
        "query_pattern": "ORDER BY stage_ordinal ASC, event_sequence ASC, occurred_at ASC, source_ingested_seq ASC, source_event_id ASC LIMIT %s",
        "gap_tolerance_mechanism": "Keyset pagination uses tuple > comparison on source_ingested_seq; insensitive to sequence gaps.",
        "stalls_on_ingested_seq_gaps": False,
    },
    {
        "consumer_id": "agora_operational_readiness",
        "module": "services/control-plane/bff/agora/operational_readiness.py",
        "symbol": "AgoraOperationalReadinessAssembler._read_canonical_telemetry",
        "checkpoint_location": "Stateless on-demand query (no persistent cursor)",
        "reads_heartbeat_rows": False,
        "heartbeat_disposition": "Queries WHERE event_type = 'signal_generation' only.",
        "query_pattern": "SELECT payload, ingested_seq, ingested_at FROM telemetry_events WHERE event_type=%s ORDER BY ingested_seq DESC LIMIT 200",
        "gap_tolerance_mechanism": "Filters event_type='signal_generation' and orders by ingested_seq DESC LIMIT 200; unaffected by gaps.",
        "stalls_on_ingested_seq_gaps": False,
    },
    {
        "consumer_id": "hosted_lifecycle_probe",
        "module": "services/trade_journey/hosted_lifecycle_probe.py",
        "symbol": "AsyncpgTelemetrySource",
        "checkpoint_location": "Ephemeral per-probe baseline_high_watermark compared against trade_journey_projection.controller.checkpoint_seq",
        "reads_heartbeat_rows": False,
        "heartbeat_disposition": "Filters WHERE event_type = ANY(LIFECYCLE_EVENT_TYPES) which excludes heartbeat.",
        "query_pattern": "SELECT ingested_seq, ingested_at, event_id, event_type, created_at, payload FROM telemetry_events WHERE ingested_seq > $1 AND event_type = ANY($2::text[]) ORDER BY ingested_seq ASC LIMIT $3",
        "gap_tolerance_mechanism": "Uses ingested_seq > baseline and checks strict monotonicity (current['ingested_seq'] < following['ingested_seq']) across lifecycle stages.",
        "stalls_on_ingested_seq_gaps": False,
    },
    {
        "consumer_id": "hosted_lifecycle_stimulus",
        "module": "services/trade_journey/hosted_lifecycle_stimulus.py",
        "symbol": "HostedLifecycleStimulus",
        "checkpoint_location": "Stateless per-run query (no persistent cursor)",
        "reads_heartbeat_rows": False,
        "heartbeat_disposition": "Queries lifecycle event types (signal_generation, trade_decision, etc.) ordered by ingested_seq DESC.",
        "query_pattern": "SELECT ingested_seq, ingested_at, event_id, event_type, created_at, payload FROM telemetry_events WHERE ... ORDER BY ingested_seq DESC LIMIT %s",
        "gap_tolerance_mechanism": "Uses ORDER BY ingested_seq DESC LIMIT %s on lifecycle event types; unaffected by sequence gaps.",
        "stalls_on_ingested_seq_gaps": False,
    },
)


HEARTBEAT_READERS: tuple[dict[str, Any], ...] = (
    {
        "reader_id": "telemetry_runtime_summary",
        "module": "services/telemetry/runtime_summary.py",
        "surface": "RuntimeSummaryProjectionStore (GET /api/telemetry/runtime-summaries, GET /api/v1/telemetry/runtime/<runtime_id>/heartbeat)",
        "reads_raw_telemetry_events": False,
        "oldest_heartbeat_needed_seconds": 90,
        "oldest_heartbeat_needed_description": "Latest heartbeat per runtime for 90s staleness threshold (TELEMETRY_RUNTIME_HEARTBEAT_STALE_SECONDS=90) and latest member_virtual_books / connectivity_status; on cold rebuild from telemetry_events, only the latest heartbeat within the 3-day window is needed.",
    },
    {
        "reader_id": "quarter_equity_and_rankings",
        "module": "services/control-plane/bff/personas/service.py & scripts/project_console_data_rankings_to_bff_surfaces.py",
        "surface": "_pm12_quarter_metrics / project_rankings (/bff/rankings, persona league quarter returns)",
        "reads_raw_telemetry_events": False,
        "oldest_heartbeat_needed_seconds": 0,
        "oldest_heartbeat_needed_description": "Known quarter_equity.v1 observations are produced exclusively by event_type='pnl_snapshot' (retained permanently); rankings read pnl, sharpe_ratio, drawdown, fill_rate, avg_slippage_bps, total_trades from runtime summaries.",
    },
    {
        "reader_id": "trade_journey_lifecycle_projector",
        "module": "services/trade_journey/lifecycle_projector.py",
        "surface": "RelationalLifecycleProjector",
        "reads_raw_telemetry_events": True,
        "oldest_heartbeat_needed_seconds": 0,
        "oldest_heartbeat_needed_description": "Heartbeats are not in LIFECYCLE_EVENT_TYPES and are marked disposition='ignored' once ingested_seq <= controller.checkpoint_seq.",
    },
    {
        "reader_id": "incidents_and_threshold_sweep",
        "module": "services/incidents/consumer.py & services/evolution/threshold_sweep_worker.py",
        "surface": "IncidentConsumer / ThresholdSweepWorker",
        "reads_raw_telemetry_events": False,
        "oldest_heartbeat_needed_seconds": 90,
        "oldest_heartbeat_needed_description": "ThresholdSweepWorker checks summary['last_heartbeat_at'] freshness (90s window) via runtime summaries; IncidentConsumer processes infrastructure_health and threshold breach payloads, never raw historical heartbeats.",
    },
    {
        "reader_id": "reconciliation_drift",
        "module": "services/reconciliation-drift/main.py & services/reconciliation-drift/consumer.py",
        "surface": "_execute_scheduled_reconcile / fetch_runtime_summaries",
        "reads_raw_telemetry_events": False,
        "oldest_heartbeat_needed_seconds": 90,
        "oldest_heartbeat_needed_description": "Fetches current runtime summaries from /api/telemetry/runtime-summaries and evaluates active bindings using last_lifecycle_identity and committed_position_snapshot.",
    },
    {
        "reader_id": "management_read_models_and_bff",
        "module": "services/control-plane/bff/management_read_models/router.py & services/control-plane/bff/ports/lifecycle_telemetry_governance.py",
        "surface": "Management AI read models, /api/v1/telemetry, paper_fleet_reconciler",
        "reads_raw_telemetry_events": False,
        "oldest_heartbeat_needed_seconds": 90,
        "oldest_heartbeat_needed_description": "Reads current runtime summaries and recent telemetry events; paper_fleet_reconciler checks current runtime heartbeat liveness.",
    },
    {
        "reader_id": "telemetry_lineage_binding_reader",
        "module": "services/telemetry/ingest_svc.py & services/telemetry/lineage_read/service.py",
        "surface": "build_postgres_event_reader._fetch_binding_events",
        "reads_raw_telemetry_events": True,
        "oldest_heartbeat_needed_seconds": 3600,
        "oldest_heartbeat_needed_description": "Queries most recent LIMIT 50..100 events per binding (ORDER BY created_at DESC, ingested_seq DESC), covered well within the 3-day (259200s) raw retention window.",
    },
)


INFRASTRUCTURE_HEALTH_READERS: tuple[dict[str, Any], ...] = (
    {
        "reader_id": "telemetry_accepted_event_lookup",
        "module": "services/telemetry/main.py & services/telemetry/ingest_svc.py",
        "surface": "GET /api/telemetry/events/<event_id> (services/telemetry/main.py:1281, TelemetryIngestService.get_accepted_event)",
        "reads_raw_telemetry_events": True,
        "oldest_infra_health_needed_seconds": 86400,
        "oldest_infra_health_needed_description": (
            "24h admission ledger dedupe horizon (TELEMETRY_INFRASTRUCTURE_HEALTH_DEDUPE_HORIZON_SECONDS=86400) for unreferenced "
            "rows; event IDs referenced by any incident or postmortem are protected from deletion for as long as that incident or "
            "postmortem can be validated."
        ),
    },
    {
        "reader_id": "telemetry_lineage_event_trace",
        "module": "services/telemetry/main.py & services/telemetry/lineage_read/service.py",
        "surface": "GET /api/telemetry/lineage/events/<event_id>/trace (services/telemetry/main.py:1457, LineageReadService._hydrate_event)",
        "reads_raw_telemetry_events": True,
        "oldest_infra_health_needed_seconds": 86400,
        "oldest_infra_health_needed_description": (
            "Hydrates telemetry_event nodes from public.telemetry_events on trace lookup; event IDs referenced by any incident or "
            "postmortem are excluded from deletion indefinitely while that incident or postmortem exists."
        ),
    },
    {
        "reader_id": "incident_and_postmortem_reference_validation",
        "module": "services/incident/reference_validation.py, services/incidents/main.py, services/postmortems/main.py",
        "surface": "CanonicalReferenceValidator.validate_incident (services/incident/reference_validation.py:200-202, services/incidents/main.py:436, services/postmortems/main.py:198,:335)",
        "reads_raw_telemetry_events": True,
        "oldest_infra_health_needed_seconds": 0,
        "oldest_infra_health_needed_description": (
            "Validates at most 8 telemetry_event_ids per incident via GET /api/telemetry/events/<event_id> and "
            "GET /api/telemetry/lineage/events/<event_id>/trace; all event IDs referenced by incident.incident_cases, "
            "incident.postmortems, on-disk incident stores, or BFF incident mappings/outbox are protected from deletion."
        ),
    },
    {
        "reader_id": "incidents_consume_infrastructure_health",
        "module": "services/incidents/main.py & services/incidents/consumer.py",
        "surface": "POST /api/incidents/consume-infrastructure-health (services/incidents/main.py:606) & InfrastructureHealthIncidentConsumer (services/incidents/consumer.py:482)",
        "reads_raw_telemetry_events": False,
        "oldest_infra_health_needed_seconds": 0,
        "oldest_infra_health_needed_description": (
            "Route-specific consumer validates pantheon.infrastructure-incident/1 payloads directly without calling "
            "CanonicalReferenceValidator and records source_event_id in trace_id, telemetry_event_ids, and evidence_summary on the IncidentCase."
        ),
    },
    {
        "reader_id": "bff_downstream_health_monitor",
        "module": "services/control-plane/bff/downstream_health_monitor.py",
        "surface": "DownstreamHealthMonitor (services/control-plane/bff/downstream_health_monitor.py:2868,:3126,:3679)",
        "reads_raw_telemetry_events": False,
        "oldest_infra_health_needed_seconds": 86400,
        "oldest_infra_health_needed_description": (
            "Producer that stores opening_event_id and resolving event_id in BFF SQLite incident_mappings/delivery_outbox and "
            "forwards them as source_event_id and telemetry_event_ids to incident-svc within its 24h retry window."
        ),
    },
    {
        "reader_id": "trade_journey_lifecycle_projector",
        "module": "services/trade_journey/lifecycle_projector.py",
        "surface": "RelationalLifecycleProjector",
        "reads_raw_telemetry_events": True,
        "oldest_infra_health_needed_seconds": 0,
        "oldest_infra_health_needed_description": (
            "infrastructure_health is not in LIFECYCLE_EVENT_TYPES and is marked disposition='ignored' once "
            "ingested_seq <= controller.checkpoint_seq; retention never deletes rows above safe_ingested_seq."
        ),
    },
)


TELEMETRY_EVENTS_READERS: tuple[dict[str, Any], ...] = (
    {
        "reader_id": "trade_journey_lifecycle_projector",
        "module": "services/trade_journey/lifecycle_projector.py",
        "surface": "RelationalLifecycleProjector / PostgresLifecycleSource",
        "reads_raw_telemetry_events": True,
        "oldest_telemetry_events_needed_seconds": 0,
        "oldest_telemetry_events_needed_description": (
            "Reads public.telemetry_events by ingested_seq > controller.checkpoint_seq and materializes "
            "journeys and loop_runs into trade_journey_projection; 14-day retention never deletes rows "
            "with ingested_seq > safe_ingested_seq."
        ),
    },
    {
        "reader_id": "telemetry_runtime_summary_and_fill_history",
        "module": "services/telemetry/runtime_summary.py & services/telemetry/ingest_svc.py",
        "surface": "RuntimeSummaryProjectionStore & CanonicalFillHistoryReader",
        "reads_raw_telemetry_events": True,
        "oldest_telemetry_events_needed_seconds": 0,
        "oldest_telemetry_events_needed_description": (
            "Latest per-runtime snapshot anchors, distinct quarter_equity.v1 pnl_snapshot observations, "
            "and canonical fill/governance/deploy/decision/episode rows are preserved permanently so "
            "runtime summaries and fill history are invariant across 14-day pruning."
        ),
    },
    {
        "reader_id": "trade_episode_projection_store",
        "module": "services/telemetry/trade_episode_projection.py",
        "surface": "TradeEpisodeProjectionStore",
        "reads_raw_telemetry_events": True,
        "oldest_telemetry_events_needed_seconds": 0,
        "oldest_telemetry_events_needed_description": (
            "All trade_episode.*, trade_reflection.*, trade_lesson.* rows and any telemetry row carrying "
            "trade_episode_id (or reversal episode IDs) are preserved permanently."
        ),
    },
    {
        "reader_id": "incident_and_postmortem_reference_validation",
        "module": "services/incident/reference_validation.py, services/incidents/main.py, services/postmortems/main.py",
        "surface": "CanonicalReferenceValidator.validate_incident & LineageReadService",
        "reads_raw_telemetry_events": True,
        "oldest_telemetry_events_needed_seconds": 0,
        "oldest_telemetry_events_needed_description": (
            "All event IDs referenced by incident.incident_cases, incident.postmortems, on-disk incidents.json, "
            "or BFF downstream_health.sqlite3 are protected from 14-day pruning indefinitely."
        ),
    },
    {
        "reader_id": "agora_operational_readiness",
        "module": "services/control-plane/bff/agora/operational_readiness.py",
        "surface": "AgoraOperationalReadinessAssembler._read_canonical_telemetry",
        "reads_raw_telemetry_events": True,
        "oldest_telemetry_events_needed_seconds": 86400,
        "oldest_telemetry_events_needed_description": (
            "Queries recent signal_generation rows (ORDER BY ingested_seq DESC LIMIT 200), well within the "
            "14-day (1209600s) retention window."
        ),
    },
    {
        "reader_id": "hosted_lifecycle_probe_and_stimulus",
        "module": "services/trade_journey/hosted_lifecycle_probe.py & services/trade_journey/hosted_lifecycle_stimulus.py",
        "surface": "AsyncpgTelemetrySource / HostedLifecycleStimulus",
        "reads_raw_telemetry_events": True,
        "oldest_telemetry_events_needed_seconds": 86400,
        "oldest_telemetry_events_needed_description": (
            "Probes recent lifecycle events emitted during or immediately preceding a probe run, well within "
            "the 14-day (1209600s) retention window."
        ),
    },
    {
        "reader_id": "telemetry_lineage_binding_reader",
        "module": "services/telemetry/ingest_svc.py & services/telemetry/lineage_read/service.py",
        "surface": "build_postgres_event_reader._fetch_binding_events",
        "reads_raw_telemetry_events": True,
        "oldest_telemetry_events_needed_seconds": 86400,
        "oldest_telemetry_events_needed_description": (
            "Queries recent LIMIT 50..100 events per binding; canonical fill history rows for active positions "
            "are preserved permanently."
        ),
    },
)


_SUMMARY_EVENT_ID_KV_RE = re.compile(
    r"(?:source_event_id|telemetry_event_id|opening_event_id|resolving_event_id)=([^;\s,]+)"
)
_SUMMARY_EVENT_IDS_LIST_RE = re.compile(r"telemetry_event_ids=([^;\s]+)")


class HeartbeatRetentionError(RuntimeError):
    """Base error for heartbeat retention failures."""


class HeartbeatRetentionReaderBlocker(HeartbeatRetentionError):
    """Raised when a heartbeat reader requires raw rows older than the retention window."""

    def __init__(
        self,
        reader_id: str,
        required_seconds: float,
        retention_window_seconds: float,
        module: str = "",
    ) -> None:
        self.reader_id = reader_id
        self.required_seconds = float(required_seconds)
        self.retention_window_seconds = float(retention_window_seconds)
        self.module = module
        super().__init__(
            f"Heartbeat retention blocked by reader {reader_id!r} ({module}): "
            f"requires raw heartbeats up to {required_seconds:.0f}s old, exceeding "
            f"retention window {retention_window_seconds:.0f}s"
        )


def parse_retention_days(raw: Any = None) -> float:
    """Parse and validate PANTHEON_TELEMETRY_HEARTBEAT_RAW_RETENTION_DAYS (default 3)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raw = os.getenv(RAW_RETENTION_DAYS_ENV, str(int(DEFAULT_RAW_RETENTION_DAYS)))
    if isinstance(raw, bool):
        raise ValueError(f"{RAW_RETENTION_DAYS_ENV} must be a positive number")
    try:
        days = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{RAW_RETENTION_DAYS_ENV} must be a positive number, got {raw!r}") from exc
    if not math.isfinite(days) or days <= 0.0:
        raise ValueError(f"{RAW_RETENTION_DAYS_ENV} must be > 0, got {raw!r}")
    return days


def parse_infra_health_retention_days(raw: Any = None) -> float:
    """Parse and validate PANTHEON_TELEMETRY_INFRA_HEALTH_RETENTION_DAYS (default 7)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raw = os.getenv(
            INFRA_HEALTH_RETENTION_DAYS_ENV,
            str(int(DEFAULT_INFRA_HEALTH_RETENTION_DAYS)),
        )
    if isinstance(raw, bool):
        raise ValueError(f"{INFRA_HEALTH_RETENTION_DAYS_ENV} must be a positive number")
    try:
        days = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{INFRA_HEALTH_RETENTION_DAYS_ENV} must be a positive number, got {raw!r}"
        ) from exc
    if not math.isfinite(days) or days <= 0.0:
        raise ValueError(f"{INFRA_HEALTH_RETENTION_DAYS_ENV} must be > 0, got {raw!r}")
    return days


def parse_state_snapshot_retention_days(raw: Any = None) -> float:
    """Parse and validate PANTHEON_TELEMETRY_STATE_SNAPSHOT_RETENTION_DAYS (default 3)."""
    if raw is None:
        raw = os.getenv(
            STATE_SNAPSHOT_RETENTION_DAYS_ENV, str(int(DEFAULT_STATE_SNAPSHOT_RETENTION_DAYS))
        )
    try:
        days = float(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError(
            f"{STATE_SNAPSHOT_RETENTION_DAYS_ENV} must be a positive number, got {raw!r}"
        ) from None
    if not math.isfinite(days) or days <= 0:
        raise ValueError(f"{STATE_SNAPSHOT_RETENTION_DAYS_ENV} must be > 0, got {raw!r}")
    return days


def parse_telemetry_events_retention_days(raw: Any = None) -> float:
    """Parse and validate PANTHEON_TELEMETRY_EVENTS_RETENTION_DAYS (default 14)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raw = os.getenv(
            TELEMETRY_EVENTS_RETENTION_DAYS_ENV,
            str(int(DEFAULT_TELEMETRY_EVENTS_RETENTION_DAYS)),
        )
    if isinstance(raw, bool):
        raise ValueError(f"{TELEMETRY_EVENTS_RETENTION_DAYS_ENV} must be a positive number")
    try:
        days = float(str(raw).strip() if isinstance(raw, str) else raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{TELEMETRY_EVENTS_RETENTION_DAYS_ENV} must be a positive number, got {raw!r}"
        ) from exc
    if not math.isfinite(days) or days <= 0.0:
        raise ValueError(f"{TELEMETRY_EVENTS_RETENTION_DAYS_ENV} must be > 0, got {raw!r}")
    return days


def check_readers_against_retention_window(
    retention_days: float,
    readers: Sequence[Mapping[str, Any]] = HEARTBEAT_READERS,
) -> dict[str, Any]:
    """Verify every heartbeat reader's required raw heartbeat age fits within retention_days.

    Raises HeartbeatRetentionReaderBlocker if any reader needs raw heartbeats
    older than the retention window.
    """
    window_seconds = float(retention_days) * 86400.0
    max_required = 0.0
    checked: list[dict[str, Any]] = []
    for reader in readers:
        reader_id = str(reader.get("reader_id") or "unknown")
        module = str(reader.get("module") or "")
        required_seconds = float(reader.get("oldest_heartbeat_needed_seconds") or 0.0)
        max_required = max(max_required, required_seconds)
        checked.append(
            {
                "reader_id": reader_id,
                "module": module,
                "oldest_heartbeat_needed_seconds": required_seconds,
                "within_retention_window": required_seconds <= window_seconds,
            }
        )
        if required_seconds > window_seconds:
            raise HeartbeatRetentionReaderBlocker(
                reader_id=reader_id,
                required_seconds=required_seconds,
                retention_window_seconds=window_seconds,
                module=module,
            )
    return {
        "all_readers_within_window": True,
        "retention_days": float(retention_days),
        "retention_window_seconds": window_seconds,
        "max_reader_required_age_seconds": max_required,
        "blocking_reader": None,
        "readers": checked,
    }


def check_infra_health_readers_against_retention_window(
    retention_days: float,
    readers: Sequence[Mapping[str, Any]] = INFRASTRUCTURE_HEALTH_READERS,
) -> dict[str, Any]:
    """Verify every infrastructure_health reader's unreferenced age fits within retention_days.

    Raises HeartbeatRetentionReaderBlocker if any reader requires unreferenced
    infrastructure_health rows older than the configured retention window.
    """
    window_seconds = float(retention_days) * 86400.0
    max_required = 0.0
    checked: list[dict[str, Any]] = []
    for reader in readers:
        reader_id = str(reader.get("reader_id") or "unknown")
        module = str(reader.get("module") or "")
        raw_required = reader.get("oldest_infra_health_needed_seconds")
        if raw_required is None:
            raw_required = reader.get("oldest_heartbeat_needed_seconds")
        required_seconds = float(raw_required or 0.0)
        max_required = max(max_required, required_seconds)
        checked.append(
            {
                "reader_id": reader_id,
                "module": module,
                "oldest_infra_health_needed_seconds": required_seconds,
                "within_retention_window": required_seconds <= window_seconds,
            }
        )
        if required_seconds > window_seconds:
            raise HeartbeatRetentionReaderBlocker(
                reader_id=reader_id,
                required_seconds=required_seconds,
                retention_window_seconds=window_seconds,
                module=module,
            )
    return {
        "all_readers_within_window": True,
        "retention_days": float(retention_days),
        "retention_window_seconds": window_seconds,
        "max_reader_required_age_seconds": max_required,
        "blocking_reader": None,
        "readers": checked,
    }


def check_telemetry_events_readers_against_retention_window(
    retention_days: float,
    readers: Sequence[Mapping[str, Any]] = TELEMETRY_EVENTS_READERS,
) -> dict[str, Any]:
    """Verify every telemetry_events reader's unreferenced age fits within retention_days.

    Raises HeartbeatRetentionReaderBlocker if any reader requires unreferenced
    non-permanent telemetry_events rows older than the configured retention window.
    """
    window_seconds = float(retention_days) * 86400.0
    max_required = 0.0
    checked: list[dict[str, Any]] = []
    for reader in readers:
        reader_id = str(reader.get("reader_id") or "unknown")
        module = str(reader.get("module") or "")
        raw_required = reader.get("oldest_telemetry_events_needed_seconds")
        if raw_required is None:
            raw_required = reader.get("oldest_infra_health_needed_seconds")
        if raw_required is None:
            raw_required = reader.get("oldest_heartbeat_needed_seconds")
        required_seconds = float(raw_required or 0.0)
        max_required = max(max_required, required_seconds)
        checked.append(
            {
                "reader_id": reader_id,
                "module": module,
                "oldest_telemetry_events_needed_seconds": required_seconds,
                "within_retention_window": required_seconds <= window_seconds,
            }
        )
        if required_seconds > window_seconds:
            raise HeartbeatRetentionReaderBlocker(
                reader_id=reader_id,
                required_seconds=required_seconds,
                retention_window_seconds=window_seconds,
                module=module,
            )
    return {
        "all_readers_within_window": True,
        "retention_days": float(retention_days),
        "retention_window_seconds": window_seconds,
        "max_reader_required_age_seconds": max_required,
        "blocking_reader": None,
        "readers": checked,
    }


def _validate_qualified_table_identifier(table: str) -> str:
    clean = str(table or "").strip()
    parts = clean.split(".")
    if not parts or len(parts) > 2 or any(not part.isidentifier() for part in parts):
        raise ValueError(f"Invalid table identifier: {table!r}")
    return clean


def extract_referenced_event_ids_from_record(record: Mapping[str, Any] | str) -> set[str]:
    """Extract all telemetry event IDs referenced by an IncidentCase or Postmortem record."""
    if isinstance(record, str):
        try:
            record = json.loads(record)
        except ValueError:
            return set()
    if not isinstance(record, Mapping):
        return set()

    event_ids: set[str] = set()

    def _add_token(value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        if text.startswith(("merged_incident_id:", "resolution_reason:")):
            return
        if text.startswith("merged_event:"):
            parts = text.split(":", 2)
            if len(parts) == 3 and parts[2].strip():
                event_ids.add(parts[2].strip())
            return
        event_ids.add(text)

    raw_list = record.get("telemetry_event_ids")
    if isinstance(raw_list, (list, tuple, set)):
        for item in raw_list:
            _add_token(item)

    for key in (
        "trace_id",
        "source_event_id",
        "telemetry_event_id",
        "opening_event_id",
        "resolving_event_id",
        "last_event_id",
    ):
        val = record.get(key)
        if isinstance(val, str) and val.strip():
            _add_token(val)

    for summary_key in ("evidence_summary", "incident_evidence_summary"):
        summary = str(record.get(summary_key) or "")
        if not summary:
            continue
        for match in _SUMMARY_EVENT_ID_KV_RE.finditer(summary):
            _add_token(match.group(1))
        for match in _SUMMARY_EVENT_IDS_LIST_RE.finditer(summary):
            for part in match.group(1).split(","):
                _add_token(part)

    return event_ids


async def resolve_protected_infrastructure_health_event_ids(
    conn: Any,
    *,
    incident_cases_table: str = DEFAULT_INCIDENT_CASES_TABLE,
    postmortems_table: str = DEFAULT_POSTMORTEMS_TABLE,
    incident_store_paths: Sequence[str | Path] | None = None,
    bff_health_sqlite_path: str | Path | None = None,
    extra_protected_event_ids: Iterable[str] | None = None,
) -> set[str]:
    """Return all infrastructure_health event IDs referenced by incidents, postmortems, or BFF state.

    Checks:
    1. PostgreSQL ``incident.incident_cases`` and ``incident.postmortems`` tables (when present).
    2. Configured or discovered JSON IncidentStore files (``incidents.json``).
    3. Configured or discovered BFF downstream health SQLite database (``incident_mappings`` and
       ``delivery_outbox``).
    4. Any explicit ``extra_protected_event_ids`` supplied by the caller.
    """
    protected: set[str] = set()

    if extra_protected_event_ids is not None:
        for item in extra_protected_event_ids:
            clean = str(item or "").strip()
            if clean:
                protected.add(clean)

    for raw_table in (incident_cases_table, postmortems_table):
        if not raw_table:
            continue
        table_name = _validate_qualified_table_identifier(raw_table)
        regclass = await conn.fetchval("SELECT to_regclass($1)", table_name)
        if regclass is None:
            continue
        rows = await conn.fetch(f"SELECT payload FROM {table_name}")
        for row in rows:
            protected.update(extract_referenced_event_ids_from_record(row["payload"]))

    candidate_json_paths: list[Path] = []
    if incident_store_paths is not None:
        for p in incident_store_paths:
            if p:
                candidate_json_paths.append(Path(p))
    else:
        for env_dir in ("INCIDENTS_DATA_DIR", "POSTMORTEMS_DATA_DIR"):
            raw_dir = os.getenv(env_dir, "").strip()
            if raw_dir:
                candidate_json_paths.append(Path(raw_dir) / "incidents.json")
        raw_file = os.getenv("PANTHEON_BFF_INCIDENT_STORE", "").strip()
        if raw_file:
            candidate_json_paths.append(Path(raw_file))

    seen_paths: set[Path] = set()
    for json_path in candidate_json_paths:
        if json_path in seen_paths or not json_path.is_file():
            continue
        seen_paths.add(json_path)
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, Mapping):
            for section in ("incidents", "postmortems"):
                items = data.get(section)
                if isinstance(items, list):
                    for rec in items:
                        if isinstance(rec, Mapping):
                            protected.update(extract_referenced_event_ids_from_record(rec))

    sqlite_candidates: list[Path] = []
    if bff_health_sqlite_path:
        sqlite_candidates.append(Path(bff_health_sqlite_path))
    else:
        for env_sqlite in ("PANTHEON_BFF_DOWNSTREAM_HEALTH_DB", "PANTHEON_BFF_HEALTH_DB_PATH"):
            raw_sq = os.getenv(env_sqlite, "").strip()
            if raw_sq:
                sqlite_candidates.append(Path(raw_sq))

    for sq_path in sqlite_candidates:
        if not sq_path.is_file():
            continue
        try:
            with sqlite3.connect(f"file:{sq_path}?mode=ro", uri=True, timeout=1.0) as sq_conn:
                cur = sq_conn.cursor()
                for query in (
                    "SELECT opening_event_id, last_event_id FROM incident_mappings",
                    "SELECT event_id FROM delivery_outbox WHERE channel IN ('incident_open', 'incident_resolve')",
                ):
                    try:
                        for sq_row in cur.execute(query).fetchall():
                            for col in sq_row:
                                clean_col = str(col or "").strip()
                                if clean_col and not clean_col.startswith("orphan-reconcile:"):
                                    protected.add(clean_col)
                    except sqlite3.Error:
                        continue
        except Exception:  # noqa: BLE001
            continue

    return protected


resolve_protected_telemetry_event_ids = resolve_protected_infrastructure_health_event_ids


async def resolve_protected_telemetry_anchor_event_ids(
    conn: Any,
    *,
    timeout: float | None = None,
) -> set[str]:
    """Return per-runtime snapshot, valuation, and boundary anchor event IDs in public.telemetry_events.

    Preserves across 14-day pruning (for non-``tenant-capacity`` tenants):
    1. Oldest and newest event per ``(tenant_key, runtime_key, binding_key)`` so binding
       first/latest event boundaries and ``last_event_id`` stay invariant.
    2. Newest lifecycle-identity event per ``(tenant_key, runtime_key, binding_key)`` so
       ``last_lifecycle_identity`` and top-level trace/correlation fields stay invariant.
    3. Latest ``pnl_snapshot``, ``drawdown_snapshot``, ``position_snapshot``,
       ``position_snapshot_received``, and ``broker_position_snapshot`` per
       ``(tenant_key, runtime_key, binding_key, event_type)`` (both by ``(created_at, ingested_seq)``
       and by explicit metric ``as_of``).
    4. Earliest ``pnl_snapshot`` per distinct ``(tenant_key, runtime_key, binding_key,
       valuation_as_of, pnl, portfolio_value)`` observation so ``quarter_equity_observations``
       and ``pool_risk_snapshots`` stay invariant while duplicate repeated ``pnl_snapshot``
       rows are pruned.
    5. Latest ``reconciliation_completed`` / ``reconciliation_failed`` row and latest
       ``member_virtual_books`` carrier per ``(tenant_key, runtime_key, binding_key)``.
    """
    rows = await conn.fetch(
        """
        WITH scoped AS (
            SELECT
                e.event_id,
                e.event_type,
                e.created_at,
                e.ingested_seq,
                COALESCE(
                    NULLIF(trim(e.payload->>'tenant_id'), ''),
                    NULLIF(trim(e.payload->'metadata'->>'tenant_id'), ''),
                    'default'
                ) AS tenant_key,
                COALESCE(
                    NULLIF(trim(e.payload->>'runtime_id'), ''),
                    NULLIF(trim(e.payload->'metadata'->>'runtime_id'), ''),
                    'unknown'
                ) AS runtime_key,
                COALESCE(
                    NULLIF(trim(e.payload->>'binding_id'), ''),
                    NULLIF(trim(e.payload->>'runtime_binding_id'), ''),
                    NULLIF(trim(e.payload->'metadata'->>'binding_id'), ''),
                    NULLIF(trim(e.payload->'metadata'->>'runtime_binding_id'), ''),
                    ''
                ) AS binding_key,
                COALESCE(
                    NULLIF(trim(e.payload->>'pnl_as_of'), ''),
                    NULLIF(trim(e.payload->>'drawdown_as_of'), ''),
                    NULLIF(trim(e.payload->'metrics'->>'pnl_as_of'), ''),
                    NULLIF(trim(e.payload->'metrics'->>'drawdown_as_of'), ''),
                    NULLIF(trim(e.payload->'metadata'->>'valuation_as_of'), ''),
                    NULLIF(trim(e.payload->>'created_at'), ''),
                    e.created_at::text
                ) AS metric_as_of_key,
                COALESCE(
                    NULLIF(trim(e.payload->>'pnl_as_of'), ''),
                    NULLIF(trim(e.payload->'metrics'->>'pnl_as_of'), ''),
                    NULLIF(trim(e.payload->'metadata'->>'valuation_as_of'), ''),
                    NULLIF(trim(e.payload->>'created_at'), ''),
                    e.created_at::text
                ) AS valuation_as_of_key,
                COALESCE(e.payload->'metrics'->>'pnl', '') AS pnl_val,
                COALESCE(e.payload->'metrics'->>'portfolio_value', '') AS portfolio_val,
                COALESCE(
                    NULLIF(trim(e.payload->>'trace_id'), ''),
                    NULLIF(trim(e.payload->'metadata'->>'trace_id'), ''),
                    NULLIF(trim(e.payload->'correlation_envelope'->>'trace_id'), '')
                ) AS trace_key,
                (jsonb_typeof(e.payload->'metadata'->'member_virtual_books') = 'array') AS has_member_books
            FROM public.telemetry_events e
            WHERE e.event_type NOT IN ('heartbeat', 'infrastructure_health', 'runtime_state_snapshot')
              AND COALESCE(
                  NULLIF(trim(e.payload->>'tenant_id'), ''),
                  NULLIF(trim(e.payload->'metadata'->>'tenant_id'), ''),
                  'default'
              ) <> 'tenant-capacity'
        )
        SELECT event_id FROM (
            SELECT DISTINCT ON (tenant_key, runtime_key, binding_key) event_id
            FROM scoped
            ORDER BY tenant_key, runtime_key, binding_key, created_at ASC, ingested_seq ASC, event_id ASC
        ) oldest_per_binding
        UNION
        SELECT event_id FROM (
            SELECT DISTINCT ON (tenant_key, runtime_key, binding_key) event_id
            FROM scoped
            ORDER BY tenant_key, runtime_key, binding_key, created_at DESC, ingested_seq DESC, event_id DESC
        ) newest_per_binding
        UNION
        SELECT event_id FROM (
            SELECT DISTINCT ON (tenant_key, runtime_key, binding_key) event_id
            FROM scoped
            WHERE trace_key IS NOT NULL
              AND event_type = ANY($1::text[])
            ORDER BY tenant_key, runtime_key, binding_key, created_at DESC, ingested_seq DESC, event_id DESC
        ) newest_lifecycle_per_binding
        UNION
        SELECT event_id FROM (
            SELECT DISTINCT ON (tenant_key, runtime_key, binding_key, event_type) event_id
            FROM scoped
            WHERE event_type = ANY($2::text[])
            ORDER BY tenant_key, runtime_key, binding_key, event_type, created_at DESC, ingested_seq DESC, event_id DESC
        ) latest_snapshot_by_created
        UNION
        SELECT event_id FROM (
            SELECT DISTINCT ON (tenant_key, runtime_key, binding_key, event_type) event_id
            FROM scoped
            WHERE event_type = ANY($2::text[])
            ORDER BY tenant_key, runtime_key, binding_key, event_type, metric_as_of_key DESC, created_at DESC, ingested_seq DESC, event_id DESC
        ) latest_snapshot_by_as_of
        UNION
        SELECT event_id FROM (
            SELECT DISTINCT ON (tenant_key, runtime_key, binding_key, valuation_as_of_key, pnl_val, portfolio_val) event_id
            FROM scoped
            WHERE event_type = 'pnl_snapshot'
            ORDER BY tenant_key, runtime_key, binding_key, valuation_as_of_key, pnl_val, portfolio_val, created_at ASC, ingested_seq ASC, event_id ASC
        ) distinct_pnl_observations
        UNION
        SELECT event_id FROM (
            SELECT DISTINCT ON (tenant_key, runtime_key, binding_key) event_id
            FROM scoped
            WHERE event_type IN ('reconciliation_completed', 'reconciliation_failed')
            ORDER BY tenant_key, runtime_key, binding_key, created_at DESC, ingested_seq DESC, event_id DESC
        ) latest_reconciliation_per_binding
        UNION
        SELECT event_id FROM (
            SELECT DISTINCT ON (tenant_key, runtime_key, binding_key) event_id
            FROM scoped
            WHERE has_member_books
            ORDER BY tenant_key, runtime_key, binding_key, created_at DESC, ingested_seq DESC, event_id DESC
        ) latest_member_books_per_binding
        """,
        list(LIFECYCLE_IDENTITY_EVENT_TYPES),
        list(SNAPSHOT_ANCHOR_EVENT_TYPES),
        timeout=timeout,
    )
    return {str(r["event_id"]) for r in rows if r["event_id"]}


def _telemetry_events_14d_prunable_where_sql(
    alias: str = "e",
    *,
    cutoff_param: str = "$1",
    safe_seq_param: str = "$2",
    protected_ids_param: str = "$3",
    permanent_types_param: str = "$4",
    canonical_fill_types_param: str = "$5",
) -> str:
    """Return the SQL WHERE predicate identifying 14-day prunable public.telemetry_events rows."""
    a = alias
    return f"""{a}.event_type NOT IN ('heartbeat', 'infrastructure_health', 'runtime_state_snapshot')
      AND {a}.created_at < {cutoff_param}
      AND {a}.ingested_seq <= {safe_seq_param}
      AND NOT ({a}.event_id = ANY({protected_ids_param}::text[]))
      AND NOT ({a}.event_type = ANY({permanent_types_param}::text[]))
      AND COALESCE(
          NULLIF(trim({a}.payload->>'trade_episode_id'), ''),
          NULLIF(trim({a}.payload->'metadata'->>'trade_episode_id'), ''),
          NULLIF(trim({a}.payload->'payload'->>'trade_episode_id'), ''),
          NULLIF(trim({a}.payload->'metadata'->'reversal'->>'closed_episode_id'), ''),
          NULLIF(trim({a}.payload->'metadata'->'reversal'->>'opened_episode_id'), '')
      ) IS NULL
      AND (
          COALESCE(
              NULLIF(trim({a}.payload->>'tenant_id'), ''),
              NULLIF(trim({a}.payload->'metadata'->>'tenant_id'), ''),
              'default'
          ) = 'tenant-capacity'
          OR (
              NOT ({a}.event_type = ANY({canonical_fill_types_param}::text[]))
              AND NOT (
                  {a}.event_type = 'order_submitted'
                  AND COALESCE(
                      NULLIF(trim({a}.payload->'metrics'->>'order_quantity'), ''),
                      NULLIF(trim({a}.payload->'metrics'->>'requested_quantity'), ''),
                      NULLIF(trim({a}.payload->>'quantity'), ''),
                      NULLIF(trim({a}.payload->'metadata'->>'submission_sizing_evidence'), '')
                  ) IS NOT NULL
              )
              AND NOT (
                  {a}.event_type = 'paper_order_simulated'
                  AND LOWER(COALESCE({a}.payload->'metrics'->>'submitted_to_broker', '')) IN ('1', 'true')
                  AND COALESCE(
                      NULLIF(trim({a}.payload->'metrics'->>'order_quantity'), ''),
                      NULLIF(trim({a}.payload->'metrics'->>'requested_quantity'), ''),
                      NULLIF(trim({a}.payload->>'quantity'), '')
                  ) IS NOT NULL
              )
              AND NOT (
                  {a}.event_type = 'trade_decision'
                  AND (
                      (
                          jsonb_typeof({a}.payload->'metadata') = 'object'
                          AND (
                              {a}.payload->'metadata' ? 'expression_decision'
                              OR {a}.payload->'metadata' ? 'pre_trade_sizing'
                          )
                      )
                      OR COALESCE(
                          NULLIF(trim({a}.payload->'metadata'->>'instrument_id'), ''),
                          NULLIF(trim({a}.payload->>'instrument_id'), '')
                      ) IS NOT NULL
                  )
              )
          )
      )"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_rfc3339(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _coerce_utc(dt: datetime | str) -> datetime:
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    text = str(dt).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def truncate_utc_hour(dt: datetime | str) -> datetime:
    """Truncate a timestamp to the start of its UTC hour."""
    utc_dt = _coerce_utc(dt)
    return utc_dt.replace(minute=0, second=0, microsecond=0)


def extract_heartbeat_connectivity_status(payload: Mapping[str, Any] | str) -> str:
    """Extract connectivity_status from a canonical heartbeat payload."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    if not isinstance(payload, Mapping):
        return "connected"
    metadata = payload.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    rt_hb = metadata.get("runtime_heartbeat")
    rt_hb = rt_hb if isinstance(rt_hb, Mapping) else {}
    for candidate in (
        rt_hb.get("connectivity_status"),
        metadata.get("connectivity_status"),
        payload.get("connectivity_status"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().lower()
    return "connected"


def extract_heartbeat_runtime_id(payload: Mapping[str, Any] | str) -> str:
    """Extract runtime_id from a canonical heartbeat payload."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    if not isinstance(payload, Mapping):
        return "unknown"
    metadata = payload.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    for candidate in (payload.get("runtime_id"), metadata.get("runtime_id")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "unknown"


@dataclass(frozen=True)
class HourlyHeartbeatSummary:
    """In-memory representation of a row in telemetry_heartbeat_hourly_summaries."""

    runtime_id: str
    hour_bucket: datetime
    heartbeat_count: int
    first_heartbeat_at: datetime
    last_heartbeat_at: datetime
    largest_gap_seconds: float
    last_connectivity_status: str
    first_event_id: str | None = None
    last_event_id: str | None = None
    min_ingested_seq: int | None = None
    max_ingested_seq: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "hour_bucket": _format_rfc3339(self.hour_bucket),
            "heartbeat_count": self.heartbeat_count,
            "first_heartbeat_at": _format_rfc3339(self.first_heartbeat_at),
            "last_heartbeat_at": _format_rfc3339(self.last_heartbeat_at),
            "largest_gap_seconds": round(float(self.largest_gap_seconds), 6),
            "last_connectivity_status": self.last_connectivity_status,
            "first_event_id": self.first_event_id,
            "last_event_id": self.last_event_id,
            "min_ingested_seq": self.min_ingested_seq,
            "max_ingested_seq": self.max_ingested_seq,
        }


def compute_hourly_summaries_from_rows(
    rows: Iterable[Mapping[str, Any]],
) -> list[HourlyHeartbeatSummary]:
    """Compute per-(runtime_id, hour_bucket) summaries from raw heartbeat rows."""
    grouped: dict[tuple[str, datetime], list[tuple[datetime, int, str, str]]] = {}
    for row in rows:
        if str(row.get("event_type") or HEARTBEAT_EVENT_TYPE) != HEARTBEAT_EVENT_TYPE:
            continue
        created_at = _coerce_utc(row["created_at"])
        hour_bucket = truncate_utc_hour(created_at)
        payload = row.get("payload") or row
        runtime_id = str(row.get("runtime_id") or "").strip() or extract_heartbeat_runtime_id(payload)
        conn_status = (
            str(row.get("connectivity_status") or "").strip().lower()
            or extract_heartbeat_connectivity_status(payload)
        )
        ingested_seq = int(row.get("ingested_seq") or 0)
        event_id = str(row.get("event_id") or "")
        grouped.setdefault((runtime_id, hour_bucket), []).append(
            (created_at, ingested_seq, event_id, conn_status)
        )

    summaries: list[HourlyHeartbeatSummary] = []
    for (runtime_id, hour_bucket), items in sorted(
        grouped.items(), key=lambda entry: (entry[0][0], entry[0][1])
    ):
        items.sort(key=lambda item: (item[0], item[1], item[2]))
        largest_gap = 0.0
        for prev, curr in zip(items, items[1:]):
            gap = (curr[0] - prev[0]).total_seconds()
            if gap > largest_gap:
                largest_gap = gap
        seqs = [item[1] for item in items if item[1] > 0]
        summaries.append(
            HourlyHeartbeatSummary(
                runtime_id=runtime_id,
                hour_bucket=hour_bucket,
                heartbeat_count=len(items),
                first_heartbeat_at=items[0][0],
                last_heartbeat_at=items[-1][0],
                largest_gap_seconds=largest_gap,
                last_connectivity_status=items[-1][3],
                first_event_id=items[0][2] or None,
                last_event_id=items[-1][2] or None,
                min_ingested_seq=min(seqs) if seqs else None,
                max_ingested_seq=max(seqs) if seqs else None,
            )
        )
    return summaries


def merge_hourly_summaries(
    existing: HourlyHeartbeatSummary,
    delta: HourlyHeartbeatSummary,
) -> HourlyHeartbeatSummary:
    """Merge newly-observed raw heartbeat stats for the same (runtime_id, hour_bucket)."""
    if existing.runtime_id != delta.runtime_id or existing.hour_bucket != delta.hour_bucket:
        raise ValueError("Cannot merge summaries with different (runtime_id, hour_bucket)")

    if delta.first_heartbeat_at >= existing.last_heartbeat_at:
        boundary_gap = (delta.first_heartbeat_at - existing.last_heartbeat_at).total_seconds()
    elif existing.first_heartbeat_at >= delta.last_heartbeat_at:
        boundary_gap = (existing.first_heartbeat_at - delta.last_heartbeat_at).total_seconds()
    else:
        boundary_gap = 0.0

    largest_gap = max(
        float(existing.largest_gap_seconds),
        float(delta.largest_gap_seconds),
        float(boundary_gap),
    )

    if delta.first_heartbeat_at < existing.first_heartbeat_at:
        first_at = delta.first_heartbeat_at
        first_eid = delta.first_event_id
    else:
        first_at = existing.first_heartbeat_at
        first_eid = existing.first_event_id

    if (
        delta.last_heartbeat_at > existing.last_heartbeat_at
        or (
            delta.last_heartbeat_at == existing.last_heartbeat_at
            and (delta.max_ingested_seq or 0) >= (existing.max_ingested_seq or 0)
        )
    ):
        last_at = delta.last_heartbeat_at
        last_status = delta.last_connectivity_status
        last_eid = delta.last_event_id
    else:
        last_at = existing.last_heartbeat_at
        last_status = existing.last_connectivity_status
        last_eid = existing.last_event_id

    seqs_min = [s for s in (existing.min_ingested_seq, delta.min_ingested_seq) if s is not None]
    seqs_max = [s for s in (existing.max_ingested_seq, delta.max_ingested_seq) if s is not None]

    return HourlyHeartbeatSummary(
        runtime_id=existing.runtime_id,
        hour_bucket=existing.hour_bucket,
        heartbeat_count=existing.heartbeat_count + delta.heartbeat_count,
        first_heartbeat_at=first_at,
        last_heartbeat_at=last_at,
        largest_gap_seconds=largest_gap,
        last_connectivity_status=last_status,
        first_event_id=first_eid,
        last_event_id=last_eid,
        min_ingested_seq=min(seqs_min) if seqs_min else None,
        max_ingested_seq=max(seqs_max) if seqs_max else None,
    )


async def ensure_heartbeat_summary_schema(conn: Any) -> None:
    """Idempotently ensure the hourly heartbeat summary table and index exist."""
    await conn.execute(CREATE_HEARTBEAT_SUMMARY_TABLE_SQL)
    await conn.execute(CREATE_HEARTBEAT_SUMMARY_INDEX_SQL)


async def resolve_safe_ingested_seq_ceiling(
    conn: Any,
    *,
    projection_schema: str = "trade_journey_projection",
    extra_consumer_checkpoints: Mapping[str, int] | None = None,
    require_controller_checkpoint: bool = False,
) -> dict[str, Any]:
    """Determine the maximum ingested_seq safe to delete across all seq consumers.

    When ``trade_journey_projection.controller`` exists:
    - If it has rows, every active controller's ``checkpoint_seq`` is respected
      (a ``<id>-migrate`` backfill controller is superseded once its live ``<id>``
      counterpart exists in the same scope).
    - If it has 0 rows, returns ``safe_ingested_seq = 0`` so no row is deleted
      before the projector initializes its checkpoint.
    """
    if not projection_schema.isidentifier():
        raise ValueError(f"Invalid projection schema identifier: {projection_schema!r}")

    controller_table = f"{projection_schema}.controller"
    regclass = await conn.fetchval("SELECT to_regclass($1)", controller_table)
    checkpoints: dict[str, int] = {}

    if regclass is not None:
        rows = await conn.fetch(
            f"""
            SELECT controller_id, tenant_scope, environment_scope, checkpoint_seq, mode, status, accepted_live
            FROM {controller_table}
            """
        )
        if not rows:
            checkpoints[f"{controller_table}:uninitialized"] = 0
        else:
            non_migrate_scopes = {
                (
                    str(r["controller_id"]),
                    str(r["tenant_scope"]),
                    str(r["environment_scope"]),
                )
                for r in rows
                if not str(r["controller_id"]).endswith("-migrate")
            }
            for r in rows:
                cid = str(r["controller_id"])
                t_scope = str(r["tenant_scope"])
                e_scope = str(r["environment_scope"])
                if cid.endswith("-migrate"):
                    base_cid = cid[: -len("-migrate")]
                    if (base_cid, t_scope, e_scope) in non_migrate_scopes:
                        # Superseded by active live/recovery controller in same scope
                        continue
                key = f"{controller_table}:{cid}:{t_scope}:{e_scope}"
                checkpoints[key] = max(0, int(r["checkpoint_seq"] or 0))
    elif require_controller_checkpoint:
        checkpoints[f"{controller_table}:missing"] = 0

    if extra_consumer_checkpoints:
        for name, seq in extra_consumer_checkpoints.items():
            checkpoints[str(name)] = max(0, int(seq))

    max_table_seq = int(
        await conn.fetchval("SELECT COALESCE(MAX(ingested_seq), 0) FROM public.telemetry_events")
        or 0
    )
    if checkpoints:
        safe_seq = min(checkpoints.values())
    else:
        safe_seq = max_table_seq

    return {
        "safe_ingested_seq": safe_seq,
        "max_table_ingested_seq": max_table_seq,
        "controller_table_exists": regclass is not None,
        "consumer_checkpoints": checkpoints,
    }


async def _summarize_hours_before_delete(
    conn: Any,
    candidate_hours: Sequence[tuple[str, datetime]],
    *,
    safe_ingested_seq: int,
) -> int:
    """Ensure every (runtime_id, hour_bucket) in candidate_hours is summarized in Postgres.

    Returns the number of summary rows inserted or updated.
    """
    if not candidate_hours:
        return 0

    unique_pairs = sorted(set(candidate_hours), key=lambda item: (item[0], item[1]))
    runtime_ids = [pair[0] for pair in unique_pairs]
    hour_buckets = [pair[1] for pair in unique_pairs]

    existing_rows = await conn.fetch(
        """
        SELECT runtime_id,
               hour_bucket,
               heartbeat_count,
               first_heartbeat_at,
               last_heartbeat_at,
               largest_gap_seconds,
               last_connectivity_status,
               first_event_id,
               last_event_id,
               min_ingested_seq,
               max_ingested_seq
        FROM public.telemetry_heartbeat_hourly_summaries
        WHERE (runtime_id, hour_bucket) IN (
            SELECT unnest($1::text[]), unnest($2::timestamptz[])
        )
        FOR UPDATE
        """,
        runtime_ids,
        hour_buckets,
    )
    existing_map: dict[tuple[str, datetime], HourlyHeartbeatSummary] = {}
    for row in existing_rows:
        hr = truncate_utc_hour(row["hour_bucket"])
        rid = str(row["runtime_id"])
        existing_map[(rid, hr)] = HourlyHeartbeatSummary(
            runtime_id=rid,
            hour_bucket=hr,
            heartbeat_count=int(row["heartbeat_count"]),
            first_heartbeat_at=_coerce_utc(row["first_heartbeat_at"]),
            last_heartbeat_at=_coerce_utc(row["last_heartbeat_at"]),
            largest_gap_seconds=float(row["largest_gap_seconds"]),
            last_connectivity_status=str(row["last_connectivity_status"]),
            first_event_id=row["first_event_id"],
            last_event_id=row["last_event_id"],
            min_ingested_seq=int(row["min_ingested_seq"]) if row["min_ingested_seq"] is not None else None,
            max_ingested_seq=int(row["max_ingested_seq"]) if row["max_ingested_seq"] is not None else None,
        )

    min_hour = min(hour_buckets)
    max_hour_end = max(hour_buckets) + timedelta(hours=1)
    pair_set = set(unique_pairs)

    raw_rows = await conn.fetch(
        """
        SELECT event_id,
               created_at,
               ingested_seq,
               COALESCE(
                   NULLIF(trim(payload->>'runtime_id'), ''),
                   NULLIF(trim(payload->'metadata'->>'runtime_id'), ''),
                   'unknown'
               ) AS runtime_id,
               COALESCE(
                   NULLIF(trim(payload->'metadata'->'runtime_heartbeat'->>'connectivity_status'), ''),
                   NULLIF(trim(payload->'metadata'->>'connectivity_status'), ''),
                   NULLIF(trim(payload->>'connectivity_status'), ''),
                   'connected'
               ) AS connectivity_status
        FROM public.telemetry_events
        WHERE event_type = 'heartbeat'
          AND created_at >= $1
          AND created_at < $2
          AND ingested_seq <= $3
        ORDER BY created_at ASC, ingested_seq ASC, event_id ASC
        """,
        min_hour,
        max_hour_end,
        int(safe_ingested_seq),
    )

    rows_to_fold_by_pair: dict[tuple[str, datetime], list[dict[str, Any]]] = {}
    for row in raw_rows:
        rid = str(row["runtime_id"])
        hr = truncate_utc_hour(row["created_at"])
        pair = (rid, hr)
        if pair not in pair_set:
            continue
        prior = existing_map.get(pair)
        seq = int(row["ingested_seq"])
        if prior is not None and prior.max_ingested_seq is not None and seq <= prior.max_ingested_seq:
            continue
        rows_to_fold_by_pair.setdefault(pair, []).append(
            {
                "event_id": str(row["event_id"]),
                "event_type": HEARTBEAT_EVENT_TYPE,
                "created_at": row["created_at"],
                "ingested_seq": seq,
                "runtime_id": rid,
                "connectivity_status": str(row["connectivity_status"]),
            }
        )

    upserted = 0
    for pair in unique_pairs:
        new_rows = rows_to_fold_by_pair.get(pair)
        prior = existing_map.get(pair)
        if not new_rows:
            if prior is None:
                raise HeartbeatRetentionError(
                    f"Refusing to delete raw heartbeats for {pair}: no summary exists and no raw rows found"
                )
            continue

        computed_list = compute_hourly_summaries_from_rows(new_rows)
        if len(computed_list) != 1:
            raise HeartbeatRetentionError(f"Expected 1 hourly summary for {pair}, got {len(computed_list)}")
        delta_summary = computed_list[0]
        target = merge_hourly_summaries(prior, delta_summary) if prior is not None else delta_summary

        await conn.execute(
            """
            INSERT INTO public.telemetry_heartbeat_hourly_summaries (
                runtime_id,
                hour_bucket,
                heartbeat_count,
                first_heartbeat_at,
                last_heartbeat_at,
                largest_gap_seconds,
                last_connectivity_status,
                first_event_id,
                last_event_id,
                min_ingested_seq,
                max_ingested_seq,
                summarized_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, clock_timestamp())
            ON CONFLICT (runtime_id, hour_bucket) DO UPDATE SET
                heartbeat_count          = EXCLUDED.heartbeat_count,
                first_heartbeat_at       = EXCLUDED.first_heartbeat_at,
                last_heartbeat_at        = EXCLUDED.last_heartbeat_at,
                largest_gap_seconds      = EXCLUDED.largest_gap_seconds,
                last_connectivity_status = EXCLUDED.last_connectivity_status,
                first_event_id           = EXCLUDED.first_event_id,
                last_event_id            = EXCLUDED.last_event_id,
                min_ingested_seq         = EXCLUDED.min_ingested_seq,
                max_ingested_seq         = EXCLUDED.max_ingested_seq,
                summarized_at            = clock_timestamp()
            """,
            target.runtime_id,
            target.hour_bucket,
            int(target.heartbeat_count),
            target.first_heartbeat_at,
            target.last_heartbeat_at,
            float(target.largest_gap_seconds),
            target.last_connectivity_status,
            target.first_event_id,
            target.last_event_id,
            target.min_ingested_seq,
            target.max_ingested_seq,
        )
        upserted += 1

    # Hard post-check before any raw row deletion: every candidate hour must exist in summary table
    verified_count = await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM public.telemetry_heartbeat_hourly_summaries
        WHERE (runtime_id, hour_bucket) IN (
            SELECT unnest($1::text[]), unnest($2::timestamptz[])
        )
          AND heartbeat_count > 0
        """,
        runtime_ids,
        hour_buckets,
    )
    if int(verified_count or 0) != len(unique_pairs):
        raise HeartbeatRetentionError(
            f"Summary pre-delete verification failed: expected {len(unique_pairs)} summarized hours, "
            f"found {verified_count}"
        )

    return upserted


@dataclass
class RetentionRunResult:
    """Result of one bounded heartbeat, infrastructure_health, state_snapshot, and 14d telemetry_events retention execution."""

    status: str
    retention_days: float
    cutoff_at: str
    safe_ingested_seq: int
    batches_executed: int
    rows_deleted: int
    hours_summarized: int
    statement_timeout_ms: int
    batch_size: int
    duration_ms: float
    consumer_checkpoints: dict[str, int] = field(default_factory=dict)
    blocking_reader: str | None = None
    error: str | None = None
    heartbeat_rows_deleted: int = 0
    infra_health_rows_deleted: int = 0
    infra_health_retention_days: float = DEFAULT_INFRA_HEALTH_RETENTION_DAYS
    infra_health_cutoff_at: str = ""
    heartbeat_batches_executed: int = 0
    infra_health_batches_executed: int = 0
    protected_infra_health_event_ids_count: int = 0
    protected_infra_health_event_ids: list[str] = field(default_factory=list)
    state_snapshot_rows_deleted: int = 0
    state_snapshot_retention_days: float = DEFAULT_STATE_SNAPSHOT_RETENTION_DAYS
    state_snapshot_batches_executed: int = 0
    telemetry_events_rows_deleted: int = 0
    telemetry_events_retention_days: float = DEFAULT_TELEMETRY_EVENTS_RETENTION_DAYS
    telemetry_events_cutoff_at: str = ""
    telemetry_events_batches_executed: int = 0
    protected_telemetry_anchor_event_ids_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "retention_days": self.retention_days,
            "cutoff_at": self.cutoff_at,
            "safe_ingested_seq": self.safe_ingested_seq,
            "batches_executed": self.batches_executed,
            "rows_deleted": self.rows_deleted,
            "hours_summarized": self.hours_summarized,
            "statement_timeout_ms": self.statement_timeout_ms,
            "batch_size": self.batch_size,
            "duration_ms": round(self.duration_ms, 3),
            "consumer_checkpoints": dict(self.consumer_checkpoints),
            "blocking_reader": self.blocking_reader,
            "error": self.error,
            "heartbeat_rows_deleted": self.heartbeat_rows_deleted,
            "infra_health_rows_deleted": self.infra_health_rows_deleted,
            "infra_health_retention_days": self.infra_health_retention_days,
            "infra_health_cutoff_at": self.infra_health_cutoff_at,
            "heartbeat_batches_executed": self.heartbeat_batches_executed,
            "infra_health_batches_executed": self.infra_health_batches_executed,
            "protected_infra_health_event_ids_count": self.protected_infra_health_event_ids_count,
            "protected_infra_health_event_ids": list(self.protected_infra_health_event_ids),
            "state_snapshot_rows_deleted": self.state_snapshot_rows_deleted,
            "state_snapshot_retention_days": self.state_snapshot_retention_days,
            "state_snapshot_batches_executed": self.state_snapshot_batches_executed,
            "telemetry_events_rows_deleted": self.telemetry_events_rows_deleted,
            "telemetry_events_retention_days": self.telemetry_events_retention_days,
            "telemetry_events_cutoff_at": self.telemetry_events_cutoff_at,
            "telemetry_events_batches_executed": self.telemetry_events_batches_executed,
            "protected_telemetry_anchor_event_ids_count": self.protected_telemetry_anchor_event_ids_count,
        }


async def run_heartbeat_retention_on_connection(
    conn: Any,
    *,
    now: datetime | None = None,
    retention_days: float | None = None,
    infra_health_retention_days: float | None = None,
    state_snapshot_retention_days: float | None = None,
    telemetry_events_retention_days: float | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int | None = DEFAULT_MAX_BATCHES_PER_RUN,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
    projection_schema: str = "trade_journey_projection",
    extra_consumer_checkpoints: Mapping[str, int] | None = None,
    require_controller_checkpoint: bool = False,
    readers: Sequence[Mapping[str, Any]] = HEARTBEAT_READERS,
    infra_health_readers: Sequence[Mapping[str, Any]] = INFRASTRUCTURE_HEALTH_READERS,
    telemetry_events_readers: Sequence[Mapping[str, Any]] = TELEMETRY_EVENTS_READERS,
    incident_cases_table: str = DEFAULT_INCIDENT_CASES_TABLE,
    postmortems_table: str = DEFAULT_POSTMORTEMS_TABLE,
    incident_store_paths: Sequence[str | Path] | None = None,
    bff_health_sqlite_path: str | Path | None = None,
    extra_protected_event_ids: Iterable[str] | None = None,
) -> RetentionRunResult:
    """Run bounded heartbeat summarization and raw heartbeat + infrastructure_health + state_snapshot + 14d telemetry_events deletion on an open asyncpg connection."""
    t0 = time.monotonic()
    effective_days = parse_retention_days(retention_days)
    effective_infra_days = parse_infra_health_retention_days(infra_health_retention_days)
    effective_snapshot_days = parse_state_snapshot_retention_days(state_snapshot_retention_days)
    effective_telemetry_days = parse_telemetry_events_retention_days(
        telemetry_events_retention_days
    )
    effective_batch_size = max(1, int(batch_size))
    effective_timeout_ms = max(100, int(statement_timeout_ms))
    reference_now = _coerce_utc(now) if now is not None else _utc_now()
    cutoff_dt = reference_now - timedelta(days=effective_days)
    cutoff_iso = _format_rfc3339(cutoff_dt) or ""
    infra_cutoff_dt = reference_now - timedelta(days=effective_infra_days)
    infra_cutoff_iso = _format_rfc3339(infra_cutoff_dt) or ""
    snapshot_cutoff_dt = reference_now - timedelta(days=effective_snapshot_days)
    telemetry_cutoff_dt = reference_now - timedelta(days=effective_telemetry_days)
    telemetry_cutoff_iso = _format_rfc3339(telemetry_cutoff_dt) or ""

    check_readers_against_retention_window(effective_days, readers=readers)
    check_infra_health_readers_against_retention_window(
        effective_infra_days,
        readers=infra_health_readers,
    )
    check_telemetry_events_readers_against_retention_window(
        effective_telemetry_days,
        readers=telemetry_events_readers,
    )
    await ensure_heartbeat_summary_schema(conn)

    ceiling_info = await resolve_safe_ingested_seq_ceiling(
        conn,
        projection_schema=projection_schema,
        extra_consumer_checkpoints=extra_consumer_checkpoints,
        require_controller_checkpoint=require_controller_checkpoint,
    )
    safe_seq = int(ceiling_info["safe_ingested_seq"])
    checkpoints = dict(ceiling_info["consumer_checkpoints"])

    protected_ids = sorted(
        await resolve_protected_infrastructure_health_event_ids(
            conn,
            incident_cases_table=incident_cases_table,
            postmortems_table=postmortems_table,
            incident_store_paths=incident_store_paths,
            bff_health_sqlite_path=bff_health_sqlite_path,
            extra_protected_event_ids=extra_protected_event_ids,
        )
    )

    if safe_seq <= 0:
        return RetentionRunResult(
            status="ok",
            retention_days=effective_days,
            cutoff_at=cutoff_iso,
            safe_ingested_seq=safe_seq,
            batches_executed=0,
            rows_deleted=0,
            hours_summarized=0,
            statement_timeout_ms=effective_timeout_ms,
            batch_size=effective_batch_size,
            duration_ms=(time.monotonic() - t0) * 1000.0,
            consumer_checkpoints=checkpoints,
            heartbeat_rows_deleted=0,
            infra_health_rows_deleted=0,
            infra_health_retention_days=effective_infra_days,
            infra_health_cutoff_at=infra_cutoff_iso,
            heartbeat_batches_executed=0,
            infra_health_batches_executed=0,
            protected_infra_health_event_ids_count=len(protected_ids),
            protected_infra_health_event_ids=protected_ids,
            state_snapshot_rows_deleted=0,
            state_snapshot_retention_days=effective_snapshot_days,
            state_snapshot_batches_executed=0,
            telemetry_events_rows_deleted=0,
            telemetry_events_retention_days=effective_telemetry_days,
            telemetry_events_cutoff_at=telemetry_cutoff_iso,
            telemetry_events_batches_executed=0,
            protected_telemetry_anchor_event_ids_count=0,
        )

    total_hb_deleted = 0
    total_hours_summarized = 0
    hb_batches_run = 0

    while max_batches is None or hb_batches_run < max_batches:
        query_timeout = (effective_timeout_ms / 1000.0) + 5.0
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('statement_timeout', $1, true)",
                f"{effective_timeout_ms}ms",
                timeout=query_timeout,
            )
            candidates = await conn.fetch(
                """
                SELECT event_id,
                       created_at,
                       ingested_seq,
                       COALESCE(
                           NULLIF(trim(payload->>'runtime_id'), ''),
                           NULLIF(trim(payload->'metadata'->>'runtime_id'), ''),
                           'unknown'
                       ) AS runtime_id
                FROM public.telemetry_events
                WHERE event_type = 'heartbeat'
                  AND created_at < $1
                  AND ingested_seq <= $2
                ORDER BY created_at ASC, ingested_seq ASC, event_id ASC
                LIMIT $3
                """,
                cutoff_dt,
                safe_seq,
                effective_batch_size,
                timeout=query_timeout,
            )
            if not candidates:
                break

            candidate_hours = [
                (str(row["runtime_id"]), truncate_utc_hour(row["created_at"]))
                for row in candidates
            ]
            hours_upserted = await _summarize_hours_before_delete(
                conn,
                candidate_hours,
                safe_ingested_seq=safe_seq,
            )
            candidate_event_ids = [str(row["event_id"]) for row in candidates]
            deleted_rows = await conn.fetch(
                """
                DELETE FROM public.telemetry_events
                WHERE event_id = ANY($1::text[])
                  AND event_type = 'heartbeat'
                  AND created_at < $2
                  AND ingested_seq <= $3
                RETURNING event_id
                """,
                candidate_event_ids,
                cutoff_dt,
                safe_seq,
                timeout=query_timeout,
            )
            deleted_in_batch = len(deleted_rows)

        hb_batches_run += 1
        total_hb_deleted += deleted_in_batch
        total_hours_summarized += hours_upserted
        if deleted_in_batch < effective_batch_size:
            break

    total_infra_deleted = 0
    infra_batches_run = 0

    while max_batches is None or infra_batches_run < max_batches:
        query_timeout = (effective_timeout_ms / 1000.0) + 5.0
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('statement_timeout', $1, true)",
                f"{effective_timeout_ms}ms",
                timeout=query_timeout,
            )
            protected_ids = sorted(
                await resolve_protected_infrastructure_health_event_ids(
                    conn,
                    incident_cases_table=incident_cases_table,
                    postmortems_table=postmortems_table,
                    incident_store_paths=incident_store_paths,
                    bff_health_sqlite_path=bff_health_sqlite_path,
                    extra_protected_event_ids=extra_protected_event_ids,
                )
            )
            infra_candidates = await conn.fetch(
                """
                SELECT event_id
                FROM public.telemetry_events
                WHERE event_type = 'infrastructure_health'
                  AND created_at < $1
                  AND ingested_seq <= $2
                  AND NOT (event_id = ANY($3::text[]))
                ORDER BY ingested_seq ASC
                LIMIT $4
                """,
                infra_cutoff_dt,
                safe_seq,
                protected_ids,
                effective_batch_size,
                timeout=query_timeout,
            )
            if not infra_candidates:
                break

            candidate_infra_ids = [str(row["event_id"]) for row in infra_candidates]
            deleted_infra_rows = await conn.fetch(
                """
                DELETE FROM public.telemetry_events
                WHERE event_id = ANY($1::text[])
                  AND event_type = 'infrastructure_health'
                  AND created_at < $2
                  AND ingested_seq <= $3
                  AND NOT (event_id = ANY($4::text[]))
                RETURNING event_id
                """,
                candidate_infra_ids,
                infra_cutoff_dt,
                safe_seq,
                protected_ids,
                timeout=query_timeout,
            )
            deleted_infra_in_batch = len(deleted_infra_rows)

        infra_batches_run += 1
        total_infra_deleted += deleted_infra_in_batch
        if deleted_infra_in_batch < effective_batch_size:
            break

    total_snapshot_deleted = 0
    snapshot_batches_run = 0
    snapshot_runtime_expr = """COALESCE(
                           NULLIF(trim({a}.payload->>'runtime_id'), ''),
                           NULLIF(trim({a}.payload->'metadata'->>'runtime_id'), ''),
                           'unknown')"""
    while max_batches is None or snapshot_batches_run < max_batches:
        query_timeout = (effective_timeout_ms / 1000.0) + 5.0
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('statement_timeout', $1, true)",
                f"{effective_timeout_ms}ms",
                timeout=query_timeout,
            )
            # Keep: the newest and the oldest snapshot per runtime (the summary's
            # binding first-event bookkeeping reads the oldest) and any
            # snapshot the runtime summary keeps as quarter-equity history.
            snapshot_deleted_rows = await conn.fetch(
                f"""
                WITH candidates AS (
                    SELECT e.event_id
                    FROM public.telemetry_events e
                    WHERE e.event_type = 'runtime_state_snapshot'
                      AND e.created_at < $1
                      AND e.ingested_seq <= $2
                      AND COALESCE(e.payload->'metadata'->'performance_telemetry'->>'status', '')
                          <> ALL($4::text[])
                      AND EXISTS (
                          SELECT 1 FROM public.telemetry_events n
                          WHERE n.event_type = 'runtime_state_snapshot'
                            AND {snapshot_runtime_expr.format(a="n")}
                                = {snapshot_runtime_expr.format(a="e")}
                            AND (n.created_at, n.ingested_seq) > (e.created_at, e.ingested_seq)
                      )
                      AND EXISTS (
                          SELECT 1 FROM public.telemetry_events o
                          WHERE o.event_type = 'runtime_state_snapshot'
                            AND {snapshot_runtime_expr.format(a="o")}
                                = {snapshot_runtime_expr.format(a="e")}
                            AND (o.created_at, o.ingested_seq) < (e.created_at, e.ingested_seq)
                      )
                    ORDER BY e.ingested_seq ASC
                    LIMIT $3
                )
                DELETE FROM public.telemetry_events d
                USING candidates c
                WHERE d.event_id = c.event_id
                  AND d.event_type = 'runtime_state_snapshot'
                RETURNING d.event_id
                """,
                snapshot_cutoff_dt,
                safe_seq,
                effective_batch_size,
                list(STATE_SNAPSHOT_KEEP_STATUSES),
                timeout=query_timeout,
            )
            deleted_snap_in_batch = len(snapshot_deleted_rows)
        if deleted_snap_in_batch == 0:
            break
        snapshot_batches_run += 1
        total_snapshot_deleted += deleted_snap_in_batch
        if deleted_snap_in_batch < effective_batch_size:
            break

    total_telemetry_events_deleted = 0
    telemetry_events_batches_run = 0
    anchor_ids: set[str] = set()
    prunable_where_sql = _telemetry_events_14d_prunable_where_sql("e")

    while max_batches is None or telemetry_events_batches_run < max_batches:
        query_timeout = (effective_timeout_ms / 1000.0) + 5.0
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('statement_timeout', $1, true)",
                f"{effective_timeout_ms}ms",
                timeout=query_timeout,
            )
            protected_ids = sorted(
                await resolve_protected_telemetry_event_ids(
                    conn,
                    incident_cases_table=incident_cases_table,
                    postmortems_table=postmortems_table,
                    incident_store_paths=incident_store_paths,
                    bff_health_sqlite_path=bff_health_sqlite_path,
                    extra_protected_event_ids=extra_protected_event_ids,
                )
            )
            anchor_ids = await resolve_protected_telemetry_anchor_event_ids(
                conn,
                timeout=query_timeout,
            )
            all_protected_14d_ids = sorted(set(protected_ids) | anchor_ids)
            telemetry_candidates = await conn.fetch(
                f"""
                SELECT e.event_id
                FROM public.telemetry_events e
                WHERE {prunable_where_sql}
                ORDER BY e.ingested_seq ASC
                LIMIT $6
                """,
                telemetry_cutoff_dt,
                safe_seq,
                all_protected_14d_ids,
                list(PERMANENT_TELEMETRY_EVENT_TYPES),
                list(CANONICAL_FILL_AND_REJECTION_EVENT_TYPES),
                effective_batch_size,
                timeout=query_timeout,
            )
            if not telemetry_candidates:
                break

            candidate_telemetry_ids = [str(row["event_id"]) for row in telemetry_candidates]
            deleted_telemetry_rows = await conn.fetch(
                f"""
                DELETE FROM public.telemetry_events e
                WHERE e.event_id = ANY($6::text[])
                  AND {prunable_where_sql}
                RETURNING e.event_id
                """,
                telemetry_cutoff_dt,
                safe_seq,
                all_protected_14d_ids,
                list(PERMANENT_TELEMETRY_EVENT_TYPES),
                list(CANONICAL_FILL_AND_REJECTION_EVENT_TYPES),
                candidate_telemetry_ids,
                timeout=query_timeout,
            )
            deleted_telemetry_in_batch = len(deleted_telemetry_rows)

        if deleted_telemetry_in_batch == 0:
            break
        telemetry_events_batches_run += 1
        total_telemetry_events_deleted += deleted_telemetry_in_batch
        if deleted_telemetry_in_batch < effective_batch_size:
            break

    return RetentionRunResult(
        status="ok",
        retention_days=effective_days,
        cutoff_at=cutoff_iso,
        safe_ingested_seq=safe_seq,
        batches_executed=(
            hb_batches_run
            + infra_batches_run
            + snapshot_batches_run
            + telemetry_events_batches_run
        ),
        rows_deleted=(
            total_hb_deleted
            + total_infra_deleted
            + total_snapshot_deleted
            + total_telemetry_events_deleted
        ),
        hours_summarized=total_hours_summarized,
        statement_timeout_ms=effective_timeout_ms,
        batch_size=effective_batch_size,
        duration_ms=(time.monotonic() - t0) * 1000.0,
        consumer_checkpoints=checkpoints,
        heartbeat_rows_deleted=total_hb_deleted,
        infra_health_rows_deleted=total_infra_deleted,
        infra_health_retention_days=effective_infra_days,
        infra_health_cutoff_at=infra_cutoff_iso,
        heartbeat_batches_executed=hb_batches_run,
        infra_health_batches_executed=infra_batches_run,
        protected_infra_health_event_ids_count=len(protected_ids),
        protected_infra_health_event_ids=protected_ids,
        state_snapshot_rows_deleted=total_snapshot_deleted,
        state_snapshot_retention_days=effective_snapshot_days,
        state_snapshot_batches_executed=snapshot_batches_run,
        telemetry_events_rows_deleted=total_telemetry_events_deleted,
        telemetry_events_retention_days=effective_telemetry_days,
        telemetry_events_cutoff_at=telemetry_cutoff_iso,
        telemetry_events_batches_executed=telemetry_events_batches_run,
        protected_telemetry_anchor_event_ids_count=len(anchor_ids),
    )


async def run_heartbeat_retention(
    dsn: str,
    *,
    now: datetime | None = None,
    retention_days: float | None = None,
    infra_health_retention_days: float | None = None,
    state_snapshot_retention_days: float | None = None,
    telemetry_events_retention_days: float | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int | None = DEFAULT_MAX_BATCHES_PER_RUN,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
    projection_schema: str = "trade_journey_projection",
    extra_consumer_checkpoints: Mapping[str, int] | None = None,
    require_controller_checkpoint: bool = False,
    readers: Sequence[Mapping[str, Any]] = HEARTBEAT_READERS,
    infra_health_readers: Sequence[Mapping[str, Any]] = INFRASTRUCTURE_HEALTH_READERS,
    telemetry_events_readers: Sequence[Mapping[str, Any]] = TELEMETRY_EVENTS_READERS,
    incident_cases_table: str = DEFAULT_INCIDENT_CASES_TABLE,
    postmortems_table: str = DEFAULT_POSTMORTEMS_TABLE,
    incident_store_paths: Sequence[str | Path] | None = None,
    bff_health_sqlite_path: str | Path | None = None,
    extra_protected_event_ids: Iterable[str] | None = None,
) -> RetentionRunResult:
    """Connect to PostgreSQL and run a bounded heartbeat + infrastructure_health + state_snapshot + 14d telemetry_events retention pass."""
    import asyncpg  # type: ignore[import]

    conn = await asyncpg.connect(dsn)
    try:
        return await run_heartbeat_retention_on_connection(
            conn,
            now=now,
            retention_days=retention_days,
            infra_health_retention_days=infra_health_retention_days,
            state_snapshot_retention_days=state_snapshot_retention_days,
            telemetry_events_retention_days=telemetry_events_retention_days,
            batch_size=batch_size,
            max_batches=max_batches,
            statement_timeout_ms=statement_timeout_ms,
            projection_schema=projection_schema,
            extra_consumer_checkpoints=extra_consumer_checkpoints,
            require_controller_checkpoint=require_controller_checkpoint,
            readers=readers,
            infra_health_readers=infra_health_readers,
            telemetry_events_readers=telemetry_events_readers,
            incident_cases_table=incident_cases_table,
            postmortems_table=postmortems_table,
            incident_store_paths=incident_store_paths,
            bff_health_sqlite_path=bff_health_sqlite_path,
            extra_protected_event_ids=extra_protected_event_ids,
        )
    finally:
        await conn.close()


async def rebuild_runtime_summaries_from_db(
    conn_or_dsn: Any,
    *,
    summary_store_path: str | None = None,
    heartbeat_stale_after_seconds: int = 90,
) -> tuple[RuntimeSummaryProjectionStore, TradeEpisodeProjectionStore]:
    """Cold-restart rebuild of RuntimeSummaryProjectionStore from public.telemetry_events.

    Replays committed ``public.telemetry_events`` rows in canonical
    ``ingested_seq ASC`` order into a fresh ``RuntimeSummaryProjectionStore``
    and ``TradeEpisodeProjectionStore``, followed by startup cost backfill.
    """
    import asyncpg  # type: ignore[import]

    close_conn = False
    if isinstance(conn_or_dsn, str):
        conn = await asyncpg.connect(conn_or_dsn)
        close_conn = True
    else:
        conn = conn_or_dsn

    try:
        rows = await conn.fetch(
            """
            SELECT event_id, event_type, created_at, payload, ingested_seq
            FROM public.telemetry_events
            ORDER BY ingested_seq ASC
            """
        )
        protected_ids = await resolve_protected_telemetry_event_ids(conn)
        anchor_ids = await resolve_protected_telemetry_anchor_event_ids(conn)
        prunable_rows = await conn.fetch(
            f"""
            SELECT e.event_id
            FROM public.telemetry_events e
            WHERE {_telemetry_events_14d_prunable_where_sql("e")}
            """,
            datetime(9999, 12, 31, tzinfo=timezone.utc),
            9_223_372_036_854_775_807,
            sorted(protected_ids | anchor_ids),
            list(PERMANENT_TELEMETRY_EVENT_TYPES),
            list(CANONICAL_FILL_AND_REJECTION_EVENT_TYPES),
        )
        prunable_transient_ids = {str(r["event_id"]) for r in prunable_rows if r["event_id"]}
    finally:
        if close_conn:
            await conn.close()

    summary_store = RuntimeSummaryProjectionStore(
        summary_store_path,
        heartbeat_stale_after_seconds=heartbeat_stale_after_seconds,
    )
    episode_store = TradeEpisodeProjectionStore()
    retained_events: list[dict[str, Any]] = []
    for row in rows:
        eid = str(row["event_id"])
        if eid in prunable_transient_ids:
            continue
        raw_payload = row["payload"]
        event = (
            json.loads(raw_payload)
            if isinstance(raw_payload, str)
            else dict(raw_payload)
        )
        event.setdefault("event_id", eid)
        event.setdefault("event_type", str(row["event_type"]))
        event["ingested_seq"] = int(row["ingested_seq"])
        retained_events.append(event)
        summary_store.project_event(event)
        ev_type = str(event.get("event_type") or "")
        meta = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        inner_payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        reversal = meta.get("reversal") if isinstance(meta.get("reversal"), Mapping) else {}
        has_episode = (
            ev_type.startswith(("trade_episode.", "trade_reflection.", "trade_lesson."))
            or event.get("trade_episode_id")
            or inner_payload.get("trade_episode_id")
            or meta.get("trade_episode_id")
            or reversal.get("closed_episode_id")
            or reversal.get("opened_episode_id")
        )
        if has_episode:
            episode_store.project_event(event)

    def _in_memory_binding_events(
        binding_id: str,
        *,
        tenant_id: str | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        target_bid = str(binding_id or "").strip()
        target_tid = str(tenant_id or "").strip()
        matched: list[dict[str, Any]] = []
        for ev in reversed(retained_events):
            ev_meta = ev.get("metadata") if isinstance(ev.get("metadata"), Mapping) else {}
            ev_bid = str(
                ev.get("binding_id")
                or ev.get("runtime_binding_id")
                or ev_meta.get("binding_id")
                or ev_meta.get("runtime_binding_id")
                or ""
            ).strip()
            if ev_bid != target_bid:
                continue
            if target_tid:
                ev_tid = str(
                    ev.get("tenant_id") or ev_meta.get("tenant_id") or ""
                ).strip()
                if ev_tid != target_tid:
                    continue
            matched.append(ev)
            if len(matched) >= limit:
                break
        return matched

    summary_store.backfill_open_position_costs(
        CanonicalFillHistoryReader(episode_store, _in_memory_binding_events)
    )
    return summary_store, episode_store


class HeartbeatRetentionJob:
    """Periodic background job inside services/telemetry that enforces heartbeat, infrastructure_health, state_snapshot, and 14d telemetry_events retention."""

    def __init__(
        self,
        dsn: str,
        *,
        retention_days: float | None = None,
        infra_health_retention_days: float | None = None,
        state_snapshot_retention_days: float | None = None,
        telemetry_events_retention_days: float | None = None,
        batch_size: int | None = None,
        max_batches_per_run: int | None = None,
        interval_seconds: float | None = None,
        statement_timeout_ms: int | None = None,
        enabled: bool = True,
    ) -> None:
        self._dsn = str(dsn or "").strip()
        self._retention_days = parse_retention_days(retention_days)
        self._infra_health_retention_days = parse_infra_health_retention_days(
            infra_health_retention_days
        )
        self._state_snapshot_retention_days = parse_state_snapshot_retention_days(
            state_snapshot_retention_days
        )
        self._telemetry_events_retention_days = parse_telemetry_events_retention_days(
            telemetry_events_retention_days
        )
        self._batch_size = max(
            1,
            int(
                batch_size
                if batch_size is not None
                else os.getenv(RETENTION_BATCH_SIZE_ENV, str(DEFAULT_BATCH_SIZE))
            ),
        )
        self._max_batches_per_run = max(
            1,
            int(
                max_batches_per_run
                if max_batches_per_run is not None
                else os.getenv(RETENTION_MAX_BATCHES_ENV, str(DEFAULT_MAX_BATCHES_PER_RUN))
            ),
        )
        self._interval_seconds = max(
            1.0,
            float(
                interval_seconds
                if interval_seconds is not None
                else os.getenv(RETENTION_INTERVAL_SECONDS_ENV, str(DEFAULT_INTERVAL_SECONDS))
            ),
        )
        self._statement_timeout_ms = max(
            100,
            int(
                statement_timeout_ms
                if statement_timeout_ms is not None
                else os.getenv(RETENTION_STATEMENT_TIMEOUT_MS_ENV, str(DEFAULT_STATEMENT_TIMEOUT_MS))
            ),
        )
        self._enabled = bool(enabled and self._dsn)
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._runs_completed = 0
        self._runs_failed = 0
        self._total_rows_deleted = 0
        self._total_heartbeat_rows_deleted = 0
        self._total_infra_health_rows_deleted = 0
        self._total_state_snapshot_rows_deleted = 0
        self._total_telemetry_events_rows_deleted = 0
        self._total_hours_summarized = 0
        self._last_run_at: str | None = None
        self._last_result: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._blocking_reader: str | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def is_running(self) -> bool:
        return bool(self._running and self._task is not None and not self._task.done())

    async def run_once(self, *, now: datetime | None = None) -> RetentionRunResult:
        """Execute a single bounded retention pass."""
        if not self._dsn:
            ref_now = _coerce_utc(now) if now else _utc_now()
            result = RetentionRunResult(
                status="skipped_no_dsn",
                retention_days=self._retention_days,
                cutoff_at=_format_rfc3339(
                    ref_now - timedelta(days=self._retention_days)
                )
                or "",
                safe_ingested_seq=0,
                batches_executed=0,
                rows_deleted=0,
                hours_summarized=0,
                statement_timeout_ms=self._statement_timeout_ms,
                batch_size=self._batch_size,
                duration_ms=0.0,
                infra_health_retention_days=self._infra_health_retention_days,
                infra_health_cutoff_at=_format_rfc3339(
                    ref_now - timedelta(days=self._infra_health_retention_days)
                )
                or "",
                state_snapshot_retention_days=self._state_snapshot_retention_days,
                telemetry_events_retention_days=self._telemetry_events_retention_days,
                telemetry_events_cutoff_at=_format_rfc3339(
                    ref_now - timedelta(days=self._telemetry_events_retention_days)
                )
                or "",
            )
            self._last_result = result.to_dict()
            return result

        try:
            result = await run_heartbeat_retention(
                self._dsn,
                now=now,
                retention_days=self._retention_days,
                infra_health_retention_days=self._infra_health_retention_days,
                state_snapshot_retention_days=self._state_snapshot_retention_days,
                telemetry_events_retention_days=self._telemetry_events_retention_days,
                batch_size=self._batch_size,
                max_batches=self._max_batches_per_run,
                statement_timeout_ms=self._statement_timeout_ms,
            )
            self._runs_completed += 1
            self._total_rows_deleted += result.rows_deleted
            self._total_heartbeat_rows_deleted += result.heartbeat_rows_deleted
            self._total_infra_health_rows_deleted += result.infra_health_rows_deleted
            self._total_state_snapshot_rows_deleted += result.state_snapshot_rows_deleted
            self._total_telemetry_events_rows_deleted += result.telemetry_events_rows_deleted
            self._total_hours_summarized += result.hours_summarized
            self._last_run_at = _format_rfc3339(_utc_now())
            self._last_result = result.to_dict()
            self._last_error = None
            self._blocking_reader = None
            return result
        except HeartbeatRetentionReaderBlocker as exc:
            self._runs_failed += 1
            self._last_run_at = _format_rfc3339(_utc_now())
            self._last_error = str(exc)
            self._blocking_reader = exc.reader_id
            raise
        except Exception as exc:
            self._runs_failed += 1
            self._last_run_at = _format_rfc3339(_utc_now())
            self._last_error = str(exc)
            raise

    async def start(self) -> None:
        if not self._enabled or self.is_running():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="telemetry-heartbeat-retention")
        log.info(
            "HeartbeatRetentionJob started (retention_days=%s, infra_health_retention_days=%s, state_snapshot_retention_days=%s, telemetry_events_retention_days=%s, batch_size=%s, interval=%ss, statement_timeout_ms=%s)",
            self._retention_days,
            self._infra_health_retention_days,
            self._state_snapshot_retention_days,
            self._telemetry_events_retention_days,
            self._batch_size,
            self._interval_seconds,
            self._statement_timeout_ms,
        )

    async def stop(self) -> None:
        self._running = False
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                log.warning("HeartbeatRetentionJob stop observed error: %s", exc)

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("HeartbeatRetentionJob pass failed: %s", exc)
            try:
                await asyncio.sleep(self._interval_seconds)
            except asyncio.CancelledError:
                break

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "running": self.is_running(),
            "retention_days": self._retention_days,
            "infra_health_retention_days": self._infra_health_retention_days,
            "state_snapshot_retention_days": self._state_snapshot_retention_days,
            "telemetry_events_retention_days": self._telemetry_events_retention_days,
            "batch_size": self._batch_size,
            "max_batches_per_run": self._max_batches_per_run,
            "interval_seconds": self._interval_seconds,
            "statement_timeout_ms": self._statement_timeout_ms,
            "runs_completed": self._runs_completed,
            "runs_failed": self._runs_failed,
            "total_rows_deleted": self._total_rows_deleted,
            "total_heartbeat_rows_deleted": self._total_heartbeat_rows_deleted,
            "total_infra_health_rows_deleted": self._total_infra_health_rows_deleted,
            "total_state_snapshot_rows_deleted": self._total_state_snapshot_rows_deleted,
            "total_telemetry_events_rows_deleted": self._total_telemetry_events_rows_deleted,
            "total_hours_summarized": self._total_hours_summarized,
            "last_run_at": self._last_run_at,
            "last_result": self._last_result,
            "last_error": self._last_error,
            "blocking_reader": self._blocking_reader,
        }
