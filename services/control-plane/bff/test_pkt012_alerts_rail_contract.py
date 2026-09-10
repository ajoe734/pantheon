from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.incidents.router import create_incident_router
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


OPERATOR_TOKEN = "Bearer op-2:operator"

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


def _build_operator_alerts_payload(store: Any, snapshot_at: str) -> Dict[str, Any]:
    def _dataset_surface(dataset: str) -> Dict[str, Any]:
        src = store.dataset_source(dataset) if hasattr(store, "dataset_source") else "canonical"
        if src == "missing":
            return {"status": "unavailable", "source": "missing"}
        elif src == "local_snapshot":
            return {"status": "degraded", "source": "local_snapshot"}
        else:
            return {"status": "ok", "source": src}

    inc_surface = _dataset_surface("incidents")
    review_surface = _dataset_surface("governance_review_queue_items")
    approval_surface = _dataset_surface("approval_queue_items")
    kill_surface = _dataset_surface("kill_switch")
    roster_surface = _dataset_surface("runtime_bindings")
    telem_surface = _dataset_surface("telemetry_summaries")

    source_surfaces = [inc_surface, review_surface, approval_surface, kill_surface, roster_surface, telem_surface]
    statuses = [s["status"] for s in source_surfaces]
    if all(st == "ok" for st in statuses):
        alerts_surface = {"status": "ok", "source": "bff_composed"}
    elif all(st == "unavailable" for st in statuses):
        alerts_surface = {
            "status": "unavailable",
            "source": "bff_composed",
            "message": "Operator alert feed unavailable.",
        }
    else:
        alerts_surface = {
            "status": "degraded",
            "source": "bff_composed",
            "message": "Operator alert feed is available, but one or more contributing surfaces are degraded.",
        }

    alerts: List[Dict[str, Any]] = []

    if inc_surface["status"] != "unavailable" and hasattr(store, "list_incidents"):
        for inc in store.list_incidents():
            st = str(inc.get("status") or "").lower()
            if st not in {"open", "in_progress"}:
                continue
            inc_id = str(inc.get("incident_id") or "")
            raw_sev = str(inc.get("severity") or "").lower()
            if raw_sev in {"high", "critical", "sev1"}:
                sev = "critical"
            elif raw_sev in {"medium", "sev2"}:
                sev = "high"
            else:
                sev = "medium"
            prefix = "Active" if st == "open" else "In-progress"
            alerts.append({
                "alert_id": f"alert-incident-{inc_id}",
                "severity": sev,
                "category": "incident",
                "raised_at": inc.get("opened_at") or inc.get("created_at") or snapshot_at,
                "summary": f"{prefix} incident: {inc.get('title') or inc_id}.",
                "target_ref": {
                    "surface_id": "PKT-002",
                    "label": "Open incident response",
                    "href": f"/operator/incidents/{inc_id}",
                    "target_id": inc_id,
                },
            })

    if review_surface["status"] != "unavailable" and hasattr(store, "list_governance_review_queue_items"):
        for item in store.list_governance_review_queue_items():
            item_id = str(item.get("item_id") or "")
            st = str(item.get("status") or "").lower()
            if st not in {"pending", "in_review", "escalated"}:
                continue
            raw_risk = str(item.get("risk_level") or "").lower()
            if st == "escalated":
                sev = "high" if _ALERT_SEVERITY_ORDER.get(raw_risk, 0) < _ALERT_SEVERITY_ORDER["high"] else raw_risk
                summary = f"Escalated governance review: {item.get('item_type') or 'Governance item'} {item_id}."
            elif st == "in_review":
                sev = raw_risk or "medium"
                summary = f"Governance review in progress: {item.get('item_type') or 'Governance item'} {item_id}."
            else:
                sev = raw_risk or "medium"
                summary = f"Pending governance review: {item.get('item_type') or 'Governance item'} {item_id}."
            alerts.append({
                "alert_id": f"alert-governance-review-{item_id}",
                "severity": sev,
                "category": "governance",
                "raised_at": item.get("submitted_at") or snapshot_at,
                "summary": summary,
                "target_ref": {
                    "surface_id": "PKT-001",
                    "label": "Open governance review queue",
                    "href": "/governance-review-queue",
                    "target_id": item_id,
                },
            })

    if approval_surface["status"] != "unavailable" and hasattr(store, "list_approval_queue_items"):
        for item in store.list_approval_queue_items():
            dec_id = str(item.get("decision_id") or "")
            st = str(item.get("decision_state") or "").lower()
            if st not in {"pending", "in_review"}:
                continue
            sev = str(item.get("risk_level") or "medium").lower()
            if st == "in_review" and _ALERT_SEVERITY_ORDER.get(sev, 0) < _ALERT_SEVERITY_ORDER["high"]:
                sev = "high"
            prefix = "Approval decision in review" if st == "in_review" else "Approval required"
            alerts.append({
                "alert_id": f"alert-approval-{dec_id}",
                "severity": sev,
                "category": "governance",
                "raised_at": item.get("submitted_at") or snapshot_at,
                "summary": f"{prefix}: {item.get('decision_type') or 'Approval item'} {dec_id}.",
                "target_ref": {
                    "surface_id": "GV-02",
                    "label": "Open approval queue",
                    "href": "/governance-approval-queue",
                    "target_id": dec_id,
                },
            })

    if kill_surface["status"] != "unavailable" and hasattr(store, "get_kill_switch_status"):
        ks = store.get_kill_switch_status() or {}
        active = ks.get("active")
        ks_status = str(ks.get("status") or "").lower()
        safe_mode = str(ks.get("safe_mode_status") or "").lower()
        safe_active = safe_mode not in {"", "off", "released", "none", "null"}
        if active or ks_status == "triggered":
            alerts.append({
                "alert_id": "alert-kill-switch-state",
                "severity": "critical",
                "category": "kill_switch",
                "raised_at": ks.get("last_triggered_at") or ks.get("last_confirmed_at") or snapshot_at,
                "summary": "Kill-switch active; operator intervention is required.",
                "target_ref": {
                    "surface_id": "OC-03",
                    "label": "Open health status board",
                    "href": "/operator/health-status",
                    "target_id": ks_status or safe_mode or "kill-switch",
                },
            })
        elif ks_status == "cooling_down":
            alerts.append({
                "alert_id": "alert-kill-switch-state",
                "severity": "high",
                "category": "kill_switch",
                "raised_at": ks.get("last_triggered_at") or ks.get("last_confirmed_at") or snapshot_at,
                "summary": "Kill-switch cooling down; verify runtime stability before resuming operations.",
                "target_ref": {
                    "surface_id": "OC-03",
                    "label": "Open health status board",
                    "href": "/operator/health-status",
                    "target_id": ks_status or safe_mode or "kill-switch",
                },
            })
        elif safe_active:
            alerts.append({
                "alert_id": "alert-kill-switch-state",
                "severity": "high",
                "category": "kill_switch",
                "raised_at": ks.get("last_triggered_at") or ks.get("last_confirmed_at") or snapshot_at,
                "summary": f"Safe mode active ({safe_mode}); use the health board to verify current restrictions.",
                "target_ref": {
                    "surface_id": "OC-03",
                    "label": "Open health status board",
                    "href": "/operator/health-status",
                    "target_id": safe_mode,
                },
            })

    if roster_surface["status"] != "unavailable" and hasattr(store, "list_runtime_bindings"):
        for binding in store.list_runtime_bindings():
            rt_id = str(binding.get("runtime_id") or binding.get("id") or "")
            telem = store.get_telemetry_summary(rt_id) if hasattr(store, "get_telemetry_summary") else None
            reasons: List[str] = []
            sevs: List[str] = []
            status = str(binding.get("status") or "").lower()
            if status in {"failed", "error"}:
                sevs.append("critical")
                reasons.append(f"runtime status is {status}")
            elif status in {"degraded", "unhealthy"}:
                sevs.append("high")
                reasons.append(f"runtime status is {status}")
            if telem:
                dd = telem.get("drawdown")
                if isinstance(dd, (int, float)):
                    if dd >= 0.10:
                        sevs.append("critical")
                        reasons.append(f"drawdown is {dd:.3f}")
                    elif dd >= 0.05:
                        sevs.append("high")
                        reasons.append(f"drawdown is {dd:.3f}")
                fr = telem.get("fill_rate")
                if isinstance(fr, (int, float)):
                    if fr < 0.90:
                        sevs.append("critical")
                        reasons.append(f"fill rate dropped to {fr:.2f}")
                    elif fr < 0.95:
                        sevs.append("high")
                        reasons.append(f"fill rate dropped to {fr:.2f}")
                slip = telem.get("avg_slippage_bps")
                if isinstance(slip, (int, float)):
                    if slip >= 4.0:
                        sevs.append("critical")
                        reasons.append(f"average slippage reached {slip:.1f} bps")
                    elif slip >= 3.0:
                        sevs.append("high")
                        reasons.append(f"average slippage reached {slip:.1f} bps")
            if reasons and sevs:
                best_rank = max(_ALERT_SEVERITY_ORDER.get(s, 0) for s in sevs)
                rt_sev = next(s for s in ("critical", "high", "medium", "low") if _ALERT_SEVERITY_ORDER[s] == best_rank)
                alerts.append({
                    "alert_id": f"alert-runtime-{rt_id}",
                    "severity": rt_sev,
                    "category": "runtime",
                    "raised_at": (telem or {}).get("collected_at") or binding.get("updated_at") or snapshot_at,
                    "summary": f"Runtime {rt_id} anomaly: {'; '.join(reasons[:2])}.",
                    "target_ref": {
                        "surface_id": "OC-04",
                        "label": "Open runtime state board",
                        "href": "/operator/runtime-state",
                        "target_id": rt_id,
                    },
                })

    def _sort_key(a: Dict[str, Any]) -> tuple:
        return (
            str(a.get("raised_at") or ""),
            _ALERT_SEVERITY_ORDER.get(str(a.get("severity") or "").lower(), 0),
            _ALERT_CATEGORY_ORDER.get(str(a.get("category") or "").lower(), 0),
            str(a.get("alert_id") or ""),
        )

    alerts.sort(key=_sort_key, reverse=True)

    if alerts_surface.get("status") == "unavailable":
        alerts = []

    by_sev = {k: 0 for k in _ALERT_SEVERITY_ORDER}
    by_cat = {k: 0 for k in _ALERT_CATEGORY_ORDER}
    for a in alerts:
        s = str(a.get("severity") or "").lower()
        c = str(a.get("category") or "").lower()
        if s in by_sev:
            by_sev[s] += 1
        if c in by_cat:
            by_cat[c] += 1

    highest_sev = None
    if alerts:
        highest_rank = max(_ALERT_SEVERITY_ORDER.get(s, 0) for s in by_sev if by_sev[s] > 0)
        highest_sev = next(s for s in ("critical", "high", "medium", "low") if _ALERT_SEVERITY_ORDER[s] == highest_rank)

    return {
        "alerts": alerts,
        "summary": {
            "total_active": len(alerts),
            "highest_severity": highest_sev,
            "by_severity": by_sev,
            "by_category": by_cat,
        },
        "meta": {
            "snapshot_at": snapshot_at,
            "acknowledgement_supported": True,
            "surfaces": {
                "alerts": alerts_surface,
                "incident_feed": inc_surface,
                "review_queue": review_surface,
                "approval_queue": approval_surface,
                "kill_switch": kill_surface,
                "runtime_roster": roster_surface,
                "telemetry_summary": telem_surface,
            },
        },
    }


def _make_client(store: Any) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_incident_router(
            read_surface=store,
            build_operator_alerts_payload=lambda s: _build_operator_alerts_payload(store, s),
        )
    )
    return TestClient(app)


def test_pkt012_alerts_rail_returns_backend_owned_alert_feed() -> None:
    store = create_in_memory_read_surface_ports()
    store.list_incidents = lambda **kwargs: [
        {
            "incident_id": "inc-001",
            "title": "Unexpected drawdown in persona-alpha",
            "severity": "high",
            "status": "open",
            "created_at": "2026-04-18T06:05:00Z",
        }
    ]
    store.list_governance_review_queue_items = lambda **kwargs: [
        {
            "item_id": "gov-review-001",
            "item_type": "DeploymentPlan",
            "risk_level": "medium",
            "status": "escalated",
            "submitted_at": "2026-04-18T06:03:00Z",
        }
    ]
    store.list_approval_queue_items = lambda **kwargs: [
        {
            "decision_id": "appr-001",
            "decision_type": "DeploymentPlan",
            "risk_level": "high",
            "decision_state": "pending",
            "submitted_at": "2026-04-18T06:04:00Z",
        }
    ]
    store.get_kill_switch_status = lambda: {
        "active": True,
        "status": "triggered",
        "safe_mode_status": "soft",
        "last_confirmed_at": "2026-04-18T06:07:00Z",
        "last_triggered_at": "2026-04-18T06:08:00Z",
        "active_commands": ["safe-mode-001"],
        "secondary_path_available": True,
    }
    store.list_runtime_bindings = lambda: [
        {
            "id": "runtime-042",
            "runtime_id": "runtime-042",
            "deployment_stage": "live",
            "status": "running",
            "plan_id": "plan-F-042",
        }
    ]
    store.get_telemetry_summary = lambda runtime_id: {
        "runtime_id": "runtime-042",
        "window": "1h",
        "drawdown": 0.125,
        "fill_rate": 0.89,
        "avg_slippage_bps": 4.2,
        "collected_at": "2026-04-18T06:10:00Z",
    } if runtime_id == "runtime-042" else None
    store.dataset_source = lambda dataset: {
        "incidents": "service_store",
        "governance_review_queue_items": "local_snapshot",
        "approval_queue_items": "local_snapshot",
        "kill_switch": "local_snapshot",
        "runtime_bindings": "canonical",
        "telemetry_summaries": "local_snapshot",
    }.get(dataset, "missing")
    client = _make_client(store)

    response = client.get(
        "/api/v1/operator/alerts",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["summary"] == {
        "total_active": 5,
        "highest_severity": "critical",
        "by_severity": {
            "critical": 3,
            "high": 2,
            "medium": 0,
            "low": 0,
        },
        "by_category": {
            "incident": 1,
            "kill_switch": 1,
            "governance": 2,
            "runtime": 1,
        },
    }
    assert payload["meta"]["acknowledgement_supported"] is True
    assert payload["meta"]["surfaces"]["alerts"]["status"] == "degraded"
    assert payload["meta"]["surfaces"]["incident_feed"]["status"] == "ok"
    assert payload["meta"]["surfaces"]["review_queue"]["status"] == "degraded"
    assert payload["meta"]["surfaces"]["approval_queue"]["status"] == "degraded"
    assert payload["meta"]["surfaces"]["kill_switch"]["status"] == "degraded"
    assert payload["meta"]["surfaces"]["runtime_roster"]["status"] == "ok"
    assert payload["meta"]["surfaces"]["telemetry_summary"]["status"] == "degraded"

    alert_ids = [alert["alert_id"] for alert in payload["alerts"]]
    assert alert_ids == [
        "alert-runtime-runtime-042",
        "alert-kill-switch-state",
        "alert-incident-inc-001",
        "alert-approval-appr-001",
        "alert-governance-review-gov-review-001",
    ]
    assert payload["alerts"][0] == {
        "alert_id": "alert-runtime-runtime-042",
        "severity": "critical",
        "category": "runtime",
        "raised_at": "2026-04-18T06:10:00Z",
        "summary": "Runtime runtime-042 anomaly: drawdown is 0.125; fill rate dropped to 0.89.",
        "target_ref": {
            "surface_id": "OC-04",
            "label": "Open runtime state board",
            "href": "/operator/runtime-state",
            "target_id": "runtime-042",
        },
    }
    assert payload["alerts"][1]["target_ref"]["surface_id"] == "OC-03"
    assert payload["alerts"][1]["target_ref"]["href"] == "/operator/health-status"
    assert payload["alerts"][2]["target_ref"]["surface_id"] == "PKT-002"
    assert payload["alerts"][2]["target_ref"]["href"] == "/operator/incidents/inc-001"
    assert payload["alerts"][3]["target_ref"]["surface_id"] == "GV-02"
    assert payload["alerts"][3]["target_ref"]["href"] == "/governance-approval-queue"
    assert payload["alerts"][4]["target_ref"]["surface_id"] == "PKT-001"
    assert payload["alerts"][4]["target_ref"]["href"] == "/governance-review-queue"


def test_pkt012_alerts_rail_returns_unavailable_when_all_sources_are_missing() -> None:
    store = create_in_memory_read_surface_ports()
    store.list_incidents = lambda **kwargs: []
    store.list_governance_review_queue_items = lambda **kwargs: []
    store.list_approval_queue_items = lambda **kwargs: []
    store.get_kill_switch_status = lambda: {}
    store.list_runtime_bindings = lambda: []
    store.get_telemetry_summary = lambda runtime_id: None
    store.dataset_source = lambda dataset: "missing"
    client = _make_client(store)

    response = client.get(
        "/api/v1/operator/alerts",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["alerts"] == []
    assert payload["summary"]["total_active"] == 0
    assert payload["meta"]["surfaces"]["alerts"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["incident_feed"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["review_queue"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["approval_queue"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["kill_switch"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["runtime_roster"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["telemetry_summary"]["status"] == "unavailable"


def test_alt001_bff_alerts_endpoint_returns_operator_alert_projection_and_detail() -> None:
    store = create_in_memory_read_surface_ports()
    store.list_incidents = lambda **kwargs: [
        {
            "incident_id": "inc-alt-001",
            "title": "Management alert live data",
            "severity": "high",
            "status": "open",
            "created_at": "2026-05-16T05:30:00Z",
        }
    ]
    store.list_governance_review_queue_items = lambda **kwargs: []
    store.list_approval_queue_items = lambda **kwargs: []
    store.get_kill_switch_status = lambda: {
        "active": False,
        "status": "released",
        "safe_mode_status": "off",
    }
    store.list_runtime_bindings = lambda: []
    store.get_telemetry_summary = lambda runtime_id: None
    store.dataset_source = lambda dataset: {
        "incidents": "service_store",
        "governance_review_queue_items": "service_store",
        "approval_queue_items": "service_store",
        "kill_switch": "service_store",
        "runtime_bindings": "canonical",
        "telemetry_summaries": "canonical",
    }.get(dataset, "missing")
    client = _make_client(store)

    response = client.get(
        "/bff/alerts",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["summary"]["total_active"] == 1
    assert payload["summary"]["highest_severity"] == "critical"
    assert payload["meta"]["surfaces"]["alerts"]["status"] == "ok"
    alert = payload["alerts"][0]
    assert alert == {
        "alert_id": "alert-incident-inc-alt-001",
        "severity": "critical",
        "category": "incident",
        "raised_at": "2026-05-16T05:30:00Z",
        "summary": "Active incident: Management alert live data.",
        "target_ref": {
            "surface_id": "PKT-002",
            "label": "Open incident response",
            "href": "/operator/incidents/inc-alt-001",
            "target_id": "inc-alt-001",
        },
    }

    detail = client.get(
        "/bff/alerts/alert-incident-inc-alt-001",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert detail.status_code == 200, detail.text
    detail_payload = detail.json()
    assert detail_payload["data"] == alert
    assert detail_payload["meta"]["surfaces"]["alerts"]["status"] == "ok"
