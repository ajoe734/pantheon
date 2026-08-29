#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from ports import create_in_memory_read_surface_ports


def test_pkt008_rollback_review_seed_contract() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = create_in_memory_read_surface_ports(
            lifecycle_telemetry_governance_kwargs={
                "rollback_reviews": {
                    "rollback-rb-001": {
                        "rollback_id": "rollback-rb-001",
                        "allowedActions": {
                            "canApproveRollback": False,
                            "canRejectRollback": True,
                        },
                        "position_impact": [
                            {"position_data_stale": False, "position_impact_summary": "closed"},
                            {"position_data_stale": False, "position_impact_summary": "neutral"},
                            {"position_data_stale": True, "position_impact_summary": None},
                        ],
                        "meta": {"surfaces": {"position_data": {"status": "degraded"}}},
                    }
                }
            }
        )
        review = store.get_rollback_review("rollback-rb-001")
        assert review is not None
        assert review["rollback_id"] == "rollback-rb-001"
        assert review["allowedActions"]["canApproveRollback"] is False
        assert review["allowedActions"]["canRejectRollback"] is True
        assert len(review["position_impact"]) == 3
        assert review["position_impact"][2]["position_data_stale"] is True
        assert review["position_impact"][2]["position_impact_summary"] is None
        assert review["meta"]["surfaces"]["position_data"]["status"] == "degraded"
