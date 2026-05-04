from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional, Tuple
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main


OPERATOR_AUTH = "Bearer op-2:operator"
VIEWER_AUTH = "Bearer viewer-1:viewer"
BASE_URL = "http://openclaw-adapter:8104"


class _FakeResponse:
    def __init__(self, body: Dict[str, Any], status: int = 200) -> None:
        self._body = json.dumps(body).encode("utf-8")
        self._status = status

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def getcode(self) -> int:
        return self._status

    def read(self) -> bytes:
        return self._body


class _Recorder:
    def __init__(self, responses: Dict[Tuple[str, str], Dict[str, Any]]) -> None:
        self.responses = responses
        self.calls = []

    def __call__(self, request: Any, timeout: Optional[float] = None) -> _FakeResponse:
        del timeout
        method = request.get_method()
        url = request.full_url
        headers = dict(request.headers)
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.calls.append((method, url, headers, body))
        key = (method, url)
        if key not in self.responses:
            raise AssertionError(f"Unexpected OpenClaw adapter call: {key}")
        return _FakeResponse(self.responses[key])


def _healthy_payloads() -> Dict[Tuple[str, str], Dict[str, Any]]:
    return {
        ("GET", f"{BASE_URL}/api/openclaw-adapter/capabilities"): {
            "adapter_version": "0.1.0",
            "activation_state": "upstream_client_ready",
            "session_lifecycle_state": "activation_ready",
            "broker_execution": "deferred",
            "paper_adapter": "deferred",
            "live_adapter": "deferred",
            "canary_adapter": "deferred",
            "capital_binding": "deferred",
            "fail_closed": True,
            "supported_session_types": ["interactive", "trainer"],
            "activation_gates": {
                "paper_adapter": "OPENCLAW_PAPER_ADAPTER_ENABLED",
                "live_adapter": "OPENCLAW_LIVE_ADAPTER_ENABLED",
                "canary_adapter": "OPENCLAW_CANARY_ADAPTER_ENABLED",
            },
            "upstream": {"status": "ok", "capabilities": {"agents": True}},
        },
        ("GET", f"{BASE_URL}/api/openclaw-adapter/upstream/status"): {
            "upstream_url": "http://upstream-openclaw:8080",
            "reachable": True,
            "details": {"probe": "/healthz", "http_status": 200},
        },
        ("GET", f"{BASE_URL}/api/openclaw-adapter/lifecycle/sessions"): {
            "status": "ok",
            "sessions": [
                {
                    "session_id": "oc-sess-1",
                    "agent_id": "agent-alpha",
                    "session_type": "interactive",
                    "state": "active",
                    "operator_id": "op-2",
                    "created_at": "2026-04-30T07:00:00Z",
                    "updated_at": "2026-04-30T07:01:00Z",
                    "upstream_session_id": "up-sess-1",
                    "context_bundle": {"ticket_id": "rt-1"},
                    "audit_log": [{"action": "create_acknowledged", "actor": "op-2"}],
                }
            ],
        },
        ("GET", f"{BASE_URL}/api/openclaw-adapter/tools/policy"): {
            "allowed_tools": ["search"],
            "allowed_workflows": ["research.daily_scan"],
            "always_blocked_tools": ["paper_order", "live_order"],
            "always_blocked_tool_prefixes": ["paper.", "live.", "broker."],
            "always_blocked_workflow_prefixes": ["paper.", "live.", "broker."],
            "default_posture": "deny_all",
        },
        ("GET", f"{BASE_URL}/api/openclaw-adapter/audit/invocations?limit=20"): {
            "status": "ok",
            "count": 2,
            "entries": [
                {
                    "request_type": "tool_invoke",
                    "trace_id": "trace-denied",
                    "operator_id": "op-2",
                    "session_id": "oc-sess-1",
                    "tool_name": "paper.execute",
                    "policy_decision": "denied",
                    "policy_class": "always_blocked",
                    "policy_reason": "paper path is blocked",
                    "outcome": "denied",
                    "args_hash": "abc123",
                    "at": "2026-04-30T07:02:00Z",
                },
                {
                    "request_type": "workflow_trigger",
                    "trace_id": "trace-ok",
                    "operator_id": "op-2",
                    "workflow_ref": "research.daily_scan",
                    "policy_decision": "allowed",
                    "policy_class": "allowlist",
                    "policy_reason": "allowed",
                    "outcome": "ok",
                    "context_hash": "def456",
                    "at": "2026-04-30T07:03:00Z",
                },
            ],
        },
    }


def test_openclaw_ops_surface_aggregates_status_sessions_gates_and_audit(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", BASE_URL)
    recorder = _Recorder(_healthy_payloads())
    client = TestClient(bff_main.app)

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", recorder):
        response = client.get(
            "/api/v1/operator/openclaw/ops",
            headers={"Authorization": OPERATOR_AUTH},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]

    assert data["overall_status"] == "ok"
    assert data["upstream"]["reachable"] is True
    assert data["session_lifecycle"]["state_counts"] == {"active": 1}
    assert data["session_lifecycle"]["sessions"][0]["allowedActions"]["canCancel"] is True

    assert data["gate_state"]["paper_adapter"]["enabled"] is False
    assert data["gate_state"]["live_adapter"]["enabled"] is False
    assert data["gate_state"]["canary_adapter"]["enabled"] is False
    assert data["gate_state"]["canary_adapter"]["gate_reason"] == "OPENCLAW_CANARY_ADAPTER_ENABLED is not enabled"
    assert data["allowedActions"]["canEnablePaper"] is False
    assert data["allowedActions"]["canEnableCanary"] is False
    assert data["allowedActions"]["canEnableLive"] is False

    audit = data["tool_workflow"]["audit"]
    assert audit["policy_decision_counts"] == {"denied": 1, "allowed": 1}
    assert audit["outcome_counts"] == {"denied": 1, "ok": 1}
    assert audit["entries"][0]["tool_name"] == "paper.execute"
    assert audit["entries"][0]["policy_class"] == "always_blocked"
    assert data["tool_workflow"]["bridge_posture"]["unknown_tools"] == "fail_closed"
    assert payload["meta"]["surfaces"]["openclaw_ops"]["status"] == "ok"
    assert payload["meta"]["surfaces"]["openclaw_tool_workflow_bridge"]["status"] == "ok"


def test_openclaw_ops_surface_degrades_when_adapter_is_not_configured(monkeypatch) -> None:
    for env_name in (
        "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL",
        "PANTHEON_OPENCLAW_ADAPTER_URL",
        "OPENCLAW_GATEWAY_ADAPTER_URL",
    ):
        monkeypatch.delenv(env_name, raising=False)
    client = TestClient(bff_main.app)

    response = client.get(
        "/api/v1/operator/openclaw/ops",
        headers={"Authorization": OPERATOR_AUTH},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]
    assert data["overall_status"] == "unavailable"
    assert data["production_activation"] == "disabled"
    assert data["gate_state"]["paper_adapter"]["enabled"] is False
    assert data["gate_state"]["live_adapter"]["enabled"] is False
    assert data["gate_state"]["canary_adapter"]["enabled"] is False
    assert data["gate_state"]["canary_adapter"]["activation_gate"] == "OPENCLAW_CANARY_ADAPTER_ENABLED"
    assert data["session_lifecycle"]["sessions"] == []
    assert any("OPENCLAW_ADAPTER_URL_NOT_CONFIGURED" in reason for reason in data["degradation"]["reasons"])
    assert payload["meta"]["surfaces"]["openclaw_ops"]["status"] == "unavailable"


def test_openclaw_session_commands_require_auth_role_and_idempotency(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", BASE_URL)
    client = TestClient(bff_main.app)
    body = {"agent_id": "agent-alpha", "session_type": "interactive"}

    missing_auth = client.post("/api/v1/operator/openclaw/sessions", json=body)
    assert missing_auth.status_code == 401

    viewer = client.post(
        "/api/v1/operator/openclaw/sessions",
        headers={
            "Authorization": VIEWER_AUTH,
            "X-Idempotency-Key": "idmp-openclaw-1",
        },
        json=body,
    )
    assert viewer.status_code == 403

    missing_idempotency = client.post(
        "/api/v1/operator/openclaw/sessions",
        headers={"Authorization": OPERATOR_AUTH},
        json=body,
    )
    assert missing_idempotency.status_code == 400
    assert missing_idempotency.json()["detail"]["error"]["details"]["precondition_failed"] == "idempotency_key"


def test_openclaw_session_create_forwards_operator_and_idempotency(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", BASE_URL)
    recorder = _Recorder(
        {
            ("POST", f"{BASE_URL}/api/openclaw-adapter/lifecycle/sessions"): {
                "status": "ok",
                "replayed": False,
                "session": {
                    "session_id": "oc-sess-created",
                    "agent_id": "agent-alpha",
                    "session_type": "interactive",
                    "state": "active",
                },
            }
        }
    )
    client = TestClient(bff_main.app)

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", recorder):
        response = client.post(
            "/api/v1/operator/openclaw/sessions",
            headers={
                "Authorization": OPERATOR_AUTH,
                "X-Idempotency-Key": "idmp-openclaw-create",
            },
            json={
                "agent_id": "agent-alpha",
                "session_type": "interactive",
                "context_bundle": {"ticket_id": "rt-1"},
            },
        )

    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["data"]["command"] == "OpenClawCreateSession"
    assert payload["data"]["session"]["session_id"] == "oc-sess-created"

    method, url, headers, body = recorder.calls[0]
    assert method == "POST"
    assert url == f"{BASE_URL}/api/openclaw-adapter/lifecycle/sessions"
    assert headers["X-operator-id"] == "op-2"
    assert headers["X-idempotency-key"] == "idmp-openclaw-create"
    assert body["agent_id"] == "agent-alpha"
    assert body["context_bundle"] == {"ticket_id": "rt-1"}
