"""Tests for the openclaw-gateway-adapter boundary service."""
from __future__ import annotations

import copy
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
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
# BFF-to-adapter assistant service authentication
# ---------------------------------------------------------------------------


class TestAssistantServiceAuthentication(unittest.TestCase):
    def _auth_config(self, *, token: str, required: bool):
        return patch.multiple(
            adapter_main,
            _ASSISTANT_SERVICE_TOKEN=token,
            _ASSISTANT_SERVICE_AUTH_REQUIRED=required,
        )

    def test_configured_token_protects_provider_invoke(self):
        with (
            self._auth_config(token="adapter-secret", required=True),
            patch.object(adapter_main._CODEX_RUNTIME, "invoke") as codex_invoke,
        ):
            provider_resp = client.post(
                "/api/openclaw-adapter/assistant/providers/codex/invoke",
                json={"mode": "kernel_debug", "prompt": "inspect"},
                headers={"X-Operator-Id": "op-1"},
            )
        self.assertEqual(provider_resp.status_code, 401, provider_resp.text)
        self.assertEqual(provider_resp.json()["error_code"], "ASSISTANT_SERVICE_AUTH_DENIED")
        codex_invoke.assert_not_called()

    def test_service_token_uses_constant_time_digest_comparison(self):
        compare_digest = adapter_main.hmac.compare_digest
        with (
            self._auth_config(token="adapter-secret", required=True),
            patch.object(adapter_main.hmac, "compare_digest", wraps=compare_digest) as compared,
        ):
            wrong_resp = client.get(
                "/api/openclaw-adapter/assistant/credentials",
                headers={"X-Pantheon-Service-Token": "wrong-secret"},
            )
            valid_resp = client.get(
                "/api/openclaw-adapter/assistant/credentials",
                headers={"X-Pantheon-Service-Token": "adapter-secret"},
            )

        self.assertEqual(wrong_resp.status_code, 401, wrong_resp.text)
        self.assertEqual(valid_resp.status_code, 200, valid_resp.text)
        self.assertEqual(compared.call_count, 2)
        for compared_call in compared.call_args_list:
            presented_digest, expected_digest = compared_call.args
            self.assertIsInstance(presented_digest, bytes)
            self.assertIsInstance(expected_digest, bytes)
            self.assertEqual(len(presented_digest), 32)
            self.assertEqual(len(expected_digest), 32)

    def test_required_auth_without_token_fails_closed_and_degrades_readiness(self):
        with (
            self._auth_config(token="", required=True),
            patch.object(adapter_main, "_probe_upstream", return_value={"reachable": True}),
        ):
            assistant_resp = client.get("/api/openclaw-adapter/assistant/credentials")
            readiness_resp = client.get("/readyz")

        self.assertEqual(assistant_resp.status_code, 503, assistant_resp.text)
        self.assertEqual(
            assistant_resp.json()["error_code"],
            "ASSISTANT_SERVICE_AUTH_MISCONFIGURED",
        )
        self.assertEqual(readiness_resp.status_code, 503, readiness_resp.text)
        self.assertEqual(
            readiness_resp.json()["dependencies"]["assistant_service_auth"]["status"],
            "error",
        )

    def test_service_token_does_not_guard_non_assistant_adapter_routes(self):
        with (
            self._auth_config(token="adapter-secret", required=True),
            patch.object(
                adapter_main,
                "_probe_upstream",
                return_value={"reachable": True, "http_status": 200},
            ),
        ):
            resp = client.get("/api/openclaw-adapter/upstream/status")

        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["reachable"])


class TestCronServiceAuthentication(unittest.TestCase):
    _CRON_CONTRACTS = {
        "pantheon.ingest": {
            "schedule": "0 */6 * * *",
            "policy_id": "oc002.cron.ingest",
            "upstream_entrypoint": "research.ingest",
        },
        "pantheon.review": {
            "schedule": "15 7 * * 1-5",
            "policy_id": "oc002.cron.review",
            "upstream_entrypoint": "governance.review",
        },
        "pantheon.retrain": {
            "schedule": "0 2 * * 1-5",
            "policy_id": "oc002.cron.retrain",
            "upstream_entrypoint": "learning.retrain",
        },
        "pantheon.deploy": {
            "schedule": "*/15 * * * *",
            "policy_id": "oc002.cron.deploy",
            "upstream_entrypoint": "deployment.plan",
        },
        "pantheon.persona.first-evaluation": {
            "schedule": "*/15 * * * *",
            "policy_id": "oc002.cron.persona-first-evaluation",
            "upstream_entrypoint": "evaluation.persona.first",
        },
    }

    def _auth_config(self, *, token: str, required: bool = False):
        return patch.multiple(
            adapter_main,
            _ASSISTANT_SERVICE_TOKEN=token,
            _ASSISTANT_SERVICE_AUTH_REQUIRED=required,
        )

    def test_cron_proxy_rejects_missing_service_token(self):
        with (
            self._auth_config(token="cron-secret"),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_cron_call",
            ) as gateway_call,
        ):
            response = client.post(
                "/api/openclaw-adapter/gateway/cron",
                json={"method": "cron.list", "params": {"limit": 1}},
            )

        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["error_code"], "CRON_SERVICE_AUTH_DENIED")
        gateway_call.assert_not_called()

    def test_cron_proxy_rejects_wrong_service_token(self):
        with (
            self._auth_config(token="cron-secret"),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_cron_call",
            ) as gateway_call,
        ):
            response = client.post(
                "/api/openclaw-adapter/gateway/cron",
                json={"method": "cron.list", "params": {"limit": 1}},
                headers={"X-Pantheon-Service-Token": "wrong-secret"},
            )

        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["error_code"], "CRON_SERVICE_AUTH_DENIED")
        gateway_call.assert_not_called()

    def test_cron_proxy_accepts_correct_service_token(self):
        with (
            self._auth_config(token="cron-secret"),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_cron_call",
                return_value={"jobs": []},
            ) as gateway_call,
        ):
            response = client.post(
                "/api/openclaw-adapter/gateway/cron",
                json={"method": "cron.list", "params": {"limit": 1}},
                headers={"X-Pantheon-Service-Token": "cron-secret"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"], {"jobs": []})
        gateway_call.assert_called_once_with("cron.list", {"limit": 1})

    def test_cron_proxy_fails_closed_when_token_is_not_configured(self):
        with (
            self._auth_config(token="", required=False),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_cron_call",
            ) as gateway_call,
        ):
            response = client.post(
                "/api/openclaw-adapter/gateway/cron",
                json={"method": "cron.list", "params": {"limit": 1}},
            )

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(
            response.json()["error_code"],
            "CRON_SERVICE_AUTH_MISCONFIGURED",
        )
        gateway_call.assert_not_called()

    @classmethod
    def _persona_job(
        cls,
        *,
        persona_id: str = "persona-1",
        workflow_id: str = "pantheon.review",
        name: str | None = None,
    ):
        contract = cls._CRON_CONTRACTS[workflow_id]
        event = {
            "kind": "pantheon.workflow.dispatch",
            "persona_id": persona_id,
            "workflow_id": workflow_id,
            "request_id": f"persona-provisioning:{persona_id}:{workflow_id}",
            "policy_id": contract["policy_id"],
            "upstream_entrypoint": contract["upstream_entrypoint"],
        }
        if workflow_id == "pantheon.persona.first-evaluation":
            event.update(
                {
                    "runtime_id": "runtime-1",
                    "runtime_binding_id": "runtime-binding-1",
                    "capital_pool_id": "pool-1",
                    "persona_capital_binding_id": "capital-binding-1",
                }
            )
        return {
            "id": "job-persona-1",
            "name": name
            if name is not None
            else adapter_main._canonical_persona_cron_job_name(workflow_id, persona_id),
            "enabled": True,
            "deleteAfterRun": False,
            "schedule": {"kind": "cron", "expr": contract["schedule"]},
            "sessionTarget": "main",
            "wakeMode": "next-heartbeat",
            "payload": {
                "kind": "systemEvent",
                "text": json.dumps(event),
            },
            "delivery": {"mode": "none"},
        }

    @staticmethod
    def _replace_event(job, **changes):
        event = json.loads(job["payload"]["text"])
        event.update(changes)
        job["payload"]["text"] = json.dumps(event)

    def test_cron_add_rejects_non_persona_payload(self):
        with (
            self._auth_config(token="cron-secret"),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_cron_call",
            ) as gateway_call,
        ):
            response = client.post(
                "/api/openclaw-adapter/gateway/cron",
                json={"method": "cron.add", "params": {"name": "arbitrary"}},
                headers={"X-Pantheon-Service-Token": "cron-secret"},
            )

        self.assertEqual(response.status_code, 403, response.text)
        gateway_call.assert_not_called()

    def test_cron_add_forwards_complete_persona_job(self):
        params = self._persona_job()
        params.pop("id")
        with (
            self._auth_config(token="cron-secret"),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_cron_call",
                return_value={"id": "job-persona-1"},
            ) as gateway_call,
        ):
            response = client.post(
                "/api/openclaw-adapter/gateway/cron",
                json={"method": "cron.add", "params": params},
                headers={"X-Pantheon-Service-Token": "cron-secret"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        gateway_call.assert_called_once_with("cron.add", params)

    def test_cron_add_accepts_exact_five_workflow_catalog(self):
        self.assertEqual(adapter_main._PERSONA_CRON_CATALOG, self._CRON_CONTRACTS)

        for workflow_id in self._CRON_CONTRACTS:
            with self.subTest(workflow_id=workflow_id):
                params = self._persona_job(workflow_id=workflow_id)
                params.pop("id")
                self.assertTrue(adapter_main._is_well_formed_persona_cron_job(params))

    def test_cron_add_accepts_openclaw_normalized_no_delivery_readback_shape(self):
        params = self._persona_job()
        params.pop("id")
        params.pop("delivery")

        self.assertTrue(adapter_main._is_well_formed_persona_cron_job(params))

    def test_cron_add_rejects_noncanonical_persona_contract_fields(self):
        invalid_jobs = {}

        invalid_jobs["name"] = copy.deepcopy(self._persona_job())
        invalid_jobs["name"]["name"] = "pantheon-pantheon-review-wrong-persona"

        invalid_jobs["enabled"] = copy.deepcopy(self._persona_job())
        invalid_jobs["enabled"]["enabled"] = False

        invalid_jobs["delete_after_run"] = copy.deepcopy(self._persona_job())
        invalid_jobs["delete_after_run"]["deleteAfterRun"] = True

        invalid_jobs["schedule"] = copy.deepcopy(self._persona_job())
        invalid_jobs["schedule"]["schedule"]["expr"] = "0 * * * *"

        invalid_jobs["session_target"] = copy.deepcopy(self._persona_job())
        invalid_jobs["session_target"]["sessionTarget"] = "persona-1"

        invalid_jobs["wake_mode"] = copy.deepcopy(self._persona_job())
        invalid_jobs["wake_mode"]["wakeMode"] = "now"

        invalid_jobs["delivery"] = copy.deepcopy(self._persona_job())
        invalid_jobs["delivery"]["delivery"] = {"mode": "announce"}

        invalid_jobs["request_id"] = copy.deepcopy(self._persona_job())
        self._replace_event(invalid_jobs["request_id"], request_id="request-random")

        invalid_jobs["policy_id"] = copy.deepcopy(self._persona_job())
        self._replace_event(invalid_jobs["policy_id"], policy_id="policy-review")

        invalid_jobs["upstream_entrypoint"] = copy.deepcopy(self._persona_job())
        self._replace_event(
            invalid_jobs["upstream_entrypoint"],
            upstream_entrypoint="persona.review",
        )

        invalid_jobs["unknown_workflow"] = copy.deepcopy(self._persona_job())
        self._replace_event(
            invalid_jobs["unknown_workflow"],
            workflow_id="pantheon.arbitrary",
            request_id="persona-provisioning:persona-1:pantheon.arbitrary",
        )
        invalid_jobs["unknown_workflow"]["name"] = (
            "pantheon-pantheon-arbitrary-persona-1"
        )

        with (
            self._auth_config(token="cron-secret"),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_cron_call",
            ) as gateway_call,
        ):
            for field, params in invalid_jobs.items():
                with self.subTest(field=field):
                    params.pop("id")
                    response = client.post(
                        "/api/openclaw-adapter/gateway/cron",
                        json={"method": "cron.add", "params": params},
                        headers={"X-Pantheon-Service-Token": "cron-secret"},
                    )

                    self.assertEqual(response.status_code, 403, response.text)

        gateway_call.assert_not_called()

    def test_cron_list_hides_external_jobs_and_preserves_reserved_malformed_rows(self):
        valid = self._persona_job()
        external = self._persona_job(name="external-job")
        malformed_reserved = {
            "id": "job-malformed-1",
            "name": "pantheon-malformed-orphan",
            "payload": {"kind": "systemEvent", "text": "not-json"},
        }
        upstream_result = {
            "jobs": [external, malformed_reserved, valid],
            "hasMore": False,
            "nextOffset": 3,
        }
        with (
            self._auth_config(token="cron-secret"),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_cron_call",
                return_value=upstream_result,
            ) as gateway_call,
        ):
            response = client.post(
                "/api/openclaw-adapter/gateway/cron",
                json={"method": "cron.list", "params": {"limit": 200, "offset": 0}},
                headers={"X-Pantheon-Service-Token": "cron-secret"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["data"],
            {
                "jobs": [malformed_reserved, valid],
                "hasMore": False,
                "nextOffset": 3,
            },
        )
        gateway_call.assert_called_once_with("cron.list", {"limit": 200, "offset": 0})

    def test_cron_update_forwards_complete_canonical_patch(self):
        current = self._persona_job()
        patch_params = self._persona_job()
        patch_params.pop("id")
        calls = []

        def gateway_call(method, params):
            calls.append((method, params))
            if method == "cron.list":
                return {"jobs": [current]}
            if method == "cron.update":
                return {"updated": True}
            raise AssertionError(method)

        with (
            self._auth_config(token="cron-secret"),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_cron_call",
                side_effect=gateway_call,
            ),
        ):
            response = client.post(
                "/api/openclaw-adapter/gateway/cron",
                json={
                    "method": "cron.update",
                    "params": {"id": "job-persona-1", "patch": patch_params},
                },
                headers={"X-Pantheon-Service-Token": "cron-secret"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"], {"updated": True})
        self.assertEqual(
            calls,
            [
                ("cron.list", {"limit": 200, "offset": 0}),
                (
                    "cron.update",
                    {"id": "job-persona-1", "patch": patch_params},
                ),
            ],
        )

    def test_cron_update_rejects_noncanonical_patch(self):
        current = self._persona_job()
        patch_params = copy.deepcopy(current)
        patch_params.pop("id")
        patch_params["schedule"]["expr"] = "0 * * * *"
        calls = []

        def gateway_call(method, params):
            calls.append((method, params))
            if method == "cron.list":
                return {"jobs": [current]}
            raise AssertionError("noncanonical update must not be forwarded")

        with (
            self._auth_config(token="cron-secret"),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_cron_call",
                side_effect=gateway_call,
            ),
        ):
            response = client.post(
                "/api/openclaw-adapter/gateway/cron",
                json={
                    "method": "cron.update",
                    "params": {"id": "job-persona-1", "patch": patch_params},
                },
                headers={"X-Pantheon-Service-Token": "cron-secret"},
            )

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual([method for method, _ in calls], ["cron.list"])

    def test_cron_update_rejects_persona_or_workflow_owner_mutation(self):
        for owner_change, patch_job in (
            ("persona", self._persona_job(persona_id="persona-2")),
            ("workflow", self._persona_job(workflow_id="pantheon.deploy")),
        ):
            patch_job.pop("id")
            calls = []

            def gateway_call(method, params):
                calls.append((method, params))
                if method == "cron.list":
                    return {"jobs": [self._persona_job()]}
                raise AssertionError("owner-changing update must not be forwarded")

            with (
                self.subTest(owner_change=owner_change),
                self._auth_config(token="cron-secret"),
                patch.object(
                    adapter_main._OPENCLAW_AGENT_PROVIDER,
                    "gateway_cron_call",
                    side_effect=gateway_call,
                ),
            ):
                response = client.post(
                    "/api/openclaw-adapter/gateway/cron",
                    json={
                        "method": "cron.update",
                        "params": {"id": "job-persona-1", "patch": patch_job},
                    },
                    headers={"X-Pantheon-Service-Token": "cron-secret"},
                )

            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual([method for method, _ in calls], ["cron.list"])

    def test_cron_run_is_not_exposed_by_persona_proxy(self):
        with (
            self._auth_config(token="cron-secret"),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_cron_call",
            ) as gateway_call,
        ):
            response = client.post(
                "/api/openclaw-adapter/gateway/cron",
                json={"method": "cron.run", "params": {"id": "job-other"}},
                headers={"X-Pantheon-Service-Token": "cron-secret"},
            )

        self.assertEqual(response.status_code, 403, response.text)
        gateway_call.assert_not_called()

    def test_cron_remove_is_fenced_to_persona_owned_namespace(self):
        calls = []

        def gateway_call(method, params):
            calls.append((method, params))
            if method == "cron.list":
                return {"jobs": [self._persona_job(name="external-job")]}
            raise AssertionError("destructive mutation must not be forwarded")

        with (
            self._auth_config(token="cron-secret"),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_cron_call",
                side_effect=gateway_call,
            ),
        ):
            response = client.post(
                "/api/openclaw-adapter/gateway/cron",
                json={"method": "cron.remove", "params": {"id": "job-persona-1"}},
                headers={"X-Pantheon-Service-Token": "cron-secret"},
            )

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(
            response.json()["error_code"],
            "OPENCLAW_CRON_TARGET_FORBIDDEN",
        )
        self.assertEqual([method for method, _ in calls], ["cron.list"])

    def test_cron_remove_forwards_verified_persona_owned_job(self):
        calls = []

        def gateway_call(method, params):
            calls.append((method, params))
            if method == "cron.list":
                return {"jobs": [self._persona_job()]}
            if method == "cron.remove":
                return {"removed": True}
            raise AssertionError(method)

        with (
            self._auth_config(token="cron-secret"),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_cron_call",
                side_effect=gateway_call,
            ),
        ):
            response = client.post(
                "/api/openclaw-adapter/gateway/cron",
                json={"method": "cron.remove", "params": {"id": "job-persona-1"}},
                headers={"X-Pantheon-Service-Token": "cron-secret"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"], {"removed": True})
        self.assertEqual(
            [method for method, _ in calls],
            ["cron.list", "cron.remove"],
        )

    def test_cron_remove_allows_reserved_malformed_orphan_cleanup(self):
        orphan = {
            "id": "job-orphan-1",
            "name": "pantheon-malformed-orphan",
            "payload": {"kind": "systemEvent", "text": "not-json"},
        }
        calls = []

        def gateway_call(method, params):
            calls.append((method, params))
            if method == "cron.list":
                return {"jobs": [orphan]}
            if method == "cron.remove":
                return {"removed": True}
            raise AssertionError(method)

        with (
            self._auth_config(token="cron-secret"),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_cron_call",
                side_effect=gateway_call,
            ),
        ):
            response = client.post(
                "/api/openclaw-adapter/gateway/cron",
                json={"method": "cron.remove", "params": {"id": "job-orphan-1"}},
                headers={"X-Pantheon-Service-Token": "cron-secret"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([method for method, _ in calls], ["cron.list", "cron.remove"])


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

    def test_capabilities_not_exposed_does_not_degrade_reachable_upstream(self):
        error = adapter_main.UpstreamClientError(
            status_code=404,
            error_code="UPSTREAM_NOT_FOUND",
            message="OpenClaw upstream returned HTTP 404.",
            retryable=False,
            upstream_status=404,
        )
        mock_client = MagicMock()
        mock_client.get_capabilities.side_effect = error
        with (
            patch.object(adapter_main, "_client", return_value=mock_client),
            patch.object(
                adapter_main,
                "_probe_upstream",
                return_value={"reachable": True, "http_status": 200, "probe": "/readyz"},
            ),
        ):
            resp = client.get("/api/openclaw-adapter/capabilities")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["activation_state"], "upstream_client_ready")
        self.assertEqual(body["upstream"]["status"], "ok")
        self.assertEqual(body["upstream"]["capabilities_status"], "not_exposed")
        self.assertFalse(body["upstream"]["capabilities_available"])
        self.assertEqual(body["upstream"]["warning_code"], "UPSTREAM_CAPABILITIES_NOT_EXPOSED")

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

    def test_assistant_skill_authorize_rejects_retired_source_tool(self):
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

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error_code"], "BRIDGE_SKILL_DENIED")
        entries = bridge._audit.read()  # noqa: SLF001 - test asserts route admission audit.
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["request_type"], "assistant_dev_docs_generate")
        self.assertEqual(entries[0]["outcome"], "denied")

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
                    "mode": "user",
                    "operator_role": "operator",
                    "confirmed": True,
                },
                headers={
                    "X-Operator-Id": "op-1",
                    "X-Trace-Id": "trace-reauth-1",
                    "X-Operator-Role": "operator",
                    "X-Assistant-Mode": "user",
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
                    "mode": "user",
                    "operator_role": "operator",
                    "confirmed": True,
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

    def test_assistant_claude_reauth_starts_auth_login_flow(self):
        fake_payload = {
            "reauth_session_id": "claude_reauth_1",
            "provider": "claude",
            "status": "pending",
            "verification_uri": "https://console.anthropic.com/login",
            "user_code": "WXYZ-1234",
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
            trace_id_factory=lambda: "trace-claude-reauth-1",
        )
        with patch.object(
            adapter_main._CLAUDE_PROVIDER,
            "start_device_reauth",
            return_value=fake_payload,
        ) as start, patch.object(adapter_main, "_BRIDGE", bridge):
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/claude/reauth",
                json={
                    "reason": "expired",
                    "captureTimeoutSeconds": 3,
                    "mode": "user",
                    "operator_role": "operator",
                    "confirmed": True,
                },
                headers={
                    "X-Operator-Id": "op-1",
                    "X-Trace-Id": "trace-claude-reauth-1",
                    "X-Operator-Role": "operator",
                    "X-Assistant-Mode": "user",
                },
            )

        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["data"]["provider"], "claude")
        self.assertEqual(body["data"]["verification_uri"], "https://console.anthropic.com/login")
        start.assert_called_once_with(
            operator_id="op-1",
            trace_id="trace-claude-reauth-1",
            reason="expired",
            capture_timeout_seconds=3,
            poll_interval_seconds=None,
            max_wait_seconds=None,
        )

    def test_assistant_claude_reauth_status_returns_session(self):
        with patch.object(
            adapter_main._CLAUDE_PROVIDER,
            "reauth_status",
            return_value={
                "reauth_session_id": "claude_reauth_1",
                "provider": "claude",
                "status": "completed",
                "readiness": {"ready": True},
            },
        ) as status:
            resp = client.get(
                "/api/openclaw-adapter/assistant/providers/claude/reauth/claude_reauth_1",
                headers={"X-Operator-Id": "op-1"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["status"], "completed")
        status.assert_called_once_with("claude_reauth_1")

    def test_assistant_claude_reauth_code_defaults_to_user_mode(self):
        fake_payload = {
            "reauth_session_id": "claude_reauth_1",
            "provider": "claude",
            "status": "code_submitted",
            "code_submitted_at": "2026-07-01T00:00:00Z",
        }
        bridge = adapter_main.ToolWorkflowBridge(
            policy=adapter_main.ToolPolicy(
                allowed_tools=[adapter_main.ASSISTANT_PROVIDER_REAUTH_TOOL_NAME]
            ),
            audit_log=adapter_main.BridgeAuditLog(path=tempfile.mktemp(suffix=".jsonl")),
            trace_id_factory=lambda: "trace-claude-code-1",
        )
        with patch.object(
            adapter_main._CLAUDE_PROVIDER,
            "submit_reauth_code",
            return_value=fake_payload,
        ) as submit, patch.object(adapter_main, "_BRIDGE", bridge):
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/claude/reauth/claude_reauth_1/code",
                json={"provider": "claude", "code": "claude-oauth-code-123", "confirmed": True},
                headers={"X-Operator-Id": "op-1", "X-Trace-Id": "trace-claude-code-1"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["status"], "code_submitted")
        submit.assert_called_once_with(
            "claude_reauth_1",
            code="claude-oauth-code-123",
            operator_id="op-1",
        )
        entries = bridge._audit.read()  # noqa: SLF001 - test asserts route admission audit.
        self.assertEqual(entries[0]["request_type"], "assistant_provider_reauth_code")
        self.assertEqual(entries[0]["mode"], "user")

    def test_assistant_provider_registration_requires_bridge_allowlist(self):
        bridge = adapter_main.ToolWorkflowBridge(
            policy=adapter_main.ToolPolicy(allowed_tools=[]),
            audit_log=adapter_main.BridgeAuditLog(path=tempfile.mktemp(suffix=".jsonl")),
        )
        with patch.object(adapter_main._PROVIDER_REGISTRY, "register") as register, patch.object(adapter_main, "_BRIDGE", bridge):
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers",
                json={
                    "provider": "gemini_cli",
                    "providerName": "Gemini CLI",
                    "mode": "kernel_debug",
                    "operator_role": "operator",
                    "control_mode": {"active": True, "mode": "kernel_debug", "activation_id": "act-1"},
                },
                headers={"X-Operator-Id": "op-1", "X-Operator-Role": "operator"},
            )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error_code"], "BRIDGE_SKILL_DENIED")
        register.assert_not_called()

    def test_assistant_provider_registration_writes_metadata_only(self):
        fake_payload = {
            "provider": "gemini_cli",
            "provider_name": "Gemini CLI",
            "runtime": "external_llm",
            "ready": False,
            "status": "registered",
            "reauth_supported": False,
        }
        bridge = adapter_main.ToolWorkflowBridge(
            policy=adapter_main.ToolPolicy(
                allowed_tools=[adapter_main.ASSISTANT_PROVIDER_REGISTER_TOOL_NAME]
            ),
            audit_log=adapter_main.BridgeAuditLog(path=tempfile.mktemp(suffix=".jsonl")),
            trace_id_factory=lambda: "trace-register-1",
        )
        with patch.object(
            adapter_main._PROVIDER_REGISTRY,
            "register",
            return_value=fake_payload,
        ) as register, patch.object(adapter_main, "_BRIDGE", bridge):
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers",
                json={
                    "provider": "gemini_cli",
                    "providerName": "Gemini CLI",
                    "model": "gemini-2.5-pro",
                    "mode": "kernel_debug",
                    "operator_role": "operator",
                    "control_mode": {"active": True, "mode": "kernel_debug", "activation_id": "act-1"},
                },
                headers={"X-Operator-Id": "op-1", "X-Trace-Id": "trace-register-1"},
            )

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["data"]["provider"], "gemini_cli")
        register.assert_called_once()
        args, kwargs = register.call_args
        self.assertEqual(args[0]["provider"], "gemini_cli")
        self.assertEqual(args[0]["model"], "gemini-2.5-pro")
        self.assertEqual(kwargs["operator_id"], "op-1")
        self.assertEqual(kwargs["trace_id"], "trace-register-1")

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

# ---------------------------------------------------------------------------
# Session stubs
# ---------------------------------------------------------------------------


class TestGovernedServantAgentSync(unittest.TestCase):
    _PERSONA_ID = "agora-servant-0123456789abcdefabcd"
    _PAYLOAD = {
        "persona_registry_ref": f"persona:{_PERSONA_ID}",
        "workspace_ref": f"/home/node/.openclaw/workspaces/{_PERSONA_ID}",
        "capability_snapshot": {
            "allowed_capabilities": ["persona_opinion"],
            "persona_class": "agora_servant",
        },
    }
    _HEADERS = {
        "X-Pantheon-Service-Token": "adapter-secret",
        "Idempotency-Key": "3c1c6580-746b-5816-b246-f46e14367875",
        "X-Request-Id": "request-agent-1",
    }

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_patch = patch.object(
            adapter_main,
            "_OPENCLAW_AGENT_IDEMPOTENCY_DB",
            Path(self._temp_dir.name) / "agent-ensure.sqlite3",
        )
        self._db_patch.start()

    def tearDown(self):
        self._db_patch.stop()
        self._temp_dir.cleanup()

    def _auth_config(self, *, token: str = "adapter-secret"):
        return patch.multiple(
            adapter_main,
            _ASSISTANT_SERVICE_TOKEN=token,
            _ASSISTANT_SERVICE_AUTH_REQUIRED=True,
        )

    def test_agent_ensure_requires_the_bff_service_token(self):
        with self._auth_config():
            response = client.post(
                "/api/openclaw-adapter/agents/ensure",
                json=self._PAYLOAD,
                headers={
                    "Idempotency-Key": self._HEADERS["Idempotency-Key"],
                    "X-Request-Id": self._HEADERS["X-Request-Id"],
                },
            )

        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["error_code"], "AGENT_SERVICE_AUTH_DENIED")

    def test_agent_ensure_reconciles_only_the_exact_governed_workspace(self):
        expected = {
            "status": "created",
            "agent_id": self._PERSONA_ID,
            "model_id": f"openclaw/{self._PERSONA_ID}",
            "model": "anthropic/claude-opus-4-8",
            "workspace_ref": self._PAYLOAD["workspace_ref"],
        }
        with (
            self._auth_config(),
            patch.object(
                adapter_main,
                "ensure_agora_servant_agent",
                return_value=expected,
            ) as ensure_agent,
        ):
            response = client.post(
                "/api/openclaw-adapter/agents/ensure",
                json=self._PAYLOAD,
                headers=self._HEADERS,
            )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["agent"], expected)
        persona = ensure_agent.call_args.args[0]
        self.assertEqual(persona["persona_id"], self._PERSONA_ID)
        self.assertEqual(persona["name"], "Agora Servant")
        self.assertEqual(persona["traits"]["decision_style"], "operator-guided")
        self.assertEqual(persona["metadata"]["execution_authority"], "none")

    def test_agent_ensure_rejects_forbidden_capability_before_cli(self):
        payload = copy.deepcopy(self._PAYLOAD)
        payload["capability_snapshot"]["allowed_capabilities"] = [
            "persona_opinion",
            "capital-binding",
        ]
        with (
            self._auth_config(),
            patch.object(adapter_main, "ensure_agora_servant_agent") as ensure_agent,
        ):
            response = client.post(
                "/api/openclaw-adapter/agents/ensure",
                json=payload,
                headers=self._HEADERS,
            )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error_code"], "AGENT_SYNC_POLICY_DENIED")
        ensure_agent.assert_not_called()

    def test_agent_ensure_rejects_non_exact_opinion_capability_before_cli(self):
        payload = copy.deepcopy(self._PAYLOAD)
        payload["capability_snapshot"]["allowed_capabilities"] = [
            "persona-opinion",
        ]
        with (
            self._auth_config(),
            patch.object(adapter_main, "ensure_agora_servant_agent") as ensure_agent,
        ):
            response = client.post(
                "/api/openclaw-adapter/agents/ensure",
                json=payload,
                headers=self._HEADERS,
            )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error_code"], "AGENT_SYNC_POLICY_DENIED")
        ensure_agent.assert_not_called()

    def test_agent_ensure_replays_same_key_without_a_second_reconcile(self):
        agent = {
            "status": "created",
            "agent_id": self._PERSONA_ID,
            "workspace_ref": self._PAYLOAD["workspace_ref"],
        }
        with (
            self._auth_config(),
            patch.object(adapter_main, "ensure_agora_servant_agent", return_value=agent) as ensure,
        ):
            first = client.post(
                "/api/openclaw-adapter/agents/ensure",
                json=self._PAYLOAD,
                headers=self._HEADERS,
            )
            replay_headers = {**self._HEADERS, "X-Request-Id": "request-agent-retry"}
            replay = client.post(
                "/api/openclaw-adapter/agents/ensure",
                json=self._PAYLOAD,
                headers=replay_headers,
            )

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(replay.json(), first.json())
        ensure.assert_called_once()

    def test_agent_ensure_rejects_same_key_with_a_different_payload(self):
        agent = {
            "status": "created",
            "agent_id": self._PERSONA_ID,
            "workspace_ref": self._PAYLOAD["workspace_ref"],
        }
        changed = copy.deepcopy(self._PAYLOAD)
        changed["workspace_ref"] = (
            "/home/node/.openclaw/workspaces/agora-servant-fedcba9876543210fedc"
        )
        with (
            self._auth_config(),
            patch.object(adapter_main, "ensure_agora_servant_agent", return_value=agent) as ensure,
        ):
            first = client.post(
                "/api/openclaw-adapter/agents/ensure",
                json=self._PAYLOAD,
                headers=self._HEADERS,
            )
            conflict = client.post(
                "/api/openclaw-adapter/agents/ensure",
                json=changed,
                headers=self._HEADERS,
            )

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(
            conflict.json()["error_code"],
            "AGENT_SYNC_IDEMPOTENCY_CONFLICT",
        )
        ensure.assert_called_once()

    def test_agent_ensure_serializes_different_keys(self):
        req = adapter_main.OpenClawAgentEnsureRequest.model_validate(self._PAYLOAD)
        active = 0
        max_active = 0
        state_lock = threading.Lock()

        def reconcile(_req):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with state_lock:
                active -= 1
            return {"status": "created", "agent_id": self._PERSONA_ID}

        def ensure(key):
            return adapter_main._ensure_agent_idempotently(
                req,
                idempotency_key=key,
                request_id=f"request-{key}",
            )

        with (
            patch.object(adapter_main, "_sync_servant_agent", side_effect=reconcile),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            results = list(executor.map(ensure, ["key-a", "key-b"]))

        self.assertEqual([result[0] for result in results], [201, 201])
        self.assertEqual(max_active, 1)

    def test_agent_cli_is_bound_to_the_gateway_state_dir(self):
        completed = MagicMock(returncode=0, stdout='{"agents": []}', stderr="")
        with patch.object(adapter_main.subprocess, "run", return_value=completed) as run:
            result = adapter_main._gateway_state_agent_runner(
                ["openclaw", "agents", "list", "--json"]
            )

        self.assertIs(result, completed)
        self.assertEqual(
            run.call_args.kwargs["env"]["OPENCLAW_STATE_DIR"],
            "/home/node/.openclaw",
        )
        self.assertEqual(run.call_args.kwargs["env"]["HOME"], "/home/node")
        self.assertEqual(run.call_args.kwargs["user"], 1000)
        self.assertEqual(run.call_args.kwargs["group"], 1000)
        self.assertFalse(run.call_args.kwargs["check"])

    def test_agent_soul_writer_uses_the_gateway_owner(self):
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch.object(adapter_main.subprocess, "run", return_value=completed) as run:
            adapter_main._gateway_state_soul_writer(
                f"/home/node/.openclaw/workspaces/{self._PERSONA_ID}",
                "# governed soul",
            )

        self.assertEqual(run.call_args.kwargs["input"], "# governed soul")
        self.assertEqual(run.call_args.kwargs["user"], 1000)
        self.assertEqual(run.call_args.kwargs["group"], 1000)
        self.assertEqual(run.call_args.kwargs["env"]["HOME"], "/home/node")

    def _opinion_payload(self):
        tenant_id = "tenant-alpha"
        persona_id = "persona-alpha"
        persona_version = "persona-alpha-v4"
        snapshot_id = "snapshot-alpha-v9"
        requested_environment = "paper"
        agent_id = adapter_main._persona_opinion_agent_id(
            tenant_id, persona_id, persona_version, snapshot_id, requested_environment
        )
        return {
            "persona_id": persona_id,
            "tenant_id": tenant_id,
            "persona_version": persona_version,
            "agent_id": agent_id,
            "workspace_ref": f"/home/node/.openclaw/workspaces/{agent_id}",
            "capability_snapshot_id": snapshot_id,
            "allowed_capabilities": ["persona_opinion"],
            "environment_ceiling": "paper",
            "requested_environment": requested_environment,
            "execution_authority": "none",
            "display_name": "Alpha",
            "mandate": "trend",
            "archetype": "challenger",
            "strategy_family": "momentum",
            "traits": {"decision_style": "evidence-first"},
        }

    def test_live_agent_visibility_waits_through_deterministic_reload_lag(self):
        agent_id = self._opinion_payload()["agent_id"]
        registries = [[], [], [{"id": agent_id}]]
        elapsed = [0.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            elapsed[0] += seconds

        with patch.object(
            adapter_main._OPENCLAW_AGENT_PROVIDER,
            "gateway_agents_list",
            side_effect=registries,
        ) as listing:
            visible = adapter_main._wait_for_live_persona_opinion_agent(
                agent_id,
                timeout_seconds=1.0,
                poll_seconds=0.1,
                clock=lambda: elapsed[0],
                sleeper=sleep,
            )

        self.assertEqual(visible["id"], agent_id)
        self.assertEqual(listing.call_count, 3)
        self.assertEqual(sleeps, [0.1, 0.1])

    def test_live_agent_visibility_threads_remaining_total_budget(self):
        agent_id = self._opinion_payload()["agent_id"]
        elapsed = [0.0]
        probe_budgets = []

        def listing(*, timeout_seconds):
            probe_budgets.append(timeout_seconds)
            elapsed[0] += 0.2
            return [{"id": agent_id}] if len(probe_budgets) == 2 else []

        def sleep(seconds):
            elapsed[0] += seconds

        with patch.object(
            adapter_main._OPENCLAW_AGENT_PROVIDER,
            "gateway_agents_list",
            side_effect=listing,
        ):
            visible = adapter_main._wait_for_live_persona_opinion_agent(
                agent_id,
                timeout_seconds=1.0,
                poll_seconds=0.1,
                clock=lambda: elapsed[0],
                sleeper=sleep,
            )

        self.assertEqual(visible["id"], agent_id)
        self.assertEqual(probe_budgets, [1.0, 0.7])

    def test_persona_ensure_timeout_is_typed_fail_closed_without_replay(self):
        payload = self._opinion_payload()
        agent = {"status": "created", "agent_id": payload["agent_id"]}
        timeout_error = adapter_main.GatewayOpenClawProviderError(
            "agents.list subprocess exceeded the remaining budget",
            status_code=504,
            error_code="OPENCLAW_GATEWAY_TIMEOUT",
        )
        with (
            self._auth_config(),
            patch.object(adapter_main, "_sync_persona_opinion_agent", return_value=agent),
            patch.object(adapter_main, "_PERSONA_OPINION_AGENT_READY_TIMEOUT_SECONDS", 0.01),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_agents_list",
                side_effect=timeout_error,
            ),
        ):
            response = client.post(
                "/api/openclaw-adapter/agents/persona-opinion/ensure",
                json=payload,
                headers={**self._HEADERS, "Idempotency-Key": "persona-never-live"},
            )

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["error_code"], "PERSONA_OPINION_AGENT_NOT_READY")
        self.assertTrue(response.json()["retryable"])
        with adapter_main.sqlite3.connect(str(adapter_main._OPENCLAW_AGENT_IDEMPOTENCY_DB)) as connection:
            replay_count = connection.execute(
                "SELECT count(*) FROM agent_ensure_replays WHERE idempotency_key=?",
                ("persona-never-live",),
            ).fetchone()[0]
            admission_table = connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='persona_opinion_admissions'"
            ).fetchone()[0]
            admission_count = (
                connection.execute(
                    "SELECT count(*) FROM persona_opinion_admissions WHERE agent_id=?",
                    (payload["agent_id"],),
                ).fetchone()[0]
                if admission_table
                else 0
            )
        self.assertEqual(replay_count, 0)
        self.assertEqual(admission_count, 0)

    def test_persona_ensure_cached_replay_still_requires_live_visibility(self):
        payload = self._opinion_payload()
        agent = {"status": "created", "agent_id": payload["agent_id"]}
        calls = [0]

        def live_registry(**_kwargs):
            calls[0] += 1
            if calls[0] == 1:
                return [{"id": payload["agent_id"]}]
            raise adapter_main.GatewayOpenClawProviderError(
                "agents.list subprocess exceeded the remaining budget",
                status_code=504,
                error_code="OPENCLAW_GATEWAY_TIMEOUT",
            )

        with (
            self._auth_config(),
            patch.object(adapter_main, "_sync_persona_opinion_agent", return_value=agent) as sync,
            patch.object(adapter_main, "_PERSONA_OPINION_AGENT_READY_TIMEOUT_SECONDS", 0.01),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_agents_list",
                side_effect=live_registry,
            ),
        ):
            headers = {**self._HEADERS, "Idempotency-Key": "persona-cached-visibility"}
            first = client.post(
                "/api/openclaw-adapter/agents/persona-opinion/ensure", json=payload, headers=headers,
            )
            replay = client.post(
                "/api/openclaw-adapter/agents/persona-opinion/ensure",
                json=payload,
                headers={**headers, "X-Request-Id": "persona-cached-retry"},
            )

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(replay.status_code, 503, replay.text)
        self.assertEqual(replay.json()["error_code"], "PERSONA_OPINION_AGENT_NOT_READY")
        sync.assert_called_once()
        with adapter_main.sqlite3.connect(str(adapter_main._OPENCLAW_AGENT_IDEMPOTENCY_DB)) as connection:
            replay_count = connection.execute(
                "SELECT count(*) FROM agent_ensure_replays WHERE idempotency_key=?",
                ("persona-cached-visibility",),
            ).fetchone()[0]
        self.assertEqual(replay_count, 1)

    def test_persona_ensure_preserves_gateway_auth_error_without_replay(self):
        payload = self._opinion_payload()
        error = adapter_main.GatewayOpenClawProviderError(
            "OpenClaw provider authorization expired.",
            status_code=401,
            error_code="OPENCLAW_OAUTH_EXPIRED",
        )
        with (
            self._auth_config(),
            patch.object(
                adapter_main,
                "_sync_persona_opinion_agent",
                return_value={"status": "created", "agent_id": payload["agent_id"]},
            ),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_agents_list",
                side_effect=error,
            ),
        ):
            response = client.post(
                "/api/openclaw-adapter/agents/persona-opinion/ensure",
                json=payload,
                headers={**self._HEADERS, "Idempotency-Key": "persona-auth-error"},
            )

        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["error_code"], "OPENCLAW_OAUTH_EXPIRED")
        self.assertFalse(response.json()["retryable"])
        with adapter_main.sqlite3.connect(str(adapter_main._OPENCLAW_AGENT_IDEMPOTENCY_DB)) as connection:
            replay_count = connection.execute(
                "SELECT count(*) FROM agent_ensure_replays WHERE idempotency_key=?",
                ("persona-auth-error",),
            ).fetchone()[0]
        self.assertEqual(replay_count, 0)

    def test_persona_opinion_agent_requires_exact_frozen_admission(self):
        payload = self._opinion_payload()
        agent = {
            "status": "created",
            "agent_id": payload["agent_id"],
            "workspace_ref": payload["workspace_ref"],
        }
        headers = {
            **self._HEADERS,
            "Idempotency-Key": "persona-opinion-agent-1",
            "X-Request-Id": "persona-opinion-request-1",
        }
        with (
            self._auth_config(),
            patch.object(adapter_main, "_sync_persona_opinion_agent", return_value=agent) as sync,
            patch.object(adapter_main, "_wait_for_live_persona_opinion_agent", return_value={}),
        ):
            accepted = client.post(
                "/api/openclaw-adapter/agents/persona-opinion/ensure",
                json=payload,
                headers=headers,
            )
            changed = {**payload, "workspace_ref": "/home/node/.openclaw/workspaces/main"}
            denied = client.post(
                "/api/openclaw-adapter/agents/persona-opinion/ensure",
                json=changed,
                headers={**headers, "Idempotency-Key": "persona-opinion-agent-2"},
            )

        self.assertEqual(accepted.status_code, 201, accepted.text)
        self.assertEqual(accepted.json()["execution_authority"], "none")
        self.assertEqual(len(accepted.json()["admission_fingerprint"]), 64)
        self.assertEqual(denied.status_code, 422, denied.text)
        sync.assert_called_once()

    def test_persona_identity_drift_conflicts_before_soul_reconciliation(self):
        payload = self._opinion_payload()
        agent = {
            "status": "created",
            "agent_id": payload["agent_id"],
            "workspace_ref": payload["workspace_ref"],
        }
        with (
            self._auth_config(),
            patch.object(adapter_main, "_sync_persona_opinion_agent", return_value=agent) as sync,
            patch.object(adapter_main, "_wait_for_live_persona_opinion_agent", return_value={}),
        ):
            accepted = client.post(
                "/api/openclaw-adapter/agents/persona-opinion/ensure",
                json=payload,
                headers={**self._HEADERS, "Idempotency-Key": "identity-original"},
            )
            drifted = client.post(
                "/api/openclaw-adapter/agents/persona-opinion/ensure",
                json={**payload, "mandate": "silently changed mandate", "traits": {"risk": "changed"}},
                headers={**self._HEADERS, "Idempotency-Key": "identity-drifted"},
            )

        self.assertEqual(accepted.status_code, 201, accepted.text)
        self.assertEqual(drifted.status_code, 409, drifted.text)
        sync.assert_called_once()

    def test_two_fresh_persona_agents_run_with_runtime_tools_and_memory_denied(self):
        runtime_agents = []
        live_gateway_agents = []
        written_souls = {}

        def completed(*, stdout="", stderr="", returncode=0):
            return MagicMock(stdout=stdout, stderr=stderr, returncode=returncode)

        def fake_runtime(args):
            if args[1:4] == ["agents", "list", "--json"]:
                return completed(stdout=json.dumps({"agents": runtime_agents}))
            if args[1:3] == ["agents", "add"]:
                runtime_agents.append({
                    "id": args[3],
                    "workspace": args[args.index("--workspace") + 1],
                    "model": args[args.index("--model") + 1],
                })
                return completed(stdout="{}")
            if args[1:5] == ["config", "get", "agents.list", "--json"]:
                return completed(stdout=json.dumps(runtime_agents))
            if args[1:4] == ["config", "set", "agents.list"]:
                runtime_agents[:] = json.loads(args[4])
                return completed(stdout="ok")
            if args[1:3] == ["agents", "set-identity"]:
                return completed(stdout="ok")
            return completed(returncode=1, stderr=f"unexpected command: {args}")

        def fake_live_agents(**_kwargs):
            live_gateway_agents[:] = copy.deepcopy(runtime_agents)
            return copy.deepcopy(live_gateway_agents)

        alpha = self._opinion_payload()
        beta = {
            **alpha,
            "persona_id": "persona-beta",
            "persona_version": "persona-beta-v2",
            "capability_snapshot_id": "snapshot-beta-v2",
            "display_name": "Beta",
            "mandate": "mean reversion",
            "archetype": "skeptic",
            "strategy_family": "reversion",
            "traits": {"decision_style": "contrarian"},
        }
        beta["agent_id"] = adapter_main._persona_opinion_agent_id(
            beta["tenant_id"], beta["persona_id"], beta["persona_version"],
            beta["capability_snapshot_id"], beta["requested_environment"],
        )
        beta["workspace_ref"] = f"/home/node/.openclaw/workspaces/{beta['agent_id']}"
        provider_result = MagicMock()
        provider_result.to_dict.return_value = {
            "provider": "openclaw", "mode": "user", "status": "completed",
            "output": {"json_events": []},
        }

        with (
            self._auth_config(),
            patch.object(adapter_main, "_gateway_state_agent_runner", side_effect=fake_runtime),
            patch.object(adapter_main, "_gateway_state_soul_writer", side_effect=lambda workspace, soul: written_souls.__setitem__(workspace, soul)),
            patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "gateway_agents_list", side_effect=fake_live_agents),
            patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "invoke", return_value=provider_result) as invoke,
        ):
            for index, payload in enumerate((alpha, beta), start=1):
                ensured = client.post(
                    "/api/openclaw-adapter/agents/persona-opinion/ensure",
                    json=payload,
                    headers={**self._HEADERS, "Idempotency-Key": f"fresh-persona-{index}"},
                )
                self.assertEqual(ensured.status_code, 201, ensured.text)
                admission = {
                    key: payload[key]
                    for key in adapter_main.PersonaOpinionInvocationAdmission.model_fields
                }
                invoked = client.post(
                    "/api/openclaw-adapter/assistant/providers/openclaw/invoke",
                    json={
                        "mode": "user", "prompt": "Return governed opinion JSON",
                        "metadata": {"allowed_tools": []}, "agent_id": payload["agent_id"],
                        "persona_admission": admission,
                    },
                    headers={"X-Pantheon-Service-Token": "adapter-secret", "X-Operator-Id": "operator-1",
                             "Idempotency-Key": f"persona-invoke-{index}"},
                )
                self.assertEqual(invoked.status_code, 200, invoked.text)

            # Runtime config is the enforcement boundary.  If either tool or
            # memory policy drifts after ensure, invocation must fail before
            # the provider CLI is reached.
            runtime_agents[0]["tools"]["deny"] = []
            runtime_agents[0]["memorySearch"]["enabled"] = True
            alpha_admission = {
                key: alpha[key]
                for key in adapter_main.PersonaOpinionInvocationAdmission.model_fields
            }
            denied = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/invoke",
                json={
                    "mode": "user", "prompt": "Attempt after policy drift",
                    "metadata": {"allowed_tools": []}, "agent_id": alpha["agent_id"],
                    "persona_admission": alpha_admission,
                },
                headers={"X-Pantheon-Service-Token": "adapter-secret", "X-Operator-Id": "operator-1",
                         "Idempotency-Key": "persona-policy-drift"},
            )
            self.assertEqual(denied.status_code, 403, denied.text)

        self.assertEqual({agent["id"] for agent in runtime_agents}, {alpha["agent_id"], beta["agent_id"]})
        self.assertEqual(set(written_souls), {alpha["workspace_ref"], beta["workspace_ref"]})
        # Restore the deliberate test drift before checking both ensured
        # runtime projections.
        runtime_agents[0].update(json.loads(json.dumps(adapter_main._PERSONA_OPINION_RUNTIME_POLICY)))
        for agent in runtime_agents:
            self.assertEqual(agent["tools"], {"allow": [], "deny": ["*"]})
            self.assertEqual(agent["skills"], [])
            self.assertFalse(agent["memorySearch"]["enabled"])
            self.assertEqual(agent["memorySearch"]["sources"], [])
            self.assertFalse(agent["memorySearch"]["experimental"]["sessionMemory"])
            self.assertEqual(agent["contextInjection"], "never")
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(len({call.kwargs["session_id"] for call in invoke.call_args_list}), 2)
        for call in invoke.call_args_list:
            self.assertEqual(call.kwargs["metadata"]["allowed_tools"], [])
            self.assertFalse(call.kwargs["metadata"]["persona_memory_mutated"])

    def test_persona_provider_invocation_uses_only_previously_admitted_exact_agent(self):
        payload = self._opinion_payload()
        agent = {
            "status": "created",
            "agent_id": payload["agent_id"],
            "workspace_ref": payload["workspace_ref"],
        }
        ensure_headers = {
            **self._HEADERS,
            "Idempotency-Key": "persona-opinion-agent-invoke",
            "X-Request-Id": "persona-opinion-request-invoke",
        }
        invocation_admission = {
            key: payload[key]
            for key in (
                "persona_id", "tenant_id", "persona_version", "agent_id", "workspace_ref",
                "capability_snapshot_id", "allowed_capabilities",
                "environment_ceiling", "requested_environment", "execution_authority",
                "display_name", "mandate", "archetype", "strategy_family", "traits",
            )
        }
        provider_result = MagicMock()
        provider_result.to_dict.return_value = {
            "provider": "openclaw",
            "mode": "user",
            "status": "completed",
            "output": {"agent_id": payload["agent_id"], "json_events": []},
        }
        with (
            self._auth_config(),
            patch.object(adapter_main, "_sync_persona_opinion_agent", return_value=agent),
            patch.object(adapter_main, "_assert_persona_opinion_runtime_policy", return_value={}),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_agents_list",
                return_value=[{"id": payload["agent_id"]}],
            ),
            patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "invoke", return_value=provider_result) as invoke,
        ):
            ensured = client.post(
                "/api/openclaw-adapter/agents/persona-opinion/ensure",
                json=payload,
                headers=ensure_headers,
            )
            invoked = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/invoke",
                json={
                    "mode": "user",
                    "prompt": "Return opinion JSON",
                    "context_pack": {},
                    "metadata": {"allowed_tools": []},
                    "agent_id": payload["agent_id"],
                    "persona_admission": invocation_admission,
                },
                headers={
                    "X-Pantheon-Service-Token": "adapter-secret",
                    "X-Operator-Id": "operator-1",
                    "Idempotency-Key": "persona-opinion-provider-invoke",
                },
            )

        self.assertEqual(ensured.status_code, 201, ensured.text)
        self.assertEqual(invoked.status_code, 200, invoked.text)
        self.assertEqual(invoke.call_args.kwargs["agent_id"], payload["agent_id"])
        self.assertEqual(invoke.call_args.kwargs["metadata"]["allowed_tools"], [])
        self.assertEqual(invoke.call_args.kwargs["metadata"]["execution_authority"], "none")
        self.assertFalse(invoke.call_args.kwargs["metadata"]["persona_memory_mutated"])
        self.assertRegex(invoke.call_args.kwargs["session_id"], r"^pint-[0-9a-f]{32}$")

    def test_persona_provider_invocation_threads_authorized_explicit_model(self):
        """SIMPLIFY-OPENCLAW-001 mounted-acceptance regression: an explicit
        `model` override paired with a governed non-default `agent_id` +
        matching `persona_admission` must actually reach the provider (and
        from there the Gateway `X-OpenClaw-Model` header) rather than being
        rejected or silently dropped -- unlike the default-agent path, where
        no `model` field is accepted at all
        (`test_default_agent_explicit_model_is_rejected` below)."""
        payload = self._opinion_payload()
        agent = {
            "status": "created",
            "agent_id": payload["agent_id"],
            "workspace_ref": payload["workspace_ref"],
        }
        ensure_headers = {
            **self._HEADERS,
            "Idempotency-Key": "persona-opinion-agent-invoke-model",
            "X-Request-Id": "persona-opinion-request-invoke-model",
        }
        invocation_admission = {
            key: payload[key]
            for key in (
                "persona_id", "tenant_id", "persona_version", "agent_id", "workspace_ref",
                "capability_snapshot_id", "allowed_capabilities",
                "environment_ceiling", "requested_environment", "execution_authority",
                "display_name", "mandate", "archetype", "strategy_family", "traits",
            )
        }
        provider_result = MagicMock()
        provider_result.to_dict.return_value = {
            "provider": "openclaw",
            "mode": "user",
            "status": "completed",
            "output": {"agent_id": payload["agent_id"], "json_events": []},
        }
        with (
            self._auth_config(),
            patch.object(adapter_main, "_sync_persona_opinion_agent", return_value=agent),
            patch.object(adapter_main, "_assert_persona_opinion_runtime_policy", return_value={}),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_agents_list",
                return_value=[{"id": payload["agent_id"]}],
            ),
            patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "invoke", return_value=provider_result) as invoke,
        ):
            ensured = client.post(
                "/api/openclaw-adapter/agents/persona-opinion/ensure",
                json=payload,
                headers=ensure_headers,
            )
            invoked = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/invoke",
                json={
                    "mode": "user",
                    "prompt": "Return opinion JSON",
                    "context_pack": {},
                    "metadata": {"allowed_tools": []},
                    "agent_id": payload["agent_id"],
                    "persona_admission": invocation_admission,
                    "model": "anthropic/claude-opus-4-8",
                },
                headers={
                    "X-Pantheon-Service-Token": "adapter-secret",
                    "X-Operator-Id": "operator-1",
                    "Idempotency-Key": "persona-opinion-provider-invoke-model",
                },
            )

        self.assertEqual(ensured.status_code, 201, ensured.text)
        self.assertEqual(invoked.status_code, 200, invoked.text)
        self.assertEqual(invoke.call_args.kwargs["agent_id"], payload["agent_id"])
        # The explicit override reaches the provider verbatim -- no
        # fallback candidate substituted in front of it.
        self.assertEqual(invoke.call_args.kwargs["model"], "anthropic/claude-opus-4-8")

    def test_admitted_invoke_and_stream_preserve_effective_model_over_http(self):
        from assistant_openclaw_provider import AssistantOpenClawProvider

        payload = self._opinion_payload()
        admission = {key: payload[key] for key in (
            "persona_id", "tenant_id", "persona_version", "agent_id", "workspace_ref",
            "capability_snapshot_id", "allowed_capabilities", "environment_ceiling",
            "requested_environment", "execution_authority", "display_name", "mandate",
            "archetype", "strategy_family", "traits",
        )}
        captured = []

        def forbidden_run(*args, **kwargs):
            raise AssertionError("ordinary admitted turn must not spawn CLI")

        def fake_http(req, timeout=None, deadline=None):
            captured.append({k.lower(): v for k, v in req.header_items()})
            class Response:
                def __iter__(self):
                    return iter([
                        b'data: {"type":"response.output_text.done","text":"ok"}\n',
                        b'data: {"type":"response.completed","response":{"status":"completed"}}\n',
                        b'data: [DONE]\n',
                    ])

                def close(self):
                    pass

            return Response()

        provider = AssistantOpenClawProvider(
            gateway_url="ws://fixture:18789", token="synthetic-token",
            _run_func=forbidden_run, _which_func=lambda _: None,
        )
        with (
            self._auth_config(),
            patch.object(adapter_main, "_OPENCLAW_AGENT_PROVIDER", provider),
            patch.object(adapter_main, "_sync_persona_opinion_agent", return_value={
                "status": "created", "agent_id": payload["agent_id"],
                "workspace_ref": payload["workspace_ref"],
            }),
            patch.object(adapter_main, "_assert_persona_opinion_runtime_policy", return_value={}),
            patch.object(provider, "gateway_agents_list", return_value=[{"id": payload["agent_id"]}]),
            patch("assistant_openclaw_provider._urlopen_with_deadline", side_effect=fake_http),
        ):
            ensured = client.post(
                "/api/openclaw-adapter/agents/persona-opinion/ensure", json=payload,
                headers={**self._HEADERS, "Idempotency-Key": "cross-route-model-ensure",
                         "X-Request-Id": "cross-route-model-ensure"},
            )
            self.assertEqual(ensured.status_code, 201, ensured.text)
            for model in (None, "fixture/explicit-model"):
                for suffix in ("", "/stream"):
                    with self.subTest(model=model, route=suffix):
                        body = {"prompt": "Return opinion", "agent_id": payload["agent_id"],
                                "persona_admission": admission, "metadata": {"allowed_tools": []}}
                        if model is not None:
                            body["model"] = model
                        response = client.post(
                            "/api/openclaw-adapter/assistant/providers/openclaw/invoke" + suffix,
                            json=body, headers={"X-Pantheon-Service-Token": "adapter-secret",
                                "X-Operator-Id": "operator-1",
                                "Idempotency-Key": f"cross-route-model-{model}-{suffix}"},
                        )
                        self.assertEqual(response.status_code, 200, response.text)
                        if suffix:
                            self.assertIn('"type": "done"', response.text)
                        else:
                            self.assertEqual(response.json()["data"]["status"], "completed")
            self.assertEqual(len(captured), 4)
            self.assertEqual([row.get("x-openclaw-model") for row in captured],
                             [None, None, "fixture/explicit-model", "fixture/explicit-model"])
            self.assertTrue(all(row["x-openclaw-agent-id"] == payload["agent_id"] for row in captured))

    @contextmanager
    def _admitted_replay_case(self, *, before_response=None, failure=False):
        from assistant_openclaw_provider import AssistantOpenClawProvider

        payload = self._opinion_payload()
        admission = {key: payload[key] for key in adapter_main.PersonaOpinionInvocationAdmission.model_fields}
        body = {"prompt": "Return the exact opinion", "agent_id": payload["agent_id"],
                "persona_admission": admission, "metadata": {"allowed_tools": []}}
        headers = {"X-Pantheon-Service-Token": "adapter-secret", "X-Operator-Id": "operator-1"}
        captured = []

        def http_response(request, **kwargs):
            captured.append(json.loads(request.data))
            if before_response is not None:
                before_response()

            class Response:
                def __iter__(self):
                    yield b'data: {"type":"response.output_text.delta","delta":"opinion"}\n\n'
                    if failure:
                        yield b'data: {"type":"response.failed","message":"fixture failure"}\n\n'
                    else:
                        yield (b'data: {"type":"response.completed","response":{"status":"completed",'
                               b'"id":"fixture-response","usage":{"input_tokens":10,"output_tokens":2}}}\n\n')

                def close(self):
                    pass

            return Response()

        provider = AssistantOpenClawProvider(
            gateway_url="ws://fixture:18789", token="synthetic-token",
            _run_func=lambda *a, **k: self.fail("ordinary turn spawned CLI"),
            _which_func=lambda _: None,
        )
        with (
            self._auth_config(),
            patch.object(adapter_main, "_OPENCLAW_AGENT_PROVIDER", provider),
            patch.object(adapter_main, "_sync_persona_opinion_agent", return_value={
                "status": "created", "agent_id": payload["agent_id"], "workspace_ref": payload["workspace_ref"],
            }),
            patch.object(adapter_main, "_assert_persona_opinion_runtime_policy", return_value={}),
            patch.object(provider, "gateway_agents_list", return_value=[{"id": payload["agent_id"]}]),
            patch("assistant_openclaw_provider._urlopen_with_deadline", side_effect=http_response),
        ):
            ensured = client.post(
                "/api/openclaw-adapter/agents/persona-opinion/ensure", json=payload,
                headers={**self._HEADERS, "Idempotency-Key": "replay-ensure"},
            )
            self.assertIn(ensured.status_code, (200, 201), ensured.text)
            yield body, headers, captured

    def _post_opinion(self, suffix, body, headers, key):
        return client.post(
            "/api/openclaw-adapter/assistant/providers/openclaw/invoke" + suffix,
            json=body, headers={**headers, "Idempotency-Key": key},
        )

    def _opinion_terminal(self, response, suffix):
        if not suffix:
            return adapter_main._persona_opinion_replay_event(response.json())
        self.assertEqual(response.text.count("data: [DONE]"), 1)
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
        terminals = [event for event in events if event["type"] in ("done", "error")]
        self.assertEqual(len(terminals), 1, response.text)
        return terminals[0]

    def test_persona_exact_attempt_replays_across_both_mounted_routes(self):
        from assistant_openclaw_provider import derive_session_user

        with self._admitted_replay_case() as (body, headers, captured):
            for first_route in ("", "/stream"):
                for replay_route in ("", "/stream"):
                    with self.subTest(first=first_route, replay=replay_route):
                        key = f"exact-{first_route}-{replay_route}"
                        before = len(captured)
                        first = self._post_opinion(first_route, body, headers, " " + key + " ")
                        replay = self._post_opinion(replay_route, body, headers, key)
                        self.assertEqual(first.status_code, 200, first.text)
                        self.assertEqual(replay.status_code, 200, replay.text)
                        self.assertEqual(len(captured), before + 1)
                        terminal = self._opinion_terminal(first, first_route)
                        self.assertEqual(self._opinion_terminal(replay, replay_route), terminal)
                        self.assertEqual(terminal["text"], "opinion")
                        self.assertEqual(terminal["usage"], {"input_tokens": 10, "output_tokens": 2})
                        self.assertEqual(terminal["response_id"], "fixture-response")
                        self.assertEqual(captured[-1]["user"], derive_session_user(
                            operator_id="operator-1", metadata={"tenant_id": body["persona_admission"]["tenant_id"]},
                            session_id=adapter_main._persona_opinion_session_id(key),
                        ))
                        if first_route:
                            self.assertIn('"type": "delta"', first.text)

    def test_persona_changed_content_or_actor_conflicts_across_routes(self):
        with self._admitted_replay_case() as (body, headers, captured):
            for first_route in ("", "/stream"):
                key = "conflict-" + first_route
                self.assertEqual(self._post_opinion(first_route, body, headers, key).status_code, 200)
                for replay_route in ("", "/stream"):
                    for changed_body, changed_headers in (
                        ({**body, "prompt": "different opinion"}, headers),
                        (body, {**headers, "X-Operator-Id": "another-actor"}),
                    ):
                        response = self._post_opinion(replay_route, changed_body, changed_headers, key)
                        if replay_route:
                            error = self._opinion_terminal(response, replay_route)
                        else:
                            self.assertEqual(response.status_code, 409, response.text)
                            error = response.json()
                        self.assertEqual(error["error_code"], "PERSONA_OPINION_IDEMPOTENCY_CONFLICT")
            self.assertEqual(len(captured), 2)

    def test_persona_failed_terminal_replays_without_false_done(self):
        with self._admitted_replay_case(failure=True) as (body, headers, captured):
            for first_route in ("", "/stream"):
                key = "failed-" + first_route
                first = self._post_opinion(first_route, body, headers, key)
                terminal = self._opinion_terminal(first, first_route)
                self.assertEqual(terminal["type"], "error")
                for replay_route in ("", "/stream"):
                    replay = self._post_opinion(replay_route, body, headers, key)
                    self.assertEqual(self._opinion_terminal(replay, replay_route), terminal)
            self.assertEqual(len(captured), 2)

    def test_persona_crash_or_cancel_keeps_both_routes_in_doubt(self):
        with self._admitted_replay_case() as (body, headers, captured):
            request = adapter_main.AssistantProviderInvokeRequest(**body)
            closed = []

            def interrupted():
                try:
                    yield {"type": "delta", "text": "partial"}
                    raise RuntimeError("process died before terminal persistence")
                finally:
                    closed.append(True)

            for cancel in (False, True):
                key = f"interrupted-{cancel}"
                events = adapter_main._stream_persona_opinion_idempotently(
                    request, idempotency_key=key, operator_id="operator-1", stream_fn=interrupted,
                )
                self.assertEqual(next(events)["type"], "delta")
                if cancel:
                    events.close()
                else:
                    with self.assertRaisesRegex(RuntimeError, "process died"):
                        next(events)
                for route in ("", "/stream"):
                    replay = self._post_opinion(route, body, headers, key)
                    error = self._opinion_terminal(replay, route) if route else replay.json()
                    self.assertEqual(error["error_code"], "PERSONA_OPINION_INVOCATION_IN_DOUBT")
            self.assertEqual(closed, [True, True])
            self.assertEqual(captured, [])

    def test_persona_mounted_crash_or_failed_commit_never_publishes_done(self):
        with self._admitted_replay_case() as (body, headers, captured):
            def crashed_stream(*args, **kwargs):
                yield {"type": "delta", "text": "partial"}
                raise RuntimeError("crashed before terminal")

            fault_patches = (
                patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "stream", side_effect=crashed_stream),
                patch.object(adapter_main, "_complete_persona_opinion_invocation",
                             side_effect=adapter_main.sqlite3.OperationalError("disk full")),
            )
            for index, fault in enumerate(fault_patches):
                key = f"mounted-fault-{index}"
                with fault:
                    response = self._post_opinion("/stream", body, headers, key)
                self.assertEqual(self._opinion_terminal(response, "/stream")["type"], "error")
                self.assertNotIn('"type": "done"', response.text)
                for route in ("", "/stream"):
                    replay = self._post_opinion(route, body, headers, key)
                    error = self._opinion_terminal(replay, route) if route else replay.json()
                    self.assertEqual(error["error_code"], "PERSONA_OPINION_INVOCATION_IN_DOUBT")
            self.assertEqual(len(captured), 1)

    def test_persona_replay_preserves_verbatim_json_text(self):
        text = '  {"answer": "embedded", "decision": "abstain"}\n'
        with self._admitted_replay_case() as (body, headers, captured):
            terminal = {"type": "done", "text": text, "transport": "responses_http", "elapsed_ms": 1}
            with patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "stream", return_value=iter([terminal])):
                first = self._post_opinion("/stream", body, headers, "json-opinion")
            for route in ("", "/stream"):
                replay = self._post_opinion(route, body, headers, "json-opinion")
                self.assertEqual(self._opinion_terminal(replay, route), self._opinion_terminal(first, "/stream"))
                self.assertNotIn("usage", self._opinion_terminal(replay, route))
            self.assertEqual(captured, [])

    def test_persona_simultaneous_claims_have_one_winner(self):
        with self._admitted_replay_case() as (body, headers, captured):
            request = adapter_main.AssistantProviderInvokeRequest(**body)
            barrier = threading.Barrier(4)

            def claim():
                barrier.wait(timeout=5)
                try:
                    adapter_main._claim_persona_opinion_invocation(
                        request, idempotency_key="simultaneous-claim", operator_id="operator-1",
                    )
                    return "claimed"
                except adapter_main._PersonaOpinionInvocationInDoubt:
                    return "in_doubt"

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(claim) for _ in range(4)]
                outcomes = [future.result(timeout=10) for future in futures]
            self.assertEqual(sorted(outcomes), ["claimed", "in_doubt", "in_doubt", "in_doubt"])
            self.assertEqual(captured, [])

    def test_persona_concurrent_mounted_attempts_are_fenced_across_routes(self):
        entered, release = threading.Event(), threading.Event()

        def pause_response():
            entered.set()
            if not release.wait(10):
                raise AssertionError("test did not release upstream")

        with self._admitted_replay_case(before_response=pause_response) as (body, headers, captured):
            for first_route in ("", "/stream"):
                for second_route in ("", "/stream"):
                    entered.clear()
                    release.clear()
                    key = f"concurrent-{first_route}-{second_route}"
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        first = executor.submit(self._post_opinion, first_route, body, headers, key)
                        try:
                            self.assertTrue(entered.wait(5))
                            second = self._post_opinion(second_route, body, headers, key)
                            error = self._opinion_terminal(second, second_route) if second_route else second.json()
                            self.assertEqual(error["error_code"], "PERSONA_OPINION_INVOCATION_IN_DOUBT")
                        finally:
                            release.set()
                        self.assertEqual(self._opinion_terminal(first.result(timeout=5), first_route)["type"], "done")
            self.assertEqual(len(captured), 4)

    def test_default_agent_explicit_model_is_rejected(self):
        """Without a governed non-default `agent_id` + `persona_admission`,
        there is no authorization surface for a model override on the
        default Management-AI agent -- it fails closed with a typed 422
        instead of being silently dropped."""
        resp = client.post(
            "/api/openclaw-adapter/assistant/providers/openclaw/invoke",
            json={"mode": "user", "prompt": "hi", "model": "anthropic/claude-opus-4-8"},
            headers={"X-Operator-Id": "operator-1"},
        )
        self.assertEqual(resp.status_code, 422, resp.text)

    def test_persona_invoke_live_visibility_preflight_does_not_claim_ledger(self):
        payload = self._opinion_payload()
        agent = {"status": "created", "agent_id": payload["agent_id"]}
        admission = {
            key: payload[key]
            for key in adapter_main.PersonaOpinionInvocationAdmission.model_fields
        }
        live_registries = [[{"id": payload["agent_id"]}], []]
        with (
            self._auth_config(),
            patch.object(adapter_main, "_sync_persona_opinion_agent", return_value=agent),
            patch.object(adapter_main, "_assert_persona_opinion_runtime_policy", return_value={}),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_agents_list",
                side_effect=live_registries,
            ) as listing,
            patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "invoke") as invoke,
        ):
            ensured = client.post(
                "/api/openclaw-adapter/agents/persona-opinion/ensure",
                json=payload,
                headers={**self._HEADERS, "Idempotency-Key": "persona-preflight-ensure"},
            )
            denied = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/invoke",
                json={
                    "mode": "user",
                    "prompt": "Return opinion JSON",
                    "metadata": {"allowed_tools": []},
                    "agent_id": payload["agent_id"],
                    "persona_admission": admission,
                },
                headers={
                    "X-Pantheon-Service-Token": "adapter-secret",
                    "X-Operator-Id": "operator-1",
                    "Idempotency-Key": "persona-preflight-invoke",
                },
            )

        self.assertEqual(ensured.status_code, 201, ensured.text)
        self.assertEqual(denied.status_code, 503, denied.text)
        self.assertEqual(denied.json()["error_code"], "PERSONA_OPINION_AGENT_NOT_READY")
        self.assertEqual(listing.call_count, 2)
        invoke.assert_not_called()
        with adapter_main.sqlite3.connect(str(adapter_main._OPENCLAW_AGENT_IDEMPOTENCY_DB)) as connection:
            table_count = connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' "
                "AND name='persona_opinion_invocation_replays'"
            ).fetchone()[0]
        self.assertEqual(table_count, 0)

    def test_persona_invocation_replays_terminal_and_fences_restart_in_doubt(self):
        payload = self._opinion_payload()
        admission = {
            key: payload[key]
            for key in adapter_main.PersonaOpinionInvocationAdmission.model_fields
        }
        request = adapter_main.AssistantProviderInvokeRequest(
            mode="user",
            prompt="Return the exact opinion",
            agent_id=payload["agent_id"],
            persona_admission=admission,
            metadata={"allowed_tools": []},
        )
        calls = []

        def completed():
            calls.append("completed")
            return {"status": "ok", "data": {"status": "completed", "output": {"request_id": "r1"}}}

        first = adapter_main._invoke_persona_opinion_idempotently(
            request, idempotency_key="invocation-terminal", invoke_fn=completed,
        )
        second = adapter_main._invoke_persona_opinion_idempotently(
            request, idempotency_key="invocation-terminal", invoke_fn=completed,
        )
        self.assertEqual(first, second)
        self.assertEqual(calls, ["completed"])

        def crash_after_claim():
            calls.append("crashed")
            raise RuntimeError("adapter process died after upstream acceptance")

        with self.assertRaises(RuntimeError):
            adapter_main._invoke_persona_opinion_idempotently(
                request, idempotency_key="invocation-in-doubt", invoke_fn=crash_after_claim,
            )
        with self.assertRaises(adapter_main._PersonaOpinionInvocationInDoubt):
            adapter_main._invoke_persona_opinion_idempotently(
                request,
                idempotency_key="invocation-in-doubt",
                invoke_fn=lambda: calls.append("must-not-run"),
            )
        self.assertNotIn("must-not-run", calls)


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

    def test_list_sessions_normalizes_missing_upstream_collection_route_to_degraded(self):
        mock_client = MagicMock()
        mock_client.list_sessions.side_effect = adapter_main.UpstreamClientError(
            status_code=404,
            error_code="UPSTREAM_NOT_FOUND",
            message="OpenClaw upstream returned HTTP 404.",
            retryable=False,
            upstream_status=404,
        )
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.get("/api/openclaw-adapter/sessions")

        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["status"], "upstream_unavailable")
        self.assertEqual(body["sessions"], [])
        self.assertEqual(body["upstream"]["error_code"], "UPSTREAM_UNAVAILABLE")
        self.assertTrue(body["upstream"]["retryable"])
        self.assertEqual(body["upstream"]["upstream_status"], 404)
        self.assertEqual(body["upstream"]["details"]["route"], "/api/sessions")

    def test_create_session_normalizes_missing_upstream_collection_route_to_degraded(self):
        mock_client = MagicMock()
        mock_client.create_session.side_effect = adapter_main.UpstreamClientError(
            status_code=404,
            error_code="UPSTREAM_NOT_FOUND",
            message="OpenClaw upstream returned HTTP 404.",
            retryable=False,
            upstream_status=404,
        )
        with patch.object(adapter_main, "_client", return_value=mock_client):
            resp = client.post(
                "/api/openclaw-adapter/sessions",
                json={"agent_id": "agent-test", "session_type": "interactive"},
            )

        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["status"], "upstream_error")
        self.assertEqual(body["error_code"], "UPSTREAM_UNAVAILABLE")
        self.assertTrue(body["retryable"])
        self.assertEqual(body["owner_plane"], "openclaw_runtime")
        self.assertEqual(body["error_layer"], "upstream")
        self.assertEqual(body["upstream_status"], 404)
        self.assertEqual(body["details"]["route"], "/api/sessions")


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

    def test_openclaw_kernel_debug_delegates_to_codex_read_only_runtime(self):
        fake_result = types.SimpleNamespace(
            provider="codex_cli",
            mode="kernel_debug",
            status="completed",
            output={
                "provider": "codex_cli",
                "runtime": "openclaw_gateway_cli_mount",
                "status": "completed",
                "sandbox": "read-only",
                "workspace_class": "read_only",
                "json_events": [{"final": "debug complete"}],
            },
            redaction={"provider_invocation": {"enabled": True}},
        )
        with (
            patch.object(adapter_main._CODEX_RUNTIME, "invoke", return_value=fake_result) as codex_invoke,
            patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "invoke") as openclaw_invoke,
        ):
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/invoke",
                json={
                    "mode": "kernel_debug",
                    "prompt": "inspect the repository",
                    "context_pack": {"context_pack_id": "ctx-debug"},
                    "metadata": {"tenant_id": "tenant-alpha", "activation_id": "ctrl-debug"},
                },
                headers={"X-Operator-Id": "op-debug", "X-Trace-Id": "trace-debug"},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["provider"], "codex_cli")
        self.assertEqual(data["runtime"], "openclaw_gateway_cli_mount")
        self.assertEqual(data["delegated_from"], "openclaw")
        self.assertEqual(data["output"]["sandbox"], "read-only")
        self.assertEqual(data["output"]["workspace_class"], "read_only")
        self.assertEqual(data["output"]["delegation"]["to_provider"], "codex_cli")
        request = codex_invoke.call_args.args[0]
        self.assertEqual(request.provider, "codex_cli")
        self.assertEqual(request.mode, "kernel_debug")
        self.assertEqual(request.context_pack, {"context_pack_id": "ctx-debug"})
        self.assertEqual(request.metadata["tenant_id"], "tenant-alpha")
        self.assertEqual(request.metadata["activation_id"], "ctrl-debug")
        self.assertEqual(request.metadata["operator_id"], "op-debug")
        self.assertEqual(request.metadata["trace_id"], "trace-debug")
        openclaw_invoke.assert_not_called()

    def test_openclaw_kernel_debug_stream_delegates_to_codex_runtime(self):
        fake_result = types.SimpleNamespace(
            provider="codex_cli",
            mode="kernel_debug",
            status="completed",
            output={
                "provider": "codex_cli",
                "runtime": "openclaw_gateway_cli_mount",
                "sandbox": "read-only",
                "workspace_class": "read_only",
                "json_events": [{"final": "streamed debug answer"}],
            },
            redaction={"provider_invocation": {"enabled": True}},
        )
        with (
            patch.object(adapter_main._CODEX_RUNTIME, "invoke", return_value=fake_result) as codex_invoke,
            patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "stream") as openclaw_stream,
        ):
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/invoke/stream",
                json={
                    "mode": "kernel_debug",
                    "prompt": "inspect",
                    "metadata": {"tenant_id": "tenant-alpha"},
                },
                headers={"X-Operator-Id": "op-debug", "X-Trace-Id": "trace-stream"},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        events = [
            json.loads(line.removeprefix("data: "))
            for line in resp.text.splitlines()
            if line.startswith("data: {")
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "done")
        self.assertEqual(events[0]["text"], "streamed debug answer")
        self.assertEqual(events[0]["provider"], "codex_cli")
        self.assertEqual(events[0]["sandbox"], "read-only")
        self.assertEqual(events[0]["workspace_class"], "read_only")
        request = codex_invoke.call_args.args[0]
        self.assertEqual(request.metadata["tenant_id"], "tenant-alpha")
        self.assertEqual(request.metadata["operator_id"], "op-debug")
        openclaw_stream.assert_not_called()

    def test_openclaw_ordinary_stream_forwards_full_mounted_contract(self):
        """SIMPLIFY-OPENCLAW-001 reviewer defect: the mounted stream route
        previously dropped `req.messages` and never forwarded
        `req.attachments`/`req.context_pack` at all — only the mounted
        invoke route did (and even that route silently ignored
        attachments). Both must reach the provider's single HTTP request
        builder. (Admitted `agent_id` forwarding is exercised separately in
        `TestGovernedServantAgentSync`'s persona-opinion invoke coverage,
        which requires a fully governed admission fixture.)"""

        def fake_stream(self_inner, prompt, **kwargs):
            yield {"type": "done", "text": "ok", "elapsed_ms": 1, "transport": "responses_http"}

        with patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER.__class__, "stream", autospec=True) as openclaw_stream:
            openclaw_stream.side_effect = fake_stream
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/invoke/stream",
                json={
                    "prompt": "hello",
                    "messages": [{"role": "user", "content": "earlier turn"}],
                    "attachments": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}],
                    "context_pack": {"context_pack_id": "ctx-1"},
                },
                headers={"X-Operator-Id": "op-1", "X-Trace-Id": "trace-1"},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        _args, kwargs = openclaw_stream.call_args
        self.assertEqual(kwargs["messages"], [{"role": "user", "content": "earlier turn"}])
        self.assertEqual(
            kwargs["attachments"],
            [{"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}],
        )
        self.assertEqual(kwargs["context_pack"], {"context_pack_id": "ctx-1"})
        self.assertEqual(kwargs["trace_id"], "trace-1")

    def test_invoke_stream_applies_same_persona_admission_fencing_as_invoke(self):
        """SIMPLIFY-OPENCLAW-001 reviewer finding 1: forwarding `req.agent_id`
        directly to the stream path bypassed ordinary `/invoke`'s Persona
        admission/runtime-policy/live-agent/idempotency fencing entirely.
        Reproduction: the exact same syntactically valid Persona request
        with an empty admission DB (no prior
        `/agents/persona-opinion/ensure` call) must be denied on BOTH routes
        with the same rejection, and the provider's `invoke()`/`stream()`
        must never be reached on either."""
        tenant_id = "tenant-fence"
        persona_id = "persona-fence"
        persona_version = "persona-fence-v1"
        snapshot_id = "snapshot-fence-v1"
        requested_environment = "paper"
        agent_id = adapter_main._persona_opinion_agent_id(
            tenant_id, persona_id, persona_version, snapshot_id, requested_environment
        )
        admission = {
            "persona_id": persona_id,
            "tenant_id": tenant_id,
            "persona_version": persona_version,
            "agent_id": agent_id,
            "workspace_ref": f"/home/node/.openclaw/workspaces/{agent_id}",
            "capability_snapshot_id": snapshot_id,
            "allowed_capabilities": ["persona_opinion"],
            "environment_ceiling": "paper",
            "requested_environment": requested_environment,
            "execution_authority": "none",
            "display_name": "Fence",
        }
        body = {
            "mode": "user",
            "prompt": "attempt without prior admission",
            "agent_id": agent_id,
            "persona_admission": admission,
            "metadata": {"allowed_tools": []},
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_patch = patch.object(
                adapter_main,
                "_OPENCLAW_AGENT_IDEMPOTENCY_DB",
                Path(tmp_dir) / "agent-ensure.sqlite3",
            )
            db_patch.start()
            try:
                with patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "invoke") as invoke_mock:
                    invoke_resp = client.post(
                        "/api/openclaw-adapter/assistant/providers/openclaw/invoke",
                        json=body,
                        headers={"X-Operator-Id": "op-1", "Idempotency-Key": "fence-invoke-1"},
                    )
                self.assertEqual(invoke_resp.status_code, 403, invoke_resp.text)
                self.assertEqual(invoke_resp.json()["error_code"], "PERSONA_OPINION_ADMISSION_DENIED")
                invoke_mock.assert_not_called()

                with patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "stream") as stream_mock:
                    stream_resp = client.post(
                        "/api/openclaw-adapter/assistant/providers/openclaw/invoke/stream",
                        json=body,
                        headers={"X-Operator-Id": "op-1", "Idempotency-Key": "fence-stream-1"},
                    )
                self.assertEqual(stream_resp.status_code, 200, stream_resp.text)
                events = [
                    json.loads(line.removeprefix("data: "))
                    for line in stream_resp.text.splitlines()
                    if line.startswith("data: {")
                ]
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["type"], "error")
                self.assertEqual(events[0]["error_code"], "PERSONA_OPINION_ADMISSION_DENIED")
                stream_mock.assert_not_called()
            finally:
                db_patch.stop()

    def test_openclaw_invoke_forwards_attachments(self):
        """The mounted (non-stream) invoke route must also forward
        `req.attachments` to the provider — previously silently dropped."""

        def fake_invoke(self_inner, prompt, *, mode="user", context_pack=None, metadata=None,
                        messages=None, attachments=None, operator_id=None, trace_id=None):
            from assistant_openclaw_provider import OpenClawProviderResult
            return OpenClawProviderResult(
                provider="openclaw",
                mode=mode,
                status="completed",
                output={"json_events": [], "agent_id": "main", "request_id": "req-1", "duration_ms": 1},
                redaction={"provider_invocation": {"redacted_fields": 0}},
            )

        with patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER.__class__, "invoke", autospec=True) as openclaw_invoke:
            openclaw_invoke.side_effect = fake_invoke
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/invoke",
                json={
                    "prompt": "hello",
                    "attachments": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}],
                },
                headers={"X-Operator-Id": "op-1"},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        _args, kwargs = openclaw_invoke.call_args
        self.assertEqual(
            kwargs["attachments"],
            [{"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}],
        )

    def test_openclaw_invoke_returns_completed_result_on_success(self):
        fake_response_body = {
            "status": "completed",
            "agent_id": "main",
            "output": {"text": "OpenClaw agent answer."},
        }

        def fake_invoke(self_inner, prompt, *, mode="user", context_pack=None, metadata=None,
                        messages=None, attachments=None, operator_id=None, trace_id=None):
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

        with (
            patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER.__class__, "invoke", fake_invoke),
            patch.object(adapter_main._CODEX_RUNTIME, "invoke") as codex_invoke,
        ):
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
        codex_invoke.assert_not_called()

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

    def test_openclaw_readiness_is_not_ready_until_answer_probe_runs(self):
        with patch.dict(os.environ, {"OPENCLAW_GATEWAY_URL": "http://openclaw-gateway:18789"}):
            # Re-read via the readiness endpoint directly
            from assistant_openclaw_provider import AssistantOpenClawProvider
            prov = AssistantOpenClawProvider(gateway_url="http://openclaw-gateway:18789")
            result = prov.readiness(auth_probe=False)

        self.assertEqual(result["status"], "not_checked")
        self.assertFalse(result["ready"])
        self.assertEqual(result["provider"], "openclaw")
        self.assertEqual(result["reason"], "answer_probe_not_run")

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
