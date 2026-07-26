"""Regression tests for the twelve-loop gap evidence validator.

Every rejection case below reproduces a defect that reached PR #4221 while the
product-evidence schema and ``sha256sum -c`` both reported success.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "validate_twelve_loop_gap_evidence.py"
MANIFEST_RELATIVE = "docs/deployment/evidence/twelve-loop-gap/OPS-L12-RUNTIME-GAP-DELTA-001/evidence.json"
CHECKSUM_RELATIVE = "docs/deployment/evidence/twelve-loop-gap/OPS-L12-RUNTIME-GAP-DELTA-001/evidence.sha256"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import validate_twelve_loop_gap_evidence as validator  # noqa: E402


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rules(rejections) -> set[str]:
    return {rejection.rule for rejection in rejections}


def _reseal(root: Path) -> None:
    """Rewrite the companion checksum so only the rule under test can fail."""

    manifest = root / MANIFEST_RELATIVE
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    (root / CHECKSUM_RELATIVE).write_text(f"{digest}  {MANIFEST_RELATIVE}\n", encoding="utf-8")


def _rebind(root: Path) -> None:
    """Recompute the content-digest head binding for a staged tree."""

    manifest_path = root / MANIFEST_RELATIVE
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bound = manifest["integrity"]["source_artifact_sha256_by_epoch"]
    for key in list(bound):
        bound[key] = validator.sha256_file(root / validator.split_epoch_key(key))
    paths = [validator.split_epoch_key(key) for key in bound]
    manifest["validation"]["validated_head_sha"] = (
        f"{validator.CONTENT_DIGEST_PREFIX}{validator.content_digest(root, paths)}"
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    _reseal(root)


@pytest.fixture()
def staged(tmp_path: Path) -> Path:
    """A minimal copy of every file the manifest binds, plus the evidence pair."""

    manifest = json.loads((REPO_ROOT / MANIFEST_RELATIVE).read_text(encoding="utf-8"))
    bound = manifest["integrity"]["source_artifact_sha256_by_epoch"]
    relatives = [validator.split_epoch_key(key) for key in bound]
    relatives += [MANIFEST_RELATIVE, CHECKSUM_RELATIVE]
    for relative in relatives:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    return tmp_path


def _load(root: Path) -> dict:
    return json.loads((root / MANIFEST_RELATIVE).read_text(encoding="utf-8"))


def _store(root: Path, manifest: dict, *, reseal: bool = True) -> None:
    (root / MANIFEST_RELATIVE).write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    if reseal:
        _reseal(root)


def test_delivered_manifest_passes_every_rule() -> None:
    """The committed manifest must satisfy all rules as delivered."""

    rejections = validator.validate(REPO_ROOT / MANIFEST_RELATIVE, REPO_ROOT, _now())
    assert rejections == [], [rejection.render() for rejection in rejections]


def test_staged_copy_passes_before_mutation(staged: Path) -> None:
    """Guards the fixture: mutations below must be the only cause of failure."""

    assert validator.validate(staged / MANIFEST_RELATIVE, staged, _now()) == []


def test_future_record_log_timestamp_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    manifest["record_log"][-1]["recorded_at"] = "2099-01-01T00:00:00Z"
    _store(staged, manifest)
    _rebind(staged)

    rejections = validator.validate(staged / MANIFEST_RELATIVE, staged, _now())
    assert "future_timestamp" in _rules(rejections)


def test_future_validated_at_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    manifest["validation"]["validated_at"] = "2099-01-01T00:00:00Z"
    _store(staged, manifest)
    _rebind(staged)

    rejections = validator.validate(staged / MANIFEST_RELATIVE, staged, _now())
    assert "future_timestamp" in _rules(rejections)


def test_past_timestamps_are_accepted_at_a_later_check_instant(staged: Path) -> None:
    """A timestamp is only rejected for being ahead of the check instant."""

    manifest = _load(staged)
    manifest["validation"]["validated_at"] = "2026-07-26T22:00:00Z"
    _store(staged, manifest)
    _rebind(staged)

    late = validator.parse_iso("2026-07-27T00:00:00Z")
    assert "future_timestamp" not in _rules(validator.validate(staged / MANIFEST_RELATIVE, staged, late))

    early = validator.parse_iso("2026-07-26T21:49:00Z")
    assert "future_timestamp" in _rules(validator.validate(staged / MANIFEST_RELATIVE, staged, early))


def test_bare_commit_sha_head_binding_is_rejected(staged: Path) -> None:
    """The exact PR #4221 shape: a commit sha that predates the delivered bytes."""

    manifest = _load(staged)
    manifest["validation"]["validated_head_sha"] = "5c39428dda1d3c1e42fa926aa5f320467e1b8324"
    _store(staged, manifest)

    rejections = validator.validate(staged / MANIFEST_RELATIVE, staged, _now())
    assert "head_binding" in _rules(rejections)
    assert any("bare commit sha" in rejection.detail for rejection in rejections)


def test_mutated_bound_artifact_is_rejected(staged: Path) -> None:
    """Changing a bound file without recutting evidence must fail closed."""

    manifest = _load(staged)
    target = next(iter(manifest["integrity"]["source_artifact_sha256_by_epoch"]))
    path = staged / validator.split_epoch_key(target)
    path.write_text(path.read_text(encoding="utf-8") + "\ntampered\n", encoding="utf-8")

    rejections = validator.validate(staged / MANIFEST_RELATIVE, staged, _now())
    assert "head_binding" in _rules(rejections)


def test_stale_content_digest_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    manifest["validation"]["validated_head_sha"] = f"{validator.CONTENT_DIGEST_PREFIX}{'0' * 64}"
    _store(staged, manifest)

    rejections = validator.validate(staged / MANIFEST_RELATIVE, staged, _now())
    assert "head_binding" in _rules(rejections)
    assert any("content digest is" in rejection.detail for rejection in rejections)


def test_record_log_sequence_must_increase(staged: Path) -> None:
    manifest = _load(staged)
    manifest["record_log"][-1]["sequence"] = manifest["record_log"][0]["sequence"]
    _store(staged, manifest)
    _rebind(staged)

    rejections = validator.validate(staged / MANIFEST_RELATIVE, staged, _now())
    assert "record_log_ordering" in _rules(rejections)


def test_record_log_timestamps_must_not_move_backwards(staged: Path) -> None:
    manifest = _load(staged)
    manifest["record_log"][-1]["recorded_at"] = "2000-01-01T00:00:00Z"
    _store(staged, manifest)
    _rebind(staged)

    rejections = validator.validate(staged / MANIFEST_RELATIVE, staged, _now())
    assert "record_log_ordering" in _rules(rejections)


def test_required_check_on_unrecorded_head_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    manifest["implementation_delivery"]["required_checks"][0]["head_sha"] = "f" * 40
    _store(staged, manifest)
    _rebind(staged)

    rejections = validator.validate(staged / MANIFEST_RELATIVE, staged, _now())
    assert "checks_bound_to_commits" in _rules(rejections)


def test_companion_checksum_mismatch_is_rejected(staged: Path) -> None:
    (staged / CHECKSUM_RELATIVE).write_text(f"{'0' * 64}  {MANIFEST_RELATIVE}\n", encoding="utf-8")

    rejections = validator.validate(staged / MANIFEST_RELATIVE, staged, _now())
    assert "companion_checksum" in _rules(rejections)


def test_cli_reports_pass_for_the_delivered_manifest() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(REPO_ROOT / MANIFEST_RELATIVE), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["result"] == "pass"
    assert payload["rejections"] == []


def test_cli_exits_nonzero_on_rejection(staged: Path) -> None:
    manifest = _load(staged)
    manifest["validation"]["validated_head_sha"] = "5c39428dda1d3c1e42fa926aa5f320467e1b8324"
    _store(staged, manifest)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(staged / MANIFEST_RELATIVE),
            "--repo-root",
            str(staged),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["result"] == "reject"
    assert {rejection["rule"] for rejection in payload["rejections"]} == {"head_binding"}
