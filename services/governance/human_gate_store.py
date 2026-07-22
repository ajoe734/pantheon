"""Canonical owner-store adapter for governed human-gate decisions."""
from __future__ import annotations

from services.governance.human_gate.decision_model import (
    HumanGateDecision,
    validate_decision,
)
from services.governance.promotion_readiness.signoff_api import SignoffApiError
from services.governance.record_store import GovernanceRecordStore


class GovernanceHumanGateDecisionStore:
    """Persist ``HumanGateDecision`` through Governance's configured backend.

    The signoff model was originally backed by a task-local JSON helper.  This
    adapter keeps its API but routes every record through Governance's owner
    store, so dev gets atomic JSON persistence and enforced environments get
    the existing Postgres JSONB posture.
    """

    def __init__(self, records: GovernanceRecordStore) -> None:
        self._records = records

    def create(self, decision: HumanGateDecision) -> HumanGateDecision:
        normalized = validate_decision(decision)
        inserted, _ = self._records.insert_if_absent(normalized.to_dict())
        if not inserted:
            raise SignoffApiError(
                f"decision already exists: {normalized.decision_id}"
            )
        return normalized

    def put(self, decision: HumanGateDecision) -> HumanGateDecision:
        normalized = validate_decision(decision)
        self._records.put(normalized.to_dict())
        return normalized

    def put_if_matches(
        self,
        expected: HumanGateDecision,
        decision: HumanGateDecision,
    ) -> HumanGateDecision:
        normalized = validate_decision(decision)
        updated, _ = self._records.compare_and_set(
            expected.to_dict(), normalized.to_dict()
        )
        if not updated:
            raise SignoffApiError(
                "human gate changed concurrently; read the canonical decision and retry"
            )
        return normalized

    def get(self, decision_id: str) -> HumanGateDecision | None:
        record = self._records.get(decision_id)
        return HumanGateDecision.from_dict(record) if record is not None else None

    def require(self, decision_id: str) -> HumanGateDecision:
        decision = self.get(decision_id)
        if decision is None:
            raise SignoffApiError(f"decision not found: {decision_id}")
        return decision


__all__ = ["GovernanceHumanGateDecisionStore"]
