"""Tests for the IMT-007 behavior-policy artifact validation gate."""

from __future__ import annotations

import copy

import pytest

from policy_validation_gate import (
    DEFAULT_ACTION_MATCH_THRESHOLD,
    PolicyValidationError,
    _compute_checksum,
    validate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_valid_artifact() -> dict:
    artifact: dict = {
        "artifact_type": "behavior_policy",
        "artifact_state": "candidate",
        "training": {
            "run_id": "bc-abc123456789",
            "epochs": 25,
            "learning_rate": 0.2,
            "device": "cpu",
        },
        "lineage": {
            "source_dataset_ref": "dataset-imt-007-test",
        },
        "eval_result": {
            "action_match_rate": 0.75,
            "return_gap": 0.05,
            "kl_divergence": 0.12,
        },
        "governance": {
            "research_only": True,
            "direct_live_influence": False,
            "eligible_promotion_states": ["candidate", "paper"],
        },
    }
    artifact["checksum"] = _compute_checksum(artifact)
    return artifact


# ---------------------------------------------------------------------------
# Pass scenario
# ---------------------------------------------------------------------------

def test_validate_pass():
    artifact = _make_valid_artifact()
    result = validate(artifact)
    assert result["passed"] is True
    assert result["rejection_reasons"] == []


# ---------------------------------------------------------------------------
# Fail scenario 1: action_match_rate below threshold
# ---------------------------------------------------------------------------

def test_validate_fail_low_action_match_rate():
    artifact = _make_valid_artifact()
    artifact["eval_result"]["action_match_rate"] = 0.45
    # Recompute checksum so only the metric check fires
    artifact["checksum"] = _compute_checksum(artifact)

    result = validate(artifact, threshold=DEFAULT_ACTION_MATCH_THRESHOLD)

    assert result["passed"] is False
    assert any("action_match_rate" in r for r in result["rejection_reasons"])
    assert any("threshold" in r for r in result["rejection_reasons"])


# ---------------------------------------------------------------------------
# Fail scenario 2: checksum mismatch
# ---------------------------------------------------------------------------

def test_validate_fail_checksum_mismatch():
    artifact = _make_valid_artifact()
    # Tamper with the artifact after checksum is set
    artifact["artifact_state"] = "tampered"

    result = validate(artifact)

    assert result["passed"] is False
    assert any("checksum mismatch" in r for r in result["rejection_reasons"])


# ---------------------------------------------------------------------------
# Fail scenario 3: required metadata missing
# ---------------------------------------------------------------------------

def test_validate_fail_missing_metadata():
    artifact = _make_valid_artifact()
    # Remove training entirely — drops producer_run_id and training_config
    del artifact["training"]
    # Remove lineage — drops dataset_ref
    del artifact["lineage"]
    artifact["checksum"] = _compute_checksum(artifact)

    result = validate(artifact)

    assert result["passed"] is False
    reasons = " ".join(result["rejection_reasons"])
    assert "producer_run_id" in reasons
    assert "dataset_ref" in reasons
    assert "training_config" in reasons


# ---------------------------------------------------------------------------
# Additional: forbidden trigger words in string values
# ---------------------------------------------------------------------------

def test_validate_fail_trigger_word_in_value():
    artifact = _make_valid_artifact()
    artifact["artifact_state"] = "live"
    artifact["checksum"] = _compute_checksum(artifact)

    result = validate(artifact)

    assert result["passed"] is False
    assert any("live" in r for r in result["rejection_reasons"])


def test_validate_fail_trigger_word_canary():
    artifact = _make_valid_artifact()
    artifact["deployment_hint"] = "canary"
    artifact["checksum"] = _compute_checksum(artifact)

    result = validate(artifact)

    assert result["passed"] is False
    assert any("canary" in r for r in result["rejection_reasons"])


# ---------------------------------------------------------------------------
# Additional: structural guard
# ---------------------------------------------------------------------------

def test_validate_raises_on_non_mapping():
    with pytest.raises(PolicyValidationError):
        validate("not a mapping")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Additional: custom threshold
# ---------------------------------------------------------------------------

def test_validate_custom_threshold_pass():
    artifact = _make_valid_artifact()
    artifact["eval_result"]["action_match_rate"] = 0.55
    artifact["checksum"] = _compute_checksum(artifact)

    result = validate(artifact, threshold=0.5)
    assert result["passed"] is True


def test_validate_custom_threshold_fail():
    artifact = _make_valid_artifact()
    artifact["eval_result"]["action_match_rate"] = 0.55
    artifact["checksum"] = _compute_checksum(artifact)

    result = validate(artifact, threshold=0.7)
    assert result["passed"] is False
    assert any("action_match_rate" in r for r in result["rejection_reasons"])


# ---------------------------------------------------------------------------
# Additional: missing eval_result
# ---------------------------------------------------------------------------

def test_validate_fail_missing_eval_result():
    artifact = _make_valid_artifact()
    del artifact["eval_result"]
    artifact["checksum"] = _compute_checksum(artifact)

    result = validate(artifact)
    assert result["passed"] is False
    assert any("action_match_rate absent" in r for r in result["rejection_reasons"])
