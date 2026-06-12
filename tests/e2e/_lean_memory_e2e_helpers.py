from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.execution.lean_runtime.runtime_identity import RuntimeIdentity


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def market_connector(
    *,
    connector_id: str,
    provider: str,
    dataset: str,
    feature_target: str,
    schema_hash: str,
) -> dict[str, Any]:
    return {
        "connector_id": connector_id,
        "source_type": "market",
        "provider": provider,
        "license_scope": "internal",
        "metadata": {
            "dataset": dataset,
            "feature_targets": [feature_target],
            "schema_hash": schema_hash,
        },
    }


def market_record(
    *,
    source_id: str,
    dataset: str,
    symbol: str,
    trade_date: str,
    close: float,
    volume: int,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": f"{symbol} daily close for {trade_date}",
        "content_ref": f"market://{dataset}/{symbol}/{trade_date}",
        "metadata": {
            "dataset": dataset,
            "date": trade_date,
            "symbol": symbol,
            "open": close + 0.25,
            "high": close + 1.25,
            "low": close - 1.25,
            "close": close,
            "volume": volume,
        },
    }


def signal_from_market_row(
    row: dict[str, Any],
    *,
    signal_id: str,
    strategy_id: str,
    symbol: str,
    action: str,
    direction: str,
    quantity: float,
    quantity_type: str,
    source_worker: str,
    alpha_source: str,
    normalized_ref_uris: list[str],
    ingest_run_id: str,
    confidence_score: float = 0.9,
    order_type: str | None = None,
    limit_price: float | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = row["metadata"]
    payload: dict[str, Any] = {
        "signal_id": signal_id,
        "version": "1.0",
        "strategy_id": strategy_id,
        "timestamp": iso_now(),
        "symbol": symbol,
        "action": action,
        "direction": direction,
        "quantity": quantity,
        "quantity_type": quantity_type,
        "source_worker": source_worker,
        "metadata": {
            "alpha_source": alpha_source,
            "confidence_score": confidence_score,
            "market_data_ref": normalized_ref_uris,
            "market_data": {
                "dataset": metadata["dataset"],
                "symbol": metadata["symbol"],
                "date": metadata["date"],
                "close": metadata["close"],
                "content_ref": row["content_ref"],
            },
            "normalized_data_ref": normalized_ref_uris,
            "source_dataset_ref": metadata["dataset"],
            "ingest_run_id": ingest_run_id,
        },
    }
    if order_type:
        payload["order_type"] = order_type
    if limit_price is not None:
        payload["limit_price"] = float(limit_price)
    if extra_metadata:
        payload["metadata"].update(extra_metadata)
    return payload


def runtime_identity(*, loop_id: str) -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": f"paper-runtime-{loop_id}",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": f"trace-e2e-loop-{loop_id}-runtime",
            "PANTHEON_REQUEST_ID": f"request-e2e-loop-{loop_id}",
        }
    )


class RuntimeManagerClient:
    def __init__(
        self,
        *,
        loop_id: str,
        artifact_id: str,
        artifact_version: str,
        plan_id: str,
        persona_capital_binding_id: str,
    ) -> None:
        self._binding = {
            "binding_id": f"binding-e2e-loop-{loop_id}",
            "runtime_id": f"paper-runtime-{loop_id}",
            "capital_pool_id": "pool-paper",
            "artifact_id": artifact_id,
            "artifact_version": artifact_version,
            "deployment_mode": "paper",
            "deployment_stage": "paper",
            "plan_id": plan_id,
            "persona_capital_binding_id": persona_capital_binding_id,
            "status": "active",
        }

    def list_all(self) -> list[dict[str, Any]]:
        return [dict(self._binding)]


class CanonicalTelemetryRecorder:
    enabled = True

    def __init__(
        self,
        *,
        loop_id: str,
        artifact_id: str,
        artifact_version: str,
        plan_id: str,
        persona_capital_binding_id: str,
        default_strategy_id: str,
    ) -> None:
        self.events: list[dict[str, Any]] = []
        self._loop_id = loop_id
        self._artifact_id = artifact_id
        self._artifact_version = artifact_version
        self._plan_id = plan_id
        self._persona_capital_binding_id = persona_capital_binding_id
        self._default_strategy_id = default_strategy_id

    def emit(self, event_type: str, metrics: dict[str, Any], metadata: dict[str, Any] | None = None) -> bool:
        metadata = dict(metadata or {})
        index = len(self.events) + 1
        event = {
            "event_id": f"e2e-loop-{self._loop_id}-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": f"binding-e2e-loop-{self._loop_id}",
            "runtime_id": f"paper-runtime-{self._loop_id}",
            "capital_pool_id": "pool-paper",
            "artifact_id": self._artifact_id,
            "artifact_version": self._artifact_version,
            "plan_id": self._plan_id,
            "persona_capital_binding_id": self._persona_capital_binding_id,
            "target": {
                "registry_id": self._artifact_id,
                "strategy_id": metadata.get("strategy_id") or self._default_strategy_id,
                "artifact_version": self._artifact_version,
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": metadata,
            "trace_id": f"trace-e2e-loop-{self._loop_id}-runtime",
        }
        self.events.append(event)
        return True

    def emit_heartbeat(self, metadata: dict[str, Any] | None = None) -> bool:
        return self.emit("heartbeat", {"heartbeat": 1}, metadata)

    def emit_pnl_snapshot(
        self,
        pnl: float,
        metadata: dict[str, Any] | None = None,
        extra_metrics: dict[str, Any] | None = None,
    ) -> bool:
        metrics = {"pnl": float(pnl)}
        metrics.update(extra_metrics or {})
        return self.emit("pnl_snapshot", metrics, metadata)

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "url": "memory://telemetry",
            "sent": len(self.events),
            "failed": 0,
            "last_error": None,
        }
