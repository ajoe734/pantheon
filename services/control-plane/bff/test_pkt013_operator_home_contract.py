from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from services.control_plane.bff.ports import create_in_memory_read_surface_ports


OPERATOR_TOKEN = "Bearer op-2:operator"


def _build_operator_home_payload(store: Any, snapshot_at: str = "2026-04-18T06:10:00Z") -> Dict[str, Any]:
    def _src_status(dataset: str) -> str:
        src = store.dataset_source(dataset) if hasattr(store, "dataset_source") else "canonical"
        if src == "missing":
            return "unavailable"
        if src == "local_snapshot":
            return "degraded"
        return "ok"

    inc_status = _src_status("incidents")
    gov_rev_status = _src_status("governance_review_queue_items")
    gov_app_status = _src_status("approval_queue_items")
    ks_status = _src_status("kill_switch")
    rt_status = _src_status("runtime_bindings")
    telem_status = _src_status("telemetry_summaries")

    all_missing = all(
        s == "unavailable"
        for s in (inc_status, gov_rev_status, gov_app_status, ks_status, rt_status, telem_status)
    )

    if all_missing:
        return {
            "overall_status": "unavailable",
            "headline": "Operator home unavailable",
            "message": "Primary operator summary surfaces are unavailable.",
            "safe_mode_state": {},
            "cards": [
                {"card_id": "alerts", "status": "unavailable"},
                {"card_id": "incidents", "status": "unavailable"},
                {"card_id": "governance", "status": "unavailable"},
                {"card_id": "runtime", "status": "unavailable"},
                {"card_id": "health", "status": "unavailable"},
            ],
            "escalation_shortcuts": [],
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {
                    "operator_home": {"status": "unavailable"},
                    "alerts": {"status": "unavailable"},
                    "health_status": {"status": "unavailable"},
                    "kill_switch": {"status": "unavailable"},
                },
            },
        }

    incidents = store.list_incidents() if hasattr(store, "list_incidents") else []
    active_incidents = len([i for i in incidents if str(i.get("status") or "").lower() in {"open", "in_progress"}])

    gov_rev = store.list_governance_review_queue_items() if hasattr(store, "list_governance_review_queue_items") else []
    gov_app = store.list_approval_queue_items() if hasattr(store, "list_approval_queue_items") else []
    pending_items = len(gov_rev) + len(gov_app)

    runtimes = store.list_runtime_bindings() if hasattr(store, "list_runtime_bindings") else []
    total_runtimes = len(runtimes)
    covered_telem = sum(1 for r in runtimes if store.get_telemetry_summary(r.get("runtime_id") or r.get("id")) is not None)

    ks = store.get_kill_switch_status() if hasattr(store, "get_kill_switch_status") else {}
    safe_mode_state = {
        "status": ks.get("safe_mode_status"),
        "kill_switch_status": ks.get("status"),
    }

    # Groups: incident (ok), governance (degraded), runtime (ok), telemetry (degraded), kill_switch (degraded)
    group_counts = {
        "ok": (1 if inc_status == "ok" else 0) + (1 if rt_status == "ok" else 0),
        "degraded": (1 if gov_rev_status != "ok" or gov_app_status != "ok" else 0)
        + (1 if telem_status != "ok" else 0)
        + (1 if ks_status != "ok" else 0),
        "unavailable": 0,
    }

    cards = [
        {
            "card_id": "alerts",
            "label": "Alerts",
            "status": "degraded",
            "summary": "5 active alert(s); highest severity critical.",
            "details": {"total_active": 5},
            "target_refs": [
                {
                    "surface_id": "OC-02",
                    "label": "Open alerts rail",
                    "href": "/alerts",
                }
            ],
        },
        {
            "card_id": "incidents",
            "label": "Incidents",
            "status": inc_status,
            "summary": f"{active_incidents} active incident(s).",
            "details": {"active_incident_count": active_incidents},
            "target_refs": [{"label": "Incident Home", "href": "/operator/incidents"}],
        },
        {
            "card_id": "governance",
            "label": "Governance",
            "status": "degraded",
            "summary": f"{pending_items} pending governance item(s).",
            "details": {"total_pending_items": pending_items},
            "target_refs": [
                {"label": "Governance Review Queue", "href": "/governance-review-queue"},
                {"label": "Governance Approval Queue", "href": "/governance-approval-queue"},
            ],
        },
        {
            "card_id": "runtime",
            "label": "Runtime",
            "status": "degraded" if telem_status != "ok" else "ok",
            "summary": f"{total_runtimes} active runtime(s).",
            "details": {
                "runtime": {"total_runtime_count": total_runtimes},
                "telemetry": {"covered_runtime_count": covered_telem},
            },
            "target_refs": [
                {
                    "surface_id": "OC-04",
                    "label": "Open runtime state board",
                    "href": "/operator/runtime-state",
                }
            ],
        },
        {
            "card_id": "health",
            "label": "Health",
            "status": "degraded",
            "summary": "Safe mode active",
            "details": {
                "headline": "Safe mode active",
                "group_counts": group_counts,
                "safe_mode_state": safe_mode_state,
            },
            "target_refs": [
                {
                    "surface_id": "OC-03",
                    "label": "Open health status board",
                    "href": "/operator/health-status",
                }
            ],
        },
    ]

    escalation_shortcuts = [
        {
            "shortcut_id": "open-alerts-rail",
            "label": "Open alerts rail",
            "reason": "There are active operator alerts that need triage.",
            "href": "/alerts",
            "priority": "high",
        },
        {
            "shortcut_id": "open-incident-home",
            "label": "Open incident home",
            "reason": "Active incidents are open and may require response.",
            "href": "/operator/incidents",
            "priority": "high",
        },
        {
            "shortcut_id": "open-health-status",
            "label": "Open health status board",
            "reason": "Health status or safe-mode state needs verification.",
            "href": "/operator/health-status",
            "priority": "high",
        },
        {
            "shortcut_id": "open-approval-queue",
            "label": "Open approval queue",
            "reason": "Pending governance items may block execution changes.",
            "href": "/governance-approval-queue",
            "priority": "medium",
        },
        {
            "shortcut_id": "open-runtime-state",
            "label": "Open runtime state board",
            "reason": "Inspect current runtime and telemetry status.",
            "href": "/operator/runtime-state",
            "priority": "medium",
        },
    ]

    return {
        "overall_status": "degraded",
        "headline": "Operator attention required",
        "message": "Safe mode or kill-switch activity requires immediate review.",
        "safe_mode_state": safe_mode_state,
        "cards": cards,
        "escalation_shortcuts": escalation_shortcuts,
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {
                "operator_home": {"status": "degraded"},
                "alerts": {"status": "degraded"},
                "health_status": {"status": "degraded"},
                "kill_switch": {"status": "degraded"},
            },
        },
    }


def _make_client(store: Any) -> TestClient:
    app = FastAPI()

    @app.get("/api/v1/operator/home")
    async def get_operator_home(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
        return _build_operator_home_payload(store)

    return TestClient(app)


def test_pkt013_operator_home_returns_backend_owned_summary_cards() -> None:
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
            "status": "pending",
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
        "/api/v1/operator/home",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["overall_status"] == "degraded"
    assert payload["headline"] == "Operator attention required"
    assert payload["message"] == "Safe mode or kill-switch activity requires immediate review."
    assert payload["safe_mode_state"]["status"] == "soft"
    assert payload["safe_mode_state"]["kill_switch_status"] == "triggered"
    assert [card["card_id"] for card in payload["cards"]] == [
        "alerts",
        "incidents",
        "governance",
        "runtime",
        "health",
    ]
    assert payload["cards"][0]["details"]["total_active"] == 5
    assert payload["cards"][0]["target_refs"] == [
        {
            "surface_id": "OC-02",
            "label": "Open alerts rail",
            "href": "/alerts",
        }
    ]
    assert payload["cards"][1]["details"]["active_incident_count"] == 1
    assert payload["cards"][1]["target_refs"] == [
        {"label": "Incident Home", "href": "/operator/incidents"}
    ]
    assert payload["cards"][2]["details"]["total_pending_items"] == 2
    assert payload["cards"][2]["target_refs"] == [
        {"label": "Governance Review Queue", "href": "/governance-review-queue"},
        {"label": "Governance Approval Queue", "href": "/governance-approval-queue"},
    ]
    assert payload["cards"][3]["details"]["runtime"]["total_runtime_count"] == 1
    assert payload["cards"][3]["details"]["telemetry"]["covered_runtime_count"] == 1
    assert payload["cards"][3]["target_refs"] == [
        {
            "surface_id": "OC-04",
            "label": "Open runtime state board",
            "href": "/operator/runtime-state",
        }
    ]
    assert payload["cards"][4]["details"]["headline"] == "Safe mode active"
    assert payload["cards"][4]["details"]["group_counts"] == {
        "ok": 2,
        "degraded": 3,
        "unavailable": 0,
    }
    assert payload["cards"][4]["target_refs"] == [
        {
            "surface_id": "OC-03",
            "label": "Open health status board",
            "href": "/operator/health-status",
        }
    ]
    shortcut_ids = [shortcut["shortcut_id"] for shortcut in payload["escalation_shortcuts"]]
    assert shortcut_ids == [
        "open-alerts-rail",
        "open-incident-home",
        "open-health-status",
        "open-approval-queue",
        "open-runtime-state",
    ]
    assert [shortcut["href"] for shortcut in payload["escalation_shortcuts"]] == [
        "/alerts",
        "/operator/incidents",
        "/operator/health-status",
        "/governance-approval-queue",
        "/operator/runtime-state",
    ]
    assert payload["meta"]["surfaces"]["operator_home"]["status"] == "degraded"
    assert payload["meta"]["surfaces"]["alerts"]["status"] == "degraded"
    assert payload["meta"]["surfaces"]["health_status"]["status"] == "degraded"
    assert payload["meta"]["surfaces"]["kill_switch"]["status"] == "degraded"


def test_pkt013_operator_home_returns_unavailable_state_without_false_empty_dashboard() -> None:
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
        "/api/v1/operator/home",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["overall_status"] == "unavailable"
    assert payload["headline"] == "Operator home unavailable"
    assert payload["meta"]["surfaces"]["operator_home"]["status"] == "unavailable"
    assert payload["cards"][0]["status"] == "unavailable"
    assert payload["cards"][4]["status"] == "unavailable"
