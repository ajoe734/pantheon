"""Tests for the OpenClaw tool/workflow bridge.

Covers SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE acceptance:
- allowed tool and workflow policy is enforced
- Pantheon operator identity and request context map into upstream calls
- request/response audit trail is written
- unknown or disallowed tools fail closed
- no broker/paper/live execution is enabled by this task
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# Bootstrap: stub foundation so tests run without installing all services
# ---------------------------------------------------------------------------


def _stub_foundation() -> None:
    if "services.foundation.health" in sys.modules:
        return
    try:
        import services.foundation.health  # noqa: F401
        return
    except ImportError:
        pass
    for pkg in ("services", "services.foundation"):
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)
    health_mod = types.ModuleType("services.foundation.health")

    def health_payload(service, *, live=True, ready=True, dependencies=None, metrics=None, details=None):
        return {"status": "ok", "service": service}

    def readiness_status_code(payload):
        return 200

    def register_fastapi_health_routes(app, service, *, dependencies=None, details=None):
        pass

    health_mod.health_payload = health_payload
    health_mod.readiness_status_code = readiness_status_code
    health_mod.register_fastapi_health_routes = register_fastapi_health_routes
    sys.modules["services.foundation.health"] = health_mod


_stub_foundation()

import httpx  # noqa: E402
import pydantic  # noqa: E402

from tool_workflow_bridge import (  # noqa: E402
    BridgeAuditLog,
    BridgeError,
    ToolPolicy,
    ToolWorkflowBridge,
    _ALWAYS_BLOCKED_TOOLS,
)


# ---------------------------------------------------------------------------
# Fake upstream shim
# ---------------------------------------------------------------------------


class FakeUpstream:
    """Minimal shim that records calls and returns canned responses."""

    def __init__(self, fail: bool = False, retryable: bool = False) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.fail = fail
        self.retryable = retryable

    def invoke_tool(self, *, session_id, tool_name, args, operator_context=None):
        self.calls.append({"method": "invoke_tool", "session_id": session_id, "tool_name": tool_name, "args": args, "operator_context": operator_context})
        if self.fail:
            raise FakeUpstreamError("TOOL_INVOCATION_FAILED", "fake upstream failure", self.retryable)
        return {"result": "ok", "tool": tool_name}

    def trigger_workflow(self, *, workflow_ref, context, operator_context=None):
        self.calls.append({"method": "trigger_workflow", "workflow_ref": workflow_ref, "context": context, "operator_context": operator_context})
        if self.fail:
            raise FakeUpstreamError("WORKFLOW_TRIGGER_FAILED", "fake upstream failure", self.retryable)
        return {"job_id": "job-123", "status": "queued"}

    def list_tools(self, *, agent_id):
        if self.fail:
            raise FakeUpstreamError("UPSTREAM_UNAVAILABLE", "fake upstream failure", self.retryable)
        return [{"name": "search"}, {"name": "summarize"}]

    def resolve_tools(self, *, agent_id, session_id):
        if self.fail:
            raise FakeUpstreamError("UPSTREAM_UNAVAILABLE", "fake upstream failure", self.retryable)
        return [{"name": "search"}, {"name": "summarize"}]


class FakeUpstreamError(Exception):
    def __init__(self, error_code, message, retryable=False):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.retryable = retryable
        self.status_code = 502

    def to_payload(self):
        return {"error_code": self.error_code, "message": self.message, "retryable": self.retryable, "status_code": self.status_code}


# ---------------------------------------------------------------------------
# ToolPolicy tests
# ---------------------------------------------------------------------------


class TestToolPolicy(unittest.TestCase):
    def test_deny_all_when_allowlist_empty(self):
        policy = ToolPolicy(allowed_tools=[], allowed_workflows=[])
        d = policy.evaluate_tool("any_tool")
        self.assertFalse(d.allowed)
        self.assertEqual(d.policy_class, "deny_all")

    def test_allowed_tool_in_allowlist(self):
        policy = ToolPolicy(allowed_tools=["search", "summarize"])
        d = policy.evaluate_tool("search")
        self.assertTrue(d.allowed)
        self.assertEqual(d.policy_class, "allowlist")

    def test_unknown_tool_not_in_allowlist(self):
        policy = ToolPolicy(allowed_tools=["search"])
        d = policy.evaluate_tool("unknown_tool")
        self.assertFalse(d.allowed)
        self.assertEqual(d.policy_class, "not_in_allowlist")

    def test_always_blocked_tool_denied_even_in_allowlist(self):
        for blocked in _ALWAYS_BLOCKED_TOOLS:
            policy = ToolPolicy(allowed_tools=[blocked])
            d = policy.evaluate_tool(blocked)
            self.assertFalse(d.allowed, f"Expected {blocked} to be blocked")
            self.assertEqual(d.policy_class, "always_blocked")

    def test_broker_order_always_blocked(self):
        policy = ToolPolicy(allowed_tools=["broker_order"])
        d = policy.evaluate_tool("broker_order")
        self.assertFalse(d.allowed)
        self.assertEqual(d.policy_class, "always_blocked")

    def test_live_order_always_blocked(self):
        policy = ToolPolicy(allowed_tools=["live_order"])
        d = policy.evaluate_tool("live_order")
        self.assertFalse(d.allowed)
        self.assertEqual(d.policy_class, "always_blocked")

    def test_paper_order_always_blocked(self):
        policy = ToolPolicy(allowed_tools=["paper_order"])
        d = policy.evaluate_tool("paper_order")
        self.assertFalse(d.allowed)
        self.assertEqual(d.policy_class, "always_blocked")

    def test_workflow_deny_all_when_allowlist_empty(self):
        policy = ToolPolicy(allowed_workflows=[])
        d = policy.evaluate_workflow("some.workflow")
        self.assertFalse(d.allowed)
        self.assertEqual(d.policy_class, "deny_all")

    def test_workflow_allowed_in_allowlist(self):
        policy = ToolPolicy(allowed_workflows=["research.daily_scan"])
        d = policy.evaluate_workflow("research.daily_scan")
        self.assertTrue(d.allowed)
        self.assertEqual(d.policy_class, "allowlist")

    def test_workflow_not_in_allowlist(self):
        policy = ToolPolicy(allowed_workflows=["research.daily_scan"])
        d = policy.evaluate_workflow("research.other_workflow")
        self.assertFalse(d.allowed)
        self.assertEqual(d.policy_class, "not_in_allowlist")

    def test_broker_workflow_always_blocked(self):
        policy = ToolPolicy(allowed_workflows=["broker.submit"])
        d = policy.evaluate_workflow("broker.submit")
        self.assertFalse(d.allowed)
        self.assertEqual(d.policy_class, "always_blocked")

    def test_live_workflow_always_blocked(self):
        policy = ToolPolicy(allowed_workflows=["live.execute"])
        d = policy.evaluate_workflow("live.execute")
        self.assertFalse(d.allowed)
        self.assertEqual(d.policy_class, "always_blocked")

    def test_paper_workflow_always_blocked(self):
        policy = ToolPolicy(allowed_workflows=["paper.run"])
        d = policy.evaluate_workflow("paper.run")
        self.assertFalse(d.allowed)
        self.assertEqual(d.policy_class, "always_blocked")

    def test_capital_workflow_always_blocked(self):
        policy = ToolPolicy(allowed_workflows=["capital.bind"])
        d = policy.evaluate_workflow("capital.bind")
        self.assertFalse(d.allowed)
        self.assertEqual(d.policy_class, "always_blocked")

    def test_to_dict_structure(self):
        policy = ToolPolicy(allowed_tools=["search"], allowed_workflows=["research.scan"])
        d = policy.to_dict()
        self.assertIn("allowed_tools", d)
        self.assertIn("allowed_workflows", d)
        self.assertIn("always_blocked_tools", d)
        self.assertIn("default_posture", d)
        self.assertEqual(d["default_posture"], "deny_all")


# ---------------------------------------------------------------------------
# BridgeAuditLog tests
# ---------------------------------------------------------------------------


class TestBridgeAuditLog(unittest.TestCase):
    def _tmp_log(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        tmp.close()
        return BridgeAuditLog(path=tmp.name)

    def test_record_and_read(self):
        log = self._tmp_log()
        log.record({"request_type": "tool_invoke", "operator_id": "op1", "tool_name": "search", "outcome": "ok"})
        entries = log.read()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["tool_name"], "search")

    def test_filter_by_session_id(self):
        log = self._tmp_log()
        log.record({"session_id": "s1", "tool_name": "a"})
        log.record({"session_id": "s2", "tool_name": "b"})
        result = log.read(session_id="s1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tool_name"], "a")

    def test_filter_by_operator_id(self):
        log = self._tmp_log()
        log.record({"operator_id": "op1", "tool_name": "x"})
        log.record({"operator_id": "op2", "tool_name": "y"})
        result = log.read(operator_id="op2")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tool_name"], "y")

    def test_limit_respected(self):
        log = self._tmp_log()
        for i in range(10):
            log.record({"n": i})
        result = log.read(limit=3)
        self.assertEqual(len(result), 3)

    def test_read_empty_when_no_file(self):
        log = BridgeAuditLog(path="/tmp/_nonexistent_bridge_audit_test.jsonl")
        self.assertEqual(log.read(), [])

    def test_timestamp_auto_injected(self):
        log = self._tmp_log()
        log.record({"tool_name": "t"})
        entries = log.read()
        self.assertIn("at", entries[0])


# ---------------------------------------------------------------------------
# ToolWorkflowBridge tests
# ---------------------------------------------------------------------------


def _make_bridge(allowed_tools=None, allowed_workflows=None, audit_path=None):
    policy = ToolPolicy(
        allowed_tools=allowed_tools if allowed_tools is not None else [],
        allowed_workflows=allowed_workflows if allowed_workflows is not None else [],
    )
    audit = BridgeAuditLog(path=audit_path or tempfile.mktemp(suffix=".jsonl"))
    return ToolWorkflowBridge(
        policy=policy,
        audit_log=audit,
        trace_id_factory=lambda: "trace-fixed",
    ), audit


class TestToolInvoke(unittest.TestCase):
    def test_invoke_denied_no_allowlist(self):
        bridge, _ = _make_bridge(allowed_tools=[])
        with self.assertRaises(BridgeError) as ctx:
            bridge.invoke_tool(session_id="s1", tool_name="search", args={}, operator_id="op1", upstream=FakeUpstream())
        self.assertEqual(ctx.exception.error_code, "BRIDGE_TOOL_DENIED")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_invoke_allowed_tool(self):
        bridge, _ = _make_bridge(allowed_tools=["search"])
        upstream = FakeUpstream()
        result = bridge.invoke_tool(session_id="s1", tool_name="search", args={"q": "hello"}, operator_id="op1", upstream=upstream)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["tool_name"], "search")
        self.assertEqual(len(upstream.calls), 1)
        self.assertEqual(upstream.calls[0]["tool_name"], "search")

    def test_invoke_requires_operator_id(self):
        bridge, _ = _make_bridge(allowed_tools=["search"])
        with self.assertRaises(BridgeError) as ctx:
            bridge.invoke_tool(session_id="s1", tool_name="search", args={}, operator_id="", upstream=FakeUpstream())
        self.assertEqual(ctx.exception.error_code, "BRIDGE_OPERATOR_REQUIRED")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_invoke_requires_session_id(self):
        bridge, _ = _make_bridge(allowed_tools=["search"])
        with self.assertRaises(BridgeError) as ctx:
            bridge.invoke_tool(session_id="", tool_name="search", args={}, operator_id="op1", upstream=FakeUpstream())
        self.assertEqual(ctx.exception.error_code, "BRIDGE_SESSION_REQUIRED")

    def test_invoke_broker_order_denied(self):
        bridge, _ = _make_bridge(allowed_tools=["broker_order"])
        with self.assertRaises(BridgeError) as ctx:
            bridge.invoke_tool(session_id="s1", tool_name="broker_order", args={}, operator_id="op1", upstream=FakeUpstream())
        self.assertEqual(ctx.exception.error_code, "BRIDGE_TOOL_DENIED")

    def test_invoke_live_order_denied(self):
        bridge, _ = _make_bridge(allowed_tools=["live_order", "search"])
        with self.assertRaises(BridgeError) as ctx:
            bridge.invoke_tool(session_id="s1", tool_name="live_order", args={}, operator_id="op1", upstream=FakeUpstream())
        self.assertEqual(ctx.exception.error_code, "BRIDGE_TOOL_DENIED")

    def test_invoke_paper_order_denied(self):
        bridge, _ = _make_bridge(allowed_tools=["paper_order"])
        with self.assertRaises(BridgeError) as ctx:
            bridge.invoke_tool(session_id="s1", tool_name="paper_order", args={}, operator_id="op1", upstream=FakeUpstream())
        self.assertEqual(ctx.exception.error_code, "BRIDGE_TOOL_DENIED")

    def test_invoke_upstream_not_configured(self):
        bridge, _ = _make_bridge(allowed_tools=["search"])
        with self.assertRaises(BridgeError) as ctx:
            bridge.invoke_tool(session_id="s1", tool_name="search", args={}, operator_id="op1", upstream=None)
        self.assertEqual(ctx.exception.error_code, "BRIDGE_UPSTREAM_NOT_CONFIGURED")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_invoke_upstream_failure_raises_bridge_error(self):
        bridge, _ = _make_bridge(allowed_tools=["search"])
        upstream = FakeUpstream(fail=True)
        with self.assertRaises(BridgeError) as ctx:
            bridge.invoke_tool(session_id="s1", tool_name="search", args={}, operator_id="op1", upstream=upstream)
        self.assertEqual(ctx.exception.error_code, "TOOL_INVOCATION_FAILED")

    def test_operator_context_passed_to_upstream(self):
        bridge, _ = _make_bridge(allowed_tools=["search"])
        upstream = FakeUpstream()
        bridge.invoke_tool(session_id="s1", tool_name="search", args={}, operator_id="op-alice", trace_id="trace-xyz", upstream=upstream)
        ctx = upstream.calls[0]["operator_context"]
        self.assertEqual(ctx["pantheon_operator_id"], "op-alice")
        self.assertEqual(ctx["pantheon_trace_id"], "trace-xyz")
        self.assertEqual(ctx["pantheon_source"], "openclaw-gateway-adapter")

    def test_audit_trail_written_on_allowed_invoke(self):
        _, audit = _make_bridge(allowed_tools=["search"])
        bridge = ToolWorkflowBridge(
            policy=ToolPolicy(allowed_tools=["search"]),
            audit_log=audit,
            trace_id_factory=lambda: "trace-audit-test",
        )
        bridge.invoke_tool(session_id="s1", tool_name="search", args={}, operator_id="op1", upstream=FakeUpstream())
        entries = audit.read()
        self.assertGreaterEqual(len(entries), 1)
        last = entries[-1]
        self.assertEqual(last["tool_name"], "search")
        self.assertEqual(last["outcome"], "ok")

    def test_audit_trail_written_on_denied_invoke(self):
        policy = ToolPolicy(allowed_tools=[])
        audit = BridgeAuditLog(path=tempfile.mktemp(suffix=".jsonl"))
        bridge = ToolWorkflowBridge(policy=policy, audit_log=audit, trace_id_factory=lambda: "t")
        with self.assertRaises(BridgeError):
            bridge.invoke_tool(session_id="s1", tool_name="search", args={}, operator_id="op1", upstream=FakeUpstream())
        entries = audit.read()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["policy_decision"], "denied")
        self.assertEqual(entries[0]["outcome"], "denied")

    def test_audit_trail_written_on_upstream_error(self):
        policy = ToolPolicy(allowed_tools=["search"])
        audit = BridgeAuditLog(path=tempfile.mktemp(suffix=".jsonl"))
        bridge = ToolWorkflowBridge(policy=policy, audit_log=audit, trace_id_factory=lambda: "t")
        with self.assertRaises(BridgeError):
            bridge.invoke_tool(session_id="s1", tool_name="search", args={}, operator_id="op1", upstream=FakeUpstream(fail=True))
        entries = audit.read()
        outcomes = [e["outcome"] for e in entries]
        self.assertIn("upstream_error", outcomes)


class TestWorkflowTrigger(unittest.TestCase):
    def test_trigger_denied_no_allowlist(self):
        bridge, _ = _make_bridge(allowed_workflows=[])
        with self.assertRaises(BridgeError) as ctx:
            bridge.trigger_workflow(workflow_ref="research.scan", context={}, operator_id="op1", upstream=FakeUpstream())
        self.assertEqual(ctx.exception.error_code, "BRIDGE_WORKFLOW_DENIED")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_trigger_allowed_workflow(self):
        bridge, _ = _make_bridge(allowed_workflows=["research.scan"])
        upstream = FakeUpstream()
        result = bridge.trigger_workflow(workflow_ref="research.scan", context={"x": 1}, operator_id="op1", upstream=upstream)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["workflow_ref"], "research.scan")
        self.assertEqual(result["job_id"], "job-123")

    def test_trigger_requires_operator_id(self):
        bridge, _ = _make_bridge(allowed_workflows=["research.scan"])
        with self.assertRaises(BridgeError) as ctx:
            bridge.trigger_workflow(workflow_ref="research.scan", context={}, operator_id="", upstream=FakeUpstream())
        self.assertEqual(ctx.exception.error_code, "BRIDGE_OPERATOR_REQUIRED")

    def test_trigger_broker_workflow_denied(self):
        bridge, _ = _make_bridge(allowed_workflows=["broker.submit"])
        with self.assertRaises(BridgeError) as ctx:
            bridge.trigger_workflow(workflow_ref="broker.submit", context={}, operator_id="op1", upstream=FakeUpstream())
        self.assertEqual(ctx.exception.error_code, "BRIDGE_WORKFLOW_DENIED")

    def test_trigger_live_workflow_denied(self):
        bridge, _ = _make_bridge(allowed_workflows=["live.execute"])
        with self.assertRaises(BridgeError) as ctx:
            bridge.trigger_workflow(workflow_ref="live.execute", context={}, operator_id="op1", upstream=FakeUpstream())
        self.assertEqual(ctx.exception.error_code, "BRIDGE_WORKFLOW_DENIED")

    def test_trigger_paper_workflow_denied(self):
        bridge, _ = _make_bridge(allowed_workflows=["paper.run"])
        with self.assertRaises(BridgeError) as ctx:
            bridge.trigger_workflow(workflow_ref="paper.run", context={}, operator_id="op1", upstream=FakeUpstream())
        self.assertEqual(ctx.exception.error_code, "BRIDGE_WORKFLOW_DENIED")

    def test_trigger_upstream_not_configured(self):
        bridge, _ = _make_bridge(allowed_workflows=["research.scan"])
        with self.assertRaises(BridgeError) as ctx:
            bridge.trigger_workflow(workflow_ref="research.scan", context={}, operator_id="op1", upstream=None)
        self.assertEqual(ctx.exception.error_code, "BRIDGE_UPSTREAM_NOT_CONFIGURED")

    def test_trigger_operator_context_passed_to_upstream(self):
        bridge, _ = _make_bridge(allowed_workflows=["research.scan"])
        upstream = FakeUpstream()
        bridge.trigger_workflow(workflow_ref="research.scan", context={}, operator_id="op-bob", trace_id="trace-wf", upstream=upstream)
        ctx = upstream.calls[0]["operator_context"]
        self.assertEqual(ctx["pantheon_operator_id"], "op-bob")
        self.assertEqual(ctx["pantheon_trace_id"], "trace-wf")

    def test_trigger_audit_trail_written(self):
        policy = ToolPolicy(allowed_workflows=["research.scan"])
        audit = BridgeAuditLog(path=tempfile.mktemp(suffix=".jsonl"))
        bridge = ToolWorkflowBridge(policy=policy, audit_log=audit)
        bridge.trigger_workflow(workflow_ref="research.scan", context={}, operator_id="op1", upstream=FakeUpstream())
        entries = audit.read()
        wf_entries = [e for e in entries if e.get("request_type") == "workflow_trigger"]
        self.assertGreaterEqual(len(wf_entries), 1)
        last = wf_entries[-1]
        self.assertEqual(last["workflow_ref"], "research.scan")
        self.assertEqual(last["outcome"], "ok")

    def test_trigger_denied_audit_trail(self):
        policy = ToolPolicy(allowed_workflows=[])
        audit = BridgeAuditLog(path=tempfile.mktemp(suffix=".jsonl"))
        bridge = ToolWorkflowBridge(policy=policy, audit_log=audit)
        with self.assertRaises(BridgeError):
            bridge.trigger_workflow(workflow_ref="research.scan", context={}, operator_id="op1", upstream=FakeUpstream())
        entries = audit.read()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["outcome"], "denied")
        self.assertEqual(entries[0]["policy_decision"], "denied")


class TestListEffectiveTools(unittest.TestCase):
    def test_effective_tools_empty_when_no_allowlist(self):
        bridge, _ = _make_bridge(allowed_tools=[])
        result = bridge.list_effective_tools(agent_id="a1", operator_id="op1", upstream=FakeUpstream())
        self.assertEqual(result["effective_tools"], [])

    def test_effective_tools_intersect_with_upstream(self):
        bridge, _ = _make_bridge(allowed_tools=["search", "analyze"])
        upstream = FakeUpstream()  # returns search, summarize
        result = bridge.list_effective_tools(agent_id="a1", operator_id="op1", upstream=upstream)
        # search is in both; analyze is not in upstream; summarize is not in allowlist
        self.assertEqual(result["effective_tools"], ["search"])

    def test_effective_tools_degraded_when_upstream_none(self):
        bridge, _ = _make_bridge(allowed_tools=["search"])
        result = bridge.list_effective_tools(agent_id="a1", operator_id="op1", upstream=None)
        self.assertEqual(result["upstream_status"], "not_configured")
        self.assertEqual(result["effective_tools"], ["search"])

    def test_list_requires_operator_id(self):
        bridge, _ = _make_bridge(allowed_tools=["search"])
        with self.assertRaises(BridgeError) as ctx:
            bridge.list_effective_tools(agent_id="a1", operator_id="", upstream=FakeUpstream())
        self.assertEqual(ctx.exception.error_code, "BRIDGE_OPERATOR_REQUIRED")

    def test_effective_tools_degraded_on_upstream_failure(self):
        bridge, _ = _make_bridge(allowed_tools=["search"])
        upstream = FakeUpstream(fail=True)
        result = bridge.list_effective_tools(agent_id="a1", operator_id="op1", upstream=upstream)
        self.assertEqual(result["upstream_status"], "degraded")
        # falls back to full policy allowlist
        self.assertEqual(result["effective_tools"], ["search"])


class TestNoBrokerLivePaperExecution(unittest.TestCase):
    """Verify broker/live/paper tools and workflows are always blocked."""

    BLOCKED_TOOLS = [
        "broker_order", "submit_order", "live_order", "paper_order", "canary_order",
        "capital_bind", "capital_release", "lean_deploy", "live_execute",
        "paper_execute", "canary_execute", "broker_session_create", "broker_session_cancel",
    ]
    BLOCKED_WORKFLOWS = [
        "broker.any_workflow",
        "live.trade",
        "paper.backtest",
        "canary.deploy",
        "capital.bind_pool",
        "lean.deploy_strategy",
    ]

    def test_all_broker_live_paper_tools_blocked(self):
        policy = ToolPolicy(allowed_tools=self.BLOCKED_TOOLS)
        for tool in self.BLOCKED_TOOLS:
            d = policy.evaluate_tool(tool)
            self.assertFalse(d.allowed, f"Tool {tool} should be blocked")
            self.assertEqual(d.policy_class, "always_blocked", f"Tool {tool} policy_class wrong")

    def test_all_broker_live_paper_workflows_blocked(self):
        policy = ToolPolicy(allowed_workflows=self.BLOCKED_WORKFLOWS)
        for wf in self.BLOCKED_WORKFLOWS:
            d = policy.evaluate_workflow(wf)
            self.assertFalse(d.allowed, f"Workflow {wf} should be blocked")
            self.assertEqual(d.policy_class, "always_blocked", f"Workflow {wf} policy_class wrong")


if __name__ == "__main__":
    unittest.main()
