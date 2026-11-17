"""Runtime heartbeat retention and permanent hourly summary aggregation.

TELEMETRY-HEARTBEAT-RETENTION-20261117:
- Keeps raw ``event_type = 'heartbeat'`` rows in ``public.telemetry_events``
  for ``PANTHEON_TELEMETRY_HEARTBEAT_RAW_RETENTION_DAYS`` (default 3 days).
- Summarises each ``(runtime_id, hour_bucket)`` in UTC into
  ``public.telemetry_heartbeat_hourly_summaries`` (storing ``heartbeat_count``,
  ``first_heartbeat_at``, ``last_heartbeat_at``, ``largest_gap_seconds``, and
  ``last_connectivity_status``) BEFORE any raw heartbeat row of that hour is
  deleted. Summary rows are permanent and never deleted.
- Never touches any non-heartbeat ``event_type``.
- Never deletes a row whose ``ingested_seq`` has not yet been passed by every
  consumer reading ``public.telemetry_events`` by ``ingested_seq``.
- Verifies that no registered heartbeat reader requires raw heartbeats older
  than the configured retention window; if any reader does, refuses to delete
  and raises ``HeartbeatRetentionReaderBlocker`` naming that reader.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import os
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

from .ingest_svc import CanonicalFillHistoryReader
from .runtime_summary import RuntimeSummaryProjectionStore
from .trade_episode_projection import TradeEpisodeProjectionStore

log = logging.getLogger(__name__)

CANONICAL_TELEMETRY_TABLE = "public.telemetry_events"
HEARTBEAT_SUMMARY_TABLE = "public.telemetry_heartbeat_hourly_summaries"
HEARTBEAT_EVENT_TYPE = "heartbeat"

DEFAULT_RAW_RETENTION_DAYS = 3.0
DEFAULT_BATCH_SIZE = 5000
DEFAULT_MAX_BATCHES_PER_RUN = 20
DEFAULT_INTERVAL_SECONDS = 300.0
DEFAULT_STATEMENT_TIMEOUT_MS = 15000

RAW_RETENTION_DAYS_ENV = "PANTHEON_TELEMETRY_HEARTBEAT_RAW_RETENTION_DAYS"
RETENTION_ENABLED_ENV = "PANTHEON_TELEMETRY_HEARTBEAT_RETENTION_ENABLED"
RETENTION_BATCH_SIZE_ENV = "PANTHEON_TELEMETRY_HEARTBEAT_RETENTION_BATCH_SIZE"
RETENTION_MAX_BATCHES_ENV = "PANTHEON_TELEMETRY_HEARTBEAT_RETENTION_MAX_BATCHES_PER_RUN"
RETENTION_INTERVAL_SECONDS_ENV = "PANTHEON_TELEMETRY_HEARTBEAT_RETENTION_INTERVAL_SECONDS"
RETENTION_STATEMENT_TIMEOUT_MS_ENV = "PANTHEON_TELEMETRY_HEARTBEAT_RETENTION_STATEMENT_TIMEOUT_MS"


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
    """Result of one bounded heartbeat retention execution."""

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
        }


async def run_heartbeat_retention_on_connection(
    conn: Any,
    *,
    now: datetime | None = None,
    retention_days: float | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int | None = DEFAULT_MAX_BATCHES_PER_RUN,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
    projection_schema: str = "trade_journey_projection",
    extra_consumer_checkpoints: Mapping[str, int] | None = None,
    require_controller_checkpoint: bool = False,
    readers: Sequence[Mapping[str, Any]] = HEARTBEAT_READERS,
) -> RetentionRunResult:
    """Run bounded heartbeat summarization and raw deletion on an open asyncpg connection."""
    t0 = time.monotonic()
    effective_days = parse_retention_days(retention_days)
    effective_batch_size = max(1, int(batch_size))
    effective_timeout_ms = max(100, int(statement_timeout_ms))
    reference_now = _coerce_utc(now) if now is not None else _utc_now()
    cutoff_dt = reference_now - timedelta(days=effective_days)
    cutoff_iso = _format_rfc3339(cutoff_dt) or ""

    check_readers_against_retention_window(effective_days, readers=readers)
    await ensure_heartbeat_summary_schema(conn)

    ceiling_info = await resolve_safe_ingested_seq_ceiling(
        conn,
        projection_schema=projection_schema,
        extra_consumer_checkpoints=extra_consumer_checkpoints,
        require_controller_checkpoint=require_controller_checkpoint,
    )
    safe_seq = int(ceiling_info["safe_ingested_seq"])
    checkpoints = dict(ceiling_info["consumer_checkpoints"])

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
        )

    total_deleted = 0
    total_hours_summarized = 0
    batches_run = 0

    while max_batches is None or batches_run < max_batches:
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

        batches_run += 1
        total_deleted += deleted_in_batch
        total_hours_summarized += hours_upserted
        if deleted_in_batch < effective_batch_size:
            break

    return RetentionRunResult(
        status="ok",
        retention_days=effective_days,
        cutoff_at=cutoff_iso,
        safe_ingested_seq=safe_seq,
        batches_executed=batches_run,
        rows_deleted=total_deleted,
        hours_summarized=total_hours_summarized,
        statement_timeout_ms=effective_timeout_ms,
        batch_size=effective_batch_size,
        duration_ms=(time.monotonic() - t0) * 1000.0,
        consumer_checkpoints=checkpoints,
    )


async def run_heartbeat_retention(
    dsn: str,
    *,
    now: datetime | None = None,
    retention_days: float | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int | None = DEFAULT_MAX_BATCHES_PER_RUN,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
    projection_schema: str = "trade_journey_projection",
    extra_consumer_checkpoints: Mapping[str, int] | None = None,
    require_controller_checkpoint: bool = False,
    readers: Sequence[Mapping[str, Any]] = HEARTBEAT_READERS,
) -> RetentionRunResult:
    """Connect to PostgreSQL and run a bounded heartbeat retention pass."""
    import asyncpg  # type: ignore[import]

    conn = await asyncpg.connect(dsn)
    try:
        return await run_heartbeat_retention_on_connection(
            conn,
            now=now,
            retention_days=retention_days,
            batch_size=batch_size,
            max_batches=max_batches,
            statement_timeout_ms=statement_timeout_ms,
            projection_schema=projection_schema,
            extra_consumer_checkpoints=extra_consumer_checkpoints,
            require_controller_checkpoint=require_controller_checkpoint,
            readers=readers,
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
    finally:
        if close_conn:
            await conn.close()

    summary_store = RuntimeSummaryProjectionStore(
        summary_store_path,
        heartbeat_stale_after_seconds=heartbeat_stale_after_seconds,
    )
    episode_store = TradeEpisodeProjectionStore()
    for row in rows:
        raw_payload = row["payload"]
        event = (
            json.loads(raw_payload)
            if isinstance(raw_payload, str)
            else dict(raw_payload)
        )
        event.setdefault("event_id", str(row["event_id"]))
        event.setdefault("event_type", str(row["event_type"]))
        event["ingested_seq"] = int(row["ingested_seq"])
        summary_store.project_event(event)
        has_episode = (
            event.get("trade_episode_id")
            or (event.get("payload") or {}).get("trade_episode_id")
            or (event.get("metadata") or {}).get("trade_episode_id")
        )
        if has_episode:
            episode_store.project_event(event)

    summary_store.backfill_open_position_costs(
        CanonicalFillHistoryReader(episode_store, None)
    )
    return summary_store, episode_store


class HeartbeatRetentionJob:
    """Periodic background job inside services/telemetry that enforces heartbeat retention."""

    def __init__(
        self,
        dsn: str,
        *,
        retention_days: float | None = None,
        batch_size: int | None = None,
        max_batches_per_run: int | None = None,
        interval_seconds: float | None = None,
        statement_timeout_ms: int | None = None,
        enabled: bool = True,
    ) -> None:
        self._dsn = str(dsn or "").strip()
        self._retention_days = parse_retention_days(retention_days)
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
            result = RetentionRunResult(
                status="skipped_no_dsn",
                retention_days=self._retention_days,
                cutoff_at=_format_rfc3339(
                    (_coerce_utc(now) if now else _utc_now()) - timedelta(days=self._retention_days)
                )
                or "",
                safe_ingested_seq=0,
                batches_executed=0,
                rows_deleted=0,
                hours_summarized=0,
                statement_timeout_ms=self._statement_timeout_ms,
                batch_size=self._batch_size,
                duration_ms=0.0,
            )
            self._last_result = result.to_dict()
            return result

        try:
            result = await run_heartbeat_retention(
                self._dsn,
                now=now,
                retention_days=self._retention_days,
                batch_size=self._batch_size,
                max_batches=self._max_batches_per_run,
                statement_timeout_ms=self._statement_timeout_ms,
            )
            self._runs_completed += 1
            self._total_rows_deleted += result.rows_deleted
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
            "HeartbeatRetentionJob started (retention_days=%s, batch_size=%s, interval=%ss, statement_timeout_ms=%s)",
            self._retention_days,
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
            "batch_size": self._batch_size,
            "max_batches_per_run": self._max_batches_per_run,
            "interval_seconds": self._interval_seconds,
            "statement_timeout_ms": self._statement_timeout_ms,
            "runs_completed": self._runs_completed,
            "runs_failed": self._runs_failed,
            "total_rows_deleted": self._total_rows_deleted,
            "total_hours_summarized": self._total_hours_summarized,
            "last_run_at": self._last_run_at,
            "last_result": self._last_result,
            "last_error": self._last_error,
            "blocking_reader": self._blocking_reader,
        }
