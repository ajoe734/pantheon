"""Daily bars store of record: one row per (market, symbol, trade_date), written once.

Design of record: SD_MARKET_DATA_ONE_WRITE_PER_DAY_2026-11-16 section 4.1.

Layout under ``<root>``::

    daily_bars/market=TW/date=YYYY-MM-DD/part-g<gen>-<n>.parquet
    daily_bars/_manifest/date=YYYY-MM-DD.json   # file list, row/symbol count, content digest
    lineage/date=YYYY-MM-DD.json                # calendar evidence once per day, run refs once per run

Write protocol for one trade date (the only mutation):

1. merge the incoming rows into the day's current rows by key; if the content digest is
   unchanged nothing is written;
2. write the new generation's part files to temp names, fsync, rename (not yet referenced);
3. write the lineage file atomically;
4. write the manifest temp file, fsync, and rename it over the old manifest LAST.

A reader resolves files only through the manifest, so it sees the old or the new complete
set. Superseded generation files are deleted after the manifest rename.

``DatasetKeyDeclaration`` is the per-dataset key declaration: other datasets only add a
declaration (SOURCE-DATASETS-KEYED-WRITE-20261119), the writer is not rewritten.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

DAILY_BARS_SCHEMA_VERSION = "daily_bars_manifest.v1"
PART_ROWS = 10_000
ROW_GROUP_ROWS = 1_000
SLICE_ROWS = 2_000  # retention sweep: rows converted per slice before the interpreter lock is released for a moment

# Budget row of design section 6 (bytes). *_TARGET is the design bound, *_ALARM the alarm line.
DAILY_BARS_DAY_TARGET_BYTES = 1_600_000
DAILY_BARS_DAY_ALARM_BYTES = 2_500_000
DAILY_BARS_TOTAL_CEILING_BYTES = 1_000_000_000
LINEAGE_DAY_TARGET_BYTES = 300_000
LINEAGE_DAY_ALARM_BYTES = 450_000  # 1.5x
LINEAGE_TOTAL_CEILING_BYTES = 150_000_000

_RAW_ROW_FIELDS = (
    "market", "symbol", "trade_date", "open", "high", "low", "close", "volume",
    "adjusted", "source_id", "ingest_run_id", "lineage_ref", "content_hash",
)


class DailyBarsError(RuntimeError):
    """Base class; the message is always a readable reason."""


class DailyBarsCorrupt(DailyBarsError):
    """A manifest names a file that is missing or has a different size."""


class DailyBarsBudgetExceeded(DailyBarsError):
    """A byte limit of design section 6 is exceeded."""


@dataclass(frozen=True)
class DatasetKeyDeclaration:
    """Unique key and partition field of one dataset."""

    dataset: str
    key_fields: tuple[str, ...]
    partition_field: str
    value_fields: tuple[str, ...] = ()
    # SOURCE-DATASETS-KEYED-WRITE-20261119 (design 6A). Optional: a dataset without them behaves as before.
    key_aliases: tuple[tuple[str, ...], ...] = ()  # per key field, the record fields that may carry it
    optional_key_fields: tuple[str, ...] = ()  # key fields a record may lack (then not part of its key)
    retention_days: int | None = None  # None = no expiry; read by SOURCE-RETENTION-SWEEPER
    day_budget_bytes: int | None = None  # section 6A growth budget per trading day (stored bytes)
    readers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.partition_field not in self.key_fields:
            raise ValueError("partition_field must be part of key_fields")
        if self.key_aliases and len(self.key_aliases) != len(self.key_fields):
            raise ValueError("key_aliases must give one tuple per key field")


DAILY_BARS = DatasetKeyDeclaration(
    dataset="daily_bars",
    key_fields=("market", "symbol", "trade_date"),
    partition_field="trade_date",
    value_fields=("open", "high", "low", "close", "volume", "adjusted"),
)

_DATE_KEYS = ("trade_date", "as_of_date", "date", "event_time")  # never available_time / created_at / ingest day


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}-{threading.get_ident()}")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _require_pyarrow():
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet  # noqa: F401
    except ImportError as exc:  # pragma: no cover - pinned in requirements.txt
        raise DailyBarsError("pyarrow is required for the daily bars store (pyarrow>=15,<30)") from exc
    import pyarrow as pa
    import pyarrow.parquet as pq

    return pa, pq


def _schema(pa):
    return pa.schema([
        ("market", pa.string()), ("symbol", pa.string()), ("trade_date", pa.string()),
        ("open", pa.float64()), ("high", pa.float64()), ("low", pa.float64()), ("close", pa.float64()),
        ("volume", pa.float64()), ("adjusted", pa.bool_()), ("source_id", pa.string()),
        ("ingest_run_id", pa.string()), ("lineage_ref", pa.string()), ("content_hash", pa.string()),
    ])


def validate_trade_date(value: Any) -> str | None:
    text = str(value or "").strip()[:10]
    try:
        return _date.fromisoformat(text).isoformat() if len(text) == 10 else None
    except ValueError:
        return None


class DailyBarsStore:
    """Writer and manifest owner of the daily bars store."""

    def __init__(
        self,
        root: str | Path,
        *,
        declaration: DatasetKeyDeclaration = DAILY_BARS,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        self.root = Path(root)
        self.declaration = declaration
        self.bars_dir = self.root / declaration.dataset
        self.manifest_dir = self.bars_dir / "_manifest"
        self.lineage_dir = self.root / "lineage"
        self._fault = fault  # test hook: called with the step name, may raise to kill the writer
        self._lock = threading.Lock()
        self.counters: dict[str, int] = {
            "rows_written": 0, "days_written": 0, "days_unchanged": 0,
            "rejected_no_trade_date": 0, "rejected_other": 0, "skipped_not_listed": 0, "dual_write_errors": 0,
        }
        self.last_error: str | None = None
        self.last_rejections: dict[str, int] = {}

    # -- record extraction -------------------------------------------------------------------
    def extract_rows(
        self,
        records: Iterable[Any],
        *,
        ingest_run_id: str,
        default_market: str | None = None,
        listing: Callable[[str], bool] | Sequence[str] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, dict[str, Any]]]:
        """Return (rows, rejection counts, calendar evidence by digest). A record without a
        trade date is rejected and counted, never filed under the ingest day."""
        from services.registry.strategy_artifact import canonical_market_context

        allowed = None if listing is None or callable(listing) else {str(s) for s in listing}
        rows: dict[tuple[str, str, str], dict[str, Any]] = {}
        rejected: dict[str, int] = {}
        evidence: dict[str, dict[str, Any]] = {}

        def reject(reason: str) -> None:
            rejected[reason] = rejected.get(reason, 0) + 1

        for record in records:
            if bool(getattr(record, "is_rejected", False)):
                continue
            metadata = getattr(record, "metadata", None)
            if not isinstance(metadata, Mapping):
                reject("no_metadata")
                continue
            normalized = metadata.get("normalized_row")
            row = normalized if isinstance(normalized, Mapping) else metadata
            trade_date = None
            for src in (row, metadata):
                for key in _DATE_KEYS:
                    if src.get(key) not in (None, ""):
                        trade_date = validate_trade_date(src[key])
                        break
                if trade_date:
                    break
            if not trade_date:
                reject("no_trade_date")
                continue
            symbol = row.get("symbol_canonical") or row.get("symbol") or metadata.get("symbol_canonical") or metadata.get("symbol")
            symbol = str(symbol or "").strip()
            if not symbol:
                reject("no_symbol")
                continue
            if allowed is not None and symbol not in allowed:
                reject("skipped_not_listed")
                continue
            if callable(listing) and not listing(symbol):
                reject("skipped_not_listed")
                continue
            close = _number(row.get("close") if row.get("close") is not None else metadata.get("close"))
            if close is None or close <= 0:
                reject("bad_close")
                continue
            candidates = {str(src["market"]).strip() for src in (row, metadata) if src.get("market")}
            if default_market:
                candidates.add(str(default_market).strip())
            try:
                markets = {canonical_market_context(m, symbols=[symbol]) for m in candidates}
            except Exception:
                reject("bad_market")
                continue
            if len(markets) != 1:
                reject("no_market" if not markets else "contradictory_market")
                continue
            market = next(iter(markets))
            ohlcv = {
                "open": _number(row.get("open")), "high": _number(row.get("high")), "low": _number(row.get("low")),
                "close": close, "volume": _number(row.get("volume")),
                "adjusted": bool(row.get("adjusted") or row.get("is_adjusted") or False),
            }
            cal = row.get("calendar_evidence") if row.get("calendar_evidence") is not None else metadata.get("calendar_evidence")
            if isinstance(cal, Mapping) and cal:
                evidence[_sha(cal)] = dict(cal)
            rows[(market, symbol, trade_date)] = {
                "market": market, "symbol": symbol, "trade_date": trade_date, **ohlcv,
                "source_id": str(getattr(record, "source_id", "") or ""),
                "ingest_run_id": ingest_run_id, "lineage_ref": ingest_run_id,
                "content_hash": _sha(ohlcv),
            }
        return list(rows.values()), rejected, evidence

    # -- write -------------------------------------------------------------------------------
    def write_records(
        self,
        records: Iterable[Any],
        *,
        ingest_run_id: str,
        default_market: str | None = None,
        connector_id: str | None = None,
        observed_at: str | None = None,
        listing: Callable[[str], bool] | Sequence[str] | None = None,
    ) -> dict[str, Any]:
        rows, rejected, evidence = self.extract_rows(
            records, ingest_run_id=ingest_run_id, default_market=default_market, listing=listing)
        self.last_rejections = dict(rejected)
        self.counters["rejected_no_trade_date"] += rejected.get("no_trade_date", 0)
        self.counters["skipped_not_listed"] += rejected.get("skipped_not_listed", 0)
        self.counters["rejected_other"] += sum(
            n for k, n in rejected.items() if k not in ("no_trade_date", "skipped_not_listed"))
        by_date: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_date.setdefault(row["trade_date"], []).append(row)
        written: list[str] = []
        unchanged: list[str] = []
        for trade_date in sorted(by_date):
            changed = self.write_day(
                trade_date, by_date[trade_date], ingest_run_id=ingest_run_id,
                calendar_evidence=evidence, connector_id=connector_id, observed_at=observed_at)
            (written if changed else unchanged).append(trade_date)
        return {
            "rows": len(rows), "days_written": written, "days_unchanged": unchanged, "rejected": rejected,
        }

    def _manifest_path(self, trade_date: str) -> Path:
        return self.manifest_dir / f"date={trade_date}.json"

    def read_manifest(self, trade_date: str) -> dict[str, Any] | None:
        path = self._manifest_path(trade_date)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except ValueError as exc:
            raise DailyBarsCorrupt(f"manifest for {trade_date} is not valid JSON") from exc

    def _read_day_rows(self, manifest: Mapping[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
        _, pq = _require_pyarrow()
        out: dict[tuple[str, str, str], dict[str, Any]] = {}
        for entry in manifest.get("files", ()):
            path = self.bars_dir / entry["path"]
            if not path.is_file() or path.stat().st_size != int(entry["bytes"]):
                raise DailyBarsCorrupt(f"daily bars file {entry['path']} is missing or has a different size")
            for row in pq.read_table(path).to_pylist():
                out[(row["market"], row["symbol"], row["trade_date"])] = row
        return out

    @staticmethod
    def day_digest(rows: Iterable[Mapping[str, Any]], *, yield_gil: bool = False) -> str:
        """Digest of a date's rows. ``yield_gil`` hashes the same bytes in slices with a pause between them, so a
        sweep of a 22,500 row date does not hold the interpreter lock for one long stretch (retention sweeper)."""
        keyed = sorted((r["market"], r["symbol"], r["trade_date"], r["content_hash"]) for r in rows)
        if not yield_gil:
            return _sha(keyed)
        digest = hashlib.sha256()
        digest.update(b"[")
        for start in range(0, len(keyed), SLICE_ROWS):
            if start:
                digest.update(b",")
            digest.update(",".join(_canonical(list(k)) for k in keyed[start:start + SLICE_ROWS]).encode("utf-8"))
            time.sleep(0.001)
        digest.update(b"]")
        return digest.hexdigest()

    def _read_day_rows_sliced(self, manifest: Mapping[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
        """``_read_day_rows`` in ``SLICE_ROWS`` slices with a pause between them (same result)."""
        _, pq = _require_pyarrow()
        out: dict[tuple[str, str, str], dict[str, Any]] = {}
        for entry in manifest.get("files", ()):
            path = self.bars_dir / entry["path"]
            if not path.is_file() or path.stat().st_size != int(entry["bytes"]):
                raise DailyBarsCorrupt(f"daily bars file {entry['path']} is missing or has a different size")
            for batch in pq.ParquetFile(path).iter_batches(batch_size=SLICE_ROWS):
                for row in batch.to_pylist():
                    out[(row["market"], row["symbol"], row["trade_date"])] = row
                time.sleep(0.001)
        return out

    def _hit(self, step: str) -> None:
        if self._fault is not None:
            self._fault(step)

    def _write_parts(self, pa, pq, trade_date: str, generation: int, ordered: Sequence[Mapping[str, Any]],
                     *, yield_gil: bool = False) -> tuple[list[dict[str, Any]], list[Path]]:
        """Write one generation of part files for a date (temp write, fsync, rename). Nothing is visible until
        the manifest is swapped."""
        files: list[dict[str, Any]] = []
        new_paths: list[Path] = []
        schema = _schema(pa)
        by_market: dict[str, list[Mapping[str, Any]]] = {}
        for row in ordered:
            by_market.setdefault(row["market"], []).append(row)
        for market, market_rows in sorted(by_market.items()):
            part_dir = self.bars_dir / f"market={market}" / f"date={trade_date}"
            part_dir.mkdir(parents=True, exist_ok=True)
            for n, start in enumerate(range(0, len(market_rows), PART_ROWS)):
                chunk = market_rows[start:start + PART_ROWS]
                final = part_dir / f"part-g{generation}-{n}.parquet"
                tmp = final.with_name(final.name + ".tmp")
                if yield_gil:  # same file layout (row groups of ROW_GROUP_ROWS), built in slices
                    with pq.ParquetWriter(tmp, schema, compression="zstd") as writer:
                        for lo in range(0, len(chunk), SLICE_ROWS):
                            writer.write_table(pa.Table.from_pylist(chunk[lo:lo + SLICE_ROWS], schema=schema),
                                               row_group_size=ROW_GROUP_ROWS)
                            time.sleep(0.001)
                else:
                    table = pa.Table.from_pylist(chunk, schema=schema)
                    pq.write_table(table, tmp, compression="zstd", row_group_size=ROW_GROUP_ROWS)
                self._hit("after_temp_write")
                with open(tmp, "rb") as handle:
                    os.fsync(handle.fileno())
                self._hit("after_fsync")
                os.replace(tmp, final)
                new_paths.append(final)
                files.append({
                    "path": final.relative_to(self.bars_dir).as_posix(), "market": market,
                    "rows": len(chunk), "bytes": final.stat().st_size,
                    "min_symbol": chunk[0]["symbol"], "max_symbol": chunk[-1]["symbol"],
                })
            _fsync_dir(part_dir)
        return files, new_paths

    def write_day(
        self,
        trade_date: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        ingest_run_id: str,
        calendar_evidence: Mapping[str, Mapping[str, Any]] | None = None,
        connector_id: str | None = None,
        observed_at: str | None = None,
    ) -> bool:
        """Merge rows into the day by key. Returns False (and writes nothing) when unchanged."""
        pa, pq = _require_pyarrow()
        if validate_trade_date(trade_date) != trade_date:
            raise DailyBarsError(f"trade_date {trade_date!r} is not an ISO date")
        for row in rows:
            if row.get("trade_date") != trade_date:
                raise DailyBarsError("row trade_date does not match the partition being written")
        self.bars_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        with self._lock, open(self.manifest_dir / ".lock", "a+") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            try:
                manifest = self.read_manifest(trade_date)
                current = self._read_day_rows(manifest) if manifest else {}
                merged = dict(current)
                for row in rows:
                    key = (row["market"], row["symbol"], row["trade_date"])
                    old = merged.get(key)
                    if old is None or old["content_hash"] != row["content_hash"]:
                        merged[key] = {k: row.get(k) for k in _RAW_ROW_FIELDS}
                digest = self.day_digest(merged.values())
                if manifest and manifest.get("content_digest") == digest:
                    self.counters["days_unchanged"] += 1
                    return False
                generation = int(manifest.get("generation", 0)) + 1 if manifest else 1
                ordered = sorted(merged.values(), key=lambda r: (r["market"], r["symbol"]))
                files, new_paths = self._write_parts(pa, pq, trade_date, generation, ordered)
                self._write_lineage(
                    trade_date, ingest_run_id, calendar_evidence or {}, connector_id, observed_at, merged.values())
                self._hit("after_lineage")
                new_manifest = {
                    "schema_version": DAILY_BARS_SCHEMA_VERSION, "dataset": self.declaration.dataset,
                    "key_fields": list(self.declaration.key_fields), "trade_date": trade_date,
                    "generation": generation, "files": files, "row_count": len(ordered),
                    "symbol_count": len({r["symbol"] for r in ordered}),
                    "bytes": sum(f["bytes"] for f in files), "content_digest": digest,
                }
                manifest_path = self._manifest_path(trade_date)
                tmp_manifest = manifest_path.with_name(manifest_path.name + ".tmp")
                with open(tmp_manifest, "wb") as handle:
                    handle.write(json.dumps(new_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                self._hit("before_manifest_rename")
                os.replace(tmp_manifest, manifest_path)  # LAST: this makes the new set visible
                _fsync_dir(self.manifest_dir)
                self._hit("after_manifest_rename")
                keep = {p.resolve() for p in new_paths}
                for stale in self.bars_dir.glob(f"market=*/date={trade_date}/*"):
                    if stale.resolve() not in keep:
                        stale.unlink(missing_ok=True)
                self.counters["rows_written"] += len(ordered)
                self.counters["days_written"] += 1
                return True
            finally:
                fcntl.flock(lock_handle, fcntl.LOCK_UN)

    def _write_lineage(
        self,
        trade_date: str,
        ingest_run_id: str | None,
        calendar_evidence: Mapping[str, Mapping[str, Any]],
        connector_id: str | None,
        observed_at: str | None,
        rows: Iterable[Mapping[str, Any]],
    ) -> None:
        path = self.lineage_dir / f"date={trade_date}.json"
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            doc = {"trade_date": trade_date, "calendar_evidence": {}, "runs": {}}
        doc["calendar_evidence"].update({k: dict(v) for k, v in calendar_evidence.items()})
        referenced = {r["lineage_ref"] for r in rows}
        doc["runs"] = {k: v for k, v in doc["runs"].items() if k in referenced}  # drop runs no row refers to
        if ingest_run_id is not None:  # None: a removal rewrite adds no run, it only drops runs no remaining row refers to
            doc["runs"].setdefault(ingest_run_id, {"connector_id": connector_id, "observed_at": observed_at})
        _atomic_write_bytes(path, json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    # -- retention support (SOURCE-RETENTION-SWEEPER-20261118-R2) -----------------------------
    def dates(self) -> list[str]:
        if not self.manifest_dir.is_dir():
            return []
        return sorted(p.name[5:-5] for p in self.manifest_dir.glob("date=*.json"))

    def lineage_dates(self) -> list[str]:
        if not self.lineage_dir.is_dir():
            return []
        return sorted(p.name[5:-5] for p in self.lineage_dir.glob("date=*.json"))

    def _lineage_size(self, trade_date: str) -> int:
        try:
            return (self.lineage_dir / f"date={trade_date}.json").stat().st_size
        except OSError:
            return 0

    def remove_rows(
        self,
        trade_date: str,
        should_remove: Callable[[Mapping[str, Any]], bool],
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Remove the rows ``should_remove(row)`` selects from one date.

        ``row`` has ``market``, ``symbol``, ``trade_date``, ``lineage_ref``. The rewrite is the normal write protocol:
        new part files (temp write, fsync, rename), the lineage rewritten to the runs the kept rows refer to, then
        the manifest swap LAST. A kill before the swap leaves the previous complete date; a reader sees the old
        manifest or the new one, never a mixture. A date left with no row is deleted (``delete_day``).
        ``dry_run`` measures and writes nothing.
        """
        _, pq = _require_pyarrow()
        empty = {"trade_date": trade_date, "rows": 0, "removed": 0, "remaining": 0, "bytes_before": 0, "bytes_freed": 0,
                 "lineage_bytes_freed": 0, "deleted_day": False}
        with self._lock, open(self._lock_path(), "a+") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            try:
                manifest = self.read_manifest(trade_date)
                if manifest is None:
                    return empty
                bytes_before = int(manifest.get("bytes", 0))
                # Cheap pass: the three small columns only. A date without a hit is not rewritten.
                hit = 0
                total = 0
                for entry in manifest.get("files", ()):
                    path = self.bars_dir / entry["path"]
                    if not path.is_file() or path.stat().st_size != int(entry["bytes"]):
                        raise DailyBarsCorrupt(f"daily bars file {entry['path']} is missing or has a different size")
                    for batch in pq.ParquetFile(path).iter_batches(
                            batch_size=SLICE_ROWS, columns=["market", "symbol", "trade_date", "lineage_ref"]):
                        cols = batch.to_pylist()
                        total += len(cols)
                        hit += sum(1 for r in cols if should_remove(r))
                        time.sleep(0.001)
                result = {**empty, "rows": total, "removed": hit, "remaining": total - hit, "bytes_before": bytes_before}
                if hit == 0:
                    return result
                lineage_before = self._lineage_size(trade_date)
                if dry_run:
                    share = hit / total if total else 0
                    result["bytes_freed"] = int(bytes_before * share)
                    result["deleted_day"] = hit == total
                    result["lineage_bytes_freed"] = lineage_before if hit == total else 0
                    return result
                rows = self._read_day_rows_sliced(manifest)
                kept = {k: v for k, v in rows.items() if not should_remove(v)}
                if not kept:
                    self._delete_day_locked(trade_date, manifest)
                    result.update(removed=total, remaining=0, bytes_freed=bytes_before, deleted_day=True,
                                  lineage_bytes_freed=lineage_before)
                    return result
                generation = int(manifest.get("generation", 0)) + 1
                ordered = sorted(kept.values(), key=lambda r: (r["market"], r["symbol"]))
                files, new_paths = self._write_parts(*_require_pyarrow(), trade_date, generation, ordered, yield_gil=True)
                self._write_lineage(trade_date, None, {}, None, None, ordered)
                self._hit("after_lineage")
                new_manifest = {
                    "schema_version": DAILY_BARS_SCHEMA_VERSION, "dataset": self.declaration.dataset,
                    "key_fields": list(self.declaration.key_fields), "trade_date": trade_date,
                    "generation": generation, "files": files, "row_count": len(ordered),
                    "symbol_count": len({r["symbol"] for r in ordered}),
                    "bytes": sum(f["bytes"] for f in files), "content_digest": self.day_digest(ordered, yield_gil=True),
                }
                manifest_path = self._manifest_path(trade_date)
                tmp_manifest = manifest_path.with_name(manifest_path.name + ".tmp")
                with open(tmp_manifest, "wb") as handle:
                    handle.write(json.dumps(new_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                self._hit("before_manifest_rename")
                os.replace(tmp_manifest, manifest_path)  # LAST: this makes the smaller set visible
                _fsync_dir(self.manifest_dir)
                self._hit("after_manifest_rename")
                keep = {p.resolve() for p in new_paths}
                for stale in self.bars_dir.glob(f"market=*/date={trade_date}/*"):
                    if stale.resolve() not in keep:
                        stale.unlink(missing_ok=True)
                result.update(removed=hit, remaining=len(ordered), bytes_freed=bytes_before - new_manifest["bytes"],
                              lineage_bytes_freed=max(0, lineage_before - self._lineage_size(trade_date)))
                self.counters["days_written"] += 1
                return result
            finally:
                fcntl.flock(lock_handle, fcntl.LOCK_UN)

    def _lock_path(self) -> Path:
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        return self.manifest_dir / ".lock"

    def _delete_day_locked(self, trade_date: str, manifest: Mapping[str, Any] | None) -> None:
        """Manifest first (the date stops existing for readers in one step), then parts, then lineage.
        A kill after the first step leaves orphan files that ``delete_orphans`` / ``sweep_interrupted`` remove."""
        manifest_path = self._manifest_path(trade_date)
        manifest_path.unlink(missing_ok=True)
        _fsync_dir(self.manifest_dir)
        self._hit("after_manifest_unlink")
        for entry in (manifest or {}).get("files", ()):
            (self.bars_dir / entry["path"]).unlink(missing_ok=True)
        for stale in self.bars_dir.glob(f"market=*/date={trade_date}/*"):
            stale.unlink(missing_ok=True)
        for part_dir in self.bars_dir.glob(f"market=*/date={trade_date}"):
            try:
                part_dir.rmdir()
            except OSError:
                pass
        (self.lineage_dir / f"date={trade_date}.json").unlink(missing_ok=True)

    def delete_day(self, trade_date: str) -> dict[str, Any]:
        with self._lock, open(self._lock_path(), "a+") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            try:
                manifest = self.read_manifest(trade_date)
                freed = int((manifest or {}).get("bytes", 0))
                lineage = self._lineage_size(trade_date)
                self._delete_day_locked(trade_date, manifest)
                return {"trade_date": trade_date, "bytes_freed": freed, "lineage_bytes_freed": lineage}
            finally:
                fcntl.flock(lock_handle, fcntl.LOCK_UN)

    def delete_orphan_lineage(self) -> int:
        """Lineage of a date that has no manifest (a delete killed after its manifest step). Returns bytes removed."""
        freed = 0
        with self._lock, open(self._lock_path(), "a+") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            try:
                have = set(self.dates())
                for day in self.lineage_dates():
                    if day not in have:
                        freed += self._lineage_size(day)
                        (self.lineage_dir / f"date={day}.json").unlink(missing_ok=True)
            finally:
                fcntl.flock(lock_handle, fcntl.LOCK_UN)
        return freed

    # -- backfill support ----------------------------------------------------------------------
    def sweep_interrupted(self) -> dict[str, int]:
        """Remove what a killed writer left behind: temp files and part files no manifest references.

        A writer only ever has one trade date in flight, so the residue of an interrupted run
        belongs to one date. Published dates (manifest present) are never touched except for
        their unreferenced generations."""
        removed = {"temp_files": 0, "orphan_parts": 0}
        with self._lock:
            for base in (self.bars_dir, self.lineage_dir):
                if base.is_dir():
                    for path in list(base.rglob("*")):
                        if path.is_file() and (path.name.endswith(".tmp") or ".tmp-" in path.name):
                            path.unlink(missing_ok=True)
                            removed["temp_files"] += 1
            if self.bars_dir.is_dir():
                for date_dir in list(self.bars_dir.glob("market=*/date=*")):
                    trade_date = date_dir.name[5:]
                    manifest = self.read_manifest(trade_date)
                    keep = {self.bars_dir / e["path"] for e in (manifest or {}).get("files", ())}
                    for part in list(date_dir.iterdir()):
                        if part not in keep:
                            part.unlink(missing_ok=True)
                            removed["orphan_parts"] += 1
                    if not any(date_dir.iterdir()):
                        date_dir.rmdir()
        return removed

    def verify_day(self, trade_date: str) -> dict[str, Any]:
        """Check one published day against its manifest (files, sizes, row and symbol counts)."""
        manifest = self.read_manifest(trade_date)
        if manifest is None:
            return {"trade_date": trade_date, "verified": False, "reason": "no_manifest"}
        try:
            rows = self._read_day_rows(manifest)
        except DailyBarsCorrupt as exc:
            return {"trade_date": trade_date, "verified": False, "reason": str(exc)}
        digest = self.day_digest(rows.values())
        ok = (
            len(rows) == manifest.get("row_count")
            and len({r["symbol"] for r in rows.values()}) == manifest.get("symbol_count")
            and digest == manifest.get("content_digest")
        )
        return {
            "trade_date": trade_date, "verified": ok, "reason": None if ok else "manifest_mismatch",
            "row_count": len(rows), "symbol_count": manifest.get("symbol_count"),
            "content_digest": manifest.get("content_digest"),
        }

    def symbol_stats(self, start: str | None = None, end: str | None = None) -> dict[str, tuple[int, str, str]]:
        """``{symbol: (days, first_date, last_date)}`` over stored days; reads the symbol column only."""
        _, pq = _require_pyarrow()
        stats: dict[str, tuple[int, str, str]] = {}
        if not self.manifest_dir.is_dir():
            return stats
        for path in sorted(self.manifest_dir.glob("date=*.json")):
            trade_date = path.name[5:-5]
            if (start and trade_date < start) or (end and trade_date > end):
                continue
            manifest = self.read_manifest(trade_date) or {}
            seen: set[str] = set()
            for entry in manifest.get("files", ()):
                seen.update(pq.read_table(self.bars_dir / entry["path"], columns=["symbol"])["symbol"].to_pylist())
            for symbol in seen:
                days, first, last = stats.get(symbol, (0, trade_date, trade_date))
                stats[symbol] = (days + 1, min(first, trade_date), max(last, trade_date))
        return stats

    # -- budget and health -------------------------------------------------------------------
    def budget_report(self) -> dict[str, Any]:
        """Measured bytes per day and total, with the alarm fields of design section 6."""
        bars_total = bars_max_day = lineage_total = lineage_max_day = 0
        max_day = None
        days = 0
        if self.manifest_dir.is_dir():
            for path in sorted(self.manifest_dir.glob("date=*.json")):
                try:
                    manifest = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                days += 1
                size = int(manifest.get("bytes", 0))
                bars_total += size
                if size > bars_max_day:
                    bars_max_day, max_day = size, manifest.get("trade_date")
        if self.lineage_dir.is_dir():
            for path in self.lineage_dir.glob("date=*.json"):
                size = path.stat().st_size
                lineage_total += size
                lineage_max_day = max(lineage_max_day, size)
        alarms = []
        if bars_max_day > DAILY_BARS_DAY_ALARM_BYTES:
            alarms.append("daily_bars_day_over_2_5mb")
        if bars_total > DAILY_BARS_TOTAL_CEILING_BYTES:
            alarms.append("daily_bars_total_over_1gb")
        if lineage_max_day > LINEAGE_DAY_ALARM_BYTES:
            alarms.append("lineage_day_over_1_5x")
        if lineage_total > LINEAGE_TOTAL_CEILING_BYTES:
            alarms.append("lineage_total_over_150mb")
        return {
            "days": days, "daily_bars_total_bytes": bars_total, "daily_bars_max_day_bytes": bars_max_day,
            "daily_bars_max_day": max_day, "lineage_total_bytes": lineage_total,
            "lineage_max_day_bytes": lineage_max_day, "alarms": alarms,
            "limits": {
                "daily_bars_day_alarm_bytes": DAILY_BARS_DAY_ALARM_BYTES,
                "daily_bars_total_ceiling_bytes": DAILY_BARS_TOTAL_CEILING_BYTES,
                "lineage_day_alarm_bytes": LINEAGE_DAY_ALARM_BYTES,
                "lineage_total_ceiling_bytes": LINEAGE_TOTAL_CEILING_BYTES,
            },
        }

    def enforce_budget(self) -> dict[str, Any]:
        report = self.budget_report()
        if report["alarms"]:
            raise DailyBarsBudgetExceeded("daily bars budget exceeded: " + ", ".join(report["alarms"]))
        return report

    def health(self) -> dict[str, Any]:
        report = self.budget_report()
        return {
            "schema_version": "daily_bars_health.v1", "counters": dict(self.counters),
            "last_error": self.last_error, "alarms": report["alarms"], "budget": report,
        }

    def record_dual_write_error(self, exc: BaseException) -> None:
        self.counters["dual_write_errors"] += 1
        self.last_error = f"{type(exc).__name__}: {exc}"[:300]


# ---------------------------------------------------------------------------------------------
# SOURCE-DATASETS-KEYED-WRITE-20261119: every other persisted dataset (design section 6A).
# One declaration per dataset: unique key, retention, per-day byte budget, readers. Aliases are the
# record fields that may carry a key part (first present wins). Budgets are section 6A bounds.
# ---------------------------------------------------------------------------------------------
_DATE_ALIASES = ("date", "trade_date", "as_of_date", "event_time")
_SYMBOL_ALIASES = ("symbol_canonical", "symbol", "stock_id", "code")


def _decl(dataset, key, aliases, *, partition="trade_date", optional=(), retention=2555, budget, readers=()):
    return DatasetKeyDeclaration(
        dataset=dataset, key_fields=tuple(key), partition_field=partition, key_aliases=tuple(tuple(a) for a in aliases),
        optional_key_fields=tuple(optional), retention_days=retention, day_budget_bytes=budget, readers=tuple(readers))


_MB = 1_000_000
KEYED_DATASETS: dict[str, DatasetKeyDeclaration] = {d.dataset: d for d in (
    _decl("tw_index_daily", ("index_code", "trade_date"), (("index_code", "index_id", "symbol_canonical", "symbol"), _DATE_ALIASES),
          budget=int(0.1 * _MB), readers=("services/research/regime/features.py", "services/source_ingestion/feature_catalogue.py")),
    _decl("tw_index_ohlc_daily", ("index_code", "trade_date"), (("index_code", "index_id", "symbol_canonical", "symbol"), _DATE_ALIASES),
          budget=int(0.1 * _MB), readers=("services/source_ingestion/connectors/collector_bundle.py", "services/research/regime/features.py")),
    _decl("tw_market_breadth_daily", ("market", "trade_date"), (("market", "exchange", "venue"), _DATE_ALIASES),
          budget=10_000, readers=("services/research/regime/features.py", "services/source_ingestion/feature_catalogue.py")),
    _decl("tw_institutional_flow", ("symbol", "trade_date", "investor_class"),
          (("market",) + _SYMBOL_ALIASES, _DATE_ALIASES, ("investor_class", "institution", "investor_type")),
          optional=("symbol", "investor_class"), budget=int(0.3 * _MB),
          readers=("services/research/regime/features.py", "services/source_ingestion/feature_catalogue.py")),
    _decl("tw_margin_short_balance", ("symbol", "trade_date"), (("market",) + _SYMBOL_ALIASES, _DATE_ALIASES),
          optional=("symbol",), budget=int(0.2 * _MB),
          readers=("services/research/regime/features.py", "services/source_ingestion/feature_catalogue.py")),
    _decl("tw_broker_top", ("symbol", "trade_date", "rank"), (_SYMBOL_ALIASES, _DATE_ALIASES, ("rank", "broker_rank", "broker_id")),
          retention=750, budget=int(0.2 * _MB), readers=("services/data-plane/taiwan_reference.py",)),
    _decl("tw_broker_branch_detail", ("symbol", "trade_date", "broker_id", "branch_id"),
          (_SYMBOL_ALIASES, _DATE_ALIASES, ("broker_id", "securities_trader_id"), ("branch_id", "branch", "securities_trader")),
          retention=250, budget=5 * _MB),
    _decl("taifex_ssf_contracts", ("contract_id", "trade_date"), (("contract_id", "contract", "symbol_canonical", "symbol"), _DATE_ALIASES),
          retention=750, budget=int(0.1 * _MB),
          readers=("services/execution/lean_runtime/instrument_candidates.py", "services/execution/lean_runtime/futures_session_inputs.py")),
    _decl("taifex_tx_daily", ("contract_id", "trade_date"), (("contract_id", "contract", "symbol_canonical", "symbol"), _DATE_ALIASES),
          budget=10_000, readers=("services/research/regime/features.py",)),
    _decl("tw_macro_series", ("series_code", "period"), (("series_code", "series_id", "symbol_canonical"), _DATE_ALIASES),
          partition="period", retention=None, budget=10_000, readers=("services/source_ingestion/feature_catalogue.py",)),
    _decl("tw_community_daily", ("symbol", "trade_date", "platform"), (_SYMBOL_ALIASES, _DATE_ALIASES, ("platform", "source")),
          retention=180, budget=int(0.2 * _MB), readers=("services/source_ingestion/feature_catalogue.py",)),
    _decl("collector_news", ("item", "trade_date"), (("url", "content_ref", "source_id"), _DATE_ALIASES),
          retention=365, budget=20 * _MB, readers=("services/search/index_pipeline.py",)),
    _decl("collector_social_posts", ("item", "trade_date"), (("url", "content_ref", "source_id"), _DATE_ALIASES),
          retention=365, budget=20 * _MB, readers=("services/search/index_pipeline.py",)),
)}
KEYED_VOLATILE_FIELDS = frozenset({
    "ingest_time", "available_time", "ingest_run_id", "fetched_at", "observed_at", "created_at", "raw_ref", "content_ref"})
KEYED_SCHEMA_VERSION = "keyed_dataset_manifest.v1"


class KeyedDatasetError(DailyBarsError):
    """Keyed dataset write or read refused; the message is a readable reason."""


def keyed_dataset_dir(root: str | Path, dataset: str) -> Path:
    return Path(root) / "keyed" / dataset


def _partition_value(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    day = validate_trade_date(text)
    if day:
        return day
    return text if len(text) <= 10 and all(c.isalnum() or c == "-" for c in text) else None


class KeyedDatasetStore:
    """Keyed partition writer for one 6A dataset: one record per key, newer replaces older,
    an unchanged re-run writes nothing. Same protocol as DailyBarsStore (parts, then manifest LAST)."""

    def __init__(self, root: str | Path, declaration: DatasetKeyDeclaration, *, fault: Callable[[str], None] | None = None) -> None:
        if not declaration.key_aliases:
            raise KeyedDatasetError(f"dataset {declaration.dataset} has no key aliases declared")
        self.root = Path(root)
        self.declaration = declaration
        self.base = keyed_dataset_dir(root, declaration.dataset)
        self.manifest_dir = self.base / "_manifest"
        self._fault = fault
        self._lock = threading.Lock()
        self.counters = {"rows_written": 0, "days_written": 0, "days_unchanged": 0, "rejected_no_key": 0}

    # -- key and content ---------------------------------------------------------------------
    @staticmethod
    def _sources(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        meta = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
        nrow = meta.get("normalized_row") if isinstance(meta.get("normalized_row"), Mapping) else {}
        return [nrow, meta, record]

    def key_of(self, record: Mapping[str, Any]) -> tuple[tuple[str, ...], str] | None:
        """Return (key parts, partition) or None when a required key part is missing."""
        decl = self.declaration
        parts: list[str] = []
        partition: str | None = None
        for name, aliases in zip(decl.key_fields, decl.key_aliases):
            value = None
            for src in self._sources(record):
                for alias in aliases:
                    if src.get(alias) not in (None, ""):
                        value = src[alias]
                        break
                if value is not None:
                    break
            if name == decl.partition_field:
                partition = _partition_value(value)
                if partition is None:
                    return None
                parts.append(partition)
            elif value is None:
                if name in decl.optional_key_fields:
                    parts.append("")
                else:
                    return None
            else:
                parts.append(str(value))
        return tuple(parts), str(partition)

    @staticmethod
    def content_hash(record: Mapping[str, Any]) -> str:
        sources = KeyedDatasetStore._sources(record)
        nrow = sources[0] or sources[1]
        return _sha({k: v for k, v in nrow.items() if k not in KEYED_VOLATILE_FIELDS})

    # -- files -------------------------------------------------------------------------------
    def _manifest_path(self, partition: str) -> Path:
        return self.manifest_dir / f"date={partition}.json"

    def read_manifest(self, partition: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._manifest_path(partition).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except ValueError as exc:
            raise KeyedDatasetError(f"manifest of {self.declaration.dataset} {partition} is not valid JSON") from exc

    def _read_partition(self, manifest: Mapping[str, Any]) -> dict[tuple[str, ...], dict[str, Any]]:
        import gzip

        out: dict[tuple[str, ...], dict[str, Any]] = {}
        for entry in manifest.get("files", ()):
            path = self.base / entry["path"]
            if not path.is_file() or path.stat().st_size != int(entry["bytes"]):
                raise KeyedDatasetError(f"{self.declaration.dataset} file {entry['path']} is missing or has a different size")
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        item = json.loads(line)
                        keyed = self.key_of(item)
                        if keyed is None:
                            raise KeyedDatasetError(f"{self.declaration.dataset} file {entry['path']} holds a record without a key")
                        item["_hash"] = self.content_hash(item)  # recomputed, not stored (bytes)
                        out[keyed[0]] = item
        return out

    def records(self, partition: str) -> list[dict[str, Any]]:
        manifest = self.read_manifest(partition)
        if manifest is None:
            return []
        return [{k: v for k, v in item.items() if k != "_hash"}
                for item in self._read_partition(manifest).values()]

    def partitions(self) -> list[str]:
        if not self.manifest_dir.is_dir():
            return []
        return sorted(p.name[5:-5] for p in self.manifest_dir.glob("date=*.json"))

    def verify_partition(self, partition: str) -> dict[str, Any]:
        """Re-read one partition against its manifest: files exist with the recorded size, every record has a
        key, the row count and the content digest match. Writes nothing. ``keys`` is the set of key tuples."""
        try:
            manifest = self.read_manifest(partition)
        except KeyedDatasetError as exc:
            return {"partition": partition, "verified": False, "reason": str(exc), "keys": set()}
        if manifest is None:
            return {"partition": partition, "verified": False, "reason": "no_manifest", "keys": set()}
        try:
            merged = self._read_partition(manifest)
        except (KeyedDatasetError, OSError, ValueError) as exc:
            return {"partition": partition, "verified": False, "reason": str(exc)[:200], "keys": set()}
        digest = _sha(sorted([list(k), v["_hash"]] for k, v in merged.items()))
        ok = digest == manifest.get("content_digest") and len(merged) == manifest.get("row_count")
        return {"partition": partition, "verified": ok, "reason": None if ok else "manifest_mismatch",
                "row_count": len(merged), "content_digest": manifest.get("content_digest"), "keys": set(merged)}

    def _hit(self, step: str) -> None:
        if self._fault is not None:
            self._fault(step)

    # -- write -------------------------------------------------------------------------------
    def write_records(self, records: Iterable[Mapping[str, Any]], *, ingest_run_id: str = "") -> dict[str, Any]:
        by_part: dict[str, dict[tuple[str, ...], dict[str, Any]]] = {}
        rejected = 0
        for record in records:
            keyed = self.key_of(record)
            if keyed is None:
                rejected += 1
                continue
            key, partition = keyed
            by_part.setdefault(partition, {})[key] = {"_hash": self.content_hash(record), **dict(record)}
        self.counters["rejected_no_key"] += rejected
        written: list[str] = []
        unchanged: list[str] = []
        for partition in sorted(by_part):
            (written if self._write_partition(partition, by_part[partition]) else unchanged).append(partition)
        return {"partitions_written": written, "partitions_unchanged": unchanged, "rejected_no_key": rejected,
                "rows": sum(len(v) for v in by_part.values())}

    def _write_partition(self, partition: str, incoming: Mapping[tuple[str, ...], dict[str, Any]]) -> bool:
        import gzip

        decl = self.declaration
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        with self._lock, open(self.manifest_dir / ".lock", "a+") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            try:
                manifest = self.read_manifest(partition)
                merged = self._read_partition(manifest) if manifest else {}
                for key, item in incoming.items():
                    old = merged.get(key)
                    if old is None or old["_hash"] != item["_hash"]:
                        merged[key] = item
                digest = _sha(sorted([list(k), v["_hash"]] for k, v in merged.items()))
                if manifest and manifest.get("content_digest") == digest:
                    self.counters["days_unchanged"] += 1
                    return False
                generation = int(manifest.get("generation", 0)) + 1 if manifest else 1
                part_dir = self.base / f"date={partition}"
                part_dir.mkdir(parents=True, exist_ok=True)
                final = part_dir / f"part-g{generation}.jsonl.gz"
                tmp = final.with_name(final.name + ".tmp")
                with open(tmp, "wb") as raw:
                    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
                        for key in sorted(merged):
                            gz.write((_canonical({k: v for k, v in merged[key].items() if k != "_hash"}) + "\n").encode("utf-8"))
                    raw.flush()
                    os.fsync(raw.fileno())
                self._hit("after_fsync")
                os.replace(tmp, final)
                _fsync_dir(part_dir)
                size = final.stat().st_size
                if decl.day_budget_bytes is not None and size > decl.day_budget_bytes:
                    final.unlink(missing_ok=True)
                    raise DailyBudgetError(decl.dataset, partition, size, decl.day_budget_bytes)
                new_manifest = {
                    "schema_version": KEYED_SCHEMA_VERSION, "dataset": decl.dataset, "key_fields": list(decl.key_fields),
                    "partition": partition, "generation": generation, "row_count": len(merged), "bytes": size,
                    "files": [{"path": final.relative_to(self.base).as_posix(), "rows": len(merged), "bytes": size}],
                    "content_digest": digest,
                }
                manifest_path = self._manifest_path(partition)
                tmp_manifest = manifest_path.with_name(manifest_path.name + ".tmp")
                with open(tmp_manifest, "wb") as handle:
                    handle.write(json.dumps(new_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                self._hit("before_manifest_rename")
                os.replace(tmp_manifest, manifest_path)  # LAST
                _fsync_dir(self.manifest_dir)
                for stale in part_dir.iterdir():
                    if stale != final:
                        stale.unlink(missing_ok=True)
                self.counters["rows_written"] += len(merged)
                self.counters["days_written"] += 1
                return True
            finally:
                fcntl.flock(lock_handle, fcntl.LOCK_UN)

    def delete_partition(self, partition: str, *, dry_run: bool = False) -> dict[str, Any]:
        """Delete one whole partition: manifest first (readers stop seeing it in one step), then its files."""
        with self._lock, open(self._manifest_lock_path(), "a+") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            try:
                manifest = self.read_manifest(partition)
                if manifest is None:
                    return {"partition": partition, "rows": 0, "bytes_freed": 0, "deleted": False}
                out = {"partition": partition, "rows": int(manifest.get("row_count", 0)),
                       "bytes_freed": int(manifest.get("bytes", 0)), "deleted": not dry_run}
                if dry_run:
                    return out
                self._manifest_path(partition).unlink(missing_ok=True)
                _fsync_dir(self.manifest_dir)
                self._hit("after_manifest_unlink")
                part_dir = self.base / f"date={partition}"
                if part_dir.is_dir():
                    for f in part_dir.iterdir():
                        f.unlink(missing_ok=True)
                    try:
                        part_dir.rmdir()
                    except OSError:
                        pass
                return out
            finally:
                fcntl.flock(lock_handle, fcntl.LOCK_UN)

    def _manifest_lock_path(self) -> Path:
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        return self.manifest_dir / ".lock"

    def budget_report(self) -> dict[str, Any]:
        sizes = []
        for part in self.partitions():
            sizes.append(int((self.read_manifest(part) or {}).get("bytes", 0)))
        limit = self.declaration.day_budget_bytes
        return {"dataset": self.declaration.dataset, "partitions": len(sizes), "total_bytes": sum(sizes),
                "max_day_bytes": max(sizes, default=0), "day_budget_bytes": limit,
                "retention_days": self.declaration.retention_days,
                "alarms": [] if limit is None or max(sizes, default=0) <= limit else [f"{self.declaration.dataset}_day_over_budget"]}


class DailyBudgetError(KeyedDatasetError):
    def __init__(self, dataset: str, partition: str, size: int, limit: int) -> None:
        super().__init__(f"{dataset} partition {partition} is {size} bytes, over the section 6A budget of {limit} bytes; nothing was published")


def keyed_dataset_store(root: str | Path, dataset: str) -> KeyedDatasetStore | None:
    decl = KEYED_DATASETS.get(dataset)
    return KeyedDatasetStore(root, decl) if decl else None
