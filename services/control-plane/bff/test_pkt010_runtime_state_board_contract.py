from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


OPERATOR_TOKEN = "Bearer op-2:operator"


def test_pkt010_runtime_state_board_returns_contract_payload() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
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
        store.get_rollbacks = lambda runtime_id: []
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
            assert payload["meta"]["surfaces"]["runtime_roster"]["source"] == "local_snapshot"
            assert payload["meta"]["surfaces"]["telemetry_summary"]["status"] == "degraded"
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


def test_pkt010_runtime_state_board_honest_mode_returns_unavailable_surface() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
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
            assert payload["meta"]["surfaces"]["rollback_history"]["status"] == "unavailable"
        finally:
            bff_main.read_store = original_store


def test_pkt010_runtime_state_board_supports_backend_owned_sorting_filtering_and_pagination() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
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
