"""Unit tests for Consultation client integration in Persona Teaching."""

import hashlib
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import uvicorn

SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from consultation_client import (
    TeachingConsultationReceipt,
    TrainingConsultationClient,
    load_consult_request_schema,
    validate_consult_request_payload,
)
from services.consultation import main as consultation_main
from services.consultation.client import ConsultationClientError
from services.consultation.models import ConsultRequestStatus
from services.consultation.store import ConsultationStore


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class TestTrainingConsultationClient(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._temp_dir = tempfile.TemporaryDirectory()
        cls._store = ConsultationStore(cls._temp_dir.name)
        cls._orig_store = consultation_main.store
        consultation_main.store = cls._store

        cls._orig_auth_required = os.environ.get("CONSULTATION_AUTH_REQUIRED")
        cls._orig_service_token = os.environ.get("CONSULTATION_SERVICE_TOKEN")
        os.environ["CONSULTATION_AUTH_REQUIRED"] = "true"
        os.environ["CONSULTATION_SERVICE_TOKEN"] = "test-service-token-secret"

        cls._port = _free_port()
        cls._server = uvicorn.Server(
            uvicorn.Config(
                consultation_main.app,
                host="127.0.0.1",
                port=cls._port,
                log_level="error",
            )
        )
        cls._thread = threading.Thread(target=cls._server.run, daemon=True)
        cls._thread.start()
        deadline = time.monotonic() + 10
        while not cls._server.started:
            if time.monotonic() >= deadline:
                raise RuntimeError("consultation test server did not start")
            time.sleep(0.01)
        cls._base_url = f"http://127.0.0.1:{cls._port}"

    @classmethod
    def tearDownClass(cls):
        cls._server.should_exit = True
        cls._thread.join(timeout=5)
        consultation_main.store = cls._orig_store
        if cls._orig_auth_required is not None:
            os.environ["CONSULTATION_AUTH_REQUIRED"] = cls._orig_auth_required
        else:
            os.environ.pop("CONSULTATION_AUTH_REQUIRED", None)
        if cls._orig_service_token is not None:
            os.environ["CONSULTATION_SERVICE_TOKEN"] = cls._orig_service_token
        else:
            os.environ.pop("CONSULTATION_SERVICE_TOKEN", None)
        cls._temp_dir.cleanup()

    def test_schema_loads(self):
        schema = load_consult_request_schema()
        self.assertIsInstance(schema, dict)
        self.assertEqual(schema.get("title"), "ConsultRequest")

    def test_absent_wiring_never_returns_submitted_success(self):
        client = TrainingConsultationClient(base_url="")
        with self.assertRaises(ConsultationClientError) as ctx:
            client.create_teaching_consult_request(
                session_id="trn-20260813-001",
                tenant_id="tenant-alpha",
                persona_id="persona-beta",
                eval_id="teval-999",
                validation_errors=["sharpe_ratio_below_threshold"],
                metrics={"total_return": -0.25, "sharpe_ratio": -0.8, "max_drawdown": 0.3},
                trace_id="trace-test-123",
                requested_by="operator",
            )
        self.assertIn("not configured", str(ctx.exception))

    def test_create_teaching_consult_request_success_with_real_http_owner_boundary(self):
        client = TrainingConsultationClient(
            base_url=self._base_url,
            token="test-service-token-secret",
            service_actor="training-session",
        )
        receipt = client.create_teaching_consult_request(
            session_id="trn-20260813-001",
            tenant_id="tenant-alpha",
            persona_id="persona-beta",
            eval_id="teval-999",
            validation_errors=["sharpe_ratio_below_threshold"],
            metrics={"total_return": -0.25, "sharpe_ratio": -0.8, "max_drawdown": 0.3},
            trace_id="trace-test-123",
            requested_by="operator",
        )
        self.assertIsInstance(receipt, TeachingConsultationReceipt)
        expected_digest = hashlib.sha256("tenant-alpha:trn-20260813-001:teval-999".encode("utf-8")).hexdigest()[:12]
        expected_request_id = f"creq-teach-{expected_digest}"
        self.assertEqual(receipt.consult_request_id, expected_request_id)
        self.assertEqual(receipt.session_id, "trn-20260813-001")
        self.assertEqual(receipt.tenant_id, "tenant-alpha")
        self.assertEqual(receipt.eval_id, "teval-999")
        self.assertEqual(receipt.status, "submitted")

        # Validate readback payload against canonical ConsultRequest JSON schema
        payload = receipt.request_payload
        validate_consult_request_payload(payload)

        self.assertEqual(payload["request_id"], expected_request_id)
        self.assertEqual(payload["tenant_id"], "tenant-alpha")
        self.assertEqual(payload["status"], "submitted")
        self.assertEqual(payload["request_type"], "persona_policy")
        self.assertEqual(payload["target_type"], "teaching_session")
        self.assertEqual(payload["target_id"], "trn-20260813-001")
        self.assertEqual(payload["requested_by"]["actor_id"], "operator")
        self.assertEqual(payload["metadata"]["eval_id"], "teval-999")
        self.assertIn("sharpe_ratio_below_threshold", payload["metadata"]["validation_errors"])

        # Verify real owner store has the submitted request
        stored = self._store.get_request(expected_request_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.status, ConsultRequestStatus.SUBMITTED)
        self.assertEqual(stored.tenant_id, "tenant-alpha")
        self.assertEqual(stored.target_id, "trn-20260813-001")

        # Verify create idempotency: replay generates same request_id and returns same receipt
        replay = client.create_teaching_consult_request(
            session_id="trn-20260813-001",
            tenant_id="tenant-alpha",
            persona_id="persona-beta",
            eval_id="teval-999",
            validation_errors=["sharpe_ratio_below_threshold"],
            metrics={"total_return": -0.25, "sharpe_ratio": -0.8, "max_drawdown": 0.3},
            trace_id="trace-test-123",
            requested_by="operator",
        )
        self.assertEqual(replay.consult_request_id, expected_request_id)
        self.assertEqual(replay.status, "submitted")

    def test_wrong_auth_never_returns_submitted_success(self):
        client = TrainingConsultationClient(
            base_url=self._base_url,
            token="wrong-token-forbidden",
            service_actor="training-session",
        )
        with self.assertRaises(ConsultationClientError) as ctx:
            client.create_teaching_consult_request(
                session_id="trn-20260813-002",
                tenant_id="tenant-alpha",
                persona_id="persona-beta",
                eval_id="teval-002",
                validation_errors=["sharpe_ratio_below_threshold"],
                metrics={"total_return": -0.1},
                trace_id="trace-test-wrong-auth",
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_missing_auth_token_never_returns_submitted_success(self):
        client = TrainingConsultationClient(
            base_url=self._base_url,
            token="",
            service_actor="training-session",
        )
        with self.assertRaises(ConsultationClientError) as ctx:
            client.create_teaching_consult_request(
                session_id="trn-20260813-003",
                tenant_id="tenant-alpha",
                persona_id="persona-beta",
                eval_id="teval-003",
                validation_errors=["sharpe_ratio_below_threshold"],
                metrics={"total_return": -0.1},
                trace_id="trace-test-missing-auth",
            )
        self.assertEqual(ctx.exception.status_code, 401)

    def test_server_unavailable_never_returns_submitted_success(self):
        client = TrainingConsultationClient(
            base_url="http://127.0.0.1:59999",
            token="test-service-token-secret",
        )
        with self.assertRaises(ConsultationClientError) as ctx:
            client.create_teaching_consult_request(
                session_id="trn-20260813-004",
                tenant_id="tenant-alpha",
                persona_id="persona-beta",
                eval_id="teval-004",
                validation_errors=[],
                metrics={},
                trace_id="trace-test-unavail",
            )
        self.assertIn("unavailable", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
