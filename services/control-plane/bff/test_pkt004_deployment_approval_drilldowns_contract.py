from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


OPERATOR_TOKEN = "Bearer op-2:operator"


def test_pkt004_deployment_approval_drilldowns_filters_follow_canonical_contract() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = create_in_memory_read_surface_ports()
        plan_records = [
            {
                "id": "plan-F-042",
                "plan_id": "plan-F-042",
                "status": "approved",
                "capital_pool_id": "pool-main",
                "approval_decision_id": "approval-042",
            }
        ]
        decision_records = [
            {
                "id": "approval-042",
                "decision_id": "approval-042",
                "outcome": "approved",
                "state": "decided",
            }
        ]
        store.list_deployment_plans = lambda **kwargs: [
            plan
            for plan in plan_records
            if (not kwargs.get("status") or plan["status"] == kwargs["status"])
            and (
                not kwargs.get("capital_pool_id")
                or plan["capital_pool_id"] == kwargs["capital_pool_id"]
            )
        ]
        store.get_deployment_plan = lambda plan_id: next(
            (plan for plan in plan_records if plan["plan_id"] == plan_id), None
        )
        store.list_approval_decisions = lambda **kwargs: [
            decision
            for decision in decision_records
            if (not kwargs.get("outcome") or decision["outcome"] == kwargs["outcome"])
            and (not kwargs.get("state") or decision["state"] == kwargs["state"])
        ]
        store.get_approval_decision = lambda decision_id: next(
            (decision for decision in decision_records if decision["id"] == decision_id), None
        )
        bff_main.read_store = store
        client = TestClient(bff_main.app)

        try:
            headers = {"Authorization": OPERATOR_TOKEN}

            plans = client.get(
                "/api/v1/deployment-plans?status=approved&capital_pool_id=pool-main",
                headers=headers,
            )
            assert plans.status_code == 200, plans.text
            plan_payload = plans.json()
            assert plan_payload["meta"]["total"] == 1
            assert plan_payload["data"][0]["plan_id"] == "plan-F-042"
            assert plan_payload["data"][0]["status"] == "approved"
            assert plan_payload["data"][0]["capital_pool_id"] == "pool-main"

            no_plans = client.get(
                "/api/v1/deployment-plans?status=rejected&capital_pool_id=pool-main",
                headers=headers,
            )
            assert no_plans.status_code == 200, no_plans.text
            assert no_plans.json()["meta"]["total"] == 0

            decisions = client.get(
                "/api/v1/approval-decisions?outcome=approved&state=decided",
                headers=headers,
            )
            assert decisions.status_code == 200, decisions.text
            decision_payload = decisions.json()
            assert decision_payload["meta"]["total"] == 1
            assert decision_payload["data"][0]["outcome"] == "approved"
            assert decision_payload["data"][0]["state"] == "decided"

            no_decisions = client.get(
                "/api/v1/approval-decisions?outcome=approved&state=pending",
                headers=headers,
            )
            assert no_decisions.status_code == 200, no_decisions.text
            assert no_decisions.json()["meta"]["total"] == 0

            plan_detail = client.get(
                "/api/v1/deployment-plans/plan-F-042",
                headers=headers,
            )
            assert plan_detail.status_code == 200, plan_detail.text
            plan_detail_payload = plan_detail.json()
            assert plan_detail_payload["data"]["plan_id"] == "plan-F-042"
            assert plan_detail_payload["data"]["approval_decision"]["id"] == "approval-042"

            decision_detail = client.get(
                "/api/v1/approval-decisions/approval-042",
                headers=headers,
            )
            assert decision_detail.status_code == 200, decision_detail.text
            assert decision_detail.json()["data"]["id"] == "approval-042"
        finally:
            bff_main.read_store = original_store
