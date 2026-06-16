"""Tests for the openclaw-gateway-adapter boundary service."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

import httpx


_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# Minimal stubs so the module can be imported without installed deps
# ---------------------------------------------------------------------------

def _stub_foundation():
    """Inject a minimal services.foundation.health stub if not present."""
    if "services.foundation.health" in sys.modules:
        return
    try:
        import services.foundation.health  # noqa: F401
        return
    except ImportError:
        pass
    # Build the package hierarchy
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
        dependency_statuses = [str(v.get("status", "ok")).lower() for v in dep.values() if isinstance(v, dict)]
        status = "ok" if live and ready else "error"
        if status == "ok" and any(value in {"degraded", "error", "unavailable", "failed"} for value in dependency_statuses):
            status = "degraded"
        return {"status": status, "service": service, "live": live, "ready": ready, "dependencies": dep}

    def readiness_status_code(payload):
        return 200 if payload.get("status") == "ok" else 503

    def register_fastapi_health_routes(app, service, *, dependencies=None, metrics=None, details=None):
        from fastapi.responses import JSONResponse

        async def healthz():
            return health_payload(service, dependencies=dependencies, details=details)

        async def livez():
            return health_payload(service, ready=True)

        async def readyz():
            payload = health_payload(service, dependencies=dependencies, details=details)
            return JSONResponse(payload, status_code=readiness_status_code(payload))

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
    sys.modules["services.foundation"] = sys.modules.get("services.foundation") or types.ModuleType("services.foundation")


_stub_foundation()

# Add the adapter directory to path so we can import main directly
_ADAPTER_DIR = os.path.join(os.path.dirname(__file__))
if _ADAPTER_DIR not in sys.path:
    sys.path.insert(0, _ADAPTER_DIR)

import main as adapter_main  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(adapter_main.app)


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


class TestHealthEndpoints(unittest.TestCase):
    def _patch_upstream(self, reachable: bool):
        result = {"reachable": reachable}
        if not reachable:
            result["reason"] = "connection refused"
        return patch.object(adapter_main, "_probe_upstream", return_value=result)

    def test_livez_always_ok(self):
        with self._patch_upstream(False):
            resp = client.get("/livez")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["live"])
        self.assertTrue(body["ready"])
        self.assertNotIn("openclaw_gateway", body["dependencies"])

    def test_healthz_ok_when_upstream_reachable(self):
        with self._patch_upstream(True):
            resp = client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("service", body)

    def test_healthz_degrades_when_upstream_absent(self):
        with self._patch_upstream(False):
            resp = client.get("/healthz")
        # healthz always returns 200 (liveness-like); readyz returns 503
        self.assertEqual(resp.status_code, 200)

    def test_readyz_ok_when_upstream_reachable(self):
        with self._patch_upstream(True):
            resp = client.get("/readyz")
        self.assertEqual(resp.status_code, 200)

    def test_readyz_503_when_upstream_absent(self):
        with self._patch_upstream(False):
            resp = client.get("/readyz")
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["status"], "degraded")
        self.assertTrue(body["live"])
        self.assertFalse(body["ready"])
        self.assertEqual(body["dependencies"]["openclaw_gateway"]["status"], "degraded")

    def test_health_compat_alias(self):
        with self._patch_upstream(True):
            resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("service", body)


# ---------------------------------------------------------------------------
# Upstream status
# ---------------------------------------------------------------------------


class TestUpstreamStatus(unittest.TestCase):
    def test_upstream_reachable(self):
        with patch.object(adapter_main, "_probe_upstream", return_value={"reachable": True, "http_status": 200}):
            resp = client.get("/api/openclaw-adapter/upstream/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["reachable"])

    def test_upstream_absent(self):
        with patch.object(adapter_main, "_probe_upstream", return_value={"reachable": False, "reason": "connection refused"}):
            resp = client.get("/api/openclaw-adapter/upstream/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["reachable"])


class _FakeProbeResponse:
    def __init__(self, status: int = 200, body: bytes = b'{"status":"ok"}') -> None:
        self._status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def getcode(self) -> int:
        return self._status

    def read(self) -> bytes:
        return self._body


class TestUpstreamProbe(unittest.TestCase):
    def test_upstream_ready_true_health_payload_is_reachable(self):
        self.assertTrue(adapter_main._is_healthy_upstream_response(200, b'{"ready":true}'))

    def test_probe_prefers_readyz_for_upstream_readiness(self):
        calls = []

        def fake_urlopen(req, timeout):
            calls.append(req.full_url)
            return _FakeProbeResponse()

        with (
            patch.object(adapter_main, "OPENCLAW_GATEWAY_URL", "http://openclaw.test"),
            patch.object(adapter_main.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            result = adapter_main._probe_upstream()

        self.assertTrue(result["reachable"])
        self.assertEqual(result["probe"], "/readyz")
        self.assertEqual(calls, ["http://openclaw.test/readyz"])

    def test_probe_falls_back_to_healthz_for_legacy_upstream(self):
        calls = []

        def fake_urlopen(req, timeout):
            calls.append(req.full_url)
            if req.full_url.endswith("/readyz"):
                raise adapter_main.urllib.error.HTTPError(
                    req.full_url,
                    404,
                    "not found",
                    hdrs=None,
                    fp=None,
                )
            return _FakeProbeResponse()

        with (
            patch.object(adapter_main, "OPENCLAW_GATEWAY_URL", "http://openclaw.test"),
            patch.object(adapter_main.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            result = adapter_main._probe_upstream()

        self.assertTrue(result["reachable"])
        self.assertEqual(result["probe"], "/healthz")
        self.assertEqual(
            calls,
            [
                "http://openclaw.test/readyz",
                "http://openclaw.test/healthz",
            ],
        )


# ---------------------------------------------------------------------------
# Capability metadata (static — no upstream call)
# ---------------------------------------------------------------------------


class TestCapabilities(unittest.TestCase):
    def test_capabilities_returned_without_upstream(self):
        error = adapter_main.UpstreamClientError(
            status_code=503,
            error_code="UPSTREAM_UNAVAILABLE",
            message="gateway absent",
            retryable=True,
        )
        mock_client = MagicMock()
        mock_client.get_capabilities.side_effect = error
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["activation_state"], "upstream_client_degraded")
        self.assertEqual(body["broker_execution"], "deferred")
        self.assertEqual(body["paper_adapter"], "deferred")
        self.assertEqual(body["live_adapter"], "deferred")
        self.assertEqual(body["canary_adapter"], "deferred")
        self.assertFalse(body["paper_broker"]["paper_adapter_enabled"])
        self.assertEqual(body["paper_broker"]["runtime_binding_check"], "required_for_submit")
        self.assertEqual(
            body["assistant_credential_mounts"]["host_policy"],
            "dedicated_service_user_only",
        )
        self.assertIn("codex", body["assistant_credential_mounts"]["mounts"])
        self.assertNotIn("/srv/pantheon-assistant", str(body["assistant_credential_mounts"]))
        self.assertIn("supported_session_types", body)
        self.assertEqual(body["upstream"]["status"], "degraded")

    def test_capabilities_include_upstream_when_available(self):
        mock_client = MagicMock()
        mock_client.get_capabilities.return_value = {"tools": ["shell"], "sessions": True}
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["activation_state"], "upstream_client_ready")
        self.assertEqual(body["upstream"]["status"], "ok")
        self.assertEqual(body["upstream"]["capabilities"]["tools"], ["shell"])

    def test_capabilities_not_live_execution(self):
        resp = client.get("/api/openclaw-adapter/capabilities")
        body = resp.json()
        self.assertNotEqual(body.get("broker_execution"), "enabled")
        self.assertNotEqual(body.get("live_adapter"), "enabled")

    def test_capabilities_include_governed_search(self):
        resp = client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["governed_search"], "enabled")
        self.assertIn("governed_search", body["activation_gates"])

    def test_assistant_credentials_endpoint_is_sanitized(self):
        resp = client.get("/api/openclaw-adapter/assistant/credentials")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["host_policy"], "dedicated_service_user_only")
        self.assertIn("claude", body["mounts"])
        self.assertNotIn("/srv/pantheon-assistant", str(body))
        self.assertNotIn("/home/pantheon-assistant", str(body))

    def test_assistant_codex_readiness_uses_codex_provider(self):
        with patch.object(
            adapter_main._CODEX_PROVIDER,
            "readiness",
            return_value={"provider": "codex_cli", "ready": False, "status": "degraded"},
        ) as readiness:
            resp = client.get("/api/openclaw-adapter/assistant/readiness/codex?auth_probe=true")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["provider"], "codex_cli")
        readiness.assert_called_once_with(auth_probe=True)

    def test_assistant_provider_list_includes_codex(self):
        with patch.object(
            adapter_main._CODEX_PROVIDER,
            "readiness",
            return_value={"provider": "codex_cli", "ready": True, "status": "ready"},
        ):
            resp = client.get("/api/openclaw-adapter/assistant/providers")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        provider_ids = [p.get("provider") or p.get("provider_id") for p in body["data"]]
        # openclaw is now the primary channel and listed first; codex_cli remains available.
        self.assertIn("codex_cli", provider_ids)
        self.assertEqual(provider_ids[0], "openclaw")

    def test_assistant_skill_authorize_allows_sa_sd_descriptor(self):
        bridge = adapter_main.ToolWorkflowBridge(
            policy=adapter_main.ToolPolicy(allowed_tools=["assistant.sa_sd.generate"]),
            audit_log=adapter_main.BridgeAuditLog(path=tempfile.mktemp(suffix=".jsonl")),
            trace_id_factory=lambda: "trace-skill-1",
        )
        with patch.object(adapter_main, "_BRIDGE", bridge):
            resp = client.post(
                "/api/openclaw-adapter/assistant/skills/assistant.sa_sd.generate/authorize",
                json={
                    "mode": "kernel_debug",
                    "operator_role": "operator",
                    "control_mode": {"active": True, "mode": "kernel_debug", "activation_id": "act-1"},
                    "session_id": "conv-1",
                    "request_type": "assistant_dev_docs_generate",
                },
                headers={"X-Operator-Id": "op-1", "X-Trace-Id": "trace-skill-1"},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["data"]["status"], "allowed")
        self.assertEqual(body["data"]["skill_id"], "assistant.sa_sd.generate")
        self.assertEqual(
            body["data"]["descriptor"]["handler_ref"],
            "bff.route:POST /bff/assistant/dev-docs/generate",
        )
        entries = bridge._audit.read()  # noqa: SLF001 - test asserts route admission audit.
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["request_type"], "assistant_dev_docs_generate")
        self.assertEqual(entries[0]["outcome"], "allowed")

    def test_assistant_skill_authorize_fails_closed_when_skill_not_allowlisted(self):
        bridge = adapter_main.ToolWorkflowBridge(
            policy=adapter_main.ToolPolicy(allowed_tools=[]),
            audit_log=adapter_main.BridgeAuditLog(path=tempfile.mktemp(suffix=".jsonl")),
        )
        with patch.object(adapter_main, "_BRIDGE", bridge):
            resp = client.post(
                "/api/openclaw-adapter/assistant/skills/assistant.sa_sd.generate/authorize",
                json={
                    "mode": "kernel_debug",
                    "operator_role": "operator",
                    "control_mode": {"active": True, "mode": "kernel_debug", "activation_id": "act-1"},
                },
                headers={"X-Operator-Id": "op-1"},
            )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error_code"], "BRIDGE_SKILL_DENIED")

    def test_assistant_codex_reauth_starts_device_flow(self):
        fake_payload = {
            "reauth_session_id": "codex_reauth_1",
            "provider": "codex_cli",
            "status": "pending",
            "verification_uri": "https://auth.openai.com/device",
            "user_code": "ABCD-EFGH",
            "credential_exchange": {
                "bff_handles_credentials": False,
                "frontend_handles_credentials": False,
            },
        }
        bridge = adapter_main.ToolWorkflowBridge(
            policy=adapter_main.ToolPolicy(
                allowed_tools=[adapter_main.ASSISTANT_PROVIDER_REAUTH_TOOL_NAME]
            ),
            audit_log=adapter_main.BridgeAuditLog(path=tempfile.mktemp(suffix=".jsonl")),
            trace_id_factory=lambda: "trace-reauth-1",
        )
        with patch.object(
            adapter_main._CODEX_PROVIDER,
            "start_device_reauth",
            return_value=fake_payload,
        ) as start, patch.object(adapter_main, "_BRIDGE", bridge):
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/codex/reauth",
                json={
                    "reason": "expired",
                    "captureTimeoutSeconds": 3,
                    "mode": "kernel_debug",
                    "operator_role": "operator",
                    "control_mode": {"active": True, "mode": "kernel_debug", "activation_id": "act-1"},
                },
                headers={
                    "X-Operator-Id": "op-1",
                    "X-Trace-Id": "trace-reauth-1",
                    "X-Operator-Role": "operator",
                    "X-Assistant-Mode": "kernel_debug",
                },
            )

        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["data"]["verification_uri"], "https://auth.openai.com/device")
        self.assertEqual(body["data"]["user_code"], "ABCD-EFGH")
        start.assert_called_once_with(
            operator_id="op-1",
            trace_id="trace-reauth-1",
            reason="expired",
            capture_timeout_seconds=3,
            poll_interval_seconds=None,
            max_wait_seconds=None,
        )
        entries = bridge._audit.read()  # noqa: SLF001 - test asserts route admission audit.
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["request_type"], "assistant_provider_reauth")
        self.assertEqual(entries[0]["outcome"], "allowed")

    def test_assistant_codex_reauth_fails_closed_when_skill_not_allowlisted(self):
        bridge = adapter_main.ToolWorkflowBridge(
            policy=adapter_main.ToolPolicy(allowed_tools=[]),
            audit_log=adapter_main.BridgeAuditLog(path=tempfile.mktemp(suffix=".jsonl")),
        )
        with patch.object(adapter_main._CODEX_PROVIDER, "start_device_reauth") as start, patch.object(adapter_main, "_BRIDGE", bridge):
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/codex/reauth",
                json={
                    "reason": "expired",
                    "mode": "kernel_debug",
                    "operator_role": "operator",
                    "control_mode": {"active": True, "mode": "kernel_debug", "activation_id": "act-1"},
                },
                headers={"X-Operator-Id": "op-1", "X-Operator-Role": "operator"},
            )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error_code"], "BRIDGE_SKILL_DENIED")
        start.assert_not_called()

    def test_assistant_codex_reauth_requires_operator(self):
        with patch.object(adapter_main._CODEX_PROVIDER, "start_device_reauth") as start:
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/codex/reauth",
                json={"reason": "expired"},
            )

        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error_code"], "OPERATOR_REQUIRED")
        start.assert_not_called()

    def test_assistant_codex_reauth_status_returns_session(self):
        with patch.object(
            adapter_main._CODEX_PROVIDER,
            "reauth_status",
            return_value={
                "reauth_session_id": "codex_reauth_1",
                "provider": "codex_cli",
                "status": "completed",
                "readiness": {"ready": True},
            },
        ) as status:
            resp = client.get(
                "/api/openclaw-adapter/assistant/providers/codex/reauth/codex_reauth_1",
                headers={"X-Operator-Id": "op-1"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["status"], "completed")
        status.assert_called_once_with("codex_reauth_1")

    def test_assistant_codex_invoke_passes_through_runtime(self):
        fake_result = types.SimpleNamespace(
            provider="codex_cli",
            mode="user",
            status="completed",
            output={"status": "completed", "stdout": "ok"},
            redaction={"provider_invocation": {"enabled": True}},
        )
        with patch.object(adapter_main._CODEX_RUNTIME, "invoke", return_value=fake_result) as invoke:
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/codex/invoke",
                json={"mode": "user", "prompt": "hello", "context_pack": {"x": 1}},
                headers={"X-Operator-Id": "op-1", "X-Trace-Id": "trace-1"},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["data"]["provider"], "codex_cli")
        request = invoke.call_args.args[0]
        self.assertEqual(request.provider, "codex_cli")
        self.assertEqual(request.mode, "user")
        self.assertEqual(request.prompt, "hello")
        self.assertEqual(request.metadata["operator_id"], "op-1")
        self.assertEqual(request.metadata["trace_id"], "trace-1")

    def test_assistant_codex_invoke_preserves_multimodal_payload(self):
        fake_result = types.SimpleNamespace(
            provider="codex_cli",
            mode="user",
            status="completed",
            output={"status": "completed", "stdout": "ok"},
            redaction={"provider_invocation": {"enabled": True}},
        )
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "inspect"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,aW1n"},
                        "attachmentId": "att-1",
                    },
                ],
            }
        ]
        attachments = [
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,aW1n"},
                "attachmentId": "att-1",
            }
        ]
        with patch.object(adapter_main._CODEX_RUNTIME, "invoke", return_value=fake_result) as invoke:
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/codex/invoke",
                json={
                    "mode": "user",
                    "prompt": "hello",
                    "context_pack": {"x": 1},
                    "messages": messages,
                    "attachments": attachments,
                },
                headers={"X-Operator-Id": "op-1"},
            )
        self.assertEqual(resp.status_code, 200)
        request = invoke.call_args.args[0]
        self.assertEqual(request.messages, messages)
        self.assertEqual(request.attachments, attachments)

    def test_assistant_codex_invoke_header_operator_overrides_body_metadata(self):
        fake_result = types.SimpleNamespace(
            provider="codex_cli",
            mode="user",
            status="completed",
            output={"status": "completed", "stdout": "ok"},
            redaction={"provider_invocation": {"enabled": True}},
        )
        with patch.object(adapter_main._CODEX_RUNTIME, "invoke", return_value=fake_result) as invoke:
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/codex/invoke",
                json={
                    "mode": "user",
                    "prompt": "hello",
                    "metadata": {"operator_id": "body-op", "task_id": "ASST-OCGW-003"},
                },
                headers={"X-Operator-Id": "header-op"},
            )
        self.assertEqual(resp.status_code, 200)
        request = invoke.call_args.args[0]
        self.assertEqual(request.metadata["operator_id"], "header-op")
        self.assertEqual(request.metadata["task_id"], "ASST-OCGW-003")

    def test_assistant_codex_invoke_requires_operator_id(self):
        with patch.object(adapter_main._CODEX_RUNTIME, "invoke") as invoke:
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/codex/invoke",
                json={"mode": "user", "prompt": "hello"},
            )
        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body["status"], "provider_error")
        self.assertEqual(body["error_code"], "OPERATOR_REQUIRED")
        invoke.assert_not_called()

    def test_assistant_codex_invoke_returns_provider_error(self):
        with patch.object(
            adapter_main._CODEX_RUNTIME,
            "invoke",
            side_effect=adapter_main.CodexProviderError(
                "CODEX_TIMEOUT",
                "Codex provider timed out after 7s.",
                status_code=504,
                retryable=True,
            ),
        ):
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/codex/invoke",
                json={"mode": "user", "prompt": "hello"},
                headers={"X-Operator-Id": "op-1"},
            )
        self.assertEqual(resp.status_code, 504)
        body = resp.json()
        self.assertEqual(body["error_code"], "CODEX_TIMEOUT")
        self.assertTrue(body["retryable"])

    def test_assistant_repair_worktree_prepare_requires_operator(self):
        resp = client.post(
            "/api/openclaw-adapter/assistant/repair-worktrees/prepare",
            json={"taskId": "MGMT-AI-REPAIR-1", "declaredScope": ["services/control-plane/bff"]},
        )

        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error_code"], "OPERATOR_REQUIRED")

    def test_assistant_repair_worktree_prepare_returns_metadata(self):
        fake_preparation = types.SimpleNamespace(
            to_dict=lambda: {
                "created": True,
                "repair": {
                    "task_id": "MGMT-AI-REPAIR-1",
                    "task_worktree": "/srv/pantheon-assistant/worktrees/mgmt-ai-repair-1",
                    "declared_scope": ["services/control-plane/bff"],
                    "expected_branch": "task/MGMT-AI-REPAIR-1",
                    "remote": "origin",
                    "merge_target": "dev",
                    "require_clean": True,
                },
            }
        )
        with patch.object(adapter_main._REPAIR_WORKFLOW, "prepare", return_value=fake_preparation) as prepare:
            resp = client.post(
                "/api/openclaw-adapter/assistant/repair-worktrees/prepare",
                json={"taskId": "MGMT-AI-REPAIR-1", "declaredScope": ["services/control-plane/bff"]},
                headers={"X-Operator-Id": "op-1", "X-Trace-Id": "trace-1"},
            )

        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["data"]["repair"]["task_id"], "MGMT-AI-REPAIR-1")
        metadata = prepare.call_args.args[0]
        self.assertEqual(metadata["taskId"], "MGMT-AI-REPAIR-1")
        self.assertEqual(metadata["declaredScope"], ["services/control-plane/bff"])
        self.assertEqual(metadata["operator_id"], "op-1")
        self.assertEqual(metadata["trace_id"], "trace-1")

    def test_assistant_repair_worktree_prepare_maps_workflow_error(self):
        with patch.object(
            adapter_main._REPAIR_WORKFLOW,
            "prepare",
            side_effect=adapter_main.AssistantRepairWorkflowError(
                "REPAIR_REPO_URL_NOT_CONFIGURED",
                "Repair repo source missing.",
                status_code=503,
            ),
        ):
            resp = client.post(
                "/api/openclaw-adapter/assistant/repair-worktrees/prepare",
                json={"taskId": "MGMT-AI-REPAIR-1", "declaredScope": ["services/control-plane/bff"]},
                headers={"X-Operator-Id": "op-1"},
            )

        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["status"], "repair_workflow_error")
        self.assertEqual(body["error_code"], "REPAIR_REPO_URL_NOT_CONFIGURED")


# ---------------------------------------------------------------------------
# Session stubs
# ---------------------------------------------------------------------------


class TestSessions(unittest.TestCase):
    def test_list_sessions_degraded_when_upstream_absent(self):
        mock_client = MagicMock()
        mock_client.list_sessions.side_effect = adapter_main.UpstreamClientError(
            status_code=503,
            error_code="UPSTREAM_UNAVAILABLE",
            message="no gateway",
            retryable=True,
        )
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.get("/api/openclaw-adapter/sessions")
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["status"], "upstream_unavailable")
        self.assertEqual(body["sessions"], [])
        self.assertEqual(body["upstream"]["error_code"], "UPSTREAM_UNAVAILABLE")

    def test_list_sessions_uses_upstream_client_when_available(self):
        mock_client = MagicMock()
        mock_client.list_sessions.return_value = [
            {
                "session_id": "sess-1",
                "agent_id": "agent-test",
                "session_type": "interactive",
                "status": "running",
            }
        ]
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.get("/api/openclaw-adapter/sessions")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["sessions"][0]["session_id"], "sess-1")

    def test_get_session_uses_upstream_client_when_available(self):
        mock_client = MagicMock()
        mock_client.get_session.return_value = {
            "session_id": "sess-1",
            "agent_id": "agent-test",
            "session_type": "interactive",
            "status": "running",
        }
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.get("/api/openclaw-adapter/sessions/sess-1")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["session"]["session_id"], "sess-1")
        mock_client.get_session.assert_called_once_with("sess-1")

    def test_create_session_uses_upstream_client_when_available(self):
        mock_client = MagicMock()
        mock_client.create_session.return_value = {
            "session_id": "sess-2",
            "agent_id": "agent-test",
            "session_type": "interactive",
            "status": "created",
        }
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.post(
                "/api/openclaw-adapter/sessions",
                json={"agent_id": "agent-test", "session_type": "interactive"},
            )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["session"]["session_id"], "sess-2")

    def test_cancel_session_uses_upstream_client_when_available(self):
        mock_client = MagicMock()
        mock_client.cancel_session.return_value = {
            "session_id": "sess-2",
            "agent_id": "agent-test",
            "session_type": "interactive",
            "status": "cancel_requested",
        }
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.post("/api/openclaw-adapter/sessions/sess-2/cancel")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["session"]["status"], "cancel_requested")

    def test_create_session_maps_upstream_timeout(self):
        mock_client = MagicMock()
        mock_client.create_session.side_effect = adapter_main.UpstreamClientError(
            status_code=504,
            error_code="UPSTREAM_TIMEOUT",
            message="timeout",
            retryable=True,
        )
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.post(
                "/api/openclaw-adapter/sessions",
                json={"agent_id": "agent-test", "session_type": "interactive"},
            )
        self.assertEqual(resp.status_code, 504)
        body = resp.json()
        self.assertEqual(body["error_code"], "UPSTREAM_TIMEOUT")
        self.assertTrue(body["retryable"])


class TestUpstreamClient(unittest.TestCase):
    def test_http_session_list_is_normalized(self):
        real_client = httpx.Client
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"sessions": [{"id": "sess-1", "agent": "agent-test", "type": "interactive", "status": "running"}]},
            )
        )
        with patch.object(adapter_main.httpx, "Client", side_effect=lambda *args, **kwargs: real_client(transport=transport)):
            sessions = adapter_main.OpenClawUpstreamClient(
                "http://openclaw.test",
                timeout=1,
                retries=0,
            ).list_sessions()
        self.assertEqual(sessions[0]["session_id"], "sess-1")
        self.assertEqual(sessions[0]["agent_id"], "agent-test")

    def test_http_500_retries_then_succeeds(self):
        real_client = httpx.Client
        calls = {"count": 0}

        def handler(request):
            calls["count"] += 1
            if calls["count"] == 1:
                return httpx.Response(500, json={"error": "not ready"})
            return httpx.Response(200, json={"sessions": []})

        transport = httpx.MockTransport(handler)
        with patch.object(adapter_main.httpx, "Client", side_effect=lambda *args, **kwargs: real_client(transport=transport)):
            sessions = adapter_main.OpenClawUpstreamClient(
                "http://openclaw.test",
                timeout=1,
                retries=1,
            ).list_sessions()
        self.assertEqual(sessions, [])
        self.assertEqual(calls["count"], 2)

    def test_timeout_maps_to_retryable_gateway_timeout(self):
        real_client = httpx.Client
        transport = httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.TimeoutException("slow")))
        with patch.object(adapter_main.httpx, "Client", side_effect=lambda *args, **kwargs: real_client(transport=transport)):
            with self.assertRaises(adapter_main.UpstreamClientError) as ctx:
                adapter_main.OpenClawUpstreamClient(
                    "http://openclaw.test",
                    timeout=1,
                    retries=0,
                ).list_sessions()
        self.assertEqual(ctx.exception.status_code, 504)
        self.assertEqual(ctx.exception.error_code, "UPSTREAM_TIMEOUT")
        self.assertTrue(ctx.exception.retryable)


# ---------------------------------------------------------------------------
# Production broker guard
# ---------------------------------------------------------------------------


class TestProductionGuard(unittest.TestCase):
    def test_production_broker_disabled_by_default(self):
        self.assertFalse(adapter_main._PRODUCTION_BROKER_ENABLED)

    def test_paper_adapter_disabled_by_default(self):
        self.assertFalse(adapter_main._PAPER_ADAPTER_ENABLED)

    def test_live_adapter_disabled_by_default(self):
        self.assertFalse(adapter_main._LIVE_ADAPTER_ENABLED)

    def test_canary_adapter_disabled_by_default(self):
        self.assertFalse(adapter_main._CANARY_ADAPTER_ENABLED)

    def test_capital_binding_disabled_by_default(self):
        self.assertFalse(adapter_main._CAPITAL_BINDING_ENABLED)


class TestCapabilityFenceCompleteness(unittest.TestCase):
    """Verify the capability snapshot exposes all fail-closed execution paths."""

    def test_capabilities_capital_binding_deferred(self):
        resp = client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["capital_binding"], "deferred")

    def test_capabilities_fail_closed_flag_set(self):
        resp = client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["fail_closed"])

    def test_capabilities_activation_gates_present(self):
        resp = client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        gates = body.get("activation_gates", {})
        self.assertIn("broker_execution", gates)
        self.assertIn("paper_adapter", gates)
        self.assertIn("live_adapter", gates)
        self.assertIn("canary_adapter", gates)
        self.assertIn("capital_binding", gates)

    def test_no_execution_paths_enabled(self):
        resp = client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for field in ("broker_execution", "paper_adapter", "live_adapter", "canary_adapter", "capital_binding"):
            self.assertNotEqual(body.get(field), "enabled", f"Expected {field} to be deferred, not enabled")


# ---------------------------------------------------------------------------
# Governed search route
# ---------------------------------------------------------------------------


class TestGovernedSearchRoute(unittest.TestCase):
    def test_search_requires_operator_id(self):
        resp = client.post(
            "/api/openclaw-adapter/search/query",
            json={
                "query": "momentum volatility",
                "persona_id": "persona-alpha",
                "workspace_id": "workspace-research",
            },
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error_code"], "SEARCH_OPERATOR_REQUIRED")

    def test_search_returns_sanitized_citation_pack(self):
        fake_repo = MagicMock()
        fake_search = MagicMock()
        fake_search.search.return_value = {
            "status": "ok",
            "request_id": "search-1",
            "trace_id": "trace-1",
            "results": [
                {
                    "evidence_bundle_id": "evbundle-1",
                    "citation_pack": [
                        {
                            "citation_label": "note#1",
                            "evidence_bundle_id": "evbundle-1",
                        }
                    ],
                    "relevance_score": 0.75,
                }
            ],
            "rejected_items_count": 0,
            "filters_applied": {
                "pre_ranking_filter": "acl_license_workspace_environment",
                "available_time": "not_future",
            },
        }
        with (
            patch.object(adapter_main, "_OPENCLAW_SEARCH_REPOSITORY", fake_repo),
            patch.object(adapter_main, "_OPENCLAW_SEARCH_GATEWAY", fake_search),
        ):
            resp = client.post(
                "/api/openclaw-adapter/search/query",
                json={
                    "request_id": "search-1",
                    "trace_id": "trace-1",
                    "query": "momentum volatility",
                    "persona_id": "persona-alpha",
                    "workspace_id": "workspace-research",
                    "access_scopes": ["research"],
                    "license_scopes": ["internal"],
                    "source_types": ["internal_note"],
                },
                headers={"X-Operator-Id": "op-1"},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        result = body["results"][0]
        self.assertEqual(result["evidence_bundle_id"], "evbundle-1")
        self.assertEqual(result["citation_pack"][0]["citation_label"], "note#1")
        self.assertNotIn("answer_context", result)
        self.assertNotIn("matched_items", result)
        self.assertNotIn("raw_payload", result)
        fake_repo.reload.assert_called_once()
        fake_search.search.assert_called_once()

    def test_search_policy_error_maps_to_400(self):
        fake_repo = MagicMock()
        fake_search = MagicMock()
        fake_search.search.side_effect = adapter_main.OpenClawSearchPolicyError("query is required")
        with (
            patch.object(adapter_main, "_OPENCLAW_SEARCH_REPOSITORY", fake_repo),
            patch.object(adapter_main, "_OPENCLAW_SEARCH_GATEWAY", fake_search),
        ):
            resp = client.post(
                "/api/openclaw-adapter/search/query",
                json={
                    "query": " ",
                    "persona_id": "persona-alpha",
                    "workspace_id": "workspace-research",
                },
                headers={"X-Operator-Id": "op-1"},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error_code"], "SEARCH_POLICY_ERROR")


# ---------------------------------------------------------------------------
# Paper broker adapter HTTP routes
# ---------------------------------------------------------------------------


class TestPaperBrokerRoutes(unittest.TestCase):
    """HTTP route coverage for the paper broker adapter endpoints in main.py."""

    def _disabled_broker(self):
        from paper_broker_adapter import PaperBrokerAdapter, PaperBrokerAdapterError

        mock = MagicMock(spec=PaperBrokerAdapter)
        mock.submit_paper_order.side_effect = PaperBrokerAdapterError(
            "PAPER_ADAPTER_DISABLED",
            "Paper broker adapter is disabled.",
            status_code=503,
        )
        mock.list_paper_orders.side_effect = PaperBrokerAdapterError(
            "PAPER_ADAPTER_DISABLED",
            "Paper broker adapter is disabled.",
            status_code=503,
        )
        mock.get_paper_order.side_effect = PaperBrokerAdapterError(
            "PAPER_ADAPTER_DISABLED",
            "Paper broker adapter is disabled.",
            status_code=503,
        )
        mock.reject_live_order.side_effect = PaperBrokerAdapterError(
            "LIVE_ADAPTER_DISABLED",
            "Live broker execution is disabled.",
            status_code=403,
        )
        mock.read_audit.return_value = []
        mock.capability_snapshot.return_value = {
            "paper_adapter_enabled": False,
            "live_adapter_enabled": False,
            "broker_sidecar_configured": False,
            "deployment_stage": "none",
            "is_real_capital": False,
            "is_real_order": False,
        }
        return mock

    def test_submit_paper_order_returns_503_when_gate_closed(self):
        with patch.object(adapter_main, "_PAPER_BROKER", self._disabled_broker()):
            resp = client.post(
                "/api/openclaw-adapter/broker/paper/orders",
                json={
                    "capital_pool_id": "pool-1",
                    "strategy_id": "strat-1",
                    "symbol": "AAPL",
                    "qty": 10.0,
                    "side": "buy",
                },
                headers={"X-Operator-Id": "op-1"},
            )
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["error_code"], "PAPER_ADAPTER_DISABLED")

    def test_submit_paper_order_requires_operator_id(self):
        resp = client.post(
            "/api/openclaw-adapter/broker/paper/orders",
            json={
                "capital_pool_id": "pool-1",
                "strategy_id": "strat-1",
                "symbol": "AAPL",
                "qty": 10.0,
                "side": "buy",
            },
        )
        self.assertEqual(resp.status_code, 401)

    def test_submit_paper_order_succeeds_when_gate_open(self):
        from paper_broker_adapter import PaperBrokerAdapter

        mock = MagicMock(spec=PaperBrokerAdapter)
        mock.submit_paper_order.return_value = {
            "status": "ok",
            "order": {
                "order_id": "ord-abc",
                "fill_price": 100.0,
                "fill_qty": 10.0,
                "status": "filled",
                "is_real_order": False,
                "is_real_capital": False,
                "sim_fill_flag": True,
                "deployment_stage": "paper",
            },
        }
        with patch.object(adapter_main, "_PAPER_BROKER", mock):
            resp = client.post(
                "/api/openclaw-adapter/broker/paper/orders",
                json={
                    "capital_pool_id": "pool-1",
                    "strategy_id": "strat-1",
                    "symbol": "AAPL",
                    "qty": 10.0,
                    "side": "buy",
                },
                headers={"X-Operator-Id": "op-1"},
            )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["order"]["order_id"], "ord-abc")
        self.assertFalse(body["order"]["is_real_order"])

    def test_cancel_paper_order_forwards_operator_and_trace(self):
        from paper_broker_adapter import PaperBrokerAdapter

        mock = MagicMock(spec=PaperBrokerAdapter)
        mock.cancel_paper_order.return_value = {
            "status": "ok",
            "order": {
                "order_id": "ord-abc",
                "status": "canceled",
                "is_real_order": False,
                "is_real_capital": False,
            },
        }
        with patch.object(adapter_main, "_PAPER_BROKER", mock):
            resp = client.post(
                "/api/openclaw-adapter/broker/paper/orders/ord-abc/cancel",
                headers={"X-Operator-Id": "op-1", "X-Trace-Id": "trace-cancel-1"},
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["order"]["status"], "canceled")
        self.assertFalse(body["order"]["is_real_order"])
        mock.cancel_paper_order.assert_called_once_with(
            "ord-abc",
            operator_id="op-1",
            trace_id="trace-cancel-1",
        )

    def test_live_order_always_rejected(self):
        from live_gate_adapter import LiveGateAdapter, LiveGateError

        mock_gate = MagicMock(spec=LiveGateAdapter)
        mock_gate.reject_live_order.side_effect = LiveGateError(
            "LIVE_EXECUTION_DISABLED",
            "Live broker execution is permanently disabled.",
            status_code=403,
            gate="live_execution",
        )
        with patch.object(adapter_main, "_LIVE_GATE", mock_gate):
            resp = client.post(
                "/api/openclaw-adapter/broker/live/orders",
                headers={"X-Operator-Id": "op-1"},
            )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error_code"], "LIVE_EXECUTION_DISABLED")

    def test_live_order_rejected_when_paper_gate_open(self):
        from live_gate_adapter import LiveGateAdapter, LiveGateError

        mock_gate = MagicMock(spec=LiveGateAdapter)
        mock_gate.reject_live_order.side_effect = LiveGateError(
            "LIVE_EXECUTION_DISABLED",
            "Live broker execution is permanently disabled.",
            status_code=403,
            gate="live_execution",
        )
        with patch.object(adapter_main, "_LIVE_GATE", mock_gate):
            resp = client.post(
                "/api/openclaw-adapter/broker/live/orders",
                headers={"X-Operator-Id": "op-1"},
            )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error_code"], "LIVE_EXECUTION_DISABLED")

    def test_canary_order_always_rejected(self):
        resp = client.post(
            "/api/openclaw-adapter/broker/canary/orders",
            headers={"X-Operator-Id": "op-1"},
        )
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body["error_code"], "CANARY_EXECUTION_DISABLED")
        self.assertEqual(body["gate"], "canary_execution")
        self.assertFalse(body["details"]["is_real_order"])
        self.assertFalse(body["details"]["is_real_capital"])
        self.assertEqual(body["details"]["configured_gate"], "OPENCLAW_CANARY_ADAPTER_ENABLED")

    def test_broker_capabilities_endpoint(self):
        from paper_broker_adapter import PaperBrokerAdapter

        mock = MagicMock(spec=PaperBrokerAdapter)
        mock.capability_snapshot.return_value = {
            "sandbox_adapter_state": "activation_ready",
            "sandbox_gate": "OPENCLAW_PAPER_ADAPTER_ENABLED",
            "paper_adapter_enabled": False,
            "live_adapter_enabled": False,
            "broker_sidecar_configured": False,
            "deployment_stage": "none",
            "is_real_capital": False,
            "is_real_order": False,
        }
        with patch.object(adapter_main, "_PAPER_BROKER", mock):
            resp = client.get("/api/openclaw-adapter/broker/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["live_adapter_enabled"])
        self.assertFalse(body["canary_adapter_enabled"])
        self.assertFalse(body["canary_execution_enabled"])
        self.assertEqual(body["canary_gate"], "OPENCLAW_CANARY_ADAPTER_ENABLED")
        self.assertFalse(body["is_real_order"])
        self.assertFalse(body["is_real_capital"])

    def test_broker_audit_endpoint_returns_list(self):
        from paper_broker_adapter import PaperBrokerAdapter

        mock = MagicMock(spec=PaperBrokerAdapter)
        mock.read_audit.return_value = [
            {"event": "paper_order_intent", "operator_id": "op-1"},
        ]
        with patch.object(adapter_main, "_PAPER_BROKER", mock):
            resp = client.get("/api/openclaw-adapter/broker/audit")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["entries"][0]["event"], "paper_order_intent")


class TestOpenClawAssistantProvider(unittest.TestCase):
    """Contract tests for the openclaw assistant provider route.

    Verifies that:
    - POST /api/openclaw-adapter/assistant/providers/openclaw/invoke requires X-Operator-Id
    - The route delegates to AssistantOpenClawProvider and returns the standard envelope
    - On gateway error the route degrades cleanly (HTTP 200, status=degraded)
    - GET /api/openclaw-adapter/assistant/readiness/openclaw returns ready when URL configured
    - GET /api/openclaw-adapter/assistant/providers lists openclaw as the first provider
    - GET /api/openclaw-adapter/capabilities includes assistant_openclaw field
    """

    def test_openclaw_invoke_requires_operator_id(self):
        resp = client.post(
            "/api/openclaw-adapter/assistant/providers/openclaw/invoke",
            json={"prompt": "hello"},
        )
        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body["error_code"], "OPERATOR_REQUIRED")

    def test_openclaw_invoke_returns_completed_result_on_success(self):
        fake_response_body = {
            "status": "completed",
            "agent_id": "main",
            "output": {"text": "OpenClaw agent answer."},
        }

        def fake_invoke(self_inner, prompt, *, mode="user", context_pack=None, metadata=None,
                        messages=None, operator_id=None, trace_id=None):
            from assistant_openclaw_provider import OpenClawProviderResult
            return OpenClawProviderResult(
                provider="openclaw",
                mode=mode,
                status="completed",
                output={
                    "json_events": [
                        {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "OpenClaw agent answer."}}
                    ],
                    "agent_id": "main",
                    "request_id": "test-req-1",
                    "duration_ms": 123,
                    "upstream": fake_response_body,
                },
                redaction={"provider_invocation": {"redacted_fields": 0}},
            )

        with patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER.__class__, "invoke", fake_invoke):
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/invoke",
                json={"prompt": "What is the portfolio status?", "mode": "user"},
                headers={"X-Operator-Id": "test-operator", "X-Trace-Id": "trace-oc-1"},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        data = body["data"]
        self.assertEqual(data["provider"], "openclaw")
        self.assertEqual(data["mode"], "user")
        self.assertEqual(data["status"], "completed")
        json_events = data["output"]["json_events"]
        self.assertEqual(len(json_events), 1)
        self.assertEqual(json_events[0]["item"]["text"], "OpenClaw agent answer.")

    def test_openclaw_invoke_degrades_cleanly_on_gateway_error(self):
        from assistant_openclaw_provider import OpenClawProviderError as ProvError

        def fake_invoke_error(self_inner, prompt, **kwargs):
            raise ProvError(
                "OpenClaw gateway is unreachable: Connection refused",
                status_code=503,
                error_code="OPENCLAW_GATEWAY_UNREACHABLE",
            )

        with patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER.__class__, "invoke", fake_invoke_error):
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/invoke",
                json={"prompt": "test prompt"},
                headers={"X-Operator-Id": "test-operator"},
            )

        # Degraded returns HTTP 200 so BFF can apply fallback — not a transport error.
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        data = body["data"]
        self.assertEqual(data["provider"], "openclaw")
        self.assertEqual(data["status"], "degraded")
        self.assertEqual(data["output"]["reason"], "OPENCLAW_GATEWAY_UNREACHABLE")

    def test_openclaw_readiness_ready_when_url_configured(self):
        with patch.dict(os.environ, {"OPENCLAW_GATEWAY_URL": "http://openclaw-gateway:18789"}):
            # Re-read via the readiness endpoint directly
            from assistant_openclaw_provider import AssistantOpenClawProvider
            prov = AssistantOpenClawProvider(gateway_url="http://openclaw-gateway:18789")
            result = prov.readiness(auth_probe=False)

        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["ready"])
        self.assertEqual(result["provider"], "openclaw")

    def test_openclaw_readiness_not_configured_when_url_absent(self):
        from assistant_openclaw_provider import AssistantOpenClawProvider
        prov = AssistantOpenClawProvider(gateway_url="")
        result = prov.readiness()
        self.assertFalse(result["ready"])
        self.assertEqual(result["status"], "not_configured")

    def test_list_providers_includes_openclaw_first(self):
        resp = client.get("/api/openclaw-adapter/assistant/providers")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        providers = body["data"]
        self.assertGreater(len(providers), 0)
        provider_ids = [p.get("provider") or p.get("provider_id") for p in providers]
        self.assertIn("openclaw", provider_ids)
        self.assertEqual(provider_ids[0], "openclaw")

    def test_capabilities_includes_assistant_openclaw(self):
        resp = client.get("/api/openclaw-adapter/capabilities")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("assistant_openclaw", body)

    def test_openclaw_ops_client_routes_openclaw_provider_to_correct_path(self):
        """Contract test: BFF OpenClawOpsClient routes openclaw to the correct adapter path."""
        import sys
        from pathlib import Path as _Path
        bff_dir = str((_Path(__file__).resolve().parent.parent / "control-plane" / "bff"))
        if bff_dir not in sys.path:
            sys.path.insert(0, bff_dir)
        from openclaw_ops_client import OpenClawOpsClient
        import json
        import urllib.request

        recorded: dict = {}

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def getcode(self): return 200
            def read(self): return json.dumps({"status": "ok", "data": {"provider": "openclaw", "status": "completed", "output": {"json_events": [{"type": "item.completed", "item": {"text": "answer"}}]}}}).encode()

        def fake_urlopen(req, timeout):
            recorded["url"] = req.full_url
            recorded["body"] = json.loads(req.data.decode())
            return FakeResponse()

        with patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
            OpenClawOpsClient(base_url="http://adapter:8104", timeout_seconds=5).invoke_assistant_provider(
                provider="openclaw",
                mode="user",
                prompt="Hello OpenClaw agent",
                context_pack={"source": "bff"},
                operator_id="op-1",
            )

        self.assertEqual(
            recorded["url"],
            "http://adapter:8104/api/openclaw-adapter/assistant/providers/openclaw/invoke",
        )
        self.assertEqual(recorded["body"]["prompt"], "Hello OpenClaw agent")
        self.assertEqual(recorded["body"]["mode"], "user")


if __name__ == "__main__":
    unittest.main()
