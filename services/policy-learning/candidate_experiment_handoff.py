"""Policy-learning candidate handoff module.

Hands off processed shadow imitation candidates from policy-learning to the
Research experiment authority (services/research/experiment_candidate_intake.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.research.experiment_candidate_intake import (
    ExperimentCandidateIntakeReceipt,
    intake_imitation_candidate,
)
from services.research.store import ResearchOrchestratorStore


class CandidateHandoffError(ValueError):
    """Raised when a candidate cannot be handed off to Research."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CandidateHandoffResult:
    candidate_id: str
    experiment_task_id: str
    experiment_run_id: str
    status: str
    handoff_at: str
    receipt: ExperimentCandidateIntakeReceipt

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "experiment_task_id": self.experiment_task_id,
            "experiment_run_id": self.experiment_run_id,
            "status": self.status,
            "handoff_at": self.handoff_at,
            "receipt": self.receipt.to_dict(),
        }


def handoff_candidate_to_experiment_authority(
    candidate: Dict[str, Any],
    *,
    research_store: Optional[ResearchOrchestratorStore] = None,
    timestamp: Optional[str] = None,
) -> CandidateHandoffResult:
    """Hand off a processed imitation candidate to Research experiment authority.

    Updates the candidate dict in-place with:
    - experiment_task_id
    - experiment_run_id
    - handoff_status: "completed"
    - handoff_at: timestamp

    Returns CandidateHandoffResult.
    """
    candidate_status = str(candidate.get("status") or "").lower()
    if candidate_status != "processed":
        raise CandidateHandoffError(
            f"Candidate must be in 'processed' status before experiment handoff (got: '{candidate_status}')"
        )

    candidate_id = str(candidate.get("candidate_id") or candidate.get("id") or "").strip()
    if not candidate_id:
        raise CandidateHandoffError("candidate_id is required for handoff")

    now = timestamp or _utc_now_iso()

    # Perform intake into Research
    receipt = intake_imitation_candidate(candidate, store=research_store, timestamp=now)

    # Attach receipt to candidate
    candidate["experiment_task_id"] = receipt.task_id
    candidate["experiment_run_id"] = receipt.run_id
    candidate["handoff_status"] = "completed"
    candidate["handoff_at"] = now

    return CandidateHandoffResult(
        candidate_id=candidate_id,
        experiment_task_id=receipt.task_id,
        experiment_run_id=receipt.run_id,
        status="completed",
        handoff_at=now,
        receipt=receipt,
    )
