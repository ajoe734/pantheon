"""
Unit tests for telemetry capture module.

TEL-001: All tests now validate against the canonical
telemetry_event.schema.json — no ad-hoc minimal schemas.
"""

import unittest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from capture import TelemetryCapture, ExecutionMode, EventType, DeploymentStage, RollbackActionType

# Canonical schema path (resolved relative to this test file)
CANONICAL_SCHEMA = str(Path(__file__).parent / "telemetry_event.schema.json")

# Full binding context that satisfies all required evidence fields
FULL_BINDING_CONTEXT = {
    "binding_id": "b5e6f7a8-1234-5678-9abc-def012345678",
    "runtime_id": "lean-worker-01",
    "capital_pool_id": "pool-alpha",
    "artifact_id": "artifact-strategy-v2",
    "artifact_version": "2.1.0",
    "deployment_stage": "canary",
    "plan_id": "plan-deploy-001",
    "persona_capital_binding_id": "pcb-001",
    "authority_refs": {
        "write_owner": "runtime-manager",
        "authority_source": "runtime_binding",
        "runtime_role": "pantheon-lean-paper-runtime",
        "runtime_mode": "paper",
        "workspace_ref": "workspace-paper-alpha",
        "auth_profile_ref": "auth-profile-paper-alpha",
        "persona_id": "persona-ops",
        "session_id": "session-001",
        "trace_id": "trace-001",
        "request_id": "request-001",
    },
}


class TestTelemetryCapture(unittest.TestCase):
    """Test TelemetryCapture class."""

    def setUp(self):
        """Create test instance with canonical schema and full binding context."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_dir = self.temp_dir.name

        self.capture = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            storage_dir=self.storage_dir,
            binding_context=FULL_BINDING_CONTEXT.copy(),
        )

    def tearDown(self):
        """Cleanup temp directory."""
        self.temp_dir.cleanup()

    def test_capture_pnl_paper(self):
        """Test capturing PnL in paper trading mode."""
        result = self.capture.capture_pnl(
            mode=ExecutionMode.PAPER,
            strategy_id="test_strategy",
            pnl_value=100.50,
        )

        self.assertTrue(result)
        events = self.capture.get_canary_events()
        self.assertEqual(len(events), 1)

        event = events[0]
        self.assertEqual(event["event_type"], EventType.PNL_SNAPSHOT.value)
        self.assertEqual(event["execution_mode"], "canary")
        self.assertEqual(event["target"]["strategy_id"], "test_strategy")
        self.assertEqual(event["metrics"]["pnl"], 100.50)

    def test_capture_pnl_live(self):
        """Test capturing PnL in live trading mode."""
        result = self.capture.capture_pnl(
            mode=ExecutionMode.LIVE,
            strategy_id="test_strategy",
            pnl_value=-50.25,
        )

        self.assertTrue(result)
        events = self.capture.get_canary_events()
        self.assertEqual(len(events), 1)

        event = events[0]
        self.assertEqual(event["execution_mode"], "canary")
        self.assertEqual(event["metrics"]["pnl"], -50.25)

    def test_capture_drawdown(self):
        """Test capturing drawdown."""
        result = self.capture.capture_drawdown(
            mode=ExecutionMode.PAPER,
            strategy_id="test_strategy",
            drawdown_pct=5.5,
        )

        self.assertTrue(result)
        events = self.capture.get_events(ExecutionMode.CANARY)
        self.assertEqual(len(events), 1)

        event = events[0]
        self.assertEqual(event["event_type"], EventType.DRAWDOWN_SNAPSHOT.value)
        self.assertEqual(event["metrics"]["drawdown_pct"], 5.5)

    def test_capture_slippage(self):
        """Test capturing slippage in basis points."""
        result = self.capture.capture_slippage(
            mode=ExecutionMode.LIVE,
            strategy_id="test_strategy",
            slippage_bps=2.5,
            signal_id="sig_123",
            run_id="run_456",
        )

        self.assertTrue(result)
        events = self.capture.get_canary_events()
        self.assertEqual(len(events), 1)

        event = events[0]
        self.assertEqual(event["event_type"], EventType.SLIPPAGE_OBSERVATION.value)
        self.assertEqual(event["metrics"]["slippage_bps"], 2.5)
        self.assertEqual(event["signal_id"], "sig_123")
        self.assertEqual(event["run_id"], "run_456")

    def test_capture_fill(self):
        """Test capturing fill observation."""
        result = self.capture.capture_fill(
            mode=ExecutionMode.PAPER,
            strategy_id="test_strategy",
            fill_quantity=100.0,
            fill_price=50.25,
            broker="interactive_brokers",
        )

        self.assertTrue(result)
        events = self.capture.get_canary_events()
        self.assertEqual(len(events), 1)

        event = events[0]
        self.assertEqual(event["event_type"], EventType.FILL_OBSERVATION.value)
        self.assertEqual(event["metrics"]["fill_quantity"], 100.0)
        self.assertEqual(event["metrics"]["fill_price"], 50.25)
        self.assertEqual(event["broker"], "interactive_brokers")

    def test_capture_order_rejection(self):
        """Test capturing order rejection."""
        result = self.capture.capture_order_rejection(
            mode=ExecutionMode.LIVE,
            strategy_id="test_strategy",
            reject_reason="insufficient_buying_power",
            account_ref="acc_001",
        )

        self.assertTrue(result)
        events = self.capture.get_canary_events()
        self.assertEqual(len(events), 1)

        event = events[0]
        self.assertEqual(event["event_type"], EventType.ORDER_REJECTION.value)
        self.assertEqual(event["metrics"]["reject_reason"], "insufficient_buying_power")
        self.assertEqual(event["account_ref"], "acc_001")

    def test_paper_and_live_separation(self):
        """Test that paper and live events are kept separate."""
        capture = TelemetryCapture()
        capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 100.0)
        capture.capture_pnl(ExecutionMode.LIVE, "strat_1", 200.0)
        capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 150.0)

        paper = capture.get_paper_events()
        live = capture.get_live_events()

        self.assertEqual(len(paper), 2)
        self.assertEqual(len(live), 1)

        self.assertEqual(paper[0]["metrics"]["pnl"], 100.0)
        self.assertEqual(paper[1]["metrics"]["pnl"], 150.0)
        self.assertEqual(live[0]["metrics"]["pnl"], 200.0)

    def test_get_all_events(self):
        """Test retrieving all events."""
        self.capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 100.0)
        self.capture.capture_pnl(ExecutionMode.LIVE, "strat_1", 200.0)

        all_events = self.capture.get_events()
        self.assertEqual(len(all_events), 2)

    def test_event_has_unique_id(self):
        """Test that each event has unique ID."""
        self.capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 100.0)
        self.capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 200.0)

        events = self.capture.get_canary_events()
        ids = [e["event_id"] for e in events]

        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)

    def test_event_has_timestamp(self):
        """Test that events have ISO8601 timestamps."""
        self.capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 100.0)

        event = self.capture.get_canary_events()[0]
        self.assertIn("created_at", event)
        self.assertTrue(event["created_at"].endswith("Z"))

    def test_clear_paper_events(self):
        """Test clearing paper events."""
        capture = TelemetryCapture()
        capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 100.0)
        capture.capture_pnl(ExecutionMode.LIVE, "strat_1", 200.0)

        capture.clear_events(ExecutionMode.PAPER)

        paper = capture.get_paper_events()
        live = capture.get_live_events()

        self.assertEqual(len(paper), 0)
        self.assertEqual(len(live), 1)

    def test_clear_all_events(self):
        """Test clearing all events."""
        self.capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 100.0)
        self.capture.capture_pnl(ExecutionMode.LIVE, "strat_1", 200.0)

        self.capture.clear_events()

        all_events = self.capture.get_events()
        self.assertEqual(len(all_events), 0)

    def test_event_persistence(self):
        """Test that events are written to disk."""
        self.capture.capture_pnl(ExecutionMode.PAPER, "strat_1", 100.0)

        canary_dir = Path(self.storage_dir) / "canary"
        self.assertTrue(canary_dir.exists())

        event_files = list(canary_dir.glob("*.json"))
        self.assertEqual(len(event_files), 1)

        with open(event_files[0], "r") as f:
            data = json.load(f)

        self.assertEqual(data["execution_mode"], "canary")
        self.assertEqual(data["metrics"]["pnl"], 100.0)


class TestExecutionMode(unittest.TestCase):
    """Test ExecutionMode enum."""

    def test_enum_values(self):
        """Test enum values."""
        self.assertEqual(ExecutionMode.PAPER.value, "paper")
        self.assertEqual(ExecutionMode.CANARY.value, "canary")
        self.assertEqual(ExecutionMode.LIVE.value, "live")
        self.assertEqual(ExecutionMode.FROZEN.value, "frozen")


class TestEventType(unittest.TestCase):
    """Test EventType enum."""

    def test_enum_values(self):
        """Test event type enum values."""
        self.assertEqual(EventType.PNL_SNAPSHOT.value, "pnl_snapshot")
        self.assertEqual(EventType.DRAWDOWN_SNAPSHOT.value, "drawdown_snapshot")
        self.assertEqual(EventType.SLIPPAGE_OBSERVATION.value, "slippage_observation")
        self.assertEqual(EventType.FILL_OBSERVATION.value, "fill_observation")
        self.assertEqual(EventType.ORDER_REJECTION.value, "order_rejection")


class TestGovernedLinkageFields(unittest.TestCase):
    """Regression test: telemetry preserves governed linkage fields."""

    def test_capture_preserves_governed_linkage_fields(self):
        """Test that TelemetryCapture preserves registry, artifact, and lineage fields."""
        capture = TelemetryCapture()
        metadata = {
            'registry_id': 'reg-123',
            'artifact_type': 'strategy_spec',
            'artifact_version': '2.1.0',
            'promotion_state': 'paper',
            'lineage_ref': 'parent-456',
            'metrics': {'custom_metric': 42.0},
        }

        capture.capture_pnl(ExecutionMode.PAPER, 'test_strategy', 100.0, metadata=metadata)
        event = capture.get_canary_events()[0]

        self.assertEqual(event['target']['strategy_id'], 'test_strategy')
        self.assertEqual(event['target']['registry_id'], 'reg-123')
        self.assertEqual(event['target']['artifact_type'], 'strategy_spec')
        self.assertEqual(event['target']['artifact_version'], '2.1.0')
        self.assertEqual(event['target']['promotion_state'], 'paper')
        self.assertEqual(event['target']['lineage_ref'], 'parent-456')

        self.assertEqual(event['metrics']['pnl'], 100.0)
        self.assertEqual(event['metrics']['custom_metric'], 42.0)

    def test_fill_capture_with_full_linkage(self):
        """Test capture_fill preserves full governed linkage."""
        capture = TelemetryCapture()
        metadata = {
            'registry_id': 'reg-fill-123',
            'artifact_type': 'execution_bundle',
            'artifact_version': '1.0.0',
            'lineage_ref': 'parent-fill-456',
        }

        capture.capture_fill(
            ExecutionMode.LIVE,
            'algo_1',
            fill_quantity=500,
            fill_price=100.50,
            signal_id='sig-789',
            metadata=metadata,
        )

        event = capture.get_live_events()[0]
        self.assertEqual(event['event_type'], 'fill_observation')
        self.assertEqual(event['target']['registry_id'], 'reg-fill-123')
        self.assertEqual(event['target']['artifact_version'], '1.0.0')
        self.assertEqual(event['metrics']['fill_quantity'], 500)
        self.assertEqual(event['metrics']['fill_price'], 100.50)

    def test_slippage_capture_with_governance_fields(self):
        """Test capture_slippage preserves governance linkage."""
        capture = TelemetryCapture()
        metadata = {
            'registry_id': 'reg-slip-123',
            'artifact_type': 'strategy_spec',
            'promotion_state': 'live',
            'lineage_ref': 'parent-slip-789',
        }

        capture.capture_slippage(
            ExecutionMode.LIVE,
            'strategy_x',
            slippage_bps=2.5,
            metadata=metadata,
        )

        event = capture.get_live_events()[0]
        self.assertEqual(event['target']['promotion_state'], 'live')
        self.assertEqual(event['target']['lineage_ref'], 'parent-slip-789')
        self.assertEqual(event['metrics']['slippage_bps'], 2.5)

    def test_repeated_capture_with_same_metadata_preserves_linkage(self):
        """
        Regression test: repeated captures with same metadata dict should preserve linkage.
        """
        capture = TelemetryCapture()
        metadata = {
            'registry_id': 'reg-repeat-123',
            'artifact_type': 'strategy_spec',
            'artifact_version': '1.0.0',
            'promotion_state': 'paper',
            'lineage_ref': 'parent-repeat-456',
            'metrics': {'slippage_bps': 1.2},
        }

        initial_keys = set(metadata.keys())

        capture.capture_pnl(ExecutionMode.PAPER, 's1', 10.0, metadata=metadata)
        capture.capture_drawdown(ExecutionMode.PAPER, 's1', 2.0, metadata=metadata)
        capture.capture_slippage(ExecutionMode.PAPER, 's1', 1.5, metadata=metadata)

        self.assertEqual(set(metadata.keys()), initial_keys,
                        "Metadata dict was mutated during captures")

        events = capture.get_paper_events()
        self.assertEqual(len(events), 3, "Expected 3 events")

        linkage_fields = ['registry_id', 'artifact_version', 'artifact_type', 'promotion_state', 'lineage_ref']
        expected_linkage = {
            'registry_id': 'reg-repeat-123',
            'artifact_version': '1.0.0',
            'artifact_type': 'strategy_spec',
            'promotion_state': 'paper',
            'lineage_ref': 'parent-repeat-456',
        }

        for i, event in enumerate(events):
            target = event.get('target', {})
            for field in linkage_fields:
                self.assertIn(field, target,
                             f"Event {i} missing linkage field {field}")
                self.assertEqual(target[field], expected_linkage[field],
                               f"Event {i} has incorrect {field}")

        self.assertEqual(events[2]['metrics'].get('slippage_bps'), 1.2,
                        "Metadata metrics not merged into event")


# ── TEL-001: Binding/Stage Evidence Contract Tests ──


class TestBindingStageEvidence(unittest.TestCase):
    """TEL-001: Verify binding/stage evidence fields are injected into events."""

    def test_capture_injects_binding_context(self):
        """Test that TelemetryCapture injects all binding context fields."""
        capture = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            binding_context=FULL_BINDING_CONTEXT.copy(),
        )
        capture.capture_pnl(ExecutionMode.PAPER, "test_strategy", 100.0)

        event = capture.get_canary_events()[0]

        # Evidence E-1: Binding identity proof
        self.assertEqual(event["binding_id"], FULL_BINDING_CONTEXT["binding_id"])
        self.assertEqual(event["runtime_id"], FULL_BINDING_CONTEXT["runtime_id"])
        self.assertEqual(event["capital_pool_id"], FULL_BINDING_CONTEXT["capital_pool_id"])
        self.assertEqual(event["artifact_id"], FULL_BINDING_CONTEXT["artifact_id"])
        self.assertEqual(event["artifact_version"], FULL_BINDING_CONTEXT["artifact_version"])

        # Evidence E-2: Deployment stage proof
        self.assertEqual(event["deployment_stage"], "canary")

        # Evidence E-3: Governance admissibility proof
        self.assertEqual(event["plan_id"], FULL_BINDING_CONTEXT["plan_id"])
        self.assertEqual(event["persona_capital_binding_id"], FULL_BINDING_CONTEXT["persona_capital_binding_id"])
        self.assertEqual(event["authority_refs"], FULL_BINDING_CONTEXT["authority_refs"])

    def test_capture_injects_authority_refs(self):
        """Telemetry events should carry explicit runtime authority and isolation refs."""
        capture = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            binding_context=FULL_BINDING_CONTEXT.copy(),
        )
        capture.capture_heartbeat(ExecutionMode.PAPER, "test_strategy")

        event = capture.get_canary_events()[0]
        self.assertEqual(event["authority_refs"]["workspace_ref"], "workspace-paper-alpha")
        self.assertEqual(event["authority_refs"]["auth_profile_ref"], "auth-profile-paper-alpha")
        self.assertEqual(event["authority_refs"]["write_owner"], "runtime-manager")

    def test_environment_equals_deployment_stage(self):
        """TEL-001A: environment MUST equal deployment_stage for v1+ events."""
        capture = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            binding_context=FULL_BINDING_CONTEXT.copy(),
        )
        capture.capture_pnl(ExecutionMode.PAPER, "test_strategy", 100.0)

        event = capture.get_canary_events()[0]
        self.assertEqual(event["environment"], event["deployment_stage"])
        self.assertEqual(event["environment"], "canary")

    def test_execution_mode_from_deployment_stage(self):
        """execution_mode is derived 1:1 from deployment_stage."""
        # canary -> canary
        ctx = FULL_BINDING_CONTEXT.copy()
        ctx["deployment_stage"] = "canary"
        capture = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            binding_context=ctx,
        )
        capture.capture_pnl(ExecutionMode.PAPER, "strat", 0.0)
        self.assertEqual(capture.get_canary_events()[0]["execution_mode"], "canary")

        # paper -> paper
        ctx["deployment_stage"] = "paper"
        capture2 = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            binding_context=ctx,
        )
        capture2.capture_pnl(ExecutionMode.LIVE, "strat", 0.0)
        self.assertEqual(capture2.get_paper_events()[0]["execution_mode"], "paper")

    def test_frozen_deployment_stage_uses_frozen_execution_mode(self):
        """Frozen stage remains explicit instead of being collapsed into paper."""
        ctx = FULL_BINDING_CONTEXT.copy()
        ctx["deployment_stage"] = "frozen"
        capture = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            binding_context=ctx,
        )
        ok = capture.capture_pnl(ExecutionMode.LIVE, "strat", 1.0)
        self.assertTrue(ok, "frozen stage should produce valid events")
        events = capture.get_frozen_events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["deployment_stage"], "frozen")
        self.assertEqual(event["environment"], "frozen")
        self.assertEqual(event["execution_mode"], "frozen")

    def test_no_binding_context_rejected_by_canonical_schema(self):
        """Without binding_context, canonical schema rejects events (all binding fields required)."""
        capture = TelemetryCapture(schema_path=CANONICAL_SCHEMA)
        ok = capture.capture_pnl(ExecutionMode.PAPER, "strat", 100.0)
        self.assertFalse(ok, "canonical schema requires binding fields")
        self.assertEqual(len(capture.get_paper_events()), 0)

    def test_partial_binding_context_rejected(self):
        """TEL-001 Fix #2: partial binding_context must be rejected, not padded with defaults."""
        capture = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            binding_context={"binding_id": "b5e6f7a8-1234-5678-9abc-def012345678"},
        )
        ok = capture.capture_pnl(ExecutionMode.PAPER, "strat", 100.0)
        self.assertFalse(ok, "partial binding_context should be rejected")
        self.assertEqual(len(capture.get_paper_events()), 0)

    def test_rollback_evidence_e5(self):
        """Evidence E-5: Rollback lineage proof — rollback_parent and rollback_action_type."""
        capture = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            binding_context=FULL_BINDING_CONTEXT.copy(),
        )
        capture.capture_rollback_started(
            ExecutionMode.LIVE,
            "test_strategy",
            rollback_parent="old-binding-id-uuid",
            rollback_action_type=RollbackActionType.PAUSE_THEN_REPLACE,
        )

        event = capture.get_canary_events()[0]
        self.assertEqual(event["event_type"], "rollback_started")
        self.assertEqual(event["rollback_parent"], "old-binding-id-uuid")
        self.assertEqual(event["rollback_action_type"], "pause_then_replace")
        self.assertEqual(event["binding_id"], FULL_BINDING_CONTEXT["binding_id"])

    def test_rollback_action_type_enum_values(self):
        """Verify all canonical rollback action types are supported."""
        capture = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            binding_context=FULL_BINDING_CONTEXT.copy(),
        )

        for action_type in RollbackActionType:
            capture.clear_events()
            capture.capture_rollback_completed(
                ExecutionMode.LIVE,
                "strat",
                rollback_parent="parent-uuid",
                rollback_action_type=action_type,
            )
            event = capture.get_canary_events()[0]
            self.assertEqual(event["rollback_action_type"], action_type.value)

    def test_deploy_events(self):
        """Test deploy_started and deploy_completed events carry binding context."""
        capture = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            binding_context=FULL_BINDING_CONTEXT.copy(),
        )

        capture.capture_deploy_started(ExecutionMode.LIVE, "strat")
        capture.capture_deploy_completed(ExecutionMode.LIVE, "strat")

        events = capture.get_canary_events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_type"], "deploy_started")
        self.assertEqual(events[1]["event_type"], "deploy_completed")

        for event in events:
            self.assertEqual(event["binding_id"], FULL_BINDING_CONTEXT["binding_id"])
            self.assertEqual(event["deployment_stage"], "canary")
            # metrics must be non-empty per canonical schema minProperties=1
            self.assertGreater(len(event["metrics"]), 0)

    def test_kill_switch_event(self):
        """Test kill switch activation event."""
        capture = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            binding_context=FULL_BINDING_CONTEXT.copy(),
        )
        capture.capture_kill_switch(
            ExecutionMode.LIVE, "strat",
            metadata={"reason": "drawdown_exceeded"},
        )

        event = capture.get_canary_events()[0]
        self.assertEqual(event["event_type"], "kill_switch_action")
        self.assertEqual(event["binding_id"], FULL_BINDING_CONTEXT["binding_id"])
        self.assertGreater(len(event["metrics"]), 0)

    def test_heartbeat_event(self):
        """Test runtime heartbeat event."""
        capture = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            binding_context=FULL_BINDING_CONTEXT.copy(),
        )
        capture.capture_heartbeat(ExecutionMode.LIVE, "strat")

        event = capture.get_canary_events()[0]
        self.assertEqual(event["event_type"], "heartbeat")
        self.assertEqual(event["deployment_stage"], "canary")
        self.assertGreater(len(event["metrics"]), 0)

    def test_order_lifecycle_and_position_snapshot_events(self):
        """Order/cancel/position events validate against the canonical schema."""
        capture = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            binding_context=FULL_BINDING_CONTEXT.copy(),
        )

        self.assertTrue(
            capture.capture_order_lifecycle(
                ExecutionMode.LIVE,
                "strat",
                EventType.ORDER_SUBMITTED,
                order_id="order-001",
                order_status="submitted",
                quantity=10,
                price=101.25,
                symbol="SPY",
                broker="paper_broker",
            )
        )
        self.assertTrue(
            capture.capture_order_lifecycle(
                ExecutionMode.LIVE,
                "strat",
                EventType.ORDER_CANCELED,
                order_id="order-001",
                order_status="canceled",
                quantity=10,
                symbol="SPY",
                broker="paper_broker",
            )
        )
        self.assertTrue(
            capture.capture_position_snapshot(
                ExecutionMode.LIVE,
                "strat",
                position_qty=0,
                symbol="SPY",
                broker="paper_broker",
            )
        )

        events = capture.get_canary_events()
        self.assertEqual([event["event_type"] for event in events], [
            "order_submitted",
            "order_canceled",
            "position_snapshot",
        ])
        self.assertEqual(events[0]["order_id"], "order-001")
        self.assertEqual(events[1]["order_status"], "canceled")
        self.assertEqual(events[2]["position_qty"], 0)

    def test_all_event_types_available(self):
        """Verify all new EventType enum values are accessible."""
        expected_types = [
            "pnl_snapshot", "drawdown_snapshot", "slippage_observation",
            "fill_observation", "fill_received", "order_submitted",
            "order_accepted", "order_partially_filled", "order_filled",
            "order_canceled", "order_cancelled", "order_rejection",
            "position_snapshot", "position_snapshot_received",
            "broker_position_snapshot", "deploy_started",
            "deploy_completed", "rollback_started", "rollback_completed",
            "pause_triggered", "liquidate_triggered", "governance_decision",
            "approval_action", "manual_override", "kill_switch_action",
            "heartbeat", "telemetry_mirror_mismatch",
        ]
        for et in expected_types:
            self.assertIn(et, [e.value for e in EventType])

    def test_deployment_stage_enum_values(self):
        """Verify all deployment stage values."""
        stages = [e.value for e in DeploymentStage]
        self.assertIn("paper", stages)
        self.assertIn("canary", stages)
        self.assertIn("live", stages)
        self.assertIn("frozen", stages)


class TestBindingContextMutationSafety(unittest.TestCase):
    """TEL-001: Ensure binding_context is not mutated by capture operations."""

    def test_binding_context_not_mutated(self):
        """Repeated captures must not mutate the binding_context dict."""
        ctx = FULL_BINDING_CONTEXT.copy()
        original_keys = set(ctx.keys())
        original_values = dict(ctx)

        capture = TelemetryCapture(
            schema_path=CANONICAL_SCHEMA,
            binding_context=ctx,
        )
        capture.capture_pnl(ExecutionMode.PAPER, "s1", 10.0)
        capture.capture_drawdown(ExecutionMode.PAPER, "s1", 2.0)
        capture.capture_rollback_started(
            ExecutionMode.PAPER, "s1",
            rollback_parent="parent-uuid",
            rollback_action_type=RollbackActionType.REPLACE,
        )

        self.assertEqual(set(ctx.keys()), original_keys)
        for k, v in original_values.items():
            self.assertEqual(ctx[k], v)


if __name__ == "__main__":
    unittest.main()
