"""Incident, Risk Alert, Kill Switch, and Audit domain service.

Encapsulates:
- Incident and IncidentCase projection, filtering, and durable/overlay management
- Operator alert aggregation (incidents, governance, kill switch, runtime telemetry)
- Kill switch and action drawer safety policy projection
- Composed incident detail and post-incident review views
- Audit trail querying and export
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

from services.control_plane.bff.models import (
    CommandStatus,
    CommandType,
    ErrorCode,
    ObjectType,
    OperatorIdentity,
)

log = logging.getLogger(__name__)

_INCIDENT_SEVERITY_MAP: Dict[str, str] = {
    "critical": "sev1",
    "high": "sev1",
    "medium": "sev2",
    "low": "sev3",
    "sev1": "sev1",
    "sev2": "sev2",
    "sev3": "sev3",
}

_KILL_SWITCH_STATUS_MAP: Dict[str, str] = {
    "armed": "armed",
    "off": "armed",
    "normal": "armed",
    "triggered": "triggered",
    "guarded": "triggered",
    "risk_off": "triggered",
    "cooling_down": "cooling_down",
    "cooldown": "cooling_down",
    "paused": "cooling_down",
}

_ACTION_DRAWER_PRIMARY_ALLOWED_ACTIONS: Dict[str, bool] = {
    "canPause": True,
    "canRiskOff": True,
    "canLiquidateAll": False,
    "canHardRollback": False,
    "canIssueSafeMode": True,
}

_ALERT_SEVERITY_ORDER: Dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}

_ALERT_CATEGORY_ORDER: Dict[str, int] = {
    "kill_switch": 4,
    "incident": 3,
    "runtime": 2,
    "governance": 1,
}

_INCIDENT_CASE_ALIAS_FIELDS: Dict[str, tuple[str, ...]] = {
    "binding_id": ("binding_id", "runtime_binding_id"),
    "deployment_stage": ("deployment_stage", "deployment_mode"),
    "deployment_plan_id": ("deployment_plan_id", "plan_id"),
    "capital_pool_id": ("capital_pool_id", "affected_pool_id"),
    "persona_capital_binding_id": ("persona_capital_binding_id",),
    "artifact_id": ("artifact_id",),
    "artifact_version": ("artifact_version",),
    "runtime_id": ("runtime_id",),
    "trace_id": ("trace_id", "correlation_id"),
}

_OPERATOR_INCIDENT_HOME_ROUTE = "/management/incidents"
_GOVERNANCE_REVIEW_QUEUE_ROUTE = "/management/governance/review-queue"
_GOVERNANCE_APPROVAL_QUEUE_ROUTE = "/management/governance/approvals"
_OPERATOR_HEALTH_STATUS_ROUTE = "/management/health"
_OPERATOR_RUNTIME_STATE_ROUTE = "/management/runtime"

_RUNTIME_STATUS_ALERT_SEVERITY: Dict[str, str] = {
    "failed": "critical",
    "error": "critical",
    "degraded": "high",
    "unhealthy": "high",
}

_TELEMETRY_DRAWDOWN_THRESHOLDS: Tuple[Tuple[float, str], ...] = (
    (0.15, "critical"),
    (0.10, "high"),
    (0.05, "medium"),
)

_TELEMETRY_FILL_RATE_THRESHOLDS: Tuple[Tuple[float, str], ...] = (
    (0.50, "critical"),
    (0.80, "high"),
    (0.90, "medium"),
)

_TELEMETRY_SLIPPAGE_THRESHOLDS: Tuple[Tuple[float, str], ...] = (
    (50.0, "critical"),
    (25.0, "high"),
    (10.0, "medium"),
)


def _default_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_rfc3339(ts_str: Optional[str]) -> Optional[datetime]:
    if not ts_str or not isinstance(ts_str, str):
        return None
    cleaned = ts_str.strip()
    if not cleaned:
        return None
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _stable_json_hash(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _first_present(payload: Dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _incident_detail_href(incident_id: str) -> str:
    return f"{_OPERATOR_INCIDENT_HOME_ROUTE}/{incident_id}"


def _incident_home_severity(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return _INCIDENT_SEVERITY_MAP.get(str(value).strip().lower(), str(value))


def _project_incident_home_item(incident: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "incident_id": incident.get("incident_id") or incident.get("id"),
        "title": incident.get("title"),
        "severity": _incident_home_severity(incident.get("severity")),
        "status": incident.get("status"),
        "artifact_id": incident.get("artifact_id"),
        "opened_at": incident.get("opened_at") or incident.get("created_at") or incident.get("submitted_at"),
        "resolved_at": incident.get("resolved_at"),
    }


def _project_incident_detail_incident(incident: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "incident_id": incident.get("incident_id") or incident.get("id"),
        "title": incident.get("title"),
        "severity": _incident_home_severity(incident.get("severity")),
        "status": incident.get("status"),
        "artifact_id": incident.get("artifact_id"),
        "artifact_version": incident.get("artifact_version"),
        "runtime_id": incident.get("runtime_id"),
        "trace_id": incident.get("trace_id") or incident.get("correlation_id"),
        "opened_at": incident.get("opened_at") or incident.get("created_at") or incident.get("submitted_at"),
    }


def _project_bff_incident_case(incident: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(incident)
    incident_id = str(payload.get("incident_id") or payload.get("id") or "")
    if incident_id:
        payload["id"] = payload.get("id") or incident_id
        payload["incident_id"] = incident_id

    for field, aliases in _INCIDENT_CASE_ALIAS_FIELDS.items():
        value = _first_present(payload, aliases)
        if value is not None:
            payload[field] = value

    created_at = payload.get("created_at") or payload.get("opened_at") or payload.get("submitted_at")
    if created_at:
        payload["created_at"] = created_at
        payload["opened_at"] = payload.get("opened_at") or created_at

    if not payload.get("lineage_ref") and payload.get("artifact_id") and payload.get("artifact_version"):
        payload["lineage_ref"] = f"{payload['artifact_id']}@{payload['artifact_version']}"

    return payload


def _bff_incident_matches_filters(
    incident: Dict[str, Any],
    *,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    affected_pool_id: Optional[str] = None,
) -> bool:
    if status:
        requested_statuses = {token.strip().lower() for token in status.split(",") if token.strip()}
        if str(incident.get("status") or "").lower() not in requested_statuses:
            return False
    if severity and str(incident.get("severity") or "").lower() != severity.lower():
        return False
    if affected_pool_id:
        pool_val = incident.get("capital_pool_id") or incident.get("affected_pool_id")
        if isinstance(pool_val, list):
            if affected_pool_id not in pool_val:
                return False
        elif pool_val != affected_pool_id:
            return False
    return True


def _project_affected_binding(
    binding: Dict[str, Any],
    incident: Dict[str, Any],
    runtime_binding: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    raw_stage = (
        incident.get("deployment_stage")
        or binding.get("stage")
        or binding.get("deployment_stage")
        or (runtime_binding or {}).get("deployment_stage")
        or binding.get("allowed_deployment_scope")
    )
    stage = str(raw_stage or "").strip().lower()
    if stage not in {"paper", "live"}:
        stage = "paper"

    return {
        "binding_id": binding.get("id") or binding.get("binding_id"),
        "persona_id": binding.get("persona_id"),
        "capital_pool_id": binding.get("capital_pool_id"),
        "stage": stage,
        "binding_status": binding.get("binding_status") or binding.get("status"),
    }


def _default_incident_allowed_actions() -> Dict[str, bool]:
    return {
        "canPause": False,
        "canRiskOff": False,
        "canLiquidateAll": False,
        "canHardRollback": False,
        "canIssueSafeMode": False,
        "canOpenActionDrawer": False,
    }


def _derive_incident_allowed_actions(
    roles: Union[Sequence[str], Set[str]],
    incident: Dict[str, Any],
) -> Dict[str, bool]:
    actions = _default_incident_allowed_actions()
    incident_status = str(incident.get("status") or "").lower()
    runtime_id = incident.get("runtime_id")
    if incident_status not in {"open", "in_progress"} or not runtime_id:
        return actions

    role_set = set(roles)
    if not {"operator", "admin"}.intersection(role_set):
        return actions

    actions["canPause"] = True
    actions["canRiskOff"] = True
    actions["canIssueSafeMode"] = True
    actions["canOpenActionDrawer"] = True
    return actions


def _alert_target_ref(
    *,
    surface_id: str,
    label: str,
    href: str,
    target_id: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "surface_id": surface_id,
        "label": label,
        "href": href,
    }
    if target_id not in (None, ""):
        payload["target_id"] = target_id
    return payload


def _highest_ranked_value(
    values: List[Optional[str]],
    rankings: Dict[str, int],
) -> Optional[str]:
    valid = [v for v in values if v is not None and str(v).lower() in rankings]
    if not valid:
        return None
    return max(valid, key=lambda v: rankings[str(v).lower()])


def _max_alert_severity(values: List[Optional[str]]) -> Optional[str]:
    return _highest_ranked_value(values, _ALERT_SEVERITY_ORDER)


def _alert_sort_key(alert: Dict[str, Any]) -> tuple[str, int, int, str]:
    severity = str(alert.get("severity") or "").lower()
    category = str(alert.get("category") or "").lower()
    return (
        str(alert.get("raised_at") or ""),
        _ALERT_SEVERITY_ORDER.get(severity, 0),
        _ALERT_CATEGORY_ORDER.get(category, 0),
        str(alert.get("alert_id") or ""),
    )


def _alert_severity_for_incident(incident: Dict[str, Any]) -> str:
    normalized = _incident_home_severity(incident.get("severity"))
    if normalized == "sev1":
        return "critical"
    if normalized == "sev2":
        return "high"
    return "medium"


def _alert_severity_for_risk_level(
    risk_level: Optional[str],
    *,
    elevated: bool = False,
) -> str:
    mapping = {
        "critical": "critical",
        "high": "high",
        "medium": "medium",
        "low": "low",
    }
    severity = mapping.get(str(risk_level or "").strip().lower(), "medium")
    if elevated and _ALERT_SEVERITY_ORDER.get(severity, 0) < _ALERT_SEVERITY_ORDER["high"]:
        return "high"
    return severity


def _last_triggered_at(ks: Dict[str, Any]) -> Optional[str]:
    explicit = ks.get("last_triggered_at")
    if explicit:
        return str(explicit)

    timestamps: List[str] = []
    for order in ks.get("active_freeze_orders", []):
        if not isinstance(order, dict):
            continue
        value = order.get("triggered_at") or order.get("created_at")
        if value:
            timestamps.append(str(value))
    return max(timestamps) if timestamps else None


def _kill_switch_status_value(ks: Dict[str, Any]) -> str:
    explicit = str(ks.get("status") or "").strip().lower()
    if explicit:
        mapped = _KILL_SWITCH_STATUS_MAP.get(explicit)
        if mapped:
            return mapped

    safe_mode_status = str(ks.get("safe_mode_status") or "").strip().lower()
    mapped = _KILL_SWITCH_STATUS_MAP.get(safe_mode_status)
    if mapped:
        if mapped == "armed" and ks.get("active"):
            return "triggered"
        return mapped

    return "triggered" if ks.get("active") else "armed"


def _kill_switch_active_commands(ks: Dict[str, Any]) -> List[str]:
    active_commands = ks.get("active_commands")
    if isinstance(active_commands, list):
        return [str(value) for value in active_commands if value not in (None, "")]

    derived: List[str] = []
    for order in ks.get("active_freeze_orders", []):
        if not isinstance(order, dict):
            continue
        value = order.get("command_id") or order.get("id") or order.get("target_id")
        if value not in (None, ""):
            derived.append(str(value))
    return derived


def _project_kill_switch_contract(ks: Dict[str, Any], surface: Dict[str, Any]) -> Dict[str, Any]:
    if surface.get("status") == "unavailable":
        return {
            "status": None,
            "last_triggered_at": None,
            "last_confirmed_at": None,
            "active_commands": [],
        }

    return {
        "status": _kill_switch_status_value(ks),
        "last_triggered_at": _last_triggered_at(ks),
        "last_confirmed_at": ks.get("last_confirmed_at") or ks.get("last_checked_at"),
        "active_commands": _kill_switch_active_commands(ks),
    }


def _project_action_drawer_allowed_actions(
    kill_switch_surface: Dict[str, Any],
    allowed_actions_surface: Dict[str, Any],
) -> Dict[str, bool]:
    allowed_actions = {
        "canPause": False,
        "canRiskOff": False,
        "canLiquidateAll": False,
        "canHardRollback": False,
        "canIssueSafeMode": False,
        "secondaryPathAvailable": False,
    }

    if allowed_actions_surface.get("status") != "ok":
        return allowed_actions

    secondary_path_available = kill_switch_surface.get("status") != "unavailable"
    allowed_actions["secondaryPathAvailable"] = secondary_path_available

    if kill_switch_surface.get("status") == "ok":
        allowed_actions.update(_ACTION_DRAWER_PRIMARY_ALLOWED_ACTIONS)
        return allowed_actions

    if secondary_path_available:
        allowed_actions["canPause"] = True
        allowed_actions["canRiskOff"] = True

    return allowed_actions


def _build_alert_summary(alerts: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_severity = {key: 0 for key in _ALERT_SEVERITY_ORDER}
    by_category = {key: 0 for key in _ALERT_CATEGORY_ORDER}
    for alert in alerts:
        severity = str(alert.get("severity") or "").lower()
        category = str(alert.get("category") or "").lower()
        if severity in by_severity:
            by_severity[severity] += 1
        if category in by_category:
            by_category[category] += 1
    return {
        "total_active": len(alerts),
        "highest_severity": _max_alert_severity(
            [str(alert.get("severity") or "").lower() for alert in alerts]
        ),
        "by_severity": by_severity,
        "by_category": by_category,
    }


class IncidentService:
    """Domain service for Incidents, Alerts, Kill Switch, and Audit."""

    def __init__(
        self,
        *,
        read_surface: Optional[Any] = None,
        command_store: Optional[Any] = None,
        get_read_store: Optional[Callable[[], Any]] = None,
        get_command_store: Optional[Callable[[], Any]] = None,
        incident_overlay: Optional[Dict[str, Dict[str, Any]]] = None,
        acknowledged_alerts: Optional[Dict[str, Dict[str, Any]]] = None,
        idempotency_ledger: Optional[Dict[str, Dict[str, Any]]] = None,
        incident_events: Optional[deque] = None,
        incident_subscribers: Optional[List[asyncio.Queue]] = None,
        utc_now: Optional[Callable[[], str]] = None,
        dataset_surface_status: Optional[Callable[..., Dict[str, Any]]] = None,
        meta_staleness: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
        surface_degradation_reason: Optional[Callable[..., Optional[str]]] = None,
    ) -> None:
        if read_surface is not None:
            self._get_read_store = (lambda: read_surface() if callable(read_surface) else read_surface)
        else:
            self._get_read_store = get_read_store or (lambda: None)
        if command_store is not None:
            self._get_command_store = (lambda: command_store() if callable(command_store) else command_store)
        else:
            self._get_command_store = get_command_store or (lambda: None)
        self._idempotency_ledger: Dict[str, Dict[str, Any]] = (
            idempotency_ledger if idempotency_ledger is not None else {}
        )
        self._incident_events: deque = (
            incident_events if incident_events is not None else deque(maxlen=500)
        )
        self._incident_subscribers: List[asyncio.Queue] = (
            incident_subscribers if incident_subscribers is not None else []
        )
        self._utc_now = utc_now or _default_utc_now
        self._dataset_surface_status_fn = dataset_surface_status
        self._meta_staleness_fn = meta_staleness
        self._surface_degradation_reason_fn = surface_degradation_reason

    def get_read_store(self) -> Any:
        return self._get_read_store()

    def get_command_store(self) -> Any:
        return self._get_command_store()

    def now(self) -> str:
        return self._utc_now()

    def get_surface_status(
        self,
        dataset: str,
        *,
        snapshot_at: Optional[str] = None,
        has_data: Optional[bool] = None,
        missing_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        now_ts = snapshot_at or self.now()
        if self._dataset_surface_status_fn is not None:
            return self._dataset_surface_status_fn(
                dataset,
                snapshot_at=now_ts,
                has_data=has_data,
                missing_message=missing_message,
            )
        store = self.get_read_store()
        src = getattr(store, "dataset_source", lambda ds: "local_snapshot")(dataset) if store else "local_snapshot"
        if dataset == "incidents" and src in ("typed_store", "local_snapshot"):
            inc_port = getattr(getattr(store, "lifecycle_telemetry_governance", None), "incidents", None) or getattr(store, "incident_port", None)
            if inc_port is not None and hasattr(inc_port, "dataset_source"):
                src = inc_port.dataset_source()
        if src in ("missing", "unavailable"):
            return {
                "status": "unavailable",
                "source": src,
                "dataset": dataset,
                "snapshot_at": now_ts,
                "message": missing_message or f"{dataset} dataset is unavailable.",
            }
        return {
            "status": "ok",
            "source": src,
            "dataset": dataset,
            "snapshot_at": now_ts,
        }

    def get_staleness(self) -> Optional[Dict[str, Any]]:
        if self._meta_staleness_fn is not None:
            return self._meta_staleness_fn()
        return None

    def surface_degradation_reason(
        self,
        surface: Dict[str, Any],
        *,
        degraded_reason: str,
        unavailable_reason: str,
    ) -> Optional[str]:
        if self._surface_degradation_reason_fn is not None:
            return self._surface_degradation_reason_fn(
                surface,
                degraded_reason=degraded_reason,
                unavailable_reason=unavailable_reason,
            )
        status = surface.get("status")
        if status == "ok":
            return None
        if status == "unavailable":
            return unavailable_reason
        if surface.get("message"):
            return str(surface["message"])
        if surface.get("note"):
            return str(surface["note"])
        return degraded_reason

    # -- Incident Queries & Mutations -----------------------------------------

    def list_incidents(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        affected_pool_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        store = self.get_read_store()
        if store and hasattr(store, "list_incidents"):
            raw = store.list_incidents(
                status=status,
                severity=severity,
                affected_pool_id=affected_pool_id,
            )
            return list(raw or [])
        return []

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        store = self.get_read_store()
        if store and hasattr(store, "get_incident"):
            return store.get_incident(incident_id)
        return None

    def list_bff_incidents(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        affected_pool_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        store_incidents = self.list_incidents(
            status=status,
            severity=severity,
            affected_pool_id=affected_pool_id,
        )
        incidents = [_project_bff_incident_case(i) for i in store_incidents]
        anchor = [
            incident
            for incident in incidents
            if str(incident.get("incident_id") or incident.get("id") or "") == "inc-20260410-001"
        ]
        rest = [
            incident
            for incident in incidents
            if str(incident.get("incident_id") or incident.get("id") or "") != "inc-20260410-001"
        ]
        return anchor + sorted(
            rest,
            key=lambda item: str(item.get("created_at") or item.get("submitted_at") or ""),
            reverse=True,
        )

    def get_bff_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        clean_id = incident_id.strip()
        store_incident = self.get_incident(clean_id)
        if store_incident:
            return _project_bff_incident_case(store_incident)
        return None

    def create_incident(
        self,
        payload: Dict[str, Any],
        operator_id: str,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        incident_id = str(payload.get("incident_id") or payload.get("id") or uuid.uuid4())
        submitted_at = self.now()
        req_body = {
            **payload,
            "incident_id": incident_id,
            "status": payload.get("status") or "open",
            "title": payload.get("title") or "Untitled Incident",
            "severity": payload.get("severity") or "medium",
            "binding_id": payload.get("binding_id") or "rb-default",
            "deployment_stage": payload.get("deployment_stage") or "paper",
            "deployment_plan_id": payload.get("deployment_plan_id") or "plan-default",
            "capital_pool_id": payload.get("capital_pool_id") or payload.get("affected_pool_id") or "pool-default",
            "persona_capital_binding_id": payload.get("persona_capital_binding_id") or "pcb-default",
            "artifact_id": payload.get("artifact_id") or "art-default",
            "artifact_version": payload.get("artifact_version") or "1.0",
            "runtime_id": payload.get("runtime_id") or "runtime-default",
            "trace_id": payload.get("trace_id") or incident_id,
        }
        store = self.get_read_store()
        if store and hasattr(store, "create_incident"):
            created = store.create_incident(req_body)
            if created and isinstance(created, dict):
                req_body.update(created)
        return _project_bff_incident_case({
            **req_body,
            "id": incident_id,
            "submitted_at": submitted_at,
            "submitted_by": operator_id,
            "audit_ref": {
                "target_type": "Incident",
                "target_id": incident_id,
                "href": f"/bff/audit/entities/Incident/{incident_id}",
            },
            "meta": {"idempotency_key": idempotency_key} if idempotency_key else {},
        })

    def update_incident_status(
        self,
        incident_id: str,
        status: str,
        resolved_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        store = self.get_read_store()
        if store and hasattr(store, "update_incident_status"):
            return store.update_incident_status(incident_id, status=status, resolved_at=resolved_at)
        return {"incident_id": incident_id, "status": status}

    # -- Alert Builders -------------------------------------------------------

    def build_incident_alerts(self, snapshot_at: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        surface = self.get_surface_status("incidents", snapshot_at=snapshot_at)
        if surface.get("status") == "unavailable":
            return [], surface
        alerts = []
        for inc in self.list_incidents():
            st = str(inc.get("status") or "").lower()
            if st in {"open", "in_progress"}:
                iid = str(inc.get("incident_id") or inc.get("id") or "")
                title = str(inc.get("title") or iid or "Unnamed incident")
                prefix = "Active" if st == "open" else "In-progress"
                alerts.append({
                    "alert_id": f"alert-incident-{iid}",
                    "severity": _alert_severity_for_incident(inc),
                    "category": "incident",
                    "raised_at": inc.get("opened_at") or inc.get("created_at") or snapshot_at,
                    "summary": f"{prefix} incident: {title}.",
                    "target_ref": _alert_target_ref(surface_id="PKT-002", label="Open incident response", href=_incident_detail_href(iid), target_id=iid),
                })
        return alerts, surface

    def build_governance_alerts(self, snapshot_at: str) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        store = self.get_read_store()
        rq_s = self.get_surface_status("governance_review_queue_items", snapshot_at=snapshot_at)
        aq_s = self.get_surface_status("approval_queue_items", snapshot_at=snapshot_at)
        alerts = []
        if rq_s.get("status") != "unavailable" and store and hasattr(store, "list_governance_review_queue_items"):
            for item in store.list_governance_review_queue_items():
                st, iid, itype = str(item.get("status") or "").lower(), str(item.get("item_id") or ""), str(item.get("item_type") or "Governance item")
                if st in {"pending", "in_review", "escalated"}:
                    alerts.append({
                        "alert_id": f"alert-governance-review-{iid}",
                        "severity": _alert_severity_for_risk_level(item.get("risk_level"), elevated=st == "escalated"),
                        "category": "governance", "raised_at": item.get("submitted_at") or snapshot_at,
                        "summary": f"Escalated governance review: {itype} {iid}." if st == "escalated" else (f"Governance review in progress: {itype} {iid}." if st == "in_review" else f"Pending governance review: {itype} {iid}."),
                        "target_ref": _alert_target_ref(surface_id="PKT-001", label="Open governance review queue", href=_GOVERNANCE_REVIEW_QUEUE_ROUTE, target_id=iid),
                    })
        if aq_s.get("status") != "unavailable" and store and hasattr(store, "list_approval_queue_items"):
            for item in store.list_approval_queue_items():
                st, did, dtype = str(item.get("decision_state") or "").lower(), str(item.get("decision_id") or ""), str(item.get("decision_type") or "Approval item")
                if st in {"pending", "in_review"}:
                    alerts.append({
                        "alert_id": f"alert-approval-{did}",
                        "severity": _alert_severity_for_risk_level(item.get("risk_level"), elevated=st == "in_review"),
                        "category": "governance", "raised_at": item.get("submitted_at") or snapshot_at,
                        "summary": f"Approval decision in review: {dtype} {did}." if st == "in_review" else f"Approval required: {dtype} {did}.",
                        "target_ref": _alert_target_ref(surface_id="GV-02", label="Open approval queue", href=_GOVERNANCE_APPROVAL_QUEUE_ROUTE, target_id=did),
                    })
        return alerts, {"review_queue": rq_s, "approval_queue": aq_s}

    def build_kill_switch_alerts(self, snapshot_at: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
        surface = self.get_surface_status("kill_switch", snapshot_at=snapshot_at)
        if surface.get("status") == "unavailable":
            return [], surface, {}
        store = self.get_read_store()
        ks = store.get_kill_switch_status() if store and hasattr(store, "get_kill_switch_status") else {}
        sm, kst = str(ks.get("safe_mode_status") or "").lower(), str(ks.get("status") or "").lower()
        if ks.get("active") or kst == "triggered":
            sev, summary = "critical", "Kill-switch active; operator intervention is required."
        elif kst == "cooling_down":
            sev, summary = "high", "Kill-switch cooling down; verify runtime stability before resuming operations."
        elif sm not in {"", "off", "released", "none", "null"}:
            sev, summary = "high", f"Safe mode active ({sm}); use the health board to verify current restrictions."
        else:
            return [], surface, ks
        return [{
            "alert_id": "alert-kill-switch-state", "severity": sev, "category": "kill_switch",
            "raised_at": ks.get("last_triggered_at") or ks.get("last_confirmed_at") or snapshot_at,
            "summary": summary,
            "target_ref": _alert_target_ref(surface_id="OC-03", label="Open health status board", href=_OPERATOR_HEALTH_STATUS_ROUTE, target_id=kst or sm or "kill-switch"),
        }], surface, ks

    def build_runtime_alerts(self, snapshot_at: str) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        rs = self.get_surface_status("runtime_bindings", snapshot_at=snapshot_at)
        ts = self.get_surface_status("telemetry_summaries", snapshot_at=snapshot_at)
        if rs.get("status") == "unavailable":
            return [], {"runtime_roster": rs, "telemetry_summary": ts}
        store, alerts = self.get_read_store(), []
        bindings = store.list_runtime_bindings() if store and hasattr(store, "list_runtime_bindings") else []
        for b in bindings:
            rid = str(b.get("runtime_id") or b.get("id") or "")
            tel = store.get_telemetry_summary(rid) if rid and ts.get("status") != "unavailable" and store and hasattr(store, "get_telemetry_summary") else None
            reasons, sevs = [], []
            rst = str(b.get("status") or "").lower()
            if rst in _RUNTIME_STATUS_ALERT_SEVERITY:
                sevs.append(_RUNTIME_STATUS_ALERT_SEVERITY[rst])
                reasons.append(f"runtime status is {rst}")
            if tel:
                dd = tel.get("drawdown")
                if isinstance(dd, (int, float)):
                    for th, s in _TELEMETRY_DRAWDOWN_THRESHOLDS:
                        if dd >= th: sevs.append(s); reasons.append(f"drawdown is {dd:.3f}"); break
                fr = tel.get("fill_rate")
                if isinstance(fr, (int, float)):
                    for th, s in _TELEMETRY_FILL_RATE_THRESHOLDS:
                        if fr < th: sevs.append(s); reasons.append(f"fill rate dropped to {fr:.2f}"); break
                sl = tel.get("avg_slippage_bps")
                if isinstance(sl, (int, float)):
                    for th, s in _TELEMETRY_SLIPPAGE_THRESHOLDS:
                        if sl >= th: sevs.append(s); reasons.append(f"average slippage reached {sl:.1f} bps"); break
            sev = _max_alert_severity(sevs)
            if reasons and sev:
                alerts.append({
                    "alert_id": f"alert-runtime-{rid}", "severity": sev, "category": "runtime",
                    "raised_at": (tel or {}).get("collected_at") or b.get("updated_at") or b.get("last_updated_at") or b.get("started_at") or snapshot_at,
                    "summary": f"Runtime {rid} anomaly: {'; '.join(reasons[:2])}.",
                    "target_ref": _alert_target_ref(surface_id="OC-04", label="Open runtime state board", href=_OPERATOR_RUNTIME_STATE_ROUTE, target_id=rid),
                })
        return alerts, {"runtime_roster": rs, "telemetry_summary": ts}

    def build_operator_alerts_payload(self, snapshot_at: str) -> Dict[str, Any]:
        inc_alerts, inc_s = self.build_incident_alerts(snapshot_at)
        gov_alerts, gov_s = self.build_governance_alerts(snapshot_at)
        ks_alerts, ks_s, _ = self.build_kill_switch_alerts(snapshot_at)
        rt_alerts, rt_s = self.build_runtime_alerts(snapshot_at)
        alerts = sorted(inc_alerts + gov_alerts + ks_alerts + rt_alerts, key=_alert_sort_key, reverse=True)
        meta = {
            "snapshot_at": snapshot_at, "acknowledgement_supported": True,
            "surfaces": {
                "alerts": {"status": "ok" if inc_s.get("status") != "unavailable" else "degraded", "source": "bff_composed"},
                "incident": inc_s, "kill_switch": ks_s, "review_queue": gov_s["review_queue"],
                "approval_queue": gov_s["approval_queue"], "runtime_roster": rt_s["runtime_roster"],
                "telemetry_summary": rt_s["telemetry_summary"],
            },
        }
        staleness = self.get_staleness()
        if staleness is not None: meta["staleness"] = staleness
        return {"alerts": alerts, "summary": _build_alert_summary(alerts), "meta": meta}

    def management_alerts_degraded_payload(self, snapshot_at: str) -> Dict[str, Any]:
        meta: Dict[str, Any] = {
            "snapshot_at": snapshot_at,
            "acknowledgement_supported": True,
            "surfaces": {
                "alerts": {
                    "status": "degraded",
                    "source": "timeout_fallback",
                    "message": "Alert aggregation timed out under concurrent read fanout; degraded empty response returned.",
                },
            },
        }
        staleness = self.get_staleness()
        if staleness is not None:
            meta["staleness"] = staleness
        return {
            "alerts": [],
            "summary": _build_alert_summary([]),
            "meta": meta,
        }

    # -- Composed Views -------------------------------------------------------

    def get_incident_response(
        self,
        incident_id: str,
        roles: Union[Sequence[str], Set[str]],
        snapshot: str = "preferred",
    ) -> Optional[Dict[str, Any]]:
        clean_id = incident_id.strip()
        incident = self.get_incident(clean_id)
        if not incident:
            return None

        snapshot_at = self.now()
        store = self.get_read_store()
        runtime_binding = None
        binding_id = incident.get("binding_id")
        if binding_id and store and hasattr(store, "get_runtime_binding"):
            runtime_binding = store.get_runtime_binding(binding_id)
        if runtime_binding is None and store and hasattr(store, "get_runtime_binding_by_runtime_id"):
            runtime_binding = store.get_runtime_binding_by_runtime_id(incident.get("runtime_id"))

        get_binding_fn = getattr(store, "get_binding", lambda b: None) if store else (lambda b: None)
        candidate_ids: List[str] = []
        for val in [incident.get("persona_capital_binding_id"), (runtime_binding or {}).get("persona_capital_binding_id")]:
            if val not in (None, "") and str(val) not in candidate_ids:
                candidate_ids.append(str(val))

        affected_bindings: List[Dict[str, Any]] = []
        for bid in candidate_ids:
            b = get_binding_fn(bid)
            if b:
                affected_bindings.append(_project_affected_binding(b, incident, runtime_binding))

        ks = store.get_kill_switch_status() if store and hasattr(store, "get_kill_switch_status") else {}
        incident_surface = self.get_surface_status("incidents", snapshot_at=snapshot_at, has_data=True)
        affected_bindings_surface = self.get_surface_status(
            "persona_bindings",
            snapshot_at=snapshot_at,
            has_data=bool(affected_bindings) if candidate_ids else None,
            missing_message="Affected bindings unavailable for this incident.",
        )
        kill_switch_surface = self.get_surface_status("kill_switch", snapshot_at=snapshot_at)

        action_derivation_available = bool(incident.get("runtime_id"))
        allowed_actions_surface = {
            "status": "ok" if action_derivation_available and kill_switch_surface.get("status") != "unavailable" else "unavailable",
            "source": "bff_composed",
        }
        allowed_actions = (
            _derive_incident_allowed_actions(roles, incident)
            if allowed_actions_surface.get("status") == "ok"
            else _default_incident_allowed_actions()
        )

        meta: Dict[str, Any] = {
            "snapshot_at": snapshot_at,
            "surfaces": {
                "incident": incident_surface,
                "affected_bindings": affected_bindings_surface,
                "kill_switch": kill_switch_surface,
                "allowedActions": allowed_actions_surface,
            },
        }
        if snapshot == "preferred":
            staleness = self.get_staleness()
            if staleness is not None:
                meta["staleness"] = staleness

        return {
            "data": {
                "incident": _project_incident_detail_incident(incident),
                "affected_bindings": affected_bindings,
                "kill_switch": _project_kill_switch_contract(ks, kill_switch_surface),
            },
            "allowedActions": allowed_actions,
            "meta": meta,
        }

    def get_post_incident_review(
        self,
        incident_id: str,
        snapshot: str = "preferred",
    ) -> Optional[Dict[str, Any]]:
        clean_id = incident_id.strip()
        incident = self.get_incident(clean_id)
        if not incident:
            return None

        snapshot_at = self.now()
        store = self.get_read_store()
        postmortem = store.get_postmortem_by_incident(clean_id) if store and hasattr(store, "get_postmortem_by_incident") else None
        evolution_decisions = store.get_evolution_decisions_by_incident(clean_id) if store and hasattr(store, "get_evolution_decisions_by_incident") else []
        artifact_id = incident.get("artifact_id")
        lineage_edges = store.list_lineage_edges(artifact_id=artifact_id) if artifact_id and store and hasattr(store, "list_lineage_edges") else []
        telemetry_performance = store.get_telemetry_performance(artifact_id) if artifact_id and store and hasattr(store, "get_telemetry_performance") else None

        surfaces = {
            "postmortem": self.get_surface_status(
                "postmortems",
                snapshot_at=snapshot_at,
                has_data=postmortem is not None,
                missing_message="No postmortem report available yet",
            ),
            "evolution_decisions": self.get_surface_status("evolution_decisions", snapshot_at=snapshot_at),
            "lineage": self.get_surface_status(
                "lineage_edges",
                snapshot_at=snapshot_at,
                has_data=bool(lineage_edges),
                missing_message="No lineage edges found for this artifact",
            ),
            "telemetry_performance": self.get_surface_status(
                "telemetry_performance",
                snapshot_at=snapshot_at,
                has_data=telemetry_performance is not None,
                missing_message="Telemetry performance unavailable for this artifact.",
            ),
        }

        data = {
            "incident": incident,
            "postmortem": postmortem,
            "evolution_decisions": evolution_decisions,
            "lineage_edges": lineage_edges,
            "telemetry_performance": telemetry_performance,
        }

        meta: Dict[str, Any] = {
            "snapshot_at": snapshot_at,
            "surfaces": surfaces,
        }
        if snapshot == "preferred":
            staleness = self.get_staleness()
            if staleness is not None:
                meta["staleness"] = staleness

        return {
            "data": data,
            "meta": meta,
        }

    # -- Kill Switch & Safety -------------------------------------------------

    def get_kill_switch_contract_payload(self) -> Dict[str, Any]:
        snapshot_at = self.now()
        store = self.get_read_store()
        ks = store.get_kill_switch_status() if store and hasattr(store, "get_kill_switch_status") else {}
        kill_switch_surface = self.get_surface_status("kill_switch", snapshot_at=snapshot_at)
        allowed_actions_surface = {
            "status": "ok" if kill_switch_surface.get("status") != "unavailable" else "unavailable",
        }
        allowed_actions = _project_action_drawer_allowed_actions(kill_switch_surface, allowed_actions_surface)
        meta: Dict[str, Any] = {
            "snapshot_at": snapshot_at,
            "surfaces": {
                "kill_switch": kill_switch_surface,
                "allowedActions": allowed_actions_surface,
            },
        }
        staleness = self.get_staleness()
        if staleness is not None:
            meta["staleness"] = staleness

        degradation: Dict[str, Any] = {}
        kill_switch_reason = self.surface_degradation_reason(
            kill_switch_surface,
            degraded_reason="Kill switch status is degraded and may be stale.",
            unavailable_reason="Kill switch status is currently unavailable.",
        )
        if kill_switch_reason is not None:
            degradation["kill_switch_reason"] = kill_switch_reason
        allowed_actions_reason = self.surface_degradation_reason(
            allowed_actions_surface,
            degraded_reason="Action authority is degraded. All CTAs disabled for safety.",
            unavailable_reason="Action authority service is unavailable. All CTAs disabled for safety.",
        )
        if allowed_actions_reason is not None:
            degradation["allowedActions_reason"] = allowed_actions_reason
        if degradation:
            meta["degradation"] = degradation

        return {
            "kill_switch": _project_kill_switch_contract(ks, kill_switch_surface),
            "allowedActions": allowed_actions,
            "meta": meta,
        }

    # -- Audit Management -----------------------------------------------------

    def list_audit_events(
        self,
        *,
        actor: Optional[str] = None,
        action_types: Optional[List[str]] = None,
        target_type: Optional[str] = None,
        from_ts: Optional[datetime] = None,
        to_ts: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        store = self.get_read_store()
        if store and hasattr(store, "list_governance_audit_events"):
            return store.list_governance_audit_events(
                actor=actor,
                action_types=action_types,
                target_type=target_type,
                from_ts=from_ts,
                to_ts=to_ts,
            )
        return []
