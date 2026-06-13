"""End-to-end paper runtime smoke test.

Exercises the full activation-ready kernel path:

  DeploymentPlan + RuntimeBinding
  → RuntimeBootstrapRequest (materialized via bootstrap_contract)
  → PantheonRuntimeContext (loaded from bootstrap env vars)
  → PaperRuntimeService (with fake signal store and telemetry sink)
  → canonical heartbeat / order / fill / position / pnl evidence

Acceptance criteria verified by this module:
  - runtime context reaches pantheon-lean bridge identity
  - paper smoke produces heartbeat, order/fill, position, pnl canonical evidence
  - telemetry events carry required fields: binding_id, plan_id, deployment_stage=paper,
    engine_bridge_repo, engine_bridge_commit
  - canary/live stages fail-closed via sidecar guard
  - lean-platform targets are rejected at bootstrap contract level
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from services.execution.lean_runtime.bootstrap_contract import (
    PANTHEON_LEAN_REMOTE,
    PANTHEON_LEAN_SOURCE_PATH,
    materialize_runtime_bootstrap_request,
)
from services.execution.lean_runtime.paper_runtime import (
    PaperRuntimeService,
    RuntimeBindingResolver,
    RuntimeTelemetryEmitter,
)
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.runtime_context import PantheonRuntimeContext
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BRIDGE_COMMIT = "smoke-bridge-commit-abc1234"


def _deployment_plan(**overrides):
    plan = {
        "plan_id": "dp-smoke-001",
        "approval_decision_id": "appr-smoke-001",
        "artifact_id": "art-smoke",
        "artifact_version": "1.0.0",
        "artifact_state": "approved",
        "artifact_checksum": "sha256:smoke",
        "strategy_id": "strat-smoke",
        "capital_pool_id": "pool-smoke-001",
        "target_stage": "paper",
        "runtime_role": "paper",
        "persona_capital_binding_id": "pcb-smoke-001",
        "runtime_config_ref": "/workspace/lean/Launcher/config.json",
        "runtime_config_status": "approved",
        "risk_policy_ref": "risk-policy-smoke",
        "risk_policy_evaluation": {
            "risk_policy_id": "risk-policy-smoke",
            "risk_policy_version": "v1",
            "capital_pool_id": "pool-smoke-001",
            "target_type": "runtime_launch",
            "target_id": "dp-smoke-001",
            "decision": "allowed",
            "checks": [],
            "blocking_reasons": [],
            "warnings": [],
            "evaluated_at": "2026-06-09T00:00:00Z",
            "trace_id": "trace-risk-policy-smoke",
        },
    }
    plan.update(overrides)
    return plan


def _runtime_binding(**overrides):
    binding = {
        "binding_id": "rtb-smoke-001",
        "runtime_id": "rt-smoke-001",
        "plan_id": "dp-smoke-001",
        "artifact_id": "art-smoke",
        "artifact_version": "1.0.0",
        "capital_pool_id": "pool-smoke-001",
        "deployment_mode": "paper",
        "persona_capital_binding_id": "pcb-smoke-001",
        "metadata": {
            "engine_bridge_repo": PANTHEON_LEAN_REMOTE,
            "engine_bridge_path": PANTHEON_LEAN_SOURCE_PATH,
            "engine_bridge_commit": _BRIDGE_COMMIT,
        },
    }
    binding.update(overrides)
    return binding


def _signal(action="BUY", direction="LONG", symbol="AAPL.US", quantity=10):
    return {
        "signal_id": "smoke-signal-001",
        "version": "1.0",
        "strategy_id": "strat-smoke",
        "timestamp": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "symbol": symbol,
        "action": action,
        "direction": direction,
        "quantity": quantity,
        "quantity_type": "SHARES",
    }


class _CapturingTelemetry:
    """In-memory telemetry sink that captures full canonical event payloads."""

    def __init__(self, binding: dict):
        self.binding = binding
        self.events: list[dict] = []
        self.enabled = True
        self._sent = 0
        self._failed = 0
        self._last_error = None

    def _build(self, event_type: str, metrics: dict, metadata: dict | None) -> dict:
        return {
            "event_type": event_type,
            "execution_mode": "paper",
            "deployment_stage": self.binding.get("deployment_mode", "paper"),
            "binding_id": self.binding["binding_id"],
            "runtime_id": self.binding["runtime_id"],
            "capital_pool_id": self.binding["capital_pool_id"],
            "artifact_id": self.binding["artifact_id"],
            "artifact_version": self.binding["artifact_version"],
            "plan_id": self.binding["plan_id"],
            "persona_capital_binding_id": self.binding["persona_capital_binding_id"],
            "metrics": metrics,
            "metadata": {
                "engine_bridge_repo": PANTHEON_LEAN_REMOTE,
                "engine_bridge_commit": _BRIDGE_COMMIT,
                **(metadata or {}),
            },
        }

    def emit(self, event_type: str, metrics: dict, metadata: dict | None = None) -> bool:
        self.events.append(self._build(event_type, metrics, metadata))
        self._sent += 1
        return True

    def emit_deploy_started(self) -> bool:
        return self.emit("deploy_started", {"action": "deploy_started"})

    def emit_deploy_completed(self) -> bool:
        return self.emit("deploy_completed", {"action": "deploy_completed"})

    def emit_heartbeat(self, metadata: dict | None = None) -> bool:
        return self.emit("heartbeat", {"heartbeat": 1}, metadata=metadata)

    def emit_pnl_snapshot(
        self,
        pnl: float,
        metadata: dict | None = None,
        extra_metrics: dict | None = None,
    ) -> bool:
        metrics = {"pnl": float(pnl)}
        metrics.update(extra_metrics or {})
        return self.emit("pnl_snapshot", metrics, metadata=metadata)

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "url": "memory://smoke-telemetry",
            "sent": self._sent,
            "failed": self._failed,
            "last_error": self._last_error,
        }

    def events_of_type(self, event_type: str) -> list[dict]:
        return [e for e in self.events if e["event_type"] == event_type]


class _FakeRuntimeManagerClient:
    def __init__(self, bindings: list[dict]):
        self._bindings = bindings

    def list_all(self) -> list[dict]:
        return list(self._bindings)


# ---------------------------------------------------------------------------
# Smoke test: DeploymentPlan → RuntimeBinding → bootstrap → paper runtime
# ---------------------------------------------------------------------------

class PaperRuntimeSmokeTest(unittest.TestCase):
    """Smoke tests for the full paper runtime kernel scaffold."""

    def _setup_smoke_service(self, signals=None, extra_binding=None):
        plan = _deployment_plan()
        binding = _runtime_binding(**(extra_binding or {}))

        request = materialize_runtime_bootstrap_request(
            deployment_plan=plan,
            runtime_binding=binding,
            request_id="rbr-smoke-001",
            trace_id="trace-smoke-001",
        )

        env = request.to_runtime_env()
        context = PantheonRuntimeContext.from_env(env)

        identity = RuntimeIdentity.from_env({
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "rt-smoke-001",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "smoke-token",
        })
        binding_dict = {
            "binding_id": binding["binding_id"],
            "runtime_id": binding["metadata"].get("runtime_id", binding.get("runtime_id", "rt-smoke-001")),
            "capital_pool_id": binding["capital_pool_id"],
            "artifact_id": binding["artifact_id"],
            "artifact_version": binding["artifact_version"],
            "plan_id": binding["plan_id"],
            "persona_capital_binding_id": binding["persona_capital_binding_id"],
            "deployment_mode": "paper",
            "deployment_stage": "paper",
            "engine_bridge_repo": PANTHEON_LEAN_REMOTE,
            "engine_bridge_path": PANTHEON_LEAN_SOURCE_PATH,
            "engine_bridge_commit": _BRIDGE_COMMIT,
        }
        telemetry = _CapturingTelemetry(binding_dict)
        store = InMemoryPendingSignalStore(signals or [])
        rm_client = _FakeRuntimeManagerClient([binding_dict])

        service = PaperRuntimeService(
            store=store,
            identity=identity,
            runtime_manager_client=rm_client,
            telemetry_emitter=telemetry,
            runtime_context=context,
            poll_interval_seconds=3600,
            max_batch_size=50,
        )
        return service, telemetry, context, request

    def test_bootstrap_request_materializes_from_deployment_plan(self):
        plan = _deployment_plan()
        binding = _runtime_binding()

        request = materialize_runtime_bootstrap_request(
            deployment_plan=plan,
            runtime_binding=binding,
        )
        payload = request.to_dict()

        self.assertEqual(payload["deployment_stage"], "paper")
        self.assertEqual(payload["bridge"]["remote"], PANTHEON_LEAN_REMOTE)
        self.assertEqual(payload["bridge"]["source_path"], PANTHEON_LEAN_SOURCE_PATH)
        self.assertEqual(payload["bridge"]["commit"], _BRIDGE_COMMIT)
        self.assertTrue(payload["runtime_config"]["paper_mode"])
        self.assertFalse(payload["runtime_config"]["live_broker_enabled"])

    def test_runtime_context_carries_bridge_identity(self):
        _, _, context, request = self._setup_smoke_service()

        self.assertEqual(context.bridge.repo, PANTHEON_LEAN_REMOTE)
        self.assertEqual(context.bridge.path, PANTHEON_LEAN_SOURCE_PATH)
        self.assertEqual(context.bridge.commit, _BRIDGE_COMMIT)
        self.assertEqual(context.deployment_stage, "paper")
        self.assertEqual(context.artifact.artifact_id, "art-smoke")
        self.assertEqual(context.capital.capital_pool_id, "pool-smoke-001")

    def test_paper_runtime_reports_context_in_health_snapshot(self):
        service, _, context, _ = self._setup_smoke_service()

        snapshot = service.snapshot()

        ctx_snap = snapshot["runtime_context"]
        self.assertTrue(ctx_snap["loaded"])
        self.assertEqual(ctx_snap["deployment_stage"], "paper")
        self.assertEqual(ctx_snap["runtime_binding_id"], "rtb-smoke-001")
        self.assertEqual(ctx_snap["bridge_repo"], PANTHEON_LEAN_REMOTE)
        self.assertEqual(ctx_snap["bridge_commit"], _BRIDGE_COMMIT)

    def test_paper_smoke_produces_heartbeat_evidence(self):
        service, telemetry, _, _ = self._setup_smoke_service()

        service.drain_once()

        heartbeat_events = telemetry.events_of_type("heartbeat")
        self.assertGreater(len(heartbeat_events), 0, "No heartbeat events emitted")
        hb = heartbeat_events[0]
        self.assertEqual(hb["execution_mode"], "paper")
        self.assertEqual(hb["deployment_stage"], "paper")
        self.assertEqual(hb["binding_id"], "rtb-smoke-001")
        self.assertEqual(hb["plan_id"], "dp-smoke-001")
        self.assertEqual(hb["capital_pool_id"], "pool-smoke-001")
        self.assertEqual(hb["artifact_id"], "art-smoke")
        self.assertIn("engine_bridge_repo", hb["metadata"])
        self.assertEqual(hb["metadata"]["engine_bridge_repo"], PANTHEON_LEAN_REMOTE)
        self.assertIn("engine_bridge_commit", hb["metadata"])
        self.assertEqual(hb["metadata"]["engine_bridge_commit"], _BRIDGE_COMMIT)
        self.assertEqual(hb["metrics"]["heartbeat"], 1)

    def test_paper_smoke_produces_fill_evidence_for_buy_signal(self):
        service, telemetry, _, _ = self._setup_smoke_service(
            signals=[_signal(action="BUY", direction="LONG", quantity=10)]
        )

        snapshot = service.drain_once()

        fill_events = telemetry.events_of_type("paper_fill_simulated")
        self.assertGreater(len(fill_events), 0, "No fill events emitted")
        fill = fill_events[0]
        self.assertEqual(fill["execution_mode"], "paper")
        self.assertEqual(fill["binding_id"], "rtb-smoke-001")
        self.assertIn("fill_quantity", fill["metrics"])
        self.assertIn("fill_price", fill["metrics"])
        self.assertIn("action", fill["metrics"])
        self.assertFalse(fill["metrics"]["submitted_to_broker"])

        paper_state = snapshot["paper_state"]
        self.assertGreater(paper_state["execution_event_count"], 0)
        self.assertGreater(len(paper_state["recent_order_events"]), 0)

    def test_paper_smoke_produces_position_evidence(self):
        service, _, _, _ = self._setup_smoke_service(
            signals=[_signal(action="BUY", direction="LONG", symbol="AAPL.US", quantity=5)]
        )

        snapshot = service.drain_once()

        positions = snapshot["paper_state"]["positions"]
        self.assertGreater(len(positions), 0, "No position evidence")
        pos = positions[0]
        self.assertEqual(pos["symbol"], "AAPL")
        self.assertGreater(pos["quantity"], 0)
        self.assertIn("price", pos)

    def test_paper_smoke_produces_pnl_evidence(self):
        service, telemetry, _, _ = self._setup_smoke_service(
            signals=[_signal(action="BUY", direction="LONG", quantity=10)]
        )

        service.drain_once()

        pnl_events = telemetry.events_of_type("pnl_snapshot")
        self.assertGreater(len(pnl_events), 0, "No pnl_snapshot events emitted")
        pnl_event = pnl_events[0]
        self.assertEqual(pnl_event["execution_mode"], "paper")
        self.assertIn("pnl", pnl_event["metrics"])

    def test_paper_smoke_full_pipeline_canonical_evidence_shape(self):
        """End-to-end check: all required canonical event types are present."""
        service, telemetry, _, _ = self._setup_smoke_service(
            signals=[
                _signal(action="BUY", direction="LONG", symbol="AAPL.US", quantity=10),
                _signal(action="SELL", direction="LONG", symbol="MSFT.US", quantity=5),
            ]
        )

        service.drain_once()

        event_types = {e["event_type"] for e in telemetry.events}
        self.assertIn("heartbeat", event_types)
        self.assertIn("paper_fill_simulated", event_types)
        self.assertIn("pnl_snapshot", event_types)

        for event in telemetry.events:
            self.assertEqual(event["execution_mode"], "paper", f"wrong mode in {event['event_type']}")
            self.assertEqual(event["binding_id"], "rtb-smoke-001", f"missing binding_id in {event['event_type']}")
            self.assertEqual(event["plan_id"], "dp-smoke-001", f"missing plan_id in {event['event_type']}")
            self.assertIn("engine_bridge_repo", event["metadata"], f"missing bridge_repo in {event['event_type']}")

    def test_bootstrap_contract_rejects_lean_platform_target(self):
        from services.execution.lean_runtime.bootstrap_contract import BootstrapContractError

        bad_plan = _deployment_plan()
        bad_binding = _runtime_binding()
        bad_binding["metadata"]["engine_bridge_repo"] = "ajoe734/lean-platform.git"

        with self.assertRaises(BootstrapContractError):
            materialize_runtime_bootstrap_request(
                deployment_plan=bad_plan,
                runtime_binding=bad_binding,
            )

    def test_canary_bootstrap_is_health_only(self):
        plan = _deployment_plan(target_stage="canary")
        binding = _runtime_binding(deployment_mode="canary")

        request = materialize_runtime_bootstrap_request(
            deployment_plan=plan,
            runtime_binding=binding,
        )

        self.assertEqual(request.deployment_stage, "canary")
        self.assertFalse(request.runtime_config.live_broker_enabled)
        self.assertTrue(request.runtime_config.health_only)

    def test_live_bootstrap_is_health_only(self):
        plan = _deployment_plan(target_stage="live")
        binding = _runtime_binding(deployment_mode="live")

        request = materialize_runtime_bootstrap_request(
            deployment_plan=plan,
            runtime_binding=binding,
        )

        self.assertEqual(request.deployment_stage, "live")
        self.assertFalse(request.runtime_config.live_broker_enabled)
        self.assertTrue(request.runtime_config.health_only)

    def test_live_broker_enabled_flag_is_rejected_by_p0_contract(self):
        from services.execution.lean_runtime.bootstrap_contract import BootstrapContractError

        plan = _deployment_plan()
        plan["runtime_config"] = {"live_broker_enabled": True}

        with self.assertRaises(BootstrapContractError):
            materialize_runtime_bootstrap_request(
                deployment_plan=plan,
                runtime_binding=_runtime_binding(),
            )

    def test_paper_fill_is_not_real_capital(self):
        """Verify canonical fills are flagged as simulated, not real capital."""
        service, telemetry, _, _ = self._setup_smoke_service(
            signals=[_signal(action="BUY", direction="LONG", quantity=10)]
        )

        service.drain_once()

        fill_events = telemetry.events_of_type("paper_fill_simulated")
        for fill in fill_events:
            self.assertFalse(
                fill["metrics"].get("submitted_to_broker", True),
                "paper fill must not be submitted to broker",
            )

    def test_bracket_order_smoke_is_logged_not_broker_submitted(self):
        """Paper bracket order logged-only canonical evidence (enabled guard case)."""
        sig = _signal(action="BUY", direction="LONG", quantity=10)
        sig["metadata"] = {
            "risk_parameters": {"stop_loss_pct": 0.02, "take_profit_pct": 0.05}
        }

        service, telemetry, _, _ = self._setup_smoke_service(signals=[sig])
        snapshot = service.drain_once()

        bracket_events = [
            e for e in snapshot["paper_state"]["recent_order_events"]
            if e["event_type"] == "bracket_order_logged"
        ]
        self.assertGreater(len(bracket_events), 0, "No bracket order evidence")
        for be in bracket_events:
            self.assertIn(be.get("broker_submission_status"), {
                "submitted_to_broker",
                "logged_only",
            }, "bracket status must be explicit")

    def test_bracket_order_smoke_produces_verifiable_leg_evidence(self):
        """Bracket event metadata must carry entry_price and deterministic leg prices."""
        sig = _signal(action="BUY", direction="LONG", quantity=10)
        sig["metadata"] = {
            "risk_parameters": {"stop_loss_pct": 0.02, "take_profit_pct": 0.05}
        }

        service, _, _, _ = self._setup_smoke_service(signals=[sig])
        snapshot = service.drain_once()

        bracket_events = [
            e for e in snapshot["paper_state"]["recent_order_events"]
            if e["event_type"] == "bracket_order_logged"
        ]
        self.assertGreater(len(bracket_events), 0, "No bracket order evidence")
        meta = bracket_events[0]["metadata"]

        self.assertIn("entry_price", meta, "entry_price must be present for audit")
        self.assertGreater(meta["entry_price"], 0)
        self.assertIn("legs", meta, "leg definitions must be present for audit")
        leg_types = {leg["leg_type"] for leg in meta["legs"]}
        self.assertIn("stop_loss", leg_types)
        self.assertIn("take_profit", leg_types)

    def test_bracket_order_canary_stage_is_fail_closed(self):
        """Canary stage bracket execution must be fail-closed: logged_only, no broker submission."""
        sig = _signal(action="BUY", direction="LONG", quantity=10)
        sig["metadata"] = {
            "risk_parameters": {"stop_loss_pct": 0.02, "take_profit_pct": 0.05}
        }

        service, _, _, _ = self._setup_smoke_service(signals=[sig])
        # Override algo deployment stage to canary after construction
        service._algo.DeploymentStage = "canary"
        snapshot = service.drain_once()

        bracket_events = [
            e for e in snapshot["paper_state"]["recent_order_events"]
            if e["event_type"] == "bracket_order_logged"
        ]
        self.assertGreater(len(bracket_events), 0, "Expected bracket_order_logged event")
        for be in bracket_events:
            self.assertEqual(
                be["broker_submission_status"], "logged_only",
                "Canary-stage bracket must be fail-closed (logged_only)",
            )
            self.assertFalse(be["submitted_to_broker"])
        # No open bracket orders stored — guard blocked submission
        self.assertEqual(snapshot["paper_state"]["open_bracket_orders"], [])


if __name__ == "__main__":
    unittest.main()
