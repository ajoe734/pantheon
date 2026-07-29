"""Runtime status projection derived from accepted telemetry events."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


_LIFECYCLE_IDENTITY_EVENT_TYPES = frozenset(
    {
        "signal_generation",
        "trade_decision",
        "risk_evaluation",
        "paper_order_simulated",
        "order_submitted",
        "order_accepted",
        "order_partially_filled",
        "paper_fill_simulated",
        "fill_received",
        "order_filled",
        "order_rejection",
        "order_rejection_simulated",
        "order_canceled",
        "order_cancelled",
        "position_snapshot",
        "position_snapshot_received",
        "broker_position_snapshot",
        "reconciliation_completed",
        "reconciliation_failed",
    }
)
_DEFAULT_RECENT_LIFECYCLE_EVENT_LIMIT = 512
_LIFECYCLE_IDENTITY_FIELDS = (
    "event_id",
    "event_type",
    "created_at",
    "tenant_id",
    "environment",
    "execution_mode",
    "deployment_stage",
    "binding_id",
    "runtime_id",
    "capital_pool_id",
    "artifact_id",
    "artifact_version",
    "plan_id",
    "persona_capital_binding_id",
    "trace_id",
    "journey_id",
    "signal_id",
    "run_id",
    "loop_run_id",
    "aggregate_type",
    "aggregate_id",
    "sequence_no",
    "causal_parent_id",
    "source_mode",
    "target",
    "authority_refs",
    "metadata",
    "correlation_envelope",
)
_SUMMARY_LIFECYCLE_IDENTITY_FIELDS = (
    "tenant_id",
    "environment",
    "execution_mode",
    "trace_id",
    "journey_id",
    "signal_id",
    "run_id",
    "loop_run_id",
    "aggregate_type",
    "aggregate_id",
    "sequence_no",
    "causal_parent_id",
    "source_mode",
    "authority_refs",
    "correlation_envelope",
)


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


def _explicit_metric_as_of(value: Any) -> Optional[str]:
    """Return a timezone-aware RFC3339 timestamp, or None when unusable.

    ``created_at`` remains the backward-compatible metric timestamp fallback.
    Explicit per-metric timestamps are only trusted when they are strings and
    carry timezone information; accepting a naive value would make freshness
    depend on the telemetry host's local timezone.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return text


def _strict_rfc3339(value: Any) -> tuple[Optional[str], Optional[datetime]]:
    """Return an explicitly timezone-aware timestamp and its UTC value.

    Binding rollover is an ownership decision, so unlike the display-oriented
    parser above it must not silently interpret a naive timestamp in the
    telemetry host's timezone.
    """
    if not isinstance(value, str):
        return None, None
    text = value.strip()
    if not text:
        return None, None
    try:
        normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None, None
    if parsed.tzinfo is None:
        return None, None
    return text, parsed.astimezone(timezone.utc)


def _binding_effective_boundary(
    metadata: dict[str, Any],
    *,
    event_at: Optional[datetime],
) -> tuple[Optional[str], Optional[datetime], Optional[str]]:
    """Read an optional RuntimeBinding effective boundary from event metadata.

    Producers have historically emitted only ``created_at``.  Newer adapters
    may attach binding authority metadata without changing the telemetry
    envelope schema, so accept the common flat and nested representations.  A
    boundary after the event that cites it is contradictory and is not used.
    """
    candidates: list[tuple[str, Any]] = [
        ("metadata.runtime_binding_effective_at", metadata.get("runtime_binding_effective_at")),
        ("metadata.binding_effective_at", metadata.get("binding_effective_at")),
    ]
    for container_key in ("runtime_binding", "binding"):
        container = metadata.get(container_key)
        if isinstance(container, dict):
            candidates.append(
                (f"metadata.{container_key}.effective_at", container.get("effective_at"))
            )

    for source, value in candidates:
        text, parsed = _strict_rfc3339(value)
        if parsed is None:
            continue
        if event_at is not None and parsed > event_at:
            continue
        return text, parsed, source
    return None, None, None


def _summary_identity(payload: dict[str, Any]) -> Optional[tuple[str, str]]:
    runtime_id = str(payload.get("runtime_id") or "").strip()
    if not runtime_id:
        return None
    tenant_id = str(payload.get("tenant_id") or "").strip()
    envelope = payload.get("correlation_envelope")
    envelope_tenant_id = (
        str(envelope.get("tenant_id") or "").strip()
        if isinstance(envelope, dict)
        else ""
    )
    if tenant_id and envelope_tenant_id and tenant_id != envelope_tenant_id:
        return None
    return tenant_id or envelope_tenant_id, runtime_id


def _summary_key(payload: dict[str, Any]) -> Optional[str]:
    identity = _summary_identity(payload)
    if identity is None:
        return None
    # Persist a collision-safe composite key while keeping the summary payload
    # and HTTP contract unchanged. Legacy unscoped records remain isolated
    # under an empty tenant and are never returned to a scoped caller.
    return json.dumps(identity, separators=(",", ":"), ensure_ascii=False)


def _json_safe_object(value: Any) -> Optional[dict[str, Any]]:
    """Return a detached JSON object, or None for unsafe/non-object input."""
    if not isinstance(value, dict):
        return None
    try:
        cloned = json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return None
    return cloned if isinstance(cloned, dict) else None


def _append_recent_lifecycle_event_id(
    values: Any,
    event_id: Any,
    *,
    limit: int,
) -> list[str]:
    """Return an ordered, deduplicated, bounded lifecycle receipt list."""
    recent: list[str] = []
    seen: set[str] = set()
    if isinstance(values, list):
        for value in values:
            normalized = str(value or "").strip()
            if normalized and normalized not in seen:
                recent.append(normalized)
                seen.add(normalized)
    normalized_event_id = str(event_id or "").strip()
    if normalized_event_id:
        # A replay through this projection becomes the most recent observation
        # without creating a second receipt, keeping the history tail aligned
        # with last_lifecycle_identity.
        recent = [value for value in recent if value != normalized_event_id]
        recent.append(normalized_event_id)
    return recent[-limit:]


class RuntimeSummaryProjectionStore:
    """Small telemetry-owned read model for BFF runtime status surfaces."""

    def __init__(
        self,
        path: Optional[str | Path] = None,
        *,
        heartbeat_stale_after_seconds: int = 90,
        recent_lifecycle_event_limit: int = _DEFAULT_RECENT_LIFECYCLE_EVENT_LIMIT,
    ) -> None:
        self._path = Path(path) if path else None
        self._heartbeat_stale_after_seconds = max(int(heartbeat_stale_after_seconds), 1)
        self._recent_lifecycle_event_limit = max(int(recent_lifecycle_event_limit), 1)
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
                "recent_lifecycle_event_limit": self._recent_lifecycle_event_limit,
            }

    _VALID_STAGES = frozenset({"paper", "canary", "live", "frozen"})

    def project_event(self, event: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Update the runtime summary from one validated, accepted telemetry event."""
        stage = str(event.get("deployment_stage") or "").lower()
        if stage not in self._VALID_STAGES:
            return None

        summary_identity = _summary_identity(event)
        summary_key = _summary_key(event)
        if summary_identity is None or summary_key is None:
            return None
        incoming_tenant_id, runtime_id = summary_identity

        event_type = str(event.get("event_type") or "").strip()
        event_time = str(event.get("created_at") or utc_now_rfc3339())
        event_boundary_text, event_boundary_at = _strict_rfc3339(event.get("created_at"))
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        metrics = event.get("metrics") if isinstance(event.get("metrics"), dict) else {}
        binding_id = str(event.get("binding_id") or event.get("runtime_binding_id") or "").strip()
        raw_trace_id = event.get("trace_id")
        trace_id = raw_trace_id.strip() if isinstance(raw_trace_id, str) else ""
        correlation_envelope = _json_safe_object(event.get("correlation_envelope"))
        if correlation_envelope is not None:
            raw_envelope_trace_id = correlation_envelope.get("trace_id")
            envelope_trace_id = (
                raw_envelope_trace_id.strip()
                if isinstance(raw_envelope_trace_id, str)
                else ""
            )
            if trace_id and envelope_trace_id and envelope_trace_id != trace_id:
                correlation_envelope = None
            elif not trace_id:
                trace_id = envelope_trace_id
        (
            binding_effective_text,
            binding_effective_at,
            binding_effective_source,
        ) = _binding_effective_boundary(metadata, event_at=event_boundary_at)
        bridge_repo = metadata.get("engine_bridge_repo")
        bridge_commit = metadata.get("engine_bridge_commit")
        bridge_path = metadata.get("engine_bridge_path")
        runtime_adapter_version = metadata.get("runtime_adapter_version")
        # A threshold-breach producer admits a derived event that just echoes
        # an existing metric value/back through ingest to prove it is
        # schema/evidence-valid before citing it as incident evidence. That
        # echo must not refresh the metric's own as-of time: doing so would
        # let a stale metric value keep looking fresh forever, because the
        # very breach it produced would re-stamp the freshness check that
        # flagged it as stale in the first place.
        is_derived_metric_echo = metadata.get("derived_from_threshold_evaluation") is True

        with self._lock:
            current = dict(self._summaries.get(summary_key, {}))
            existing_binding = str(
                current.get("binding_id") or current.get("runtime_binding_id") or ""
            ).strip()
            if existing_binding and not binding_id:
                return self._reject_binding_reclaim(
                    summary_key,
                    current,
                    event,
                    candidate_binding_id=binding_id,
                    reason="missing_binding_id",
                    candidate_boundary_at=binding_effective_text or event_boundary_text,
                )

            if existing_binding and binding_id and existing_binding != binding_id:
                accepted, reason = self._binding_rollover_is_newer(
                    current,
                    event,
                    candidate_binding_id=binding_id,
                    candidate_event_at=event_boundary_at,
                    candidate_effective_at=binding_effective_at,
                )
                if not accepted:
                    return self._reject_binding_reclaim(
                        summary_key,
                        current,
                        event,
                        candidate_binding_id=binding_id,
                        reason=reason,
                        candidate_boundary_at=binding_effective_text or event_boundary_text,
                    )

                retired_binding_ids = {
                    str(value)
                    for value in current.get("_retired_binding_ids", [])
                    if str(value).strip()
                }
                retired_binding_ids.add(existing_binding)
                current["_retired_binding_ids"] = sorted(retired_binding_ids)
                # Reset old metrics, positions, and history to prevent carrying them across rollover
                keys_to_clear = [
                    "pnl", "pnl_at", "pnl_binding_id",
                    "drawdown", "drawdown_at", "drawdown_binding_id",
                    "sharpe_ratio", "sharpe_ratio_at", "sharpe_ratio_binding_id",
                    "fill_rate", "fill_rate_at", "fill_rate_binding_id",
                    "avg_slippage_bps", "avg_slippage_bps_at", "avg_slippage_bps_binding_id",
                    "total_trades", "total_trades_at", "total_trades_binding_id",
                    "executed_trade_count",
                    "last_fill",
                    "_positions_by_symbol", "positions", "position_count",
                    "last_heartbeat_at", "last_heartbeat_event_id",
                    "state", "connectivity_status", "broker_status",
                    "queue_lag_ms", "event_delivery_lag_ms", "reported_health_summary",
                    "trace_id", "correlation_envelope", "last_lifecycle_identity",
                    "recent_lifecycle_event_ids",
                ]
                for key in keys_to_clear:
                    current.pop(key, None)

                self._set_binding_boundary(
                    current,
                    event_at_text=event_boundary_text,
                    event_at=event_boundary_at,
                    effective_at_text=binding_effective_text,
                    effective_at=binding_effective_at,
                    effective_source=binding_effective_source,
                    reset=True,
                )
            elif not existing_binding:
                self._set_binding_boundary(
                    current,
                    event_at_text=event_boundary_text,
                    event_at=event_boundary_at,
                    effective_at_text=binding_effective_text,
                    effective_at=binding_effective_at,
                    effective_source=binding_effective_source,
                    reset=True,
                )
            else:
                self._set_binding_boundary(
                    current,
                    event_at_text=event_boundary_text,
                    event_at=event_boundary_at,
                    effective_at_text=binding_effective_text,
                    effective_at=binding_effective_at,
                    effective_source=binding_effective_source,
                    reset=False,
                )

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
            if incoming_tenant_id:
                current["tenant_id"] = incoming_tenant_id
            incoming_environment = str(event.get("environment") or "").strip()
            if incoming_environment:
                current["environment"] = incoming_environment

            # A lifecycle event owns the reconciliation trace identity. Runtime
            # freshness events (especially heartbeats) may advance last_event
            # and heartbeat fields, but they cannot replace or remove that
            # identity at the consumer-compatible top level.
            is_lifecycle_identity = (
                event_type in _LIFECYCLE_IDENTITY_EVENT_TYPES
                and bool(trace_id)
            )
            if is_lifecycle_identity:
                lifecycle_identity = {
                    field: json.loads(json.dumps(event[field]))
                    for field in _LIFECYCLE_IDENTITY_FIELDS
                    if event.get(field) not in (None, "", [], {})
                }
                lifecycle_identity["trace_id"] = trace_id
                if correlation_envelope is not None:
                    lifecycle_identity["correlation_envelope"] = correlation_envelope
                current["last_lifecycle_identity"] = lifecycle_identity
                current["recent_lifecycle_event_ids"] = (
                    _append_recent_lifecycle_event_id(
                        current.get("recent_lifecycle_event_ids"),
                        event.get("event_id"),
                        limit=self._recent_lifecycle_event_limit,
                    )
                )

            preserved_identity = current.get("last_lifecycle_identity")
            if isinstance(preserved_identity, dict):
                for field in _SUMMARY_LIFECYCLE_IDENTITY_FIELDS:
                    value = preserved_identity.get(field)
                    if value not in (None, "", [], {}):
                        current[field] = json.loads(json.dumps(value))
            else:
                if trace_id:
                    current["trace_id"] = trace_id
                if correlation_envelope is not None:
                    current["correlation_envelope"] = correlation_envelope

            if event_type == "heartbeat":
                current["last_heartbeat_at"] = event_time
                current["last_heartbeat_event_id"] = event.get("event_id")
                current["state"] = "active"
                runtime_heartbeat = (
                    metadata.get("runtime_heartbeat")
                    if isinstance(metadata.get("runtime_heartbeat"), dict)
                    else {}
                )
                connectivity_status = (
                    runtime_heartbeat.get("connectivity_status")
                    or metadata.get("connectivity_status")
                )
                broker_status = runtime_heartbeat.get("broker_status") or metadata.get("broker_status")
                queue_lag_ms = runtime_heartbeat.get("queue_lag_ms", metadata.get("queue_lag_ms"))
                event_delivery_lag_ms = runtime_heartbeat.get(
                    "event_delivery_lag_ms",
                    metadata.get("event_delivery_lag_ms"),
                )
                reported_health_summary = (
                    runtime_heartbeat.get("health_summary")
                    if isinstance(runtime_heartbeat.get("health_summary"), dict)
                    else metadata.get("reported_health_summary")
                )
                if connectivity_status is not None:
                    current["connectivity_status"] = connectivity_status
                    if connectivity_status != "connected":
                        current["state"] = "degraded"
                if broker_status is not None:
                    current["broker_status"] = broker_status
                if queue_lag_ms is not None:
                    current["queue_lag_ms"] = queue_lag_ms
                if event_delivery_lag_ms is not None:
                    current["event_delivery_lag_ms"] = event_delivery_lag_ms
                if isinstance(reported_health_summary, dict):
                    current["reported_health_summary"] = reported_health_summary
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
                if is_derived_metric_echo:
                    break
                for source_key in source_keys:
                    if source_key in metrics:
                        # Per-metric as-of timestamp: a metric only carried
                        # forward by `dict.update` above (e.g. an old drawdown
                        # value untouched by a newer heartbeat-only event)
                        # must not read as fresh just because the summary's
                        # overall `last_event_at` advanced.
                        explicit_as_of = _explicit_metric_as_of(
                            event.get(f"{target_key}_as_of")
                        )
                        metric_as_of = explicit_as_of or event_time
                        previous_as_of = _parse_rfc3339(
                            current.get(f"{target_key}_at")
                        )
                        candidate_as_of = _parse_rfc3339(metric_as_of)
                        if previous_as_of is not None and (
                            candidate_as_of is None or candidate_as_of < previous_as_of
                        ):
                            # Events can arrive out of order. Compare each
                            # metric against its own as-of field so a stale
                            # pnl observation cannot overwrite pnl while an
                            # independently newer drawdown still advances.
                            break
                        current[target_key] = metrics[source_key]
                        current[f"{target_key}_at"] = metric_as_of
                        current[f"{target_key}_binding_id"] = binding_id
                        break

            # Surface executed paper fills so trade activity is visible end-to-end.
            # ``bracket_order_logged`` only records order intent and must never
            # inflate fill counts or positions.  Only the simulated-fill event
            # proves that the paper ledger actually changed.
            if event_type == "paper_fill_simulated":
                symbol = metadata.get("symbol") or metrics.get("symbol")
                fill_qty = metrics.get("fill_quantity")
                fill_price = metrics.get("fill_price")
                trade_count = int(current.get("executed_trade_count") or 0) + 1
                current["executed_trade_count"] = trade_count
                # total_trades may also arrive via metrics; the executed counter is
                # authoritative for paper fills and must not regress below it.
                current["total_trades"] = max(int(current.get("total_trades") or 0), trade_count)
                if symbol is not None:
                    current["last_fill"] = {
                        "symbol": symbol,
                        "quantity": fill_qty,
                        "fill_price": fill_price,
                        "action": metrics.get("action"),
                        "submitted_to_broker": bool(metrics.get("submitted_to_broker")),
                        "at": event_time,
                    }
                    positions = current.get("_positions_by_symbol")
                    if not isinstance(positions, dict):
                        positions = {}
                    if isinstance(fill_qty, (int, float)):
                        positions[symbol] = float(positions.get(symbol, 0.0)) + float(fill_qty)
                    current["_positions_by_symbol"] = positions
                    current["positions"] = [
                        {"symbol": s, "quantity": q}
                        for s, q in sorted(positions.items())
                        if q
                    ]
                    current["position_count"] = len(current["positions"])

            if bridge_repo is not None:
                current["engine_bridge_repo"] = bridge_repo
            if bridge_commit is not None:
                current["engine_bridge_commit"] = bridge_commit
            if bridge_path is not None:
                current["engine_bridge_path"] = bridge_path
            if runtime_adapter_version is not None:
                current["runtime_adapter_version"] = runtime_adapter_version

            # total_trades must never regress below the authoritative executed fill
            # counter, regardless of event ordering or stale metric payloads.
            executed = int(current.get("executed_trade_count") or 0)
            if executed:
                current["total_trades"] = max(int(current.get("total_trades") or 0), executed)

            current["health_summary"] = self._health_summary(current)
            self._summaries[summary_key] = current
            self._persist()
            return json.loads(json.dumps(current))

    def _binding_rollover_is_newer(
        self,
        current: dict[str, Any],
        event: dict[str, Any],
        *,
        candidate_binding_id: str,
        candidate_event_at: Optional[datetime],
        candidate_effective_at: Optional[datetime],
    ) -> tuple[bool, str]:
        """Decide whether a different binding may take ownership of a runtime.

        Binding IDs are immutable generations.  Once one has been replaced it
        is a tombstone for this runtime and can never reclaim the projection.
        For a previously unseen binding, require binding-generation evidence:
        comparable effective times, an effective time beyond a legacy
        projection's first-event boundary, or explicit replacement lineage.
        Event time alone proves when an event was created, not whether its
        binding generation is newer.
        """
        retired_binding_ids = {
            str(value)
            for value in current.get("_retired_binding_ids", [])
            if str(value).strip()
        }
        if candidate_binding_id in retired_binding_ids:
            return False, "retired_binding_reclaim"

        existing_binding = str(
            current.get("binding_id") or current.get("runtime_binding_id") or ""
        ).strip()
        rollback_parent = str(event.get("rollback_parent") or "").strip()
        if rollback_parent and rollback_parent == existing_binding and (
            candidate_event_at is not None or candidate_effective_at is not None
        ):
            return True, "explicit_replacement_lineage"

        _, current_effective_at = _strict_rfc3339(current.get("_binding_effective_at"))
        if current_effective_at is not None and candidate_effective_at is not None:
            if candidate_effective_at > current_effective_at:
                return True, "newer_binding_effective_at"
            return False, "binding_effective_at_not_newer"

        _, current_first_event_at = _strict_rfc3339(
            current.get("_binding_first_event_at")
            or current.get("last_event_at")
            or current.get("collected_at")
        )
        if candidate_effective_at is not None and current_effective_at is None:
            if current_first_event_at is None:
                return False, "binding_boundary_unavailable"
            if candidate_effective_at > current_first_event_at:
                return True, "binding_effective_at_after_legacy_boundary"
            return False, "binding_effective_at_not_after_legacy_boundary"

        if current_effective_at is not None:
            return False, "candidate_binding_effective_at_missing"
        if current_first_event_at is None or candidate_event_at is None:
            return False, "binding_boundary_unavailable"
        return False, "binding_effective_boundary_unavailable"

    @staticmethod
    def _set_binding_boundary(
        current: dict[str, Any],
        *,
        event_at_text: Optional[str],
        event_at: Optional[datetime],
        effective_at_text: Optional[str],
        effective_at: Optional[datetime],
        effective_source: Optional[str],
        reset: bool,
    ) -> None:
        if reset:
            current.pop("_binding_effective_at", None)
            current.pop("_binding_effective_source", None)
            current.pop("_binding_first_event_at", None)
            current.pop("_binding_latest_event_at", None)
            current.pop("_binding_boundary_at", None)
            current.pop("_binding_boundary_source", None)

        first_text, first_at = _strict_rfc3339(current.get("_binding_first_event_at"))
        latest_text, latest_at = _strict_rfc3339(current.get("_binding_latest_event_at"))
        if event_at is not None:
            if first_at is None or event_at < first_at:
                first_text, first_at = event_at_text, event_at
            if latest_at is None or event_at > latest_at:
                latest_text, latest_at = event_at_text, event_at
        if first_text is not None:
            current["_binding_first_event_at"] = first_text
        if latest_text is not None:
            current["_binding_latest_event_at"] = latest_text

        _, stored_effective_at = _strict_rfc3339(current.get("_binding_effective_at"))
        # A late same-binding event may upgrade a created_at-only legacy
        # projection with an effective boundary, but only if that boundary is
        # no later than the first event already attributed to the binding.
        if (
            stored_effective_at is None
            and effective_at is not None
            and (first_at is None or effective_at <= first_at)
        ):
            current["_binding_effective_at"] = effective_at_text
            current["_binding_effective_source"] = effective_source

        boundary_at = current.get("_binding_effective_at") or current.get(
            "_binding_first_event_at"
        )
        if boundary_at is not None:
            current["_binding_boundary_at"] = boundary_at
            current["_binding_boundary_source"] = (
                current.get("_binding_effective_source") or "event.created_at"
            )

    def _reject_binding_reclaim(
        self,
        summary_key: str,
        current: dict[str, Any],
        event: dict[str, Any],
        *,
        candidate_binding_id: str,
        reason: str,
        candidate_boundary_at: Optional[str],
    ) -> dict[str, Any]:
        diagnostics = (
            dict(current.get("projection_diagnostics"))
            if isinstance(current.get("projection_diagnostics"), dict)
            else {}
        )
        diagnostics["binding_rollover_rejection_count"] = int(
            diagnostics.get("binding_rollover_rejection_count") or 0
        ) + 1
        diagnostics["last_binding_rollover_rejection"] = {
            "reason": reason,
            "event_id": event.get("event_id"),
            "candidate_binding_id": candidate_binding_id or None,
            "current_binding_id": current.get("binding_id")
            or current.get("runtime_binding_id"),
            "candidate_boundary_at": candidate_boundary_at,
            "current_boundary_at": current.get("_binding_boundary_at")
            or current.get("_binding_latest_event_at")
            or current.get("last_event_at"),
            "recorded_at": utc_now_rfc3339(),
        }
        current["projection_diagnostics"] = diagnostics
        self._summaries[summary_key] = current
        self._persist()
        return json.loads(json.dumps(current))

    def get(
        self,
        runtime_id: str,
        *,
        tenant_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Optional[dict[str, Any]]:
        with self._lock:
            normalized_runtime_id = str(runtime_id).strip()
            if tenant_id is not None:
                key = json.dumps(
                    (str(tenant_id).strip(), normalized_runtime_id),
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                summary = self._summaries.get(key)
                return self._apply_staleness(summary, now=now) if summary else None

            matches = [
                summary
                for summary in self._summaries.values()
                if str(summary.get("runtime_id") or "").strip() == normalized_runtime_id
            ]
            # Unscoped internal callers are retained for compatibility only
            # when the runtime identity is unambiguous.
            if len(matches) != 1:
                return None
            return self._apply_staleness(matches[0], now=now)

    def list(
        self,
        *,
        tenant_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            return [
                self._apply_staleness(summary, now=now)
                for summary in sorted(
                    self._summaries.values(),
                    key=lambda item: (
                        str(item.get("tenant_id") or ""),
                        str(item.get("runtime_id") or ""),
                    ),
                )
                if tenant_id is None
                or str(summary.get("tenant_id") or "").strip()
                == str(tenant_id).strip()
            ]

    def _health_summary(self, summary: dict[str, Any]) -> dict[str, str]:
        has_bridge_identity = bool(summary.get("engine_bridge_repo") and summary.get("engine_bridge_commit"))
        connectivity_status = summary.get("connectivity_status")
        telemetry_state = "ok" if summary.get("last_heartbeat_at") else "degraded"
        if connectivity_status in {"degraded", "disconnected"}:
            telemetry_state = "degraded"
        broker_status = summary.get("broker_status")
        stage = str(summary.get("deployment_stage") or "paper").lower()
        stage_key = f"{stage}_runtime"
        return {
            stage_key: (
                "ok"
                if summary.get("state") == "active" and connectivity_status != "disconnected"
                else "degraded"
            ),
            "bridge": "ok" if has_bridge_identity else "degraded",
            "telemetry": telemetry_state,
            "broker": broker_status if broker_status in {"ok", "degraded", "unavailable"} else "not_applicable",
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
            if projected.get("connectivity_status") != "disconnected":
                projected["connectivity_status"] = "degraded"
            health = dict(projected.get("health_summary") or {})
            health["telemetry"] = "degraded"
            stage = str(projected.get("deployment_stage") or "paper").lower()
            stage_key = f"{stage}_runtime"
            health[stage_key] = "degraded"
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
                recent_lifecycle_event_ids = _append_recent_lifecycle_event_id(
                    item.get("recent_lifecycle_event_ids"),
                    None,
                    limit=self._recent_lifecycle_event_limit,
                )
                if recent_lifecycle_event_ids:
                    item["recent_lifecycle_event_ids"] = recent_lifecycle_event_ids
                else:
                    item.pop("recent_lifecycle_event_ids", None)
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
