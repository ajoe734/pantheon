"""services/broker — Pantheon paper broker sidecar.

Paper order simulation only. Live orders are always rejected (fail-closed).

Gate: BROKER_PAPER_ENABLED=true enables paper simulation (default: false).

Routes
------
POST /api/broker/paper/orders       — submit paper order (gated)
GET  /api/broker/paper/orders       — list paper orders (gated)
GET  /api/broker/paper/orders/{id}  — get paper order status (gated)
POST /api/broker/live/orders        — always rejected (fail-closed)
GET  /healthz /livez /readyz        — health probes
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from paper_simulation import PaperSimulationStore, SimulationError, simulate_paper_order
from quote_pricing import QuotePricer
from streaming_quotes import StreamingQuoteManager
from sinopac.adapter import ShioajiBrokerAdapter, ShioajiBrokerError

_PAPER_ENABLED = os.getenv("BROKER_PAPER_ENABLED", "").lower() in {"1", "true", "yes"}
_LIVE_ENABLED = False  # never enabled; kept explicit for telemetry clarity

_STORE = PaperSimulationStore()

_SHIOAJI_SANDBOX_ENABLED = os.getenv("BROKER_SHIOAJI_SANDBOX_ENABLED", "").lower() in {"1", "true", "yes"}
_TW_QUOTE_SEED = [s.strip() for s in os.getenv("BROKER_TW_QUOTE_SEED", "2330,2317,2454").split(",") if s.strip()]


def _build_quote_pricer() -> QuotePricer:
    """Authoritative live-quote pricer: Shioaji streaming tick primary (when the
    sandbox session is enabled and credentials are set), TWSE MIS fallback.

    The streaming manager subscribes the seed TW execution universe and grows the
    quote list (報價列, <= 500) as the broker prices new symbols.
    """
    manager = None
    if _SHIOAJI_SANDBOX_ENABLED:
        api_key = os.getenv("BROKER_SHIOAJI_API_KEY", "")
        secret_key = os.getenv("BROKER_SHIOAJI_SECRET_KEY", "")
        if api_key and secret_key:
            try:
                manager = StreamingQuoteManager(
                    api_key, secret_key, seed_symbols=_TW_QUOTE_SEED
                )
            except Exception:  # pragma: no cover - never block paper pricing on quote setup
                manager = None
    return QuotePricer(streaming_manager=manager)


_QUOTE_PRICER = _build_quote_pricer()

app = FastAPI(
    title="Pantheon Broker Sidecar",
    version="0.1.0",
    description=(
        "Paper order simulation sidecar. Live orders are always rejected. "
        "Gated by BROKER_PAPER_ENABLED."
    ),
)


@app.get("/healthz")
@app.get("/livez")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "broker",
        "paper_enabled": _PAPER_ENABLED,
        "live_enabled": _LIVE_ENABLED,
    }


@app.get("/readyz")
def readyz() -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "service": "broker",
            "paper_enabled": _PAPER_ENABLED,
            "live_enabled": _LIVE_ENABLED,
        },
    )


def _paper_gate_check() -> Optional[JSONResponse]:
    if not _PAPER_ENABLED:
        return JSONResponse(
            status_code=503,
            content={
                "status": "broker_error",
                "error_code": "PAPER_ADAPTER_DISABLED",
                "message": (
                    "Paper broker is disabled. "
                    "Set BROKER_PAPER_ENABLED=true to enable paper simulation."
                ),
            },
        )
    return None


class PaperOrderRequest(BaseModel):
    capital_pool_id: str
    strategy_id: str
    symbol: str
    qty: float
    side: str
    order_type: str = "market"
    limit_price: Optional[float] = None
    market_price: Optional[float] = None


@app.post("/api/broker/paper/orders")
def submit_paper_order(req: PaperOrderRequest) -> JSONResponse:
    gate = _paper_gate_check()
    if gate is not None:
        return gate
    market_price = req.market_price
    if req.order_type == "market" and (market_price is None or market_price <= 0):
        market_price = _QUOTE_PRICER.market_price(req.symbol)
    try:
        order = simulate_paper_order(
            capital_pool_id=req.capital_pool_id,
            strategy_id=req.strategy_id,
            symbol=req.symbol,
            qty=req.qty,
            side=req.side,
            order_type=req.order_type,
            limit_price=req.limit_price,
            market_price=market_price,
        )
        _STORE.submit(order)
        return JSONResponse(status_code=201, content={"status": "ok", "order": order.to_dict()})
    except SimulationError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())


@app.get("/api/broker/quote-debug")
def quote_debug() -> JSONResponse:
    """Read-only streaming quote diagnostics: session health, subscription list
    (報價列), live tick price map, reconnect count. Used to verify the live
    tick feed during market hours."""
    mgr = getattr(_QUOTE_PRICER, "_streaming", None)
    if mgr is None:
        return JSONResponse(status_code=200, content={"streaming": "disabled"})
    return JSONResponse(status_code=200, content={
        "streaming": "enabled",
        "session_ok": bool(mgr.session_ok),
        "quote_list": mgr.quote_list,
        "quote_list_size": len(mgr.quote_list),
        "reconnect_count": mgr.reconnect_count,
        "prices": mgr.prices_snapshot(),
    })


@app.get("/api/broker/paper/orders")
def list_paper_orders(
    capital_pool_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    limit: int = 100,
) -> JSONResponse:
    gate = _paper_gate_check()
    if gate is not None:
        return gate
    orders = _STORE.list_orders(
        capital_pool_id=capital_pool_id,
        strategy_id=strategy_id,
        limit=limit,
    )
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "orders": [o.to_dict() for o in orders]},
    )


@app.get("/api/broker/paper/orders/{order_id}")
def get_paper_order(order_id: str) -> JSONResponse:
    gate = _paper_gate_check()
    if gate is not None:
        return gate
    order = _STORE.get(order_id)
    if order is None:
        return JSONResponse(
            status_code=404,
            content={
                "status": "broker_error",
                "error_code": "ORDER_NOT_FOUND",
                "message": f"Paper order {order_id!r} not found.",
            },
        )
    return JSONResponse(status_code=200, content={"status": "ok", "order": order.to_dict()})


@app.post("/api/broker/live/orders")
def reject_live_order() -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={
            "status": "broker_error",
            "error_code": "LIVE_ADAPTER_DISABLED",
            "message": (
                "Live broker execution is disabled. "
                "Live orders are not accepted in this deployment."
            ),
            "live_enabled": False,
            "deployment_stage": "paper",
        },
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8102"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
