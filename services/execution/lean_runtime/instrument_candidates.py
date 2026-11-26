"""Layer B of the instrument expression layer: the candidate list per decision.

``build_candidates`` returns every instrument a persona may use today for one
Taiwan underlying: the cash share (always) and each listed single stock or ETF
future that passes the generation rules. It does not choose, size a trade, or
place an order; the expression policy chooses among the returned records.

Inputs are read only from the normalized ``taifex_ssf_contracts`` dataset
through ``read_normalized_ssf_rows`` (bounded file and row counts). The module
makes no network call. Specification: DEV_PLAN_INSTRUMENT_EXPRESSION_LAYER
section 4.2 and 5.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Callable, Collection, Mapping, Sequence

from services.execution.lean_runtime.taifex_fee_schedule import (
    STOCK_FUTURES_TRANSACTION_TAX_RATE,
    fee_row_for,
)
from services.execution.shioaji_adapter import normalize_taiwan_symbol
from services.source_ingestion.connectors.taiwan_official import (
    governed_taiwan_calendar_evidence,
)

SSF_DATASET = "taifex_ssf_contracts"
NORMALIZED_DATASET_PATH = "normalized/taifex_ssf_contracts"
CONTRACT_CODE = re.compile(r"^[A-Z]{2}F$")
CONTRACT_MONTH = re.compile(r"^\d{6}$")
RANDOM_INGEST_RUN_STEM = re.compile(r"^ingest-[0-9a-f]{8,}$", re.IGNORECASE)
MAX_PARTITIONS = 3
MAX_FILES = 6
MAX_SCAN_FILES = 64
MAX_ROWS = 20000
MAX_FILE_BYTES = 16 * 1024 * 1024
INDUSTRY_DATASET = "tw_industry_classification"
DAILY_PRICE_DATASET = "tw_price_daily"
DEFAULT_ADV_WINDOW_DAYS = 20
_JSONL_GLOB = "*." + "jsonl*"

# Bounded in-memory market fact cache (tw_industry_classification + 20-day ADV).
# Runtime budget: process-local RAM only (0 persistent writers), <= 256 entries
# (~64 KB max RSS), 300s TTL eviction on read/write + LRU eviction at cap.
FACT_CACHE_MAX_ENTRIES = 256
FACT_CACHE_TTL_SECONDS = 300.0
_MARKET_FACT_CACHE: "OrderedDict[tuple[Any, ...], tuple[float, Any]]" = OrderedDict()

RowsReader = Callable[[], "Sequence[Mapping[str, Any]] | None"]


def clear_market_fact_cache() -> None:
    """Clear the bounded in-memory market fact cache."""
    _MARKET_FACT_CACHE.clear()


def market_fact_cache_stats() -> dict[str, Any]:
    """Return runtime budget metrics for the in-memory market fact cache."""
    return {
        "entries": len(_MARKET_FACT_CACHE),
        "max_entries": FACT_CACHE_MAX_ENTRIES,
        "ttl_seconds": FACT_CACHE_TTL_SECONDS,
    }


def _fact_cache_get(key: tuple[Any, ...]) -> tuple[bool, Any]:
    now_mono = time.monotonic()
    expired = [k for k, (exp, _) in list(_MARKET_FACT_CACHE.items()) if exp <= now_mono]
    for k in expired:
        _MARKET_FACT_CACHE.pop(k, None)
    entry = _MARKET_FACT_CACHE.get(key)
    if entry is None:
        return False, None
    _MARKET_FACT_CACHE.move_to_end(key)
    return True, entry[1]


def _fact_cache_put(key: tuple[Any, ...], value: Any) -> Any:
    now_mono = time.monotonic()
    _MARKET_FACT_CACHE[key] = (now_mono + FACT_CACHE_TTL_SECONDS, value)
    _MARKET_FACT_CACHE.move_to_end(key)
    while len(_MARKET_FACT_CACHE) > FACT_CACHE_MAX_ENTRIES:
        _MARKET_FACT_CACHE.popitem(last=False)
    return value


@dataclass
class _CandidateDiskFile:
    file_path: Path
    part_date: date
    part_name: str
    file_name: str
    is_random_ingest_stem: bool
    file_time_key: tuple[date, date, float, str, float, str]
    parsed_file_rows: list[dict[str, Any]]
    relevant_rows: list[dict[str, Any]]
    file_contracts: list[str]
    file_truncated: bool
    discovered_codes: set[str]
    has_all_scope: bool


def normalize_underlying_code(symbol: str) -> str:
    """``2330``, ``2330.TW`` and ``2330.TWSE`` all normalise to ``2330``."""
    cleaned = str(symbol or "").strip()
    if cleaned.upper().startswith("EQ:"):
        cleaned = cleaned[3:].strip()
    native, _venue = normalize_taiwan_symbol(cleaned)
    return native


def _finalize_disk_rows(rows_newest_first: list[dict[str, Any]], total_files: int) -> list[dict[str, Any]]:
    rows_newest_first.reverse()
    for row in rows_newest_first:
        rev_idx = int(row.pop("_reverse_file_idx", 0) or 0)
        if rev_idx > 0 and "_file_run_seq" not in row:
            row["_file_run_seq"] = total_files - rev_idx + 1
    return rows_newest_first


def _row_snapshot_time_key(row: Mapping[str, Any]) -> tuple[date, date, float, str, float, str]:
    market_date = _snapshot_market_date(row)
    margin_date = _snapshot_margin_date(row)
    effective_market_date = market_date or margin_date or date.min
    effective_margin_date = margin_date or effective_market_date
    avail_epoch, avail_raw = _optional_timestamp(row.get("available_time"))
    ingest_epoch, ingest_raw = _optional_timestamp(row.get("ingest_time"))
    return (
        effective_market_date,
        effective_margin_date,
        avail_epoch,
        avail_raw,
        ingest_epoch,
        ingest_raw,
    )


def _discovered_codes_in_row(row: Mapping[str, Any]) -> set[str]:
    codes: set[str] = set()
    direct = _row_underlying_code(row)
    if direct:
        codes.add(direct)
    for key in ("snapshot_scope", "snapshot_underlyings"):
        for item in row.get(key) or ():
            text = str(item or "").strip()
            if not text:
                continue
            try:
                norm = normalize_underlying_code(text)
            except ValueError:
                norm = text
            if norm:
                codes.add(norm)
    return codes


def _rows_digest_for_code(rows: Sequence[Mapping[str, Any]], code: str) -> tuple[str, ...]:
    items: list[str] = []
    for row in rows:
        if not _row_covers_underlying(row, code):
            continue
        cleaned = {
            str(k): v
            for k, v in row.items()
            if not str(k).startswith("_") and str(k) != "ingest_time"
        }
        items.append(json.dumps(cleaned, ensure_ascii=False, sort_keys=True, default=str))
    items.sort()
    return tuple(items)


def _file_time_key_for_code(cand: _CandidateDiskFile, code: str) -> tuple[date, date, float, str, float, str] | None:
    keys = [_row_snapshot_time_key(r) for r in cand.relevant_rows if _row_covers_underlying(r, code)]
    return max(keys) if keys else None


def read_normalized_ssf_rows(
    root: str | Path,
    *,
    underlying: str | None = None,
    as_of_trading_day: str | date | None = None,
    max_partitions: int = MAX_PARTITIONS,
    max_files: int = MAX_FILES,
    max_rows: int = MAX_ROWS,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_scan_files: int = MAX_SCAN_FILES,
) -> list[dict[str, Any]] | None:
    """Read the newest normalized partitions of ``taifex_ssf_contracts``.

    Layout is the one ``MarketDataStorageWriter`` writes:
    ``<root>/normalized/taifex_ssf_contracts/date=YYYY-MM-DD/<run>.jsonl`` with
    the row under ``metadata.normalized_row``. Returns ``None`` when the dataset
    directory does not exist; candidate files are ranked by their authoritative
    snapshot chronology before selecting ``max_files``, and reading fails closed
    when latest coverage cannot be established within the bounded limits.
    """
    from services.source_ingestion.market_data_storage import normalized_dataset_base

    # PANTHEON_KEYED_DATASET_READER=keyed reads the keyed store (same record shape); default = run files.
    base = normalized_dataset_base(root, NORMALIZED_DATASET_PATH.rsplit("/", 1)[-1])
    if not base.is_dir():
        return None
    if max_partitions <= 0 or max_files <= 0 or max_rows <= 0 or max_file_bytes <= 0 or max_scan_files <= 0:
        return []
    target_code = normalize_underlying_code(underlying) if underlying else None
    target_day = _as_date(as_of_trading_day) if as_of_trading_day is not None else None
    partition_dirs: list[tuple[date, str, Path]] = []
    for path in base.iterdir():
        if not path.is_dir() or not path.name.startswith("date="):
            continue
        part_date = _optional_date(path.name[5:])
        if target_day is not None and part_date is not None and part_date > target_day:
            continue
        partition_dirs.append((part_date or date.min, path.name, path))
    partition_dirs.sort(key=lambda item: (item[0], item[1]), reverse=True)
    partitions = partition_dirs[:max_partitions]

    candidate_files: list[_CandidateDiskFile] = []
    scanned_files = 0
    for part_date, part_name, partition in partitions:
        for file_path in sorted(partition.glob("*.jsonl*")):
            scanned_files += 1
            if scanned_files > max_scan_files:
                return []
            try:
                stat = file_path.stat()
            except OSError:
                return []
            if stat.st_size <= 0 or stat.st_size > max_file_bytes:
                return []
            try:
                if file_path.suffix == ".gz":
                    import gzip

                    with gzip.open(file_path, "rt", encoding="utf-8") as handle:
                        lines = handle.readlines()
                else:
                    with file_path.open("r", encoding="utf-8") as handle:
                        lines = handle.readlines()
            except OSError:
                return []
            parsed_file_rows: list[dict[str, Any]] = []
            file_truncated = False
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    return []
                if not isinstance(record, Mapping):
                    return []
                row = (record.get("metadata") or {}).get("normalized_row")
                if not isinstance(row, Mapping):
                    return []
                if len(parsed_file_rows) >= max_rows:
                    file_truncated = True
                    break
                parsed_file_rows.append(dict(row))
            if not parsed_file_rows:
                return []
            applicable_rows = (
                [r for r in parsed_file_rows if _is_applicable_on_decision_day(r, target_day)]
                if target_day is not None
                else parsed_file_rows
            )
            if not applicable_rows:
                continue
            relevant_rows = (
                [r for r in applicable_rows if _row_covers_underlying(r, target_code)]
                if target_code is not None
                else applicable_rows
            )
            if not relevant_rows:
                continue
            file_contracts = sorted({str(r.get("contract") or "") for r in parsed_file_rows if r.get("contract")})
            discovered_codes: set[str] = set()
            has_all_scope = False
            for r in relevant_rows:
                discovered_codes |= _discovered_codes_in_row(r)
                if str(r.get("snapshot_scope_mode") or "").strip() == "all":
                    has_all_scope = True
            candidate_files.append(
                _CandidateDiskFile(
                    file_path=file_path,
                    part_date=part_date,
                    part_name=part_name,
                    file_name=file_path.name,
                    is_random_ingest_stem=bool(RANDOM_INGEST_RUN_STEM.match(file_path.stem)),
                    file_time_key=max(_row_snapshot_time_key(r) for r in relevant_rows),
                    parsed_file_rows=parsed_file_rows,
                    relevant_rows=relevant_rows,
                    file_contracts=file_contracts,
                    file_truncated=file_truncated,
                    discovered_codes=discovered_codes,
                    has_all_scope=has_all_scope,
                )
            )
    if not candidate_files:
        return []

    all_codes = (
        {target_code}
        if target_code is not None
        else set().union(*(c.discovered_codes for c in candidate_files))
    )
    authoritative_latest_key: dict[str, tuple[date, date, float, str, float, str]] = {}
    incomplete_codes: set[str] = set()
    for code in all_codes:
        key_and_cand = [
            (key, cand)
            for cand in candidate_files
            for key in (_file_time_key_for_code(cand, code),)
            if key is not None
        ]
        if not key_and_cand:
            continue
        latest_key = max(item[0] for item in key_and_cand)
        authoritative_latest_key[code] = latest_key
        tied_at_latest = [cand for key, cand in key_and_cand if key == latest_key]
        if any(cand.file_truncated for cand in tied_at_latest):
            incomplete_codes.add(code)
        elif len(tied_at_latest) > 1 and any(cand.is_random_ingest_stem for cand in tied_at_latest):
            if len({_rows_digest_for_code(cand.relevant_rows, code) for cand in tied_at_latest}) > 1:
                incomplete_codes.add(code)

    candidate_files.sort(
        key=lambda cand: (cand.file_time_key, cand.part_date, cand.part_name, cand.file_name),
        reverse=True,
    )
    selected_indices: list[int] = []
    covered_latest_for_code: set[str] = set()
    for idx, cand in enumerate(candidate_files):
        if len(selected_indices) >= max_files:
            break
        newly_covered = {
            code
            for code, latest_key in authoritative_latest_key.items()
            if code not in covered_latest_for_code and _file_time_key_for_code(cand, code) == latest_key
        }
        if newly_covered or not selected_indices:
            selected_indices.append(idx)
            covered_latest_for_code |= newly_covered
    for idx, cand in enumerate(candidate_files):
        if len(selected_indices) >= max_files:
            break
        if idx not in selected_indices:
            selected_indices.append(idx)
            for code, latest_key in authoritative_latest_key.items():
                if code not in covered_latest_for_code and _file_time_key_for_code(cand, code) == latest_key:
                    covered_latest_for_code.add(code)
    selected_indices.sort()
    selected_files = [candidate_files[idx] for idx in selected_indices]
    for code in authoritative_latest_key:
        if code not in covered_latest_for_code:
            incomplete_codes.add(code)

    rows_newest_first: list[dict[str, Any]] = []
    for file_rank, cand in enumerate(selected_files, start=1):
        start_pos = len(rows_newest_first)
        for item in cand.parsed_file_rows:
            if len(rows_newest_first) >= max_rows:
                for partial in rows_newest_first[start_pos:]:
                    partial["_snapshot_truncated"] = True
                return _finalize_disk_rows(rows_newest_first, file_rank)
            row_copy = dict(item)
            if cand.file_truncated or any(_row_covers_underlying(row_copy, code) for code in incomplete_codes):
                row_copy["_snapshot_truncated"] = True
            row_copy["_reverse_file_idx"] = file_rank
            row_copy.setdefault("_file_contracts", cand.file_contracts)
            rows_newest_first.append(row_copy)
    return _finalize_disk_rows(rows_newest_first, len(selected_files))


def _sector_limit_for_key(limit: Any, sector_key: str | None) -> Decimal | None:
    if limit is None or isinstance(limit, bool):
        return None
    if isinstance(limit, (int, float, Decimal)):
        dec = _decimal(limit)
        return dec if dec > 0 else None
    if isinstance(limit, Mapping):
        if sector_key is None or not str(sector_key).strip():
            return None
        clean = str(sector_key).strip()
        raw = limit.get(clean)
        if raw is None and clean.isdigit():
            for alias in (f"{int(clean):02d}", str(int(clean))):
                if alias in limit:
                    raw = limit.get(alias)
                    break
        if raw is None:
            raw = limit.get("*")
        if raw is None or isinstance(raw, bool):
            return None
        try:
            dec = _decimal(raw)
        except Exception:
            return None
        return dec if dec > 0 else None
    return None


def read_industry_classifications(
    root: str | Path,
    *,
    underlying: str | None = None,
    as_of_trading_day: str | date | None = None,
    max_partitions: int = MAX_PARTITIONS,
    max_files: int = MAX_FILES,
    max_rows: int = MAX_ROWS,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> dict[str, str] | None:
    """Read Taiwan official industry classifications (``tw_industry_classification``) from disk.

    Returns ``{underlying_code: industry_code_str}`` or ``None`` when the dataset directory is absent.
    Makes no network call and uses the bounded in-memory ``_MARKET_FACT_CACHE``.
    """
    from services.source_ingestion.market_data_storage import normalized_dataset_base

    if root is None or not str(root).strip():
        return None
    base = normalized_dataset_base(root, INDUSTRY_DATASET)
    if not base.is_dir():
        return None
    target_day = _optional_date(as_of_trading_day) if as_of_trading_day is not None else None
    partition_dirs: list[tuple[date, str, Path, int]] = []
    for path in base.iterdir():
        if not path.is_dir() or not path.name.startswith("date="):
            continue
        part_date = _optional_date(path.name[5:])
        if target_day is not None and part_date is not None and part_date > target_day:
            continue
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        partition_dirs.append((part_date or date.min, path.name, path, mtime_ns))
    partition_dirs.sort(key=lambda item: (item[0], item[1]), reverse=True)
    partitions = partition_dirs[: max(1, int(max_partitions))]
    if not partitions:
        return {}
    cache_key = (
        "tw_industry_classification",
        str(base.resolve()),
        str(target_day or ""),
        tuple((p[1], p[3]) for p in partitions),
    )
    hit, cached = _fact_cache_get(cache_key)
    if hit and isinstance(cached, dict):
        full_map = dict(cached)
    else:
        full_map: dict[str, str] = {}
        files_read = 0
        rows_read = 0
        for _part_date, _part_name, partition, _mtime in partitions:
            for file_path in sorted(partition.glob(_JSONL_GLOB), reverse=True):
                if files_read >= max_files or rows_read >= max_rows:
                    break
                try:
                    st = file_path.stat()
                except OSError:
                    continue
                if st.st_size <= 0 or st.st_size > max_file_bytes:
                    continue
                files_read += 1
                try:
                    if file_path.suffix == ".gz":
                        import gzip

                        with gzip.open(file_path, "rt", encoding="utf-8") as handle:
                            lines = handle.readlines()
                    else:
                        with file_path.open("r", encoding="utf-8") as handle:
                            lines = handle.readlines()
                except OSError:
                    continue
                for line in reversed(lines):
                    if rows_read >= max_rows:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    rows_read += 1
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, Mapping):
                        continue
                    meta = rec.get("metadata") if isinstance(rec.get("metadata"), Mapping) else {}
                    row = meta.get("normalized_row") if isinstance(meta.get("normalized_row"), Mapping) else rec
                    raw_sym = (
                        row.get("symbol")
                        or row.get("underlying_code")
                        or row.get("code")
                        or rec.get("symbol")
                        or meta.get("symbol")
                    )
                    if not raw_sym:
                        continue
                    try:
                        sym_code = normalize_underlying_code(str(raw_sym))
                    except Exception:
                        sym_code = str(raw_sym).strip()
                    if not sym_code or sym_code in full_map:
                        continue
                    raw_ind = (
                        row.get("industry_code_raw")
                        if row.get("industry_code_raw") not in (None, "")
                        else (
                            row.get("industry_code")
                            if row.get("industry_code") not in (None, "")
                            else (row.get("sector") or row.get("industry") or meta.get("industry_code"))
                        )
                    )
                    if raw_ind is None or isinstance(raw_ind, bool):
                        continue
                    ind_str = str(raw_ind).strip()
                    if not ind_str:
                        continue
                    if ind_str.isdigit():
                        ind_str = f"{int(ind_str):02d}" if len(ind_str) == 1 else str(int(ind_str))
                    full_map[sym_code] = ind_str
        _fact_cache_put(cache_key, dict(full_map))
    if underlying is not None and str(underlying).strip():
        try:
            u_code = normalize_underlying_code(str(underlying))
        except Exception:
            u_code = str(underlying).strip()
        return {u_code: full_map[u_code]} if u_code in full_map else {}
    return full_map


def read_avg_daily_volume(
    root: str | Path,
    underlying: str,
    *,
    as_of_trading_day: str | date | None = None,
    window_days: int = DEFAULT_ADV_WINDOW_DAYS,
    max_partitions: int = 30,
    max_files: int = MAX_SCAN_FILES,
    max_rows: int = MAX_ROWS,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> float | None:
    """Compute recent ``window_days`` (default 20) average daily volume from local daily bars.

    Reads ``daily_bars`` (Parquet store via ``DailyBarsReader``) first, then falls back to
    ``normalized/tw_price_daily`` partitions on disk. Returns ``None`` when no positive volume
    observation is available. Makes no network call and uses ``_MARKET_FACT_CACHE``.
    """
    from services.source_ingestion.market_data_storage import normalized_dataset_base

    if root is None or not str(root).strip() or not str(underlying or "").strip() or window_days <= 0:
        return None
    root_path = Path(root)
    if not root_path.exists():
        return None
    try:
        code = normalize_underlying_code(str(underlying))
    except Exception:
        code = str(underlying).strip()
    target_day = _optional_date(as_of_trading_day) if as_of_trading_day is not None else None
    end_date_str = target_day.isoformat() if target_day is not None else "9999-12-31"
    try:
        root_mtime = root_path.stat().st_mtime_ns
    except OSError:
        root_mtime = 0
    cache_key = ("avg_daily_volume", str(root_path.resolve()), code, end_date_str, int(window_days), root_mtime)
    hit, cached = _fact_cache_get(cache_key)
    if hit:
        return cached

    # 1. Try governed daily_bars Parquet reader if _manifest exists
    manifest_dir = root_path / "daily_bars" / "_manifest"
    if manifest_dir.is_dir():
        try:
            from services.data_plane.daily_bars import DailyBarsReader

            reader = DailyBarsReader(root_path)
            bar_rows = reader.read_close_window(
                code,
                end_date_str,
                int(window_days),
                any_form=True,
                columns=["symbol", "trade_date", "close", "volume"],
            )
            vols = [
                float(r["volume"])
                for r in bar_rows
                if isinstance(r, Mapping)
                and r.get("volume") is not None
                and not isinstance(r.get("volume"), bool)
                and math.isfinite(float(r["volume"]))
                and float(r["volume"]) > 0.0
            ]
            if vols:
                tail = vols[-int(window_days) :]
                return _fact_cache_put(cache_key, float(sum(tail) / len(tail)))
        except Exception:
            pass

    # 2. Read normalized/tw_price_daily partitions
    base = normalized_dataset_base(root_path, DAILY_PRICE_DATASET)
    if base.is_dir():
        partition_dirs: list[tuple[date, str, Path]] = []
        for path in base.iterdir():
            if not path.is_dir() or not path.name.startswith("date="):
                continue
            part_date = _optional_date(path.name[5:])
            if target_day is not None and part_date is not None and part_date > target_day:
                continue
            partition_dirs.append((part_date or date.min, path.name, path))
        partition_dirs.sort(key=lambda item: (item[0], item[1]), reverse=True)
        vol_by_day: dict[date, float] = {}
        files_read = 0
        rows_read = 0
        for part_date, _part_name, partition in partition_dirs[: max(1, int(max_partitions))]:
            if len(vol_by_day) >= window_days or files_read >= max_files or rows_read >= max_rows:
                break
            for file_path in sorted(partition.glob(_JSONL_GLOB), reverse=True):
                if files_read >= max_files or rows_read >= max_rows:
                    break
                try:
                    st = file_path.stat()
                except OSError:
                    continue
                if st.st_size <= 0 or st.st_size > max_file_bytes:
                    continue
                files_read += 1
                try:
                    if file_path.suffix == ".gz":
                        import gzip

                        with gzip.open(file_path, "rt", encoding="utf-8") as handle:
                            lines = handle.readlines()
                    else:
                        with file_path.open("r", encoding="utf-8") as handle:
                            lines = handle.readlines()
                except OSError:
                    continue
                for line in reversed(lines):
                    if rows_read >= max_rows:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    rows_read += 1
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, Mapping):
                        continue
                    meta = rec.get("metadata") if isinstance(rec.get("metadata"), Mapping) else {}
                    row = meta.get("normalized_row") if isinstance(meta.get("normalized_row"), Mapping) else rec
                    raw_sym = (
                        row.get("symbol")
                        or row.get("underlying_code")
                        or row.get("code")
                        or rec.get("symbol")
                        or meta.get("symbol")
                    )
                    if not raw_sym:
                        continue
                    try:
                        sym_code = normalize_underlying_code(str(raw_sym))
                    except Exception:
                        sym_code = str(raw_sym).strip()
                    if sym_code != code:
                        continue
                    row_day = (
                        _optional_date(row.get("trade_date") or row.get("date") or row.get("price_date"))
                        or _optional_date(str(rec.get("event_time") or "")[:10])
                        or (part_date if part_date != date.min else None)
                    )
                    if row_day is None or (target_day is not None and row_day > target_day):
                        continue
                    if row_day in vol_by_day:
                        continue
                    raw_vol = (
                        row.get("volume")
                        if row.get("volume") is not None
                        else (
                            row.get("trading_volume")
                            if row.get("trading_volume") is not None
                            else row.get("volume_shares")
                        )
                    )
                    vol_val: float | None = None
                    if raw_vol is not None and not isinstance(raw_vol, bool):
                        try:
                            vol_val = float(raw_vol)
                        except (TypeError, ValueError):
                            vol_val = None
                    elif row.get("volume_lots") is not None and not isinstance(row.get("volume_lots"), bool):
                        try:
                            vol_val = float(row["volume_lots"]) * 1000.0
                        except (TypeError, ValueError):
                            vol_val = None
                    if vol_val is not None and math.isfinite(vol_val) and vol_val > 0.0:
                        vol_by_day[row_day] = vol_val
        if vol_by_day:
            ordered_days = sorted(vol_by_day.keys(), reverse=True)[: int(window_days)]
            adv = float(sum(vol_by_day[d] for d in ordered_days) / len(ordered_days))
            return _fact_cache_put(cache_key, adv)

    return None


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    digits = re.sub(r"\D", "", text)
    if re.fullmatch(r"\d{7}", text) or (len(digits) == 7 and "/" in text):
        text = f"{int(digits[:3]) + 1911:04d}-{digits[3:5]}-{digits[5:7]}"
    elif re.fullmatch(r"\d{8}", text) or (len(digits) == 8 and "/" in text):
        text = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return date.fromisoformat(text)


def _optional_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return _as_date(value)
    except (ValueError, TypeError):
        return None


def _optional_timestamp(value: Any) -> tuple[float, str]:
    text = str(value or "").strip()
    if not text:
        return float("-inf"), ""
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp(), text
    except ValueError:
        parsed_date = _optional_date(text)
        if parsed_date is not None:
            return datetime(parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=timezone.utc).timestamp(), text
        return float("-inf"), text


def _snapshot_market_date(row: Mapping[str, Any]) -> date | None:
    raw_market = row.get("raw_market_row")
    return (
        _optional_date(row.get("price_date"))
        or _optional_date(row.get("date"))
        or _optional_date(row.get("Date"))
        or (_optional_date(raw_market.get("Date")) if isinstance(raw_market, Mapping) else None)
    )


def _snapshot_margin_date(row: Mapping[str, Any]) -> date | None:
    raw_margin = row.get("raw_margin_row")
    return _optional_date(row.get("margin_date")) or (
        _optional_date(raw_margin.get("Date")) if isinstance(raw_margin, Mapping) else None
    )


def _is_applicable_on_decision_day(row: Mapping[str, Any], decision_day: date) -> bool:
    for candidate in (_snapshot_market_date(row), _snapshot_margin_date(row)):
        if candidate is not None and candidate > decision_day:
            return False
    return True


def _snapshot_run_key(
    row: Mapping[str, Any],
) -> tuple[date, date, float, str, float, str, int]:
    return (
        *_row_snapshot_time_key(row),
        int(row.get("_file_run_seq") or 0),
    )


def _row_underlying_code(row: Mapping[str, Any]) -> str:
    raw_underlying = str(row.get("underlying_code") or "").strip()
    if not raw_underlying:
        return ""
    try:
        return normalize_underlying_code(raw_underlying)
    except ValueError:
        return raw_underlying


def _row_covers_underlying(row: Mapping[str, Any], code: str) -> bool:
    if _row_underlying_code(row) == code:
        return True
    scope_mode = str(row.get("snapshot_scope_mode") or "").strip()
    if scope_mode == "all":
        return True
    if scope_mode == "scoped":
        for item in row.get("snapshot_scope") or ():
            text = str(item or "").strip()
            if not text:
                continue
            try:
                if normalize_underlying_code(text) == code:
                    return True
            except ValueError:
                if text == code:
                    return True
    return False


def _snapshot_sort_key(
    row: Mapping[str, Any],
    order_index: int,
) -> tuple[date, date, float, str, float, str, int, int]:
    return (
        *_snapshot_run_key(row),
        order_index,
    )


def governed_twse_holidays(trade_date: str | date | None = None) -> frozenset[date]:
    """Return holidays from the repository's governed TWSE calendar catalog for ``trade_date``."""
    day = _optional_date(trade_date) if trade_date is not None else date(2026, 11, 6)
    if day is None:
        return frozenset()
    try:
        evidence = governed_taiwan_calendar_evidence(venue="TWSE", trade_date=day.isoformat())
    except Exception:
        return frozenset()
    if not isinstance(evidence, Mapping):
        return frozenset()
    result: set[date] = set()
    holidays_map = evidence.get("holidays")
    if isinstance(holidays_map, Mapping):
        for raw_key in holidays_map:
            parsed = _optional_date(raw_key)
            if parsed is not None:
                result.add(parsed)
    trading_days_raw = evidence.get("trading_days")
    if isinstance(trading_days_raw, Sequence) and not isinstance(trading_days_raw, (str, bytes)):
        trading_days = {
            parsed
            for item in trading_days_raw
            for parsed in (_optional_date(item),)
            if parsed is not None
        }
        if day.weekday() < 5 and day not in trading_days:
            result.add(day)
    return frozenset(result)


def _is_trading_day(day: date, holidays: Collection[date] = ()) -> bool:
    if day.weekday() >= 5 or day in holidays:
        return False
    return day not in governed_twse_holidays(day)


def is_trading_day(day: str | date, holidays: Collection[date] = ()) -> bool:
    """True when ``day`` is a Taiwan trading day under the governed calendar and ``holidays``."""
    return _is_trading_day(_as_date(day), holidays)


def parse_instrument_id(instrument_id: str) -> dict[str, Any]:
    """Parse ``eq:<code>`` or ``ssf:<code>:<YYYYMM>`` into structured fields."""
    text = str(instrument_id or "").strip()
    parts = text.split(":")
    if len(parts) == 2 and parts[0] == "eq" and parts[1]:
        code = normalize_underlying_code(parts[1])
        return {
            "instrument_id": f"eq:{code}",
            "kind": "cash_share",
            "underlying": code,
            "contract": None,
            "contract_month": None,
        }
    if (
        len(parts) == 3
        and parts[0] == "ssf"
        and CONTRACT_CODE.match(parts[1])
        and CONTRACT_MONTH.match(parts[2])
    ):
        return {
            "instrument_id": f"ssf:{parts[1]}:{parts[2]}",
            "kind": "stock_future",
            "underlying": None,
            "contract": parts[1],
            "contract_month": parts[2],
        }
    raise ValueError(f"invalid instrument_id: {instrument_id!r}")


def lookup_final_settlement_price(
    contract: str,
    contract_month: str,
    source: Any,
    *,
    settlement_day: str | date | None = None,
    as_of_trading_day: str | date | None = None,
) -> dict[str, Any] | None:
    """Resolve ``FinalSettlementPriceSSF`` for ``(contract, contract_month)``.

    Accepts raw ``FinalSettlementPriceSSF`` rows, a payload mapping containing
    ``FinalSettlementPriceSSF``, normalized SSF rows carrying
    ``last_final_settlement``, a normalized dataset root ``Path``/``str``, a
    callable dataset reader, or an explicit mapping keyed by
    ``ssf:<contract>:<month>``, ``(contract, month)``, or ``month``.
    Returns ``None`` when no valid positive finite settlement price is present.
    """
    clean_contract = str(contract or "").strip().upper()
    clean_month = str(contract_month or "").strip()
    if not CONTRACT_CODE.match(clean_contract) or not CONTRACT_MONTH.match(clean_month):
        return None
    expected_day = _optional_date(settlement_day) if settlement_day is not None else None
    inst_id = f"ssf:{clean_contract}:{clean_month}"

    def _coerce_result(price_val: Any, day_val: Any = None) -> dict[str, Any] | None:
        if price_val is None or isinstance(price_val, bool):
            return None
        try:
            dec = _decimal(price_val)
        except Exception:
            return None
        if not dec.is_finite() or dec <= 0:
            return None
        parsed_day = _optional_date(day_val) if day_val not in (None, "") else expected_day
        return {
            "instrument_id": inst_id,
            "contract": clean_contract,
            "contract_month": clean_month,
            "settlement_day": parsed_day.isoformat() if parsed_day is not None else None,
            "final_settlement_price": float(dec),
        }

    if source is None:
        return None
    if isinstance(source, (str, Path)):
        text_root = str(source).strip()
        if not text_root:
            return None
        effective_day = as_of_trading_day if as_of_trading_day is not None else settlement_day
        disk_rows = read_normalized_ssf_rows(
            text_root,
            as_of_trading_day=effective_day,
        )
        if not disk_rows:
            return None
        return lookup_final_settlement_price(
            clean_contract,
            clean_month,
            list(reversed(disk_rows)),
            settlement_day=settlement_day,
            as_of_trading_day=as_of_trading_day,
        )
    if callable(source):
        try:
            resolved = source(
                clean_contract,
                clean_month,
                settlement_day=settlement_day,
                as_of_trading_day=as_of_trading_day,
            )
        except TypeError:
            resolved = source()
        if resolved is None or resolved is source:
            return None
        return lookup_final_settlement_price(
            clean_contract,
            clean_month,
            resolved,
            settlement_day=settlement_day,
            as_of_trading_day=as_of_trading_day,
        )
    if isinstance(source, Mapping):
        if "FinalSettlementPriceSSF" in source:
            return lookup_final_settlement_price(
                clean_contract,
                clean_month,
                source.get("FinalSettlementPriceSSF"),
                settlement_day=settlement_day,
                as_of_trading_day=as_of_trading_day,
            )
        for key in (inst_id, (clean_contract, clean_month), f"{clean_contract}:{clean_month}", clean_month):
            if key in source:
                entry = source[key]
                if isinstance(entry, Mapping):
                    return _coerce_result(
                        entry.get("final_settlement_price")
                        or entry.get("TheFinalSettlementPrice")
                        or entry.get("price"),
                        entry.get("settlement_day")
                        or entry.get("TheFinalSettlementDay")
                        or entry.get("day"),
                    )
                return _coerce_result(entry, expected_day)
    if isinstance(source, Sequence) and not isinstance(source, (str, bytes)):
        for item in source:
            if not isinstance(item, Mapping):
                continue
            if item.get("tombstone") or item.get("_snapshot_truncated"):
                continue
            row_contract = str(
                item.get("Contract")
                or item.get("ContractCode")
                or item.get("contract")
                or ""
            ).strip().upper()
            row_month = str(
                item.get("ContractDeliveryMonth")
                or item.get("SettlementMonth")
                or item.get("contract_month")
                or item.get("ContractMonth")
                or ""
            ).strip()
            if row_contract == clean_contract and row_month == clean_month:
                raw_price = (
                    item.get("TheFinalSettlementPrice")
                    if "TheFinalSettlementPrice" in item
                    else (
                        item.get("SettlementPrice")
                        if "SettlementPrice" in item
                        else item.get("final_settlement_price")
                    )
                )
                if raw_price is not None:
                    res = _coerce_result(
                        raw_price,
                        item.get("TheFinalSettlementDay")
                        or item.get("Date")
                        or item.get("settlement_day")
                        or item.get("day"),
                    )
                    if res is not None:
                        return res
            last_final = item.get("last_final_settlement")
            if isinstance(last_final, Mapping) and row_contract == clean_contract:
                lf_month = str(last_final.get("contract_month") or "").strip()
                if lf_month == clean_month:
                    res = _coerce_result(last_final.get("price"), last_final.get("day"))
                    if res is not None:
                        return res
    return None


def previous_trading_day(decision_day: date, holidays: Collection[date] = ()) -> date:
    day = decision_day - timedelta(days=1)
    while not _is_trading_day(day, holidays):
        day -= timedelta(days=1)
    return day


def next_trading_day(decision_day: date, holidays: Collection[date] = ()) -> date:
    """First governed trading day strictly after ``decision_day`` (D -> D+1)."""
    day = decision_day + timedelta(days=1)
    while not _is_trading_day(day, holidays):
        day += timedelta(days=1)
    return day


TAIPEI_TZ = timezone(timedelta(hours=8))
SESSION_OPEN_LOCAL = (9, 0)  # regular session open, Asia/Taipei (SD_INTRADAY_MINUTE_BARS 6.2)
SESSION_CLOSE_LOCAL = (13, 30)  # regular session close
SUBMISSION_CUTOFF_LOCAL = (8, 55)  # next-session decision window closes (SD 6.4 step 3)


def session_bounds(day: str | date, holidays: Collection[date] = ()) -> tuple[datetime, datetime]:
    """``(open, close)`` of the regular session of a governed trading day, Asia/Taipei.

    A non-trading day has no session and raises ``ValueError``; the calendar is the
    existing governed one (``is_trading_day``), not a new calendar.
    """
    resolved = _as_date(day)
    if not _is_trading_day(resolved, holidays):
        raise ValueError(f"{resolved.isoformat()} is not a Taiwan trading day")

    def at(hm: tuple[int, int]) -> datetime:
        return datetime(resolved.year, resolved.month, resolved.day, hm[0], hm[1], tzinfo=TAIPEI_TZ)

    return at(SESSION_OPEN_LOCAL), at(SESSION_CLOSE_LOCAL)


def submission_cutoff(day: str | date) -> datetime:
    """08:55 Asia/Taipei of ``day``: the latest instant a next-session decision may be submitted."""
    resolved = _as_date(day)
    return datetime(
        resolved.year, resolved.month, resolved.day, *SUBMISSION_CUTOFF_LOCAL, tzinfo=TAIPEI_TZ
    )


def in_regular_session(moment: datetime, holidays: Collection[date] = ()) -> bool:
    """True when ``moment`` falls inside a trading day's 09:00-13:30 regular session."""
    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware")
    local = moment.astimezone(TAIPEI_TZ)
    if not _is_trading_day(local.date(), holidays):
        return False
    opened, closed = session_bounds(local.date(), holidays)
    return opened <= local < closed


def last_trading_day(contract_month: str, holidays: Collection[date] = ()) -> date:
    """Third Wednesday of the month; a non-trading day moves to the next trading day.

    The holiday shift follows the contract specification page footnote only; it
    is unverified against the exchange calendar for a holiday month (the caller
    supplies the holidays).
    """
    first = date(int(contract_month[:4]), int(contract_month[4:6]), 1)
    day = first + timedelta(days=(2 - first.weekday()) % 7 + 14)
    while not _is_trading_day(day, holidays):
        day += timedelta(days=1)
    return day


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _floor_units(amount: Decimal, per_unit: Decimal) -> int:
    if per_unit <= 0:
        return 0
    return int((max(amount, Decimal(0)) / per_unit).to_integral_value(rounding=ROUND_DOWN))


def _book_and_reservation_exposures(
    persona_book: Mapping[str, Any] | None,
    risk_budget: Mapping[str, Any],
) -> dict[str, Any]:
    """Compute gross, signed net, per-underlying exposure and reserved cash from book + budget."""
    book = persona_book or {}
    positions = book.get("positions") if isinstance(book.get("positions"), list) else []
    pos_gross = Decimal(0)
    pos_signed_net = Decimal(0)
    pos_underlying_gross: dict[str, Decimal] = {}
    for pos in positions:
        if not isinstance(pos, Mapping):
            continue
        raw_notional = pos.get("notional")
        if raw_notional is None:
            units = _decimal(pos.get("units") or pos.get("quantity") or 0)
            mult = _decimal(pos.get("multiplier") or pos.get("unit") or 1)
            px = _decimal(pos.get("mark_price") or pos.get("price") or 0)
            notional = abs(units) * mult * px
        else:
            notional = abs(_decimal(raw_notional))
        direction = str(pos.get("direction") or "LONG").strip().upper()
        signed = -notional if direction == "SHORT" else notional
        pos_gross += notional
        pos_signed_net += signed
        raw_u = str(pos.get("underlying") or pos.get("symbol") or pos.get("instrument_id") or "").strip()
        if raw_u:
            try:
                u_code = normalize_underlying_code(raw_u)
            except Exception:
                u_code = raw_u
            if u_code:
                pos_underlying_gross[u_code] = pos_underlying_gross.get(u_code, Decimal(0)) + notional

    raw_res = risk_budget.get("order_reservations")
    if raw_res is None:
        raw_res = risk_budget.get("reservations")
    if raw_res is None:
        raw_res = book.get("order_reservations") or book.get("reservations")
    res_items = list(raw_res.values()) if isinstance(raw_res, Mapping) else (raw_res if isinstance(raw_res, list) else [])

    res_gross = Decimal(0)
    res_signed_net = Decimal(0)
    res_cash = Decimal(0)
    res_margin = Decimal(0)
    res_underlying_gross: dict[str, Decimal] = {}
    for item in res_items:
        if not isinstance(item, Mapping):
            continue
        if bool(item.get("is_risk_reducing") or item.get("close_leg") or item.get("reason_code") == "close_leg"):
            continue
        qty = abs(_decimal(item.get("units") if item.get("units") is not None else (item.get("quantity") or 0)))
        mult = _decimal(item.get("multiplier") if item.get("multiplier") is not None else (item.get("unit") or 1))
        px = _decimal(item.get("price") if item.get("price") is not None else (item.get("mark_price") or 0))
        raw_n = item.get("notional") if item.get("notional") is not None else item.get("gross_notional")
        notional = abs(_decimal(raw_n)) if raw_n is not None else (qty * mult * px)
        side = str(item.get("side") or item.get("action") or "").strip().lower()
        direction = str(item.get("direction") or ("SHORT" if side == "sell" else "LONG")).strip().upper()
        signed = -notional if (direction == "SHORT" or side == "sell") else notional
        res_gross += notional
        res_signed_net += signed
        raw_c = item.get("cash_reserved") if item.get("cash_reserved") is not None else item.get("cash_required")
        if raw_c is not None:
            res_cash += max(Decimal(0), _decimal(raw_c))
        elif str(item.get("kind") or "cash_share") == "cash_share" and direction == "LONG" and qty > 0 and px > 0:
            from services.execution.lean_runtime.taiwan_cash_costs import buy_cash_required
            raw_u_for_fee = str(item.get("underlying") or item.get("symbol") or "0000")
            res_cash += _decimal(buy_cash_required(raw_u_for_fee, int(qty), float(px)))
        raw_m = item.get("margin_reserved") if item.get("margin_reserved") is not None else item.get("margin_required")
        if raw_m is not None:
            res_margin += max(Decimal(0), _decimal(raw_m))
        raw_u = str(item.get("underlying") or item.get("symbol") or item.get("instrument_id") or "").strip()
        if raw_u:
            try:
                u_code = normalize_underlying_code(raw_u)
            except Exception:
                u_code = raw_u
            if u_code:
                res_underlying_gross[u_code] = res_underlying_gross.get(u_code, Decimal(0)) + notional

    pos_margin = Decimal(0)
    for pos in positions:
        if isinstance(pos, Mapping):
            raw_pm = pos.get("margin_reserved") if pos.get("margin_reserved") is not None else pos.get("initial_margin_reserved")
            if raw_pm is not None:
                pos_margin += max(Decimal(0), _decimal(raw_pm))

    scalar_cash_res = _decimal(risk_budget.get("cash_reserved") or risk_budget.get("reserved_cash") or 0)
    cash_reserved = max(scalar_cash_res, res_cash)
    unfolded_cash_res = max(res_cash - scalar_cash_res, Decimal(0))

    scalar_margin_res = _decimal(
        risk_budget.get("margin_reserved")
        or risk_budget.get("order_margin_reserved")
        or risk_budget.get("reserved_margin")
        or 0
    )
    if risk_budget.get("order_margin_reserved") is not None:
        folded_order_margin = _decimal(risk_budget["order_margin_reserved"])
        inferred_pos_margin = max(scalar_margin_res - folded_order_margin, pos_margin)
    else:
        folded_order_margin = scalar_margin_res
        inferred_pos_margin = pos_margin
    margin_reserved = max(scalar_margin_res, inferred_pos_margin + res_margin)
    unfolded_margin_res = max(res_margin - folded_order_margin, Decimal(0))

    folded_res_u_map = (
        risk_budget.get("reserved_underlying_notional")
        if isinstance(risk_budget.get("reserved_underlying_notional"), Mapping)
        else {}
    )
    folded_res_gross = sum(_decimal(v or 0) for v in folded_res_u_map.values())
    reservations_folded_in_budget = (
        "reserved_underlying_notional" in risk_budget
        and folded_res_gross >= sum(res_underlying_gross.values(), Decimal(0))
        and scalar_cash_res >= res_cash
        and scalar_margin_res >= res_margin
        and _decimal(risk_budget.get("gross_used") or 0) >= pos_gross + res_gross
    )
    unfolded_res_gross = Decimal(0) if reservations_folded_in_budget else max(res_gross - folded_res_gross, Decimal(0))

    raw_gross_used = risk_budget.get("gross_used")
    if raw_gross_used is None:
        raw_gross_used = book.get("gross_notional", 0)
        gross_used = max(_decimal(raw_gross_used or 0) + res_gross, pos_gross + res_gross)
    else:
        gross_used = max(_decimal(raw_gross_used or 0) + unfolded_res_gross, pos_gross + res_gross)

    if risk_budget.get("signed_net_used") is not None:
        signed_net_used = _decimal(risk_budget["signed_net_used"])
        if not reservations_folded_in_budget and unfolded_res_gross > 0 and res_signed_net != 0:
            signed_net_used += res_signed_net
    else:
        signed_net_used = pos_signed_net + res_signed_net

    budget_u_map = risk_budget.get("underlying_exposures")
    underlying_gross: dict[str, Decimal] = {}
    if isinstance(budget_u_map, Mapping):
        for k, v in budget_u_map.items():
            if v is not None:
                underlying_gross[str(k)] = _decimal(v)
    all_u_codes = set(underlying_gross.keys()) | set(pos_underlying_gross.keys()) | set(res_underlying_gross.keys())
    for u_code in all_u_codes:
        pos_u = pos_underlying_gross.get(u_code, Decimal(0))
        res_u = res_underlying_gross.get(u_code, Decimal(0))
        budget_u = underlying_gross.get(u_code, Decimal(0))
        folded_u = res_u if reservations_folded_in_budget else min(res_u, _decimal(folded_res_u_map.get(u_code) or 0))
        unfolded_u = max(res_u - folded_u, Decimal(0))
        underlying_gross[u_code] = max(budget_u + unfolded_u, pos_u + res_u)

    return {
        "gross_used": gross_used,
        "signed_net_used": signed_net_used,
        "underlying_gross": underlying_gross,
        "cash_reserved": cash_reserved,
        "unfolded_cash_reserved": unfolded_cash_res,
        "margin_reserved": margin_reserved,
        "unfolded_margin_reserved": unfolded_margin_res,
        "budget_already_folded": reservations_folded_in_budget,
    }


def _remaining_gross(persona_book: Mapping[str, Any] | None, risk_budget: Mapping[str, Any]) -> Decimal:
    """Fail closed: a missing limit leaves nothing to spend."""
    limit = risk_budget.get("gross_limit")
    equity = risk_budget.get("equity")
    if limit is None and risk_budget.get("gross_limit_multiple") is not None and equity is not None:
        limit = _decimal(risk_budget["gross_limit_multiple"]) * _decimal(equity)
    if limit is None:
        return Decimal(0)
    limit_dec = _decimal(limit)
    leverage = risk_budget.get("max_leverage")
    if leverage is not None and equity is not None:
        # ``max_leverage`` is a multiple of equity; the tighter bound wins.
        limit_dec = min(limit_dec, _decimal(leverage) * _decimal(equity))
    exposures = _book_and_reservation_exposures(persona_book, risk_budget)
    return max(limit_dec - exposures["gross_used"], Decimal(0))


def _remaining_notional_for_underlying(
    code: str,
    persona_book: Mapping[str, Any] | None,
    risk_budget: Mapping[str, Any],
    *,
    long_only: bool,
) -> Decimal:
    """Most restrictive remaining notional across gross, leverage, net, single-name and reservations."""
    remaining = _remaining_gross(persona_book, risk_budget)
    exposures = _book_and_reservation_exposures(persona_book, risk_budget)
    equity_raw = risk_budget.get("equity")
    equity = _decimal(equity_raw) if equity_raw is not None else None

    net_limit_raw = risk_budget.get("net_limit")
    if net_limit_raw is None and risk_budget.get("net_limit_multiple") is not None and equity is not None:
        net_limit_raw = _decimal(risk_budget["net_limit_multiple"]) * equity
    if net_limit_raw is not None:
        net_limit = _decimal(net_limit_raw)
        signed_net = exposures["signed_net_used"]
        rem_long = max(net_limit - signed_net, Decimal(0))
        rem_short = max(net_limit + signed_net, Decimal(0))
        rem_net = rem_long if long_only else max(rem_long, rem_short)
        remaining = min(remaining, rem_net)

    single_name_raw = risk_budget.get("max_single_name_notional")
    if single_name_raw is None and risk_budget.get("max_single_name_weight") is not None and equity is not None:
        single_name_raw = _decimal(risk_budget["max_single_name_weight"]) * equity
    if single_name_raw is not None:
        single_name_limit = _decimal(single_name_raw)
        existing_u = exposures["underlying_gross"].get(code, Decimal(0))
        rem_single = max(single_name_limit - existing_u, Decimal(0))
        remaining = min(remaining, rem_single)

    max_sector_raw = risk_budget.get("max_sector_exposure")
    if max_sector_raw not in (None, {}):
        missing_sec = risk_budget.get("missing_sector_underlyings") or risk_budget.get("missing_sector_symbols") or ()
        sector_by_u = risk_budget.get("sector_by_underlying")
        sec_map = dict(sector_by_u) if isinstance(sector_by_u, Mapping) else {}
        code_sector = sec_map.get(code)
        if code_sector is None and not isinstance(max_sector_raw, Mapping) and not sec_map and not missing_sec:
            # When scalar max_sector_exposure is configured without a sector map, fail closed as unavailable
            return Decimal(0)
        if code in missing_sec or bool(missing_sec) or code_sector is None or equity is None:
            return Decimal(0)
        sector_cap_pct = _sector_limit_for_key(max_sector_raw, str(code_sector))
        if sector_cap_pct is not None:
            sector_limit_notional = sector_cap_pct * equity
            sector_used_from_u = sum(
                (notional for u, notional in exposures["underlying_gross"].items() if str(sec_map.get(u)) == str(code_sector)),
                Decimal(0),
            )
            budget_sec_exp = risk_budget.get("sector_exposures")
            sector_used_from_budget = Decimal(0)
            if isinstance(budget_sec_exp, Mapping) and str(code_sector) in budget_sec_exp and budget_sec_exp[str(code_sector)] is not None:
                sector_used_from_budget = _decimal(budget_sec_exp[str(code_sector)]) * equity
            existing_sector = max(sector_used_from_u, sector_used_from_budget)
            rem_sector = max(sector_limit_notional - existing_sector, Decimal(0))
            remaining = min(remaining, rem_sector)

    return max(remaining, Decimal(0))


def _available_cash(
    risk_budget: Mapping[str, Any],
    persona_book: Mapping[str, Any] | None = None,
) -> Decimal:
    cash_raw = risk_budget.get("available_buying_power")
    if cash_raw is None:
        cash_raw = risk_budget.get("available_cash")
    if cash_raw is None:
        return Decimal(0)
    avail = max(_decimal(cash_raw), Decimal(0))
    equity_raw = risk_budget.get("equity")
    equity = _decimal(equity_raw) if equity_raw is not None else None

    reserve_req = Decimal(0)
    if risk_budget.get("cash_reserve_required") is not None:
        reserve_req = max(reserve_req, _decimal(risk_budget["cash_reserve_required"]))
    if risk_budget.get("min_cash_reserve") is not None:
        reserve_req = max(reserve_req, _decimal(risk_budget["min_cash_reserve"]))
    if risk_budget.get("min_cash_reserve_pct") is not None:
        base_eq = equity if (equity is not None and equity > 0) else _decimal(risk_budget.get("raw_cash") or avail)
        reserve_req = max(reserve_req, _decimal(risk_budget["min_cash_reserve_pct"]) * base_eq)

    exposures = _book_and_reservation_exposures(persona_book, risk_budget)
    cash_reserved = exposures["cash_reserved"]
    margin_reserved = exposures["margin_reserved"]
    unfolded_cash_res = exposures["unfolded_cash_reserved"]
    unfolded_margin_res = exposures["unfolded_margin_reserved"]

    if "raw_cash" in risk_budget:
        raw_cash = _decimal(risk_budget["raw_cash"])
        base_margin_eq = equity if (equity is not None and equity > 0) else raw_cash
        if risk_budget.get("free_margin") is not None:
            free_margin = min(
                _decimal(risk_budget["free_margin"]) - unfolded_margin_res,
                base_margin_eq - margin_reserved,
            )
        else:
            free_margin = base_margin_eq - margin_reserved
        raw_bp = max(min(raw_cash - cash_reserved, free_margin), Decimal(0))
        return max(min(avail, raw_bp - reserve_req), Decimal(0))

    if risk_budget.get("free_margin") is not None:
        free_margin = _decimal(risk_budget["free_margin"]) - unfolded_margin_res
        if equity is not None and equity > 0:
            free_margin = min(free_margin, equity - margin_reserved)
        unreserved = max(min(avail - unfolded_cash_res, free_margin), Decimal(0))
    elif (cash_reserved > 0 or margin_reserved > 0) and equity is not None and equity > 0:
        unreserved = max(
            min(avail - unfolded_cash_res - unfolded_margin_res, equity - cash_reserved - margin_reserved),
            Decimal(0),
        )
    else:
        unreserved = max(avail - cash_reserved - margin_reserved, Decimal(0))
    return max(unreserved - reserve_req, Decimal(0))


def _cash_candidate(
    code: str,
    *,
    price: float | None,
    price_date: str | None,
    risk_budget: Mapping[str, Any],
    persona_book: Mapping[str, Any] | None,
    source_refs: list[dict[str, Any]],
    cash_avg_daily_volume: float | None = None,
) -> dict[str, Any]:
    if price is None or price <= 0:
        max_units = 0
        source_refs = [*source_refs, {"reason": "cash_price_unavailable"}]
        margin: float | None = None
    else:
        spend = min(
            _available_cash(risk_budget, persona_book),
            _remaining_notional_for_underlying(code, persona_book, risk_budget, long_only=True),
        )
        max_units = _floor_units(spend, _decimal(price))
        margin = float(price)
    adv_val = cash_avg_daily_volume
    if adv_val is None:
        adv_map = risk_budget.get("avg_daily_volume_by_underlying")
        if isinstance(adv_map, Mapping) and code in adv_map and adv_map[code] is not None:
            try:
                adv_val = float(adv_map[code])
            except (TypeError, ValueError):
                adv_val = None
        elif risk_budget.get("avg_daily_volume") is not None:
            try:
                adv_val = float(risk_budget["avg_daily_volume"])
            except (TypeError, ValueError):
                adv_val = None
    liquidity_payload = (
        {"avg_daily_volume": float(adv_val)}
        if (adv_val is not None and not isinstance(adv_val, bool) and math.isfinite(float(adv_val)) and float(adv_val) > 0.0)
        else None
    )
    return {
        "instrument_id": f"eq:{code}",
        "kind": "cash_share",
        "underlying": code,
        "side_capability": ["long"],
        "unit": 1,
        "multiplier": 1,
        "price": price,
        "price_date": price_date,
        "price_basis": "stock_close_prev_session",
        "initial_margin_per_unit": margin,
        "maintenance_margin_per_unit": 0,
        # Share costs (broker commission, securities transaction tax) are
        # ledger-owned and not part of the TAIFEX fee schedule.
        "fee_per_unit_per_side": 0.0,
        "tax_rate_per_side": 0.0,
        "expiry": None,
        "last_trading_day": None,
        "liquidity": liquidity_payload,
        "max_units_by_risk": max_units,
        "source_refs": source_refs,
    }


def _margin_per_unit(row: Mapping[str, Any]) -> tuple[Decimal, Decimal] | None:
    """(initial, maintenance) per contract, or None when the margin row is missing."""
    price = row.get("settlement_price")
    unit = row.get("unit")
    if row.get("margin_mode") == "fixed":
        initial, maintenance = row.get("initial_margin_amount"), row.get("maintenance_margin_amount")
        if initial is None or maintenance is None:
            return None
        return _decimal(initial), _decimal(maintenance)
    initial, maintenance = row.get("initial_margin_rate"), row.get("maintenance_margin_rate")
    if initial is None or maintenance is None or price is None or unit is None:
        return None
    notional = _decimal(price) * _decimal(unit)
    return notional * _decimal(initial), notional * _decimal(maintenance)


def _future_candidate(
    row: Mapping[str, Any],
    *,
    last_day: date,
    risk_budget: Mapping[str, Any],
    persona_book: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    margins = _margin_per_unit(row)
    if margins is None:
        return None
    initial, maintenance = margins
    unit = int(row["unit"])
    price = _decimal(row["settlement_price"])
    fee = fee_row_for(str(row.get("underlying_kind") or "stock"), unit)
    code = normalize_underlying_code(str(row["underlying_code"]))
    max_units = min(
        _floor_units(_available_cash(risk_budget, persona_book), initial),
        _floor_units(
            _remaining_notional_for_underlying(code, persona_book, risk_budget, long_only=False),
            price * unit,
        ),
    )
    return {
        "instrument_id": f"ssf:{row['contract']}:{row['contract_month']}",
        "kind": "stock_future",
        "underlying": str(row["underlying_code"]),
        "side_capability": ["long", "short"],
        "unit": unit,
        "multiplier": unit,
        "price": float(price),
        "price_date": str(row["price_date"]),
        "price_basis": "futures_daily_settlement_prev_session",
        "initial_margin_per_unit": _money(initial),
        "maintenance_margin_per_unit": _money(maintenance),
        "fee_per_unit_per_side": fee.per_side_fee,
        "tax_rate_per_side": STOCK_FUTURES_TRANSACTION_TAX_RATE,
        "expiry": last_day.isoformat(),
        "last_trading_day": last_day.isoformat(),
        "liquidity": {"volume": int(row.get("volume") or 0), "open_interest": int(row.get("open_interest") or 0)},
        "max_units_by_risk": max_units,
        "source_refs": [
            {
                "dataset": SSF_DATASET,
                "partition": f"{NORMALIZED_DATASET_PATH}/date={row['price_date']}",
                "contract": row["contract"],
                "contract_month": row["contract_month"],
                "price_date": str(row["price_date"]),
                "margin_date": str(row.get("margin_date") or ""),
            }
        ],
    }


def _has_incomplete_latest_coverage(
    rows: Sequence[Mapping[str, Any]],
    code: str,
    decision_day: date,
) -> bool:
    covering_rows = [
        row
        for row in rows
        if _is_applicable_on_decision_day(row, decision_day) and _row_covers_underlying(row, code)
    ]
    if not covering_rows:
        return False
    latest_run_key = max(_snapshot_run_key(row) for row in covering_rows)
    return any(
        bool(row.get("_snapshot_truncated"))
        for row in covering_rows
        if _snapshot_run_key(row) == latest_run_key
    )


def _eligible_rows(
    rows: Sequence[Mapping[str, Any]],
    code: str,
    decision_day: date,
    holidays: Collection[date],
) -> list[tuple[Mapping[str, Any], date]]:
    """Apply the generation rules; one row (nearest month) per contract code at most."""
    newest_allowed = previous_trading_day(decision_day, holidays)
    covering_rows = [
        row
        for row in rows
        if _is_applicable_on_decision_day(row, decision_day) and _row_covers_underlying(row, code)
    ]
    if not covering_rows:
        return []
    latest_run_key = max(_snapshot_run_key(row) for row in covering_rows)
    newest_covering_rows = [row for row in covering_rows if _snapshot_run_key(row) == latest_run_key]
    if any(row.get("tombstone") or row.get("_snapshot_truncated") for row in newest_covering_rows):
        return []
    newest_absent_contracts = {
        str(contract)
        for row in newest_covering_rows
        for contract in (row.get("snapshot_absent_contracts") or ())
        if contract
    }
    newest_present_contracts: set[str] | None = None
    if any("snapshot_contracts" in row or "_file_contracts" in row for row in newest_covering_rows):
        newest_present_contracts = {
            str(contract)
            for row in newest_covering_rows
            for contract in (
                (row.get("_file_contracts") if "_file_contracts" in row else row.get("snapshot_contracts"))
                or ([row["contract"]] if row.get("contract") else ())
            )
            if contract
        }
    latest_by_contract_month: dict[
        tuple[str, str],
        tuple[tuple[date, date, float, str, float, str, int, int], Mapping[str, Any], date],
    ] = {}
    for order_index, row in enumerate(rows):
        if row.get("tombstone") or row.get("_snapshot_truncated"):
            continue
        contract = str(row.get("contract") or "")
        month = str(row.get("contract_month") or "")
        if _row_underlying_code(row) != code:
            continue
        # Adjusted codes and spread rows (``202611/202612``) are never candidates.
        if not CONTRACT_CODE.match(contract) or not CONTRACT_MONTH.match(month):
            continue
        if contract in newest_absent_contracts:
            continue
        if newest_present_contracts is not None and contract not in newest_present_contracts:
            continue
        last_day = last_trading_day(month, holidays)
        if last_day <= decision_day:
            continue
        if not _is_applicable_on_decision_day(row, decision_day):
            continue
        row_run_key = _snapshot_run_key(row)
        if row_run_key != latest_run_key and row_run_key[2:] != latest_run_key[2:]:
            continue
        sort_key = _snapshot_sort_key(row, order_index)
        cm_key = (contract, month)
        existing = latest_by_contract_month.get(cm_key)
        if existing is None or sort_key >= existing[0]:
            latest_by_contract_month[cm_key] = (sort_key, row, last_day)

    by_contract: dict[str, list[tuple[Mapping[str, Any], date]]] = {}
    for (contract, _month), (_sort_key, row, last_day) in latest_by_contract_month.items():
        by_contract.setdefault(contract, []).append((row, last_day))

    selected: list[tuple[Mapping[str, Any], date]] = []
    for contract_rows in by_contract.values():
        row, last_day = min(
            contract_rows,
            key=lambda item: (item[1], str(item[0].get("contract_month") or "")),
        )
        try:
            fresh = all(
                _as_date(row[key]) >= newest_allowed for key in ("price_date", "margin_date") if row.get(key)
            ) and bool(row.get("price_date")) and bool(row.get("margin_date"))
        except (ValueError, TypeError):
            fresh = False
        raw_price = row.get("settlement_price")
        try:
            valid_price = (
                raw_price is not None
                and not isinstance(raw_price, bool)
                and _decimal(raw_price).is_finite()
                and _decimal(raw_price) > 0
            )
        except Exception:
            valid_price = False
        if not fresh or not valid_price or not row.get("unit"):
            continue
        if int(row.get("volume") or 0) == 0 and int(row.get("open_interest") or 0) == 0:
            continue
        selected.append((row, last_day))
    return selected


def build_candidates(
    underlying: str,
    as_of_trading_day: str | date,
    persona_book: Mapping[str, Any] | None,
    risk_budget: Mapping[str, Any],
    *,
    cash_price: float | None = None,
    cash_price_date: str | None = None,
    cash_avg_daily_volume: float | None = None,
    rows_reader: RowsReader | None = None,
    normalized_root: str | Path | None = None,
    holidays: Collection[date] = (),
    decision_id: str | None = None,
) -> list[dict[str, Any]]:
    """Candidates for ``underlying`` on decision day ``as_of_trading_day``.

    ``risk_budget`` carries ``available_cash`` and ``gross_limit`` (and optionally
    ``gross_used``; otherwise ``persona_book['gross_notional']``). A missing
    figure yields ``max_units_by_risk`` 0. The cash share is returned in every
    case; futures are returned only when the normalized dataset is readable and
    the row passes the generation rules.
    """
    code = normalize_underlying_code(underlying)
    decision_day = _as_date(as_of_trading_day)
    resolved_cash_adv = cash_avg_daily_volume
    if resolved_cash_adv is None and normalized_root is not None and risk_budget.get("liquidity_constraints"):
        resolved_cash_adv = read_avg_daily_volume(
            normalized_root,
            code,
            as_of_trading_day=decision_day,
        )
    reader = rows_reader
    if reader is None and normalized_root is not None:
        root = normalized_root
        reader = lambda: read_normalized_ssf_rows(  # noqa: E731
            root,
            underlying=code,
            as_of_trading_day=decision_day,
        )
    rows = reader() if reader is not None else None
    cash_refs: list[dict[str, Any]] = []
    if not rows or _has_incomplete_latest_coverage(rows, code, decision_day):
        cash_refs.append({"reason": "candidates_unavailable", "dataset": SSF_DATASET})
    candidates = [
        _cash_candidate(
            code,
            price=cash_price,
            price_date=cash_price_date,
            risk_budget=risk_budget,
            persona_book=persona_book,
            source_refs=cash_refs,
            cash_avg_daily_volume=resolved_cash_adv,
        )
    ]
    if rows:
        for row, last_day in _eligible_rows(rows, code, decision_day, holidays):
            candidate = _future_candidate(row, last_day=last_day, risk_budget=risk_budget, persona_book=persona_book)
            if candidate is not None:
                candidates.append(candidate)
    if len(candidates) == 1 and {"reason": "candidates_unavailable", "dataset": SSF_DATASET} not in cash_refs:
        cash_refs.append({"reason": "candidates_unavailable", "dataset": SSF_DATASET})
    candidates[1:] = sorted(candidates[1:], key=lambda item: item["instrument_id"])
    if decision_id is not None:
        for candidate in candidates:
            candidate["decision_id"] = decision_id
    return candidates
