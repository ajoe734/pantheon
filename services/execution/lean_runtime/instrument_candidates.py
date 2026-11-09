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
import re
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

RowsReader = Callable[[], "Sequence[Mapping[str, Any]] | None"]


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
    base = Path(root) / NORMALIZED_DATASET_PATH
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
        for file_path in sorted(partition.glob("*.jsonl")):
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
) -> dict[str, Any] | None:
    """Resolve ``FinalSettlementPriceSSF`` for ``(contract, contract_month)``.

    Accepts raw ``FinalSettlementPriceSSF`` rows, a payload mapping containing
    ``FinalSettlementPriceSSF``, normalized SSF rows carrying
    ``last_final_settlement``, or an explicit mapping keyed by
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
    if isinstance(source, Mapping):
        if "FinalSettlementPriceSSF" in source:
            return lookup_final_settlement_price(
                clean_contract,
                clean_month,
                source.get("FinalSettlementPriceSSF"),
                settlement_day=settlement_day,
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


def _remaining_gross(persona_book: Mapping[str, Any] | None, risk_budget: Mapping[str, Any]) -> Decimal:
    """Fail closed: a missing limit leaves nothing to spend."""
    limit = risk_budget.get("gross_limit")
    if limit is None:
        return Decimal(0)
    leverage = risk_budget.get("max_leverage")
    equity = risk_budget.get("equity")
    if leverage is not None and equity is not None:
        # ``max_leverage`` is a multiple of equity; the tighter bound wins.
        limit = min(_decimal(limit), _decimal(leverage) * _decimal(equity))
    used = risk_budget.get("gross_used")
    if used is None:
        used = (persona_book or {}).get("gross_notional", 0)
    return max(_decimal(limit) - _decimal(used or 0), Decimal(0))


def _available_cash(risk_budget: Mapping[str, Any]) -> Decimal:
    cash = risk_budget.get("available_cash")
    return Decimal(0) if cash is None else max(_decimal(cash), Decimal(0))


def _cash_candidate(
    code: str,
    *,
    price: float | None,
    price_date: str | None,
    risk_budget: Mapping[str, Any],
    persona_book: Mapping[str, Any] | None,
    source_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    if price is None or price <= 0:
        max_units = 0
        source_refs = [*source_refs, {"reason": "cash_price_unavailable"}]
        margin: float | None = None
    else:
        spend = min(_available_cash(risk_budget), _remaining_gross(persona_book, risk_budget))
        max_units = _floor_units(spend, _decimal(price))
        margin = float(price)
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
        "liquidity": None,
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
    max_units = min(
        _floor_units(_available_cash(risk_budget), initial),
        _floor_units(_remaining_gross(persona_book, risk_budget), price * unit),
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
        )
    ]
    if rows:
        for row, last_day in _eligible_rows(rows, code, decision_day, holidays):
            candidate = _future_candidate(row, last_day=last_day, risk_budget=risk_budget, persona_book=persona_book)
            if candidate is not None:
                candidates.append(candidate)
    candidates[1:] = sorted(candidates[1:], key=lambda item: item["instrument_id"])
    if decision_id is not None:
        for candidate in candidates:
            candidate["decision_id"] = decision_id
    return candidates
