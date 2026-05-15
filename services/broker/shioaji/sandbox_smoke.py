#!/usr/bin/env python3
"""Run a Shioaji broker sandbox place/cancel/readback/reconcile smoke.

This harness exercises ``services.broker.shioaji.ShioajiBrokerAdapter`` under
the explicit ``BROKER_SHIOAJI_SANDBOX_ENABLED`` gate. By default it uses the
real Shioaji simulation SDK path through the adapter. ``--mock-api`` is only a
repo-safe replay mode for CI and local verification when sandbox credentials
are unavailable; evidence generated that way is marked as mock replay.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.broker.shioaji.adapter import (  # noqa: E402
    ShioajiBrokerAdapter,
    ShioajiBrokerError,
)


TASK_ID = "MGMT-BROKER-003"
PROOF_BOUNDARY = "broker_adapter_sandbox_smoke; not canary/live/capital proof"
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
SANDBOX_WINDOW_START_HOUR = 8
SANDBOX_WINDOW_END_HOUR = 20
SDK_RESPONSE_EVENT_RE = re.compile(
    r"Response Code:\s*(?P<response_code>\d+)\s*\|\s*Event Code:\s*(?P<event_code>\d+).*?\|\s*Event:\s*(?P<event>.+)$"
)
SDK_ORDER_STATE_RE = re.compile(
    r"OrderState\.(?P<state>\w+).*?'op_code':\s*'(?P<op_code>[^']*)'.*?'op_msg':\s*'(?P<op_msg>[^']*)'"
)


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def env_flag_enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def order_payload(order: Any) -> dict[str, Any]:
    if hasattr(order, "to_dict"):
        return dict(order.to_dict())
    if dataclasses.is_dataclass(order):
        return dataclasses.asdict(order)
    raise TypeError(f"unsupported order payload type: {type(order)!r}")


def taipei_window_payload(*, label: str) -> dict[str, Any]:
    now = datetime.now(TAIPEI_TZ).replace(microsecond=0)
    within_window = SANDBOX_WINDOW_START_HOUR <= now.hour < SANDBOX_WINDOW_END_HOUR
    return {
        "label": label,
        "timezone": "Asia/Taipei",
        "observed_at": now.isoformat(),
        "window_start_hour": SANDBOX_WINDOW_START_HOUR,
        "window_end_hour_exclusive": SANDBOX_WINDOW_END_HOUR,
        "within_window": within_window,
    }


def fail_if_outside_taipei_window(*, label: str) -> dict[str, Any]:
    payload = taipei_window_payload(label=label)
    if not payload["within_window"]:
        raise ShioajiBrokerError(
            "SHIOAJI_SANDBOX_WINDOW_CLOSED",
            (
                "Shioaji sandbox smoke must run between 08:00 and 20:00 Asia/Taipei; "
                f"observed {payload['observed_at']}."
            ),
            status_code=503,
        )
    return payload


def redacted_account_payload(account: Any) -> dict[str, Any]:
    def safe_attr(name: str) -> str | bool | None:
        value = getattr(account, name, None)
        if isinstance(value, (str, bool)) or value is None:
            return value
        return str(value)

    account_id = safe_attr("account_id")
    person_id = safe_attr("person_id")
    return {
        "account_type": str(safe_attr("account_type")) if safe_attr("account_type") is not None else None,
        "broker_id": safe_attr("broker_id"),
        "account_id_last4": account_id[-4:] if isinstance(account_id, str) and account_id else None,
        "person_id_last4": person_id[-4:] if isinstance(person_id, str) and person_id else None,
        "signed": bool(getattr(account, "signed", False)),
    }


def broker_error_payload(exc: ShioajiBrokerError) -> dict[str, Any]:
    payload = exc.to_payload()
    payload["status_code"] = exc.status_code
    return payload


def parse_sdk_stdout_observations(stdout_text: str) -> dict[str, Any]:
    response_events: list[dict[str, str]] = []
    order_state_events: list[dict[str, str]] = []
    for line in stdout_text.splitlines():
        response_match = SDK_RESPONSE_EVENT_RE.search(line)
        if response_match:
            response_events.append(response_match.groupdict())
            continue
        order_match = SDK_ORDER_STATE_RE.search(line)
        if order_match:
            order_state_events.append(
                {
                    "state": order_match.group("state"),
                    "operation_code": order_match.group("op_code"),
                    "operation_message": order_match.group("op_msg"),
                }
            )

    return {
        "captured": bool(stdout_text.strip()),
        "raw_stdout_persisted": False,
        "session_response_code_0_observed": any(
            event["response_code"] == "0" for event in response_events
        ),
        "session_up_observed": any(event["event"] == "Session up" for event in response_events),
        "contract_subscription_observed": any(
            "CONTRACT" in event["event"] or event["response_code"] == "200"
            for event in response_events
        ),
        "response_events": response_events,
        "order_state_events": order_state_events,
    }


class MockShioajiApi:
    """Small Shioaji SDK double used only for explicit ``--mock-api`` runs."""

    class _Contracts:
        class _Stocks:
            def __getitem__(self, symbol: str) -> dict[str, str]:
                return {"symbol": symbol, "exchange": "TSE", "mock_contract": "stock"}

        class _Futures:
            class _TXF:
                def __getitem__(self, symbol: str) -> dict[str, str]:
                    return {"symbol": symbol, "exchange": "TAIFEX", "mock_contract": "futures"}

            TXF = _TXF()

            def __getitem__(self, symbol: str) -> dict[str, str] | _TXF:
                if symbol == "TXF":
                    return self.TXF
                return self.TXF[symbol]

        Stocks = _Stocks()
        Futures = _Futures()

    @dataclasses.dataclass
    class _Trade:
        trade_id: str
        contract: dict[str, str]
        order: dict[str, Any]
        status: str = "submitted"

    def __init__(self) -> None:
        self.Contracts = self._Contracts()
        self.calls: list[dict[str, Any]] = []
        self._next_trade = 1
        self.stock_account = type(
            "StockAccount",
            (),
            {"broker_id": "9A95", "account_id": "mockstock", "person_id": "mockperson", "signed": True},
        )()
        self.futopt_account = type(
            "FutureAccount",
            (),
            {"broker_id": "F002000", "account_id": "mockfuture", "person_id": "mockperson", "signed": True},
        )()

    def Order(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802 - mirrors Shioaji SDK
        payload = dict(kwargs)
        account = payload.get("account")
        if account is not None:
            payload["account"] = redacted_account_payload(account)
        self.calls.append({"call": "Order", "payload": payload})
        return payload

    def place_order(self, contract: dict[str, str], order: dict[str, Any]) -> _Trade:
        trade = self._Trade(
            trade_id=f"mock-shioaji-trade-{self._next_trade:03d}",
            contract=dict(contract),
            order=dict(order),
        )
        self._next_trade += 1
        self.calls.append(
            {
                "call": "place_order",
                "contract": dict(contract),
                "order": dict(order),
                "trade_id": trade.trade_id,
            }
        )
        return trade

    def cancel_order(self, trade: _Trade) -> None:
        trade.status = "cancelled"
        self.calls.append({"call": "cancel_order", "trade_id": trade.trade_id})

    def update_status(self, account: Any = None) -> None:
        self.calls.append({"call": "update_status", "account": redacted_account_payload(account) if account else None})

    def export_calls(self) -> list[dict[str, Any]]:
        return list(self.calls)


def build_adapter(
    *,
    mock_api: bool,
    submit_spacing_seconds: float = 1.0,
) -> tuple[ShioajiBrokerAdapter, MockShioajiApi | None]:
    api = MockShioajiApi() if mock_api else None
    return ShioajiBrokerAdapter(_api=api, submit_spacing_seconds=submit_spacing_seconds), api


def comparison(name: str, expected: Any, observed: Any) -> dict[str, Any]:
    return {
        "field": name,
        "expected": expected,
        "observed": observed,
        "status": "match" if expected == observed else "diff",
    }


def build_reconciliation(
    *,
    request: dict[str, Any],
    placed: dict[str, Any],
    after_place: dict[str, Any],
    cancelled: dict[str, Any],
    after_cancel: dict[str, Any],
    live_reject: dict[str, Any],
) -> dict[str, Any]:
    rows = [
        comparison("place.symbol", request["symbol"], placed.get("symbol")),
        comparison("place.qty", request["qty"], placed.get("qty")),
        comparison("place.side", request["side"], placed.get("side")),
        comparison("place.account_kind", request["account_kind"], placed.get("account_kind")),
        comparison("place.status", "submitted", placed.get("status")),
        comparison("readback_after_place.order_id", placed.get("order_id"), after_place.get("order_id")),
        comparison("readback_after_place.status", "submitted", after_place.get("status")),
        comparison("cancel.order_id", placed.get("order_id"), cancelled.get("order_id")),
        comparison("cancel.status", "cancelled", cancelled.get("status")),
        comparison("readback_after_cancel.order_id", placed.get("order_id"), after_cancel.get("order_id")),
        comparison("readback_after_cancel.status", "cancelled", after_cancel.get("status")),
        comparison("is_real_order", False, after_cancel.get("is_real_order")),
        comparison("is_real_capital", False, after_cancel.get("is_real_capital")),
        comparison("deployment_stage", "sandbox", after_cancel.get("deployment_stage")),
        comparison("live_reject.error_code", "SHIOAJI_LIVE_DISABLED", live_reject.get("error_code")),
    ]
    rows.append(
        {
            "field": "shioaji_trade_id",
            "expected": "present",
            "observed": placed.get("shioaji_trade_id"),
            "status": "match" if placed.get("shioaji_trade_id") else "diff",
        }
    )
    return {
        "status": "passed" if all(row["status"] == "match" for row in rows) else "failed",
        "diff": rows,
    }


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    sandbox_env_enabled = env_flag_enabled(os.getenv("BROKER_SHIOAJI_SANDBOX_ENABLED"))
    enforce_window = getattr(args, "enforce_taipei_window", not args.mock_api)
    window_start = None
    if enforce_window:
        window_start = fail_if_outside_taipei_window(label=f"{args.account_kind}_smoke_start")
    else:
        window_start = taipei_window_payload(label=f"{args.account_kind}_smoke_start")

    adapter, mock_api = build_adapter(
        mock_api=args.mock_api,
        submit_spacing_seconds=getattr(args, "submit_spacing_seconds", 1.0),
    )
    generated_at = iso_now()

    request = {
        "capital_pool_id": args.capital_pool_id,
        "strategy_id": args.strategy_id,
        "symbol": args.symbol,
        "qty": float(args.qty),
        "side": args.side,
        "order_type": args.order_type,
        "limit_price": args.limit_price,
        "account_kind": args.account_kind,
        "futures_category": args.futures_category,
    }
    cancel_delay_seconds = max(0.0, float(getattr(args, "cancel_delay_seconds", 1.0)))
    cancel_delay_applied = False

    live_reject: dict[str, Any] = {}
    try:
        try:
            adapter.reject_live_order()
        except ShioajiBrokerError as exc:
            live_reject = broker_error_payload(exc)
        else:  # pragma: no cover - adapter invariant should make this unreachable
            live_reject = {"status": "unexpected_success", "error_code": None, "status_code": None}

        placed_order = adapter.submit(**request)
        placed = order_payload(placed_order)
        after_place = order_payload(adapter.get_status(placed_order.order_id))
        if cancel_delay_seconds > 0:
            time.sleep(cancel_delay_seconds)
            cancel_delay_applied = True
        cancelled = order_payload(adapter.cancel(placed_order.order_id))
        after_cancel = order_payload(adapter.get_status(placed_order.order_id))
        error = None
    except ShioajiBrokerError as exc:
        error = broker_error_payload(exc)
        placed = {}
        after_place = {}
        cancelled = {}
        after_cancel = {}
        if not live_reject:
            live_reject = error if exc.error_code == "SHIOAJI_LIVE_DISABLED" else {}

    reconciliation = (
        build_reconciliation(
            request=request,
            placed=placed,
            after_place=after_place,
            cancelled=cancelled,
            after_cancel=after_cancel,
            live_reject=live_reject,
        )
        if error is None
        else {"status": "failed", "diff": [], "error": error}
    )
    status = "passed" if error is None and reconciliation["status"] == "passed" else "failed"
    window_end = taipei_window_payload(label=f"{args.account_kind}_smoke_end")

    return {
        "task_id": str(getattr(args, "task_id", TASK_ID) or TASK_ID),
        "generated_at": generated_at,
        "completed_at": iso_now(),
        "provider": "Shioaji",
        "adapter": "services.broker.shioaji.ShioajiBrokerAdapter",
        "account_kind": args.account_kind,
        "run_mode": "mock_api_replay" if args.mock_api else "shioaji_simulation_sdk",
        "status": status,
        "proof_boundary": PROOF_BOUNDARY,
        "environment": {
            "BROKER_SHIOAJI_SANDBOX_ENABLED": sandbox_env_enabled,
            "BROKER_SHIOAJI_API_KEY_configured": bool(os.getenv("BROKER_SHIOAJI_API_KEY")),
            "BROKER_SHIOAJI_SECRET_KEY_configured": bool(os.getenv("BROKER_SHIOAJI_SECRET_KEY")),
            "raw_secret_material_persisted": False,
        },
        "taipei_sandbox_window": {
            "start": window_start,
            "end": window_end,
            "fail_fast_enforced": bool(enforce_window),
        },
        "submit_spacing_gate": {
            "scope": "per-account thread-local adapter gate",
            "configured_seconds": float(getattr(args, "submit_spacing_seconds", 1.0)),
        },
        "place_to_cancel_delay": {
            "configured_seconds": cancel_delay_seconds,
            "applied": cancel_delay_applied,
            "meets_minimum_seconds": cancel_delay_seconds >= 1.0,
            "rationale": "Sinopac simulation audit flow requires waiting at least one second after place_order before cancel_order.",
        },
        "request": request,
        "place": {
            "request": request,
            "response": placed,
            "sdk_trace": mock_api.export_calls() if mock_api else "not_captured_for_real_sdk",
        },
        "readback": {
            "after_place": after_place,
            "after_cancel": after_cancel,
        },
        "cancel": {
            "request": {"order_id": placed.get("order_id")},
            "response": cancelled,
        },
        "order_ids": {
            "pantheon_order_id": placed.get("order_id"),
            "shioaji_trade_id": placed.get("shioaji_trade_id"),
        },
        "status_transitions": [
            {
                "step": "place",
                "order_id": placed.get("order_id"),
                "status": placed.get("status"),
                "observed_at": placed.get("created_at"),
            },
            {
                "step": "readback_after_place",
                "order_id": after_place.get("order_id"),
                "status": after_place.get("status"),
                "observed_at": iso_now(),
            },
            {
                "step": "cancel",
                "order_id": cancelled.get("order_id"),
                "status": cancelled.get("status"),
                "observed_at": cancelled.get("filled_at"),
            },
            {
                "step": "readback_after_cancel",
                "order_id": after_cancel.get("order_id"),
                "status": after_cancel.get("status"),
                "observed_at": iso_now(),
            },
        ],
        "live_gate": {
            "status": "rejected" if live_reject.get("error_code") == "SHIOAJI_LIVE_DISABLED" else "failed",
            "response": live_reject,
        },
        "reconciliation": reconciliation,
        "no_real_capital": {
            "status": "passed"
            if error is not None
            or (
                after_cancel.get("is_real_capital") is False
                and after_cancel.get("is_real_order") is False
            )
            else "failed",
            "real_capital_used": False,
            "real_capital_reserved": False,
            "production_live_order_submitted": False,
            "production_live_cancel_submitted": False,
        },
        "error": error,
        "notes": [
            "BROKER_SHIOAJI_SANDBOX_ENABLED must be explicitly true; default-false adapter behavior is not bypassed.",
            "Live execution remains SHIOAJI_LIVE_DISABLED.",
            "No capital binding, registry admission, paper promotion, canary activation, or live order is performed.",
        ],
    }


def write_bundle(output_dir: Path, payload: dict[str, Any]) -> None:
    dump_json(output_dir / "summary.json", payload)
    dump_json(output_dir / "place.request.json", payload["place"]["request"])
    dump_json(output_dir / "place.response.json", payload["place"]["response"])
    dump_json(output_dir / "readback.after_place.json", payload["readback"]["after_place"])
    dump_json(output_dir / "cancel.request.json", payload["cancel"]["request"])
    dump_json(output_dir / "cancel.response.json", payload["cancel"]["response"])
    dump_json(output_dir / "readback.after_cancel.json", payload["readback"]["after_cancel"])
    dump_json(output_dir / "live-disabled.json", payload["live_gate"])
    dump_json(output_dir / "reconciliation.json", payload["reconciliation"])
    dump_json(output_dir / "no-real-capital-evidence.json", payload["no_real_capital"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Shioaji broker sandbox place/cancel/readback smoke.")
    parser.add_argument("--symbol", default="2890")
    parser.add_argument("--qty", type=float, default=1.0)
    parser.add_argument("--side", choices=("buy", "sell"), default="buy")
    parser.add_argument("--order-type", choices=("market", "limit"), default="limit")
    parser.add_argument("--limit-price", type=float, default=18.0)
    parser.add_argument("--account-kind", choices=("stock", "futures"), default="stock")
    parser.add_argument("--futures-category", default=None)
    parser.add_argument("--submit-spacing-seconds", type=float, default=1.0)
    parser.add_argument("--cancel-delay-seconds", type=float, default=1.0)
    parser.add_argument("--capital-pool-id", default="pool-ep5-broker-tw-sandbox")
    parser.add_argument("--strategy-id", default="strategy-ep5-broker-tw-smoke")
    parser.add_argument("--task-id", default=TASK_ID, help="Task id to stamp into generated evidence.")
    parser.add_argument("--mock-api", action="store_true", help="Use an explicit mock Shioaji API replay.")
    parser.add_argument("--output-dir")
    parser.add_argument("--output-file")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.output_dir and not args.output_file:
        parser.error("one of --output-dir or --output-file is required")

    if args.mock_api:
        payload = run_smoke(args)
    else:
        sdk_stdout = io.StringIO()
        with contextlib.redirect_stdout(sdk_stdout):
            payload = run_smoke(args)
        sdk_stdout_text = sdk_stdout.getvalue()
        payload["sdk_stdout_observations"] = parse_sdk_stdout_observations(sdk_stdout_text)
        if sdk_stdout_text:
            print(sdk_stdout_text, file=sys.stderr, end="")

    if args.output_dir:
        write_bundle(Path(args.output_dir), payload)
    if args.output_file:
        dump_json(Path(args.output_file), payload)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "run_mode": payload["run_mode"],
                "account_kind": payload["account_kind"],
                "output_dir": args.output_dir,
                "output_file": args.output_file,
                "order_id": payload["order_ids"]["pantheon_order_id"],
                "shioaji_trade_id": payload["order_ids"]["shioaji_trade_id"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
