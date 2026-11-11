"""Governed vectorbt backtesting adapter for Pantheon Research Plane.

Governance boundary:
- Input: Pantheon-governed OHLCV dataset with lineage refs
- Output: registry-ready backtest_result artifact (artifact_state=draft) + registry_entry
- vectorbt never writes directly to registry, runtime, or LEAN.
- Real backend is gated behind PANTHEON_VECTORBT_BACKEND=real environment flag.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

VECTORBT_VERSION_PIN = "1.1.1"
PRIMARY_BACKEND = "vectorbt_portfolio"
STUB_BACKEND = "stub_backtest"
REQUIRED_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")
MIN_INSTRUMENTS = 2
MIN_BARS = 30
ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Taiwan statutory transaction cost schedule (cited in task evidence.json):
# - Broker commission ceiling: 0.1425% (14.25 bps) per fill on both buy and sell
#   (TWSE Operating Rules Art. 85 / TPEx Brokerage Fee Schedule ceiling).
# - Securities transaction tax: 0.3% (30 bps = 3‰) on ordinary equity sell fills
#   (Taiwan Securities Transaction Tax Act Art. 2 / 證券交易稅條例第2條), and
#   0.1% (10 bps = 1‰) on ETF sell fills such as 0050 (Art. 2-2).
TAIWAN_BROKER_COMMISSION_RATE = 0.001425
TAIWAN_EQUITY_TRANSACTION_TAX_RATE = 0.003
TAIWAN_ETF_TRANSACTION_TAX_RATE = 0.001
MIN_PIT_HISTORY_CALENDAR_DAYS = 730
MIN_PIT_HISTORY_TRADING_DAYS = 480
INSUFFICIENT_HISTORY_STATUS = "insufficient_history"

TAIWAN_COST_RATE_CITATIONS: dict[str, str] = {
    "broker_commission": (
        "TWSE Operating Rules Art. 85 / TPEx Brokerage Fee Schedule statutory ceiling: "
        "0.1425% (0.001425) per fill on buy and sell orders."
    ),
    "securities_transaction_tax_equity": (
        "Taiwan Securities Transaction Tax Act Art. 2 (證券交易稅條例第2條): "
        "0.3% (0.003) on corporate stock sell fills."
    ),
    "securities_transaction_tax_etf": (
        "Taiwan Securities Transaction Tax Act Art. 2-2: "
        "0.1% (0.001) on beneficiary certificate / ETF (e.g. 0050) sell fills."
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


class VectorbtWorkflowError(ValueError):
    """Raised when a governed vectorbt workflow cannot be built safely."""


@dataclass(frozen=True)
class PreparedVectorbtDataset:
    dataset_id: str
    strategy_id: str
    source_dataset_refs: tuple[str, ...]
    source_strategy_spec_id: str | None
    instruments: tuple[str, ...]
    bars_per_instrument: dict[str, int]
    ohlcv_by_instrument: dict[str, tuple[tuple[float, float, float, float, float], ...]]
    data_frequency: str
    num_instruments: int
    total_bars: int
    dates_by_instrument: dict[str, tuple[str, ...]] = field(default_factory=dict)
    features_by_instrument: dict[str, dict[str, Any]] = field(default_factory=dict)

    def dataset_summary(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "strategy_id": self.strategy_id,
            "source_dataset_refs": list(self.source_dataset_refs),
            "source_strategy_spec_id": self.source_strategy_spec_id,
            "num_instruments": self.num_instruments,
            "total_bars": self.total_bars,
            "bars_per_instrument": dict(self.bars_per_instrument),
            "data_frequency": self.data_frequency,
            "instruments": list(self.instruments),
        }


@dataclass(frozen=True)
class BacktestConfig:
    version: str = "1.0.0"
    requested_by: str = "Claude"
    strategy_params: dict[str, Any] = field(default_factory=dict)
    # Canonical (frozen) StrategyArtifact to evaluate through the Registry
    # interpreter.  When unset the legacy unbound MA-crossover path runs.
    strategy_artifact: dict[str, Any] | None = None
    init_cash: float = 100_000.0
    fees: float = 0.001
    commission_rate: float = TAIWAN_BROKER_COMMISSION_RATE
    transaction_tax_rate: float = TAIWAN_EQUITY_TRANSACTION_TAX_RATE
    etf_transaction_tax_rate: float = TAIWAN_ETF_TRANSACTION_TAX_RATE
    apply_taiwan_fill_costs: bool = True
    storage_backend: str = "object_store"
    storage_path_template: str = "research/vectorbt/{strategy_id}/{version}/artifact.json"


@dataclass(frozen=True)
class BacktestRunResult:
    backend: str
    run_id: str
    per_instrument_metrics: dict[str, dict[str, Any]]
    aggregate_metrics: dict[str, Any]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class VectorbtRunResult:
    prepared_dataset: PreparedVectorbtDataset
    backtest_result: BacktestRunResult
    artifact_bundle: dict[str, Any]
    registry_entry: dict[str, Any]


class VectorbtBackendProtocol(Protocol):
    def run(
        self, dataset: PreparedVectorbtDataset, config: BacktestConfig
    ) -> BacktestRunResult: ...


class GovernedVectorbtInputAdapter:
    """Validates and normalizes OHLCV input before it reaches vectorbt.

    Governance rules:
    - All input must carry governed source_dataset_refs (no ad-hoc files).
    - Missing OHLCV fields are rejected.
    - Each instrument must have at least MIN_BARS bars.
    - At least MIN_INSTRUMENTS instruments are required.
    """

    def prepare(self, dataset: Mapping[str, Any]) -> PreparedVectorbtDataset:
        dataset_id = self._req_str(dataset, "dataset_id")
        strategy_id = self._req_str(dataset, "strategy_id")
        source_dataset_refs = self._normalize_refs(dataset)
        source_strategy_spec_id = self._opt_str(dataset.get("source_strategy_spec_id"))
        data_frequency = self._opt_str(dataset.get("data_frequency")) or "daily"

        records = dataset.get("records")
        if not isinstance(records, Sequence) or not records:
            raise VectorbtWorkflowError("dataset.records must be a non-empty list of OHLCV dicts")

        # Collect normalized ISO dates plus OHLCV tuples so sorting does not depend on input order.
        instrument_pairs: dict[
            str, list[tuple[str, tuple[float, float, float, float, float], dict[str, Any]]]
        ] = {}
        for rec in records:
            if not isinstance(rec, Mapping):
                raise VectorbtWorkflowError("Each record must be a mapping")
            instrument = self._req_str(rec, "instrument")
            date_val = self._req_iso_date(rec, "date", instrument)
            bar: list[float] = []
            for f in REQUIRED_OHLCV_FIELDS:
                v = rec.get(f)
                if not isinstance(v, (int, float)):
                    raise VectorbtWorkflowError(
                        f"record for {instrument} missing numeric field '{f}'"
                    )
                bar.append(float(v))
            extra_rec_fields: dict[str, Any] = {}
            for k, v in rec.items():
                if k not in {"instrument", "date", *REQUIRED_OHLCV_FIELDS} and isinstance(
                    v, (int, float)
                ) and not isinstance(v, bool):
                    extra_rec_fields[str(k)] = float(v)
            if isinstance(rec.get("features"), Mapping):
                for fk, fv in rec["features"].items():
                    if isinstance(fv, (int, float)) and not isinstance(fv, bool):
                        extra_rec_fields[str(fk)] = float(fv)
            instrument_pairs.setdefault(instrument, []).append(
                (date_val, (bar[0], bar[1], bar[2], bar[3], bar[4]), extra_rec_fields)
            )

        instruments = tuple(sorted(instrument_pairs.keys()))
        if len(instruments) < MIN_INSTRUMENTS:
            raise VectorbtWorkflowError(
                f"dataset must have at least {MIN_INSTRUMENTS} instruments; got {len(instruments)}"
            )

        raw_dataset_features = dataset.get("features_by_instrument") or dataset.get("features")
        bars_per_instrument: dict[str, int] = {}
        ohlcv_by_instrument: dict[str, tuple[tuple[float, float, float, float, float], ...]] = {}
        dates_by_instrument: dict[str, tuple[str, ...]] = {}
        features_by_instrument: dict[str, dict[str, Any]] = {}
        for inst in instruments:
            # Dates are normalized to YYYY-MM-DD, so lexical order matches chronological order.
            sorted_pairs = sorted(instrument_pairs[inst], key=lambda p: p[0])
            dates = [p[0] for p in sorted_pairs]
            bars = [p[1] for p in sorted_pairs]
            extras = [p[2] for p in sorted_pairs]
            if len(bars) < MIN_BARS:
                raise VectorbtWorkflowError(
                    f"instrument {inst} has {len(bars)} bars; minimum is {MIN_BARS}"
                )
            bars_per_instrument[inst] = len(bars)
            ohlcv_by_instrument[inst] = tuple(bars)
            dates_by_instrument[inst] = tuple(dates)

            inst_features: dict[str, Any] = {
                "open": [b[0] for b in bars],
                "high": [b[1] for b in bars],
                "low": [b[2] for b in bars],
                "close": [b[3] for b in bars],
                "volume": [b[4] for b in bars],
            }
            extra_keys = {k for ex in extras for k in ex.keys()}
            for ek in sorted(extra_keys):
                if all(ek in ex for ex in extras):
                    inst_features[ek] = [ex[ek] for ex in extras]
            if isinstance(raw_dataset_features, Mapping):
                if inst in raw_dataset_features and isinstance(raw_dataset_features[inst], Mapping):
                    for fk, fv in raw_dataset_features[inst].items():
                        inst_features[str(fk)] = fv
                else:
                    for fk, fv in raw_dataset_features.items():
                        if fk not in instruments:
                            inst_features[str(fk)] = fv
            features_by_instrument[inst] = inst_features

        total_bars = sum(bars_per_instrument.values())
        return PreparedVectorbtDataset(
            dataset_id=dataset_id,
            strategy_id=strategy_id,
            source_dataset_refs=tuple(source_dataset_refs),
            source_strategy_spec_id=source_strategy_spec_id,
            instruments=instruments,
            bars_per_instrument=bars_per_instrument,
            ohlcv_by_instrument=ohlcv_by_instrument,
            data_frequency=data_frequency,
            num_instruments=len(instruments),
            total_bars=total_bars,
            dates_by_instrument=dates_by_instrument,
            features_by_instrument=features_by_instrument,
        )

    def _normalize_refs(self, dataset: Mapping[str, Any]) -> list[str]:
        refs = dataset.get("source_dataset_refs")
        if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes)):
            result = [r for r in refs if isinstance(r, str) and r.strip()]
            if result:
                return result
        single = self._opt_str(dataset.get("source_dataset_ref"))
        if single:
            return [single]
        raise VectorbtWorkflowError("dataset must include source_dataset_ref or source_dataset_refs")

    def _req_str(self, payload: Mapping[str, Any], key: str) -> str:
        value = self._opt_str(payload.get(key))
        if not value:
            raise VectorbtWorkflowError(f"'{key}' must be a non-empty string")
        return value

    def _req_iso_date(self, payload: Mapping[str, Any], key: str, instrument: str) -> str:
        value = self._opt_str(payload.get(key))
        if not value:
            raise VectorbtWorkflowError(f"record for {instrument} missing non-empty '{key}'")
        if not ISO_DATE_PATTERN.fullmatch(value):
            raise VectorbtWorkflowError(
                f"record for {instrument} has invalid '{key}'; expected YYYY-MM-DD"
            )
        try:
            return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise VectorbtWorkflowError(
                f"record for {instrument} has invalid '{key}'; expected YYYY-MM-DD"
            ) from exc

    def _opt_str(self, value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise VectorbtWorkflowError(f"expected string, got {type(value).__name__}")
        return value.strip()


def _sma(closes: list[float], window: int) -> list[float | None]:
    """Simple moving average — returns None for positions without enough history."""
    result: list[float | None] = []
    for i in range(len(closes)):
        if i < window - 1:
            result.append(None)
        else:
            result.append(sum(closes[i - window + 1 : i + 1]) / window)
    return result


def _compute_instrument_metrics(
    bars: tuple[tuple[float, float, float, float, float], ...],
    config: BacktestConfig,
    short_window: int,
    long_window: int,
) -> dict[str, Any]:
    """MA-crossover backtest on a single instrument's OHLCV bars."""
    closes = [b[3] for b in bars]
    short_ma = _sma(closes, short_window)
    long_ma = _sma(closes, long_window)

    cash = config.init_cash
    shares = 0.0
    entry_price = 0.0
    portfolio_values: list[float] = []
    trade_count = 0

    for i, (o, h, l, c, v) in enumerate(bars):
        s = short_ma[i]
        lg = long_ma[i]
        if s is not None and lg is not None:
            in_position = shares > 0
            signal_long = s > lg
            if signal_long and not in_position:
                # Enter long at close
                buy_cost = cash * (1.0 - config.fees)
                shares = buy_cost / c
                cash -= buy_cost
                entry_price = c
                trade_count += 1
            elif not signal_long and in_position:
                # Exit at close
                proceeds = shares * c * (1.0 - config.fees)
                cash += proceeds
                shares = 0.0
                trade_count += 1
        portfolio_values.append(cash + shares * c)

    # Close any open position at last bar
    if shares > 0 and bars:
        last_close = bars[-1][3]
        cash += shares * last_close * (1.0 - config.fees)
        shares = 0.0

    final_value = cash
    total_return = (final_value - config.init_cash) / config.init_cash
    n = len(portfolio_values)

    # Daily returns for Sharpe
    daily_rets: list[float] = []
    for i in range(1, n):
        prev = portfolio_values[i - 1]
        if prev > 0:
            daily_rets.append((portfolio_values[i] - prev) / prev)

    mean_ret = sum(daily_rets) / len(daily_rets) if daily_rets else 0.0
    variance = (
        sum((r - mean_ret) ** 2 for r in daily_rets) / len(daily_rets) if daily_rets else 0.0
    )
    std_ret = variance ** 0.5
    sharpe = (mean_ret / std_ret) * (252 ** 0.5) if std_ret > 1e-12 else 0.0

    # Max drawdown
    peak = config.init_cash
    max_dd = 0.0
    for pv in portfolio_values:
        if pv > peak:
            peak = pv
        dd = (peak - pv) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    return {
        "total_return": round(total_return, 6),
        "sharpe_ratio": round(sharpe, 6),
        "max_drawdown": round(max_dd, 6),
        "final_portfolio_value": round(final_value, 4),
        "trade_count": trade_count,
        "num_bars": len(bars),
    }


class StubVectorbtBackend:
    """Deterministic stub for CI and smoke tests.

    Runs the MA-crossover logic in pure Python with no external dependencies.
    """

    def run(self, dataset: PreparedVectorbtDataset, config: BacktestConfig) -> BacktestRunResult:
        if config.strategy_artifact is not None:
            raise VectorbtWorkflowError(
                "Stub backend cannot evaluate a canonical strategy artifact; "
                "use VectorbtBackend with PANTHEON_VECTORBT_BACKEND=real."
            )
        short_window = config.strategy_params.get("short_window", 5)
        long_window = config.strategy_params.get("long_window", 20)
        run_id = f"vbt-stub-{uuid.uuid4().hex[:12]}"
        per_instrument: dict[str, dict[str, Any]] = {}
        for inst in dataset.instruments:
            bars = dataset.ohlcv_by_instrument[inst]
            per_instrument[inst] = _compute_instrument_metrics(bars, config, short_window, long_window)

        # Aggregate: mean of per-instrument metrics
        returns = [m["total_return"] for m in per_instrument.values()]
        sharpes = [m["sharpe_ratio"] for m in per_instrument.values()]
        drawdowns = [m["max_drawdown"] for m in per_instrument.values()]
        mean_return = sum(returns) / len(returns) if returns else 0.0
        mean_sharpe = sum(sharpes) / len(sharpes) if sharpes else 0.0
        mean_drawdown = sum(drawdowns) / len(drawdowns) if drawdowns else 0.0
        total_trades = sum(m["trade_count"] for m in per_instrument.values())

        aggregate: dict[str, Any] = {
            "num_instruments": dataset.num_instruments,
            "mean_total_return": round(mean_return, 6),
            "mean_sharpe_ratio": round(mean_sharpe, 6),
            "mean_max_drawdown": round(mean_drawdown, 6),
            "total_trades": total_trades,
        }
        return BacktestRunResult(
            backend=STUB_BACKEND,
            run_id=run_id,
            per_instrument_metrics=per_instrument,
            aggregate_metrics=aggregate,
            notes=("Stub backend is intended for governed smoke tests and packaging validation.",),
        )

NO_TRADING_ACTIVITY = "no_trading_activity"
ZERO_RETURN_VARIANCE = "zero_return_variance"


def _sharpe_undefined_reason(metrics: Mapping[str, Any]) -> str | None:
    """Name why a non-finite Sharpe is a legitimate zero-activity outcome.

    Sharpe is 0/0 when nothing traded or returns never varied. A non-finite
    Sharpe from an instrument that traded and had return variance stays
    unexplained so downstream consumers keep rejecting it fail-closed.
    """

    if math.isfinite(metrics["sharpe_ratio"]):
        return None
    if metrics["trade_count"] == 0:
        return NO_TRADING_ACTIVITY
    if metrics["total_return"] == 0 and metrics["max_drawdown"] == 0:
        return ZERO_RETURN_VARIANCE
    return None


def _annotate_sharpe_reason(metrics: dict[str, Any]) -> None:
    reason = _sharpe_undefined_reason(metrics)
    if reason is not None:
        metrics["sharpe_undefined_reason"] = reason


def _aggregate_sharpe_reason(per_instrument: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate reason only when every non-finite Sharpe is explained."""

    reasons: set[str] = set()
    for metrics in per_instrument.values():
        if math.isfinite(metrics["sharpe_ratio"]):
            continue
        reason = metrics.get("sharpe_undefined_reason")
        if reason is None:
            return {}
        reasons.add(reason)
    if not reasons:
        return {}
    return {"sharpe_undefined_reason": NO_TRADING_ACTIVITY if NO_TRADING_ACTIVITY in reasons else ZERO_RETURN_VARIANCE}


def _instrument_tax_rate(instrument: str, config: BacktestConfig) -> float:
    """Resolve the Taiwan securities transaction tax rate for ``instrument``."""
    if float(config.transaction_tax_rate) != TAIWAN_EQUITY_TRANSACTION_TAX_RATE:
        return float(config.transaction_tax_rate)
    code = str(instrument or "").strip().upper().split(".")[0]
    if code.startswith("00"):
        return float(config.etf_transaction_tax_rate)
    return float(config.transaction_tax_rate)


def _extract_fill_cost_summary(
    pf: Any,
    *,
    commission_rate: float,
    tax_rate: float,
) -> dict[str, Any]:
    """Compute per-fill commission and sell transaction tax paid from vectorbt orders."""
    records = pf.orders.records_readable
    buy_fills = 0
    sell_fills = 0
    commission_paid = 0.0
    tax_paid = 0.0
    if len(records) > 0:
        for _, row in records.iterrows():
            notional = float(row["Size"]) * float(row["Price"])
            side = str(row["Side"]).strip().lower()
            commission_paid += notional * commission_rate
            if side == "buy":
                buy_fills += 1
            elif side == "sell":
                sell_fills += 1
                tax_paid += notional * tax_rate
    closed_trades = int(pf.trades.closed.count())
    round_trips = max(closed_trades, min(buy_fills, sell_fills))
    total_fees = float(pf.orders.fees.sum()) if int(pf.orders.count()) > 0 else 0.0
    return {
        "commission_rate": round(float(commission_rate), 6),
        "transaction_tax_rate": round(float(tax_rate), 6),
        "buy_fill_count": buy_fills,
        "sell_fill_count": sell_fills,
        "round_trips": round_trips,
        "total_commission_paid": round(commission_paid, 6),
        "total_transaction_tax_paid": round(tax_paid, 6),
        "total_fees_paid": round(total_fees, 6),
    }


class VectorbtBackend:
    """Real upstream backend using vbt.Portfolio.from_signals().

    Requires: pip install vectorbt==1.1.1
    Gated behind PANTHEON_VECTORBT_BACKEND=real environment variable.
    """

    def run(self, dataset: PreparedVectorbtDataset, config: BacktestConfig) -> BacktestRunResult:
        if os.environ.get("PANTHEON_VECTORBT_BACKEND", "stub") != "real":
            raise VectorbtWorkflowError(
                "VectorbtBackend requires PANTHEON_VECTORBT_BACKEND=real. "
                "Use StubVectorbtBackend for CI."
            )
        try:
            import numpy as np  # type: ignore
            import pandas as pd  # type: ignore
            import vectorbt as vbt  # type: ignore
        except ImportError as exc:
            raise VectorbtWorkflowError(
                "vectorbt backend unavailable. Install services/research/vectorbt/requirements.txt."
            ) from exc

        short_window = config.strategy_params.get("short_window", 5)
        long_window = config.strategy_params.get("long_window", 20)

        run_id = f"vbt-real-{uuid.uuid4().hex[:12]}"
        per_instrument: dict[str, dict[str, Any]] = {}
        artifact_orders = (
            _artifact_order_sizes(dataset, config.strategy_artifact)
            if config.strategy_artifact is not None
            else None
        )

        for inst in artifact_orders if artifact_orders is not None else dataset.instruments:
            bars = dataset.ohlcv_by_instrument[inst]
            closes = pd.Series([b[3] for b in bars])
            inst_tax_rate = (
                _instrument_tax_rate(inst, config)
                if config.apply_taiwan_fill_costs
                else 0.0
            )
            comm_rate = (
                float(config.commission_rate)
                if config.apply_taiwan_fill_costs
                else float(config.fees)
            )
            if artifact_orders is not None:
                sizes = pd.Series(artifact_orders[inst], dtype="float64")
                if config.apply_taiwan_fill_costs:
                    fee_series = pd.Series(
                        [
                            comm_rate + inst_tax_rate
                            if (not math.isnan(float(s)) and float(s) < 0.0)
                            else comm_rate
                            for s in artifact_orders[inst]
                        ],
                        dtype="float64",
                    )
                else:
                    fee_series = config.fees
                pf = vbt.Portfolio.from_orders(
                    closes,
                    size=sizes,
                    size_type="amount",
                    direction="both",
                    init_cash=config.init_cash,
                    fees=fee_series,
                    freq="1D",
                )
                cost_summary = _extract_fill_cost_summary(
                    pf,
                    commission_rate=comm_rate,
                    tax_rate=inst_tax_rate,
                )
                per_instrument[inst] = {
                    "total_return": round(float(pf.total_return()), 6),
                    "sharpe_ratio": round(float(pf.sharpe_ratio()), 6),
                    "max_drawdown": round(abs(float(pf.max_drawdown())), 6),
                    "final_portfolio_value": round(float(pf.final_value()), 4),
                    "trade_count": int(pf.orders.count()),
                    "num_bars": len(bars),
                    "net_position": float(pf.asset_flow().sum()),
                    **cost_summary,
                }
                _annotate_sharpe_reason(per_instrument[inst])
                continue
            short_ma = closes.rolling(short_window).mean()
            long_ma = closes.rolling(long_window).mean()
            entries = (short_ma > long_ma) & (short_ma.shift(1) <= long_ma.shift(1))
            exits = (short_ma < long_ma) & (short_ma.shift(1) >= long_ma.shift(1))
            if config.apply_taiwan_fill_costs:
                signal_fees = pd.Series(
                    [
                        comm_rate + inst_tax_rate if bool(is_exit) else comm_rate
                        for is_exit in exits
                    ],
                    dtype="float64",
                )
            else:
                signal_fees = config.fees
            pf = vbt.Portfolio.from_signals(
                closes,
                entries=entries,
                exits=exits,
                init_cash=config.init_cash,
                fees=signal_fees,
                freq="1D",
            )
            cost_summary = _extract_fill_cost_summary(
                pf,
                commission_rate=comm_rate,
                tax_rate=inst_tax_rate,
            )
            per_instrument[inst] = {
                "total_return": round(float(pf.total_return()), 6),
                "sharpe_ratio": round(float(pf.sharpe_ratio()), 6),
                "max_drawdown": round(abs(float(pf.max_drawdown())), 6),
                "final_portfolio_value": round(float(pf.final_value()), 4),
                "trade_count": int(pf.trades.count()),
                "num_bars": len(bars),
                **cost_summary,
            }
            _annotate_sharpe_reason(per_instrument[inst])

        returns = [m["total_return"] for m in per_instrument.values()]
        sharpes = [m["sharpe_ratio"] for m in per_instrument.values()]
        drawdowns = [m["max_drawdown"] for m in per_instrument.values()]
        aggregate: dict[str, Any] = {
            "num_instruments": len(per_instrument),
            "mean_total_return": round(sum(returns) / len(returns), 6) if returns else 0.0,
            "mean_sharpe_ratio": round(sum(sharpes) / len(sharpes), 6) if sharpes else 0.0,
            "mean_max_drawdown": round(sum(drawdowns) / len(drawdowns), 6) if drawdowns else 0.0,
            "total_trades": sum(m["trade_count"] for m in per_instrument.values()),
            "total_round_trips": sum(int(m.get("round_trips", 0)) for m in per_instrument.values()),
            "commission_rate": round(float(config.commission_rate), 6),
            "transaction_tax_rate": round(float(config.transaction_tax_rate), 6),
            "total_commission_paid": round(
                sum(float(m.get("total_commission_paid", 0.0)) for m in per_instrument.values()), 6
            ),
            "total_transaction_tax_paid": round(
                sum(float(m.get("total_transaction_tax_paid", 0.0)) for m in per_instrument.values()), 6
            ),
            "total_fees_paid": round(
                sum(float(m.get("total_fees_paid", 0.0)) for m in per_instrument.values()), 6
            ),
            **_aggregate_sharpe_reason(per_instrument),
        }
        return BacktestRunResult(
            backend=PRIMARY_BACKEND,
            run_id=run_id,
            per_instrument_metrics=per_instrument,
            aggregate_metrics=aggregate,
            notes=(
                (
                    f"vectorbt {VECTORBT_VERSION_PIN} from_orders backtest of canonical "
                    f"{config.strategy_artifact['strategy_logic']['kind']} complete.",
                )
                if config.strategy_artifact is not None
                else (f"vectorbt {VECTORBT_VERSION_PIN} MA-crossover backtest complete.",)
            ),
        )


def _bar_features_for_index(
    dataset: PreparedVectorbtDataset,
    instrument: str,
    index: int,
) -> tuple[dict[str, Any], str | None]:
    """Build the point-in-time feature snapshot and decision_time for bar ``index``."""
    bars_slice = dataset.ohlcv_by_instrument[instrument][: index + 1]
    total_len = len(dataset.ohlcv_by_instrument[instrument])
    bar_features: dict[str, Any] = {
        "open": [b[0] for b in bars_slice],
        "high": [b[1] for b in bars_slice],
        "low": [b[2] for b in bars_slice],
        "close": [b[3] for b in bars_slice],
        "volume": [b[4] for b in bars_slice],
    }
    inst_features = dataset.features_by_instrument.get(instrument) or {}
    for fk, fv in inst_features.items():
        if fk in {"open", "high", "low", "close", "volume"}:
            continue
        if (
            isinstance(fv, Sequence)
            and not isinstance(fv, (str, bytes))
            and len(fv) == total_len
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in fv)
        ):
            bar_features[fk] = list(fv[: index + 1])
        else:
            bar_features[fk] = fv

    dates = dataset.dates_by_instrument.get(instrument)
    decision_time = dates[index] if dates and index < len(dates) else None
    return bar_features, decision_time


def _artifact_action_sequence(
    dataset: PreparedVectorbtDataset, artifact: Mapping[str, Any]
) -> dict[str, list[str | None]]:
    """Per-bar evaluated actions (BUY/SELL/HOLD or None on warmup bars) from the Registry interpreter."""
    from services.execution.shioaji_adapter import normalize_taiwan_symbol
    from services.registry.factor_rule import required_history_bars
    from services.registry.strategy_artifact import (
        StrategyArtifactValidationError,
        evaluate_strategy_action,
        validate_strategy_artifact,
    )

    try:
        validate_strategy_artifact(artifact)
    except StrategyArtifactValidationError as exc:
        raise VectorbtWorkflowError(f"strategy artifact validation failed: {exc}") from exc

    parameters = artifact["parameters"]
    if parameters.get("quantity_type") != "SHARES":
        raise VectorbtWorkflowError("canonical backtests support quantity_type SHARES only")

    logic = artifact["strategy_logic"]
    kind = logic["kind"]
    if kind == "close_to_close_momentum":
        start_index = int(parameters[logic["lookback_parameter"]]) - 1
    elif kind == "factor_rule_v1":
        start_index = max(0, required_history_bars(logic, parameters) - 1)
    else:
        raise VectorbtWorkflowError(f"unsupported strategy_logic kind {kind!r}")

    wanted = {normalize_taiwan_symbol(symbol) for symbol in parameters["symbols"]}
    selected: dict[str, str] = {}
    for instrument in dataset.instruments:
        try:
            key = normalize_taiwan_symbol(instrument)
        except ValueError:
            continue
        if key in wanted:
            selected[instrument] = instrument
    covered = {normalize_taiwan_symbol(inst) for inst in selected}
    if covered != wanted:
        raise VectorbtWorkflowError(
            "dataset does not cover the strategy artifact symbols: "
            + ", ".join(sorted(f"{s}:{v}" for s, v in wanted - covered))
        )

    actions_by_inst: dict[str, list[str | None]] = {}
    for instrument in selected:
        closes = [bar[3] for bar in dataset.ohlcv_by_instrument[instrument]]
        actions: list[str | None] = [None] * len(closes)
        evaluated_count = 0
        for index in range(start_index, len(closes)):
            bar_features, decision_time = _bar_features_for_index(dataset, instrument, index)
            try:
                action = evaluate_strategy_action(
                    artifact,
                    closes[: index + 1],
                    features=bar_features,
                    decision_time=decision_time,
                )
            except StrategyArtifactValidationError as exc:
                msg = str(exc)
                if kind == "factor_rule_v1" and (
                    "requires sufficient" in msg or "no visible observations" in msg
                ):
                    continue
                raise VectorbtWorkflowError(f"strategy artifact evaluation failed: {exc}") from exc
            if action not in {"BUY", "SELL", "HOLD"}:
                raise VectorbtWorkflowError(f"unsupported strategy action {action!r}")
            actions[index] = action
            evaluated_count += 1
        if evaluated_count == 0:
            raise VectorbtWorkflowError(
                f"strategy artifact evaluation produced 0 valid bars for {instrument}"
            )
        actions_by_inst[instrument] = actions
    return actions_by_inst


def _artifact_order_sizes(
    dataset: PreparedVectorbtDataset, artifact: Mapping[str, Any]
) -> dict[str, list[float]]:
    """Signed per-bar order sizes from the Registry interpreter, artifact symbols only."""
    actions_by_inst = _artifact_action_sequence(dataset, artifact)
    quantity = float(artifact["parameters"]["order_quantity"])
    sizes: dict[str, list[float]] = {}
    for instrument, actions in actions_by_inst.items():
        orders = [float("nan")] * len(actions)
        for index, action in enumerate(actions):
            if action == "BUY":
                orders[index] = quantity
            elif action == "SELL":
                orders[index] = -quantity
        sizes[instrument] = orders
    return sizes


def run_vectorbt_workflow(
    dataset: Mapping[str, Any],
    *,
    backend: VectorbtBackendProtocol | None = None,
    config: BacktestConfig | None = None,
) -> VectorbtRunResult:
    prepared = GovernedVectorbtInputAdapter().prepare(dataset)
    backtest_config = config or BacktestConfig()
    runner = backend or StubVectorbtBackend()
    backtest_result = runner.run(prepared, backtest_config)
    artifact_bundle = _build_artifact_bundle(prepared, backtest_result, backtest_config)
    registry_entry = _build_registry_entry(prepared, backtest_result, artifact_bundle, backtest_config)
    return VectorbtRunResult(
        prepared_dataset=prepared,
        backtest_result=backtest_result,
        artifact_bundle=artifact_bundle,
        registry_entry=registry_entry,
    )


def _artifact_view_strength(
    dataset: PreparedVectorbtDataset, artifact: Mapping[str, Any] | None
) -> tuple[float | dict[str, float | None] | None, dict[str, list[float | None]] | None]:
    """Compute view strength series from evaluate_strategy_view for run metadata only (never sizing)."""

    if artifact is None:
        return None, None
    from services.execution.shioaji_adapter import normalize_taiwan_symbol
    from services.registry.factor_rule import required_history_bars
    from services.registry.strategy_artifact import (
        StrategyArtifactValidationError,
        evaluate_strategy_view,
    )

    parameters = artifact["parameters"]
    logic = artifact["strategy_logic"]
    kind = logic["kind"]
    if kind == "close_to_close_momentum":
        start_index = int(parameters[logic["lookback_parameter"]]) - 1
    elif kind == "factor_rule_v1":
        start_index = max(0, required_history_bars(logic, parameters) - 1)
    else:
        raise VectorbtWorkflowError(f"unsupported strategy_logic kind {kind!r}")

    wanted = {normalize_taiwan_symbol(symbol) for symbol in parameters["symbols"]}
    selected: list[str] = []
    for instrument in dataset.instruments:
        try:
            key = normalize_taiwan_symbol(instrument)
        except ValueError:
            continue
        if key in wanted:
            selected.append(instrument)
    by_instrument: dict[str, list[float | None]] = {}
    terminal: dict[str, float | None] = {}
    for instrument in selected:
        closes = [bar[3] for bar in dataset.ohlcv_by_instrument[instrument]]
        strengths: list[float | None] = [None] * len(closes)
        for index in range(start_index, len(closes)):
            bar_features, decision_time = _bar_features_for_index(dataset, instrument, index)
            try:
                view = evaluate_strategy_view(
                    artifact,
                    closes[: index + 1],
                    features=bar_features,
                    decision_time=decision_time,
                )
            except StrategyArtifactValidationError as exc:
                msg = str(exc)
                if kind == "factor_rule_v1" and (
                    "requires sufficient" in msg or "no visible observations" in msg
                ):
                    continue
                raise VectorbtWorkflowError(
                    f"strategy artifact view evaluation failed: {exc}"
                ) from exc
            strengths[index] = view["strength"]
        by_instrument[instrument] = strengths
        terminal[instrument] = strengths[-1] if strengths else None
    if len(terminal) == 1:
        return next(iter(terminal.values())), by_instrument
    return (terminal or None), (by_instrument or None)


def _artifact_identity(artifact: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Identity (never the model body) of the artifact a backtest actually evaluated."""

    if artifact is None:
        return None
    from services.registry.strategy_artifact import strategy_artifact_checksum

    identity: dict[str, Any] = {
        "artifact_id": artifact["artifact_id"],
        "strategy_id": artifact["strategy_id"],
        "version": artifact["version"],
        "checksum": strategy_artifact_checksum(artifact),
        "strategy_kind": artifact["strategy_logic"]["kind"],
        "parameters": copy.deepcopy(artifact["parameters"]),
    }
    if "expression_policy" in artifact:
        identity["expression_policy"] = copy.deepcopy(artifact["expression_policy"])
    return identity


def _build_artifact_bundle(
    dataset: PreparedVectorbtDataset,
    result: BacktestRunResult,
    config: BacktestConfig,
) -> dict[str, Any]:
    strength_summary, _ = _artifact_view_strength(dataset, config.strategy_artifact)
    return {
        "schema_version": "1.0",
        "artifact_family": "vectorbt_backtest",
        "framework": "vectorbt",
        "framework_version": VECTORBT_VERSION_PIN,
        "created_at": utc_now(),
        "created_by": config.requested_by,
        "dataset_summary": dataset.dataset_summary(),
        "backtest_config": {
            "version": config.version,
            "strategy_params": config.strategy_params,
            "strategy_artifact": _artifact_identity(config.strategy_artifact),
            "strength": strength_summary,
            "init_cash": config.init_cash,
            "fees": config.fees,
            "commission_rate": config.commission_rate,
            "transaction_tax_rate": config.transaction_tax_rate,
            "etf_transaction_tax_rate": config.etf_transaction_tax_rate,
            "apply_taiwan_fill_costs": config.apply_taiwan_fill_costs,
            "requested_by": config.requested_by,
        },
        "per_instrument_metrics": copy.deepcopy(result.per_instrument_metrics),
        "aggregate_metrics": copy.deepcopy(result.aggregate_metrics),
        "governance": {
            "required_ohlcv_fields": list(REQUIRED_OHLCV_FIELDS),
            "direct_live_influence": False,
            "output_type": "backtest_result",
            "lean_consumption": "scoring_only_not_direct_action",
            "notes": list(result.notes),
        },
        "registry_hints": {
            "artifact_type": "backtest_result",
            "artifact_state": "draft",
            "deployment_stage": "none",
            "source_dataset_refs": list(dataset.source_dataset_refs),
        },
    }


def _build_registry_entry(
    dataset: PreparedVectorbtDataset,
    result: BacktestRunResult,
    artifact_bundle: Mapping[str, Any],
    config: BacktestConfig,
) -> dict[str, Any]:
    strength_summary, view_strength_by_instrument = _artifact_view_strength(
        dataset, config.strategy_artifact
    )
    storage_path = config.storage_path_template.format(
        strategy_id=dataset.strategy_id,
        version=config.version,
    )
    lineage: dict[str, Any] = {
        "source_run_ids": [result.run_id],
        "source_dataset_refs": list(dataset.source_dataset_refs),
    }
    if dataset.source_strategy_spec_id:
        lineage["source_strategy_spec_id"] = dataset.source_strategy_spec_id

    return {
        "registry_id": f"vbt-backtest-{dataset.strategy_id}-{config.version}",
        "artifact_type": "backtest_result",
        "strategy_id": dataset.strategy_id,
        "version": config.version,
        "artifact_state": "draft",
        "deployment_summary": {"current_stage": "none"},
        "created_at": artifact_bundle["created_at"],
        "lineage": lineage,
        "storage_ref": {
            "backend": config.storage_backend,
            "path": storage_path,
        },
        "checksum": f"sha256:{_sha256_json(artifact_bundle)}",
        "producer_run_id": result.run_id,
        "aggregate_metrics": copy.deepcopy(result.aggregate_metrics),
        "metadata": {
            "framework": "vectorbt",
            "framework_version": VECTORBT_VERSION_PIN,
            "backtest_backend": result.backend,
            "strategy": (
                config.strategy_artifact["strategy_logic"]["kind"]
                if config.strategy_artifact is not None
                else "ma_crossover"
            ),
            "strategy_params": config.strategy_params,
            "strategy_artifact": _artifact_identity(config.strategy_artifact),
            "strength": strength_summary,
            "view_strength_by_instrument": view_strength_by_instrument,
            "num_instruments": dataset.num_instruments,
            "total_bars": dataset.total_bars,
            "data_frequency": dataset.data_frequency,
        },
        "approved_at": None,
        "approver": None,
        "rollback_target": None,
    }


def _slice_prepared_dataset(
    dataset: PreparedVectorbtDataset,
    start_idx: int,
    end_idx: int,
    suffix: str,
) -> PreparedVectorbtDataset:
    """Slice a PreparedVectorbtDataset onto bar range ``[start_idx, end_idx)``."""
    bars_per_inst: dict[str, int] = {}
    ohlcv_by_inst: dict[str, tuple[tuple[float, float, float, float, float], ...]] = {}
    dates_by_inst: dict[str, tuple[str, ...]] = {}
    features_by_inst: dict[str, dict[str, Any]] = {}

    for inst in dataset.instruments:
        full_bars = dataset.ohlcv_by_instrument[inst]
        total_len = len(full_bars)
        sliced_bars = full_bars[start_idx:end_idx]
        bars_per_inst[inst] = len(sliced_bars)
        ohlcv_by_inst[inst] = sliced_bars
        full_dates = dataset.dates_by_instrument.get(inst) or ()
        dates_by_inst[inst] = full_dates[start_idx:end_idx] if full_dates else ()

        inst_features: dict[str, Any] = {
            "open": [b[0] for b in sliced_bars],
            "high": [b[1] for b in sliced_bars],
            "low": [b[2] for b in sliced_bars],
            "close": [b[3] for b in sliced_bars],
            "volume": [b[4] for b in sliced_bars],
        }
        raw_inst_features = dataset.features_by_instrument.get(inst) or {}
        for fk, fv in raw_inst_features.items():
            if fk in {"open", "high", "low", "close", "volume"}:
                continue
            if (
                isinstance(fv, Sequence)
                and not isinstance(fv, (str, bytes))
                and len(fv) == total_len
                and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in fv)
            ):
                inst_features[fk] = list(fv[start_idx:end_idx])
            else:
                inst_features[fk] = fv
        features_by_inst[inst] = inst_features

    return PreparedVectorbtDataset(
        dataset_id=f"{dataset.dataset_id}:{suffix}",
        strategy_id=dataset.strategy_id,
        source_dataset_refs=dataset.source_dataset_refs,
        source_strategy_spec_id=dataset.source_strategy_spec_id,
        instruments=dataset.instruments,
        bars_per_instrument=bars_per_inst,
        ohlcv_by_instrument=ohlcv_by_inst,
        data_frequency=dataset.data_frequency,
        num_instruments=dataset.num_instruments,
        total_bars=sum(bars_per_inst.values()),
        dates_by_instrument=dates_by_inst,
        features_by_instrument=features_by_inst,
    )


def _collect_inputs(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, Mapping):
        inp = node.get("input")
        if isinstance(inp, str) and inp.strip():
            found.add(inp.strip())
        for val in node.values():
            found |= _collect_inputs(val)
    elif isinstance(node, (list, tuple)):
        for item in node:
            found |= _collect_inputs(item)
    return found


def _measure_pit_history(
    dataset: Mapping[str, Any],
    strategy_artifact: Mapping[str, Any],
) -> tuple[int, int, dict[str, Any]]:
    """Measure point-in-time history length (calendar days and bars) across candidate inputs."""
    from datetime import date as _date, timedelta
    from services.execution.shioaji_adapter import normalize_taiwan_symbol

    parameters = strategy_artifact.get("parameters") or {}
    wanted = {normalize_taiwan_symbol(s) for s in parameters.get("symbols", [])}
    records = dataset.get("records") or []
    dates_by_symbol: dict[str, list[str]] = {}
    for rec in records:
        if not isinstance(rec, Mapping):
            continue
        inst = str(rec.get("instrument") or "").strip()
        dt_str = str(rec.get("date") or "").strip()
        if not inst or not dt_str:
            continue
        try:
            norm = normalize_taiwan_symbol(inst)
        except ValueError:
            continue
        if norm in wanted:
            dates_by_symbol.setdefault(norm[0], []).append(dt_str[:10])

    per_symbol_span: dict[str, dict[str, Any]] = {}
    min_calendar_days: int | None = None
    min_bars: int | None = None
    for code, raw_dates in dates_by_symbol.items():
        sorted_dates = sorted(raw_dates)
        bars = len(sorted_dates)
        if len(sorted_dates) >= 2:
            d0 = _date.fromisoformat(sorted_dates[0])
            d1 = _date.fromisoformat(sorted_dates[-1])
            cal_days = (d1 - d0).days
        else:
            cal_days = 0
        per_symbol_span[code] = {
            "bars": bars,
            "calendar_days": cal_days,
            "start_date": sorted_dates[0] if sorted_dates else None,
            "end_date": sorted_dates[-1] if sorted_dates else None,
        }
        min_calendar_days = cal_days if min_calendar_days is None else min(min_calendar_days, cal_days)
        min_bars = bars if min_bars is None else min(min_bars, bars)

    if min_calendar_days is None or min_bars is None:
        return 0, 0, {"per_symbol": per_symbol_span, "features": {}}

    # Check explicit dataset-level history depth overrides if provided
    explicit_days = dataset.get("pit_history_days") or dataset.get("history_days")
    if isinstance(explicit_days, (int, float)) and not isinstance(explicit_days, bool):
        min_calendar_days = min(min_calendar_days, int(explicit_days))
    explicit_years = dataset.get("pit_history_years") or dataset.get("history_years")
    if isinstance(explicit_years, (int, float)) and not isinstance(explicit_years, bool):
        min_calendar_days = min(min_calendar_days, int(round(float(explicit_years) * 365)))

    # For factor_rule_v1, resolve the real ref graph (signal/entry/exit/strength/filters)
    # to declared inputs and measure the PIT history of every required feature.
    feature_spans: dict[str, dict[str, Any]] = {}
    logic = strategy_artifact.get("strategy_logic") or {}
    if logic.get("kind") == "factor_rule_v1":
        from services.registry.factor_rule import (
            BUILT_IN_PRICE_VOLUME_FIELDS,
            _lookup_raw_feature,
            get_feature_definition,
            collect_expression_refs_and_params,
        )

        referenced: set[str] = set()
        roots: list[Any] = [logic.get("signal"), logic.get("strength")]
        for flt in logic.get("filters") or []:
            roots.append(flt)
        for section in ("entry", "exit"):
            cfg = logic.get(section)
            if isinstance(cfg, Mapping):
                roots.extend(cfg.values())
        for root in roots:
            if isinstance(root, Mapping):
                refs, _ = collect_expression_refs_and_params(root)
                referenced |= refs
        declared = {
            str(item.get("name")): item
            for item in logic.get("inputs") or []
            if isinstance(item, Mapping)
        }
        price_fields = set(BUILT_IN_PRICE_VOLUME_FIELDS) | {"open", "high", "low", "close", "volume"}
        raw_features = dataset.get("features_by_instrument") or dataset.get("features") or {}

        def _present(value: Any) -> bool:
            if value is None:
                return False
            if isinstance(value, float) and math.isnan(value):
                return False
            return True

        def _day(value: Any) -> _date | None:
            text = str(value or "").strip()[:10]
            if len(text) != 10:
                return None
            try:
                return _date.fromisoformat(text)
            except ValueError:
                return None

        def _obs_stamp(obs: Any, lag_days: int) -> tuple[bool, _date | None]:
            """Return (non-null, effective availability day).

            Effective availability = max(available_time, event_time + lag).
            """
            if isinstance(obs, Mapping):
                event = _day(obs.get("event_time") or obs.get("date") or obs.get("as_of"))
                avail = _day(obs.get("available_time"))
                ok = _present(obs["value"]) if "value" in obs else True
            elif isinstance(obs, (list, tuple)) and len(obs) == 2:
                event, avail, ok = None, _day(obs[0]), _present(obs[1])
            else:
                event, avail, ok = None, None, _present(obs)
            if event is not None:
                lagged = event + timedelta(days=max(0, lag_days))
                if avail is None or lagged > avail:
                    avail = lagged
            return ok, avail

        def _span(stamps: list[str]) -> int:
            days = sorted(d for d in (_day(x) for x in stamps) if d is not None)
            return (days[-1] - days[0]).days if len(days) >= 2 else 0

        def _usable(avail_days: list[_date], decisions: list[str]) -> tuple[int, int]:
            """(usable decision bars, calendar span) from first effective availability."""
            if not avail_days:
                return 0, 0
            first_avail = min(avail_days)
            usable = [d for d in (_day(x) for x in decisions) if d is not None and d >= first_avail]
            if len(usable) < 2:
                return len(usable), 0
            return len(usable), (max(usable) - min(usable)).days

        def _norm_key(key: Any) -> str | None:
            try:
                return normalize_taiwan_symbol(str(key))[0]
            except (ValueError, TypeError, IndexError):
                return None

        wanted_codes = {w[0] for w in wanted}
        inst_maps: dict[str, Mapping[str, Any]] = {}
        global_map: dict[str, Any] = {}
        if isinstance(raw_features, Mapping):
            for k, v in raw_features.items():
                if isinstance(v, Mapping):
                    code = _norm_key(k)
                    if code is not None:
                        inst_maps[code] = v
                else:
                    global_map[str(k)] = v

        def _measure_symbol(
            code: str, name: str, ds: str, fld: str, lag_days: int
        ) -> tuple[int, int | None, bool, int | None] | None:
            """Usable PIT coverage of one input for one instrument; None when absent.

            Returns (visible observations, calendar days, bar_aligned, usable decision bars).
            """
            decisions = dates_by_symbol.get(code, [])
            last_decision = _day(max(decisions)) if decisions else None
            series = None
            if code in inst_maps:
                series = _lookup_raw_feature(inst_maps[code], name=name, dataset=ds, field=fld)
            if series is None and global_map:
                series = _lookup_raw_feature(global_map, name=name, dataset=ds, field=fld)
            if isinstance(series, Sequence) and not isinstance(series, (str, bytes)):
                pairs = [_obs_stamp(o, lag_days) for o in series]
                good = [ts for ok, ts in pairs if ok]
                if good:
                    stamps = [ts for ts in good if ts is not None]
                    if not stamps:
                        return len(good), None, True, None
                    visible = [t for t in stamps if last_decision is None or t <= last_decision]
                    bars, days = _usable(visible, decisions)
                    return len(visible), days, False, bars
            # Fall back to non-null per-record values for this instrument.
            stamps_s: list[str] = []
            for rec in records:
                if not isinstance(rec, Mapping) or _norm_key(rec.get("instrument")) != code:
                    continue
                feats = rec.get("features") if isinstance(rec.get("features"), Mapping) else {}
                if any(_present(rec.get(k)) or _present(feats.get(k)) for k in {name, fld}):
                    stamps_s.append(str(rec.get("date") or "").strip()[:10])
            if stamps_s:
                dated = [t for t in stamps_s if len(t) == 10]
                return len(stamps_s), (_span(dated) if dated else None), True, None
            return None

        for ref in sorted(referenced - {"signal"}):
            item = declared.get(ref)
            if item is None:
                if ref in price_fields:
                    continue
                feature_spans[ref] = {"observations": 0, "calendar_days": 0, "missing": True}
                min_calendar_days = 0
                min_bars = 0
                continue
            name, ds, fld = str(item.get("name")), str(item.get("dataset")), str(item.get("field"))
            if fld in price_fields:
                continue
            lag_days = int(item.get("availability_lag_days") or 0)
            cat_def = get_feature_definition(ds, fld)
            if cat_def is not None:
                lag_days = max(lag_days, cat_def.min_availability_lag_days)
            worst_bars: int | None = None
            per_inst: dict[str, Any] = {}
            missing_codes: list[str] = []
            worst_n: int | None = None
            worst_days: int | None = None
            bar_aligned = False
            for code in sorted(wanted_codes):
                res = _measure_symbol(code, name, ds, fld, lag_days)
                if res is None:
                    missing_codes.append(code)
                    per_inst[code] = {"observations": 0, "calendar_days": 0, "missing": True}
                    continue
                n, d, aligned, ubars = res
                bar_aligned = bar_aligned or aligned
                per_inst[code] = {"observations": n, "calendar_days": d, "usable_bars": ubars}
                if ubars is not None:
                    worst_bars = ubars if worst_bars is None else min(worst_bars, ubars)
                worst_n = n if worst_n is None else min(worst_n, n)
                if d is not None:
                    worst_days = d if worst_days is None else min(worst_days, d)
            if missing_codes or worst_n is None:
                feature_spans[ref] = {
                    "observations": 0,
                    "calendar_days": 0,
                    "missing": True,
                    "missing_instruments": missing_codes,
                    "per_instrument": per_inst,
                }
                min_calendar_days = 0
                min_bars = 0
                continue
            feature_spans[ref] = {
                "observations": worst_n,
                "calendar_days": worst_days if worst_days is not None else min_calendar_days,
                "per_instrument": per_inst,
            }
            if worst_days is not None:
                min_calendar_days = min(min_calendar_days, worst_days)
            if worst_days is None or bar_aligned:
                min_bars = min(min_bars, worst_n)
            if worst_bars is not None:
                min_bars = min(min_bars, worst_bars)

    return (
        int(min_calendar_days),
        int(min_bars),
        {"per_symbol": per_symbol_span, "features": feature_spans},
    )


def _compute_buy_and_hold_benchmark(
    dataset: PreparedVectorbtDataset,
    instruments: Sequence[str],
    config: BacktestConfig,
) -> dict[str, Any] | None:
    """Compute buy-and-hold benchmark metrics on ``dataset`` with commission and sell tax."""
    if not instruments:
        return None
    per_inst: dict[str, dict[str, float]] = {}
    for inst in instruments:
        if inst not in dataset.ohlcv_by_instrument:
            continue
        bars = dataset.ohlcv_by_instrument[inst]
        if len(bars) < 2:
            continue
        closes = [float(b[3]) for b in bars]
        comm_rate = float(config.commission_rate)
        tax_rate = _instrument_tax_rate(inst, config)
        entry_cost_factor = 1.0 - comm_rate
        exit_cost_factor = 1.0 - (comm_rate + tax_rate)
        shares = (config.init_cash * entry_cost_factor) / closes[0] if closes[0] > 0 else 0.0
        equity_curve = [shares * c for c in closes[:-1]] + [
            shares * closes[-1] * exit_cost_factor
        ]
        total_return = (equity_curve[-1] - config.init_cash) / config.init_cash
        daily_rets: list[float] = []
        for idx in range(1, len(equity_curve)):
            prev = equity_curve[idx - 1]
            if prev > 0:
                daily_rets.append((equity_curve[idx] - prev) / prev)
        mean_ret = sum(daily_rets) / len(daily_rets) if daily_rets else 0.0
        var_ret = (
            sum((r - mean_ret) ** 2 for r in daily_rets) / (len(daily_rets) - 1)
            if len(daily_rets) > 1
            else 0.0
        )
        std_ret = math.sqrt(var_ret)
        sharpe = (mean_ret / std_ret) * math.sqrt(252.0) if std_ret > 1e-12 else 0.0
        peak = equity_curve[0] if equity_curve else config.init_cash
        max_dd = 0.0
        for val in equity_curve:
            if val > peak:
                peak = val
            dd = (peak - val) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        per_inst[inst] = {
            "total_return": round(total_return, 6),
            "sharpe_ratio": round(sharpe, 6),
            "max_drawdown": round(max_dd, 6),
            "commission_rate": round(comm_rate, 6),
            "transaction_tax_rate": round(tax_rate, 6),
        }
    if not per_inst:
        return None
    rets = [m["total_return"] for m in per_inst.values()]
    sharpes = [m["sharpe_ratio"] for m in per_inst.values()]
    dds = [m["max_drawdown"] for m in per_inst.values()]
    return {
        "instruments": list(per_inst.keys()),
        "total_return": round(sum(rets) / len(rets), 6),
        "sharpe_ratio": round(sum(sharpes) / len(sharpes), 6),
        "max_drawdown": round(max(dds), 6),
        "per_instrument": per_inst,
    }


def _summarize_window_result(
    window_dataset: PreparedVectorbtDataset,
    result: BacktestRunResult,
    *,
    window_fraction: float,
) -> dict[str, Any]:
    """Build a structured metrics summary for an IS, OOS, or walk-forward window."""
    agg = result.aggregate_metrics
    first_inst = next(iter(result.per_instrument_metrics.keys()), None)
    dates = (
        window_dataset.dates_by_instrument.get(first_inst, ())
        if first_inst is not None
        else ()
    )
    sharpe = float(agg.get("mean_sharpe_ratio", 0.0))
    return {
        "run_id": result.run_id,
        "start_date": dates[0] if dates else None,
        "end_date": dates[-1] if dates else None,
        "num_bars": len(dates) if dates else 0,
        "window_fraction": round(float(window_fraction), 4),
        "sharpe_ratio": round(sharpe, 6) if math.isfinite(sharpe) else sharpe,
        "total_return": round(float(agg.get("mean_total_return", 0.0)), 6),
        "max_drawdown": round(abs(float(agg.get("mean_max_drawdown", 0.0))), 6),
        "trade_count": int(agg.get("total_trades", 0)),
        "round_trips": int(agg.get("total_round_trips", 0)),
        "total_commission_paid": round(float(agg.get("total_commission_paid", 0.0)), 6),
        "total_transaction_tax_paid": round(
            float(agg.get("total_transaction_tax_paid", 0.0)), 6
        ),
        "total_fees_paid": round(float(agg.get("total_fees_paid", 0.0)), 6),
        "per_instrument_metrics": copy.deepcopy(result.per_instrument_metrics),
    }


def run_replication_backtest_protocol(
    dataset: Mapping[str, Any],
    strategy_artifact: Mapping[str, Any],
    *,
    config: BacktestConfig | None = None,
    in_sample_fraction: float = 0.70,
    walk_forward_folds: int = 3,
    min_pit_history_days: int = MIN_PIT_HISTORY_CALENDAR_DAYS,
    min_pit_history_bars: int = MIN_PIT_HISTORY_TRADING_DAYS,
    mandate_max_drawdown: float = 0.25,
    min_oos_round_trips: int = 20,
    run_id: str | None = None,
    backend: VectorbtBackendProtocol | None = None,
) -> dict[str, Any]:
    """Run the §10 real Taiwan backtest protocol (70% IS, 30% OOS, 3 WF folds, statutory costs, benchmarks).

    If the candidate's inputs have less than 2 years of point-in-time history,
    returns ``status = 'insufficient_history'`` without raising or admitting.
    """
    from services.execution.shioaji_adapter import normalize_taiwan_symbol
    from services.registry.strategy_artifact import (
        StrategyArtifactValidationError,
        validate_strategy_artifact,
    )

    try:
        validate_strategy_artifact(strategy_artifact)
    except StrategyArtifactValidationError as exc:
        raise VectorbtWorkflowError(f"strategy artifact validation failed: {exc}") from exc

    market = str((strategy_artifact.get("parameters") or {}).get("market") or "TW").strip().upper()
    if market != "TW":
        raise VectorbtWorkflowError(
            f"replication backtest protocol requires governed Taiwan market ('TW'); got {market!r}"
        )

    pit_days, pit_bars, history_details = _measure_pit_history(dataset, strategy_artifact)
    history_sufficient = pit_days >= min_pit_history_days and pit_bars >= min_pit_history_bars
    history_check = {
        "sufficient": history_sufficient,
        "calendar_days": pit_days,
        "trading_days": pit_bars,
        "required_calendar_days": min_pit_history_days,
        "required_trading_days": min_pit_history_bars,
        "details": history_details,
    }
    if not history_sufficient:
        ih_run_id = run_id or "bt-insufficient-history"
        return {
            "status": INSUFFICIENT_HISTORY_STATUS,
            "backtest_run_id": ih_run_id,
            "artifact_ref": f"vectorbt://backtest-runs/{ih_run_id}",
            "backend": PRIMARY_BACKEND,
            "framework": "vectorbt",
            "framework_version": VECTORBT_VERSION_PIN,
            "market": "TW",
            "governed_data": True,
            "dataset_id": str(dataset.get("dataset_id") or ""),
            "source_dataset_refs": list(dataset.get("source_dataset_refs") or []),
            "strategy_artifact": _artifact_identity(strategy_artifact),
            "pit_history_days": pit_days,
            "pit_history_bars": pit_bars,
            "required_pit_history_days": min_pit_history_days,
            "required_pit_history_bars": min_pit_history_bars,
            "history_details": history_details,
            "history_check": history_check,
            "reason": (
                f"Candidate inputs have insufficient point-in-time history: "
                f"{pit_days} calendar days ({pit_bars} bars) < required 2 years "
                f"({min_pit_history_days} calendar days / {min_pit_history_bars} bars)."
            ),
        }

    prepared = GovernedVectorbtInputAdapter().prepare(dataset)
    base_cfg = config or BacktestConfig(strategy_artifact=dict(strategy_artifact))
    bt_config = BacktestConfig(
        version=base_cfg.version,
        requested_by=base_cfg.requested_by,
        strategy_params=dict(strategy_artifact.get("parameters") or {}),
        strategy_artifact=dict(strategy_artifact),
        init_cash=base_cfg.init_cash,
        fees=base_cfg.commission_rate,
        commission_rate=base_cfg.commission_rate,
        transaction_tax_rate=base_cfg.transaction_tax_rate,
        etf_transaction_tax_rate=base_cfg.etf_transaction_tax_rate,
        apply_taiwan_fill_costs=True,
        storage_backend=base_cfg.storage_backend,
        storage_path_template=base_cfg.storage_path_template,
    )

    wanted_norm = {
        normalize_taiwan_symbol(s)
        for s in (strategy_artifact.get("parameters") or {}).get("symbols", [])
    }
    target_instruments = [
        inst
        for inst in prepared.instruments
        if normalize_taiwan_symbol(inst) in wanted_norm
    ]
    benchmark_0050_instruments = [
        inst
        for inst in prepared.instruments
        if inst.split(".")[0].strip() == "0050"
    ]

    total_len = min(prepared.bars_per_instrument[inst] for inst in target_instruments)
    split_idx = int(total_len * in_sample_fraction)
    is_dataset = _slice_prepared_dataset(prepared, 0, split_idx, "in_sample")
    oos_dataset = _slice_prepared_dataset(prepared, split_idx, total_len, "out_of_sample")

    fold_datasets: list[PreparedVectorbtDataset] = []
    for k in range(walk_forward_folds):
        f_start = (k * split_idx) // walk_forward_folds
        f_end = ((k + 1) * split_idx) // walk_forward_folds
        fold_datasets.append(
            _slice_prepared_dataset(prepared, f_start, f_end, f"wf_fold_{k + 1}")
        )

    prev_env = os.environ.get("PANTHEON_VECTORBT_BACKEND")
    os.environ["PANTHEON_VECTORBT_BACKEND"] = "real"
    try:
        runner = backend or VectorbtBackend()
        full_workflow = run_vectorbt_workflow(dataset, backend=runner, config=bt_config)
        is_result = runner.run(is_dataset, bt_config)
        oos_result = runner.run(oos_dataset, bt_config)
        wf_results = [runner.run(f_ds, bt_config) for f_ds in fold_datasets]
    finally:
        if prev_env is None:
            os.environ.pop("PANTHEON_VECTORBT_BACKEND", None)
        else:
            os.environ["PANTHEON_VECTORBT_BACKEND"] = prev_env

    is_summary = _summarize_window_result(
        is_dataset, is_result, window_fraction=in_sample_fraction
    )
    oos_summary = _summarize_window_result(
        oos_dataset, oos_result, window_fraction=round(1.0 - in_sample_fraction, 4)
    )
    wf_summaries: list[dict[str, Any]] = []
    for idx, (f_ds, f_res) in enumerate(zip(fold_datasets, wf_results), start=1):
        fold_summary = _summarize_window_result(
            f_ds, f_res, window_fraction=round(in_sample_fraction / walk_forward_folds, 4)
        )
        fold_summary["fold_index"] = idx
        wf_summaries.append(fold_summary)

    wf_sharpes = [float(f["sharpe_ratio"]) for f in wf_summaries]
    wf_consistent_sign = (
        len(wf_sharpes) == walk_forward_folds
        and all(math.isfinite(s) and s != 0.0 for s in wf_sharpes)
        and (all(s > 0.0 for s in wf_sharpes) or all(s < 0.0 for s in wf_sharpes))
    )
    wf_all_positive = len(wf_sharpes) == walk_forward_folds and all(
        math.isfinite(s) and s > 0.0 for s in wf_sharpes
    )

    bh_same_symbols = _compute_buy_and_hold_benchmark(
        oos_dataset, target_instruments, bt_config
    )
    bh_0050 = _compute_buy_and_hold_benchmark(
        oos_dataset, benchmark_0050_instruments, bt_config
    )

    backtest_run_id = run_id or full_workflow.backtest_result.run_id
    artifact_ref = f"vectorbt://backtest-runs/{backtest_run_id}"
    full_agg = full_workflow.backtest_result.aggregate_metrics
    cost_payload = {
        "applied_on_every_fill": True,
        "commission_rate": round(float(bt_config.commission_rate), 6),
        "transaction_tax_rate": round(float(bt_config.transaction_tax_rate), 6),
        "equity_transaction_tax_rate": round(float(bt_config.transaction_tax_rate), 6),
        "etf_transaction_tax_rate": round(float(bt_config.etf_transaction_tax_rate), 6),
        "in_sample_commission_paid": is_summary["total_commission_paid"],
        "in_sample_transaction_tax_paid": is_summary["total_transaction_tax_paid"],
        "in_sample_total_fees_paid": is_summary["total_fees_paid"],
        "out_of_sample_commission_paid": oos_summary["total_commission_paid"],
        "out_of_sample_transaction_tax_paid": oos_summary["total_transaction_tax_paid"],
        "out_of_sample_total_fees_paid": oos_summary["total_fees_paid"],
        "total_commission_paid": round(
            float(full_agg.get("total_commission_paid", 0.0)), 6
        ),
        "total_transaction_tax_paid": round(
            float(full_agg.get("total_transaction_tax_paid", 0.0)), 6
        ),
        "total_fees_paid": round(float(full_agg.get("total_fees_paid", 0.0)), 6),
        "rate_citations": copy.deepcopy(TAIWAN_COST_RATE_CITATIONS),
        "citations": copy.deepcopy(TAIWAN_COST_RATE_CITATIONS),
    }

    return {
        "status": "completed",
        "backtest_run_id": backtest_run_id,
        "artifact_ref": artifact_ref,
        "registry_id": full_workflow.registry_entry["registry_id"],
        "backend": full_workflow.backtest_result.backend,
        "framework": "vectorbt",
        "framework_version": VECTORBT_VERSION_PIN,
        "market": "TW",
        "governed_data": True,
        "dataset_id": prepared.dataset_id,
        "source_dataset_refs": list(prepared.source_dataset_refs),
        "strategy_artifact": _artifact_identity(strategy_artifact),
        "pit_history_days": pit_days,
        "pit_history_bars": pit_bars,
        "required_pit_history_days": min_pit_history_days,
        "required_pit_history_bars": min_pit_history_bars,
        "history_details": history_details,
        "history_check": history_check,
        "window_protocol": {
            "in_sample_fraction": round(in_sample_fraction, 4),
            "out_of_sample_fraction": round(1.0 - in_sample_fraction, 4),
            "walk_forward_folds": walk_forward_folds,
        },
        "protocol": {
            "in_sample_ratio": round(in_sample_fraction, 4),
            "out_of_sample_ratio": round(1.0 - in_sample_fraction, 4),
            "walk_forward_folds": walk_forward_folds,
            "mandate_max_drawdown": mandate_max_drawdown,
            "min_oos_round_trips": min_oos_round_trips,
        },
        "in_sample": is_summary,
        "out_of_sample": oos_summary,
        "walk_forward_folds": wf_summaries,
        "walk_forward": {
            "folds": wf_summaries,
            "consistent_sign": wf_consistent_sign,
            "all_positive": wf_all_positive,
        },
        "walk_forward_consistent_sign": wf_consistent_sign,
        "walk_forward_all_positive": wf_all_positive,
        "costs": cost_payload,
        "transaction_costs": copy.deepcopy(cost_payload),
        "benchmarks": {
            "buy_and_hold_same_symbols": bh_same_symbols,
            "benchmark_0050": bh_0050,
            "tw_0050": bh_0050,
        },
        "artifact_bundle": full_workflow.artifact_bundle,
        "registry_entry": full_workflow.registry_entry,
    }


def build_canonical_tw_strategy_artifact(
    *,
    strategy_id: str = "strat-tw-replication-001",
    artifact_id: str | None = None,
    version: str = "1.0.0",
    symbols: Sequence[str] = ("2330.TW",),
    lookback_bars: int = 2,
    momentum_threshold: float = 0.0,
    order_quantity: int = 10,
    source_strategy_spec_id: str | None = None,
    source_run_ids: Sequence[str] = ("replication-bootstrap-run-001",),
) -> dict[str, Any]:
    """Build a schema-valid canonical Taiwan StrategyArtifact for replication evaluation."""
    from services.execution.shioaji_adapter import normalize_taiwan_symbol

    tw_symbols: list[str] = []
    for raw_sym in symbols:
        try:
            code, suffix = normalize_taiwan_symbol(raw_sym)
            tw_symbols.append(f"{code}.{'TWO' if suffix in ('TPEX', 'OTC') else 'TW'}")
        except ValueError:
            tw_symbols.append("2330.TW")
    if not tw_symbols:
        tw_symbols = ["2330.TW"]

    resolved_artifact_id = artifact_id or f"artifact-tw-{strategy_id}-{version.replace('.', '-')}"
    lineage: dict[str, Any] = {
        "source_run_ids": list(source_run_ids),
    }
    if source_strategy_spec_id:
        lineage["source_strategy_spec_id"] = source_strategy_spec_id

    return {
        "artifact_id": resolved_artifact_id,
        "strategy_id": strategy_id,
        "version": version,
        "artifact_schema_version": "1.0",
        "lineage": lineage,
        "algorithm_ref": {
            "path": "services/registry/strategy_artifact.py",
            "commit": "1cfaf933fbcc119605a455ebea483d54d2521ebb",
            "engine": "lean",
            "entrypoint": "services.registry.strategy_artifact:evaluate_strategy_action",
            "repository": "ajoe734/pantheon",
            "signal_interface": "services.execution.lean_runtime.paper_signal_producer:Strategy",
            "logic_interpreter": "services.registry.strategy_artifact:evaluate_strategy_action",
            "signal_schema_version": "1.0",
        },
        "strategy_logic": {
            "kind": "close_to_close_momentum",
            "positive_action": "BUY",
            "non_positive_action": "SELL",
            "lookback_parameter": "lookback_bars",
            "threshold_parameter": "momentum_threshold",
        },
        "parameters": {
            "market": "TW",
            "symbols": tw_symbols,
            "data_source": "source-ingest:twse/daily-bars",
            "bar_frequency": "1d",
            "lookback_bars": int(lookback_bars),
            "quantity_type": "SHARES",
            "order_quantity": int(order_quantity),
            "momentum_threshold": float(momentum_threshold),
            "zero_momentum_action": "SELL",
        },
        "provenance_refs": ["task:REPLICATION-GATE-REAL-BACKTEST-20261110"],
        "mutation_surface": {
            "controls": [
                {
                    "parameter_key": "lookback_bars",
                    "value_type": "integer",
                    "current_value": int(lookback_bars),
                    "allowed_range": {"min": 2, "max": 60},
                    "step": 1,
                },
                {
                    "parameter_key": "momentum_threshold",
                    "value_type": "number",
                    "current_value": float(momentum_threshold),
                    "allowed_range": {"min": 0.0, "max": 0.05},
                    "step": 0.001,
                },
            ],
            "immutable_parameters": [
                "symbols",
                "bar_frequency",
                "data_source",
                "order_quantity",
                "quantity_type",
                "zero_momentum_action",
                "market",
            ],
        },
    }


def build_governed_tw_replication_dataset(
    *,
    dataset_id: str = "dataset:tw-governed-daily-v1",
    strategy_id: str = "strat-tw-replication-001",
    source_dataset_refs: Sequence[str] = ("dataset:twse-daily-ohlcv-v1",),
    source_strategy_spec_id: str | None = None,
    symbols: Sequence[str] = ("2330.TWSE",),
    num_bars: int = 760,
    trading_days: int | None = None,
    start_date_iso: str = "2023-01-01",
    up_jump: float = 0.048,
    down_drop: float = -0.046,
    oos_invert: bool = False,
    negative_oos: bool = False,
) -> dict[str, Any]:
    """Build a deterministic governed Taiwan OHLCV dataset (with 0050 benchmark) for replication backtests."""
    from datetime import date as _date, timedelta as _timedelta
    from services.execution.shioaji_adapter import normalize_taiwan_symbol

    effective_bars = int(trading_days) if trading_days is not None else int(num_bars)
    invert_oos = bool(oos_invert or negative_oos)
    start_dt = _date.fromisoformat(start_date_iso)
    split_idx = int(effective_bars * 0.70)
    # 8-bar balanced cycle: 4 BUY steps (+0.2%, +0.2%, +0.2%, +up_jump) and
    # 4 SELL steps (-0.1%, -0.1%, -0.1%, down_drop) so net position resets to 0 every 8 bars.
    is_cycle = [0.002, 0.002, 0.002, float(up_jump), -0.001, -0.001, -0.001, float(down_drop)]
    oos_cycle = (
        [0.002, 0.002, 0.002, -abs(float(up_jump)), -0.001, -0.001, -0.001, abs(float(down_drop))]
        if invert_oos
        else is_cycle
    )

    inst_list: list[str] = []
    for s in symbols:
        try:
            code, suffix = normalize_taiwan_symbol(s)
            inst_name = f"{code}.{'TPEX' if suffix == 'OTC' else 'TWSE'}"
        except ValueError:
            inst_name = "2330.TWSE"
        if inst_name not in inst_list:
            inst_list.append(inst_name)
    if not any(inst.startswith("0050.") for inst in inst_list):
        inst_list.append("0050.TWSE")

    records: list[dict[str, Any]] = []
    for inst in inst_list:
        is_etf = inst.startswith("0050.")
        price = 150.0 if is_etf else 500.0
        for i in range(effective_bars):
            if i > 0:
                if is_etf:
                    # 0050 mild oscillation with near-zero drift
                    step = 0.0015 if (i % 2 == 0) else -0.00148
                else:
                    cycle = oos_cycle if i >= split_idx else is_cycle
                    step = cycle[i % len(cycle)]
                price = max(10.0, round(price * (1.0 + step), 4))
            dt_str = (start_dt + _timedelta(days=i)).isoformat()
            records.append(
                {
                    "instrument": inst,
                    "date": dt_str,
                    "open": round(price * 0.999, 4),
                    "high": round(price * 1.003, 4),
                    "low": round(price * 0.997, 4),
                    "close": round(price, 4),
                    "volume": 2_000_000 + (i % 20) * 10_000,
                }
            )

    payload: dict[str, Any] = {
        "dataset_id": dataset_id,
        "strategy_id": strategy_id,
        "source_dataset_refs": list(source_dataset_refs),
        "data_frequency": "daily",
        "records": records,
    }
    if source_strategy_spec_id:
        payload["source_strategy_spec_id"] = source_strategy_spec_id
    return payload
