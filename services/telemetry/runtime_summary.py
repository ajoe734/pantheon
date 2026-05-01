"""Runtime status projection derived from accepted telemetry events."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_rfc3339(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _summary_key(payload: dict[str, Any]) -> Optional[str]:
    runtime_id = str(payload.get("runtime_id") or "").strip()
    return runtime_id or None


class RuntimeSummaryProjectionStore:
    """Small telemetry-owned read model for BFF runtime status surfaces."""

    def __init__(
        self,
        path: Optional[str | Path] = None,
        *,
        heartbeat_stale_after_seconds: int = 90,
    ) -> None:
        self._path = Path(path) if path else None
        self._heartbeat_stale_after_seconds = max(int(heartbeat_stale_after_seconds), 1)
        self._lock = threading.RLock()
        self._summaries: dict[str, dict[str, Any]] = {}
        self._load()

    @property
    def path(self) -> Optional[str]:
        return str(self._path) if self._path else None

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "summary_count": len(self._summaries),
                "path": self.path,
                "heartbeat_stale_after_seconds": self._heartbeat_stale_after_seconds,
            }

    def project_event(self, event: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Update the runtime summary from one validated, accepted telemetry event."""
        if str(event.get("deployment_stage") or "").lower() != "paper":
            return None

        runtime_id = _summary_key(event)
        if not runtime_id:
            return None

        event_type = str(event.get("event_type") or "").strip()
        event_time = str(event.get("created_at") or utc_now_rfc3339())
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        metrics = event.get("metrics") if isinstance(event.get("metrics"), dict) else {}
        binding_id = str(event.get("binding_id") or event.get("runtime_binding_id") or "").strip()
        bridge_repo = metadata.get("engine_bridge_repo")
        bridge_commit = metadata.get("engine_bridge_commit")
        bridge_path = metadata.get("engine_bridge_path")
        runtime_adapter_version = metadata.get("runtime_adapter_version")

        with self._lock:
            current = dict(self._summaries.get(runtime_id, {}))
            current.update(
                {
                    "id": runtime_id,
                    "runtime_id": runtime_id,
                    "runtime_binding_id": binding_id,
                    "binding_id": binding_id,
                    "deployment_stage": event.get("deployment_stage"),
                    "capital_pool_id": event.get("capital_pool_id"),
                    "artifact_id": event.get("artifact_id"),
                    "artifact_version": event.get("artifact_version"),
                    "plan_id": event.get("plan_id"),
                    "deployment_plan_id": event.get("plan_id"),
                    "persona_capital_binding_id": event.get("persona_capital_binding_id"),
                    "last_event_id": event.get("event_id"),
                    "last_event_type": event_type,
                    "last_event_at": event_time,
                    "collected_at": event_time,
                    "window": "latest",
                    "projection_source": "telemetry_ingest",
                    "projection_updated_at": utc_now_rfc3339(),
                }
            )

            if event_type == "heartbeat":
                current["last_heartbeat_at"] = event_time
                current["state"] = "active"
            elif event_type == "deploy_started":
                current.setdefault("state", "active")
            elif event_type == "deploy_completed":
                current["state"] = "active"

            metric_map = {
                "pnl": ("pnl",),
                "drawdown": ("drawdown", "drawdown_pct"),
                "sharpe_ratio": ("sharpe_ratio",),
                "fill_rate": ("fill_rate",),
                "avg_slippage_bps": ("avg_slippage_bps", "slippage_bps"),
                "total_trades": ("total_trades",),
            }
            for target_key, source_keys in metric_map.items():
                for source_key in source_keys:
                    if source_key in metrics:
                        current[target_key] = metrics[source_key]
                        break

            if bridge_repo is not None:
                current["engine_bridge_repo"] = bridge_repo
            if bridge_commit is not None:
                current["engine_bridge_commit"] = bridge_commit
            if bridge_path is not None:
                current["engine_bridge_path"] = bridge_path
            if runtime_adapter_version is not None:
                current["runtime_adapter_version"] = runtime_adapter_version

            current["health_summary"] = self._health_summary(current)
            self._summaries[runtime_id] = current
            self._persist()
            return json.loads(json.dumps(current))

    def get(self, runtime_id: str, *, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
        with self._lock:
            summary = self._summaries.get(str(runtime_id))
            if not summary:
                return None
            return self._apply_staleness(summary, now=now)

    def list(self, *, now: Optional[datetime] = None) -> list[dict[str, Any]]:
        with self._lock:
            return [
                self._apply_staleness(summary, now=now)
                for summary in sorted(self._summaries.values(), key=lambda item: str(item.get("runtime_id") or ""))
            ]

    def _health_summary(self, summary: dict[str, Any]) -> dict[str, str]:
        has_bridge_identity = bool(summary.get("engine_bridge_repo") and summary.get("engine_bridge_commit"))
        telemetry_state = "ok" if summary.get("last_heartbeat_at") else "degraded"
        return {
            "paper_runtime": "ok" if summary.get("state") in {"active", "degraded"} else "degraded",
            "bridge": "ok" if has_bridge_identity else "degraded",
            "telemetry": telemetry_state,
            "broker": "not_applicable",
        }

    def _apply_staleness(
        self,
        summary: dict[str, Any],
        *,
        now: Optional[datetime] = None,
    ) -> dict[str, Any]:
        projected = json.loads(json.dumps(summary))
        heartbeat_at = _parse_rfc3339(projected.get("last_heartbeat_at"))
        if heartbeat_at is None:
            return projected

        reference = now or datetime.now(timezone.utc)
        age_seconds = (reference - heartbeat_at).total_seconds()
        if age_seconds > self._heartbeat_stale_after_seconds:
            projected["state"] = "degraded"
            health = dict(projected.get("health_summary") or {})
            health["telemetry"] = "degraded"
            projected["health_summary"] = health
            projected["staleness"] = {
                "last_known_at": projected.get("last_heartbeat_at"),
                "age_seconds": int(age_seconds),
                "threshold_seconds": self._heartbeat_stale_after_seconds,
            }
        return projected

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8").strip() or "{}")
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(payload, dict) and isinstance(payload.get("summaries"), dict):
            payload = payload["summaries"]
        normalized: dict[str, dict[str, Any]] = {}
        if isinstance(payload, dict):
            records = payload.values()
        elif isinstance(payload, list):
            records = payload
        else:
            records = []
        for item in records:
            if not isinstance(item, dict):
                continue
            key = _summary_key(item)
            if key:
                normalized[key] = item
        self._summaries = normalized

    def _persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_name(f"{self._path.name}.{uuid.uuid4().hex}.tmp")
        tmp_path.write_text(
            json.dumps(self._summaries, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp_path, self._path)
