"""
Unit tests for EvolutionDecision governance contract.

Run:
    python3 -m unittest discover -s services/control-plane/governance -p 'test_*.py'
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from services.incident.incident import IncidentCase, IncidentStore, Postmortem

from evolution_decision import (
    ComparisonOperator,
    ExecutionPlane,
    ExecutionResult,
    ExecutionStatus,
    EvolutionActionType,
    EvolutionActorRole,
    EvolutionDecision,
    EvolutionDecisionError,
    EvolutionDecisionState,
    EvolutionDecisionStore,
    EvolutionTargetType,
    ReviewStepType,
    RiskLevel,
    ThresholdSignalType,
    ThresholdSnapshot,
    infer_risk_level,
    to_audit_event,
    validate_evolution_decision,
    validate_evolution_decision_json,
)
from approval_decision import EvidenceRef, EvidenceRefType


def make_threshold() -> ThresholdSnapshot:
    return ThresholdSnapshot(
        policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md#7.5",
        signal_type=ThresholdSignalType.GOVERNANCE_INCIDENT,
        metric_name="severity1_incident_count",
        comparator=ComparisonOperator.GTE,
        observed_value=1,
        threshold_value=1,
        window="30d",
        note="Severity-1 incident triggered governance path",
    )


def make_evidence() -> EvidenceRef:
    return EvidenceRef(
        ref_type=EvidenceRefType.TELEMETRY_SUMMARY,
        ref_id="telemetry-sum-001",
        storage_ref={"backend": "object_store", "path": "/telemetry/summary/001"},
    )


def make_incident_store() -> IncidentStore:
    store = IncidentStore()
    store.create_incident(
        IncidentCase(
            incident_id="inc-001",
            title="Live strategy incident",
            status="open",
            severity="critical",
            created_at="2026-04-10T01:00:00Z",
            binding_id="binding-001",
            deployment_stage="live",
            deployment_plan_id="plan-001",
            capital_pool_id="pool-001",
            persona_capital_binding_id="pcb-001",
            artifact_id="artifact-001",
            artifact_version="1.2.3",
            runtime_id="runtime-001",
            trace_id="trace-001",
        )
    )
    store.create_postmortem(
        Postmortem(
            postmortem_id="pm-001",
            title="Live strategy incident postmortem",
            status="draft",
            created_at="2026-04-10T03:00:00Z",
            incident_id="inc-001",
            binding_id="binding-001",
            deployment_stage="live",
            deployment_plan_id="plan-001",
            capital_pool_id="pool-001",
            persona_capital_binding_id="pcb-001",
            artifact_id="artifact-001",
            artifact_version="1.2.3",
            runtime_id="runtime-001",
            trace_id="trace-001",
            root_cause="Loader mismatch caused unsafe execution.",
        )
    )
    return store


def make_decision(**overrides) -> EvolutionDecision:
    defaults = dict(
        decision_id="evo-001",
        target_type=EvolutionTargetType.CANDIDATE_ARTIFACT,
        target_id="artifact-001",
        target_version="1.2.3",
        action_type=EvolutionActionType.FREEZE,
        rationale="Freeze live artifact after severity-1 incident.",
        created_by_id="evolution-controller-01",
        created_by_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
        target_stage="live",
        evidence_refs=[make_evidence()],
        threshold_snapshots=[make_threshold()],
        linked_postmortem_id="pm-001",
        linked_incident_id="inc-001",
        capital_pool_id="pool-001",
        persona_id="persona-ops",
    )
    defaults.update(overrides)
    return EvolutionDecision.create_proposed(**defaults)


class TestRiskNormalization(unittest.TestCase):
    def test_retrain_is_low_risk(self):
        self.assertEqual(infer_risk_level(EvolutionActionType.RETRAIN), RiskLevel.LOW)

    def test_freeze_live_is_high_risk(self):
        self.assertEqual(
            infer_risk_level(EvolutionActionType.FREEZE, target_stage="live"),
            RiskLevel.HIGH,
        )

    def test_freeze_canary_is_medium_risk(self):
        self.assertEqual(
            infer_risk_level(EvolutionActionType.FREEZE, target_stage="canary"),
            RiskLevel.MEDIUM,
        )


class TestEvolutionDecisionLifecycle(unittest.TestCase):
    def test_create_proposed(self):
        decision = make_decision()
        self.assertEqual(decision.decision_state, EvolutionDecisionState.PROPOSED)
        self.assertEqual(decision.risk_level, RiskLevel.HIGH)
        self.assertEqual(len(decision.threshold_snapshots), 1)

    def test_mark_reviewed_requires_authorized_role(self):
        decision = make_decision()
        with self.assertRaises(EvolutionDecisionError):
            decision.mark_reviewed(
                EvolutionActorRole.REVIEWER_ON_DUTY,
                "reviewer-01",
                "approval-001",
            )

    def test_mark_reviewed_then_approve(self):
        decision = make_decision()
        decision.mark_reviewed(
            EvolutionActorRole.GOVERNANCE_COMMITTEE,
            "committee-01",
            "approval-001",
            note="Committee accepted the case.",
        )
        self.assertEqual(decision.decision_state, EvolutionDecisionState.REVIEWED)
        self.assertEqual(decision.review_chain[-1].step_type, ReviewStepType.REVIEWED)

        decision.approve(
            EvolutionActorRole.GOVERNANCE_COMMITTEE,
            "committee-01",
            note="Approved freeze path.",
        )
        self.assertEqual(decision.decision_state, EvolutionDecisionState.APPROVED)
        self.assertEqual(decision.review_chain[-1].step_type, ReviewStepType.APPROVED)

    def test_execute_requires_cooldown_windows(self):
        decision = make_decision()
        decision.mark_reviewed(
            EvolutionActorRole.GOVERNANCE_COMMITTEE,
            "committee-01",
            "approval-001",
        )
        decision.approve(EvolutionActorRole.GOVERNANCE_COMMITTEE, "committee-01")
        decision.execute(
            EvolutionActorRole.EVOLUTION_CONTROLLER,
            "evolution-controller-01",
            ExecutionResult(
                status=ExecutionStatus.SUCCEEDED,
                plane=ExecutionPlane.RUNTIME,
                executed_at="2026-04-10T05:00:00Z",
                execution_ref_id="freeze-order-001",
                outcome_summary="Freeze order confirmed terminal by runtime manager.",
            ),
            cooldown_ends_at="2026-04-17T05:00:00Z",
            observation_window_ends_at="2026-04-24T05:00:00Z",
        )
        self.assertEqual(decision.decision_state, EvolutionDecisionState.EXECUTED)
        self.assertTrue(decision.is_active(as_of="2026-04-12T00:00:00Z"))
        self.assertFalse(decision.is_active(as_of="2026-04-25T00:00:00Z"))

    def test_execute_rejects_unrecognized_role_cleanly(self):
        """EVOCHAIN-005 round 2: an actor_role the enum doesn't recognize (e.g.
        the BFF's "admin" label, which has no EvolutionActorRole member) must
        raise a domain EvolutionDecisionError, not an unhandled ValueError
        from EvolutionActorRole("admin") that would surface as a 500."""
        decision = make_decision()
        decision.mark_reviewed(
            EvolutionActorRole.GOVERNANCE_COMMITTEE,
            "committee-01",
            "approval-001",
        )
        decision.approve(EvolutionActorRole.GOVERNANCE_COMMITTEE, "committee-01")
        with self.assertRaises(EvolutionDecisionError):
            decision.execute(
                "admin",
                "admin-01",
                ExecutionResult(
                    status=ExecutionStatus.SUCCEEDED,
                    plane=ExecutionPlane.RUNTIME,
                    executed_at="2026-04-10T05:00:00Z",
                    execution_ref_id="freeze-order-001",
                    outcome_summary="Freeze order confirmed terminal by runtime manager.",
                ),
                cooldown_ends_at="2026-04-17T05:00:00Z",
                observation_window_ends_at="2026-04-24T05:00:00Z",
            )
        # Decision state must be unchanged — the rejected role never mutated it.
        self.assertEqual(decision.decision_state, EvolutionDecisionState.APPROVED)

    def test_reject_allows_review_owner_for_medium(self):
        decision = make_decision(
            action_type=EvolutionActionType.MUTATE_PERSONA_ROUTE_POLICY,
            target_stage=None,
            linked_postmortem_id=None,
            linked_incident_id="inc-001",
        )
        decision.mark_reviewed(
            EvolutionActorRole.REVIEWER,
            "reviewer-01",
            "approval-002",
        )
        decision.reject(
            EvolutionActorRole.REVIEWER,
            "reviewer-01",
            note="Route policy mutation rejected pending more evidence.",
        )
        self.assertEqual(decision.decision_state, EvolutionDecisionState.REJECTED)


class TestEvolutionDecisionValidation(unittest.TestCase):
    def test_valid_executed_decision_has_no_errors(self):
        decision = make_decision()
        decision.mark_reviewed(
            EvolutionActorRole.GOVERNANCE_COMMITTEE,
            "committee-01",
            "approval-001",
        )
        decision.approve(EvolutionActorRole.GOVERNANCE_COMMITTEE, "committee-01")
        decision.execute(
            EvolutionActorRole.EVOLUTION_CONTROLLER,
            "evolution-controller-01",
            ExecutionResult(
                status=ExecutionStatus.SUCCEEDED,
                plane=ExecutionPlane.RUNTIME,
                executed_at="2026-04-10T05:00:00Z",
                execution_ref_id="freeze-order-001",
                outcome_summary="Runtime entered frozen state.",
            ),
            cooldown_ends_at="2026-04-17T05:00:00Z",
            observation_window_ends_at="2026-04-24T05:00:00Z",
        )
        self.assertEqual(validate_evolution_decision(decision), [])

    def test_missing_evidence_is_invalid(self):
        decision = make_decision(
            evidence_refs=[],
            threshold_snapshots=[],
            linked_postmortem_id=None,
            linked_incident_id=None,
        )
        errors = validate_evolution_decision(decision)
        self.assertTrue(any("At least one evidence link" in error for error in errors))

    def test_executed_requires_execution_result(self):
        decision = make_decision()
        decision.mark_reviewed(
            EvolutionActorRole.GOVERNANCE_COMMITTEE,
            "committee-01",
            "approval-001",
        )
        decision.approve(EvolutionActorRole.GOVERNANCE_COMMITTEE, "committee-01")
        decision.decision_state = EvolutionDecisionState.EXECUTED
        decision.cooldown_started_at = "2026-04-10T05:00:00Z"
        decision.cooldown_ends_at = "2026-04-17T05:00:00Z"
        decision.observation_window_started_at = "2026-04-10T05:00:00Z"
        decision.observation_window_ends_at = "2026-04-24T05:00:00Z"
        errors = validate_evolution_decision(decision)
        self.assertTrue(any("execution_result is required" in error for error in errors))

    def test_json_validation_rejects_freeze_without_stage(self):
        data = make_decision().to_dict()
        data["target_stage"] = None
        errors = validate_evolution_decision_json(data)
        self.assertTrue(any("freeze action_type requires target_stage" in error for error in errors))


class TestEvolutionDecisionStore(unittest.TestCase):
    def test_postgres_write_uses_cross_replica_table_fence(self):
        connection = MagicMock()
        rows = MagicMock()
        rows.fetchall.return_value = []
        connection.execute.side_effect = [None, rows, None]
        connection_context = MagicMock()
        connection_context.__enter__.return_value = connection

        postgres = MagicMock()
        postgres.table = '"evolution"."decisions"'
        postgres._connect.return_value = connection_context

        with patch(
            "evolution_decision.PostgresJsonOwnerStore",
            return_value=postgres,
        ):
            store = EvolutionDecisionStore(
                backend="postgres",
                dsn="postgresql://evolution.test/pantheon",
            )
            store.put(make_decision())

        statements = [str(call.args[0]) for call in connection.execute.call_args_list]
        self.assertTrue(
            any("LOCK TABLE" in statement for statement in statements),
            "the active-decision check must be fenced across service replicas",
        )
        self.assertTrue(any("INSERT INTO" in statement for statement in statements))

    def test_postgres_fence_enforces_active_uniqueness_per_tenant(self):
        existing = make_decision(decision_id="evo-existing").to_dict()
        connection = MagicMock()
        rows = MagicMock()
        rows.fetchall.return_value = [("evo-existing", existing)]
        connection.execute.side_effect = [None, rows]
        connection_context = MagicMock()
        connection_context.__enter__.return_value = connection

        postgres = MagicMock()
        postgres.table = '"evolution"."decisions"'
        postgres._connect.return_value = connection_context
        postgres._decode_payload.side_effect = lambda payload: payload

        with patch(
            "evolution_decision.PostgresJsonOwnerStore",
            return_value=postgres,
        ):
            store = EvolutionDecisionStore(
                backend="postgres",
                dsn="postgresql://evolution.test/pantheon",
            )
            with self.assertRaisesRegex(
                EvolutionDecisionError,
                "already has an active EvolutionDecision",
            ):
                store.put(make_decision(decision_id="evo-competing"))

        self.assertFalse(
            any(
                "INSERT INTO" in str(call.args[0])
                for call in connection.execute.call_args_list
            )
        )

    def test_postgres_active_uniqueness_is_tenant_scoped(self):
        existing = make_decision(
            decision_id="evo-existing",
            tenant_id="tenant-a",
        ).to_dict()
        connection = MagicMock()
        rows = MagicMock()
        rows.fetchall.return_value = [("evo-existing", existing)]
        connection.execute.side_effect = [None, rows, None]
        connection_context = MagicMock()
        connection_context.__enter__.return_value = connection

        postgres = MagicMock()
        postgres.table = '"evolution"."decisions"'
        postgres._connect.return_value = connection_context
        postgres._decode_payload.side_effect = lambda payload: payload

        with patch(
            "evolution_decision.PostgresJsonOwnerStore",
            return_value=postgres,
        ):
            store = EvolutionDecisionStore(
                backend="postgres",
                dsn="postgresql://evolution.test/pantheon",
            )
            store.put(
                make_decision(
                    decision_id="evo-other-tenant",
                    tenant_id="tenant-b",
                )
            )

        self.assertTrue(
            any(
                "INSERT INTO" in str(call.args[0])
                for call in connection.execute.call_args_list
            )
        )

    def test_single_active_rule_blocks_parallel_target(self):
        store = EvolutionDecisionStore()
        first = make_decision(decision_id="evo-001")
        store.put(first)
        second = make_decision(decision_id="evo-002")
        with self.assertRaises(EvolutionDecisionError):
            store.put(second)

    def test_superseded_previous_allows_new_active_decision(self):
        store = EvolutionDecisionStore()
        first = make_decision(decision_id="evo-001")
        store.put(first)
        first.supersede("evo-002")
        store.put(first)
        second = make_decision(decision_id="evo-002")
        store.put(second)
        self.assertEqual(len(store.find_active_by_target(first.target_type, first.target_id)), 1)

    def test_postmortem_reverse_link_is_synced(self):
        incident_store = make_incident_store()
        store = EvolutionDecisionStore(incident_store=incident_store)
        decision = make_decision()
        store.put(decision)
        postmortem = incident_store.require_postmortem("pm-001")
        self.assertEqual(postmortem.linked_evolution_decision_id, "evo-001")

    def test_store_persistence_roundtrip(self):
        decision = make_decision()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
            tmp_path = handle.name
        try:
            store = EvolutionDecisionStore(tmp_path)
            store.put(decision)
            loaded = EvolutionDecisionStore(tmp_path)
            restored = loaded.get("evo-001")
            self.assertIsNotNone(restored)
            self.assertEqual(restored.action_type, EvolutionActionType.FREEZE.value)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_two_json_instances_preserve_concurrent_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "decisions.json")
            first_store = EvolutionDecisionStore(path)
            second_store = EvolutionDecisionStore(path)

            def write(index: int) -> None:
                selected = first_store if index % 2 == 0 else second_store
                selected.put(
                    make_decision(
                        decision_id=f"evo-concurrent-{index:02d}",
                        target_id=f"artifact-concurrent-{index:02d}",
                        linked_postmortem_id=f"pm-concurrent-{index:02d}",
                    )
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(write, range(24)))

            restarted = EvolutionDecisionStore(path)
            self.assertEqual(len(restarted.list_all()), 24)
            self.assertEqual(
                {decision.decision_id for decision in restarted.list_all()},
                {f"evo-concurrent-{index:02d}" for index in range(24)},
            )

    def test_atomic_write_failure_rolls_back_memory_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "decisions.json")
            store = EvolutionDecisionStore(path)
            original = make_decision(
                decision_id="evo-durable-original",
                target_id="artifact-durable-original",
            )
            rejected = make_decision(
                decision_id="evo-write-failed",
                target_id="artifact-write-failed",
            )
            store.put(original)

            with patch("evolution_decision.os.replace", side_effect=OSError("disk unavailable")):
                with self.assertRaisesRegex(OSError, "disk unavailable"):
                    store.put(rejected)

            self.assertIsNotNone(store.get(original.decision_id))
            self.assertIsNone(store.get(rejected.decision_id))
            restarted = EvolutionDecisionStore(path)
            self.assertIsNotNone(restarted.get(original.decision_id))
            self.assertIsNone(restarted.get(rejected.decision_id))

    def test_stale_instance_cannot_overwrite_a_concurrent_decision_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "decisions.json")
            first_store = EvolutionDecisionStore(path)
            second_store = EvolutionDecisionStore(path)
            first_store.put(make_decision())

            first_update = first_store.get("evo-001")
            stale_update = second_store.get("evo-001")
            self.assertIsNotNone(first_update)
            self.assertIsNotNone(stale_update)
            first_update.rationale = "First writer committed a reviewed rationale."
            stale_update.rationale = "Stale writer must not replace that rationale."

            first_store.put(first_update)
            with self.assertRaisesRegex(
                EvolutionDecisionError,
                "concurrent modification detected",
            ):
                second_store.put(stale_update)

            restarted = EvolutionDecisionStore(path)
            self.assertEqual(
                restarted.get("evo-001").rationale,
                "First writer committed a reviewed rationale.",
            )


class TestAuditEvent(unittest.TestCase):
    def test_audit_event_uses_latest_actor(self):
        decision = make_decision()
        decision.mark_reviewed(
            EvolutionActorRole.GOVERNANCE_COMMITTEE,
            "committee-01",
            "approval-001",
        )
        event = to_audit_event(decision, "evolution_decision_reviewed")
        self.assertEqual(event["actor_id"], "committee-01")
        self.assertEqual(event["actor_role"], EvolutionActorRole.GOVERNANCE_COMMITTEE.value)
