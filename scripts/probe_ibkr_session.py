#!/usr/bin/env python3
"""Probe an IB Gateway / TWS session without placing orders.

This script validates the live session boundary by connecting to the IB socket,
capturing a small set of broker-truth signals, and writing a JSON evidence
packet. It does not submit, modify, or cancel any orders.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
except ImportError as exc:  # pragma: no cover - runtime-only dependency
    raise SystemExit(
        "ibapi is required for probe_ibkr_session.py. "
        "Install it with: python3 -m pip install --user ibapi==9.81.1.post1"
    ) from exc


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class IBKRProbe(EWrapper, EClient):
    def __init__(self, account_ref: str | None):
        EClient.__init__(self, self)
        self.account_ref = account_ref
        self.connected_at: str | None = None
        self.next_valid_order_id: int | None = None
        self.managed_accounts: list[str] = []
        self.current_time_epoch: int | None = None
        self.account_summary: dict[str, dict[str, str]] = defaultdict(dict)
        self.errors: list[dict[str, Any]] = []
        self.summary_done = threading.Event()
        self.managed_accounts_done = threading.Event()
        self.next_valid_done = threading.Event()
        self.current_time_done = threading.Event()

    def connectAck(self) -> None:  # noqa: N802
        self.connected_at = iso_now()
        self.startApi()

    def nextValidId(self, orderId: int) -> None:  # noqa: N802
        self.next_valid_order_id = orderId
        self.next_valid_done.set()

    def managedAccounts(self, accountsList: str) -> None:  # noqa: N802
        self.managed_accounts = [acct.strip() for acct in accountsList.split(",") if acct.strip()]
        self.managed_accounts_done.set()

    def currentTime(self, time_: int) -> None:  # noqa: N802
        self.current_time_epoch = time_
        self.current_time_done.set()

    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str) -> None:  # noqa: N802
        self.account_summary[account][tag] = value if not currency else f"{value} {currency}".strip()

    def accountSummaryEnd(self, reqId: int) -> None:  # noqa: N802
        self.summary_done.set()

    def error(  # noqa: A003,N802
        self,
        reqId: int,
        errorCode: int,
        errorString: str,
        advancedOrderRejectJson: str = "",
    ) -> None:
        self.errors.append(
            {
                "req_id": reqId,
                "error_code": errorCode,
                "error": errorString,
                "advanced_reject_json": advancedOrderRejectJson or None,
            }
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe an IB Gateway/TWS session without sending orders.")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--client-id", required=True, type=int)
    parser.add_argument("--account-ref", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--output-json", default=None)
    return parser.parse_args(argv[1:])


def wait_for_until(event: threading.Event, deadline: float) -> bool:
    remaining = max(0.0, deadline - time.time())
    return event.wait(timeout=remaining)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    probe = IBKRProbe(account_ref=args.account_ref)

    try:
        probe.connect(args.host, args.port, args.client_id)
    except Exception as exc:  # noqa: BLE001
        payload = {
            "status": "connect_failed",
            "generated_at": iso_now(),
            "host": args.host,
            "port": args.port,
            "client_id": args.client_id,
            "account_ref": args.account_ref,
            "error": str(exc),
        }
        if args.output_json:
            Path(args.output_json).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 1

    thread = threading.Thread(target=probe.run, daemon=True, name="ibkr-probe")
    thread.start()

    # Kick the smallest read-only probes available on the socket.
    deadline = time.time() + args.timeout_seconds
    while not probe.isConnected() and time.time() < deadline:
        time.sleep(0.1)

    if not probe.isConnected():
        payload = {
            "status": "not_connected",
            "generated_at": iso_now(),
            "host": args.host,
            "port": args.port,
            "client_id": args.client_id,
            "account_ref": args.account_ref,
            "errors": probe.errors,
        }
        if args.output_json:
            Path(args.output_json).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 1

    probe.reqIds(-1)
    probe.reqManagedAccts()
    probe.reqCurrentTime()
    probe.reqAccountSummary(
        9001,
        "All",
        "NetLiquidation,BuyingPower,AvailableFunds,TotalCashValue,AccountType",
    )

    wait_for_until(probe.next_valid_done, deadline)
    wait_for_until(probe.managed_accounts_done, deadline)
    wait_for_until(probe.current_time_done, deadline)
    wait_for_until(probe.summary_done, deadline)
    probe.cancelAccountSummary(9001)
    time.sleep(0.5)
    probe.disconnect()
    thread.join(timeout=2.0)

    summary_accounts = dict(probe.account_summary)
    account_match = None
    if args.account_ref:
        account_match = args.account_ref in probe.managed_accounts or args.account_ref in summary_accounts

    payload = {
        "status": "ok" if probe.next_valid_order_id is not None and probe.managed_accounts else "partial",
        "generated_at": iso_now(),
        "host": args.host,
        "port": args.port,
        "client_id": args.client_id,
        "account_ref": args.account_ref,
        "connected_at": probe.connected_at,
        "next_valid_order_id": probe.next_valid_order_id,
        "managed_accounts": probe.managed_accounts,
        "account_ref_present": account_match,
        "current_time_epoch": probe.current_time_epoch,
        "account_summary": summary_accounts,
        "errors": probe.errors,
        "notes": [
            "This probe is read-only and does not place, cancel, or modify orders.",
            "Successful nextValidId + managedAccounts proves the socket session is broker-authenticated.",
        ],
    }
    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
