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
from typing import Any, Callable, Mapping, Optional, Sequence

from services.source_ingestion.distillation_worker import (
    TERMINAL_COUNTS_TABLE,
    _EXTERNAL_TEXT_TYPES,
)
from services.source_ingestion.hypothesis_card import (
    BUDGET_PATH_ENV,
    RELEASE_SWITCH_TABLE,
    active_deployment_sha,
    daily_cap_from_env,
    has_first_run_ritual_marker,
    valid_deployment_sha,
)

SUMMARY_SOURCE = "source_ingestion.distillation_queue"
LEDGER_SOURCE = "source_ingestion.l2_extraction_ledger"
LEDGER_FILENAME = "l2_extraction_ledger.sqlite"
RELEASE_SWITCHED_AT_ENV = "PANTHEON_RELEASE_SWITCHED_AT"
ACCEPTANCE_SWITCHED_AT_ENV = "ACCEPTANCE_RELEASE_SWITCHED_AT"

WINDOW_SECONDS = 86400
# Rows examined per window read; the tenant's newest-first index range is cut here.
WINDOW_ROW_CAP = 100_000
NEWEST_CARD_LIMIT = 50
# Newest done rows examined to find the newest external-text cards.
NEWEST_CARD_SCAN = 500
# Newest ledger outcome events examined for post-release-switch ritual checks.
LEDGER_EVENT_SCAN_CAP = 5_000
MAX_RITUAL_EVENT_SAMPLES = 50
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


def _parse_epoch(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        val = float(value)
        return val if val == val and val > 0 else None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            parsed = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return parsed.timestamp()


def _read_db_release_switch(
    connection: "_BoundedConnection", tables: set[str]
) -> Optional[tuple[str, str]]:
    if RELEASE_SWITCH_TABLE not in tables:
        return None
    row = connection.execute(
        f"SELECT deployment_sha, switched_at FROM {RELEASE_SWITCH_TABLE} WHERE singleton = 1"
    ).fetchone()
    if row is None:
        return None
    return str(row[0] or "").strip(), str(row[1] or "").strip()


def _resolve_release_switched_at(
    queue_path: Path,
    release_switched_at: Any = None,
    env: Optional[Mapping[str, str]] = None,
    *,
    db_switch_rows: Sequence[tuple[str, str]] = (),
) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """Resolve an authoritative release switch timestamp; never guess or fall back to started_at."""
    effective_env = env if env is not None else os.environ
    env_sha = active_deployment_sha(effective_env)
    if release_switched_at is not None:
        if isinstance(release_switched_at, str) and not release_switched_at.strip():
            return None, None, "release_switch_time_missing"
        epoch = _parse_epoch(release_switched_at)
        if epoch is None:
            return None, None, "release_switch_time_invalid"
        return epoch, env_sha, None
    for key in (RELEASE_SWITCHED_AT_ENV, ACCEPTANCE_SWITCHED_AT_ENV):
        if key in effective_env:
            raw = str(effective_env.get(key) or "").strip()
            if not raw:
                return None, None, "release_switch_time_missing"
            epoch = _parse_epoch(raw)
            if epoch is None:
                return None, None, "release_switch_time_invalid"
            return epoch, env_sha, None

    state_shas: list[str] = []
    state_has_invalid_sha = False
    explicit_state_switch: Optional[tuple[float, Optional[str]]] = None
    for state_name in ("distill_controller_state.json", "controller_state.json"):
        candidate = queue_path.with_name(state_name)
        try:
            if not candidate.is_file():
                continue
            if candidate.stat().st_size > 65_536:
                return None, None, "release_switch_time_unreadable"
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, None, "release_switch_time_unreadable"
        if not isinstance(payload, dict):
            return None, None, "release_switch_time_unreadable"
        deployment = payload.get("deployment")
        file_sha: Optional[str] = None
        if isinstance(deployment, Mapping) and "git_sha" in deployment:
            file_sha = valid_deployment_sha(deployment.get("git_sha"))
            if file_sha is None:
                state_has_invalid_sha = True
            else:
                state_shas.append(file_sha)
        # Only explicit release_switched_at fields qualify; controller started_at
        # is reset on every container restart and is never release switch truth.
        raw_switch = payload.get("release_switched_at")
        if raw_switch is None and isinstance(deployment, Mapping):
            raw_switch = deployment.get("release_switched_at")
        if raw_switch is not None:
            if isinstance(raw_switch, str) and not raw_switch.strip():
                return None, None, "release_switch_time_missing"
            parsed_switch = _parse_epoch(raw_switch)
            if parsed_switch is None:
                return None, None, "release_switch_time_invalid"
            if explicit_state_switch is None:
                explicit_state_switch = (parsed_switch, file_sha or env_sha)

    if len(set(state_shas)) > 1:
        return None, None, "release_switch_deployment_mismatch"
    if env_sha is not None and state_shas and state_shas[0] != env_sha:
        return None, None, "release_switch_deployment_mismatch"

    if explicit_state_switch is not None:
        return explicit_state_switch[0], explicit_state_switch[1], None

    if db_switch_rows:
        if state_has_invalid_sha and env_sha is None:
            return None, None, "release_switch_deployment_mismatch"
        expected_sha = env_sha or (state_shas[0] if state_shas else None)
        valid_rows: list[tuple[str, float]] = []
        for raw_sha, raw_ts in db_switch_rows:
            epoch = _parse_epoch(raw_ts)
            if epoch is None:
                return None, None, "release_switch_time_invalid"
            if raw_sha != "explicit-switch" and valid_deployment_sha(raw_sha) is None:
                return None, None, "release_switch_time_invalid"
            if expected_sha is not None and raw_sha != "explicit-switch" and raw_sha != expected_sha:
                return None, None, "release_switch_deployment_mismatch"
            valid_rows.append((raw_sha, epoch))
        distinct_shas = {sha for sha, _ in valid_rows if sha != "explicit-switch"}
        if len(distinct_shas) > 1:
            return None, None, "release_switch_deployment_mismatch"
        chosen_epoch = min(ep for _, ep in valid_rows)
        chosen_sha = next(iter(distinct_shas), expected_sha)
        return chosen_epoch, chosen_sha, None

    return None, None, "release_switch_time_missing"


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


def _schema_ready(connection: _BoundedConnection) -> tuple[Optional[str], set[str]]:
    tables = {
        str(r[0]) for r in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")
    }
    if "distillation_outbox" not in tables:
        return "distillation_queue_not_initialized", tables
    needed = {TERMINAL_COUNTS_TABLE, "distillation_outbox_tenant_enqueued_idx", "distillation_outbox_tenant_terminal_idx"}
    if not needed <= tables:
        return "distillation_queue_not_migrated", tables
    return None, tables


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
    ledger_path: Path,
    newest: list[dict[str, Any]],
    now: float,
    deadline: float,
    default_cap: int,
    *,
    queue_path: Path,
    release_switched_at: Any = None,
    queue_switch_row: Optional[tuple[str, str]] = None,
) -> dict[str, Any]:
    """LLM calls today, the claim of each newest card, and bounded post-switch ritual observation."""
    day = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
    if not ledger_path.is_file():
        for card in newest:
            card.update(claim=None, claim_status="ledger_unavailable")
        return _unavailable(LEDGER_SOURCE, "extraction_ledger_missing")
    ritual_obs: dict[str, Any]
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
            ledger_switch_row = _read_db_release_switch(connection, tables)
            db_switch_rows = [r for r in (ledger_switch_row, queue_switch_row) if r is not None]
            switch_epoch, switch_sha, switch_error = _resolve_release_switched_at(
                queue_path,
                release_switched_at=release_switched_at,
                db_switch_rows=db_switch_rows,
            )
            if switch_error is not None or switch_epoch is None:
                ritual_obs = {
                    "release_switched_at": None,
                    "release_deployment_sha": switch_sha,
                    "ritual_events_status": "unavailable",
                    "ritual_events_reason": switch_error or "release_switch_time_missing",
                    "ritual_events_since_switch": None,
                }
            elif "events" not in tables:
                ritual_obs = {
                    "release_switched_at": _iso(switch_epoch),
                    "release_deployment_sha": switch_sha,
                    "ritual_events_status": "unavailable",
                    "ritual_events_reason": "extraction_ledger_events_missing",
                    "ritual_events_since_switch": None,
                }
            else:
                # Events ledger is shared across tenants (scope="service"); never project raw
                # source_id, outcome, or free-text detail into tenant-visible summary output.
                event_rows = connection.execute(
                    "SELECT ts, outcome, detail FROM events ORDER BY id DESC LIMIT ?",
                    (LEDGER_EVENT_SCAN_CAP + 1,),
                ).fetchall()
                ritual_since_switch = 0
                examined_since_switch = 0
                ritual_timestamps: list[str] = []
                total_ritual_scanned = 0
                invalid_ts = False
                oldest_scanned_epoch: Optional[float] = None
                for erow in event_rows[:LEDGER_EVENT_SCAN_CAP]:
                    ev_epoch = _parse_epoch(erow["ts"])
                    if ev_epoch is None:
                        invalid_ts = True
                        break
                    oldest_scanned_epoch = (
                        ev_epoch if oldest_scanned_epoch is None else min(oldest_scanned_epoch, ev_epoch)
                    )
                    outcome_str = str(erow["outcome"] or "")
                    detail_str = str(erow["detail"] or "")
                    is_ritual = (
                        outcome_str == "openclaw_workspace_first_run_ritual"
                        or has_first_run_ritual_marker(detail_str)
                        or has_first_run_ritual_marker(outcome_str)
                    )
                    if ev_epoch >= switch_epoch:
                        examined_since_switch += 1
                        if is_ritual:
                            ritual_since_switch += 1
                    if is_ritual:
                        total_ritual_scanned += 1
                        if len(ritual_timestamps) < MAX_RITUAL_EVENT_SAMPLES:
                            ritual_timestamps.append(_iso(ev_epoch))
                scan_hit_cap = len(event_rows) > LEDGER_EVENT_SCAN_CAP
                oldest_scanned_iso = _iso(oldest_scanned_epoch) if oldest_scanned_epoch is not None else None
                truncated = bool(
                    scan_hit_cap and (oldest_scanned_epoch is None or oldest_scanned_epoch >= switch_epoch)
                )
                if invalid_ts:
                    ritual_obs = {
                        "release_switched_at": _iso(switch_epoch),
                        "release_deployment_sha": switch_sha,
                        "ritual_events_status": "unavailable",
                        "ritual_events_reason": "ledger_event_timestamp_invalid",
                        "ritual_events_since_switch": None,
                    }
                elif truncated:
                    ritual_obs = {
                        "release_switched_at": _iso(switch_epoch),
                        "release_deployment_sha": switch_sha,
                        "ritual_events_status": "unavailable",
                        "ritual_events_reason": "observation_truncated",
                        "ritual_events_since_switch": None,
                        "ritual_events_truncated": True,
                        "ritual_events_scan_hit_cap": True,
                        "ritual_events_oldest_scanned_at": oldest_scanned_iso,
                        "events_examined_since_switch": examined_since_switch,
                    }
                else:
                    ritual_obs = {
                        "release_switched_at": _iso(switch_epoch),
                        "release_deployment_sha": switch_sha,
                        "ritual_events_status": "ok",
                        "ritual_events_since_switch": ritual_since_switch,
                        "events_examined_since_switch": examined_since_switch,
                        "ritual_events_truncated": False,
                        "ritual_events_scan_hit_cap": bool(scan_hit_cap),
                        "ritual_events_oldest_scanned_at": oldest_scanned_iso,
                        "ritual_event_timestamps": ritual_timestamps,
                        "ritual_events_sample_truncated": total_ritual_scanned > MAX_RITUAL_EVENT_SAMPLES,
                    }
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
        **ritual_obs,
    }


def read_discovery_summary(
    *,
    queue_path: Path,
    tenant_id: str,
    ledger_path: Optional[Path] = None,
    now: Optional[float] = None,
    daily_cap: Optional[int] = None,
    release_switched_at: Any = None,
    time_budget_seconds: float = READ_TIME_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    started = clock()
    deadline = time.monotonic() + max(0.05, float(time_budget_seconds))
    effective_now = float(now) if now is not None else time.time()
    queue_path = Path(queue_path)
    ledger = Path(ledger_path) if ledger_path is not None else resolve_ledger_path(queue_path)
    tenant = str(tenant_id or "").strip()
    queue_switch_row: Optional[tuple[str, str]] = None

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
                not_ready, queue_tables = _schema_ready(connection)
                queue_switch_row = _read_db_release_switch(connection, queue_tables)
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
        queue_path=queue_path,
        release_switched_at=release_switched_at,
        queue_switch_row=queue_switch_row,
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
            "ledger_event_scan_cap": LEDGER_EVENT_SCAN_CAP,
            "max_reasons": MAX_REASONS,
            "read_time_budget_ms": int(time_budget_seconds * 1000),
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "elapsed_ms": round((clock() - started) * 1000, 3),
            "reads": ["distillation_outbox (index ranges)", TERMINAL_COUNTS_TABLE, "l2_extraction_ledger (point reads)"],
        },
    }


__all__ = [
    "EXTERNAL_TEXT_FAMILIES",
    "LEDGER_EVENT_SCAN_CAP",
    "MAX_RESPONSE_BYTES",
    "NEWEST_CARD_LIMIT",
    "WINDOW_ROW_CAP",
    "read_discovery_summary",
    "resolve_ledger_path",
]
