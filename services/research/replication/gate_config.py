"""
Replication Gate Configuration

Defines the admission criteria and rules that govern whether
a research candidate is admitted to the registry.
"""

from typing import List, Dict, Any, Optional
from .gate_schema import ReplicationCriteria
import json
import math


class GateConfig:
    """Configuration for replication gate criteria and rules."""

    # Hard requirement: ALL required criteria must pass for admission
    ADMISSION_THRESHOLD_REQUIRED = 1.0

    # Soft requirement: at least 80% of optional criteria should pass for strong admission
    ADMISSION_THRESHOLD_OPTIONAL = 0.80

    # Multiple-testing and out-of-sample backtest admission parameters (§10.2)
    DEFAULT_BASE_OOS_SHARPE_THRESHOLD = 0.50
    DEFAULT_TRIAL_PENALTY_SCALE = 0.25
    DEFAULT_MIN_OOS_ROUND_TRIPS = 20
    DEFAULT_MANDATE_MAX_DRAWDOWN = 0.25
    DEFAULT_MIN_PIT_HISTORY_DAYS = 730
    DEFAULT_MIN_PIT_HISTORY_BARS = 480
    DEFAULT_IN_SAMPLE_RATIO = 0.70
    DEFAULT_OUT_OF_SAMPLE_RATIO = 0.30
    DEFAULT_WALK_FORWARD_FOLDS = 3
    # SD_STRATEGY_SCREEN_ENTRY_EXIT §4: a screen is admitted only when the screened
    # version beats the same entry/exit rules over the whole boundary out of sample
    # after costs by strictly more than this Sharpe margin.
    DEFAULT_MIN_SCREEN_OOS_SHARPE_IMPROVEMENT = 0.0

    def __init__(
        self,
        *,
        base_oos_sharpe_threshold: float = DEFAULT_BASE_OOS_SHARPE_THRESHOLD,
        trial_penalty_scale: float = DEFAULT_TRIAL_PENALTY_SCALE,
        min_oos_round_trips: int = DEFAULT_MIN_OOS_ROUND_TRIPS,
        default_mandate_max_drawdown: float = DEFAULT_MANDATE_MAX_DRAWDOWN,
        min_pit_history_days: int = DEFAULT_MIN_PIT_HISTORY_DAYS,
        min_pit_history_bars: int = DEFAULT_MIN_PIT_HISTORY_BARS,
        in_sample_ratio: float = DEFAULT_IN_SAMPLE_RATIO,
        out_of_sample_ratio: float = DEFAULT_OUT_OF_SAMPLE_RATIO,
        walk_forward_folds: int = DEFAULT_WALK_FORWARD_FOLDS,
        min_screen_oos_sharpe_improvement: float = DEFAULT_MIN_SCREEN_OOS_SHARPE_IMPROVEMENT,
    ):
        """Initialize gate with standard admission criteria."""
        self.base_oos_sharpe_threshold = float(base_oos_sharpe_threshold)
        self.trial_penalty_scale = float(trial_penalty_scale)
        self.min_oos_round_trips = int(min_oos_round_trips)
        self.default_mandate_max_drawdown = float(default_mandate_max_drawdown)
        self.max_oos_drawdown = float(default_mandate_max_drawdown)
        self.min_pit_history_days = int(min_pit_history_days)
        self.min_pit_history_bars = int(min_pit_history_bars)
        self.min_history_calendar_days = int(min_pit_history_days)
        self.min_history_trading_days = int(min_pit_history_bars)
        self.in_sample_ratio = float(in_sample_ratio)
        self.out_of_sample_ratio = float(out_of_sample_ratio)
        self.walk_forward_folds = int(walk_forward_folds)
        self.min_screen_oos_sharpe_improvement = float(min_screen_oos_sharpe_improvement)
        self.required_criteria = self._build_required_criteria()
        self.optional_criteria = self._build_optional_criteria()

    def compute_deflated_sharpe_threshold(self, trial_count: int) -> Dict[str, Any]:
        """Compute the trial-deflated Sharpe admission threshold and multiple-testing haircut.

        Every evaluated variant for a hypothesis/template family increments ``trial_count``.
        The required out-of-sample Sharpe threshold increases by ``penalty_scale * sqrt(2 * ln(N))``
        (equivalently deflating the candidate's observed Sharpe by ``haircut``).
        """
        n = max(1, int(trial_count))
        haircut = (
            self.trial_penalty_scale * math.sqrt(2.0 * math.log(float(n)))
            if n > 1
            else 0.0
        )
        threshold = self.base_oos_sharpe_threshold + haircut
        return {
            "trial_count": n,
            "base_sharpe_threshold": round(self.base_oos_sharpe_threshold, 6),
            "sharpe_haircut": round(haircut, 6),
            "deflated_sharpe_threshold": round(threshold, 6),
        }

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
            ReplicationCriteria(
                criterion_id="real_backtest_validation",
                name="Real Taiwan Backtest Validation",
                description=(
                    "Candidate must pass a real backtest on governed Taiwan market data "
                    "(70% IS / 30% OOS / 3 walk-forward folds, statutory commission and tax "
                    "on every fill, benchmark comparison, and trial-deflated Sharpe threshold)"
                ),
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
