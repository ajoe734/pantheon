#!/usr/bin/env python3
"""Fail-closed validator for twelve-loop gap product-evidence manifests.

The rules here exist because PR #4221 shipped a manifest that passed both the
product-evidence JSON schema and ``sha256sum -c`` while still asserting facts it
could not support:

* ``record_log`` sequence 7 recorded ``2026-07-26T22:00:00Z``, a time that had
  not happened yet when the manifest was audited.
* ``validation.validated_head_sha`` named commit ``5c39428``, but the delivered
  bytes had already moved on to commit ``0bb6d7f`` -- the follow-up commit that
  rewrote ``evidence.json`` itself.  Binding delivered bytes to a commit sha is
  structurally circular: the sha is unknowable until after the bytes are
  committed, and any commit that records it invalidates it.

So this validator rejects future timestamps outright, and requires the head
binding to be a *content digest* over artifacts that are finalized before the
manifest is written.  ``evidence.json`` cannot hash itself; its bytes are sealed
by the companion ``evidence.sha256`` instead, which keeps the chain acyclic.

The v5 recut was rejected for two further defects, which the last two rules
close:

* every ``required_checks`` entry named a *superseded* head (the rejected v4
  commits ``5c39428`` / ``0bb6d7f``).  ``checks_bound_to_commits`` only asks
  whether a check head appears somewhere in ``anchor_commits``, so a manifest
  with no check at all for the delivered bytes still reported zero rejections.
  ``current_delivery_checks`` therefore requires the manifest to name a
  *delivery receipt*: an anchor commit whose tree carries the bound artifacts
  (``bound_content_digest`` equal to ``validated_head_sha``), which is not
  itself superseded, and which has a green check for every required workflow.
* the manifest quoted mutable GitHub state (PR heads, merge states, check
  colours) as though it were current at the evidence cut, when the underlying
  PRs had already moved.  ``mutable_observation_binding`` requires any
  ``validation.commands`` entry that reads a mutable GitHub surface to carry an
  ``observed_at`` and an ``observations`` list binding each fact to the exact
  head it was read from, so the claim can only ever be a point-in-time
  observation.

The v6 cut was rejected for a defect that the seven rules above cannot see,
because every one of them reads the manifest's own assertions about the receipt
rather than the receipt itself.  Moving ``receipt_role`` onto the rejected v4
commit ``5c39428``, copying the live ``bound_content_digest`` onto it, marking it
live, and resealing ``evidence.sha256`` passed all seven rules -- while git
objects said that commit carries the delta document at
``9ac925e0...`` rather than the bound ``4f2f7735...`` and does not contain either
validator script at all.  ``receipt_commit_artifacts`` closes this by reading the
receipt commit out of the local object store: every bound artifact must exist at
that commit, each blob's sha256 must equal the digest the manifest records, and
the digest recomputed from those blobs must equal ``validated_head_sha``.  The
check is offline -- ``git ls-tree`` and ``git cat-file`` only -- and fails closed
when git is unavailable, the commit is unknown, a path is absent, or a digest
disagrees.

Usage::

    python3 scripts/validate_twelve_loop_gap_evidence.py <evidence.json> \
        [--repo-root DIR] [--git-root DIR] [--now ISO8601] [--json]

``--repo-root`` is the tree whose bytes are hashed; ``--git-root`` is the
repository whose object store holds the receipt commit.  They are the same
repository in normal use and are separable only so a staged copy of the bound
artifacts can still be checked against real git history.

Exit status is 0 when no rule rejects the manifest and 1 otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]

BARE_COMMIT_SHA = re.compile(r"\A[0-9a-f]{40}\Z")
CONTENT_DIGEST_PREFIX = "content-digest:sha256:"

# The three required status checks on the dev and master branch protections
# (.github/workflows/branch-ci.yml).  A delivery receipt is only complete when
# all three are green on the receipt head.
REQUIRED_DELIVERY_WORKFLOWS = ("Commit trailers", "Runtime mirror guard", "Smoke acceptance")

# Substrings that mark an anchor commit as no longer the delivered state.
SUPERSEDED_MARKERS = ("superseded", "squashed", "rejected", "merged_to_dev")

RECEIPT_ROLE = "current_delivery_receipt"

# Commands that read a surface which can change between the observation and the
# review.  Facts sourced from these must be bound to an exact head and instant.
MUTABLE_COMMAND = re.compile(r"\bgh\s+(pr|run|api|search)\b")

RULES = (
    "future_timestamp",
    "head_binding",
    "record_log_ordering",
    "checks_bound_to_commits",
    "current_delivery_checks",
    "receipt_commit_artifacts",
    "mutable_observation_binding",
    "companion_checksum",
)


@dataclass(frozen=True)
class Rejection:
    """One fail-closed rejection.  Presence of any rejection fails the run."""

    rule: str
    detail: str

    def render(self) -> str:
        return f"{self.rule}: {self.detail}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def split_epoch_key(key: str) -> str:
    """``docs/x.md@v5.0.0`` -> ``docs/x.md``.

    The ``@<epoch>`` suffix is optional so a manifest may bind a path directly.
    """

    head, sep, _tail = key.rpartition("@")
    return head if sep else key


def digest_of_pairs(sha256_by_path: dict[str, str]) -> str:
    """Digest of ``"<sha256>  <path>\\n"`` lines, sorted by path.

    Sorting makes the digest independent of manifest key order, so a reviewer
    can recompute it from the repository alone.  Both the working-tree digest
    and the digest recomputed from a receipt commit's blobs go through here, so
    the two can only ever disagree about content, never about framing.
    """

    lines = [f"{sha256_by_path[relative]}  {relative}\n" for relative in sorted(sha256_by_path)]
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def content_digest(repo_root: Path, relative_paths: Iterable[str]) -> str:
    """``digest_of_pairs`` over the bytes currently in ``repo_root``."""

    return digest_of_pairs({relative: sha256_file(repo_root / relative) for relative in set(relative_paths)})


class GitUnavailable(RuntimeError):
    """Raised when the local object store cannot answer an offline query."""


def _git(git_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Run a read-only, network-free ``git`` command against ``git_root``."""

    try:
        return subprocess.run(
            ["git", "--no-optional-locks", "-C", str(git_root), *args],
            capture_output=True,
            check=False,
        )
    except OSError as error:  # git missing, git_root unreadable
        raise GitUnavailable(f"cannot run git in {git_root}: {error}") from error


def git_commit_exists(git_root: Path, sha: str) -> bool:
    """True when ``sha`` resolves to a commit object already in the store."""

    if _git(git_root, "rev-parse", "--git-dir").returncode != 0:
        raise GitUnavailable(f"{git_root} is not a git repository, so no receipt commit can be verified")
    return _git(git_root, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def git_tree_paths(git_root: Path, sha: str) -> set[str]:
    """Every blob path in the tree of commit ``sha``.

    Absence of a path must be distinguishable from git failing: a commit that
    never carried the artifact is the exact 5c39428 substitution shape, whereas a
    git failure means the claim is unverifiable and has to fail closed.  Listing
    the tree once answers that from an exit status, instead of inferring it from
    the wording of a ``cat-file`` error message.
    """

    completed = _git(git_root, "ls-tree", "-r", "--name-only", "-z", sha)
    if completed.returncode != 0:
        raise GitUnavailable(
            f"git ls-tree of {sha} failed: {completed.stderr.decode('utf-8', 'replace').strip()}"
        )
    return {path for path in completed.stdout.decode("utf-8").split("\0") if path}


def git_blob_at(git_root: Path, sha: str, relative: str) -> bytes:
    """Bytes of ``relative`` at commit ``sha``.  The path must already be known
    to exist there (see :func:`git_tree_paths`), so any failure is fail-closed."""

    completed = _git(git_root, "cat-file", "blob", f"{sha}:{relative}")
    if completed.returncode != 0:
        raise GitUnavailable(
            f"git cat-file blob {sha}:{relative} failed: "
            f"{completed.stderr.decode('utf-8', 'replace').strip()}"
        )
    return completed.stdout


def parse_iso(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def timestamp_fields(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    """Every timestamp the manifest asserts as already-observed fact."""

    found: list[tuple[str, str]] = []

    def add(label: str, value: Any) -> None:
        if isinstance(value, str) and value:
            found.append((label, value))

    add("task.evidence_cut_at", manifest.get("task", {}).get("evidence_cut_at"))
    add("validation.validated_at", manifest.get("validation", {}).get("validated_at"))
    pre_deploy = manifest.get("hosted_readback", {}).get("pre_deploy", {})
    add("hosted_readback.pre_deploy.observed_at", pre_deploy.get("observed_at"))
    for index, entry in enumerate(manifest.get("record_log", []) or []):
        add(f"record_log[{index}].recorded_at", entry.get("recorded_at"))
    delivery = manifest.get("implementation_delivery", {}) or {}
    for index, check in enumerate(delivery.get("required_checks", []) or []):
        add(f"implementation_delivery.required_checks[{index}].completed_at", check.get("completed_at"))
    for index, entry in enumerate(manifest.get("validation", {}).get("commands", []) or []):
        if not isinstance(entry, dict):
            continue
        add(f"validation.commands[{index}].observed_at", entry.get("observed_at"))
        for position, observation in enumerate(entry.get("observations", []) or []):
            if isinstance(observation, dict):
                add(
                    f"validation.commands[{index}].observations[{position}].observed_at",
                    observation.get("observed_at"),
                )
    return found


def check_future_timestamps(manifest: dict[str, Any], now: datetime) -> list[Rejection]:
    rejections = []
    for label, raw in timestamp_fields(manifest):
        try:
            observed = parse_iso(raw)
        except ValueError:
            rejections.append(Rejection("future_timestamp", f"{label} is not an ISO-8601 timestamp: {raw!r}"))
            continue
        if observed > now:
            rejections.append(
                Rejection(
                    "future_timestamp",
                    f"{label} = {raw} is later than the check instant {now.isoformat().replace('+00:00', 'Z')}",
                )
            )
    return rejections


def check_head_binding(manifest: dict[str, Any], repo_root: Path) -> list[Rejection]:
    rejections: list[Rejection] = []
    head = manifest.get("validation", {}).get("validated_head_sha", "")
    bound = manifest.get("integrity", {}).get("source_artifact_sha256_by_epoch", {}) or {}

    if BARE_COMMIT_SHA.match(head.strip()):
        rejections.append(
            Rejection(
                "head_binding",
                "validated_head_sha is a bare commit sha; a commit sha cannot cover the bytes of the "
                "commit that records it (the PR #4221 defect). Use "
                f"'{CONTENT_DIGEST_PREFIX}<digest>' instead.",
            )
        )
        return rejections

    if not head.startswith(CONTENT_DIGEST_PREFIX):
        rejections.append(
            Rejection("head_binding", f"validated_head_sha must start with {CONTENT_DIGEST_PREFIX!r}; got {head!r}")
        )
        return rejections

    if not bound:
        rejections.append(
            Rejection("head_binding", "integrity.source_artifact_sha256_by_epoch is empty; nothing binds the bytes")
        )
        return rejections

    relative_paths = []
    for key, recorded in bound.items():
        relative = split_epoch_key(key)
        relative_paths.append(relative)
        target = repo_root / relative
        if not target.is_file():
            rejections.append(Rejection("head_binding", f"bound artifact is missing from the tree: {relative}"))
            continue
        actual = sha256_file(target)
        if actual != recorded:
            rejections.append(
                Rejection("head_binding", f"{relative} sha256 is {actual}, manifest records {recorded}")
            )

    if rejections:
        return rejections

    expected = content_digest(repo_root, relative_paths)
    declared = head[len(CONTENT_DIGEST_PREFIX) :]
    if declared != expected:
        rejections.append(
            Rejection("head_binding", f"content digest is {expected}, validated_head_sha declares {declared}")
        )
    return rejections


def check_record_log_ordering(manifest: dict[str, Any]) -> list[Rejection]:
    rejections: list[Rejection] = []
    previous_sequence: int | None = None
    previous_time: datetime | None = None
    for index, entry in enumerate(manifest.get("record_log", []) or []):
        sequence = entry.get("sequence")
        if not isinstance(sequence, int):
            rejections.append(Rejection("record_log_ordering", f"record_log[{index}].sequence is not an integer"))
            continue
        if previous_sequence is not None and sequence <= previous_sequence:
            rejections.append(
                Rejection(
                    "record_log_ordering",
                    f"record_log[{index}].sequence {sequence} does not increase past {previous_sequence}",
                )
            )
        previous_sequence = sequence

        raw = entry.get("recorded_at")
        if not isinstance(raw, str):
            continue
        try:
            recorded = parse_iso(raw)
        except ValueError:
            continue
        if previous_time is not None and recorded < previous_time:
            rejections.append(
                Rejection(
                    "record_log_ordering",
                    f"record_log[{index}].recorded_at {raw} moves backwards past "
                    f"{previous_time.isoformat().replace('+00:00', 'Z')}",
                )
            )
        previous_time = recorded
    return rejections


def check_checks_bound_to_commits(manifest: dict[str, Any]) -> list[Rejection]:
    delivery = manifest.get("implementation_delivery", {}) or {}
    known = {commit.get("sha") for commit in delivery.get("anchor_commits", []) or []}
    rejections = []
    for index, check in enumerate(delivery.get("required_checks", []) or []):
        head = check.get("head_sha")
        if head and head not in known:
            rejections.append(
                Rejection(
                    "checks_bound_to_commits",
                    f"required_checks[{index}] reports {check.get('workflow')!r} on head {head}, "
                    "which is not one of the recorded anchor_commits",
                )
            )
    return rejections


def is_superseded_state(delivery_state: Any) -> bool:
    """True when an anchor's ``delivery_state`` marks it as no longer delivered."""

    text = delivery_state if isinstance(delivery_state, str) else ""
    lowered = text.lower()
    return any(marker in lowered for marker in SUPERSEDED_MARKERS)


def check_current_delivery_checks(manifest: dict[str, Any]) -> list[Rejection]:
    """Required checks must prove CI on the bytes this manifest actually delivers.

    ``checks_bound_to_commits`` is satisfied by any check whose head appears
    anywhere in ``anchor_commits``, including heads the manifest itself declares
    superseded.  That let the v5 cut report five green rules while carrying no
    check at all for its own delivery.  This rule closes that hole from both
    ends: the named receipt must be complete, and at least one green check must
    land on a head the manifest does not call superseded.
    """

    delivery = manifest.get("implementation_delivery", {}) or {}
    anchors = delivery.get("anchor_commits", []) or []
    checks = delivery.get("required_checks", []) or []
    declared_head = manifest.get("validation", {}).get("validated_head_sha", "")

    receipts = [anchor for anchor in anchors if anchor.get("receipt_role") == RECEIPT_ROLE]
    if not receipts:
        return [
            Rejection(
                "current_delivery_checks",
                f"no anchor_commits entry carries receipt_role={RECEIPT_ROLE!r}; the manifest never "
                "names the delivery whose bytes its required checks are supposed to cover",
            )
        ]

    rejections: list[Rejection] = []
    for receipt in receipts:
        sha = receipt.get("sha") or "<missing sha>"
        label = f"delivery receipt {sha}"

        if is_superseded_state(receipt.get("delivery_state")):
            rejections.append(
                Rejection(
                    "current_delivery_checks",
                    f"{label} is marked delivery_state={receipt.get('delivery_state')!r}; a superseded "
                    "commit cannot be the current delivery receipt",
                )
            )

        bound = receipt.get("bound_content_digest")
        if bound != declared_head:
            rejections.append(
                Rejection(
                    "current_delivery_checks",
                    f"{label} declares bound_content_digest={bound!r}, which does not equal "
                    f"validation.validated_head_sha={declared_head!r}; the receipt does not cover the "
                    "delivered bytes",
                )
            )

        green = {
            check.get("workflow")
            for check in checks
            if check.get("head_sha") == receipt.get("sha") and check.get("conclusion") == "success"
        }
        missing = [workflow for workflow in REQUIRED_DELIVERY_WORKFLOWS if workflow not in green]
        if missing:
            rejections.append(
                Rejection(
                    "current_delivery_checks",
                    f"{label} has no successful required_checks entry for {missing}; a receipt is only "
                    f"complete with all of {list(REQUIRED_DELIVERY_WORKFLOWS)} green on that head",
                )
            )

    state_by_sha = {anchor.get("sha"): anchor.get("delivery_state", "") for anchor in anchors}
    green_heads = {
        check.get("head_sha") for check in checks if check.get("conclusion") == "success" and check.get("head_sha")
    }
    live_heads = {head for head in green_heads if not is_superseded_state(state_by_sha.get(head, ""))}
    if green_heads and not live_heads:
        rejections.append(
            Rejection(
                "current_delivery_checks",
                "every successful required check is bound to a superseded delivery head "
                f"({sorted(green_heads)}); no check covers the current delivery",
            )
        )
    return rejections


def bound_relative_paths(manifest: dict[str, Any]) -> dict[str, str]:
    """``{relative path: recorded sha256}`` from the epoch-keyed binding map."""

    bound = manifest.get("integrity", {}).get("source_artifact_sha256_by_epoch", {}) or {}
    return {split_epoch_key(key): recorded for key, recorded in bound.items()}


def check_receipt_commit_artifacts(manifest: dict[str, Any], git_root: Path) -> list[Rejection]:
    """The receipt commit must really carry the bytes the manifest binds.

    Every other receipt rule reads the manifest's own description of the
    receipt, so all of them are satisfied by editing that description.  The v6
    rejection proved it: ``receipt_role`` and ``bound_content_digest`` moved onto
    the rejected v4 commit ``5c39428``, ``delivery_state`` was rewritten to look
    live, ``evidence.sha256`` was resealed, and all seven rules passed -- even
    though that commit carries a different delta document and neither validator
    script.  This rule asks the object store instead of the manifest, so the
    only way to satisfy it is to name a commit that genuinely holds the bytes.
    """

    delivery = manifest.get("implementation_delivery", {}) or {}
    receipts = [
        anchor
        for anchor in delivery.get("anchor_commits", []) or []
        if isinstance(anchor, dict) and anchor.get("receipt_role") == RECEIPT_ROLE
    ]
    if not receipts:
        # current_delivery_checks already rejects a manifest that names no
        # receipt; re-reporting it here would only duplicate that rejection.
        return []

    recorded_by_path = bound_relative_paths(manifest)
    declared_head = manifest.get("validation", {}).get("validated_head_sha", "")

    rejections: list[Rejection] = []
    for receipt in receipts:
        raw_sha = receipt.get("sha")
        sha = raw_sha.strip() if isinstance(raw_sha, str) else ""
        label = f"delivery receipt {sha}"

        if not BARE_COMMIT_SHA.match(sha):
            rejections.append(
                Rejection(
                    "receipt_commit_artifacts",
                    f"receipt sha is {raw_sha!r}; a receipt must name an exact 40-character lowercase "
                    "commit so its tree can be read offline",
                )
            )
            continue

        if not recorded_by_path:
            rejections.append(
                Rejection(
                    "receipt_commit_artifacts",
                    f"integrity.source_artifact_sha256_by_epoch is empty, so nothing can be verified "
                    f"against {label}",
                )
            )
            continue

        try:
            if not git_commit_exists(git_root, sha):
                rejections.append(
                    Rejection(
                        "receipt_commit_artifacts",
                        f"{label} is not a commit object in {git_root}; a receipt that cannot be read "
                        "offline cannot prove it carries the bound artifacts",
                    )
                )
                continue

            tree_paths = git_tree_paths(git_root, sha)
            actual_by_path: dict[str, str] = {}
            for relative, recorded in sorted(recorded_by_path.items()):
                if relative not in tree_paths:
                    rejections.append(
                        Rejection(
                            "receipt_commit_artifacts",
                            f"{label} does not contain bound artifact {relative}; a commit whose tree "
                            "never carried the artifact cannot be its delivery receipt",
                        )
                    )
                    continue
                actual = hashlib.sha256(git_blob_at(git_root, sha, relative)).hexdigest()
                actual_by_path[relative] = actual
                if actual != recorded:
                    rejections.append(
                        Rejection(
                            "receipt_commit_artifacts",
                            f"{label} carries {relative} at sha256 {actual}, but the manifest binds "
                            f"{recorded}; the receipt covers different bytes than the delivery",
                        )
                    )
        except GitUnavailable as error:
            rejections.append(
                Rejection(
                    "receipt_commit_artifacts",
                    f"{label} could not be verified offline: {error}",
                )
            )
            continue

        if len(actual_by_path) != len(recorded_by_path):
            # A path was missing or unreadable; the aggregate digest below would
            # only restate that, so stop at the specific rejection.
            continue
        if not declared_head.startswith(CONTENT_DIGEST_PREFIX):
            # head_binding owns the shape of validated_head_sha.
            continue
        recomputed = digest_of_pairs(actual_by_path)
        if recomputed != declared_head[len(CONTENT_DIGEST_PREFIX) :]:
            rejections.append(
                Rejection(
                    "receipt_commit_artifacts",
                    f"{label} recomputes content digest {recomputed} from its own blobs, but "
                    f"validation.validated_head_sha declares {declared_head[len(CONTENT_DIGEST_PREFIX):]}",
                )
            )
    return rejections


def check_mutable_observation_binding(manifest: dict[str, Any]) -> list[Rejection]:
    """Facts read from a mutable surface must name the head and instant they came from.

    A PR head, merge state, or check colour is true only of one head at one
    instant.  The v5 cut quoted three PRs as current at its evidence cut when
    all three had already advanced, so every such command entry must now bind
    its facts explicitly and can only be read as a point-in-time observation.
    """

    rejections: list[Rejection] = []
    for index, entry in enumerate(manifest.get("validation", {}).get("commands", []) or []):
        if not isinstance(entry, dict):
            continue
        command = entry.get("command")
        if not isinstance(command, str) or not MUTABLE_COMMAND.search(command):
            continue
        label = f"validation.commands[{index}]"

        raw_observed = entry.get("observed_at")
        if not isinstance(raw_observed, str) or not raw_observed.strip():
            rejections.append(
                Rejection(
                    "mutable_observation_binding",
                    f"{label} reads a mutable GitHub surface but records no observed_at",
                )
            )
        else:
            try:
                parse_iso(raw_observed)
            except ValueError:
                rejections.append(
                    Rejection(
                        "mutable_observation_binding",
                        f"{label}.observed_at is not an ISO-8601 timestamp: {raw_observed!r}",
                    )
                )

        observations = entry.get("observations")
        if not isinstance(observations, list) or not observations:
            rejections.append(
                Rejection(
                    "mutable_observation_binding",
                    f"{label} reads a mutable GitHub surface but records no observations[]; each fact "
                    "must name the exact head it was read from",
                )
            )
            continue

        for position, observation in enumerate(observations):
            where = f"{label}.observations[{position}]"
            if not isinstance(observation, dict):
                rejections.append(Rejection("mutable_observation_binding", f"{where} is not an object"))
                continue

            subject = observation.get("subject")
            if not isinstance(subject, str) or not subject.strip():
                rejections.append(Rejection("mutable_observation_binding", f"{where}.subject is missing"))

            head = observation.get("head_sha")
            if not isinstance(head, str) or not BARE_COMMIT_SHA.match(head.strip()):
                rejections.append(
                    Rejection(
                        "mutable_observation_binding",
                        f"{where}.head_sha is {head!r}; a mutable observation must name the exact "
                        "40-character lowercase head it was read from",
                    )
                )

            raw = observation.get("observed_at")
            if not isinstance(raw, str) or not raw.strip():
                rejections.append(Rejection("mutable_observation_binding", f"{where}.observed_at is missing"))
                continue
            try:
                parse_iso(raw)
            except ValueError:
                rejections.append(
                    Rejection(
                        "mutable_observation_binding",
                        f"{where}.observed_at is not an ISO-8601 timestamp: {raw!r}",
                    )
                )
    return rejections


def check_companion_checksum(manifest: dict[str, Any], manifest_path: Path, repo_root: Path) -> list[Rejection]:
    relative = manifest.get("integrity", {}).get("companion_checksum_path")
    if not relative:
        return [Rejection("companion_checksum", "integrity.companion_checksum_path is missing")]
    companion = repo_root / relative
    if not companion.is_file():
        return [Rejection("companion_checksum", f"companion checksum file is missing: {relative}")]

    actual = sha256_file(manifest_path)
    recorded = None
    for line in companion.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and Path(parts[1]).name == manifest_path.name:
            recorded = parts[0]
            break
    if recorded is None:
        return [Rejection("companion_checksum", f"{relative} records no digest for {manifest_path.name}")]
    if recorded != actual:
        return [
            Rejection("companion_checksum", f"{manifest_path.name} sha256 is {actual}, {relative} records {recorded}")
        ]
    return []


def validate(
    manifest_path: Path,
    repo_root: Path,
    now: datetime,
    git_root: Path | None = None,
) -> list[Rejection]:
    """Run every rule.  ``git_root`` defaults to ``repo_root``.

    ``repo_root`` supplies the bytes to hash; ``git_root`` supplies the object
    store the receipt commit is read from.  They are the same repository in
    normal use.  A caller that hashes a staged copy of the bound artifacts must
    name the real repository explicitly, because a directory with no object
    store cannot answer the receipt question and so fails closed.
    """

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rejections: list[Rejection] = []
    rejections += check_future_timestamps(manifest, now)
    rejections += check_head_binding(manifest, repo_root)
    rejections += check_record_log_ordering(manifest)
    rejections += check_checks_bound_to_commits(manifest)
    rejections += check_current_delivery_checks(manifest)
    rejections += check_receipt_commit_artifacts(manifest, git_root if git_root is not None else repo_root)
    rejections += check_mutable_observation_binding(manifest)
    rejections += check_companion_checksum(manifest, manifest_path, repo_root)
    return rejections


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path, help="path to the evidence.json manifest")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="repository root the manifest paths resolve against")
    parser.add_argument(
        "--git-root",
        type=Path,
        default=None,
        help="repository whose object store holds the delivery receipt commit (default: --repo-root)",
    )
    parser.add_argument("--now", default=None, help="ISO-8601 check instant (default: current UTC time)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit machine-readable output")
    args = parser.parse_args(argv)

    now = parse_iso(args.now) if args.now else datetime.now(timezone.utc)
    manifest_path = args.manifest.resolve()
    repo_root = args.repo_root.resolve()
    git_root = args.git_root.resolve() if args.git_root is not None else repo_root
    rejections = validate(manifest_path, repo_root, now, git_root=git_root)

    if args.as_json:
        print(
            json.dumps(
                {
                    "manifest": str(manifest_path),
                    "checked_at": now.isoformat().replace("+00:00", "Z"),
                    "rules": list(RULES),
                    "rejections": [{"rule": r.rule, "detail": r.detail} for r in rejections],
                    "result": "pass" if not rejections else "reject",
                },
                indent=2,
                sort_keys=True,
            )
        )
    elif rejections:
        print(f"REJECT {manifest_path} ({len(rejections)} rejection(s))", file=sys.stderr)
        for rejection in rejections:
            print(f"  - {rejection.render()}", file=sys.stderr)
    else:
        print(f"PASS {manifest_path} ({len(RULES)} rules, 0 rejections)")

    return 1 if rejections else 0


if __name__ == "__main__":
    raise SystemExit(main())
