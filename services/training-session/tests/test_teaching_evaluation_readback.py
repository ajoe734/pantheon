"""Unit & Integration tests for Teaching terminal evaluation readback and conditional Consultation handoff."""

from __future__ import annotations

import copy
import importlib.util
import math
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from typing import Any, Sequence
from unittest import mock

import uvicorn
from fastapi.testclient import TestClient

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
L12_DIR = REPO_ROOT / "tests" / "integration" / "l12"

for path_str in (str(SERVICE_DIR), str(REPO_ROOT), str(L12_DIR)):
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from models import TeachingEvaluationResult
from services.consultation import main as consultation_main
from services.consultation.store import ConsultationStore
from strict_test_support import (
    FIXED_TRUSTED_NOW,
    make_fake_persona_target_commit,
    make_fake_real_vectorbt_workflow,
    make_fake_target_precondition_reader,
    materialize_strict_authority,
    seed_changed_supported_controls,
)
from tests.integration.l12.test_current_research_loops_deployed_e2e import (
    BoundaryFailure,
    DeployedResearchHarness,
)


def _load_service_module():
    fixture = materialize_strict_authority(tempfile.mkdtemp())
    os.environ.update(fixture.environment())
    with mock.patch.dict("os.environ", fixture.environment(), clear=False):
        sys.path.insert(0, str(SERVICE_DIR))
        spec = importlib.util.spec_from_file_location("training_session_test_readback_main", SERVICE_DIR / "main.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["training_session_test_readback_main"] = module
        spec.loader.exec_module(module)
    module.store = module.TrainingSessionStore(fixture.data_dir)
    module._trusted_now = lambda: FIXED_TRUSTED_NOW
    module.run_vectorbt_workflow = make_fake_real_vectorbt_workflow()
    module._read_target_precondition = make_fake_target_precondition_reader()
    module._commit_authoritative_persona_target = make_fake_persona_target_commit()
    module._strict_authority_fixture = fixture
    return module


def _make_test_harness(
    module: Any,
    client: TestClient,
    session_id: str,
    persona_id: str = "persona-test",
    *,
    auto_run: bool = True,
    interceptor: Any = None,
) -> tuple[DeployedResearchHarness, dict[str, Any]]:
    os.environ["PANTHEON_L12_TRAINING_TOKEN"] = "test-token"
    os.environ["PANTHEON_L12_REPORT_PATH"] = str(
        Path(tempfile.gettempdir()) / f"harness-report-{uuid.uuid4().hex}.json"
    )
    os.environ["PANTHEON_L12_TENANT_ID"] = "tenant-test"

    harness = DeployedResearchHarness()
    harness.timeout_seconds = 0.5
    harness.poll_seconds = 0.01
    harness._persona_headers = lambda: {
        "Authorization": "Bearer test-operator-token",
        "X-Tenant-Id": harness.tenant_id,
    }
    harness._operator_training_headers = lambda: {
        "Authorization": "Bearer test-operator-token",
        "X-Tenant-Id": harness.tenant_id,
        "X-Pantheon-Service": "control-plane-bff",
    }
    harness._service_identity = lambda service: {
        "compose_service": service,
        "container_id": "test-container-id",
        "image_id": "sha256:test",
        "state": "running",
        "health": "healthy",
        "restart_count": 0,
    }

    orig_http_json = harness._http_json

    def mock_http_json(
        base_url: str,
        path: str,
        *,
        method: str = "GET",
        payload: Any = None,
        headers: Any = None,
        expected: Sequence[int] = (200,),
    ) -> Any:
        if base_url == harness.persona_url:
            persona = path.split("/")[-1]
            return {"persona_id": persona, "tenant_id": harness.tenant_id, "status": "active"}
        if base_url == harness.training_url:
            if auto_run and method == "GET" and "/api/training/preview-jobs/" in path:
                job_id = path.split("/")[-1]
                job_record = module.store.get_preview_job(job_id)
                if job_record and job_record.get("status") == "queued":
                    run_res = client.post(f"/api/training/preview-jobs/{job_id}/run", headers=dict(headers or {}))
                    if run_res.status_code != 200:
                        raise RuntimeError(f"preview-jobs run failed: {run_res.status_code}: {run_res.text}")
            r = client.request(method, path, json=payload, headers=dict(headers or {}))
            if r.status_code not in expected:
                raise RuntimeError(
                    f"{method} {path} returned HTTP {r.status_code}; expected {list(expected)}; body={r.text}"
                )
            data = r.json() if r.text else {}
            if interceptor is not None:
                data = interceptor(path, method, data)
            return data
        return orig_http_json(base_url, path, method=method, payload=payload, headers=headers, expected=expected)

    harness._http_json = mock_http_json
    harness.chain["session"] = {"session_id": session_id, "persona_id": persona_id}
    case = {"case_index": 4, "loop_id": "persona_teaching", "status": "running"}
    harness._current_case = case
    return harness, case


class TestTeachingEvaluationReadback(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._consult_temp_dir = tempfile.TemporaryDirectory()
        cls._consult_store = ConsultationStore(cls._consult_temp_dir.name)
        cls._orig_consult_store = consultation_main.store
        consultation_main.store = cls._consult_store

        cls._orig_auth_required = os.environ.get("CONSULTATION_AUTH_REQUIRED")
        cls._orig_service_token = os.environ.get("CONSULTATION_SERVICE_TOKEN")
        cls._orig_consult_url = os.environ.get("PANTHEON_CONSULTATION_API_URL")

        os.environ["CONSULTATION_AUTH_REQUIRED"] = "true"
        os.environ["CONSULTATION_SERVICE_TOKEN"] = "test-consultation-service-token"

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            cls._consult_port = int(s.getsockname()[1])

        cls._consult_server = uvicorn.Server(
            uvicorn.Config(
                consultation_main.app,
                host="127.0.0.1",
                port=cls._consult_port,
                log_level="error",
            )
        )
        cls._consult_thread = threading.Thread(target=cls._consult_server.run, daemon=True)
        cls._consult_thread.start()
        deadline = time.monotonic() + 10
        while not cls._consult_server.started:
            if time.monotonic() >= deadline:
                raise RuntimeError("consultation test server did not start")
            time.sleep(0.01)

        os.environ["PANTHEON_CONSULTATION_API_URL"] = f"http://127.0.0.1:{cls._consult_port}"

    @classmethod
    def tearDownClass(cls):
        cls._consult_server.should_exit = True
        cls._consult_thread.join(timeout=5)
        consultation_main.store = cls._orig_consult_store

        if cls._orig_auth_required is not None:
            os.environ["CONSULTATION_AUTH_REQUIRED"] = cls._orig_auth_required
        else:
            os.environ.pop("CONSULTATION_AUTH_REQUIRED", None)

        if cls._orig_service_token is not None:
            os.environ["CONSULTATION_SERVICE_TOKEN"] = cls._orig_service_token
        else:
            os.environ.pop("CONSULTATION_SERVICE_TOKEN", None)

        if cls._orig_consult_url is not None:
            os.environ["PANTHEON_CONSULTATION_API_URL"] = cls._orig_consult_url
        else:
            os.environ.pop("PANTHEON_CONSULTATION_API_URL", None)

        cls._consult_temp_dir.cleanup()
        super().tearDownClass()

    def test_teaching_evaluation_result_model(self):
        result = TeachingEvaluationResult(
            eval_id="teval-100",
            session_id="trn-100",
            tenant_id="tenant-test",
            persona_id="persona-test",
            validation_status="passed",
            validation_errors=[],
            metrics={"total_return": 0.15, "sharpe_ratio": 1.2, "max_drawdown": 0.05},
            eval_identity="teval-identity-abcd1234efgh5678",
            evaluated_at="2026-08-13T12:00:00Z",
            consultation_required=False,
        )
        as_dict = result.to_dict()
        self.assertEqual(as_dict["eval_id"], "teval-100")
        self.assertEqual(as_dict["eval_identity"], "teval-identity-abcd1234efgh5678")
        self.assertFalse(as_dict["consultation_required"])
        self.assertNotIn("consult_request_id", as_dict)

        restored = TeachingEvaluationResult.from_dict(as_dict)
        self.assertEqual(restored.eval_id, "teval-100")
        self.assertEqual(restored.eval_identity, "teval-identity-abcd1234efgh5678")

    def test_ordinary_teaching_terminal_readback_without_consultation(self):
        module = _load_service_module()
        client = TestClient(module.app)
        headers = {"X-Pantheon-Tenant-ID": "tenant-test", "X-Pantheon-Actor-ID": "operator"}

        # 1. Create active session
        resp = client.post(
            "/api/training/sessions",
            json={
                "persona_id": "persona-test",
                "objective": "Test ordinary teaching terminal",
                "opened_by": "operator",
                "mode": "evaluation",
            },
            headers=headers,
        )
        self.assertEqual(resp.status_code, 201)
        session_id = resp.json()["session_id"]

        # 2. Drive the actual DeployedResearchHarness._case_persona_teaching method
        harness, case = _make_test_harness(module, client, session_id, "persona-test")
        harness._case_persona_teaching(case)

        # 3. Assert caller requested terminalize_session=False
        job_id = case["terminal_output"]["job_id"]
        job_record = module.store.get_preview_job(job_id)
        self.assertIsNotNone(job_record)
        self.assertFalse(job_record.get("terminalize_session"))

        # 4. Assert real completed result/proof/job/evaluation identity
        self.assertEqual(case["terminal_output"]["type"], "TeachingEvaluationResult")
        self.assertEqual(case["terminal_output"]["session_id"], session_id)
        self.assertEqual(case["terminal_output"]["job_id"], job_id)
        self.assertEqual(case["terminal_output"]["validation_status"], "passed")
        eval_identity = case["terminal_output"]["id"]
        self.assertTrue(eval_identity.startswith("teval-identity-"))
        eval_id = case["terminal_output"]["eval_id"]
        self.assertTrue(eval_id.startswith("teval-"))

        # 5. Assert authority readback
        self.assertEqual(case["authority_readback"]["session_id"], session_id)
        self.assertEqual(case["authority_readback"]["status"], "active")
        self.assertEqual(case["authority_readback"]["eval_identity"], eval_identity)
        self.assertEqual(case["authority_readback"]["validation_status"], "passed")
        self.assertEqual(case["authority_readback"]["governance_gate_state"], "passed")
        self.assertFalse(case["authority_readback"]["consultation_required"])

        # 6. Assert original Learning next-consumer binding
        self.assertEqual(
            case["next_consumer"],
            {
                "consumer": "operator-learning-verifier",
                "receipt_type": "TeachingTerminalReadback",
                "receipt_id": eval_identity,
                "session_id": session_id,
            },
        )

        # 7. Verify session remains active with ended_at None
        sess_resp = client.get(f"/api/training/sessions/{session_id}", headers=headers)
        self.assertEqual(sess_resp.status_code, 200)
        sess_data = sess_resp.json()
        self.assertEqual(sess_data["status"], "active")
        self.assertIsNone(sess_data["ended_at"])
        self.assertEqual(sess_data["eval_identity"], eval_identity)

        # 8. Verify readback endpoint returns consistent evaluation identity
        readback_resp = client.get(f"/api/training/sessions/{session_id}/readback", headers=headers)
        self.assertEqual(readback_resp.status_code, 200)
        readback_data = readback_resp.json()
        self.assertEqual(readback_data["session_id"], session_id)
        self.assertEqual(readback_data["eval_identity"], eval_identity)
        self.assertFalse(readback_data["consultation_required"])

    def test_review_required_teaching_creates_consult_request_receipt(self):
        module = _load_service_module()
        client = TestClient(module.app)
        headers = {"X-Pantheon-Tenant-ID": "tenant-test", "X-Pantheon-Actor-ID": "operator"}

        # 1. Create session with explicit review_required / consultation_required mode
        resp = client.post(
            "/api/training/sessions",
            json={
                "persona_id": "persona-test",
                "objective": "Test review-required consultation handoff",
                "opened_by": "operator",
                "mode": "coaching",
            },
            headers=headers,
        )
        self.assertEqual(resp.status_code, 201)
        session_id = resp.json()["session_id"]

        # 2. Seed controls and run preview evaluation in consultation_required mode
        seed_changed_supported_controls(module, session_id)
        queued = client.post(
            f"/api/training/sessions/{session_id}/preview-jobs",
            json={"mode": "consultation_required", "requested_by": "operator", "terminalize_session": False},
            headers={**headers, "Idempotency-Key": "test-key-review-1"},
        )
        self.assertEqual(queued.status_code, 201)
        job_id = queued.json()["job_id"]

        ran = client.post(f"/api/training/preview-jobs/{job_id}/run", headers=headers)
        self.assertEqual(ran.status_code, 200)
        job_res = ran.json()
        self.assertEqual(job_res["status"], "completed")
        preview = job_res["preview"]

        # Check consultation handoff
        eval_result = preview.get("evaluation_result")
        self.assertIsNotNone(eval_result)
        self.assertTrue(eval_result["consultation_required"])
        self.assertIsNotNone(eval_result.get("consult_request_id"))
        self.assertTrue(eval_result["consult_request_id"].startswith("creq-teach-"))

        receipt = eval_result.get("consultation_receipt")
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["consult_request_id"], eval_result["consult_request_id"])
        self.assertEqual(receipt["session_id"], session_id)
        self.assertEqual(receipt["status"], "submitted")

        # Verify readback endpoint returns receipt & request id
        readback_resp = client.get(f"/api/training/sessions/{session_id}/readback", headers=headers)
        self.assertEqual(readback_resp.status_code, 200)
        readback_data = readback_resp.json()
        self.assertTrue(readback_data["consultation_required"])
        self.assertEqual(readback_data["consult_request_id"], eval_result["consult_request_id"])
        self.assertEqual(readback_data["consultation_receipt"], receipt)

        # Verify event log contains event with consult_request_id & consultation_receipt
        events_resp = client.get(f"/api/training/sessions/{session_id}/events", headers=headers)
        self.assertEqual(events_resp.status_code, 200)
        events = events_resp.json()
        preview_events = [ev for ev in events if ev.get("event_type") == "preview_result"]
        self.assertTrue(len(preview_events) > 0)
        eval_ref = preview_events[0].get("eval_ref") or {}
        self.assertTrue(eval_ref.get("consultation_required"))
        self.assertEqual(eval_ref.get("consult_request_id"), eval_result["consult_request_id"])

    def test_completed_computation_ineligible_candidate_preserves_active_session(self):
        module = _load_service_module()
        module.run_vectorbt_workflow = make_fake_real_vectorbt_workflow(
            total_return=0.02,
            sharpe_ratio=-0.2,
            max_drawdown=0.08,
            run_id="vbt-real-test-ineligible-001",
        )
        client = TestClient(module.app)
        headers = {"X-Pantheon-Tenant-ID": "tenant-test", "X-Pantheon-Actor-ID": "operator"}

        resp = client.post(
            "/api/training/sessions",
            json={
                "persona_id": "persona-test",
                "objective": "Test ineligible candidate evaluation",
                "opened_by": "operator",
                "mode": "evaluation",
            },
            headers=headers,
        )
        self.assertEqual(resp.status_code, 201)
        session_id = resp.json()["session_id"]

        harness, case = _make_test_harness(module, client, session_id, "persona-test")
        harness._case_persona_teaching(case)

        # Assert caller requested terminalize_session=False
        job_id = case["terminal_output"]["job_id"]
        job_record = module.store.get_preview_job(job_id)
        self.assertIsNotNone(job_record)
        self.assertFalse(job_record.get("terminalize_session"))

        # Assert real completed result/proof/job/evaluation identity
        self.assertEqual(case["terminal_output"]["type"], "TeachingEvaluationResult")
        self.assertEqual(case["terminal_output"]["session_id"], session_id)
        self.assertEqual(case["terminal_output"]["job_id"], job_id)
        self.assertEqual(case["terminal_output"]["validation_status"], "failed")
        eval_identity = case["terminal_output"]["id"]
        self.assertTrue(eval_identity.startswith("teval-identity-"))

        # Assert authority readback preserves failed governance and active session
        self.assertEqual(case["authority_readback"]["session_id"], session_id)
        self.assertEqual(case["authority_readback"]["status"], "active")
        self.assertEqual(case["authority_readback"]["eval_identity"], eval_identity)
        self.assertEqual(case["authority_readback"]["validation_status"], "failed")
        self.assertEqual(case["authority_readback"]["governance_gate_state"], "failed")
        self.assertTrue(case["authority_readback"]["consultation_required"])

        # Assert original Learning next-consumer binding
        self.assertEqual(
            case["next_consumer"],
            {
                "consumer": "operator-learning-verifier",
                "receipt_type": "TeachingTerminalReadback",
                "receipt_id": eval_identity,
                "session_id": session_id,
            },
        )

        # Verify owner session remains active with ended_at null
        sess = module.store.get_session(session_id)
        self.assertEqual(sess.get("status"), "active")
        self.assertIsNone(sess.get("ended_at"))

        # Verify /complete fail-closed check (evaluation governance gate is not passed)
        complete_resp = client.post(f"/api/training/sessions/{session_id}/complete", headers=headers)
        self.assertEqual(complete_resp.status_code, 409)
        self.assertIn("evaluation governance gate is not passed", complete_resp.json()["detail"])

        sess_after = module.store.get_session(session_id)
        self.assertEqual(sess_after.get("status"), "active")
        self.assertIsNone(sess_after.get("ended_at"))

    def test_complete_session_invalid_proof_rejection(self):
        module = _load_service_module()
        client = TestClient(module.app)
        headers = {"X-Pantheon-Tenant-ID": "tenant-test", "X-Pantheon-Actor-ID": "operator"}

        resp = client.post(
            "/api/training/sessions",
            json={
                "persona_id": "persona-test",
                "objective": "Test complete without proof",
                "opened_by": "operator",
                "mode": "evaluation",
            },
            headers=headers,
        )
        self.assertEqual(resp.status_code, 201)
        session_id = resp.json()["session_id"]

        complete_resp = client.post(f"/api/training/sessions/{session_id}/complete", headers=headers)
        self.assertEqual(complete_resp.status_code, 409)
        self.assertIn("passing worker evaluation proof required before completion", complete_resp.json()["detail"])

        sess_resp = client.get(f"/api/training/sessions/{session_id}", headers=headers)
        self.assertEqual(sess_resp.status_code, 200)
        self.assertEqual(sess_resp.json()["status"], "active")
        self.assertIsNone(sess_resp.json()["ended_at"])

    def test_harness_boundary_rejection_for_pending_failed_or_mismatched_identities(self):
        module = _load_service_module()
        client = TestClient(module.app)
        headers = {"X-Pantheon-Tenant-ID": "tenant-test", "X-Pantheon-Actor-ID": "operator"}

        def _create_session(objective: str) -> str:
            resp = client.post(
                "/api/training/sessions",
                json={
                    "persona_id": "persona-test",
                    "objective": objective,
                    "opened_by": "operator",
                    "mode": "evaluation",
                },
                headers=headers,
            )
            self.assertEqual(resp.status_code, 201)
            return resp.json()["session_id"]

        # A: Pending job must not pass (times out polling durable terminal output)
        sess_pending = _create_session("Test pending rejection")
        harness, case = _make_test_harness(module, client, sess_pending, "persona-test", auto_run=False)
        with self.assertRaises((RuntimeError, BoundaryFailure)) as ctx:
            harness._case_persona_teaching(case)
        self.assertIn("timed out waiting for", str(ctx.exception))

        # B: Failed compute must not pass
        def fail_job(path: str, method: str, data: Any) -> Any:
            if "/api/training/preview-jobs/" in path and method == "GET":
                data["status"] = "failed"
                data["error_code"] = "evaluation_computation_error"
                data["failure_reason"] = "backend failed"
            return data

        sess_fail = _create_session("Test failed compute rejection")
        harness, case = _make_test_harness(module, client, sess_fail, "persona-test", interceptor=fail_job)
        with self.assertRaises((RuntimeError, BoundaryFailure)) as ctx:
            harness._case_persona_teaching(case)
        self.assertIn("evaluation_computation_error", str(ctx.exception))

        # C: Mismatched job_id must not pass
        def mismatch_job(path: str, method: str, data: Any) -> Any:
            if "/api/training/preview-jobs/" in path and method == "GET":
                data["job_id"] = "pvjob-different"
            return data

        sess_mismatch_job = _create_session("Test mismatched job rejection")
        harness, case = _make_test_harness(
            module, client, sess_mismatch_job, "persona-test", interceptor=mismatch_job
        )
        with self.assertRaises((RuntimeError, BoundaryFailure)) as ctx:
            harness._case_persona_teaching(case)
        self.assertIn("mismatched job/session identity", str(ctx.exception))

        # C1: Missing queued eval_id must not pass
        def missing_queued_eval(path: str, method: str, data: Any) -> Any:
            if "/preview-jobs" in path and method == "POST":
                data["eval_id"] = ""
            return data

        sess_missing_q_eval = _create_session("Test missing queued eval rejection")
        harness, case = _make_test_harness(
            module, client, sess_missing_q_eval, "persona-test", interceptor=missing_queued_eval
        )
        with self.assertRaises((RuntimeError, BoundaryFailure)) as ctx:
            harness._case_persona_teaching(case)
        self.assertIn("Teaching preview queue did not return eval_id", str(ctx.exception))

        # C2: Missing job eval_id must not pass
        def missing_job_eval(path: str, method: str, data: Any) -> Any:
            if "/api/training/preview-jobs/" in path and method == "GET":
                data["eval_id"] = ""
            return data

        sess_missing_j_eval = _create_session("Test missing job eval rejection")
        harness, case = _make_test_harness(
            module, client, sess_missing_j_eval, "persona-test", interceptor=missing_job_eval
        )
        with self.assertRaises((RuntimeError, BoundaryFailure)) as ctx:
            harness._case_persona_teaching(case)
        self.assertIn("Teaching preview job did not return eval_id", str(ctx.exception))

        # C3: Wrong job eval_id must not pass
        def wrong_job_eval(path: str, method: str, data: Any) -> Any:
            if "/api/training/preview-jobs/" in path and method == "GET":
                data["eval_id"] = "teval-different-job-id"
            return data

        sess_wrong_j_eval = _create_session("Test wrong job eval rejection")
        harness, case = _make_test_harness(
            module, client, sess_wrong_j_eval, "persona-test", interceptor=wrong_job_eval
        )
        with self.assertRaises((RuntimeError, BoundaryFailure)) as ctx:
            harness._case_persona_teaching(case)
        self.assertIn("Teaching preview job eval_id mismatch", str(ctx.exception))

        # C4: Wrong queued eval_id against job must not pass
        def wrong_queued_eval(path: str, method: str, data: Any) -> Any:
            if "/preview-jobs" in path and method == "POST":
                data["eval_id"] = "teval-different-queued-id"
            return data

        sess_wrong_q_eval = _create_session("Test wrong queued eval rejection")
        harness, case = _make_test_harness(
            module, client, sess_wrong_q_eval, "persona-test", interceptor=wrong_queued_eval
        )
        with self.assertRaises((RuntimeError, BoundaryFailure)) as ctx:
            harness._case_persona_teaching(case)
        self.assertIn("Teaching preview job eval_id mismatch", str(ctx.exception))

        # D1: Mismatched session_id in terminal readback must not pass (poll times out)
        def mismatch_session(path: str, method: str, data: Any) -> Any:
            if "/terminal-readback" in path and method == "GET":
                data["session_id"] = "trn-different"
            return data

        sess_mismatch_s = _create_session("Test mismatched session rejection")
        harness, case = _make_test_harness(
            module, client, sess_mismatch_s, "persona-test", interceptor=mismatch_session
        )
        with self.assertRaises((RuntimeError, BoundaryFailure)) as ctx:
            harness._case_persona_teaching(case)
        self.assertIn("timed out waiting for terminal TeachingSession", str(ctx.exception))

        # D2: Mismatched persona_id in terminal readback must not pass
        def mismatch_persona(path: str, method: str, data: Any) -> Any:
            if "/terminal-readback" in path and method == "GET":
                data["persona_id"] = "persona-different"
            return data

        sess_mismatch_p = _create_session("Test mismatched persona rejection")
        harness, case = _make_test_harness(
            module, client, sess_mismatch_p, "persona-test", interceptor=mismatch_persona
        )
        with self.assertRaises((RuntimeError, BoundaryFailure)) as ctx:
            harness._case_persona_teaching(case)
        self.assertIn("persona_id mismatch", str(ctx.exception))

        # E: Mismatched eval_identity must not pass
        def drift_identity(path: str, method: str, data: Any) -> Any:
            if "/terminal-readback" in path and method == "GET":
                data["evaluation_result"]["eval_identity"] = "teval-identity-other"
            return data

        sess_drift = _create_session("Test drifted identity rejection")
        harness, case = _make_test_harness(
            module, client, sess_drift, "persona-test", interceptor=drift_identity
        )
        with self.assertRaises((RuntimeError, BoundaryFailure)) as ctx:
            harness._case_persona_teaching(case)
        self.assertIn("identity drifted", str(ctx.exception))

        # E1: Mismatched durable evaluation eval_id must not pass
        def mismatch_eval_eval(path: str, method: str, data: Any) -> Any:
            if "/terminal-readback" in path and method == "GET":
                if "evaluation_result" in data and isinstance(data["evaluation_result"], dict):
                    data["evaluation_result"]["eval_id"] = "teval-different-readback"
            return data

        sess_mismatch_eval = _create_session("Test mismatched evaluation eval rejection")
        harness, case = _make_test_harness(
            module, client, sess_mismatch_eval, "persona-test", interceptor=mismatch_eval_eval
        )
        with self.assertRaises((RuntimeError, BoundaryFailure)) as ctx:
            harness._case_persona_teaching(case)
        self.assertIn("Teaching evaluation eval_id mismatch", str(ctx.exception))

        # E2: Mismatched proof eval_id must not pass
        def mismatch_proof_eval(path: str, method: str, data: Any) -> Any:
            if "/terminal-readback" in path and method == "GET":
                if "preview" in data and isinstance(data["preview"], dict):
                    proof = data["preview"].get("evaluation_proof")
                    if isinstance(proof, dict):
                        proof["eval_id"] = "teval-different-proof"
            return data

        sess_mismatch_proof = _create_session("Test mismatched proof eval rejection")
        harness, case = _make_test_harness(
            module, client, sess_mismatch_proof, "persona-test", interceptor=mismatch_proof_eval
        )
        with self.assertRaises((RuntimeError, BoundaryFailure)) as ctx:
            harness._case_persona_teaching(case)
        self.assertIn("Teaching evaluation proof eval_id mismatch", str(ctx.exception))

        # F: Non-finite metric must not pass
        def bad_metric(path: str, method: str, data: Any) -> Any:
            if "/terminal-readback" in path and method == "GET":
                data["evaluation_result"]["metrics"]["sharpe_ratio"] = float("nan")
            return data

        sess_metric = _create_session("Test non-finite metric rejection")
        harness, case = _make_test_harness(
            module, client, sess_metric, "persona-test", interceptor=bad_metric
        )
        with self.assertRaises((RuntimeError, BoundaryFailure)) as ctx:
            harness._case_persona_teaching(case)
        self.assertIn("non-finite", str(ctx.exception))

        # G: Missing authority dataset must not pass
        def missing_dataset(path: str, method: str, data: Any) -> Any:
            if "/terminal-readback" in path and method == "GET":
                data["preview"]["evaluation_proof"]["authority"]["dataset"]["dataset_version_id"] = ""
            return data

        sess_auth = _create_session("Test missing dataset rejection")
        harness, case = _make_test_harness(
            module, client, sess_auth, "persona-test", interceptor=missing_dataset
        )
        with self.assertRaises((RuntimeError, BoundaryFailure)) as ctx:
            harness._case_persona_teaching(case)
        self.assertIn("missing authority dataset_version_id", str(ctx.exception))

        # H: Failed validation incorrectly claiming completed session must not pass
        def false_completed_session(path: str, method: str, data: Any) -> Any:
            if "/terminal-readback" in path and method == "GET":
                data["evaluation_result"]["validation_status"] = "failed"
                data["preview"]["evaluation_proof"]["governance_gate_state"] = "failed"
                data["preview"]["evaluation_proof"]["status"] = "failed"
                data["preview"]["evaluation_proof"]["commit_eligible"] = False
                data["evaluation_result"]["validation_errors"] = ["sharpe_ratio_below_threshold"]
                data["evaluation_result"]["consultation_required"] = True
                data["evaluation_result"]["consult_request_id"] = "creq-teach-12345"
                data["evaluation_result"]["consultation_receipt"] = {"status": "submitted"}
                data["status"] = "completed"
            return data

        sess_false_completed = _create_session("Test false completed session rejection")
        harness, case = _make_test_harness(
            module, client, sess_false_completed, "persona-test", interceptor=false_completed_session
        )
        with self.assertRaises((RuntimeError, BoundaryFailure)) as ctx:
            harness._case_persona_teaching(case)
        self.assertIn("remain active with ended_at null", str(ctx.exception))

        # I: Next consumer readback mismatch must not pass
        call_count = {"val": 0}

        def mismatch_next_consumer(path: str, method: str, data: Any) -> Any:
            if "/terminal-readback" in path and method == "GET":
                call_count["val"] += 1
                if call_count["val"] > 1:
                    data["eval_identity"] = "teval-identity-different"
            return data

        sess_next_consumer = _create_session("Test next consumer mismatch rejection")
        harness, case = _make_test_harness(
            module, client, sess_next_consumer, "persona-test", interceptor=mismatch_next_consumer
        )
        with self.assertRaises((RuntimeError, BoundaryFailure)) as ctx:
            harness._case_persona_teaching(case)
        self.assertIn(
            "operator/Learning verifier did not read the exact Teaching evaluation identity",
            str(ctx.exception),
        )

    def test_review_required_teaching_fails_when_consultation_unconfigured(self):
        module = _load_service_module()
        client = TestClient(module.app)
        headers = {"X-Pantheon-Tenant-ID": "tenant-test", "X-Pantheon-Actor-ID": "operator"}

        resp = client.post(
            "/api/training/sessions",
            json={
                "persona_id": "persona-test",
                "objective": "Test consultation handoff failure when unconfigured",
                "opened_by": "operator",
                "mode": "coaching",
            },
            headers=headers,
        )
        self.assertEqual(resp.status_code, 201)
        session_id = resp.json()["session_id"]
        seed_changed_supported_controls(module, session_id)

        queued = client.post(
            f"/api/training/sessions/{session_id}/preview-jobs",
            json={"mode": "consultation_required", "requested_by": "operator", "terminalize_session": False},
            headers={**headers, "Idempotency-Key": "test-key-unconfigured-1"},
        )
        self.assertEqual(queued.status_code, 201)
        job_id = queued.json()["job_id"]

        with mock.patch.dict("os.environ", {"PANTHEON_CONSULTATION_API_URL": "", "PANTHEON_CONSULTATION_SERVICE_URL": ""}):
            ran = client.post(f"/api/training/preview-jobs/{job_id}/run", headers=headers)
        self.assertEqual(ran.status_code, 200)
        job_res = ran.json()
        self.assertEqual(job_res["status"], "failed")
        self.assertEqual(job_res.get("error_code"), "runtime_evidence_unavailable")

        readback_resp = client.get(f"/api/training/sessions/{session_id}/readback", headers=headers)
        self.assertEqual(readback_resp.status_code, 200)
        readback_data = readback_resp.json()
        self.assertIsNone(readback_data.get("consultation_receipt"))
        self.assertIsNone(readback_data.get("consult_request_id"))
        self.assertIsNone(readback_data.get("evaluation_result"))

    def test_review_required_teaching_fails_when_consultation_unauthorized(self):
        module = _load_service_module()
        client = TestClient(module.app)
        headers = {"X-Pantheon-Tenant-ID": "tenant-test", "X-Pantheon-Actor-ID": "operator"}

        resp = client.post(
            "/api/training/sessions",
            json={
                "persona_id": "persona-test",
                "objective": "Test consultation handoff failure when unauthorized",
                "opened_by": "operator",
                "mode": "coaching",
            },
            headers=headers,
        )
        self.assertEqual(resp.status_code, 201)
        session_id = resp.json()["session_id"]
        seed_changed_supported_controls(module, session_id)

        queued = client.post(
            f"/api/training/sessions/{session_id}/preview-jobs",
            json={"mode": "consultation_required", "requested_by": "operator", "terminalize_session": False},
            headers={**headers, "Idempotency-Key": "test-key-unauthorized-1"},
        )
        self.assertEqual(queued.status_code, 201)
        job_id = queued.json()["job_id"]

        orig_init = module.TrainingConsultationClient.__init__

        def bad_token_init(client_self, *args, **kwargs):
            orig_init(client_self, *args, **kwargs)
            client_self.token = "bad-unauthorized-token"

        with mock.patch.object(module.TrainingConsultationClient, "__init__", bad_token_init):
            ran = client.post(f"/api/training/preview-jobs/{job_id}/run", headers=headers)
        self.assertEqual(ran.status_code, 200)
        job_res = ran.json()
        self.assertEqual(job_res["status"], "failed")
        self.assertEqual(job_res.get("error_code"), "runtime_evidence_unavailable")

        readback_resp = client.get(f"/api/training/sessions/{session_id}/readback", headers=headers)
        self.assertEqual(readback_resp.status_code, 200)
        readback_data = readback_resp.json()
        self.assertIsNone(readback_data.get("consultation_receipt"))
        self.assertIsNone(readback_data.get("consult_request_id"))
        self.assertIsNone(readback_data.get("evaluation_result"))


if __name__ == "__main__":
    unittest.main()

