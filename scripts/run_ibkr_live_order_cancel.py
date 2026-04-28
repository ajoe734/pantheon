#!/usr/bin/env python3
"""Run the smallest operator-supervised IBKR live order/cancel proof.

This script places one live limit order and immediately cancels it. It is
intentionally narrow and requires an explicit live-order acknowledgement flag.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ACK_STATES = {"accepted", "open", "submitted", "presubmitted", "pendingcancel", "pending_submit", "pendingsubmit"}
CANCEL_STATES = {"cancelled", "canceled", "apicancelled", "inactive", "cancel_confirmed"}


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalize_state(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "").replace("_", "")


def validate_request_shape(account: str, symbol: str, quantity: int, limit_price: float) -> None:
    if not account:
        raise ValueError("account is required")
    if symbol.upper() != "AAPL":
        raise ValueError("only AAPL is allowed for this EP5-002 minimal proof")
    if quantity != 1:
        raise ValueError("quantity must be exactly 1")
    if limit_price <= 0:
        raise ValueError("limit_price must be positive")


def ibapi_modules() -> tuple[Any, Any, Any, Any]:
    try:
        from ibapi.client import EClient
        from ibapi.contract import Contract
        from ibapi.order import Order
        from ibapi.wrapper import EWrapper
    except ImportError as exc:  # pragma: no cover - runtime-only dependency
        raise SystemExit(
            "ibapi is required. Use the prepared venv or install ibapi==9.81.1.post1."
        ) from exc
    return EClient, EWrapper, Contract, Order


class IBKRLiveOrderCancel:
    def __init__(self, *, account: str, symbol: str, quantity: int, limit_price: float):
        EClient, EWrapper, _Contract, _Order = ibapi_modules()

        class App(EWrapper, EClient):  # type: ignore[misc, valid-type]
            def __init__(self) -> None:
                EClient.__init__(self, self)
                self.account = account
                self.symbol = symbol
                self.quantity = quantity
                self.limit_price = limit_price
                self.connected_at: str | None = None
                self.next_order_id: int | None = None
                self.order_id: int | None = None
                self.status_events: list[dict[str, Any]] = []
                self.open_order_events: list[dict[str, Any]] = []
                self.errors: list[dict[str, Any]] = []
                self.next_valid_done = threading.Event()
                self.submit_seen = threading.Event()
                self.cancel_seen = threading.Event()

            def connectAck(self) -> None:  # noqa: N802
                self.connected_at = iso_now()
                self.startApi()

            def nextValidId(self, orderId: int) -> None:  # noqa: N802
                self.next_order_id = orderId
                self.next_valid_done.set()

            def openOrder(self, orderId: int, contract: Any, order: Any, orderState: Any) -> None:  # noqa: N802
                status = str(getattr(orderState, "status", "") or "")
                self.open_order_events.append(
                    {
                        "captured_at": iso_now(),
                        "order_id": orderId,
                        "status": status,
                        "symbol": getattr(contract, "symbol", None),
                        "action": getattr(order, "action", None),
                        "total_quantity": getattr(order, "totalQuantity", None),
                        "order_type": getattr(order, "orderType", None),
                        "limit_price": getattr(order, "lmtPrice", None),
                    }
                )
                if normalize_state(status) in {normalize_state(item) for item in ACK_STATES}:
                    self.submit_seen.set()

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
                event = {
                    "captured_at": iso_now(),
                    "order_id": orderId,
                    "status": status,
                    "filled": filled,
                    "remaining": remaining,
                    "avg_fill_price": avgFillPrice,
                    "perm_id": permId,
                    "parent_id": parentId,
                    "last_fill_price": lastFillPrice,
                    "client_id": clientId,
                    "why_held": whyHeld,
                    "mkt_cap_price": mktCapPrice,
                }
                self.status_events.append(event)
                normalized = normalize_state(status)
                if normalized in {normalize_state(item) for item in ACK_STATES}:
                    self.submit_seen.set()
                if normalized in {normalize_state(item) for item in CANCEL_STATES}:
                    self.cancel_seen.set()

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

        self.app = App()
        self.Contract = _Contract
        self.Order = _Order

    def build_contract(self) -> Any:
        contract = self.Contract()
        contract.symbol = self.app.symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        return contract

    def build_order(self) -> Any:
        order = self.Order()
        order.action = "BUY"
        order.totalQuantity = self.app.quantity
        order.orderType = "LMT"
        order.lmtPrice = self.app.limit_price
        order.tif = "DAY"
        order.account = self.app.account
        order.outsideRth = False
        # ibapi 9.81 defaults these deprecated SMART-routing flags to True.
        # TWS rejects that shape with error 10268 before an order is accepted.
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        order.transmit = True
        return order


def latest_status(events: list[dict[str, Any]], states: set[str]) -> dict[str, Any] | None:
    normalized_states = {normalize_state(item) for item in states}
    for event in reversed(events):
        if normalize_state(event.get("status")) in normalized_states:
            return event
    return None


def run_live_order_cancel(args: argparse.Namespace) -> dict[str, Any]:
    if not args.i_understand_live_order:
        raise SystemExit("--i-understand-live-order is required to place a live order")
    validate_request_shape(args.account, args.symbol, args.quantity, args.limit_price)

    harness = IBKRLiveOrderCancel(
        account=args.account,
        symbol=args.symbol,
        quantity=args.quantity,
        limit_price=args.limit_price,
    )
    app = harness.app
    app.connect(args.host, args.port, args.client_id)
    thread = threading.Thread(target=app.run, daemon=True, name="ibkr-live-order-cancel")
    thread.start()

    if not app.next_valid_done.wait(timeout=args.connect_timeout_seconds):
        app.disconnect()
        thread.join(timeout=2)
        raise RuntimeError("IBKR did not return nextValidId before timeout")

    order_id = args.order_id if args.order_id is not None else app.next_order_id
    if order_id is None:
        app.disconnect()
        thread.join(timeout=2)
        raise RuntimeError("missing order id")

    app.order_id = int(order_id)
    submitted_at = iso_now()
    app.placeOrder(int(order_id), harness.build_contract(), harness.build_order())
    app.submit_seen.wait(timeout=args.submit_timeout_seconds)
    time.sleep(args.cancel_after_seconds)

    cancel_requested_at = iso_now()
    app.cancelOrder(int(order_id))
    app.cancel_seen.wait(timeout=args.cancel_timeout_seconds)
    canceled_at = iso_now()

    time.sleep(0.5)
    app.disconnect()
    thread.join(timeout=2)

    submit_status = latest_status(app.status_events, ACK_STATES) or latest_status(app.open_order_events, ACK_STATES)
    cancel_status = latest_status(app.status_events, CANCEL_STATES)
    last_status = app.status_events[-1] if app.status_events else None
    filled_qty = max([float(event.get("filled") or 0.0) for event in app.status_events] or [0.0])
    remaining = None
    if app.status_events:
        remaining = app.status_events[-1].get("remaining")

    packet_dir = Path(args.packet_dir)
    submit_response = {
        "order_id": str(order_id),
        "order_status": (submit_status or {}).get("status") or "unknown",
        "captured_at": submitted_at,
        "source": "scripts/run_ibkr_live_order_cancel.py",
        "broker": "IBKR",
        "client_id": args.client_id,
        "account": args.account,
        "filled": filled_qty,
        "remaining": remaining,
        "status_events": app.status_events,
        "open_order_events": app.open_order_events,
        "errors": app.errors,
    }
    cancel_response = {
        "order_id": str(order_id),
        "order_status": (cancel_status or {}).get("status") or "unknown",
        "captured_at": canceled_at,
        "source": "scripts/run_ibkr_live_order_cancel.py",
        "broker": "IBKR",
        "filled": filled_qty,
        "remaining": remaining,
        "status_events": app.status_events,
        "errors": app.errors,
    }
    run_summary = {
        "task_id": "EP5-002",
        "generated_at": iso_now(),
        "status": "cancel_confirmed" if cancel_status else "cancel_pending_or_unconfirmed",
        "proof_boundary": "operator_supervised_ibkr_api_harness",
        "host": args.host,
        "port": args.port,
        "client_id": args.client_id,
        "order_id": str(order_id),
        "submitted_at": submitted_at,
        "cancel_requested_at": cancel_requested_at,
        "canceled_at": canceled_at,
        "filled": filled_qty,
        "submit_status": submit_response["order_status"],
        "cancel_status": cancel_response["order_status"],
        "last_observed_status": (last_status or {}).get("status"),
        "notes": [
            "This script places one live IBKR limit order and cancels it immediately.",
            "It is a broker API harness, not a runtime-manager route.",
            "Operator must verify TWS visible state and no-fill disposition before EP5-002 closeout.",
        ],
    }

    dump_json(packet_dir / "live-order-submit.response.json", submit_response)
    dump_json(packet_dir / "live-order-cancel.request.json", {"body": {"order_id": str(order_id)}})
    dump_json(packet_dir / "live-order-cancel.response.json", cancel_response)
    dump_json(packet_dir / "ibkr-live-order-cancel-run.json", run_summary)
    return run_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Place and cancel one supervised IBKR live limit order.")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--client-id", required=True, type=int)
    parser.add_argument("--account", required=True)
    parser.add_argument("--packet-dir", required=True)
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--limit-price", type=float, required=True)
    parser.add_argument("--order-id", type=int, default=None)
    parser.add_argument("--connect-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--submit-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--cancel-after-seconds", type=float, default=2.0)
    parser.add_argument("--cancel-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--i-understand-live-order", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result = run_live_order_cancel(args)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "cancel_confirmed" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
