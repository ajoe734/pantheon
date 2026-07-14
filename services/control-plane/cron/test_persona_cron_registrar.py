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


def _existing_job_fixture(workflow_id: str, persona_id: str, job_id: str) -> dict:
    """Build a fixture cron-list entry shaped like a real registered job.

    Existence is now matched on the payload's ``persona_id``/``workflow_id``
    (see ``PersonaCronRegistrar._existing_registrations``), not the "name"
    field, so fixtures must carry a real payload to be recognized as already
    registered.
    """
    return {
        "id": job_id,
        "name": _job_name(workflow_id, persona_id),
        "payload": {
            "kind": "systemEvent",
            "text": json.dumps({"persona_id": persona_id, "workflow_id": workflow_id}),
        },
    }


class GatewayRuntimeSpy:
    """Minimal spy that records gateway_call invocations and returns fake job IDs."""

    def __init__(
        self,
        fail_on: set[str] | None = None,
        existing_jobs: list[dict] | None = None,
        cron_list_page_size: int | None = None,
        cron_list_raises: bool = False,
    ):
        self.calls: list[tuple[str, dict | None]] = []
        self._fail_on = fail_on or set()
        self._existing_jobs = existing_jobs or []
        self._cron_list_page_size = cron_list_page_size
        self._cron_list_raises = cron_list_raises

    def gateway_call(self, method: str, params: dict | None = None) -> dict:
        self.calls.append((method, params))
        if method == "cron.list":
            if self._cron_list_raises:
                raise RuntimeError("simulated cron.list failure")
            if self._cron_list_page_size is None:
                return {"jobs": list(self._existing_jobs)}
            offset = (params or {}).get("offset", 0)
            page = self._existing_jobs[offset:offset + self._cron_list_page_size]
            next_offset = offset + len(page)
            return {
                "jobs": page,
                "hasMore": next_offset < len(self._existing_jobs),
                "nextOffset": next_offset,
            }
        if method == "cron.remove":
            job_id = (params or {}).get("id")
            self._existing_jobs = [j for j in self._existing_jobs if j.get("id") != job_id]
            return {"status": "ok"}
        # OpenClaw's cron.add schema has no "metadata" property; workflow_id
        # is only recoverable from the systemEvent payload text, same as in
        # production (see persona_cron_registrar._build_system_event_text).
        payload_text = (params or {}).get("payload", {}).get("text", "{}")
        try:
            workflow_id = json.loads(payload_text).get("workflow_id", "unknown")
        except Exception:
            workflow_id = "unknown"
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

    def test_realistic_persona_id_is_not_truncated(self):
        # Regression: persona ids of the form "persona-<date>-<hex>" (~26
        # chars) must round-trip fully so same-day personas never collide.
        name = _job_name("pantheon.ingest", "persona-20260528-04688755")
        self.assertTrue(name.endswith("persona-20260528-04688755"), name)

    def test_same_day_persona_ids_do_not_collide(self):
        # These two ids previously truncated to the same 16-char prefix
        # ("persona-20260528"), causing the second persona's registration to
        # be silently skipped as a false-positive idempotent duplicate.
        name_a = _job_name("pantheon.ingest", "persona-20260528-04688755")
        name_b = _job_name("pantheon.ingest", "persona-20260528-5937dea1")
        self.assertNotEqual(name_a, name_b)

    def test_long_persona_ids_with_shared_prefix_do_not_collide(self):
        long_a = "persona-" + "x" * 100 + "-a"
        long_b = "persona-" + "x" * 100 + "-b"
        name_a = _job_name("pantheon.deploy", long_a)
        name_b = _job_name("pantheon.deploy", long_b)
        self.assertNotEqual(name_a, name_b)
        self.assertLessEqual(len(name_a), 60)
        self.assertLessEqual(len(name_b), 60)


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

    def test_persona_id_is_embedded_in_payload_text(self):
        # cron.add has no "metadata" property in OpenClaw's schema (additionalProperties:
        # false rejects it), so persona_id must travel in the systemEvent payload text.
        spy = GatewayRuntimeSpy()
        PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-meta-001", binding_id="bind-001")
        for _, params in spy.add_calls:
            self.assertNotIn("metadata", params or {})
            payload_text = json.loads((params or {}).get("payload", {}).get("text", "{}"))
            self.assertEqual(payload_text.get("persona_id"), "persona-meta-001")

    def test_idempotent_skip_when_job_already_present(self):
        # Pre-seed two of the four workflow jobs as already-registered.
        existing = [
            _existing_job_fixture("pantheon.ingest", "persona-idem-001", "j1"),
            _existing_job_fixture("pantheon.deploy", "persona-idem-001", "j2"),
        ]
        spy = GatewayRuntimeSpy(existing_jobs=existing)
        result = PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-idem-001")
        self.assertEqual(len(result.skipped), 2)
        self.assertEqual(len(result.registered), len(WORKFLOW_CATALOG) - 2)
        # cron.add must NOT be called for the pre-existing jobs.
        self.assertEqual(len(spy.add_calls), len(WORKFLOW_CATALOG) - 2)

    def test_skip_matches_by_payload_identity_not_job_name(self):
        # Regression: two personas whose ids share a long common prefix (e.g.
        # the same creation date) can end up with the same truncated/hashed
        # job "name". Matching on name alone would either wrongly skip a
        # persona that was never registered, or (if the naming scheme ever
        # changes) fail to recognize an already-registered persona and
        # re-add it under a new name. Match on the payload's real
        # persona_id/workflow_id instead.
        other_persona_same_name_bucket = _existing_job_fixture(
            "pantheon.ingest", "persona-real-owner", "j1"
        )
        # Force a job-name collision: same displayed name, different persona.
        other_persona_same_name_bucket["name"] = _job_name("pantheon.ingest", "persona-collider")
        spy = GatewayRuntimeSpy(existing_jobs=[other_persona_same_name_bucket])
        result = PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-collider")
        # persona-collider's own ingest job was never actually registered
        # (only persona-real-owner's was, under a colliding name), so it must
        # still be created rather than silently skipped.
        self.assertEqual(len(result.skipped), 0)
        self.assertEqual(len(result.registered), len(WORKFLOW_CATALOG))

    def test_existing_job_names_paginates_past_single_page_limit(self):
        # Regression: the gateway rejects cron.list limit > 200, so a listing
        # of more than one page must be paginated via offset/hasMore rather
        # than requested in one oversized call.
        existing = [
            _existing_job_fixture("pantheon.ingest", "persona-page-001", "j1"),
            _existing_job_fixture("pantheon.deploy", "persona-page-001", "j2"),
        ]
        spy = GatewayRuntimeSpy(existing_jobs=existing, cron_list_page_size=1)
        result = PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-page-001")
        cron_list_calls = [c for c in spy.calls if c[0] == "cron.list"]
        self.assertGreaterEqual(len(cron_list_calls), 2)
        for _, params in cron_list_calls:
            self.assertLessEqual((params or {}).get("limit", 0), 200)
        self.assertEqual(len(result.skipped), 2)
        self.assertEqual(len(result.registered), len(WORKFLOW_CATALOG) - 2)

    def test_cron_list_failure_fails_closed_instead_of_duplicating(self):
        # Regression: if cron.list cannot be verified, the registrar must NOT
        # fall through to calling cron.add for every workflow (that silently
        # created real duplicate jobs for personas that already had them).
        spy = GatewayRuntimeSpy(cron_list_raises=True)
        result = PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-failclosed-001")
        self.assertEqual(spy.add_calls, [])
        self.assertEqual(result.registered, [])
        self.assertTrue(result.failed)

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
        results, removed, remove_failed = PersonaCronRegistrar(gateway_runtime=spy).reconcile_personas(
            ["persona-a", "persona-b"]
        )
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.mode == "gateway_rpc" for r in results))
        self.assertEqual(len(spy.add_calls), 2 * len(WORKFLOW_CATALOG))
        self.assertEqual(removed, [])
        self.assertEqual(remove_failed, [])

    def test_reconcile_personas_removes_orphans(self):
        # Seed some existing jobs, including one valid job for persona-a and one orphan job for persona-orphan
        existing = [
            _existing_job_fixture("pantheon.ingest", "persona-a", "job-valid-1"),
            _existing_job_fixture("pantheon.ingest", "persona-orphan", "job-orphan-1"),
            # Also seed a job with no/invalid payload matching "pantheon-"
            {
                "id": "job-orphan-2",
                "name": "pantheon-invalid-job",
                "payload": {}
            }
        ]
        spy = GatewayRuntimeSpy(existing_jobs=existing)
        results, removed, remove_failed = PersonaCronRegistrar(gateway_runtime=spy).reconcile_personas(
            ["persona-a"]
        )
        # Should register 3 missing jobs for persona-a (valid ingest is skipped)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0].registered), len(WORKFLOW_CATALOG) - 1)
        self.assertEqual(results[0].skipped, ["pantheon-pantheon-ingest-persona-a"])

        # Should remove 2 orphan jobs
        self.assertEqual(len(removed), 2)
        removed_ids = {r["job_id"] for r in removed}
        self.assertEqual(removed_ids, {"job-orphan-1", "job-orphan-2"})
        self.assertEqual(remove_failed, [])

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
