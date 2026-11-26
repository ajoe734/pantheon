"""Complete stored daily bars (``volume``, ``traded_value``) from the retained normalized run files.

DAILY-BARS-COLLECTOR-VOLUME-VALUE-20261125. Until this task the store read ``volume`` and ``value`` while the collector
rows carry ``volume_shares`` and ``turnover_twd``, so almost every stored row has no volume and no traded value. The
mapping is fixed in ``DailyBarsStore.extract_rows``; this module repairs the days that were already stored, with no
manual hosted write: the market data writer starts it after a daily bars batch (single flight, daemon thread, at most once per 30 s),
and every call is bounded and resumable.

How a tick works
* Index (newest write-date partition first): the run files of ``normalized/tw_price_daily/date=<write date>/`` are read
  line by line, one file at a time, and the trade dates each file holds are recorded in the state file. A run file
  cannot hold a trade date later than its write date, so a stored day D is eligible once every partition with write
  date >= D is indexed.
* Complete (newest stored day first): an eligible day whose traded rows still lack volume or traded value is read from
  its indexed run files, one file at a time, turned into bars by ``extract_rows`` (the unit check refuses a row whose
  close x volume is far from its traded value) and merged into a per-day stage (copies of one key merged by
  ``DailyBarsStore._merge_copies``; copies that disagree on prices are refused as ``conflicting_duplicate``, exactly as
  one ``extract_rows`` pass over all the day's files would). When every file of the day is staged the stage is applied
  with ``DailyBarsStore.complete_day_values``: only null values are filled, a non-null value is never overwritten, a
  source that disagrees is refused and counted. A filled day gets a new manifest generation and digest.
* A day with no retained source row is listed (``unretained``), never guessed.
* Bounds per call: ``max_days`` completed days, ``max_seconds`` wall time, ``max_rss_mb`` resident memory, and
  ``max_partitions`` indexed partitions. Hitting a bound stops the call; the state file resumes the next one.

DAILY-BARS-COMPLETION-RESUMABLE-INDEX-20261126: a hosted partition is 120 to 350 MB (about 200 files) and one trade
date's files are as large, which one 20 s call cannot read; the old call read a whole partition or a whole day or
nothing, so it ended with zero progress and every later call repeated it. Now the unit of progress is one run file
(hosted files are at most 4 MB): the index records the files of a partition it has read (``indexing``), the day
completion records the files it has staged (``daily_bars_completion_stage/<trade date>.json``), and the time budget is
only checked once the call has made progress, so a call stopped on seconds has always recorded at least one file.
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
STATE_SCHEMA = "daily_bars_completion_state.v1"  # ``indexing`` (partial partitions) is optional: v1 states load
STAGE_DIRNAME = "daily_bars_completion_stage"
STAGE_SCHEMA = "daily_bars_completion_stage.v1"
_PRICE_FIELDS = ("open", "high", "low", "close", "volume", "traded_value")
MAX_DAYS = int(os.getenv("SOURCE_INGEST_DAILY_BARS_COMPLETION_MAX_DAYS", "3"))
MAX_SECONDS = float(os.getenv("SOURCE_INGEST_DAILY_BARS_COMPLETION_MAX_SECONDS", "20"))
# Memory: the call may GROW the process by MAX_GROWTH_MB over its RSS at the start (the source-ingest process is already
# ~2.7 GB on hosted, so a flat 768 MB cap would stop every call before the first partition). MAX_RSS_MB is the absolute
# guard against the 6 GiB (6144 MB) source-ingest container limit: 5120 MB leaves 1 GiB of headroom for the rest of the
# process. 2727 MB (hosted today) + 768 MB growth = 3495 MB, inside the ceiling.
MAX_GROWTH_MB = float(os.getenv("SOURCE_INGEST_DAILY_BARS_COMPLETION_MAX_GROWTH_MB", "768"))
MAX_RSS_MB = float(os.getenv("SOURCE_INGEST_DAILY_BARS_COMPLETION_MAX_RSS_MB", "5120"))
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
        self.stage_dir = self.state_path.parent / STAGE_DIRNAME
        self._clock = clock
        self._rss = rss

    # -- state --------------------------------------------------------------------------------
    def load_state(self) -> dict[str, Any]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state.get("schema_version") == STATE_SCHEMA:
                for key in ("indexed", "files_by_date", "days", "indexing"):
                    state.setdefault(key, {})
                return state
        except (OSError, ValueError):
            pass
        return {"schema_version": STATE_SCHEMA, "indexed": {}, "indexing": {}, "files_by_date": {}, "days": {}}

    def _save(self, state: dict[str, Any]) -> None:
        _atomic_write_bytes(self.state_path, json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    # -- per-day stage (files of one trade date read so far) ----------------------------------
    def _stage_path(self, trade_date: str) -> Path:
        return self.stage_dir / f"{trade_date}.json"

    def load_stage(self, trade_date: str) -> dict[str, Any]:
        try:
            stage = json.loads(self._stage_path(trade_date).read_text(encoding="utf-8"))
            if stage.get("schema_version") == STAGE_SCHEMA and stage.get("trade_date") == trade_date:
                return stage
        except (OSError, ValueError):
            pass
        return {"schema_version": STAGE_SCHEMA, "trade_date": trade_date, "done": [], "rows": {}, "conflicted": [],
                "rejected": {}}

    def _save_stage(self, stage: dict[str, Any]) -> None:
        self.stage_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(self._stage_path(stage["trade_date"]),
                            json.dumps(stage, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    def _drop_stage(self, trade_date: str) -> None:
        try:
            self._stage_path(trade_date).unlink()
        except FileNotFoundError:
            pass

    def _drop_stale_stages(self, stored: set[str]) -> None:
        """A stage of a day that is no longer stored (retention) is never applied: remove it."""
        if not self.stage_dir.is_dir():
            return
        for path in self.stage_dir.glob("*.json"):
            if path.stem not in stored:
                path.unlink(missing_ok=True)

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

    # -- one bounded call ----------------------------------------------------------------------
    def run(self, *, max_days: int = MAX_DAYS, max_seconds: float = MAX_SECONDS, max_rss_mb: float = MAX_RSS_MB,
            max_growth_mb: float = MAX_GROWTH_MB, max_partitions: int = MAX_PARTITIONS) -> dict[str, Any]:
        started = self._clock()
        baseline_rss = self._rss()
        state = self.load_state()
        report: dict[str, Any] = {
            "baseline_rss_mb": round(baseline_rss, 1), "max_growth_mb": max_growth_mb, "max_rss_mb": max_rss_mb,
            "completed_days": [], "unchanged_days": [], "indexed_partitions": 0, "indexed_files": 0,
            "staged_files": 0, "examined_days": 0, "progress": 0, "filled_rows": 0,
            "conflicts": 0, "rejected": {}, "stopped": None, "peak_rss_mb": 0.0}

        def out_of_budget() -> str | None:
            report["peak_rss_mb"] = max(report["peak_rss_mb"], round(self._rss(), 1))
            rss = self._rss()
            if rss >= max_rss_mb:
                return "memory_ceiling"  # absolute guard against the container limit
            if rss - baseline_rss >= max_growth_mb:
                return "memory_growth"  # this call grew the process by its declared increment
            # The time budget applies once this call has recorded progress (one run file is at most a few MB, so the
            # overrun is one file): a call stopped on seconds never ends with nothing recorded.
            if report["progress"] and self._clock() - started >= max_seconds:
                return "seconds"
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
                reason = self._index_partition(state, part, report, out_of_budget)
                if reason:
                    report["stopped"] = reason
                    break
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
                self._drop_stage(trade_date)
                report["examined_days"] += 1
                report["progress"] += 1
                continue
            files = state["files_by_date"].get(trade_date, [])
            if not files:
                state["days"][trade_date] = {"status": "unretained", "digest": missing["digest"], **missing}
                report["examined_days"] += 1
                report["progress"] += 1
                continue
            result = self._complete_day(trade_date, sorted(files), report, reason_check=out_of_budget)
            if isinstance(result, str):  # budget hit inside the day: the files read so far are staged, not written
                report["stopped"] = result
                break
            report["filled_rows"] += result["filled_rows"]
            report["conflicts"] += result["conflicts"]
            for key, n in result["rejected"].items():
                report["rejected"][key] = report["rejected"].get(key, 0) + n
            after = self.missing_values(trade_date) or missing
            # Every retained file of the day was staged, so a row still null has no usable source value (unit
            # mismatch, bad close, conflict, absent): re-reading the same files cannot fill it. The old "partial"
            # status re-read a whole day (up to 350 MB on hosted) on the next call for nothing.
            status = "complete" if after["no_volume"] == 0 and after["no_value"] == 0 else "source_has_no_values"
            state["days"][trade_date] = {
                "status": status, "digest": after["digest"], "filled_rows": result["filled_rows"],
                "conflicts": result["conflicts"], "rejected": result["rejected"], "traded_rows": after["traded_rows"],
                "no_volume": after["no_volume"], "no_value": after["no_value"]}
            (report["completed_days"] if result["written"] else report["unchanged_days"]).append(trade_date)
            report["progress"] += 1
            self._save(state)  # resumable after every day
        self._save(state)
        self._drop_stale_stages(set(stored))
        days = state["days"]
        unretained = sorted(d for d, v in days.items() if v.get("status") == "unretained")
        report["unretained_count"] = len(unretained)
        report["unretained_days"] = unretained[:UNRETAINED_LIST_LIMIT]
        report["pending_days"] = sum(1 for d in stored if d not in days or days[d].get("status") == "partial")
        report["indexed_total"] = len(state["indexed"])
        report["partitions_total"] = len(root_parts)
        report["elapsed_seconds"] = round(self._clock() - started, 3)
        report["peak_rss_mb"] = max(report["peak_rss_mb"], round(self._rss(), 1))
        # A call that stopped for memory before doing anything can never make progress: a visible failure, not idle.
        report["failed"] = bool(str(report["stopped"] or "").startswith("memory") and not report["progress"])
        state = self.load_state()
        state["last_run"] = {k: report[k] for k in (
            "stopped", "failed", "baseline_rss_mb", "peak_rss_mb", "max_growth_mb", "max_rss_mb", "elapsed_seconds",
            "indexed_partitions", "indexed_files", "staged_files", "progress", "filled_rows", "conflicts",
            "indexed_total", "partitions_total")}
        state["last_run"]["completed_days"] = len(report["completed_days"])
        state["last_run"]["finished_at"] = time.time()
        self._save(state)
        return report

    # -- parts ---------------------------------------------------------------------------------
    def _index_partition(self, state: dict[str, Any], partition: Path, report: dict[str, Any], reason_check) -> str | None:
        """Index the run files of one partition not read yet, one file at a time; the files read are recorded in
        ``state["indexing"]`` (and their trade dates in ``files_by_date``) and saved when the call stops inside the
        partition, so the next call continues with the next file. Returns the stop reason, None when the partition
        is fully indexed."""
        partial = state["indexing"].setdefault(partition.name, {"files": [], "dates": []})
        done = set(partial["files"])
        dates = set(partial["dates"])
        for path in partition_files(partition):
            if path.name in done:
                continue
            reason = reason_check()
            if reason:
                partial["files"], partial["dates"] = sorted(done), sorted(dates)
                self._save(state)
                return reason
            rel = path.relative_to(self.root).as_posix()
            seen: set[str] = set()
            try:
                for row in _iter_lines(path):
                    day = row_trade_date(row)
                    if day:
                        seen.add(day)
            except (OSError, EOFError):
                seen = set()  # an unreadable run file indexes nothing: its days read as unretained, not guessed
            for day in seen:
                held = state["files_by_date"].setdefault(day, [])
                if rel not in held:
                    held.append(rel)
            dates |= seen
            done.add(path.name)
            report["indexed_files"] += 1
            report["progress"] += 1
        state["indexing"].pop(partition.name, None)
        state["indexed"][partition.name] = {"dates": len(dates), "files": len(done)}
        report["indexed_partitions"] += 1
        self._save(state)
        return None

    def _stage_file(self, stage: dict[str, Any], trade_date: str, path: Path) -> None:
        """Merge the bars of one run file into the day stage with the same rule as one ``extract_rows`` pass."""
        records: list[_Rec] = []
        try:
            for row in _iter_lines(path):
                if row_trade_date(row) != trade_date:
                    continue
                records.append(_Rec(metadata=effective_metadata(row), source_id=str(row.get("source_id") or "")))
        except (OSError, EOFError):
            records = []  # unreadable: contributes nothing (same as before: the file was skipped)
        ingest_run_id = f"completion-{trade_date}"
        rows, rejected, _ = self.store.extract_rows(records, ingest_run_id=ingest_run_id, default_market="TW")
        conflicted = set(stage["conflicted"])
        file_conflicts: set[str] = set()
        if rejected.get("conflicting_duplicate"):
            # Rare: which keys disagreed inside this file. Re-extract one record at a time and replay the merge.
            held: dict[str, dict[str, Any]] = {}
            for rec in records:
                one, _, _ = self.store.extract_rows([rec], ingest_run_id=ingest_run_id, default_market="TW")
                for row in one:
                    key = _key(row)
                    if key in file_conflicts:
                        continue
                    if key in held and DailyBarsStore._merge_copies(held[key], row) is None:
                        file_conflicts.add(key)
                    else:
                        held[key] = row
            rejected = {k: v for k, v in rejected.items() if k != "conflicting_duplicate"}
        del records
        new_conflicts = file_conflicts - conflicted
        merged = stage["rows"]
        for row in rows:
            key = _key(row)
            if key in conflicted or key in file_conflicts:
                continue
            compact = {f: row.get(f) for f in _PRICE_FIELDS}
            if key in merged:
                keep = DailyBarsStore._merge_copies(dict(zip(_PRICE_FIELDS, merged[key])), compact)
                if keep is None:  # copies in two files disagree on open, high, low or close: no choice is made
                    del merged[key]
                    new_conflicts.add(key)
                    continue
                compact = keep
            merged[key] = [compact.get(f) for f in _PRICE_FIELDS]
        for key in new_conflicts:
            merged.pop(key, None)
        if new_conflicts:
            rejected["conflicting_duplicate"] = len(new_conflicts)
        stage["conflicted"] = sorted(conflicted | new_conflicts)
        for key, n in rejected.items():
            stage["rejected"][key] = stage["rejected"].get(key, 0) + n

    def _complete_day(self, trade_date: str, files: list[str], report: dict[str, Any], *,
                      reason_check) -> dict[str, Any] | str:
        """Stage the day's run files not staged yet, one at a time, then apply the stage. Returns the stop reason
        (the stage is saved, nothing is written to the store) when the budget is hit before the last file."""
        stage = self.load_stage(trade_date)
        done = set(stage["done"])
        for rel in files:  # oldest first inside the partition list order of the index
            if rel in done:
                continue
            reason = reason_check()
            if reason:
                stage["done"] = sorted(done)
                self._save_stage(stage)
                return reason
            self._stage_file(stage, trade_date, self.root / rel)
            done.add(rel)
            report["staged_files"] += 1
            report["progress"] += 1
        rows = []
        for key, vals in stage["rows"].items():
            market, symbol, day = key.split("|", 2)
            rows.append({"market": market, "symbol": symbol, "trade_date": day, **dict(zip(_PRICE_FIELDS, vals))})
        out = self.store.complete_day_values(trade_date, rows)
        self._drop_stage(trade_date)
        rejected = dict(stage["rejected"])
        refused = rejected.get("conflicting_duplicate", 0)
        return {"filled_rows": out["filled_rows"], "conflicts": out["conflicts"] + refused, "written": out["written"],
                "rejected": rejected}


def _key(row: Mapping[str, Any]) -> str:
    return f"{row['market']}|{row['symbol']}|{row['trade_date']}"
