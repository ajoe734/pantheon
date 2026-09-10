from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.management_read_models.router import create_management_router
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


OPERATOR_TOKEN = "Bearer op-2:operator"


_HEALTH_GROUP_LABELS = {
    "runtime": "Runtime Execution",
    "telemetry": "Telemetry & Metrics",
    "incident": "Active Incidents",
    "governance": "Governance & Approvals",
    "kill_switch": "Kill-Switch & Safe Mode",
}

_SECONDARY_CONTROL_PATH_ADVISORY_TARGETS = (
    {
        "operation": "Health diagnostics",
        "channel": "admin_cli",
        "command": "pantheon admin health",
        "api_path": "GET /admin/health",
        "required_role": "operator",
        "requires_mfa": False,
    },
    {
        "operation": "Runtime status",
        "channel": "admin_cli",
        "command": "pantheon admin runtime status --runtime={runtime_id}",
        "api_path": "GET /admin/runtimes/{runtime_id}/status",
        "required_role": "operator",
        "requires_mfa": False,
    },
    {
        "operation": "Kill-switch status",
        "channel": "admin_cli",
        "command": "pantheon admin kill-switch status",
        "api_path": "GET /admin/kill-switch/status",
        "required_role": "operator",
        "requires_mfa": False,
    },
)

_SECONDARY_CONTROL_PATH_RECOMMENDED_TARGETS = _SECONDARY_CONTROL_PATH_ADVISORY_TARGETS + (
    {
        "operation": "Runtime pause",
        "channel": "admin_cli",
        "command": "pantheon admin runtime pause --runtime={runtime_id}",
        "api_path": "POST /admin/runtimes/{runtime_id}/pause",
        "required_role": "admin",
        "requires_mfa": True,
    },
    {
        "operation": "Runtime rollback",
        "channel": "admin_cli",
        "command": "pantheon admin runtime rollback --runtime={runtime_id} --target={version}",
        "api_path": "POST /admin/runtimes/{runtime_id}/rollback",
        "required_role": "admin",
        "requires_mfa": True,
    },
    {
        "operation": "Kill-switch activation",
        "channel": "admin_cli",
        "command": "pantheon admin kill-switch activate --runtime={runtime_id}",
        "api_path": "POST /admin/kill-switch/activate",
        "required_role": "admin",
        "requires_mfa": True,
    },
)

_INCIDENT_SEVERITY_ORDER = {"sev1": 3, "sev2": 2, "sev3": 1}
_GOVERNANCE_RISK_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_INCIDENT_SEVERITY_MAP = {
    "high": "sev1",
    "medium": "sev2",
    "low": "sev3",
    "critical": "sev1",
    "sev1": "sev1",
    "sev2": "sev2",
    "sev3": "sev3",
}


def _incident_home_severity(value: str | None) -> str | None:
    if value is None:
        return None
    return _INCIDENT_SEVERITY_MAP.get(str(value).strip().lower(), str(value))


def _highest_ranked_value(values: list[str | None], order: dict[str, int]) -> str | None:
    best_value: str | None = None
    best_rank = -1
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip().lower()
        rank = order.get(normalized)
        if rank is None:
            continue
        if rank > best_rank:
            best_rank = rank
            best_value = normalized
    return best_value


def _dataset_surface_status(store: Any, dataset: str, snapshot_at: str) -> dict[str, Any]:
    source = store.dataset_source(dataset) if hasattr(store, "dataset_source") else "missing"
    surface: dict[str, Any] = {"status": "ok", "source": source}
    if source == "local_snapshot":
        surface["status"] = "degraded"
        surface["note"] = "Served from local BFF snapshot fallback instead of a backend-owned read store."
        surface["staleness"] = {
            "served_from": "local_snapshot",
            "last_known_at": snapshot_at,
        }
    elif source == "missing":
        surface["status"] = "unavailable"
        surface["staleness"] = {
            "served_from": "unverifiable",
            "last_known_at": snapshot_at,
        }
    elif source in {"canonical", "service_store", "service_client"}:
        surface["status"] = "ok"
    return surface


def _aggregate_group_surface(
    surface_key: str,
    source_surfaces: list[dict[str, Any]],
    snapshot_at: str,
    unavailable_message: str,
    degraded_message: str,
) -> dict[str, Any]:
    surface: dict[str, Any] = {
        "status": "ok",
        "source": "bff_composed",
        "staleness": {"served_from": "bff_composed", "last_known_at": snapshot_at},
    }
    statuses = [entry.get("status", "ok") for entry in source_surfaces]
    if statuses and all(status == "ok" for status in statuses):
        return surface
    if statuses and all(status == "unavailable" for status in statuses):
        surface["status"] = "unavailable"
        surface["message"] = unavailable_message
        return surface
    surface["status"] = "degraded"
    surface["message"] = degraded_message
    return surface


def _build_operator_health_status_payload(store: Any, snapshot_at: str) -> dict[str, Any]:
    # 1. Runtime group
    bindings = store.list_runtime_bindings() if hasattr(store, "list_runtime_bindings") else []
    runtime_surface = _dataset_surface_status(store, "runtime_bindings", snapshot_at)
    runtime_group_surface = _aggregate_group_surface(
        "runtime",
        [runtime_surface],
        snapshot_at,
        "Runtime roster unavailable.",
        "Runtime roster degraded or stale.",
    )
    runtime_group = {
        "group_id": "runtime",
        "label": _HEALTH_GROUP_LABELS["runtime"],
        "status": runtime_group_surface["status"],
        "summary": f"{len(bindings)} runtime(s) tracked." if bindings else "No runtimes reported.",
        "details": {
            "total_runtime_count": len(bindings),
        },
        "target_refs": [
            {"label": "Runtime State Board", "href": "/operator/runtime-state"},
        ],
    }

    # 2. Telemetry group
    telemetry_surface = _dataset_surface_status(store, "telemetry_summaries", snapshot_at)
    covered = 0
    for b in bindings:
        rid = b.get("runtime_id") or b.get("id")
        if rid and hasattr(store, "get_telemetry_summary") and store.get_telemetry_summary(rid):
            covered += 1
    total_rt = len(bindings)
    missing_rt = max(total_rt - covered, 0)
    if total_rt > 0 and missing_rt > 0 and telemetry_surface.get("status") == "ok":
        telemetry_surface["status"] = "degraded"
        telemetry_surface["message"] = "Telemetry summary missing for one or more runtimes."
    telemetry_group_surface = _aggregate_group_surface(
        "telemetry",
        [telemetry_surface],
        snapshot_at,
        "Telemetry summary unavailable.",
        "Telemetry summary coverage is degraded or stale.",
    )
    telemetry_group = {
        "group_id": "telemetry",
        "label": _HEALTH_GROUP_LABELS["telemetry"],
        "status": telemetry_group_surface["status"],
        "summary": f"Telemetry coverage for {covered} of {total_rt} runtime(s).",
        "details": {
            "covered_runtime_count": covered,
        },
        "target_refs": [
            {"label": "Runtime State Board", "href": "/operator/runtime-state"},
        ],
    }

    # 3. Incident group
    incident_surface = _dataset_surface_status(store, "incidents", snapshot_at)
    incidents = store.list_incidents() if hasattr(store, "list_incidents") else []
    active_incidents = [
        i for i in incidents if str(i.get("status") or "").lower() in {"open", "in_progress"}
    ]
    highest_sev = _highest_ranked_value(
        [_incident_home_severity(i.get("severity")) for i in active_incidents],
        _INCIDENT_SEVERITY_ORDER,
    )
    incident_group_surface = _aggregate_group_surface(
        "incident",
        [incident_surface],
        snapshot_at,
        "Incident surface unavailable.",
        "Incident surface degraded or stale.",
    )
    incident_group = {
        "group_id": "incident",
        "label": _HEALTH_GROUP_LABELS["incident"],
        "status": incident_group_surface["status"],
        "summary": f"{len(active_incidents)} active incident(s).",
        "details": {
            "active_incident_count": len(active_incidents),
            "highest_severity": highest_sev,
        },
        "target_refs": [
            {"label": "Incident Home", "href": "/operator/incidents"},
        ],
    }

    # 4. Governance group
    rev_surface = _dataset_surface_status(store, "governance_review_queue_items", snapshot_at)
    app_surface = _dataset_surface_status(store, "approval_queue_items", snapshot_at)
    rev_items = (
        store.list_governance_review_queue_items()
        if hasattr(store, "list_governance_review_queue_items")
        else []
    )
    app_items = (
        store.list_approval_queue_items()
        if hasattr(store, "list_approval_queue_items")
        else []
    )
    highest_risk = _highest_ranked_value(
        [item.get("risk_level") for item in rev_items + app_items],
        _GOVERNANCE_RISK_ORDER,
    )
    gov_group_surface = _aggregate_group_surface(
        "governance",
        [rev_surface, app_surface],
        snapshot_at,
        "Governance health unavailable.",
        "Governance review or approval surfaces are degraded.",
    )
    total_pending = len(rev_items) + len(app_items)
    gov_group = {
        "group_id": "governance",
        "label": _HEALTH_GROUP_LABELS["governance"],
        "status": gov_group_surface["status"],
        "summary": f"{total_pending} governance item(s) pending review or approval.",
        "details": {
            "total_pending_items": total_pending,
            "highest_risk_level": highest_risk,
        },
        "target_refs": [
            {"label": "Governance Review Queue", "href": "/governance-review-queue"},
            {"label": "Governance Approval Queue", "href": "/governance-approval-queue"},
        ],
    }

    # 5. Kill switch group
    kill_switch_surface = _dataset_surface_status(store, "kill_switch", snapshot_at)
    ks = (
        store.get_kill_switch_status()
        if hasattr(store, "get_kill_switch_status") and kill_switch_surface.get("status") != "unavailable"
        else {}
    )
    safe_mode_status = ks.get("safe_mode_status")
    kill_switch_status = ks.get("status")
    ks_group_surface = _aggregate_group_surface(
        "kill_switch",
        [kill_switch_surface],
        snapshot_at,
        "Kill-switch and safe-mode state unavailable.",
        "Kill-switch or safe-mode state is degraded or stale.",
    )
    safe_mode_state = {
        "status": None if kill_switch_surface.get("status") == "unavailable" else safe_mode_status,
        "kill_switch_status": None if kill_switch_surface.get("status") == "unavailable" else kill_switch_status,
        "active": None if kill_switch_surface.get("status") == "unavailable" else ks.get("active"),
        "last_confirmed_at": None if kill_switch_surface.get("status") == "unavailable" else ks.get("last_confirmed_at"),
        "last_triggered_at": None if kill_switch_surface.get("status") == "unavailable" else ks.get("last_triggered_at"),
        "secondary_path_available": None if kill_switch_surface.get("status") == "unavailable" else ks.get("secondary_path_available"),
    }
    kill_switch_group = {
        "group_id": "kill_switch",
        "label": _HEALTH_GROUP_LABELS["kill_switch"],
        "status": ks_group_surface["status"],
        "summary": f"Kill-switch {kill_switch_status}; safe mode {safe_mode_status}.",
        "details": {
            "safe_mode_status": safe_mode_state["status"],
        },
        "target_refs": [
            {"label": "Health Status Board", "href": "/operator/health-status"},
        ],
    }

    group_surfaces = {
        "runtime": runtime_group_surface,
        "telemetry": telemetry_group_surface,
        "incident": incident_group_surface,
        "governance": gov_group_surface,
        "kill_switch": ks_group_surface,
    }
    overall_surface = _aggregate_group_surface(
        "health_status",
        list(group_surfaces.values()),
        snapshot_at,
        "All health groups are unavailable.",
        "One or more health groups are degraded or unavailable.",
    )
    overall_status = overall_surface.get("status", "ok")

    group_counts = {
        "ok": sum(1 for surface in group_surfaces.values() if surface.get("status") == "ok"),
        "degraded": sum(
            1 for surface in group_surfaces.values() if surface.get("status") == "degraded"
        ),
        "unavailable": sum(
            1 for surface in group_surfaces.values() if surface.get("status") == "unavailable"
        ),
    }

    safe_mode_active = str(safe_mode_state.get("status") or "").lower() not in {
        "",
        "off",
        "released",
        "none",
        "null",
    }
    ks_status_str = str(safe_mode_state.get("kill_switch_status") or "").lower()

    if overall_status == "unavailable" or safe_mode_active or ks_status_str in {"triggered", "cooling_down"}:
        sec_mode = "recommended"
        sec_reason = (
            "One or more critical health groups are unavailable or safe mode is active. "
            "Use the secondary control path for verification or intervention."
        )
        sec_targets = _SECONDARY_CONTROL_PATH_RECOMMENDED_TARGETS
    else:
        sec_mode = "advisory"
        sec_reason = (
            "Some health groups are degraded. Use the secondary control path to verify "
            "current control-plane state before critical decisions."
        )
        sec_targets = _SECONDARY_CONTROL_PATH_ADVISORY_TARGETS

    secondary_control_path = {
        "mode": sec_mode,
        "reason": sec_reason,
        "targets": list(sec_targets),
    }

    if safe_mode_active:
        headline = "Safe mode active"
    elif ks_status_str == "cooling_down":
        headline = "Kill-switch cooling down"
    elif ks_status_str == "triggered":
        headline = "Kill-switch triggered"
    elif overall_status == "ok":
        headline = "Control plane healthy"
    elif overall_status == "degraded":
        headline = "Some services degraded"
    else:
        headline = "Control plane health unavailable"

    return {
        "overall_status": overall_status,
        "headline": headline,
        "group_counts": group_counts,
        "safe_mode_state": safe_mode_state,
        "secondary_control_path": secondary_control_path,
        "groups": [
            runtime_group,
            telemetry_group,
            incident_group,
            gov_group,
            kill_switch_group,
        ],
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {
                "health_status": overall_surface,
                **group_surfaces,
            },
        },
    }


def _build_client(store: Any) -> TestClient:
    app = FastAPI()

    @app.get("/api/v1/operator/health-status")
    async def get_operator_health_status() -> dict[str, Any]:
        return _build_operator_health_status_payload(store, "2026-04-18T06:00:00Z")

    return TestClient(app)


def test_pkt011_health_status_board_returns_contract_payload() -> None:
    store = create_in_memory_read_surface_ports()
    store.list_runtime_bindings = lambda: [
        {
            "id": "runtime-042",
            "runtime_id": "runtime-042",
            "deployment_stage": "paper",
            "status": "idle",
            "plan_id": "plan-F-042",
            "artifact_id": "artifact-042",
            "artifact_version": "v2.1.0",
        }
    ]
    store.get_telemetry_summary = lambda runtime_id: {
        "runtime_id": "runtime-042",
        "window": "1h",
        "pnl": -0.12,
        "drawdown": 0.125,
        "sharpe_ratio": -0.8,
        "fill_rate": 0.94,
        "avg_slippage_bps": 3.2,
        "total_trades": 47,
        "collected_at": "2026-04-10T15:00:00Z",
    } if runtime_id == "runtime-042" else None
    store.list_incidents = lambda **kwargs: [
        {
            "incident_id": "inc-001",
            "severity": "high",
            "status": "open",
        },
        {
            "incident_id": "inc-002",
            "severity": "low",
            "status": "resolved",
        },
    ]
    store.list_governance_review_queue_items = lambda **kwargs: [
        {"item_id": "gov-001", "risk_level": "medium"}
    ]
    store.list_approval_queue_items = lambda **kwargs: [
        {"decision_id": "appr-001", "risk_level": "high"}
    ]
    store.get_kill_switch_status = lambda: {
        "active": False,
        "status": "armed",
        "safe_mode_status": "off",
        "last_confirmed_at": "2026-04-18T06:00:00Z",
        "last_triggered_at": None,
        "active_commands": [],
        "secondary_path_available": True,
    }
    store.dataset_source = lambda dataset: {
        "runtime_bindings": "canonical",
        "telemetry_summaries": "local_snapshot",
        "incidents": "service_store",
        "governance_review_queue_items": "local_snapshot",
        "approval_queue_items": "local_snapshot",
        "kill_switch": "local_snapshot",
    }.get(dataset, "missing")
    client = _build_client(store)

    response = client.get(
        "/api/v1/operator/health-status",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["overall_status"] == "degraded"
    assert payload["headline"] == "Some services degraded"
    assert payload["group_counts"] == {"ok": 2, "degraded": 3, "unavailable": 0}
    assert payload["safe_mode_state"] == {
        "status": "off",
        "kill_switch_status": "armed",
        "active": False,
        "last_confirmed_at": "2026-04-18T06:00:00Z",
        "last_triggered_at": None,
        "secondary_path_available": True,
    }
    assert payload["secondary_control_path"]["mode"] == "advisory"
    assert payload["secondary_control_path"]["targets"][0]["command"] == "pantheon admin health"

    groups = {group["group_id"]: group for group in payload["groups"]}
    assert groups["runtime"]["status"] == "ok"
    assert groups["runtime"]["details"]["total_runtime_count"] == 1
    assert groups["runtime"]["target_refs"] == [
        {"label": "Runtime State Board", "href": "/operator/runtime-state"}
    ]
    assert groups["telemetry"]["status"] == "degraded"
    assert groups["telemetry"]["details"]["covered_runtime_count"] == 1
    assert groups["telemetry"]["target_refs"] == [
        {"label": "Runtime State Board", "href": "/operator/runtime-state"}
    ]
    assert groups["incident"]["details"]["active_incident_count"] == 1
    assert groups["incident"]["details"]["highest_severity"] == "sev1"
    assert groups["incident"]["target_refs"] == [
        {"label": "Incident Home", "href": "/operator/incidents"}
    ]
    assert groups["governance"]["details"]["total_pending_items"] == 2
    assert groups["governance"]["details"]["highest_risk_level"] == "high"
    assert groups["governance"]["target_refs"] == [
        {"label": "Governance Review Queue", "href": "/governance-review-queue"},
        {"label": "Governance Approval Queue", "href": "/governance-approval-queue"},
    ]
    assert groups["kill_switch"]["details"]["safe_mode_status"] == "off"
    assert groups["kill_switch"]["target_refs"] == [
        {"label": "Health Status Board", "href": "/operator/health-status"}
    ]

    assert payload["meta"]["surfaces"]["health_status"]["status"] == "degraded"
    assert payload["meta"]["surfaces"]["runtime"]["status"] == "ok"
    assert payload["meta"]["surfaces"]["telemetry"]["status"] == "degraded"
    assert payload["meta"]["surfaces"]["incident"]["status"] == "ok"
    assert payload["meta"]["surfaces"]["governance"]["status"] == "degraded"
    assert payload["meta"]["surfaces"]["kill_switch"]["status"] == "degraded"


def test_pkt011_health_status_board_returns_unavailable_when_primary_surfaces_missing() -> None:
    store = create_in_memory_read_surface_ports()
    store.list_runtime_bindings = lambda: []
    store.get_telemetry_summary = lambda runtime_id: None
    store.list_incidents = lambda **kwargs: []
    store.list_governance_review_queue_items = lambda **kwargs: []
    store.list_approval_queue_items = lambda **kwargs: []
    store.dataset_source = lambda dataset: "missing"
    client = _build_client(store)

    response = client.get(
        "/api/v1/operator/health-status",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["overall_status"] == "unavailable"
    assert payload["headline"] == "Control plane health unavailable"
    assert payload["group_counts"] == {"ok": 0, "degraded": 0, "unavailable": 5}
    assert payload["safe_mode_state"]["status"] is None
    assert payload["secondary_control_path"]["mode"] == "recommended"
    assert len(payload["secondary_control_path"]["targets"]) == 6
    assert all(group["status"] == "unavailable" for group in payload["groups"])
    assert payload["meta"]["surfaces"]["health_status"]["status"] == "unavailable"


def test_pkt011_health_status_board_escalates_secondary_path_when_safe_mode_active() -> None:
    store = create_in_memory_read_surface_ports()
    store.list_runtime_bindings = lambda: [
        {
            "id": "runtime-042",
            "runtime_id": "runtime-042",
            "deployment_stage": "live",
            "status": "running",
        }
    ]
    store.get_telemetry_summary = lambda runtime_id: {
        "runtime_id": runtime_id,
        "window": "1h",
        "pnl": 0.15,
        "drawdown": 0.02,
        "sharpe_ratio": 2.0,
        "fill_rate": 0.99,
        "avg_slippage_bps": 1.0,
        "total_trades": 21,
        "collected_at": "2026-04-18T06:10:00Z",
    }
    store.list_incidents = lambda **kwargs: []
    store.list_governance_review_queue_items = lambda **kwargs: []
    store.list_approval_queue_items = lambda **kwargs: []
    store.get_kill_switch_status = lambda: {
        "active": True,
        "status": "triggered",
        "safe_mode_status": "soft",
        "last_confirmed_at": "2026-04-18T06:12:00Z",
        "last_triggered_at": "2026-04-18T06:11:00Z",
        "active_commands": ["safe-mode-001"],
        "secondary_path_available": True,
    }
    store.dataset_source = lambda dataset: {
        "runtime_bindings": "canonical",
        "telemetry_summaries": "service_store",
        "incidents": "service_store",
        "governance_review_queue_items": "service_store",
        "approval_queue_items": "service_store",
        "kill_switch": "local_snapshot",
    }.get(dataset, "missing")
    client = _build_client(store)

    response = client.get(
        "/api/v1/operator/health-status",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["headline"] == "Safe mode active"
    assert payload["safe_mode_state"]["status"] == "soft"
    assert payload["safe_mode_state"]["kill_switch_status"] == "triggered"
    assert payload["secondary_control_path"]["mode"] == "recommended"
    commands = [target["command"] for target in payload["secondary_control_path"]["targets"]]
    assert "pantheon admin runtime rollback --runtime={runtime_id} --target={version}" in commands
