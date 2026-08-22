from __future__ import annotations

import json
import os
from pathlib import Path
import pytest

from scripts.lifecycle_projector_legacy_retire import (
    APPROVAL_SCHEMA_VERSION,
    DEFAULT_LIFECYCLE_ROOT,
    DEFAULT_QUARANTINE_SUBDIR,
    TASK_ID,
    RetirementValidationError,
    compute_approval_signature,
    compute_inventory_digest,
    execute_retirement,
    load_and_validate_approval_record,
    main as cli_main,
    resolve_governed_status_root,
    run_retirement,
    validate_destination_path_safety,
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


def _create_approval_record(
    manifest: dict,
    *,
    actor: str = "Human/Ops",
    approved: bool = True,
    task_id: str = TASK_ID,
    action: str | None = None,
    recovery_possible: bool | None = None,
    root_path: str | None = None,
    quarantine_path: str | None = None,
    inventory_sha256: str | None = None,
    schema_version: str = APPROVAL_SCHEMA_VERSION,
    approved_at_utc: str = "2026-08-22T18:00:00Z",
    signature_sha256: str | None = None,
    include_signature: bool = True,
) -> dict:
    act = action if action is not None else manifest.get("action", "archive")
    rec = (
        recovery_possible
        if recovery_possible is not None
        else manifest.get("recovery_possible", act != "delete")
    )
    r_path = root_path if root_path is not None else manifest.get("root_path")
    q_path = (
        quarantine_path
        if quarantine_path is not None
        else manifest.get("quarantine_path")
    )
    inv_sha = (
        inventory_sha256
        if inventory_sha256 is not None
        else manifest.get("inventory_sha256")
    )
    if include_signature and signature_sha256 is None:
        sig = compute_approval_signature(
            task_id=task_id,
            actor=actor,
            action=act,
            root_path=r_path,
            inventory_sha256=inv_sha,
            recovery_possible=rec,
            quarantine_path=q_path,
            approved_at_utc=approved_at_utc,
        )
    else:
        sig = signature_sha256

    record = {
        "schema_version": schema_version,
        "task_id": task_id,
        "actor": actor,
        "approved": approved,
        "approved_at_utc": approved_at_utc,
        "action": act,
        "recovery_possible": rec,
        "root_path": r_path,
        "quarantine_path": q_path,
        "inventory_sha256": inv_sha,
        "notes": "Approved by Human/Ops after reviewing exact dry-run inventory digest.",
    }
    if sig is not None:
        record["signature_sha256"] = sig
    return record


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
    assert manifest["required_approval_record"]["actor"] == "Human/Ops"
    assert manifest["required_approval_record"]["approved"] is True
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
            approval_record_path=None,
            dry_run_manifest_path=None,
            allow_custom_root=True,
        )


def test_execute_requires_governed_approval_record(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root, action="archive", execute=False, allow_custom_root=True
    )
    manifest_path = tmp_path / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    # Missing approval record must fail closed even if caller supplies token/approver
    with pytest.raises(RetirementValidationError, match="requires --approval-record"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_record_path=None,
            dry_run_manifest_path=manifest_path,
            approval_token="Human/Ops-approved:LIFECYCLE-PROJ-RETIRE-001",
            approver="Human/Ops",
            allow_custom_root=True,
        )


def test_execute_rejects_caller_supplied_token_bypass_without_approval_record(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root, action="archive", execute=False, allow_custom_root=True
    )
    manifest_path = tmp_path / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    # Bypass regression test: caller cannot bypass Human/Ops approval record with CLI parameters
    with pytest.raises(RetirementValidationError, match="caller-supplied tokens or approvers cannot bypass governed approval"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_record_path=None,
            dry_run_manifest_path=manifest_path,
            approval_token="Human/Ops-approved",
            approver="Human/Ops",
            allow_custom_root=True,
        )


def test_execute_rejects_unauthorized_approver_in_approval_record(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root, action="archive", execute=False, allow_custom_root=True
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    # Spoofed/unauthorized approver in record
    unauthorized_record = _create_approval_record(dry_run_manifest, actor="malicious_user")
    approval_path = status_root / "unauthorized-approval.json"
    approval_path.write_text(json.dumps(unauthorized_record), encoding="utf-8")

    with pytest.raises(RetirementValidationError, match="authorized operator"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_record_path=approval_path,
            dry_run_manifest_path=manifest_path,
            status_root=status_root,
            allow_custom_root=True,
        )


def test_execute_rejects_self_authored_approval_record_outside_status_root(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root, action="archive", execute=False, allow_custom_root=True
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    # Self-authored record placed in untrusted directory outside status_root
    untrusted_dir = tmp_path / "untrusted"
    untrusted_dir.mkdir(parents=True, exist_ok=True)
    approval_record = _create_approval_record(dry_run_manifest)
    self_authored_path = untrusted_dir / "self-authored-approval.json"
    self_authored_path.write_text(json.dumps(approval_record), encoding="utf-8")

    with pytest.raises(RetirementValidationError, match="outside the governed status root"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_record_path=self_authored_path,
            dry_run_manifest_path=manifest_path,
            status_root=status_root,
            allow_custom_root=True,
        )


def test_execute_rejects_unsigned_approval_record(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root, action="archive", execute=False, allow_custom_root=True
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    unsigned_record = _create_approval_record(dry_run_manifest, include_signature=False)
    approval_path = status_root / "unsigned-approval.json"
    approval_path.write_text(json.dumps(unsigned_record), encoding="utf-8")

    with pytest.raises(RetirementValidationError, match="authoritative signature"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_record_path=approval_path,
            dry_run_manifest_path=manifest_path,
            status_root=status_root,
            allow_custom_root=True,
        )


def test_execute_rejects_signature_mismatch_in_approval_record(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root, action="archive", execute=False, allow_custom_root=True
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    tampered_record = _create_approval_record(
        dry_run_manifest, signature_sha256="deadbeef" * 8
    )
    approval_path = status_root / "tampered-approval.json"
    approval_path.write_text(json.dumps(tampered_record), encoding="utf-8")

    with pytest.raises(RetirementValidationError, match="signature mismatch"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_record_path=approval_path,
            dry_run_manifest_path=manifest_path,
            status_root=status_root,
            allow_custom_root=True,
        )


def test_execute_rejects_unapproved_record(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root, action="archive", execute=False, allow_custom_root=True
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    unapproved_record = _create_approval_record(dry_run_manifest, approved=False)
    approval_path = status_root / "unapproved-record.json"
    approval_path.write_text(json.dumps(unapproved_record), encoding="utf-8")

    with pytest.raises(RetirementValidationError, match="must be boolean True"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_record_path=approval_path,
            dry_run_manifest_path=manifest_path,
            status_root=status_root,
            allow_custom_root=True,
        )


def test_execute_rejects_task_id_mismatch_in_approval_record(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root, action="archive", execute=False, allow_custom_root=True
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    wrong_task_record = _create_approval_record(dry_run_manifest, task_id="OTHER-TASK-999")
    approval_path = status_root / "wrong-task-approval.json"
    approval_path.write_text(json.dumps(wrong_task_record), encoding="utf-8")

    with pytest.raises(RetirementValidationError, match="task mismatch"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_record_path=approval_path,
            dry_run_manifest_path=manifest_path,
            status_root=status_root,
            allow_custom_root=True,
        )


def test_execute_rejects_inventory_digest_mismatch_in_approval_record(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root, action="archive", execute=False, allow_custom_root=True
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    stale_record = _create_approval_record(dry_run_manifest, inventory_sha256="0" * 64)
    approval_path = status_root / "stale-approval.json"
    approval_path.write_text(json.dumps(stale_record), encoding="utf-8")

    with pytest.raises(RetirementValidationError, match="inventory digest mismatch"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_record_path=approval_path,
            dry_run_manifest_path=manifest_path,
            status_root=status_root,
            allow_custom_root=True,
        )


def test_execute_rejects_root_mismatch_in_approval_record(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root, action="archive", execute=False, allow_custom_root=True
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    mismatched_root_record = _create_approval_record(
        dry_run_manifest, root_path="/data/bff/other-root"
    )
    approval_path = status_root / "mismatched-root-approval.json"
    approval_path.write_text(json.dumps(mismatched_root_record), encoding="utf-8")

    with pytest.raises(RetirementValidationError, match="root mismatch"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_record_path=approval_path,
            dry_run_manifest_path=manifest_path,
            status_root=status_root,
            allow_custom_root=True,
        )


def test_execute_rejects_action_mismatch_in_approval_record(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root, action="archive", execute=False, allow_custom_root=True
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    # Record specifies delete when executing archive
    mismatched_action_record = _create_approval_record(
        dry_run_manifest, action="delete", recovery_possible=False, quarantine_path=None
    )
    approval_path = status_root / "mismatched-action-approval.json"
    approval_path.write_text(json.dumps(mismatched_action_record), encoding="utf-8")

    with pytest.raises(RetirementValidationError, match="action mismatch"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_record_path=approval_path,
            dry_run_manifest_path=manifest_path,
            status_root=status_root,
            allow_custom_root=True,
        )


def test_execute_rejects_recovery_posture_mismatch_in_approval_record(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root, action="archive", execute=False, allow_custom_root=True
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    mismatched_rec_record = _create_approval_record(
        dry_run_manifest, recovery_possible=False
    )
    approval_path = status_root / "mismatched-rec-approval.json"
    approval_path.write_text(json.dumps(mismatched_rec_record), encoding="utf-8")

    with pytest.raises(RetirementValidationError, match="recovery posture mismatch"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_record_path=approval_path,
            dry_run_manifest_path=manifest_path,
            status_root=status_root,
            allow_custom_root=True,
        )


def test_execute_rejects_quarantine_path_mismatch_in_approval_record(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    quarantine_a = tmp_path / "quarantine_a"
    dry_run_manifest = run_retirement(
        root_path=root,
        action="archive",
        execute=False,
        quarantine_dir=quarantine_a,
        allow_custom_root=True,
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    mismatched_quarantine_record = _create_approval_record(
        dry_run_manifest, quarantine_path=str((tmp_path / "quarantine_other").resolve())
    )
    approval_path = status_root / "mismatched-quarantine-approval.json"
    approval_path.write_text(json.dumps(mismatched_quarantine_record), encoding="utf-8")

    with pytest.raises(RetirementValidationError, match="quarantine path mismatch"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_record_path=approval_path,
            dry_run_manifest_path=manifest_path,
            quarantine_dir=quarantine_a,
            status_root=status_root,
            allow_custom_root=True,
        )


def test_execute_rejects_drifted_inventory(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root, action="archive", execute=False, allow_custom_root=True
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    approval_record = _create_approval_record(dry_run_manifest)
    approval_path = status_root / "approval-record.json"
    approval_path.write_text(json.dumps(approval_record), encoding="utf-8")

    # Modify a file after dry-run
    (root / "trade_journey_events.json").write_text('[{"modified": true}]', encoding="utf-8")

    with pytest.raises(RetirementValidationError, match="Live inventory drifted"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_record_path=approval_path,
            dry_run_manifest_path=manifest_path,
            status_root=status_root,
            allow_custom_root=True,
        )


def test_execute_archive_moves_to_quarantine_with_receipt(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    quarantine_dir = tmp_path / "quarantine"
    dry_run_manifest = run_retirement(
        root_path=root,
        action="archive",
        execute=False,
        quarantine_dir=quarantine_dir,
        allow_custom_root=True,
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    approval_record = _create_approval_record(dry_run_manifest)
    approval_path = status_root / "human-ops-approval.json"
    approval_path.write_text(json.dumps(approval_record), encoding="utf-8")

    manifest = run_retirement(
        root_path=root,
        action="archive",
        execute=True,
        approval_record_path=approval_path,
        dry_run_manifest_path=manifest_path,
        quarantine_dir=quarantine_dir,
        status_root=status_root,
        allow_custom_root=True,
    )

    assert manifest["mode"] == "executed"
    receipt = manifest["execution_receipt"]
    assert receipt is not None
    assert receipt["status"] == "completed"
    assert receipt["action"] == "quarantine"
    assert receipt["recovery_possible"] is True
    assert receipt["approver"] == "Human/Ops"
    assert receipt["approval_record_path"] == str(approval_path)
    assert len(receipt["approval_record_sha256"]) == 64
    assert len(receipt["approval_record_signature"]) == 64
    assert receipt["bound_inventory_sha256"] == dry_run_manifest["inventory_sha256"]
    assert (quarantine_dir / "controller_state.json").exists()
    assert not (root / "controller_state.json").exists()


def test_execute_delete_removes_files_with_receipt(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root,
        action="delete",
        execute=False,
        allow_custom_root=True,
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    approval_record = _create_approval_record(dry_run_manifest)
    approval_path = status_root / "human-ops-delete-approval.json"
    approval_path.write_text(json.dumps(approval_record), encoding="utf-8")

    manifest = run_retirement(
        root_path=root,
        action="delete",
        execute=True,
        approval_record_path=approval_path,
        dry_run_manifest_path=manifest_path,
        status_root=status_root,
        allow_custom_root=True,
    )

    assert manifest["mode"] == "executed"
    receipt = manifest["execution_receipt"]
    assert receipt is not None
    assert receipt["status"] == "completed"
    assert receipt["action"] == "delete"
    assert receipt["recovery_possible"] is False
    assert receipt["approver"] == "Human/Ops"
    assert not (root / "controller_state.json").exists()
    assert not (root / "gen-000001").exists()


def test_execute_delete_preserves_unlisted_file_and_nonempty_directory_mutation_toctou(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root,
        action="delete",
        execute=False,
        allow_custom_root=True,
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    approval_record = _create_approval_record(dry_run_manifest)
    approval_path = status_root / "approval.json"
    approval_path.write_text(json.dumps(approval_record), encoding="utf-8")

    # Mutation / TOCTOU simulation: an unlisted file is placed into gen-000001 after scan
    unlisted_file = root / "gen-000001" / "unlisted_after_scan.json"
    unlisted_file.write_text('{"unlisted": true}', encoding="utf-8")

    # When execute_retirement runs against the previously scanned inventory:
    receipt = execute_retirement(
        root,
        dry_run_manifest["items"],
        action="delete",
        approval_record=approval_record,
        approval_record_path=approval_path,
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
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root,
        action="delete",
        execute=False,
        allow_custom_root=True,
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    approval_record = _create_approval_record(dry_run_manifest)
    approval_path = status_root / "approval.json"
    approval_path.write_text(json.dumps(approval_record), encoding="utf-8")

    # Add unlisted file after dry-run
    (root / "gen-000001" / "rogue.json").write_text('{"rogue": true}', encoding="utf-8")

    with pytest.raises(RetirementValidationError):
        run_retirement(
            root_path=root,
            action="delete",
            execute=True,
            approval_record_path=approval_path,
            dry_run_manifest_path=manifest_path,
            status_root=status_root,
            allow_custom_root=True,
        )


def test_quarantine_safety_validation_rejects_broad_and_system_paths():
    root = Path(DEFAULT_LIFECYCLE_ROOT)
    with pytest.raises(RetirementValidationError, match="broad or system directory"):
        validate_destination_path_safety(Path("/"), root, allow_custom_root=True)
    with pytest.raises(RetirementValidationError, match="broad or system directory"):
        validate_destination_path_safety(Path("/data"), root, allow_custom_root=True)
    with pytest.raises(RetirementValidationError, match="broad or system directory"):
        validate_destination_path_safety(Path("/data/bff"), root, allow_custom_root=True)
    with pytest.raises(RetirementValidationError, match="broad or system directory"):
        validate_destination_path_safety(Path("/var"), root, allow_custom_root=True)
    with pytest.raises(RetirementValidationError, match="broad or system directory"):
        validate_destination_path_safety(Path("/tmp"), root, allow_custom_root=True)
    with pytest.raises(RetirementValidationError, match="broad or system directory"):
        validate_destination_path_safety(Path("/workspace"), root, allow_custom_root=True)


def test_quarantine_safety_validation_rejects_globs_and_unresolved_vars():
    root = Path(DEFAULT_LIFECYCLE_ROOT)
    with pytest.raises(RetirementValidationError, match="unresolved environment variables"):
        validate_destination_path_safety(
            Path("/data/bff/lifecycle-projection/$QUARANTINE_DIR"), root, allow_custom_root=True
        )
    with pytest.raises(RetirementValidationError, match="prohibited glob"):
        validate_destination_path_safety(
            Path("/data/bff/lifecycle-projection/quarantine-*"), root, allow_custom_root=True
        )


def test_quarantine_safety_validation_rejects_canonical_sources():
    root = Path(DEFAULT_LIFECYCLE_ROOT)
    with pytest.raises(RetirementValidationError, match="canonical source pattern"):
        validate_destination_path_safety(Path("/tmp/test/telemetry_events"), root, allow_custom_root=True)
    with pytest.raises(RetirementValidationError, match="canonical source pattern"):
        validate_destination_path_safety(Path("/tmp/test/pgdata"), root, allow_custom_root=True)


def test_quarantine_safety_validation_rejects_identical_to_root():
    root = Path("/data/bff/lifecycle-projection")
    with pytest.raises(RetirementValidationError, match="cannot be identical to the lifecycle root"):
        validate_destination_path_safety(root, root, allow_custom_root=True)


def test_quarantine_safety_validation_rejects_outside_default_root_without_flag():
    root = Path(DEFAULT_LIFECYCLE_ROOT)
    with pytest.raises(RetirementValidationError, match="outside the allowed default root"):
        validate_destination_path_safety(Path("/tmp/arbitrary_dest"), root, allow_custom_root=False)


def test_run_retirement_execute_rejects_unsafe_quarantine_destination(tmp_path: Path):
    root = tmp_path / "lifecycle-projection"
    _seed_legacy_projection_fixture(root)

    # Rejects broad path in dry-run and execute
    with pytest.raises(RetirementValidationError, match="broad or system directory"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=False,
            quarantine_dir=Path("/var"),
            allow_custom_root=True,
        )

    # Rejects unresolved environment variable
    with pytest.raises(RetirementValidationError, match="unresolved environment variables"):
        run_retirement(
            root_path=root,
            action="archive",
            execute=False,
            quarantine_dir=Path("/tmp/quarantine-$UNRESOLVED"),
            allow_custom_root=True,
        )


def test_cli_main_rejects_allow_custom_root_flag(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["--allow-custom-root"])
    assert exc_info.value.code == 2


def test_cli_main_rejects_custom_root_without_governance_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PANTHEON_ALLOW_TEST_CUSTOM_ROOT", raising=False)
    custom_root = tmp_path / "custom-lifecycle"
    _seed_legacy_projection_fixture(custom_root)
    rc = cli_main(["--root", str(custom_root)])
    assert rc == 1


def test_cli_main_dry_run_and_execute_governed_mode(tmp_path: Path, monkeypatch):
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    monkeypatch.setenv("PANTHEON_ALLOW_TEST_CUSTOM_ROOT", "1")
    monkeypatch.setenv("PANTHEON_STATUS_ROOT", str(status_root))

    manifest_output = status_root / "dry-run.json"
    rc = cli_main(
        [
            "--root",
            str(root),
            "--action",
            "archive",
            "--output",
            str(manifest_output),
        ]
    )
    assert rc == 0
    assert manifest_output.exists()
    dry_run = json.loads(manifest_output.read_text(encoding="utf-8"))
    assert dry_run["mode"] == "dry_run"

    approval_record = _create_approval_record(dry_run)
    approval_path = status_root / "approval.json"
    approval_path.write_text(json.dumps(approval_record), encoding="utf-8")

    receipt_output = status_root / "receipt.json"
    rc = cli_main(
        [
            "--root",
            str(root),
            "--action",
            "archive",
            "--execute",
            "--dry-run-manifest",
            str(manifest_output),
            "--approval-record",
            str(approval_path),
            "--output",
            str(receipt_output),
        ]
    )
    assert rc == 0
    assert receipt_output.exists()
    receipt_data = json.loads(receipt_output.read_text(encoding="utf-8"))
    assert receipt_data["mode"] == "executed"
    assert receipt_data["execution_receipt"]["status"] == "completed"
