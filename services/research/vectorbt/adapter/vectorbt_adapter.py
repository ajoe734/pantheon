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
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

VECTORBT_VERSION_PIN = "0.26.2"
PRIMARY_BACKEND = "vectorbt_portfolio"
STUB_BACKEND = "stub_backtest"
REQUIRED_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")
MIN_INSTRUMENTS = 2
MIN_BARS = 30


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
    short_window: int = 5
    long_window: int = 20
    init_cash: float = 100_000.0
    fees: float = 0.001
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

        # Collect (date_str, ohlcv_tuple) pairs per instrument so we can sort chronologically.
        instrument_pairs: dict[
            str, list[tuple[str, tuple[float, float, float, float, float]]]
        ] = {}
        for rec in records:
            if not isinstance(rec, Mapping):
                raise VectorbtWorkflowError("Each record must be a mapping")
            instrument = self._req_str(rec, "instrument")
            date_raw = rec.get("date", "")
            date_val: str = str(date_raw).strip() if date_raw is not None else ""
            bar: list[float] = []
            for f in REQUIRED_OHLCV_FIELDS:
                v = rec.get(f)
                if not isinstance(v, (int, float)):
                    raise VectorbtWorkflowError(
                        f"record for {instrument} missing numeric field '{f}'"
                    )
                bar.append(float(v))
            instrument_pairs.setdefault(instrument, []).append(
                (date_val, (bar[0], bar[1], bar[2], bar[3], bar[4]))
            )

        instruments = tuple(sorted(instrument_pairs.keys()))
        if len(instruments) < MIN_INSTRUMENTS:
            raise VectorbtWorkflowError(
                f"dataset must have at least {MIN_INSTRUMENTS} instruments; got {len(instruments)}"
            )

        bars_per_instrument: dict[str, int] = {}
        ohlcv_by_instrument: dict[str, tuple[tuple[float, float, float, float, float], ...]] = {}
        for inst in instruments:
            # Sort chronologically by ISO date string; empty strings sort before dated records.
            sorted_pairs = sorted(instrument_pairs[inst], key=lambda p: p[0])
            bars = [p[1] for p in sorted_pairs]
            if len(bars) < MIN_BARS:
                raise VectorbtWorkflowError(
                    f"instrument {inst} has {len(bars)} bars; minimum is {MIN_BARS}"
                )
            bars_per_instrument[inst] = len(bars)
            ohlcv_by_instrument[inst] = tuple(bars)

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
) -> dict[str, Any]:
    """MA-crossover backtest on a single instrument's OHLCV bars."""
    closes = [b[3] for b in bars]
    short_ma = _sma(closes, config.short_window)
    long_ma = _sma(closes, config.long_window)

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
        run_id = f"vbt-stub-{uuid.uuid4().hex[:12]}"
        per_instrument: dict[str, dict[str, Any]] = {}
        for inst in dataset.instruments:
            bars = dataset.ohlcv_by_instrument[inst]
            per_instrument[inst] = _compute_instrument_metrics(bars, config)

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


class VectorbtBackend:
    """Real upstream backend using vbt.Portfolio.from_signals().

    Requires: pip install vectorbt==0.26.2
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

        run_id = f"vbt-real-{uuid.uuid4().hex[:12]}"
        per_instrument: dict[str, dict[str, Any]] = {}

        for inst in dataset.instruments:
            bars = dataset.ohlcv_by_instrument[inst]
            closes = pd.Series([b[3] for b in bars])
            short_ma = closes.rolling(config.short_window).mean()
            long_ma = closes.rolling(config.long_window).mean()
            entries = (short_ma > long_ma) & (short_ma.shift(1) <= long_ma.shift(1))
            exits = (short_ma < long_ma) & (short_ma.shift(1) >= long_ma.shift(1))
            pf = vbt.Portfolio.from_signals(
                closes,
                entries=entries,
                exits=exits,
                init_cash=config.init_cash,
                fees=config.fees,
            )
            stats = pf.stats()
            per_instrument[inst] = {
                "total_return": round(float(pf.total_return()), 6),
                "sharpe_ratio": round(float(pf.sharpe_ratio()), 6),
                "max_drawdown": round(float(pf.max_drawdown()), 6),
                "final_portfolio_value": round(float(pf.final_value()), 4),
                "trade_count": int(pf.trade_records_readable.shape[0]),
                "num_bars": len(bars),
            }

        returns = [m["total_return"] for m in per_instrument.values()]
        sharpes = [m["sharpe_ratio"] for m in per_instrument.values()]
        drawdowns = [m["max_drawdown"] for m in per_instrument.values()]
        aggregate: dict[str, Any] = {
            "num_instruments": dataset.num_instruments,
            "mean_total_return": round(sum(returns) / len(returns), 6) if returns else 0.0,
            "mean_sharpe_ratio": round(sum(sharpes) / len(sharpes), 6) if sharpes else 0.0,
            "mean_max_drawdown": round(sum(drawdowns) / len(drawdowns), 6) if drawdowns else 0.0,
            "total_trades": sum(m["trade_count"] for m in per_instrument.values()),
        }
        return BacktestRunResult(
            backend=PRIMARY_BACKEND,
            run_id=run_id,
            per_instrument_metrics=per_instrument,
            aggregate_metrics=aggregate,
            notes=(f"vectorbt {VECTORBT_VERSION_PIN} MA-crossover backtest complete.",),
        )


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


def _build_artifact_bundle(
    dataset: PreparedVectorbtDataset,
    result: BacktestRunResult,
    config: BacktestConfig,
) -> dict[str, Any]:
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
            "short_window": config.short_window,
            "long_window": config.long_window,
            "init_cash": config.init_cash,
            "fees": config.fees,
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
            "strategy": "ma_crossover",
            "short_window": config.short_window,
            "long_window": config.long_window,
            "num_instruments": dataset.num_instruments,
            "total_bars": dataset.total_bars,
            "data_frequency": dataset.data_frequency,
        },
        "approved_at": None,
        "approver": None,
        "rollback_target": None,
    }
