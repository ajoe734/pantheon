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
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Callable, Collection, Mapping, Sequence

from services.execution.lean_runtime.taifex_fee_schedule import (
    STOCK_FUTURES_TRANSACTION_TAX_RATE,
    fee_row_for,
)
from services.execution.shioaji_adapter import normalize_taiwan_symbol

SSF_DATASET = "taifex_ssf_contracts"
NORMALIZED_DATASET_PATH = "normalized/taifex_ssf_contracts"
CONTRACT_CODE = re.compile(r"^[A-Z]{2}F$")
CONTRACT_MONTH = re.compile(r"^\d{6}$")
MAX_PARTITIONS = 3
MAX_FILES = 6
MAX_ROWS = 20000
MAX_FILE_BYTES = 16 * 1024 * 1024

RowsReader = Callable[[], "Sequence[Mapping[str, Any]] | None"]


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


def read_normalized_ssf_rows(
    root: str | Path,
    *,
    max_partitions: int = MAX_PARTITIONS,
    max_files: int = MAX_FILES,
    max_rows: int = MAX_ROWS,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> list[dict[str, Any]] | None:
    """Read the newest normalized partitions of ``taifex_ssf_contracts``.

    Layout is the one ``MarketDataStorageWriter`` writes:
    ``<root>/normalized/taifex_ssf_contracts/date=YYYY-MM-DD/<run>.jsonl`` with
    the row under ``metadata.normalized_row``. Returns ``None`` when the dataset
    directory does not exist; reading stops at the file and row limits.
    """
    base = Path(root) / NORMALIZED_DATASET_PATH
    if not base.is_dir():
        return None
    partitions = sorted(
        (path for path in base.iterdir() if path.is_dir() and path.name.startswith("date=")),
        key=lambda path: path.name,
        reverse=True,
    )[:max_partitions]
    rows_newest_first: list[dict[str, Any]] = []
    files_read = 0
    for partition in partitions:
        for file_path in sorted(partition.glob("*.jsonl"), reverse=True):
            if files_read >= max_files or len(rows_newest_first) >= max_rows:
                return _finalize_disk_rows(rows_newest_first, files_read)
            files_read += 1
            if file_path.stat().st_size > max_file_bytes:
                continue
            with file_path.open("r", encoding="utf-8") as handle:
                lines = handle.readlines()
            parsed_file_rows: list[dict[str, Any]] = []
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                row = (record.get("metadata") or {}).get("normalized_row")
                if isinstance(row, Mapping):
                    parsed_file_rows.append(dict(row))
            file_contracts = sorted({str(r.get("contract") or "") for r in parsed_file_rows if r.get("contract")})
            for item in parsed_file_rows:
                if len(rows_newest_first) >= max_rows:
                    return _finalize_disk_rows(rows_newest_first, files_read)
                item["_reverse_file_idx"] = files_read
                item.setdefault("_file_contracts", file_contracts)
                rows_newest_first.append(item)
    return _finalize_disk_rows(rows_newest_first, files_read)


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if re.fullmatch(r"\d{7}", text):
        text = f"{int(text[:3]) + 1911:04d}-{text[3:5]}-{text[5:7]}"
    elif re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
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
) -> tuple[date, float, str, float, str, int]:
    market_date = _snapshot_market_date(row)
    margin_date = _snapshot_margin_date(row)
    effective_market_date = market_date or margin_date or date.min
    avail_epoch, avail_raw = _optional_timestamp(row.get("available_time"))
    ingest_epoch, ingest_raw = _optional_timestamp(row.get("ingest_time"))
    return (
        effective_market_date,
        avail_epoch,
        avail_raw,
        ingest_epoch,
        ingest_raw,
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
        int(row.get("_file_run_seq") or 0),
        order_index,
    )


def _is_trading_day(day: date, holidays: Collection[date]) -> bool:
    return day.weekday() < 5 and day not in holidays


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
    newest_absent_contracts = {
        str(contract)
        for row in newest_covering_rows
        for contract in (row.get("snapshot_absent_contracts") or ())
        if contract
    }
    latest_by_contract_month: dict[
        tuple[str, str],
        tuple[tuple[date, date, float, str, float, str, int, int], Mapping[str, Any], date],
    ] = {}
    for order_index, row in enumerate(rows):
        if row.get("tombstone"):
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
        last_day = last_trading_day(month, holidays)
        if last_day <= decision_day:
            continue
        if not _is_applicable_on_decision_day(row, decision_day):
            continue
        if _snapshot_run_key(row) != latest_run_key:
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
        if not fresh or row.get("settlement_price") is None or not row.get("unit"):
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
        reader = lambda: read_normalized_ssf_rows(root)  # noqa: E731
    rows = reader() if reader is not None else None
    cash_refs: list[dict[str, Any]] = []
    if not rows:
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
