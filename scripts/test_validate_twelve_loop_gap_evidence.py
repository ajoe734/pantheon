"""Regression tests for the twelve-loop gap evidence validator.

Every rejection case below reproduces a defect that reached PR #4221 while the
product-evidence schema and ``sha256sum -c`` both reported success.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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


def _validate(root: Path, *, now: datetime | None = None):
    """Validate a staged tree against this repository's real object store.

    ``root`` is a plain directory holding copies of the bound artifacts, so it
    has no ``.git``.  ``receipt_commit_artifacts`` reads the receipt commit out
    of git, which only the real repository can answer, so every staged case
    names ``REPO_ROOT`` as the object store while still hashing staged bytes.
    """

    return validator.validate(root / MANIFEST_RELATIVE, root, now or _now(), git_root=REPO_ROOT)


def _reseal(root: Path) -> None:
    """Rewrite the companion checksum so only the rule under test can fail."""

    manifest = root / MANIFEST_RELATIVE
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    (root / CHECKSUM_RELATIVE).write_text(f"{digest}  {MANIFEST_RELATIVE}\n", encoding="utf-8")


def _rebind(root: Path) -> None:
    """Recompute the content-digest head binding for a staged tree.

    A real recut moves the receipt's ``bound_content_digest`` with the head, so
    this does too; otherwise every rebind would leave ``current_delivery_checks``
    complaining and mask the rule actually under test.
    """

    manifest_path = root / MANIFEST_RELATIVE
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bound = manifest["integrity"]["source_artifact_sha256_by_epoch"]
    for key in list(bound):
        bound[key] = validator.sha256_file(root / validator.split_epoch_key(key))
    paths = [validator.split_epoch_key(key) for key in bound]
    head = f"{validator.CONTENT_DIGEST_PREFIX}{validator.content_digest(root, paths)}"
    manifest["validation"]["validated_head_sha"] = head
    for anchor in manifest["implementation_delivery"]["anchor_commits"]:
        if anchor.get("receipt_role") == validator.RECEIPT_ROLE:
            anchor["bound_content_digest"] = head
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

    rejections = validator.validate(REPO_ROOT / MANIFEST_RELATIVE, REPO_ROOT, _now(), git_root=REPO_ROOT)
    assert rejections == [], [rejection.render() for rejection in rejections]


def test_staged_copy_passes_before_mutation(staged: Path) -> None:
    """Guards the fixture: mutations below must be the only cause of failure."""

    assert _validate(staged) == []


def test_future_record_log_timestamp_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    manifest["record_log"][-1]["recorded_at"] = "2099-01-01T00:00:00Z"
    _store(staged, manifest)
    _rebind(staged)

    rejections = _validate(staged)
    assert "future_timestamp" in _rules(rejections)


def test_future_validated_at_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    manifest["validation"]["validated_at"] = "2099-01-01T00:00:00Z"
    _store(staged, manifest)
    _rebind(staged)

    rejections = _validate(staged)
    assert "future_timestamp" in _rules(rejections)


def test_past_timestamps_are_accepted_at_a_later_check_instant(staged: Path) -> None:
    """A timestamp is only rejected for being ahead of the check instant.

    The "late" instant is derived from the manifest rather than hardcoded: every
    recut adds later record_log entries, so a fixed date would silently start
    failing on the manifest's own freshness instead of on the rule under test.
    """

    manifest = _load(staged)
    probe = "2026-07-26T22:00:00Z"
    manifest["validation"]["validated_at"] = probe
    _store(staged, manifest)
    _rebind(staged)

    latest = max(validator.parse_iso(raw) for _label, raw in validator.timestamp_fields(_load(staged)))
    late = latest + timedelta(minutes=1)
    assert "future_timestamp" not in _rules(_validate(staged, now=late))

    early = validator.parse_iso(probe) - timedelta(minutes=11)
    assert "future_timestamp" in _rules(_validate(staged, now=early))


def test_bare_commit_sha_head_binding_is_rejected(staged: Path) -> None:
    """The exact PR #4221 shape: a commit sha that predates the delivered bytes."""

    manifest = _load(staged)
    manifest["validation"]["validated_head_sha"] = "5c39428dda1d3c1e42fa926aa5f320467e1b8324"
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "head_binding" in _rules(rejections)
    assert any("bare commit sha" in rejection.detail for rejection in rejections)


def test_mutated_bound_artifact_is_rejected(staged: Path) -> None:
    """Changing a bound file without recutting evidence must fail closed."""

    manifest = _load(staged)
    target = next(iter(manifest["integrity"]["source_artifact_sha256_by_epoch"]))
    path = staged / validator.split_epoch_key(target)
    path.write_text(path.read_text(encoding="utf-8") + "\ntampered\n", encoding="utf-8")

    rejections = _validate(staged)
    assert "head_binding" in _rules(rejections)


def test_stale_content_digest_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    manifest["validation"]["validated_head_sha"] = f"{validator.CONTENT_DIGEST_PREFIX}{'0' * 64}"
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "head_binding" in _rules(rejections)
    assert any("content digest is" in rejection.detail for rejection in rejections)


def test_record_log_sequence_must_increase(staged: Path) -> None:
    manifest = _load(staged)
    manifest["record_log"][-1]["sequence"] = manifest["record_log"][0]["sequence"]
    _store(staged, manifest)
    _rebind(staged)

    rejections = _validate(staged)
    assert "record_log_ordering" in _rules(rejections)


def test_record_log_timestamps_must_not_move_backwards(staged: Path) -> None:
    manifest = _load(staged)
    manifest["record_log"][-1]["recorded_at"] = "2000-01-01T00:00:00Z"
    _store(staged, manifest)
    _rebind(staged)

    rejections = _validate(staged)
    assert "record_log_ordering" in _rules(rejections)


def test_required_check_on_unrecorded_head_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    manifest["implementation_delivery"]["required_checks"][0]["head_sha"] = "f" * 40
    _store(staged, manifest)
    _rebind(staged)

    rejections = _validate(staged)
    assert "checks_bound_to_commits" in _rules(rejections)


def _receipt(manifest: dict) -> dict:
    anchors = manifest["implementation_delivery"]["anchor_commits"]
    return next(anchor for anchor in anchors if anchor.get("receipt_role") == validator.RECEIPT_ROLE)


def test_checks_covering_only_superseded_heads_are_rejected(staged: Path) -> None:
    """The exact v5 defect: every required check names a rejected v4 head.

    ``checks_bound_to_commits`` accepts this manifest because both heads are
    recorded anchors, so only ``current_delivery_checks`` can catch it.
    """

    manifest = _load(staged)
    delivery = manifest["implementation_delivery"]
    superseded = {
        anchor["sha"]
        for anchor in delivery["anchor_commits"]
        if validator.is_superseded_state(anchor.get("delivery_state"))
    }
    delivery["required_checks"] = [
        check for check in delivery["required_checks"] if check.get("head_sha") in superseded
    ]
    assert delivery["required_checks"], "fixture must retain the superseded-head checks"
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "checks_bound_to_commits" not in _rules(rejections)
    assert "current_delivery_checks" in _rules(rejections)
    assert any("no check covers the current delivery" in rejection.detail for rejection in rejections)


def test_missing_delivery_receipt_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    for anchor in manifest["implementation_delivery"]["anchor_commits"]:
        anchor.pop("receipt_role", None)
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_delivery_checks" in _rules(rejections)
    assert any("never names the delivery" in rejection.detail for rejection in rejections)


def test_receipt_not_covering_the_bound_digest_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    _receipt(manifest)["bound_content_digest"] = f"{validator.CONTENT_DIGEST_PREFIX}{'0' * 64}"
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_delivery_checks" in _rules(rejections)
    assert any("does not equal" in rejection.detail for rejection in rejections)


def test_receipt_missing_one_required_workflow_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    delivery = manifest["implementation_delivery"]
    head = _receipt(manifest)["sha"]
    dropped = validator.REQUIRED_DELIVERY_WORKFLOWS[-1]
    delivery["required_checks"] = [
        check
        for check in delivery["required_checks"]
        if not (check.get("head_sha") == head and check.get("workflow") == dropped)
    ]
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_delivery_checks" in _rules(rejections)
    assert any(dropped in rejection.detail for rejection in rejections)


def test_superseded_commit_cannot_be_the_delivery_receipt(staged: Path) -> None:
    manifest = _load(staged)
    _receipt(manifest)["delivery_state"] = "superseded_by_a_later_recut"
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_delivery_checks" in _rules(rejections)
    assert any("cannot be the current delivery receipt" in rejection.detail for rejection in rejections)


def _mutable_command_index(manifest: dict) -> int:
    for index, entry in enumerate(manifest["validation"]["commands"]):
        if validator.MUTABLE_COMMAND.search(entry.get("command", "")):
            return index
    raise AssertionError("manifest records no mutable GitHub command")


def test_mutable_command_without_observations_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    manifest["validation"]["commands"][_mutable_command_index(manifest)].pop("observations")
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "mutable_observation_binding" in _rules(rejections)
    assert any("records no observations[]" in rejection.detail for rejection in rejections)


def test_mutable_observation_without_an_exact_head_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    entry = manifest["validation"]["commands"][_mutable_command_index(manifest)]
    entry["observations"][0]["head_sha"] = "5dbc956"
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "mutable_observation_binding" in _rules(rejections)
    assert any("must name the exact" in rejection.detail for rejection in rejections)


def test_mutable_command_without_observed_at_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    manifest["validation"]["commands"][_mutable_command_index(manifest)].pop("observed_at")
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "mutable_observation_binding" in _rules(rejections)
    assert any("records no observed_at" in rejection.detail for rejection in rejections)


def test_future_observation_timestamp_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    manifest["validation"]["commands"][_mutable_command_index(manifest)]["observations"][0][
        "observed_at"
    ] = "2099-01-01T00:00:00Z"
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "future_timestamp" in _rules(rejections)


def test_companion_checksum_mismatch_is_rejected(staged: Path) -> None:
    (staged / CHECKSUM_RELATIVE).write_text(f"{'0' * 64}  {MANIFEST_RELATIVE}\n", encoding="utf-8")

    rejections = _validate(staged)
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
            "--git-root",
            str(REPO_ROOT),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["result"] == "reject"
    # Rewriting the head binding also orphans the delivery receipt, which is
    # bound to the same content digest.  receipt_commit_artifacts stays quiet:
    # the receipt's blobs still match the recorded per-path digests, and that
    # rule defers the aggregate comparison to head_binding once
    # validated_head_sha is no longer a content digest at all.
    assert {rejection["rule"] for rejection in payload["rejections"]} == {
        "head_binding",
        "current_delivery_checks",
    }


def test_cli_without_git_root_falls_back_to_repo_root_and_fails_closed(staged: Path) -> None:
    """A staged tree has no object store, so the receipt cannot be verified.

    The default must not quietly reach for some other repository's history: an
    unverifiable receipt is a rejection, not a pass.
    """

    _rebind(staged)  # isolate the rule under test from the manifest's own freshness
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(staged / MANIFEST_RELATIVE), "--repo-root", str(staged), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    rules = {rejection["rule"] for rejection in payload["rejections"]}
    assert rules == {"receipt_commit_artifacts"}
    assert any("not a git repository" in rejection["detail"] for rejection in payload["rejections"])


V4_RECEIPT_SUBSTITUTE_SHA = "5c39428dda1d3c1e42fa926aa5f320467e1b8324"
V4_DELTA_DOC_SHA256 = "9ac925e0c66a3c48a916b7b3c97fbac580034ee96f7e3f53e1e0a42c4093a5d3"
DELTA_DOC_RELATIVE = "docs/bff/execution-tasks/2026-07-26-twelve-loop-gap/POST_DISPATCH_RUNTIME_GAP_DELTA.md"
VALIDATOR_RELATIVES = (
    "scripts/validate_twelve_loop_gap_evidence.py",
    "scripts/test_validate_twelve_loop_gap_evidence.py",
)


def test_v4_receipt_substitution_with_resealed_checksum_is_rejected(staged: Path) -> None:
    """The exact v6 rejection: an old green commit resealed as the receipt.

    Codex2 showed that moving ``receipt_role`` and ``bound_content_digest`` onto
    the rejected v4 commit ``5c39428``, marking it live, and resealing
    ``evidence.sha256`` passed all seven earlier rules -- ``5c39428`` already had
    all three required checks green, so nothing in the manifest contradicted
    itself.  Only the object store does: that commit carries the delta document
    at a different digest and neither validator script.
    """

    _rebind(staged)  # isolate the rule under test from the manifest's own freshness
    manifest = _load(staged)
    delivery = manifest["implementation_delivery"]
    anchors = delivery["anchor_commits"]

    live_receipt = _receipt(manifest)
    declared_head = manifest["validation"]["validated_head_sha"]
    substitute = next(anchor for anchor in anchors if anchor["sha"] == V4_RECEIPT_SUBSTITUTE_SHA)

    # Hand the receipt role to the v4 commit and dress it up as the live one.
    live_receipt.pop("receipt_role")
    live_receipt.pop("bound_content_digest")
    live_receipt["delivery_state"] = "superseded_by_the_substituted_receipt"
    substitute["receipt_role"] = validator.RECEIPT_ROLE
    substitute["bound_content_digest"] = declared_head
    substitute["delivery_state"] = "v6_delivery_receipt_head"
    _store(staged, manifest)  # _store reseals evidence.sha256

    rejections = _validate(staged)
    rules = _rules(rejections)

    # The seven rules that shipped before receipt_commit_artifacts are all
    # satisfied by the doctored manifest -- that is the point of the case.  Two
    # rules catch the swap, from opposite directions: receipt_commit_artifacts
    # reads git and finds the wrong bytes, while current_cut_consistency notices
    # that the manifest's prose still names the receipt this edit just demoted.
    assert rules == {"receipt_commit_artifacts", "current_cut_consistency"}, [
        rejection.render() for rejection in rejections
    ]
    assert any(
        "which is the superseded commit" in rejection.detail
        for rejection in rejections
        if rejection.rule == "current_cut_consistency"
    )

    details = " | ".join(rejection.detail for rejection in rejections)
    # Both halves of the git-object contradiction must be reported.
    assert V4_DELTA_DOC_SHA256 in details
    for relative in VALIDATOR_RELATIVES:
        assert f"does not contain bound artifact {relative}" in details


def test_receipt_naming_an_unknown_commit_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    _receipt(manifest)["sha"] = "0" * 40
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "receipt_commit_artifacts" in _rules(rejections)
    assert any("not a commit object" in rejection.detail for rejection in rejections)


def test_receipt_with_an_abbreviated_sha_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    _receipt(manifest)["sha"] = V4_RECEIPT_SUBSTITUTE_SHA[:7]
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "receipt_commit_artifacts" in _rules(rejections)
    assert any("exact 40-character" in rejection.detail for rejection in rejections)


def test_receipt_verification_reads_blobs_not_the_working_tree(staged: Path) -> None:
    """Rewriting the staged bytes cannot make a wrong receipt look right.

    ``head_binding`` hashes the working tree, so a consistent rebind satisfies
    it.  ``receipt_commit_artifacts`` reads git, so the rebind cannot follow it.
    """

    manifest = _load(staged)
    live_receipt = _receipt(manifest)
    substitute = next(
        anchor
        for anchor in manifest["implementation_delivery"]["anchor_commits"]
        if anchor["sha"] == V4_RECEIPT_SUBSTITUTE_SHA
    )
    live_receipt.pop("receipt_role")
    live_receipt.pop("bound_content_digest")
    substitute["receipt_role"] = validator.RECEIPT_ROLE
    substitute["delivery_state"] = "v6_delivery_receipt_head"
    _store(staged, manifest)

    # Replace the delta document with the exact v4 bytes and rebind everything
    # to them, so head_binding and the companion checksum are both consistent.
    v4_bytes = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "blob", f"{V4_RECEIPT_SUBSTITUTE_SHA}:{DELTA_DOC_RELATIVE}"],
        capture_output=True,
        check=True,
    ).stdout
    (staged / DELTA_DOC_RELATIVE).write_bytes(v4_bytes)
    _rebind(staged)
    manifest = _load(staged)
    _receipt(manifest)["bound_content_digest"] = manifest["validation"]["validated_head_sha"]
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "head_binding" not in _rules(rejections)
    assert "companion_checksum" not in _rules(rejections)
    assert "receipt_commit_artifacts" in _rules(rejections)
    # The v4 commit has no validator scripts at all, which no rebind can fix.
    assert any("does not contain bound artifact scripts/" in rejection.detail for rejection in rejections)


# ---------------------------------------------------------------------------
# current_cut_consistency
#
# The v7 cut carried a verifiable receipt and an unrecut narrative: prose that
# still called the document v6, still cited journal sequences 2046 and 2014,
# still counted seven rules, and still described PR #4203 as red while the same
# manifest recorded it green at a newer head.  Eight rules passed it, because
# not one of them read prose.  These cases pin each of those shapes.
# ---------------------------------------------------------------------------

STALE_SNAPSHOT_SEQUENCE = 2046
STALE_AC4_STATEMENT = (
    "The CAP evidence-only false closeout and the DIST malformed-trailer blockage are recorded with "
    "their reproduced defects and mapped to the canonical reopen/fix tasks L12-CAP-001 (Codex / Claude, "
    "in_progress since 2026-07-26T21:58:01Z, PR #4203 open with failing Commit trailers and BEHIND) and "
    "L12-DIST-001 (Claude / Antigravity, in_progress, PR #4193 open with failing Commit trailers)."
)


def _current_command_index(manifest: dict) -> int:
    for index, entry in enumerate(manifest["validation"]["commands"]):
        if entry.get("claim_scope") == "current":
            return index
    raise AssertionError("manifest records no current-scope command")


def _acceptance(manifest: dict, identifier: str) -> dict:
    return next(clause for clause in manifest["acceptance"] if clause["id"] == identifier)


def test_missing_version_declaration_is_rejected(staged: Path) -> None:
    """The cut names its version once, at the head of evidence_cut_semantics."""

    manifest = _load(staged)
    manifest["task"]["evidence_cut_semantics"] = "This cut supersedes the previous ones."
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_cut_consistency" in _rules(rejections)
    assert any("does not open with" in rejection.detail for rejection in rejections)


def test_ambiguous_snapshot_declaration_is_rejected(staged: Path) -> None:
    """Two unmarked sequences in the authority line leave no single snapshot."""

    manifest = _load(staged)
    authorities = manifest["authorities"]["actual_state"]
    authorities[0] = authorities[0] + f" Also verified through sequence {STALE_SNAPSHOT_SEQUENCE}."
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_cut_consistency" in _rules(rejections)
    assert any("unmarked journal" in rejection.detail for rejection in rejections)


def test_stale_version_token_in_a_current_claim_is_rejected(staged: Path) -> None:
    """The exact v7 shape: behavioral_proof still naming the previous cut."""

    manifest = _load(staged)
    manifest["behavioral_proof"]["duplicate_safety"]["proof"][0] = (
        "The delta document is versioned 7.0.0 and its correction sections enumerate every superseded claim."
    )
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_cut_consistency" in _rules(rejections)
    assert any("states version" in rejection.detail for rejection in rejections)


def test_short_form_stale_cut_name_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    manifest["residual_risks"]["independent_review"]["description"] = (
        "The v7 recut has not yet been independently reviewed by Codex2."
    )
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_cut_consistency" in _rules(rejections)
    assert any("names cut" in rejection.detail for rejection in rejections)


def test_historical_marker_keeps_a_superseded_version_legal(staged: Path) -> None:
    """The marker is the sanctioned way to keep a historical claim, not a hole.

    Same sentence as the rejection above, with the stale token labelled: the
    rule must fall silent, or every correction section would become unwritable.
    """

    manifest = _load(staged)
    manifest["residual_risks"]["independent_review"]["description"] = (
        "The v7 (historical) recut was rejected by Codex2; this recut has not yet been reviewed."
    )
    _store(staged, manifest)

    assert "current_cut_consistency" not in _rules(_validate(staged))


def test_stale_journal_sequence_in_a_current_claim_is_rejected(staged: Path) -> None:
    """authorities.actual_state[0] naming the sequence the previous cut used."""

    manifest = _load(staged)
    authorities = manifest["authorities"]["actual_state"]
    authorities[0] = re.sub(
        r"sequence \d+", f"sequence {STALE_SNAPSHOT_SEQUENCE}", authorities[0]
    )
    assert f"sequence {STALE_SNAPSHOT_SEQUENCE}" in authorities[0]
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_cut_consistency" in _rules(rejections)
    assert any("cites journal" in rejection.detail for rejection in rejections)


def test_dropping_the_journal_sequence_is_rejected(staged: Path) -> None:
    """Deleting the stale sequence must not be a cheaper fix than refreshing it."""

    manifest = _load(staged)
    manifest["deployment"]["identity_admission"]["proof"] = (
        "Owner Claude and reviewer Codex2 match the canonical task row and the task brief header."
    )
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_cut_consistency" in _rules(rejections)
    assert any("carries no unmarked journal sequence" in rejection.detail for rejection in rejections)


def test_superseded_receipt_sha_in_a_current_claim_is_rejected(staged: Path) -> None:
    """residual_risks still naming the previous cut's delivery receipt."""

    manifest = _load(staged)
    superseded = next(
        anchor["sha"]
        for anchor in manifest["implementation_delivery"]["anchor_commits"]
        if validator.is_superseded_state(anchor.get("delivery_state"))
    )
    risk = manifest["residual_risks"]["delivery_receipt_intermediate_state"]
    risk["description"] = (
        f"At delivery receipt commit {superseded} the tree carries the new artifacts with the previous "
        "evidence.json, so the validator regression suite fails at that one intermediate commit."
    )
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_cut_consistency" in _rules(rejections)
    assert any("which is the superseded commit" in rejection.detail for rejection in rejections)


def test_unbound_pull_request_state_claim_is_rejected(staged: Path) -> None:
    """The exact stale-AC4 shape: a PR state quoted with no head behind it."""

    manifest = _load(staged)
    _acceptance(manifest, "AC4")["statement"] = STALE_AC4_STATEMENT
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_cut_consistency" in _rules(rejections)
    assert any("without quoting that head" in rejection.detail for rejection in rejections)


def test_pull_request_claim_bound_to_the_observed_head_is_accepted(staged: Path) -> None:
    """A PR claim stays legal when it carries the head the cut observed it at."""

    manifest = _load(staged)
    heads = validator.observed_pull_request_heads(manifest)
    assert heads, "manifest records no pull-request observation on a current command entry"
    number, observed = sorted(heads.items())[0]
    head = sorted(observed)[0]
    _acceptance(manifest, "AC4")["statement"] = (
        f"Pull request #{number} was observed at head {head} for this cut."
    )
    _store(staged, manifest)

    rejections = [
        rejection for rejection in _validate(staged) if "pull request" in rejection.detail
    ]
    assert rejections == [], [rejection.render() for rejection in rejections]


def test_stale_rule_count_in_a_current_claim_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    manifest["evidence_policy"]["mutation_rule"] = (
        "scripts/validate_twelve_loop_gap_evidence.py must return zero rejections over all seven rules "
        "before handoff."
    )
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_cut_consistency" in _rules(rejections)
    assert any("counts" in rejection.detail for rejection in rejections)


def test_command_entry_without_a_claim_scope_is_rejected(staged: Path) -> None:
    manifest = _load(staged)
    manifest["validation"]["commands"][0].pop("claim_scope", None)
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_cut_consistency" in _rules(rejections)
    assert any("claim_scope" in rejection.detail for rejection in rejections)


def test_historical_command_entry_must_say_which_cut_it_belongs_to(staged: Path) -> None:
    manifest = _load(staged)
    entry = next(
        entry for entry in manifest["validation"]["commands"] if entry.get("claim_scope") == "historical"
    )
    entry.pop("historical_note")
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_cut_consistency" in _rules(rejections)
    assert any("records no historical_note" in rejection.detail for rejection in rejections)


def test_stale_claim_hidden_in_a_current_command_entry_is_rejected(staged: Path) -> None:
    """A current command entry is prose too, and is held to the same cut."""

    manifest = _load(staged)
    index = _current_command_index(manifest)
    manifest["validation"]["commands"][index]["note"] = (
        f"Verified against journal sequence {STALE_SNAPSHOT_SEQUENCE}."
    )
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_cut_consistency" in _rules(rejections)
    assert any("cites journal" in rejection.detail for rejection in rejections)


def test_removing_a_current_claim_field_is_rejected(staged: Path) -> None:
    """Enrolment lives in the validator, so deleting the field is not an escape."""

    manifest = _load(staged)
    manifest["residual_risks"].pop("independent_review")
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_cut_consistency" in _rules(rejections)
    assert any("does not resolve" in rejection.detail for rejection in rejections)


def test_two_delivery_receipts_leave_the_cut_undeclared(staged: Path) -> None:
    """The receipt is derived from the anchors, so it must be unambiguous."""

    manifest = _load(staged)
    anchors = manifest["implementation_delivery"]["anchor_commits"]
    second = next(anchor for anchor in anchors if anchor.get("receipt_role") != validator.RECEIPT_ROLE)
    second["receipt_role"] = validator.RECEIPT_ROLE
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "current_cut_consistency" in _rules(rejections)
    assert any("exactly one anchor must carry" in rejection.detail for rejection in rejections)


def test_schema_version_names_are_not_read_as_cut_names(staged: Path) -> None:
    """``loop_catalog.v2`` is a schema name; only a bare ``vN`` names a cut."""

    manifest = _load(staged)
    manifest["integrity"]["self_hash_reason"] = (
        manifest["integrity"]["self_hash_reason"]
        + " The registry it cites is loop_catalog.v2 and this manifest is loop_product_evidence.v1."
    )
    _store(staged, manifest)

    assert "current_cut_consistency" not in _rules(_validate(staged))


# ---------------------------------------------------------------------------
# bound_document_consistency
#
# The v8 cut was internally consistent -- inside evidence.json.  The delta
# document that ``validated_head_sha`` binds still told the reader the cut
# identity was declared in a ``current_cut`` key the manifest has never had, and
# still reported its journal scan as bounded at sequence 2014 when the cut's
# boundary was 2191.  Nine rules passed it, because not one of them opened the
# document.  These cases pin both shapes, and the non-false-positive cases that
# make the rule usable on a document full of legitimate historical sequences.
# ---------------------------------------------------------------------------

STALE_SCAN_BOUNDARY = 2014


def _document(root: Path) -> Path:
    manifest = _load(root)
    relative = validator.bound_document_relative(manifest)
    assert relative is not None, "the manifest must bind exactly one markdown document"
    return root / relative


def _rewrite_document(root: Path, text: str) -> None:
    """Replace the bound document's bytes and rebind the digest around them."""

    _document(root).write_text(text, encoding="utf-8")
    _rebind(root)


def _document_details(rejections) -> list[str]:
    return [r.detail for r in rejections if r.rule == "bound_document_consistency"]


def test_nonexistent_manifest_field_named_as_the_cut_declaration_is_rejected(staged: Path) -> None:
    """The v8 defect verbatim: §1 pointed at ``evidence.json``'s ``current_cut``."""

    text = _document(staged).read_text(encoding="utf-8")
    _rewrite_document(
        staged,
        text + "\n把當前 cut 的版本、交付收據與快照序號集中宣告於 `evidence.json` 的 `current_cut`。\n",
    )

    rejections = _validate(staged)
    assert "bound_document_consistency" in _rules(rejections)
    assert any("'current_cut'" in detail and "does not resolve" in detail for detail in _document_details(rejections))


def test_resolvable_manifest_field_reference_is_accepted(staged: Path) -> None:
    """Pointing at a field that exists is the normal case, not a rejection."""

    text = _document(staged).read_text(encoding="utf-8")
    _rewrite_document(
        staged,
        text + "\n被綁定檔案的 sha256 記入 `evidence.json` 的 `integrity.source_artifact_sha256_by_epoch`。\n",
    )

    assert "bound_document_consistency" not in _rules(_validate(staged))


def test_historically_marked_declaration_reference_is_accepted(staged: Path) -> None:
    """Correction sections quote the superseded claim; the marker keeps that legal."""

    text = _document(staged).read_text(encoding="utf-8")
    _rewrite_document(
        staged,
        text + "\n前一版寫的是「宣告於 `evidence.json` 的 `current_cut` (historical)」，該欄位並不存在。\n",
    )

    assert "bound_document_consistency" not in _rules(_validate(staged))


def test_stale_scan_boundary_in_the_bound_document_is_rejected(staged: Path) -> None:
    """The other v8 defect: §3.4 reported a scan two cuts old."""

    text = _document(staged).read_text(encoding="utf-8")
    _rewrite_document(
        staged,
        text
        + f"\n在 seq 1..{STALE_SCAN_BOUNDARY} 的全量掃描中，空快照共 9 筆"
        f"（本版把掃描邊界推進到 {STALE_SCAN_BOUNDARY} 後計數不變）。\n",
    )

    rejections = _validate(staged)
    assert "bound_document_consistency" in _rules(rejections)
    details = _document_details(rejections)
    assert len(details) == 2, details
    assert all(f"scan boundary of {STALE_SCAN_BOUNDARY}" in detail for detail in details)


def test_historically_marked_scan_boundary_is_accepted(staged: Path) -> None:
    """A boundary that genuinely belongs to an earlier cut only has to say so."""

    text = _document(staged).read_text(encoding="utf-8")
    _rewrite_document(
        staged,
        text + f"\nv5.0.0 (historical) 把掃描邊界推進到 seq {STALE_SCAN_BOUNDARY} (historical)。\n",
    )

    assert "bound_document_consistency" not in _rules(_validate(staged))


def test_journal_event_sequences_are_not_read_as_scan_boundaries(staged: Path) -> None:
    """§3 lists the empty-snapshot events themselves; those are not boundaries."""

    text = _document(staged).read_text(encoding="utf-8")
    _rewrite_document(
        staged,
        text + "\nseq 1593 提交了空快照，seq 1594 以 append-only 方式復原，seq 1595 由 `Human/Ops` 確認。\n",
    )

    assert "bound_document_consistency" not in _rules(_validate(staged))


def test_dropping_every_scan_boundary_is_rejected(staged: Path) -> None:
    """Deleting the numbers must fail the rule, not satisfy it vacuously."""

    text = _document(staged).read_text(encoding="utf-8")
    sequence = validator.declared_snapshot_sequence(_load(staged))
    _rewrite_document(staged, re.sub(rf"\b{sequence}\b", "該次快照", text))

    rejections = _validate(staged)
    assert "bound_document_consistency" in _rules(rejections)
    assert any("never states its scan boundary" in detail for detail in _document_details(rejections))


def test_wrapped_boundary_phrase_is_still_matched(staged: Path) -> None:
    """The document hard-wraps CJK, so ``掃描`` / ``邊界`` can land on two lines."""

    text = _document(staged).read_text(encoding="utf-8")
    _rewrite_document(
        staged,
        text + f"\n本版把掃描\n邊界推進到 {STALE_SCAN_BOUNDARY} 後計數不變。\n",
    )

    rejections = _validate(staged)
    assert "bound_document_consistency" in _rules(rejections)
    assert any(f"scan boundary of {STALE_SCAN_BOUNDARY}" in detail for detail in _document_details(rejections))


def test_cjk_terminated_sequence_token_is_still_matched(staged: Path) -> None:
    """CJK is word-constituent, so ``seq 2014時`` has no ``\\b`` after the digits."""

    text = _document(staged).read_text(encoding="utf-8")
    _rewrite_document(
        staged,
        text + f"\n本版查證截止於 seq {STALE_SCAN_BOUNDARY}時，計數不變。\n",
    )

    rejections = _validate(staged)
    assert "bound_document_consistency" in _rules(rejections)
    assert any(f"scan boundary of {STALE_SCAN_BOUNDARY}" in detail for detail in _document_details(rejections))


def test_bound_document_missing_from_the_tree_is_rejected(staged: Path) -> None:
    """An unreadable document is unverifiable, so the rule fails closed."""

    document = _document(staged)
    document.unlink()

    rejections = _validate(staged)
    assert "bound_document_consistency" in _rules(rejections)
    assert any("bound document is missing" in detail for detail in _document_details(rejections))


def test_two_bound_documents_leave_the_document_undefined(staged: Path) -> None:
    """With two markdown artifacts bound, "the document" stops being well defined."""

    manifest = _load(staged)
    bound = manifest["integrity"]["source_artifact_sha256_by_epoch"]
    original = validator.bound_document_relative(manifest)
    duplicate = str(Path(original).with_name("SECOND_DOCUMENT.md"))
    (staged / duplicate).write_text("# second\n", encoding="utf-8")
    bound[duplicate] = validator.sha256_file(staged / duplicate)
    _store(staged, manifest, reseal=False)
    _rebind(staged)

    rejections = _validate(staged)
    assert "bound_document_consistency" in _rules(rejections)
    assert any("exactly one" in detail for detail in _document_details(rejections))


def test_an_undeclarable_snapshot_leaves_the_document_unmeasurable(staged: Path) -> None:
    """Without one declared sequence there is nothing to hold the document to."""

    manifest = _load(staged)
    authorities = manifest["authorities"]["actual_state"]
    authorities[0] = authorities[0] + f" Also verified through sequence {STALE_SNAPSHOT_SEQUENCE}."
    _store(staged, manifest)

    rejections = _validate(staged)
    assert "bound_document_consistency" in _rules(rejections)
    assert any("no declared boundary" in detail for detail in _document_details(rejections))
