"""Unit tests for Consultation client integration in Persona Teaching."""

import sys
import unittest
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from consultation_client import (
    TeachingConsultationReceipt,
    TrainingConsultationClient,
    load_consult_request_schema,
    validate_consult_request_payload,
)


class TestTrainingConsultationClient(unittest.TestCase):

    def test_schema_loads(self):
        schema = load_consult_request_schema()
        self.assertIsInstance(schema, dict)
        self.assertEqual(schema.get("title"), "ConsultRequest")

    def test_create_teaching_consult_request_success(self):
        client = TrainingConsultationClient()
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
        self.assertTrue(receipt.consult_request_id.startswith("creq-teach-"))
        self.assertEqual(receipt.session_id, "trn-20260813-001")
        self.assertEqual(receipt.tenant_id, "tenant-alpha")
        self.assertEqual(receipt.status, "submitted")

        payload = receipt.request_payload
        # Validate against canonical ConsultRequest JSON schema
        validate_consult_request_payload(payload)

        self.assertEqual(payload["request_type"], "persona_policy")
        self.assertEqual(payload["target_type"], "teaching_session")
        self.assertEqual(payload["target_id"], "trn-20260813-001")
        self.assertEqual(payload["requested_by"]["actor_id"], "operator")
        self.assertEqual(payload["metadata"]["eval_id"], "teval-999")
        self.assertIn("sharpe_ratio_below_threshold", payload["metadata"]["validation_errors"])


if __name__ == "__main__":
    unittest.main()
