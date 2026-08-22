from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import pytest

from scripts.lifecycle_projector_legacy_retire import (
    APPROVAL_SCHEMA_VERSION,
    AUTHORITATIVE_SIGNING_KEY_PATHS,
    AUTHORITATIVE_SUPERVISOR_CONFIG_PATH,
    CANONICAL_REPO_ROOT,
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
    resolve_signing_key,
    run_retirement,
    validate_destination_path_safety,
    validate_path_safety,
)

TEST_SIGNING_KEY = "test-secret-human-ops-signing-key-12345"


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.delenv("PANTHEON_HUMAN_OPS_SIGNING_KEY", raising=False)
    monkeypatch.delenv("PANTHEON_OPERATOR_APPROVAL_SECRET", raising=False)
    monkeypatch.delenv("PANTHEON_RETIREMENT_SIGNING_KEY", raising=False)
    monkeypatch.delenv("PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON", raising=False)
    monkeypatch.delenv("PANTHEON_ALLOW_TEST_CUSTOM_ROOT", raising=False)


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
    signing_key: str | bytes = TEST_SIGNING_KEY,
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
            signing_key=signing_key,
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
            signing_key=TEST_SIGNING_KEY,
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
        signing_key=TEST_SIGNING_KEY,
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
        signing_key=TEST_SIGNING_KEY,
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
        signing_key=TEST_SIGNING_KEY,
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


def test_cli_main_rejects_signing_key_and_operator_key_flags():
    """Negative regression test: CLI rejects --signing-key and --operator-key flags."""
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["--signing-key", "attacker-chosen-key"])
    assert exc_info.value.code == 2

    with pytest.raises(SystemExit) as exc_info2:
        cli_main(["--operator-key", "attacker-chosen-key"])
    assert exc_info2.value.code == 2


def test_cli_main_rejects_custom_root_outside_default_root(tmp_path: Path):
    custom_root = tmp_path / "custom-lifecycle"
    _seed_legacy_projection_fixture(custom_root)
    rc = cli_main(["--root", str(custom_root)])
    assert rc == 1


def test_cli_main_rejects_test_custom_root_env_bypass(tmp_path: Path, monkeypatch):
    """Negative regression test: setting PANTHEON_ALLOW_TEST_CUSTOM_ROOT=1 cannot bypass CLI root restriction."""
    monkeypatch.setenv("PANTHEON_ALLOW_TEST_CUSTOM_ROOT", "1")
    custom_root = tmp_path / "custom-lifecycle"
    _seed_legacy_projection_fixture(custom_root)
    rc = cli_main(["--root", str(custom_root)])
    assert rc == 1


def test_cli_main_dry_run_and_execute_governed_mode(tmp_path: Path, monkeypatch):
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    (status_root / "ai-status.json").write_text("{}", encoding="utf-8")
    _seed_legacy_projection_fixture(root)

    # In unit tests, isolate default root and canonical repo root via test mocking
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.DEFAULT_LIFECYCLE_ROOT", str(root))
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.CANONICAL_REPO_ROOT", status_root)
    monkeypatch.delenv("PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON", raising=False)
    monkeypatch.delenv("PANTHEON_STATUS_ROOT", raising=False)

    runtime_dir = status_root.parent / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    event_log = runtime_dir / "task-state-events-v2.jsonl"
    event_log.write_text("", encoding="utf-8")
    key_file = runtime_dir / "human-ops-signing.key"
    key_file.write_text(TEST_SIGNING_KEY, encoding="utf-8")
    live_config = {
        "paths": {"status_file": str(status_root / "ai-status.json")},
        "task_state_store": {"mode": "authoritative", "event_log": str(event_log)},
    }
    live_config_file = runtime_dir / "live-supervisor-config.json"
    live_config_file.write_text(json.dumps(live_config), encoding="utf-8")
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SUPERVISOR_CONFIG_PATH", live_config_file)
    monkeypatch.setenv("PANTHEON_LIVE_SUPERVISOR_CONFIG", str(live_config_file))

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


def test_cli_rejects_forged_canonical_task_state_identity_json(tmp_path: Path, monkeypatch):
    """Negative regression test: CLI rejects execution with forged task state identity env."""
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    (status_root / "ai-status.json").write_text("{}", encoding="utf-8")
    _seed_legacy_projection_fixture(root)

    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.DEFAULT_LIFECYCLE_ROOT", str(root))
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.CANONICAL_REPO_ROOT", status_root)

    runtime_dir = status_root.parent / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    event_log = runtime_dir / "task-state-events-v2.jsonl"
    event_log.write_text("", encoding="utf-8")
    live_config = {
        "paths": {"status_file": str(status_root / "ai-status.json")},
        "task_state_store": {"mode": "authoritative", "event_log": str(event_log)},
    }
    live_config_file = runtime_dir / "live-supervisor-config.json"
    live_config_file.write_text(json.dumps(live_config), encoding="utf-8")
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SUPERVISOR_CONFIG_PATH", live_config_file)
    monkeypatch.setenv("PANTHEON_LIVE_SUPERVISOR_CONFIG", str(live_config_file))

    manifest_output = status_root / "dry-run.json"
    rc = cli_main(["--root", str(root), "--output", str(manifest_output)])
    assert rc == 0
    dry_run = json.loads(manifest_output.read_text(encoding="utf-8"))

    attacker_root = tmp_path / "attacker_root"
    attacker_root.mkdir(parents=True, exist_ok=True)
    (attacker_root / "ai-status.json").write_text("{}", encoding="utf-8")

    forged_payload = {
        "schema_version": 1,
        "status_root": str(attacker_root),
        "status_file": str(attacker_root / "ai-status.json"),
        "archive_root": str(attacker_root / "ai-task-archive"),
        "event_log": str(attacker_root / "ai-activity-log.jsonl"),
    }
    encoded = json.dumps(forged_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    identity_sha256 = hashlib.sha256(encoded).hexdigest()
    monkeypatch.setenv(
        "PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON",
        json.dumps({**forged_payload, "identity_sha256": identity_sha256}),
    )

    approval_record = _create_approval_record(dry_run)
    approval_path = status_root / "approval.json"
    approval_path.write_text(json.dumps(approval_record), encoding="utf-8")

    rc = cli_main(
        [
            "--root",
            str(root),
            "--execute",
            "--dry-run-manifest",
            str(manifest_output),
            "--approval-record",
            str(approval_path),
        ]
    )
    assert rc == 1


def test_cli_rejects_caller_controlled_status_root_env(tmp_path: Path, monkeypatch):
    """Negative regression test: CLI rejects execution with caller-overridden PANTHEON_STATUS_ROOT."""
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    (status_root / "ai-status.json").write_text("{}", encoding="utf-8")
    _seed_legacy_projection_fixture(root)

    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.DEFAULT_LIFECYCLE_ROOT", str(root))
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.CANONICAL_REPO_ROOT", status_root)

    runtime_dir = status_root.parent / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    event_log = runtime_dir / "task-state-events-v2.jsonl"
    event_log.write_text("", encoding="utf-8")
    live_config = {
        "paths": {"status_file": str(status_root / "ai-status.json")},
        "task_state_store": {"mode": "authoritative", "event_log": str(event_log)},
    }
    live_config_file = runtime_dir / "live-supervisor-config.json"
    live_config_file.write_text(json.dumps(live_config), encoding="utf-8")
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SUPERVISOR_CONFIG_PATH", live_config_file)
    monkeypatch.setenv("PANTHEON_LIVE_SUPERVISOR_CONFIG", str(live_config_file))

    manifest_output = status_root / "dry-run.json"
    rc = cli_main(["--root", str(root), "--output", str(manifest_output)])
    assert rc == 0
    dry_run = json.loads(manifest_output.read_text(encoding="utf-8"))

    attacker_root = tmp_path / "attacker_status_root"
    monkeypatch.setenv("PANTHEON_STATUS_ROOT", str(attacker_root))

    approval_record = _create_approval_record(dry_run)
    approval_path = status_root / "approval.json"
    approval_path.write_text(json.dumps(approval_record), encoding="utf-8")

    rc = cli_main(
        [
            "--root",
            str(root),
            "--execute",
            "--dry-run-manifest",
            str(manifest_output),
            "--approval-record",
            str(approval_path),
        ]
    )
    assert rc == 1


def test_cli_rejects_caller_controlled_supervisor_config_override(tmp_path: Path, monkeypatch):
    """Negative regression test: CLI rejects execution with caller-overridden PANTHEON_LIVE_SUPERVISOR_CONFIG."""
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    (status_root / "ai-status.json").write_text("{}", encoding="utf-8")
    _seed_legacy_projection_fixture(root)

    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.DEFAULT_LIFECYCLE_ROOT", str(root))
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.CANONICAL_REPO_ROOT", status_root)

    runtime_dir = status_root.parent / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    event_log = runtime_dir / "task-state-events-v2.jsonl"
    event_log.write_text("", encoding="utf-8")
    live_config = {
        "paths": {"status_file": str(status_root / "ai-status.json")},
        "task_state_store": {"mode": "authoritative", "event_log": str(event_log)},
    }
    live_config_file = runtime_dir / "live-supervisor-config.json"
    live_config_file.write_text(json.dumps(live_config), encoding="utf-8")
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SUPERVISOR_CONFIG_PATH", live_config_file)

    manifest_output = status_root / "dry-run.json"
    rc = cli_main(["--root", str(root), "--output", str(manifest_output)])
    assert rc == 0
    dry_run = json.loads(manifest_output.read_text(encoding="utf-8"))

    attacker_config = tmp_path / "attacker-live-config.json"
    attacker_config.write_text(json.dumps({"paths": {"status_file": "/tmp/attacker/ai-status.json"}}), encoding="utf-8")
    monkeypatch.setenv("PANTHEON_LIVE_SUPERVISOR_CONFIG", str(attacker_config))

    approval_record = _create_approval_record(dry_run)
    approval_path = status_root / "approval.json"
    approval_path.write_text(json.dumps(approval_record), encoding="utf-8")

    rc = cli_main(
        [
            "--root",
            str(root),
            "--execute",
            "--dry-run-manifest",
            str(manifest_output),
            "--approval-record",
            str(approval_path),
        ]
    )
    assert rc == 1


def test_cli_rejects_forged_hmac_key_in_approval_record(tmp_path: Path, monkeypatch):
    """Negative regression test: CLI rejects approval record signed with an attacker's HMAC key."""
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    (status_root / "ai-status.json").write_text("{}", encoding="utf-8")
    _seed_legacy_projection_fixture(root)

    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.DEFAULT_LIFECYCLE_ROOT", str(root))
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.CANONICAL_REPO_ROOT", status_root)

    runtime_dir = status_root.parent / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    event_log = runtime_dir / "task-state-events-v2.jsonl"
    event_log.write_text("", encoding="utf-8")
    (runtime_dir / "human-ops-signing.key").write_text(TEST_SIGNING_KEY, encoding="utf-8")
    live_config = {
        "paths": {"status_file": str(status_root / "ai-status.json")},
        "task_state_store": {"mode": "authoritative", "event_log": str(event_log)},
    }
    live_config_file = runtime_dir / "live-supervisor-config.json"
    live_config_file.write_text(json.dumps(live_config), encoding="utf-8")
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SUPERVISOR_CONFIG_PATH", live_config_file)
    monkeypatch.setenv("PANTHEON_LIVE_SUPERVISOR_CONFIG", str(live_config_file))

    manifest_output = status_root / "dry-run.json"
    rc = cli_main(["--root", str(root), "--output", str(manifest_output)])
    assert rc == 0
    dry_run = json.loads(manifest_output.read_text(encoding="utf-8"))

    attacker_signed_record = _create_approval_record(
        dry_run, signing_key="attacker-arbitrary-hmac-key"
    )
    approval_path = status_root / "attacker-approval.json"
    approval_path.write_text(json.dumps(attacker_signed_record), encoding="utf-8")

    rc = cli_main(
        [
            "--root",
            str(root),
            "--execute",
            "--dry-run-manifest",
            str(manifest_output),
            "--approval-record",
            str(approval_path),
        ]
    )
    assert rc == 1


def test_cli_rejects_combined_override_chain_attack(tmp_path: Path, monkeypatch):
    """Negative regression test: CLI rejects combined override chain (fake config + fake status root + fake HMAC key + self-authored approval record)."""
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    (status_root / "ai-status.json").write_text("{}", encoding="utf-8")
    _seed_legacy_projection_fixture(root)

    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.DEFAULT_LIFECYCLE_ROOT", str(root))
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.CANONICAL_REPO_ROOT", status_root)

    runtime_dir = status_root.parent / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    event_log = runtime_dir / "task-state-events-v2.jsonl"
    event_log.write_text("", encoding="utf-8")
    (runtime_dir / "human-ops-signing.key").write_text(TEST_SIGNING_KEY, encoding="utf-8")
    live_config = {
        "paths": {"status_file": str(status_root / "ai-status.json")},
        "task_state_store": {"mode": "authoritative", "event_log": str(event_log)},
    }
    live_config_file = runtime_dir / "live-supervisor-config.json"
    live_config_file.write_text(json.dumps(live_config), encoding="utf-8")
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SUPERVISOR_CONFIG_PATH", live_config_file)

    manifest_output = status_root / "dry-run.json"
    rc = cli_main(["--root", str(root), "--output", str(manifest_output)])
    assert rc == 0
    dry_run = json.loads(manifest_output.read_text(encoding="utf-8"))

    # Attacker sets up completely separate fake hierarchy
    attacker_dir = tmp_path / "attacker_env"
    attacker_dir.mkdir(parents=True, exist_ok=True)
    attacker_status_root = attacker_dir / "coordination-root"
    attacker_status_root.mkdir(parents=True, exist_ok=True)
    (attacker_status_root / "ai-status.json").write_text("{}", encoding="utf-8")

    attacker_runtime = attacker_dir / "runtime"
    attacker_runtime.mkdir(parents=True, exist_ok=True)
    attacker_event_log = attacker_runtime / "task-state-events-v2.jsonl"
    attacker_event_log.write_text("", encoding="utf-8")
    attacker_config = attacker_runtime / "live-supervisor-mainroot-config.json"
    attacker_config.write_text(
        json.dumps({
            "paths": {"status_file": str(attacker_status_root / "ai-status.json")},
            "task_state_store": {"mode": "authoritative", "event_log": str(attacker_event_log)},
        }),
        encoding="utf-8",
    )

    attacker_key = "attacker-forged-hmac-key-99999"
    attacker_record = _create_approval_record(
        dry_run,
        root_path=str(root),
        signing_key=attacker_key,
    )
    attacker_approval_path = attacker_status_root / "self-authored-approval.json"
    attacker_approval_path.write_text(json.dumps(attacker_record), encoding="utf-8")

    # Attacker injects combined environment override chain
    monkeypatch.setenv("PANTHEON_LIVE_SUPERVISOR_CONFIG", str(attacker_config))
    monkeypatch.setenv("PANTHEON_STATUS_ROOT", str(attacker_status_root))
    monkeypatch.setenv("PANTHEON_HUMAN_OPS_SIGNING_KEY", attacker_key)
    monkeypatch.setenv("PANTHEON_OPERATOR_APPROVAL_SECRET", attacker_key)
    monkeypatch.setenv("PANTHEON_RETIREMENT_SIGNING_KEY", attacker_key)

    rc = cli_main(
        [
            "--root",
            str(root),
            "--execute",
            "--dry-run-manifest",
            str(manifest_output),
            "--approval-record",
            str(attacker_approval_path),
        ]
    )
    assert rc == 1
    # Ensure destructive deletion/quarantine did NOT happen
    assert (root / "controller_state.json").exists()
    assert (root / "health_state.json").exists()


def test_cli_rejects_legacy_signing_key_aliases(tmp_path: Path, monkeypatch):
    """Negative regression test: legacy signing key aliases cannot satisfy the execution gate."""
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    (status_root / "ai-status.json").write_text("{}", encoding="utf-8")
    _seed_legacy_projection_fixture(root)

    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.DEFAULT_LIFECYCLE_ROOT", str(root))
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.CANONICAL_REPO_ROOT", status_root)

    runtime_dir = status_root.parent / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    event_log = runtime_dir / "task-state-events-v2.jsonl"
    event_log.write_text("", encoding="utf-8")
    (runtime_dir / "human-ops-signing.key").write_text(TEST_SIGNING_KEY, encoding="utf-8")
    live_config = {
        "paths": {"status_file": str(status_root / "ai-status.json")},
        "task_state_store": {"mode": "authoritative", "event_log": str(event_log)},
    }
    live_config_file = runtime_dir / "live-supervisor-config.json"
    live_config_file.write_text(json.dumps(live_config), encoding="utf-8")
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SUPERVISOR_CONFIG_PATH", live_config_file)

    manifest_output = status_root / "dry-run.json"
    rc = cli_main(["--root", str(root), "--output", str(manifest_output)])
    assert rc == 0
    dry_run = json.loads(manifest_output.read_text(encoding="utf-8"))

    legacy_key = "legacy-alias-signing-key-8888"
    approval_record = _create_approval_record(dry_run, signing_key=legacy_key)
    approval_path = status_root / "approval.json"
    approval_path.write_text(json.dumps(approval_record), encoding="utf-8")

    # Set legacy aliases without canonical protected key files
    monkeypatch.delenv("PANTHEON_HUMAN_OPS_SIGNING_KEY", raising=False)
    monkeypatch.setenv("PANTHEON_OPERATOR_APPROVAL_SECRET", legacy_key)
    monkeypatch.setenv("PANTHEON_RETIREMENT_SIGNING_KEY", legacy_key)

    rc = cli_main(
        [
            "--root",
            str(root),
            "--execute",
            "--dry-run-manifest",
            str(manifest_output),
            "--approval-record",
            str(approval_path),
        ]
    )
    assert rc == 1


def test_cli_rejects_attacker_env_key_when_protected_key_files_absent_archive(tmp_path: Path, monkeypatch):
    """Negative regression test: CLI fails closed in archive mode when protected key files are absent,
    even if an attacker sets PANTHEON_HUMAN_OPS_SIGNING_KEY in the environment and provides a matching HMAC record.
    """
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    (status_root / "ai-status.json").write_text("{}", encoding="utf-8")
    _seed_legacy_projection_fixture(root)

    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.DEFAULT_LIFECYCLE_ROOT", str(root))
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.CANONICAL_REPO_ROOT", status_root)

    runtime_dir = status_root.parent / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    event_log = runtime_dir / "task-state-events-v2.jsonl"
    event_log.write_text("", encoding="utf-8")
    live_config = {
        "paths": {"status_file": str(status_root / "ai-status.json")},
        "task_state_store": {"mode": "authoritative", "event_log": str(event_log)},
    }
    live_config_file = runtime_dir / "live-supervisor-config.json"
    live_config_file.write_text(json.dumps(live_config), encoding="utf-8")
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SUPERVISOR_CONFIG_PATH", live_config_file)
    monkeypatch.setenv("PANTHEON_LIVE_SUPERVISOR_CONFIG", str(live_config_file))

    # Point fixed key paths to non-existent files to simulate absent/unreadable protected key sources
    monkeypatch.setattr(
        "scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SIGNING_KEY_PATHS",
        (
            runtime_dir / "nonexistent-human-ops.key",
            runtime_dir / "nonexistent-authority-signing.env",
        ),
    )

    manifest_output = status_root / "dry-run.json"
    rc = cli_main(["--root", str(root), "--output", str(manifest_output)])
    assert rc == 0
    dry_run = json.loads(manifest_output.read_text(encoding="utf-8"))

    # Attacker crafts HMAC approval record signed with attacker-chosen key
    attacker_key = "attacker-controlled-secret-key-666"
    attacker_record = _create_approval_record(
        dry_run,
        action="archive",
        signing_key=attacker_key,
    )
    approval_path = status_root / "attacker-approval.json"
    approval_path.write_text(json.dumps(attacker_record), encoding="utf-8")

    # Attacker sets environment variable PANTHEON_HUMAN_OPS_SIGNING_KEY to attacker key
    monkeypatch.setenv("PANTHEON_HUMAN_OPS_SIGNING_KEY", attacker_key)

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
        ]
    )
    # Must fail closed
    assert rc == 1

    # Verify no files were moved to quarantine
    assert (root / "controller_state.json").exists()
    assert (root / "health_state.json").exists()
    assert (root / "trade_journey_events.json").exists()
    assert (root / "loop_runs.json").exists()
    assert (root / "gen-000001").exists()
    assert not (root / "quarantine").exists()


def test_cli_rejects_attacker_env_key_when_protected_key_files_absent_delete(tmp_path: Path, monkeypatch):
    """Negative regression test: CLI fails closed in delete mode when protected key files are absent,
    even if an attacker sets PANTHEON_HUMAN_OPS_SIGNING_KEY in the environment and provides a matching HMAC record.
    """
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    (status_root / "ai-status.json").write_text("{}", encoding="utf-8")
    _seed_legacy_projection_fixture(root)

    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.DEFAULT_LIFECYCLE_ROOT", str(root))
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.CANONICAL_REPO_ROOT", status_root)

    runtime_dir = status_root.parent / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    event_log = runtime_dir / "task-state-events-v2.jsonl"
    event_log.write_text("", encoding="utf-8")
    live_config = {
        "paths": {"status_file": str(status_root / "ai-status.json")},
        "task_state_store": {"mode": "authoritative", "event_log": str(event_log)},
    }
    live_config_file = runtime_dir / "live-supervisor-config.json"
    live_config_file.write_text(json.dumps(live_config), encoding="utf-8")
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SUPERVISOR_CONFIG_PATH", live_config_file)
    monkeypatch.setenv("PANTHEON_LIVE_SUPERVISOR_CONFIG", str(live_config_file))

    # Point fixed key paths to non-existent files
    monkeypatch.setattr(
        "scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SIGNING_KEY_PATHS",
        (
            runtime_dir / "nonexistent-human-ops.key",
            runtime_dir / "nonexistent-authority-signing.env",
        ),
    )

    manifest_output = status_root / "dry-run.json"
    rc = cli_main(["--root", str(root), "--action", "delete", "--output", str(manifest_output)])
    assert rc == 0
    dry_run = json.loads(manifest_output.read_text(encoding="utf-8"))

    # Attacker crafts HMAC approval record signed with attacker-chosen key
    attacker_key = "attacker-controlled-secret-key-777"
    attacker_record = _create_approval_record(
        dry_run,
        action="delete",
        recovery_possible=False,
        quarantine_path=None,
        signing_key=attacker_key,
    )
    approval_path = status_root / "attacker-approval.json"
    approval_path.write_text(json.dumps(attacker_record), encoding="utf-8")

    # Attacker sets environment variable PANTHEON_HUMAN_OPS_SIGNING_KEY to attacker key
    monkeypatch.setenv("PANTHEON_HUMAN_OPS_SIGNING_KEY", attacker_key)

    rc = cli_main(
        [
            "--root",
            str(root),
            "--action",
            "delete",
            "--execute",
            "--dry-run-manifest",
            str(manifest_output),
            "--approval-record",
            str(approval_path),
        ]
    )
    # Must fail closed
    assert rc == 1

    # Verify no files were deleted
    assert (root / "controller_state.json").exists()
    assert (root / "health_state.json").exists()
    assert (root / "trade_journey_events.json").exists()
    assert (root / "loop_runs.json").exists()
    assert (root / "gen-000001").exists()


def test_execute_rejects_caller_env_root_override_outside_repo(tmp_path: Path, monkeypatch):
    """Regression test: caller cannot point PANTHEON_STATUS_ROOT outside the repository root."""
    monkeypatch.delenv("PANTHEON_ALLOW_TEST_CUSTOM_ROOT", raising=False)
    monkeypatch.delenv("PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON", raising=False)
    monkeypatch.delenv("PANTHEON_LIVE_SUPERVISOR_CONFIG", raising=False)
    attacker_dir = tmp_path / "attacker_controlled_dir"
    attacker_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PANTHEON_STATUS_ROOT", str(attacker_dir))

    with pytest.raises(
        RetirementValidationError,
        match="Caller-controlled PANTHEON_STATUS_ROOT override .* outside authoritative status root",
    ):
        resolve_governed_status_root()


def test_execute_rejects_unkeyed_deterministic_hash_forge(tmp_path: Path):
    """Regression test: caller cannot satisfy the execution gate with an unkeyed SHA-256 hash."""
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root, action="archive", execute=False, allow_custom_root=True
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    approved_at = "2026-08-22T18:00:00Z"
    canonical_payload = {
        "action": "archive",
        "actor": "Human/Ops",
        "approved": True,
        "approved_at_utc": approved_at,
        "inventory_sha256": dry_run_manifest["inventory_sha256"],
        "quarantine_path": dry_run_manifest["quarantine_path"],
        "recovery_possible": True,
        "root_path": str(root),
        "task_id": TASK_ID,
    }
    unkeyed_hash = hashlib.sha256(
        json.dumps(canonical_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    forged_record = _create_approval_record(
        dry_run_manifest,
        approved_at_utc=approved_at,
        signature_sha256=unkeyed_hash,
    )
    approval_path = status_root / "forged-unkeyed-approval.json"
    approval_path.write_text(json.dumps(forged_record), encoding="utf-8")

    with pytest.raises(
        RetirementValidationError,
        match="forgeable unkeyed SHA-256 hash",
    ):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_record_path=approval_path,
            dry_run_manifest_path=manifest_path,
            status_root=status_root,
            allow_custom_root=True,
        )


def test_execute_rejects_missing_signing_key_in_execute_mode(tmp_path: Path, monkeypatch):
    """Regression test: execute mode fails closed when no authoritative signing key is present."""
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
    approval_path = status_root / "approval.json"
    approval_path.write_text(json.dumps(approval_record), encoding="utf-8")

    monkeypatch.delenv("PANTHEON_HUMAN_OPS_SIGNING_KEY", raising=False)
    monkeypatch.delenv("PANTHEON_OPERATOR_APPROVAL_SECRET", raising=False)
    monkeypatch.delenv("PANTHEON_RETIREMENT_SIGNING_KEY", raising=False)

    with pytest.raises(
        RetirementValidationError,
        match="signing key .* is required",
    ):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_record_path=approval_path,
            dry_run_manifest_path=manifest_path,
            status_root=status_root,
            allow_custom_root=True,
            signing_key=None,
        )


def test_execute_rejects_wrong_signing_key(tmp_path: Path):
    """Regression test: signature verification fails when signed with wrong key."""
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    _seed_legacy_projection_fixture(root)

    dry_run_manifest = run_retirement(
        root_path=root, action="archive", execute=False, allow_custom_root=True
    )
    manifest_path = status_root / "dry-run-manifest.json"
    manifest_path.write_text(json.dumps(dry_run_manifest), encoding="utf-8")

    wrong_key_record = _create_approval_record(
        dry_run_manifest, signing_key="attacker-wrong-secret-key"
    )
    approval_path = status_root / "wrong-key-approval.json"
    approval_path.write_text(json.dumps(wrong_key_record), encoding="utf-8")

    with pytest.raises(
        RetirementValidationError,
        match="signature mismatch: record specifies .*, expected exact HMAC signature",
    ):
        run_retirement(
            root_path=root,
            action="archive",
            execute=True,
            approval_record_path=approval_path,
            dry_run_manifest_path=manifest_path,
            status_root=status_root,
            allow_custom_root=True,
            signing_key=TEST_SIGNING_KEY,
        )


def test_canonical_task_state_identity_binding_valid_with_live_config(tmp_path: Path, monkeypatch):
    """Verify that canonical supervisor task state identity binding securely matches live config."""
    monkeypatch.delenv("PANTHEON_ALLOW_TEST_CUSTOM_ROOT", raising=False)
    monkeypatch.delenv("PANTHEON_STATUS_ROOT", raising=False)

    supervisor_root = tmp_path / "supervisor_root"
    status_root = supervisor_root / "coordination-root"
    status_root.mkdir(parents=True, exist_ok=True)
    (status_root / "ai-status.json").write_text("{}", encoding="utf-8")
    runtime_dir = supervisor_root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    event_log = runtime_dir / "task-state-events-v2.jsonl"
    event_log.write_text("", encoding="utf-8")

    live_config = {
        "paths": {
            "status_file": str(status_root / "ai-status.json"),
        },
        "task_state_store": {
            "mode": "authoritative",
            "event_log": str(event_log),
        },
    }
    live_config_file = runtime_dir / "live-supervisor-config.json"
    live_config_file.write_text(json.dumps(live_config), encoding="utf-8")
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SUPERVISOR_CONFIG_PATH", live_config_file)
    monkeypatch.setenv("PANTHEON_LIVE_SUPERVISOR_CONFIG", str(live_config_file))

    payload = {
        "schema_version": 1,
        "status_root": str(status_root),
        "status_file": str(status_root / "ai-status.json"),
        "archive_root": str(status_root / "ai-task-archive"),
        "event_log": str(event_log),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    identity_sha256 = hashlib.sha256(encoded).hexdigest()
    identity_json = json.dumps({**payload, "identity_sha256": identity_sha256})
    monkeypatch.setenv("PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON", identity_json)

    resolved = resolve_governed_status_root()
    assert resolved == status_root.resolve()


def test_canonical_task_state_identity_binding_rejects_forged_binding(tmp_path: Path, monkeypatch):
    """Verify that forged canonical identity not matching live supervisor config is rejected."""
    monkeypatch.delenv("PANTHEON_ALLOW_TEST_CUSTOM_ROOT", raising=False)
    monkeypatch.delenv("PANTHEON_STATUS_ROOT", raising=False)

    supervisor_root = tmp_path / "supervisor_root"
    status_root = supervisor_root / "coordination-root"
    status_root.mkdir(parents=True, exist_ok=True)
    (status_root / "ai-status.json").write_text("{}", encoding="utf-8")
    runtime_dir = supervisor_root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    event_log = runtime_dir / "task-state-events-v2.jsonl"
    event_log.write_text("", encoding="utf-8")

    live_config = {
        "paths": {
            "status_file": str(status_root / "ai-status.json"),
        },
        "task_state_store": {
            "mode": "authoritative",
            "event_log": str(event_log),
        },
    }
    live_config_file = runtime_dir / "live-supervisor-config.json"
    live_config_file.write_text(json.dumps(live_config), encoding="utf-8")
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SUPERVISOR_CONFIG_PATH", live_config_file)
    monkeypatch.setenv("PANTHEON_LIVE_SUPERVISOR_CONFIG", str(live_config_file))

    # Attacker crafts identity pointing to a fake root with valid sha256
    attacker_root = tmp_path / "attacker_root"
    attacker_root.mkdir(parents=True, exist_ok=True)
    (attacker_root / "ai-status.json").write_text("{}", encoding="utf-8")

    forged_payload = {
        "schema_version": 1,
        "status_root": str(attacker_root),
        "status_file": str(attacker_root / "ai-status.json"),
        "archive_root": str(attacker_root / "ai-task-archive"),
        "event_log": str(attacker_root / "ai-activity-log.jsonl"),
    }
    encoded = json.dumps(forged_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    identity_sha256 = hashlib.sha256(encoded).hexdigest()
    identity_json = json.dumps({**forged_payload, "identity_sha256": identity_sha256})
    monkeypatch.setenv("PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON", identity_json)

    with pytest.raises(
        RetirementValidationError,
        match="does not match the authoritative supervisor task state identity",
    ):
        resolve_governed_status_root()


def test_canonical_task_state_identity_binding_rejects_tampered_hash(tmp_path: Path, monkeypatch):
    """Verify that tampered task state identity hash is rejected."""
    monkeypatch.delenv("PANTHEON_ALLOW_TEST_CUSTOM_ROOT", raising=False)
    monkeypatch.delenv("PANTHEON_STATUS_ROOT", raising=False)

    bound_root = tmp_path / "supervisor_status_root"
    bound_root.mkdir(parents=True, exist_ok=True)
    (bound_root / "ai-status.json").write_text("{}", encoding="utf-8")

    identity_payload = {
        "schema_version": 1,
        "status_root": str(bound_root),
        "status_file": str(bound_root / "ai-status.json"),
        "archive_root": str(bound_root / "ai-task-archive"),
        "event_log": str(bound_root / "ai-activity-log.jsonl"),
    }
    identity_json = json.dumps({**identity_payload, "identity_sha256": "0" * 64})
    monkeypatch.setenv("PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON", identity_json)

    with pytest.raises(
        RetirementValidationError,
        match="identity_sha256 integrity mismatch",
    ):
        resolve_governed_status_root()


def test_canonical_task_state_identity_binding_rejects_conflicting_status_root_env(
    tmp_path: Path, monkeypatch
):
    """Verify that conflicting PANTHEON_STATUS_ROOT is rejected when canonical identity is bound."""
    monkeypatch.delenv("PANTHEON_ALLOW_TEST_CUSTOM_ROOT", raising=False)

    supervisor_root = tmp_path / "supervisor_root"
    status_root = supervisor_root / "coordination-root"
    status_root.mkdir(parents=True, exist_ok=True)
    (status_root / "ai-status.json").write_text("{}", encoding="utf-8")
    runtime_dir = supervisor_root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    event_log = runtime_dir / "task-state-events-v2.jsonl"
    event_log.write_text("", encoding="utf-8")

    live_config = {
        "paths": {
            "status_file": str(status_root / "ai-status.json"),
        },
        "task_state_store": {
            "mode": "authoritative",
            "event_log": str(event_log),
        },
    }
    live_config_file = runtime_dir / "live-supervisor-config.json"
    live_config_file.write_text(json.dumps(live_config), encoding="utf-8")
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SUPERVISOR_CONFIG_PATH", live_config_file)
    monkeypatch.setenv("PANTHEON_LIVE_SUPERVISOR_CONFIG", str(live_config_file))

    payload = {
        "schema_version": 1,
        "status_root": str(status_root),
        "status_file": str(status_root / "ai-status.json"),
        "archive_root": str(status_root / "ai-task-archive"),
        "event_log": str(event_log),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    identity_sha256 = hashlib.sha256(encoded).hexdigest()
    identity_json = json.dumps({**payload, "identity_sha256": identity_sha256})
    monkeypatch.setenv("PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON", identity_json)

    # Conflicting status root
    monkeypatch.setenv("PANTHEON_STATUS_ROOT", str(tmp_path / "conflicting_root"))

    with pytest.raises(
        RetirementValidationError,
        match="conflicts with authoritative identity root",
    ):
        resolve_governed_status_root()


def test_cli_rejects_forged_identity_chain_when_supervisor_config_absent(
    tmp_path: Path, monkeypatch
):
    """Negative regression test: CLI rejects forged self-consistent identity rooted at canonical repo
    with attacker runtime key when authoritative supervisor config/key files are absent.
    """
    root = tmp_path / "lifecycle-projection"
    status_root = tmp_path / "status_root"
    status_root.mkdir(parents=True, exist_ok=True)
    (status_root / "ai-status.json").write_text("{}", encoding="utf-8")
    _seed_legacy_projection_fixture(root)

    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.DEFAULT_LIFECYCLE_ROOT", str(root))
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.CANONICAL_REPO_ROOT", status_root)

    # Point fixed config & key paths to non-existent files
    nonexistent_config = tmp_path / "nonexistent-runtime" / "live-supervisor-config.json"
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SUPERVISOR_CONFIG_PATH", nonexistent_config)
    monkeypatch.setattr(
        "scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SIGNING_KEY_PATHS",
        (
            tmp_path / "nonexistent-runtime" / "human-ops-signing.key",
            tmp_path / "nonexistent-runtime" / "authority-signing.env",
        ),
    )
    monkeypatch.delenv("PANTHEON_LIVE_SUPERVISOR_CONFIG", raising=False)
    monkeypatch.delenv("PANTHEON_STATUS_ROOT", raising=False)
    monkeypatch.delenv("PANTHEON_HUMAN_OPS_SIGNING_KEY", raising=False)

    manifest_output = status_root / "dry-run.json"
    rc = cli_main(["--root", str(root), "--output", str(manifest_output)])
    assert rc == 0
    dry_run = json.loads(manifest_output.read_text(encoding="utf-8"))

    # Attacker sets up runtime with attacker key
    attacker_runtime = tmp_path / "attacker_runtime"
    attacker_runtime.mkdir(parents=True, exist_ok=True)
    attacker_event_log = attacker_runtime / "task-state-events-v2.jsonl"
    attacker_event_log.write_text("", encoding="utf-8")
    attacker_key = "attacker-forged-secret-key-333"
    (attacker_runtime / "human-ops-signing.key").write_text(attacker_key, encoding="utf-8")

    # Attacker crafts self-consistent identity pointing to status_root (canonical repo root) and attacker event_log
    identity_payload = {
        "schema_version": 1,
        "status_root": str(status_root),
        "status_file": str(status_root / "ai-status.json"),
        "archive_root": str(status_root / "ai-task-archive"),
        "event_log": str(attacker_event_log),
    }
    encoded = json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    identity_sha256 = hashlib.sha256(encoded).hexdigest()
    identity_json = json.dumps({**identity_payload, "identity_sha256": identity_sha256})
    monkeypatch.setenv("PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON", identity_json)

    # Attacker creates signed approval record in status_root
    attacker_record = _create_approval_record(
        dry_run,
        action="archive",
        root_path=str(root),
        signing_key=attacker_key,
    )
    approval_path = status_root / "attacker-approval.json"
    approval_path.write_text(json.dumps(attacker_record), encoding="utf-8")

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
        ]
    )
    assert rc == 1

    # Verify no files were moved or deleted
    assert (root / "controller_state.json").exists()
    assert (root / "health_state.json").exists()
    assert (root / "trade_journey_events.json").exists()
    assert (root / "loop_runs.json").exists()
    assert not (root / "quarantine").exists()


def test_resolve_signing_key_fails_closed_without_authoritative_config_or_keys(
    tmp_path: Path, monkeypatch
):
    """Negative regression test: resolve_signing_key returns None when fixed config/keys are absent,
    even if caller-provided PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON or env key is present.
    """
    nonexistent_config = tmp_path / "nonexistent-runtime" / "live-supervisor-config.json"
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SUPERVISOR_CONFIG_PATH", nonexistent_config)
    monkeypatch.setattr(
        "scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SIGNING_KEY_PATHS",
        (
            tmp_path / "nonexistent-runtime" / "human-ops-signing.key",
            tmp_path / "nonexistent-runtime" / "authority-signing.env",
        ),
    )
    monkeypatch.delenv("PANTHEON_LIVE_SUPERVISOR_CONFIG", raising=False)

    attacker_runtime = tmp_path / "attacker_runtime"
    attacker_runtime.mkdir(parents=True, exist_ok=True)
    attacker_event_log = attacker_runtime / "task-state-events-v2.jsonl"
    attacker_event_log.write_text("", encoding="utf-8")
    (attacker_runtime / "human-ops-signing.key").write_text("attacker-key", encoding="utf-8")

    identity_payload = {
        "schema_version": 1,
        "status_root": str(tmp_path),
        "status_file": str(tmp_path / "ai-status.json"),
        "archive_root": str(tmp_path / "ai-task-archive"),
        "event_log": str(attacker_event_log),
    }
    encoded = json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    identity_sha256 = hashlib.sha256(encoded).hexdigest()
    identity_json = json.dumps({**identity_payload, "identity_sha256": identity_sha256})
    monkeypatch.setenv("PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON", identity_json)
    monkeypatch.setenv("PANTHEON_HUMAN_OPS_SIGNING_KEY", "attacker-env-key")

    resolved = resolve_signing_key(allow_custom_root=False)
    assert resolved is None


def test_resolve_governed_status_root_rejects_identity_when_supervisor_config_absent(
    tmp_path: Path, monkeypatch
):
    """Negative regression test: resolve_governed_status_root fails closed when PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON
    is provided but fixed authoritative supervisor config is absent.
    """
    monkeypatch.delenv("PANTHEON_ALLOW_TEST_CUSTOM_ROOT", raising=False)
    monkeypatch.delenv("PANTHEON_STATUS_ROOT", raising=False)
    monkeypatch.delenv("PANTHEON_LIVE_SUPERVISOR_CONFIG", raising=False)

    nonexistent_config = tmp_path / "nonexistent-runtime" / "live-supervisor-config.json"
    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.AUTHORITATIVE_SUPERVISOR_CONFIG_PATH", nonexistent_config)

    status_root = tmp_path / "repo_root"
    status_root.mkdir(parents=True, exist_ok=True)
    (status_root / "ai-status.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr("scripts.lifecycle_projector_legacy_retire.CANONICAL_REPO_ROOT", status_root)

    identity_payload = {
        "schema_version": 1,
        "status_root": str(status_root),
        "status_file": str(status_root / "ai-status.json"),
        "archive_root": str(status_root / "ai-task-archive"),
        "event_log": str(tmp_path / "event-log.jsonl"),
    }
    encoded = json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    identity_sha256 = hashlib.sha256(encoded).hexdigest()
    identity_json = json.dumps({**identity_payload, "identity_sha256": identity_sha256})
    monkeypatch.setenv("PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON", identity_json)

    with pytest.raises(
        RetirementValidationError,
        match="authoritative supervisor configuration is absent",
    ):
        resolve_governed_status_root(allow_custom_root=False)
