"""Trade episode projection store supporting replay, out-of-order sequencing, and as-of queries."""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


def utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _encode_cursor(opened_at: str, episode_id: str) -> str:
    raw = f"{opened_at}|{episode_id}"
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8")


def _decode_cursor(cursor_str: str) -> tuple[str, str]:
    try:
        decoded = base64.b64decode(cursor_str.encode("utf-8")).decode("utf-8")
        parts = decoded.split("|", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
    except Exception:
        pass
    return "", ""


class TradeEpisodeProjectionStore:
    """Store for trade episode projections, backed by append-only events for full replay capability."""

    def __init__(
        self,
        projections_path: Optional[str | Path] = None,
        events_path: Optional[str | Path] = None,
    ) -> None:
        self._projections_path = Path(projections_path) if projections_path else None
        self._events_path = Path(events_path) if events_path else None
        self._lock = threading.RLock()
        self._projections: dict[str, dict[str, Any]] = {}  # trade_episode_id -> projection
        self._events: dict[str, list[dict[str, Any]]] = defaultdict(list)  # trade_episode_id -> list of events
        self._load()

    @property
    def projections_path(self) -> Optional[str]:
        return str(self._projections_path) if self._projections_path else None

    @property
    def events_path(self) -> Optional[str]:
        return str(self._events_path) if self._events_path else None

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "projection_count": len(self._projections),
                "event_count": sum(len(evs) for evs in self._events.values()),
                "projections_path": self.projections_path,
                "events_path": self.events_path,
            }

    def project_event(self, event: dict[str, Any]) -> Optional[dict[str, Any]]:
        """
        Ingest a telemetry or trade journal event and update the corresponding projection.

        Performs idempotent deduplication by event_id.
        """
        trade_episode_id = event.get("trade_episode_id")
        if not trade_episode_id:
            trade_episode_id = event.get("payload", {}).get("trade_episode_id")
        if not trade_episode_id:
            trade_episode_id = event.get("metadata", {}).get("trade_episode_id")

        event_id = event.get("event_id")
        if not trade_episode_id or not event_id:
            return None

        with self._lock:
            # 1. Idempotent check
            existing_events = self._events[trade_episode_id]
            existing_tenants = {
                str(ev.get("tenant_id") or "").strip()
                for ev in existing_events
                if str(ev.get("tenant_id") or "").strip()
            }
            incoming_tenant = str(event.get("tenant_id") or "").strip()
            if existing_tenants and incoming_tenant not in existing_tenants:
                log.warning(
                    "Rejected cross-tenant trade episode projection event %s for %s",
                    event_id,
                    trade_episode_id,
                )
                return None
            if any(ev.get("event_id") == event_id for ev in existing_events):
                log.debug("Event %s already exists for episode %s; skipping ingest", event_id, trade_episode_id)
                return self._projections.get(trade_episode_id)

            # 2. Append event
            self._events[trade_episode_id].append(event)

            # 3. Rebuild projection
            projection = self._rebuild_projection(trade_episode_id)
            if projection:
                self._projections[trade_episode_id] = projection
                self._persist()
                return json.loads(json.dumps(projection))

        return None

    def get(
        self,
        trade_episode_id: str,
        *,
        as_of: Optional[str] = None,
        as_of_sequence: Optional[int] = None,
        tenant_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """
        Get a projection for a trade episode.

        If as_of or as_of_sequence is specified, the projection is rebuilt on-the-fly.
        """
        with self._lock:
            if as_of or as_of_sequence is not None:
                summary = self._rebuild_projection(
                    trade_episode_id,
                    as_of=as_of,
                    as_of_sequence=as_of_sequence,
                )
                if (
                    summary is not None
                    and tenant_id is not None
                    and summary.get("tenant_id") != tenant_id
                ):
                    return None
                return summary
            summary = self._projections.get(trade_episode_id)
            if summary and (
                tenant_id is None or summary.get("tenant_id") == tenant_id
            ):
                return json.loads(json.dumps(summary))
            return None

    def list(
        self,
        *,
        persona_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 20,
        environment: Optional[str] = None,
        strategy_id: Optional[str] = None,
        instrument_id: Optional[str] = None,
        side: Optional[str] = None,
        status: Optional[str] = None,
        outcome: Optional[str] = None,
        reflection_state: Optional[str] = None,
        coverage_state: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        List projections with filters, sorting, and cursor pagination.

        Returns a dict with {"projections": [...], "next_cursor": str or None, "count": int}.
        """
        with self._lock:
            results = list(self._projections.values())

        # Apply filters
        filtered: list[dict[str, Any]] = []
        for proj in results:
            if tenant_id is not None and proj.get("tenant_id") != tenant_id:
                continue
            if persona_id and proj.get("persona_id") != persona_id:
                continue
            if environment and proj.get("environment") != environment:
                continue
            if strategy_id and proj.get("strategy_id") != strategy_id:
                continue
            if instrument_id and proj.get("instrument_id") != instrument_id:
                continue
            if side and proj.get("side") != side:
                continue
            if status and proj.get("status") != status:
                continue
            if coverage_state and proj.get("coverage", {}).get("state") != coverage_state:
                continue
            if reflection_state:
                ref_summary = proj.get("reflection_summary")
                ref_status = ref_summary.get("status") if ref_summary else None
                if ref_status != reflection_state:
                    continue

            # Outcome filter: profitable, loss, flat
            if outcome:
                realized_pnl = proj.get("realized_pnl") or 0.0
                if outcome == "profitable" and realized_pnl <= 0.0:
                    continue
                if outcome == "loss" and realized_pnl >= 0.0:
                    continue
                if outcome == "flat" and realized_pnl != 0.0:
                    continue

            # Temporal filter
            opened_at = proj.get("opened_at")
            if opened_at:
                if start_time and opened_at < start_time:
                    continue
                if end_time and opened_at > end_time:
                    continue

            filtered.append(proj)

        # Sort: opened_at DESC, trade_episode_id DESC
        def sort_key(item: dict[str, Any]) -> tuple[str, str]:
            return (item.get("opened_at") or "", item.get("trade_episode_id") or "")

        sorted_list = sorted(filtered, key=sort_key, reverse=True)

        # Cursor pagination
        sliced_list: list[dict[str, Any]] = []
        start_idx = 0

        if cursor:
            c_opened_at, c_episode_id = _decode_cursor(cursor)
            if c_episode_id:
                for idx, item in enumerate(sorted_list):
                    item_key = (item.get("opened_at") or "", item.get("trade_episode_id") or "")
                    # In reverse/descending sort, older items have smaller keys
                    if item_key < (c_opened_at, c_episode_id):
                        start_idx = idx
                        break
                else:
                    start_idx = len(sorted_list)  # cursor not found or past end

        sliced_list = sorted_list[start_idx : start_idx + limit]

        next_cursor = None
        if start_idx + limit < len(sorted_list) and sliced_list:
            last_item = sliced_list[-1]
            next_cursor = _encode_cursor(
                last_item.get("opened_at") or "",
                last_item.get("trade_episode_id") or "",
            )

        return {
            "projections": json.loads(json.dumps(sliced_list)),
            "next_cursor": next_cursor,
            "count": len(sorted_list),
        }

    def _init_minimal_projection(self, ev: dict[str, Any]) -> dict[str, Any]:
        trade_episode_id = ev.get("trade_episode_id")
        if not trade_episode_id:
            trade_episode_id = ev.get("payload", {}).get("trade_episode_id")
        if not trade_episode_id:
            trade_episode_id = ev.get("metadata", {}).get("trade_episode_id")

        return {
            "trade_episode_id": trade_episode_id,
            "tenant_id": ev.get("tenant_id"),
            "environment": ev.get("environment") or ev.get("deployment_stage") or "paper",
            "persona_id": ev.get("persona_id") or "unknown",
            "strategy_id": ev.get("strategy_id") or "unknown",
            "artifact_id": ev.get("artifact_id") or "unknown",
            "artifact_version": ev.get("artifact_version") or "unknown",
            "runtime_binding_id": ev.get("binding_id") or ev.get("runtime_binding_id") or "00000000-0000-0000-0000-000000000000",
            "capital_pool_id": ev.get("capital_pool_id") or "unknown",
            "instrument_id": ev.get("instrument_id") or "unknown",
            "side": "long",
            "status": "open",
            "decision_id": None,
            "proposal_id": None,
            "evidence_refs": [],
            "trace_ids": [ev.get("trace_id")] if ev.get("trace_id") else [],
            "order_ids": [],
            "fill_ids": [],
            "position_snapshot_refs": [],
            "attribution_ref": None,
            "reflection_id": None,
            "opened_at": ev.get("occurred_at") or ev.get("created_at"),
            "closed_at": None,
            "entry_actor": "persona",
            "exit_actor": None,
            "exit_reason": None,
            "requested_quantity": 0.0,
            "filled_quantity": 0.0,
            "remaining_quantity": 0.0,
            "vwap": None,
            "fees": 0.0,
            "slippage": None,
            "rejects": [],
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "return_percent": None,
            "mae": None,
            "mfe": None,
            "holding_duration_seconds": None,
            "benchmark_delta": None,
            "thesis": None,
            "expected_catalyst": None,
            "invalidation_conditions": [],
            "time_horizon": None,
            "confidence": None,
            "rationale_source_ref": None,
            "limits": None,
            "expected_loss": None,
            "stop_exit_plan": None,
            "approval_refs": [],
            "coverage": {
                "state": "partial",
                "missing_refs": ["opened_event"],
                "as_of": ev.get("occurred_at") or ev.get("created_at"),
                "source_system": ev.get("producer") or "trade-journal-service",
            },
            "reflection_summary": None,
            "memory_governance_refs": [],
            "last_event_sequence": ev.get("sequence_number"),
        }

    def _rebuild_projection(
        self,
        trade_episode_id: str,
        *,
        as_of: Optional[str] = None,
        as_of_sequence: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        events = self._events.get(trade_episode_id, [])
        if not events:
            return None

        # Filter by as_of and as_of_sequence
        filtered_events = []
        as_of_dt = None
        if as_of:
            try:
                as_of_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
                if as_of_dt.tzinfo is None:
                    as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        for ev in events:
            if as_of_dt:
                occ = ev.get("occurred_at") or ev.get("created_at")
                if occ:
                    try:
                        ev_dt = datetime.fromisoformat(occ.replace("Z", "+00:00"))
                        if ev_dt.tzinfo is None:
                            ev_dt = ev_dt.replace(tzinfo=timezone.utc)
                        if ev_dt > as_of_dt:
                            continue
                    except ValueError:
                        pass
            if as_of_sequence is not None:
                seq = ev.get("sequence_number")
                if seq is not None and seq > as_of_sequence:
                    continue
            filtered_events.append(ev)

        # Sort by sequence_number ascending, then occurred_at/created_at ascending, then event_id
        def sort_key(ev: dict[str, Any]) -> tuple[int, str, str]:
            seq = ev.get("sequence_number")
            seq_val = int(seq) if seq is not None else 0
            occ = ev.get("occurred_at") or ev.get("created_at") or ""
            return (seq_val, occ, ev.get("event_id") or "")

        sorted_events = sorted(filtered_events, key=sort_key)

        proj: dict[str, Any] = {}
        for ev in sorted_events:
            event_type = ev.get("event_type")
            payload = ev.get("payload") or {}

            if event_type == "trade_episode.opened":
                proj = {
                    "trade_episode_id": ev.get("trade_episode_id"),
                    "tenant_id": ev.get("tenant_id"),
                    "environment": ev.get("environment") or ev.get("deployment_stage"),
                    "persona_id": ev.get("persona_id"),
                    "strategy_id": payload.get("strategy_id") or ev.get("strategy_id") or ev.get("persona_id") or "unknown",
                    "artifact_id": payload.get("artifact_id") or ev.get("artifact_id") or "unknown",
                    "artifact_version": payload.get("artifact_version") or ev.get("artifact_version") or "unknown",
                    "runtime_binding_id": payload.get("runtime_binding_id") or ev.get("binding_id") or ev.get("runtime_binding_id") or "00000000-0000-0000-0000-000000000000",
                    "capital_pool_id": payload.get("capital_pool_id") or ev.get("capital_pool_id") or "unknown",
                    "instrument_id": payload.get("instrument_id") or ev.get("instrument_id") or "unknown",
                    "side": payload.get("side") or "long",
                    "status": payload.get("status") or "open",
                    "decision_id": payload.get("decision_id"),
                    "proposal_id": payload.get("proposal_id"),
                    "evidence_refs": payload.get("evidence_refs") or [],
                    "trace_ids": payload.get("trace_ids") or ([ev.get("trace_id")] if ev.get("trace_id") else []),
                    "order_ids": payload.get("order_ids") or [],
                    "fill_ids": payload.get("fill_ids") or [],
                    "position_snapshot_refs": payload.get("position_snapshot_refs") or [],
                    "attribution_ref": payload.get("attribution_ref"),
                    "reflection_id": payload.get("reflection_id"),
                    "opened_at": payload.get("opened_at") or ev.get("occurred_at") or ev.get("created_at"),
                    "closed_at": payload.get("closed_at"),
                    "entry_actor": payload.get("entry_actor") or payload.get("opened_by") or "persona",
                    "exit_actor": payload.get("exit_actor"),
                    "exit_reason": payload.get("exit_reason"),
                    "requested_quantity": float(payload.get("requested_quantity") or 0.0),
                    "filled_quantity": float(payload.get("filled_quantity") or 0.0),
                    "remaining_quantity": float(payload.get("remaining_quantity") or 0.0),
                    "vwap": float(payload["vwap"]) if payload.get("vwap") is not None else None,
                    "fees": float(payload.get("fees") or 0.0),
                    "slippage": float(payload["slippage"]) if payload.get("slippage") is not None else None,
                    "rejects": payload.get("rejects") or [],
                    "realized_pnl": float(payload["realized_pnl"]) if payload.get("realized_pnl") is not None else 0.0,
                    "unrealized_pnl": float(payload["unrealized_pnl"]) if payload.get("unrealized_pnl") is not None else 0.0,
                    "return_percent": float(payload["return_percent"]) if payload.get("return_percent") is not None else None,
                    "mae": float(payload["mae"]) if payload.get("mae") is not None else None,
                    "mfe": float(payload["mfe"]) if payload.get("mfe") is not None else None,
                    "holding_duration_seconds": float(payload["holding_duration_seconds"]) if payload.get("holding_duration_seconds") is not None else None,
                    "benchmark_delta": float(payload["benchmark_delta"]) if payload.get("benchmark_delta") is not None else None,
                    "thesis": payload.get("thesis"),
                    "expected_catalyst": payload.get("expected_catalyst"),
                    "invalidation_conditions": payload.get("invalidation_conditions") or [],
                    "time_horizon": payload.get("time_horizon"),
                    "confidence": float(payload["confidence"]) if payload.get("confidence") is not None else None,
                    "rationale_source_ref": payload.get("rationale_source_ref"),
                    "limits": payload.get("limits"),
                    "expected_loss": float(payload["expected_loss"]) if payload.get("expected_loss") is not None else None,
                    "stop_exit_plan": payload.get("stop_exit_plan"),
                    "approval_refs": payload.get("approval_refs") or [],
                    "coverage": {
                        "state": "complete",
                        "missing_refs": [],
                        "as_of": ev.get("occurred_at") or ev.get("created_at"),
                        "source_system": ev.get("producer") or "trade-journal-service",
                    },
                    "reflection_summary": None,
                    "memory_governance_refs": [],
                    "last_event_sequence": ev.get("sequence_number"),
                }
            elif event_type == "trade_episode.updated":
                if not proj:
                    proj = self._init_minimal_projection(ev)

                for field_name in [
                    "status", "exit_actor", "exit_reason", "closed_at", "thesis",
                    "expected_catalyst", "time_horizon", "rationale_source_ref",
                    "limits", "stop_exit_plan", "attribution_ref", "reflection_id",
                ]:
                    if field_name in payload:
                        proj[field_name] = payload[field_name]

                for num_field in [
                    "requested_quantity", "filled_quantity", "remaining_quantity",
                    "vwap", "fees", "slippage", "realized_pnl", "unrealized_pnl",
                    "return_percent", "mae", "mfe", "holding_duration_seconds",
                    "benchmark_delta", "expected_loss", "confidence",
                ]:
                    if num_field in payload:
                        proj[num_field] = float(payload[num_field]) if payload[num_field] is not None else None

                for list_field in ["trace_ids", "order_ids", "fill_ids", "position_snapshot_refs", "evidence_refs", "approval_refs"]:
                    if list_field in payload and isinstance(payload[list_field], list):
                        for item in payload[list_field]:
                            if item not in proj[list_field]:
                                proj[list_field].append(item)

                if "rejects" in payload and isinstance(payload["rejects"], list):
                    proj["rejects"].extend(payload["rejects"])

                proj["coverage"]["as_of"] = ev.get("occurred_at") or ev.get("created_at")
                proj["last_event_sequence"] = ev.get("sequence_number")

            elif event_type == "trade_episode.closed":
                if not proj:
                    proj = self._init_minimal_projection(ev)
                proj["status"] = payload.get("status") or "closed"
                proj["closed_at"] = payload.get("closed_at") or ev.get("occurred_at") or ev.get("created_at")
                if "exit_actor" in payload:
                    proj["exit_actor"] = payload["exit_actor"]
                if "exit_reason" in payload:
                    proj["exit_reason"] = payload["exit_reason"]
                if "realized_pnl" in payload:
                    proj["realized_pnl"] = float(payload["realized_pnl"])
                proj["coverage"]["as_of"] = ev.get("occurred_at") or ev.get("created_at")
                proj["last_event_sequence"] = ev.get("sequence_number")

            elif event_type == "trade_episode.unresolved":
                if not proj:
                    proj = self._init_minimal_projection(ev)
                proj["status"] = "unresolved"
                proj["coverage"]["state"] = "degraded"
                if "missing_refs" in payload:
                    proj["coverage"]["missing_refs"] = payload["missing_refs"]
                if "source_system" in payload:
                    proj["coverage"]["source_system"] = payload["source_system"]
                proj["coverage"]["as_of"] = ev.get("occurred_at") or ev.get("created_at")
                proj["last_event_sequence"] = ev.get("sequence_number")

            elif event_type == "trade_reflection.requested":
                if not proj:
                    proj = self._init_minimal_projection(ev)
                proj["status"] = "reflection_pending"
                if "reflection_summary" in payload:
                    proj["reflection_summary"] = payload["reflection_summary"]
                proj["coverage"]["as_of"] = ev.get("occurred_at") or ev.get("created_at")
                proj["last_event_sequence"] = ev.get("sequence_number")

            elif event_type == "trade_reflection.completed":
                if not proj:
                    proj = self._init_minimal_projection(ev)
                proj["status"] = "reflected"
                ref_id = payload.get("reflection_id") or ev.get("event_id")
                proj["reflection_id"] = ref_id
                proj["reflection_summary"] = {
                    "reflection_id": ref_id,
                    "status": "completed",
                    "generated_at": payload.get("completed_at") or ev.get("occurred_at") or ev.get("created_at"),
                }
                proj["coverage"]["as_of"] = ev.get("occurred_at") or ev.get("created_at")
                proj["last_event_sequence"] = ev.get("sequence_number")

            elif event_type == "trade_reflection.failed":
                if not proj:
                    proj = self._init_minimal_projection(ev)
                proj["status"] = "reflection_failed"
                ref_id = payload.get("reflection_id") or ev.get("event_id")
                proj["reflection_id"] = ref_id
                proj["reflection_summary"] = {
                    "reflection_id": ref_id,
                    "status": "failed",
                    "generated_at": payload.get("failed_at") or ev.get("occurred_at") or ev.get("created_at"),
                }
                proj["coverage"]["as_of"] = ev.get("occurred_at") or ev.get("created_at")
                proj["last_event_sequence"] = ev.get("sequence_number")

            elif event_type in ("trade_lesson.proposed", "trade_lesson.reviewed", "trade_lesson.merged", "trade_lesson.quarantined"):
                if not proj:
                    proj = self._init_minimal_projection(ev)
                ref = payload.get("lesson_candidate_id") or payload.get("lesson_id") or ev.get("event_id")
                if ref and ref not in proj["memory_governance_refs"]:
                    proj["memory_governance_refs"].append(ref)
                proj["coverage"]["as_of"] = ev.get("occurred_at") or ev.get("created_at")
                proj["last_event_sequence"] = ev.get("sequence_number")

            # regular telemetry events support:
            elif event_type in ("order_submitted", "paper_order_simulated"):
                if not proj:
                    proj = self._init_minimal_projection(ev)
                order_id = ev.get("event_id")
                if order_id and order_id not in proj["order_ids"]:
                    proj["order_ids"].append(order_id)
                metrics = ev.get("metrics") or {}
                req_qty = float(metrics.get("quantity") or metrics.get("requested_quantity") or 0.0)
                if req_qty > 0:
                    proj["requested_quantity"] = req_qty
                if proj["status"] in ("proposed", "approved"):
                    proj["status"] = "submitted"
                proj["coverage"]["as_of"] = ev.get("occurred_at") or ev.get("created_at")

            elif event_type in ("order_filled", "paper_fill_simulated", "fill_observation", "fill_received"):
                if not proj:
                    proj = self._init_minimal_projection(ev)

                fill_id = ev.get("event_id")
                if fill_id and fill_id not in proj["fill_ids"]:
                    proj["fill_ids"].append(fill_id)

                metrics = ev.get("metrics") or {}

                fill_qty = float(metrics.get("fill_quantity") or metrics.get("quantity") or 0.0)
                fill_price = float(metrics.get("fill_price") or metrics.get("price") or 0.0)

                if fill_qty > 0:
                    prev_qty = proj["filled_quantity"]
                    prev_vwap = proj["vwap"] or 0.0

                    new_qty = prev_qty + fill_qty
                    proj["filled_quantity"] = new_qty

                    if new_qty > 0:
                        proj["vwap"] = (prev_vwap * prev_qty + fill_price * fill_qty) / new_qty

                    req_qty = proj["requested_quantity"]
                    if req_qty > 0 and new_qty >= req_qty:
                        proj["status"] = "open"
                    else:
                        proj["status"] = "partially_filled"

                    slippage = float(metrics.get("slippage") or metrics.get("slippage_bps") or 0.0)
                    if slippage > 0:
                        if proj["slippage"] is None:
                            proj["slippage"] = slippage
                        else:
                            proj["slippage"] = (proj["slippage"] * prev_qty + slippage * fill_qty) / new_qty

                fees = float(metrics.get("fees") or metrics.get("commission") or 0.0)
                proj["fees"] += fees

                proj["coverage"]["as_of"] = ev.get("occurred_at") or ev.get("created_at")

            elif event_type in ("order_canceled", "order_cancelled", "order_rejection"):
                if not proj:
                    proj = self._init_minimal_projection(ev)
                if proj["status"] in ("proposed", "approved", "submitted", "partially_filled"):
                    proj["status"] = "cancelled" if "cancel" in event_type else "rejected"
                proj["coverage"]["as_of"] = ev.get("occurred_at") or ev.get("created_at")

            elif event_type in ("position_snapshot", "position_snapshot_received", "broker_position_snapshot"):
                if not proj:
                    proj = self._init_minimal_projection(ev)
                snap_id = ev.get("event_id")
                if snap_id and snap_id not in proj["position_snapshot_refs"]:
                    proj["position_snapshot_refs"].append(snap_id)

                metrics = ev.get("metrics") or {}
                pos_qty = float(metrics.get("position_quantity") or metrics.get("quantity") or 0.0)
                if pos_qty == 0.0 and proj["status"] in ("open", "reducing", "partially_filled"):
                    proj["status"] = "closed"
                    proj["closed_at"] = ev.get("occurred_at") or ev.get("created_at")
                elif pos_qty > 0.0 and proj["status"] == "partially_filled":
                    proj["status"] = "open"

                proj["coverage"]["as_of"] = ev.get("occurred_at") or ev.get("created_at")

            elif event_type in ("pnl_snapshot", "drawdown_snapshot"):
                if not proj:
                    proj = self._init_minimal_projection(ev)
                metrics = ev.get("metrics") or {}
                if "realized_pnl" in metrics:
                    proj["realized_pnl"] = float(metrics["realized_pnl"])
                if "unrealized_pnl" in metrics:
                    proj["unrealized_pnl"] = float(metrics["unrealized_pnl"])
                if "mae" in metrics:
                    proj["mae"] = float(metrics["mae"])
                if "mfe" in metrics:
                    proj["mfe"] = float(metrics["mfe"])
                proj["coverage"]["as_of"] = ev.get("occurred_at") or ev.get("created_at")

        return proj or None

    def _load(self) -> None:
        # Load events first
        if self._events_path and self._events_path.exists():
            try:
                data = json.loads(self._events_path.read_text(encoding="utf-8").strip() or "{}")
                if isinstance(data, dict):
                    self._events = defaultdict(list, {k: list(v) for k, v in data.items()})
            except Exception as e:
                log.warning("Failed to load trade episode events: %s", e)

        # Load projections
        if self._projections_path and self._projections_path.exists():
            try:
                data = json.loads(self._projections_path.read_text(encoding="utf-8").strip() or "{}")
                if isinstance(data, dict):
                    self._projections = dict(data)
            except Exception as e:
                log.warning("Failed to load trade episode projections: %s", e)

    def _persist(self) -> None:
        # Persist events
        if self._events_path:
            try:
                self._events_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = self._events_path.with_name(f"{self._events_path.name}.{uuid.uuid4().hex}.tmp")
                tmp_path.write_text(
                    json.dumps(self._events, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
                os.replace(tmp_path, self._events_path)
            except Exception as e:
                log.warning("Failed to persist trade episode events: %s", e)

        # Persist projections
        if self._projections_path:
            try:
                self._projections_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = self._projections_path.with_name(f"{self._projections_path.name}.{uuid.uuid4().hex}.tmp")
                tmp_path.write_text(
                    json.dumps(self._projections, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
                os.replace(tmp_path, self._projections_path)
            except Exception as e:
                log.warning("Failed to persist trade episode projections: %s", e)
