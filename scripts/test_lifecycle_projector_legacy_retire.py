from __future__ import annotations

import json
import os
from pathlib import Path
import pytest

from scripts.lifecycle_projector_legacy_retire import (
    RetirementValidationError,
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

    gen1 = root / "gen-000001"
    gen1.mkdir()
    (gen1 / "trade_journey_events.json").write_text("[]", encoding="utf-8")
    (gen1 / "loop_runs.json").write_text("[]", encoding="utf-8")

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
        validate_path_safety(Path("/var"), allow_custom_root=True)


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


def test_dry_run_scans_inventory_without_mutations(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)

    manifest = run_retirement(
        root_path=root,
        action="archive",
        execute=False,
        allow_custom_root=True,
    )

    assert manifest["schema_version"] == "pantheon.lifecycle-projector-legacy-retirement.v1"
    assert manifest["mode"] == "dry_run"
    assert manifest["action"] == "archive"
    assert manifest["total_files"] >= 6
    assert manifest["execution_receipt"] is None
    assert (root / "controller_state.json").exists()
    assert (root / "current").is_symlink()


def test_execute_requires_approval_token(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)

    with pytest.raises(RetirementValidationError, match="requires an explicit --approval-token"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_token="",
            allow_custom_root=True,
        )


def test_execute_archive_moves_to_quarantine(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)

    quarantine_dir = tmp_path / "quarantine"
    manifest = run_retirement(
        root_path=root,
        action="archive",
        execute=True,
        approval_token="Human/Ops-approved",
        approver="Human/Ops",
        quarantine_dir=quarantine_dir,
        allow_custom_root=True,
    )

    assert manifest["mode"] == "executed"
    receipt = manifest["execution_receipt"]
    assert receipt is not None
    assert receipt["status"] == "completed"
    assert receipt["action"] == "quarantine"
    assert receipt["recovery_possible"] is True
    assert (quarantine_dir / "controller_state.json").exists()
    assert not (root / "controller_state.json").exists()


def test_execute_delete_removes_files(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)

    manifest = run_retirement(
        root_path=root,
        action="delete",
        execute=True,
        approval_token="Human/Ops-approved",
        approver="Human/Ops",
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
