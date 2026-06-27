"""Unit tests for fleet_desired_state module.

Verifies the desired-state query contract for LOOP-AUTO-RT-001:
  - Active paper bindings are included as desired fleet members
  - Active canary bindings are included as desired fleet members
  - Live, frozen, retired, failed, pending_pause, paused bindings are excluded
  - Policy envelope is correct per stage and status
  - Stage filter narrows results without mutating the set
  - Query output is deterministic (idempotent on same input)
  - to_dict shape matches the HTTP response contract
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from fleet_desired_state import (
    FLEET_ELIGIBLE_STATUS,
    FLEET_EXCLUDED_STATUSES,
    FLEET_MANAGED_STAGES,
    ExcludedBinding,
    FleetDesiredStateQueryError,
    FleetDesiredState,
    PolicyEnvelope,
    build_fleet_desired_state,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _b(
    binding_id: str = "rb-1",
    runtime_id: str = "rt-1",
    capital_pool_id: str = "pool-1",
    deployment_mode: str = "paper",
    status: str = "active",
    plan_id: str = "plan-1",
    artifact_id: str = "art-1",
    artifact_version: str = "1.0.0",
    persona_capital_binding_id: str = "pcb-1",
    effective_at: str = "2026-01-01T00:00:00Z",
    **extra,
) -> dict:
    d = {
        "binding_id": binding_id,
        "runtime_id": runtime_id,
        "capital_pool_id": capital_pool_id,
        "deployment_mode": deployment_mode,
        "status": status,
        "plan_id": plan_id,
        "artifact_id": artifact_id,
        "artifact_version": artifact_version,
        "persona_capital_binding_id": persona_capital_binding_id,
        "effective_at": effective_at,
    }
    d.update(extra)
    return d


# ---------------------------------------------------------------------------
# Tests — fleet membership
# ---------------------------------------------------------------------------

class TestFleetMembership(unittest.TestCase):
    def test_active_paper_is_desired(self):
        state = build_fleet_desired_state([_b(deployment_mode="paper", status="active")])
        self.assertEqual(state.active_count, 1)
        self.assertEqual(state.bindings[0].deployment_mode, "paper")
        self.assertEqual(state.bindings[0].status, "active")
        self.assertEqual(state.excluded_count, 0)

    def test_active_canary_is_desired(self):
        state = build_fleet_desired_state([_b(deployment_mode="canary", status="active")])
        self.assertEqual(state.active_count, 1)
        self.assertEqual(state.bindings[0].deployment_mode, "canary")
        self.assertEqual(state.excluded_count, 0)

    def test_active_live_is_excluded(self):
        state = build_fleet_desired_state([_b(deployment_mode="live", status="active")])
        self.assertEqual(state.active_count, 0)
        self.assertEqual(state.excluded_count, 1)
        self.assertEqual(state.excluded[0].exclusion_reason, "non_fleet_stage")

    def test_active_frozen_is_excluded(self):
        state = build_fleet_desired_state([_b(deployment_mode="frozen", status="active")])
        self.assertEqual(state.active_count, 0)
        self.assertEqual(state.excluded[0].exclusion_reason, "non_fleet_stage")

    def test_retired_paper_is_excluded_terminal(self):
        state = build_fleet_desired_state([_b(status="retired")])
        self.assertEqual(state.active_count, 0)
        self.assertEqual(state.excluded[0].exclusion_reason, "terminal_status")

    def test_failed_paper_is_excluded_terminal(self):
        state = build_fleet_desired_state([_b(status="failed")])
        self.assertEqual(state.excluded[0].exclusion_reason, "terminal_status")

    def test_pending_pause_is_excluded_draining(self):
        state = build_fleet_desired_state([_b(status="pending_pause")])
        self.assertEqual(state.excluded[0].exclusion_reason, "draining")

    def test_paused_is_excluded_draining(self):
        state = build_fleet_desired_state([_b(status="paused")])
        self.assertEqual(state.excluded[0].exclusion_reason, "draining")

    def test_empty_input(self):
        state = build_fleet_desired_state([])
        self.assertEqual(state.active_count, 0)
        self.assertEqual(state.excluded_count, 0)

    def test_mixed_bindings_counted_correctly(self):
        bindings = [
            _b(binding_id="rb-1", deployment_mode="paper", status="active"),
            _b(binding_id="rb-2", deployment_mode="canary", status="active"),
            _b(binding_id="rb-3", deployment_mode="paper", status="retired"),
            _b(binding_id="rb-4", deployment_mode="live", status="active"),
            _b(binding_id="rb-5", deployment_mode="paper", status="paused"),
        ]
        state = build_fleet_desired_state(bindings)
        self.assertEqual(state.active_count, 2)
        desired_ids = {b.binding_id for b in state.bindings}
        self.assertEqual(desired_ids, {"rb-1", "rb-2"})
        self.assertEqual(state.excluded_count, 3)


# ---------------------------------------------------------------------------
# Tests — stage filter
# ---------------------------------------------------------------------------

class TestStageFilter(unittest.TestCase):
    def setUp(self):
        self.bindings = [
            _b(binding_id="rb-p1", deployment_mode="paper"),
            _b(binding_id="rb-p2", deployment_mode="paper"),
            _b(binding_id="rb-c1", deployment_mode="canary"),
        ]

    def test_paper_filter(self):
        state = build_fleet_desired_state(self.bindings, stage_filter="paper")
        self.assertEqual(state.active_count, 2)
        for b in state.bindings:
            self.assertEqual(b.deployment_mode, "paper")

    def test_canary_filter(self):
        state = build_fleet_desired_state(self.bindings, stage_filter="canary")
        self.assertEqual(state.active_count, 1)
        self.assertEqual(state.bindings[0].binding_id, "rb-c1")

    def test_stage_filter_sets_fleet_stages(self):
        state = build_fleet_desired_state(self.bindings, stage_filter="paper")
        self.assertEqual(state.fleet_stages, ["paper"])

    def test_no_filter_includes_all_managed_stages(self):
        state = build_fleet_desired_state(self.bindings)
        self.assertEqual(sorted(state.fleet_stages), ["canary", "paper"])

    def test_stage_filter_silently_skips_non_matching(self):
        # With paper filter, canary binding is silently skipped — not in excluded
        state = build_fleet_desired_state(self.bindings, stage_filter="paper")
        excluded_ids = {e.binding_id for e in state.excluded}
        self.assertNotIn("rb-c1", excluded_ids)

    def test_invalid_stage_filter_is_rejected(self):
        with self.assertRaisesRegex(FleetDesiredStateQueryError, "stage_filter"):
            build_fleet_desired_state(self.bindings, stage_filter="live")


# ---------------------------------------------------------------------------
# Tests — policy envelope
# ---------------------------------------------------------------------------

class TestPolicyEnvelope(unittest.TestCase):
    def test_eligible_paper_envelope(self):
        state = build_fleet_desired_state([_b(deployment_mode="paper")])
        env = state.bindings[0].policy_envelope
        self.assertEqual(env.stage, "paper")
        self.assertTrue(env.fleet_eligible)
        self.assertIsNone(env.exclusion_reason)

    def test_eligible_canary_envelope(self):
        state = build_fleet_desired_state([_b(deployment_mode="canary")])
        env = state.bindings[0].policy_envelope
        self.assertEqual(env.stage, "canary")
        self.assertTrue(env.fleet_eligible)

    def test_excluded_retired_envelope(self):
        state = build_fleet_desired_state([_b(status="retired")])
        env = state.excluded[0]
        self.assertEqual(env.exclusion_reason, "terminal_status")

    def test_allowed_scope_from_activation_gate(self):
        b = _b(metadata={"activation_gate": {"allowed_deployment_scope": "canary"}})
        state = build_fleet_desired_state([b])
        env = state.bindings[0].policy_envelope
        self.assertEqual(env.allowed_deployment_scope, "canary")

    def test_allowed_scope_absent_when_no_metadata(self):
        state = build_fleet_desired_state([_b()])
        env = state.bindings[0].policy_envelope
        self.assertIsNone(env.allowed_deployment_scope)

    def test_allowed_scope_absent_when_metadata_missing_gate(self):
        state = build_fleet_desired_state([_b(metadata={"other_key": "value"})])
        env = state.bindings[0].policy_envelope
        self.assertIsNone(env.allowed_deployment_scope)

    def test_policy_envelope_to_dict_eligible(self):
        state = build_fleet_desired_state([_b()])
        d = state.bindings[0].policy_envelope.to_dict()
        self.assertIn("stage", d)
        self.assertIn("fleet_eligible", d)
        self.assertTrue(d["fleet_eligible"])
        self.assertNotIn("exclusion_reason", d)

    def test_policy_envelope_to_dict_excluded(self):
        state = build_fleet_desired_state([_b(status="retired")])
        env = PolicyEnvelope(
            stage="paper",
            allowed_deployment_scope=None,
            fleet_eligible=False,
            exclusion_reason="terminal_status",
        )
        d = env.to_dict()
        self.assertIn("exclusion_reason", d)
        self.assertEqual(d["exclusion_reason"], "terminal_status")


# ---------------------------------------------------------------------------
# Tests — to_dict shape
# ---------------------------------------------------------------------------

class TestToDictShape(unittest.TestCase):
    def test_default_excludes_excluded_list(self):
        state = build_fleet_desired_state([_b(), _b(binding_id="rb-2", status="retired")])
        d = state.to_dict()
        self.assertIn("query_version", d)
        self.assertIn("fleet_stages", d)
        self.assertIn("eligible_statuses", d)
        self.assertIn("active_count", d)
        self.assertIn("bindings", d)
        self.assertNotIn("excluded", d)
        self.assertNotIn("excluded_count", d)

    def test_include_excluded_adds_lists(self):
        state = build_fleet_desired_state([_b(), _b(binding_id="rb-2", status="retired")])
        d = state.to_dict(include_excluded=True)
        self.assertIn("excluded", d)
        self.assertIn("excluded_count", d)
        self.assertEqual(d["excluded_count"], 1)

    def test_query_version_is_string(self):
        state = build_fleet_desired_state([])
        d = state.to_dict()
        self.assertIsInstance(d["query_version"], str)

    def test_fleet_stages_sorted(self):
        state = build_fleet_desired_state([])
        self.assertEqual(state.to_dict()["fleet_stages"], sorted(state.fleet_stages))

    def test_binding_dict_has_policy_envelope(self):
        state = build_fleet_desired_state([_b()])
        binding_dict = state.to_dict()["bindings"][0]
        self.assertIn("policy_envelope", binding_dict)
        self.assertIn("fleet_eligible", binding_dict["policy_envelope"])
        self.assertIn("stage", binding_dict["policy_envelope"])


# ---------------------------------------------------------------------------
# Tests — stability / idempotency
# ---------------------------------------------------------------------------

class TestStability(unittest.TestCase):
    def test_idempotent_on_same_input(self):
        bindings = [
            _b(binding_id="rb-1"),
            _b(binding_id="rb-2", status="retired"),
        ]
        s1 = build_fleet_desired_state(bindings)
        s2 = build_fleet_desired_state(bindings)
        self.assertEqual(
            s1.to_dict(include_excluded=True),
            s2.to_dict(include_excluded=True),
        )

    def test_order_preserved_from_input(self):
        bindings = [
            _b(binding_id="rb-a"),
            _b(binding_id="rb-b"),
            _b(binding_id="rb-c"),
        ]
        state = build_fleet_desired_state(bindings)
        ids = [b.binding_id for b in state.bindings]
        self.assertEqual(ids, ["rb-a", "rb-b", "rb-c"])

    def test_constants_are_frozensets(self):
        self.assertIsInstance(FLEET_MANAGED_STAGES, frozenset)
        self.assertIsInstance(FLEET_ELIGIBLE_STATUS, frozenset)

    def test_excluded_statuses_cover_non_active(self):
        for status in ("retired", "failed", "pending_pause", "paused"):
            self.assertIn(status, FLEET_EXCLUDED_STATUSES)


if __name__ == "__main__":
    unittest.main()
