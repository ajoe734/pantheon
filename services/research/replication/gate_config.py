"""
Replication Gate Configuration

Defines the admission criteria and rules that govern whether
a research candidate is admitted to the registry.
"""

from typing import List, Dict, Any, Optional
from .gate_schema import ReplicationCriteria
import json


class GateConfig:
    """Configuration for replication gate criteria and rules."""

    # Hard requirement: ALL required criteria must pass for admission
    ADMISSION_THRESHOLD_REQUIRED = 1.0

    # Soft requirement: at least 80% of optional criteria should pass for strong admission
    ADMISSION_THRESHOLD_OPTIONAL = 0.80

    def __init__(self):
        """Initialize gate with standard admission criteria."""
        self.required_criteria = self._build_required_criteria()
        self.optional_criteria = self._build_optional_criteria()

    def _build_required_criteria(self) -> List[ReplicationCriteria]:
        """Build list of required admission criteria (must all pass)."""
        return [
            ReplicationCriteria(
                criterion_id="schema_validity",
                name="Schema Validity",
                description="Proposed StrategySpec must validate against research schema",
                required=True,
            ),
            ReplicationCriteria(
                criterion_id="lineage_complete",
                name="Lineage Completeness",
                description="Source metadata and lineage must be complete and traceable",
                required=True,
            ),
            ReplicationCriteria(
                criterion_id="governance_context",
                name="Governance Context",
                description="Research handoff must include governance compliance metadata",
                required=True,
            ),
            ReplicationCriteria(
                criterion_id="no_live_bypass",
                name="No Live Bypass",
                description="Candidate must not attempt to bypass registry promotion gates",
                required=True,
            ),
        ]

    def _build_optional_criteria(self) -> List[ReplicationCriteria]:
        """Build list of optional admission criteria (soft requirements)."""
        return [
            ReplicationCriteria(
                criterion_id="confidence_score",
                name="Research Confidence",
                description="Normalized research confidence should be >= 0.7",
                required=False,
            ),
            ReplicationCriteria(
                criterion_id="replication_notes_present",
                name="Replication Notes",
                description="Research handoff should include replication notes",
                required=False,
            ),
            ReplicationCriteria(
                criterion_id="evaluation_hypotheses",
                name="Evaluation Hypotheses",
                description="Research should define evaluation metrics and risk hypotheses",
                required=False,
            ),
            ReplicationCriteria(
                criterion_id="implementation_ready",
                name="Implementation Readiness",
                description="Research should indicate downstream_readiness status",
                required=False,
            ),
        ]

    def get_all_criteria(self) -> List[ReplicationCriteria]:
        """Get all criteria (required + optional)."""
        return self.required_criteria + self.optional_criteria

    def get_required_criteria(self) -> List[ReplicationCriteria]:
        """Get only required criteria."""
        return self.required_criteria

    def get_optional_criteria(self) -> List[ReplicationCriteria]:
        """Get only optional criteria."""
        return self.optional_criteria

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            "admission_threshold_required": self.ADMISSION_THRESHOLD_REQUIRED,
            "admission_threshold_optional": self.ADMISSION_THRESHOLD_OPTIONAL,
            "required_criteria": [c.to_dict() for c in self.required_criteria],
            "optional_criteria": [c.to_dict() for c in self.optional_criteria],
        }

    def to_json(self) -> str:
        """Convert configuration to JSON."""
        return json.dumps(self.to_dict(), indent=2)


# Global gate configuration
DEFAULT_GATE_CONFIG = GateConfig()


class AdmissionRules:
    """Detailed admission rules that map criteria results to final decision."""

    @staticmethod
    def evaluate_admission(
        required_results: Dict[str, bool],
        optional_results: Dict[str, bool],
    ) -> tuple[bool, str]:
        """
        Evaluate admission decision based on criteria results.

        Returns:
            (admitted: bool, reasoning: str)
        """
        # All required criteria must pass
        required_passed = all(required_results.values())
        required_summary = (
            f"{sum(required_results.values())}/{len(required_results)} required criteria passed"
        )

        if not required_passed:
            failed = [k for k, v in required_results.items() if not v]
            return False, f"Failed required criteria: {', '.join(failed)}"

        # Optional criteria: 80% pass rate for strong admission
        if optional_results:
            optional_passed = sum(optional_results.values())
            optional_total = len(optional_results)
            optional_pass_rate = optional_passed / optional_total if optional_total > 0 else 1.0

            if optional_pass_rate < GateConfig.ADMISSION_THRESHOLD_OPTIONAL:
                return False, (
                    f"Insufficient optional criteria pass rate: "
                    f"{optional_passed}/{optional_total} (need {GateConfig.ADMISSION_THRESHOLD_OPTIONAL})"
                )

        # All checks passed
        return True, f"{required_summary} and {sum(optional_results.values())}/{len(optional_results)} optional criteria passed"

    @staticmethod
    def get_admission_summary(admitted: bool, reasoning: str) -> Dict[str, Any]:
        """Generate admission summary for documentation."""
        return {
            "admitted": admitted,
            "reasoning": reasoning,
            "next_step": (
                "Candidate ready for registry admission via REG-001"
                if admitted
                else "Request clarification or reject from research intake"
            ),
        }
