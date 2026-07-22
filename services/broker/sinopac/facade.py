"""Management-facing Shioaji sandbox facade.

This module composes the low-level sandbox adapter operations into the stable
shape needed by Management/OODA views. It does not write evidence files and it
does not enable canary, live broker, or capital-binding side effects.
"""
from __future__ import annotations

from typing import Any, Optional

from .adapter import ShioajiBrokerAdapter, ShioajiBrokerError


PROOF_BOUNDARY = "management_sandbox_facade; not canary/live/capital proof"


def _broker_error_payload(exc: ShioajiBrokerError) -> dict[str, Any]:
    payload = exc.to_payload()
    payload["status_code"] = exc.status_code
    return payload


def _comparison(field: str, expected: Any, observed: Any) -> dict[str, Any]:
    return {
        "field": field,
        "expected": expected,
        "observed": observed,
        "status": "match" if expected == observed else "diff",
    }


class ShioajiSandboxFacade:
    """Small facade for sandbox account, order lifecycle, and safety summary."""

    def __init__(self, adapter: Optional[ShioajiBrokerAdapter] = None) -> None:
        self._adapter = adapter or ShioajiBrokerAdapter()

    def connect(self) -> dict[str, Any]:
        return self._adapter.connect()

    def account_status(self, *, account_kind: str = "stock") -> dict[str, Any]:
        return self._adapter.account_status(account_kind)

    def place_test_order(
        self,
        *,
        capital_pool_id: str,
        strategy_id: str,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "limit",
        limit_price: Optional[float] = None,
        account_kind: str = "stock",
        futures_category: Optional[str] = None,
    ) -> dict[str, Any]:
        order = self._adapter.submit(
            capital_pool_id=capital_pool_id,
            strategy_id=strategy_id,
            symbol=symbol,
            qty=qty,
            side=side,
            order_type=order_type,
            limit_price=limit_price,
            account_kind=account_kind,
            futures_category=futures_category,
        )
        return order.to_dict()

    def cancel_test_order(self, *, order_id: str) -> dict[str, Any]:
        return self._adapter.cancel(order_id).to_dict()

    def readback(self, *, order_id: str) -> dict[str, Any]:
        return self._adapter.get_status(order_id).to_dict()

    def live_disabled_result(self) -> dict[str, Any]:
        try:
            self._adapter.reject_live_order()
        except ShioajiBrokerError as exc:
            payload = _broker_error_payload(exc)
            return {
                "status": "rejected"
                if payload.get("error_code") == "SHIOAJI_LIVE_DISABLED"
                else "failed",
                "response": payload,
            }
        return {
            "status": "failed",
            "response": {
                "status": "unexpected_success",
                "error_code": None,
                "message": "Live broker execution unexpectedly succeeded.",
            },
        }

    def reconcile(
        self,
        *,
        place_result: dict[str, Any],
        cancel_result: dict[str, Any],
        readback_result: dict[str, Any],
        live_disabled_result: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        order_id = place_result.get("order_id")
        rows = [
            _comparison("place.status", "submitted", place_result.get("status")),
            _comparison("cancel.order_id", order_id, cancel_result.get("order_id")),
            _comparison("cancel.status", "cancelled", cancel_result.get("status")),
            _comparison("readback.order_id", order_id, readback_result.get("order_id")),
            _comparison("readback.status", "cancelled", readback_result.get("status")),
            _comparison("is_real_order", False, readback_result.get("is_real_order")),
            _comparison("is_real_capital", False, readback_result.get("is_real_capital")),
            _comparison("deployment_stage", "sandbox", readback_result.get("deployment_stage")),
        ]
        rows.append(
            {
                "field": "shioaji_trade_id",
                "expected": "present",
                "observed": place_result.get("shioaji_trade_id"),
                "status": "match" if place_result.get("shioaji_trade_id") else "diff",
            }
        )
        if live_disabled_result is not None:
            response = live_disabled_result.get("response") or {}
            rows.append(
                _comparison(
                    "live_reject.error_code",
                    "SHIOAJI_LIVE_DISABLED",
                    response.get("error_code"),
                )
            )

        return {
            "status": "passed" if all(row["status"] == "match" for row in rows) else "failed",
            "comparisons": rows,
        }

    def run_lifecycle(
        self,
        *,
        capital_pool_id: str,
        strategy_id: str,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "limit",
        limit_price: Optional[float] = None,
        account_kind: str = "stock",
        futures_category: Optional[str] = None,
    ) -> dict[str, Any]:
        connect_result: dict[str, Any] = {}
        account_result: dict[str, Any] = {}
        account_status = "missing"
        place_result: dict[str, Any] = {}
        cancel_result: dict[str, Any] = {}
        readback_result: dict[str, Any] = {}
        reconcile_result: dict[str, Any] = {"status": "failed", "comparisons": []}
        live_result = self.live_disabled_result()
        error: dict[str, Any] | None = None

        try:
            connect_result = self.connect()
            account_result = self.account_status(account_kind=account_kind)
            account_status = str(account_result.get("account_status") or "missing")
            place_result = self.place_test_order(
                capital_pool_id=capital_pool_id,
                strategy_id=strategy_id,
                symbol=symbol,
                qty=qty,
                side=side,
                order_type=order_type,
                limit_price=limit_price,
                account_kind=account_kind,
                futures_category=futures_category,
            )
            self.readback(order_id=place_result["order_id"])
            cancel_result = self.cancel_test_order(order_id=place_result["order_id"])
            readback_result = self.readback(order_id=place_result["order_id"])
            reconcile_result = self.reconcile(
                place_result=place_result,
                cancel_result=cancel_result,
                readback_result=readback_result,
                live_disabled_result=live_result,
            )
        except ShioajiBrokerError as exc:
            error = _broker_error_payload(exc)
            reconcile_result = {"status": "failed", "comparisons": [], "error": error}

        status = "passed" if error is None and reconcile_result.get("status") == "passed" else "failed"
        return {
            "broker": "shioaji",
            "provider": "Shioaji",
            "environment": "sandbox",
            "status": status,
            "proof_boundary": PROOF_BOUNDARY,
            "connect_result": connect_result,
            "account_status": account_status,
            "account_result": account_result,
            "place_result": place_result,
            "cancel_result": cancel_result,
            "readback_result": readback_result,
            "reconcile_result": reconcile_result,
            "live_disabled_result": live_result,
            "production_live_enabled": False,
            "capital_binding_enabled": False,
            "human_gate_required": True,
            "error": error,
        }
