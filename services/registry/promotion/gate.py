"""
REG-002: Pantheon Promotion Gate Enforcement.
Ensures artifacts follow governed state transitions and meet metadata requirements.
"""
import logging
from enum import Enum
from typing import Any, Dict, List, Optional

class PromotionState(str, Enum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    PAPER = "paper"
    LIVE = "live"
    RETIRED = "retired"

class PromotionError(Exception):
    """Raised when a promotion requirement is not met."""
    pass

class PromotionGate:
    def __init__(self, logger: logging.Logger = None):
        self.log = logger or logging.getLogger(__name__)

    def validate_transition(self, current_state: PromotionState, target_state: PromotionState):
        """Enforces allowed transitions per REG-001 §3."""
        allowed = {
            PromotionState.DRAFT: [PromotionState.CANDIDATE, PromotionState.RETIRED],
            PromotionState.CANDIDATE: [PromotionState.PAPER, PromotionState.RETIRED],
            PromotionState.PAPER: [PromotionState.LIVE, PromotionState.RETIRED],
            PromotionState.LIVE: [PromotionState.RETIRED],
        }
        
        if target_state not in allowed.get(current_state, []):
            raise PromotionError(f"Forbidden transition: {current_state} -> {target_state}")

    def check_requirements(self, target_state: PromotionState, metadata: Dict[str, Any]):
        """Enforces metadata requirements for each state per TARGET_ARCHITECTURE.md §3."""
        
        if target_state == PromotionState.CANDIDATE:
            if not metadata.get("replication_success"):
                raise PromotionError("Candidate promotion requires 'replication_success' flag.")
            if "source_run_id" not in metadata.get("lineage", {}):
                raise PromotionError("Candidate promotion requires source lineage.")

        elif target_state == PromotionState.PAPER:
            eval_summary = metadata.get("evaluation_summary", {})
            if not eval_summary.get("risk_review_passed"):
                raise PromotionError("Paper promotion requires 'risk_review_passed' in evaluation_summary.")
            if not eval_summary.get("sharpe_ratio"):
                raise PromotionError("Paper promotion requires Sharpe Ratio metric.")

        elif target_state == PromotionState.LIVE:
            if not metadata.get("approver"):
                raise PromotionError("Live promotion requires an explicit 'approver'.")
            if not metadata.get("rollback_target"):
                raise PromotionError("Live promotion requires a defined 'rollback_target'.")
            
            if metadata.get("rollback_target") == metadata.get("version"):
                raise PromotionError("Rollback target cannot be the current version.")

    def promote(self, entry: Dict[str, Any], target_state: PromotionState, approver: Optional[str] = None) -> Dict[str, Any]:
        """
        Attempts to promote a registry entry.
        Returns the updated entry if successful.
        """
        current_state = PromotionState(entry.get("lifecycle_state", "draft"))
        
        self.log.info(f"Attempting promotion: {entry['strategy_id']}@{entry['version']} "
                      f"({current_state} -> {target_state})")

        try:
            self.validate_transition(current_state, target_state)
            
            if approver:
                entry["approver"] = approver
                
            self.check_requirements(target_state, entry)
            
            entry["lifecycle_state"] = target_state.value
            self.log.info(f"Promotion successful: {entry['strategy_id']} is now {target_state.value}")
            
            return entry

        except PromotionError as e:
            self.log.error(f"Promotion rejected: {str(e)}")
            raise