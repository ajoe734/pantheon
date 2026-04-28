#!/usr/bin/env python3
"""Read-only IBKR open-order probe for live-order safety readback."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
except ImportError as exc:  # pragma: no cover - runtime-only dependency
    raise SystemExit("ibapi is required. Install ibapi==9.81.1.post1.") from exc


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class OpenOrdersProbe(EWrapper, EClient):
    def __init__(self) -> None:
        EClient.__init__(self, self)
        self.connected_at: str | None = None
        self.next_valid_order_id: int | None = None
        self.open_orders: list[dict[str, Any]] = []
        self.status_events: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.next_valid_done = threading.Event()
        self.open_order_end = threading.Event()

    def connectAck(self) -> None:  # noqa: N802
        self.connected_at = iso_now()
        self.startApi()

    def nextValidId(self, orderId: int) -> None:  # noqa: N802
        self.next_valid_order_id = orderId
        self.next_valid_done.set()

    def openOrder(self, orderId: int, contract: Any, order: Any, orderState: Any) -> None:  # noqa: N802
        self.open_orders.append(
            {
                "captured_at": iso_now(),
                "order_id": orderId,
                "symbol": getattr(contract, "symbol", None),
                "sec_type": getattr(contract, "secType", None),
                "exchange": getattr(contract, "exchange", None),
                "currency": getattr(contract, "currency", None),
                "action": getattr(order, "action", None),
                "total_quantity": getattr(order, "totalQuantity", None),
                "order_type": getattr(order, "orderType", None),
                "limit_price": getattr(order, "lmtPrice", None),
                "account": getattr(order, "account", None),
                "status": getattr(orderState, "status", None),
            }
        )

    def openOrderEnd(self) -> None:  # noqa: N802
        self.open_order_end.set()

    def orderStatus(  # noqa: N802
        self,
        orderId: int,
        status: str,
        filled: float,
        remaining: float,
        avgFillPrice: float,
        permId: int,
        parentId: int,
        lastFillPrice: float,
        clientId: int,
        whyHeld: str,
        mktCapPrice: float = 0.0,
    ) -> None:
        self.status_events.append(
            {
                "captured_at": iso_now(),
                "order_id": orderId,
                "status": status,
                "filled": filled,
                "remaining": remaining,
                "avg_fill_price": avgFillPrice,
                "perm_id": permId,
                "client_id": clientId,
            }
        )

    def error(  # noqa: A003,N802
        self,
        reqId: int,
        errorCode: int,
        errorString: str,
        advancedOrderRejectJson: str = "",
    ) -> None:
        self.errors.append(
            {
                "captured_at": iso_now(),
                "req_id": reqId,
                "error_code": errorCode,
                "error": errorString,
                "advanced_reject_json": advancedOrderRejectJson or None,
            }
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe IBKR open orders without placing orders.")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--client-id", required=True, type=int)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args(argv)

    probe = OpenOrdersProbe()
    probe.connect(args.host, args.port, args.client_id)
    thread = threading.Thread(target=probe.run, daemon=True, name="ibkr-open-orders-probe")
    thread.start()

    if probe.next_valid_done.wait(timeout=args.timeout_seconds):
        probe.reqOpenOrders()
        probe.reqAllOpenOrders()
        probe.open_order_end.wait(timeout=args.timeout_seconds)
        time.sleep(1.0)

    probe.disconnect()
    thread.join(timeout=2.0)

    payload = {
        "status": "ok" if probe.next_valid_order_id is not None else "partial",
        "generated_at": iso_now(),
        "host": args.host,
        "port": args.port,
        "client_id": args.client_id,
        "connected_at": probe.connected_at,
        "next_valid_order_id": probe.next_valid_order_id,
        "open_order_count": len(probe.open_orders),
        "open_orders": probe.open_orders,
        "status_events": probe.status_events,
        "errors": probe.errors,
        "notes": ["This probe is read-only and does not place, modify, or cancel orders."],
    }
    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
