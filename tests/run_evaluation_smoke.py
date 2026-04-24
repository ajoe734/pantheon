"""Smoke test for services.evaluation core evaluator path."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.evaluation import Recommendation, ScoreComponent, evaluate_artifact


def assert_true(condition, message):
    if not condition:
        print("FAIL", message)
        raise SystemExit(1)
    print("OK", message)


artifact = {
    "registry_id": "strat_xyz_v1.3.0",
    "artifact_type": "strategy_spec",
    "strategy_id": "strat_xyz",
    "lifecycle_state": "candidate",
    "version": "1.3.0",
    "checksum": "sha256:abc123",
    "created_at": "2026-04-14T12:00:00Z",
    "lineage": {
        "parent_registry_ids": ["strat_xyz_v1.2.0"],
        "source_run_ids": ["eval-smoke-run"],
    },
}

score_components = {
    "sharpe_ratio": ScoreComponent(
        value=1.52,
        weight=0.45,
        dimension_score=0.84,
        interpretation="Strong risk-adjusted returns",
        confidence=0.88,
    ),
    "max_drawdown": ScoreComponent(
        value=-0.11,
        weight=0.30,
        dimension_score=0.77,
        interpretation="Within acceptable band",
        confidence=0.75,
    ),
    "replication_fidelity": ScoreComponent(
        value=0.96,
        weight=0.25,
        dimension_score=0.93,
        interpretation="High replication success",
        confidence=0.91,
    ),
}

result = evaluate_artifact(
    artifact=artifact,
    score_components=score_components,
    execution_telemetry_window={
        "start_date": "2026-04-10T00:00:00Z",
        "end_date": "2026-04-16T23:59:59Z",
        "execution_mode": "paper",
        "num_executions": 18,
    },
    feedback_events_considered={"count": 2, "types": ["approve", "rationale"]},
    rationale="Smoke path for evaluator artifact ingestion",
)

payload = result.to_dict()

assert_true(payload["artifact_type"] == "evaluation_result", "artifact_type is evaluation_result")
assert_true(payload["target_artifact_id"] == artifact["registry_id"], "artifact linkage preserved")
assert_true(payload["overall_score"] > 0.0, "overall score computed")
assert_true(
    payload["recommendation"] in {
        Recommendation.CANDIDATE_TO_PAPER.value,
        Recommendation.CANDIDATE_HOLD.value,
    },
    "recommendation derived for candidate artifact",
)
assert_true(payload["evaluation_data_snapshot"]["target_artifact_checksum"] == artifact["checksum"], "snapshot captured")

print("SMOKE OK")
