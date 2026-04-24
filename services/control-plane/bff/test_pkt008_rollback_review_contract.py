#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from read_store import ReadSurfaceStore


def test_pkt008_rollback_review_seed_contract() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
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
