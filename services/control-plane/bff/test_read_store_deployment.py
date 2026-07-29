#!/usr/bin/env python3
"""Unit test for F-042 deployment-review read-store helpers.

This test avoids FastAPI so it can run in lightweight environments while still
verifying that the Promotion Review payload can be composed from:

- seeded read-model data
- canonical governance/runtime snapshot files when they exist
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from read_store import ReadSurfaceStore


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_seeded_review_summary():
    with tempfile.TemporaryDirectory() as td:
        store_path = os.path.join(td, "read_surfaces.json")
        store = ReadSurfaceStore(store_path, allow_local_snapshot_fallback=True)

        plan = store.get_deployment_plan("plan-F-042")
        assert plan is not None
        assert plan["approval_decision_id"] == "approval-042"

        review = store.get_review_summary("plan-F-042")
        assert review["riskSummary"] == "No unresolved severity-1 or severity-2 incidents."
        assert review["governanceOutcome"] == "approved"
        assert review["decisionState"] == "decided"
        assert review["reviewer"] == "governance"

        actions = store.get_allowed_actions("plan-F-042")
        assert actions["canPromoteToPaper"] is True

        print("✅ Seeded F-042 review summary exposes governanceOutcome and CTA shaping")


def test_canonical_overlay():
    tracked_env = {
        "PANTHEON_GOVERNANCE_DATA_DIR": os.environ.get("PANTHEON_GOVERNANCE_DATA_DIR"),
        "PANTHEON_RUNTIME_DATA_DIR": os.environ.get("PANTHEON_RUNTIME_DATA_DIR"),
    }

    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            governance_dir = root / "governance"
            runtime_dir = root / "runtime"

            _write_json(
                governance_dir / "deployment_plans.json",
                {
                    "plan-live-001": {
                        "plan_id": "plan-live-001",
                        "approval_decision_id": "approval-live-001",
                        "artifact_id": "artifact-live-001",
                        "artifact_version": "v3.2.1",
                        "artifact_type": "strategy_bundle",
                        "strategy_id": "strat-live-001",
                        "capital_pool_id": "pool-live-001",
                        "current_stage": "none",
                        "target_stage": "paper",
                        "transition_type": "activate",
                        "runtime_action": "deploy_new_binding",
                        "status": "approved",
                        "created_at": "2026-04-11T10:00:00Z",
                        "binding_id": "pcb-live-001",
                    }
                },
            )
            _write_json(
                governance_dir / "approval_decisions.json",
                {
                    "approval-live-001": {
                        "decision_id": "approval-live-001",
                        "decision": "approved",
                        "decision_state": "decided",
                        "actor_id": "risk-committee",
                        "actor_role": "reviewer",
                        "risk_level": "medium",
                        "decided_at": "2026-04-11T10:05:00Z",
                        "rationale": "Promotion to paper is within policy.",
                    }
                },
            )
            _write_json(
                governance_dir / "deployment_sagas.json",
                {
                    "sagas": {
                        "deployment-saga-plan-live-001": {
                            "saga_id": "deployment-saga-plan-live-001",
                            "plan_id": "plan-live-001",
                            "approval_decision_id": "approval-live-001",
                            "strategy_id": "strat-live-001",
                            "artifact_id": "artifact-live-001",
                            "artifact_version": "v3.2.1",
                            "capital_pool_id": "pool-live-001",
                            "current_stage": "none",
                            "target_stage": "paper",
                            "runtime_action": "deploy_new_binding",
                            "rollback_action_type": "replace",
                            "status": "awaiting_binding",
                            "current_step": "binding_requested",
                            "trace_id": "trace-live-001",
                            "created_at": "2026-04-11T10:00:00Z",
                            "updated_at": "2026-04-11T10:15:00Z",
                            "last_sequence_no": 1,
                            "last_event_id": "deployment-saga-plan-live-001-evt-0001",
                            "history": [
                                {
                                    "step": "binding_requested",
                                    "status": "awaiting_binding",
                                    "event_id": "deployment-saga-plan-live-001-evt-0001",
                                    "sequence_no": 1,
                                    "emitted_at": "2026-04-11T10:00:00Z",
                                }
                            ],
                        }
                    },
                    "outbox": [
                        {
                            "owner_service": "deployment-orchestrator",
                            "event": {
                                "event_id": "deployment-saga-plan-live-001-evt-0001",
                                "event_type": "runtime.binding.requested",
                                "aggregate_type": "deployment_saga",
                                "aggregate_id": "deployment-saga-plan-live-001",
                                "sequence_no": 1,
                                "causal_parent_id": None,
                                "event_time": "2026-04-11T10:00:00Z",
                                "emitted_at": "2026-04-11T10:00:00Z",
                                "trace_id": "trace-live-001",
                                "idempotency_key": "deployment-saga-plan-live-001:1:runtime.binding.requested",
                                "payload": {"plan_id": "plan-live-001"},
                            },
                            "status": "dead_lettered",
                            "delivery_attempts": 3,
                            "last_error": "runtime-manager unavailable",
                            "last_attempt_at": "2026-04-11T10:14:00Z",
                            "blocked_reason": "runtime-manager unavailable",
                            "dlq_at": "2026-04-11T10:15:00Z",
                            "replay_count": 0,
                            "retry_policy": {
                                "consumer_name": "deployment-outbox-consumer",
                                "retryable": True,
                                "max_attempts": 3,
                                "retry_delay_seconds": 30,
                            },
                        }
                    ],
                    "inbox": [],
                },
            )
            _write_json(
                governance_dir / "capital_pools.json",
                [
                    {
                        "pool_id": "pool-live-001",
                        "name": "Live Pool",
                        "owner_id": "desk-crypto",
                        "owner_type": "desk",
                        "status": "active",
                        "created_at": "2026-04-01T00:00:00Z",
                        "single_runtime_enforced": True,
                    }
                ],
            )
            _write_json(
                governance_dir / "persona_capital_bindings.json",
                [
                    {
                        "binding_id": "pcb-live-001",
                        "persona_id": "persona-live-001",
                        "capital_pool_id": "pool-live-001",
                        "role": "paper_owner",
                        "status": "active",
                        "approval_decision_id": "approval-live-001",
                        "allowed_deployment_scope": "paper",
                    }
                ],
            )
            _write_json(
                runtime_dir / "runtime_bindings.json",
                [
                    {
                        "binding_id": "rb-live-001",
                        "runtime_id": "runtime-live-001",
                        "capital_pool_id": "pool-live-001",
                        "artifact_id": "artifact-live-001",
                        "artifact_version": "v3.2.1",
                        "deployment_stage": "paper",
                        "deployment_mode": "paper",
                        "effective_at": "2026-04-11T10:10:00Z",
                        "status": "active",
                        "plan_id": "plan-live-001",
                        "persona_capital_binding_id": "pcb-live-001",
                    }
                ],
            )

            os.environ["PANTHEON_GOVERNANCE_DATA_DIR"] = str(governance_dir)
            os.environ["PANTHEON_RUNTIME_DATA_DIR"] = str(runtime_dir)

            store_path = os.path.join(td, "read_surfaces.json")
            store = ReadSurfaceStore(store_path, allow_local_snapshot_fallback=False)

            plan = store.get_deployment_plan("plan-live-001")
            assert plan is not None
            assert plan["strategy_id"] == "strat-live-001"
            assert plan["runtime_binding_id"] == "rb-live-001"
            assert plan["current_stage"] == "none"
            assert plan["target_stage"] == "paper"
            assert plan["deployment_saga_id"] == "deployment-saga-plan-live-001"
            assert plan["saga_progress_status"] == "blocked"
            assert plan["blocked_reason"] == "runtime-manager unavailable"
            assert plan["dlq_count"] == 1
            assert plan["retry_state"][0]["status"] == "dead_lettered"
            assert plan["saga_progress"]["retry_policy"]["max_attempts"] == 3

            decision = store.get_approval_decision("approval-live-001")
            assert decision is not None
            assert decision["outcome"] == "approved"
            assert decision["reviewer"] == "risk-committee"

            pool = store.get_capital_pool("pool-live-001")
            assert pool is not None
            assert pool["single_runtime_enforced"] is True

            bindings = store.get_bindings_for_pool("pool-live-001")
            assert len(bindings) == 1
            assert bindings[0]["id"] == "pcb-live-001"

            runtime = store.get_runtime_binding("rb-live-001")
            assert runtime is not None
            assert runtime["deployment_stage"] == "paper"
            assert runtime["plan_id"] == "plan-live-001"
            assert runtime["persona_id"] == "persona-live-001"

            runtime_list = store.list_runtime_bindings()
            assert runtime_list[0]["persona_id"] == "persona-live-001"

            review = store.get_review_summary("plan-live-001")
            assert review["governanceOutcome"] == "approved"
            assert review["decisionState"] == "decided"
            assert review["reviewer"] == "risk-committee"
            assert review["riskSummary"] == "Approval decision risk level: medium."

            actions = store.get_allowed_actions("plan-live-001")
            assert actions["canPromoteToPaper"] is True

            print("✅ Canonical overlay composes F-042 inputs from governance/runtime snapshots")
    finally:
        for key, value in tracked_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_missing_reference_query_count_regression():
    tracked_env = {
        "PANTHEON_GOVERNANCE_DATA_DIR": os.environ.get("PANTHEON_GOVERNANCE_DATA_DIR"),
        "PANTHEON_RUNTIME_DATA_DIR": os.environ.get("PANTHEON_RUNTIME_DATA_DIR"),
    }

    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            governance_dir = root / "governance"
            runtime_dir = root / "runtime"

            # 2 plans pointing to absent decisions
            _write_json(
                governance_dir / "deployment_plans.json",
                {
                    "plan-missing-1": {
                        "plan_id": "plan-missing-1",
                        "approval_decision_id": "approval-absent-1",
                        "status": "pending_review",
                        "target_stage": "paper",
                        "current_stage": "none",
                        "created_at": "2026-04-11T10:00:00Z",
                    },
                    "plan-missing-2": {
                        "plan_id": "plan-missing-2",
                        "approval_decision_id": "approval-absent-2",
                        "status": "pending_review",
                        "target_stage": "paper",
                        "current_stage": "none",
                        "created_at": "2026-04-11T10:00:00Z",
                    }
                },
            )
            # Empty approval decisions, so approval-absent-1 and approval-absent-2 are not found
            _write_json(governance_dir / "approval_decisions.json", {})

            os.environ["PANTHEON_GOVERNANCE_DATA_DIR"] = str(governance_dir)
            os.environ["PANTHEON_RUNTIME_DATA_DIR"] = str(runtime_dir)

            store_path = os.path.join(td, "read_surfaces.json")
            store = ReadSurfaceStore(store_path, allow_local_snapshot_fallback=False)

            # Spy on get_approval_decision
            original_get_approval_decision = store.get_approval_decision
            call_count = 0
            def spied_get_approval_decision(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                return original_get_approval_decision(*args, **kwargs)
            store.get_approval_decision = spied_get_approval_decision

            # Call list_governance_review_queue_items
            items = store.list_governance_review_queue_items()
            assert len(items) == 2
            
            # Assert that no redundant single get_approval_decision calls were made (regression count is 0)
            assert call_count == 0, f"Expected 0 calls to get_approval_decision, but got {call_count}"

            print("✅ Regression test: missing-reference query-count is 0")
    finally:
        for key, value in tracked_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    test_seeded_review_summary()
    test_canonical_overlay()
    test_missing_reference_query_count_regression()
    print("\n" + "=" * 50)
    print("Deployment review read_store tests: ALL PASSED")
