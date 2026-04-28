#!/usr/bin/env python3
"""Read-only IBKR execution probe for no-fill evidence."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ibapi_modules() -> tuple[Any, Any, Any]:
    try:
        from ibapi.client import EClient
        from ibapi.execution import ExecutionFilter
        from ibapi.wrapper import EWrapper
    except ImportError as exc:  # pragma: no cover - runtime-only dependency
        raise SystemExit("ibapi is required. Install ibapi==9.81.1.post1.") from exc
    return EClient, EWrapper, ExecutionFilter


def normalize_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def execution_to_dict(contract: Any, execution: Any) -> dict[str, Any]:
    return {
        "captured_at": iso_now(),
        "exec_id": getattr(execution, "execId", None),
        "time": getattr(execution, "time", None),
        "account": getattr(execution, "acctNumber", None),
        "exchange": getattr(execution, "exchange", None),
        "side": getattr(execution, "side", None),
        "shares": getattr(execution, "shares", None),
        "price": getattr(execution, "price", None),
        "order_id": getattr(execution, "orderId", None),
        "perm_id": getattr(execution, "permId", None),
        "client_id": getattr(execution, "clientId", None),
        "symbol": getattr(contract, "symbol", None),
        "sec_type": getattr(contract, "secType", None),
        "currency": getattr(contract, "currency", None),
    }


def matches_target(
    execution: dict[str, Any],
    *,
    account: str | None,
    symbol: str | None,
    order_id: int | None,
    perm_id: int | None,
) -> bool:
    if account and str(execution.get("account") or "") != account:
        return False
    if symbol and str(execution.get("symbol") or "").upper() != symbol.upper():
        return False
    if order_id is not None and normalize_int(execution.get("order_id")) != order_id:
        return False
    if perm_id is not None and normalize_int(execution.get("perm_id")) != perm_id:
        return False
    return True


def summarize_fill_status(matching_executions: list[dict[str, Any]]) -> dict[str, Any]:
    total_shares = 0.0
    for execution in matching_executions:
        try:
            total_shares += float(execution.get("shares") or 0.0)
        except (TypeError, ValueError):
            pass
    return {
        "fill_status": "fills_observed" if total_shares > 0 else "no_matching_executions",
        "matching_execution_count": len(matching_executions),
        "matching_shares": total_shares,
    }


class ExecutionsProbe:
    def __init__(self) -> None:
        EClient, EWrapper, ExecutionFilter = ibapi_modules()

        class App(EWrapper, EClient):  # type: ignore[misc, valid-type]
            def __init__(self) -> None:
                EClient.__init__(self, self)
                self.ExecutionFilter = ExecutionFilter
                self.connected_at: str | None = None
                self.next_valid_order_id: int | None = None
                self.executions: list[dict[str, Any]] = []
                self.commission_reports: list[dict[str, Any]] = []
                self.errors: list[dict[str, Any]] = []
                self.next_valid_done = threading.Event()
                self.exec_end_done = threading.Event()

            def connectAck(self) -> None:  # noqa: N802
                self.connected_at = iso_now()
                self.startApi()

            def nextValidId(self, orderId: int) -> None:  # noqa: N802
                self.next_valid_order_id = orderId
                self.next_valid_done.set()

            def execDetails(self, reqId: int, contract: Any, execution: Any) -> None:  # noqa: N802
                self.executions.append(execution_to_dict(contract, execution))

            def execDetailsEnd(self, reqId: int) -> None:  # noqa: N802
                self.exec_end_done.set()

            def commissionReport(self, commissionReport: Any) -> None:  # noqa: N802
                self.commission_reports.append(
                    {
                        "captured_at": iso_now(),
                        "exec_id": getattr(commissionReport, "execId", None),
                        "commission": getattr(commissionReport, "commission", None),
                        "currency": getattr(commissionReport, "currency", None),
                        "realized_pnl": getattr(commissionReport, "realizedPNL", None),
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

        self.app = App()

    def run(
        self,
        *,
        host: str,
        port: int,
        client_id: int,
        timeout_seconds: float,
        account: str | None,
        symbol: str | None,
        since: str | None,
    ) -> None:
        self.app.connect(host, port, client_id)
        thread = threading.Thread(target=self.app.run, daemon=True, name="ibkr-executions-probe")
        thread.start()

        if self.app.next_valid_done.wait(timeout=timeout_seconds):
            execution_filter = self.app.ExecutionFilter()
            if account:
                execution_filter.acctCode = account
            if symbol:
                execution_filter.symbol = symbol.upper()
            if since:
                execution_filter.time = since
            self.app.reqExecutions(9701, execution_filter)
            self.app.exec_end_done.wait(timeout=timeout_seconds)
            time.sleep(0.5)

        self.app.disconnect()
        thread.join(timeout=2.0)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe IBKR executions without placing orders.")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--client-id", required=True, type=int)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--account")
    parser.add_argument("--symbol")
    parser.add_argument("--order-id", type=int)
    parser.add_argument("--perm-id", type=int)
    parser.add_argument("--since", help="Optional IBKR execution filter time, e.g. 20260426-00:00:00")
    parser.add_argument("--output-json", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    probe = ExecutionsProbe()
    probe.run(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        timeout_seconds=args.timeout_seconds,
        account=args.account,
        symbol=args.symbol,
        since=args.since,
    )
    matching_executions = [
        execution
        for execution in probe.app.executions
        if matches_target(
            execution,
            account=args.account,
            symbol=args.symbol,
            order_id=args.order_id,
            perm_id=args.perm_id,
        )
    ]
    fill_summary = summarize_fill_status(matching_executions)
    payload = {
        "status": "ok" if probe.app.next_valid_order_id is not None else "partial",
        "generated_at": iso_now(),
        "host": args.host,
        "port": args.port,
        "client_id": args.client_id,
        "connected_at": probe.app.connected_at,
        "next_valid_order_id": probe.app.next_valid_order_id,
        "target": {
            "account": args.account,
            "symbol": args.symbol,
            "order_id": args.order_id,
            "perm_id": args.perm_id,
            "since": args.since,
        },
        "execution_count": len(probe.app.executions),
        "executions": probe.app.executions,
        "matching_executions": matching_executions,
        "commission_reports": probe.app.commission_reports,
        "errors": probe.app.errors,
        **fill_summary,
        "notes": ["This probe is read-only and does not place, modify, or cancel orders."],
    }
    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
