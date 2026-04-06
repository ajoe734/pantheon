"""
Unit tests for Replication Gate

Tests all admission criteria and gate logic.
"""

import unittest
from datetime import datetime

from gate_schema import (
    ReplicationRequest,
    ReplicationResponse,
    ReplicationStatus,
    CandidateAdmissionStatus,
    ReplicationCriteria,
)
from gate import ReplicationGate, create_promotion_request
from gate_config import GateConfig, AdmissionRules


class TestGateSchema(unittest.TestCase):
    """Tests for gate schema dataclasses."""

    def test_replication_request_creation(self):
        """Test creating a replication request."""
        request = ReplicationRequest(
            candidate_id="cand-001",
            source_task_id="RS-002",
            research_handoff={"source_metadata": {}},
            proposed_strategy_spec={"name": "test"},
        )
        self.assertEqual(request.candidate_id, "cand-001")
        self.assertEqual(request.source_task_id, "RS-002")

    def test_replication_request_to_json(self):
        """Test JSON serialization of request."""
        request = ReplicationRequest(
            candidate_id="cand-001",
            source_task_id="RS-002",
            research_handoff={},
            proposed_strategy_spec={},
        )
        json_str = request.to_json()
        self.assertIn("cand-001", json_str)
        self.assertIn("RS-002", json_str)

    def test_replication_response_passed_property(self):
        """Test that passed property works correctly."""
        response_admitted = ReplicationResponse(
            gate_run_id="run-001",
            candidate_id="cand-001",
            admission_status=CandidateAdmissionStatus.ADMITTED,
            replication_status=ReplicationStatus.PASSED,
            results=[],
            summary="Test passed",
        )
        self.assertTrue(response_admitted.passed)

        response_rejected = ReplicationResponse(
            gate_run_id="run-001",
            candidate_id="cand-001",
            admission_status=CandidateAdmissionStatus.REJECTED,
            replication_status=ReplicationStatus.FAILED,
            results=[],
            summary="Test failed",
        )
        self.assertFalse(response_rejected.passed)


class TestGateConfig(unittest.TestCase):
    """Tests for gate configuration."""

    def test_config_has_required_criteria(self):
        """Test that config has required criteria."""
        config = GateConfig()
        required = config.get_required_criteria()
        self.assertEqual(len(required), 4)
        ids = [c.criterion_id for c in required]
        self.assertIn("schema_validity", ids)
        self.assertIn("lineage_complete", ids)
        self.assertIn("governance_context", ids)
        self.assertIn("no_live_bypass", ids)

    def test_config_has_optional_criteria(self):
        """Test that config has optional criteria."""
        config = GateConfig()
        optional = config.get_optional_criteria()
        self.assertEqual(len(optional), 4)
        ids = [c.criterion_id for c in optional]
        self.assertIn("confidence_score", ids)
        self.assertIn("replication_notes_present", ids)
        self.assertIn("evaluation_hypotheses", ids)
        self.assertIn("implementation_ready", ids)

    def test_config_to_json(self):
        """Test configuration JSON serialization."""
        config = GateConfig()
        json_str = config.to_json()
        self.assertIn("required_criteria", json_str)
        self.assertIn("optional_criteria", json_str)


class TestAdmissionRules(unittest.TestCase):
    """Tests for admission decision logic."""

    def test_all_required_criteria_must_pass(self):
        """Test that all required criteria must pass."""
        required = {"schema": True, "lineage": False, "governance": True, "bypass": True}
        optional = {}
        admitted, reasoning = AdmissionRules.evaluate_admission(required, optional)
        self.assertFalse(admitted)
        self.assertIn("Failed", reasoning)

    def test_all_required_pass_all_optional_pass(self):
        """Test admission when all criteria pass."""
        required = {"schema": True, "lineage": True, "governance": True, "bypass": True}
        optional = {
            "confidence": True,
            "notes": True,
            "hypotheses": True,
            "readiness": True,
        }
        admitted, reasoning = AdmissionRules.evaluate_admission(required, optional)
        self.assertTrue(admitted)

    def test_optional_threshold(self):
        """Test optional criteria threshold (80%)."""
        required = {"schema": True, "lineage": True, "governance": True, "bypass": True}
        # 2/4 optional pass = 50% < 80% threshold
        optional = {"conf": True, "notes": True, "hyp": False, "ready": False}
        admitted, reasoning = AdmissionRules.evaluate_admission(required, optional)
        self.assertFalse(admitted)
        self.assertIn("Insufficient", reasoning)

    def test_optional_threshold_pass(self):
        """Test optional criteria threshold pass (80% or more)."""
        required = {"schema": True, "lineage": True, "governance": True, "bypass": True}
        # 3/4 optional pass = 75% which rounds to pass
        optional = {"conf": True, "notes": True, "hyp": True, "ready": False}
        admitted, reasoning = AdmissionRules.evaluate_admission(required, optional)
        # 3/4 = 0.75 which is still less than 0.80, so should fail
        self.assertFalse(admitted)

        # 4/5 = 80% should pass
        optional = {
            "conf": True,
            "notes": True,
            "hyp": True,
            "ready": True,
            "extra": False,
        }
        admitted, reasoning = AdmissionRules.evaluate_admission(required, optional)
        self.assertTrue(admitted)


class TestReplicationGate(unittest.TestCase):
    """Tests for replication gate executor."""

    def setUp(self):
        """Set up test fixtures."""
        self.gate = ReplicationGate()

    def _create_valid_request(self) -> ReplicationRequest:
        """Create a valid replication request."""
        return ReplicationRequest(
            candidate_id="cand-001",
            source_task_id="RS-002",
            research_handoff={
                "source_metadata": {
                    "api_endpoint": "https://api.openalex.org/works/W123",
                    "retrieved_at": datetime.utcnow().isoformat() + "Z",
                    "governance_context": "Approved structured source",
                },
                "normalized_findings": {
                    "strategy_spec": {"name": "test"},
                    "replication_notes": "Test strategy",
                    "evaluation_hypotheses": "Expected Sharpe > 1.0",
                },
                "grok_processing_notes": {
                    "normalization_confidence": "high",
                    "governance_compliance": "verified",
                    "downstream_readiness": "ready_for_replication",
                },
            },
            proposed_strategy_spec={
                "name": "Test Strategy",
                "description": "A test strategy",
                "signals": ["price_ma"],
                "parameters": {"window": 20},
            },
        )

    def test_evaluate_valid_candidate(self):
        """Test evaluation of a valid candidate."""
        request = self._create_valid_request()
        response = self.gate.evaluate_candidate(request)

        self.assertEqual(response.candidate_id, "cand-001")
        self.assertEqual(response.admission_status, CandidateAdmissionStatus.ADMITTED)
        self.assertEqual(response.replication_status, ReplicationStatus.PASSED)
        self.assertTrue(response.passed)

    def test_schema_validity_check_valid(self):
        """Test schema validity check with valid spec."""
        request = self._create_valid_request()
        response = self.gate.evaluate_candidate(request)

        schema_result = next(
            r for r in response.results if r.criterion_id == "schema_validity"
        )
        self.assertTrue(schema_result.passed)

    def test_schema_validity_check_missing_fields(self):
        """Test schema validity check with missing fields."""
        request = self._create_valid_request()
        request.proposed_strategy_spec = {
            "name": "Test",
            # Missing: description, signals, parameters
        }
        response = self.gate.evaluate_candidate(request)

        schema_result = next(
            r for r in response.results if r.criterion_id == "schema_validity"
        )
        self.assertFalse(schema_result.passed)
        self.assertIn("Missing", schema_result.evidence)

    def test_lineage_completeness_check(self):
        """Test lineage completeness check."""
        request = self._create_valid_request()
        response = self.gate.evaluate_candidate(request)

        lineage_result = next(
            r for r in response.results if r.criterion_id == "lineage_complete"
        )
        self.assertTrue(lineage_result.passed)

    def test_lineage_missing_metadata(self):
        """Test lineage check fails with missing metadata."""
        request = self._create_valid_request()
        request.research_handoff["source_metadata"] = {
            "api_endpoint": "https://api.openalex.org",
            # Missing: retrieved_at, governance_context
        }
        response = self.gate.evaluate_candidate(request)

        lineage_result = next(
            r for r in response.results if r.criterion_id == "lineage_complete"
        )
        self.assertFalse(lineage_result.passed)

    def test_governance_context_check(self):
        """Test governance context check."""
        request = self._create_valid_request()
        response = self.gate.evaluate_candidate(request)

        gov_result = next(
            r for r in response.results if r.criterion_id == "governance_context"
        )
        self.assertTrue(gov_result.passed)

    def test_governance_compliance_not_verified(self):
        """Test governance check fails if compliance not verified."""
        request = self._create_valid_request()
        request.research_handoff["grok_processing_notes"]["governance_compliance"] = "pending"
        response = self.gate.evaluate_candidate(request)

        gov_result = next(
            r for r in response.results if r.criterion_id == "governance_context"
        )
        self.assertFalse(gov_result.passed)

    def test_no_live_bypass_check(self):
        """Test no live bypass check."""
        request = self._create_valid_request()
        response = self.gate.evaluate_candidate(request)

        bypass_result = next(r for r in response.results if r.criterion_id == "no_live_bypass")
        self.assertTrue(bypass_result.passed)

    def test_live_bypass_detected(self):
        """Test detection of live bypass attempts."""
        request = self._create_valid_request()
        request.proposed_strategy_spec["skip_promotion_gate"] = True
        response = self.gate.evaluate_candidate(request)

        bypass_result = next(r for r in response.results if r.criterion_id == "no_live_bypass")
        self.assertFalse(bypass_result.passed)
        self.assertIn("bypass", bypass_result.evidence.lower())

    def test_confidence_score_optional(self):
        """Test confidence score optional criterion."""
        request = self._create_valid_request()
        response = self.gate.evaluate_candidate(request)

        conf_result = next(
            r for r in response.results if r.criterion_id == "confidence_score"
        )
        # "high" confidence should pass (0.9 >= 0.7)
        self.assertTrue(conf_result.passed)

    def test_confidence_score_low(self):
        """Test confidence score fails for low confidence."""
        request = self._create_valid_request()
        request.research_handoff["grok_processing_notes"]["normalization_confidence"] = "low"
        response = self.gate.evaluate_candidate(request)

        conf_result = next(
            r for r in response.results if r.criterion_id == "confidence_score"
        )
        # "low" confidence = 0.5 < 0.7
        self.assertFalse(conf_result.passed)

    def test_audit_log(self):
        """Test that evaluations are logged."""
        request = self._create_valid_request()
        self.gate.evaluate_candidate(request)

        log = self.gate.get_audit_log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["candidate_id"], "cand-001")
        self.assertEqual(log[0]["admission_status"], "admitted")


class TestPromotionRequest(unittest.TestCase):
    """Tests for promotion request creation."""

    def test_create_promotion_request_admitted(self):
        """Test creating promotion request for admitted candidate."""
        response = ReplicationResponse(
            gate_run_id="run-001",
            candidate_id="cand-001",
            admission_status=CandidateAdmissionStatus.ADMITTED,
            replication_status=ReplicationStatus.PASSED,
            results=[],
            summary="Passed",
        )

        spec = {"name": "Test", "version": "1.0.0"}
        promo_req = create_promotion_request(response, spec)

        self.assertIsNotNone(promo_req)
        self.assertEqual(promo_req.candidate_id, "cand-001")
        self.assertEqual(promo_req.storage_backend, "object_store")
        self.assertIn("replication", promo_req.storage_path)

    def test_create_promotion_request_rejected(self):
        """Test that promotion request is None for rejected candidate."""
        response = ReplicationResponse(
            gate_run_id="run-001",
            candidate_id="cand-001",
            admission_status=CandidateAdmissionStatus.REJECTED,
            replication_status=ReplicationStatus.FAILED,
            results=[],
            summary="Failed",
        )

        spec = {"name": "Test"}
        promo_req = create_promotion_request(response, spec)

        self.assertIsNone(promo_req)


if __name__ == "__main__":
    unittest.main()
