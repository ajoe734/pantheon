"""Tests for OSS-STAT-V2-001 production cointegration and admission packet."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RESEARCH_DIR = REPO_ROOT / "services" / "research"
SERVICE_DIR = Path(__file__).resolve().parent
sys.path = [path for path in sys.path if Path(path or ".").resolve() != RESEARCH_DIR]
if "statsmodels" in sys.modules:
    sys.modules.pop("statsmodels")
pytest.importorskip("statsmodels.tsa.stattools")

for path in (REPO_ROOT, SERVICE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from production_cointegration import (  # noqa: E402
    DEFAULT_DATASET_PATH,
    DEFAULT_ROLLING_WINDOW,
    ProductionCointegrationError,
    run_production,
)
from registry_admission_packet import (  # noqa: E402
    REQUIRED_EVIDENCE,
    build_admission_packet,
    emit_admission_packet,
    validate_admission_packet,
)


@pytest.fixture(scope="module")
def signal_snapshot() -> dict:
    return run_production(
        "twse_large_cap_pairs",
        DEFAULT_ROLLING_WINDOW,
        created_at="2026-05-17T16:30:00Z",
    )


def test_run_production_returns_signal_snapshot_with_pair_metrics(signal_snapshot: dict) -> None:
    assert signal_snapshot["task_id"] == "OSS-STAT-V2-001"
    assert signal_snapshot["backend_id"] == "statsmodels_production_cointegration"
    assert signal_snapshot["artifact_type"] == "signal_snapshot"
    assert signal_snapshot["rolling_window"] == 504
    assert signal_snapshot["checksum"].startswith("sha256:")

    pairs = signal_snapshot["pairs"]
    assert len(pairs) == 10
    assert all({"pair_id", "p_value", "half_life", "spread_zscore"} <= set(pair) for pair in pairs)
    assert all(pair["observations"] == 504 for pair in pairs)
    assert all(pair["p_value"] < 0.05 for pair in signal_snapshot["top_cointegrated_pairs"][:3])
    assert signal_snapshot["cointegration_summary"]["cointegrated_pair_count"] >= 3


def test_run_production_uses_mgmt_qlib_dataset_manifest(signal_snapshot: dict) -> None:
    dataset = signal_snapshot["dataset"]
    assert dataset["dataset_id"] == "dataset:tw-equity-ohlcv-top50-2024-daily"
    assert dataset["manifest_id"] == "qlib-dataset-manifest:dataset-tw-equity-ohlcv-top50-2024-daily"
    assert dataset["num_instruments"] >= 50
    assert dataset["min_periods_per_instrument"] >= 504
    assert dataset["production_scale_satisfied"] is True
    assert "dataset:tw-equity-ohlcv-top50-2024-daily" in dataset["source_dataset_refs"]


def test_run_production_registers_signal_snapshot_projection(signal_snapshot: dict) -> None:
    registry_entry = signal_snapshot["registry_entry"]
    signal_ref = signal_snapshot["signal_snapshot_ref"]

    assert registry_entry["artifact_type"] == "signal_snapshot"
    assert registry_entry["artifact_state"] == "draft"
    assert registry_entry["checksum"] == signal_snapshot["checksum"]
    assert registry_entry["deployment_summary"]["current_stage"] == "none"
    assert registry_entry["lineage"]["source_strategy_spec_id"] == "qlib-tw-cross-sectional-alpha-spec-v1"
    assert "dataset:tw-equity-ohlcv-top50-2024-daily" in registry_entry["lineage"]["source_dataset_refs"]
    assert signal_ref["artifact_type"] == "signal_snapshot"
    assert signal_ref["registry_id"] == registry_entry["registry_id"]


def test_run_production_rejects_dataset_not_bound_to_manifest() -> None:
    dataset = json.loads(DEFAULT_DATASET_PATH.read_text(encoding="utf-8"))
    dataset["dataset_id"] = "dataset:synthetic-not-mgmt-qlib-001"

    with pytest.raises(ProductionCointegrationError, match="does not match MGMT-QLIB-001"):
        run_production(
            "twse_large_cap_pairs",
            DEFAULT_ROLLING_WINDOW,
            dataset=dataset,
            created_at="2026-05-17T16:30:00Z",
        )


def test_run_production_rejects_window_larger_than_aligned_history() -> None:
    with pytest.raises(ProductionCointegrationError, match="rolling_window requires"):
        run_production(
            "twse_large_cap_pairs",
            9999,
            created_at="2026-05-17T16:30:00Z",
        )


def test_build_admission_packet_conforms_to_promotion_readiness_shape(signal_snapshot: dict) -> None:
    packet = build_admission_packet(
        signal_snapshot,
        created_at="2026-05-17T16:45:00Z",
    )
    assert validate_admission_packet(packet) == []
    assert packet["schema_version"] == "PromotionReadinessPacket.v1"
    assert packet["target_type"] == "artifact"
    assert packet["environment"] == "paper"
    assert packet["required_evidence"] == REQUIRED_EVIDENCE
    assert packet["missing_evidence"] == []
    assert packet["can_proceed"] is True
    assert packet["registry_request"]["artifact_type"] == "signal_snapshot"
    assert packet["registry_request"]["requested_transition"] == "draft_to_candidate"
    assert packet["registry_request"]["registry_write_performed"] is False
    assert packet["candidate_artifact"]["checksum"].startswith("sha256:")
    assert packet["cointegration_summary"]["cointegrated_pair_count"] >= 3
    assert packet["safety_assertions"]["no_registry_write"] is True
    assert packet["safety_assertions"]["no_gpu"] is True


def test_emit_admission_packet_writes_json(tmp_path: Path, signal_snapshot: dict) -> None:
    output = tmp_path / "admission_packet.json"
    packet = emit_admission_packet(
        output,
        signal_snapshot=signal_snapshot,
        created_at="2026-05-17T16:45:00Z",
    )

    assert output.exists()
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted == packet
    assert persisted["signal_snapshot_ref"]["artifact_type"] == "signal_snapshot"
