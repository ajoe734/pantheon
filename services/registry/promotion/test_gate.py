from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

_PROMOTION_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROMOTION_DIR not in sys.path:
    sys.path.insert(0, _PROMOTION_DIR)

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

    def test_live_promotion_via_canonical_canary_path(self):
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

        canary_entry = self.gate.promote(paper_entry, PromotionState.CANARY)
        self.assertEqual(canary_entry["lifecycle_state"], "canary")

        promoted = self.gate.promote(canary_entry, PromotionState.LIVE)
        self.assertEqual(promoted["lifecycle_state"], "live")

    def test_paper_to_live_skip_is_forbidden(self):
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

        with self.assertRaises(PromotionError):
            self.gate.promote(paper_entry, PromotionState.LIVE)

    def test_live_promotion_rejects_without_rollback_registry_reference(self):
        canary_entry = build_candidate_entry()
        canary_entry["lifecycle_state"] = "canary"
        canary_entry["evaluation_summary"] = {
            "risk_review_passed": True,
            "sharpe_ratio": 1.4,
        }
        canary_entry["approver"] = "risk-committee"
        canary_entry["rollback_target"] = "0.9.0"
        canary_entry["metadata"] = {}

        with self.assertRaises(PromotionError):
            self.gate.promote(canary_entry, PromotionState.LIVE)

    def test_build_execution_projection_omits_null_lineage_fields(self):
        entry = build_candidate_entry()
        entry["lifecycle_state"] = "paper"
        entry["lineage"] = {"source_run_ids": ["r1"], "source_dataset_refs": None}

        projection = self.gate.build_execution_projection(entry)

        self.assertEqual(projection.metadata["lineage"], {"source_run_ids": ["r1"]})

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
        self.assertEqual(projection.metadata["rollback"]["target_version"], "0.9.0")
        self.assertEqual(projection.metadata["lineage"]["source_run_ids"], ["replication-run-001"])

    def test_promotion_gate_receives_real_replication_and_sharpe_from_replication_gate(self):
        from services.research.replication.gate import (
            ReplicationGate,
            create_promotion_request,
        )
        from services.research.replication.gate_schema import ReplicationRequest
        # Recorded output of run_replication_backtest_protocol (real vectorbt
        # run, see services/research/replication/fixtures/); the registry suite must not
        # require the vectorbt backend.
        fixture = (
            Path(__file__).resolve().parents[2]
            / "research"
            / "replication"
            / "fixtures"
            / "recorded_backtest_tw_2330.json"
        )
        bt_eval = json.loads(fixture.read_text(encoding="utf-8"))
        req = ReplicationRequest(
            candidate_id="cand-promo-001",
            source_task_id="RS-002",
            research_handoff={
                "source_metadata": {
                    "api_endpoint": "https://api.openalex.org/works/W123",
                    "retrieved_at": "2026-11-10T00:00:00Z",
                    "governance_context": "Approved structured source",
                },
                "normalized_findings": {
                    "strategy_spec": {"name": "tw_momentum"},
                    "replication_notes": "Real TW backtest",
                    "evaluation_hypotheses": "H1: OOS Sharpe > deflated threshold",
                },
            },
            proposed_strategy_spec={
                "name": "tw_momentum",
                "description": "Taiwan momentum",
                "signals": ["momentum"],
                "parameters": {"lookback": 2},
                "governance": {
                    "approval_required": True,
                    "policy_id": "oc-001-research-normalization",
                },
            },
            backtest_evaluation=bt_eval,
            metadata={
                "strategy_artifact_checksum": bt_eval["strategy_artifact"]["checksum"],
                "dataset_version_id": bt_eval["dataset_id"],
            },
        )
        rep_gate = ReplicationGate()
        # The recorded fixture stands in for the persisted run the protocol wrote.
        rep_gate.backtest_run_store.record(bt_eval)
        rep_response = rep_gate.evaluate_candidate(req)
        self.assertTrue(rep_response.passed)

        # An unpersisted / unbound copy of the same evaluation must not be admitted.
        req.backtest_evaluation = {**bt_eval, "backtest_run_id": "nonexistent-run-review-probe"}
        self.assertFalse(rep_gate.evaluate_candidate(req).passed)
        req.backtest_evaluation = bt_eval

        promo_req = create_promotion_request(rep_response, req.proposed_strategy_spec)
        self.assertIsNotNone(promo_req)
        draft_entry = PromotionGate.build_draft_entry_from_replication_request(
            promo_req,
            strategy_id="strat-tw-momentum-001",
        )
        self.assertTrue(draft_entry["replication_success"])
        self.assertGreater(draft_entry["evaluation_summary"]["sharpe_ratio"], 0.5)
        self.assertEqual(
            draft_entry["lineage"]["source_run_ids"],
            ["bt-promo-gate-001"],
        )

        candidate_entry = self.gate.promote(draft_entry, PromotionState.CANDIDATE)
        self.assertEqual(candidate_entry["lifecycle_state"], "candidate")

        candidate_entry["evaluation_summary"]["risk_review_passed"] = True
        paper_entry = self.gate.promote(candidate_entry, PromotionState.PAPER)
        self.assertEqual(paper_entry["lifecycle_state"], "paper")

    def test_promotion_gate_rejects_negative_or_below_threshold_sharpe(self):
        entry = build_candidate_entry()
        entry["evaluation_summary"] = {
            "sharpe_ratio": -0.35,
            "deflated_sharpe_threshold": 0.50,
        }
        with self.assertRaises(PromotionError):
            self.gate.promote(entry, PromotionState.CANDIDATE)

        entry_marginal = build_candidate_entry()
        entry_marginal["evaluation_summary"] = {
            "sharpe_ratio": 0.85,
            "deflated_sharpe_threshold": 1.112,
        }
        with self.assertRaises(PromotionError):
            self.gate.promote(entry_marginal, PromotionState.CANDIDATE)


if __name__ == "__main__":
    unittest.main()
