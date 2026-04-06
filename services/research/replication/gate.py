"""
Replication Gate Executor

Core implementation of the first-pass replication gate.
Validates research candidates against admission criteria before
allowing promotion to the registry.
"""

import uuid
import json
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

from jsonschema import Draft7Validator, RefResolver

from .gate_schema import (
    ReplicationRequest,
    ReplicationResponse,
    ReplicationResult,
    ReplicationStatus,
    CandidateAdmissionStatus,
    RegistryPromotionRequest,
)
from .gate_config import GateConfig, AdmissionRules


class ReplicationGate:
    """
    Gate that sits between discovered research (RS-001/RS-002) and registry entry (REG-001).

    Validates research candidates against:
    1. Schema compliance
    2. Lineage completeness
    3. Governance context
    4. No live bypass attempts
    5. Optional: Confidence, replication notes, evaluation hypotheses
    """

    def __init__(self, config: Optional[GateConfig] = None):
        self.config = config or GateConfig()
        self.gate_run_logs: List[Dict[str, Any]] = []
        self._canonical_spec_validator = self._build_strategy_spec_validator()

    def evaluate_candidate(self, request: ReplicationRequest) -> ReplicationResponse:
        """
        Evaluate a research candidate for registry admission.

        Args:
            request: Candidate research request with handoff and proposed StrategySpec

        Returns:
            ReplicationResponse with admission decision and evidence
        """
        gate_run_id = str(uuid.uuid4())[:8]
        results: List[ReplicationResult] = []

        # Required criteria checks
        for criterion in self.config.get_required_criteria():
            result = self._check_criterion(criterion, request)
            results.append(result)

        # Optional criteria checks
        for criterion in self.config.get_optional_criteria():
            result = self._check_criterion(criterion, request)
            results.append(result)

        # Determine admission status
        required_results = {
            r.criterion_id: r.passed
            for r in results
            if any(r.criterion_id == c.criterion_id for c in self.config.get_required_criteria())
        }
        optional_results = {
            r.criterion_id: r.passed
            for r in results
            if any(r.criterion_id == c.criterion_id for c in self.config.get_optional_criteria())
        }

        admitted, reasoning = AdmissionRules.evaluate_admission(
            required_results, optional_results
        )

        # Determine replication status
        replication_status = (
            ReplicationStatus.PASSED
            if admitted
            else ReplicationStatus.FAILED
        )

        admission_status = (
            CandidateAdmissionStatus.ADMITTED
            if admitted
            else CandidateAdmissionStatus.REJECTED
        )

        # Build summary
        required_passed = sum(1 for r in required_results.values() if r)
        required_total = len(required_results)
        optional_passed = sum(1 for r in optional_results.values() if r)
        optional_total = len(optional_results)

        summary = (
            f"Replication gate evaluation: {required_passed}/{required_total} required criteria, "
            f"{optional_passed}/{optional_total} optional criteria. {reasoning}"
        )

        response = ReplicationResponse(
            gate_run_id=gate_run_id,
            candidate_id=request.candidate_id,
            admission_status=admission_status,
            replication_status=replication_status,
            results=results,
            summary=summary,
            metadata={
                "source_task_id": request.source_task_id,
                "passed_all_required": all(required_results.values()),
                "optional_pass_rate": (
                    optional_passed / optional_total if optional_total > 0 else 1.0
                ),
            },
        )

        # Log the evaluation
        self._log_evaluation(gate_run_id, request, response)

        return response

    def _check_criterion(
        self, criterion, request: ReplicationRequest
    ) -> ReplicationResult:
        """Check a single admission criterion."""
        try:
            if criterion.criterion_id == "schema_validity":
                return self._check_schema_validity(request)
            elif criterion.criterion_id == "lineage_complete":
                return self._check_lineage_completeness(request)
            elif criterion.criterion_id == "governance_context":
                return self._check_governance_context(request)
            elif criterion.criterion_id == "no_live_bypass":
                return self._check_no_live_bypass(request)
            elif criterion.criterion_id == "confidence_score":
                return self._check_confidence_score(request)
            elif criterion.criterion_id == "replication_notes_present":
                return self._check_replication_notes(request)
            elif criterion.criterion_id == "evaluation_hypotheses":
                return self._check_evaluation_hypotheses(request)
            elif criterion.criterion_id == "implementation_ready":
                return self._check_implementation_readiness(request)
            else:
                return ReplicationResult(
                    criterion_id=criterion.criterion_id,
                    passed=False,
                    evidence="Unknown criterion",
                )
        except Exception as e:
            return ReplicationResult(
                criterion_id=criterion.criterion_id,
                passed=False,
                evidence=f"Check failed with error: {str(e)}",
                details={"error": str(e)},
            )

    def _check_schema_validity(self, request: ReplicationRequest) -> ReplicationResult:
        """Check if proposed StrategySpec is valid JSON."""
        try:
            spec = request.proposed_strategy_spec
            if not isinstance(spec, dict):
                return ReplicationResult(
                    criterion_id="schema_validity",
                    passed=False,
                    evidence="StrategySpec must be a JSON object",
                )

            if self._is_canonical_strategy_spec(spec):
                errors = sorted(
                    self._canonical_spec_validator.iter_errors(spec),
                    key=lambda item: list(item.path),
                )
                if errors:
                    first = errors[0]
                    location = ".".join(str(part) for part in first.path) or "<root>"
                    return ReplicationResult(
                        criterion_id="schema_validity",
                        passed=False,
                        evidence=f"Canonical StrategySpec invalid at {location}: {first.message}",
                        details={"schema": "OC-003", "path": list(first.path)},
                    )

                return ReplicationResult(
                    criterion_id="schema_validity",
                    passed=True,
                    evidence="Canonical StrategySpec schema is valid",
                    details={"schema": "OC-003", "strategy_id": spec.get("strategy_id")},
                )

            # Check required fields
            required_fields = ["name", "description", "signals", "parameters"]
            missing = [f for f in required_fields if f not in spec]

            if missing:
                return ReplicationResult(
                    criterion_id="schema_validity",
                    passed=False,
                    evidence=f"Missing required fields: {', '.join(missing)}",
                    details={"missing_fields": missing},
                )

            return ReplicationResult(
                criterion_id="schema_validity",
                passed=True,
                evidence="StrategySpec schema is valid",
                details={"validated_fields": required_fields},
            )
        except Exception as e:
            return ReplicationResult(
                criterion_id="schema_validity",
                passed=False,
                evidence=f"Schema validation failed: {str(e)}",
            )

    def _build_strategy_spec_validator(self) -> Draft7Validator:
        schema_path = (
            Path(__file__).resolve().parents[2]
            / "control-plane"
            / "specs"
            / "strategy_spec.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        resolver = RefResolver(
            base_uri=f"{schema_path.parent.resolve().as_uri()}/",
            referrer=schema,
        )
        return Draft7Validator(schema, resolver=resolver)

    def _is_canonical_strategy_spec(self, spec: Dict[str, Any]) -> bool:
        return any(
            field in spec
            for field in ("spec_version", "strategy_id", "market_scope", "execution_profile")
        )

    def _check_lineage_completeness(self, request: ReplicationRequest) -> ReplicationResult:
        """Check if research handoff has complete lineage information."""
        handoff = request.research_handoff
        if not isinstance(handoff, dict):
            return ReplicationResult(
                criterion_id="lineage_complete",
                passed=False,
                evidence="Research handoff must be a JSON object",
            )

        # Check for source metadata
        source_metadata = handoff.get("source_metadata", {})
        required_metadata = ["api_endpoint", "retrieved_at", "governance_context"]
        missing_metadata = [f for f in required_metadata if f not in source_metadata]

        if missing_metadata:
            return ReplicationResult(
                criterion_id="lineage_complete",
                passed=False,
                evidence=f"Source metadata missing fields: {', '.join(missing_metadata)}",
                details={"missing_metadata": missing_metadata},
            )

        return ReplicationResult(
            criterion_id="lineage_complete",
            passed=True,
            evidence="Research handoff has complete lineage metadata",
            details={"source_metadata_fields": required_metadata},
        )

    def _check_governance_context(self, request: ReplicationRequest) -> ReplicationResult:
        """Check if research includes governance compliance metadata."""
        handoff = request.research_handoff
        grok_notes = handoff.get("grok_processing_notes", {})

        required_governance = [
            "normalization_confidence",
            "governance_compliance",
            "downstream_readiness",
        ]
        missing = [f for f in required_governance if f not in grok_notes]

        if missing:
            return ReplicationResult(
                criterion_id="governance_context",
                passed=False,
                evidence=f"Missing governance metadata: {', '.join(missing)}",
                details={"missing_fields": missing},
            )

        # Check compliance value
        compliance = grok_notes.get("governance_compliance", "")
        if compliance != "verified":
            return ReplicationResult(
                criterion_id="governance_context",
                passed=False,
                evidence=f"Governance compliance not verified: {compliance}",
                details={"compliance_status": compliance},
            )

        return ReplicationResult(
            criterion_id="governance_context",
            passed=True,
            evidence="Governance context verified and complete",
            details={
                "compliance": compliance,
                "confidence": grok_notes.get("normalization_confidence"),
                "readiness": grok_notes.get("downstream_readiness"),
            },
        )

    def _check_no_live_bypass(self, request: ReplicationRequest) -> ReplicationResult:
        """Check that candidate doesn't attempt to bypass promotion gates."""
        spec = request.proposed_strategy_spec

        # Check for suspicious patterns that might indicate bypass attempts
        dangerous_fields = ["live_execution_direct", "skip_promotion_gate", "force_live"]
        found_dangerous = [f for f in dangerous_fields if f in spec]

        if found_dangerous:
            return ReplicationResult(
                criterion_id="no_live_bypass",
                passed=False,
                evidence=f"Found suspicious bypass fields: {', '.join(found_dangerous)}",
                details={"dangerous_fields": found_dangerous},
            )

        # Check lifecycle_state if present
        lifecycle = spec.get("lifecycle_state")
        if lifecycle and lifecycle not in ["draft", "candidate"]:
            return ReplicationResult(
                criterion_id="no_live_bypass",
                passed=False,
                evidence=f"Candidate attempting to set lifecycle_state to {lifecycle}",
                details={"attempted_lifecycle": lifecycle},
            )

        return ReplicationResult(
            criterion_id="no_live_bypass",
            passed=True,
            evidence="No live bypass attempts detected",
        )

    def _check_confidence_score(self, request: ReplicationRequest) -> ReplicationResult:
        """Check research confidence (optional criterion)."""
        handoff = request.research_handoff
        grok_notes = handoff.get("grok_processing_notes", {})
        confidence = grok_notes.get("normalization_confidence", "low")

        # Convert to numeric if possible
        confidence_scores = {"high": 0.9, "medium": 0.7, "low": 0.5}
        score = confidence_scores.get(confidence, 0.5)

        threshold = 0.7
        passed = score >= threshold

        return ReplicationResult(
            criterion_id="confidence_score",
            passed=passed,
            evidence=f"Research confidence: {confidence} (score: {score})",
            details={"confidence_level": confidence, "score": score, "threshold": threshold},
        )

    def _check_replication_notes(self, request: ReplicationRequest) -> ReplicationResult:
        """Check if replication notes are present (optional criterion)."""
        handoff = request.research_handoff
        normalized = handoff.get("normalized_findings", {})
        notes = normalized.get("replication_notes", "")

        passed = bool(notes and len(notes.strip()) > 0)

        return ReplicationResult(
            criterion_id="replication_notes_present",
            passed=passed,
            evidence="Replication notes present" if passed else "Replication notes missing",
            details={"has_notes": passed, "notes_length": len(notes) if notes else 0},
        )

    def _check_evaluation_hypotheses(self, request: ReplicationRequest) -> ReplicationResult:
        """Check if evaluation hypotheses are defined (optional criterion)."""
        handoff = request.research_handoff
        normalized = handoff.get("normalized_findings", {})
        hypotheses = normalized.get("evaluation_hypotheses", "")

        passed = bool(hypotheses and len(hypotheses.strip()) > 0)

        return ReplicationResult(
            criterion_id="evaluation_hypotheses",
            passed=passed,
            evidence=(
                "Evaluation hypotheses defined" if passed else "Evaluation hypotheses missing"
            ),
            details={"has_hypotheses": passed, "hypotheses_length": len(hypotheses) if hypotheses else 0},
        )

    def _check_implementation_readiness(self, request: ReplicationRequest) -> ReplicationResult:
        """Check if research indicates implementation readiness (optional criterion)."""
        handoff = request.research_handoff
        grok_notes = handoff.get("grok_processing_notes", {})
        readiness = grok_notes.get("downstream_readiness", "")

        passed = readiness == "ready_for_replication"

        return ReplicationResult(
            criterion_id="implementation_ready",
            passed=passed,
            evidence=f"Implementation readiness: {readiness}",
            details={"readiness_status": readiness},
        )

    def _log_evaluation(
        self,
        gate_run_id: str,
        request: ReplicationRequest,
        response: ReplicationResponse,
    ):
        """Log evaluation for audit trail."""
        log_entry = {
            "gate_run_id": gate_run_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "candidate_id": request.candidate_id,
            "admission_status": response.admission_status.value,
            "replication_status": response.replication_status.value,
            "required_passed": sum(1 for r in response.results if r.passed and any(
                r.criterion_id == c.criterion_id for c in self.config.get_required_criteria()
            )),
            "optional_passed": sum(1 for r in response.results if r.passed and any(
                r.criterion_id == c.criterion_id for c in self.config.get_optional_criteria()
            )),
        }
        self.gate_run_logs.append(log_entry)

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Get audit log of all evaluations."""
        return self.gate_run_logs


def create_promotion_request(
    response: ReplicationResponse,
    strategy_spec: Dict[str, Any],
    storage_backend: str = "object_store",
) -> Optional[RegistryPromotionRequest]:
    """
    Create a registry promotion request if candidate was admitted.

    Returns:
        RegistryPromotionRequest if admitted, None otherwise
    """
    if not response.passed:
        return None

    # Build registry entry
    registry_entry = {
        "artifact_type": "strategy_spec",
        "lifecycle_state": "candidate",
        "version": "1.0.0",
        "content": strategy_spec,
    }

    # Build replication proof
    replication_proof = {
        "gate_run_id": response.gate_run_id,
        "replication_results": [r.to_dict() for r in response.results],
        "admission_status": response.admission_status.value,
    }

    # Build lineage
    lineage = {
        "source_gate_run": response.gate_run_id,
        "source_candidate_id": response.candidate_id,
        "replication_timestamp": response.timestamp,
    }

    # Determine storage path
    storage_path = f"research/replication/{response.candidate_id}/strategy_spec_v1.0.0.json"

    return RegistryPromotionRequest(
        gate_run_id=response.gate_run_id,
        candidate_id=response.candidate_id,
        registry_entry=registry_entry,
        replication_proof=replication_proof,
        lineage=lineage,
        storage_backend=storage_backend,
        storage_path=storage_path,
    )
