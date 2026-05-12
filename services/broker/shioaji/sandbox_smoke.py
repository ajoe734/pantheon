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
import dataclasses
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.broker.shioaji.adapter import (  # noqa: E402
    ShioajiBrokerAdapter,
    ShioajiBrokerError,
)


TASK_ID = "EP5-BROKER-TW-002"
PROOF_BOUNDARY = "broker_adapter_sandbox_smoke; not canary/live/capital proof"


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


def broker_error_payload(exc: ShioajiBrokerError) -> dict[str, Any]:
    payload = exc.to_payload()
    payload["status_code"] = exc.status_code
    return payload


class MockShioajiApi:
    """Small Shioaji SDK double used only for explicit ``--mock-api`` runs."""

    class _Contracts:
        class _Stocks:
            def __getitem__(self, symbol: str) -> dict[str, str]:
                return {"symbol": symbol, "exchange": "TSE", "mock_contract": "stock"}

        Stocks = _Stocks()

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

    def Order(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802 - mirrors Shioaji SDK
        payload = dict(kwargs)
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

    def update_status(self) -> None:
        self.calls.append({"call": "update_status"})

    def export_calls(self) -> list[dict[str, Any]]:
        return list(self.calls)


def build_adapter(*, mock_api: bool) -> tuple[ShioajiBrokerAdapter, MockShioajiApi | None]:
    api = MockShioajiApi() if mock_api else None
    return ShioajiBrokerAdapter(_api=api), api


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
    adapter, mock_api = build_adapter(mock_api=args.mock_api)
    generated_at = iso_now()

    request = {
        "capital_pool_id": args.capital_pool_id,
        "strategy_id": args.strategy_id,
        "symbol": args.symbol,
        "qty": float(args.qty),
        "side": args.side,
        "order_type": args.order_type,
        "limit_price": args.limit_price,
    }

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
        cancelled = order_payload(adapter.cancel(placed_order.order_id))
        after_cancel = order_payload(adapter.get_status(placed_order.order_id))
        error = None
    except ShioajiBrokerError as exc:
        error = broker_error_payload(exc)
        placed = {}
        after_place = {}
        cancelled = {}
        after_cancel = {}
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

    return {
        "task_id": TASK_ID,
        "generated_at": generated_at,
        "provider": "Shioaji",
        "adapter": "services.broker.shioaji.ShioajiBrokerAdapter",
        "run_mode": "mock_api_replay" if args.mock_api else "shioaji_simulation_sdk",
        "status": status,
        "proof_boundary": PROOF_BOUNDARY,
        "environment": {
            "BROKER_SHIOAJI_SANDBOX_ENABLED": sandbox_env_enabled,
            "BROKER_SHIOAJI_API_KEY_configured": bool(os.getenv("BROKER_SHIOAJI_API_KEY")),
            "BROKER_SHIOAJI_SECRET_KEY_configured": bool(os.getenv("BROKER_SHIOAJI_SECRET_KEY")),
            "raw_secret_material_persisted": False,
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
            if after_cancel.get("is_real_capital") is False and after_cancel.get("is_real_order") is False
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
    parser.add_argument("--symbol", default="2330")
    parser.add_argument("--qty", type=float, default=1.0)
    parser.add_argument("--side", choices=("buy", "sell"), default="buy")
    parser.add_argument("--order-type", choices=("market", "limit"), default="limit")
    parser.add_argument("--limit-price", type=float, default=950.0)
    parser.add_argument("--capital-pool-id", default="pool-ep5-broker-tw-sandbox")
    parser.add_argument("--strategy-id", default="strategy-ep5-broker-tw-smoke")
    parser.add_argument("--mock-api", action="store_true", help="Use an explicit mock Shioaji API replay.")
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    payload = run_smoke(args)
    write_bundle(Path(args.output_dir), payload)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "run_mode": payload["run_mode"],
                "output_dir": args.output_dir,
                "order_id": payload["order_ids"]["pantheon_order_id"],
                "shioaji_trade_id": payload["order_ids"]["shioaji_trade_id"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
