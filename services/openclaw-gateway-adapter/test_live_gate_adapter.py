"""Tests for the live gate adapter.

Coverage:
  - All gate checks fail-closed when conditions are not met
  - Mocked fully-approved dry handoff succeeds
  - Audit log records all intents and outcomes
  - Adapter routes in main.py return correct HTTP codes

Acceptance:
  - live path remains disabled without all gates
  - human approval, capital binding, kill switch, and rollback policy are
    enforced before any live handoff
  - missing approval or unsafe capital state fails closed
  - no real broker live execution occurs
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import types
import unittest
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch


_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# Stubs for importing the modules under test without full dependencies
# ---------------------------------------------------------------------------

def _stub_foundation() -> None:
    if "services.foundation.health" in sys.modules:
        return
    for pkg in ("services", "services.foundation"):
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)
    health_mod = types.ModuleType("services.foundation.health")

    def health_payload(service, *, live=True, ready=True, dependencies=None, metrics=None, details=None):
        dep = {}
        if dependencies:
            resolved = dependencies() if callable(dependencies) else dependencies
            for k, v in resolved.items():
                dep[k] = v() if callable(v) else v
        dep_statuses = [str(v.get("status", "ok")).lower() for v in dep.values() if isinstance(v, dict)]
        status = "ok" if live and ready else "error"
        if status == "ok" and any(s in {"degraded", "error", "unavailable", "failed"} for s in dep_statuses):
            status = "degraded"
        return {"status": status, "service": service, "live": live, "ready": ready, "dependencies": dep}

    def readiness_status_code(payload):
        return 200 if payload.get("status") == "ok" else 503

    def register_fastapi_health_routes(app, service, *, dependencies=None, metrics=None, details=None):
        from fastapi.responses import JSONResponse

        async def healthz():
            return health_payload(service, dependencies=dependencies)

        async def livez():
            return health_payload(service)

        async def readyz():
            p = health_payload(service, dependencies=dependencies)
            return JSONResponse(p, status_code=readiness_status_code(p))

        async def metrics_route():
            return {"service": service}

        app.add_api_route("/healthz", healthz, methods=["GET"])
        app.add_api_route("/livez", livez, methods=["GET"])
        app.add_api_route("/readyz", readyz, methods=["GET"])
        app.add_api_route("/metrics", metrics_route, methods=["GET"])

    health_mod.health_payload = health_payload
    health_mod.readiness_status_code = readiness_status_code
    health_mod.register_fastapi_health_routes = register_fastapi_health_routes
    sys.modules["services.foundation.health"] = health_mod
    if "services.foundation" not in sys.modules:
        sys.modules["services.foundation"] = types.ModuleType("services.foundation")


_stub_foundation()
sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_gate_adapter import (  # noqa: E402
    LiveGateAdapter,
    LiveGateAuditLog,
    LiveGateError,
)

# Optional: stub httpx for route tests when not installed
try:
    import httpx as _httpx  # noqa: F401
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ACTIVE_LIVE_BINDING: Dict[str, Any] = {
    "binding_id": "rb-live-001",
    "capital_pool_id": "pool-a",
    "artifact_id": "art-live-v1",
    "deployment_mode": "live",
    "status": "active",
    "persona_capital_binding_id": "pcb-001",
}

_VALID_TOKEN = "approved-by-human-ep5"


def _make_adapter(
    enabled: bool = True,
    token: str = _VALID_TOKEN,
    binding: Optional[Dict[str, Any]] = _ACTIVE_LIVE_BINDING,
    safe_mode: str = "normal",
    audit_path: Optional[str] = None,
) -> LiveGateAdapter:
    def binding_resolver(pool_id: str) -> Optional[Dict[str, Any]]:
        return binding

    def safe_mode_resolver(pool_id: str) -> str:
        return safe_mode

    return LiveGateAdapter(
        enabled=enabled,
        human_approval_token=token,
        binding_resolver=binding_resolver,
        safe_mode_resolver=safe_mode_resolver,
        audit_log=LiveGateAuditLog(path=audit_path or "/tmp/test-live-gate-audit.jsonl"),
    )


# ---------------------------------------------------------------------------
# Gate 1 — live adapter enabled
# ---------------------------------------------------------------------------

class TestGateEnabled(unittest.TestCase):
    def test_disabled_raises_gate_error(self) -> None:
        adapter = _make_adapter(enabled=False)
        with self.assertRaises(LiveGateError) as ctx:
            adapter.check_all_gates(
                capital_pool_id="pool-a",
                human_approval_token=_VALID_TOKEN,
                operator_id="op-1",
            )
        err = ctx.exception
        self.assertEqual(err.error_code, "LIVE_GATE_DISABLED")
        self.assertEqual(err.gate, "live_adapter_enabled")
        self.assertEqual(err.status_code, 503)

    def test_enabled_proceeds_to_next_gate(self) -> None:
        adapter = _make_adapter(enabled=True, token="", binding=None)
        with self.assertRaises(LiveGateError) as ctx:
            adapter.check_all_gates(
                capital_pool_id="pool-a",
                human_approval_token=_VALID_TOKEN,
                operator_id="op-1",
            )
        # Should fail at the human approval gate (token not configured), not the enabled gate
        self.assertNotEqual(ctx.exception.error_code, "LIVE_GATE_DISABLED")


# ---------------------------------------------------------------------------
# Gate 2 — human approval token
# ---------------------------------------------------------------------------

class TestHumanApprovalGate(unittest.TestCase):
    def test_missing_token_header_fails(self) -> None:
        adapter = _make_adapter(enabled=True, token=_VALID_TOKEN)
        with self.assertRaises(LiveGateError) as ctx:
            adapter.check_all_gates(
                capital_pool_id="pool-a",
                human_approval_token="",
                operator_id="op-1",
            )
        err = ctx.exception
        self.assertEqual(err.error_code, "HUMAN_APPROVAL_TOKEN_MISSING")
        self.assertEqual(err.gate, "human_approval_token")
        self.assertEqual(err.status_code, 401)

    def test_wrong_token_fails(self) -> None:
        adapter = _make_adapter(enabled=True, token=_VALID_TOKEN)
        with self.assertRaises(LiveGateError) as ctx:
            adapter.check_all_gates(
                capital_pool_id="pool-a",
                human_approval_token="wrong-token",
                operator_id="op-1",
            )
        err = ctx.exception
        self.assertEqual(err.error_code, "HUMAN_APPROVAL_TOKEN_INVALID")
        self.assertEqual(err.gate, "human_approval_token")
        self.assertEqual(err.status_code, 403)

    def test_unconfigured_secret_fails(self) -> None:
        adapter = _make_adapter(enabled=True, token="")
        with self.assertRaises(LiveGateError) as ctx:
            adapter.check_all_gates(
                capital_pool_id="pool-a",
                human_approval_token=_VALID_TOKEN,
                operator_id="op-1",
            )
        err = ctx.exception
        self.assertEqual(err.error_code, "HUMAN_APPROVAL_NOT_CONFIGURED")
        self.assertEqual(err.gate, "human_approval_token")
        self.assertEqual(err.status_code, 503)


# ---------------------------------------------------------------------------
# Gate 3 — active live RuntimeBinding
# ---------------------------------------------------------------------------

class TestCapitalBindingGate(unittest.TestCase):
    def test_no_binding_fails(self) -> None:
        adapter = _make_adapter(enabled=True, token=_VALID_TOKEN, binding=None)
        with self.assertRaises(LiveGateError) as ctx:
            adapter.check_all_gates(
                capital_pool_id="pool-a",
                human_approval_token=_VALID_TOKEN,
                operator_id="op-1",
            )
        err = ctx.exception
        self.assertEqual(err.error_code, "LIVE_RUNTIME_BINDING_NOT_FOUND")
        self.assertEqual(err.gate, "active_live_runtime_binding")
        self.assertEqual(err.status_code, 409)

    def test_paper_binding_fails(self) -> None:
        paper_binding = {**_ACTIVE_LIVE_BINDING, "deployment_mode": "paper"}
        adapter = _make_adapter(enabled=True, token=_VALID_TOKEN, binding=paper_binding)
        with self.assertRaises(LiveGateError) as ctx:
            adapter.check_all_gates(
                capital_pool_id="pool-a",
                human_approval_token=_VALID_TOKEN,
                operator_id="op-1",
            )
        err = ctx.exception
        self.assertEqual(err.error_code, "LIVE_RUNTIME_BINDING_REQUIRED")
        self.assertEqual(err.gate, "active_live_runtime_binding")

    def test_missing_capital_pool_id_fails(self) -> None:
        adapter = _make_adapter(enabled=True, token=_VALID_TOKEN)
        with self.assertRaises(LiveGateError) as ctx:
            adapter.check_all_gates(
                capital_pool_id="",
                human_approval_token=_VALID_TOKEN,
                operator_id="op-1",
            )
        err = ctx.exception
        self.assertEqual(err.error_code, "CAPITAL_POOL_REQUIRED")


# ---------------------------------------------------------------------------
# Gate 4 — kill switch safe mode
# ---------------------------------------------------------------------------

class TestKillSwitchGate(unittest.TestCase):
    def _expect_denied(self, safe_mode: str) -> None:
        adapter = _make_adapter(enabled=True, token=_VALID_TOKEN, safe_mode=safe_mode)
        with self.assertRaises(LiveGateError) as ctx:
            adapter.check_all_gates(
                capital_pool_id="pool-a",
                human_approval_token=_VALID_TOKEN,
                operator_id="op-1",
            )
        err = ctx.exception
        self.assertEqual(err.error_code, "KILL_SWITCH_UNSAFE_STATE")
        self.assertEqual(err.gate, "kill_switch_safe_mode")
        self.assertIn(safe_mode, str(err.message))

    def test_paused_fails(self) -> None:
        self._expect_denied("paused")

    def test_risk_off_fails(self) -> None:
        self._expect_denied("risk_off")

    def test_guarded_fails(self) -> None:
        self._expect_denied("guarded")

    def test_recovery_testing_fails(self) -> None:
        self._expect_denied("recovery_testing")

    def test_normal_passes(self) -> None:
        adapter = _make_adapter(enabled=True, token=_VALID_TOKEN, safe_mode="normal")
        result = adapter.check_all_gates(
            capital_pool_id="pool-a",
            human_approval_token=_VALID_TOKEN,
            operator_id="op-1",
        )
        self.assertTrue(result["gates_passed"])

    def test_normal_restored_passes(self) -> None:
        adapter = _make_adapter(enabled=True, token=_VALID_TOKEN, safe_mode="normal_restored")
        result = adapter.check_all_gates(
            capital_pool_id="pool-a",
            human_approval_token=_VALID_TOKEN,
            operator_id="op-1",
        )
        self.assertTrue(result["gates_passed"])


# ---------------------------------------------------------------------------
# Gate 5 — binding not in rollback state
# ---------------------------------------------------------------------------

class TestRollbackPolicyGate(unittest.TestCase):
    def _binding_with_status(self, status: str) -> Dict[str, Any]:
        return {**_ACTIVE_LIVE_BINDING, "status": status}

    def test_paused_binding_fails(self) -> None:
        adapter = _make_adapter(
            enabled=True,
            token=_VALID_TOKEN,
            binding=self._binding_with_status("paused"),
        )
        with self.assertRaises(LiveGateError) as ctx:
            adapter.check_all_gates(
                capital_pool_id="pool-a",
                human_approval_token=_VALID_TOKEN,
                operator_id="op-1",
            )
        err = ctx.exception
        self.assertEqual(err.error_code, "BINDING_IN_UNSAFE_STATE")
        self.assertEqual(err.gate, "binding_not_in_rollback")

    def test_pending_pause_binding_fails(self) -> None:
        adapter = _make_adapter(
            enabled=True,
            token=_VALID_TOKEN,
            binding=self._binding_with_status("pending_pause"),
        )
        with self.assertRaises(LiveGateError) as ctx:
            adapter.check_all_gates(
                capital_pool_id="pool-a",
                human_approval_token=_VALID_TOKEN,
                operator_id="op-1",
            )
        self.assertEqual(ctx.exception.error_code, "BINDING_IN_UNSAFE_STATE")

    def test_retired_binding_fails(self) -> None:
        adapter = _make_adapter(
            enabled=True,
            token=_VALID_TOKEN,
            binding=self._binding_with_status("retired"),
        )
        with self.assertRaises(LiveGateError) as ctx:
            adapter.check_all_gates(
                capital_pool_id="pool-a",
                human_approval_token=_VALID_TOKEN,
                operator_id="op-1",
            )
        self.assertEqual(ctx.exception.error_code, "BINDING_IN_UNSAFE_STATE")

    def test_active_binding_passes(self) -> None:
        adapter = _make_adapter(
            enabled=True,
            token=_VALID_TOKEN,
            binding=self._binding_with_status("active"),
        )
        result = adapter.check_all_gates(
            capital_pool_id="pool-a",
            human_approval_token=_VALID_TOKEN,
            operator_id="op-1",
        )
        self.assertTrue(result["gates_passed"])


# ---------------------------------------------------------------------------
# Fully approved dry handoff
# ---------------------------------------------------------------------------

class TestDryHandoff(unittest.TestCase):
    def _make_passing_adapter(self, audit_path: str) -> LiveGateAdapter:
        return _make_adapter(
            enabled=True,
            token=_VALID_TOKEN,
            binding=_ACTIVE_LIVE_BINDING,
            safe_mode="normal",
            audit_path=audit_path,
        )

    def test_dry_handoff_returns_correct_fields(self, tmp_path: Optional[Path] = None) -> None:
        audit_path = "/tmp/test-live-gate-dry-handoff.jsonl"
        adapter = self._make_passing_adapter(audit_path)
        result = adapter.dry_handoff(
            capital_pool_id="pool-a",
            strategy_id="strat-1",
            symbol="AAPL",
            qty=10.0,
            side="buy",
            order_type="market",
            operator_id="op-1",
            human_approval_token=_VALID_TOKEN,
            trace_id="trace-abc",
        )
        self.assertEqual(result["status"], "dry_handoff_accepted")
        self.assertTrue(result["dry_handoff"])
        self.assertFalse(result["is_real_order"])
        self.assertFalse(result["is_real_capital"])
        self.assertEqual(result["deployment_stage"], "live_gate_harness")
        self.assertEqual(result["trace_id"], "trace-abc")
        self.assertEqual(result["capital_pool_id"], "pool-a")
        self.assertEqual(result["symbol"], "AAPL")
        attestation = result["gate_attestation"]
        self.assertTrue(attestation["gates_passed"])
        self.assertEqual(attestation["binding_id"], "rb-live-001")
        self.assertEqual(attestation["safe_mode"], "normal")

    def test_dry_handoff_records_audit(self) -> None:
        audit_path = "/tmp/test-live-gate-audit-record.jsonl"
        # Clear any prior audit
        Path(audit_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            Path(audit_path).unlink()
        except FileNotFoundError:
            pass

        adapter = self._make_passing_adapter(audit_path)
        adapter.dry_handoff(
            capital_pool_id="pool-a",
            strategy_id="strat-1",
            symbol="TSLA",
            qty=5.0,
            side="sell",
            operator_id="op-2",
            human_approval_token=_VALID_TOKEN,
        )

        entries = adapter.read_audit()
        # Should have at least 2 entries: pending + accepted
        self.assertGreaterEqual(len(entries), 2)
        outcomes = [e.get("outcome") for e in entries]
        self.assertIn("pending", outcomes)
        self.assertIn("dry_handoff_accepted", outcomes)
        # All entries mark no real order
        for entry in entries:
            self.assertFalse(entry.get("is_real_order", True))

    def test_dry_handoff_denied_records_audit(self) -> None:
        audit_path = "/tmp/test-live-gate-denied-audit.jsonl"
        Path(audit_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            Path(audit_path).unlink()
        except FileNotFoundError:
            pass

        adapter = _make_adapter(
            enabled=True,
            token=_VALID_TOKEN,
            binding=_ACTIVE_LIVE_BINDING,
            safe_mode="paused",  # will fail at kill switch gate
            audit_path=audit_path,
        )
        with self.assertRaises(LiveGateError):
            adapter.dry_handoff(
                capital_pool_id="pool-a",
                strategy_id="strat-1",
                symbol="SPY",
                qty=1.0,
                side="buy",
                operator_id="op-3",
                human_approval_token=_VALID_TOKEN,
            )

        entries = adapter.read_audit()
        self.assertGreaterEqual(len(entries), 2)
        outcomes = [e.get("outcome") for e in entries]
        self.assertIn("denied", outcomes)
        denied_entries = [e for e in entries if e.get("outcome") == "denied"]
        self.assertEqual(denied_entries[0].get("error_code"), "KILL_SWITCH_UNSAFE_STATE")

    def test_dry_handoff_disabled_gate_fails(self) -> None:
        audit_path = "/tmp/test-live-gate-disabled.jsonl"
        adapter = _make_adapter(enabled=False, audit_path=audit_path)
        with self.assertRaises(LiveGateError) as ctx:
            adapter.dry_handoff(
                capital_pool_id="pool-a",
                strategy_id="strat-1",
                symbol="AAPL",
                qty=1.0,
                side="buy",
                operator_id="op-4",
                human_approval_token=_VALID_TOKEN,
            )
        self.assertEqual(ctx.exception.error_code, "LIVE_GATE_DISABLED")


# ---------------------------------------------------------------------------
# Reject live order (always denied regardless of gate state)
# ---------------------------------------------------------------------------

class TestRejectLiveOrder(unittest.TestCase):
    def test_always_rejects_when_enabled(self) -> None:
        adapter = _make_adapter(enabled=True, token=_VALID_TOKEN)
        with self.assertRaises(LiveGateError) as ctx:
            adapter.reject_live_order(operator_id="op-1")
        err = ctx.exception
        self.assertEqual(err.error_code, "LIVE_EXECUTION_DISABLED")
        self.assertEqual(err.status_code, 403)

    def test_always_rejects_when_disabled(self) -> None:
        adapter = _make_adapter(enabled=False)
        with self.assertRaises(LiveGateError) as ctx:
            adapter.reject_live_order(operator_id="op-1")
        self.assertEqual(ctx.exception.error_code, "LIVE_EXECUTION_DISABLED")


# ---------------------------------------------------------------------------
# Capability snapshot
# ---------------------------------------------------------------------------

class TestCapabilitySnapshot(unittest.TestCase):
    def test_snapshot_fields_when_disabled(self) -> None:
        adapter = _make_adapter(enabled=False, token=_VALID_TOKEN)
        snap = adapter.capability_snapshot()
        self.assertFalse(snap["live_gate_enabled"])
        self.assertFalse(snap["live_adapter_enabled"])
        self.assertTrue(snap["dry_handoff_only"])
        self.assertFalse(snap["is_real_capital"])
        self.assertFalse(snap["is_real_order"])

    def test_snapshot_fields_when_enabled(self) -> None:
        adapter = _make_adapter(enabled=True, token=_VALID_TOKEN)
        snap = adapter.capability_snapshot()
        self.assertTrue(snap["live_gate_enabled"])
        # live_adapter_enabled is permanently False (no real execution)
        self.assertFalse(snap["live_adapter_enabled"])

    def test_snapshot_lists_all_gate_checks(self) -> None:
        adapter = _make_adapter(enabled=True, token=_VALID_TOKEN)
        snap = adapter.capability_snapshot()
        expected_gates = {
            "live_adapter_enabled",
            "human_approval_token",
            "active_live_runtime_binding",
            "kill_switch_safe_mode",
            "binding_not_in_rollback",
        }
        self.assertEqual(set(snap["gate_checks"]), expected_gates)


# ---------------------------------------------------------------------------
# Runtime-manager resolver failure handling
# ---------------------------------------------------------------------------

class TestRuntimeManagerFailures(unittest.TestCase):
    def test_binding_resolver_exception_fails_closed(self) -> None:
        def bad_resolver(pool_id: str):
            raise RuntimeError("connection refused")

        adapter = LiveGateAdapter(
            enabled=True,
            human_approval_token=_VALID_TOKEN,
            binding_resolver=bad_resolver,
            safe_mode_resolver=lambda pool_id: "normal",
            audit_log=LiveGateAuditLog(path="/tmp/test-live-gate-rm-fail.jsonl"),
        )
        with self.assertRaises(LiveGateError) as ctx:
            adapter.check_all_gates(
                capital_pool_id="pool-a",
                human_approval_token=_VALID_TOKEN,
                operator_id="op-1",
            )
        err = ctx.exception
        self.assertEqual(err.error_code, "RUNTIME_BINDING_CHECK_FAILED")
        self.assertEqual(err.gate, "active_live_runtime_binding")

    def test_safe_mode_resolver_exception_fails_closed(self) -> None:
        def bad_safe_mode_resolver(pool_id: str) -> str:
            raise RuntimeError("timeout")

        adapter = LiveGateAdapter(
            enabled=True,
            human_approval_token=_VALID_TOKEN,
            binding_resolver=lambda pool_id: _ACTIVE_LIVE_BINDING,
            safe_mode_resolver=bad_safe_mode_resolver,
            audit_log=LiveGateAuditLog(path="/tmp/test-live-gate-sm-fail.jsonl"),
        )
        with self.assertRaises(LiveGateError) as ctx:
            adapter.check_all_gates(
                capital_pool_id="pool-a",
                human_approval_token=_VALID_TOKEN,
                operator_id="op-1",
            )
        err = ctx.exception
        self.assertEqual(err.error_code, "SAFE_MODE_CHECK_FAILED")
        self.assertEqual(err.gate, "kill_switch_safe_mode")

    def test_no_runtime_manager_url_fails_closed(self) -> None:
        adapter = LiveGateAdapter(
            enabled=True,
            human_approval_token=_VALID_TOKEN,
            runtime_manager_url="",
            audit_log=LiveGateAuditLog(path="/tmp/test-live-gate-no-url.jsonl"),
        )
        with self.assertRaises(LiveGateError) as ctx:
            adapter.check_all_gates(
                capital_pool_id="pool-a",
                human_approval_token=_VALID_TOKEN,
                operator_id="op-1",
            )
        err = ctx.exception
        self.assertEqual(err.error_code, "RUNTIME_MANAGER_NOT_CONFIGURED")


# ---------------------------------------------------------------------------
# FastAPI route integration (via TestClient)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HTTPX_AVAILABLE, "httpx not installed; route tests require Docker env")
class TestLiveGateRoutes(unittest.TestCase):
    def setUp(self) -> None:
        # Patch env before module reload
        self._env_patch = patch.dict(os.environ, {
            "OPENCLAW_LIVE_ADAPTER_ENABLED": "true",
            "OPENCLAW_LIVE_HUMAN_APPROVAL_TOKEN": _VALID_TOKEN,
        })
        self._env_patch.start()

        # Reload main to pick up env patch
        if "main" in sys.modules:
            del sys.modules["main"]
        import main as gateway_main  # noqa: F401
        self._main = gateway_main

        # Patch the _LIVE_GATE singleton on the module with a controlled adapter
        self._adapter = _make_adapter(
            enabled=True,
            token=_VALID_TOKEN,
            binding=_ACTIVE_LIVE_BINDING,
            safe_mode="normal",
            audit_path="/tmp/test-live-gate-routes.jsonl",
        )
        self._orig = self._main._LIVE_GATE
        self._main._LIVE_GATE = self._adapter

        from fastapi.testclient import TestClient
        self._client = TestClient(self._main.app)

    def tearDown(self) -> None:
        self._main._LIVE_GATE = self._orig
        self._env_patch.stop()

    def test_live_orders_always_rejected(self) -> None:
        resp = self._client.post(
            "/api/openclaw-adapter/broker/live/orders",
            headers={"X-Operator-Id": "op-1"},
        )
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body["error_code"], "LIVE_EXECUTION_DISABLED")

    def test_gate_status_returns_200(self) -> None:
        resp = self._client.get("/api/openclaw-adapter/broker/live/gate/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["live_execution_enabled"])

    def test_gate_validate_missing_operator_returns_401(self) -> None:
        resp = self._client.post(
            "/api/openclaw-adapter/broker/live/gate/validate",
            json={"capital_pool_id": "pool-a"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_gate_validate_missing_approval_token_returns_401(self) -> None:
        resp = self._client.post(
            "/api/openclaw-adapter/broker/live/gate/validate",
            headers={"X-Operator-Id": "op-1"},
            json={"capital_pool_id": "pool-a"},
        )
        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body["error_code"], "HUMAN_APPROVAL_TOKEN_MISSING")

    def test_gate_validate_wrong_token_returns_403(self) -> None:
        resp = self._client.post(
            "/api/openclaw-adapter/broker/live/gate/validate",
            headers={"X-Operator-Id": "op-1", "X-Human-Approval-Token": "wrong"},
            json={"capital_pool_id": "pool-a"},
        )
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body["error_code"], "HUMAN_APPROVAL_TOKEN_INVALID")

    def test_gate_validate_all_gates_pass_returns_200(self) -> None:
        resp = self._client.post(
            "/api/openclaw-adapter/broker/live/gate/validate",
            headers={"X-Operator-Id": "op-1", "X-Human-Approval-Token": _VALID_TOKEN},
            json={"capital_pool_id": "pool-a"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["attestation"]["gates_passed"])

    def test_dry_handoff_all_gates_pass(self) -> None:
        resp = self._client.post(
            "/api/openclaw-adapter/broker/live/gate/dry-handoff",
            headers={"X-Operator-Id": "op-1", "X-Human-Approval-Token": _VALID_TOKEN},
            json={
                "capital_pool_id": "pool-a",
                "strategy_id": "strat-live",
                "symbol": "AAPL",
                "qty": 5.0,
                "side": "buy",
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "dry_handoff_accepted")
        self.assertFalse(body["is_real_order"])
        self.assertFalse(body["is_real_capital"])

    def test_dry_handoff_no_real_broker_execution(self) -> None:
        # Verify that no broker sidecar URL is ever called
        with patch("httpx.Client") as mock_httpx:
            resp = self._client.post(
                "/api/openclaw-adapter/broker/live/gate/dry-handoff",
                headers={"X-Operator-Id": "op-1", "X-Human-Approval-Token": _VALID_TOKEN},
                json={
                    "capital_pool_id": "pool-a",
                    "strategy_id": "strat-live",
                    "symbol": "MSFT",
                    "qty": 2.0,
                    "side": "sell",
                },
            )
        # httpx should not be called (adapter uses mock resolvers, not real HTTP)
        mock_httpx.assert_not_called()
        self.assertEqual(resp.status_code, 200)

    def test_dry_handoff_denied_when_gate_disabled(self) -> None:
        disabled_adapter = _make_adapter(
            enabled=False,
            audit_path="/tmp/test-live-gate-disabled-route.jsonl",
        )
        self._main._LIVE_GATE = disabled_adapter
        resp = self._client.post(
            "/api/openclaw-adapter/broker/live/gate/dry-handoff",
            headers={"X-Operator-Id": "op-1", "X-Human-Approval-Token": _VALID_TOKEN},
            json={
                "capital_pool_id": "pool-a",
                "strategy_id": "strat-live",
                "symbol": "AAPL",
                "qty": 1.0,
                "side": "buy",
            },
        )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error_code"], "LIVE_GATE_DISABLED")

    def test_audit_endpoint_returns_200(self) -> None:
        resp = self._client.get("/api/openclaw-adapter/broker/live/gate/audit")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("entries", body)
        self.assertIn("count", body)


if __name__ == "__main__":
    unittest.main()
