#!/usr/bin/env python3
"""One-time and operator-triggered telemetry heartbeat backlog reclaim tool.

TELEMETRY-HEARTBEAT-RETENTION-20261117:
- ``--dry-run`` (default): non-destructively inspects ``public.telemetry_events``
  and reports rows to delete, bytes to delete, retained table bytes, required
  1.5x free disk floor, current filesystem free bytes, expected filesystem free
  bytes after reclaim, per-event-type breakdown, consumer checkpoints, and
  reader retention window verification.
- ``--apply``:
  1. Refuses to start when free disk is below 1.5x the retained table size
     (``InsufficientDiskSpaceError``) or when any heartbeat reader needs raw
     heartbeats older than the window (``HeartbeatRetentionReaderBlocker``).
  2. Summarises each ``(runtime_id, hour_bucket)`` in UTC into
     ``public.telemetry_heartbeat_hourly_summaries`` and deletes raw
     ``event_type = 'heartbeat'`` rows older than the retention window in
     bounded batches under a statement timeout.
  3. Returns space to the filesystem with a standard Postgres mechanism
     (``VACUUM (FULL, ANALYZE) public.telemetry_events`` by default, or
     ``pg_repack`` / ``table_rewrite``) under a bounded, measured lock window.
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
from typing import Any, Callable, Mapping, Sequence

from services.telemetry.heartbeat_retention import (
    CANONICAL_TELEMETRY_TABLE,
    DEFAULT_BATCH_SIZE,
    DEFAULT_RAW_RETENTION_DAYS,
    HEARTBEAT_EVENT_TYPE,
    HEARTBEAT_READERS,
    HEARTBEAT_SUMMARY_TABLE,
    INGESTED_SEQ_CONSUMERS,
    RAW_RETENTION_DAYS_ENV,
    HeartbeatRetentionError,
    HeartbeatRetentionReaderBlocker,
    check_readers_against_retention_window,
    ensure_heartbeat_summary_schema,
    parse_retention_days,
    resolve_safe_ingested_seq_ceiling,
    run_heartbeat_retention_on_connection,
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
    ) -> None:
        self.free_disk_bytes = int(free_disk_bytes)
        self.retained_table_bytes = int(retained_table_bytes)
        self.required_free_disk_bytes = int(required_free_disk_bytes)
        self.filesystem_path = filesystem_path
        super().__init__(
            f"Refusing to start telemetry heartbeat reclaim on {filesystem_path!r}: "
            f"free disk ({self.free_disk_bytes} bytes) is below 1.5x retained table size "
            f"({self.required_free_disk_bytes} bytes required for {self.retained_table_bytes} bytes retained)"
        )


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


async def inspect_reclaim_plan(
    conn: Any,
    *,
    now: datetime | None = None,
    retention_days: float | None = None,
    disk_path: str | Path | None = None,
    free_disk_bytes_override: int | None = None,
    projection_schema: str = "trade_journey_projection",
    extra_consumer_checkpoints: Mapping[str, int] | None = None,
    require_controller_checkpoint: bool = False,
    readers: Sequence[Mapping[str, Any]] = HEARTBEAT_READERS,
) -> dict[str, Any]:
    """Non-destructively inspect public.telemetry_events and compute the reclaim plan."""
    effective_days = parse_retention_days(retention_days)
    reference_now = _coerce_utc(now) if now is not None else _utc_now()
    cutoff_dt = reference_now - timedelta(days=effective_days)
    cutoff_iso = _format_rfc3339(cutoff_dt) or ""

    reader_check = check_readers_against_retention_window(effective_days, readers=readers)
    ceiling_info = await resolve_safe_ingested_seq_ceiling(
        conn,
        projection_schema=projection_schema,
        extra_consumer_checkpoints=extra_consumer_checkpoints,
        require_controller_checkpoint=require_controller_checkpoint,
    )
    safe_seq = int(ceiling_info["safe_ingested_seq"])

    size_row = await conn.fetchrow(
        """
        SELECT pg_total_relation_size('public.telemetry_events')::bigint AS total_bytes,
               pg_relation_size('public.telemetry_events')::bigint AS heap_bytes,
               pg_indexes_size('public.telemetry_events')::bigint AS index_bytes
        """
    )
    table_bytes_before = int(size_row["total_bytes"] or 0) if size_row else 0
    heap_bytes_before = int(size_row["heap_bytes"] or 0) if size_row else 0
    index_bytes_before = int(size_row["index_bytes"] or 0) if size_row else 0
    toast_bytes_before = max(0, table_bytes_before - heap_bytes_before - index_bytes_before)

    counts_row = await conn.fetchrow(
        """
        SELECT COUNT(*)::bigint AS rows_total,
               COUNT(*) FILTER (WHERE event_type = 'heartbeat')::bigint AS heartbeat_rows_total,
               COUNT(*) FILTER (
                   WHERE event_type = 'heartbeat'
                     AND created_at < $1
                     AND ingested_seq <= $2
               )::bigint AS rows_to_delete,
               COALESCE(SUM(octet_length(payload::text)), 0)::bigint AS payload_bytes_total,
               COALESCE(
                   SUM(octet_length(payload::text)) FILTER (
                       WHERE event_type = 'heartbeat'
                         AND created_at < $1
                         AND ingested_seq <= $2
                   ),
                   0
               )::bigint AS payload_bytes_to_delete,
               COUNT(
                   DISTINCT CASE
                       WHEN event_type = 'heartbeat' AND created_at < $1 AND ingested_seq <= $2 THEN
                           COALESCE(
                               NULLIF(trim(payload->>'runtime_id'), ''),
                               NULLIF(trim(payload->'metadata'->>'runtime_id'), ''),
                               'unknown'
                           ) || '|' || (date_trunc('hour', created_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')::text
                       ELSE NULL
                   END
               )::bigint AS hours_to_summarize,
               MIN(created_at) AS min_created_at,
               MAX(created_at) AS max_created_at
        FROM public.telemetry_events
        """,
        cutoff_dt,
        safe_seq,
    )

    rows_total = int(counts_row["rows_total"] or 0) if counts_row else 0
    heartbeat_rows_total = int(counts_row["heartbeat_rows_total"] or 0) if counts_row else 0
    rows_to_delete = int(counts_row["rows_to_delete"] or 0) if counts_row else 0
    rows_to_retain = max(0, rows_total - rows_to_delete)
    heartbeat_rows_to_retain = max(0, heartbeat_rows_total - rows_to_delete)
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

    if rows_to_delete == 0 or table_bytes_before == 0:
        bytes_to_delete = 0
    elif payload_bytes_total > 0:
        bytes_to_delete = int(
            round(table_bytes_before * (payload_bytes_to_delete / payload_bytes_total))
        )
    elif rows_total > 0:
        bytes_to_delete = int(round(table_bytes_before * (rows_to_delete / rows_total)))
    else:
        bytes_to_delete = 0

    bytes_to_delete = min(table_bytes_before, max(0, bytes_to_delete))
    retained_table_bytes = max(0, table_bytes_before - bytes_to_delete)
    required_free_disk_bytes = int(
        math.ceil(FREE_DISK_SAFETY_MULTIPLIER * retained_table_bytes)
    )

    disk_info = resolve_disk_usage(
        disk_path,
        free_disk_bytes_override=free_disk_bytes_override,
    )
    filesystem_free_before = int(disk_info["filesystem_free_bytes"])
    expected_filesystem_free_after = filesystem_free_before + bytes_to_delete
    free_disk_check_passed = filesystem_free_before >= required_free_disk_bytes

    return {
        "mode": "dry_run",
        "table": CANONICAL_TELEMETRY_TABLE,
        "summary_table": HEARTBEAT_SUMMARY_TABLE,
        "retention_days": effective_days,
        "cutoff_at": cutoff_iso,
        "safe_ingested_seq": safe_seq,
        "consumer_checkpoints": dict(ceiling_info["consumer_checkpoints"]),
        "ingested_seq_consumers": list(INGESTED_SEQ_CONSUMERS),
        "reader_check": reader_check,
        "rows_total": rows_total,
        "heartbeat_rows_total": heartbeat_rows_total,
        "rows_to_delete": rows_to_delete,
        "rows_to_retain": rows_to_retain,
        "heartbeat_rows_to_retain": heartbeat_rows_to_retain,
        "non_heartbeat_rows_total": non_heartbeat_rows_total,
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


async def execute_space_reclaim(
    conn: Any,
    *,
    dsn: str = "",
    method: str = "vacuum_full",
    lock_timeout_ms: int = 15000,
    statement_timeout_ms: int = 300000,
    on_lock_acquired: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Return deleted table space to the filesystem with a standard Postgres mechanism.

    Supports:
    - ``vacuum_full``: ``VACUUM (FULL, ANALYZE) public.telemetry_events``
    - ``table_rewrite``: ``VACUUM (FULL, ANALYZE) public.telemetry_events``
    - ``pg_repack``: runs ``pg_repack --no-kill-backend --table public.telemetry_events``
    """
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
            CANONICAL_TELEMETRY_TABLE,
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
        mechanism_sql = "pg_repack --no-kill-backend --table public.telemetry_events"
    else:
        await conn.execute(f"SET lock_timeout = '{eff_lock_ms}ms'")
        await conn.execute(f"SET statement_timeout = '{eff_stmt_ms}ms'")
        try:
            if on_lock_acquired is not None:
                maybe_coro = on_lock_acquired()
                if inspect.isawaitable(maybe_coro):
                    await maybe_coro
            await conn.execute(
                "VACUUM (FULL, ANALYZE) public.telemetry_events",
                timeout=(eff_stmt_ms / 1000.0) + 10.0,
            )
        finally:
            try:
                await conn.execute("RESET lock_timeout")
                await conn.execute("RESET statement_timeout")
            except Exception:  # noqa: BLE001
                pass
        mechanism_sql = "VACUUM (FULL, ANALYZE) public.telemetry_events"

    duration_ms = (time.monotonic() - t0) * 1000.0
    completed_at = _utc_now()
    return {
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
    dsn: str = "",
    apply: bool = False,
    now: datetime | None = None,
    retention_days: float | None = None,
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
    on_lock_acquired: Callable[[], Any] | None = None,
    replay_dlq_callback: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Run dry-run or apply mode heartbeat backlog reclaim on an open connection."""
    plan = await inspect_reclaim_plan(
        conn,
        now=now,
        retention_days=retention_days,
        disk_path=disk_path,
        free_disk_bytes_override=free_disk_bytes_override,
        projection_schema=projection_schema,
        extra_consumer_checkpoints=extra_consumer_checkpoints,
        require_controller_checkpoint=require_controller_checkpoint,
        readers=readers,
    )
    if not apply:
        return plan

    if not plan["free_disk_check_passed"]:
        raise InsufficientDiskSpaceError(
            free_disk_bytes=int(plan["filesystem_free_bytes_before"]),
            retained_table_bytes=int(plan["retained_table_bytes"]),
            required_free_disk_bytes=int(plan["required_free_disk_bytes"]),
            filesystem_path=str(plan["filesystem_path"]),
        )

    await ensure_heartbeat_summary_schema(conn)
    retention_result = await run_heartbeat_retention_on_connection(
        conn,
        now=now,
        retention_days=plan["retention_days"],
        batch_size=batch_size,
        max_batches=max_batches,
        statement_timeout_ms=statement_timeout_ms,
        projection_schema=projection_schema,
        extra_consumer_checkpoints=extra_consumer_checkpoints,
        require_controller_checkpoint=require_controller_checkpoint,
        readers=readers,
    )

    reclaim_info = await execute_space_reclaim(
        conn,
        dsn=dsn,
        method=method,
        lock_timeout_ms=reclaim_lock_timeout_ms,
        statement_timeout_ms=reclaim_statement_timeout_ms,
        on_lock_acquired=on_lock_acquired,
    )

    dlq_replayed = 0
    if replay_dlq_callback is not None:
        maybe_replayed = replay_dlq_callback()
        if inspect.isawaitable(maybe_replayed):
            maybe_replayed = await maybe_replayed
        dlq_replayed = int(maybe_replayed or 0)

    after_size_row = await conn.fetchrow(
        """
        SELECT pg_total_relation_size('public.telemetry_events')::bigint AS total_bytes,
               pg_relation_size('public.telemetry_events')::bigint AS heap_bytes,
               pg_indexes_size('public.telemetry_events')::bigint AS index_bytes
        """
    )
    table_bytes_after = int(after_size_row["total_bytes"] or 0) if after_size_row else 0
    heap_bytes_after = int(after_size_row["heap_bytes"] or 0) if after_size_row else 0
    index_bytes_after = int(after_size_row["index_bytes"] or 0) if after_size_row else 0

    after_counts_row = await conn.fetchrow(
        """
        SELECT COUNT(*)::bigint AS rows_after,
               COUNT(*) FILTER (WHERE event_type = 'heartbeat')::bigint AS heartbeat_rows_after
        FROM public.telemetry_events
        """
    )
    rows_after = int(after_counts_row["rows_after"] or 0) if after_counts_row else 0
    heartbeat_rows_after = int(after_counts_row["heartbeat_rows_after"] or 0) if after_counts_row else 0
    non_heartbeat_rows_after = max(0, rows_after - heartbeat_rows_after)
    summary_rows_after = int(
        await conn.fetchval(f"SELECT COUNT(*)::bigint FROM {HEARTBEAT_SUMMARY_TABLE}") or 0
    )

    actual_bytes_reclaimed = max(0, int(plan["table_bytes_before"]) - table_bytes_after)
    disk_after = resolve_disk_usage(
        disk_path,
        free_disk_bytes_override=(
            int(free_disk_bytes_override) + actual_bytes_reclaimed
            if free_disk_bytes_override is not None
            else None
        ),
    )

    return {
        **plan,
        "mode": "apply",
        "retention": retention_result.to_dict(),
        "reclaim": reclaim_info,
        "dlq_replayed_count": dlq_replayed,
        "rows_deleted_actual": retention_result.rows_deleted,
        "hours_summarized_actual": retention_result.hours_summarized,
        "rows_after": rows_after,
        "heartbeat_rows_after": heartbeat_rows_after,
        "non_heartbeat_rows_after": non_heartbeat_rows_after,
        "summary_rows_after": summary_rows_after,
        "table_bytes_after": table_bytes_after,
        "heap_bytes_after": heap_bytes_after,
        "index_bytes_after": index_bytes_after,
        "actual_bytes_reclaimed": actual_bytes_reclaimed,
        "filesystem_free_bytes_after": int(disk_after["filesystem_free_bytes"]),
    }


async def run_reclaim(
    dsn: str,
    *,
    apply: bool = False,
    now: datetime | None = None,
    retention_days: float | None = None,
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
    on_lock_acquired: Callable[[], Any] | None = None,
    replay_dlq_callback: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Connect to PostgreSQL and execute dry-run or apply reclaim."""
    import asyncpg  # type: ignore[import]

    conn = await asyncpg.connect(dsn)
    try:
        return await run_reclaim_on_connection(
            conn,
            dsn=dsn,
            apply=apply,
            now=now,
            retention_days=retention_days,
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
            on_lock_acquired=on_lock_acquired,
            replay_dlq_callback=replay_dlq_callback,
        )
    finally:
        await conn.close()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or reclaim raw heartbeat backlog from public.telemetry_events."
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
        help="Summarize hours, delete expired raw heartbeats in batches, and reclaim disk space.",
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
        "--retention-days",
        type=float,
        default=None,
        help=f"Raw heartbeat retention window in days (defaults to {RAW_RETENTION_DAYS_ENV} or {int(DEFAULT_RAW_RETENTION_DAYS)}).",
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
        report = asyncio.run(
            run_reclaim(
                args.dsn,
                apply=bool(args.apply),
                now=now_dt,
                retention_days=args.retention_days,
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
