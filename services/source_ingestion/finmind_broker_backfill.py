"""One-time FinMind broker-branch backfill (TaiwanStockTradingDailyReport).

Walks weekdays over a two-year window, fetches each date's SponsorPro
storage-object parquet body through the existing bulk parquet adapter, decodes the
branch rows, and writes governed ``tw_broker_top`` and
``tw_broker_branch_detail`` records as JSONL partitions.

* Resumable: a state file records completed dates; they are never refetched.
* Throttled: default 1 request per 1.5s (<=2400/h) vs. the 6000/h SponsorYear cap.
* No token -> records a ``credential_unavailable`` reason and fetches nothing.
* FinMind data is licence ``vendor_research`` and not redistributable. Nothing
  here writes to ``normalized/tw_price_daily``.

Usage::

    python -m services.source_ingestion.finmind_broker_backfill --output-dir DIR
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .connectors.base import SourceRecord
from .connectors.finmind_taiwan import (
    FINMIND_BROKER_REPORT_DATASET,
    FinMindCredentialError,
    FinMindFetchError,
    FinMindLiveFetcher,
    FinMindQuotaError,
    FinMindTaiwanBrokerBulkBackfillAdapter,
    FinMindTaiwanBrokerDailyReportAdapter,
)

BACKFILL_YEARS = 2
DEFAULT_MIN_INTERVAL_SECONDS = 1.5
STATE_SCHEMA = "finmind_broker_backfill_state.v1"

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


@dataclass
class BackfillResult:
    status: str = "ok"  # ok | credential_unavailable | quota_exhausted | partial
    reason: str | None = None
    completed: list[str] = field(default_factory=list)
    skipped_completed: list[str] = field(default_factory=list)
    empty_dates: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    requests_made: int = 0
    top_records: int = 0
    detail_records: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "completed_count": len(self.completed),
            "skipped_completed_count": len(self.skipped_completed),
            "empty_dates": self.empty_dates,
            "failed": self.failed,
            "requests_made": self.requests_made,
            "top_records": self.top_records,
            "detail_records": self.detail_records,
        }


def _load_state(path: Path) -> set[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()
    return set(data.get("completed_dates", [])) if isinstance(data, dict) else set()


def _save_state(path: Path, completed: set[str]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"schema": STATE_SCHEMA, "dataset": FINMIND_BROKER_REPORT_DATASET,
                    "completed_dates": sorted(completed)}, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)


def _record_dict(record: SourceRecord) -> dict[str, Any]:
    return {
        "source_id": record.source_id,
        "connector_id": record.connector_id,
        "title": record.title,
        "content_ref": record.content_ref,
        "metadata": dict(record.metadata),
    }


def _write_partition(path: Path, records: Sequence[SourceRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(_record_dict(record), ensure_ascii=False, sort_keys=True, default=str) + "\n")
    tmp.replace(path)


def run_backfill(
    *,
    output_dir: Path,
    start: date,
    end: date,
    fetcher: FinMindLiveFetcher | None = None,
    min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    max_rank: int = 20,
) -> BackfillResult:
    fetcher = fetcher or FinMindLiveFetcher()
    result = BackfillResult()
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

    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "backfill_state.json"
    completed = _load_state(state_path)
    bulk = FinMindTaiwanBrokerBulkBackfillAdapter()
    report = FinMindTaiwanBrokerDailyReportAdapter(max_rank=max_rank)
    last_request: float | None = None

    for day in weekdays(start, end):
        day_s = day.isoformat()
        if day_s in completed:
            result.skipped_completed.append(day_s)
            continue
        if last_request is not None:
            wait = min_interval_seconds - (clock() - last_request)
            if wait > 0:
                sleep(wait)
        last_request = clock()
        try:
            raw, meta = fetcher.fetch_storage_objects_raw(FINMIND_BROKER_REPORT_DATASET, day_s)
            result.requests_made += 1
            if raw[:4] == PARQUET_MAGIC:
                # The response body is the parquet file itself.
                try:
                    rows = decode_parquet_bytes(raw)
                except Exception as exc:  # corrupt/truncated body: fail this day only
                    raise FinMindFetchError(
                        f"parquet decode failed: {type(exc).__name__}"
                    ) from exc
                manifest_records = bulk.records_from_storage_objects_payload(
                    {"data": [{"object_name": f"{FINMIND_BROKER_REPORT_DATASET}/{day_s}.parquet",
                               "row_count": len(rows)}]},
                    date=day_s,
                )
            else:
                # Non-parquet body: JSON application error/quota/credential raises;
                # an empty-data response is a no-data day; anything else is unexpected.
                payload, _ = fetcher.parse_json_payload(raw, dict(meta))
                if payload.get("data"):
                    raise FinMindFetchError("unexpected non-parquet storage_objects body")
                rows, manifest_records = [], []
        except FinMindCredentialError as exc:
            result.status, result.reason = "credential_unavailable", type(exc).__name__
            break
        except FinMindQuotaError as exc:
            result.status, result.reason = "quota_exhausted", type(exc).__name__
            break
        except (FinMindFetchError, OSError, ImportError) as exc:
            result.failed[day_s] = f"{type(exc).__name__}: {exc}"[:200]
            result.status = "partial"
            continue

        if not rows:
            # Holiday / no data: mark done so it is not refetched.
            result.empty_dates.append(day_s)
            completed.add(day_s)
            _save_state(state_path, completed)
            continue

        by_symbol: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            sym = str(row.get("stock_id") or row.get("data_id") or "").upper()
            if sym:
                by_symbol[sym].append(row)
        available_time = datetime.now(timezone.utc).isoformat()
        tops: list[SourceRecord] = []
        for sym, sym_rows in sorted(by_symbol.items()):
            tops.extend(
                report.records_from_daily_report_payload(
                    sym_rows, symbol=sym, trade_date=day_s, available_time=available_time
                )
            )
        details = report.branch_detail_records_from_payload(rows, available_time=available_time)
        part = f"date={day_s}"
        _write_partition(output_dir / "tw_broker_top" / part / "records.jsonl", tops)
        _write_partition(output_dir / "tw_broker_branch_detail" / part / "records.jsonl", details)
        _write_partition(output_dir / "manifest" / part / "records.jsonl", manifest_records)
        # State is committed only after all partitions are durable.
        completed.add(day_s)
        _save_state(state_path, completed)
        result.completed.append(day_s)
        result.top_records += len(tops)
        result.detail_records += len(details)

    if result.failed and result.status == "ok":
        result.status = "partial"
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
    prior = read_whole_market_receipt(root, trade_date)
    if prior and prior.get("status") == "complete" and store is not None:
        check = store.verify_partition(trade_date)
        if check.get("verified"):
            return {**prior, "status": "already_complete", "fresh": True, "requests_made": 0,
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
    sides: dict[str, int] = {"buy": 0, "sell": 0}
    for sym in sorted(by_symbol):
        top = report.top_rows_from_payload(by_symbol[sym], symbol=sym, trade_date=trade_date,
                                           available_time=available_time)
        for row in top:
            sides[row["side"]] += 1
        records.extend(report.compact_keyed_records(top, run_id=run_id))
    stored_symbols = sorted({r["metadata"]["normalized_row"]["symbol"] for r in records})
    uncompressed = sum(len(json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")) + 1
                       for r in records)
    try:
        write = store.write_records(records, ingest_run_id=run_id)
    except DailyBudgetError as exc:
        return finish("failed", str(exc)[:300], rows_built=len(records))
    except (KeyedDatasetError, OSError) as exc:
        return finish("failed", _safe_reason(exc, token), rows_built=len(records))
    manifest = store.read_manifest(trade_date) or {}

    expected = expected_symbols if expected_symbols is not None else traded_share_symbols(root, trade_date)
    stored_set = set(stored_symbols)
    coverage: dict[str, Any] = {"max_missing_ratio": COVERAGE_MAX_MISSING_RATIO,
                                "min_stored_symbols": MIN_STORED_SYMBOLS,
                                "expected_basis": "daily_bars volume>0, 4-digit share codes"}
    if expected is None:
        status, reason = "partial", "coverage_unverified: daily_bars has no row for the session"
        coverage.update(expected_symbols=None, missing_symbols=None)
    else:
        missing = sorted(set(expected) - stored_set)
        ratio = (len(missing) / len(expected)) if expected else 1.0
        coverage.update(expected_symbols=len(expected), missing_count=len(missing),
                        missing_ratio=round(ratio, 4), missing_sample=missing[:20])
        if not expected:
            status, reason = "partial", "coverage_unverified: no traded share in daily_bars for the session"
        elif ratio > COVERAGE_MAX_MISSING_RATIO or len(stored_set) < MIN_STORED_SYMBOLS:
            status, reason = "partial", (f"coverage {len(expected) - len(missing)}/{len(expected)} expected symbols, "
                                         f"{len(stored_set)} stored")
        else:
            status, reason = "complete", None
    return finish(
        status, reason,
        source_branch_rows=len(agg),
        stored_symbol_count=len(stored_set),
        rows=len(records), rows_by_side=sides,
        bytes_uncompressed=uncompressed,
        bytes_compressed=int(manifest.get("bytes") or 0),
        partition_row_count=int(manifest.get("row_count") or 0),
        partition_written=bool(write.get("partitions_written")),
        coverage=coverage,
    )


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "daily":
        return daily_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--min-interval-seconds", type=float, default=DEFAULT_MIN_INTERVAL_SECONDS)
    args = parser.parse_args(argv)
    start = args.start or (args.end - timedelta(days=365 * BACKFILL_YEARS))
    result = run_backfill(
        output_dir=args.output_dir, start=start, end=args.end,
        min_interval_seconds=args.min_interval_seconds,
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


if __name__ == "__main__":
    sys.exit(main())
