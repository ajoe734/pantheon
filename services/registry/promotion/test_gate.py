from __future__ import annotations

import unittest

from gate import ExecutionProjection, PromotionError, PromotionGate, PromotionState


def build_candidate_entry() -> dict:
    return {
        "registry_id": "reg-strat-001-1.0.0",
        "artifact_type": "model_artifact",
        "strategy_id": "strat-001",
        "version": "1.0.0",
        "lifecycle_state": "draft",
        "checksum": "sha256:abc123def4567890",
        "replication_success": True,
        "lineage": {
            "source_run_id": "replication-run-001",
        },
        "storage_ref": {
            "backend": "object_store",
            "path": "object://pantheon/registry/strat-001/1.0.0/artifact.bin",
        },
    }


class TestPromotionGate(unittest.TestCase):
    def setUp(self):
        self.gate = PromotionGate()

    def test_candidate_promotion_accepts_legacy_source_run_id(self):
        promoted = self.gate.promote(build_candidate_entry(), PromotionState.CANDIDATE)

        self.assertEqual(promoted["lifecycle_state"], "candidate")
        self.assertIn("promoted_at", promoted)

    def test_live_promotion_accepts_legacy_rollback_fields(self):
        paper_entry = build_candidate_entry()
        paper_entry["lifecycle_state"] = "paper"
        paper_entry["evaluation_summary"] = {
            "risk_review_passed": True,
            "sharpe_ratio": 1.4,
        }
        paper_entry["approver"] = "risk-committee"
        paper_entry["rollback_target"] = "0.9.0"
        paper_entry["metadata"] = {
            "rollback_target_registry_id": "reg-strat-001-0.9.0",
        }

        promoted = self.gate.promote(paper_entry, PromotionState.LIVE)

        self.assertEqual(promoted["lifecycle_state"], "live")

    def test_live_promotion_rejects_without_rollback_registry_reference(self):
        paper_entry = build_candidate_entry()
        paper_entry["lifecycle_state"] = "paper"
        paper_entry["evaluation_summary"] = {
            "risk_review_passed": True,
            "sharpe_ratio": 1.4,
        }
        paper_entry["approver"] = "risk-committee"
        paper_entry["rollback_target"] = "0.9.0"
        paper_entry["metadata"] = {}

        with self.assertRaises(PromotionError):
            self.gate.promote(paper_entry, PromotionState.LIVE)

    def test_build_execution_projection_returns_canonical_keys_and_metadata(self):
        live_entry = build_candidate_entry()
        live_entry["lifecycle_state"] = "live"
        live_entry["approver"] = "risk-committee"
        live_entry["promoted_at"] = "2026-04-06T12:10:00Z"
        live_entry["metadata"] = {
            "rollback": {
                "target_registry_id": "reg-strat-001-0.9.0",
                "target_version": "0.9.0",
            }
        }

        projection = self.gate.build_execution_projection(live_entry)

        self.assertIsInstance(projection, ExecutionProjection)
        self.assertEqual(
            projection.metadata_key,
            "openclaw/registry/strat-001/1.0.0/metadata.json",
        )
        self.assertEqual(
            projection.artifact_key,
            "openclaw/registry/strat-001/1.0.0/artifact.bin",
        )
        self.assertEqual(projection.metadata["promotion_state"], "live")
        self.assertEqual(projection.metadata["rollback"]["target_version"], "0.9.0")
        self.assertEqual(projection.metadata["lineage"]["source_run_ids"], ["replication-run-001"])


if __name__ == "__main__":
    unittest.main()
