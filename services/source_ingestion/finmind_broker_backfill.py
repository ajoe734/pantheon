"""One-time and resumable FinMind broker-branch backfill (TaiwanStockTradingDailyReport).

Walks weekdays over a two-year window (or a bounded date sub-window), fetches each date's
SponsorPro storage-object parquet body through the bulk parquet adapter, aggregates per
(date, symbol, branch) inside pyarrow, ranks the top-20 buy and top-20 sell branches by net
quantity for every symbol, and writes compact records into the governed ``keyed/tw_broker_top``
dataset (SOURCE-BROKER-TWO-YEAR-BACKFILL-HOSTED-20261127).

* Resumable: ``backfill_state.json`` records completed dates and session counts; completed
  dates whose keyed partition exists (or confirmed empty holiday dates) are never refetched.
* Throttled: default 1 request per 1.5s (<=2400/h) vs. the 6000/h SponsorYear cap, with optional
  per-dispatch ``--max-requests`` and ``--max-wall-seconds`` bounds so no run holds the dev
  environment lease longer than the stated limit.
* Cap-enforced: stops immediately with ``status="cap_exceeded"`` if the per-session byte/row cap
  (6 MB / 120,000 rows) or two-year total byte/row cap (3.0 GB / 60,000,000 rows) or plan hourly
  request cap (6,000/h) would be exceeded.
* No token -> records a ``credential_unavailable`` reason and fetches nothing.
* FinMind data is licence ``vendor_research`` and not redistributable. Nothing
  here writes to ``normalized/tw_price_daily`` or a side directory.

Daily whole-market run (SOURCE-BROKER-WHOLE-MARKET-DAILY-20261127): ``run_whole_market_session`` uses the
same storage_objects request and parquet decode for one published session and writes compact top-20 buy and
sell rows for every symbol into ``keyed/tw_broker_top`` plus one run receipt.

Usage::

    python -m services.source_ingestion.finmind_broker_backfill --output-dir ROOT
    python -m services.source_ingestion.finmind_broker_backfill daily --market-data-root ROOT --trade-date D
    python -m services.source_ingestion.finmind_broker_backfill verify --market-data-root ROOT
"""

from __future__ import annotations

import argparse
import builtins
import gzip
import json
import os
import re
import sys
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .connectors.finmind_taiwan import (
    FINMIND_BROKER_REPORT_DATASET,
    FinMindCredentialError,
    FinMindFetchError,
    FinMindLiveFetcher,
    FinMindQuotaError,
    FinMindTaiwanBrokerDailyReportAdapter,
)

BACKFILL_YEARS = 2
DEFAULT_MIN_INTERVAL_SECONDS = 1.5
STATE_SCHEMA = "finmind_broker_backfill_state.v2"
FINMIND_PLAN_HOURLY_CAP = 6000
BACKFILL_HOURLY_SELF_LIMIT = 2400
BACKFILL_MAX_SESSION_BYTES = 6_000_000
BACKFILL_MAX_SESSION_ROWS = 120_000
BACKFILL_MAX_TOTAL_BYTES = 3_000_000_000
BACKFILL_MAX_TOTAL_ROWS = 60_000_000
BACKFILL_DEFAULT_MAX_REQUESTS = 180
BACKFILL_DEFAULT_MAX_WALL_SECONDS = 2100.0
BACKFILL_MIN_COVERAGE_SESSIONS = 480
BACKFILL_COVERAGE_SCHEMA = "finmind_broker_two_year_coverage_proof.v1"

PARQUET_MAGIC = b"PAR1"
PYARROW_REQUIREMENTS = "services/source_ingestion/requirements.txt"


class ParquetDependencyError(RuntimeError):
    pass


def require_pyarrow() -> Any:
    """Import pyarrow.parquet or raise with install instructions (before any fetch)."""
    try:
        import pyarrow.parquet as pq  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ParquetDependencyError(
            f"pyarrow is required for FinMind parquet decoding; "
            f"pip install -r {PYARROW_REQUIREMENTS}"
        ) from exc
    return pq


def decode_parquet_bytes(raw: bytes) -> list[dict[str, Any]]:
    import io

    return require_pyarrow().read_table(io.BytesIO(raw)).to_pylist()


def weekdays(start: date, end: date) -> Iterable[date]:
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


_GUARD_CODE_CACHE: dict[str, Any] = {}


def _load_bounded_runtime_market_open_guard_code() -> Any:
    """Compile the single bounded_runtime_market_open_guard GUARD_PY block from deploy_nonprod_vm.sh."""
    raw_func = os.environ.get("PANTHEON_MARKET_OPEN_GUARD_FUNC", "").strip()
    if not raw_func:
        script_path = Path(__file__).resolve().parents[2] / "scripts" / "deploy_nonprod_vm.sh"
        raw_func = script_path.read_text(encoding="utf-8")
    cached = _GUARD_CODE_CACHE.get(raw_func)
    if cached is not None:
        return cached
    marker_start = "bounded_runtime_market_open_guard"
    idx = raw_func.find(marker_start)
    search_text = raw_func[idx:] if idx >= 0 else raw_func
    m = re.search(r"<<'GUARD_PY'\n(.*?)\nGUARD_PY", search_text, re.DOTALL)
    if not m:
        raise RuntimeError("bounded_runtime_market_open_guard GUARD_PY block not found")
    code = compile(m.group(1), "<deploy_nonprod_vm.sh:bounded_runtime_market_open_guard>", "exec")
    _GUARD_CODE_CACHE[raw_func] = code
    return code


def invoke_bounded_runtime_market_open_guard(
    *,
    elapsed_seconds: float = 0.0,
    initial_refresh_now: str = "",
) -> str | None:
    """Reuse bounded_runtime_market_open_guard (no second time rule).

    Returns the named market_open_0055z reason when inside the Taiwan weekday window
    where a profile/session and its steady restore cannot finish before 00:55Z, else None.
    """
    code = _load_bounded_runtime_market_open_guard_code()
    current_raw = os.environ.get("PANTHEON_REFRESH_NOW", "").strip()
    restore_raw: str | None = None
    if current_raw and current_raw == initial_refresh_now and elapsed_seconds > 0:
        parsed = datetime.fromisoformat(current_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        advanced = (parsed + timedelta(seconds=elapsed_seconds)).isoformat().replace("+00:00", "Z")
        restore_raw = current_raw
        os.environ["PANTHEON_REFRESH_NOW"] = advanced
    printed: list[str] = []

    def _capture_print(*args: Any, **_kwargs: Any) -> None:
        printed.append(" ".join(str(a) for a in args))

    ns: dict[str, Any] = {
        "__builtins__": {**builtins.__dict__, "print": _capture_print},
        "__name__": "__main__",
    }
    exit_code = 1
    try:
        exec(code, ns, ns)
    except SystemExit as exc:
        exit_code = 0 if exc.code in (0, None) else int(exc.code)
    finally:
        if restore_raw is not None:
            os.environ["PANTHEON_REFRESH_NOW"] = restore_raw
    if exit_code == 0:
        return printed[-1].strip() if printed else "market_open_0055z"
    return None


@dataclass
class BackfillResult:
    status: str = "ok"  # ok | not_run | credential_unavailable | dependency_unavailable | quota_exhausted | partial | cap_exceeded | failed
    reason: str | None = None
    completed: list[str] = field(default_factory=list)
    skipped_completed: list[str] = field(default_factory=list)
    empty_dates: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    requests_made: int = 0
    top_records: int = 0
    detail_records: int = 0
    bytes_written: int = 0
    total_stored_sessions: int = 0
    total_stored_rows: int = 0
    total_stored_bytes: int = 0
    max_total_bytes: int = BACKFILL_MAX_TOTAL_BYTES
    max_total_rows: int = BACKFILL_MAX_TOTAL_ROWS
    max_session_bytes: int = BACKFILL_MAX_SESSION_BYTES
    max_session_rows: int = BACKFILL_MAX_SESSION_ROWS
    plan_hourly_cap: int = FINMIND_PLAN_HOURLY_CAP
    remaining_dates_count: int = 0
    stopped_reason: str | None = None
    session_symbol_counts: dict[str, int] = field(default_factory=dict)
    window_start: str = ""
    window_end: str = ""
    branch_detail_written: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "completed_count": len(self.completed),
            "skipped_completed_count": len(self.skipped_completed),
            "empty_dates": self.empty_dates,
            "failed": self.failed,
            "requests_made": self.requests_made,
            "plan_hourly_cap": self.plan_hourly_cap,
            "top_records": self.top_records,
            "detail_records": self.detail_records,
            "branch_detail_written": self.branch_detail_written,
            "bytes_written": self.bytes_written,
            "total_stored_sessions": self.total_stored_sessions,
            "total_stored_rows": self.total_stored_rows,
            "total_stored_bytes": self.total_stored_bytes,
            "max_total_bytes": self.max_total_bytes,
            "max_total_rows": self.max_total_rows,
            "max_session_bytes": self.max_session_bytes,
            "max_session_rows": self.max_session_rows,
            "remaining_dates_count": self.remaining_dates_count,
            "stopped_reason": self.stopped_reason,
            "session_symbol_counts": self.session_symbol_counts,
        }


def _load_state(path: Path) -> tuple[set[str], set[str], dict[str, dict[str, Any]]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return set(), set(), {}
    if not isinstance(data, dict):
        return set(), set(), {}
    completed = {str(d) for d in data.get("completed_dates", []) if d}
    empty = {str(d) for d in data.get("empty_dates", []) if d}
    raw_sessions = data.get("sessions")
    sessions = {
        str(k): dict(v)
        for k, v in (raw_sessions.items() if isinstance(raw_sessions, dict) else ())
        if isinstance(v, Mapping)
    }
    return completed, empty, sessions


def _save_state(
    path: Path,
    completed: set[str],
    empty_dates: set[str] | None = None,
    sessions: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload: dict[str, Any] = {
        "schema": STATE_SCHEMA,
        "dataset": "tw_broker_top",
        "source_dataset": FINMIND_BROKER_REPORT_DATASET,
        "completed_dates": sorted(completed),
        "empty_dates": sorted(empty_dates or ()),
        "sessions": {k: dict(sessions[k]) for k in sorted(sessions or {})},
    }
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _store_partition_totals(store: Any) -> dict[str, tuple[int, int]]:
    totals: dict[str, tuple[int, int]] = {}
    for part in store.partitions():
        try:
            manifest = store.read_manifest(part) or {}
        except Exception:  # noqa: BLE001
            continue
        totals[part] = (int(manifest.get("row_count") or 0), int(manifest.get("bytes") or 0))
    return totals


def run_backfill(
    *,
    output_dir: Path | str | None = None,
    market_data_root: Path | str | None = None,
    start: date,
    end: date,
    fetcher: FinMindLiveFetcher | None = None,
    min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    max_rank: int = 20,
    max_requests: int | None = None,
    max_wall_seconds: float | None = None,
    max_total_bytes: int = BACKFILL_MAX_TOTAL_BYTES,
    max_total_rows: int = BACKFILL_MAX_TOTAL_ROWS,
    max_session_bytes: int = BACKFILL_MAX_SESSION_BYTES,
    max_session_rows: int = BACKFILL_MAX_SESSION_ROWS,
    run_id: str = "",
    market_open_guard: Callable[[], str | None] | None = None,
) -> BackfillResult:
    from .daily_bars_store import DailyBudgetError, KeyedDatasetError, keyed_dataset_store

    target_root = Path(market_data_root if market_data_root is not None else (output_dir or "."))
    result = BackfillResult(
        max_total_bytes=max_total_bytes,
        max_total_rows=max_total_rows,
        max_session_bytes=max_session_bytes,
        max_session_rows=max_session_rows,
        window_start=start.isoformat(),
        window_end=end.isoformat(),
    )
    initial_refresh_now = os.environ.get("PANTHEON_REFRESH_NOW", "").strip()

    def _check_market_open(elapsed: float = 0.0) -> str | None:
        if market_open_guard is not None:
            return market_open_guard()
        return invoke_bounded_runtime_market_open_guard(
            elapsed_seconds=elapsed,
            initial_refresh_now=initial_refresh_now,
        )

    guard_reason = _check_market_open(0.0)
    if guard_reason:
        result.status = "not_run"
        result.reason = guard_reason
        result.stopped_reason = "market_open_0055z"
        return result

    fetcher = fetcher or FinMindLiveFetcher()
    cred = fetcher.credential_status()
    if not cred.get("available"):
        result.status = "credential_unavailable"
        result.reason = f"missing credential at {cred.get('secret_ref_id')}"
        return result
    try:
        require_pyarrow()  # fail before spending any provider quota
    except ParquetDependencyError as exc:
        result.status, result.reason = "dependency_unavailable", str(exc)
        return result

    token_getter = getattr(fetcher, "resolve_token", None)
    token = token_getter() if callable(token_getter) else None

    if max_requests is not None and max_requests > FINMIND_PLAN_HOURLY_CAP:
        result.status = "cap_exceeded"
        result.reason = f"max_requests {max_requests} exceeds plan_hourly_cap {FINMIND_PLAN_HOURLY_CAP}"
        result.stopped_reason = result.reason
        return result
    if min_interval_seconds > 0 and (3600.0 / min_interval_seconds) > FINMIND_PLAN_HOURLY_CAP:
        result.status = "cap_exceeded"
        result.reason = (
            f"request rate {3600.0 / min_interval_seconds:.1f}/h exceeds plan_hourly_cap {FINMIND_PLAN_HOURLY_CAP}"
        )
        result.stopped_reason = result.reason
        return result

    target_root.mkdir(parents=True, exist_ok=True)
    store = keyed_dataset_store(target_root, "tw_broker_top")
    if store is None:
        result.status = "failed"
        result.reason = "keyed tw_broker_top declaration missing"
        return result

    state_path = target_root / "backfill_state.json"
    completed, empty_set, sessions_state = _load_state(state_path)
    partition_stats = _store_partition_totals(store)
    running_total_rows = sum(r for r, _ in partition_stats.values())
    running_total_bytes = sum(b for _, b in partition_stats.values())

    if running_total_bytes > max_total_bytes or running_total_rows > max_total_rows:
        result.status = "cap_exceeded"
        result.reason = (
            f"existing store exceeds cap before run: bytes={running_total_bytes}/{max_total_bytes} "
            f"rows={running_total_rows}/{max_total_rows}"
        )
        result.stopped_reason = result.reason
        result.total_stored_sessions = len(partition_stats)
        result.total_stored_rows = running_total_rows
        result.total_stored_bytes = running_total_bytes
        return result

    report = FinMindTaiwanBrokerDailyReportAdapter(max_rank=max_rank)
    last_request: float | None = None
    run_started = clock()

    for day in weekdays(start, end):
        day_s = day.isoformat()
        if day_s in completed and (day_s in empty_set or day_s in partition_stats or not partition_stats):
            # If the date was marked complete in state and its keyed partition exists (or it was an empty holiday),
            # skip without refetching.
            if day_s in empty_set or day_s in partition_stats:
                result.skipped_completed.append(day_s)
                if day_s in sessions_state:
                    result.session_symbol_counts[day_s] = int(
                        sessions_state[day_s].get("symbols_with_both_sides")
                        or sessions_state[day_s].get("symbols")
                        or 0
                    )
                continue
            completed.discard(day_s)

        if day_s not in completed and day_s in partition_stats:
            prior_receipt = read_whole_market_receipt(target_root, day_s)
            if prior_receipt and prior_receipt.get("status") == "complete":
                readback = _whole_market_readback(store, day_s, None)
                if (
                    readback["coverage"]["partition_verified"]
                    and readback["coverage"]["single_sided_count"] == 0
                    and readback["coverage"]["symbols_with_both_sides"] >= MIN_STORED_SYMBOLS
                ):
                    p_rows, p_bytes = partition_stats[day_s]
                    both_cnt = int(readback["coverage"]["symbols_with_both_sides"])
                    sessions_state[day_s] = {
                        "symbols": int(readback["stored_symbol_count"]),
                        "symbols_with_both_sides": both_cnt,
                        "rows": p_rows,
                        "bytes_compressed": p_bytes,
                    }
                    completed.add(day_s)
                    _save_state(state_path, completed, empty_set, sessions_state)
                    result.skipped_completed.append(day_s)
                    result.session_symbol_counts[day_s] = both_cnt
                    continue

        if max_requests is not None and result.requests_made >= max_requests:
            result.stopped_reason = "max_requests_reached"
            break
        if max_wall_seconds is not None and (clock() - run_started) >= max_wall_seconds:
            result.stopped_reason = "max_wall_seconds_reached"
            break
        guard_reason = _check_market_open(max(0.0, clock() - run_started))
        if guard_reason:
            result.reason = guard_reason
            result.stopped_reason = "market_open_0055z"
            if not result.completed and result.requests_made == 0:
                result.status = "not_run"
            break

        if last_request is not None:
            wait = min_interval_seconds - (clock() - last_request)
            if wait > 0:
                sleep(wait)
        last_request = clock()
        try:
            raw, meta = fetcher.fetch_storage_objects_raw(FINMIND_BROKER_REPORT_DATASET, day_s)
            result.requests_made += 1
            if raw[:4] == PARQUET_MAGIC:
                try:
                    agg = aggregate_branch_rows(raw)
                except Exception as exc:  # corrupt/truncated body: fail this day only
                    raise FinMindFetchError(
                        f"parquet decode failed: {type(exc).__name__}"
                    ) from exc
            else:
                # Non-parquet body: JSON application error/quota/credential raises;
                # an empty-data response is a no-data day; anything else is unexpected.
                payload, _ = fetcher.parse_json_payload(raw, dict(meta))
                if payload.get("data"):
                    raise FinMindFetchError("unexpected non-parquet storage_objects body")
                agg = []
        except FinMindCredentialError as exc:
            result.status, result.reason = "credential_unavailable", type(exc).__name__
            break
        except FinMindQuotaError as exc:
            result.status, result.reason = "quota_exhausted", type(exc).__name__
            break
        except (FinMindFetchError, OSError, ImportError) as exc:
            result.failed[day_s] = _safe_reason(exc, token)[:200]
            result.status = "partial"
            _emit_safe_traceback(day_s, token)
            continue

        if not agg:
            # Holiday / no data: mark done so it is not refetched.
            result.empty_dates.append(day_s)
            empty_set.add(day_s)
            completed.add(day_s)
            _save_state(state_path, completed, empty_set, sessions_state)
            continue

        has_exact_day = any(str(r.get("date") or "")[:10] == day_s for r in agg)
        by_symbol: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in agg:
            row_day = str(row.get("date") or "")[:10]
            if has_exact_day and row_day != day_s:
                continue
            sym = str(row.get("stock_id") or row.get("data_id") or "").strip().upper()
            if sym:
                by_symbol[sym].append({**row, "date": day_s})

        if not by_symbol:
            result.empty_dates.append(day_s)
            empty_set.add(day_s)
            completed.add(day_s)
            _save_state(state_path, completed, empty_set, sessions_state)
            continue

        available_time = datetime.now(timezone.utc).isoformat()
        day_run_id = run_id or f"broker-backfill-{day_s}"
        compact_records: list[dict[str, Any]] = []
        per_symbol_sides: dict[str, set[str]] = defaultdict(set)
        for sym, sym_rows in sorted(by_symbol.items()):
            top_rows = report.top_rows_from_payload(
                sym_rows, symbol=sym, trade_date=day_s, available_time=available_time
            )
            for rec in report.compact_keyed_records(top_rows, run_id=day_run_id):
                rec["metadata"]["dataset"] = "tw_broker_top"
                rec["metadata"]["event_time"] = day_s
                nrow = rec["metadata"]["normalized_row"]
                per_symbol_sides[nrow["symbol"]].add(nrow["side"])
                compact_records.append(rec)

        old_rows, old_bytes = partition_stats.get(day_s, (0, 0))
        projected_rows = running_total_rows - old_rows + len(compact_records)
        if len(compact_records) > max_session_rows:
            result.status = "cap_exceeded"
            result.reason = f"session_rows_cap_exceeded on {day_s}: {len(compact_records)} > {max_session_rows}"
            result.stopped_reason = result.reason
            break
        if projected_rows > max_total_rows:
            result.status = "cap_exceeded"
            result.reason = f"total_rows_cap_exceeded on {day_s}: {projected_rows} > {max_total_rows}"
            result.stopped_reason = result.reason
            break

        try:
            store.write_records(compact_records, ingest_run_id=day_run_id)
        except DailyBudgetError as exc:
            result.status = "cap_exceeded"
            result.reason = f"day_budget_exceeded on {day_s}: {_safe_reason(exc, token)}"
            result.stopped_reason = result.reason
            break
        except (KeyedDatasetError, OSError) as exc:
            result.failed[day_s] = _safe_reason(exc, token)[:200]
            result.status = "partial"
            _emit_safe_traceback(day_s, token)
            continue

        manifest = store.read_manifest(day_s) or {}
        part_bytes = int(manifest.get("bytes") or 0)
        part_rows = int(manifest.get("row_count") or len(compact_records))
        projected_bytes = running_total_bytes - old_bytes + part_bytes
        if part_bytes > max_session_bytes or projected_bytes > max_total_bytes:
            store.delete_partition(day_s)
            partition_stats.pop(day_s, None)
            running_total_rows = sum(r for r, _ in partition_stats.values())
            running_total_bytes = sum(b for _, b in partition_stats.values())
            result.status = "cap_exceeded"
            if part_bytes > max_session_bytes:
                result.reason = f"session_bytes_cap_exceeded on {day_s}: {part_bytes} > {max_session_bytes}"
            else:
                result.reason = f"total_bytes_cap_exceeded on {day_s}: {projected_bytes} > {max_total_bytes}"
            result.stopped_reason = result.reason
            break

        partition_stats[day_s] = (part_rows, part_bytes)
        running_total_rows = projected_rows
        running_total_bytes = projected_bytes
        both_sides_count = sum(1 for sides in per_symbol_sides.values() if sides == {"buy", "sell"})
        sessions_state[day_s] = {
            "symbols": len(per_symbol_sides),
            "symbols_with_both_sides": both_sides_count,
            "rows": part_rows,
            "bytes_compressed": part_bytes,
        }
        empty_set.discard(day_s)
        completed.add(day_s)
        _save_state(state_path, completed, empty_set, sessions_state)
        result.completed.append(day_s)
        result.top_records += len(compact_records)
        result.bytes_written += part_bytes
        result.session_symbol_counts[day_s] = both_sides_count or len(per_symbol_sides)

    if result.failed and result.status == "ok":
        result.status = "partial"
    all_weekdays = [d.isoformat() for d in weekdays(start, end)]
    result.remaining_dates_count = sum(1 for d in all_weekdays if d not in completed)
    result.total_stored_sessions = len(partition_stats)
    result.total_stored_rows = running_total_rows
    result.total_stored_bytes = running_total_bytes
    return result


# ---------------------------------------------------------------------------------------------
# SOURCE-BROKER-WHOLE-MARKET-DAILY-20261127: the daily whole-market run.
#
# One SponsorPro whole-day parquet request per published session (the same storage_objects call and
# parquet decode as the backfill above), aggregated per (symbol, branch) inside pyarrow, ranked into
# the top 20 buy and top 20 sell branches by net quantity for EVERY symbol in the file, and written
# as compact records into keyed/tw_broker_top. tw_broker_branch_detail is not written by the daily
# run (decision in the task evidence: every branch x symbol is ~10x the top-20 rows with no reader).
# The source evidence store receives nothing per row; the run writes one receipt with counts.
# ---------------------------------------------------------------------------------------------
WHOLE_MARKET_CONNECTOR_ID = "tw-finmind-broker-daily-report"
WHOLE_MARKET_RECEIPT_SCHEMA = "finmind_broker_whole_market_receipt.v1"
WHOLE_MARKET_RECEIPT_DIR = ("receipts", "tw_broker_whole_market")
# Requests per run: one storage_objects call (parquet body). The plan cap is 6,000/h (SponsorYear);
# the shared KBar self-limit already leaves 25 % headroom, so this run's 1 request stays far inside it.
WHOLE_MARKET_REQUESTS_PER_RUN = 1
FINMIND_PLAN_HOURLY_CAP = 6000
# Coverage gate: the expected set is the 4-digit share codes (TWSE and TPEx ordinary shares) that
# traded that session in daily_bars (volume > 0). The run is complete only when at most this share
# of them is missing from the stored partition and at least MIN_STORED_SYMBOLS symbols are stored.
COVERAGE_MAX_MISSING_RATIO = 0.02
MIN_STORED_SYMBOLS = 1000
RECEIPT_RETENTION_SESSIONS = 750  # same as keyed tw_broker_top retention; older receipts are pruned
_SHARE_CODE_RE = re.compile(r"^[0-9]{4}$")

# Run statuses. Only "complete" (or "already_complete") is fresh. ``fallback_required`` tells the
# caller to keep the per-symbol path for the active symbols and to tell the coordinator.
REFUSAL_STATUSES = frozenset({"credential_unavailable", "dependency_unavailable", "provider_refused", "no_data"})


def _receipt_dir(root: Path) -> Path:
    return root.joinpath(*WHOLE_MARKET_RECEIPT_DIR)


def _receipt_path(root: Path, trade_date: str) -> Path:
    return _receipt_dir(root) / f"date={trade_date}.json"


def read_whole_market_receipt(root: str | Path, trade_date: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_receipt_path(Path(root), trade_date).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_receipt(root: Path, receipt: Mapping[str, Any]) -> Path:
    path = _receipt_path(root, str(receipt["trade_date"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    # Bounded: one receipt per session, pruned past the keyed retention.
    receipts = sorted(_receipt_dir(root).glob("date=*.json"))
    for stale in receipts[: max(0, len(receipts) - RECEIPT_RETENTION_SESSIONS)]:
        stale.unlink(missing_ok=True)
    return path


def traded_share_symbols(root: str | Path, trade_date: str) -> set[str] | None:
    """Share codes that traded ``trade_date`` according to daily_bars (volume > 0), or None when the
    governed daily bars hold no such day. daily_bars only stores symbols of the governed listing."""
    from .daily_bars_store import STATUS_ONLY_KIND, DailyBarsStore

    store = DailyBarsStore(Path(root))
    manifest = store.read_manifest(trade_date)
    if not manifest:
        return None
    pq = require_pyarrow()
    out: set[str] = set()
    for entry in manifest.get("files", ()):
        if entry.get("kind") == STATUS_ONLY_KIND:
            continue
        table = pq.read_table(store.bars_dir / entry["path"], columns=["symbol", "volume"])
        for sym, vol in zip(table["symbol"].to_pylist(), table["volume"].to_pylist()):
            code = str(sym or "").strip().upper().split(".", 1)[0]
            if vol and float(vol) > 0 and _SHARE_CODE_RE.match(code):
                out.add(code)
    return out


def aggregate_branch_rows(raw: bytes) -> list[dict[str, Any]]:
    """Decode a whole-day parquet body and sum it per (date, symbol, branch) inside pyarrow, so the
    1-2 M price-level rows of a session never become Python dicts. Each output row keeps the count
    of price levels it summarised."""
    import io

    pq = require_pyarrow()
    if pq is None:
        return decode_parquet_bytes(raw)
    import pyarrow as pa  # type: ignore[import-not-found]

    table = pq.read_table(io.BytesIO(raw))
    names = set(table.column_names)

    def pick(*cands: str) -> str | None:
        return next((c for c in cands if c in names), None)

    sym_col = pick("stock_id", "data_id")
    date_col = pick("date")
    broker_col = pick("securities_trader")
    broker_id_col = pick("securities_trader_id")
    buy_col, sell_col = pick("buy"), pick("sell")
    if not sym_col or not date_col or not (broker_col or broker_id_col) or not buy_col or not sell_col:
        raise FinMindFetchError(f"whole-day parquet lacks required columns: {sorted(names)[:12]}")
    keys = [c for c in (date_col, sym_col, broker_id_col, broker_col) if c]
    cols = {c: table[c].cast(pa.string()) for c in keys}
    cols["buy"] = table[buy_col].cast(pa.int64())
    cols["sell"] = table[sell_col].cast(pa.int64())
    slim = pa.table(cols)
    grouped = slim.group_by(keys).aggregate([("buy", "sum"), ("sell", "sum"), ("buy", "count")])
    out_cols = {name: grouped[name].to_pylist() for name in grouped.column_names}
    n = grouped.num_rows
    return [
        {
            "date": str(out_cols[date_col][i])[:10],
            "stock_id": out_cols[sym_col][i],
            "securities_trader_id": out_cols[broker_id_col][i] if broker_id_col else "",
            "securities_trader": out_cols[broker_col][i] if broker_col else out_cols[broker_id_col][i],
            "buy": out_cols["buy_sum"][i] or 0,
            "sell": out_cols["sell_sum"][i] or 0,
            "price_levels": out_cols["buy_count"][i] or 0,
        }
        for i in range(n)
    ]


def _safe_reason(exc: BaseException, token: str | None) -> str:
    text = f"{type(exc).__name__}: {exc}"[:300]
    return text.replace(token, "***") if token else text


def _emit_safe_traceback(day_s: str, token: str | None) -> None:
    tb = traceback.format_exc()
    if token:
        tb = tb.replace(token, "***")
    print(f"[finmind_broker_backfill] session {day_s} failed:\n{tb.rstrip()}", file=sys.stderr, flush=True)


def _whole_market_readback(store: Any, trade_date: str, expected: set[str] | None) -> dict[str, Any]:
    """Coverage is measured from verified persisted keys, including on a complete receipt's rerun."""
    check = store.verify_partition(trade_date)
    per_symbol: dict[str, set[str]] = defaultdict(set)
    sides = {"buy": 0, "sell": 0}
    if check.get("verified"):
        for symbol, day, side, rank in check["keys"]:
            if day == trade_date and side in sides:
                per_symbol[symbol].add(side)
                sides[side] += 1
    both_sides = {symbol for symbol, values in per_symbol.items() if values == {"buy", "sell"}}
    single_sided = sorted(set(per_symbol) - both_sides)
    coverage: dict[str, Any] = {
        "max_missing_ratio": COVERAGE_MAX_MISSING_RATIO, "min_stored_symbols": MIN_STORED_SYMBOLS,
        "expected_basis": "daily_bars volume>0, 4-digit share codes",
        "partition_verified": bool(check.get("verified")),
        "symbols_with_both_sides": len(both_sides),
        "single_sided_count": len(single_sided), "single_sided_sample": single_sided[:20],
    }
    if expected is None:
        coverage.update(expected_symbols=None, missing_symbols=None)
    else:
        missing = sorted(expected - both_sides)
        ratio = len(missing) / len(expected) if expected else 1.0
        coverage.update(expected_symbols=len(expected), missing_count=len(missing),
                        missing_ratio=round(ratio, 4), missing_sample=missing[:20])
    if not check.get("verified"):
        reason = f"coverage_unverified: persisted partition {check.get('reason')}"
    elif not expected:
        reason = "coverage_unverified: daily_bars has no traded share for the session"
    elif single_sided:
        reason = f"coverage incomplete: {len(single_sided)} stored symbols lack buy or sell side"
    elif ratio > COVERAGE_MAX_MISSING_RATIO or len(both_sides) < MIN_STORED_SYMBOLS:
        reason = (f"coverage {len(expected) - len(missing)}/{len(expected)} expected symbols, "
                  f"{len(both_sides)} stored with both sides")
    else:
        reason = None
    return {"status": "partial" if reason else "complete", "reason": reason, "coverage": coverage,
            "stored_symbol_count": len(per_symbol), "rows_by_side": sides,
            "partition_row_count": int(check.get("row_count") or 0)}


def run_whole_market_session(
    *,
    market_data_root: str | Path,
    trade_date: str,
    fetcher: FinMindLiveFetcher | None = None,
    expected_symbols: set[str] | None = None,
    max_rank: int = 20,
    run_id: str = "",
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Store the whole market's top-20 buy/sell branches for one published session.

    Idempotent: a session whose receipt says complete and whose keyed partition still verifies is
    not refetched (0 requests). Never prints or stores the token. Returns the receipt."""
    from .daily_bars_store import DailyBudgetError, KeyedDatasetError, keyed_dataset_store

    root = Path(market_data_root)
    started = now()
    receipt: dict[str, Any] = {
        "schema": WHOLE_MARKET_RECEIPT_SCHEMA, "connector_id": WHOLE_MARKET_CONNECTOR_ID,
        "dataset": "tw_broker_top", "source_dataset": FINMIND_BROKER_REPORT_DATASET,
        "trade_date": trade_date, "run_id": run_id, "started_at": started.isoformat(),
        "requests_made": 0, "plan_hourly_cap": FINMIND_PLAN_HOURLY_CAP,
        "license_scope": "vendor_research", "redistribution_allowed": False,
        "branch_detail_written": False, "status": "failed", "reason": None, "fresh": False,
        "fallback_required": False,
    }
    store = keyed_dataset_store(root, "tw_broker_top")
    expected = expected_symbols if expected_symbols is not None else traded_share_symbols(root, trade_date)
    prior = read_whole_market_receipt(root, trade_date)
    if prior and prior.get("status") == "complete" and store is not None:
        readback = _whole_market_readback(store, trade_date, expected)
        if readback["status"] == "complete":
            return {**prior, **readback, "status": "already_complete", "fresh": True, "requests_made": 0,
                    "checked_at": started.isoformat()}

    fetcher = fetcher or FinMindLiveFetcher()
    token = fetcher.resolve_token()

    def finish(status: str, reason: str | None = None, **extra: Any) -> dict[str, Any]:
        receipt.update(extra)
        receipt["status"], receipt["reason"] = status, reason
        receipt["fresh"] = status == "complete"
        receipt["fallback_required"] = status in REFUSAL_STATUSES
        receipt["finished_at"] = now().isoformat()
        try:
            _write_receipt(root, receipt)
        except OSError as exc:  # the receipt is evidence, not data: report, do not mask the status
            receipt["receipt_error"] = f"{type(exc).__name__}"
        return dict(receipt)

    if not fetcher.credential_status().get("available"):
        return finish("credential_unavailable", f"missing credential at {fetcher.secret_ref_id}")
    try:
        require_pyarrow()
    except ParquetDependencyError as exc:
        return finish("dependency_unavailable", str(exc))
    if store is None:
        return finish("failed", "keyed tw_broker_top declaration missing")

    try:
        raw, meta = fetcher.fetch_storage_objects_raw(FINMIND_BROKER_REPORT_DATASET, trade_date)
        receipt["requests_made"] = WHOLE_MARKET_REQUESTS_PER_RUN
        receipt["provider_http_status"] = meta.get("http_status")
        for key in ("rate_limit_remaining", "rate_limit_limit", "x-ratelimit-remaining", "x-ratelimit-limit"):
            if key in meta:
                receipt.setdefault("provider_quota", {})[key] = meta[key]
        receipt["download_bytes"] = len(raw)
        if raw[:4] != PARQUET_MAGIC:
            payload, _ = fetcher.parse_json_payload(raw, dict(meta))
            if payload.get("data"):
                raise FinMindFetchError("unexpected non-parquet storage_objects body")
            return finish("no_data", "provider returned no whole-day file for the session")
        agg = aggregate_branch_rows(raw)
    except FinMindCredentialError as exc:
        receipt["requests_made"] = max(receipt["requests_made"], WHOLE_MARKET_REQUESTS_PER_RUN)
        return finish("provider_refused", _safe_reason(exc, token), provider_reason_code="credential_or_plan_rejected")
    except FinMindQuotaError as exc:
        receipt["requests_made"] = max(receipt["requests_made"], WHOLE_MARKET_REQUESTS_PER_RUN)
        return finish("provider_refused", _safe_reason(exc, token), provider_reason_code="quota_exhausted")
    except FinMindFetchError as exc:
        receipt["requests_made"] = max(receipt["requests_made"], WHOLE_MARKET_REQUESTS_PER_RUN)
        return finish("provider_refused", _safe_reason(exc, token), provider_reason_code="provider_error")
    except Exception as exc:  # noqa: BLE001 - corrupt body / network: no data was written
        receipt["requests_made"] = max(receipt["requests_made"], WHOLE_MARKET_REQUESTS_PER_RUN)
        return finish("failed", _safe_reason(exc, token))

    by_symbol: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in agg:
        if str(row.get("date") or "")[:10] != trade_date:
            continue
        sym = str(row.get("stock_id") or "").strip().upper()
        if sym:
            by_symbol[sym].append(row)
    if not by_symbol:
        return finish("no_data", "whole-day file holds no rows for the session",
                      source_branch_rows=len(agg))
    report = FinMindTaiwanBrokerDailyReportAdapter(max_rank=max_rank)
    available_time = now().isoformat()
    records: list[dict[str, Any]] = []
    for sym in sorted(by_symbol):
        top = report.top_rows_from_payload(by_symbol[sym], symbol=sym, trade_date=trade_date,
                                           available_time=available_time)
        records.extend(report.compact_keyed_records(top, run_id=run_id))
    uncompressed = sum(len(json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")) + 1
                       for r in records)
    try:
        write = store.write_records(records, ingest_run_id=run_id)
    except DailyBudgetError as exc:
        return finish("failed", str(exc)[:300], rows_built=len(records))
    except (KeyedDatasetError, OSError) as exc:
        return finish("failed", _safe_reason(exc, token), rows_built=len(records))
    manifest = store.read_manifest(trade_date) or {}

    readback = _whole_market_readback(store, trade_date, expected)
    return finish(
        **readback,
        source_branch_rows=len(agg),
        rows=len(records),
        bytes_uncompressed=uncompressed,
        bytes_compressed=int(manifest.get("bytes") or 0),
        partition_written=bool(write.get("partitions_written")),
    )


def verify_two_year_backfill_coverage(
    market_data_root: str | Path,
    *,
    min_sessions: int = BACKFILL_MIN_COVERAGE_SESSIONS,
    min_symbols_per_session: int = 1,
    years: int = BACKFILL_YEARS,
) -> dict[str, Any]:
    """Read-only hosted acceptance check for the two-year keyed ``tw_broker_top`` dataset.

    Verifies that ``keyed/tw_broker_top`` covers at least ``min_sessions`` trading sessions from two years
    before the latest stored session to the latest session, that every session in the window has both buy
    and sell sides and at least ``min_symbols_per_session`` two-sided symbols, and records per-session
    symbol counts. Never creates directories, never mutates manifests or partitions, and makes 0 requests.
    """
    from .daily_bars_store import KEYED_DATASETS, KeyedDatasetStore

    root = Path(market_data_root)
    base_dir = root / "keyed" / "tw_broker_top"
    manifest_dir = base_dir / "_manifest"
    decl = KEYED_DATASETS["tw_broker_top"]
    if not manifest_dir.is_dir():
        return {
            "schema": BACKFILL_COVERAGE_SCHEMA,
            "dataset": "tw_broker_top",
            "read_only": True,
            "status": "fail",
            "passed": False,
            "reason": "no_keyed_tw_broker_top_manifests",
            "min_sessions_required": min_sessions,
            "min_symbols_per_session_required": min_symbols_per_session,
            "window_start": None,
            "latest_session": None,
            "sessions_in_window": 0,
            "two_sided_sessions_count": 0,
            "total_rows_in_window": 0,
            "total_bytes_compressed_in_window": 0,
            "per_session_symbol_counts": {},
            "deficient_sessions": [],
        }

    store = KeyedDatasetStore(root, decl)
    partitions = [p for p in store.partitions() if re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", p)]
    if not partitions:
        return {
            "schema": BACKFILL_COVERAGE_SCHEMA,
            "dataset": "tw_broker_top",
            "read_only": True,
            "status": "fail",
            "passed": False,
            "reason": "no_stored_sessions",
            "min_sessions_required": min_sessions,
            "min_symbols_per_session_required": min_symbols_per_session,
            "window_start": None,
            "latest_session": None,
            "sessions_in_window": 0,
            "two_sided_sessions_count": 0,
            "total_rows_in_window": 0,
            "total_bytes_compressed_in_window": 0,
            "per_session_symbol_counts": {},
            "deficient_sessions": [],
        }

    latest_session = max(partitions)
    window_start = (date.fromisoformat(latest_session) - timedelta(days=365 * years)).isoformat()
    window_sessions = [d for d in partitions if window_start <= d <= latest_session]

    per_session_symbol_counts: dict[str, int] = {}
    two_sided_sessions: list[str] = []
    deficient_sessions: list[dict[str, Any]] = []
    total_rows = 0
    total_bytes = 0

    for day_s in window_sessions:
        try:
            manifest = store.read_manifest(day_s)
        except Exception as exc:  # noqa: BLE001
            deficient_sessions.append({"session": day_s, "reason": f"manifest_error: {type(exc).__name__}"})
            continue
        if not manifest or manifest.get("key_fields") != list(decl.key_fields):
            deficient_sessions.append({"session": day_s, "reason": "key_fields_mismatch_or_missing_manifest"})
            continue

        part_bytes = 0
        part_rows = 0
        sides_by_symbol: dict[str, set[str]] = defaultdict(set)
        side_totals = {"buy": 0, "sell": 0}
        corrupt_reason: str | None = None

        for entry in manifest.get("files", ()):
            path = base_dir / str(entry.get("path") or "")
            expected_bytes = int(entry.get("bytes") or 0)
            if not path.is_file() or path.stat().st_size != expected_bytes:
                corrupt_reason = "missing_or_size_mismatched_part_file"
                break
            part_bytes += expected_bytes
            try:
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        rec = json.loads(line)
                        keyed = store.key_of(rec)
                        if keyed is None:
                            corrupt_reason = "record_missing_key"
                            break
                        sym, part_day, side, _rank = keyed[0]
                        if part_day != day_s or side not in side_totals:
                            corrupt_reason = "record_partition_or_side_mismatch"
                            break
                        md = rec.get("metadata") if isinstance(rec.get("metadata"), Mapping) else {}
                        if md.get("license_scope") != "vendor_research" or md.get("redistribution_allowed") is not False:
                            corrupt_reason = "licence_metadata_invalid"
                            break
                        sides_by_symbol[sym].add(side)
                        side_totals[side] += 1
                        part_rows += 1
            except Exception as exc:  # noqa: BLE001
                corrupt_reason = f"read_error: {type(exc).__name__}"
                break
            if corrupt_reason:
                break

        if corrupt_reason:
            deficient_sessions.append({"session": day_s, "reason": corrupt_reason})
            continue
        if part_rows != int(manifest.get("row_count") or 0) or part_bytes != int(manifest.get("bytes") or 0):
            deficient_sessions.append({"session": day_s, "reason": "manifest_count_or_bytes_mismatch"})
            continue
        if decl.day_budget_bytes is not None and part_bytes > decl.day_budget_bytes:
            deficient_sessions.append({"session": day_s, "reason": f"over_day_budget:{part_bytes}"})
            continue

        both_sides_count = sum(1 for s in sides_by_symbol.values() if s == {"buy", "sell"})
        per_session_symbol_counts[day_s] = both_sides_count
        total_rows += part_rows
        total_bytes += part_bytes

        if side_totals["buy"] == 0 or side_totals["sell"] == 0:
            deficient_sessions.append({"session": day_s, "reason": "missing_buy_or_sell_side"})
        elif both_sides_count < min_symbols_per_session:
            deficient_sessions.append(
                {
                    "session": day_s,
                    "reason": f"two_sided_symbols_{both_sides_count}_below_{min_symbols_per_session}",
                }
            )
        else:
            two_sided_sessions.append(day_s)

    passed = (
        decl.retention_days == 750
        and len(two_sided_sessions) >= min_sessions
        and len(deficient_sessions) == 0
        and total_bytes <= BACKFILL_MAX_TOTAL_BYTES
        and total_rows <= BACKFILL_MAX_TOTAL_ROWS
    )
    reason: str | None = None
    if not passed:
        if decl.retention_days != 750:
            reason = f"keyed_retention_days {decl.retention_days} != 750"
        elif deficient_sessions:
            reason = f"{len(deficient_sessions)} deficient sessions (first: {deficient_sessions[0]})"
        elif total_bytes > BACKFILL_MAX_TOTAL_BYTES:
            reason = f"total_bytes {total_bytes} exceeds cap {BACKFILL_MAX_TOTAL_BYTES}"
        elif total_rows > BACKFILL_MAX_TOTAL_ROWS:
            reason = f"total_rows {total_rows} exceeds cap {BACKFILL_MAX_TOTAL_ROWS}"
        else:
            reason = f"two_sided_sessions {len(two_sided_sessions)} < required {min_sessions}"

    return {
        "schema": BACKFILL_COVERAGE_SCHEMA,
        "dataset": "tw_broker_top",
        "read_only": True,
        "status": "pass" if passed else "fail",
        "passed": passed,
        "reason": reason,
        "window_start": window_start,
        "latest_session": latest_session,
        "min_sessions_required": min_sessions,
        "min_symbols_per_session_required": min_symbols_per_session,
        "sessions_in_window": len(window_sessions),
        "two_sided_sessions_count": len(two_sided_sessions),
        "total_rows_in_window": total_rows,
        "total_bytes_compressed_in_window": total_bytes,
        "max_total_bytes_cap": BACKFILL_MAX_TOTAL_BYTES,
        "max_total_rows_cap": BACKFILL_MAX_TOTAL_ROWS,
        "keyed_retention_days": decl.retention_days,
        "min_session_symbols_with_both_sides": min(per_session_symbol_counts.values(), default=0),
        "max_session_symbols_with_both_sides": max(per_session_symbol_counts.values(), default=0),
        "per_session_symbol_counts": per_session_symbol_counts,
        "deficient_sessions": deficient_sessions[:20],
    }


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "daily":
        return daily_main(argv[1:])
    if argv and argv[0] == "verify":
        return verify_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--market-data-root", type=Path, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--min-interval-seconds", type=float, default=DEFAULT_MIN_INTERVAL_SECONDS)
    parser.add_argument("--max-requests", type=int, default=None)
    parser.add_argument("--max-wall-seconds", type=float, default=None)
    parser.add_argument("--max-total-bytes", type=int, default=BACKFILL_MAX_TOTAL_BYTES)
    parser.add_argument("--max-total-rows", type=int, default=BACKFILL_MAX_TOTAL_ROWS)
    parser.add_argument("--run-id", default="")
    args = parser.parse_args(argv)
    target_root = args.market_data_root or args.output_dir
    if target_root is None:
        parser.error("one of --output-dir or --market-data-root is required")
    start = args.start or (args.end - timedelta(days=365 * BACKFILL_YEARS))
    result = run_backfill(
        output_dir=target_root,
        start=start,
        end=args.end,
        min_interval_seconds=args.min_interval_seconds,
        max_requests=args.max_requests,
        max_wall_seconds=args.max_wall_seconds,
        max_total_bytes=args.max_total_bytes,
        max_total_rows=args.max_total_rows,
        run_id=args.run_id,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if result.status == "ok" else 1


def daily_main(argv: Sequence[str]) -> int:
    """``python -m services.source_ingestion.finmind_broker_backfill daily --market-data-root R --trade-date D``.
    Prints the run receipt as one JSON line (no token). Exit 0 for complete/already_complete/partial and for a
    provider refusal (the caller reads ``fallback_required``); exit 1 only for a failed write."""
    parser = argparse.ArgumentParser(description="FinMind whole-market daily broker top-20")
    parser.add_argument("--market-data-root", required=True, type=Path)
    parser.add_argument("--trade-date", required=True, type=date.fromisoformat)
    parser.add_argument("--run-id", default="")
    args = parser.parse_args(list(argv))
    receipt = run_whole_market_session(market_data_root=args.market_data_root,
                                       trade_date=args.trade_date.isoformat(), run_id=args.run_id)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 1 if receipt.get("status") == "failed" else 0


def verify_main(argv: Sequence[str]) -> int:
    """``python -m services.source_ingestion.finmind_broker_backfill verify --market-data-root R``.
    Read-only two-year coverage check for keyed ``tw_broker_top``."""
    parser = argparse.ArgumentParser(description="Read-only FinMind two-year broker coverage check")
    parser.add_argument("--market-data-root", required=True, type=Path)
    parser.add_argument("--min-sessions", type=int, default=BACKFILL_MIN_COVERAGE_SESSIONS)
    parser.add_argument("--min-symbols-per-session", type=int, default=1)
    parser.add_argument("--years", type=int, default=BACKFILL_YEARS)
    args = parser.parse_args(list(argv))
    report = verify_two_year_backfill_coverage(
        args.market_data_root,
        min_sessions=args.min_sessions,
        min_symbols_per_session=args.min_symbols_per_session,
        years=args.years,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
