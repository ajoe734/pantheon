"""TWSE FinRL StockTradingEnv wrapper with an import-safe fallback."""
from __future__ import annotations

from typing import Any

try:
    from finrl.meta.env_stock_trading.env_stocktrading import StockTradingEnv as _FinRLStockTradingEnv
except ImportError:  # FinRL is optional in repo-local CI; the adapter remains offline-safe.
    _FinRLStockTradingEnv = None


class TWSESerialEnv:
    """Thin TWSE adapter around FinRL's StockTradingEnv when explicitly enabled."""

    def __init__(self, records: Any, **kwargs: Any):
        self.records = records
        use_finrl = bool(kwargs.pop("use_finrl", False))
        self.kwargs = kwargs
        self._env = None
        if use_finrl and _FinRLStockTradingEnv is not None:
            self._env = _FinRLStockTradingEnv(records, **kwargs)

    @property
    def finrl_available(self) -> bool:
        return self._env is not None

    def reset(self):
        if self._env is not None:
            return self._env.reset()
        return {"records": len(self.records) if hasattr(self.records, "__len__") else None}

    def step(self, actions):
        if self._env is not None:
            return self._env.step(actions)
        raise RuntimeError("FinRL StockTradingEnv is not installed; TWSESerialEnv is metadata-only.")
