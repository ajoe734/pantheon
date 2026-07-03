"""Tests for PersonaCronRegistrar.

All tests use a spy gateway runtime so no Docker or network calls are made.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from persona_cron_registrar import (
    PersonaCronRegistrar,
    PersonaCronRegistrationResult,
    _job_name,
)
from workflows import WORKFLOW_CATALOG


class GatewayRuntimeSpy:
    """Minimal spy that records gateway_call invocations and returns fake job IDs."""

    def __init__(self, fail_on: set[str] | None = None, existing_jobs: list[dict] | None = None):
        self.calls: list[tuple[str, dict | None]] = []
        self._fail_on = fail_on or set()
        self._existing_jobs = existing_jobs or []

    def gateway_call(self, method: str, params: dict | None = None) -> dict:
        self.calls.append((method, params))
        if method == "cron.list":
            return {"jobs": list(self._existing_jobs)}
        workflow_id = (params or {}).get("metadata", {}).get("workflow_id", "unknown")
        if workflow_id in self._fail_on:
            raise RuntimeError(f"Simulated failure for {workflow_id}")
        return {"id": f"job-{workflow_id}-001"}

    @property
    def add_calls(self) -> list[tuple[str, dict | None]]:
        return [c for c in self.calls if c[0] == "cron.add"]


class TestJobName(unittest.TestCase):
    def test_name_is_deterministic_and_slug_safe(self):
        name = _job_name("pantheon.ingest", "persona-abc-123")
        self.assertTrue(name.startswith("pantheon-"), name)
        self.assertNotIn(".", name)
        self.assertNotIn("_", name)

    def test_long_persona_id_is_truncated(self):
        name = _job_name("pantheon.deploy", "persona-" + "x" * 100)
        self.assertLessEqual(len(name), 60)


class TestPersonaCronRegistrarDryRun(unittest.TestCase):
    def test_dry_run_returns_all_four_workflows(self):
        registrar = PersonaCronRegistrar(dry_run=True)
        result = registrar.register_for_persona("persona-test-001")

        self.assertIsInstance(result, PersonaCronRegistrationResult)
        self.assertEqual(result.mode, "dry_run")
        self.assertEqual(len(result.registered), len(WORKFLOW_CATALOG))
        self.assertEqual(result.failed, [])
        self.assertEqual(result.persona_id, "persona-test-001")

    def test_dry_run_without_adapter_env(self):
        with patch.dict("os.environ", {
            "OPENCLAW_PAPER_ADAPTER_ENABLED": "false",
            "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL": "",
        }):
            registrar = PersonaCronRegistrar()
            result = registrar.register_for_persona("persona-paper-001")
        self.assertEqual(result.mode, "dry_run")
        self.assertEqual(len(result.registered), len(WORKFLOW_CATALOG))

    def test_dry_run_result_is_serializable(self):
        registrar = PersonaCronRegistrar(dry_run=True)
        result = registrar.register_for_persona("persona-serial-001")
        d = result.to_dict()
        serialized = json.dumps(d)
        self.assertIn("persona-serial-001", serialized)
        self.assertIn("dry_run", serialized)


class TestPersonaCronRegistrarGatewayRpc(unittest.TestCase):
    def test_registers_all_workflows_via_gateway(self):
        spy = GatewayRuntimeSpy()
        registrar = PersonaCronRegistrar(gateway_runtime=spy)
        result = registrar.register_for_persona("persona-gw-001", capital_pool_id="pool-001")

        self.assertEqual(result.mode, "gateway_rpc")
        self.assertEqual(len(result.registered), len(WORKFLOW_CATALOG))
        self.assertEqual(result.failed, [])
        self.assertEqual(len(spy.add_calls), len(WORKFLOW_CATALOG))

    def test_each_call_uses_cron_add_method(self):
        spy = GatewayRuntimeSpy()
        PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-method-001")
        add_methods = [method for method, _ in spy.add_calls]
        self.assertEqual(len(add_methods), len(WORKFLOW_CATALOG))
        self.assertTrue(all(m == "cron.add" for m in add_methods))

    def test_registered_jobs_have_expected_fields(self):
        spy = GatewayRuntimeSpy()
        result = PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-fields-001")
        for record in result.registered:
            self.assertIn(record.workflow_id, WORKFLOW_CATALOG)
            self.assertTrue(record.job_name.startswith("pantheon-"))
            self.assertTrue(record.job_id.startswith("job-"))
            self.assertIn(record.schedule, {wf.schedule for wf in WORKFLOW_CATALOG.values()})
            self.assertTrue(record.registered_at)

    def test_each_job_uses_recurring_cron_schedule(self):
        spy = GatewayRuntimeSpy()
        PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-sched-001")
        for _, params in spy.add_calls:
            schedule = (params or {}).get("schedule", {})
            # OpenClaw 2026.6.8 cron schema: {"kind":"cron","expr":"<cron>"}.
            self.assertEqual(schedule.get("kind"), "cron")
            self.assertIn("expr", schedule)
            self.assertNotIn("cron", schedule)

    def test_delete_after_run_is_false_for_recurring_jobs(self):
        spy = GatewayRuntimeSpy()
        PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-del-001")
        for _, params in spy.add_calls:
            self.assertFalse((params or {}).get("deleteAfterRun"))

    def test_persona_id_is_embedded_in_metadata(self):
        spy = GatewayRuntimeSpy()
        PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-meta-001", binding_id="bind-001")
        for _, params in spy.add_calls:
            metadata = (params or {}).get("metadata", {})
            self.assertEqual(metadata.get("persona_id"), "persona-meta-001")
            self.assertEqual(metadata.get("binding_id"), "bind-001")

    def test_idempotent_skip_when_job_already_present(self):
        # Pre-seed two of the four workflow job names as already-registered.
        existing = [
            {"name": _job_name("pantheon.ingest", "persona-idem-001"), "id": "j1"},
            {"name": _job_name("pantheon.deploy", "persona-idem-001"), "id": "j2"},
        ]
        spy = GatewayRuntimeSpy(existing_jobs=existing)
        result = PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-idem-001")
        self.assertEqual(len(result.skipped), 2)
        self.assertEqual(len(result.registered), len(WORKFLOW_CATALOG) - 2)
        # cron.add must NOT be called for the pre-existing jobs.
        self.assertEqual(len(spy.add_calls), len(WORKFLOW_CATALOG) - 2)

    def test_session_target_defaults_to_persona_own_agent(self):
        spy = GatewayRuntimeSpy()
        PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-crypto")
        for _, params in spy.add_calls:
            self.assertEqual((params or {}).get("sessionTarget"), "persona-crypto")

    def test_session_target_override_is_respected(self):
        spy = GatewayRuntimeSpy()
        PersonaCronRegistrar(gateway_runtime=spy, session_target="main").register_for_persona("persona-crypto")
        for _, params in spy.add_calls:
            self.assertEqual((params or {}).get("sessionTarget"), "main")

    def test_reconcile_personas_registers_each(self):
        spy = GatewayRuntimeSpy()
        results = PersonaCronRegistrar(gateway_runtime=spy).reconcile_personas(
            ["persona-a", "persona-b"]
        )
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.mode == "gateway_rpc" for r in results))
        self.assertEqual(len(spy.add_calls), 2 * len(WORKFLOW_CATALOG))

    def test_adapter_runtime_selected_when_adapter_url_set(self):
        from persona_cron_registrar import AdapterCronRuntime
        with patch.dict(
            "os.environ",
            {"PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL": "http://openclaw-gateway-adapter:8104"},
        ):
            runtime = PersonaCronRegistrar()._get_runtime()
        self.assertIsInstance(runtime, AdapterCronRuntime)

    def test_gateway_failure_captured_in_failed_list(self):
        spy = GatewayRuntimeSpy(fail_on={"pantheon.deploy"})
        result = PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-fail-001")
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(result.failed[0]["workflow_id"], "pantheon.deploy")
        self.assertEqual(len(result.registered), len(WORKFLOW_CATALOG) - 1)

    def test_all_failures_captured_gracefully(self):
        spy = GatewayRuntimeSpy(fail_on=set(WORKFLOW_CATALOG.keys()))
        result = PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-allfail-001")
        self.assertEqual(len(result.failed), len(WORKFLOW_CATALOG))
        self.assertEqual(result.registered, [])
        self.assertEqual(result.mode, "gateway_rpc")

    def test_to_dict_is_json_serializable(self):
        spy = GatewayRuntimeSpy()
        result = PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-dict-001")
        d = result.to_dict()
        json.dumps(d)


if __name__ == "__main__":
    unittest.main()
