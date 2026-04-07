"""
Feedback Store Adapter for Telemetry Events

Links execution telemetry to the feedback store so the evolution plane
can query and evaluate execution performance across paper and live modes.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional
from datetime import datetime, timezone

# Add control-plane to path for imports
_control_plane_path = Path(__file__).parent.parent / "control-plane"
if str(_control_plane_path) not in sys.path:
    sys.path.insert(0, str(_control_plane_path))

from feedback.store import TraderFeedbackStore, build_query_filters, parse_rfc3339

log = logging.getLogger(__name__)

# Telemetry event types - must be filtered explicitly to separate from feedback events
TELEMETRY_EVENT_TYPES = {
    "pnl_snapshot",
    "drawdown_snapshot",
    "slippage_observation",
    "fill_observation",
    "order_rejection",
}

# Feedback event types - must be explicitly excluded in telemetry queries
FEEDBACK_EVENT_TYPES = {
    "approve",
    "edit",
    "reject",
    "rationale",
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
