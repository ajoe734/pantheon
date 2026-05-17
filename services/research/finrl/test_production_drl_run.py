"""Tests for the TWSE FinRL offline evidence run."""
from __future__ import annotations

import json
from pathlib import Path

from production_drl_run import build_dataset_config, load_twse_data, main
from registry_admission_packet import generate_admission_packet
from twse_stock_env import TWSESerialEnv


def test_twse_records_are_governed_ohlcv():
    records = load_twse_data(periods=8)
    dataset = build_dataset_config(records)
    assert dataset["dataset_id"] == "twse-offline-dataset-001"
    assert len({record["instrument"] for record in records}) >= 5
    assert {"open", "high", "low", "close", "volume"}.issubset(records[0])


def test_twse_env_import_safe_without_finrl():
    env = TWSESerialEnv([{"instrument": "2330"}])
    assert "records" in env.reset()


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
    assert admission_packet["environment"] == "offline_review"
    assert admission_packet["can_proceed"] is False
    assert admission_packet["allowed_next_action"] == "offline_registry_review_only"
    assert "annual_return" in admission_packet["evaluation_summary"]
    assert "max_drawdown" in admission_packet["evaluation_summary"]


def test_admission_packet_generation(tmp_path: Path):
    run_id = "test-run"
    summary = {"sharpe": 1.0}
    packet = generate_admission_packet(
        run_id,
        summary,
        "test-artifact",
        output_path=tmp_path / "admission_packet.json",
        created_at="2026-05-17T00:00:00Z",
    )
    assert packet["packet_id"].startswith("finrl-admission-test-run")
    assert packet["environment"] == "offline_review"
    assert packet["gate_state"] == "closed"
    assert packet["can_proceed"] is False
