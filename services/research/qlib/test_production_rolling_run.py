"""Tests for OSS-QLIB-V2-001 production rolling run and admission packet."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_DIR = Path(__file__).resolve().parent
for path in (REPO_ROOT, SERVICE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from production_rolling_run import (  # noqa: E402
    DEFAULT_DATASET_PATH,
    ProductionRollingRunError,
    run_production,
)
from registry_admission_packet import (  # noqa: E402
    REQUIRED_EVIDENCE,
    build_admission_packet,
    emit_admission_packet,
    validate_admission_packet,
)
from services.research.experiments.models import validate_experiment_run_payload  # noqa: E402


@pytest.fixture(scope="module")
def production_run() -> dict:
    return run_production(
        "manifest",
        "2024-01-02",
        "2026-01-05",
        63,
        created_at="2026-05-17T11:30:00Z",
    )


def test_run_production_returns_experiment_run_with_per_window_metrics(production_run: dict) -> None:
    validate_experiment_run_payload(production_run)
    assert production_run["task_id"] == "OSS-QLIB-V2-001"
    assert production_run["backend_id"] == "qlib_production_rolling"

    metadata = production_run["metadata"]
    dataset = metadata["production_dataset"]
    assert dataset["dataset_id"] == "dataset:tw-equity-ohlcv-top50-2024-daily"
    assert dataset["num_instruments"] >= 50
    assert dataset["history_years"] >= 2.0
    assert dataset["min_periods_per_instrument"] >= 504
    assert dataset["production_scale_satisfied"] is True

    windows = metadata["production_rolling_windows"]
    assert windows
    assert all("rolling_sharpe" in window and "rolling_ic" in window for window in windows)
    assert any(window["rolling_sharpe"] > 0 for window in windows)
    assert metadata["rolling_metric_summary"]["positive_rolling_sharpe_windows"] > 0


def test_run_production_registers_model_artifact_projection(production_run: dict) -> None:
    metadata = production_run["metadata"]
    model = metadata["model_artifact"]
    model_ref = metadata["model_artifact_ref"]

    assert model["artifact_type"] == "model_artifact"
    assert model["artifact_state"] == "draft"
    assert model["checksum"].startswith("sha256:")
    assert model["deployment_summary"]["current_stage"] == "none"
    assert model["lineage"]["source_strategy_spec_id"] == "qlib-tw-cross-sectional-alpha-spec-v1"
    assert "dataset:tw-equity-ohlcv-top50-2024-daily" in model["lineage"]["source_dataset_refs"]
    assert production_run["run_id"] in model["lineage"]["source_run_ids"]
    assert model_ref["artifact_type"] == "model_artifact"
    assert model_ref["artifact_ref"] in production_run["artifact_refs"]


def test_run_production_rejects_dataset_not_bound_to_mgmt_manifest() -> None:
    dataset = json.loads(DEFAULT_DATASET_PATH.read_text(encoding="utf-8"))
    dataset["dataset_id"] = "dataset:synthetic-not-mgmt-qlib-001"

    with pytest.raises(ProductionRollingRunError, match="does not match MGMT-QLIB-001"):
        run_production(
            "manifest",
            "2024-01-02",
            "2026-01-05",
            63,
            dataset=dataset,
            created_at="2026-05-17T11:30:00Z",
        )


def test_build_admission_packet_conforms_to_promotion_readiness_shape(production_run: dict) -> None:
    packet = build_admission_packet(
        production_run,
        created_at="2026-05-17T11:45:00Z",
    )
    assert validate_admission_packet(packet) == []
    assert packet["schema_version"] == "PromotionReadinessPacket.v1"
    assert packet["target_type"] == "artifact"
    assert packet["environment"] == "paper"
    assert packet["required_evidence"] == REQUIRED_EVIDENCE
    assert packet["missing_evidence"] == []
    assert packet["can_proceed"] is True
    assert packet["registry_request"]["artifact_type"] == "model_artifact"
    assert packet["registry_request"]["requested_transition"] == "draft_to_candidate"
    assert packet["registry_request"]["registry_write_performed"] is False
    assert packet["candidate_artifact"]["checksum"].startswith("sha256:")
    assert packet["safety_assertions"]["no_registry_write"] is True
    assert packet["safety_assertions"]["no_gpu"] is True


def test_emit_admission_packet_writes_json(tmp_path: Path, production_run: dict) -> None:
    output = tmp_path / "admission_packet.json"
    packet = emit_admission_packet(
        output,
        experiment_run=production_run,
        created_at="2026-05-17T11:45:00Z",
    )

    assert output.exists()
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted == packet
    assert persisted["model_artifact_ref"]["artifact_type"] == "model_artifact"
