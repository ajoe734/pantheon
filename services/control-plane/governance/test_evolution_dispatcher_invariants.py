"""
Evolution Dispatcher Invariant Tests — P1-EVO-001

Closes PM-GAP-005 (postmortem → EvolutionDecision not verified) and the
EvolutionActionDispatcher gap from SA-17 §12.2.

Invariants verified
-------------------
1. dispatch_approved() rejects any non-approved decision (proposed, reviewed,
   executed, rejected, canceled, superseded).
2. execute() on EvolutionDecision rejects any non-approved state.
3. Live-stage mutations (freeze_live, force_risk_off) require the approved gate
   before dispatch.
4. Postmortem evidence link (linked_postmortem_id) is preserved through the
   dispatch chain without being silently dropped.
5. Single-active-rule: one active decision per target is enforced in the store.

Run:
    python3 -m pytest services/control-plane/governance/test_evolution_dispatcher_invariants.py -v
    python3 services/control-plane/governance/test_evolution_dispatcher_invariants.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from approval_decision import EvidenceRef, EvidenceRefType, RiskLevel
from deployment_plan import RollbackActionType
from evolution_controller import (
    EvolutionController,
    EvolutionControllerError,
    FreezeFollowthroughMode,
)
from evolution_decision import (
    ComparisonOperator,
    EvolutionActionType,
    EvolutionActorRole,
    EvolutionDecision,
    EvolutionDecisionError,
    EvolutionDecisionState,
    EvolutionDecisionStore,
    EvolutionTargetType,
    ExecutionResult,
    ExecutionStatus,
    ExecutionPlane,
    ThresholdSignalType,
    ThresholdSnapshot,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _evidence() -> EvidenceRef:
    return EvidenceRef(
        ref_type=EvidenceRefType.AUDIT_LOG_ENTRY,
        ref_id="inc-inv-001",
        storage_ref={"backend": "incidents_db", "table": "incident_cases"},
    )


def _threshold(
    metric: str = "severity1_incident_count",
    observed: float = 1.0,
) -> ThresholdSnapshot:
    return ThresholdSnapshot(
        policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.5",
        signal_type=ThresholdSignalType.GOVERNANCE_INCIDENT,
        metric_name=metric,
        comparator=ComparisonOperator.GTE,
        observed_value=observed,
        threshold_value=1.0,
        window="incident",
    )


def _proposed(
    *,
    decision_id: str = "evo-inv-001",
    action_type: EvolutionActionType = EvolutionActionType.FREEZE,
    target_stage: str | None = "live",
    linked_postmortem_id: str | None = "pm-inv-001",
    linked_incident_id: str | None = "inc-inv-001",
) -> EvolutionDecision:
    return EvolutionDecision.create_proposed(
        decision_id=decision_id,
        target_type=EvolutionTargetType.CANDIDATE_ARTIFACT,
        target_id="artifact-inv-001",
        target_version="3.0.0",
        action_type=action_type,
        rationale="Invariant test: live mutation guard verification.",
        created_by_id="evolution-controller-test",
        created_by_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
        evidence_refs=[_evidence()],
        threshold_snapshots=[_threshold()],
        linked_postmortem_id=linked_postmortem_id,
        linked_incident_id=linked_incident_id,
        target_stage=target_stage,
    )


def _advance_to_approved(decision: EvolutionDecision) -> EvolutionDecision:
    """Walk the decision through proposed → reviewed → approved."""
    risk = RiskLevel(decision.risk_level)
    if risk == RiskLevel.LOW:
        review_role = EvolutionActorRole.REVIEWER_ON_DUTY
        approve_role = EvolutionActorRole.REVIEWER_ON_DUTY
    elif risk == RiskLevel.MEDIUM:
        review_role = EvolutionActorRole.REVIEWER
        approve_role = EvolutionActorRole.RISK_OWNER
    else:
        review_role = EvolutionActorRole.GOVERNANCE_COMMITTEE
        approve_role = EvolutionActorRole.GOVERNANCE_COMMITTEE

    decision.mark_reviewed(
        actor_role=review_role,
        actor_id="reviewer-test",
        approval_decision_id="appr-inv-001",
        note="Review passed for invariant test.",
    )
    decision.approve(
        actor_role=approve_role,
        actor_id="approver-test",
        note="Approved for invariant test.",
    )
    return decision


def _execution_result(plane: ExecutionPlane = ExecutionPlane.GOVERNANCE) -> ExecutionResult:
    return ExecutionResult(
        status=ExecutionStatus.SUBMITTED,
        plane=plane,
        executed_at="2026-05-01T12:00:00Z",
        execution_ref_id="cmd-inv-001",
        outcome_summary="Invariant test execution.",
    )


# ---------------------------------------------------------------------------
# Invariant 1: dispatch_approved rejects non-approved states
# ---------------------------------------------------------------------------

class TestDispatchRequiresApprovedState(unittest.TestCase):
    """
    P1-EVO-001 acceptance: EvolutionDecision dispatch requires approved state.
    """

    def setUp(self):
        self.controller = EvolutionController()

    def test_proposed_decision_cannot_be_dispatched(self):
        decision = _proposed()
        self.assertEqual(decision.decision_state, EvolutionDecisionState.PROPOSED)
        with self.assertRaises(EvolutionControllerError) as ctx:
            self.controller.dispatch_approved(decision)
        self.assertIn("approved", str(ctx.exception).lower())

    def test_reviewed_decision_cannot_be_dispatched(self):
        decision = _proposed()
        decision.mark_reviewed(
            actor_role=EvolutionActorRole.GOVERNANCE_COMMITTEE,
            actor_id="reviewer",
            approval_decision_id="appr-reviewed",
        )
        self.assertEqual(decision.decision_state, EvolutionDecisionState.REVIEWED)
        with self.assertRaises(EvolutionControllerError) as ctx:
            self.controller.dispatch_approved(decision)
        self.assertIn("approved", str(ctx.exception).lower())

    def test_rejected_decision_cannot_be_dispatched(self):
        decision = _proposed()
        decision.mark_reviewed(
            actor_role=EvolutionActorRole.GOVERNANCE_COMMITTEE,
            actor_id="reviewer",
            approval_decision_id="appr-rej",
        )
        decision.reject(
            actor_role=EvolutionActorRole.GOVERNANCE_COMMITTEE,
            actor_id="committee",
            note="Rejected in test.",
        )
        self.assertEqual(decision.decision_state, EvolutionDecisionState.REJECTED)
        with self.assertRaises(EvolutionControllerError) as ctx:
            self.controller.dispatch_approved(decision)
        self.assertIn("approved", str(ctx.exception).lower())

    def test_canceled_decision_cannot_be_dispatched(self):
        decision = _proposed()
        decision.cancel(
            actor_role=EvolutionActorRole.OPERATOR,
            actor_id="operator",
            note="Canceled in test.",
        )
        self.assertEqual(decision.decision_state, EvolutionDecisionState.CANCELED)
        with self.assertRaises(EvolutionControllerError) as ctx:
            self.controller.dispatch_approved(decision)
        self.assertIn("approved", str(ctx.exception).lower())

    def test_approved_decision_can_be_dispatched(self):
        decision = _proposed()
        _advance_to_approved(decision)
        self.assertEqual(decision.decision_state, EvolutionDecisionState.APPROVED)
        # Should not raise
        outcome = self.controller.dispatch_approved(
            decision,
            has_active_runtime=True,
            freeze_mode=FreezeFollowthroughMode.GOVERNANCE_ONLY,
        )
        self.assertIsNotNone(outcome.primary_command)
        self.assertEqual(outcome.primary_command.decision_id, "evo-inv-001")


# ---------------------------------------------------------------------------
# Invariant 2: execute() on EvolutionDecision rejects non-approved state
# ---------------------------------------------------------------------------

class TestExecuteRequiresApprovedState(unittest.TestCase):
    """
    P1-EVO-001 acceptance: live mutation remains blocked by invariant tests.
    EvolutionDecision.execute() must reject any non-approved decision.
    """

    def _result(self) -> ExecutionResult:
        return _execution_result()

    def test_proposed_decision_execute_raises(self):
        decision = _proposed()
        with self.assertRaises(EvolutionDecisionError) as ctx:
            decision.execute(
                actor_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
                actor_id="controller",
                execution_result=self._result(),
                cooldown_ends_at="2026-05-15T12:00:00Z",
                observation_window_ends_at="2026-05-15T12:00:00Z",
            )
        self.assertIn("approved", str(ctx.exception).lower())

    def test_reviewed_decision_execute_raises(self):
        decision = _proposed()
        decision.mark_reviewed(
            actor_role=EvolutionActorRole.GOVERNANCE_COMMITTEE,
            actor_id="reviewer",
            approval_decision_id="appr-test",
        )
        with self.assertRaises(EvolutionDecisionError) as ctx:
            decision.execute(
                actor_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
                actor_id="controller",
                execution_result=self._result(),
                cooldown_ends_at="2026-05-15T12:00:00Z",
                observation_window_ends_at="2026-05-15T12:00:00Z",
            )
        self.assertIn("approved", str(ctx.exception).lower())

    def test_approved_decision_execute_succeeds(self):
        decision = _proposed()
        _advance_to_approved(decision)
        # Should not raise
        decision.execute(
            actor_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
            actor_id="controller",
            execution_result=self._result(),
            cooldown_ends_at="2026-05-15T12:00:00Z",
            observation_window_ends_at="2026-05-15T12:00:00Z",
        )
        self.assertEqual(decision.decision_state, EvolutionDecisionState.EXECUTED)

    def test_double_execute_raises(self):
        decision = _proposed()
        _advance_to_approved(decision)
        decision.execute(
            actor_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
            actor_id="controller",
            execution_result=self._result(),
            cooldown_ends_at="2026-05-15T12:00:00Z",
            observation_window_ends_at="2026-05-15T12:00:00Z",
        )
        # Attempt to execute again (already executed)
        with self.assertRaises(EvolutionDecisionError):
            decision.execute(
                actor_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
                actor_id="controller",
                execution_result=self._result(),
                cooldown_ends_at="2026-05-20T12:00:00Z",
                observation_window_ends_at="2026-05-20T12:00:00Z",
            )


# ---------------------------------------------------------------------------
# Invariant 3: live mutations require full approval
# ---------------------------------------------------------------------------

class TestLiveMutationApprovalGate(unittest.TestCase):
    """
    P1-EVO-001 acceptance: live mutation remains blocked by invariant tests.

    All live-stage mutations (freeze_live, force_risk_off) require the
    governance-committee approval gate before they can be dispatched.
    """

    def setUp(self):
        self.controller = EvolutionController()

    def test_freeze_live_proposed_cannot_mutate_live(self):
        decision = _proposed(
            action_type=EvolutionActionType.FREEZE,
            target_stage="live",
        )
        with self.assertRaises(EvolutionControllerError):
            self.controller.dispatch_approved(
                decision, has_active_runtime=True,
            )

    def test_freeze_live_approved_can_dispatch(self):
        decision = _proposed(
            action_type=EvolutionActionType.FREEZE,
            target_stage="live",
        )
        _advance_to_approved(decision)
        outcome = self.controller.dispatch_approved(
            decision,
            has_active_runtime=True,
            freeze_mode=FreezeFollowthroughMode.GOVERNANCE_ONLY,
        )
        self.assertEqual(
            outcome.primary_command.execution_plane,
            ExecutionPlane.GOVERNANCE,
        )

    def test_force_risk_off_proposed_cannot_mutate_live(self):
        decision = _proposed(
            action_type=EvolutionActionType.FORCE_RISK_OFF,
            target_stage=None,
        )
        with self.assertRaises(EvolutionControllerError):
            self.controller.dispatch_approved(
                decision, has_active_runtime=True,
            )

    def test_force_risk_off_approved_can_dispatch_with_rollback(self):
        decision = _proposed(
            action_type=EvolutionActionType.FORCE_RISK_OFF,
            target_stage=None,
        )
        _advance_to_approved(decision)
        outcome = self.controller.dispatch_approved(
            decision,
            has_active_runtime=True,
            active_binding_id="binding-live-001",
        )
        self.assertIsNotNone(outcome.rollback_command)
        self.assertEqual(
            outcome.rollback_command.rollback_action_type,
            RollbackActionType.LIQUIDATE_THEN_REPLACE,
        )

    def test_freeze_live_with_rollback_mode_emits_rollback_command(self):
        decision = _proposed(
            action_type=EvolutionActionType.FREEZE,
            target_stage="live",
        )
        _advance_to_approved(decision)
        outcome = self.controller.dispatch_approved(
            decision,
            has_active_runtime=True,
            freeze_mode=FreezeFollowthroughMode.ROLLBACK,
            active_binding_id="binding-live-001",
        )
        self.assertIsNotNone(outcome.rollback_command)
        self.assertEqual(outcome.rollback_command.target_binding_id, "binding-live-001")

    def test_retrain_low_risk_can_be_approved_by_reviewer_on_duty(self):
        decision = _proposed(
            action_type=EvolutionActionType.RETRAIN,
            target_stage=None,
        )
        # Low-risk: reviewer_on_duty can both review and approve
        decision.mark_reviewed(
            actor_role=EvolutionActorRole.REVIEWER_ON_DUTY,
            actor_id="reviewer-duty",
            approval_decision_id="appr-low-001",
        )
        decision.approve(
            actor_role=EvolutionActorRole.REVIEWER_ON_DUTY,
            actor_id="approver-duty",
        )
        self.assertEqual(decision.decision_state, EvolutionDecisionState.APPROVED)
        outcome = self.controller.dispatch_approved(decision)
        self.assertEqual(
            outcome.boundary.execution_plane,
            ExecutionPlane.RESEARCH,
        )


# ---------------------------------------------------------------------------
# Invariant 4: postmortem evidence link preserved through dispatch
# ---------------------------------------------------------------------------

class TestPostmortemEvidenceLinkPreserved(unittest.TestCase):
    """
    PM-GAP-005: postmortem → EvolutionDecision linkage is not silently dropped.

    Verifies that linked_postmortem_id and linked_incident_id survive through
    the full proposed → approved → dispatched lifecycle.
    """

    def setUp(self):
        self.controller = EvolutionController()

    def test_postmortem_link_preserved_in_proposed(self):
        decision = _proposed(
            linked_postmortem_id="pm-inv-001",
            linked_incident_id="inc-inv-001",
        )
        self.assertEqual(decision.linked_postmortem_id, "pm-inv-001")
        self.assertEqual(decision.linked_incident_id, "inc-inv-001")

    def test_postmortem_link_preserved_after_review_and_approval(self):
        decision = _proposed(
            linked_postmortem_id="pm-inv-001",
            linked_incident_id="inc-inv-001",
        )
        _advance_to_approved(decision)
        self.assertEqual(decision.linked_postmortem_id, "pm-inv-001")
        self.assertEqual(decision.linked_incident_id, "inc-inv-001")

    def test_postmortem_link_visible_in_dispatch_metadata(self):
        decision = _proposed(
            linked_postmortem_id="pm-inv-001",
            linked_incident_id="inc-inv-001",
        )
        _advance_to_approved(decision)
        outcome = self.controller.dispatch_approved(
            decision,
            has_active_runtime=False,
            freeze_mode=FreezeFollowthroughMode.GOVERNANCE_ONLY,
        )
        metadata = outcome.primary_command.metadata
        self.assertEqual(metadata.get("linked_postmortem_id"), "pm-inv-001")
        self.assertEqual(metadata.get("linked_incident_id"), "inc-inv-001")

    def test_evolution_decision_requires_at_least_one_evidence_link(self):
        """
        An EvolutionDecision with no evidence refs, no threshold snapshots, and
        no incident/postmortem link must fail validation.
        """
        decision = EvolutionDecision.create_proposed(
            decision_id="evo-no-evidence",
            target_type=EvolutionTargetType.CANDIDATE_ARTIFACT,
            target_id="artifact-001",
            target_version="1.0.0",
            action_type=EvolutionActionType.OBSERVE,
            rationale="Deliberately missing all evidence links.",
            created_by_id="controller",
        )
        errors = decision.validate()
        evidence_error = next(
            (e for e in errors if "evidence" in e.lower()),
            None,
        )
        self.assertIsNotNone(
            evidence_error,
            "Expected an evidence-link validation error, got none. "
            f"All validation errors: {errors}",
        )

    def test_postmortem_link_synced_to_store_on_put(self):
        """
        EvolutionDecisionStore.put() must call incident_store.link_evolution_decision
        when linked_postmortem_id is set.
        """
        # Arrange: mock incident store
        linked_pm_ids: list[str] = []

        class _FakeIncidentStore:
            def link_evolution_decision(self, postmortem_id: str, decision_id: str) -> None:
                linked_pm_ids.append(postmortem_id)

        evo_store = EvolutionDecisionStore(incident_store=_FakeIncidentStore())

        decision = _proposed(
            linked_postmortem_id="pm-sync-001",
            linked_incident_id="inc-sync-001",
        )
        _advance_to_approved(decision)

        evo_store.put(decision)
        self.assertIn("pm-sync-001", linked_pm_ids)


# ---------------------------------------------------------------------------
# Invariant 5: single-active-rule per target
# ---------------------------------------------------------------------------

class TestSingleActiveRuleInvariant(unittest.TestCase):
    """
    Only one active EvolutionDecision may exist per target at a time.
    The store enforces this invariant.
    """

    def _make_and_approve(self, decision_id: str) -> EvolutionDecision:
        decision = _proposed(decision_id=decision_id)
        _advance_to_approved(decision)
        return decision

    def test_two_active_decisions_for_same_target_raises(self):
        store = EvolutionDecisionStore()
        d1 = self._make_and_approve("evo-single-001")
        store.put(d1)

        d2 = _proposed(decision_id="evo-single-002")
        _advance_to_approved(d2)

        with self.assertRaises(EvolutionDecisionError) as ctx:
            store.put(d2)
        self.assertIn("single-active-rule", str(ctx.exception))

    def test_second_active_decision_allowed_after_first_is_executed(self):
        store = EvolutionDecisionStore()
        d1 = self._make_and_approve("evo-seq-001")
        past_result = ExecutionResult(
            status=ExecutionStatus.SUBMITTED,
            plane=ExecutionPlane.GOVERNANCE,
            executed_at="2020-01-01T12:00:00Z",
            execution_ref_id="cmd-past-001",
        )
        d1.execute(
            actor_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
            actor_id="controller",
            execution_result=past_result,
            cooldown_ends_at="2020-01-08T12:00:00Z",
            observation_window_ends_at="2020-01-08T12:00:00Z",
        )
        store.put(d1)

        # d1 is executed and its observation window is in the past → not active
        self.assertFalse(d1.is_active(as_of="2026-05-01T00:00:00Z"))

        d2 = _proposed(decision_id="evo-seq-002")
        _advance_to_approved(d2)
        # Should not raise
        store.put(d2)

    def test_different_targets_allow_simultaneous_active_decisions(self):
        store = EvolutionDecisionStore()

        d1 = EvolutionDecision.create_proposed(
            decision_id="evo-multi-001",
            target_type=EvolutionTargetType.CANDIDATE_ARTIFACT,
            target_id="artifact-A",
            target_version="1.0.0",
            action_type=EvolutionActionType.FREEZE,
            rationale="Target A freeze.",
            created_by_id="controller",
            evidence_refs=[_evidence()],
            target_stage="live",
        )
        _advance_to_approved(d1)
        store.put(d1)

        d2 = EvolutionDecision.create_proposed(
            decision_id="evo-multi-002",
            target_type=EvolutionTargetType.CANDIDATE_ARTIFACT,
            target_id="artifact-B",
            target_version="2.0.0",
            action_type=EvolutionActionType.FREEZE,
            rationale="Target B freeze.",
            created_by_id="controller",
            evidence_refs=[_evidence()],
            target_stage="live",
        )
        _advance_to_approved(d2)
        # Different target_id — should not raise
        store.put(d2)

        self.assertEqual(len(store.list_all()), 2)


if __name__ == "__main__":
    unittest.main()
