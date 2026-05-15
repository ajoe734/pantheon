from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


OPERATOR_TOKEN = "Bearer op-2:operator"


def test_pkt004_deployment_approval_drilldowns_filters_follow_canonical_contract() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
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
