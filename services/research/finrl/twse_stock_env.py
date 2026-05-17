"""TWSE OHLCV adapter for FinRL's StockTradingEnv.

The production evidence path must stay import-safe in repo-local CI where the
upstream FinRL package may not be installed. This module therefore normalizes
TWSE records into the dataframe/parameter shape expected by StockTradingEnv and
uses a small deterministic fallback environment unless ``use_finrl=True`` can
construct the real upstream env.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

try:
    from finrl.meta.env_stock_trading.env_stocktrading import StockTradingEnv as _FinRLStockTradingEnv
except ImportError:  # FinRL is optional in repo-local CI; the adapter remains offline-safe.
    _FinRLStockTradingEnv = None

try:
    import pandas as _pd
except ImportError:  # pragma: no cover - pandas is service-local but optional for import safety.
    _pd = None


REQUIRED_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")
DEFAULT_TECH_INDICATORS = ("daily_return", "volume_change")


class TWSEStockEnvError(ValueError):
    """Raised when TWSE OHLCV cannot be adapted to the governed FinRL env."""


def normalize_twse_ohlcv(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return FinRL-ready rows with ``tic`` plus deterministic tech indicators."""

    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)) or not records:
        raise TWSEStockEnvError("records must be a non-empty sequence of OHLCV mappings")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for raw in records:
        if not isinstance(raw, Mapping):
            raise TWSEStockEnvError("each record must be a mapping")
        instrument = str(raw.get("instrument") or raw.get("tic") or "").strip()
        date_value = str(raw.get("date") or "").strip()
        if not instrument:
            raise TWSEStockEnvError("record.instrument is required")
        if not date_value:
            raise TWSEStockEnvError("record.date is required")
        normalized = {"date": date_value, "instrument": instrument, "tic": instrument}
        for field in REQUIRED_OHLCV_FIELDS:
            value = raw.get(field)
            if not isinstance(value, (int, float)):
                raise TWSEStockEnvError(f"record for {instrument} missing numeric {field}")
            normalized[field] = float(value)
        grouped.setdefault(instrument, []).append(normalized)

    rows: list[dict[str, Any]] = []
    for instrument in sorted(grouped):
        previous_close: float | None = None
        previous_volume: float | None = None
        for row in sorted(grouped[instrument], key=lambda item: str(item["date"])):
            close = float(row["close"])
            volume = float(row["volume"])
            row["daily_return"] = 0.0 if previous_close is None else (close - previous_close) / (previous_close + 1e-9)
            row["volume_change"] = 0.0 if previous_volume is None else (volume - previous_volume) / (previous_volume + 1e-9)
            row["turbulence"] = 0.0
            rows.append(row)
            previous_close = close
            previous_volume = volume

    rows.sort(key=lambda item: (str(item["date"]), str(item["tic"])))
    return rows


class TWSESerialEnv:
    """Import-safe TWSE adapter around FinRL's StockTradingEnv."""

    def __init__(self, records: Any, **kwargs: Any):
        min_instruments = int(kwargs.pop("min_instruments", 5))
        self.records = normalize_twse_ohlcv(records)
        self.instruments = tuple(sorted({str(row["tic"]) for row in self.records}))
        if len(self.instruments) < min_instruments:
            raise TWSEStockEnvError(
                f"TWSE StockTradingEnv requires at least {min_instruments} instruments; "
                f"got {len(self.instruments)}"
            )
        self.dates = tuple(dict.fromkeys(str(row["date"]) for row in self.records))
        self.tech_indicator_list = tuple(kwargs.pop("tech_indicator_list", DEFAULT_TECH_INDICATORS))
        self.initial_amount = float(kwargs.pop("initial_amount", 1_000_000.0))
        self.hmax = int(kwargs.pop("hmax", 100))
        self.buy_cost_pct = float(kwargs.pop("buy_cost_pct", 0.001425))
        self.sell_cost_pct = float(kwargs.pop("sell_cost_pct", 0.004425))
        self.reward_scaling = float(kwargs.pop("reward_scaling", 1e-4))
        self.stock_dim = len(self.instruments)
        self.action_space = self.stock_dim
        self.state_space = 1 + (2 + len(self.tech_indicator_list)) * self.stock_dim
        self.cash = self.initial_amount
        self.holdings = {instrument: 0.0 for instrument in self.instruments}
        self.portfolio_value = self.initial_amount
        self._cursor = 0
        use_finrl = bool(kwargs.pop("use_finrl", False))
        self.kwargs = kwargs
        self._env = None
        if use_finrl and _FinRLStockTradingEnv is not None:
            self._env = _FinRLStockTradingEnv(**self._finrl_env_kwargs(kwargs))

    @property
    def finrl_available(self) -> bool:
        return self._env is not None

    def environment_summary(self) -> dict[str, Any]:
        return {
            "wrapper": "TWSESerialEnv",
            "upstream_env": "finrl.meta.env_stock_trading.env_stocktrading.StockTradingEnv",
            "finrl_available": self.finrl_available,
            "num_instruments": self.stock_dim,
            "num_periods": len(self.dates),
            "state_space": self.state_space,
            "action_space": self.action_space,
            "tech_indicator_list": list(self.tech_indicator_list),
            "cpu_only": True,
        }

    def reset(self):
        if self._env is not None:
            return self._env.reset()
        self.cash = self.initial_amount
        self.holdings = {instrument: 0.0 for instrument in self.instruments}
        self.portfolio_value = self.initial_amount
        self._cursor = 0
        return self._observation()

    def step(self, actions):
        if self._env is not None:
            return self._env.step(actions)
        if self._cursor >= len(self.dates) - 1:
            return self._observation(), 0.0, True, {"portfolio_value": round(self.portfolio_value, 6)}

        action_values = list(actions) if isinstance(actions, Sequence) and not isinstance(actions, (str, bytes)) else []
        action_values = (action_values + [0.0] * self.stock_dim)[: self.stock_dim]
        current_prices = self._prices_for_cursor(self._cursor)
        previous_value = self.portfolio_value

        for instrument, raw_action in zip(self.instruments, action_values):
            action = max(-1.0, min(1.0, float(raw_action)))
            price = current_prices[instrument]
            if action > 0 and self.cash > 0:
                budget = min(self.cash, self.cash * action / self.stock_dim)
                shares = budget / (price * (1.0 + self.buy_cost_pct))
                self.cash -= shares * price * (1.0 + self.buy_cost_pct)
                self.holdings[instrument] += shares
            elif action < 0 and self.holdings[instrument] > 0:
                shares = min(self.holdings[instrument], self.holdings[instrument] * abs(action))
                self.cash += shares * price * (1.0 - self.sell_cost_pct)
                self.holdings[instrument] -= shares

        self._cursor += 1
        self.portfolio_value = self._portfolio_value(self._prices_for_cursor(self._cursor))
        reward = (self.portfolio_value - previous_value) * self.reward_scaling
        done = self._cursor >= len(self.dates) - 1
        return self._observation(), reward, done, {"portfolio_value": round(self.portfolio_value, 6)}

    def _finrl_env_kwargs(self, overrides: Mapping[str, Any]) -> dict[str, Any]:
        if _pd is None:
            raise RuntimeError("pandas is required to construct FinRL StockTradingEnv")
        env_kwargs = {
            "df": _pd.DataFrame(self.records),
            "stock_dim": self.stock_dim,
            "hmax": self.hmax,
            "initial_amount": self.initial_amount,
            "num_stock_shares": [0] * self.stock_dim,
            "buy_cost_pct": [self.buy_cost_pct] * self.stock_dim,
            "sell_cost_pct": [self.sell_cost_pct] * self.stock_dim,
            "reward_scaling": self.reward_scaling,
            "state_space": self.state_space,
            "action_space": self.action_space,
            "tech_indicator_list": list(self.tech_indicator_list),
            "turbulence_threshold": None,
            "risk_indicator_col": "turbulence",
            "make_plots": False,
            "print_verbosity": 0,
        }
        env_kwargs.update(dict(overrides))
        return env_kwargs

    def _prices_for_cursor(self, cursor: int) -> dict[str, float]:
        date_value = self.dates[cursor]
        prices = {str(row["tic"]): float(row["close"]) for row in self.records if row["date"] == date_value}
        missing = [instrument for instrument in self.instruments if instrument not in prices]
        if missing:
            raise TWSEStockEnvError("missing prices for " + ", ".join(missing))
        return prices

    def _portfolio_value(self, prices: Mapping[str, float]) -> float:
        return self.cash + sum(self.holdings[instrument] * prices[instrument] for instrument in self.instruments)

    def _observation(self) -> dict[str, Any]:
        prices = self._prices_for_cursor(self._cursor)
        self.portfolio_value = self._portfolio_value(prices)
        return {
            "date": self.dates[self._cursor],
            "cash": round(self.cash, 6),
            "holdings": {instrument: round(self.holdings[instrument], 8) for instrument in self.instruments},
            "close": {instrument: round(prices[instrument], 6) for instrument in self.instruments},
            "portfolio_value": round(self.portfolio_value, 6),
            "records": len(self.records),
            "stock_dim": self.stock_dim,
            "state_space": self.state_space,
        }


TWSEStockTradingEnv = TWSESerialEnv
