from __future__ import annotations

import json
import os
from pathlib import Path
import pytest

from scripts.lifecycle_projector_legacy_retire import (
    RetirementValidationError,
    compute_inventory_digest,
    execute_retirement,
    run_retirement,
    validate_path_safety,
)


def _seed_legacy_projection_fixture(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "controller_state.json").write_text(
        json.dumps({"controller_id": "canonical-lifecycle-projector", "checkpoint": 100}),
        encoding="utf-8",
    )
    (root / "health_state.json").write_text(
        json.dumps({"ready": True, "worker_status": "ready"}),
        encoding="utf-8",
    )
    (root / "trade_journey_events.json").write_text("[]", encoding="utf-8")
    (root / "loop_runs.json").write_text("[]", encoding="utf-8")
    (root / "cutover-legacy-baseline.snapshot.json").write_text("{}", encoding="utf-8")

    gen1 = root / "gen-000001"
    gen1.mkdir()
    (gen1 / "trade_journey_events.json").write_text("[]", encoding="utf-8")
    (gen1 / "loop_runs.json").write_text("[]", encoding="utf-8")
    (gen1 / "controller_state.json").write_text("{}", encoding="utf-8")

    staging = root / "staging-temp"
    staging.mkdir()
    (staging / "temp.json").write_text("{}", encoding="utf-8")

    current_symlink = root / "current"
    os.symlink("gen-000001", current_symlink)


def test_safety_validation_rejects_broad_paths():
    with pytest.raises(RetirementValidationError, match="broad or system directory"):
        validate_path_safety(Path("/"), allow_custom_root=True)
    with pytest.raises(RetirementValidationError, match="broad or system directory"):
        validate_path_safety(Path("/data"), allow_custom_root=True)
    with pytest.raises(RetirementValidationError, match="broad or system directory"):
        validate_path_safety(Path("/data/bff"), allow_custom_root=True)
    with pytest.raises(RetirementValidationError, match="broad or system directory"):
        validate_path_safety(Path("/var"), allow_custom_root=True)
    with pytest.raises(RetirementValidationError, match="broad or system directory"):
        validate_path_safety(Path("/workspace"), allow_custom_root=True)


def test_safety_validation_rejects_globs_and_unresolved_vars():
    with pytest.raises(RetirementValidationError, match="unresolved environment variables"):
        validate_path_safety(Path("/data/bff/$MY_VAR/test"), allow_custom_root=True)
    with pytest.raises(RetirementValidationError, match="prohibited glob"):
        validate_path_safety(Path("/data/bff/lifecycle-*"), allow_custom_root=True)


def test_safety_validation_rejects_canonical_sources():
    with pytest.raises(RetirementValidationError, match="canonical source pattern"):
        validate_path_safety(Path("/tmp/test/telemetry_events"), allow_custom_root=True)
    with pytest.raises(RetirementValidationError, match="canonical source pattern"):
        validate_path_safety(Path("/tmp/test/pgdata"), allow_custom_root=True)


def test_scan_fails_closed_on_unallowlisted_root_file(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)
    (root / "unauthorized_data.json").write_text("{}", encoding="utf-8")

    with pytest.raises(RetirementValidationError, match="Unexpected un-allowlisted file"):
        run_retirement(root_path=root, action="archive", execute=False, allow_custom_root=True)


def test_scan_fails_closed_on_unallowlisted_directory(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)
    (root / "random_dir").mkdir()

    with pytest.raises(RetirementValidationError, match="Unexpected un-allowlisted directory"):
        run_retirement(root_path=root, action="archive", execute=False, allow_custom_root=True)


def test_scan_fails_closed_on_unallowlisted_generation_file(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)
    (root / "gen-000001" / "rogue_script.sh").write_text("#!/bin/sh", encoding="utf-8")

    with pytest.raises(RetirementValidationError, match="Unexpected non-allowlisted file"):
        run_retirement(root_path=root, action="archive", execute=False, allow_custom_root=True)


def test_dry_run_scans_inventory_and_computes_digest(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)

    manifest = run_retirement(
        root_path=root,
        action="archive",
        execute=False,
        allow_custom_root=True,
    )

    assert manifest["schema_version"] == "pantheon.lifecycle-projector-legacy-retirement.v1"
    assert manifest["task_id"] == "LIFECYCLE-PROJ-RETIRE-001"
    assert manifest["mode"] == "dry_run"
    assert manifest["action"] == "archive"
    assert manifest["total_files"] == 10
    assert len(manifest["inventory_sha256"]) == 64
    assert manifest["execution_receipt"] is None
    assert (root / "controller_state.json").exists()
    assert (root / "current").is_symlink()


def test_execute_requires_dry_run_manifest(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)

    with pytest.raises(RetirementValidationError, match="requires --dry-run-manifest"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_token="Human/Ops-approved",
            approver="Human/Ops",
            dry_run_manifest_path=None,
            allow_custom_root=True,
        )


def test_execute_rejects_drifted_inventory(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root, action="archive", execute=False, allow_custom_root=True
    )
    manifest_path = tmp_path / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    # Modify a file after dry-run
    (root / "trade_journey_events.json").write_text('[{"modified": true}]', encoding="utf-8")

    with pytest.raises(RetirementValidationError, match="Live inventory drifted"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_token="Human/Ops-approved",
            approver="Human/Ops",
            dry_run_manifest_path=manifest_path,
            allow_custom_root=True,
        )


def test_execute_rejects_unauthorized_approver_or_token(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root, action="archive", execute=False, allow_custom_root=True
    )
    manifest_path = tmp_path / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    # Spoofed approver
    with pytest.raises(RetirementValidationError, match="not in the authorized operator allowlist"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_token="Human/Ops-approved",
            approver="malicious_user",
            dry_run_manifest_path=manifest_path,
            allow_custom_root=True,
        )

    # Invalid token format
    with pytest.raises(RetirementValidationError, match="valid structured approval token"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_token="any-random-string",
            approver="Human/Ops",
            dry_run_manifest_path=manifest_path,
            allow_custom_root=True,
        )


def test_execute_archive_moves_to_quarantine_with_receipt(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)

    quarantine_dir = tmp_path / "quarantine"
    dry_run_manifest = run_retirement(
        root_path=root,
        action="archive",
        execute=False,
        quarantine_dir=quarantine_dir,
        allow_custom_root=True,
    )
    manifest_path = tmp_path / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    manifest = run_retirement(
        root_path=root,
        action="archive",
        execute=True,
        approval_token="Human/Ops-approved:LIFECYCLE-PROJ-RETIRE-001",
        approver="Human/Ops",
        dry_run_manifest_path=manifest_path,
        quarantine_dir=quarantine_dir,
        allow_custom_root=True,
    )

    assert manifest["mode"] == "executed"
    receipt = manifest["execution_receipt"]
    assert receipt is not None
    assert receipt["status"] == "completed"
    assert receipt["action"] == "quarantine"
    assert receipt["recovery_possible"] is True
    assert receipt["approver"] == "Human/Ops"
    assert receipt["approval_token"] == "Human/Ops-approved:LIFECYCLE-PROJ-RETIRE-001"
    assert receipt["bound_inventory_sha256"] == dry_run_manifest["inventory_sha256"]
    assert (quarantine_dir / "controller_state.json").exists()
    assert not (root / "controller_state.json").exists()


def test_execute_delete_removes_files_with_receipt(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root,
        action="delete",
        execute=False,
        allow_custom_root=True,
    )
    manifest_path = tmp_path / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    manifest = run_retirement(
        root_path=root,
        action="delete",
        execute=True,
        approval_token="Human/Ops-approved",
        approver="Human/Ops",
        dry_run_manifest_path=manifest_path,
        allow_custom_root=True,
    )

    assert manifest["mode"] == "executed"
    receipt = manifest["execution_receipt"]
    assert receipt is not None
    assert receipt["status"] == "completed"
    assert receipt["action"] == "delete"
    assert receipt["recovery_possible"] is False
    assert not (root / "controller_state.json").exists()
    assert not (root / "gen-000001").exists()


def test_execute_delete_preserves_unlisted_file_and_nonempty_directory_mutation_toctou(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root,
        action="delete",
        execute=False,
        allow_custom_root=True,
    )
    manifest_path = tmp_path / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    # Mutation / TOCTOU simulation: an unlisted file is placed into gen-000001 after scan
    unlisted_file = root / "gen-000001" / "unlisted_after_scan.json"
    unlisted_file.write_text('{"unlisted": true}', encoding="utf-8")

    # When execute_retirement runs against the previously scanned inventory:
    receipt = execute_retirement(
        root,
        dry_run_manifest["items"],
        action="delete",
        approver="Human/Ops",
        approval_token="Human/Ops-approved:LIFECYCLE-PROJ-RETIRE-001",
        dry_run_manifest_path=str(manifest_path),
        bound_inventory_sha256=dry_run_manifest["inventory_sha256"],
    )

    assert receipt["status"] == "completed"
    assert receipt["action"] == "delete"
    # Unlisted file must NOT be deleted
    assert unlisted_file.exists()
    assert unlisted_file.read_text(encoding="utf-8") == '{"unlisted": true}'
    # Non-empty directory must NOT be removed
    assert (root / "gen-000001").is_dir()
    # Scanned items that were in the inventory should be deleted
    assert not (root / "controller_state.json").exists()
    assert not (root / "gen-000001" / "trade_journey_events.json").exists()


def test_run_retirement_execute_rejects_unlisted_file_mutation(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root,
        action="delete",
        execute=False,
        allow_custom_root=True,
    )
    manifest_path = tmp_path / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    # Add unlisted file after dry-run
    (root / "gen-000001" / "rogue.json").write_text('{"rogue": true}', encoding="utf-8")

    with pytest.raises(RetirementValidationError):
        run_retirement(
            root_path=root,
            action="delete",
            execute=True,
            approval_token="Human/Ops-approved",
            approver="Human/Ops",
            dry_run_manifest_path=manifest_path,
            allow_custom_root=True,
        )


def test_run_retirement_execute_rejects_action_mismatch(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)

    # 1. Approved dry-run is archive/quarantine (recovery_possible=True)
    archive_dry_run = run_retirement(
        root_path=root,
        action="archive",
        execute=False,
        allow_custom_root=True,
    )
    manifest_path = tmp_path / "archive-dry-run-manifest.json"
    manifest_path.write_text(json.dumps(archive_dry_run), encoding="utf-8")

    # Attempting to execute with action=delete must fail closed
    with pytest.raises(RetirementValidationError, match="Action mismatch"):
        run_retirement(
            root_path=root,
            action="delete",
            execute=True,
            approval_token="Human/Ops-approved:LIFECYCLE-PROJ-RETIRE-001",
            approver="Human/Ops",
            dry_run_manifest_path=manifest_path,
            allow_custom_root=True,
        )

    # 2. Approved dry-run is delete (recovery_possible=False), execution is archive
    delete_dry_run = run_retirement(
        root_path=root,
        action="delete",
        execute=False,
        allow_custom_root=True,
    )
    del_manifest_path = tmp_path / "delete-dry-run-manifest.json"
    del_manifest_path.write_text(json.dumps(delete_dry_run), encoding="utf-8")

    with pytest.raises(RetirementValidationError, match="Action mismatch"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_token="Human/Ops-approved:LIFECYCLE-PROJ-RETIRE-001",
            approver="Human/Ops",
            dry_run_manifest_path=del_manifest_path,
            allow_custom_root=True,
        )
