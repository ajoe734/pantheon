"""Unit tests for Alpha Replication Controller."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from services.research.alpha_replication.controller_state import ControllerState, ControllerStateStore
from services.research.alpha_replication.queue import AlphaReplicationQueue
from services.research.alpha_replication.replication_controller import (
    ReplicationControllerConfig,
    run_controller_tick,
)


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def test_run_controller_tick_success(temp_dir):
    # 1. Create a dummy seeds file
    seed_store_path = temp_dir / "distill_seeds.jsonl"
    seed_store_path.write_text(
        json.dumps({"source_id": "strat-001"}) + "\n" +
        json.dumps({"source_id": "strat-002"}) + "\n"
    )

    # 2. Setup controller state
    state_path = temp_dir / "state.json"
    state = ControllerState(
        controller_id="test-controller",
        controller_name="test-replication-controller",
        environment="test",
        tenant_id="default",
        deployment={"git_sha": "test-sha"},
    )
    store = ControllerStateStore(state_path)
    store.save(state)

    # 3. Setup configuration
    config = ReplicationControllerConfig(
        database_url=None,
        registry_url="http://mock-registry:8087",
        interval_seconds=10,
        max_ticks=1,
        state_path=state_path,
        data_dir=temp_dir,
        seed_store_path=seed_store_path,
    )

    # 4. Mock registry responses
    mock_entry_view1 = {
        "entry": {
            "registry_id": "reg-strategy-spec-strat-001",
            "strategy_id": "strat-001",
            "version": "1.0",
            "artifact_state": "approved",
            "metadata": {
                "strategy_spec": {
                    "spec_version": "1.0",
                    "strategy_id": "strat-001",
                    "title": "Strategy 001",
                    "hypothesis": "Hypothesis 001",
                    "objective": "Objective 001",
                    "lifecycle_state": "approved",
                    "market_scope": {
                        "symbols": ["SPY"],
                        "asset_classes": ["equity"],
                        "frequency": "1d",
                        "venues": ["NYSE"]
                    },
                    "data_dependencies": [{"ref": "ds1", "kind": "dataset"}],
                    "execution_profile": {
                        "signal_schema_version": "1.0",
                        "quantity_type": "PERCENT_PORTFOLIO",
                        "rebalance_cadence": "1d",
                        "execution_mode_hint": "research"
                    },
                    "evaluation_plan": {
                        "metrics": ["sharpe_ratio"],
                        "candidate_gate": "Gate pass.",
                        "paper_gate": "Paper gate.",
                        "live_gate": "Live gate."
                    },
                    "governance": {
                        "approval_required": True,
                        "policy_id": "policy-1",
                        "risk_profile": "research_only"
                    },
                    "provenance": {
                        "source_kind": "workflow",
                        "created_at": "2026-05-17T11:10:00Z",
                        "source_refs": ["source:1"],
                        "created_by": "Codex"
                    }
                }
            }
        }
    }

    # Mock _get_registry_entry helper
    with mock.patch(
        "services.research.alpha_replication.replication_controller._get_registry_entry"
    ) as mock_get:
        def side_effect(registry_url, registry_id):
            if registry_id == "reg-strategy-spec-strat-001":
                return mock_entry_view1
            return None  # strat-002 not approved or missing
        
        mock_get.side_effect = side_effect

        # Mock LoopControllerWriter to check interactions
        mock_writer = mock.MagicMock()

        # Run tick in stub mode (default worker mode in test setup)
        with mock.patch.dict(
            "os.environ", {"PANTHEON_ALPHA_REVALIDATION_DISPATCH_MODE": "stub"}
        ):
            res = run_controller_tick(
                config=config,
                state=state,
                store=store,
                writer=mock_writer,
            )

        # Assertions
        assert res["total_successes"] == 1
        assert res["desired_state"]["approved_spec_count"] == 1
        assert res["reconcile"]["enqueued_new"] == 1
        assert res["reconcile"]["processed"] == 1
        assert len(res["reconcile"]["created_run_ids"]) == 1
        assert res["actual_readback"]["queue_revalidated"] == 1

        # Check queue
        queue = AlphaReplicationQueue(temp_dir)
        entries = queue.list_all()
        assert len(entries) == 1
        assert entries[0]["strategy_id"] == "strat-001"


def test_run_controller_tick_registry_failure(temp_dir):
    state_path = temp_dir / "state.json"
    state = ControllerState(
        controller_id="test-controller",
        controller_name="test-replication-controller",
        environment="test",
        tenant_id="default",
        deployment={"git_sha": "test-sha"},
    )
    store = ControllerStateStore(state_path)
    store.save(state)

    config = ReplicationControllerConfig(
        database_url=None,
        registry_url="http://mock-registry:8087",
        interval_seconds=10,
        max_ticks=1,
        state_path=state_path,
        data_dir=temp_dir,
        seed_store_path=temp_dir / "non-existent-seeds.jsonl",
    )

    # Mock _get_registry_entry to raise exception
    with mock.patch(
        "services.research.alpha_replication.replication_controller._get_registry_entry",
        side_effect=RuntimeError("Registry internal error")
    ):
        mock_writer = mock.MagicMock()
        
        # In this test, we scan no seeds, so it succeeds with 0 processed
        res = run_controller_tick(
            config=config,
            state=state,
            store=store,
            writer=mock_writer,
        )
        assert res["total_successes"] == 1
        assert res["desired_state"]["approved_spec_count"] == 0
