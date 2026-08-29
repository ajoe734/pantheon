from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
import read_store as bff_read_store
from ports import ReadSurfacePorts, create_in_memory_read_surface_ports, create_read_surface_ports


OPERATOR_TOKEN = "Bearer op-2:operator"


def test_pkt010_runtime_state_board_returns_contract_payload() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
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
            "runtime_binding_id": "rtb-042",
            "deployment_stage": "paper",
            "state": "active",
            "window": "1h",
            "pnl": -0.12,
            "drawdown": 0.125,
            "sharpe_ratio": -0.8,
            "fill_rate": 0.94,
            "avg_slippage_bps": 3.2,
            "total_trades": 47,
            "collected_at": "2026-04-10T15:00:00Z",
            "last_heartbeat_at": "2026-04-10T15:00:00Z",
            "last_event_at": "2026-04-10T15:00:00Z",
            "engine_bridge_repo": "ajoe734/pantheon-lean.git",
            "engine_bridge_commit": "abc1234",
            "health_summary": {
                "paper_runtime": "ok",
                "bridge": "ok",
                "telemetry": "ok",
                "broker": "not_applicable",
            },
            "projection_source": "telemetry_ingest",
        } if runtime_id == "runtime-042" else None
        store.get_paper_runtime_monitoring_session = lambda *, runtime_id=None, binding_id=None: {
            "session_id": "prmon-rtb-042",
            "session_type": "paper_runtime_monitoring",
            "binding_id": "runtime-042",
            "runtime_binding_id": "runtime-042",
            "runtime_id": "runtime-042",
            "deployment_stage": "paper",
            "status": "running",
            "active": True,
            "started_at": "2026-04-10T14:55:00Z",
            "ended_at": None,
            "last_heartbeat_at": "2026-04-10T15:00:00Z",
            "heartbeat_status": "active",
            "stale_after_seconds": 90,
            "restart_count": 0,
        } if runtime_id == "runtime-042" else None
        store.get_rollbacks = lambda runtime_id: []
        store.dataset_source = lambda dataset: {
            "runtime_bindings": "local_snapshot",
            "telemetry_summaries": "local_snapshot",
            "paper_runtime_monitoring_sessions": "local_snapshot",
            "rollbacks": "local_snapshot",
        }.get(dataset, "missing")
        bff_main.read_store = store
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/runtime-state",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["page_info"]["next_page_token"] is None
            assert payload["meta"]["total"] == 1
            assert payload["meta"]["sort"] == {
                "sort_by": "last_updated_at",
                "sort_order": "desc",
            }
            assert payload["meta"]["surfaces"]["runtime_state"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["runtime_state"]["support_surface_status"] == {
                "runtime_roster": "degraded",
                "telemetry_summary": "degraded",
                "paper_runtime_monitoring": "degraded",
                "rollback_history": "degraded",
            }
            assert {
                surface["surface_key"]
                for surface in payload["meta"]["surfaces"]["runtime_state"]["degraded_support_surfaces"]
            } == {
                "runtime_roster",
                "telemetry_summary",
                "paper_runtime_monitoring",
                "rollback_history",
            }
            assert payload["meta"]["surfaces"]["runtime_roster"]["source"] == "local_snapshot"
            assert payload["meta"]["surfaces"]["telemetry_summary"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["paper_runtime_monitoring"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["rollback_history"]["status"] == "degraded"

            assert payload["runtimes"] == [
                {
                    "runtime_id": "runtime-042",
                    "runtime_binding_id": "runtime-042",
                    "deployment_stage": "paper",
                    "status": "idle",
                    "capital_pool_id": None,
                    "plan_ref": {
                        "plan_id": "plan-F-042",
                        "href": "/operator/deployment-review?plan=plan-F-042",
                    },
                    "artifact_ref": {
                        "artifact_id": "artifact-042",
                        "artifact_version": "v2.1.0",
                    },
                    "telemetry_summary": {
                        "window": "1h",
                        "collected_at": "2026-04-10T15:00:00Z",
                        "metrics": {
                            "pnl": -0.12,
                            "drawdown": 0.125,
                            "sharpe_ratio": -0.8,
                            "fill_rate": 0.94,
                            "avg_slippage_bps": 3.2,
                            "total_trades": 47,
                        },
                        "runtime_binding_id": "rtb-042",
                        "deployment_stage": "paper",
                        "state": "active",
                        "last_heartbeat_at": "2026-04-10T15:00:00Z",
                        "last_event_at": "2026-04-10T15:00:00Z",
                        "engine_bridge_repo": "ajoe734/pantheon-lean.git",
                        "engine_bridge_commit": "abc1234",
                        "health_summary": {
                            "paper_runtime": "ok",
                            "bridge": "ok",
                            "telemetry": "ok",
                            "broker": "not_applicable",
                        },
                        "projection_source": "telemetry_ingest",
                    },
                    "executed_trade_count": None,
                    "total_trades": 47,
                    "position_count": None,
                    "positions": None,
                    "last_fill": None,
                    "paper_runtime_monitoring": {
                        "session_id": "prmon-rtb-042",
                        "session_type": "paper_runtime_monitoring",
                        "binding_id": "runtime-042",
                        "runtime_binding_id": "runtime-042",
                        "runtime_id": "runtime-042",
                        "deployment_stage": "paper",
                        "status": "running",
                        "active": True,
                        "started_at": "2026-04-10T14:55:00Z",
                        "ended_at": None,
                        "last_heartbeat_at": "2026-04-10T15:00:00Z",
                        "heartbeat_status": "active",
                        "stale_after_seconds": 90,
                        "restart_count": 0,
                    },
                    "row_health": {
                        "status": "ok",
                        "checks": {
                            "runtime_binding": {
                                "status": "ok",
                                "source": "runtime_bindings",
                                "applies": True,
                            },
                            "telemetry_summary": {
                                "status": "ok",
                                "source": "telemetry_summaries",
                                "applies": True,
                            },
                            "paper_runtime_monitoring": {
                                "status": "ok",
                                "source": "paper_runtime_monitoring_sessions",
                                "applies": True,
                            },
                        },
                        "degraded_checks": [],
                    },
                    "rollback_summary": {
                        "count": 0,
                        "latest": None,
                        "href": "/api/v1/runtimes/runtime-042/rollbacks",
                    },
                    "last_updated_at": "2026-04-10T15:00:00Z",
                }
            ]
        finally:
            bff_main.read_store = original_store


def test_pkt010_runtime_state_board_surfaces_terminal_monitoring_session() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = create_in_memory_read_surface_ports()
        store.list_runtime_bindings = lambda: [
            {
                "id": "rtb-stale-001",
                "runtime_id": "runtime-stale-001",
                "deployment_stage": "paper",
                "status": "running",
                "plan_id": "plan-stale-001",
                "artifact_id": "artifact-stale-001",
                "artifact_version": "1.0.0",
            }
        ]
        store.get_telemetry_summary = lambda runtime_id: {
            "runtime_id": "runtime-stale-001",
            "runtime_binding_id": "rtb-stale-001",
            "deployment_stage": "paper",
            "state": "active",
            "window": "latest",
            "collected_at": "2026-06-09T00:10:00Z",
            "last_heartbeat_at": "2026-06-09T00:00:00Z",
            "last_event_at": "2026-06-09T00:00:00Z",
        } if runtime_id == "runtime-stale-001" else None
        store.get_paper_runtime_monitoring_session = lambda *, runtime_id=None, binding_id=None: {
            "session_id": "prmon-stale-001",
            "session_type": "paper_runtime_monitoring",
            "binding_id": "rtb-stale-001",
            "runtime_binding_id": "rtb-stale-001",
            "runtime_id": "runtime-stale-001",
            "deployment_stage": "paper",
            "status": "ended",
            "active": False,
            "started_at": "2026-06-09T00:00:00Z",
            "ended_at": "2026-06-09T00:10:00Z",
            "ended_reason": "stale_heartbeat",
            "last_heartbeat_at": "2026-06-09T00:00:00Z",
            "heartbeat_status": "active",
            "stale_after_seconds": 90,
            "restart_count": 1,
            "staleness": {
                "status": "stale",
                "reason": "stale_heartbeat",
                "last_known_at": "2026-06-09T00:00:00Z",
                "age_seconds": 600,
                "threshold_seconds": 90,
            },
        } if runtime_id == "runtime-stale-001" else None
        store.get_rollbacks = lambda runtime_id: []
        store.dataset_source = lambda dataset: {
            "runtime_bindings": "canonical",
            "telemetry_summaries": "service_client",
            "paper_runtime_monitoring_sessions": "service_client",
            "rollbacks": "local_snapshot",
        }.get(dataset, "missing")
        bff_main.read_store = store
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/runtime-state",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            runtime = response.json()["runtimes"][0]
            monitoring = runtime["paper_runtime_monitoring"]
            assert monitoring["active"] is False
            assert monitoring["ended_reason"] == "stale_heartbeat"
            assert monitoring["terminal_reason"] == "stale_heartbeat"
            assert monitoring["staleness"]["reason"] == "stale_heartbeat"
            assert runtime["row_health"]["status"] == "degraded"
            check = runtime["row_health"]["checks"]["paper_runtime_monitoring"]
            assert check["status"] == "degraded"
            assert "stale_heartbeat" in check["message"]
        finally:
            bff_main.read_store = original_store


def test_pkt010_runtime_state_board_keeps_healthy_rows_when_rollback_surface_unavailable() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = create_in_memory_read_surface_ports()
        runtime_bindings = [
            {
                "id": f"rtb-paper-{idx:03d}",
                "runtime_id": f"runtime-paper-{idx:03d}",
                "deployment_stage": "paper",
                "status": "running",
                "plan_id": f"plan-paper-{idx:03d}",
                "artifact_id": "artifact-paper",
                "artifact_version": "v1.0.0",
            }
            for idx in range(1, 16)
        ]
        telemetry_summaries = {
            binding["runtime_id"]: {
                "runtime_id": binding["runtime_id"],
                "runtime_binding_id": binding["id"],
                "deployment_stage": "paper",
                "state": "active",
                "window": "latest",
                "pnl": 0.01,
                "drawdown": 0.02,
                "sharpe_ratio": 1.2,
                "fill_rate": 0.99,
                "avg_slippage_bps": 0.8,
                "total_trades": 10,
                "collected_at": f"2026-06-06T12:{idx:02d}:00Z",
                "last_heartbeat_at": f"2026-06-06T12:{idx:02d}:00Z",
            }
            for idx, binding in enumerate(runtime_bindings, start=1)
        }
        monitoring_sessions = {
            binding["runtime_id"]: {
                "session_id": f"prmon-{binding['id']}",
                "session_type": "paper_runtime_monitoring",
                "binding_id": binding["id"],
                "runtime_binding_id": binding["id"],
                "runtime_id": binding["runtime_id"],
                "deployment_stage": "paper",
                "status": "running",
                "active": True,
                "started_at": "2026-06-06T12:00:00Z",
                "ended_at": None,
                "last_heartbeat_at": telemetry_summaries[binding["runtime_id"]][
                    "last_heartbeat_at"
                ],
                "heartbeat_status": "active",
                "stale_after_seconds": 90,
                "restart_count": 0,
            }
            for binding in runtime_bindings
        }

        def get_monitoring_session(*, runtime_id=None, binding_id=None):
            return monitoring_sessions.get(runtime_id)

        store.list_runtime_bindings = lambda: list(runtime_bindings)
        store.get_telemetry_summary = lambda runtime_id: telemetry_summaries.get(runtime_id)
        store.get_paper_runtime_monitoring_session = get_monitoring_session
        store.get_rollbacks = lambda runtime_id: []
        store.dataset_source = lambda dataset: {
            "runtime_bindings": "canonical",
            "telemetry_summaries": "service_client",
            "paper_runtime_monitoring_sessions": "service_client",
            "rollbacks": "missing",
        }.get(dataset, "missing")
        bff_main.read_store = store
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/runtime-state",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["meta"]["total"] == 15
            assert len(payload["runtimes"]) == 15
            assert all(row["row_health"]["status"] == "ok" for row in payload["runtimes"])
            assert all(
                row["row_health"]["checks"]["telemetry_summary"]["status"] == "ok"
                for row in payload["runtimes"]
            )
            assert payload["meta"]["surfaces"]["runtime_state"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["rollback_history"]["status"] == "unavailable"
            degraded_keys = {
                surface["surface_key"]
                for surface in payload["meta"]["surfaces"]["runtime_state"]["degraded_support_surfaces"]
            }
            assert degraded_keys == {"rollback_history"}
            assert payload["meta"]["surfaces"]["runtime_state"]["support_surface_status"] == {
                "runtime_roster": "ok",
                "telemetry_summary": "ok",
                "paper_runtime_monitoring": "ok",
                "rollback_history": "unavailable",
            }
        finally:
            bff_main.read_store = original_store


def test_pkt010_runtime_state_board_honest_mode_returns_unavailable_surface() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = create_in_memory_read_surface_ports()
        store.list_runtime_bindings = lambda: []
        store.get_telemetry_summary = lambda runtime_id: None
        store.get_rollbacks = lambda runtime_id: []
        store.dataset_source = lambda dataset: "missing"
        bff_main.read_store = store
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/runtime-state",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["runtimes"] == []
            assert payload["page_info"]["next_page_token"] is None
            assert payload["meta"]["total"] == 0
            assert payload["meta"]["surfaces"]["runtime_state"]["status"] == "unavailable"
            assert payload["meta"]["surfaces"]["runtime_roster"]["status"] == "unavailable"
            assert payload["meta"]["surfaces"]["runtime_roster"]["source"] == "missing"
            assert payload["meta"]["surfaces"]["telemetry_summary"]["status"] == "unavailable"
            assert payload["meta"]["surfaces"]["paper_runtime_monitoring"]["status"] == "unavailable"
            assert payload["meta"]["surfaces"]["rollback_history"]["status"] == "unavailable"
        finally:
            bff_main.read_store = original_store


def test_pkt010_runtime_state_board_supports_backend_owned_sorting_filtering_and_pagination() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = create_in_memory_read_surface_ports()
        runtime_bindings = [
            {
                "id": "runtime-100",
                "runtime_id": "runtime-100",
                "deployment_stage": "paper",
                "status": "running",
                "plan_id": "plan-paper-100",
                "artifact_id": "artifact-paper-100",
                "artifact_version": "v1.0.0",
            },
            {
                "id": "runtime-200",
                "runtime_id": "runtime-200",
                "deployment_stage": "live",
                "status": "running",
                "plan_id": "plan-live-200",
                "artifact_id": "artifact-live-200",
                "artifact_version": "v2.0.0",
            },
        ]
        telemetry_summaries = {
            "runtime-100": {
                "runtime_id": "runtime-100",
                "window": "1h",
                "pnl": 0.04,
                "drawdown": 0.01,
                "sharpe_ratio": 1.5,
                "total_trades": 12,
                "fill_rate": 0.98,
                "avg_slippage_bps": 1.1,
                "collected_at": "2026-04-18T04:10:00Z",
            },
            "runtime-200": {
                "runtime_id": "runtime-200",
                "window": "1h",
                "pnl": 0.12,
                "drawdown": 0.02,
                "sharpe_ratio": 2.1,
                "total_trades": 21,
                "fill_rate": 0.99,
                "avg_slippage_bps": 0.9,
                "collected_at": "2026-04-18T05:15:00Z",
            },
        }
        rollbacks = {
            "runtime-100": [],
            "runtime-200": [
                {
                    "id": "rb-200",
                    "runtime_id": "runtime-200",
                    "action_type": "rollback",
                    "from_version": "v2.0.0",
                    "to_version": "v1.9.0",
                    "status": "completed",
                    "initiated_at": "2026-04-18T05:00:00Z",
                    "completed_at": "2026-04-18T05:02:00Z",
                }
            ],
        }
        store.list_runtime_bindings = lambda: list(runtime_bindings)
        store.get_telemetry_summary = lambda runtime_id: telemetry_summaries.get(runtime_id)
        store.get_rollbacks = lambda runtime_id: list(rollbacks.get(runtime_id, []))
        store.dataset_source = lambda dataset: {
            "runtime_bindings": "local_snapshot",
            "telemetry_summaries": "local_snapshot",
            "rollbacks": "local_snapshot",
        }.get(dataset, "missing")
        bff_main.read_store = store
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/runtime-state",
                params={
                    "deployment_stage": "paper,live",
                    "status": "running",
                    "sort_by": "last_updated_at",
                    "sort_order": "desc",
                    "page_size": 1,
                },
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["meta"]["total"] == 2
            assert payload["page_info"]["next_page_token"] == "1"
            assert payload["runtimes"][0]["runtime_id"] == "runtime-200"
            assert payload["runtimes"][0]["last_updated_at"] == "2026-04-18T05:15:00Z"
            assert payload["runtimes"][0]["rollback_summary"]["latest"] == {
                "rollback_id": "rb-200",
                "action_type": "rollback",
                "status": "completed",
                "from_version": "v2.0.0",
                "to_version": "v1.9.0",
                "initiated_at": "2026-04-18T05:00:00Z",
                "completed_at": "2026-04-18T05:02:00Z",
            }
        finally:
            bff_main.read_store = original_store


def test_pkt010_runtime_state_board_reads_runtime_summary_from_telemetry_service(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "http://telemetry.test")
        monkeypatch.setenv("PANTHEON_PAPER_FLEET_RECONCILER_URL", "http://paper-fleet.test")

        def fake_http_json_get(base_url, path, *, headers=None):
            if base_url == "http://telemetry.test" and path == "/api/telemetry/runtime-summaries":
                return True, {
                    "summaries": [
                        {
                            "runtime_id": "runtime-paper-001",
                            "runtime_binding_id": "rtb-paper-001",
                            "deployment_stage": "paper",
                            "state": "active",
                            "window": "latest",
                            "collected_at": "2026-05-01T00:00:30Z",
                            "last_heartbeat_at": "2026-05-01T00:00:30Z",
                            "last_event_at": "2026-05-01T00:00:30Z",
                            "engine_bridge_repo": "ajoe734/pantheon-lean.git",
                            "engine_bridge_commit": "abc1234",
                            "health_summary": {
                                "paper_runtime": "ok",
                                "bridge": "ok",
                                "telemetry": "ok",
                                "broker": "not_applicable",
                            },
                        }
                    ]
                }
            if base_url == "http://paper-fleet.test" and path == "/api/fleet/state":
                return True, {
                    "monitoring_sessions": [
                        {
                            "session_id": "prmon-rtb-paper-001",
                            "session_type": "paper_runtime_monitoring",
                            "binding_id": "rtb-paper-001",
                            "runtime_binding_id": "rtb-paper-001",
                            "runtime_id": "runtime-paper-001",
                            "deployment_stage": "paper",
                            "status": "running",
                            "active": True,
                            "started_at": "2026-05-01T00:00:00Z",
                            "ended_at": None,
                            "last_heartbeat_at": "2026-05-01T00:00:30Z",
                            "heartbeat_status": "active",
                            "stale_after_seconds": 90,
                            "restart_count": 0,
                        }
                    ],
                    "monitoring_session_count": 1,
                }
            return False, None

        monkeypatch.setattr(bff_read_store, "_http_json_get", fake_http_json_get)
        store = create_in_memory_read_surface_ports()
        store.list_runtime_bindings = lambda: [
            {
                "id": "rtb-paper-001",
                "runtime_id": "runtime-paper-001",
                "deployment_stage": "paper",
                "status": "running",
                "plan_id": "plan-paper-001",
                "artifact_id": "artifact-paper-001",
                "artifact_version": "1.0.0",
            }
        ]
        store.get_rollbacks = lambda runtime_id: []
        store.dataset_source = lambda dataset: {
            "runtime_bindings": "canonical",
            "telemetry_summaries": "service_client",
            "paper_runtime_monitoring_sessions": "service_client",
            "rollbacks": "local_snapshot",
        }.get(dataset, "missing")
        bff_main.read_store = store
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/runtime-state",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            runtime = response.json()["runtimes"][0]
            assert runtime["runtime_binding_id"] == "rtb-paper-001"
            assert runtime["deployment_stage"] == "paper"
            assert runtime["telemetry_summary"]["last_heartbeat_at"] == "2026-05-01T00:00:30Z"
            assert runtime["telemetry_summary"]["runtime_binding_id"] == "rtb-paper-001"
            assert runtime["telemetry_summary"]["engine_bridge_repo"] == "ajoe734/pantheon-lean.git"
            assert runtime["telemetry_summary"]["engine_bridge_commit"] == "abc1234"
            assert runtime["paper_runtime_monitoring"]["session_id"] == "prmon-rtb-paper-001"
            assert runtime["paper_runtime_monitoring"]["active"] is True
            assert runtime["paper_runtime_monitoring"]["last_heartbeat_at"] == "2026-05-01T00:00:30Z"
            assert response.json()["meta"]["surfaces"]["paper_runtime_monitoring"]["source"] == "service_client"
        finally:
            bff_main.read_store = original_store
