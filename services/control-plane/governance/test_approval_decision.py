"""
Unit tests for ApprovalDecision governance contract.

Run:
    python3 -m unittest discover -s services/control-plane/governance -p 'test_*.py'
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from approval_decision import (
    ActorRole,
    ApprovalDecision,
    ApprovalDecisionStore,
    DecisionOutcome,
    DecisionState,
    EvidenceRef,
    EvidenceRefType,
    OwnerMatrix,
    RiskLevel,
    TargetType,
    to_audit_event,
    validate_decision,
    validate_decision_json,
)

GOVERNANCE_DIR = Path(__file__).resolve().parent


class TestOwnerMatrix(unittest.TestCase):
    """Test risk-level to actor-role authorization matrix."""

    def test_low_risk_allows_reviewer_and_automated(self):
        self.assertTrue(OwnerMatrix.is_authorized(ActorRole.GOVERNANCE_REVIEWER, RiskLevel.LOW))
        self.assertTrue(OwnerMatrix.is_authorized(ActorRole.AUTOMATED_GATE, RiskLevel.LOW))
        self.assertFalse(OwnerMatrix.is_authorized(ActorRole.RISK_OWNER, RiskLevel.LOW))

    def test_medium_risk_allows_reviewer_and_risk_owner(self):
        self.assertTrue(OwnerMatrix.is_authorized(ActorRole.GOVERNANCE_REVIEWER, RiskLevel.MEDIUM))
        self.assertTrue(OwnerMatrix.is_authorized(ActorRole.RISK_OWNER, RiskLevel.MEDIUM))
        self.assertFalse(OwnerMatrix.is_authorized(ActorRole.AUTOMATED_GATE, RiskLevel.MEDIUM))

    def test_high_risk_allows_risk_owner_and_committee(self):
        self.assertTrue(OwnerMatrix.is_authorized(ActorRole.RISK_OWNER, RiskLevel.HIGH))
        self.assertTrue(OwnerMatrix.is_authorized(ActorRole.GOVERNANCE_COMMITTEE, RiskLevel.HIGH))
        self.assertFalse(OwnerMatrix.is_authorized(ActorRole.GOVERNANCE_REVIEWER, RiskLevel.HIGH))

    def test_critical_only_committee(self):
        self.assertTrue(OwnerMatrix.is_authorized(ActorRole.GOVERNANCE_COMMITTEE, RiskLevel.CRITICAL))
        self.assertFalse(OwnerMatrix.is_authorized(ActorRole.RISK_OWNER, RiskLevel.CRITICAL))

    def test_minimum_roles_for(self):
        roles = OwnerMatrix.minimum_roles_for(RiskLevel.HIGH)
        self.assertIn(ActorRole.RISK_OWNER, roles)
        self.assertIn(ActorRole.GOVERNANCE_COMMITTEE, roles)
        self.assertEqual(len(roles), 2)


class TestApprovalDecisionCreation(unittest.TestCase):
    """Test ApprovalDecision factory and lifecycle methods."""

    def test_create_proposed(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        self.assertEqual(d.decision_id, "approval-001")
        self.assertEqual(d.decision_state, DecisionState.PROPOSED)
        self.assertEqual(d.risk_level, RiskLevel.LOW)
        self.assertIsNone(d.decision)
        self.assertIsNone(d.actor_role)
        self.assertIsNone(d.actor_id)
        self.assertIsNone(d.rationale)
        self.assertIsNone(d.decided_at)

    def test_accept_review(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        d.accept_review(ActorRole.GOVERNANCE_REVIEWER, "reviewer-01")
        self.assertEqual(d.decision_state, DecisionState.UNDER_REVIEW)
        self.assertEqual(d.actor_role, ActorRole.GOVERNANCE_REVIEWER)
        self.assertEqual(d.actor_id, "reviewer-01")

    def test_accept_review_unauthorized_role(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
            risk_level=RiskLevel.HIGH,
        )
        with self.assertRaises(ValueError):
            # governance_reviewer not authorized for HIGH risk
            d.accept_review(ActorRole.GOVERNANCE_REVIEWER, "reviewer-01")

    def test_accept_review_wrong_state(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        d.accept_review(ActorRole.GOVERNANCE_REVIEWER, "reviewer-01")
        with self.assertRaises(ValueError):
            d.accept_review(ActorRole.GOVERNANCE_REVIEWER, "reviewer-02")

    def test_decide_approved(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        d.accept_review(ActorRole.GOVERNANCE_REVIEWER, "reviewer-01")
        d.decide(DecisionOutcome.APPROVED, "Looks good")
        self.assertEqual(d.decision_state, DecisionState.DECIDED)
        self.assertEqual(d.decision, DecisionOutcome.APPROVED)
        self.assertEqual(d.rationale, "Looks good")
        self.assertIsNotNone(d.decided_at)

    def test_decide_updates_approver_identity(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
            risk_level=RiskLevel.MEDIUM,
        )
        d.accept_review(ActorRole.GOVERNANCE_REVIEWER, "reviewer-01")
        d.decide(
            DecisionOutcome.APPROVED,
            "Escalated approval",
            actor_role=ActorRole.RISK_OWNER,
            actor_id="risk-owner-01",
        )
        self.assertEqual(d.actor_role, ActorRole.RISK_OWNER)
        self.assertEqual(d.actor_id, "risk-owner-01")

    def test_decide_requires_under_review(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        with self.assertRaises(ValueError):
            d.decide(DecisionOutcome.APPROVED, "Looks good")

    def test_decide_approved_with_conditions(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        d.accept_review(ActorRole.GOVERNANCE_REVIEWER, "reviewer-01")
        d.decide(
            DecisionOutcome.APPROVED_WITH_CONDITIONS,
            "Approved with conditions",
            conditions=["Must reduce canary funding to 5%", "Must add stop-loss"],
        )
        self.assertEqual(d.decision, DecisionOutcome.APPROVED_WITH_CONDITIONS)
        self.assertEqual(len(d.conditions), 2)

    def test_decide_approved_with_conditions_no_conditions_raises(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        d.accept_review(ActorRole.GOVERNANCE_REVIEWER, "reviewer-01")
        with self.assertRaises(ValueError):
            d.decide(DecisionOutcome.APPROVED_WITH_CONDITIONS, "No conditions provided")

    def test_revoke(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        d.accept_review(ActorRole.GOVERNANCE_REVIEWER, "reviewer-01")
        d.decide(DecisionOutcome.APPROVED, "Looks good")
        d.revoke(ActorRole.RISK_OWNER, "operator-01")
        self.assertEqual(d.decision_state, DecisionState.REVOKED)
        self.assertEqual(d.actor_role, ActorRole.RISK_OWNER)
        self.assertEqual(d.actor_id, "operator-01")

    def test_revoke_non_decided_raises(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        with self.assertRaises(ValueError):
            d.revoke(ActorRole.RISK_OWNER, "operator-01")

    def test_revoke_requires_authorized_role(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        d.accept_review(ActorRole.GOVERNANCE_REVIEWER, "reviewer-01")
        d.decide(DecisionOutcome.APPROVED, "Looks good")
        with self.assertRaises(ValueError):
            d.revoke(ActorRole.GOVERNANCE_REVIEWER, "reviewer-01")

    def test_supersede(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        d.supersede("approval-002")
        self.assertEqual(d.superseded_by, "approval-002")
        self.assertEqual(d.decision_state, DecisionState.SUPERSEDED)


class TestApprovalDecisionValidation(unittest.TestCase):
    """Test ApprovalDecision validation rules."""

    def test_valid_decided_decision(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        d.accept_review(ActorRole.GOVERNANCE_REVIEWER, "reviewer-01")
        d.decide(DecisionOutcome.APPROVED, "Looks good")
        errors = d.validate()
        self.assertEqual(errors, [])

    def test_proposed_decision_does_not_require_decision_payload(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        errors = d.validate()
        self.assertEqual(errors, [])

    def test_missing_rationale_for_decided(self):
        d = ApprovalDecision(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
            decision=DecisionOutcome.APPROVED,
            decision_state=DecisionState.DECIDED,
            actor_role=ActorRole.GOVERNANCE_REVIEWER,
            actor_id="reviewer-01",
            rationale="",  # empty
            created_at="2026-04-10T00:00:00Z",
            decided_at="2026-04-10T00:01:00Z",
        )
        errors = d.validate()
        self.assertTrue(any("rationale" in e for e in errors))

    def test_role_not_authorized_for_risk(self):
        d = ApprovalDecision(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
            decision=DecisionOutcome.APPROVED,
            decision_state=DecisionState.DECIDED,
            actor_role=ActorRole.AUTOMATED_GATE,  # not authorized for HIGH
            actor_id="auto-01",
            rationale="Auto approved",
            created_at="2026-04-10T00:00:00Z",
            decided_at="2026-04-10T00:01:00Z",
            risk_level=RiskLevel.HIGH,
        )
        errors = d.validate()
        self.assertTrue(any("not authorized" in e for e in errors))


class TestApprovalDecisionSerialization(unittest.TestCase):
    """Test ApprovalDecision to/from dict and JSON."""

    def test_to_dict_and_back(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        d.accept_review(ActorRole.GOVERNANCE_REVIEWER, "reviewer-01")
        d.decide(DecisionOutcome.APPROVED, "Looks good")

        data = d.to_dict()
        self.assertEqual(data["decision_id"], "approval-001")
        self.assertEqual(data["decision"], "approved")
        self.assertEqual(data["decision_state"], "decided")

        restored = ApprovalDecision.from_dict(data)
        self.assertEqual(restored.decision_id, d.decision_id)
        self.assertEqual(restored.decision, d.decision)
        self.assertEqual(restored.rationale, d.rationale)

    def test_to_json_and_back(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        json_str = d.to_json()
        restored = ApprovalDecision.from_json(json_str)
        self.assertEqual(restored.decision_id, d.decision_id)

    def test_evidence_refs_serialization(self):
        refs = [EvidenceRef(ref_type=EvidenceRefType.EVALUATOR_RESULT, ref_id="eval-001")]
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
            risk_level=RiskLevel.MEDIUM,
        )
        d.evidence_refs = refs
        data = d.to_dict()
        self.assertEqual(len(data["evidence_refs"]), 1)
        self.assertEqual(data["evidence_refs"][0]["ref_id"], "eval-001")


class TestValidateDecisionJSON(unittest.TestCase):
    """Test standalone JSON validation function."""

    def test_valid_decision(self):
        data = {
            "decision_id": "approval-001",
            "target_type": "registry_entry",
            "target_id": "reg-001",
            "target_version": "1.0.0",
            "decision": "approved",
            "decision_state": "decided",
            "actor_role": "governance_reviewer",
            "actor_id": "reviewer-01",
            "rationale": "Looks good",
            "created_at": "2026-04-10T00:00:00Z",
            "decided_at": "2026-04-10T00:01:00Z",
        }
        errors = validate_decision_json(data)
        self.assertEqual(errors, [])

    def test_proposed_decision_is_valid_without_decision_fields(self):
        data = {
            "decision_id": "approval-001",
            "target_type": "registry_entry",
            "target_id": "reg-001",
            "target_version": "1.0.0",
            "decision_state": "proposed",
            "created_at": "2026-04-10T00:00:00Z",
        }
        errors = validate_decision_json(data)
        self.assertEqual(errors, [])

    def test_missing_required_field(self):
        data = {
            "decision_id": "approval-001",
            # missing target_type
            "target_id": "reg-001",
        }
        errors = validate_decision_json(data)
        self.assertTrue(any("target_type" in e for e in errors))

    def test_invalid_enum_value(self):
        data = {
            "decision_id": "approval-001",
            "target_type": "invalid_type",
            "target_id": "reg-001",
            "target_version": "1.0.0",
            "decision": "approved",
            "decision_state": "decided",
            "actor_role": "governance_reviewer",
            "actor_id": "reviewer-01",
            "rationale": "Looks good",
            "created_at": "2026-04-10T00:00:00Z",
            "decided_at": "2026-04-10T00:01:00Z",
        }
        errors = validate_decision_json(data)
        self.assertTrue(any("Invalid target_type" in e for e in errors))

    def test_approved_with_conditions_requires_conditions(self):
        data = {
            "decision_id": "approval-001",
            "target_type": "registry_entry",
            "target_id": "reg-001",
            "target_version": "1.0.0",
            "decision": "approved_with_conditions",
            "decision_state": "decided",
            "actor_role": "governance_reviewer",
            "actor_id": "reviewer-01",
            "rationale": "Conditional",
            "created_at": "2026-04-10T00:00:00Z",
            "decided_at": "2026-04-10T00:01:00Z",
        }
        errors = validate_decision_json(data)
        self.assertTrue(any("conditions" in e for e in errors))


class TestApprovalDecisionStore(unittest.TestCase):
    """Test in-memory store with filesystem persistence."""

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmpfile.close()
        self.store = ApprovalDecisionStore(self.tmpfile.name)

    def tearDown(self):
        if os.path.exists(self.tmpfile.name):
            os.unlink(self.tmpfile.name)

    def test_put_and_get(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        self.store.put(d)
        retrieved = self.store.get("approval-001")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.decision_id, "approval-001")

    def test_get_nonexistent(self):
        self.assertIsNone(self.store.get("nonexistent"))

    def test_find_by_target(self):
        d1 = ApprovalDecision.create_proposed("a1", TargetType.REGISTRY_ENTRY, "reg-001", "1.0.0")
        d2 = ApprovalDecision.create_proposed("a2", TargetType.REGISTRY_ENTRY, "reg-001", "2.0.0")
        d3 = ApprovalDecision.create_proposed("a3", TargetType.REGISTRY_ENTRY, "reg-002", "1.0.0")
        self.store.put(d1)
        self.store.put(d2)
        self.store.put(d3)
        results = self.store.find_by_target("registry_entry", "reg-001")
        self.assertEqual(len(results), 2)

    def test_find_latest_approved(self):
        d1 = ApprovalDecision.create_proposed("a1", TargetType.REGISTRY_ENTRY, "reg-001", "1.0.0")
        d1.accept_review(ActorRole.GOVERNANCE_REVIEWER, "r1")
        d1.decide(DecisionOutcome.APPROVED, "ok")
        d1.decided_at = "2026-04-10T00:00:00Z"

        d2 = ApprovalDecision.create_proposed("a2", TargetType.REGISTRY_ENTRY, "reg-001", "2.0.0")
        d2.accept_review(ActorRole.GOVERNANCE_REVIEWER, "r1")
        d2.decide(DecisionOutcome.APPROVED, "newer ok")
        d2.decided_at = "2026-04-10T01:00:00Z"

        self.store.put(d1)
        self.store.put(d2)

        latest = self.store.find_latest_approved("registry_entry", "reg-001")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.decision_id, "a2")

    def test_persistence_reload(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        self.store.put(d)

        # Create a new store instance from the same file
        store2 = ApprovalDecisionStore(self.tmpfile.name)
        retrieved = store2.get("approval-001")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.decision_id, "approval-001")

    def test_list_all(self):
        d1 = ApprovalDecision.create_proposed("a1", TargetType.REGISTRY_ENTRY, "reg-001", "1.0.0")
        d2 = ApprovalDecision.create_proposed("a2", TargetType.REGISTRY_ENTRY, "reg-002", "1.0.0")
        self.store.put(d1)
        self.store.put(d2)
        self.assertEqual(len(self.store.list_all()), 2)


class TestAuditEvent(unittest.TestCase):
    """Test audit event generation."""

    def test_to_audit_event(self):
        d = ApprovalDecision.create_proposed(
            decision_id="approval-001",
            target_type=TargetType.REGISTRY_ENTRY,
            target_id="reg-001",
            target_version="1.0.0",
        )
        d.accept_review(ActorRole.GOVERNANCE_REVIEWER, "reviewer-01")
        d.decide(DecisionOutcome.APPROVED, "Looks good")

        event = to_audit_event(d, "approval_decision_state_changed")
        self.assertEqual(event["event_type"], "approval_decision_state_changed")
        self.assertEqual(event["decision_id"], "approval-001")
        self.assertEqual(event["target_type"], "registry_entry")
        self.assertEqual(event["target_id"], "reg-001")
        self.assertEqual(event["decision"], "approved")
        self.assertEqual(event["actor_id"], "reviewer-01")
        self.assertIn("timestamp", event)


class TestSchemaFile(unittest.TestCase):
    """Test that the canonical schema file is valid and readable."""

    def test_schema_file_exists(self):
        schema_path = GOVERNANCE_DIR / "approval_decision.schema.json"
        self.assertTrue(schema_path.exists())

    def test_schema_is_valid_json(self):
        schema_path = GOVERNANCE_DIR / "approval_decision.schema.json"
        data = json.loads(schema_path.read_text())
        self.assertIn("$schema", data)
        self.assertIn("title", data)
        self.assertIn("properties", data)
        self.assertIn("required", data)


if __name__ == "__main__":
    unittest.main()
