"""Paper broker adapter for the OpenClaw gateway adapter.

Gated by OPENCLAW_PAPER_ADAPTER_ENABLED. Routes paper orders to the broker
sidecar when enabled. Live orders are always rejected regardless of gate state.
Capital and strategy binding checks are enforced before forwarding any order.

Env vars:
  OPENCLAW_PAPER_ADAPTER_ENABLED   — "true" enables paper orders (default: false)
  OPENCLAW_BROKER_SIDECAR_URL      — broker sidecar base URL (e.g. http://broker:8102)
  OPENCLAW_RUNTIME_MANAGER_URL     — runtime-manager base URL for active binding checks
  PANTHEON_RUNTIME_MANAGER_TOKEN   — bearer token for runtime-manager calls
  OPENCLAW_PAPER_BROKER_AUDIT_PATH — override audit log path
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class PaperBrokerAdapterError(Exception):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        status_code: int = 400,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": "paper_broker_error",
            "error_code": self.error_code,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class PaperBrokerAuditLog:
    """Append-only JSONL audit log for paper order intents and results."""

    def __init__(
        self,
        path: Optional[str] = None,
        *,
        clock: Callable[[], str] = _utc_now_iso,
    ) -> None:
        if path is None:
            raw = os.getenv("OPENCLAW_PAPER_BROKER_AUDIT_PATH", "")
            path = raw or "/tmp/openclaw-gateway-adapter/paper_broker_audit.jsonl"
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()

    def record(self, entry: Dict[str, Any]) -> None:
        entry.setdefault("at", self._clock())
        line = json.dumps(entry, separators=(",", ":")) + "\n"
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    def read(
        self,
        *,
        operator_id: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        if not self._path.exists():
            return []
        results: List[Dict[str, Any]] = []
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                for raw_line in fh:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        entry = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    if operator_id and entry.get("operator_id") != operator_id:
                        continue
                    if capital_pool_id and entry.get("capital_pool_id") != capital_pool_id:
                        continue
                    results.append(entry)
        except OSError:
            return []
        return results[-limit:]


class PaperBrokerAdapter:
    """Routes paper orders to the broker sidecar when the paper gate is enabled.

    Live orders are always rejected regardless of gate state.
    Capital and strategy binding checks are enforced on every paper order.
    """

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        broker_url: Optional[str] = None,
        runtime_manager_url: Optional[str] = None,
        runtime_manager_token: Optional[str] = None,
        binding_resolver: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
        audit_log: Optional[PaperBrokerAuditLog] = None,
        clock: Callable[[], str] = _utc_now_iso,
        trace_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        if enabled is None:
            enabled = os.getenv("OPENCLAW_PAPER_ADAPTER_ENABLED", "").lower() in {"1", "true", "yes"}
        if broker_url is None:
            broker_url = os.getenv("OPENCLAW_BROKER_SIDECAR_URL", "")
        if runtime_manager_url is None:
            runtime_manager_url = (
                os.getenv("OPENCLAW_RUNTIME_MANAGER_URL", "")
                or os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "")
            )
        if runtime_manager_token is None:
            runtime_manager_token = os.getenv("PANTHEON_RUNTIME_MANAGER_TOKEN", "runtime-control-internal")
        self._enabled = enabled
        self._broker_url = (broker_url or "").rstrip("/")
        self._runtime_manager_url = (runtime_manager_url or "").rstrip("/")
        self._runtime_manager_token = (runtime_manager_token or "").strip()
        self._binding_resolver = binding_resolver
        self._audit = audit_log or PaperBrokerAuditLog()
        self._clock = clock
        self._trace_id_factory = trace_id_factory

    @property
    def enabled(self) -> bool:
        return self._enabled

    def capability_snapshot(self) -> Dict[str, Any]:
        return {
            "sandbox_adapter_state": "activation_ready",
            "sandbox_gate": "OPENCLAW_PAPER_ADAPTER_ENABLED",
            "paper_adapter_enabled": self._enabled,
            "live_adapter_enabled": False,
            "broker_sidecar_configured": bool(self._broker_url),
            "runtime_manager_configured": bool(self._runtime_manager_url or self._binding_resolver),
            "runtime_binding_check": "required_for_submit",
            "deployment_stage": "paper" if self._enabled else "none",
            "is_real_capital": False,
            "is_real_order": False,
        }

    # ------------------------------------------------------------------
    # Paper order operations (gated)
    # ------------------------------------------------------------------

    def submit_paper_order(
        self,
        *,
        capital_pool_id: str,
        strategy_id: str,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        operator_id: str,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._gate_check()
        runtime_binding = self._binding_check(
            capital_pool_id=capital_pool_id,
            strategy_id=strategy_id,
            operator_id=operator_id,
        )

        trace_id = trace_id or self._trace_id_factory()
        base_audit: Dict[str, Any] = {
            "event": "paper_order_intent",
            "trace_id": trace_id,
            "operator_id": operator_id,
            "capital_pool_id": capital_pool_id,
            "strategy_id": strategy_id,
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "order_type": order_type,
            "limit_price": limit_price,
            "is_real_order": False,
            "is_real_capital": False,
            "deployment_stage": "paper",
            "runtime_binding_id": runtime_binding.get("binding_id"),
            "persona_capital_binding_id": runtime_binding.get("persona_capital_binding_id"),
            "artifact_id": runtime_binding.get("artifact_id"),
        }
        self._audit.record({**base_audit, "outcome": "pending"})

        try:
            result = self._call_sidecar(
                "POST",
                "/api/broker/paper/orders",
                payload={
                    "capital_pool_id": capital_pool_id,
                    "strategy_id": strategy_id,
                    "symbol": symbol,
                    "qty": qty,
                    "side": side,
                    "order_type": order_type,
                    "limit_price": limit_price,
                },
            )
        except PaperBrokerAdapterError as exc:
            self._audit.record(
                {**base_audit, "outcome": "error", "error_code": exc.error_code, "error": exc.message}
            )
            raise

        order = result.get("order", {})
        self._audit.record(
            {
                **base_audit,
                "outcome": "ok",
                "order_id": order.get("order_id"),
                "fill_price": order.get("fill_price"),
                "fill_qty": order.get("fill_qty"),
                "status": order.get("status"),
                "sim_fill_flag": True,
            }
        )
        return result

    def list_paper_orders(
        self,
        *,
        capital_pool_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        self._gate_check()
        params: Dict[str, Any] = {"limit": limit}
        if capital_pool_id:
            params["capital_pool_id"] = capital_pool_id
        if strategy_id:
            params["strategy_id"] = strategy_id
        return self._call_sidecar("GET", "/api/broker/paper/orders", params=params)

    def get_paper_order(self, order_id: str) -> Dict[str, Any]:
        self._gate_check()
        return self._call_sidecar("GET", f"/api/broker/paper/orders/{order_id}")

    def cancel_paper_order(
        self,
        order_id: str,
        *,
        operator_id: str,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._gate_check()
        if not operator_id:
            raise PaperBrokerAdapterError(
                "OPERATOR_REQUIRED",
                "operator_id is required for paper order cancellation.",
                status_code=401,
            )
        trace_id = trace_id or self._trace_id_factory()
        audit_entry: Dict[str, Any] = {
            "event": "paper_order_cancel_intent",
            "trace_id": trace_id,
            "operator_id": operator_id,
            "order_id": order_id,
            "is_real_order": False,
            "is_real_capital": False,
            "deployment_stage": "paper",
        }
        self._audit.record({**audit_entry, "outcome": "pending"})
        try:
            result = self._call_sidecar("POST", f"/api/broker/paper/orders/{order_id}/cancel")
        except PaperBrokerAdapterError as exc:
            self._audit.record(
                {**audit_entry, "outcome": "error", "error_code": exc.error_code, "error": exc.message}
            )
            raise
        self._audit.record({**audit_entry, "outcome": "ok"})
        return result

    # ------------------------------------------------------------------
    # Live order operations (always rejected)
    # ------------------------------------------------------------------

    def reject_live_order(self, operator_id: Optional[str] = None) -> None:
        """Always reject live orders. Gate state is irrelevant."""
        self._audit.record(
            {
                "event": "live_order_rejected",
                "operator_id": operator_id or "",
                "is_real_order": False,
                "is_real_capital": False,
                "live_enabled": False,
                "outcome": "rejected",
            }
        )
        raise PaperBrokerAdapterError(
            "LIVE_ADAPTER_DISABLED",
            "Live broker execution is disabled. Live orders are not accepted.",
            status_code=403,
            details={"live_enabled": False, "deployment_stage": "paper"},
        )

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def read_audit(
        self,
        *,
        operator_id: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        return self._audit.read(
            operator_id=operator_id,
            capital_pool_id=capital_pool_id,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _gate_check(self) -> None:
        if not self._enabled:
            raise PaperBrokerAdapterError(
                "PAPER_ADAPTER_DISABLED",
                (
                    "Paper broker adapter is disabled. "
                    "Set OPENCLAW_PAPER_ADAPTER_ENABLED=true to enable."
                ),
                status_code=503,
            )

    def _binding_check(self, *, capital_pool_id: str, strategy_id: str, operator_id: str) -> Dict[str, Any]:
        if not capital_pool_id:
            raise PaperBrokerAdapterError(
                "CAPITAL_POOL_REQUIRED",
                "capital_pool_id is required for paper orders.",
                status_code=400,
            )
        if not strategy_id:
            raise PaperBrokerAdapterError(
                "STRATEGY_ID_REQUIRED",
                "strategy_id is required for paper orders.",
                status_code=400,
            )
        if not operator_id:
            raise PaperBrokerAdapterError(
                "OPERATOR_REQUIRED",
                "operator_id is required for paper orders.",
                status_code=401,
            )
        binding = self._resolve_active_binding(capital_pool_id)
        if not binding:
            raise PaperBrokerAdapterError(
                "RUNTIME_BINDING_NOT_FOUND",
                (
                    "No active RuntimeBinding was found for this capital_pool_id. "
                    "Paper order submission requires an active paper runtime binding."
                ),
                status_code=409,
                details={"capital_pool_id": capital_pool_id},
            )
        if binding.get("capital_pool_id") != capital_pool_id:
            raise PaperBrokerAdapterError(
                "CAPITAL_BINDING_MISMATCH",
                "Active RuntimeBinding capital_pool_id does not match the paper order.",
                status_code=409,
                details={
                    "expected_capital_pool_id": capital_pool_id,
                    "actual_capital_pool_id": binding.get("capital_pool_id"),
                    "binding_id": binding.get("binding_id"),
                },
            )
        if binding.get("status") != "active":
            raise PaperBrokerAdapterError(
                "RUNTIME_BINDING_NOT_ACTIVE",
                "RuntimeBinding must be active before accepting paper orders.",
                status_code=409,
                details={"binding_id": binding.get("binding_id"), "status": binding.get("status")},
            )
        if binding.get("deployment_mode") != "paper":
            raise PaperBrokerAdapterError(
                "PAPER_RUNTIME_BINDING_REQUIRED",
                "RuntimeBinding deployment_mode must be paper before accepting paper orders.",
                status_code=409,
                details={"binding_id": binding.get("binding_id"), "deployment_mode": binding.get("deployment_mode")},
            )
        candidates = self._strategy_candidates(binding)
        if strategy_id not in candidates:
            raise PaperBrokerAdapterError(
                "STRATEGY_BINDING_MISMATCH",
                "strategy_id does not match the active paper RuntimeBinding.",
                status_code=409,
                details={
                    "strategy_id": strategy_id,
                    "allowed_strategy_refs": sorted(candidates),
                    "binding_id": binding.get("binding_id"),
                    "artifact_id": binding.get("artifact_id"),
                },
            )
        return binding

    def _resolve_active_binding(self, capital_pool_id: str) -> Optional[Dict[str, Any]]:
        if self._binding_resolver is not None:
            try:
                return self._binding_resolver(capital_pool_id)
            except PaperBrokerAdapterError:
                raise
            except Exception as exc:
                raise PaperBrokerAdapterError(
                    "RUNTIME_BINDING_CHECK_FAILED",
                    f"RuntimeBinding resolver failed: {exc}",
                    status_code=503,
                ) from exc
        if not self._runtime_manager_url:
            raise PaperBrokerAdapterError(
                "RUNTIME_MANAGER_NOT_CONFIGURED",
                (
                    "OPENCLAW_RUNTIME_MANAGER_URL or PANTHEON_RUNTIME_MANAGER_URL "
                    "must be configured before paper order submission."
                ),
                status_code=503,
            )
        try:
            import httpx

            encoded_pool_id = urllib.parse.quote(capital_pool_id, safe="")
            headers = {"Accept": "application/json"}
            if self._runtime_manager_token:
                headers["Authorization"] = f"Bearer {self._runtime_manager_token}"
            with httpx.Client(timeout=5) as client:
                response = client.get(
                    f"{self._runtime_manager_url}/api/runtimes/{encoded_pool_id}/active",
                    headers=headers,
                )
            if response.status_code == 404:
                return None
            if response.status_code >= 400:
                try:
                    body = response.json()
                except Exception:
                    body = {"message": response.text}
                error = body.get("error", {}) if isinstance(body.get("error"), dict) else {}
                raise PaperBrokerAdapterError(
                    error.get("code") or body.get("error_code") or "RUNTIME_MANAGER_ERROR",
                    error.get("message")
                    or body.get("message")
                    or f"runtime-manager returned HTTP {response.status_code}",
                    status_code=response.status_code if response.status_code < 500 else 502,
                    details=body,
                )
            payload = response.json()
            if not isinstance(payload, dict):
                raise PaperBrokerAdapterError(
                    "RUNTIME_BINDING_SCHEMA_ERROR",
                    "runtime-manager active binding response did not match the expected schema.",
                    status_code=502,
                    details={"payload_type": type(payload).__name__},
                )
            return payload
        except PaperBrokerAdapterError:
            raise
        except Exception as exc:
            raise PaperBrokerAdapterError(
                "RUNTIME_MANAGER_UNAVAILABLE",
                f"runtime-manager is unavailable: {exc}",
                status_code=503,
            ) from exc

    def _strategy_candidates(self, binding: Dict[str, Any]) -> set[str]:
        candidates: set[str] = set()
        for key in ("strategy_id", "artifact_id"):
            value = str(binding.get(key) or "").strip()
            if value:
                candidates.add(value)
        metadata = binding.get("metadata")
        if isinstance(metadata, dict):
            for key in ("strategy_id", "strategy_ref", "artifact_strategy_id"):
                value = str(metadata.get(key) or "").strip()
                if value:
                    candidates.add(value)
        return candidates

    def _call_sidecar(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self._broker_url:
            raise PaperBrokerAdapterError(
                "BROKER_SIDECAR_NOT_CONFIGURED",
                "OPENCLAW_BROKER_SIDECAR_URL is not configured.",
                status_code=503,
                details={"hint": "Set OPENCLAW_BROKER_SIDECAR_URL to point to the broker sidecar."},
            )
        try:
            import httpx

            with httpx.Client(timeout=5) as client:
                if method == "GET":
                    response = client.get(
                        f"{self._broker_url}{path}",
                        params=params,
                        headers={"Accept": "application/json"},
                    )
                else:
                    response = client.post(
                        f"{self._broker_url}{path}",
                        json=payload,
                        headers={"Accept": "application/json"},
                    )
            if response.status_code >= 400:
                try:
                    body = response.json()
                except Exception:
                    body = {"message": response.text}
                raise PaperBrokerAdapterError(
                    body.get("error_code", "BROKER_SIDECAR_ERROR"),
                    body.get("message", f"Broker sidecar returned HTTP {response.status_code}"),
                    status_code=response.status_code if response.status_code < 500 else 502,
                    details=body,
                )
            if not response.content:
                return {}
            return response.json()
        except PaperBrokerAdapterError:
            raise
        except Exception as exc:
            raise PaperBrokerAdapterError(
                "BROKER_SIDECAR_UNAVAILABLE",
                f"Broker sidecar is unavailable: {exc}",
                status_code=503,
            ) from exc
