"""Paper order simulation engine for the Pantheon broker sidecar.

Simulates order fills without real broker connectivity.
All orders are paper-only: no real capital, no real orders.

Key invariants:
- sim_fill_flag is always True
- is_real_order is always False
- is_real_capital is always False
- deployment_stage is always "paper"
- Live order requests always raise SimulationError
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class SimulationError(Exception):
    def __init__(self, error_code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code

    def to_payload(self) -> Dict[str, Any]:
        return {
            "status": "broker_error",
            "error_code": self.error_code,
            "message": self.message,
        }


@dataclasses.dataclass
class PaperOrder:
    order_id: str
    capital_pool_id: str
    strategy_id: str
    symbol: str
    qty: float
    side: str
    order_type: str
    limit_price: Optional[float]
    created_at: str
    filled_at: Optional[str]
    fill_price: Optional[float]
    fill_qty: float
    status: str
    sim_fill_flag: bool = True
    is_real_order: bool = False
    is_real_capital: bool = False
    deployment_stage: str = "paper"
    reject_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class PaperSimulationStore:
    """In-memory paper order store with JSONL file persistence."""

    def __init__(self, store_path: Optional[str] = None) -> None:
        if store_path is None:
            raw = os.getenv("BROKER_PAPER_STORE_PATH", "")
            store_path = raw or "/tmp/pantheon/broker/paper_orders.jsonl"
        self._path = Path(store_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._orders: Dict[str, PaperOrder] = {}
        self._lock = threading.Lock()
        self._load_existing()

    def _load_existing(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        order = PaperOrder(**data)
                        self._orders[order.order_id] = order
                    except Exception:
                        pass
        except OSError:
            pass

    def _append(self, order: PaperOrder) -> None:
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(order.to_dict(), separators=(",", ":")) + "\n")
        except OSError:
            pass

    def submit(self, order: PaperOrder) -> None:
        with self._lock:
            self._orders[order.order_id] = order
            self._append(order)

    def get(self, order_id: str) -> Optional[PaperOrder]:
        with self._lock:
            return self._orders.get(order_id)

    def list_orders(
        self,
        capital_pool_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[PaperOrder]:
        with self._lock:
            orders = list(self._orders.values())
        if capital_pool_id:
            orders = [o for o in orders if o.capital_pool_id == capital_pool_id]
        if strategy_id:
            orders = [o for o in orders if o.strategy_id == strategy_id]
        return sorted(orders, key=lambda o: o.created_at)[-limit:]


def simulate_paper_order(
    *,
    capital_pool_id: str,
    strategy_id: str,
    symbol: str,
    qty: float,
    side: str,
    order_type: str = "market",
    limit_price: Optional[float] = None,
) -> PaperOrder:
    """Simulate a paper order fill synchronously.

    Market orders fill at a placeholder price of 100.0; the real market price
    comes from the strategy's market data feed, not the broker sidecar.
    Limit orders fill at the specified limit_price.
    All fills are 100% of requested qty.
    """
    if side not in ("buy", "sell"):
        raise SimulationError("INVALID_SIDE", f"side must be 'buy' or 'sell', got {side!r}")
    if order_type not in ("market", "limit"):
        raise SimulationError("INVALID_ORDER_TYPE", f"order_type must be 'market' or 'limit', got {order_type!r}")
    if qty <= 0:
        raise SimulationError("INVALID_QTY", "qty must be positive")
    if order_type == "limit" and (limit_price is None or limit_price <= 0):
        raise SimulationError("INVALID_LIMIT_PRICE", "limit_price must be positive for limit orders")

    now = _utc_now_iso()
    fill_price = limit_price if order_type == "limit" else 100.0
    return PaperOrder(
        order_id=uuid.uuid4().hex,
        capital_pool_id=capital_pool_id,
        strategy_id=strategy_id,
        symbol=symbol,
        qty=qty,
        side=side,
        order_type=order_type,
        limit_price=limit_price,
        created_at=now,
        filled_at=now,
        fill_price=fill_price,
        fill_qty=qty,
        status="filled",
    )
