"""Unit & Integration tests for Teaching terminal evaluation readback and conditional Consultation handoff."""

from __future__ import annotations

import copy
import importlib.util
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from models import TeachingEvaluationResult
from strict_test_support import (
    FIXED_TRUSTED_NOW,
    make_fake_persona_target_commit,
    make_fake_real_vectorbt_workflow,
    make_fake_target_precondition_reader,
    materialize_strict_authority,
    seed_changed_supported_controls,
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


def check_persona_teaching_harness_boundary(
    *,
    job: dict[str, Any],
    terminal: dict[str, Any],
    session_id: str,
    persona_id: str,
    job_id: str,
    eval_id: str = "",
) -> None:
    if not (isinstance(job, dict) and job.get("status") == "completed"):
        raise RuntimeError(f"Teaching evaluation job failed: {job.get('error_code')}: {job.get('failure_reason')}")
    if not (job.get("job_id") == job_id and job.get("session_id") == session_id):
        raise RuntimeError("Teaching preview job returned mismatched job/session identity")
    if terminal.get("session_id") != session_id:
        raise RuntimeError("Teaching readback session_id mismatch")
    if terminal.get("persona_id") != persona_id:
        raise RuntimeError("Teaching readback persona_id mismatch")
    if terminal.get("status") not in {"active", "completed"}:
        raise RuntimeError(f"Unexpected TeachingSession status: {terminal.get('status')}")
    evaluation = terminal.get("evaluation_result")
    if not isinstance(evaluation, dict):
        raise RuntimeError("Teaching readback missing evaluation_result")
    eval_identity = str(terminal.get("eval_identity") or "")
    if not eval_identity:
        raise RuntimeError("Teaching readback missing eval_identity")
    if evaluation.get("eval_identity") != eval_identity:
        raise RuntimeError("Teaching evaluation identity drifted")
    if evaluation.get("session_id") != session_id:
        raise RuntimeError("Teaching evaluation session_id mismatch")
    if evaluation.get("persona_id") != persona_id:
        raise RuntimeError("Teaching evaluation persona_id mismatch")
    expected_eval_id = eval_id or str(job.get("eval_id") or "")
    if expected_eval_id and evaluation.get("eval_id") != expected_eval_id:
        raise RuntimeError("Teaching evaluation eval_id mismatch")

    preview = terminal.get("preview") if isinstance(terminal.get("preview"), dict) else (job.get("preview") if isinstance(job.get("preview"), dict) else {})
    if not (isinstance(preview, dict) and preview):
        raise RuntimeError("Teaching preview missing from readback")
    proof = preview.get("evaluation_proof") if isinstance(preview, dict) else {}
    if not (isinstance(proof, dict) and proof):
        raise RuntimeError("Teaching evaluation proof missing from readback")
    if proof.get("session_id") != session_id:
        raise RuntimeError("Teaching evaluation proof session_id mismatch")
    if proof.get("job_id") != job_id:
        raise RuntimeError("Teaching evaluation proof job_id mismatch")
    if proof.get("eval_id") != evaluation.get("eval_id"):
        raise RuntimeError("Teaching evaluation proof eval_id mismatch")
    if evaluation.get("evaluation_proof_ref") and proof.get("proof_ref") != evaluation.get("evaluation_proof_ref"):
        raise RuntimeError("Teaching evaluation proof_ref mismatch")
    if not proof.get("proof_digest"):
        raise RuntimeError("Teaching evaluation proof missing proof_digest")

    backend = proof.get("backend") if isinstance(proof.get("backend"), dict) else {}
    if backend.get("framework") != "vectorbt":
        raise RuntimeError(f"Teaching computation framework mismatch: {backend.get('framework')}")
    if not backend.get("run_id"):
        raise RuntimeError("Teaching computation missing backend run_id")
    if not preview.get("preview_quality"):
        raise RuntimeError("Teaching preview missing preview_quality")

    metrics = evaluation.get("metrics")
    if not (isinstance(metrics, dict) and metrics):
        raise RuntimeError("Teaching evaluation missing metrics")
    for metric_key in ("total_return", "sharpe_ratio", "max_drawdown"):
        val = metrics.get(metric_key)
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
            raise RuntimeError(f"Teaching evaluation metric {metric_key} non-finite or invalid: {val}")

    authority = proof.get("authority") if isinstance(proof.get("authority"), dict) else {}
    dataset = authority.get("dataset") if isinstance(authority.get("dataset"), dict) else {}
    if not dataset.get("dataset_version_id"):
        raise RuntimeError("Teaching evaluation missing authority dataset_version_id")
    if not dataset.get("digest"):
        raise RuntimeError("Teaching evaluation missing authority dataset digest")
    policy = authority.get("policy") if isinstance(authority.get("policy"), dict) else {}
    if not policy.get("policy_id"):
        raise RuntimeError("Teaching evaluation missing authority policy_id")
    if not policy.get("digest"):
        raise RuntimeError("Teaching evaluation missing authority policy digest")

    val_status = evaluation.get("validation_status")
    if val_status not in {"passed", "failed"}:
        raise RuntimeError(f"Unexpected Teaching evaluation validation_status: {val_status}")
    if val_status == "failed":
        if proof.get("governance_gate_state") != "failed":
            raise RuntimeError("Failed evaluation must preserve failed governance_gate_state")
        if proof.get("status") != "failed":
            raise RuntimeError("Failed evaluation proof must preserve failed status")
        if proof.get("commit_eligible") is True:
            raise RuntimeError("Failed evaluation must not be commit eligible")
        if not evaluation.get("validation_errors"):
            raise RuntimeError("Failed evaluation missing validation_errors")
        if evaluation.get("consultation_required") is not True:
            raise RuntimeError("Failed evaluation must require consultation")
        if not evaluation.get("consult_request_id"):
            raise RuntimeError("Failed evaluation missing consult_request_id")
        if not isinstance(evaluation.get("consultation_receipt"), dict):
            raise RuntimeError("Failed evaluation missing consultation_receipt")
        if not (terminal.get("status") == "active" and terminal.get("ended_at") is None):
            raise RuntimeError("Failed evaluation session must remain active with ended_at null")
    else:
        if proof.get("governance_gate_state") != "passed":
            raise RuntimeError("Passed evaluation must preserve passed governance_gate_state")
        if evaluation.get("validation_errors") != []:
            raise RuntimeError("Passed evaluation must have empty validation_errors")


class TestTeachingEvaluationReadback(unittest.TestCase):

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

        # 2. Seed controls and run preview evaluation job
        seed_changed_supported_controls(module, session_id)
        queued = client.post(
            f"/api/training/sessions/{session_id}/preview-jobs",
            json={"mode": "refresh", "requested_by": "operator", "terminalize_session": False},
            headers={**headers, "Idempotency-Key": "test-key-ordinary-1"},
        )
        self.assertEqual(queued.status_code, 201)
        job_id = queued.json()["job_id"]

        ran = client.post(f"/api/training/preview-jobs/{job_id}/run", headers=headers)
        self.assertEqual(ran.status_code, 200)
        job_res = ran.json()
        self.assertEqual(job_res["status"], "completed")
        preview = job_res["preview"]

        # In fake vectorbt workflow, threshold metrics pass -> validation_status == "passed"
        self.assertEqual(preview["validation_status"], "passed")
        eval_result = preview.get("evaluation_result")
        self.assertIsNotNone(eval_result)
        self.assertFalse(eval_result["consultation_required"])
        self.assertIsNone(eval_result.get("consult_request_id"))
        self.assertIsNone(eval_result.get("consultation_receipt"))
        self.assertTrue(eval_result["eval_identity"].startswith("teval-identity-"))

        # 3. Readback endpoint check
        readback_resp = client.get(f"/api/training/sessions/{session_id}/readback", headers=headers)
        self.assertEqual(readback_resp.status_code, 200)
        readback_data = readback_resp.json()
        self.assertEqual(readback_data["session_id"], session_id)
        self.assertEqual(readback_data["eval_identity"], eval_result["eval_identity"])
        self.assertFalse(readback_data["consultation_required"])
        self.assertIsNone(readback_data.get("consult_request_id"))

        # Confirm GET /api/training/sessions/{session_id} also carries evaluation_result & eval_identity
        sess_resp = client.get(f"/api/training/sessions/{session_id}", headers=headers)
        self.assertEqual(sess_resp.status_code, 200)
        sess_data = sess_resp.json()
        self.assertEqual(sess_data["eval_identity"], eval_result["eval_identity"])
        self.assertIsNotNone(sess_data.get("evaluation_result"))

        # Verify terminal-readback endpoint and harness boundary
        term_resp = client.get(f"/api/training/sessions/{session_id}/terminal-readback", headers=headers)
        self.assertEqual(term_resp.status_code, 200)
        check_persona_teaching_harness_boundary(
            job=job_res,
            terminal=term_resp.json(),
            session_id=session_id,
            persona_id="persona-test",
            job_id=job_id,
            eval_id=eval_result["eval_id"],
        )

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

        seed_changed_supported_controls(module, session_id)
        queued = client.post(
            f"/api/training/sessions/{session_id}/preview-jobs",
            json={"mode": "refresh", "requested_by": "operator", "terminalize_session": False},
            headers={**headers, "Idempotency-Key": "test-key-ineligible-1"},
        )
        self.assertEqual(queued.status_code, 201)
        job_id = queued.json()["job_id"]
        eval_id = queued.json()["eval_id"]

        ran = client.post(f"/api/training/preview-jobs/{job_id}/run", headers=headers)
        self.assertEqual(ran.status_code, 200)
        job_res = ran.json()
        self.assertEqual(job_res["status"], "completed")
        self.assertEqual(job_res["job_id"], job_id)
        self.assertEqual(job_res["session_id"], session_id)

        preview = job_res["preview"]
        self.assertEqual(preview["validation_status"], "failed")
        self.assertIn("sharpe_ratio_below_threshold", preview["validation_errors"])

        eval_result = preview.get("evaluation_result")
        self.assertIsNotNone(eval_result)
        self.assertEqual(eval_result["validation_status"], "failed")
        self.assertIn("sharpe_ratio_below_threshold", eval_result["validation_errors"])
        self.assertTrue(eval_result["consultation_required"])
        self.assertIsNotNone(eval_result.get("consult_request_id"))
        self.assertTrue(eval_result["consult_request_id"].startswith("creq-teach-"))
        self.assertIsNotNone(eval_result.get("consultation_receipt"))
        self.assertEqual(eval_result["eval_id"], eval_id)

        metrics = eval_result["metrics"]
        self.assertIsInstance(metrics, dict)
        for key in ("total_return", "sharpe_ratio", "max_drawdown"):
            val = metrics[key]
            self.assertIsInstance(val, (int, float))
            self.assertFalse(isinstance(val, bool))
            self.assertTrue(math.isfinite(val))

        proof = preview.get("evaluation_proof")
        self.assertIsNotNone(proof)
        self.assertEqual(proof["governance_gate_state"], "failed")
        self.assertEqual(proof["status"], "failed")
        self.assertFalse(proof.get("commit_eligible", False))
        self.assertEqual(proof["backend"]["framework"], "vectorbt")
        self.assertEqual(proof["backend"]["run_id"], "vbt-real-test-ineligible-001")
        self.assertTrue(bool(proof["authority"]["dataset"]["dataset_version_id"]))
        self.assertTrue(bool(proof["authority"]["policy"]["policy_id"]))

        readback_resp = client.get(f"/api/training/sessions/{session_id}/terminal-readback", headers=headers)
        self.assertEqual(readback_resp.status_code, 200)
        readback_data = readback_resp.json()
        self.assertEqual(readback_data["session_id"], session_id)
        self.assertEqual(readback_data["status"], "active")
        self.assertIsNone(readback_data["ended_at"])
        self.assertEqual(readback_data["eval_identity"], eval_result["eval_identity"])
        self.assertEqual(readback_data["evaluation_result"]["eval_id"], eval_id)
        self.assertEqual(readback_data["evaluation_result"]["validation_status"], "failed")
        self.assertTrue(readback_data["consultation_required"])

        check_persona_teaching_harness_boundary(
            job=job_res,
            terminal=readback_data,
            session_id=session_id,
            persona_id="persona-test",
            job_id=job_id,
            eval_id=eval_id,
        )

        complete_resp = client.post(f"/api/training/sessions/{session_id}/complete", headers=headers)
        self.assertEqual(complete_resp.status_code, 409)
        self.assertIn("evaluation governance gate is not passed", complete_resp.json()["detail"])

        sess_resp = client.get(f"/api/training/sessions/{session_id}", headers=headers)
        self.assertEqual(sess_resp.status_code, 200)
        self.assertEqual(sess_resp.json()["status"], "active")
        self.assertIsNone(sess_resp.json()["ended_at"])

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

        resp = client.post(
            "/api/training/sessions",
            json={
                "persona_id": "persona-test",
                "objective": "Test harness boundary rejections",
                "opened_by": "operator",
                "mode": "evaluation",
            },
            headers=headers,
        )
        session_id = resp.json()["session_id"]
        seed_changed_supported_controls(module, session_id)
        queued = client.post(
            f"/api/training/sessions/{session_id}/preview-jobs",
            json={"mode": "refresh", "requested_by": "operator", "terminalize_session": False},
            headers={**headers, "Idempotency-Key": "test-key-rejections-1"},
        )
        job_id = queued.json()["job_id"]
        eval_id = queued.json()["eval_id"]

        # A: Pending job must not pass
        pending_job = client.get(f"/api/training/preview-jobs/{job_id}", headers=headers).json()
        self.assertEqual(pending_job["status"], "queued")
        with self.assertRaises(RuntimeError) as ctx:
            check_persona_teaching_harness_boundary(
                job=pending_job,
                terminal={},
                session_id=session_id,
                persona_id="persona-test",
                job_id=job_id,
                eval_id=eval_id,
            )
        self.assertIn("Teaching evaluation job failed", str(ctx.exception))

        # Run job to completion so we have a valid baseline for mutation tests
        ran = client.post(f"/api/training/preview-jobs/{job_id}/run", headers=headers)
        self.assertEqual(ran.status_code, 200)
        completed_job = ran.json()
        terminal_readback = client.get(f"/api/training/sessions/{session_id}/terminal-readback", headers=headers).json()

        # Sanity check: valid baseline passes
        check_persona_teaching_harness_boundary(
            job=completed_job,
            terminal=terminal_readback,
            session_id=session_id,
            persona_id="persona-test",
            job_id=job_id,
            eval_id=eval_id,
        )

        # B: Failed compute must not pass
        failed_job = copy.deepcopy(completed_job)
        failed_job["status"] = "failed"
        failed_job["error_code"] = "evaluation_computation_error"
        failed_job["failure_reason"] = "backend failed"
        with self.assertRaises(RuntimeError) as ctx:
            check_persona_teaching_harness_boundary(
                job=failed_job,
                terminal=terminal_readback,
                session_id=session_id,
                persona_id="persona-test",
                job_id=job_id,
                eval_id=eval_id,
            )
        self.assertIn("evaluation_computation_error", str(ctx.exception))

        # C: Mismatched job_id must not pass
        mismatched_job = copy.deepcopy(completed_job)
        mismatched_job["job_id"] = "pvjob-different"
        with self.assertRaises(RuntimeError) as ctx:
            check_persona_teaching_harness_boundary(
                job=mismatched_job,
                terminal=terminal_readback,
                session_id=session_id,
                persona_id="persona-test",
                job_id=job_id,
                eval_id=eval_id,
            )
        self.assertIn("mismatched job/session identity", str(ctx.exception))

        # D: Mismatched session_id in terminal readback must not pass
        mismatched_term = copy.deepcopy(terminal_readback)
        mismatched_term["session_id"] = "trn-different"
        with self.assertRaises(RuntimeError) as ctx:
            check_persona_teaching_harness_boundary(
                job=completed_job,
                terminal=mismatched_term,
                session_id=session_id,
                persona_id="persona-test",
                job_id=job_id,
                eval_id=eval_id,
            )
        self.assertIn("session_id mismatch", str(ctx.exception))

        # E: Mismatched eval_identity must not pass
        mismatched_eval_id = copy.deepcopy(terminal_readback)
        mismatched_eval_id["eval_identity"] = "teval-identity-other"
        with self.assertRaises(RuntimeError) as ctx:
            check_persona_teaching_harness_boundary(
                job=completed_job,
                terminal=mismatched_eval_id,
                session_id=session_id,
                persona_id="persona-test",
                job_id=job_id,
                eval_id=eval_id,
            )
        self.assertIn("identity drifted", str(ctx.exception))

        # F: Non-finite metric must not pass
        bad_metric_term = copy.deepcopy(terminal_readback)
        bad_metric_term["evaluation_result"]["metrics"]["sharpe_ratio"] = float("nan")
        with self.assertRaises(RuntimeError) as ctx:
            check_persona_teaching_harness_boundary(
                job=completed_job,
                terminal=bad_metric_term,
                session_id=session_id,
                persona_id="persona-test",
                job_id=job_id,
                eval_id=eval_id,
            )
        self.assertIn("non-finite", str(ctx.exception))

        # G: Mismatched authority dataset must not pass
        bad_authority_term = copy.deepcopy(terminal_readback)
        bad_authority_term["preview"]["evaluation_proof"]["authority"]["dataset"]["dataset_version_id"] = ""
        with self.assertRaises(RuntimeError) as ctx:
            check_persona_teaching_harness_boundary(
                job=completed_job,
                terminal=bad_authority_term,
                session_id=session_id,
                persona_id="persona-test",
                job_id=job_id,
                eval_id=eval_id,
            )
        self.assertIn("missing authority dataset_version_id", str(ctx.exception))

        # H: Failed validation incorrectly claiming completed session must not pass
        ineligible_term = copy.deepcopy(terminal_readback)
        ineligible_term["evaluation_result"]["validation_status"] = "failed"
        ineligible_term["preview"]["evaluation_proof"]["governance_gate_state"] = "failed"
        ineligible_term["preview"]["evaluation_proof"]["status"] = "failed"
        ineligible_term["preview"]["evaluation_proof"]["commit_eligible"] = False
        ineligible_term["evaluation_result"]["validation_errors"] = ["sharpe_ratio_below_threshold"]
        ineligible_term["evaluation_result"]["consultation_required"] = True
        ineligible_term["evaluation_result"]["consult_request_id"] = "creq-teach-12345"
        ineligible_term["evaluation_result"]["consultation_receipt"] = {"status": "submitted"}
        ineligible_term["status"] = "completed"  # False claim: must be active!
        with self.assertRaises(RuntimeError) as ctx:
            check_persona_teaching_harness_boundary(
                job=completed_job,
                terminal=ineligible_term,
                session_id=session_id,
                persona_id="persona-test",
                job_id=job_id,
                eval_id=eval_id,
            )
        self.assertIn("remain active with ended_at null", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
