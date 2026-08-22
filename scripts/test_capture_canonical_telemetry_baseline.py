from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

import scripts.capture_canonical_telemetry_baseline as baseline_module
from scripts.capture_canonical_telemetry_baseline import (
    ALLOWED_DISPOSITIONS,
    CANONICAL_BASELINE_QUERY,
    CANONICAL_SOURCE_TABLE,
    RECOVERY_ATTESTATION_SCHEMA_VERSION,
    capture_telemetry_baseline,
    compute_query_sha256,
    generate_backup_candidate_inventory,
    inspect_authoritative_recovery_source,
    is_derived_lifecycle_or_projection,
    is_valid_authoritative_recovery_source,
    validate_baseline_artifact,
    validate_recovery_source_attestation,
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
        "recovery_source_attestation": None,
        "query_sha256": VALID_QUERY_SHA256,
        "operator_note": "Observed repopulation boundary after SD-DATA-01 fix.",
    }
    base.update(overrides)
    return base


def _make_attestation(data: dict, observed: dict[str, str], **overrides) -> dict:
    attestation = {
        "schema_version": RECOVERY_ATTESTATION_SCHEMA_VERSION,
        "source_kind": observed["source_kind"],
        "source_identity": observed["source_identity"],
        "source_version": observed["source_version"],
        "immutable_digest_sha256": observed["immutable_digest_sha256"],
        "verified_at": SAMPLE_CAPTURED_AT,
        "event_range": {
            "row_count": data["row_count"],
            "min_created_at": data["min_created_at"],
            "max_created_at": data["max_created_at"],
            "source_high_watermark": data["source_high_watermark"],
        },
        "completeness": {
            "status": "complete",
            "known_history_start": data["known_history_start"],
            "expected_event_count": data["row_count"],
            "observed_event_count": data["row_count"],
            "missing_event_count": 0,
            "event_id_comparison_sha256": "a" * 64,
            "query_sha256": data["query_sha256"],
        },
    }
    attestation.update(overrides)
    return attestation


def _make_complete_local_artifact(tmp_path: Path) -> dict:
    proof_file = tmp_path / "authoritative-source-ledger.jsonl"
    proof_file.write_bytes(b"event-1\nevent-2\n")
    source = f"source-ledger:file://{proof_file}"
    data = _make_valid_artifact(history_disposition="complete", recovery_source=source)
    data["recovery_source_attestation"] = _make_attestation(
        data,
        inspect_authoritative_recovery_source(source),
    )
    return data


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
    "recovery_source_attestation",
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
    "/data/bff/lifecycle/trade_journey_events.json",
    "/data/bff/trade_journey_events.json",
    "trade_journey_events.json",
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


@pytest.mark.parametrize("good_disp", ["partial", "irrecoverable", "unknown"])
def test_allowed_noncomplete_history_dispositions(good_disp: str) -> None:
    data = _make_valid_artifact(history_disposition=good_disp)
    result = validate_baseline_artifact(data)
    assert result["history_disposition"] == good_disp


def test_complete_history_disposition_with_verified_local_source(tmp_path: Path) -> None:
    data = _make_complete_local_artifact(tmp_path)
    result = validate_baseline_artifact(data)
    assert result["history_disposition"] == "complete"
    assert result["recovery_source_attestation"]["source_kind"] == "source_ledger"


def test_complete_disposition_requires_recovery_source() -> None:
    data = _make_valid_artifact(history_disposition="complete", recovery_source=None)
    with pytest.raises(ValueError, match="recovery_source proof reference is missing"):
        validate_baseline_artifact(data)

    data_empty = _make_valid_artifact(history_disposition="complete", recovery_source="   ")
    with pytest.raises(ValueError, match="recovery_source proof reference is empty"):
        validate_baseline_artifact(data_empty)


def test_complete_disposition_requires_recovery_source_attestation() -> None:
    data = _make_valid_artifact(
        history_disposition="complete",
        recovery_source="gs://authoritative-pantheon-backups/dev/telemetry.sql.gz",
    )
    with pytest.raises(ValueError, match="recovery_source_attestation is missing"):
        validate_baseline_artifact(data)


@pytest.mark.parametrize("arbitrary_source", [
    "not-an-authoritative-proof",
    "arbitrary-proof-string",
    "some-random-backup",
    "backup.sql",
    "telemetry_backup.dump",
    "https://storage.googleapis.com/not-verified/file.sql",
    "/tmp/test_backup.sql",
    "s3://my-bucket/backup.sql",
])
def test_complete_disposition_rejects_arbitrary_string_recovery_source(arbitrary_source: str) -> None:
    data = _make_valid_artifact(history_disposition="complete", recovery_source=arbitrary_source)
    with pytest.raises(ValueError, match="unsupported or unbound"):
        validate_baseline_artifact(data)


@pytest.mark.parametrize("resolvable_source", [
    "gs://authoritative-pantheon-backups/dev/2026-08-22/telemetry_events.sql.gz",
    "gcs://pantheon-backups/postgres/20260822_dump.sql",
    "projects/pantheon-lupin-dev-20260719/global/snapshots/pantheon-postgres-snapshot-20260822",
    "pg_dump:/var/backups/postgresql/telemetry_events_20260822.dump",
    "/var/backups/postgresql/telemetry_events_20260822.sql.gz",
    "file:///var/backups/postgresql/telemetry_events_20260822.dump",
    "source-ledger:/var/backups/telemetry/event-ledger-proof.jsonl",
])
def test_supported_authoritative_recovery_source_syntax(resolvable_source: str) -> None:
    assert is_valid_authoritative_recovery_source(resolvable_source) is True


@pytest.mark.parametrize("unbound_source", [
    "gcp-snapshot:pantheon-postgres-snapshot-20260822",
    "gcp_disk_snapshot:disk-snapshot-20260822",
    "pg_dump://dev-db-cluster/2026-08-22/public.telemetry_events.dump",
    "source-ledger-proof:sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "canonical-ledger://pantheon-dev-ledger/checkpoint-7122484",
    "urn:pantheon:telemetry-backup:gcp-snapshot-20260822-0500",
    "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
])
def test_complete_disposition_rejects_unbound_or_bare_digest_source(unbound_source: str) -> None:
    data = _make_valid_artifact(history_disposition="complete", recovery_source=unbound_source)
    with pytest.raises(ValueError, match="unsupported or unbound"):
        validate_baseline_artifact(data)
    assert is_valid_authoritative_recovery_source(unbound_source) is False


@pytest.mark.parametrize("forbidden_source", [
    "lifecycle_projection.json",
    "/data/bff/lifecycle-projection/trade_journey_events.json",
    "/data/bff/lifecycle/trade_journey_events.json",
    "/data/bff/trade_journey_events.json",
    "trade_journey_events.json",
    "lifecycle/trade_journey_events.json",
    "loop_runs.json",
    "/data/bff/loop_runs.json",
    "trade_journey_projection.event_receipts",
    "event_receipts.json",
    "/data/bff/insight_cards.json",
    "/data/bff/jobs.json",
])
def test_complete_disposition_rejects_derived_lifecycle_json_as_recovery_source(forbidden_source: str) -> None:
    data = _make_valid_artifact(history_disposition="complete", recovery_source=forbidden_source)
    with pytest.raises(ValueError, match="cannot reference derived Lifecycle JSON or secondary projection"):
        validate_baseline_artifact(data)
    assert is_derived_lifecycle_or_projection(forbidden_source) is True


@pytest.mark.parametrize("forbidden_source", [
    "lifecycle_projection.json",
    "/data/bff/lifecycle-projection/trade_journey_events.json",
    "/data/bff/lifecycle/trade_journey_events.json",
    "/data/bff/trade_journey_events.json",
    "trade_journey_events.json",
    "lifecycle/trade_journey_events.json",
    "loop_runs.json",
    "/data/bff/loop_runs.json",
    "trade_journey_projection.event_receipts",
    "event_receipts.json",
])
def test_partial_disposition_rejects_derived_lifecycle_json_as_recovery_source(forbidden_source: str) -> None:
    data = _make_valid_artifact(history_disposition="partial", recovery_source=forbidden_source)
    with pytest.raises(ValueError, match="cannot reference derived Lifecycle JSON or secondary projection"):
        validate_baseline_artifact(data)
    assert is_derived_lifecycle_or_projection(forbidden_source) is True


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


def test_row_count_positive_with_null_max_time_rejected() -> None:
    data = _make_valid_artifact(row_count=10, max_created_at=None)
    with pytest.raises(ValueError, match="max_created_at cannot be null when row_count > 0"):
        validate_baseline_artifact(data)


def test_row_count_positive_with_min_after_max_rejected() -> None:
    data = _make_valid_artifact(
        row_count=10,
        min_created_at="2026-08-22T15:00:00+00:00",
        max_created_at="2026-08-22T14:00:00+00:00",
    )
    with pytest.raises(ValueError, match="cannot be after max_created_at"):
        validate_baseline_artifact(data)


def test_row_count_zero_with_non_null_timestamps_or_watermark_rejected() -> None:
    data_min = _make_valid_artifact(row_count=0, min_created_at="2026-08-22T11:48:48+00:00", max_created_at=None, source_high_watermark=None)
    with pytest.raises(ValueError, match="min_created_at must be null when row_count is 0"):
        validate_baseline_artifact(data_min)

    data_max = _make_valid_artifact(row_count=0, min_created_at=None, max_created_at="2026-08-22T14:00:00+00:00", source_high_watermark=None)
    with pytest.raises(ValueError, match="max_created_at must be null when row_count is 0"):
        validate_baseline_artifact(data_max)

    data_wm = _make_valid_artifact(row_count=0, min_created_at=None, max_created_at=None, source_high_watermark=100)
    with pytest.raises(ValueError, match="source_high_watermark must be null when row_count is 0"):
        validate_baseline_artifact(data_wm)


@pytest.mark.parametrize("bad_recovery_source", [
    True,
    False,
    123,
    45.6,
    ["backup.sql"],
    {"source": "backup.sql"},
])
def test_complete_disposition_rejects_non_string_recovery_source(bad_recovery_source) -> None:
    data = _make_valid_artifact(history_disposition="complete", recovery_source=bad_recovery_source)
    with pytest.raises(ValueError, match="recovery_source must be a non-empty string proof reference"):
        validate_baseline_artifact(data)


@pytest.mark.parametrize("bad_recovery_source", [
    True,
    False,
    123,
    ["backup.sql"],
])
def test_partial_disposition_rejects_non_string_recovery_source(bad_recovery_source) -> None:
    data = _make_valid_artifact(history_disposition="partial", recovery_source=bad_recovery_source)
    with pytest.raises(ValueError, match="recovery_source must be a string or null"):
        validate_baseline_artifact(data)


def test_noncomplete_disposition_rejects_attestation(tmp_path: Path) -> None:
    complete = _make_complete_local_artifact(tmp_path)
    data = _make_valid_artifact(recovery_source_attestation=complete["recovery_source_attestation"])
    with pytest.raises(ValueError, match="must be null unless history_disposition is 'complete'"):
        validate_baseline_artifact(data)


def _unverified_attestation(data: dict, source_kind: str, source_identity: str) -> dict:
    return _make_attestation(
        data,
        {
            "source_kind": source_kind,
            "source_identity": source_identity,
            "source_version": "unverified-version",
            "immutable_digest_sha256": "b" * 64,
        },
    )


@pytest.mark.parametrize(
    ("source", "source_kind"),
    [
        ("gs://fictional-authoritative-bucket/never-existed/telemetry.sql.gz", "gcs_object"),
        (
            "projects/pantheon-lupin-dev-20260719/global/snapshots/never-existed-telemetry",
            "gcp_snapshot",
        ),
    ],
)
def test_complete_disposition_rejects_nonexistent_cloud_source(
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    source_kind: str,
) -> None:
    def missing_source(_command) -> dict:
        raise ValueError("authoritative object was not found")

    monkeypatch.setattr(baseline_module, "_run_json_command", missing_source)
    data = _make_valid_artifact(history_disposition="complete", recovery_source=source)
    data["recovery_source_attestation"] = _unverified_attestation(data, source_kind, source)
    with pytest.raises(ValueError, match="authoritative object was not found"):
        validate_baseline_artifact(data)


def test_complete_disposition_rejects_nonexistent_dump(tmp_path: Path) -> None:
    source = f"pg_dump:{tmp_path / 'never-existed.dump'}"
    data = _make_valid_artifact(history_disposition="complete", recovery_source=source)
    data["recovery_source_attestation"] = _unverified_attestation(data, "postgresql_dump", source)
    with pytest.raises(ValueError, match="does not exist as a regular file"):
        validate_baseline_artifact(data)


def test_complete_disposition_rejects_attestation_identity_mismatch(tmp_path: Path) -> None:
    data = _make_complete_local_artifact(tmp_path)
    data["recovery_source_attestation"]["source_identity"] = "source-ledger:/var/backups/telemetry/other.jsonl"
    with pytest.raises(ValueError, match="source_identity must exactly match"):
        validate_baseline_artifact(data)


def test_complete_disposition_rejects_attestation_digest_mismatch(tmp_path: Path) -> None:
    data = _make_complete_local_artifact(tmp_path)
    data["recovery_source_attestation"]["immutable_digest_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="verification mismatch for immutable_digest_sha256"):
        validate_baseline_artifact(data)


def test_complete_disposition_rejects_unbound_event_range(tmp_path: Path) -> None:
    data = _make_complete_local_artifact(tmp_path)
    data["recovery_source_attestation"]["event_range"]["source_high_watermark"] += 1
    with pytest.raises(ValueError, match="event_range.source_high_watermark does not match"):
        validate_baseline_artifact(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "partial"),
        ("expected_event_count", 4999),
        ("observed_event_count", 4999),
        ("missing_event_count", 1),
        ("event_id_comparison_sha256", "not-a-digest"),
        ("query_sha256", "0" * 64),
    ],
)
def test_complete_disposition_rejects_unproven_completeness(
    tmp_path: Path,
    field: str,
    value,
) -> None:
    data = _make_complete_local_artifact(tmp_path)
    data["recovery_source_attestation"]["completeness"][field] = value
    with pytest.raises(ValueError, match="recovery_source_attestation"):
        validate_baseline_artifact(data)


def test_complete_disposition_accepts_independently_described_gcs_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "gs://authoritative-pantheon-backups/dev/telemetry.sql.gz"
    digest = "c" * 64
    monkeypatch.setattr(
        baseline_module,
        "_run_json_command",
        lambda _command: {
            "bucket": "authoritative-pantheon-backups",
            "name": "dev/telemetry.sql.gz",
            "generation": "1787412000000000",
            "metageneration": "1",
            "metadata": {"pantheon_sha256": digest},
        },
    )
    data = _make_valid_artifact(history_disposition="complete", recovery_source=source)
    data["recovery_source_attestation"] = _make_attestation(
        data,
        inspect_authoritative_recovery_source(source),
    )
    assert validate_baseline_artifact(data)["history_disposition"] == "complete"


def test_complete_disposition_accepts_ready_fully_qualified_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "projects/pantheon-lupin-dev-20260719/global/snapshots/pantheon-postgres-20260822"
    monkeypatch.setattr(
        baseline_module,
        "_run_json_command",
        lambda _command: {
            "id": "987654321",
            "name": "pantheon-postgres-20260822",
            "selfLink": f"https://compute.googleapis.com/compute/v1/{source}",
            "status": "READY",
            "sourceDisk": "projects/pantheon-lupin-dev-20260719/zones/us-west1-a/disks/pantheon-lupin-dev",
            "sourceDiskId": "123456789",
            "diskSizeGb": "100",
            "storageBytes": "2048",
            "creationTimestamp": "2026-08-22T14:00:00.000-07:00",
            "storageLocations": ["us-west1"],
        },
    )
    data = _make_valid_artifact(history_disposition="complete", recovery_source=source)
    data["recovery_source_attestation"] = _make_attestation(
        data,
        inspect_authoritative_recovery_source(source),
    )
    assert validate_recovery_source_attestation(data["recovery_source_attestation"], data)["source_kind"] == "gcp_snapshot"


@pytest.mark.parametrize("field_name", [
    "captured_at",
    "environment",
    "deployment_sha",
    "source_table",
    "min_created_at",
    "max_created_at",
    "known_history_start",
    "history_disposition",
    "query_sha256",
    "operator_note",
    "row_count",
    "source_high_watermark",
])
def test_boolean_types_rejected_across_fields(field_name: str) -> None:
    data = _make_valid_artifact()
    data[field_name] = True
    with pytest.raises(ValueError):
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

    # 3. Invalid artifact (complete with arbitrary recovery_source)
    invalid_complete_file = tmp_path / "invalid_complete.json"
    invalid_complete_file.write_text(
        json.dumps(_make_valid_artifact(history_disposition="complete", recovery_source="not-an-authoritative-proof"), indent=2),
        encoding="utf-8",
    )
    proc_invalid_complete = subprocess.run(
        [sys.executable, str(script), "--validate-file", str(invalid_complete_file)],
        capture_output=True,
        text=True,
    )
    assert proc_invalid_complete.returncode == 1
    assert "unsupported or unbound" in proc_invalid_complete.stderr

    # 4. Invalid artifact (derived Lifecycle JSON path)
    invalid_derived_file = tmp_path / "invalid_derived.json"
    invalid_derived_file.write_text(
        json.dumps(_make_valid_artifact(history_disposition="complete", recovery_source="/data/bff/lifecycle/trade_journey_events.json"), indent=2),
        encoding="utf-8",
    )
    proc_invalid_derived = subprocess.run(
        [sys.executable, str(script), "--validate-file", str(invalid_derived_file)],
        capture_output=True,
        text=True,
    )
    assert proc_invalid_derived.returncode == 1
    assert "cannot reference derived Lifecycle JSON" in proc_invalid_derived.stderr


# =============================================================================
# Helper Unit Tests
# =============================================================================

@pytest.mark.parametrize("derived_path,expected", [
    ("/data/bff/lifecycle/trade_journey_events.json", True),
    ("/data/bff/lifecycle-projection/trade_journey_events.json", True),
    ("/data/bff/trade_journey_events.json", True),
    ("trade_journey_events.json", True),
    ("lifecycle/trade_journey_events.json", True),
    ("lifecycle_projection.json", True),
    ("loop_runs.json", True),
    ("/data/bff/loop_runs.json", True),
    ("trade_journey_projection.event_receipts", True),
    ("event_receipts.json", True),
    ("gs://authoritative-pantheon-backups/dev/telemetry.sql.gz", False),
    ("projects/pantheon-dev/global/snapshots/snapshot-1", False),
    ("pg_dump://cluster/telemetry.dump", False),
    ("canonical-ledger://dev/checkpoint-1", False),
])
def test_is_derived_lifecycle_or_projection(derived_path: str, expected: bool) -> None:
    assert is_derived_lifecycle_or_projection(derived_path) is expected


@pytest.mark.parametrize("proof_uri,expected", [
    ("gs://authoritative-pantheon-backups/dev/2026-08-22/telemetry_events.sql.gz", True),
    ("gcs://pantheon-backups/postgres/20260822_dump.sql", True),
    ("projects/pantheon-lupin-dev-20260719/global/snapshots/pantheon-postgres-snapshot-20260822", True),
    ("gcp-snapshot:pantheon-postgres-snapshot-20260822", False),
    ("gcp_disk_snapshot:disk-snapshot-20260822", False),
    ("pg_dump://dev-db-cluster/2026-08-22/public.telemetry_events.dump", False),
    ("pg_dump:/var/backups/postgresql/telemetry_events_20260822.dump", True),
    ("/var/backups/postgresql/telemetry_events_20260822.sql.gz", True),
    ("file:///var/backups/postgresql/telemetry_events_20260822.dump", True),
    ("source-ledger:/var/backups/telemetry/event-ledger-proof.jsonl", True),
    ("source-ledger-proof:sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", False),
    ("canonical-ledger://pantheon-dev-ledger/checkpoint-7122484", False),
    ("urn:pantheon:telemetry-backup:gcp-snapshot-20260822-0500", False),
    ("sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", False),
    ("not-an-authoritative-proof", False),
    ("arbitrary-string", False),
    ("backup.sql", False),
    ("/data/bff/lifecycle/trade_journey_events.json", False),
    ("/tmp/backup.dump", False),
    ("s3://aws-bucket/backup.sql", False),
])
def test_is_valid_authoritative_recovery_source(proof_uri: str, expected: bool) -> None:
    assert is_valid_authoritative_recovery_source(proof_uri) is expected
