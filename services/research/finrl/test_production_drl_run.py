"""Tests for the TWSE FinRL offline evidence run."""
from __future__ import annotations

import json
from pathlib import Path

from production_drl_run import build_dataset_config, load_twse_data, main
from registry_admission_packet import generate_admission_packet, validate_admission_packet
from twse_stock_env import TWSESerialEnv


def test_twse_records_are_governed_ohlcv():
    records = load_twse_data(periods=8)
    dataset = build_dataset_config(records)
    assert dataset["dataset_id"] == "twse-offline-dataset-001"
    assert len({record["instrument"] for record in records}) >= 5
    assert {"open", "high", "low", "close", "volume"}.issubset(records[0])
    assert dataset["twse_stock_env"]["num_instruments"] >= 5
    assert dataset["twse_stock_env"]["upstream_env"].endswith("StockTradingEnv")


def test_twse_env_import_safe_without_finrl():
    env = TWSESerialEnv(load_twse_data(periods=6))
    reset_state = env.reset()
    assert reset_state["records"] == 30
    assert reset_state["stock_dim"] == 5
    assert env.environment_summary()["action_space"] == 5
    _, reward, _, info = env.step([0.2, 0.1, 0.0, -0.1, 0.3])
    assert isinstance(reward, float)
    assert info["portfolio_value"] != reset_state["portfolio_value"]


def test_production_drl_training_writes_offline_artifacts(tmp_path: Path):
    result = main(output_dir=tmp_path)

    metrics = result.training_result.metrics
    assert "sharpe" in metrics
    assert metrics["sharpe"] > 0
    assert "annual_return" in metrics
    assert "max_drawdown" in metrics
    assert 0.0 <= metrics["max_drawdown"] <= 1.0

    # Production path must clear the 1000-step task acceptance threshold.
    assert metrics["total_training_steps"] >= 1000
    assert metrics["portfolio_value_final"] != metrics["portfolio_value_initial"]

    # Registry entry must carry checksum and trained_policy_ref.
    assert result.registry_entry["artifact_state"] == "draft"
    assert result.registry_entry["deployment_summary"]["current_stage"] == "none"
    assert "checksum" in result.registry_entry
    assert result.registry_entry["trained_policy_ref"] == result.registry_entry["storage_ref"]["path"]

    assert result.candidate_packet["gate_state"] == "closed"

    assert (tmp_path / "evaluation_summary.json").exists()
    assert (tmp_path / "artifact_bundle.json").exists()
    assert (tmp_path / "registry_entry.json").exists()
    assert (tmp_path / "candidate_packet.json").exists()
    admission_packet = json.loads((tmp_path / "admission_packet.json").read_text(encoding="utf-8"))
    assert validate_admission_packet(admission_packet) == []
    assert admission_packet["schema_version"] == "PromotionReadinessPacket.v1"
    assert admission_packet["environment"] == "paper"
    assert admission_packet["can_proceed"] is True
    assert admission_packet["registry_request"]["requested_transition"] == "draft_to_candidate"
    assert admission_packet["legacy_review_fields"]["allowed_next_action"] == "offline_registry_review_only"
    assert "annual_return" in admission_packet["evaluation_summary"]
    assert "max_drawdown" in admission_packet["evaluation_summary"]
    assert admission_packet["candidate_artifact"]["trained_policy_ref"]
    assert admission_packet["model_artifact_ref"]["checksum"].startswith("sha256:")


def test_admission_packet_generation(tmp_path: Path):
    run_id = "test-run"
    summary = {
        "algorithm": "ppo",
        "num_steps": 100,
        "total_training_steps": 1200,
        "sharpe": 1.0,
        "annual_return": 0.12,
        "max_drawdown": 0.03,
    }
    packet = generate_admission_packet(
        run_id,
        summary,
        "test-artifact",
        output_path=tmp_path / "admission_packet.json",
        created_at="2026-05-17T00:00:00Z",
    )
    assert packet["packet_id"].startswith("prp-oss-finrl-v2-001-test-artifact")
    assert packet["environment"] == "paper"
    assert packet["can_proceed"] is True
    assert validate_admission_packet(packet) == []
