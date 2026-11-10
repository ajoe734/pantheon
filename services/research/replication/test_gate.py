"""
Unit tests for Replication Gate

Tests all admission criteria and gate logic.
"""

import copy
import unittest
from datetime import datetime

from .gate_schema import (
    ReplicationRequest,
    ReplicationResponse,
    ReplicationStatus,
    CandidateAdmissionStatus,
    ReplicationCriteria,
)
from .gate import ReplicationGate, create_promotion_request
from .gate_config import GateConfig, AdmissionRules
from services.research.vectorbt.adapter.vectorbt_adapter import (
    build_canonical_tw_strategy_artifact,
    build_governed_tw_replication_dataset,
    run_replication_backtest_protocol,
)

_CACHED_VALID_BACKTEST = None


def _get_valid_backtest() -> dict:
    global _CACHED_VALID_BACKTEST
    if _CACHED_VALID_BACKTEST is None:
        _CACHED_VALID_BACKTEST = run_replication_backtest_protocol(
            strategy_artifact=build_canonical_tw_strategy_artifact(),
            dataset=build_governed_tw_replication_dataset(),
            run_id="bt-test-valid-001",
        )
    return copy.deepcopy(_CACHED_VALID_BACKTEST)


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
        self.assertEqual(len(required), 5)
        ids = [c.criterion_id for c in required]
        self.assertIn("schema_validity", ids)
        self.assertIn("lineage_complete", ids)
        self.assertIn("governance_context", ids)
        self.assertIn("no_live_bypass", ids)
        self.assertIn("real_backtest_validation", ids)

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
                "spec_version": "1.0",
                "strategy_id": "strat-test-001",
                "title": "Test Strategy",
                "hypothesis": "Price moving-average momentum can survive governed replication.",
                "objective": "Validate Sharpe ratio above 1.0 with bounded drawdown.",
                "market_scope": {
                    "symbols": ["2330", "2317"],
                    "asset_classes": ["equities"],
                    "frequency": "1d",
                },
                "data_dependencies": [
                    {"ref": "https://api.openalex.org/works/W123", "kind": "paper"}
                ],
                "execution_profile": {
                    "signal_schema_version": "1.0",
                    "quantity_type": "PERCENT_PORTFOLIO",
                    "execution_mode_hint": "research",
                },
                "evaluation_plan": {
                    "metrics": ["sharpe_ratio", "max_drawdown"],
                    "candidate_gate": "Pass replication gate",
                },
                "governance": {
                    "approval_required": True,
                    "policy_id": "oc-001-research-normalization",
                },
                "provenance": {
                    "source_kind": "paper",
                    "created_at": datetime.utcnow().isoformat() + "Z",
                    "source_refs": ["https://api.openalex.org/works/W123"],
                    "created_by": "Codex",
                },
            },
            backtest_evaluation=_get_valid_backtest(),
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
            "spec_version": "1.0",
            "strategy_id": "broken-spec",
            "title": "Broken spec",
            # Missing canonical required sections
        }
        response = self.gate.evaluate_candidate(request)

        schema_result = next(
            r for r in response.results if r.criterion_id == "schema_validity"
        )
        self.assertFalse(schema_result.passed)
        self.assertIn("required property", schema_result.evidence)

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

    def test_candidate_with_no_backtest_run_is_refused(self):
        """§10.3: A candidate with no backtest run is refused by ReplicationGate."""
        request = self._create_valid_request()
        request.backtest_evaluation = None
        response = self.gate.evaluate_candidate(request)

        self.assertFalse(response.passed)
        self.assertEqual(response.admission_status, CandidateAdmissionStatus.REJECTED)
        self.assertEqual(response.replication_status, ReplicationStatus.FAILED)
        bt_result = next(
            r for r in response.results if r.criterion_id == "real_backtest_validation"
        )
        self.assertFalse(bt_result.passed)
        self.assertIn("No real backtest run", bt_result.evidence)

    def test_candidate_with_good_is_sharpe_and_negative_oos_sharpe_is_refused(self):
        """§10.3: A candidate with good in-sample Sharpe and negative out-of-sample Sharpe is refused."""
        request = self._create_valid_request()
        dataset = build_governed_tw_replication_dataset(
            symbols=["2330", "2317"],
            trading_days=756,
            negative_oos=True,
        )
        request.backtest_evaluation = run_replication_backtest_protocol(
            strategy_artifact=build_canonical_tw_strategy_artifact(symbols=["2330", "2317"]),
            dataset=dataset,
            run_id="bt-neg-oos-001",
        )
        self.assertGreater(
            request.backtest_evaluation["in_sample"]["sharpe_ratio"],
            1.0,
        )
        self.assertLess(
            request.backtest_evaluation["out_of_sample"]["sharpe_ratio"],
            0.0,
        )

        response = self.gate.evaluate_candidate(request)
        self.assertFalse(response.passed)
        self.assertEqual(response.admission_status, CandidateAdmissionStatus.REJECTED)
        bt_result = next(
            r for r in response.results if r.criterion_id == "real_backtest_validation"
        )
        self.assertFalse(bt_result.passed)
        self.assertIn("oos_sharpe_positive_after_costs", bt_result.details["failed_checks"])

    def test_twenty_variants_of_one_family_raises_sharpe_threshold_and_refuses_marginal_variant(self):
        """§10.3: Running 20 variants of one family raises the required Sharpe threshold so a marginal variant that passed on trial 1 is refused on trial 20."""
        gate = ReplicationGate()
        marginal_eval = _get_valid_backtest()
        # Marginal OOS Sharpe = 0.85: beats base threshold (0.50 on trial 1) and buy-and-hold (0.31),
        # but is below the 20-trial deflated threshold (~1.112).
        marginal_eval["out_of_sample"]["sharpe_ratio"] = 0.85

        req_trial_1 = self._create_valid_request()
        req_trial_1.candidate_id = "cand-family-v1"
        req_trial_1.research_handoff["family_id"] = "tw-momentum-family-alpha"
        req_trial_1.backtest_evaluation = copy.deepcopy(marginal_eval)

        resp_trial_1 = gate.evaluate_candidate(req_trial_1)
        self.assertTrue(resp_trial_1.passed)
        self.assertEqual(gate.get_family_trial_count("tw-momentum-family-alpha"), 1)

        # Evaluate 18 intermediate variants of the same family (trials 2..19)
        for idx in range(2, 20):
            req_i = self._create_valid_request()
            req_i.candidate_id = f"cand-family-v{idx}"
            req_i.research_handoff["family_id"] = "tw-momentum-family-alpha"
            req_i.backtest_evaluation = copy.deepcopy(marginal_eval)
            gate.evaluate_candidate(req_i)

        # Trial 20 with the exact same marginal OOS Sharpe (0.85) must now be refused
        req_trial_20 = self._create_valid_request()
        req_trial_20.candidate_id = "cand-family-v20"
        req_trial_20.research_handoff["family_id"] = "tw-momentum-family-alpha"
        req_trial_20.backtest_evaluation = copy.deepcopy(marginal_eval)

        resp_trial_20 = gate.evaluate_candidate(req_trial_20)
        self.assertEqual(gate.get_family_trial_count("tw-momentum-family-alpha"), 20)
        self.assertFalse(resp_trial_20.passed)
        self.assertEqual(resp_trial_20.admission_status, CandidateAdmissionStatus.REJECTED)
        bt_20 = next(
            r for r in resp_trial_20.results if r.criterion_id == "real_backtest_validation"
        )
        self.assertFalse(bt_20.passed)
        self.assertEqual(bt_20.details["trial_count"], 20)
        self.assertGreater(
            bt_20.details["deflated_sharpe_threshold"],
            0.85,
        )
        self.assertIn(
            "oos_sharpe_exceeds_deflated_threshold",
            bt_20.details["failed_checks"],
        )

    def test_candidate_requiring_one_year_dataset_is_recorded_as_insufficient_history(self):
        """§10.3: A candidate requiring a dataset with 1 year of history is recorded as insufficient_history, neither failed nor admitted."""
        request = self._create_valid_request()
        one_year_dataset = build_governed_tw_replication_dataset(
            symbols=["2330", "2317"],
            trading_days=252,  # ~1 year (< 2 years required)
        )
        request.backtest_evaluation = run_replication_backtest_protocol(
            strategy_artifact=build_canonical_tw_strategy_artifact(symbols=["2330", "2317"]),
            dataset=one_year_dataset,
            run_id="bt-1y-history",
        )

        response = self.gate.evaluate_candidate(request)
        self.assertFalse(response.passed)
        self.assertEqual(
            response.admission_status,
            CandidateAdmissionStatus.INSUFFICIENT_HISTORY,
        )
        self.assertEqual(
            response.replication_status,
            ReplicationStatus.INSUFFICIENT_HISTORY,
        )
        self.assertNotIn(
            response.admission_status,
            (CandidateAdmissionStatus.ADMITTED, CandidateAdmissionStatus.REJECTED),
        )
        self.assertNotIn(
            response.replication_status,
            (ReplicationStatus.PASSED, ReplicationStatus.FAILED),
        )

    def test_worker_request_without_grok_processing_notes_passes_with_verified_policy_and_real_backtest(self):
        """Verify ReplicationGate evaluates governance from StrategySpec policy + real backtest when grok_processing_notes is not self-attested."""
        request = self._create_valid_request()
        request.research_handoff.pop("grok_processing_notes", None)

        response = self.gate.evaluate_candidate(request)
        self.assertTrue(response.passed)
        self.assertEqual(response.admission_status, CandidateAdmissionStatus.ADMITTED)


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
