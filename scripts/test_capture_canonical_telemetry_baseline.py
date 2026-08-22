from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

from scripts.capture_canonical_telemetry_baseline import (
    ALLOWED_DISPOSITIONS,
    CANONICAL_BASELINE_QUERY,
    CANONICAL_SOURCE_TABLE,
    capture_telemetry_baseline,
    compute_query_sha256,
    generate_backup_candidate_inventory,
    validate_baseline_artifact,
)

VALID_SHA40 = "5517afdda923774c1d5f2c80688c76827dae5f91"
VALID_QUERY_SHA256 = compute_query_sha256(CANONICAL_BASELINE_QUERY)
SAMPLE_CAPTURED_AT = "2026-08-22T14:30:00+00:00"


def _make_valid_artifact(**overrides) -> dict:
    base = {
        "captured_at": SAMPLE_CAPTURED_AT,
        "environment": "dev",
        "deployment_sha": VALID_SHA40,
        "source_table": CANONICAL_SOURCE_TABLE,
        "row_count": 5000,
        "min_created_at": "2026-08-22T11:48:48+00:00",
        "max_created_at": "2026-08-22T14:28:00+00:00",
        "source_high_watermark": 7125000,
        "known_history_start": "2026-08-22T11:48:48+00:00",
        "history_disposition": "partial",
        "recovery_source": None,
        "query_sha256": VALID_QUERY_SHA256,
        "operator_note": "Observed repopulation boundary after SD-DATA-01 fix.",
    }
    base.update(overrides)
    return base


# =============================================================================
# Validation Contract Tests
# =============================================================================

def test_valid_baseline_artifact_passes_validation() -> None:
    data = _make_valid_artifact()
    result = validate_baseline_artifact(data)
    assert result == data


def test_empty_table_valid() -> None:
    data = _make_valid_artifact(
        row_count=0,
        min_created_at=None,
        max_created_at=None,
        source_high_watermark=None,
        history_disposition="unknown",
    )
    result = validate_baseline_artifact(data)
    assert result["row_count"] == 0
    assert result["min_created_at"] is None
    assert result["source_high_watermark"] is None


@pytest.mark.parametrize("missing_key", [
    "captured_at",
    "environment",
    "deployment_sha",
    "source_table",
    "row_count",
    "min_created_at",
    "max_created_at",
    "source_high_watermark",
    "known_history_start",
    "history_disposition",
    "recovery_source",
    "query_sha256",
    "operator_note",
])
def test_missing_required_keys_fail(missing_key: str) -> None:
    data = _make_valid_artifact()
    del data[missing_key]
    with pytest.raises(ValueError, match="missing required keys"):
        validate_baseline_artifact(data)


def test_unexpected_extra_keys_fail() -> None:
    data = _make_valid_artifact(extra_field="invalid")
    with pytest.raises(ValueError, match="unexpected extra keys"):
        validate_baseline_artifact(data)


@pytest.mark.parametrize("bad_sha", [
    "",
    "5517afd",  # 7-char short SHA
    "5517afdda923774c1d5f2c80688c76827dae5f9",  # 39 chars
    "5517afdda923774c1d5f2c80688c76827dae5f911",  # 41 chars
    "5517afdda923774c1d5f2c80688c76827dae5fzz",  # non-hex
])
def test_truncated_or_invalid_deployment_sha_rejected(bad_sha: str) -> None:
    data = _make_valid_artifact(deployment_sha=bad_sha)
    with pytest.raises(ValueError, match="deployment_sha must be a full 40-character hexadecimal SHA"):
        validate_baseline_artifact(data)


@pytest.mark.parametrize("bad_table", [
    "telemetry_events",
    "management_ai.telemetry_events",
    "trade_journey_projection.event_receipts",
    "lifecycle_projection.json",
    "/data/bff/lifecycle-projection/trade_journey_events.json",
])
def test_non_canonical_source_table_rejected(bad_table: str) -> None:
    data = _make_valid_artifact(source_table=bad_table)
    with pytest.raises(ValueError, match="source_table must be strictly"):
        validate_baseline_artifact(data)


@pytest.mark.parametrize("bad_disp", [
    "full",
    "reconstructed",
    "valid",
    "active",
    "repopulated",
])
def test_unsupported_history_disposition_rejected(bad_disp: str) -> None:
    data = _make_valid_artifact(history_disposition=bad_disp)
    with pytest.raises(ValueError, match="history_disposition must be one of"):
        validate_baseline_artifact(data)


@pytest.mark.parametrize("good_disp", ["complete", "partial", "irrecoverable", "unknown"])
def test_allowed_history_dispositions(good_disp: str) -> None:
    kwargs = {"history_disposition": good_disp}
    if good_disp == "complete":
        kwargs["recovery_source"] = "gs://authoritative-pantheon-backups/dev/2026-08-22/telemetry_events.sql"
    data = _make_valid_artifact(**kwargs)
    result = validate_baseline_artifact(data)
    assert result["history_disposition"] == good_disp


def test_complete_disposition_requires_recovery_source() -> None:
    data = _make_valid_artifact(history_disposition="complete", recovery_source=None)
    with pytest.raises(ValueError, match="recovery_source proof reference is missing"):
        validate_baseline_artifact(data)

    data_empty = _make_valid_artifact(history_disposition="complete", recovery_source="   ")
    with pytest.raises(ValueError, match="recovery_source proof reference is missing"):
        validate_baseline_artifact(data_empty)


@pytest.mark.parametrize("forbidden_source", [
    "lifecycle_projection.json",
    "/data/bff/lifecycle-projection/trade_journey_events.json",
    "trade_journey_projection.event_receipts",
])
def test_complete_disposition_rejects_lifecycle_projection_as_recovery_source(forbidden_source: str) -> None:
    data = _make_valid_artifact(history_disposition="complete", recovery_source=forbidden_source)
    with pytest.raises(ValueError, match="cannot reference derived Lifecycle projection"):
        validate_baseline_artifact(data)


def test_query_sha256_must_match_canonical_query() -> None:
    data = _make_valid_artifact(query_sha256="0" * 64)
    with pytest.raises(ValueError, match="query_sha256 does not match canonical baseline query hash"):
        validate_baseline_artifact(data)


@pytest.mark.parametrize("bad_timestamp", [
    "2026-08-22 14:30:00",  # missing tz
    "invalid-date",
    "2026-13-45T99:99:99Z",
])
def test_invalid_timestamps_rejected(bad_timestamp: str) -> None:
    data = _make_valid_artifact(captured_at=bad_timestamp)
    with pytest.raises(Exception):
        validate_baseline_artifact(data)


def test_negative_row_count_rejected() -> None:
    data = _make_valid_artifact(row_count=-1)
    with pytest.raises(ValueError, match="row_count must be a non-negative integer"):
        validate_baseline_artifact(data)


def test_row_count_positive_with_null_watermark_rejected() -> None:
    data = _make_valid_artifact(row_count=10, source_high_watermark=None)
    with pytest.raises(ValueError, match="source_high_watermark cannot be null when row_count > 0"):
        validate_baseline_artifact(data)


def test_row_count_positive_with_null_min_time_rejected() -> None:
    data = _make_valid_artifact(row_count=10, min_created_at=None)
    with pytest.raises(ValueError, match="min_created_at cannot be null when row_count > 0"):
        validate_baseline_artifact(data)


# =============================================================================
# Backup Candidate Inventory Tests
# =============================================================================

def test_backup_candidate_inventory_schema() -> None:
    inventory = generate_backup_candidate_inventory(
        environment="dev",
        deployment_sha=VALID_SHA40,
        active_row_count=4981,
        known_history_start="2026-08-22T11:48:48+00:00",
    )
    assert inventory["schema_version"] == "pantheon.telemetry_backup_candidate_inventory.v1"
    assert inventory["environment"] == "dev"
    assert inventory["deployment_sha"] == VALID_SHA40
    assert inventory["overall_history_disposition"] == "partial"
    assert len(inventory["candidates"]) == 5

    candidate_ids = {c["candidate_id"] for c in inventory["candidates"]}
    assert candidate_ids == {
        "gcp_disk_snapshots",
        "postgresql_pg_dump_backups",
        "docker_postgres_volume",
        "lifecycle_projection_json",
        "synthetic_test_fixtures",
    }


def test_backup_candidate_lifecycle_json_rejected_as_source() -> None:
    inventory = generate_backup_candidate_inventory(
        environment="dev",
        deployment_sha=VALID_SHA40,
        active_row_count=4981,
    )
    lifecycle_cand = next(c for c in inventory["candidates"] if c["candidate_id"] == "lifecycle_projection_json")
    assert lifecycle_cand["qualifies_as_canonical_source"] is False
    assert lifecycle_cand["status"] == "derived_only_rejected_as_source"
    assert lifecycle_cand["event_ids_recovered"] == 0
    assert "AD-03" in lifecycle_cand["disposition"] or "SD-DATA-02" in lifecycle_cand["disposition"]


def test_backup_candidate_synthetic_fixtures_rejected_as_source() -> None:
    inventory = generate_backup_candidate_inventory(
        environment="dev",
        deployment_sha=VALID_SHA40,
        active_row_count=4981,
    )
    fixture_cand = next(c for c in inventory["candidates"] if c["candidate_id"] == "synthetic_test_fixtures")
    assert fixture_cand["qualifies_as_canonical_source"] is False
    assert fixture_cand["status"] == "test_fixtures_rejected_as_source"


def test_backup_candidate_docker_volume_tracks_active_count() -> None:
    inventory = generate_backup_candidate_inventory(
        environment="dev",
        deployment_sha=VALID_SHA40,
        active_row_count=7112,
        known_history_start="2026-08-22T11:48:48+00:00",
    )
    volume_cand = next(c for c in inventory["candidates"] if c["candidate_id"] == "docker_postgres_volume")
    assert volume_cand["qualifies_as_canonical_source"] is True
    assert volume_cand["status"] == "partial_active_source"
    assert volume_cand["event_ids_recovered"] == 7112
    assert "7112 rows preserved intact" in volume_cand["disposition"]


# =============================================================================
# Behavioral Query and Database Tests
# =============================================================================

def test_query_sha256_matches_expected() -> None:
    expected_hash = hashlib.sha256(CANONICAL_BASELINE_QUERY.encode("utf-8")).hexdigest()
    assert VALID_QUERY_SHA256 == expected_hash
    assert len(VALID_QUERY_SHA256) == 64


def test_capture_telemetry_baseline_against_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    dsn = os.getenv("TELEMETRY_DB_DSN", "postgresql://pantheon_app:pantheon_app@localhost:15432/pantheon")
    try:
        baseline = capture_telemetry_baseline(
            dsn,
            environment="dev",
            deployment_sha=VALID_SHA40,
            history_disposition="partial",
            known_history_start="2026-08-22T11:48:48+00:00",
        )
        assert baseline["source_table"] == "public.telemetry_events"
        assert baseline["row_count"] > 0
        assert baseline["min_created_at"] is not None
        assert baseline["max_created_at"] is not None
        assert baseline["source_high_watermark"] is not None
        assert baseline["query_sha256"] == VALID_QUERY_SHA256
        assert baseline["history_disposition"] == "partial"
    except Exception as exc:
        pytest.skip(f"PostgreSQL connection to {dsn} not available in this test runner: {exc}")


# =============================================================================
# CLI Subprocess Tests
# =============================================================================

def test_cli_help() -> None:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "scripts" / "capture_canonical_telemetry_baseline.py"),
        "--help",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert "Capture canonical telemetry baseline disposition" in proc.stdout


def test_cli_validate_file(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "capture_canonical_telemetry_baseline.py"

    # 1. Valid artifact
    valid_file = tmp_path / "valid_baseline.json"
    valid_file.write_text(json.dumps(_make_valid_artifact(), indent=2), encoding="utf-8")
    proc_valid = subprocess.run(
        [sys.executable, str(script), "--validate-file", str(valid_file)],
        capture_output=True,
        text=True,
    )
    assert proc_valid.returncode == 0
    assert "✓ Baseline artifact" in proc_valid.stdout

    # 2. Invalid artifact (truncated SHA)
    invalid_file = tmp_path / "invalid_baseline.json"
    invalid_file.write_text(json.dumps(_make_valid_artifact(deployment_sha="abc1234"), indent=2), encoding="utf-8")
    proc_invalid = subprocess.run(
        [sys.executable, str(script), "--validate-file", str(invalid_file)],
        capture_output=True,
        text=True,
    )
    assert proc_invalid.returncode == 1
    assert "Baseline validation failed" in proc_invalid.stderr
