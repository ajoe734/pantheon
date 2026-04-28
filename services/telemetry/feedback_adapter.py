"""
Feedback Store Adapter for Telemetry Events

Links execution telemetry to the feedback store so the evolution plane
can query and evaluate execution performance across paper and live modes.
"""

from __future__ import annotations

from collections import Counter
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Add control-plane to path for imports
_control_plane_path = Path(__file__).parent.parent / "control-plane"
if str(_control_plane_path) not in sys.path:
    sys.path.insert(0, str(_control_plane_path))

from feedback.store import TraderFeedbackStore, parse_rfc3339

log = logging.getLogger(__name__)

# Telemetry event types - must be filtered explicitly to separate from feedback events.
# Includes both trading metrics (FB-003) and lifecycle events (TEL-001).
TELEMETRY_EVENT_TYPES = {
    "pnl_snapshot",
    "drawdown_snapshot",
    "slippage_observation",
    "fill_observation",
    "fill_received",
    "order_submitted",
    "order_accepted",
    "order_partially_filled",
    "order_filled",
    "order_canceled",
    "order_cancelled",
    "order_rejection",
    "position_snapshot",
    "position_snapshot_received",
    "broker_position_snapshot",
    "deploy_started",
    "deploy_completed",
    "rollback_started",
    "rollback_completed",
    "pause_triggered",
    "liquidate_triggered",
    "governance_decision",
    "approval_action",
    "manual_override",
    "kill_switch_action",
    "heartbeat",
    "telemetry_mirror_mismatch",
}

# Feedback event types - must be explicitly excluded in telemetry queries
FEEDBACK_EVENT_TYPES = {
    "approve",
    "edit",
    "reject",
    "rationale",
}

LINEAGE_TARGET_FIELDS = {
    "runtime_binding": "runtime_binding_id",
    "deployment_plan": "deployment_plan_id",
    "capital_pool": "capital_pool_id",
    "persona_capital_binding": "persona_capital_binding_id",
    "strategy": "strategy_id",
    "registry": "registry_id",
    "trace": "trace_id",
    "artifact": "artifact_ref",
}


class FeedbackStoreAdapter:
    """
    Adapts telemetry events to the feedback store interface.
    
    This adapter enables telemetry events to be ingested into the shared feedback store
    so they can be:
    - Queried by the evolution plane evaluators with full append/query semantics
    - Correlated with trader feedback
    - Used as ground truth for strategy evaluation
    - Linked to promotion state changes
    - Recovered by new processes via TraderFeedbackStore.get()/list() methods
    
    Uses TraderFeedbackStore.append() for idempotent, conflict-free event persistence
    and supports querying by registry_id, strategy_id, event_type, created_at via shared store.
    """

    def __init__(self, feedback_store_path: Optional[str] = None):
        """
        Initialize adapter.
        
        Parameters
        ----------
        feedback_store_path : str, optional
            Path to the shared feedback store file (e.g., feedback_store.jsonl).
            If provided, telemetry events are persisted to this store using
            TraderFeedbackStore.append() for idempotent writes and conflict-free updates.
        """
        self.feedback_store = None
        self.feedback_store_path = feedback_store_path
        if feedback_store_path:
            self.feedback_store = TraderFeedbackStore(feedback_store_path)
        
        # In-memory buffer for events (populated from store on recovery if configured)
        self.telemetry_log = []
        
        # Recover existing events from shared store if configured
        if self.feedback_store:
            self._recover_from_store()

    def _recover_from_store(self) -> None:
        """
        Recover existing telemetry events from shared feedback store.
        
        This ensures that new adapter instances can see all previously persisted
        telemetry events from the shared store, enabling proper cross-process queries
        and preventing duplicate event_id issues in query results.
        
        Explicitly filters to telemetry event types only to maintain event family separation.
        Feedback events (approve, edit, reject, rationale) are excluded from recovery.
        """
        if not self.feedback_store:
            return
        
        all_events = self.feedback_store.iter_events()
        # Filter to telemetry events only - exclude feedback events
        self.telemetry_log = [
            e for e in all_events
            if e.get("event_type") in TELEMETRY_EVENT_TYPES
        ]
        log.debug(f"Recovered {len(self.telemetry_log)} telemetry events from shared store")

    def ingest_telemetry_event(
        self,
        event: dict[str, Any],
        strategy_id: str,
        promotion_state: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Convert telemetry event to feedback store format and persist using shared store semantics.
        
        Parameters
        ----------
        event : dict
            Telemetry event from TelemetryCapture (already validated against schema)
        strategy_id : str
            Strategy ID for correlation
        promotion_state : str, optional
            Promotion state (candidate, paper, live, retired) at time of capture
            
        Returns
        -------
        dict
            Telemetry event (either newly appended or existing duplicate if event_id matches).
            No additional fields are added to maintain schema compliance.
        """
        enriched = event.copy()
        
        # Deep copy target to avoid mutating original
        enriched["target"] = enriched.get("target", {}).copy()
        
        # Ensure strategy_id is in target
        enriched["target"]["strategy_id"] = strategy_id
        
        # Add promotion state to target if provided
        if promotion_state:
            enriched["target"]["promotion_state"] = promotion_state
        
        # Persist to shared feedback store if configured
        # append() returns (success, event) tuple; if duplicate event_id, returns (False, existing)
        if self.feedback_store:
            success, stored_event = self.feedback_store.append(enriched)
            if not success:
                log.debug(f"Event ID already exists in store (idempotent): {event.get('event_id')}")
                # Don't add duplicate to telemetry_log; return existing without modifying buffer
                return stored_event
            enriched = stored_event
        else:
            # Only add to telemetry_log if no store configured (process-local mode)
            self.telemetry_log.append(enriched)
        
        # For shared store mode, add to buffer only on successful new append
        if self.feedback_store:
            self.telemetry_log.append(enriched)
        
        log.debug(f"Ingested telemetry event: {event.get('event_id')}")
        
        return enriched

    def _iter_telemetry_events(self):
        """
        Yield telemetry events only.

        This is the family boundary used by both the legacy telemetry queries and
        the LIN-001 derived read-model helpers. The projection layer may derive
        semantic lineage refs from these events, but it must not widen the event
        family beyond canonical telemetry types.
        """
        if self.feedback_store:
            for event in self.feedback_store.iter_events():
                if event.get("event_type") in TELEMETRY_EVENT_TYPES:
                    yield event
            return

        for event in self.telemetry_log:
            if event.get("event_type") in TELEMETRY_EVENT_TYPES:
                yield event

    @staticmethod
    def _runtime_binding_id(event: dict[str, Any]) -> Optional[str]:
        return event.get("runtime_binding_id") or event.get("binding_id")

    @staticmethod
    def _deployment_plan_id(event: dict[str, Any]) -> Optional[str]:
        return event.get("deployment_plan_id") or event.get("plan_id")

    @staticmethod
    def _persona_capital_binding_id(event: dict[str, Any]) -> Optional[str]:
        return event.get("persona_capital_binding_id") or event.get("persona_binding_id")

    @staticmethod
    def _deployment_stage(event: dict[str, Any]) -> Optional[str]:
        return event.get("deployment_stage") or event.get("environment")

    @staticmethod
    def _artifact_version(event: dict[str, Any]) -> Optional[str]:
        return event.get("artifact_version") or event.get("target", {}).get("artifact_version")

    @classmethod
    def _artifact_ref(cls, event: dict[str, Any]) -> Optional[str]:
        artifact_id = event.get("artifact_id")
        artifact_version = cls._artifact_version(event)
        if artifact_id and artifact_version:
            return f"{artifact_id}@{artifact_version}"
        return artifact_id

    @staticmethod
    def _request_id(event: dict[str, Any]) -> Optional[str]:
        if event.get("request_id"):
            return event.get("request_id")
        metadata = event.get("metadata")
        if isinstance(metadata, dict):
            return metadata.get("request_id")
        return None

    @classmethod
    def _lineage_conflict_markers(cls, event: dict[str, Any]) -> list[dict[str, Any]]:
        markers: list[dict[str, Any]] = []

        runtime_binding_alias = event.get("runtime_binding_id")
        if runtime_binding_alias and event.get("binding_id") and runtime_binding_alias != event.get("binding_id"):
            markers.append(
                {
                    "code": "runtime_binding_alias_mismatch",
                    "left": runtime_binding_alias,
                    "right": event.get("binding_id"),
                }
            )

        deployment_plan_alias = event.get("deployment_plan_id")
        if deployment_plan_alias and event.get("plan_id") and deployment_plan_alias != event.get("plan_id"):
            markers.append(
                {
                    "code": "deployment_plan_alias_mismatch",
                    "left": deployment_plan_alias,
                    "right": event.get("plan_id"),
                }
            )

        deployment_stage = event.get("deployment_stage")
        environment = event.get("environment")
        if deployment_stage and environment and deployment_stage != environment:
            markers.append(
                {
                    "code": "deployment_stage_alias_mismatch",
                    "left": deployment_stage,
                    "right": environment,
                }
            )

        target_artifact_version = event.get("target", {}).get("artifact_version")
        top_level_artifact_version = event.get("artifact_version")
        if (
            target_artifact_version
            and top_level_artifact_version
            and target_artifact_version != top_level_artifact_version
        ):
            markers.append(
                {
                    "code": "artifact_version_target_mismatch",
                    "left": top_level_artifact_version,
                    "right": target_artifact_version,
                }
            )

        return markers

    def build_lineage_record(self, telemetry_event: dict[str, Any]) -> dict[str, Any]:
        """
        Build a derived-only lineage read record from a raw telemetry event.

        LIN-001 keeps raw owner fields unchanged (`binding_id`, `plan_id`, etc.)
        but the read model must expose semantic fields (`runtime_binding_id`,
        `deployment_plan_id`) so downstream surfaces do not have to infer object
        meaning from ambiguous raw names.
        """
        target = telemetry_event.get("target", {})
        return {
            "record_type": "telemetry_event",
            "record_id": telemetry_event.get("event_id"),
            "derived_only": True,
            "created_at": telemetry_event.get("created_at"),
            "event_type": telemetry_event.get("event_type"),
            "strategy_id": target.get("strategy_id"),
            "registry_id": target.get("registry_id"),
            "promotion_state": target.get("promotion_state"),
            "runtime_binding_id": self._runtime_binding_id(telemetry_event),
            "deployment_plan_id": self._deployment_plan_id(telemetry_event),
            "capital_pool_id": telemetry_event.get("capital_pool_id"),
            "persona_capital_binding_id": self._persona_capital_binding_id(telemetry_event),
            "runtime_id": telemetry_event.get("runtime_id"),
            "artifact_id": telemetry_event.get("artifact_id"),
            "artifact_version": self._artifact_version(telemetry_event),
            "artifact_ref": self._artifact_ref(telemetry_event),
            "deployment_stage": self._deployment_stage(telemetry_event),
            "trace_id": telemetry_event.get("trace_id"),
            "request_id": self._request_id(telemetry_event),
            "lineage_ref": target.get("lineage_ref"),
            "conflict_markers": self._lineage_conflict_markers(telemetry_event),
        }

    def query_lineage_records(
        self,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Return derived-only lineage read records from telemetry truth.

        This is a LIN-001 reference implementation for the telemetry slice of the
        read model. It does not replace canonical tables; it normalizes telemetry
        fields into semantic refs for downstream consumers.
        """
        if (target_type is None) != (target_id is None):
            raise ValueError("target_type and target_id must be provided together")
        if target_type is not None and target_type not in LINEAGE_TARGET_FIELDS:
            supported = ", ".join(sorted(LINEAGE_TARGET_FIELDS))
            raise ValueError(f"Unsupported target_type {target_type!r}. Supported: {supported}")

        field_name = LINEAGE_TARGET_FIELDS.get(target_type or "", "")
        results: list[dict[str, Any]] = []
        for event in self._iter_telemetry_events():
            record = self.build_lineage_record(event)
            if target_type and record.get(field_name) != target_id:
                continue
            results.append(record)
            if len(results) >= limit:
                break
        return results

    def build_lineage_summary(
        self,
        target_type: str,
        target_id: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        Build a derived-only lineage summary for one semantic target.

        The summary is intentionally read-only. It is meant to match the
        LIN-001 lineage_projection / lineage_summary_json contract shape at the
        telemetry slice, not to become a second telemetry truth source.
        """
        records = self.query_lineage_records(target_type=target_type, target_id=target_id, limit=limit)

        projection_updated_at = None
        parsed_timestamps = []
        for record in records:
            created_at = record.get("created_at")
            if created_at:
                try:
                    parsed_timestamps.append(parse_rfc3339(created_at))
                except (TypeError, ValueError):
                    continue
        if parsed_timestamps:
            projection_updated_at = max(parsed_timestamps).isoformat().replace("+00:00", "Z")

        refs = {
            "strategy_ids": sorted({record["strategy_id"] for record in records if record.get("strategy_id")}),
            "registry_ids": sorted({record["registry_id"] for record in records if record.get("registry_id")}),
            "runtime_binding_ids": sorted(
                {record["runtime_binding_id"] for record in records if record.get("runtime_binding_id")}
            ),
            "deployment_plan_ids": sorted(
                {record["deployment_plan_id"] for record in records if record.get("deployment_plan_id")}
            ),
            "capital_pool_ids": sorted(
                {record["capital_pool_id"] for record in records if record.get("capital_pool_id")}
            ),
            "persona_capital_binding_ids": sorted(
                {
                    record["persona_capital_binding_id"]
                    for record in records
                    if record.get("persona_capital_binding_id")
                }
            ),
            "artifact_refs": sorted({record["artifact_ref"] for record in records if record.get("artifact_ref")}),
            "trace_ids": sorted({record["trace_id"] for record in records if record.get("trace_id")}),
        }

        conflict_markers = []
        for record in records:
            conflict_markers.extend(record.get("conflict_markers", []))

        return {
            "target_type": target_type,
            "target_id": target_id,
            "derived_only": True,
            "source_of_truth": "telemetry_event + normalized lineage edges",
            "projection_updated_at": projection_updated_at,
            "record_count": len(records),
            "event_type_counts": dict(Counter(record["event_type"] for record in records if record.get("event_type"))),
            "refs": refs,
            "conflict_markers": conflict_markers,
            "records": records,
        }

    def get_telemetry_for_strategy(
        self,
        strategy_id: str,
        mode: Optional[str] = None,
        promotion_state: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieve telemetry events for a strategy via shared store semantics.
        
        If a shared feedback store is configured, iterates through the store directly
        with proper telemetry family filtering applied BEFORE any limit. Otherwise, queries local buffer.
        
        Maintains event family separation: only returns telemetry event types
        (pnl_snapshot, drawdown_snapshot, slippage_observation, fill_observation, order_rejection).
        Feedback events (approve, edit, reject, rationale) are explicitly excluded.
        
        Parameters
        ----------
        strategy_id : str
            Strategy ID to query
        mode : str, optional
            Filter by execution mode (paper or live) - legacy support
        promotion_state : str, optional
            Filter by promotion state (candidate, paper, live, retired)
        event_type : str, optional
            Filter by event type
            
        Returns
        -------
        list[dict]
            Telemetry events matching filters
        """
        if self.feedback_store:
            # Iterate through shared store directly with telemetry family boundary applied first
            # This ensures limit is applied after family filtering, not before
            results = []
            for event in self.feedback_store.iter_events():
                # First filter: only telemetry event types
                if event.get("event_type") not in TELEMETRY_EVENT_TYPES:
                    continue
                
                # Second filter: strategy_id
                if event.get("target", {}).get("strategy_id") != strategy_id:
                    continue
                
                # Third filter: promotion_state if provided
                if promotion_state and event.get("target", {}).get("promotion_state") != promotion_state:
                    continue
                
                # Fourth filter: event_type if provided
                if event_type and event.get("event_type") != event_type:
                    continue
                
                # Fifth filter: mode if provided
                if mode and event.get("execution_mode") != mode:
                    continue
                
                results.append(event)
            
            return results
        
        # Fall back to local buffer if no store
        results = [
            event for event in self.telemetry_log
            if event.get("target", {}).get("strategy_id") == strategy_id
        ]
        
        if mode:
            results = [e for e in results if e.get("execution_mode") == mode]
        
        if promotion_state:
            results = [e for e in results if e.get("target", {}).get("promotion_state") == promotion_state]
        
        if event_type:
            results = [e for e in results if e.get("event_type") == event_type]
        
        return results

    def get_telemetry_by_promotion_state(
        self,
        promotion_state: str,
    ) -> list[dict[str, Any]]:
        """
        Retrieve telemetry events by promotion state via shared store semantics.
        
        If a shared feedback store is configured, iterates through the store directly
        with proper telemetry family filtering applied BEFORE any limit. Otherwise, queries local buffer.
        
        Maintains event family separation: only returns telemetry event types.
        Feedback events (approve, edit, reject, rationale) are explicitly excluded.
        
        Parameters
        ----------
        promotion_state : str
            Promotion state to query (candidate, paper, live, retired)
            
        Returns
        -------
        list[dict]
            Telemetry events in given state
        """
        if self.feedback_store:
            # Iterate through shared store directly with telemetry family boundary applied first
            # This ensures limit is applied after family filtering, not before
            results = []
            for event in self.feedback_store.iter_events():
                # First filter: only telemetry event types
                if event.get("event_type") not in TELEMETRY_EVENT_TYPES:
                    continue
                
                # Second filter: promotion_state
                if event.get("target", {}).get("promotion_state") != promotion_state:
                    continue
                
                results.append(event)
            
            return results
        
        # Fall back to local buffer if no store
        return [
            event for event in self.telemetry_log
            if event.get("target", {}).get("promotion_state") == promotion_state
        ]

    def query_telemetry(
        self,
        strategy_id: Optional[str] = None,
        registry_id: Optional[str] = None,
        promotion_state: Optional[str] = None,
        event_type: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Query telemetry events via shared store semantics with full contract support.
        
        This method implements the full query contract with all filters:
        strategy_id, registry_id, promotion_state, event_type, created_at range.
        
        Maintains event family separation: only returns telemetry event types.
        Feedback events (approve, edit, reject, rationale) are explicitly excluded.
        
        The limit is applied AFTER filtering for telemetry event family to ensure
        that feedback events in the shared store do not consume the query limit.
        
        Parameters
        ----------
        strategy_id : str, optional
            Strategy ID filter
        registry_id : str, optional
            Registry ID filter
        promotion_state : str, optional
            Promotion state filter
        event_type : str, optional
            Event type filter
        created_after : str, optional
            RFC3339 timestamp filter (inclusive)
        created_before : str, optional
            RFC3339 timestamp filter (inclusive)
        limit : int
            Maximum number of results to return (applied after telemetry family filter)
            
        Returns
        -------
        list[dict]
            Telemetry events matching all provided filters
        """
        if self.feedback_store:
            # Parse time filters once if provided
            parsed_after = parse_rfc3339(created_after) if created_after else None
            parsed_before = parse_rfc3339(created_before) if created_before else None
            
            # Iterate through shared store directly with telemetry family boundary applied first
            # This ensures limit is applied after family filtering, not before
            results = []
            for event in self.feedback_store.iter_events():
                # First filter: only telemetry event types
                if event.get("event_type") not in TELEMETRY_EVENT_TYPES:
                    continue
                
                # Second filter: strategy_id if provided
                if strategy_id and event.get("target", {}).get("strategy_id") != strategy_id:
                    continue
                
                # Third filter: registry_id if provided
                if registry_id and event.get("target", {}).get("registry_id") != registry_id:
                    continue
                
                # Fourth filter: promotion_state if provided
                if promotion_state and event.get("target", {}).get("promotion_state") != promotion_state:
                    continue
                
                # Fifth filter: event_type if provided
                if event_type and event.get("event_type") != event_type:
                    continue
                
                # Sixth filter: created_at range if provided
                if parsed_after or parsed_before:
                    try:
                        event_time = datetime.fromisoformat(
                            event.get("created_at", "").replace("Z", "+00:00")
                        )
                        if parsed_after and event_time < parsed_after:
                            continue
                        if parsed_before and event_time > parsed_before:
                            continue
                    except (ValueError, TypeError):
                        continue
                
                results.append(event)
                
                # Apply limit only after all family and filter conditions pass
                if len(results) >= limit:
                    break
            
            return results
        
        # Fall back to local buffer filtering
        results = self.telemetry_log
        
        if strategy_id:
            results = [e for e in results if e.get("target", {}).get("strategy_id") == strategy_id]
        if registry_id:
            results = [e for e in results if e.get("target", {}).get("registry_id") == registry_id]
        if promotion_state:
            results = [e for e in results if e.get("target", {}).get("promotion_state") == promotion_state]
        if event_type:
            results = [e for e in results if e.get("event_type") == event_type]
        
        # Time range filtering
        if created_after or created_before:
            parsed_after = parse_rfc3339(created_after) if created_after else None
            parsed_before = parse_rfc3339(created_before) if created_before else None
            
            filtered = []
            for event in results:
                try:
                    event_time = datetime.fromisoformat(
                        event.get("created_at", "").replace("Z", "+00:00")
                    )
                    if parsed_after and event_time < parsed_after:
                        continue
                    if parsed_before and event_time > parsed_before:
                        continue
                    filtered.append(event)
                except (ValueError, TypeError):
                    continue
            results = filtered
        
        return results[:limit]

    def correlate_with_feedback(
        self,
        telemetry_event: dict[str, Any],
        feedback_events: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """
        Correlate telemetry with trader feedback.
        
        Parameters
        ----------
        telemetry_event : dict
            Telemetry event to correlate
        feedback_events : list[dict], optional
            List of feedback events to correlate with
            
        Returns
        -------
        dict
            Correlation result with metrics and insights
        """
        correlation = {
            "telemetry_event_id": telemetry_event.get("event_id"),
            "telemetry_type": telemetry_event.get("event_type"),
            "correlated_feedback": [],
            "strategy_id": telemetry_event.get("target", {}).get("strategy_id"),
            "execution_mode": telemetry_event.get("execution_mode"),
        }
        
        if not feedback_events:
            feedback_events = []
        
        # Find feedback events within time window (24 hours)
        from datetime import timedelta, datetime as dt
        
        try:
            event_time = dt.fromisoformat(
                telemetry_event["created_at"].replace("Z", "+00:00")
            )
        except (ValueError, KeyError):
            return correlation
        
        window_start = event_time - timedelta(hours=24)
        window_end = event_time + timedelta(hours=24)
        
        for feedback in feedback_events:
            try:
                feedback_time = dt.fromisoformat(
                    feedback["created_at"].replace("Z", "+00:00")
                )
                
                if window_start <= feedback_time <= window_end:
                    if (feedback.get("target", {}).get("strategy_id") ==
                        correlation["strategy_id"]):
                        correlation["correlated_feedback"].append({
                            "feedback_id": feedback.get("event_id"),
                            "feedback_type": feedback.get("event_type"),
                            "time_diff_seconds": (
                                feedback_time - event_time
                            ).total_seconds(),
                        })
            except (ValueError, KeyError):
                continue
        
        return correlation

    def export_telemetry(
        self,
        output_path: str,
        format: str = "jsonl",
    ) -> None:
        """
        Export telemetry log to file.
        
        Parameters
        ----------
        output_path : str
            Output file path
        format : str, optional
            Export format (jsonl or json)
        """
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        
        try:
            with open(output_path, "w") as f:
                if format == "jsonl":
                    for event in self.telemetry_log:
                        f.write(json.dumps(event) + "\n")
                else:  # json
                    json.dump(self.telemetry_log, f, indent=2)
            log.info(f"Exported {len(self.telemetry_log)} telemetry events to {output_path}")
        except Exception as e:
            log.error(f"Failed to export telemetry: {e}")

    def clear_log(self) -> None:
        """Clear telemetry log."""
        self.telemetry_log.clear()
