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

    def __init__(self, fail_on: set[str] | None = None):
        self.calls: list[tuple[str, dict | None]] = []
        self._fail_on = fail_on or set()

    def gateway_call(self, method: str, params: dict | None = None) -> dict:
        self.calls.append((method, params))
        workflow_id = (params or {}).get("metadata", {}).get("workflow_id", "unknown")
        if workflow_id in self._fail_on:
            raise RuntimeError(f"Simulated failure for {workflow_id}")
        return {"id": f"job-{workflow_id}-001"}


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
        with patch.dict("os.environ", {"OPENCLAW_PAPER_ADAPTER_ENABLED": "false"}):
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
        self.assertEqual(len(spy.calls), len(WORKFLOW_CATALOG))

    def test_each_call_uses_cron_add_method(self):
        spy = GatewayRuntimeSpy()
        PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-method-001")
        methods = [method for method, _ in spy.calls]
        self.assertTrue(all(m == "cron.add" for m in methods))

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
        for _, params in spy.calls:
            schedule = (params or {}).get("schedule", {})
            self.assertEqual(schedule.get("kind"), "cron")
            self.assertIn("cron", schedule)

    def test_delete_after_run_is_false_for_recurring_jobs(self):
        spy = GatewayRuntimeSpy()
        PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-del-001")
        for _, params in spy.calls:
            self.assertFalse((params or {}).get("deleteAfterRun"))

    def test_persona_id_is_embedded_in_metadata(self):
        spy = GatewayRuntimeSpy()
        PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-meta-001", binding_id="bind-001")
        for _, params in spy.calls:
            metadata = (params or {}).get("metadata", {})
            self.assertEqual(metadata.get("persona_id"), "persona-meta-001")
            self.assertEqual(metadata.get("binding_id"), "bind-001")

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
