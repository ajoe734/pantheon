"""Complete stored daily bars (``volume``, ``traded_value``) from the retained normalized run files.

DAILY-BARS-COLLECTOR-VOLUME-VALUE-20261125. Until this task the store read ``volume`` and ``value`` while the collector
rows carry ``volume_shares`` and ``turnover_twd``, so almost every stored row has no volume and no traded value. The
mapping is fixed in ``DailyBarsStore.extract_rows``; this module repairs the days that were already stored, with no
manual hosted write: the controller tick calls it (``/api/source-ingest/daily-bars/complete``), and every call is
bounded and resumable.

How a tick works
* Index (newest write-date partition first): a partition of ``normalized/tw_price_daily/date=<write date>/`` is read
  once, line by line, and the trade dates it holds are recorded in the state file. A run file cannot hold a trade date
  later than its write date, so a stored day D is eligible once every partition with write date >= D is indexed.
* Complete (newest stored day first): an eligible day whose traded rows still lack volume or traded value is read from
  its indexed run files (one day of rows in memory), turned into bars by ``extract_rows`` (the unit check refuses a row
  whose close x volume is far from its traded value) and applied with ``DailyBarsStore.complete_day_values``: only null
  values are filled, a non-null value is never overwritten, a source that disagrees is refused and counted. A filled
  day gets a new manifest generation and digest.
* A day with no retained source row is listed (``unretained``), never guessed.
* Bounds per call: ``max_days`` completed days, ``max_seconds`` wall time, ``max_rss_mb`` resident memory, and
  ``max_partitions`` indexed partitions. Hitting a bound stops the call; the state file resumes the next one.
"""

from __future__ import annotations

import gzip
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from .daily_bars_store import _DATE_KEYS, DailyBarsStore, _atomic_write_bytes, _require_pyarrow, validate_trade_date

DATASET = "tw_price_daily"
STATE_FILENAME = "daily_bars_completion_state.json"
STATE_SCHEMA = "daily_bars_completion_state.v1"
MAX_DAYS = int(os.getenv("SOURCE_INGEST_DAILY_BARS_COMPLETION_MAX_DAYS", "3"))
MAX_SECONDS = float(os.getenv("SOURCE_INGEST_DAILY_BARS_COMPLETION_MAX_SECONDS", "20"))
MAX_RSS_MB = float(os.getenv("SOURCE_INGEST_DAILY_BARS_COMPLETION_MAX_RSS_MB", "768"))
MAX_PARTITIONS = int(os.getenv("SOURCE_INGEST_DAILY_BARS_COMPLETION_MAX_PARTITIONS", "40"))
UNRETAINED_LIST_LIMIT = 500


@dataclass
class _Rec:
    metadata: Mapping[str, Any]
    source_id: str = ""
    is_rejected: bool = False


def rss_mb() -> float:
    """Resident set size of this process in MB (0.0 where /proc is absent)."""
    try:
        with open("/proc/self/statm", encoding="utf-8") as handle:
            return int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        return 0.0


def _open(path: Path):
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open("r", encoding="utf-8")


def _iter_lines(path: Path) -> Iterator[Mapping[str, Any]]:
    with _open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, Mapping):
                yield row


def effective_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    """Metadata of a run-file line with ``normalized_row`` completed from the JSON ``body`` (the hosted snapshot lines
    carry both shapes); same rule as ``scripts/backfill_daily_bars.py``."""
    meta = dict(row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {})
    body = meta.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            body = None
    merged: dict[str, Any] = dict(body) if isinstance(body, Mapping) else {}
    if isinstance(meta.get("normalized_row"), Mapping):
        merged.update(meta["normalized_row"])
    if merged:
        meta["normalized_row"] = merged
    return meta


def row_trade_date(row: Mapping[str, Any]) -> str | None:
    meta = effective_metadata(row)
    normalized = meta.get("normalized_row")
    body = normalized if isinstance(normalized, Mapping) else meta
    for src in (body, meta):
        for key in _DATE_KEYS:
            if src.get(key) not in (None, ""):
                return validate_trade_date(src[key])
    return None


def partitions(root: Path) -> list[Path]:
    """Write-date partitions of the normalized run files, newest first."""
    base = root / "normalized" / DATASET
    if not base.is_dir():
        return []
    return sorted((p for p in base.glob("date=*") if p.is_dir()), key=lambda p: p.name, reverse=True)


def partition_files(partition: Path) -> list[Path]:
    files = [p for p in partition.glob("*.jsonl*") if p.suffix in (".jsonl", ".gz")]
    return sorted(files, key=lambda p: (p.stat().st_mtime_ns, p.name))


class DailyBarsCompleter:
    def __init__(self, store: DailyBarsStore, root: str | Path | None = None, *, state_path: str | Path | None = None,
                 clock=time.monotonic, rss=rss_mb) -> None:
        self.store = store
        self.root = Path(root) if root is not None else store.root
        self.state_path = Path(state_path) if state_path else self.root / STATE_FILENAME
        self._clock = clock
        self._rss = rss

    # -- state --------------------------------------------------------------------------------
    def load_state(self) -> dict[str, Any]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state.get("schema_version") == STATE_SCHEMA:
                return state
        except (OSError, ValueError):
            pass
        return {"schema_version": STATE_SCHEMA, "indexed": {}, "files_by_date": {}, "days": {}}

    def _save(self, state: dict[str, Any]) -> None:
        _atomic_write_bytes(self.state_path, json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    # -- what a stored day still lacks ---------------------------------------------------------
    def missing_values(self, trade_date: str) -> dict[str, int] | None:
        """Traded rows (close present) of a stored day with no volume / no traded value; None when no manifest."""
        manifest = self.store.read_manifest(trade_date)
        if manifest is None:
            return None
        _, pq = _require_pyarrow()
        traded = no_volume = no_value = 0
        for entry in manifest.get("files", ()):
            if entry.get("kind"):
                continue  # status-only part: no close, no traded row
            table = pq.read_table(self.store.bars_dir / entry["path"], columns=["close", "volume", "traded_value"])
            for close, volume, value in zip(*(table.column(n).to_pylist() for n in ("close", "volume", "traded_value"))):
                if close is None:
                    continue
                traded += 1
                no_volume += volume is None
                no_value += value is None
        return {"traded_rows": traded, "no_volume": no_volume, "no_value": no_value,
                "digest": manifest.get("content_digest")}

    def volume_report(self, *, since: str, limit_days: int = 60, threshold: float = 0.05) -> dict[str, Any]:
        """Read-only (acceptance check): stored days, newest ``limit_days`` of them, whose traded rows lack a traded
        value for more than ``threshold`` of the rows. ``newer_than_fix`` are those dated after ``since``."""
        dates = sorted(self.store.read_manifest_dates(), reverse=True)[:limit_days]
        bad: list[dict[str, Any]] = []
        for day in dates:
            m = self.missing_values(day)
            if m and m["traded_rows"] and m["no_value"] / m["traded_rows"] > threshold:
                bad.append({"trade_date": day, "traded_rows": m["traded_rows"], "no_value": m["no_value"],
                            "no_volume": m["no_volume"]})
        return {"stored_days": len(self.store.read_manifest_dates()), "checked_days": len(dates), "since": since,
                "days_over_threshold": len(bad), "days_over_threshold_newer_than_fix": sum(1 for b in bad if b["trade_date"] > since),
                "days": bad[:UNRETAINED_LIST_LIMIT]}

    # -- one bounded call ----------------------------------------------------------------------
    def run(self, *, max_days: int = MAX_DAYS, max_seconds: float = MAX_SECONDS, max_rss_mb: float = MAX_RSS_MB,
            max_partitions: int = MAX_PARTITIONS) -> dict[str, Any]:
        started = self._clock()
        state = self.load_state()
        report: dict[str, Any] = {
            "completed_days": [], "unchanged_days": [], "indexed_partitions": 0, "filled_rows": 0,
            "conflicts": 0, "rejected": {}, "stopped": None, "peak_rss_mb": 0.0}

        def out_of_budget() -> str | None:
            report["peak_rss_mb"] = max(report["peak_rss_mb"], round(self._rss(), 1))
            if self._clock() - started >= max_seconds:
                return "seconds"
            if self._rss() >= max_rss_mb:
                return "memory"
            return None

        root_parts = partitions(self.root)
        stored = sorted(self.store.read_manifest_dates(), reverse=True)  # newest first
        for trade_date in stored:
            if report["stopped"]:
                break
            day = state["days"].get(trade_date, {})
            if day.get("status") in ("complete", "unretained", "source_has_no_values"):
                manifest = self.store.read_manifest(trade_date) or {}
                if day.get("digest") == manifest.get("content_digest"):
                    continue
            # eligible once every partition with write date >= trade_date is indexed (newest first)
            for part in root_parts:
                if part.name[5:] < trade_date:
                    break
                if part.name in state["indexed"]:
                    continue
                if report["indexed_partitions"] >= max_partitions:
                    report["stopped"] = "partitions"
                    break
                reason = out_of_budget()
                if reason:
                    report["stopped"] = reason
                    break
                self._index_partition(state, part)
                report["indexed_partitions"] += 1
            if report["stopped"]:
                break
            if len(report["completed_days"]) + len(report["unchanged_days"]) >= max_days:
                report["stopped"] = "days"
                break
            missing = self.missing_values(trade_date)
            if missing is None:
                continue
            if missing["no_volume"] == 0 and missing["no_value"] == 0:
                state["days"][trade_date] = {"status": "complete", "digest": missing["digest"], **missing}
                continue
            files = state["files_by_date"].get(trade_date, [])
            if not files:
                state["days"][trade_date] = {"status": "unretained", "digest": missing["digest"], **missing}
                continue
            result = self._complete_day(trade_date, [self.root / f for f in sorted(files)], reason_check=out_of_budget)
            if result is None:  # budget hit inside the day: nothing was written, retry next call
                report["stopped"] = out_of_budget() or "seconds"
                break
            report["filled_rows"] += result["filled_rows"]
            report["conflicts"] += result["conflicts"]
            for key, n in result["rejected"].items():
                report["rejected"][key] = report["rejected"].get(key, 0) + n
            after = self.missing_values(trade_date) or missing
            status = ("complete" if after["no_volume"] == 0 and after["no_value"] == 0
                      else "source_has_no_values" if result["filled_rows"] == 0 else "partial")
            state["days"][trade_date] = {
                "status": status, "digest": after["digest"], "filled_rows": result["filled_rows"],
                "conflicts": result["conflicts"], "rejected": result["rejected"], "traded_rows": after["traded_rows"],
                "no_volume": after["no_volume"], "no_value": after["no_value"]}
            (report["completed_days"] if result["written"] else report["unchanged_days"]).append(trade_date)
            self._save(state)  # resumable after every day
        self._save(state)
        days = state["days"]
        unretained = sorted(d for d, v in days.items() if v.get("status") == "unretained")
        report["unretained_count"] = len(unretained)
        report["unretained_days"] = unretained[:UNRETAINED_LIST_LIMIT]
        report["pending_days"] = sum(1 for d in stored if d not in days or days[d].get("status") == "partial")
        report["elapsed_seconds"] = round(self._clock() - started, 3)
        report["peak_rss_mb"] = max(report["peak_rss_mb"], round(self._rss(), 1))
        return report

    # -- parts ---------------------------------------------------------------------------------
    def _index_partition(self, state: dict[str, Any], partition: Path) -> None:
        by_date: dict[str, set[str]] = {}
        for path in partition_files(partition):
            rel = path.relative_to(self.root).as_posix()
            seen: set[str] = set()
            try:
                for row in _iter_lines(path):
                    day = row_trade_date(row)
                    if day:
                        seen.add(day)
            except (OSError, EOFError):
                continue  # an unreadable run file indexes nothing: its days read as unretained, not guessed
            for day in seen:
                by_date.setdefault(day, set()).add(rel)
        for day, rels in by_date.items():
            held = state["files_by_date"].setdefault(day, [])
            held.extend(sorted(rels - set(held)))
        state["indexed"][partition.name] = {"dates": len(by_date)}

    def _complete_day(self, trade_date: str, files: list[Path], *, reason_check) -> dict[str, Any] | None:
        records: list[_Rec] = []
        for path in files:  # oldest first inside the partition list order of the index
            if reason_check():
                return None
            try:
                for row in _iter_lines(path):
                    if row_trade_date(row) != trade_date:
                        continue
                    records.append(_Rec(metadata=effective_metadata(row), source_id=str(row.get("source_id") or "")))
            except (OSError, EOFError):
                continue
        rows, rejected, _ = self.store.extract_rows(records, ingest_run_id=f"completion-{trade_date}", default_market="TW")
        del records
        out = self.store.complete_day_values(trade_date, rows)
        refused = rejected.get("conflicting_duplicate", 0)
        return {"filled_rows": out["filled_rows"], "conflicts": out["conflicts"] + refused, "written": out["written"],
                "rejected": rejected}
