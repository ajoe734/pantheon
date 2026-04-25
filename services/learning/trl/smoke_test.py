#!/usr/bin/env python3
"""Governed TRL DPO preference-learning smoke test for Pantheon.

Proves the TRL row is backed by a runnable governed adapter path rather than
a placeholder activation criteria document.

Usage:
    python3 services/learning/trl/smoke_test.py               # stub DPO (no TRL install)
    python3 services/learning/trl/smoke_test.py --backend trl  # upstream TRL DPO
"""
from __future__ import annotations

import argparse
import json
import sys
import os

# Support running from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from services.learning.trl.adapter import (
    GovernedPreferencePairAdapter,
    StubDPOBackend,
    TRLDPOBackend,
    TrainingConfig,
    run_trl_dpo_workflow,
)


SYNTHETIC_EVENTS = [
    {
        "feedback_event_id": "fb-smoke-001",
        "actor_role": "operator",
        "promotion_state": "candidate",
        "action": "approve",
        "strategy_family": "equity_cross_sectional",
        "operator_id": "op-001",
        "artifact": {
            "artifact_id": "art-a1",
            "strategy_id": "equity-alpha-v1",
            "sharpe": 1.42,
            "max_drawdown": -0.08,
            "sector": "technology",
        },
    },
    {
        "feedback_event_id": "fb-smoke-002",
        "actor_role": "operator",
        "promotion_state": "candidate",
        "action": "reject",
        "strategy_family": "equity_cross_sectional",
        "operator_id": "op-001",
        "artifact": {
            "artifact_id": "art-a2",
            "strategy_id": "equity-alpha-v2",
            "sharpe": 0.55,
            "max_drawdown": -0.22,
            "sector": "energy",
        },
    },
    {
        "feedback_event_id": "fb-smoke-003",
        "actor_role": "approver",
        "promotion_state": "paper",
        "action": "edit",
        "strategy_family": "stat_arb",
        "operator_id": "op-002",
        "artifact": {
            "artifact_id": "art-b1",
            "strategy_id": "stat-arb-v1",
            "sharpe": 1.10,
            "max_drawdown": -0.12,
            "sector": "financials",
        },
        "artifact_edited": {
            "artifact_id": "art-b1-edited",
            "strategy_id": "stat-arb-v1",
            "sharpe": 1.15,
            "max_drawdown": -0.10,
            "sector": "financials",
            "edit_note": "tightened drawdown constraint",
        },
    },
    {
        "feedback_event_id": "fb-smoke-004",
        "actor_role": "operator",
        "promotion_state": "candidate",
        "action": "approve",
        "strategy_family": "stat_arb",
        "operator_id": "op-001",
        "artifact": {
            "artifact_id": "art-b2",
            "strategy_id": "stat-arb-v2",
            "sharpe": 1.28,
            "max_drawdown": -0.09,
            "sector": "consumer",
        },
    },
    {
        "feedback_event_id": "fb-smoke-005",
        "actor_role": "operator",
        "promotion_state": "candidate",
        "action": "reject",
        "strategy_family": "equity_cross_sectional",
        "operator_id": "op-002",
        "artifact": {
            "artifact_id": "art-a3",
            "strategy_id": "equity-alpha-v3",
            "sharpe": 0.30,
            "max_drawdown": -0.35,
            "sector": "utilities",
        },
    },
]


def run_smoke(backend_name: str = "stub") -> None:
    print(f"TRL DPO smoke test — backend: {backend_name}")
    print("-" * 60)

    backend = TRLDPOBackend() if backend_name == "trl" else StubDPOBackend()
    config = TrainingConfig(
        version="1.0.0",
        requested_by="Claude",
        method="dpo",
        beta=0.1,
        learning_rate=5e-6,
        batch_size=4,
        num_epochs=1,
        seed=42,
    )

    result = run_trl_dpo_workflow(
        SYNTHETIC_EVENTS,
        dataset_id="fb002-smoke-2026-04-17",
        strategy_id="preference-learning-multi-asset",
        source_dataset_refs=["fb002-events-smoke-2026-04-17"],
        backend=backend,
        config=config,
    )

    ds = result.preference_dataset
    reg = result.registry_entry
    ab = result.artifact_bundle

    print(f"dataset_id:         {ds.dataset_id}")
    print(f"strategy_id:        {ds.strategy_id}")
    print(f"num_pairs:          {ds.num_pairs}")
    print(f"strategy_families:  {ds.strategy_families}")
    print(f"num_operators:      {ds.num_operators}")
    print(f"action_distribution:{ds.action_distribution}")
    print()
    print(f"backend:            {result.training_result.backend}")
    print(f"run_id:             {result.training_result.run_id}")
    print(f"accuracy:           {result.training_result.metrics.get('accuracy', 'n/a')}")
    print()
    print(f"registry_id:        {reg['registry_id']}")
    print(f"artifact_state:     {reg['artifact_state']}")
    print(f"deployment_stage:   {reg['deployment_summary']['current_stage']}")
    print(f"storage_path:       {reg['storage_ref']['path']}")
    print(f"checksum:           {reg['checksum']}")
    print(f"artifact_family:    {ab['artifact_family']}")
    print()

    # Governance assertions
    assert reg["artifact_state"] == "draft", (
        f"artifact_state must be 'draft', got '{reg['artifact_state']}'"
    )
    assert reg["deployment_summary"]["current_stage"] == "none", (
        "deployment_stage must be 'none' for TRL preference models"
    )
    assert reg["checksum"].startswith("sha256:"), "checksum must be sha256"
    assert reg["storage_ref"]["path"].startswith("learning/trl/"), (
        "storage path must be under learning/trl/"
    )
    assert ab["governance"]["direct_live_influence"] is False, (
        "direct_live_influence must be False"
    )
    assert ab["governance"]["execution_stage"] == "none", (
        "execution_stage must be 'none' for preference models"
    )
    assert ds.num_pairs >= 2, "smoke test requires at least 2 preference pairs"
    assert len(ds.strategy_families) >= 2, (
        "smoke test requires at least 2 strategy families"
    )
    assert reg["lineage"]["source_feedback_event_ids"], (
        "registry entry must carry source_feedback_event_ids"
    )
    assert reg["approved_at"] is None, "new artifact must have approved_at=None"
    assert reg["rollback_target"] is None, "new artifact must have rollback_target=None"

    print("assertions: OK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TRL DPO governed smoke test")
    parser.add_argument(
        "--backend",
        choices=["stub", "trl"],
        default="stub",
        help="DPO backend: 'stub' (no TRL install) or 'trl' (requires requirements.txt)",
    )
    args = parser.parse_args()
    run_smoke(args.backend)
