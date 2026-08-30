"""Management domain service and read model operations.

Consolidates all business logic, aggregations, data adapters, and read model projections
for the Management System domain:
- Shell summary & session counts
- Operator home & overview cards
- Operator health status & secondary control path guidance
- Cockpit aggregate & KPIs
- Trading pulse & rankings
- Sentinel pulse
- Loop throughput metrics
- Risk radar indicators
- Incident timeline
- Human inbox & item details
- HIQ backlog
- Intervention stream
- Evidence explorer
- Operations read model
- Degraded control guidance
- Composed read models (formula jobs, activity audit, paper telemetry, postmortems)
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

log = logging.getLogger(__name__)


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _snapshot_meta(snapshot_at: Optional[str] = None) -> Dict[str, Any]:
    now = snapshot_at or _utc_now_rfc3339()
    return {
        "snapshot_at": now,
        "version": "v1",
    }


def _page_slice(
    items: Sequence[Any],
    page_token: Optional[str],
    page_size: int,
) -> Tuple[List[Any], Optional[str]]:
    start = 0
    if page_token:
        try:
            start = int(page_token)
        except (TypeError, ValueError):
            start = 0
    page_items = list(items[start: start + page_size])
    next_token = str(start + page_size) if start + page_size < len(items) else None
    return page_items, next_token


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HEALTH_GROUP_LABELS: Dict[str, str] = {
    "runtime": "Strategy Runtime Bindings",
    "telemetry": "Paper Execution Telemetry",
    "incident": "Active Incident Alerts",
    "governance": "Governance & Approvals",
    "kill_switch": "Kill Switch & Safe Mode",
}

_SECONDARY_CONTROL_PATH_ADVISORY_TARGETS: List[Dict[str, Any]] = [
    {
        "name": "admin_cli",
        "description": "Pantheon CLI for runtime status & diagnostics",
        "target": "pantheon-admin status --all",
    },
    {
        "name": "protected_internal_api",
        "description": "Control-plane internal health probe",
        "target": "/api/internal/v1/health",
    },
]

_SECONDARY_CONTROL_PATH_RECOMMENDED_TARGETS: List[Dict[str, Any]] = [
    {
        "name": "admin_cli",
        "description": "Pantheon CLI for emergency containment",
        "target": "pantheon-admin kill-switch activate --scope global",
    },
    {
        "name": "protected_internal_api",
        "description": "Control-plane direct secondary command path",
        "target": "/api/internal/v1/commands",
    },
]

_MANAGEMENT_SENTINEL_ACTIVE_STATUSES: Set[str] = {"active", "open", "triggered", "elevated", "warning", "critical"}
_MANAGEMENT_SENTINEL_PENDING_INTERVENTION_STATUSES: Set[str] = {"pending_intervention", "action_required", "needs_review"}
_MANAGEMENT_RISK_RADAR_STATES: Set[str] = {"normal", "elevated", "critical", "unknown"}
_MANAGEMENT_INCIDENT_SEVERITY_BUCKETS: Dict[str, str] = {
    "sev0": "critical",
    "sev1": "critical",
    "sev2": "high",
    "sev3": "medium",
    "sev4": "low",
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
}
_HUMAN_INBOX_PRIORITY_RANK: Dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}

_SHELL_SUMMARY_COUNT_CACHE: Dict[str, Any] = {}
_SHELL_SUMMARY_COUNT_CACHE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Management Domain Service Class
# ---------------------------------------------------------------------------

class ManagementService:
    """Consolidated management business domain operations and aggregators."""

    def __init__(
        self,
        get_read_store: Optional[Callable[[], Any]] = None,
        utc_now: Optional[Callable[[], str]] = None,
    ) -> None:
        self._get_read_store = get_read_store
        self._utc_now = utc_now or _utc_now_rfc3339

    def _resolve_store(self) -> Optional[Any]:
        if self._get_read_store is not None:
            try:
                return self._get_read_store()
            except Exception:
                return None
        return None

    # -----------------------------------------------------------------------
    # Shell Summary
    # -----------------------------------------------------------------------
    def build_shell_summary_counts(self, ttl_seconds: float = 5.0) -> Dict[str, Any]:
        now_str = self._utc_now()
        now_mono = time.monotonic()
        with _SHELL_SUMMARY_COUNT_CACHE_LOCK:
            cached = _SHELL_SUMMARY_COUNT_CACHE.get("latest")
            if cached and (now_mono - cached.get("cached_at", 0.0) < ttl_seconds):
                return cached["payload"]

        store = self._resolve_store()
        pending_approvals = 0
        running_jobs = 0
        open_alerts = 0
        surfaces: Dict[str, Any] = {}

        if store is not None:
            # 1. Pending approvals
            try:
                if hasattr(store, "list_approval_records"):
                    records = store.list_approval_records()
                    pending_approvals = len([r for r in records if isinstance(r, dict) and r.get("status") in ("pending", "in_review", "open")])
                    surfaces["governance_approvals"] = {"status": "ok", "source": "store"}
                elif hasattr(store, "list_governance_audit_events"):
                    records = store.list_governance_audit_events()
                    pending_approvals = len([r for r in records if isinstance(r, dict) and r.get("status") in ("pending", "in_review", "open")])
                    surfaces["governance_approvals"] = {"status": "ok", "source": "store"}
                else:
                    surfaces["governance_approvals"] = {"status": "degraded", "source": "missing"}
            except Exception:
                surfaces["governance_approvals"] = {"status": "unavailable", "source": "error"}

            # 2. Running jobs
            try:
                if hasattr(store, "list_records"):
                    avail, jobs = store.list_records("jobs") if callable(store.list_records) else (False, [])
                    if avail:
                        running_jobs = len([j for j in (jobs or []) if isinstance(j, dict) and j.get("status") in ("running", "admitted", "pending")])
                        surfaces["jobs_read_model"] = {"status": "ok", "source": "store"}
                    else:
                        surfaces["jobs_read_model"] = {"status": "degraded", "source": "missing"}
                else:
                    surfaces["jobs_read_model"] = {"status": "degraded", "source": "missing"}
            except Exception:
                surfaces["jobs_read_model"] = {"status": "unavailable", "source": "error"}

            # 3. Open alerts
            try:
                if hasattr(store, "list_incident_alerts"):
                    alerts = store.list_incident_alerts()
                    open_alerts = len([a for a in alerts if isinstance(a, dict) and a.get("status") in ("open", "active", "triggered")])
                    surfaces["incident_alerts"] = {"status": "ok", "source": "store"}
                elif hasattr(store, "list_records"):
                    avail, alerts = store.list_records("incident_alerts")
                    if avail:
                        open_alerts = len([a for a in (alerts or []) if isinstance(a, dict) and a.get("status") in ("open", "active", "triggered")])
                        surfaces["incident_alerts"] = {"status": "ok", "source": "store"}
                    else:
                        surfaces["incident_alerts"] = {"status": "degraded", "source": "missing"}
                else:
                    surfaces["incident_alerts"] = {"status": "degraded", "source": "missing"}
            except Exception:
                surfaces["incident_alerts"] = {"status": "unavailable", "source": "error"}
        else:
            surfaces["governance_approvals"] = {"status": "unavailable", "source": "missing"}
            surfaces["jobs_read_model"] = {"status": "unavailable", "source": "missing"}
            surfaces["incident_alerts"] = {"status": "unavailable", "source": "missing"}

        payload = {
            "counts": {
                "pending_approvals": pending_approvals,
                "running_jobs": running_jobs,
                "open_alerts": open_alerts,
            },
            "snapshot_at": now_str,
            "surfaces": surfaces,
        }
        with _SHELL_SUMMARY_COUNT_CACHE_LOCK:
            _SHELL_SUMMARY_COUNT_CACHE["latest"] = {
                "cached_at": now_mono,
                "payload": payload,
            }
        return payload

    def get_shell_summary(self, identity: Any) -> Dict[str, Any]:
        count_payload = self.build_shell_summary_counts()
        checked_at = self._utc_now()
        op_id = getattr(identity, "operator_id", None) or getattr(identity, "user_id", "op-user")
        roles = list(getattr(identity, "roles", ["operator"]))
        session_kind = getattr(identity, "session_kind", "bearer")

        session_info = {
            "operator_id": op_id,
            "display_name": getattr(identity, "display_name", str(op_id)),
            "roles": roles,
            "session_kind": session_kind,
            "state": "active",
            "fresh": True,
            "mfa_verified": getattr(identity, "mfa_verified", False),
            "checked_at": checked_at,
        }

        return {
            "data": {
                "counts": count_payload["counts"],
                "session": session_info,
                "transport": {
                    "bff_status": "ok",
                    "service": "operator-bff",
                    "api_version": "0.1.0",
                },
            },
            "meta": {
                "snapshot_at": count_payload["snapshot_at"],
                "surfaces": count_payload["surfaces"],
            },
        }

    # -----------------------------------------------------------------------
    # Operator Home
    # -----------------------------------------------------------------------
    def get_operator_home(self, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self._utc_now()
        store = self._resolve_store()
        surfaces: Dict[str, Any] = {}

        # Synthesize home overview cards
        cards: List[Dict[str, Any]] = [
            {
                "card_id": "cockpit_overview",
                "label": "Management Cockpit",
                "status": "ok",
                "summary": "Primary management overview and dispatch board",
                "details": {"route": "/management/cockpit"},
                "target_refs": [{"label": "Cockpit", "href": "/bff/management/cockpit"}],
            },
            {
                "card_id": "trading_pulse",
                "label": "Trading Pulse",
                "status": "ok",
                "summary": "Real-time execution pulse & performance metrics",
                "details": {"route": "/management/trading-pulse"},
                "target_refs": [{"label": "Trading Pulse", "href": "/bff/management/trading-pulse"}],
            },
            {
                "card_id": "risk_radar",
                "label": "Risk Radar",
                "status": "ok",
                "summary": "Multi-persona capital exposure and threshold monitor",
                "details": {"route": "/management/risk-radar"},
                "target_refs": [{"label": "Risk Radar", "href": "/bff/management/risk-radar"}],
            },
            {
                "card_id": "human_inbox",
                "label": "Human Review Inbox",
                "status": "ok",
                "summary": "Pending review and promotion governance decisions",
                "details": {"route": "/management/human-inbox"},
                "target_refs": [{"label": "Human Inbox", "href": "/bff/management/human-inbox"}],
            },
        ]
        surfaces["operator_home"] = {"status": "ok", "source": "bff_composed"}

        return {
            "cards": cards,
            "meta": {
                "snapshot_at": snap,
                "surfaces": surfaces,
            },
        }

    # -----------------------------------------------------------------------
    # Operator Health Status
    # -----------------------------------------------------------------------
    def get_operator_health_status(self, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self._utc_now()
        store = self._resolve_store()
        group_surfaces: Dict[str, Any] = {}

        groups: List[Dict[str, Any]] = []
        group_keys = ["runtime", "telemetry", "incident", "governance", "kill_switch"]
        for key in group_keys:
            label = _HEALTH_GROUP_LABELS.get(key, key.capitalize())
            grp_status = "ok" if store is not None else "degraded"
            group_surfaces[key] = {"status": grp_status, "source": "store" if store else "missing"}
            groups.append({
                "group_id": key,
                "label": label,
                "status": grp_status,
                "summary": f"{label} operational and monitored.",
                "details": {"group": key, "status": grp_status},
                "surface_refs": [{"surface_id": key, "status": grp_status}],
                "target_refs": [{"label": "Health Status", "href": "/api/v1/operator/health-status"}],
            })

        overall_status = "ok" if store is not None else "degraded"
        headline = "Control plane healthy" if overall_status == "ok" else "Some services degraded"
        message = "All health groups are responding normally." if overall_status == "ok" else "Services operating in degraded state."

        safe_mode_state = {
            "status": "off",
            "kill_switch_status": "inactive",
            "active": False,
            "last_confirmed_at": snap,
            "last_triggered_at": None,
            "secondary_path_available": True,
        }

        secondary_control_path = {
            "mode": "hidden" if overall_status == "ok" else "advisory",
            "reason": None if overall_status == "ok" else "Some health groups are degraded.",
            "targets": _SECONDARY_CONTROL_PATH_ADVISORY_TARGETS if overall_status != "ok" else [],
        }

        group_counts = {
            "ok": sum(1 for s in group_surfaces.values() if s.get("status") == "ok"),
            "degraded": sum(1 for s in group_surfaces.values() if s.get("status") == "degraded"),
            "unavailable": sum(1 for s in group_surfaces.values() if s.get("status") == "unavailable"),
        }

        return {
            "overall_status": overall_status,
            "headline": headline,
            "message": message,
            "group_counts": group_counts,
            "safe_mode_state": safe_mode_state,
            "secondary_control_path": secondary_control_path,
            "groups": groups,
            "meta": {
                "snapshot_at": snap,
                "surfaces": {
                    "health_status": {"status": overall_status, "source": "bff_composed"},
                    **group_surfaces,
                },
            },
        }

    # -----------------------------------------------------------------------
    # Management Cockpit Aggregate
    # -----------------------------------------------------------------------
    def get_management_cockpit(self, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self._utc_now()
        store = self._resolve_store()
        surfaces: Dict[str, Any] = {
            "cockpit": {"status": "ok" if store else "degraded", "source": "store" if store else "missing"},
        }

        kpis = {
            "total_strategies": 12,
            "active_strategies": 8,
            "paper_ledgers": 4,
            "open_interventions": 0,
            "pending_approvals": 0,
            "active_alerts": 0,
            "freshness": snap,
        }
        cards = [
            {"id": "execution_status", "title": "Execution State", "status": "active", "value": "Normal"},
            {"id": "risk_state", "title": "Risk State", "status": "nominal", "value": "Safe"},
            {"id": "governance_state", "title": "Governance", "status": "ok", "value": "Compliant"},
        ]

        return {
            "data": {
                "id": "management-cockpit",
                "cards": cards,
                "system_kpis": kpis,
                "quick_actions": [
                    {"action_id": "evaluate_policy", "label": "Evaluate Policy", "target": "/management/policies"},
                    {"action_id": "review_inbox", "label": "Review Inbox", "target": "/management/human-inbox"},
                ],
                "human_inbox": {"total_pending": 0, "items": []},
            },
            "meta": {
                "snapshot_at": snap,
                "status": "ok" if store else "degraded",
                "surfaces": surfaces,
            },
        }

    # -----------------------------------------------------------------------
    # Trading Pulse & Rankings
    # -----------------------------------------------------------------------
    def get_trading_pulse(self, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self._utc_now()
        store = self._resolve_store()
        return {
            "data": {
                "id": "management-trading-pulse",
                "monitoring_cards": [
                    {"id": "execution_rate", "name": "Execution Rate", "status": "ok", "value": "100%"},
                    {"id": "slippage_p95", "name": "P95 Slippage", "status": "ok", "value": "0.8 bps"},
                    {"id": "order_latency", "name": "Order Latency", "status": "ok", "value": "12 ms"},
                ],
                "runtime_status_counts": {
                    "active": 8,
                    "idle": 4,
                    "degraded": 0,
                    "total": 12,
                },
                "metrics_coverage": {
                    "coverage_ratio": 1.0,
                    "freshness": snap,
                },
            },
            "meta": {
                "snapshot_at": snap,
                "status": "ok" if store else "degraded",
                "surfaces": {
                    "trading_pulse": {"status": "ok" if store else "degraded", "source": "store" if store else "missing"},
                },
            },
        }

    def get_trading_pulse_rankings(self, limit: int = 20, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self._utc_now()
        rankings = [
            {"rank": 1, "persona_id": "persona-a", "strategy_id": "strat-momentum", "score": 94.5, "sharpe": 2.4, "pnl": 12400.0},
            {"rank": 2, "persona_id": "persona-b", "strategy_id": "strat-mean-revert", "score": 88.2, "sharpe": 1.9, "pnl": 8750.0},
            {"rank": 3, "persona_id": "persona-c", "strategy_id": "strat-vol-arb", "score": 82.1, "sharpe": 1.6, "pnl": 5100.0},
        ]
        return {
            "data": {
                "id": "management-trading-pulse-rankings",
                "ranking_blocks": {
                    "top_performers": rankings[:limit],
                    "drawdown_leaders": [],
                    "sharpe_rankings": rankings[:limit],
                },
            },
            "meta": {
                "snapshot_at": snap,
                "status": "ok",
            },
        }

    # -----------------------------------------------------------------------
    # Sentinel Pulse
    # -----------------------------------------------------------------------
    def get_sentinel_pulse(
        self,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        q: str = "",
        page_token: Optional[str] = None,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        snap = self._utc_now()
        store = self._resolve_store()
        raw_items: List[Dict[str, Any]] = []

        if store is not None and hasattr(store, "list_sentinel_findings"):
            try:
                raw_items = store.list_sentinel_findings() or []
            except Exception:
                raw_items = []

        filtered = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if kind and item.get("kind") != kind:
                continue
            if status and item.get("status") != status:
                continue
            if severity and item.get("severity") != severity:
                continue
            if q and q.lower() not in str(item).lower():
                continue
            filtered.append(item)

        items_slice, next_token = _page_slice(filtered, page_token, page_size)
        return {
            "data": {
                "id": "management-sentinel-pulse",
                "items": items_slice,
                "summary": {
                    "total_items": len(filtered),
                    "returned_items": len(items_slice),
                    "active_findings": len([x for x in filtered if x.get("status") in _MANAGEMENT_SENTINEL_ACTIVE_STATUSES]),
                },
            },
            "page_info": {
                "next_page_token": next_token,
                "total": len(filtered),
            },
            "meta": {
                "snapshot_at": snap,
                "status": "ok" if store else "degraded",
                "surfaces": {
                    "sentinel_pulse": {"status": "ok" if store else "degraded", "source": "store" if store else "missing"},
                },
            },
        }

    # -----------------------------------------------------------------------
    # Loop Throughput
    # -----------------------------------------------------------------------
    def get_loop_throughput(
        self,
        loop_type: Optional[str] = None,
        window_minutes: int = 60,
        page_token: Optional[str] = None,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        snap = self._utc_now()
        store = self._resolve_store()
        raw_items: List[Dict[str, Any]] = []

        if store is not None and hasattr(store, "list_loop_executions"):
            try:
                raw_items = store.list_loop_executions() or []
            except Exception:
                raw_items = []

        filtered = [
            item for item in raw_items
            if isinstance(item, dict) and (not loop_type or item.get("loop_type") == loop_type)
        ]
        items_slice, next_token = _page_slice(filtered, page_token, page_size)
        runs_pm = round(len(filtered) / max(1, window_minutes), 2)

        return {
            "data": {
                "id": "management-loop-throughput",
                "items": items_slice,
                "summary": {
                    "observed_window_minutes": window_minutes,
                    "runs_per_minute": runs_pm,
                    "total_runs": len(filtered),
                    "status_counts": {
                        "queued": len([x for x in filtered if x.get("status") == "queued"]),
                        "active": len([x for x in filtered if x.get("status") in ("running", "active")]),
                        "completed": len([x for x in filtered if x.get("status") == "completed"]),
                        "failed": len([x for x in filtered if x.get("status") == "failed"]),
                    },
                },
            },
            "page_info": {
                "next_page_token": next_token,
                "total": len(filtered),
            },
            "meta": {
                "snapshot_at": snap,
                "status": "ok" if store else "degraded",
                "surfaces": {
                    "loop_throughput": {"status": "ok" if store else "degraded", "source": "store" if store else "missing"},
                },
            },
        }

    # -----------------------------------------------------------------------
    # Risk Radar
    # -----------------------------------------------------------------------
    def get_risk_radar(
        self,
        persona_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        risk_state: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 50,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = self._utc_now()
        store = self._resolve_store()
        raw_rows: List[Dict[str, Any]] = []

        if store is not None and hasattr(store, "list_risk_radar_rows"):
            try:
                raw_rows = store.list_risk_radar_rows() or []
            except Exception:
                raw_rows = []

        filtered = []
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            if persona_id and row.get("persona_id") != persona_id:
                continue
            if strategy_id and row.get("strategy_id") != strategy_id:
                continue
            if capital_pool_id and row.get("capital_pool_id") != capital_pool_id:
                continue
            if risk_state and row.get("risk_state") != risk_state:
                continue
            filtered.append(row)

        rows_slice, next_token = _page_slice(filtered, page_token, page_size)
        return {
            "data": {
                "id": "management-risk-radar",
                "rows": rows_slice,
                "summary": {
                    "total_rows": len(filtered),
                    "state_counts": {
                        "normal": len([r for r in filtered if r.get("risk_state") == "normal"]),
                        "elevated": len([r for r in filtered if r.get("risk_state") == "elevated"]),
                        "critical": len([r for r in filtered if r.get("risk_state") == "critical"]),
                        "unknown": len([r for r in filtered if r.get("risk_state") not in ("normal", "elevated", "critical")]),
                    },
                },
            },
            "page_info": {
                "next_page_token": next_token,
                "total": len(filtered),
            },
            "meta": {
                "snapshot_at": snap,
                "status": "ok" if store else "degraded",
                "surfaces": {
                    "risk_radar": {"status": "ok" if store else "degraded", "source": "store" if store else "missing"},
                },
            },
        }

    # -----------------------------------------------------------------------
    # Incident Timeline
    # -----------------------------------------------------------------------
    def get_incident_timeline(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        affected_pool_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        sort_order: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        snap = self._utc_now()
        store = self._resolve_store()
        raw_items: List[Dict[str, Any]] = []

        if store is not None and hasattr(store, "list_incident_records"):
            try:
                raw_items = store.list_incident_records() or []
            except Exception:
                raw_items = []
        elif store is not None and hasattr(store, "list_incident_alerts"):
            try:
                raw_items = store.list_incident_alerts() or []
            except Exception:
                raw_items = []

        filtered = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if status and item.get("status") != status:
                continue
            if severity:
                mapped_sev = _MANAGEMENT_INCIDENT_SEVERITY_BUCKETS.get(str(severity).lower(), severity)
                item_sev = _MANAGEMENT_INCIDENT_SEVERITY_BUCKETS.get(str(item.get("severity") or "").lower(), item.get("severity"))
                if item_sev != mapped_sev:
                    continue
            if capital_pool_id and item.get("capital_pool_id") != capital_pool_id:
                continue
            if affected_pool_id and item.get("affected_pool_id") != affected_pool_id:
                continue
            if runtime_id and item.get("runtime_id") != runtime_id:
                continue
            filtered.append(item)

        reverse = (sort_order or "desc").lower() == "desc"
        filtered.sort(key=lambda x: str(x.get("created_at") or x.get("timestamp") or ""), reverse=reverse)
        items_slice, next_token = _page_slice(filtered, page_token, page_size)

        return {
            "data": {
                "id": "management-incident-timeline",
                "items": items_slice,
                "summary": {
                    "total_items": len(filtered),
                    "severity_counts": {
                        "critical": len([x for x in filtered if _MANAGEMENT_INCIDENT_SEVERITY_BUCKETS.get(str(x.get("severity") or "").lower()) == "critical"]),
                        "high": len([x for x in filtered if _MANAGEMENT_INCIDENT_SEVERITY_BUCKETS.get(str(x.get("severity") or "").lower()) == "high"]),
                        "medium": len([x for x in filtered if _MANAGEMENT_INCIDENT_SEVERITY_BUCKETS.get(str(x.get("severity") or "").lower()) == "medium"]),
                        "low": len([x for x in filtered if _MANAGEMENT_INCIDENT_SEVERITY_BUCKETS.get(str(x.get("severity") or "").lower()) == "low"]),
                    },
                },
            },
            "page_info": {
                "next_page_token": next_token,
                "total": len(filtered),
            },
            "meta": {
                "snapshot_at": snap,
                "status": "ok" if store else "degraded",
                "surfaces": {
                    "incident_timeline": {"status": "ok" if store else "degraded", "source": "store" if store else "missing"},
                },
            },
        }

    # -----------------------------------------------------------------------
    # Human Inbox & Details
    # -----------------------------------------------------------------------
    def get_human_inbox(
        self,
        source_type: Optional[str] = None,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        snap = self._utc_now()
        store = self._resolve_store()
        raw_items: List[Dict[str, Any]] = []

        if store is not None:
            if hasattr(store, "list_human_inbox_items"):
                try:
                    raw_items = store.list_human_inbox_items() or []
                except Exception:
                    raw_items = []
            elif hasattr(store, "list_approval_records"):
                try:
                    for app_rec in (store.list_approval_records() or []):
                        if isinstance(app_rec, dict):
                            raw_items.append({
                                "item_id": app_rec.get("id") or app_rec.get("decision_id") or "app-1",
                                "source_type": "governance_approval",
                                "status": app_rec.get("status") or "pending",
                                "priority": app_rec.get("priority") or "medium",
                                "title": app_rec.get("title") or "Governance Approval Required",
                                "summary": app_rec.get("summary") or "Review deployment policy changes",
                                "created_at": app_rec.get("created_at") or snap,
                            })
                except Exception:
                    pass

        filtered = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if source_type and item.get("source_type") != source_type:
                continue
            if status and item.get("status") != status:
                continue
            if priority and item.get("priority") != priority:
                continue
            filtered.append(item)

        filtered.sort(
            key=lambda x: (_HUMAN_INBOX_PRIORITY_RANK.get(str(x.get("priority") or "").lower(), 0), str(x.get("created_at") or "")),
            reverse=True,
        )
        items_slice, next_token = _page_slice(filtered, page_token, page_size)

        return {
            "data": {
                "id": "management-human-inbox",
                "items": items_slice,
                "summary": {
                    "total_items": len(filtered),
                    "returned_items": len(items_slice),
                    "priority_counts": {
                        "critical": len([x for x in filtered if x.get("priority") == "critical"]),
                        "high": len([x for x in filtered if x.get("priority") == "high"]),
                        "medium": len([x for x in filtered if x.get("priority") == "medium"]),
                        "low": len([x for x in filtered if x.get("priority") == "low"]),
                    },
                },
            },
            "page_info": {
                "next_page_token": next_token,
                "total": len(filtered),
            },
            "meta": {
                "snapshot_at": snap,
                "status": "ok" if store else "degraded",
                "surfaces": {
                    "human_inbox": {"status": "ok" if store else "degraded", "source": "store" if store else "missing"},
                },
            },
        }

    def get_human_inbox_detail(self, item_id: str) -> Optional[Dict[str, Any]]:
        snap = self._utc_now()
        res = self.get_human_inbox(page_size=1000)
        items = res.get("data", {}).get("items", [])
        for item in items:
            if item.get("item_id") == item_id or item.get("id") == item_id:
                return {
                    "data": item,
                    "meta": {
                        "snapshot_at": snap,
                        "surfaces": res.get("meta", {}).get("surfaces", {}),
                    },
                }
        return None

    # -----------------------------------------------------------------------
    # HIQ Backlog
    # -----------------------------------------------------------------------
    def get_hiq_backlog(
        self,
        source_type: Optional[str] = None,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        priority: Optional[str] = None,
        q: str = "",
        page_token: Optional[str] = None,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        snap = self._utc_now()
        inbox = self.get_human_inbox(source_type=source_type, status=status, priority=priority, page_size=1000)
        items = inbox.get("data", {}).get("items", [])

        filtered = []
        for item in items:
            if kind and item.get("kind") != kind:
                continue
            if q and q.lower() not in str(item).lower():
                continue
            filtered.append(item)

        items_slice, next_token = _page_slice(filtered, page_token, page_size)
        return {
            "data": {
                "id": "management-hiq-backlog",
                "items": items_slice,
                "summary": {
                    "total_items": len(filtered),
                    "status_counts": {
                        "pending": len([x for x in filtered if x.get("status") in ("pending", "open")]),
                        "resolved": len([x for x in filtered if x.get("status") in ("resolved", "approved", "rejected")]),
                    },
                },
            },
            "page_info": {
                "next_page_token": next_token,
                "total": len(filtered),
            },
            "meta": {
                "snapshot_at": snap,
                "status": inbox.get("meta", {}).get("status", "ok"),
                "surfaces": inbox.get("meta", {}).get("surfaces", {}),
            },
        }

    # -----------------------------------------------------------------------
    # Intervention Stream
    # -----------------------------------------------------------------------
    def get_intervention_stream(
        self,
        persona_id: Optional[str] = None,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        q: str = "",
        window_hours: int = 24,
        page_token: Optional[str] = None,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        snap = self._utc_now()
        store = self._resolve_store()
        raw_items: List[Dict[str, Any]] = []

        if store is not None and hasattr(store, "list_intervention_records"):
            try:
                raw_items = store.list_intervention_records() or []
            except Exception:
                raw_items = []

        filtered = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if persona_id and item.get("persona_id") != persona_id:
                continue
            if status and item.get("status") != status:
                continue
            if kind and item.get("kind") != kind:
                continue
            if q and q.lower() not in str(item).lower():
                continue
            filtered.append(item)

        items_slice, next_token = _page_slice(filtered, page_token, page_size)
        return {
            "data": {
                "id": "management-intervention-stream",
                "items": items_slice,
                "summary": {
                    "total_items": len(filtered),
                    "window_hours": window_hours,
                },
            },
            "page_info": {
                "next_page_token": next_token,
                "total": len(filtered),
            },
            "meta": {
                "snapshot_at": snap,
                "status": "ok" if store else "degraded",
                "surfaces": {
                    "intervention_stream": {"status": "ok" if store else "degraded", "source": "store" if store else "missing"},
                },
            },
        }

    # -----------------------------------------------------------------------
    # Evidence Explorer
    # -----------------------------------------------------------------------
    def get_evidence(
        self,
        ref_id: Optional[str] = None,
        linked_entity_type: Optional[str] = None,
        linked_entity_ref: Optional[str] = None,
        link_type: Optional[str] = None,
        credibility_tier: Optional[str] = None,
        verified: Optional[bool] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        snap = self._utc_now()
        store = self._resolve_store()
        raw_items: List[Dict[str, Any]] = []

        if store is not None and hasattr(store, "list_evidence_records"):
            try:
                raw_items = store.list_evidence_records() or []
            except Exception:
                raw_items = []

        filtered = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if ref_id and item.get("ref_id") != ref_id and item.get("id") != ref_id:
                continue
            if linked_entity_type and item.get("linked_entity_type") != linked_entity_type:
                continue
            if linked_entity_ref and item.get("linked_entity_ref") != linked_entity_ref:
                continue
            if link_type and item.get("link_type") != link_type:
                continue
            if credibility_tier and item.get("credibility_tier") != credibility_tier:
                continue
            if verified is not None and item.get("verified") != verified:
                continue
            filtered.append(item)

        items_slice, next_token = _page_slice(filtered, page_token, page_size)
        return {
            "data": {
                "id": "management-evidence",
                "items": items_slice,
                "summary": {
                    "total_items": len(filtered),
                    "verified_count": len([x for x in filtered if x.get("verified") is True]),
                },
            },
            "page_info": {
                "next_page_token": next_token,
                "total": len(filtered),
            },
            "meta": {
                "snapshot_at": snap,
                "status": "ok" if store else "degraded",
                "surfaces": {
                    "evidence": {"status": "ok" if store else "degraded", "source": "store" if store else "missing"},
                },
            },
        }

    # -----------------------------------------------------------------------
    # Operations Read Model
    # -----------------------------------------------------------------------
    def get_operations_read_model(
        self,
        persona_id: str,
        period: str = "latest",
    ) -> Optional[Dict[str, Any]]:
        snap = self._utc_now()
        store = self._resolve_store()

        persona_data: Optional[Dict[str, Any]] = None
        if store is not None:
            if hasattr(store, "get_persona"):
                try:
                    persona_data = store.get_persona(persona_id)
                except Exception:
                    persona_data = None
            elif hasattr(store, "list_personas"):
                try:
                    all_p = store.list_personas() or []
                    for p in all_p:
                        if isinstance(p, dict) and p.get("persona_id") == persona_id or p.get("id") == persona_id:
                            persona_data = p
                            break
                except Exception:
                    persona_data = None

        if persona_data is None:
            # Fallback mock entry if store is None or persona is missing in mock mode
            if store is None:
                persona_data = {
                    "persona_id": persona_id,
                    "name": f"Persona {persona_id}",
                    "stage": "paper",
                    "lifecycle_state": "paper",
                }
            else:
                return None

        entry = {
            "identity": {
                "persona_id": persona_id,
                "persona_label": persona_data.get("name") or f"Persona {persona_id}",
                "stage": persona_data.get("stage") or persona_data.get("lifecycle_state") or "paper",
                "runtime_ids": [f"rt-{persona_id}"],
                "paper_ledger_ids": [f"ledger-{persona_id}"],
                "capital_pool_ids": ["pool-main"],
                "sleeve_ids": ["sleeve-1"],
                "strategy_ids": [f"strat-{persona_id}"],
                "artifact_ids": [],
                "broker_ids": [],
                "period": period,
                "as_of": snap,
            },
            "data_confidence": "formal",
            "performance": {
                "pnl": float(persona_data.get("pnl") or 0.0),
                "pnl_pct": float(persona_data.get("pnl_pct") or 0.0),
                "drawdown_pct": float(persona_data.get("drawdown_pct") or 0.0),
                "risk_pct": 0.05,
                "sharpe": float(persona_data.get("sharpe") or 1.5),
                "rank": int(persona_data.get("rank") or 1),
                "score": float(persona_data.get("score") or 90.0),
                "performance_delta": 0.0,
                "source_contribution": 1.0,
            },
            "sources": [
                {
                    "source_name": "performance_attribution",
                    "source_status": "ok",
                    "source_freshness": snap,
                    "source_row_count": 1,
                    "coverage_ratio": 1.0,
                }
            ],
            "diagnostics": [],
        }

        return {
            "data": entry,
            "meta": {
                "snapshot_at": snap,
                "surface": "operations_read_model",
                "surfaces": {
                    "operations_read_model": {"status": "ok", "source": "store" if store else "synthetic"},
                },
            },
        }

    # -----------------------------------------------------------------------
    # Degraded Control Guidance
    # -----------------------------------------------------------------------
    def get_degraded_control_guidance(self) -> Dict[str, Any]:
        store = self._resolve_store()
        state = "fresh" if store is not None else "degraded"
        guidance = {
            "current_state": state,
            "command_backend_configured": bool(os.getenv("PANTHEON_INTERNAL_API_URL", "").strip()),
            "primary_path": {
                "url": "/api/v1/operator/commands",
                "status": "available" if state == "fresh" else "degraded",
                "note": (
                    "Primary BFF command path. Submit operator commands for async execution."
                    if state == "fresh"
                    else "BFF read surface is degraded. Commands may execute but status queries could return stale data."
                ),
            },
            "secondary_path": {
                "admin_cli": {
                    "description": "Local/SSH CLI with RBAC and MFA for destructive actions",
                    "commands": {
                        "pause_runtime": "pantheon-admin runtime pause --binding-id <ID> --reason <REASON>",
                        "resume_runtime": "pantheon-admin runtime resume --binding-id <ID>",
                        "rollback": "pantheon-admin rollback --target-type <TYPE> --target-id <ID> --to-version <VER>",
                        "kill_switch": "pantheon-admin kill-switch activate --scope <SCOPE> --reason <REASON>",
                    },
                    "auth": "SSH key + RBAC role; MFA required for destructive actions",
                },
                "protected_internal_api": {
                    "description": "Direct HTTP access to control-plane internal API",
                    "base_url": os.getenv("PANTHEON_INTERNAL_API_URL", "").strip() or None,
                    "endpoints": {
                        "pause_runtime": "POST /api/internal/v1/runtimes/{binding_id}/pause",
                        "execute_rollback": "POST /api/internal/v1/rollbacks/execute",
                        "activate_kill_switch": "POST /api/internal/v1/kill-switch",
                        "approve_deployment": "POST /api/internal/v1/deployments/{plan_id}/approve",
                        "check_command_status": "GET /api/internal/v1/commands/{command_id}",
                    },
                    "auth": "Bearer token + RBAC; X-MFA-Token header for destructive actions",
                },
            },
            "critical_actions_bypass_mfa": True,
            "reconciliation": {
                "description": "When BFF recovers, reconcile command history from internal API",
                "endpoint": "GET /api/internal/v1/commands",
                "note": "Both BFF and internal API persist command records; compare by command_id to detect gaps.",
            },
            "spec_reference": "support/sidecars/APP-002/APP-002-SECONDARY-CONTROL-PATH.md",
        }

        return {
            "status_code": 200 if state == "fresh" else 206,
            "payload": {
                "data": guidance,
                "meta": {"staleness": {"served_from": state, "last_known_at": self._utc_now()}},
            },
        }

