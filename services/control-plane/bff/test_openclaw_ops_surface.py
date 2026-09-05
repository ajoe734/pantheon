from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional, Tuple
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from services.control_plane.bff import main as bff_main
from openclaw_ops_client import OpenClawOpsClient


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


def _error_details(payload: Dict[str, Any]) -> Dict[str, Any]:
    detail_error = payload.get("detail", {}).get("error") if isinstance(payload.get("detail"), dict) else None
    error = detail_error if isinstance(detail_error, dict) else payload.get("error")
    return error.get("details", {}) if isinstance(error, dict) else {}


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


def test_openclaw_ops_surface_projects_effective_skill_descriptors(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", BASE_URL)
    responses = _healthy_payloads()
    responses[
        (
            "GET",
            f"{BASE_URL}/api/openclaw-adapter/tools?agent_id=management-ai&mode=kernel_debug&operator_role=operator",
        )
    ] = {
        "status": "ok",
        "schema_version": "assistant_skill_descriptor.v1",
        "upstream_status": "ok",
        "agent_id": "management-ai",
        "mode": "kernel_debug",
        "operator_role": "operator",
        "policy_allowed_tools": ["assistant.command"],
        "policy_blocked_tools": [],
        "effective_tools": ["assistant.command"],
        "effective_workflows": [],
        "effective_skills": [
            {
                "id": "assistant.command",
                "title": "Assistant Command Authorization",
                "surface": "assistant_command",
                "mode_gate": {
                    "type": "allowlist",
                    "default": "deny",
                    "allowed_modes": ["kernel_observe", "kernel_debug"],
                },
                "role": "operator",
                "confirm_policy": {"required": False},
                "input_schema": {"type": "object"},
                "handler_ref": "openclaw.tool:assistant.command",
                "result_surface": "assistant_command_authorization",
            },
        ],
    }
    recorder = _Recorder(responses)
    client = TestClient(bff_main.app)

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", recorder):
        response = client.get(
            "/api/v1/operator/openclaw/ops?agent_id=management-ai&mode=kernel_debug",
            headers={"Authorization": OPERATOR_AUTH},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    effective = payload["data"]["tool_workflow"]["effective_tools"]
    assert effective["schema_version"] == "assistant_skill_descriptor.v1"
    assert effective["effective_skills"][0]["id"] == "assistant.command"
    assert effective["effective_skills"][0]["handler_ref"] == "openclaw.tool:assistant.command"
    assert len(effective["effective_skills"]) == 1
    assert payload["meta"]["surfaces"]["openclaw_effective_tools"]["status"] == "ok"

    tool_call = [call for call in recorder.calls if "/api/openclaw-adapter/tools?" in call[1]][0]
    _, _, headers, _ = tool_call
    assert headers["X-operator-id"] == "op-2"
    assert headers["X-operator-role"] == "operator"


def test_openclaw_ops_client_authorizes_assistant_skill(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", BASE_URL)
    recorder = _Recorder({
        (
            "POST",
            f"{BASE_URL}/api/openclaw-adapter/assistant/skills/assistant.command/authorize",
        ): {
            "status": "ok",
            "data": {
                "status": "allowed",
                "skill_id": "assistant.command",
            },
        }
    })

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", recorder):
        result = OpenClawOpsClient(timeout_seconds=1.5).authorize_assistant_skill(
            skill_id="assistant.command",
            operator_id="op-2",
            mode="kernel_debug",
            operator_role="operator",
            control_mode={"active": True, "mode": "kernel_debug", "activation_id": "act-1"},
            session_id="conv-1",
            request_type="assistant_command_authorize",
            audit_extra={"archive": True},
            trace_id="trace-1",
        )

    assert result["data"]["status"] == "allowed"
    method, url, headers, body = recorder.calls[0]
    assert method == "POST"
    assert url.endswith("/api/openclaw-adapter/assistant/skills/assistant.command/authorize")
    assert headers["X-operator-id"] == "op-2"
    assert headers["X-operator-role"] == "operator"
    assert headers["X-assistant-mode"] == "kernel_debug"
    assert headers["X-trace-id"] == "trace-1"
    assert body["mode"] == "kernel_debug"
    assert body["operator_role"] == "operator"
    assert body["control_mode"]["activation_id"] == "act-1"
    assert body["session_id"] == "conv-1"
    assert body["request_type"] == "assistant_command_authorize"
    assert body["audit_extra"] == {"archive": True}


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
    assert _error_details(missing_idempotency.json())["precondition_failed"] == "idempotency_key"


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


# --------------------------------------------------------------------------- #
# OpenClaw Broker Adapter Readiness Surface (SVC-OPENCLAW-BROKER-ADAPTER-ACTIVATION-READY)
# GET /api/v1/operator/openclaw/broker-adapter-readiness
# AC4: BFF surfaces gate reason and adapter readiness without claiming live activation.
# --------------------------------------------------------------------------- #

_BROKER_CAPS_DEFERRED = {
    "sandbox_adapter_state": "activation_ready",
    "sandbox_gate": "OPENCLAW_PAPER_ADAPTER_ENABLED",
    "paper_adapter_state": "gated",
    "paper_adapter_gate": "OPENCLAW_PAPER_ADAPTER_ENABLED",
    "canary_adapter_state": "fail_closed",
    "canary_adapter_gate": "OPENCLAW_CANARY_ADAPTER_ENABLED",
    "live_adapter_state": "fail_closed",
    "live_adapter_gate": "OPENCLAW_LIVE_ADAPTER_ENABLED",
    "paper_adapter_enabled": False,
    "live_adapter_enabled": False,
    "broker_sidecar_configured": True,
    "runtime_manager_configured": True,
    "canary_adapter_enabled": False,
    "canary_execution_enabled": False,
    "is_real_capital": False,
    "is_real_order": False,
}

_BROKER_CAPS_PAPER_ENABLED = {
    **_BROKER_CAPS_DEFERRED,
    "paper_adapter_state": "enabled",
    "paper_adapter_enabled": True,
}


def test_broker_adapter_readiness_projects_fail_closed_live_and_canary(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", BASE_URL)
    recorder = _Recorder(
        {("GET", f"{BASE_URL}/api/openclaw-adapter/broker/capabilities"): _BROKER_CAPS_DEFERRED}
    )
    client = TestClient(bff_main.app)

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", recorder):
        response = client.get(
            "/api/v1/operator/openclaw/broker-adapter-readiness",
            headers={"Authorization": OPERATOR_AUTH},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]

    # AC1: explicit capability states
    assert data["sandbox_adapter_state"] == "activation_ready"
    assert data["paper_adapter_state"] == "gated"
    assert data["canary_adapter_state"] == "fail_closed"
    assert data["live_adapter_state"] == "fail_closed"

    # AC4: gate reasons are present and explain why
    assert "gate" in data["sandbox_gate_reason"].lower() or "enabled" in data["sandbox_gate_reason"].lower()
    assert data["canary_gate_reason"] == "fail_closed_explicit_gate_required"
    assert data["live_gate_reason"] == "fail_closed_explicit_gate_required"

    # Always fail-closed
    assert data["live_execution_enabled"] is False
    assert data["canary_execution_enabled"] is False
    assert data["is_real_capital"] is False
    assert data["is_real_order"] is False

    # BFF activation command is always not_exposed
    assert data["bff_activation_command"] == "not_exposed"

    # Wrapper fields
    assert payload["surface"] == "openclaw_broker_adapter_readiness"
    assert "snapshot_at" in payload


def test_broker_adapter_readiness_paper_enabled_state(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", BASE_URL)
    recorder = _Recorder(
        {("GET", f"{BASE_URL}/api/openclaw-adapter/broker/capabilities"): _BROKER_CAPS_PAPER_ENABLED}
    )
    client = TestClient(bff_main.app)

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", recorder):
        response = client.get(
            "/api/v1/operator/openclaw/broker-adapter-readiness",
            headers={"Authorization": OPERATOR_AUTH},
        )

    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["paper_adapter_state"] == "enabled"
    assert data["paper_gate_reason"] == "enabled_by_adapter"
    # live remains fail-closed even when paper is enabled
    assert data["live_adapter_state"] == "fail_closed"
    assert data["live_execution_enabled"] is False
    assert data["canary_execution_enabled"] is False
    assert data["is_real_capital"] is False
    assert data["is_real_order"] is False


def test_broker_adapter_readiness_requires_auth(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", BASE_URL)
    client = TestClient(bff_main.app)
    response = client.get("/api/v1/operator/openclaw/broker-adapter-readiness")
    assert response.status_code == 401


def test_broker_adapter_readiness_requires_operator_role(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", BASE_URL)
    client = TestClient(bff_main.app)
    response = client.get(
        "/api/v1/operator/openclaw/broker-adapter-readiness",
        headers={"Authorization": VIEWER_AUTH},
    )
    assert response.status_code == 403


def test_broker_adapter_readiness_degrades_when_adapter_unconfigured(monkeypatch) -> None:
    for env_name in (
        "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL",
        "PANTHEON_OPENCLAW_ADAPTER_URL",
        "OPENCLAW_GATEWAY_ADAPTER_URL",
    ):
        monkeypatch.delenv(env_name, raising=False)
    client = TestClient(bff_main.app)

    response = client.get(
        "/api/v1/operator/openclaw/broker-adapter-readiness",
        headers={"Authorization": OPERATOR_AUTH},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    # Always fail-closed regardless of degradation
    assert data["live_execution_enabled"] is False
    assert data["canary_execution_enabled"] is False
    assert data["is_real_capital"] is False
    assert data["is_real_order"] is False
    # Overall status reflects unavailability
    assert data["overall_status"] in {"unavailable", "degraded"}
