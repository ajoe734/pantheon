"""Bounded, tenant-scoped discovery summary (DISCOVERY-OWNER-SUMMARY-READ-20261125).

One read-only answer to "what did discovery produce": external text records
admitted in the last 24 h by source family, records closed by typed reason
(last 24 h and in total), today's model calls against the cap, hypothesis
cards (today and in total) and the newest cards.

Cost does not grow with total history. The read opens the distillation queue
and the extraction ledger read-only and uses only:

* the trigger-kept ``distillation_terminal_counts`` table (totals; the same
  table ``DistillationJobQueue.external_text_closure_counts`` reads);
* index range reads over ``distillation_outbox`` for the 24 h / today windows,
  each examining at most ``WINDOW_ROW_CAP`` rows (``truncated`` when hit);
* primary-key point reads for the newest ``NEWEST_CARD_LIMIT`` cards.

It never reads ``source_evidence``, the source-records route or the seed
file. A missing queue or ledger yields ``unavailable`` with a reason, never 0.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from services.source_ingestion.distillation_worker import (
    TERMINAL_COUNTS_TABLE,
    _EXTERNAL_TEXT_TYPES,
)
from services.source_ingestion.hypothesis_card import (
    BUDGET_PATH_ENV,
    daily_cap_from_env,
)

SUMMARY_SOURCE = "source_ingestion.distillation_queue"
LEDGER_SOURCE = "source_ingestion.l2_extraction_ledger"
LEDGER_FILENAME = "l2_extraction_ledger.sqlite"

WINDOW_SECONDS = 86400
# Rows examined per window read; the tenant's newest-first index range is cut here.
WINDOW_ROW_CAP = 100_000
NEWEST_CARD_LIMIT = 50
# Newest done rows examined to find the newest external-text cards.
NEWEST_CARD_SCAN = 500
# Distinct reason codes listed per family/total; the rest fold into "other".
MAX_REASONS = 40
MAX_TITLE_CHARS = 200
MAX_CLAIM_CHARS = 400
MAX_ID_CHARS = 200
# SQLite work is interrupted past this wall time (progress handler).
READ_TIME_BUDGET_SECONDS = 2.0
# Worst case: 50 cards x (title 200 + claim 400 + 2 ids x 200 chars, <= 3 bytes
# each, + keys) plus 2 x 6 families x 41 reasons; tested against this bound.
MAX_RESPONSE_BYTES = 256 * 1024

EXTERNAL_TEXT_FAMILIES = tuple(sorted(t.value for t in _EXTERNAL_TEXT_TYPES))
_FAMILY_SQL = ", ".join(f"'{family}'" for family in EXTERNAL_TEXT_FAMILIES)


class _ReadBudgetExceeded(Exception):
    pass


def _failure_reason(exc: BaseException, default: str) -> str:
    if isinstance(exc, _ReadBudgetExceeded) or "interrupt" in str(exc).lower():
        return "read_time_budget_exceeded"
    return default


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clip(value: Any, limit: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _unavailable(source: str, reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "source": source, "reason": reason}


def resolve_ledger_path(queue_path: Path, env: Optional[Mapping[str, str]] = None) -> Path:
    """The ledger the distillation worker writes: env path, else next to the queue."""
    raw = (env if env is not None else os.environ).get(BUDGET_PATH_ENV) or ""
    return Path(raw) if raw.strip() else Path(queue_path).with_name(LEDGER_FILENAME)


class _BoundedConnection:
    """Read-only connection that refuses work past the deadline.

    The deadline is checked before every statement and, inside a statement,
    by the SQLite progress handler (interrupts a long scan).
    """

    def __init__(self, connection: sqlite3.Connection, deadline: float) -> None:
        self._connection = connection
        self._deadline = deadline

    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        if time.monotonic() > self._deadline:
            raise _ReadBudgetExceeded()
        return self._connection.execute(sql, params)

    def set_trace_callback(self, callback: Any) -> None:
        self._connection.set_trace_callback(callback)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "_BoundedConnection":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _open_read_only(path: Path, deadline: float) -> _BoundedConnection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")

    def check() -> int:
        return 1 if time.monotonic() > deadline else 0

    connection.set_progress_handler(check, 1_000)
    return _BoundedConnection(connection, deadline)


def _fold(counts: dict[str, int]) -> dict[str, int]:
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    out = dict(ranked[:MAX_REASONS])
    rest = sum(v for _, v in ranked[MAX_REASONS:])
    if rest:
        out["other"] = out.get("other", 0) + rest
    return out


def _reason_block(rows: list[tuple[str, str, int]]) -> dict[str, Any]:
    by_reason: dict[str, int] = {}
    by_family: dict[str, dict[str, int]] = {}
    for family, reason, n in rows:
        if n <= 0:
            continue
        by_reason[reason] = by_reason.get(reason, 0) + n
        fam = by_family.setdefault(family, {})
        fam[reason] = fam.get(reason, 0) + n
    return {
        "total": sum(by_reason.values()),
        "by_reason": _fold(by_reason),
        "by_source_family": {f: _fold(r) for f, r in sorted(by_family.items())},
    }


def _schema_ready(connection: _BoundedConnection) -> Optional[str]:
    tables = {
        str(r[0]) for r in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")
    }
    if "distillation_outbox" not in tables:
        return "distillation_queue_not_initialized"
    needed = {TERMINAL_COUNTS_TABLE, "distillation_outbox_tenant_enqueued_idx", "distillation_outbox_tenant_terminal_idx"}
    if not needed <= tables:
        return "distillation_queue_not_migrated"
    return None


def _window_rows(
    connection: _BoundedConnection, sql: str, params: tuple[Any, ...]
) -> tuple[list[sqlite3.Row], bool]:
    rows = connection.execute(sql, (*params, WINDOW_ROW_CAP + 1)).fetchall()
    truncated = len(rows) > WINDOW_ROW_CAP
    return rows[:WINDOW_ROW_CAP], truncated


def _read_queue(connection: _BoundedConnection, tenant_id: str, now: float) -> dict[str, Any]:
    since = now - WINDOW_SECONDS
    day_start = datetime.fromtimestamp(now, tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    # Collected: source versions admitted to the queue in the last 24 h (covering index).
    collected_rows, collected_truncated = _window_rows(
        connection,
        """SELECT source_type FROM distillation_outbox INDEXED BY distillation_outbox_tenant_enqueued_idx
            WHERE tenant_id = ? AND enqueued_at >= ? ORDER BY enqueued_at DESC LIMIT ?""",
        (tenant_id, since),
    )
    by_family: dict[str, int] = {}
    for row in collected_rows:
        family = str(row["source_type"] or "")
        if family in EXTERNAL_TEXT_FAMILIES:
            by_family[family] = by_family.get(family, 0) + 1
    collected = {
        "status": "ok",
        "source": SUMMARY_SOURCE,
        "definition": "external-text source versions admitted to the distillation queue",
        "last_24h": {"total": sum(by_family.values()), "by_source_family": dict(sorted(by_family.items()))},
        "rows_examined": len(collected_rows),
        "truncated": collected_truncated,
    }

    # Closed in the last 24 h (index range) and in total (trigger-kept counter).
    closed_rows, closed_truncated = _window_rows(
        connection,
        """SELECT source_type, skip_code FROM distillation_outbox INDEXED BY distillation_outbox_tenant_terminal_idx
            WHERE tenant_id = ? AND status = 'skipped' AND processed_at >= ?
            ORDER BY processed_at DESC LIMIT ?""",
        (tenant_id, since),
    )
    window_counts: dict[tuple[str, str], int] = {}
    for row in closed_rows:
        family = str(row["source_type"] or "")
        if family in EXTERNAL_TEXT_FAMILIES:
            key = (family, str(row["skip_code"] or "reason_not_recorded"))
            window_counts[key] = window_counts.get(key, 0) + 1
    total_rows = connection.execute(
        f"""SELECT source_type, reason, n FROM {TERMINAL_COUNTS_TABLE}
             WHERE tenant_id = ? AND status = 'skipped' AND source_type IN ({_FAMILY_SQL})""",
        (tenant_id,),
    ).fetchall()
    unattributed = connection.execute(
        f"""SELECT COALESCE(SUM(n), 0) FROM {TERMINAL_COUNTS_TABLE}
             WHERE tenant_id = '' AND status IN ('done', 'skipped')""",
    ).fetchone()[0]
    closed = {
        "status": "ok",
        "source": SUMMARY_SOURCE,
        "definition": "external-text queue rows closed without a draft, by typed reason",
        "last_24h": {**_reason_block([(f, r, n) for (f, r), n in window_counts.items()]), "truncated": closed_truncated},
        "total": _reason_block([(str(r["source_type"]), str(r["reason"]), int(r["n"])) for r in total_rows]),
        # Terminal rows whose snapshot was pruned before tenant stamping: in no tenant's total.
        "unattributed_terminal_rows": int(unattributed),
    }

    # Cards: today (index range) and total (counter), plus the newest cards.
    today_rows, today_truncated = _window_rows(
        connection,
        """SELECT source_type FROM distillation_outbox INDEXED BY distillation_outbox_tenant_terminal_idx
            WHERE tenant_id = ? AND status = 'done' AND processed_at >= ?
            ORDER BY processed_at DESC LIMIT ?""",
        (tenant_id, day_start),
    )
    cards_today = sum(1 for r in today_rows if str(r["source_type"] or "") in EXTERNAL_TEXT_FAMILIES)
    cards_total = connection.execute(
        f"""SELECT COALESCE(SUM(n), 0) FROM {TERMINAL_COUNTS_TABLE}
             WHERE tenant_id = ? AND status = 'done' AND source_type IN ({_FAMILY_SQL})""",
        (tenant_id,),
    ).fetchone()[0]
    newest = connection.execute(
        f"""SELECT o.source_id, o.source_digest, o.source_type, o.processed_at, o.seed_id,
                   CASE WHEN json_valid(s.payload_json) THEN json_extract(s.payload_json, '$.title') END AS title,
                   s.payload_json = '{{"_pruned":true}}' AS pruned
              FROM (SELECT source_id, source_digest, source_type, processed_at, seed_id
                      FROM distillation_outbox INDEXED BY distillation_outbox_tenant_terminal_idx
                     WHERE tenant_id = ? AND status = 'done'
                     ORDER BY processed_at DESC LIMIT ?) AS o
              LEFT JOIN distillation_source_versions AS s
                ON s.source_id = o.source_id AND s.source_digest = o.source_digest
             WHERE o.source_type IN ({_FAMILY_SQL})
             ORDER BY o.processed_at DESC LIMIT ?""",
        (tenant_id, NEWEST_CARD_SCAN, NEWEST_CARD_LIMIT),
    ).fetchall()
    cards = {
        "status": "ok",
        "source": SUMMARY_SOURCE,
        "definition": "external-text queue rows that became a draft (a verified card is required, RC2)",
        "utc_day": _iso(day_start)[:10],
        "today": cards_today,
        "today_truncated": today_truncated,
        "total": int(cards_total),
        "newest": [
            {
                "source_id": _clip(r["source_id"], MAX_ID_CHARS),
                "source_digest": _clip(r["source_digest"], MAX_ID_CHARS),
                "source_family": r["source_type"],
                "seed_id": _clip(r["seed_id"], MAX_ID_CHARS),
                "title": _clip(r["title"], MAX_TITLE_CHARS),
                "title_status": "ok" if r["title"] else ("payload_pruned" if r["pruned"] else "not_recorded"),
                "carded_at": _iso(float(r["processed_at"])) if r["processed_at"] is not None else None,
            }
            for r in newest
        ],
    }
    return {"collected": collected, "closed": closed, "cards": cards}


def _read_ledger(
    ledger_path: Path, newest: list[dict[str, Any]], now: float, deadline: float, default_cap: int
) -> dict[str, Any]:
    """LLM calls today and the claim of each newest card (PK reads only)."""
    day = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
    if not ledger_path.is_file():
        for card in newest:
            card.update(claim=None, claim_status="ledger_unavailable")
        return _unavailable(LEDGER_SOURCE, "extraction_ledger_missing")
    try:
        with closing(_open_read_only(ledger_path, deadline)) as connection:
            tables = {str(r[0]) for r in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "calls" not in tables:
                for card in newest:
                    card.update(claim=None, claim_status="ledger_unavailable")
                return _unavailable(LEDGER_SOURCE, "extraction_ledger_not_initialized")
            cols = {str(r[1]) for r in connection.execute("PRAGMA table_info(calls)")}
            row = connection.execute(
                f"SELECT n, {'cap' if 'cap' in cols else 'NULL'} AS cap FROM calls WHERE day = ?", (day,)
            ).fetchone()
            for card in newest:
                found = None
                if "cards" in tables:
                    found = connection.execute(
                        "SELECT card FROM cards WHERE source_id = ? AND digest = ?",
                        (card["source_id"], card["source_digest"]),
                    ).fetchone()
                if found is None:
                    card.update(claim=None, claim_status="card_not_in_ledger")
                    continue
                try:
                    claim = json.loads(found[0]).get("claim")
                except (ValueError, AttributeError):
                    claim = None
                card.update(claim=_clip(claim, MAX_CLAIM_CHARS), claim_status="ok" if claim else "claim_missing")
    except (sqlite3.Error, _ReadBudgetExceeded) as exc:
        for card in newest:
            card.setdefault("claim", None)
            card.setdefault("claim_status", "ledger_unavailable")
        return _unavailable(LEDGER_SOURCE, _failure_reason(exc, "extraction_ledger_unreadable"))
    recorded_cap = row["cap"] if row is not None else None
    return {
        "status": "ok",
        "source": LEDGER_SOURCE,
        "scope": "service",  # one daily budget shared by every tenant of this deployment
        "utc_day": day,
        "calls": int(row["n"]) if row is not None else 0,
        "daily_cap": int(recorded_cap) if recorded_cap is not None else int(default_cap),
        "cap_source": "ledger" if recorded_cap is not None else "service_env",
    }


def read_discovery_summary(
    *,
    queue_path: Path,
    tenant_id: str,
    ledger_path: Optional[Path] = None,
    now: Optional[float] = None,
    daily_cap: Optional[int] = None,
    time_budget_seconds: float = READ_TIME_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    started = clock()
    deadline = time.monotonic() + max(0.05, float(time_budget_seconds))
    effective_now = float(now) if now is not None else time.time()
    queue_path = Path(queue_path)
    ledger = Path(ledger_path) if ledger_path is not None else resolve_ledger_path(queue_path)
    tenant = str(tenant_id or "").strip()

    sections: dict[str, Any]
    if not tenant:
        reason = _unavailable(SUMMARY_SOURCE, "missing_tenant_scope")
        sections = {"collected": reason, "closed": reason, "cards": dict(reason, newest=[])}
    elif not queue_path.is_file():
        reason = _unavailable(SUMMARY_SOURCE, "distillation_queue_missing")
        sections = {"collected": reason, "closed": reason, "cards": dict(reason, newest=[])}
    else:
        try:
            with closing(_open_read_only(queue_path, deadline)) as connection:
                not_ready = _schema_ready(connection)
                if not_ready:
                    reason = _unavailable(SUMMARY_SOURCE, not_ready)
                    sections = {"collected": reason, "closed": reason, "cards": dict(reason, newest=[])}
                else:
                    sections = _read_queue(connection, tenant, effective_now)
        except (sqlite3.Error, _ReadBudgetExceeded) as exc:
            reason = _unavailable(SUMMARY_SOURCE, _failure_reason(exc, "distillation_queue_unreadable"))
            sections = {"collected": reason, "closed": reason, "cards": dict(reason, newest=[])}

    llm_calls = _read_ledger(
        ledger,
        sections["cards"].get("newest") or [],
        effective_now,
        deadline,
        daily_cap if daily_cap is not None else daily_cap_from_env(),
    )
    return {
        "tenant_id": tenant or None,
        "generated_at": _iso(effective_now),
        "window": {"since": _iso(effective_now - WINDOW_SECONDS), "until": _iso(effective_now)},
        "collected_text_records": sections["collected"],
        "closed_text_records": sections["closed"],
        "llm_calls": llm_calls,
        "hypothesis_cards": sections["cards"],
        "bounds": {
            "window_row_cap": WINDOW_ROW_CAP,
            "newest_card_limit": NEWEST_CARD_LIMIT,
            "newest_card_scan": NEWEST_CARD_SCAN,
            "max_reasons": MAX_REASONS,
            "read_time_budget_ms": int(time_budget_seconds * 1000),
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "elapsed_ms": round((clock() - started) * 1000, 3),
            "reads": ["distillation_outbox (index ranges)", TERMINAL_COUNTS_TABLE, "l2_extraction_ledger (point reads)"],
        },
    }


__all__ = [
    "EXTERNAL_TEXT_FAMILIES",
    "MAX_RESPONSE_BYTES",
    "NEWEST_CARD_LIMIT",
    "WINDOW_ROW_CAP",
    "read_discovery_summary",
    "resolve_ledger_path",
]
