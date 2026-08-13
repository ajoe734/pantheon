"""Unit & Integration tests for Teaching terminal evaluation readback and conditional Consultation handoff."""

from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
