"""Session settlement and expiry settlement dataset inputs for open stock futures.

Wires the normalized ``taifex_ssf_contracts`` dataset into:
1. Producer signals for bindings whose published book holds open ``stock_future``
   positions (including HOLD and NoTrade signals), attaching the session
   ``trade_date`` and daily settlement prices (or explicit missing/stale reasons)
   so the paper runtime marks positions and evaluates maintenance margin once per
   session without inventing or reusing stale prices.
2. The paper runtime's dataset-backed final settlement source so expiring contracts
   settle at ``FinalSettlementPriceSSF`` on or after ``last_trading_day`` and stay
   ``settlement_pending`` until published.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from datetime import datetime, timezone
from typing import Callable

from services.execution.lean_runtime.performance_telemetry import (
    FUTURES_SETTLEMENT_MARK_KIND,
    MarketMark,
    expected_futures_settlement_session,
)
from services.execution.lean_runtime.instrument_candidates import (
    CONTRACT_CODE,
    CONTRACT_MONTH,
    NORMALIZED_DATASET_PATH,
    _as_date,
    _optional_date,
    _row_covers_underlying,
    _row_underlying_code,
    _snapshot_market_date,
    _snapshot_run_key,
    _snapshot_sort_key,
    normalize_underlying_code,
    parse_instrument_id,
    read_normalized_ssf_rows,
)

SSF_NORMALIZED_ROOT_ENV = "PANTHEON_TAIFEX_SSF_NORMALIZED_ROOT"


def open_stock_future_positions(
    persona_book: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Extract open ``stock_future`` positions from a published or inline persona book."""
    if persona_book is None:
        return []
    if isinstance(persona_book, Mapping):
        raw_positions = persona_book.get("positions") or ()
    elif isinstance(persona_book, Sequence) and not isinstance(persona_book, (str, bytes)):
        raw_positions = persona_book
    else:
        return []
    if not isinstance(raw_positions, Sequence) or isinstance(raw_positions, (str, bytes)):
        return []

    by_instrument: dict[str, dict[str, Any]] = {}
    for item in raw_positions:
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status == "closed":
            continue
        raw_inst_id = str(item.get("instrument_id") or "").strip()
        kind = str(item.get("kind") or "").strip()
        is_future = kind == "stock_future" or raw_inst_id.startswith("ssf:")
        if not is_future:
            continue

        qty_val: float | None = None
        if item.get("units") is not None and not isinstance(item.get("units"), bool):
            try:
                qty_val = abs(float(item["units"]))
            except (TypeError, ValueError):
                qty_val = None
        elif item.get("quantity") is not None and not isinstance(item.get("quantity"), bool):
            try:
                qty_val = abs(float(item["quantity"]))
            except (TypeError, ValueError):
                qty_val = None
        if qty_val is None or qty_val <= 1e-12:
            continue

        contract = str(item.get("contract") or "").strip().upper()
        contract_month = str(item.get("contract_month") or "").strip()
        if raw_inst_id.startswith("ssf:"):
            try:
                parsed = parse_instrument_id(raw_inst_id)
                contract = str(parsed["contract"])
                contract_month = str(parsed["contract_month"])
                inst_id = str(parsed["instrument_id"])
            except ValueError:
                continue
        elif CONTRACT_CODE.match(contract) and CONTRACT_MONTH.match(contract_month):
            inst_id = f"ssf:{contract}:{contract_month}"
        else:
            continue

        raw_underlying = item.get("underlying") or item.get("normalised_underlying") or item.get("symbol")
        underlying: str | None = None
        if raw_underlying:
            text_u = str(raw_underlying).strip()
            if text_u and not text_u.startswith("ssf:"):
                try:
                    underlying = normalize_underlying_code(text_u)
                except ValueError:
                    underlying = text_u

        by_instrument[inst_id] = {
            "instrument_id": inst_id,
            "kind": "stock_future",
            "contract": contract,
            "contract_month": contract_month,
            "underlying": underlying,
            "units": int(round(qty_val)),
        }
    return [by_instrument[k] for k in sorted(by_instrument)]


def _positive_finite_price(raw_price: Any) -> float | None:
    if raw_price is None or isinstance(raw_price, bool):
        return None
    try:
        dec = Decimal(str(raw_price))
    except Exception:
        return None
    if not dec.is_finite() or dec <= 0:
        return None
    return float(dec)


def read_session_settlement_prices_for_open_futures(
    open_positions: Sequence[Mapping[str, Any]],
    trade_date: str | date,
    *,
    normalized_root: str | Path | None = None,
    partition_horizon: str | date | None = None,
    historical_session: bool = False,
) -> dict[str, Any]:
    """Read daily settlement prices for open futures contracts on ``trade_date``.

    ``historical_session`` is for grading a session that is no longer the
    newest one: the row used is the newest row whose ``price_date`` is exactly
    ``trade_date`` instead of the newest row overall (which would be a later
    session and therefore "stale").  A session with no such row is still
    reported missing or stale, never filled from another date.

    ``partition_horizon`` is the newest partition date that may be read; it
    defaults to ``trade_date``. A valuation after the session passes the
    current day because the ingest run that publishes the D-1 settlement lands
    in the current day's partition while its rows carry the session
    ``price_date``.

    Prices must come from the dataset snapshot whose ``price_date`` equals
    ``trade_date``. When a contract's price is missing, unreadable, tombstoned,
    or comes only from an older session, no stale or invented price is emitted
    and an explicit per-contract reason is recorded.
    """
    session_day = _as_date(trade_date)
    session_iso = session_day.isoformat()
    positions = open_stock_future_positions(open_positions)
    contracts = [pos["instrument_id"] for pos in positions]
    prices: dict[str, float] = {}
    reasons: dict[str, str] = {}

    if not positions:
        return {
            "trade_date": session_iso,
            "open_futures_contracts": [],
            "session_settlement_prices": {},
            "session_settlement_price_reasons": {},
            "futures_mark_unavailable_reason": None,
        }

    effective_root_str = (
        str(normalized_root).strip()
        if normalized_root is not None
        else os.getenv(SSF_NORMALIZED_ROOT_ENV, "").strip()
    )
    if not effective_root_str:
        for inst_id in contracts:
            reasons[inst_id] = "dataset_root_unset"
        return {
            "trade_date": session_iso,
            "open_futures_contracts": contracts,
            "session_settlement_prices": {},
            "session_settlement_price_reasons": reasons,
            "futures_mark_unavailable_reason": "dataset_root_unset",
        }

    root_path = Path(effective_root_str)
    dataset_dir = root_path / NORMALIZED_DATASET_PATH
    if not dataset_dir.is_dir():
        for inst_id in contracts:
            reasons[inst_id] = "dataset_unreadable"
        return {
            "trade_date": session_iso,
            "open_futures_contracts": contracts,
            "session_settlement_prices": {},
            "session_settlement_price_reasons": reasons,
            "futures_mark_unavailable_reason": "dataset_unreadable",
        }

    horizon_day = _as_date(partition_horizon) if partition_horizon is not None else session_day
    session_partition = dataset_dir / f"date={horizon_day.isoformat()}"
    rows = read_normalized_ssf_rows(
        root_path,
        as_of_trading_day=horizon_day,
    )
    if rows is None:
        for inst_id in contracts:
            reasons[inst_id] = "dataset_unreadable"
        return {
            "trade_date": session_iso,
            "open_futures_contracts": contracts,
            "session_settlement_prices": {},
            "session_settlement_price_reasons": reasons,
            "futures_mark_unavailable_reason": "dataset_unreadable",
        }
    if not rows:
        reason_code = (
            "session_partition_unreadable"
            if session_partition.is_dir()
            else "session_partition_missing"
        )
        for inst_id in contracts:
            reasons[inst_id] = reason_code
        return {
            "trade_date": session_iso,
            "open_futures_contracts": contracts,
            "session_settlement_prices": {},
            "session_settlement_price_reasons": reasons,
            "futures_mark_unavailable_reason": reason_code,
        }

    for pos in positions:
        inst_id = pos["instrument_id"]
        contract = pos["contract"]
        contract_month = pos["contract_month"]
        underlying = pos.get("underlying")

        covering_rows = [
            row
            for row in rows
            if str(row.get("contract") or "").strip().upper() == contract
            or (underlying and _row_covers_underlying(row, underlying))
        ]
        if covering_rows:
            latest_run_key = max(_snapshot_run_key(r) for r in covering_rows)
            newest_covering = [r for r in covering_rows if _snapshot_run_key(r) == latest_run_key]
            if any(r.get("tombstone") for r in newest_covering):
                reasons[inst_id] = "contract_tombstoned"
                continue
            if any(r.get("_snapshot_truncated") for r in newest_covering):
                reasons[inst_id] = "snapshot_truncated"
                continue

        matching_rows: list[tuple[tuple[Any, ...], Mapping[str, Any]]] = []
        other_session_rows = 0
        for idx, row in enumerate(rows):
            if row.get("tombstone") or row.get("_snapshot_truncated"):
                continue
            row_contract = str(row.get("contract") or "").strip().upper()
            row_month = str(row.get("contract_month") or "").strip()
            if row_contract == contract and row_month == contract_month:
                if historical_session and (
                    (_optional_date(row.get("price_date")) or _snapshot_market_date(row))
                    != session_day
                ):
                    other_session_rows += 1
                    continue
                matching_rows.append((_snapshot_sort_key(row, idx), row))

        if not matching_rows and other_session_rows:
            reasons[inst_id] = "settlement_price_stale"
            continue
        if not matching_rows:
            reasons[inst_id] = (
                "settlement_price_missing"
                if session_partition.is_dir()
                else "session_partition_missing"
            )
            continue

        _, latest_row = max(matching_rows, key=lambda item: item[0])
        row_price_date = _optional_date(latest_row.get("price_date")) or _snapshot_market_date(latest_row)
        if row_price_date != session_day:
            reasons[inst_id] = "settlement_price_stale"
            continue

        valid_price = _positive_finite_price(latest_row.get("settlement_price"))
        if valid_price is None:
            reasons[inst_id] = "settlement_price_missing"
            continue

        prices[inst_id] = valid_price

    overall_reason: str | None = None
    if reasons:
        overall_reason = "; ".join(f"{cid}:{reasons[cid]}" for cid in sorted(reasons))

    return {
        "trade_date": session_iso,
        "open_futures_contracts": contracts,
        "session_settlement_prices": prices,
        "session_settlement_price_reasons": reasons,
        "futures_mark_unavailable_reason": overall_reason,
    }


def attach_futures_session_inputs(
    decision: dict[str, Any],
    persona_book: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
    *,
    trade_date: str | date | None = None,
    normalized_root: str | Path | None = None,
) -> dict[str, Any]:
    """Attach session ``trade_date`` and open-contract daily settlement prices to ``decision``."""
    open_positions = open_stock_future_positions(persona_book)
    if not open_positions:
        return decision

    metadata = decision.setdefault("metadata", {})
    raw_trade_date = (
        trade_date
        or metadata.get("trade_date")
        or metadata.get("as_of_trading_day")
        or str(decision.get("timestamp") or "")[:10]
    )
    parsed_day = _optional_date(raw_trade_date)
    if parsed_day is None:
        return decision

    session_info = read_session_settlement_prices_for_open_futures(
        open_positions,
        parsed_day,
        normalized_root=normalized_root,
    )
    metadata["trade_date"] = session_info["trade_date"]
    metadata["open_futures_contracts"] = session_info["open_futures_contracts"]
    metadata["session_settlement_prices"] = session_info["session_settlement_prices"]
    metadata["session_settlement_price_reasons"] = session_info["session_settlement_price_reasons"]
    if session_info["futures_mark_unavailable_reason"] is not None:
        metadata["futures_mark_unavailable_reason"] = session_info["futures_mark_unavailable_reason"]
    else:
        metadata.pop("futures_mark_unavailable_reason", None)
    return decision


@dataclass(frozen=True)
class DatasetFinalSettlementSource:
    """Dataset-backed ``FinalSettlementPriceSSF`` source reading ``taifex_ssf_contracts``."""

    normalized_root: str | Path | None = None
    env_var: str = SSF_NORMALIZED_ROOT_ENV

    def resolve_root(self) -> Path | None:
        if self.normalized_root is not None:
            text = str(self.normalized_root).strip()
            return Path(text) if text else None
        raw = os.getenv(self.env_var, "").strip()
        return Path(raw) if raw else None

    def rows_for(
        self,
        contract: str | None = None,
        contract_month: str | None = None,
        *,
        settlement_day: str | date | None = None,
        as_of_trading_day: str | date | None = None,
    ) -> list[dict[str, Any]]:
        root = self.resolve_root()
        if root is None:
            return []
        effective_day = as_of_trading_day if as_of_trading_day is not None else settlement_day
        rows = read_normalized_ssf_rows(
            root,
            as_of_trading_day=effective_day,
        )
        if not rows:
            return []
        # Return newest rows first so lookup_final_settlement_price inspects the
        # latest partition/snapshot before older snapshots.
        return list(reversed(rows))

    def __call__(
        self,
        contract: str | None = None,
        contract_month: str | None = None,
        *,
        settlement_day: str | date | None = None,
        as_of_trading_day: str | date | None = None,
    ) -> list[dict[str, Any]]:
        return self.rows_for(
            contract,
            contract_month,
            settlement_day=settlement_day,
            as_of_trading_day=as_of_trading_day,
        )


def final_settlement_source_from_env(
    normalized_root: str | Path | None = None,
) -> DatasetFinalSettlementSource:
    """Build the default dataset-backed final settlement source for the paper runtime."""
    effective_root = (
        normalized_root
        if normalized_root is not None
        else (os.getenv(SSF_NORMALIZED_ROOT_ENV, "").strip() or None)
    )
    return DatasetFinalSettlementSource(normalized_root=effective_root)


class DatasetFuturesMarkProvider:
    """Mark open stock futures from the dataset's D-1 daily settlement.

    The price is read through ``read_session_settlement_prices_for_open_futures``,
    the same seam the signal producer uses to feed ``MarkFuturesSession``, so
    valuation and the margin check share one value per contract and session.
    A contract with no fresh, non-tombstone row yields no mark and a per-contract
    reason; the fill price or an older session's price is never substituted.
    """

    def __init__(
        self,
        normalized_root: str | Path | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._normalized_root = normalized_root
        self._now = now or (lambda: datetime.now(timezone.utc))

    def resolve(
        self, positions: Sequence[Mapping[str, Any]]
    ) -> tuple[dict[str, MarketMark], dict[str, Any]]:
        now = self._now()
        session_day = expected_futures_settlement_session(now)
        info = read_session_settlement_prices_for_open_futures(
            positions,
            session_day,
            normalized_root=self._normalized_root,
            partition_horizon=now.astimezone(timezone(timedelta(hours=8))).date(),
        )
        session_iso = info["trade_date"]
        marks: dict[str, MarketMark] = {}
        for inst_id, price in info["session_settlement_prices"].items():
            marks[inst_id] = MarketMark(
                symbol=inst_id,
                price=float(price),
                # RFC 3339 instant of the session's 13:30 Asia/Taipei close, so
                # the telemetry projection accepts it; the date is the session.
                as_of=f"{session_iso}T05:30:00Z",
                source_ref=(
                    f"dataset:{NORMALIZED_DATASET_PATH}/price_date={session_iso}"
                    f"#{inst_id}"
                ),
                lineage={"mark_kind": FUTURES_SETTLEMENT_MARK_KIND},
            )
        diagnostic = {
            "source": "taifex_ssf_contracts_daily_settlement",
            "session": session_iso,
            "open_contracts": list(info["open_futures_contracts"]),
            "resolved_symbols": sorted(marks),
            "reasons": dict(info["session_settlement_price_reasons"]),
            "unavailable_reason": info["futures_mark_unavailable_reason"],
        }
        return marks, diagnostic
