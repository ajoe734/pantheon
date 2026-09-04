"""Tests for PersonaCronRegistrar.

All tests use a spy gateway runtime so no Docker or network calls are made.
"""
from __future__ import annotations

import json
import unittest
from collections.abc import Callable
from unittest.mock import patch

from services.control_plane.cron.persona_cron_registrar import (
    AdapterCronRuntime,
    PersonaCronRegistrar,
    PersonaCronRegistrationResult,
    _job_name,
)
from services.control_plane.cron.workflows import (
    PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
    WORKFLOW_CATALOG,
)


def _existing_job_fixture(
    workflow_id: str,
    persona_id: str,
    job_id: str,
    *,
    runtime_id: str | None = None,
    runtime_binding_id: str | None = None,
    capital_pool_id: str | None = None,
    persona_capital_binding_id: str | None = None,
) -> dict:
    """Build a fixture cron-list entry shaped like a real registered job.

    Existence is now matched on the payload's ``persona_id``/``workflow_id``
    (see ``PersonaCronRegistrar._existing_registrations``), not the "name"
    field, so fixtures must carry a real payload to be recognized as already
    registered.
    """
    workflow = WORKFLOW_CATALOG[workflow_id]
    event = {
        "kind": "pantheon.workflow.dispatch",
        "persona_id": persona_id,
        "policy_id": workflow.policy_id,
        "request_id": f"persona-provisioning:{persona_id}:{workflow_id}",
        "upstream_entrypoint": workflow.upstream_entrypoint,
        "workflow_id": workflow_id,
    }
    if workflow_id == PERSONA_FIRST_EVALUATION_WORKFLOW_ID:
        event.update(
            {
                "runtime_id": runtime_id,
                "runtime_binding_id": runtime_binding_id,
                "capital_pool_id": capital_pool_id,
                "persona_capital_binding_id": persona_capital_binding_id,
            }
        )
    return {
        "id": job_id,
        "name": _job_name(workflow_id, persona_id),
        "enabled": True,
        "deleteAfterRun": False,
        "schedule": {"kind": "cron", "expr": workflow.schedule},
        "sessionTarget": "main",
        "wakeMode": "next-heartbeat",
        "payload": {
            "kind": "systemEvent",
            "text": json.dumps(event),
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
        response_loss_after_apply: set[str] | None = None,
        mutation_fail_before_apply: set[str] | None = None,
    ):
        self.calls: list[tuple[str, dict | None]] = []
        self._fail_on = fail_on or set()
        self._existing_jobs = existing_jobs or []
        self._cron_list_page_size = cron_list_page_size
        self._cron_list_raises = cron_list_raises
        self._response_loss_after_apply = set(response_loss_after_apply or set())
        self._mutation_fail_before_apply = set(mutation_fail_before_apply or set())

    def _fail_before_apply(self, method: str) -> None:
        if method in self._mutation_fail_before_apply:
            self._mutation_fail_before_apply.remove(method)
            raise RuntimeError(f"simulated {method} failure before apply")

    def _lose_response_after_apply(self, method: str) -> None:
        if method in self._response_loss_after_apply:
            self._response_loss_after_apply.remove(method)
            raise RuntimeError(f"simulated {method} response loss after apply")

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
            self._fail_before_apply(method)
            job_id = (params or {}).get("id")
            self._existing_jobs = [j for j in self._existing_jobs if j.get("id") != job_id]
            self._lose_response_after_apply(method)
            return {"status": "ok"}
        if method == "cron.update":
            self._fail_before_apply(method)
            job_id = (params or {}).get("id")
            patch_body = (params or {}).get("patch")
            if not isinstance(patch_body, dict):
                raise RuntimeError("cron.update requires a patch")
            matching_jobs = [job for job in self._existing_jobs if job.get("id") == job_id]
            if len(matching_jobs) != 1:
                raise RuntimeError(f"cron.update could not find unique job {job_id}")
            matching_jobs[0].update(patch_body)
            self._lose_response_after_apply(method)
            return {"id": job_id}
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
        self._fail_before_apply("cron.add")
        job_id = f"job-{workflow_id}-{len(self._existing_jobs) + 1:03d}"
        self._existing_jobs.append({"id": job_id, **dict(params or {})})
        self._lose_response_after_apply("cron.add")
        return {"id": job_id}

    @property
    def add_calls(self) -> list[tuple[str, dict | None]]:
        return [c for c in self.calls if c[0] == "cron.add"]

    @property
    def update_calls(self) -> list[tuple[str, dict | None]]:
        return [c for c in self.calls if c[0] == "cron.update"]

    @property
    def remove_calls(self) -> list[tuple[str, dict | None]]:
        return [c for c in self.calls if c[0] == "cron.remove"]


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
    def test_dry_run_returns_all_catalog_workflows(self):
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

    def test_dry_run_respects_explicit_workflow_selection(self):
        registrar = PersonaCronRegistrar(dry_run=True)

        result = registrar.register_for_persona(
            "persona-selected-dry-run",
            workflow_ids=[PERSONA_FIRST_EVALUATION_WORKFLOW_ID],
        )

        self.assertEqual(
            [record.workflow_id for record in result.registered],
            [PERSONA_FIRST_EVALUATION_WORKFLOW_ID],
        )


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

    def test_explicit_workflow_ids_register_only_selected_workflow(self):
        spy = GatewayRuntimeSpy()

        result = PersonaCronRegistrar(gateway_runtime=spy).register_for_persona(
            "persona-selected-001",
            capital_pool_id="pool-selected-001",
            workflow_ids=[PERSONA_FIRST_EVALUATION_WORKFLOW_ID],
            runtime_id="runtime-selected-001",
            runtime_binding_id="runtime-binding-selected-001",
            persona_capital_binding_id="persona-capital-binding-selected-001",
        )

        self.assertEqual(result.failed, [])
        self.assertEqual(
            [record.workflow_id for record in result.registered],
            [PERSONA_FIRST_EVALUATION_WORKFLOW_ID],
        )
        self.assertEqual(len(spy.add_calls), 1)

    def test_selected_first_evaluation_is_not_failed_by_unrelated_workflows(self):
        unrelated_workflows = set(WORKFLOW_CATALOG) - {
            PERSONA_FIRST_EVALUATION_WORKFLOW_ID
        }
        spy = GatewayRuntimeSpy(fail_on=unrelated_workflows)

        result = PersonaCronRegistrar(gateway_runtime=spy).register_for_persona(
            "persona-required-first-eval-001",
            workflow_ids=[PERSONA_FIRST_EVALUATION_WORKFLOW_ID],
        )

        self.assertEqual(result.failed, [])
        self.assertEqual(len(result.registered), 1)
        self.assertEqual(len(spy.add_calls), 1)

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

    def test_registers_stable_persona_first_evaluation_workflow(self):
        spy = GatewayRuntimeSpy()
        result = PersonaCronRegistrar(gateway_runtime=spy).register_for_persona(
            "persona-first-eval-001",
            capital_pool_id="pool-first-eval-001",
            workflow_ids=[PERSONA_FIRST_EVALUATION_WORKFLOW_ID],
            runtime_id="runtime-first-eval-001",
            runtime_binding_id="runtime-binding-first-eval-001",
            persona_capital_binding_id="persona-capital-binding-first-eval-001",
        )

        matching_records = [
            record
            for record in result.registered
            if record.workflow_id == PERSONA_FIRST_EVALUATION_WORKFLOW_ID
        ]
        self.assertEqual(len(matching_records), 1)
        matching_calls = []
        for _, params in spy.add_calls:
            payload = json.loads((params or {}).get("payload", {}).get("text", "{}"))
            if payload.get("workflow_id") == PERSONA_FIRST_EVALUATION_WORKFLOW_ID:
                matching_calls.append((params, payload))
        self.assertEqual(len(matching_calls), 1)
        params, payload = matching_calls[0]
        self.assertEqual(payload["persona_id"], "persona-first-eval-001")
        self.assertEqual(payload["runtime_id"], "runtime-first-eval-001")
        self.assertEqual(
            payload["runtime_binding_id"], "runtime-binding-first-eval-001"
        )
        self.assertEqual(payload["capital_pool_id"], "pool-first-eval-001")
        self.assertEqual(
            payload["persona_capital_binding_id"],
            "persona-capital-binding-first-eval-001",
        )
        self.assertEqual(
            (params or {}).get("schedule", {}).get("expr"),
            WORKFLOW_CATALOG[PERSONA_FIRST_EVALUATION_WORKFLOW_ID].schedule,
        )

    def test_first_evaluation_reconciles_null_runtime_identity_via_update(self):
        persona_id = "persona-reconcile-null-001"
        identity = {
            "runtime_id": "runtime-reconcile-001",
            "runtime_binding_id": "runtime-binding-reconcile-001",
            "capital_pool_id": "pool-reconcile-001",
            "persona_capital_binding_id": "persona-capital-binding-reconcile-001",
        }
        existing = [
            _existing_job_fixture(
                PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
                persona_id,
                "job-first-eval-reconcile",
            )
        ]
        spy = GatewayRuntimeSpy(existing_jobs=existing)
        registrar = PersonaCronRegistrar(gateway_runtime=spy)

        result = registrar.register_for_persona(
            persona_id,
            workflow_ids=[PERSONA_FIRST_EVALUATION_WORKFLOW_ID],
            **identity,
        )

        self.assertEqual(result.failed, [])
        self.assertEqual(result.registered, [])
        self.assertEqual(result.skipped, [_job_name(PERSONA_FIRST_EVALUATION_WORKFLOW_ID, persona_id)])
        self.assertEqual(spy.add_calls, [])
        self.assertEqual(len(spy.update_calls), 1)
        _, update_params = spy.update_calls[0]
        self.assertEqual((update_params or {}).get("id"), "job-first-eval-reconcile")
        updated_event = json.loads(
            (update_params or {}).get("patch", {}).get("payload", {}).get("text", "{}")
        )
        for key, value in identity.items():
            self.assertEqual(updated_event.get(key), value)
        self.assertTrue(
            registrar.has_first_evaluation_registration(
                persona_id,
                runtime=spy,
                **identity,
            )
        )

    def test_first_evaluation_registration_refuses_malformed_same_name_without_mutation(self):
        persona_id = "persona-ambiguous-name-001"
        malformed = _existing_job_fixture(
            PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
            persona_id,
            "job-ambiguous-name",
        )
        malformed["payload"]["text"] = "not-json"
        spy = GatewayRuntimeSpy(existing_jobs=[malformed])

        result = PersonaCronRegistrar(gateway_runtime=spy).register_for_persona(
            persona_id
        )

        self.assertEqual(result.registered, [])
        self.assertEqual(result.skipped, [])
        self.assertEqual(len(result.failed), 1)
        self.assertIn("ambiguous deterministic", result.failed[0]["error"])
        self.assertEqual(spy.add_calls, [])
        self.assertEqual(spy.update_calls, [])
        self.assertEqual(spy.remove_calls, [])

    def test_first_evaluation_registration_refuses_same_name_owned_by_other_persona(self):
        persona_id = "persona-name-collider-001"
        collider = _existing_job_fixture(
            PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
            "persona-real-owner-001",
            "job-name-collider",
        )
        collider["name"] = _job_name(
            PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
            persona_id,
        )
        spy = GatewayRuntimeSpy(existing_jobs=[collider])

        result = PersonaCronRegistrar(gateway_runtime=spy).register_for_persona(
            persona_id,
            workflow_ids=[PERSONA_FIRST_EVALUATION_WORKFLOW_ID],
        )

        self.assertEqual(result.registered, [])
        self.assertEqual(len(result.failed), 1)
        self.assertIn("does not prove", result.failed[0]["error"])
        self.assertEqual(spy.add_calls, [])
        self.assertEqual(spy.update_calls, [])
        self.assertEqual(spy.remove_calls, [])

    def test_first_evaluation_readback_rejects_exact_owner_plus_ambiguous_same_name(self):
        persona_id = "persona-readback-ambiguous-001"
        identity = {
            "runtime_id": "runtime-readback-ambiguous-001",
            "runtime_binding_id": "runtime-binding-readback-ambiguous-001",
            "capital_pool_id": "pool-readback-ambiguous-001",
            "persona_capital_binding_id": "pcb-readback-ambiguous-001",
        }
        exact = _existing_job_fixture(
            PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
            persona_id,
            "job-readback-exact",
            **identity,
        )
        ambiguous = _existing_job_fixture(
            PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
            persona_id,
            "job-readback-ambiguous",
        )
        ambiguous["payload"]["text"] = "not-json"
        spy = GatewayRuntimeSpy(existing_jobs=[exact, ambiguous])
        registrar = PersonaCronRegistrar(gateway_runtime=spy)

        self.assertIsNone(
            registrar.get_first_evaluation_registration(
                persona_id,
                runtime=spy,
                **identity,
            )
        )
        self.assertFalse(
            registrar.has_first_evaluation_registration(
                persona_id,
                runtime=spy,
                **identity,
            )
        )

    def test_first_evaluation_duplicate_owners_converge_stably(self):
        persona_id = "persona-reconcile-duplicates-001"
        identity = {
            "runtime_id": "runtime-duplicates-001",
            "runtime_binding_id": "runtime-binding-duplicates-001",
            "capital_pool_id": "pool-duplicates-001",
            "persona_capital_binding_id": "persona-capital-binding-duplicates-001",
        }
        existing = [
            _existing_job_fixture(
                PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
                persona_id,
                job_id,
            )
            for job_id in ("job-first-eval-z", "job-first-eval-a")
        ]
        spy = GatewayRuntimeSpy(existing_jobs=existing)
        registrar = PersonaCronRegistrar(gateway_runtime=spy)

        result = registrar.register_for_persona(
            persona_id,
            workflow_ids=[PERSONA_FIRST_EVALUATION_WORKFLOW_ID],
            **identity,
        )

        self.assertEqual(result.failed, [])
        self.assertEqual(len(spy.remove_calls), 1)
        self.assertEqual((spy.remove_calls[0][1] or {}).get("id"), "job-first-eval-z")
        self.assertEqual(len(spy.update_calls), 1)
        self.assertEqual((spy.update_calls[0][1] or {}).get("id"), "job-first-eval-a")
        self.assertEqual([job.get("id") for job in spy._existing_jobs], ["job-first-eval-a"])
        self.assertTrue(
            registrar.has_first_evaluation_registration(
                persona_id,
                runtime=spy,
                **identity,
            )
        )

    def test_first_evaluation_update_response_loss_uses_authoritative_list(self):
        persona_id = "persona-update-response-loss-001"
        identity = {
            "runtime_id": "runtime-update-response-loss-001",
            "runtime_binding_id": "runtime-binding-update-response-loss-001",
            "capital_pool_id": "pool-update-response-loss-001",
            "persona_capital_binding_id": "pcb-update-response-loss-001",
        }
        spy = GatewayRuntimeSpy(
            existing_jobs=[
                _existing_job_fixture(
                    PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
                    persona_id,
                    "job-update-response-loss",
                )
            ],
            response_loss_after_apply={"cron.update"},
        )
        registrar = PersonaCronRegistrar(gateway_runtime=spy)

        result = registrar.register_for_persona(
            persona_id,
            workflow_ids=[PERSONA_FIRST_EVALUATION_WORKFLOW_ID],
            **identity,
        )

        self.assertEqual(result.failed, [])
        self.assertEqual(len(spy.update_calls), 1)
        self.assertTrue(
            registrar.has_first_evaluation_registration(
                persona_id,
                runtime=spy,
                **identity,
            )
        )

    def test_first_evaluation_remove_response_loss_uses_authoritative_list(self):
        persona_id = "persona-remove-response-loss-001"
        identity = {
            "runtime_id": "runtime-remove-response-loss-001",
            "runtime_binding_id": "runtime-binding-remove-response-loss-001",
            "capital_pool_id": "pool-remove-response-loss-001",
            "persona_capital_binding_id": "pcb-remove-response-loss-001",
        }
        spy = GatewayRuntimeSpy(
            existing_jobs=[
                _existing_job_fixture(
                    PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
                    persona_id,
                    job_id,
                )
                for job_id in ("job-remove-response-loss-a", "job-remove-response-loss-b")
            ],
            response_loss_after_apply={"cron.remove"},
        )
        registrar = PersonaCronRegistrar(gateway_runtime=spy)

        result = registrar.register_for_persona(
            persona_id,
            workflow_ids=[PERSONA_FIRST_EVALUATION_WORKFLOW_ID],
            **identity,
        )

        self.assertEqual(result.failed, [])
        self.assertEqual(len(spy.remove_calls), 1)
        self.assertEqual(len(spy.update_calls), 1)
        self.assertTrue(
            registrar.has_first_evaluation_registration(
                persona_id,
                runtime=spy,
                **identity,
            )
        )

    def test_remove_first_evaluation_registration_removes_all_owner_rows(self):
        persona_id = "persona-terminal-remove-001"
        spy = GatewayRuntimeSpy(
            existing_jobs=[
                _existing_job_fixture(
                    PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
                    persona_id,
                    job_id,
                )
                for job_id in ("job-terminal-remove-a", "job-terminal-remove-b")
            ]
        )
        registrar = PersonaCronRegistrar(gateway_runtime=spy)

        result = registrar.remove_first_evaluation_registration(persona_id)

        self.assertEqual(
            result,
            {
                "registered": False,
                "removed_ids": [
                    "job-terminal-remove-a",
                    "job-terminal-remove-b",
                ],
            },
        )
        self.assertEqual(len(spy.remove_calls), 2)
        self.assertFalse(
            registrar.has_workflow_registration(
                persona_id,
                PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
                runtime=spy,
            )
        )

    def test_remove_first_evaluation_refuses_deterministic_malformed_row(self):
        persona_id = "persona-terminal-malformed-001"
        malformed = _existing_job_fixture(
            PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
            persona_id,
            "job-terminal-malformed",
        )
        malformed["payload"]["text"] = "not-json"
        spy = GatewayRuntimeSpy(existing_jobs=[malformed])

        with self.assertRaisesRegex(RuntimeError, "ambiguous deterministic"):
            PersonaCronRegistrar(
                gateway_runtime=spy
            ).remove_first_evaluation_registration(persona_id)

        self.assertEqual(spy.remove_calls, [])

    def test_remove_first_evaluation_response_loss_uses_authoritative_list(self):
        persona_id = "persona-terminal-response-loss-001"
        spy = GatewayRuntimeSpy(
            existing_jobs=[
                _existing_job_fixture(
                    PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
                    persona_id,
                    "job-terminal-response-loss",
                )
            ],
            response_loss_after_apply={"cron.remove"},
        )

        result = PersonaCronRegistrar(
            gateway_runtime=spy
        ).remove_first_evaluation_registration(persona_id)

        self.assertFalse(result["registered"])
        self.assertEqual(result["removed_ids"], ["job-terminal-response-loss"])

    def test_remove_first_evaluation_fails_when_row_remains(self):
        persona_id = "persona-terminal-remove-failed-001"
        spy = GatewayRuntimeSpy(
            existing_jobs=[
                _existing_job_fixture(
                    PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
                    persona_id,
                    "job-terminal-remove-failed",
                )
            ],
            mutation_fail_before_apply={"cron.remove"},
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "did not converge to registered=false",
        ):
            PersonaCronRegistrar(
                gateway_runtime=spy
            ).remove_first_evaluation_registration(persona_id)

    def test_remove_first_evaluation_fails_when_final_readback_is_unknown(self):
        persona_id = "persona-terminal-readback-unknown-001"

        class FinalListFailureRuntime(GatewayRuntimeSpy):
            def __init__(self):
                super().__init__(
                    existing_jobs=[
                        _existing_job_fixture(
                            PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
                            persona_id,
                            "job-terminal-readback-unknown",
                        )
                    ]
                )
                self._list_count = 0

            def gateway_call(self, method, params=None):
                if method == "cron.list":
                    self._list_count += 1
                    if self._list_count > 1:
                        raise RuntimeError("simulated final cron.list failure")
                return super().gateway_call(method, params)

        with self.assertRaisesRegex(
            RuntimeError,
            "cron.list failed after first-evaluation removal",
        ):
            PersonaCronRegistrar(
                gateway_runtime=FinalListFailureRuntime()
            ).remove_first_evaluation_registration(persona_id)

    def test_first_evaluation_add_response_loss_uses_authoritative_list(self):
        persona_id = "persona-add-response-loss-001"
        identity = {
            "runtime_id": "runtime-add-response-loss-001",
            "runtime_binding_id": "runtime-binding-add-response-loss-001",
            "capital_pool_id": "pool-add-response-loss-001",
            "persona_capital_binding_id": "pcb-add-response-loss-001",
        }
        spy = GatewayRuntimeSpy(response_loss_after_apply={"cron.add"})
        registrar = PersonaCronRegistrar(gateway_runtime=spy)

        result = registrar.register_for_persona(
            persona_id,
            workflow_ids=[PERSONA_FIRST_EVALUATION_WORKFLOW_ID],
            **identity,
        )

        self.assertEqual(result.failed, [])
        self.assertEqual(len(result.registered), 1)
        self.assertEqual(len(spy.add_calls), 1)
        owner_jobs = registrar._matching_first_evaluation_jobs(  # noqa: SLF001
            registrar._list_jobs(spy),  # noqa: SLF001
            persona_id,
        )
        self.assertEqual(len(owner_jobs), 1)
        self.assertTrue(
            registrar.has_first_evaluation_registration(
                persona_id,
                runtime=spy,
                **identity,
            )
        )

    def test_first_evaluation_update_failure_is_not_assumed_successful(self):
        persona_id = "persona-update-not-applied-001"
        identity = {
            "runtime_id": "runtime-update-not-applied-001",
            "runtime_binding_id": "runtime-binding-update-not-applied-001",
            "capital_pool_id": "pool-update-not-applied-001",
            "persona_capital_binding_id": "pcb-update-not-applied-001",
        }
        spy = GatewayRuntimeSpy(
            existing_jobs=[
                _existing_job_fixture(
                    PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
                    persona_id,
                    "job-update-not-applied",
                )
            ],
            mutation_fail_before_apply={"cron.update"},
        )
        registrar = PersonaCronRegistrar(gateway_runtime=spy)

        result = registrar.register_for_persona(
            persona_id,
            workflow_ids=[PERSONA_FIRST_EVALUATION_WORKFLOW_ID],
            **identity,
        )

        self.assertEqual(len(result.failed), 1)
        self.assertEqual(result.skipped, [])
        self.assertFalse(
            registrar.has_first_evaluation_registration(
                persona_id,
                runtime=spy,
                **identity,
            )
        )

    def test_first_evaluation_remove_failure_is_not_assumed_successful(self):
        persona_id = "persona-remove-not-applied-001"
        identity = {
            "runtime_id": "runtime-remove-not-applied-001",
            "runtime_binding_id": "runtime-binding-remove-not-applied-001",
            "capital_pool_id": "pool-remove-not-applied-001",
            "persona_capital_binding_id": "pcb-remove-not-applied-001",
        }
        spy = GatewayRuntimeSpy(
            existing_jobs=[
                _existing_job_fixture(
                    PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
                    persona_id,
                    job_id,
                )
                for job_id in ("job-remove-not-applied-a", "job-remove-not-applied-b")
            ],
            mutation_fail_before_apply={"cron.remove"},
        )
        registrar = PersonaCronRegistrar(gateway_runtime=spy)

        result = registrar.register_for_persona(
            persona_id,
            workflow_ids=[PERSONA_FIRST_EVALUATION_WORKFLOW_ID],
            **identity,
        )

        self.assertEqual(len(result.failed), 1)
        self.assertEqual(result.skipped, [])
        self.assertFalse(
            registrar.has_first_evaluation_registration(
                persona_id,
                runtime=spy,
                **identity,
            )
        )

    def test_first_evaluation_readback_rejects_other_workflow_for_same_persona(self):
        existing = [
            _existing_job_fixture("pantheon.ingest", "persona-readback-001", "job-ingest")
        ]
        spy = GatewayRuntimeSpy(existing_jobs=existing)
        registrar = PersonaCronRegistrar(gateway_runtime=spy)

        self.assertFalse(
            registrar.has_first_evaluation_registration(
                "persona-readback-001",
                runtime=spy,
            )
        )

    def test_first_evaluation_readback_requires_matching_persona(self):
        existing = [
            _existing_job_fixture(
                PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
                "persona-other",
                "job-first-eval-other",
            )
        ]
        spy = GatewayRuntimeSpy(existing_jobs=existing)
        registrar = PersonaCronRegistrar(gateway_runtime=spy)

        self.assertFalse(
            registrar.has_first_evaluation_registration(
                "persona-readback-001",
                runtime=spy,
            )
        )

    def test_first_evaluation_readback_accepts_exact_identity(self):
        identity = {
            "runtime_id": "runtime-readback-001",
            "runtime_binding_id": "runtime-binding-readback-001",
            "capital_pool_id": "pool-readback-001",
            "persona_capital_binding_id": "persona-capital-binding-readback-001",
        }
        existing = [
            _existing_job_fixture(
                PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
                "persona-readback-001",
                "job-first-eval",
                **identity,
            )
        ]
        spy = GatewayRuntimeSpy(existing_jobs=existing)
        registrar = PersonaCronRegistrar(gateway_runtime=spy)

        self.assertTrue(
            registrar.has_first_evaluation_registration(
                "persona-readback-001",
                runtime=spy,
                **identity,
            )
        )

    def test_first_evaluation_readback_rejects_every_wrong_contract_field(self):
        persona_id = "persona-strict-readback-001"
        identity = {
            "runtime_id": "runtime-strict-001",
            "runtime_binding_id": "runtime-binding-strict-001",
            "capital_pool_id": "pool-strict-001",
            "persona_capital_binding_id": "persona-capital-binding-strict-001",
        }

        def fixture() -> dict:
            return _existing_job_fixture(
                PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
                persona_id,
                "job-first-eval-strict",
                **identity,
            )

        def change_event(job: dict, key: str, value: object) -> None:
            event = json.loads(job["payload"]["text"])
            event[key] = value
            job["payload"]["text"] = json.dumps(event)

        outer_cases: list[tuple[str, Callable[[dict], None]]] = [
            ("name", lambda job: job.__setitem__("name", "pantheon-wrong-name")),
            ("enabled", lambda job: job.__setitem__("enabled", False)),
            (
                "delete after run",
                lambda job: job.__setitem__("deleteAfterRun", True),
            ),
            (
                "schedule kind",
                lambda job: job["schedule"].__setitem__("kind", "at"),
            ),
            (
                "schedule expression",
                lambda job: job["schedule"].__setitem__("expr", "0 0 * * *"),
            ),
            (
                "session target",
                lambda job: job.__setitem__("sessionTarget", "another-agent"),
            ),
            (
                "wake mode",
                lambda job: job.__setitem__("wakeMode", "now"),
            ),
            (
                "delivery",
                lambda job: job.__setitem__("delivery", {"mode": "announce"}),
            ),
            (
                "payload kind",
                lambda job: job["payload"].__setitem__("kind", "agentTurn"),
            ),
        ]
        for label, mutate in outer_cases:
            with self.subTest(field=label):
                job = fixture()
                mutate(job)
                spy = GatewayRuntimeSpy(existing_jobs=[job])
                self.assertFalse(
                    PersonaCronRegistrar(
                        gateway_runtime=spy
                    ).has_first_evaluation_registration(
                        persona_id,
                        runtime=spy,
                        **identity,
                    )
                )

        event_cases = {
            "event kind": ("kind", "pantheon.workflow.other"),
            "persona id": ("persona_id", "persona-other"),
            "request id": ("request_id", "arbitrary-request"),
            "runtime id": ("runtime_id", "runtime-other"),
            "runtime binding id": (
                "runtime_binding_id",
                "runtime-binding-other",
            ),
            "capital pool id": ("capital_pool_id", "pool-other"),
            "persona capital binding id": (
                "persona_capital_binding_id",
                "persona-capital-binding-other",
            ),
            "workflow id": ("workflow_id", "pantheon.ingest"),
            "policy id": ("policy_id", "policy-other"),
            "upstream entrypoint": (
                "upstream_entrypoint",
                "evaluation.persona.other",
            ),
        }
        for label, (key, value) in event_cases.items():
            with self.subTest(field=label):
                job = fixture()
                change_event(job, key, value)
                spy = GatewayRuntimeSpy(existing_jobs=[job])
                self.assertFalse(
                    PersonaCronRegistrar(
                        gateway_runtime=spy
                    ).has_first_evaluation_registration(
                        persona_id,
                        runtime=spy,
                        **identity,
                    )
                )

        with self.subTest(field="missing explicit identity field"):
            job = fixture()
            event = json.loads(job["payload"]["text"])
            del event["runtime_id"]
            job["payload"]["text"] = json.dumps(event)
            spy = GatewayRuntimeSpy(existing_jobs=[job])
            self.assertFalse(
                PersonaCronRegistrar(
                    gateway_runtime=spy
                ).has_first_evaluation_registration(
                    persona_id,
                    runtime=spy,
                    **identity,
                )
            )

    def test_first_evaluation_readback_rejects_duplicate_identity_records(self):
        identity = {
            "runtime_id": "runtime-duplicate-001",
            "runtime_binding_id": "runtime-binding-duplicate-001",
            "capital_pool_id": "pool-duplicate-001",
            "persona_capital_binding_id": "persona-capital-binding-duplicate-001",
        }
        existing = [
            _existing_job_fixture(
                PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
                "persona-duplicate-001",
                job_id,
                **identity,
            )
            for job_id in ("job-first-eval-a", "job-first-eval-b")
        ]
        spy = GatewayRuntimeSpy(existing_jobs=existing)

        self.assertFalse(
            PersonaCronRegistrar(
                gateway_runtime=spy
            ).has_first_evaluation_registration(
                "persona-duplicate-001",
                runtime=spy,
                **identity,
            )
        )

    def test_dry_run_is_not_authoritative_registration_readback(self):
        registrar = PersonaCronRegistrar(dry_run=True)

        self.assertFalse(
            registrar.has_first_evaluation_registration("persona-dry-run-001")
        )

    def test_idempotent_skip_when_job_already_present(self):
        # Pre-seed two catalog workflow jobs as already-registered.
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
        # General workflows retain legacy pair-skip behavior; only the owned
        # first-evaluation workflow participates in identity reconciliation.
        self.assertEqual(spy.update_calls, [])
        self.assertEqual(spy.remove_calls, [])

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

    def test_cron_list_pagination_cycle_fails_closed(self):
        class CyclingRuntime(GatewayRuntimeSpy):
            def gateway_call(self, method, params=None):
                if method == "cron.list":
                    self.calls.append((method, params))
                    return {"jobs": [], "hasMore": True, "nextOffset": 0}
                return super().gateway_call(method, params)

        spy = CyclingRuntime()
        result = PersonaCronRegistrar(gateway_runtime=spy).register_for_persona(
            "persona-pagination-cycle"
        )

        self.assertEqual(spy.add_calls, [])
        self.assertEqual(result.registered, [])
        self.assertTrue(result.failed)
        self.assertIn("pagination cycle", result.failed[0]["error"])

    def test_session_target_defaults_to_main_system_event_session(self):
        spy = GatewayRuntimeSpy()
        PersonaCronRegistrar(gateway_runtime=spy).register_for_persona("persona-crypto")
        for _, params in spy.add_calls:
            self.assertEqual((params or {}).get("sessionTarget"), "main")

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
        # Seed one valid job, one payload-owned orphan, and one Pantheon job
        # whose payload cannot prove an owner.
        existing = [
            _existing_job_fixture("pantheon.ingest", "persona-a", "job-valid-1"),
            _existing_job_fixture("pantheon.ingest", "persona-orphan", "job-orphan-1"),
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
        # Should register every missing job for persona-a (valid ingest is skipped)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0].registered), len(WORKFLOW_CATALOG) - 1)
        self.assertEqual(results[0].skipped, ["pantheon-pantheon-ingest-persona-a"])

        # Only the payload-owned orphan is safe to remove. The unowned row is
        # retained and surfaced for governed cleanup.
        self.assertEqual(len(removed), 1)
        removed_ids = {r["job_id"] for r in removed}
        self.assertEqual(removed_ids, {"job-orphan-1"})
        self.assertEqual(len(remove_failed), 1)
        self.assertEqual(remove_failed[0]["job_id"], "job-orphan-2")
        self.assertIn("governed cleanup", remove_failed[0]["error"])
        self.assertIn(
            "job-orphan-2",
            {job.get("id") for job in spy._existing_jobs},
        )

    def test_reconcile_personas_reports_malformed_pantheon_rows_without_mutation(self):
        malformed_payload = {
            "id": "job-malformed-payload",
            "name": "pantheon-malformed-payload",
            "payload": {"text": "not-json"},
        }
        missing_id = {
            "name": "pantheon-missing-id",
            "payload": {
                "text": json.dumps(
                    {
                        "persona_id": "persona-orphan",
                        "workflow_id": "pantheon.ingest",
                    }
                )
            },
        }
        unrelated = {
            "id": "external-malformed",
            "name": "external-malformed",
            "payload": {},
        }
        spy = GatewayRuntimeSpy(
            existing_jobs=[malformed_payload, missing_id, unrelated]
        )

        results, removed, remove_failed = PersonaCronRegistrar(
            gateway_runtime=spy
        ).reconcile_personas([])

        self.assertEqual(results, [])
        self.assertEqual(removed, [])
        self.assertEqual(spy.remove_calls, [])
        self.assertEqual(
            {failure["job_name"] for failure in remove_failed},
            {"pantheon-malformed-payload", "pantheon-missing-id"},
        )
        self.assertTrue(
            all("governed cleanup" in failure["error"] for failure in remove_failed)
        )

    def test_adapter_runtime_selected_when_adapter_url_set(self):
        with patch.dict(
            "os.environ",
            {"PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL": "http://openclaw-gateway-adapter:8104"},
        ):
            runtime = PersonaCronRegistrar()._get_runtime()
        self.assertIsInstance(runtime, AdapterCronRuntime)

    def test_adapter_runtime_attaches_service_token(self):
        captured: dict[str, object] = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

            def read(self) -> bytes:
                return b'{"status":"ok","data":{"jobs":[]}}'

        def fake_urlopen(request, *, timeout):
            captured["headers"] = {
                key.lower(): value for key, value in request.header_items()
            }
            captured["timeout"] = timeout
            return Response()

        runtime = AdapterCronRuntime(
            "http://openclaw-gateway-adapter:8104",
            service_token="cron-service-secret",
        )
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = runtime.gateway_call("cron.list", {"limit": 1})

        self.assertEqual(result, {"jobs": []})
        self.assertEqual(
            captured["headers"]["x-pantheon-service-token"],
            "cron-service-secret",
        )

    def test_adapter_runtime_fails_closed_without_service_token(self):
        runtime = AdapterCronRuntime(
            "http://openclaw-gateway-adapter:8104",
            service_token="",
        )
        with (
            patch("urllib.request.urlopen") as urlopen,
            self.assertRaisesRegex(RuntimeError, "service authentication is required"),
        ):
            runtime.gateway_call("cron.list", {"limit": 1})
        urlopen.assert_not_called()

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
