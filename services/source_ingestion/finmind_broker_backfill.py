"""One-time FinMind broker-branch backfill (TaiwanStockTradingDailyReport).

Walks weekdays over a two-year window, fetches each date's SponsorPro
storage-object manifest through the existing bulk parquet adapter, loads the
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

RowsLoader = Callable[[Sequence[Mapping[str, Any]]], Sequence[Mapping[str, Any]]]


PARQUET_MAGIC = b"PAR1"
PYARROW_REQUIREMENTS = "docs/deployment/evidence/FINMIND-BROKER-CONNECTOR-BACKFILL-20261111/requirements-finmind-backfill.txt"


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


def default_parquet_rows_loader(manifest_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Download signed parquet objects and return rows (needs pyarrow at runtime)."""
    import io
    import urllib.request

    pq = require_pyarrow()

    from services.external_egress import open_external_url

    rows: list[dict[str, Any]] = []
    for item in manifest_rows:
        url = item.get("url") or item.get("signed_url") or item.get("download_url")
        if not url:
            continue
        request = urllib.request.Request(str(url), headers={"User-Agent": "pantheon-source-ingest/0.1"})
        with open_external_url(request, caller="source_ingest.finmind_broker_backfill", timeout=60) as resp:
            rows.extend(pq.read_table(io.BytesIO(resp.read())).to_pylist())
    return rows


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
    rows_loader: RowsLoader = default_parquet_rows_loader,
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
                # Official shape: response body is the parquet file itself.
                rows = decode_parquet_bytes(raw)
                manifest_records = bulk.records_from_storage_objects_payload(
                    {"data": [{"object_name": f"{FINMIND_BROKER_REPORT_DATASET}/{day_s}.parquet",
                               "row_count": len(rows)}]},
                    date=day_s,
                )
            else:
                # JSON: application error (raises) or a signed-URL manifest.
                payload, _ = fetcher.parse_json_payload(raw, dict(meta))
                manifest_records = bulk.records_from_storage_objects_payload(payload, date=day_s)
                manifest_rows = [
                    row for row in (payload.get("data") or []) if isinstance(row, Mapping)
                ]
                rows = list(rows_loader(manifest_rows)) if manifest_rows else []
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


def main(argv: Sequence[str] | None = None) -> int:
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


if __name__ == "__main__":
    sys.exit(main())
