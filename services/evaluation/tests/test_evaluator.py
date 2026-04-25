"""Unit tests for services/evaluation/evaluator.py and models.py."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator, FormatChecker

from services.evaluation.evaluator import (
    evaluate,
    evaluate_artifact,
    _compute_overall_score,
    _derive_recommendation,
)
from services.evaluation.models import (
    EvaluatorResult,
    Recommendation,
    ScoreComponent,
)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "contracts" / "evaluator_result.schema.json"
FORMAT_CHECKER = FormatChecker()


def _load_evaluator_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _schema_errors(payload: dict) -> list[str]:
    validator = Draft7Validator(_load_evaluator_schema(), format_checker=FORMAT_CHECKER)
    return sorted(error.message for error in validator.iter_errors(payload))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_components(*, weights=(0.4, 0.35, 0.25), scores=(0.85, 0.78, 0.90)):
    keys = ["sharpe_ratio", "max_drawdown", "replication_fidelity"]
    return {
        k: ScoreComponent(value=v, weight=w, dimension_score=s, interpretation="ok")
        for k, (w, s, v) in zip(keys, zip(weights, scores, [1.5, -0.10, 0.92]))
    }


def make_artifact():
    return {
        "registry_id": "strat_xyz_v1.3.0",
        "artifact_type": "strategy_spec",
        "strategy_id": "strat_xyz",
        "lifecycle_state": "candidate",
        "version": "1.3.0",
        "checksum": "sha256:abc123",
        "created_at": "2026-04-14T12:00:00Z",
        "lineage": {
            "parent_registry_ids": ["strat_xyz_v1.2.0"],
            "source_run_ids": ["run-42"],
        },
        "metadata": {
            "risk_band": "medium",
        },
    }


# ---------------------------------------------------------------------------
# ScoreComponent validation
# ---------------------------------------------------------------------------

def test_score_component_valid():
    c = ScoreComponent(value=1.5, weight=0.5, dimension_score=0.8)
    assert c.weight == 0.5
    assert c.dimension_score == 0.8


def test_score_component_invalid_weight():
    with pytest.raises(ValueError, match="weight"):
        ScoreComponent(value=0, weight=1.5, dimension_score=0.5)


def test_score_component_invalid_dimension_score():
    with pytest.raises(ValueError, match="dimension_score"):
        ScoreComponent(value=0, weight=0.5, dimension_score=-0.1)


# ---------------------------------------------------------------------------
# Overall score calculation
# ---------------------------------------------------------------------------

def test_compute_overall_score():
    components = make_components(weights=(0.4, 0.35, 0.25), scores=(1.0, 0.0, 1.0))
    score = _compute_overall_score(components)
    assert abs(score - 0.65) < 1e-5


# ---------------------------------------------------------------------------
# Recommendation derivation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("score,state,expected", [
    (0.80, "candidate", "candidate_to_paper"),
    (0.60, "candidate", "candidate_hold"),
    (0.85, "paper", "paper_to_live"),
    (0.70, "paper", "paper_hold"),
    (0.90, "live", "needs_critique"),
])
def test_derive_recommendation(score, state, expected):
    assert _derive_recommendation(score, state) == expected


# ---------------------------------------------------------------------------
# evaluate() integration
# ---------------------------------------------------------------------------

def test_evaluate_returns_evaluator_result():
    components = make_components()
    result = evaluate(
        strategy_id="strat_xyz",
        target_artifact_id="strat_xyz_v1.3.0",
        target_artifact_type="strategy_spec",
        target_promotion_state="candidate",
        target_artifact_version="1.3.0",
        target_artifact_checksum="sha256:abc123",
        score_components=components,
    )
    assert isinstance(result, EvaluatorResult)
    assert result.artifact_type == "evaluation_result"
    assert result.strategy_id == "strat_xyz"
    assert 0.0 <= result.overall_score <= 1.0
    assert result.recommendation in {r.value for r in Recommendation}


def test_evaluate_overall_score_weighted():
    components = make_components(weights=(0.4, 0.35, 0.25), scores=(0.8, 0.6, 1.0))
    result = evaluate(
        strategy_id="s",
        target_artifact_id="s_v1",
        target_artifact_type="strategy_spec",
        target_promotion_state="candidate",
        target_artifact_version="1.0",
        target_artifact_checksum="sha256:x",
        score_components=components,
    )
    expected = 0.4 * 0.8 + 0.35 * 0.6 + 0.25 * 1.0
    assert abs(result.overall_score - expected) < 1e-5


def test_evaluate_registry_id_auto_generated():
    components = make_components()
    r = evaluate(
        strategy_id="strat_a",
        target_artifact_id="strat_a_v1",
        target_artifact_type="strategy_spec",
        target_promotion_state="candidate",
        target_artifact_version="1.0",
        target_artifact_checksum="sha256:x",
        score_components=components,
    )
    assert r.registry_id.startswith("eval-strat_a-")


def test_evaluate_explicit_registry_id():
    components = make_components()
    r = evaluate(
        strategy_id="s",
        target_artifact_id="s_v1",
        target_artifact_type="strategy_spec",
        target_promotion_state="candidate",
        target_artifact_version="1.0",
        target_artifact_checksum="sha256:x",
        score_components=components,
        registry_id="my-registry-id",
    )
    assert r.registry_id == "my-registry-id"


def test_evaluate_confidence_from_components():
    components = {
        "a": ScoreComponent(value=1.0, weight=0.6, dimension_score=0.9, confidence=0.8),
        "b": ScoreComponent(value=1.0, weight=0.4, dimension_score=0.7, confidence=0.6),
    }
    r = evaluate(
        strategy_id="s",
        target_artifact_id="s_v1",
        target_artifact_type="strategy_spec",
        target_promotion_state="candidate",
        target_artifact_version="1.0",
        target_artifact_checksum="sha256:x",
        score_components=components,
    )
    assert abs(r.confidence - 0.7) < 1e-5


def test_evaluate_default_confidence_when_none():
    components = make_components()
    r = evaluate(
        strategy_id="s",
        target_artifact_id="s_v1",
        target_artifact_type="strategy_spec",
        target_promotion_state="candidate",
        target_artifact_version="1.0",
        target_artifact_checksum="sha256:x",
        score_components=components,
    )
    assert r.confidence == 0.8


def test_evaluate_invalid_weights_raises():
    bad_components = {
        "a": ScoreComponent(value=1.0, weight=0.6, dimension_score=0.9),
        "b": ScoreComponent(value=1.0, weight=0.6, dimension_score=0.7),
    }
    with pytest.raises(ValueError, match="weights must sum"):
        evaluate(
            strategy_id="s",
            target_artifact_id="s_v1",
            target_artifact_type="strategy_spec",
            target_promotion_state="candidate",
            target_artifact_version="1.0",
            target_artifact_checksum="sha256:x",
            score_components=bad_components,
        )


def test_evaluate_empty_score_components_raises():
    with pytest.raises(ValueError, match="score_components must contain at least one component"):
        evaluate(
            strategy_id="s",
            target_artifact_id="s_v1",
            target_artifact_type="strategy_spec",
            target_promotion_state="candidate",
            target_artifact_version="1.0",
            target_artifact_checksum="sha256:x",
            score_components={},
        )


def test_evaluate_to_dict_round_trip():
    components = make_components()
    r = evaluate(
        strategy_id="strat_xyz",
        target_artifact_id="strat_xyz_v1.3.0",
        target_artifact_type="strategy_spec",
        target_promotion_state="candidate",
        target_artifact_version="1.3.0",
        target_artifact_checksum="sha256:abc",
        score_components=components,
        rationale="All metrics above threshold.",
    )
    d = r.to_dict()
    assert d["artifact_type"] == "evaluation_result"
    assert "score_components" in d
    assert "evaluation_data_snapshot" in d
    assert d["rationale"] == "All metrics above threshold."
    assert "sharpe_ratio" in d["score_components"]


def test_evaluate_payload_matches_schema():
    payload = evaluate(
        strategy_id="strat_xyz",
        target_artifact_id="strat_xyz_v1.3.0",
        target_artifact_type="strategy_spec",
        target_promotion_state="candidate",
        target_artifact_version="1.3.0",
        target_artifact_checksum="sha256:abc123",
        score_components=make_components(),
    ).to_dict()
    assert _schema_errors(payload) == []


def test_evaluate_artifact_derives_target_fields():
    artifact = make_artifact()
    result = evaluate_artifact(
        artifact=artifact,
        score_components=make_components(),
        execution_telemetry_window={"execution_mode": "paper", "num_executions": 12},
    )
    assert result.strategy_id == "strat_xyz"
    assert result.target_artifact_id == "strat_xyz_v1.3.0"
    assert result.target_artifact_type == "strategy_spec"
    assert result.target_promotion_state == "candidate"
    assert result.evaluation_data_snapshot.target_lineage == artifact["lineage"]
    assert result.auditable_fields == {"target_metadata": artifact["metadata"]}


def test_evaluate_artifact_accepts_field_overrides():
    artifact = make_artifact()
    result = evaluate_artifact(
        artifact=artifact,
        score_components=make_components(),
        target_promotion_state="paper",
        target_artifact_type="model_artifact",
    )
    assert result.target_promotion_state == "paper"
    assert result.target_artifact_type == "model_artifact"
    assert result.recommendation in {
        Recommendation.PAPER_TO_LIVE.value,
        Recommendation.PAPER_HOLD.value,
    }


def test_evaluate_artifact_missing_artifact_returns_fallback_result():
    result = evaluate_artifact(
        artifact=None,
        strategy_id="strat_xyz",
        target_artifact_id="strat_xyz_v1.3.0",
        target_artifact_type="strategy_spec",
        score_components=make_components(),
    )
    assert result.overall_score == 0.0
    assert result.confidence == 0.0
    assert result.recommendation == Recommendation.NEEDS_CRITIQUE.value
    assert result.auditable_fields == {
        "missing_artifact": {
            "status": "not_found",
            "requested_target_artifact_id": "strat_xyz_v1.3.0",
        }
    }


def test_evaluate_artifact_missing_artifact_requires_target_artifact_type():
    with pytest.raises(ValueError, match="target_artifact_type is required"):
        evaluate_artifact(
            artifact=None,
            strategy_id="strat_xyz",
            target_artifact_id="strat_xyz_v1.3.0",
            score_components=make_components(),
        )


def test_evaluate_artifact_missing_artifact_requires_score_components():
    with pytest.raises(ValueError, match="score_components must contain at least one component"):
        evaluate_artifact(
            artifact=None,
            strategy_id="strat_xyz",
            target_artifact_id="strat_xyz_v1.3.0",
            target_artifact_type="strategy_spec",
            score_components={},
        )


def test_evaluate_artifact_partial_metadata_requires_target_artifact_type():
    artifact = {
        "registry_id": "strat_xyz_v1.3.0",
        "strategy_id": "strat_xyz",
        "lifecycle_state": "candidate",
        "version": "1.3.0",
        "checksum": "sha256:abc123",
    }
    with pytest.raises(ValueError, match="target_artifact_type is required"):
        evaluate_artifact(
            artifact=artifact,
            score_components=make_components(),
        )


def test_evaluate_artifact_fallback_payload_matches_schema():
    payload = evaluate_artifact(
        artifact=None,
        strategy_id="strat_xyz",
        target_artifact_id="strat_xyz_v1.3.0",
        target_artifact_type="strategy_spec",
        score_components=make_components(),
    ).to_dict()
    assert _schema_errors(payload) == []


def test_evaluate_artifact_does_not_mutate_input_artifact():
    artifact = make_artifact()
    original = copy.deepcopy(artifact)
    evaluate_artifact(
        artifact=artifact,
        score_components=make_components(),
    )
    assert artifact == original
