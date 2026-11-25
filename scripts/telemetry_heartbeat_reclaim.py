#!/usr/bin/env python3
"""One-time and operator-triggered telemetry & event_receipts space reclaim tool.

TELEMETRY-HEARTBEAT-RETENTION-20261117 & TELEMETRY-INFRA-HEALTH-RETENTION-20261117:
- Accepts an explicit allow-list of tables:
  ``public.telemetry_events`` and ``trade_journey_projection.event_receipts``.
- ``--dry-run`` (default): non-destructively inspects each target table in the
  allow-list and reports rows to delete, bytes to delete, retained table bytes,
  required 1.5x free disk floor, current filesystem free bytes, expected
  filesystem free bytes after reclaim, per-event-type / disposition breakdown,
  consumer checkpoints, and reader retention window verification.
- ``--apply``:
  1. Refuses to start when free disk is below 1.5x the retained table size for
     any target table (``InsufficientDiskSpaceError``) or when any reader needs
     unreferenced raw rows older than the window (``HeartbeatRetentionReaderBlocker``).
  2. When ``public.telemetry_events`` is in the target list, summarises each
     ``(runtime_id, hour_bucket)`` in UTC into
     ``public.telemetry_heartbeat_hourly_summaries``, deletes raw
     ``event_type = 'heartbeat'`` rows older than the heartbeat retention window,
     and deletes unreferenced ``event_type = 'infrastructure_health'`` rows older
     than ``PANTHEON_TELEMETRY_INFRA_HEALTH_RETENTION_DAYS`` (default 7 days) in
     bounded batches under a statement timeout.
  3. Returns physical space to the filesystem for each target table with a
     standard Postgres mechanism (``VACUUM (FULL, ANALYZE) <table>`` by default,
     or ``pg_repack`` / ``table_rewrite``) under a bounded, measured lock window.
  4. Optionally replays any telemetry DLQ write-failure entries accumulated
     during the lock window.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import inspect
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

from services.telemetry.heartbeat_retention import (
    CANONICAL_FILL_AND_REJECTION_EVENT_TYPES,
    CANONICAL_TELEMETRY_TABLE,
    DEFAULT_BATCH_SIZE,
    DEFAULT_INCIDENT_CASES_TABLE,
    DEFAULT_INFRA_HEALTH_RETENTION_DAYS,
    DEFAULT_POSTMORTEMS_TABLE,
    DEFAULT_RAW_RETENTION_DAYS,
    DEFAULT_TELEMETRY_EVENTS_RETENTION_DAYS,
    HEARTBEAT_EVENT_TYPE,
    HEARTBEAT_READERS,
    HEARTBEAT_SUMMARY_TABLE,
    INFRASTRUCTURE_HEALTH_READERS,
    INFRA_HEALTH_EVENT_TYPE,
    INFRA_HEALTH_RETENTION_DAYS_ENV,
    INGESTED_SEQ_CONSUMERS,
    PERMANENT_TELEMETRY_EVENT_TYPES,
    RAW_RETENTION_DAYS_ENV,
    TELEMETRY_EVENTS_READERS,
    TELEMETRY_EVENTS_RETENTION_DAYS_ENV,
    HeartbeatRetentionError,
    HeartbeatRetentionReaderBlocker,
    _telemetry_events_14d_prunable_where_sql,
    check_infra_health_readers_against_retention_window,
    check_readers_against_retention_window,
    check_telemetry_events_readers_against_retention_window,
    ensure_heartbeat_summary_schema,
    parse_infra_health_retention_days,
    parse_retention_days,
    parse_telemetry_events_retention_days,
    resolve_protected_infrastructure_health_event_ids,
    resolve_protected_telemetry_anchor_event_ids,
    resolve_safe_ingested_seq_ceiling,
    run_heartbeat_retention_on_connection,
)

CANONICAL_EVENT_RECEIPTS_TABLE = "trade_journey_projection.event_receipts"
ALLOWED_RECLAIM_TABLES: tuple[str, ...] = (
    CANONICAL_TELEMETRY_TABLE,
    CANONICAL_EVENT_RECEIPTS_TABLE,
)
FREE_DISK_SAFETY_MULTIPLIER = 1.5
SUPPORTED_RECLAIM_METHODS = ("vacuum_full", "table_rewrite", "pg_repack")


class InsufficientDiskSpaceError(HeartbeatRetentionError):
    """Raised when free filesystem space is below 1.5x the retained table size."""

    def __init__(
        self,
        *,
        free_disk_bytes: int,
        retained_table_bytes: int,
        required_free_disk_bytes: int,
        filesystem_path: str,
        table: str = CANONICAL_TELEMETRY_TABLE,
    ) -> None:
        self.free_disk_bytes = int(free_disk_bytes)
        self.retained_table_bytes = int(retained_table_bytes)
        self.required_free_disk_bytes = int(required_free_disk_bytes)
        self.filesystem_path = filesystem_path
        self.table = table
        super().__init__(
            f"Refusing to start space reclaim for {table!r} on {filesystem_path!r}: "
            f"free disk ({self.free_disk_bytes} bytes) is below 1.5x retained table size "
            f"({self.required_free_disk_bytes} bytes required for {self.retained_table_bytes} bytes retained)"
        )


def validate_reclaim_tables(
    tables: Sequence[str] | str | None = None,
) -> tuple[str, ...]:
    """Validate and normalize a caller-supplied list of tables against ALLOWED_RECLAIM_TABLES."""
    if tables is None:
        return ()
    raw_items: list[str] = []
    if isinstance(tables, str):
        for part in tables.split(","):
            if part.strip():
                raw_items.append(part.strip())
    elif isinstance(tables, Sequence):
        for entry in tables:
            for part in str(entry or "").split(","):
                if part.strip():
                    raw_items.append(part.strip())
    else:
        raise ValueError(f"Invalid tables specification: {tables!r}")

    if not raw_items:
        raise ValueError(
            f"At least one table from allow-list {ALLOWED_RECLAIM_TABLES} must be specified"
        )

    normalized: list[str] = []
    for item in raw_items:
        if item not in ALLOWED_RECLAIM_TABLES:
            raise ValueError(
                f"Table {item!r} is not in the reclaim allow-list {ALLOWED_RECLAIM_TABLES}"
            )
        if item not in normalized:
            normalized.append(item)
    return tuple(normalized)


async def _resolve_effective_reclaim_tables(
    conn: Any,
    tables: Sequence[str] | str | None = None,
) -> tuple[str, ...]:
    explicit = validate_reclaim_tables(tables)
    if explicit:
        for tbl in explicit:
            reg = await conn.fetchval("SELECT to_regclass($1)", tbl)
            if reg is None:
                raise HeartbeatRetentionError(f"Requested reclaim table {tbl!r} does not exist")
        return explicit

    resolved = [CANONICAL_TELEMETRY_TABLE]
    receipts_reg = await conn.fetchval("SELECT to_regclass($1)", CANONICAL_EVENT_RECEIPTS_TABLE)
    if receipts_reg is not None:
        resolved.append(CANONICAL_EVENT_RECEIPTS_TABLE)
    return tuple(resolved)


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
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    text = str(dt).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def resolve_disk_usage(
    disk_path: str | Path | None = None,
    *,
    free_disk_bytes_override: int | None = None,
) -> dict[str, Any]:
    """Return total and free filesystem bytes for the target storage path."""
    if disk_path is None:
        candidate = Path("/var/lib/docker")
        target_path = candidate if candidate.exists() else Path("/")
    else:
        target_path = Path(disk_path)
        if not target_path.exists():
            target_path = Path("/")

    usage = shutil.disk_usage(str(target_path))
    free_bytes = (
        int(free_disk_bytes_override)
        if free_disk_bytes_override is not None
        else int(usage.free)
    )
    total_bytes = max(int(usage.total), free_bytes)
    return {
        "filesystem_path": str(target_path),
        "filesystem_total_bytes": total_bytes,
        "filesystem_free_bytes": free_bytes,
    }


async def _inspect_telemetry_events_plan(
    conn: Any,
    *,
    cutoff_dt: datetime,
    cutoff_iso: str,
    effective_days: float,
    infra_cutoff_dt: datetime,
    infra_cutoff_iso: str,
    effective_infra_days: float,
    telemetry_cutoff_dt: datetime,
    telemetry_cutoff_iso: str,
    effective_telemetry_days: float,
    safe_seq: int,
    protected_ids: list[str],
    anchor_ids: set[str],
    ceiling_info: Mapping[str, Any],
    reader_check: Mapping[str, Any],
    infra_reader_check: Mapping[str, Any],
    telemetry_reader_check: Mapping[str, Any],
    disk_info: Mapping[str, Any],
) -> dict[str, Any]:
    size_row = await conn.fetchrow(
        """
        SELECT pg_total_relation_size('public.telemetry_events')::bigint AS total_bytes,
               pg_relation_size('public.telemetry_events')::bigint AS heap_bytes,
               pg_indexes_size('public.telemetry_events')::bigint AS index_bytes,
               COALESCE(pg_stat_get_dead_tuples('public.telemetry_events'::regclass), 0)::bigint AS dead_tuples
        """
    )
    table_bytes_before = int(size_row["total_bytes"] or 0) if size_row else 0
    heap_bytes_before = int(size_row["heap_bytes"] or 0) if size_row else 0
    index_bytes_before = int(size_row["index_bytes"] or 0) if size_row else 0
    dead_tuples_before = max(0, int(size_row["dead_tuples"] or 0)) if size_row else 0
    toast_bytes_before = max(0, table_bytes_before - heap_bytes_before - index_bytes_before)

    all_protected_14d_ids = sorted(set(protected_ids) | set(anchor_ids))
    prunable_14d_sql = _telemetry_events_14d_prunable_where_sql(
        "e",
        cutoff_param="$5",
        safe_seq_param="$2",
        protected_ids_param="$6",
        permanent_types_param="$7",
        canonical_fill_types_param="$8",
    )

    counts_row = await conn.fetchrow(
        f"""
        SELECT COUNT(*)::bigint AS rows_total,
               COUNT(*) FILTER (WHERE e.event_type = 'heartbeat')::bigint AS heartbeat_rows_total,
               COUNT(*) FILTER (WHERE e.event_type = 'infrastructure_health')::bigint AS infra_health_rows_total,
               COUNT(*) FILTER (
                   WHERE e.event_type = 'heartbeat'
                     AND e.created_at < $1
                     AND e.ingested_seq <= $2
               )::bigint AS heartbeat_rows_to_delete,
               COUNT(*) FILTER (
                   WHERE e.event_type = 'infrastructure_health'
                     AND e.created_at < $3
                     AND e.ingested_seq <= $2
                     AND NOT (e.event_id = ANY($4::text[]))
               )::bigint AS infra_health_rows_to_delete,
               COUNT(*) FILTER (
                   WHERE {prunable_14d_sql}
               )::bigint AS telemetry_events_rows_to_delete,
               COALESCE(SUM(octet_length(e.payload::text)), 0)::bigint AS payload_bytes_total,
               COALESCE(
                   SUM(octet_length(e.payload::text)) FILTER (
                       WHERE (
                           e.event_type = 'heartbeat'
                           AND e.created_at < $1
                           AND e.ingested_seq <= $2
                       )
                       OR (
                           e.event_type = 'infrastructure_health'
                           AND e.created_at < $3
                           AND e.ingested_seq <= $2
                           AND NOT (e.event_id = ANY($4::text[]))
                       )
                       OR (
                           {prunable_14d_sql}
                       )
                   ),
                   0
               )::bigint AS payload_bytes_to_delete,
               COUNT(
                   DISTINCT CASE
                       WHEN e.event_type = 'heartbeat' AND e.created_at < $1 AND e.ingested_seq <= $2 THEN
                           COALESCE(
                               NULLIF(trim(e.payload->>'runtime_id'), ''),
                               NULLIF(trim(e.payload->'metadata'->>'runtime_id'), ''),
                               'unknown'
                           ) || '|' || (date_trunc('hour', e.created_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')::text
                       ELSE NULL
                   END
               )::bigint AS hours_to_summarize,
               MIN(e.created_at) AS min_created_at,
               MAX(e.created_at) AS max_created_at
        FROM public.telemetry_events e
        """,
        cutoff_dt,
        safe_seq,
        infra_cutoff_dt,
        protected_ids,
        telemetry_cutoff_dt,
        all_protected_14d_ids,
        list(PERMANENT_TELEMETRY_EVENT_TYPES),
        list(CANONICAL_FILL_AND_REJECTION_EVENT_TYPES),
    )

    rows_total = int(counts_row["rows_total"] or 0) if counts_row else 0
    heartbeat_rows_total = int(counts_row["heartbeat_rows_total"] or 0) if counts_row else 0
    infra_health_rows_total = int(counts_row["infra_health_rows_total"] or 0) if counts_row else 0
    heartbeat_rows_to_delete = int(counts_row["heartbeat_rows_to_delete"] or 0) if counts_row else 0
    infra_health_rows_to_delete = (
        int(counts_row["infra_health_rows_to_delete"] or 0) if counts_row else 0
    )
    telemetry_events_rows_to_delete = (
        int(counts_row["telemetry_events_rows_to_delete"] or 0) if counts_row else 0
    )
    rows_to_delete = (
        heartbeat_rows_to_delete
        + infra_health_rows_to_delete
        + telemetry_events_rows_to_delete
    )
    rows_to_retain = max(0, rows_total - rows_to_delete)
    heartbeat_rows_to_retain = max(0, heartbeat_rows_total - heartbeat_rows_to_delete)
    infra_health_rows_to_retain = max(0, infra_health_rows_total - infra_health_rows_to_delete)
    non_heartbeat_rows_total = max(0, rows_total - heartbeat_rows_total)
    payload_bytes_total = int(counts_row["payload_bytes_total"] or 0) if counts_row else 0
    payload_bytes_to_delete = int(counts_row["payload_bytes_to_delete"] or 0) if counts_row else 0
    hours_to_summarize = int(counts_row["hours_to_summarize"] or 0) if counts_row else 0
    min_created_at = _format_rfc3339(counts_row["min_created_at"]) if counts_row else None
    max_created_at = _format_rfc3339(counts_row["max_created_at"]) if counts_row else None

    summary_regclass = await conn.fetchval("SELECT to_regclass($1)", HEARTBEAT_SUMMARY_TABLE)
    if summary_regclass is not None:
        existing_summary_rows = int(
            await conn.fetchval(f"SELECT COUNT(*)::bigint FROM {HEARTBEAT_SUMMARY_TABLE}") or 0
        )
    else:
        existing_summary_rows = 0

    type_rows = await conn.fetch(
        """
        SELECT event_type,
               COUNT(*)::bigint AS row_count,
               COALESCE(SUM(octet_length(payload::text)), 0)::bigint AS payload_bytes
        FROM public.telemetry_events
        GROUP BY event_type
        ORDER BY row_count DESC, event_type ASC
        """
    )
    event_type_breakdown = [
        {
            "event_type": str(r["event_type"]),
            "row_count": int(r["row_count"] or 0),
            "payload_bytes": int(r["payload_bytes"] or 0),
        }
        for r in type_rows
    ]

    if table_bytes_before == 0:
        bytes_to_delete = 0
    elif rows_to_delete > 0 and payload_bytes_total > 0:
        bytes_to_delete = int(
            round(table_bytes_before * (payload_bytes_to_delete / payload_bytes_total))
        )
    elif rows_to_delete > 0 and rows_total > 0:
        bytes_to_delete = int(round(table_bytes_before * (rows_to_delete / rows_total)))
    elif rows_to_delete == 0 and dead_tuples_before > 0 and (rows_total + dead_tuples_before) > 0:
        bytes_to_delete = int(
            round(table_bytes_before * (dead_tuples_before / (rows_total + dead_tuples_before)))
        )
    else:
        bytes_to_delete = 0

    bytes_to_delete = min(table_bytes_before, max(0, bytes_to_delete))
    retained_table_bytes = max(0, table_bytes_before - bytes_to_delete)
    required_free_disk_bytes = int(
        math.ceil(FREE_DISK_SAFETY_MULTIPLIER * retained_table_bytes)
    )

    filesystem_free_before = int(disk_info["filesystem_free_bytes"])
    expected_filesystem_free_after = filesystem_free_before + bytes_to_delete
    free_disk_check_passed = filesystem_free_before >= required_free_disk_bytes

    return {
        "mode": "dry_run",
        "table": CANONICAL_TELEMETRY_TABLE,
        "summary_table": HEARTBEAT_SUMMARY_TABLE,
        "retention_days": effective_days,
        "infra_health_retention_days": effective_infra_days,
        "telemetry_events_retention_days": effective_telemetry_days,
        "cutoff_at": cutoff_iso,
        "infra_health_cutoff_at": infra_cutoff_iso,
        "telemetry_events_cutoff_at": telemetry_cutoff_iso,
        "safe_ingested_seq": safe_seq,
        "consumer_checkpoints": dict(ceiling_info["consumer_checkpoints"]),
        "ingested_seq_consumers": list(INGESTED_SEQ_CONSUMERS),
        "reader_check": dict(reader_check),
        "infra_health_reader_check": dict(infra_reader_check),
        "telemetry_events_reader_check": dict(telemetry_reader_check),
        "protected_infra_health_event_ids_count": len(protected_ids),
        "protected_infra_health_event_ids": list(protected_ids),
        "protected_telemetry_anchor_event_ids_count": len(anchor_ids),
        "rows_total": rows_total,
        "heartbeat_rows_total": heartbeat_rows_total,
        "infra_health_rows_total": infra_health_rows_total,
        "rows_to_delete": rows_to_delete,
        "heartbeat_rows_to_delete": heartbeat_rows_to_delete,
        "infra_health_rows_to_delete": infra_health_rows_to_delete,
        "telemetry_events_rows_to_delete": telemetry_events_rows_to_delete,
        "rows_to_retain": rows_to_retain,
        "heartbeat_rows_to_retain": heartbeat_rows_to_retain,
        "infra_health_rows_to_retain": infra_health_rows_to_retain,
        "non_heartbeat_rows_total": non_heartbeat_rows_total,
        "dead_tuples_before": dead_tuples_before,
        "hours_to_summarize": hours_to_summarize,
        "existing_summary_rows": existing_summary_rows,
        "min_created_at": min_created_at,
        "max_created_at": max_created_at,
        "table_bytes_before": table_bytes_before,
        "heap_bytes_before": heap_bytes_before,
        "index_bytes_before": index_bytes_before,
        "toast_bytes_before": toast_bytes_before,
        "payload_bytes_total": payload_bytes_total,
        "payload_bytes_to_delete": payload_bytes_to_delete,
        "bytes_to_delete": bytes_to_delete,
        "retained_table_bytes": retained_table_bytes,
        "required_free_disk_bytes": required_free_disk_bytes,
        "free_disk_safety_multiplier": FREE_DISK_SAFETY_MULTIPLIER,
        "filesystem_path": disk_info["filesystem_path"],
        "filesystem_total_bytes": disk_info["filesystem_total_bytes"],
        "filesystem_free_bytes_before": filesystem_free_before,
        "expected_filesystem_free_bytes_after": expected_filesystem_free_after,
        "free_disk_check_passed": free_disk_check_passed,
        "event_type_breakdown": event_type_breakdown,
    }


async def _inspect_event_receipts_plan(
    conn: Any,
    *,
    disk_info: Mapping[str, Any],
) -> dict[str, Any]:
    size_row = await conn.fetchrow(
        """
        SELECT pg_total_relation_size('trade_journey_projection.event_receipts')::bigint AS total_bytes,
               pg_relation_size('trade_journey_projection.event_receipts')::bigint AS heap_bytes,
               pg_indexes_size('trade_journey_projection.event_receipts')::bigint AS index_bytes,
               COALESCE(pg_stat_get_dead_tuples('trade_journey_projection.event_receipts'::regclass), 0)::bigint AS dead_tuples
        """
    )
    table_bytes_before = int(size_row["total_bytes"] or 0) if size_row else 0
    heap_bytes_before = int(size_row["heap_bytes"] or 0) if size_row else 0
    index_bytes_before = int(size_row["index_bytes"] or 0) if size_row else 0
    dead_tuples_before = max(0, int(size_row["dead_tuples"] or 0)) if size_row else 0
    toast_bytes_before = max(0, table_bytes_before - heap_bytes_before - index_bytes_before)

    counts_row = await conn.fetchrow(
        """
        SELECT COUNT(*)::bigint AS rows_total,
               COALESCE(SUM(pg_column_size(r.*)), 0)::bigint AS live_row_bytes,
               MIN(created_at) AS min_created_at,
               MAX(created_at) AS max_created_at
        FROM trade_journey_projection.event_receipts r
        """
    )
    rows_total = int(counts_row["rows_total"] or 0) if counts_row else 0
    live_row_bytes = int(counts_row["live_row_bytes"] or 0) if counts_row else 0
    min_created_at = _format_rfc3339(counts_row["min_created_at"]) if counts_row else None
    max_created_at = _format_rfc3339(counts_row["max_created_at"]) if counts_row else None

    disp_rows = await conn.fetch(
        """
        SELECT disposition,
               COUNT(*)::bigint AS row_count
        FROM trade_journey_projection.event_receipts
        GROUP BY disposition
        ORDER BY row_count DESC, disposition ASC
        """
    )
    disposition_breakdown = [
        {
            "disposition": str(r["disposition"]),
            "row_count": int(r["row_count"] or 0),
        }
        for r in disp_rows
    ]

    if table_bytes_before == 0:
        bytes_to_delete = 0
    elif rows_total == 0:
        bytes_to_delete = table_bytes_before
    elif dead_tuples_before > 0:
        bytes_to_delete = int(
            round(table_bytes_before * (dead_tuples_before / (rows_total + dead_tuples_before)))
        )
    else:
        bytes_to_delete = 0

    bytes_to_delete = min(table_bytes_before, max(0, bytes_to_delete))
    retained_table_bytes = max(0, table_bytes_before - bytes_to_delete)
    required_free_disk_bytes = int(
        math.ceil(FREE_DISK_SAFETY_MULTIPLIER * retained_table_bytes)
    )

    filesystem_free_before = int(disk_info["filesystem_free_bytes"])
    expected_filesystem_free_after = filesystem_free_before + bytes_to_delete
    free_disk_check_passed = filesystem_free_before >= required_free_disk_bytes

    return {
        "mode": "dry_run",
        "table": CANONICAL_EVENT_RECEIPTS_TABLE,
        "rows_total": rows_total,
        "rows_to_delete": 0,
        "rows_to_retain": rows_total,
        "dead_tuples_before": dead_tuples_before,
        "live_row_bytes": live_row_bytes,
        "min_created_at": min_created_at,
        "max_created_at": max_created_at,
        "table_bytes_before": table_bytes_before,
        "heap_bytes_before": heap_bytes_before,
        "index_bytes_before": index_bytes_before,
        "toast_bytes_before": toast_bytes_before,
        "bytes_to_delete": bytes_to_delete,
        "retained_table_bytes": retained_table_bytes,
        "required_free_disk_bytes": required_free_disk_bytes,
        "free_disk_safety_multiplier": FREE_DISK_SAFETY_MULTIPLIER,
        "filesystem_path": disk_info["filesystem_path"],
        "filesystem_total_bytes": disk_info["filesystem_total_bytes"],
        "filesystem_free_bytes_before": filesystem_free_before,
        "expected_filesystem_free_bytes_after": expected_filesystem_free_after,
        "free_disk_check_passed": free_disk_check_passed,
        "disposition_breakdown": disposition_breakdown,
    }


async def inspect_reclaim_plan(
    conn: Any,
    *,
    tables: Sequence[str] | str | None = None,
    now: datetime | None = None,
    retention_days: float | None = None,
    infra_health_retention_days: float | None = None,
    telemetry_events_retention_days: float | None = None,
    disk_path: str | Path | None = None,
    free_disk_bytes_override: int | None = None,
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
) -> dict[str, Any]:
    """Non-destructively inspect allow-listed tables and compute the reclaim plan."""
    selected_tables = await _resolve_effective_reclaim_tables(conn, tables)
    effective_days = parse_retention_days(retention_days)
    effective_infra_days = parse_infra_health_retention_days(infra_health_retention_days)
    effective_telemetry_days = parse_telemetry_events_retention_days(
        telemetry_events_retention_days
    )
    reference_now = _coerce_utc(now) if now is not None else _utc_now()
    cutoff_dt = reference_now - timedelta(days=effective_days)
    cutoff_iso = _format_rfc3339(cutoff_dt) or ""
    infra_cutoff_dt = reference_now - timedelta(days=effective_infra_days)
    infra_cutoff_iso = _format_rfc3339(infra_cutoff_dt) or ""
    telemetry_cutoff_dt = reference_now - timedelta(days=effective_telemetry_days)
    telemetry_cutoff_iso = _format_rfc3339(telemetry_cutoff_dt) or ""

    reader_check = check_readers_against_retention_window(effective_days, readers=readers)
    infra_reader_check = check_infra_health_readers_against_retention_window(
        effective_infra_days,
        readers=infra_health_readers,
    )
    telemetry_reader_check = check_telemetry_events_readers_against_retention_window(
        effective_telemetry_days,
        readers=telemetry_events_readers,
    )
    ceiling_info = await resolve_safe_ingested_seq_ceiling(
        conn,
        projection_schema=projection_schema,
        extra_consumer_checkpoints=extra_consumer_checkpoints,
        require_controller_checkpoint=require_controller_checkpoint,
    )
    safe_seq = int(ceiling_info["safe_ingested_seq"])

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
    anchor_ids: set[str] = set()
    if CANONICAL_TELEMETRY_TABLE in selected_tables:
        anchor_ids = await resolve_protected_telemetry_anchor_event_ids(conn)

    disk_info = resolve_disk_usage(
        disk_path,
        free_disk_bytes_override=free_disk_bytes_override,
    )

    table_plans: dict[str, dict[str, Any]] = {}
    for tbl in selected_tables:
        if tbl == CANONICAL_TELEMETRY_TABLE:
            table_plans[tbl] = await _inspect_telemetry_events_plan(
                conn,
                cutoff_dt=cutoff_dt,
                cutoff_iso=cutoff_iso,
                effective_days=effective_days,
                infra_cutoff_dt=infra_cutoff_dt,
                infra_cutoff_iso=infra_cutoff_iso,
                effective_infra_days=effective_infra_days,
                telemetry_cutoff_dt=telemetry_cutoff_dt,
                telemetry_cutoff_iso=telemetry_cutoff_iso,
                effective_telemetry_days=effective_telemetry_days,
                safe_seq=safe_seq,
                protected_ids=protected_ids,
                anchor_ids=anchor_ids,
                ceiling_info=ceiling_info,
                reader_check=reader_check,
                infra_reader_check=infra_reader_check,
                telemetry_reader_check=telemetry_reader_check,
                disk_info=disk_info,
            )
        elif tbl == CANONICAL_EVENT_RECEIPTS_TABLE:
            table_plans[tbl] = await _inspect_event_receipts_plan(
                conn,
                disk_info=disk_info,
            )

    primary_table = (
        CANONICAL_TELEMETRY_TABLE
        if CANONICAL_TELEMETRY_TABLE in table_plans
        else selected_tables[0]
    )
    primary_plan = dict(table_plans[primary_table])
    all_free_disk_passed = all(
        bool(tp["free_disk_check_passed"]) for tp in table_plans.values()
    )

    return {
        **primary_plan,
        "mode": "dry_run",
        "table": primary_table,
        "allowed_tables": list(ALLOWED_RECLAIM_TABLES),
        "tables": list(selected_tables),
        "table_plans": table_plans,
        "free_disk_check_passed": all_free_disk_passed,
    }


async def execute_space_reclaim(
    conn: Any,
    *,
    table: str = CANONICAL_TELEMETRY_TABLE,
    dsn: str = "",
    method: str = "vacuum_full",
    lock_timeout_ms: int = 15000,
    statement_timeout_ms: int = 300000,
    on_lock_acquired: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Return deleted table space to the filesystem with a standard Postgres mechanism.

    Supports allow-listed tables (``public.telemetry_events`` and
    ``trade_journey_projection.event_receipts``):
    - ``vacuum_full``: ``VACUUM (FULL, ANALYZE) <table>``
    - ``table_rewrite``: ``VACUUM (FULL, ANALYZE) <table>``
    - ``pg_repack``: runs ``pg_repack --no-kill-backend --table <table>``
    """
    validated_table = validate_reclaim_tables((table,))[0]
    clean_method = str(method or "vacuum_full").strip().lower()
    if clean_method not in SUPPORTED_RECLAIM_METHODS:
        raise ValueError(
            f"Unsupported reclaim method {method!r}; must be one of {SUPPORTED_RECLAIM_METHODS}"
        )

    eff_lock_ms = max(100, int(lock_timeout_ms))
    eff_stmt_ms = max(1000, int(statement_timeout_ms))
    started_at = _utc_now()
    t0 = time.monotonic()

    if clean_method == "pg_repack":
        repack_bin = shutil.which("pg_repack")
        if not repack_bin:
            raise HeartbeatRetentionError("pg_repack binary is not installed in PATH")
        cmd = [
            repack_bin,
            "--no-kill-backend",
            "--wait-timeout",
            str(max(1, eff_lock_ms // 1000)),
            "--table",
            validated_table,
        ]
        if dsn:
            cmd.extend(["--dbname", dsn])
        await asyncio.to_thread(
            subprocess.run,
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=(eff_stmt_ms / 1000.0) + 10.0,
        )
        mechanism_sql = f"pg_repack --no-kill-backend --table {validated_table}"
    else:
        await conn.execute(f"SET lock_timeout = '{eff_lock_ms}ms'")
        await conn.execute(f"SET statement_timeout = '{eff_stmt_ms}ms'")
        try:
            if on_lock_acquired is not None:
                maybe_coro = on_lock_acquired()
                if inspect.isawaitable(maybe_coro):
                    await maybe_coro
            await conn.execute(
                f"VACUUM (FULL, ANALYZE) {validated_table}",
                timeout=(eff_stmt_ms / 1000.0) + 10.0,
            )
        finally:
            try:
                await conn.execute("RESET lock_timeout")
                await conn.execute("RESET statement_timeout")
            except Exception:  # noqa: BLE001
                pass
        mechanism_sql = f"VACUUM (FULL, ANALYZE) {validated_table}"

    duration_ms = (time.monotonic() - t0) * 1000.0
    completed_at = _utc_now()
    return {
        "table": validated_table,
        "method": clean_method,
        "mechanism_sql": mechanism_sql,
        "lock_timeout_ms": eff_lock_ms,
        "statement_timeout_ms": eff_stmt_ms,
        "lock_window_started_at": _format_rfc3339(started_at),
        "lock_window_completed_at": _format_rfc3339(completed_at),
        "lock_window_ms": round(duration_ms, 3),
    }


async def run_reclaim_on_connection(
    conn: Any,
    *,
    tables: Sequence[str] | str | None = None,
    dsn: str = "",
    apply: bool = False,
    now: datetime | None = None,
    retention_days: float | None = None,
    infra_health_retention_days: float | None = None,
    telemetry_events_retention_days: float | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int | None = None,
    statement_timeout_ms: int = 30000,
    reclaim_lock_timeout_ms: int = 15000,
    reclaim_statement_timeout_ms: int = 300000,
    method: str = "vacuum_full",
    disk_path: str | Path | None = None,
    free_disk_bytes_override: int | None = None,
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
    on_lock_acquired: Callable[[], Any] | None = None,
    replay_dlq_callback: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Run dry-run or apply mode space reclaim on allow-listed tables over an open connection."""
    plan = await inspect_reclaim_plan(
        conn,
        tables=tables,
        now=now,
        retention_days=retention_days,
        infra_health_retention_days=infra_health_retention_days,
        telemetry_events_retention_days=telemetry_events_retention_days,
        disk_path=disk_path,
        free_disk_bytes_override=free_disk_bytes_override,
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
    if not apply:
        return plan

    selected_tables = list(plan["tables"])
    table_plans = plan["table_plans"]

    for tbl in selected_tables:
        tp = table_plans[tbl]
        if not tp["free_disk_check_passed"]:
            raise InsufficientDiskSpaceError(
                free_disk_bytes=int(tp["filesystem_free_bytes_before"]),
                retained_table_bytes=int(tp["retained_table_bytes"]),
                required_free_disk_bytes=int(tp["required_free_disk_bytes"]),
                filesystem_path=str(tp["filesystem_path"]),
                table=tbl,
            )

    table_results: dict[str, dict[str, Any]] = {}
    retention_dict: dict[str, Any] | None = None
    cumulative_reclaimed = 0

    for idx, tbl in enumerate(selected_tables):
        tp = table_plans[tbl]
        current_disk = resolve_disk_usage(
            disk_path,
            free_disk_bytes_override=(
                int(free_disk_bytes_override) + cumulative_reclaimed
                if free_disk_bytes_override is not None
                else None
            ),
        )
        current_free = int(current_disk["filesystem_free_bytes"])
        if current_free < int(tp["required_free_disk_bytes"]):
            raise InsufficientDiskSpaceError(
                free_disk_bytes=current_free,
                retained_table_bytes=int(tp["retained_table_bytes"]),
                required_free_disk_bytes=int(tp["required_free_disk_bytes"]),
                filesystem_path=str(current_disk["filesystem_path"]),
                table=tbl,
            )

        if tbl == CANONICAL_TELEMETRY_TABLE:
            await ensure_heartbeat_summary_schema(conn)
            retention_result = await run_heartbeat_retention_on_connection(
                conn,
                now=now,
                retention_days=plan["retention_days"],
                infra_health_retention_days=plan["infra_health_retention_days"],
                telemetry_events_retention_days=plan["telemetry_events_retention_days"],
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
            retention_dict = retention_result.to_dict()

        reclaim_info = await execute_space_reclaim(
            conn,
            table=tbl,
            dsn=dsn,
            method=method,
            lock_timeout_ms=reclaim_lock_timeout_ms,
            statement_timeout_ms=reclaim_statement_timeout_ms,
            on_lock_acquired=on_lock_acquired if idx == 0 else None,
        )

        after_size_row = await conn.fetchrow(
            f"""
            SELECT pg_total_relation_size('{tbl}')::bigint AS total_bytes,
                   pg_relation_size('{tbl}')::bigint AS heap_bytes,
                   pg_indexes_size('{tbl}')::bigint AS index_bytes
            """
        )
        t_bytes_after = int(after_size_row["total_bytes"] or 0) if after_size_row else 0
        h_bytes_after = int(after_size_row["heap_bytes"] or 0) if after_size_row else 0
        i_bytes_after = int(after_size_row["index_bytes"] or 0) if after_size_row else 0
        t_reclaimed = max(0, int(tp["table_bytes_before"]) - t_bytes_after)
        cumulative_reclaimed += t_reclaimed

        disk_after_tbl = resolve_disk_usage(
            disk_path,
            free_disk_bytes_override=(
                int(free_disk_bytes_override) + cumulative_reclaimed
                if free_disk_bytes_override is not None
                else None
            ),
        )

        if tbl == CANONICAL_TELEMETRY_TABLE:
            after_counts_row = await conn.fetchrow(
                """
                SELECT COUNT(*)::bigint AS rows_after,
                       COUNT(*) FILTER (WHERE event_type = 'heartbeat')::bigint AS heartbeat_rows_after,
                       COUNT(*) FILTER (WHERE event_type = 'infrastructure_health')::bigint AS infra_health_rows_after
                FROM public.telemetry_events
                """
            )
            r_after = int(after_counts_row["rows_after"] or 0) if after_counts_row else 0
            hb_after = int(after_counts_row["heartbeat_rows_after"] or 0) if after_counts_row else 0
            inf_after = int(after_counts_row["infra_health_rows_after"] or 0) if after_counts_row else 0
            non_hb_after = max(0, r_after - hb_after)
            sum_after = int(
                await conn.fetchval(f"SELECT COUNT(*)::bigint FROM {HEARTBEAT_SUMMARY_TABLE}") or 0
            )
            table_results[tbl] = {
                **tp,
                "mode": "apply",
                "retention": retention_dict,
                "reclaim": reclaim_info,
                "rows_deleted_actual": retention_dict["rows_deleted"] if retention_dict else 0,
                "heartbeat_rows_deleted_actual": (
                    retention_dict["heartbeat_rows_deleted"] if retention_dict else 0
                ),
                "infra_health_rows_deleted_actual": (
                    retention_dict["infra_health_rows_deleted"] if retention_dict else 0
                ),
                "telemetry_events_rows_deleted_actual": (
                    retention_dict["telemetry_events_rows_deleted"] if retention_dict else 0
                ),
                "hours_summarized_actual": (
                    retention_dict["hours_summarized"] if retention_dict else 0
                ),
                "rows_after": r_after,
                "heartbeat_rows_after": hb_after,
                "infra_health_rows_after": inf_after,
                "non_heartbeat_rows_after": non_hb_after,
                "summary_rows_after": sum_after,
                "table_bytes_after": t_bytes_after,
                "heap_bytes_after": h_bytes_after,
                "index_bytes_after": i_bytes_after,
                "actual_bytes_reclaimed": t_reclaimed,
                "filesystem_free_bytes_after": int(disk_after_tbl["filesystem_free_bytes"]),
            }
        else:
            r_after = int(await conn.fetchval(f"SELECT COUNT(*)::bigint FROM {tbl}") or 0)
            table_results[tbl] = {
                **tp,
                "mode": "apply",
                "reclaim": reclaim_info,
                "rows_deleted_actual": 0,
                "rows_after": r_after,
                "table_bytes_after": t_bytes_after,
                "heap_bytes_after": h_bytes_after,
                "index_bytes_after": i_bytes_after,
                "actual_bytes_reclaimed": t_reclaimed,
                "filesystem_free_bytes_after": int(disk_after_tbl["filesystem_free_bytes"]),
            }

    dlq_replayed = 0
    if replay_dlq_callback is not None:
        maybe_replayed = replay_dlq_callback()
        if inspect.isawaitable(maybe_replayed):
            maybe_replayed = await maybe_replayed
        dlq_replayed = int(maybe_replayed or 0)

    primary_table = str(plan["table"])
    primary_result = dict(table_results[primary_table])

    return {
        **plan,
        **primary_result,
        "mode": "apply",
        "table": primary_table,
        "allowed_tables": list(ALLOWED_RECLAIM_TABLES),
        "tables": selected_tables,
        "table_plans": table_plans,
        "table_results": table_results,
        "dlq_replayed_count": dlq_replayed,
        "total_actual_bytes_reclaimed": cumulative_reclaimed,
    }


async def run_reclaim(
    dsn: str,
    *,
    tables: Sequence[str] | str | None = None,
    apply: bool = False,
    now: datetime | None = None,
    retention_days: float | None = None,
    infra_health_retention_days: float | None = None,
    telemetry_events_retention_days: float | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int | None = None,
    statement_timeout_ms: int = 30000,
    reclaim_lock_timeout_ms: int = 15000,
    reclaim_statement_timeout_ms: int = 300000,
    method: str = "vacuum_full",
    disk_path: str | Path | None = None,
    free_disk_bytes_override: int | None = None,
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
    on_lock_acquired: Callable[[], Any] | None = None,
    replay_dlq_callback: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Connect to PostgreSQL and execute dry-run or apply reclaim."""
    validate_reclaim_tables(tables)
    import asyncpg  # type: ignore[import]

    conn = await asyncpg.connect(dsn)
    try:
        return await run_reclaim_on_connection(
            conn,
            tables=tables,
            dsn=dsn,
            apply=apply,
            now=now,
            retention_days=retention_days,
            infra_health_retention_days=infra_health_retention_days,
            telemetry_events_retention_days=telemetry_events_retention_days,
            batch_size=batch_size,
            max_batches=max_batches,
            statement_timeout_ms=statement_timeout_ms,
            reclaim_lock_timeout_ms=reclaim_lock_timeout_ms,
            reclaim_statement_timeout_ms=reclaim_statement_timeout_ms,
            method=method,
            disk_path=disk_path,
            free_disk_bytes_override=free_disk_bytes_override,
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
            on_lock_acquired=on_lock_acquired,
            replay_dlq_callback=replay_dlq_callback,
        )
    finally:
        await conn.close()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or reclaim space from allow-listed tables (public.telemetry_events and trade_journey_projection.event_receipts)."
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Report rows and bytes to delete and expected free disk space without mutating (default).",
    )
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Summarize hours, delete expired raw heartbeats, infrastructure_health, and >14d prunable telemetry_events rows in batches, and reclaim disk space.",
    )
    parser.add_argument(
        "--dsn",
        default=(
            os.environ.get("TELEMETRY_DB_DSN")
            or os.environ.get("DATABASE_URL")
            or "postgresql://pantheon_app:pantheon_app@localhost:15432/pantheon"
        ),
        help="PostgreSQL connection DSN (defaults to TELEMETRY_DB_DSN / DATABASE_URL).",
    )
    parser.add_argument(
        "--tables",
        "--table",
        dest="tables",
        nargs="+",
        default=None,
        help=(
            f"Explicit allow-list of tables to inspect/reclaim "
            f"(allowed: {', '.join(ALLOWED_RECLAIM_TABLES)})."
        ),
    )
    parser.add_argument(
        "--retention-days",
        type=float,
        default=None,
        help=f"Raw heartbeat retention window in days (defaults to {RAW_RETENTION_DAYS_ENV} or {int(DEFAULT_RAW_RETENTION_DAYS)}).",
    )
    parser.add_argument(
        "--infra-health-retention-days",
        type=float,
        default=None,
        help=f"Raw infrastructure_health retention window in days (defaults to {INFRA_HEALTH_RETENTION_DAYS_ENV} or {int(DEFAULT_INFRA_HEALTH_RETENTION_DAYS)}).",
    )
    parser.add_argument(
        "--telemetry-events-retention-days",
        type=float,
        default=None,
        help=f"Unreferenced non-permanent telemetry_events retention window in days (defaults to {TELEMETRY_EVENTS_RETENTION_DAYS_ENV} or {int(DEFAULT_TELEMETRY_EVENTS_RETENTION_DAYS)}).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Maximum rows deleted per batch transaction (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help="Optional cap on batches executed in apply mode (default: unlimited until backlog is cleared).",
    )
    parser.add_argument(
        "--statement-timeout-ms",
        type=int,
        default=30000,
        help="Per-batch statement timeout in milliseconds (default: 30000).",
    )
    parser.add_argument(
        "--reclaim-lock-timeout-ms",
        type=int,
        default=15000,
        help="Lock timeout in milliseconds for VACUUM FULL / table rewrite (default: 15000).",
    )
    parser.add_argument(
        "--reclaim-statement-timeout-ms",
        type=int,
        default=300000,
        help="Statement timeout in milliseconds for VACUUM FULL / table rewrite (default: 300000).",
    )
    parser.add_argument(
        "--method",
        choices=SUPPORTED_RECLAIM_METHODS,
        default="vacuum_full",
        help="Postgres physical space reclaim mechanism (default: vacuum_full).",
    )
    parser.add_argument(
        "--disk-path",
        default=None,
        help="Filesystem path used to check free disk space (defaults to /var/lib/docker or /).",
    )
    parser.add_argument(
        "--free-disk-bytes",
        type=int,
        default=None,
        help="Optional explicit override for current free disk bytes.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional RFC3339 reference timestamp for deterministic cutoff calculation.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional file path to write the JSON report.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    now_dt = _coerce_utc(args.now) if args.now else None
    try:
        validate_reclaim_tables(args.tables)
        report = asyncio.run(
            run_reclaim(
                args.dsn,
                tables=args.tables,
                apply=bool(args.apply),
                now=now_dt,
                retention_days=args.retention_days,
                infra_health_retention_days=args.infra_health_retention_days,
                telemetry_events_retention_days=args.telemetry_events_retention_days,
                batch_size=args.batch_size,
                max_batches=args.max_batches,
                statement_timeout_ms=args.statement_timeout_ms,
                reclaim_lock_timeout_ms=args.reclaim_lock_timeout_ms,
                reclaim_statement_timeout_ms=args.reclaim_statement_timeout_ms,
                method=args.method,
                disk_path=args.disk_path,
                free_disk_bytes_override=args.free_disk_bytes,
            )
        )
    except (InsufficientDiskSpaceError, HeartbeatRetentionReaderBlocker) as exc:
        err_payload = {
            "status": "refused",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        if isinstance(exc, InsufficientDiskSpaceError):
            err_payload.update(
                {
                    "table": exc.table,
                    "filesystem_path": exc.filesystem_path,
                    "free_disk_bytes": exc.free_disk_bytes,
                    "retained_table_bytes": exc.retained_table_bytes,
                    "required_free_disk_bytes": exc.required_free_disk_bytes,
                }
            )
        elif isinstance(exc, HeartbeatRetentionReaderBlocker):
            err_payload.update(
                {
                    "blocking_reader": exc.reader_id,
                    "required_seconds": exc.required_seconds,
                    "retention_window_seconds": exc.retention_window_seconds,
                }
            )
        print(json.dumps(err_payload, indent=2, sort_keys=True), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1

    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
