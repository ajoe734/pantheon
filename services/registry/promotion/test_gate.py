from __future__ import annotations

import json
import unittest
from pathlib import Path

from services.registry.promotion.gate import ExecutionProjection, PromotionError, PromotionGate, PromotionState


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

        # The producer's own proof verifies; tampered/failed/unbound proofs do not.
        proof, summary = draft_entry["replication_proof"], draft_entry["evaluation_summary"]
        lineage = draft_entry["lineage"]
        checksum = bt_eval["strategy_artifact"]["checksum"]
        kw = {"backtest_evaluation": bt_eval, "artifact_checksum": checksum}
        verified = PromotionGate.verify_admitted_replication_proof(proof, summary, lineage, **kw)
        self.assertEqual({k: v for k, v in verified.items() if k != "owner_verified_binding"}, proof)
        self.assertEqual(verified["owner_verified_binding"]["artifact_checksum"], checksum)
        import copy as _copy
        failed = _copy.deepcopy(proof)
        for r in failed["replication_results"]:
            if r.get("criterion_id") == "real_backtest_validation":
                r["passed"] = False
        for bad_proof, bad_summary, bad_lineage in (
            (failed, summary, lineage),
            ({}, summary, lineage),
            (proof, {**summary, "out_of_sample_sharpe_ratio": 9.9}, lineage),
            (proof, summary, {"source_run_ids": ["some-other-run"]}),
            ({**proof, "admission_status": "rejected"}, summary, lineage),
        ):
            with self.assertRaises(PromotionError):
                PromotionGate.verify_admitted_replication_proof(bad_proof, bad_summary, bad_lineage, **kw)

        # Gate prerequisites beyond the six metric checks must also hold on the evaluation.
        for mutate in (
            lambda e: e["history_check"].update(sufficient=False, calendar_days=1, trading_days=1),
            lambda e: e["protocol"].update(in_sample_ratio=1.0, out_of_sample_ratio=0.0),
            lambda e: e["protocol"].update(walk_forward_folds=0),
            lambda e: e["transaction_costs"].update(applied_on_every_fill=False, commission_rate=0.0),
            lambda e: e["benchmarks"].pop("tw_0050"),
        ):
            weak = _copy.deepcopy(bt_eval)
            mutate(weak)
            with self.assertRaises(PromotionError):
                PromotionGate.verify_admitted_replication_proof(
                    proof, summary, lineage, backtest_evaluation=weak, artifact_checksum=checksum)

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
