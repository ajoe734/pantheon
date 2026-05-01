"""Truthful paper execution runtime package for the VM-2 execution plane."""

from __future__ import annotations

import json
import logging
import os
import threading
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_RUNTIME_MANAGER_DIR = Path(__file__).resolve().parents[2] / "runtime-manager"
if str(_RUNTIME_MANAGER_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_MANAGER_DIR))

from runtime_manager_client import RuntimeManagerClient
from services.execution.lean_runtime.pending_signal_store import (
    PendingSignalStore,
    build_pending_signal_store,
)
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.execution.lean_runtime.signal_consumer import SignalConsumer

log = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_float(value: str | None, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class _Holding:
    def __init__(self, quantity: float = 0.0) -> None:
        self.Quantity = quantity


class _Security:
    def __init__(self, price: float = 100.0) -> None:
        self.Price = price


@dataclass
class OrderEvent:
    event_type: str
    symbol: str
    quantity: float
    fill_price: float
    action: str
    submitted_to_broker: bool = False
    broker_submission_status: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_iso_now)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "event_type": self.event_type,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "fill_price": self.fill_price,
            "action": self.action,
            "submitted_to_broker": self.submitted_to_broker,
            "created_at": self.created_at,
        }
        if self.broker_submission_status:
            payload["broker_submission_status"] = self.broker_submission_status
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


class PaperExecutionAlgorithm:
    """Small LEAN-like surface used by the executor for paper fills."""

    def __init__(
        self,
        *,
        initial_cash: float = 100_000.0,
        default_price: float = 100.0,
        event_sink: Callable[[OrderEvent], None] | None = None,
    ) -> None:
        self._initial_cash = initial_cash
        self._default_price = default_price
        self._event_sink = event_sink
        self.Portfolio: dict[str, _Holding] = {}
        self.Securities: dict[str, _Security] = {}

    def _holding(self, symbol: str) -> _Holding:
        return self.Portfolio.setdefault(symbol, _Holding())

    def _security(self, symbol: str) -> _Security:
        return self.Securities.setdefault(symbol, _Security(price=self._default_price))

    def _publish(
        self,
        event_type: str,
        symbol: str,
        quantity: float,
        action: str,
        *,
        broker_submission_status: str | None = None,
        submitted_to_broker: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self._event_sink is None:
            return
        security = self._security(symbol)
        self._event_sink(
            OrderEvent(
                event_type=event_type,
                symbol=str(symbol),
                quantity=float(quantity),
                fill_price=float(security.Price),
                action=action,
                submitted_to_broker=submitted_to_broker,
                broker_submission_status=broker_submission_status,
                metadata=metadata or {},
            )
        )

    def SetHoldings(self, symbol: str, target_percent: float) -> None:  # noqa: N802
        security = self._security(symbol)
        target_quantity = (self._initial_cash * float(target_percent)) / max(float(security.Price), 0.01)
        delta = target_quantity - self._holding(symbol).Quantity
        self._holding(symbol).Quantity = target_quantity
        self._publish("fill_observation", symbol, delta, "set_holdings")

    def MarketOrder(self, symbol: str, quantity: float) -> None:  # noqa: N802
        self._security(symbol)
        self._holding(symbol).Quantity += float(quantity)
        self._publish("fill_observation", symbol, quantity, "market_order")

    def LimitOrder(self, symbol: str, quantity: float, limit_price: float) -> None:  # noqa: N802
        security = self._security(symbol)
        security.Price = float(limit_price)
        self._holding(symbol).Quantity += float(quantity)
        self._publish("fill_observation", symbol, quantity, "limit_order")

    def Liquidate(self, symbol: str) -> None:  # noqa: N802
        quantity = self._holding(symbol).Quantity
        self._holding(symbol).Quantity = 0.0
        self._publish("fill_observation", symbol, -quantity, "liquidate")

    def RecordBracketOrderLogged(  # noqa: N802
        self,
        symbol: str,
        *,
        signal_id: str,
        stop_loss_pct: float,
        take_profit_pct: float,
        broker_submission_status: str,
        submitted_to_broker: bool,
    ) -> None:
        self._publish(
            "bracket_order_logged",
            str(symbol),
            0.0,
            "bracket_order_logged",
            broker_submission_status=broker_submission_status,
            submitted_to_broker=submitted_to_broker,
            metadata={
                "signal_id": signal_id,
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
            },
        )

    def positions(self) -> list[dict[str, Any]]:
        positions: list[dict[str, Any]] = []
        for symbol, holding in sorted(self.Portfolio.items()):
            if holding.Quantity == 0:
                continue
            positions.append(
                {
                    "symbol": symbol,
                    "quantity": holding.Quantity,
                    "price": self._security(symbol).Price,
                }
            )
        return positions


class RuntimeBindingResolver:
    """Resolve the current binding context for this runtime id."""

    def __init__(self, client: RuntimeManagerClient, runtime_id: str | None) -> None:
        self._client = client
        self._runtime_id = runtime_id
        self._cached_binding: dict[str, Any] | None = None
        self._last_sync_at: str | None = None
        self._last_error: str | None = None

    def resolve(self) -> dict[str, Any] | None:
        if not self._runtime_id:
            return None
        try:
            bindings = self._client.list_all()
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{type(exc).__name__}: {exc}"
            log.warning("runtime-manager binding lookup failed: %s", exc)
            return self._cached_binding

        statuses = {"active": 0, "paused": 1, "pending_pause": 2, "failed": 3, "retired": 4}
        matches = [
            binding
            for binding in bindings
            if binding.get("runtime_id") == self._runtime_id
        ]
        if not matches:
            self._cached_binding = None
            self._last_sync_at = _iso_now()
            self._last_error = None
            return None

        matches.sort(key=lambda item: statuses.get(str(item.get("status")), 99))
        self._cached_binding = matches[0]
        self._last_sync_at = _iso_now()
        self._last_error = None
        return self._cached_binding

    def snapshot(self) -> dict[str, Any]:
        binding = self._cached_binding or {}
        return {
            "resolved": self._cached_binding is not None,
            "binding_id": binding.get("binding_id"),
            "status": binding.get("status"),
            "deployment_mode": binding.get("deployment_mode"),
            "capital_pool_id": binding.get("capital_pool_id"),
            "plan_id": binding.get("plan_id"),
            "artifact_id": binding.get("artifact_id"),
            "artifact_version": binding.get("artifact_version"),
            "persona_capital_binding_id": binding.get("persona_capital_binding_id"),
            "last_sync_at": self._last_sync_at,
            "last_error": self._last_error,
        }


class RuntimeTelemetryEmitter:
    """Emit canonical telemetry envelopes to the configured ingest surface."""

    def __init__(self, identity: RuntimeIdentity, binding_resolver: RuntimeBindingResolver) -> None:
        self._identity = identity
        self._binding_resolver = binding_resolver
        self._url = str(os.getenv("PANTHEON_TELEMETRY_URL", "")).strip().rstrip("/")
        self._timeout = int(os.getenv("PANTHEON_TELEMETRY_TIMEOUT_SECONDS", "5"))
        self._enabled = bool(self._url)
        self._sent = 0
        self._failed = 0
        self._last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def emit(self, event_type: str, metrics: dict[str, Any], metadata: dict[str, Any] | None = None) -> bool:
        if not self._enabled:
            return False

        binding = self._binding_resolver.resolve()
        if not binding:
            self._failed += 1
            self._last_error = "binding context unresolved"
            return False

        deployment_stage = str(binding.get("deployment_mode") or self._identity.runtime_mode or "paper")
        execution_mode = "paper" if deployment_stage == "paper" else "live"
        artifact_version = str(binding.get("artifact_version") or "0.0.0")
        artifact_id = str(binding.get("artifact_id") or "")
        strategy_id = str(os.getenv("PANTHEON_STRATEGY_ID", artifact_id or "paper-runtime"))
        artifact_type = str(os.getenv("PANTHEON_ARTIFACT_TYPE", "model_artifact"))
        payload = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": execution_mode,
            "environment": deployment_stage,
            "deployment_stage": deployment_stage,
            "binding_id": str(binding.get("binding_id") or ""),
            "runtime_id": str(binding.get("runtime_id") or self._identity.runtime_id or ""),
            "capital_pool_id": str(binding.get("capital_pool_id") or ""),
            "artifact_id": artifact_id,
            "artifact_version": artifact_version,
            "plan_id": str(binding.get("plan_id") or ""),
            "persona_capital_binding_id": str(binding.get("persona_capital_binding_id") or ""),
            "authority_refs": self._identity.authority_refs(),
            "target": {
                "registry_id": artifact_id,
                "strategy_id": strategy_id,
                "artifact_version": artifact_version,
                "artifact_type": artifact_type,
                "promotion_state": "paper" if deployment_stage == "paper" else "live",
            },
            "metrics": metrics,
            "metadata": metadata or {},
        }
        if self._identity.trace_id:
            payload["trace_id"] = self._identity.trace_id

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self._url}/api/telemetry/ingest",
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                status_code = getattr(response, "status", 200)
                if status_code not in {200, 202}:
                    raise RuntimeError(f"telemetry ingest returned HTTP {status_code}")
        except urllib.error.HTTPError as exc:
            self._failed += 1
            self._last_error = f"HTTPError {exc.code}: {exc.reason}"
            return False
        except Exception as exc:  # noqa: BLE001
            self._failed += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            return False

        self._sent += 1
        self._last_error = None
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "url": self._url or None,
            "sent": self._sent,
            "failed": self._failed,
            "last_error": self._last_error,
        }


class PaperRuntimeService:
    """Background execution loop plus HTTP-readable runtime state."""

    def __init__(
        self,
        *,
        store: PendingSignalStore | None = None,
        identity: RuntimeIdentity | None = None,
        runtime_manager_client: RuntimeManagerClient | None = None,
        telemetry_emitter: RuntimeTelemetryEmitter | None = None,
        poll_interval_seconds: float | None = None,
        max_batch_size: int | None = None,
    ) -> None:
        self._identity = identity or RuntimeIdentity.from_env()
        self._store = store or build_pending_signal_store(
            os.getenv("SIGNAL_STORE_URL", "redis://signal-store:6379"),
            queue_key=os.getenv("PANTHEON_SIGNAL_QUEUE_KEY", "pantheon:signals:pending"),
            default_batch_size=int(os.getenv("PANTHEON_SIGNAL_BATCH_SIZE", "100")),
        )
        self._runtime_manager_client = runtime_manager_client or RuntimeManagerClient(
            base_url=self._identity.runtime_manager_url,
            bearer_token=self._identity.runtime_manager_auth.token,
        )
        self._binding_resolver = RuntimeBindingResolver(self._runtime_manager_client, self._identity.runtime_id)
        self._telemetry = telemetry_emitter or RuntimeTelemetryEmitter(self._identity, self._binding_resolver)
        self._algo = PaperExecutionAlgorithm(event_sink=self._handle_order_event)
        self._consumer = SignalConsumer(store_client=self._store)
        self._poll_interval_seconds = poll_interval_seconds or _as_float(
            os.getenv("PANTHEON_RUNTIME_POLL_INTERVAL_SECONDS"),
            1.0,
        )
        self._max_batch_size = max(int(max_batch_size or os.getenv("PANTHEON_SIGNAL_BATCH_SIZE", "100")), 1)
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = _iso_now()
        self._last_poll_at: str | None = None
        self._last_drain_at: str | None = None
        self._last_error: str | None = None
        self._last_heartbeat_at: str | None = None
        self._poll_count = 0
        self._processed_signal_count = 0
        self._execution_event_count = 0
        self._recent_order_events: list[dict[str, Any]] = []

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="paper-runtime-loop")
        self._thread.start()

    def stop(self) -> None:
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def drain_once(self) -> dict[str, Any]:
        with self._lock:
            self._last_poll_at = _iso_now()
            before = len(self._consumer._processed_signal_ids)
            self._binding_resolver.resolve()
            try:
                self._consumer.drain(algo=self._algo)
                self._last_drain_at = _iso_now()
                self._last_error = None
            except Exception as exc:  # noqa: BLE001
                self._last_error = f"{type(exc).__name__}: {exc}"
                log.exception("paper runtime drain failed")
            after = len(self._consumer._processed_signal_ids)
            self._processed_signal_count += max(after - before, 0)
            self._poll_count += 1
            self._maybe_emit_heartbeat()
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._identity.to_health_payload(),
                "status": "ok" if self._last_error is None else "degraded",
                "runtime_package": "paper_execution_runtime",
                "runtime_package_version": "ep4",
                "paper_execution_ready": True,
                "signal_consumer_ready": True,
                "signal_store": {
                    "kind": getattr(self._store, "kind", type(self._store).__name__),
                    "queue_depth": self._safe_queue_depth(),
                    "batch_size": self._max_batch_size,
                },
                "binding_lookup": self._binding_resolver.snapshot(),
                "telemetry": self._telemetry.snapshot(),
                "paper_state": {
                    "started_at": self._started_at,
                    "last_poll_at": self._last_poll_at,
                    "last_drain_at": self._last_drain_at,
                    "last_heartbeat_at": self._last_heartbeat_at,
                    "poll_count": self._poll_count,
                    "processed_signal_count": self._processed_signal_count,
                    "execution_event_count": self._execution_event_count,
                    "positions": self._algo.positions(),
                    "recent_order_events": list(self._recent_order_events),
                    "last_error": self._last_error,
                },
                "stub_mode": False,
            }

    def _run_loop(self) -> None:
        while not self._shutdown.is_set():
            self.drain_once()
            self._shutdown.wait(self._poll_interval_seconds)

    def _handle_order_event(self, event: OrderEvent) -> None:
        event_payload = event.to_dict()
        self._execution_event_count += 1
        self._recent_order_events.append(event_payload)
        self._recent_order_events = self._recent_order_events[-20:]
        telemetry_metadata = {
            "runtime_package": "paper_execution_runtime",
            "symbol": event.symbol,
            "sim_fill_flag": event.event_type == "fill_observation",
            "is_real_order": False,
            "is_real_capital": False,
            "submitted_to_broker": event.submitted_to_broker,
            "capital_scale_pct": 0,
        }
        if event.broker_submission_status:
            telemetry_metadata["broker_submission_status"] = event.broker_submission_status
        telemetry_metadata.update(event.metadata)
        self._telemetry.emit(
            event.event_type,
            {
                "fill_quantity": event.quantity,
                "fill_price": event.fill_price,
                "action": event.action,
                "submitted_to_broker": event.submitted_to_broker,
            },
            metadata=telemetry_metadata,
        )

    def _maybe_emit_heartbeat(self) -> None:
        if not self._telemetry.enabled:
            return
        now = _iso_now()
        if self._last_heartbeat_at == now:
            return
        emitted = self._telemetry.emit(
            "heartbeat",
            {"heartbeat": 1},
            metadata={
                "runtime_package": "paper_execution_runtime",
                "queue_depth": self._safe_queue_depth(),
                "is_real_order": False,
                "is_real_capital": False,
                "sim_fill_flag": True,
                "capital_scale_pct": 0,
            },
        )
        if emitted:
            self._last_heartbeat_at = now

    def _safe_queue_depth(self) -> int | None:
        try:
            return int(self._store.queue_depth())
        except Exception:  # noqa: BLE001
            return None


_SERVICE: PaperRuntimeService | None = None


def get_service() -> PaperRuntimeService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = PaperRuntimeService()
    return _SERVICE


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _write_json(self, status_code: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/__health__", "/health"}:
            snapshot = get_service().snapshot()
            status_code = 200 if snapshot.get("status") == "ok" else 503
            self._write_json(status_code, snapshot)
            return
        if self.path == "/api/runtime/state":
            self._write_json(200, get_service().snapshot())
            return
        if self.path == "/api/runtime/orders":
            snapshot = get_service().snapshot()
            self._write_json(200, {"orders": snapshot["paper_state"]["recent_order_events"]})
            return
        self._write_json(404, {"status": "not_found", "path": self.path})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/runtime/drain":
            self._write_json(200, get_service().drain_once())
            return
        self._write_json(404, {"status": "not_found", "path": self.path})


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    service = get_service()
    service.start()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8010"))
    server = ThreadingHTTPServer((host, port), _Handler)
    print(
        json.dumps(
            {
                "message": "paper execution runtime ready",
                "role": service.snapshot().get("runtime_role"),
                "runtime_package": "paper_execution_runtime",
                "port": port,
                "stub_mode": False,
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        service.stop()


if __name__ == "__main__":
    main()
