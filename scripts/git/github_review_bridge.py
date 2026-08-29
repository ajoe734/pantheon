#!/usr/bin/env python3
"""Bridge governed task-review decisions to GitHub's PR gate.

Pantheon agents currently share one GitHub account, so GitHub can reject an
otherwise valid approving review as a self-review.  The bridge therefore:

1. verifies the exact PR, base, head branch, and head commit;
2. attempts to submit a real GitHub pull-request review;
3. when the base branch explicitly requires the canonical review status
   context, records the decision on the exact head commit as a required status.

The status path is not a generic fallback.  It is accepted only when GitHub's
current branch-protection response names the exact context, making the
alternative visible to and enforced by branch policy.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import quote, urlparse


CANONICAL_REVIEW_CONTEXT = "Pantheon canonical review gate"

# Git-native proof of a governed review decision: a tag object pushed to the
# exact reviewed SHA. Unlike a commit *status* (opaque metadata GitHub stores
# out-of-band) or ai-status.json (a live file that only exists on the host
# running Pantheon), a pushed tag is part of the repository's own object
# graph -- any clone or fetch, including a GitHub Actions runner with zero
# access to this host, sees it via a plain `gh api` call against GitHub's Git
# Data API. This is what makes the required check in
# .github/workflows/canonical-review-gate.yml able to answer "was this exact
# head approved" without ever needing to read live task state.
# SUP-REVIEW-GATE-GIT-NATIVE-PROOF-20260804.
REVIEW_PROOF_TAG_PREFIX = "pantheon-review"
APPROVE = "approve"
REOPEN = "reopen"
DECISIONS = {APPROVE, REOPEN}
# This is deliberately not a review decision.  It is an explicit Human/Ops
# acceptance of the current immutable PR head when an operator has chosen not
# to consume another reviewer pass.  Keeping its tag name distinct prevents a
# later reader from mistaking it for independent review evidence.
OPERATOR_ACCEPT = "operator-accept"
REVIEW_STATES = {
    APPROVE: "APPROVED",
    REOPEN: "CHANGES_REQUESTED",
}
REVIEW_EVENTS = {
    APPROVE: "APPROVE",
    REOPEN: "REQUEST_CHANGES",
}
STATUS_STATES = {
    APPROVE: "success",
    REOPEN: "failure",
}
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
OID_RE = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_REVIEW_MERGE_METHOD = "MERGE"
TASK_BRIEF_PREFIX = ".orchestrator/task-briefs/"
# GitHub's commit-files response is bounded. A closeout record should be a
# tiny one-file successor. Request the largest supported page, then reject a
# full page rather than risk treating an omitted later page as proof that no
# code changed.
COMMIT_FILES_PAGE_SIZE = 100
MAX_SAFE_SUCCESSOR_FILES = COMMIT_FILES_PAGE_SIZE - 1


class GitHubReviewBridgeError(RuntimeError):
    """The governed decision could not be represented on GitHub."""


class ReviewBindingMismatch(GitHubReviewBridgeError):
    """The bound PR identity definitively differs from GitHub's current PR."""


class JsonRunner(Protocol):
    def run_json(
        self,
        args: Sequence[str],
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        ...


class GhJsonRunner:
    """Small bounded `gh` JSON runner used by the status command."""

    def __init__(self, *, timeout_seconds: float | None = None) -> None:
        raw_timeout = str(os.environ.get("PANTHEON_GITHUB_REVIEW_TIMEOUT_SECONDS") or "").strip()
        if timeout_seconds is not None:
            self.timeout_seconds = timeout_seconds
        elif raw_timeout:
            try:
                self.timeout_seconds = max(1.0, float(raw_timeout))
            except ValueError:
                self.timeout_seconds = 15.0
        else:
            self.timeout_seconds = 15.0

    def run_json(
        self,
        args: Sequence[str],
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        command = [str(arg) for arg in args]
        try:
            result = subprocess.run(
                command,
                input=json.dumps(dict(payload)) if payload is not None else None,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise GitHubReviewBridgeError("GitHub CLI `gh` is not installed") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitHubReviewBridgeError(
                f"GitHub review bridge timed out after {self.timeout_seconds:g}s"
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "GitHub command failed").strip()
            raise GitHubReviewBridgeError(detail[:600])
        text = (result.stdout or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise GitHubReviewBridgeError("GitHub command returned invalid JSON") from exc


@dataclass(frozen=True)
class ReviewBinding:
    pr: int
    head_sha: str
    head_branch: str
    base: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReviewBinding":
        try:
            pr = int(value.get("pr"))
        except (TypeError, ValueError) as exc:
            raise GitHubReviewBridgeError("review binding requires a positive PR number") from exc
        head_sha = str(value.get("head_sha") or "").strip().lower()
        head_branch = str(value.get("head_branch") or "").strip()
        base = str(value.get("base") or "").strip()
        if pr <= 0:
            raise GitHubReviewBridgeError("review binding requires a positive PR number")
        if not OID_RE.fullmatch(head_sha):
            raise GitHubReviewBridgeError("review binding requires a full 40-hex head sha")
        if not head_branch:
            raise GitHubReviewBridgeError("review binding requires a head branch")
        if not base:
            raise GitHubReviewBridgeError("review binding requires a base branch")
        return cls(pr=pr, head_sha=head_sha, head_branch=head_branch, base=base)


@dataclass(frozen=True)
class ReviewAdmissionBinding:
    """Immutable delivery facts required before a task may enter review."""

    pr: int
    head_sha: str
    head_branch: str
    base: str
    base_sha: str
    required_merge_method: str
    manifest_path: str
    manifest_blob_sha: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "pr": self.pr,
            "head_sha": self.head_sha,
            "head_branch": self.head_branch,
            "base": self.base,
            "base_sha": self.base_sha,
            "required_merge_method": self.required_merge_method,
            "evidence_manifest": {
                "path": self.manifest_path,
                "blob_sha": self.manifest_blob_sha,
            },
        }


@dataclass(frozen=True)
class BridgeResult:
    repository: str
    pr: int
    head_sha: str
    head_branch: str
    base: str
    decision: str
    actor: str
    mode: str
    github_review_id: int | None
    status_id: int | None
    status_context: str | None
    status_state: str | None
    review_proof_ref: str | None
    pr_url: str
    recorded_at: str
    intent_nonce: str = ""
    review_error: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "repository": self.repository,
            "pr": self.pr,
            "head_sha": self.head_sha,
            "head_branch": self.head_branch,
            "base": self.base,
            "decision": self.decision,
            "actor": self.actor,
            "mode": self.mode,
            "github_review_id": self.github_review_id,
            "status_id": self.status_id,
            "status_context": self.status_context,
            "status_state": self.status_state,
            "review_proof_ref": self.review_proof_ref,
            "pr_url": self.pr_url,
            "recorded_at": self.recorded_at,
            "intent_nonce": self.intent_nonce,
        }
        if self.review_error:
            payload["review_error"] = self.review_error
        return {key: value for key, value in payload.items() if value not in (None, "")}


def validate_result_evidence(
    value: Mapping[str, Any],
    *,
    repository: str,
    actor: str,
    decision: str,
    binding: Mapping[str, Any] | ReviewBinding,
    intent_nonce: str | None = None,
) -> dict[str, Any]:
    """Validate the durable exact-head evidence returned by the bridge.

    Canonical task state stores this payload after network I/O completes.  Keep
    its shape and exact-head checks here beside the producer so status writers
    do not grow a second, subtly different evidence validator.
    """

    repository = _require_repository_slug(repository)
    reviewed = (
        binding if isinstance(binding, ReviewBinding) else ReviewBinding.from_mapping(binding)
    )
    normalized_decision = str(decision or "").strip().lower()
    if normalized_decision not in DECISIONS:
        raise GitHubReviewBridgeError(f"unsupported review decision {decision!r}")
    expected = {
        "repository": repository,
        "pr": reviewed.pr,
        "head_sha": reviewed.head_sha,
        "head_branch": reviewed.head_branch,
        "base": reviewed.base,
        "decision": normalized_decision,
        "actor": str(actor or "").strip(),
    }
    try:
        observed_pr = int(value.get("pr") or 0)
    except (TypeError, ValueError) as exc:
        raise GitHubReviewBridgeError("bridge result has an invalid PR number") from exc
    observed = {
        "repository": str(value.get("repository") or "").strip(),
        "pr": observed_pr,
        "head_sha": str(value.get("head_sha") or "").strip().lower(),
        "head_branch": str(value.get("head_branch") or "").strip(),
        "base": str(value.get("base") or "").strip(),
        "decision": str(value.get("decision") or "").strip().lower(),
        "actor": str(value.get("actor") or "").strip(),
    }
    if observed != expected:
        raise GitHubReviewBridgeError(
            f"bridge result exact-head mismatch: expected={expected!r} observed={observed!r}"
        )
    if intent_nonce is not None and str(value.get("intent_nonce") or "") != intent_nonce:
        raise GitHubReviewBridgeError("bridge result intent nonce mismatch")

    mode = str(value.get("mode") or "").strip()
    review_recorded = bool(value.get("github_review_id"))
    status_recorded = bool(
        value.get("status_id")
        and value.get("status_context") == CANONICAL_REVIEW_CONTEXT
        and str(value.get("status_state") or "").strip().lower()
        == STATUS_STATES[normalized_decision]
    )
    recognized = {
        "pull_request_review": review_recorded,
        "required_commit_status": status_recorded,
        "pull_request_review_and_required_status": review_recorded and status_recorded,
    }
    if not recognized.get(mode, False):
        raise GitHubReviewBridgeError(
            f"bridge result has no recognized {normalized_decision} evidence for mode {mode!r}"
        )
    proof_ref = str(value.get("review_proof_ref") or "").strip()
    expected_proof = (
        f"refs/tags/{REVIEW_PROOF_TAG_PREFIX}/{normalized_decision}/{reviewed.head_sha}"
    )
    if proof_ref != expected_proof:
        raise GitHubReviewBridgeError(
            f"bridge result proof ref mismatch: {proof_ref!r} != {expected_proof!r}"
        )
    return dict(value)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_repository_slug(value: str) -> str:
    slug = str(value or "").strip()
    if not REPO_RE.fullmatch(slug):
        raise GitHubReviewBridgeError(
            f"GitHub repository must use owner/name form, got {slug!r}"
        )
    return slug


def repository_from_pull_request_url(value: Any) -> str | None:
    """Return a GitHub ``owner/repo`` slug from a normal pull-request URL."""

    parsed = urlparse(str(value or "").strip())
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4 or parts[2] != "pull" or not parts[3].isdigit():
        return None
    slug = f"{parts[0]}/{parts[1]}"
    try:
        return _require_repository_slug(slug)
    except GitHubReviewBridgeError:
        return None


def _is_task_brief_path(value: Any) -> bool:
    path = str(value or "").strip().lstrip("/")
    if not path.startswith(TASK_BRIEF_PREFIX):
        return False
    remainder = path[len(TASK_BRIEF_PREFIX) :]
    return bool(remainder) and ".." not in remainder.split("/")


def task_brief_only_successor(
    *,
    repository: str,
    approved_head_sha: str,
    successor_head_sha: str,
    runner: JsonRunner | None = None,
) -> dict[str, Any] | None:
    """Classify one harmless generated-task-brief successor, or reject it.

    This is intentionally much narrower than a generic docs-only exemption.
    The successor must be a *single direct child* of the approved head, and
    every reported new and previous filename must be inside task-briefs. Any
    unavailable, truncated, malformed, renamed-from-code, or multi-commit
    response fails closed by returning ``None``.

    The caller may then carry the existing review forward to the successor
    without pretending that a broader post-approval change was reviewed.
    """

    repository = _require_repository_slug(repository)
    approved = str(approved_head_sha or "").strip().lower()
    successor = str(successor_head_sha or "").strip().lower()
    if not OID_RE.fullmatch(approved) or not OID_RE.fullmatch(successor):
        return None
    if approved == successor:
        return None
    client = runner or GhJsonRunner()
    try:
        payload = client.run_json(
            [
                "gh",
                "api",
                f"repos/{repository}/commits/{successor}"
                f"?per_page={COMMIT_FILES_PAGE_SIZE}&page=1",
            ]
        )
    except GitHubReviewBridgeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    if str(payload.get("sha") or "").strip().lower() != successor:
        return None
    parents = payload.get("parents")
    if not isinstance(parents, list) or len(parents) != 1:
        return None
    parent = parents[0]
    if not isinstance(parent, Mapping):
        return None
    if str(parent.get("sha") or "").strip().lower() != approved:
        return None
    files = payload.get("files")
    if (
        not isinstance(files, list)
        or not files
        or len(files) > MAX_SAFE_SUCCESSOR_FILES
        or bool(payload.get("truncated"))
    ):
        return None
    changed_paths: list[str] = []
    for file in files:
        if not isinstance(file, Mapping):
            return None
        filename = str(file.get("filename") or "").strip().lstrip("/")
        if not _is_task_brief_path(filename):
            return None
        previous_filename = file.get("previous_filename")
        if previous_filename is not None and not _is_task_brief_path(previous_filename):
            return None
        changed_paths.append(filename)
    return {
        "kind": "task_brief_only_successor",
        "approved_head_sha": approved,
        "successor_head_sha": successor,
        "changed_paths": changed_paths,
    }


def carry_approval_to_task_brief_only_successor(
    *,
    repository: str,
    task_id: str,
    actor: str,
    approved_head_sha: str,
    successor_head_sha: str,
    pr: int,
    head_branch: str,
    base: str,
    publish: bool,
    runner: JsonRunner | None = None,
) -> dict[str, Any] | None:
    """Carry one approved task-brief-only successor without a new review.

    The classifier is deliberately fail-closed. When it accepts the direct
    successor and ``publish`` is true, it records a git-native proof tag at
    that successor and wakes the canonical gate workflow. No PR review, status
    row, task brief, or branch commit is written as a side effect.
    """

    client = runner or GhJsonRunner()
    carried = task_brief_only_successor(
        repository=repository,
        approved_head_sha=approved_head_sha,
        successor_head_sha=successor_head_sha,
        runner=client,
    )
    if carried is None:
        return None
    if not publish:
        return carried
    return publish_task_brief_only_successor_proof(
        repository=repository,
        task_id=task_id,
        actor=actor,
        carried=carried,
        pr=pr,
        head_branch=head_branch,
        base=base,
        runner=client,
    )


def publish_task_brief_only_successor_proof(
    *,
    repository: str,
    task_id: str,
    actor: str,
    carried: Mapping[str, Any],
    pr: int,
    head_branch: str,
    base: str,
    dispatch_if_proof_exists: bool = True,
    runner: JsonRunner | None = None,
) -> dict[str, Any]:
    """Publish a proof for an already-classified harmless successor.

    Callers must first classify the successor and obtain an allow decision from
    the full review gate.  Keeping this write-only step separate makes it
    impossible for classification to create a GitHub proof for a head the gate
    subsequently rejects.
    """

    if carried.get("kind") != "task_brief_only_successor":
        raise GitHubReviewBridgeError("carry-forward proof requires a task-brief-only classification")
    approved_head_sha = str(carried.get("approved_head_sha") or "").strip().lower()
    successor_head_sha = str(carried.get("successor_head_sha") or "").strip().lower()
    changed_paths = carried.get("changed_paths")
    if (
        not OID_RE.fullmatch(approved_head_sha)
        or not OID_RE.fullmatch(successor_head_sha)
        or approved_head_sha == successor_head_sha
        or not isinstance(changed_paths, list)
        or not changed_paths
        or not all(_is_task_brief_path(path) for path in changed_paths)
    ):
        raise GitHubReviewBridgeError("carry-forward proof received an invalid task-brief classification")
    binding = ReviewBinding.from_mapping(
        {
            "pr": pr,
            "head_sha": successor_head_sha,
            "head_branch": head_branch,
            "base": base,
        }
    )
    client = runner or GhJsonRunner()
    proof = _push_review_proof_tag(
        client,
        repository=_require_repository_slug(repository),
        binding=binding,
        task_id=task_id,
        actor=actor,
        decision=APPROVE,
        message=(
            "Automatic carry-forward: this one direct successor changes only "
            "generated .orchestrator/task-briefs/ paths after the approved head."
        ),
    )
    proof_published = bool(proof.get("created"))
    # A new proof always needs a workflow run.  If the ref already exists,
    # callers can suppress a redundant dispatch only after observing the
    # canonical check green on a later integration pass.  Keeping the retry
    # path available matters when a worker died after creating the tag but
    # before dispatching the workflow.
    workflow_dispatched = proof_published or dispatch_if_proof_exists
    if workflow_dispatched:
        _dispatch_canonical_review_gate_workflow(
            client,
            repository=_require_repository_slug(repository),
            binding=binding,
            required=True,
        )
    return {
        **dict(carried),
        "review_proof_ref": str(proof.get("ref") or "") or None,
        "proof_published": proof_published,
        "workflow_dispatched": workflow_dispatched,
    }


def _review_marker(
    *,
    task_id: str,
    actor: str,
    decision: str,
    head_sha: str,
    intent_nonce: str = "",
) -> str:
    return (
        "<!-- pantheon-review-bridge "
        f"task={task_id} actor={actor} decision={decision} head={head_sha}"
        f"{f' intent={intent_nonce}' if intent_nonce else ''} -->"
    )


def _review_body(
    *,
    task_id: str,
    actor: str,
    decision: str,
    head_sha: str,
    message: str,
    intent_nonce: str = "",
) -> str:
    verdict = "approved" if decision == APPROVE else "requested changes"
    return (
        f"Pantheon governed reviewer `{actor}` {verdict} for task `{task_id}` "
        f"at exact head `{head_sha}`.\n\n{message.strip()}\n\n"
        f"{_review_marker(task_id=task_id, actor=actor, decision=decision, head_sha=head_sha, intent_nonce=intent_nonce)}"
    ).strip()


def _pr_snapshot(
    runner: JsonRunner,
    *,
    repository: str,
    binding: ReviewBinding,
    allowed_states: frozenset[str] = frozenset({"OPEN", "MERGED"}),
) -> dict[str, Any]:
    payload = runner.run_json(
        [
            "gh",
            "pr",
            "view",
            str(binding.pr),
            "--repo",
            repository,
            "--json",
            "number,url,state,headRefName,headRefOid,baseRefName,"
            "isDraft,mergeStateStatus,autoMergeRequest",
        ]
    )
    if not isinstance(payload, Mapping):
        raise GitHubReviewBridgeError(f"GitHub PR #{binding.pr} metadata is unavailable")
    if int(payload.get("number") or 0) != binding.pr:
        raise ReviewBindingMismatch(f"GitHub returned the wrong PR for #{binding.pr}")
    actual_state = str(payload.get("state") or "").upper()
    if actual_state not in allowed_states:
        expected = " or ".join(sorted(state.lower() for state in allowed_states))
        raise ReviewBindingMismatch(
            f"GitHub PR #{binding.pr} is {actual_state.lower() or 'unknown'}, "
            f"expected {expected}"
        )
    actual_head = str(payload.get("headRefOid") or "").strip().lower()
    actual_branch = str(payload.get("headRefName") or "").strip()
    actual_base = str(payload.get("baseRefName") or "").strip()
    mismatches: list[str] = []
    if actual_head != binding.head_sha:
        mismatches.append(f"head {actual_head or 'missing'} != {binding.head_sha}")
    if actual_branch != binding.head_branch:
        mismatches.append(f"branch {actual_branch or 'missing'} != {binding.head_branch}")
    if actual_base != binding.base:
        mismatches.append(f"base {actual_base or 'missing'} != {binding.base}")
    if mismatches:
        raise ReviewBindingMismatch(
            f"GitHub PR #{binding.pr} no longer matches reviewed identity: "
            + "; ".join(mismatches)
        )
    return dict(payload)


def _current_base_ref_sha(
    runner: JsonRunner,
    *,
    repository: str,
    base: str,
    pr: int,
) -> str:
    """Resolve the current base commit through the stable REST ref endpoint.

    ``gh pr view --json`` is convenient for PR identity but its supported
    GraphQL-field set varies by CLI version.  In particular, gh 2.45 does not
    accept ``baseRefOid``.  The Git Data REST endpoint is available on that
    version and gives us the same commit identity without weakening the
    ancestry check that follows.
    """

    ref = quote(str(base or "").strip(), safe="")
    payload = runner.run_json(
        ["gh", "api", f"repos/{repository}/git/ref/heads/{ref}"]
    )
    object_payload = payload.get("object") if isinstance(payload, Mapping) else None
    base_sha = (
        str(object_payload.get("sha") or "").strip().lower()
        if isinstance(object_payload, Mapping)
        else ""
    )
    if not OID_RE.fullmatch(base_sha):
        raise GitHubReviewBridgeError(
            f"GitHub PR #{pr} has no current base SHA for {base}"
        )
    return base_sha


def _review_manifest_identity(
    runner: JsonRunner,
    *,
    repository: str,
    head_sha: str,
    base_sha: str,
    pr: int,
    review_file: str,
) -> tuple[str, str]:
    raw_path = str(review_file or "").strip()
    path = raw_path.rstrip("/")
    if (
        not path
        or raw_path.startswith("/")
        or path in {".", ".."}
        or path.startswith("../")
        or "/../" in f"/{path}/"
    ):
        raise GitHubReviewBridgeError(
            "review admission requires a repository-relative REVIEW_FILE"
        )
    payload = runner.run_json(
        [
            "gh",
            "api",
            f"repos/{repository}/contents/{quote(path, safe='/')}?ref={head_sha}",
        ]
    )
    if not isinstance(payload, Mapping) or str(payload.get("type") or "") != "file":
        raise GitHubReviewBridgeError(
            f"REVIEW_FILE={path!r} is not a committed file at head {head_sha}"
        )
    blob_sha = str(payload.get("sha") or "").strip().lower()
    if not OID_RE.fullmatch(blob_sha):
        raise GitHubReviewBridgeError(
            f"REVIEW_FILE={path!r} has no immutable Git blob identity at head {head_sha}"
        )
    base_endpoint = (
        f"repos/{repository}/contents/{quote(path, safe='/')}?ref={base_sha}"
    )

    def exact_pr_file_change() -> bool:
        files = runner.run_json(
            ["gh", "api", f"repos/{repository}/pulls/{pr}/files?per_page=100"]
        )
        return isinstance(files, list) and any(
            isinstance(item, Mapping)
            and str(item.get("filename") or "") == path
            and str(item.get("sha") or "").strip().lower() == blob_sha
            and str(item.get("status") or "").strip().lower()
            in {"added", "modified", "renamed"}
            for item in files
        )

    try:
        base_payload = runner.run_json(["gh", "api", base_endpoint])
    except GitHubReviewBridgeError as exc:
        detail = str(exc).casefold()
        if "not found" in detail or "404" in detail:
            return path, blob_sha
        if not exact_pr_file_change():
            raise GitHubReviewBridgeError(
                f"REVIEW_FILE={path!r} has no exact PR-file change evidence"
            ) from exc
        return path, blob_sha
    if not isinstance(base_payload, Mapping) or str(base_payload.get("type") or "") != "file":
        if exact_pr_file_change():
            return path, blob_sha
        raise GitHubReviewBridgeError(
            f"REVIEW_FILE={path!r} has malformed base contents and no exact "
            "PR-file change evidence"
        )
    base_blob_sha = str(base_payload.get("sha") or "").strip().lower()
    if not OID_RE.fullmatch(base_blob_sha):
        if exact_pr_file_change():
            return path, blob_sha
        raise GitHubReviewBridgeError(
            f"REVIEW_FILE={path!r} has invalid base blob identity at {base_sha} "
            "and no exact PR-file change evidence"
        )
    if base_blob_sha == blob_sha:
        raise GitHubReviewBridgeError(
            f"REVIEW_FILE={path!r} is unchanged from the exact base {base_sha}"
        )
    return path, blob_sha


def _validate_pr_admission_metadata(
    *,
    repository: str,
    binding: Mapping[str, Any] | ReviewBinding,
    required_merge_method: str = REQUIRED_REVIEW_MERGE_METHOD,
    allow_base_advance: bool = False,
    frozen_base_sha: str = "",
    runner: JsonRunner | None = None,
) -> tuple[ReviewBinding, str, str, JsonRunner]:
    repository = _require_repository_slug(repository)
    normalized = (
        binding
        if isinstance(binding, ReviewBinding)
        else ReviewBinding.from_mapping(binding)
    )
    method = str(required_merge_method or "").strip().upper()
    if method != REQUIRED_REVIEW_MERGE_METHOD:
        raise GitHubReviewBridgeError("review admission requires merge method MERGE")
    client = runner or GhJsonRunner()
    snapshot = _pr_snapshot(
        client,
        repository=repository,
        binding=normalized,
        allowed_states=frozenset({"OPEN"}),
    )
    if str(snapshot.get("state") or "").strip().upper() != "OPEN":
        raise GitHubReviewBridgeError(
            f"GitHub PR #{normalized.pr} must be open before review admission"
        )
    if bool(snapshot.get("isDraft")):
        raise GitHubReviewBridgeError(
            f"GitHub PR #{normalized.pr} is a draft and cannot enter review"
        )

    auto_merge = snapshot.get("autoMergeRequest")
    if auto_merge:
        armed_method = (
            str(auto_merge.get("mergeMethod") or "").strip().upper()
            if isinstance(auto_merge, Mapping)
            else "UNKNOWN"
        )
        raise GitHubReviewBridgeError(
            f"GitHub PR #{normalized.pr} already has armed auto-merge "
            f"({armed_method or 'UNKNOWN'}); supervisor integration owns every merge"
        )

    merge_state = str(snapshot.get("mergeStateStatus") or "").strip().upper()
    if merge_state == "DIRTY":
        raise GitHubReviewBridgeError(
            f"GitHub PR #{normalized.pr} has merge conflicts and cannot enter review"
        )
    if merge_state == "BEHIND" and not allow_base_advance:
        raise GitHubReviewBridgeError(
            f"GitHub PR #{normalized.pr} is BEHIND {normalized.base}; refresh it before review"
        )
    base_sha = _current_base_ref_sha(
        client,
        repository=repository,
        base=normalized.base,
        pr=normalized.pr,
    )
    comparison = client.run_json(
        [
            "gh",
            "api",
            f"repos/{repository}/compare/{base_sha}...{normalized.head_sha}",
        ]
    )
    if not isinstance(comparison, Mapping):
        raise GitHubReviewBridgeError(
            f"GitHub PR #{normalized.pr} base ancestry is unavailable"
        )
    compare_status = str(comparison.get("status") or "").strip().lower()
    try:
        behind_by = int(comparison.get("behind_by") or 0)
    except (TypeError, ValueError) as exc:
        raise GitHubReviewBridgeError(
            f"GitHub PR #{normalized.pr} has invalid base ancestry evidence"
        ) from exc
    current_base_is_contained = (
        compare_status in {"ahead", "identical"} and behind_by == 0
    )
    if not current_base_is_contained:
        frozen_base_sha = str(frozen_base_sha or "").strip().lower()
        if not allow_base_advance or not OID_RE.fullmatch(frozen_base_sha):
            raise GitHubReviewBridgeError(
                f"GitHub PR #{normalized.pr} head does not contain current base "
                f"{base_sha} (status={compare_status or 'unknown'}, behind_by={behind_by})"
            )
        advance = client.run_json(
            [
                "gh",
                "api",
                f"repos/{repository}/compare/{frozen_base_sha}...{base_sha}",
            ]
        )
        if not isinstance(advance, Mapping):
            raise GitHubReviewBridgeError(
                f"GitHub PR #{normalized.pr} frozen-base advance evidence is unavailable"
            )
        advance_status = str(advance.get("status") or "").strip().lower()
        try:
            advance_behind_by = int(advance.get("behind_by") or 0)
        except (TypeError, ValueError) as exc:
            raise GitHubReviewBridgeError(
                f"GitHub PR #{normalized.pr} has invalid frozen-base advance evidence"
            ) from exc
        if advance_status not in {"ahead", "identical"} or advance_behind_by != 0:
            raise GitHubReviewBridgeError(
                f"GitHub PR #{normalized.pr} current base {base_sha} is not a linear "
                f"advance of frozen base {frozen_base_sha} "
                f"(status={advance_status or 'unknown'}, behind_by={advance_behind_by})"
            )
    return normalized, method, base_sha, client


def validate_review_admission(
    *,
    repository: str,
    binding: Mapping[str, Any] | ReviewBinding,
    review_file: str,
    required_merge_method: str = REQUIRED_REVIEW_MERGE_METHOD,
    allow_base_advance: bool = False,
    frozen_base_sha: str = "",
    runner: JsonRunner | None = None,
) -> ReviewAdmissionBinding:
    """Fail closed before a canonical task is allowed to enter ``review``.

    This is deliberately stricter than :func:`validate_review_binding`, which
    also supports an approval retry after a PR has merged. Review admission
    requires an open, current delivery whose evidence and merge policy can be
    frozen before any lifecycle mutation occurs.
    """

    normalized, method, base_sha, client = _validate_pr_admission_metadata(
        repository=repository,
        binding=binding,
        required_merge_method=required_merge_method,
        allow_base_advance=allow_base_advance,
        frozen_base_sha=frozen_base_sha,
        runner=runner,
    )
    manifest_path, manifest_blob_sha = _review_manifest_identity(
        client,
        repository=repository,
        head_sha=normalized.head_sha,
        base_sha=base_sha,
        pr=normalized.pr,
        review_file=review_file,
    )
    return ReviewAdmissionBinding(
        pr=normalized.pr,
        head_sha=normalized.head_sha,
        head_branch=normalized.head_branch,
        base=normalized.base,
        base_sha=base_sha,
        required_merge_method=method,
        manifest_path=manifest_path,
        manifest_blob_sha=manifest_blob_sha,
    )


def rehabilitate_operator_admission(
    *,
    repository: str,
    binding: Mapping[str, Any] | ReviewBinding,
    required_merge_method: str = REQUIRED_REVIEW_MERGE_METHOD,
    allow_base_advance: bool = False,
    frozen_base_sha: str = "",
    runner: JsonRunner | None = None,
) -> ReviewAdmissionBinding:
    """Validate and rehabilitate a legacy PR delivery for Human/Ops operator acceptance.

    Rehabilitates the minimal delivery binding by verifying the PR's exact
    identity, open state, and base ancestry on GitHub without requiring or
    fabricating an evidence manifest.
    """

    normalized, method, base_sha, _ = _validate_pr_admission_metadata(
        repository=repository,
        binding=binding,
        required_merge_method=required_merge_method,
        allow_base_advance=allow_base_advance,
        frozen_base_sha=frozen_base_sha,
        runner=runner,
    )
    return ReviewAdmissionBinding(
        pr=normalized.pr,
        head_sha=normalized.head_sha,
        head_branch=normalized.head_branch,
        base=normalized.base,
        base_sha=base_sha,
        required_merge_method=method,
        manifest_path=None,
        manifest_blob_sha=None,
    )


def revalidate_review_admission(
    *,
    repository: str,
    delivery_binding: Mapping[str, Any],
    allow_base_advance: bool = True,
    runner: JsonRunner | None = None,
) -> ReviewAdmissionBinding:
    """Recheck a frozen admission before approval can unlock integration.

    The exact reviewed head, branch and evidence blob remain frozen. An
    unrelated linear base advance is accepted when GitHub still reports the
    exact PR open and non-conflicting. A head/branch/blob mismatch, base rewind/divergence,
    conflict or non-linear base advance still fails closed.
    """

    manifest = delivery_binding.get("evidence_manifest")
    if not isinstance(manifest, Mapping):
        raise GitHubReviewBridgeError("delivery binding has no frozen evidence manifest")
    frozen_path = str(manifest.get("path") or "").strip()
    frozen_blob_sha = str(manifest.get("blob_sha") or "").strip().lower()
    frozen_base_sha = str(delivery_binding.get("base_sha") or "").strip().lower()
    frozen_method = str(
        delivery_binding.get("required_merge_method") or ""
    ).strip().upper()
    if (
        not frozen_path
        or not OID_RE.fullmatch(frozen_blob_sha)
        or not OID_RE.fullmatch(frozen_base_sha)
        or frozen_method != REQUIRED_REVIEW_MERGE_METHOD
    ):
        raise GitHubReviewBridgeError("delivery binding has incomplete review admission evidence")
    current = validate_review_admission(
        repository=repository,
        binding=delivery_binding,
        review_file=frozen_path,
        required_merge_method=frozen_method,
        allow_base_advance=allow_base_advance,
        frozen_base_sha=frozen_base_sha,
        runner=runner,
    )
    if current.manifest_blob_sha != frozen_blob_sha:
        raise GitHubReviewBridgeError(
            "review evidence manifest blob differs from the handoff admission"
        )
    return current


def revalidate_operator_admission(
    *,
    repository: str,
    delivery_binding: Mapping[str, Any],
    allow_base_advance: bool = True,
    runner: JsonRunner | None = None,
) -> ReviewAdmissionBinding:
    """Recheck a delivery binding before Human/Ops operator acceptance.

    If a frozen evidence manifest is present, it is revalidated. If absent
    (rehabilitated legacy PR row), admission is rechecked without manifest
    evidence.
    """

    manifest = delivery_binding.get("evidence_manifest")
    if isinstance(manifest, Mapping) and str(manifest.get("path") or "").strip():
        return revalidate_review_admission(
            repository=repository,
            delivery_binding=delivery_binding,
            allow_base_advance=allow_base_advance,
            runner=runner,
        )
    frozen_base_sha = str(delivery_binding.get("base_sha") or "").strip().lower()
    frozen_method = str(
        delivery_binding.get("required_merge_method") or REQUIRED_REVIEW_MERGE_METHOD
    ).strip().upper()
    return rehabilitate_operator_admission(
        repository=repository,
        binding=delivery_binding,
        required_merge_method=frozen_method,
        allow_base_advance=allow_base_advance,
        frozen_base_sha=frozen_base_sha,
        runner=runner,
    )


def validate_review_binding(
    *,
    repository: str,
    binding: Mapping[str, Any] | ReviewBinding,
    runner: JsonRunner | None = None,
) -> ReviewBinding:
    """Verify one proposed canonical binding against GitHub without mutation.

    Handoff calls this before persisting review identity.  Review decisions call
    the same ``_pr_snapshot`` path before any GitHub write, so exact PR identity
    has one validator instead of separate handoff and reviewer interpretations.
    """

    repository = _require_repository_slug(repository)
    normalized = (
        binding
        if isinstance(binding, ReviewBinding)
        else ReviewBinding.from_mapping(binding)
    )
    _pr_snapshot(
        runner or GhJsonRunner(),
        repository=repository,
        binding=normalized,
    )
    return normalized


def _reviews(
    runner: JsonRunner,
    *,
    repository: str,
    pr: int,
) -> list[dict[str, Any]]:
    payload = runner.run_json(
        ["gh", "api", f"repos/{repository}/pulls/{pr}/reviews?per_page=100"]
    )
    if not isinstance(payload, list):
        raise GitHubReviewBridgeError(f"GitHub reviews for PR #{pr} are unavailable")
    return [dict(item) for item in payload if isinstance(item, Mapping)]


def _matching_review(
    reviews: Sequence[Mapping[str, Any]],
    *,
    marker: str,
    expected_state: str,
    head_sha: str,
) -> dict[str, Any] | None:
    for review in reversed(reviews):
        if str(review.get("state") or "").upper() != expected_state:
            continue
        if str(review.get("commit_id") or "").strip().lower() != head_sha:
            continue
        if marker not in str(review.get("body") or ""):
            continue
        return dict(review)
    return None


def _submit_review(
    runner: JsonRunner,
    *,
    repository: str,
    binding: ReviewBinding,
    body: str,
    marker: str,
    decision: str,
) -> dict[str, Any]:
    expected_state = REVIEW_STATES[decision]
    existing = _matching_review(
        _reviews(runner, repository=repository, pr=binding.pr),
        marker=marker,
        expected_state=expected_state,
        head_sha=binding.head_sha,
    )
    if existing is not None:
        return existing
    runner.run_json(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{repository}/pulls/{binding.pr}/reviews",
            "--input",
            "-",
        ],
        payload={
            "body": body,
            "event": REVIEW_EVENTS[decision],
            "commit_id": binding.head_sha,
        },
    )
    created = _matching_review(
        _reviews(runner, repository=repository, pr=binding.pr),
        marker=marker,
        expected_state=expected_state,
        head_sha=binding.head_sha,
    )
    if created is None:
        raise GitHubReviewBridgeError(
            f"GitHub did not expose the {expected_state} review for PR #{binding.pr}"
        )
    return created


def _required_status_contexts(
    runner: JsonRunner,
    *,
    repository: str,
    base: str,
) -> set[str]:
    payload = runner.run_json(
        [
            "gh",
            "api",
            f"repos/{repository}/branches/{quote(base, safe='')}/protection/required_status_checks",
        ]
    )
    if not isinstance(payload, Mapping):
        return set()
    contexts = {
        str(item).strip()
        for item in payload.get("contexts", [])
        if str(item).strip()
    }
    for check in payload.get("checks", []):
        if not isinstance(check, Mapping):
            continue
        context = str(check.get("context") or "").strip()
        if context:
            contexts.add(context)
    return contexts


def _statuses(
    runner: JsonRunner,
    *,
    repository: str,
    head_sha: str,
) -> list[dict[str, Any]]:
    payload = runner.run_json(
        ["gh", "api", f"repos/{repository}/commits/{head_sha}/statuses?per_page=100"]
    )
    if not isinstance(payload, list):
        raise GitHubReviewBridgeError(
            f"GitHub commit statuses for {head_sha} are unavailable"
        )
    return [dict(item) for item in payload if isinstance(item, Mapping)]


def _matching_latest_status(
    statuses: Sequence[Mapping[str, Any]],
    *,
    expected_state: str,
    target_url: str,
) -> dict[str, Any] | None:
    for status in statuses:
        if str(status.get("context") or "") != CANONICAL_REVIEW_CONTEXT:
            continue
        if str(status.get("state") or "").lower() != expected_state:
            return None
        if str(status.get("target_url") or "").strip() != target_url:
            return None
        return dict(status)
    return None


def _submit_required_status(
    runner: JsonRunner,
    *,
    repository: str,
    binding: ReviewBinding,
    task_id: str,
    actor: str,
    decision: str,
    target_url: str,
) -> dict[str, Any]:
    expected_state = STATUS_STATES[decision]
    existing = _matching_latest_status(
        _statuses(runner, repository=repository, head_sha=binding.head_sha),
        expected_state=expected_state,
        target_url=target_url,
    )
    if existing is not None:
        return existing
    description = (
        f"{task_id} {decision} by {actor} at {binding.head_sha[:12]}"
    )[:140]
    created = runner.run_json(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{repository}/statuses/{binding.head_sha}",
            "--input",
            "-",
        ],
        payload={
            "state": expected_state,
            "context": CANONICAL_REVIEW_CONTEXT,
            "description": description,
            "target_url": target_url,
        },
    )
    if not isinstance(created, Mapping):
        raise GitHubReviewBridgeError("GitHub did not return the canonical review status")
    observed = _matching_latest_status(
        _statuses(runner, repository=repository, head_sha=binding.head_sha),
        expected_state=expected_state,
        target_url=target_url,
    )
    if observed is None:
        raise GitHubReviewBridgeError(
            f"GitHub did not expose required status {CANONICAL_REVIEW_CONTEXT!r}"
        )
    return observed


def review_proof_tag_name(*, decision: str, head_sha: str) -> str:
    return f"{REVIEW_PROOF_TAG_PREFIX}/{decision}/{head_sha}"


def operator_acceptance_proof_tag_name(*, head_sha: str) -> str:
    """Return the exact-head proof ref name for a Human/Ops acceptance."""

    return review_proof_tag_name(decision=OPERATOR_ACCEPT, head_sha=head_sha)


def _push_review_proof_tag(
    runner: JsonRunner,
    *,
    repository: str,
    binding: ReviewBinding,
    task_id: str,
    actor: str,
    decision: str,
    message: str,
) -> dict[str, Any]:
    """Push a git tag at the exact reviewed head recording the decision.

    Idempotent: if the tag ref already exists (a retried approve/reopen on
    the same head), it is returned as-is rather than recreated, matching
    `_submit_required_status`'s existing-first pattern.
    """

    tag_name = review_proof_tag_name(decision=decision, head_sha=binding.head_sha)
    ref = f"refs/tags/{tag_name}"
    # GitHub's git-refs lookup route takes `git/refs/tags/<name>` with
    # `refs/tags/` as literal path segments -- only the tag's own internal
    # slashes need percent-encoding. Encoding the whole ref (including
    # `refs/tags/` itself) 404s; verified against the live API before this
    # landed, after the first version of this file shipped that exact bug.
    encoded_tag_name = quote(tag_name, safe="")
    try:
        existing = runner.run_json(
            ["gh", "api", f"repos/{repository}/git/refs/tags/{encoded_tag_name}"]
        )
    except GitHubReviewBridgeError:
        # `gh api` exits non-zero on a 404, which is the expected outcome the
        # first time this exact head is approved/reopened -- not a failure.
        existing = None
    if isinstance(existing, Mapping) and existing.get("ref") == ref:
        return {**dict(existing), "created": False}

    tag_message = json.dumps(
        {
            "task_id": task_id,
            "decision": decision,
            "actor": actor,
            "pr": binding.pr,
            "head_sha": binding.head_sha,
            "head_branch": binding.head_branch,
            "base": binding.base,
            "message": message,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    created_tag = runner.run_json(
        ["gh", "api", "--method", "POST", f"repos/{repository}/git/tags", "--input", "-"],
        payload={
            "tag": tag_name,
            "message": tag_message,
            "object": binding.head_sha,
            "type": "commit",
            "tagger": {
                "name": "Pantheon Review Bridge",
                "email": "pantheon-review-bridge@noreply.local",
                "date": _utc_now(),
            },
        },
    )
    if not isinstance(created_tag, Mapping) or not created_tag.get("sha"):
        raise GitHubReviewBridgeError("GitHub did not return the review-proof tag object")
    created_ref = runner.run_json(
        ["gh", "api", "--method", "POST", f"repos/{repository}/git/refs", "--input", "-"],
        payload={"ref": ref, "sha": created_tag["sha"]},
    )
    if not isinstance(created_ref, Mapping) or created_ref.get("ref") != ref:
        raise GitHubReviewBridgeError("GitHub did not expose the pushed review-proof tag ref")
    return {**dict(created_ref), "created": True}


# The two current delivery repositories intentionally use different workflow
# display names (and filenames) for the same protected check. Keep this short
# compatibility set at the GitHub workflow boundary; task state and product
# code stay repository-agnostic.
CANONICAL_REVIEW_GATE_WORKFLOW_NAMES = frozenset(
    {"Canonical Review Gate", "Pantheon canonical review gate"}
)
WORKFLOW_DISPATCH_DELIVERY_CLASS_INPUT = "delivery_class"
WORKFLOW_DISPATCH_PRODUCT_DELIVERY_CLASS = "product"


def _canonical_review_gate_workflow_id(
    runner: JsonRunner,
    *,
    repository: str,
) -> str:
    """Return the active canonical-gate workflow id for this repository.

    Pantheon and execute-plans intentionally use different workflow filenames.
    The protected check's display name is the shared contract, while a filename
    is repository-local implementation detail. Dispatching by GitHub's stable
    workflow id keeps a proof-tag retry on the same repository that owns the
    reviewed PR.
    """

    payload = runner.run_json(
        ["gh", "api", f"repos/{repository}/actions/workflows?per_page=100"]
    )
    workflows = payload.get("workflows") if isinstance(payload, Mapping) else None
    if not isinstance(workflows, list):
        raise GitHubReviewBridgeError(
            "GitHub workflow inventory is unavailable for canonical review dispatch"
        )
    candidates = [
        workflow
        for workflow in workflows
        if isinstance(workflow, Mapping)
        and str(workflow.get("name") or "").strip()
        in CANONICAL_REVIEW_GATE_WORKFLOW_NAMES
        and str(workflow.get("state") or "").strip().lower() == "active"
        and str(workflow.get("id") or "").strip().isdigit()
    ]
    if len(candidates) != 1:
        raise GitHubReviewBridgeError(
            "GitHub repository must expose exactly one active "
            f"canonical review-gate workflow; found {len(candidates)}"
        )
    return str(candidates[0]["id"])


def _dispatch_canonical_review_gate_workflow(
    runner: JsonRunner,
    *,
    repository: str,
    binding: ReviewBinding,
    required: bool = False,
) -> None:
    """Best-effort: wake the Canonical Review Gate workflow so it re-reads
    the tag just pushed and posts its own, correctly-attributed status.

    Pushing the review-proof tag is necessary but not sufficient. GitHub
    pins the "Pantheon canonical review gate" required context to whichever
    identity has historically posted it -- in practice, this workflow's own
    GITHUB_TOKEN-authenticated run. A status this process posts from a
    personal-token host (e.g. _submit_required_status, above) does not
    satisfy the required check even though it looks identical in a plain
    status listing -- verified empirically against a live PR: the status
    shows success, but GitHub's own mergeable_state stays blocked until the
    workflow itself runs again. None of that workflow's pull_request event
    types fire from a bare tag push, so dispatch it explicitly here.
    Dispatch against `binding.base` (the PR's base branch, e.g. "dev"), not
    `binding.head_branch`. GitHub's workflow-dispatch API validates that the
    workflow_dispatch trigger exists in the workflow file AS IT EXISTS ON THE
    TARGET REF being dispatched -- not on the default branch. A task branch
    created before this trigger existed (or during any later regression that
    dropped it, e.g. a stale-branch squash-merge reverting the file) does not
    carry it in its own history, so dispatching against that branch 422s with
    "Workflow does not have 'workflow_dispatch' trigger" even though the
    workflow definitively has that trigger on dev right now -- verified
    empirically against a live, indefinitely-stuck PR. The checked-out ref
    only supplies the script file to run; canonical_review_gate_ci.py acts
    entirely on the explicit --head-ref/--head-sha inputs below regardless of
    what commit is physically checked out, so targeting the base branch is
    always correct and always has the current trigger definition.

    Normal reviewer decisions treat dispatch as best-effort because their
    proof is already durable. A carried approval is different: it exists only
    to make the successor's required check re-evaluate, so its caller passes
    ``required=True`` and must fail closed if the dispatch cannot be made.
    """

    try:
        workflow_id = _canonical_review_gate_workflow_id(
            runner,
            repository=repository,
        )
        command = [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{repository}/actions/workflows/{workflow_id}/dispatches",
            "--input",
            "-",
        ]
        payload = {
            "ref": binding.base,
            "inputs": {
                "head_ref": binding.head_branch,
                "head_sha": binding.head_sha,
            },
        }
        try:
            runner.run_json(command, payload=payload)
        except GitHubReviewBridgeError as exc:
            # execute-plans declares delivery_class as a required dispatch
            # input, whereas Pantheon's workflow derives it from the PR label.
            # Retry only for GitHub's explicit missing-input response; any
            # other dispatch error remains fail-closed for required callers.
            if (
                f"Required input '{WORKFLOW_DISPATCH_DELIVERY_CLASS_INPUT}' not provided"
                not in str(exc)
            ):
                raise
            payload["inputs"][WORKFLOW_DISPATCH_DELIVERY_CLASS_INPUT] = (
                WORKFLOW_DISPATCH_PRODUCT_DELIVERY_CLASS
            )
            runner.run_json(command, payload=payload)
    except GitHubReviewBridgeError:
        if required:
            raise


def validate_operator_acceptance_evidence(
    value: Mapping[str, Any],
    *,
    repository: str,
    actor: str,
    binding: Mapping[str, Any] | ReviewBinding,
    intent_nonce: str | None = None,
) -> dict[str, Any]:
    """Validate one durable, non-review Human/Ops exact-head acceptance.

    This is intentionally separate from ``validate_result_evidence``: a
    reviewer bridge result and an operator acceptance have different
    authority.  Treating the latter as a reviewer result would make the
    audit trail lie about what happened.
    """

    repository = _require_repository_slug(repository)
    accepted = binding if isinstance(binding, ReviewBinding) else ReviewBinding.from_mapping(binding)
    expected = {
        "repository": repository,
        "pr": accepted.pr,
        "head_sha": accepted.head_sha,
        "head_branch": accepted.head_branch,
        "base": accepted.base,
        "decision": OPERATOR_ACCEPT,
        "actor": "Human/Ops",
        "mode": "operator_exact_head",
    }
    try:
        observed_pr = int(value.get("pr") or 0)
    except (TypeError, ValueError) as exc:
        raise GitHubReviewBridgeError("operator acceptance has an invalid PR number") from exc
    observed = {
        "repository": str(value.get("repository") or "").strip(),
        "pr": observed_pr,
        "head_sha": str(value.get("head_sha") or "").strip().lower(),
        "head_branch": str(value.get("head_branch") or "").strip(),
        "base": str(value.get("base") or "").strip(),
        "decision": str(value.get("decision") or "").strip(),
        "actor": str(value.get("actor") or "").strip(),
        "mode": str(value.get("mode") or "").strip(),
    }
    if actor != "Human/Ops" or observed != expected:
        raise GitHubReviewBridgeError(
            f"operator acceptance exact-head mismatch: expected={expected!r} observed={observed!r}"
        )
    if intent_nonce is not None and str(value.get("intent_nonce") or "") != intent_nonce:
        raise GitHubReviewBridgeError("operator acceptance intent nonce mismatch")
    expected_ref = f"refs/tags/{operator_acceptance_proof_tag_name(head_sha=accepted.head_sha)}"
    if str(value.get("operator_acceptance_proof_ref") or "").strip() != expected_ref:
        raise GitHubReviewBridgeError("operator acceptance proof ref mismatch")
    frozen_base_sha = str(value.get("frozen_base_sha") or "").strip().lower()
    current_base_sha = str(value.get("current_base_sha") or "").strip().lower()
    if frozen_base_sha or current_base_sha:
        expected_frozen_base_sha = str(
            binding.get("base_sha") if isinstance(binding, Mapping) else ""
        ).strip().lower()
        if (
            not OID_RE.fullmatch(frozen_base_sha)
            or not OID_RE.fullmatch(current_base_sha)
            or (
                expected_frozen_base_sha
                and frozen_base_sha != expected_frozen_base_sha
            )
        ):
            raise GitHubReviewBridgeError("operator acceptance base evidence mismatch")
    return dict(value)


def bridge_operator_acceptance(
    *,
    repository: str,
    task_id: str,
    actor: str,
    message: str,
    binding: Mapping[str, Any] | ReviewBinding,
    intent_nonce: str = "",
    current_admission: ReviewAdmissionBinding | None = None,
    runner: JsonRunner | None = None,
) -> dict[str, Any]:
    """Publish an operator acceptance proof without creating a PR review.

    The caller has already revalidated the frozen review-admission binding.
    We still verify the PR's current exact identity before the GitHub write,
    then publish a distinct tag and re-dispatch the gate from the base branch.
    """

    repository = _require_repository_slug(repository)
    task_id = str(task_id or "").strip()
    if not task_id:
        raise GitHubReviewBridgeError("operator acceptance requires a task id")
    if actor != "Human/Ops":
        raise GitHubReviewBridgeError("operator acceptance requires Human/Ops")
    normalized = binding if isinstance(binding, ReviewBinding) else ReviewBinding.from_mapping(binding)
    nonce = str(intent_nonce or "").strip().lower()
    if nonce and not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise GitHubReviewBridgeError("operator acceptance intent nonce must be 32 lowercase hex")
    client = runner or GhJsonRunner()
    snapshot = _pr_snapshot(
        client,
        repository=repository,
        binding=normalized,
        allowed_states=frozenset({"OPEN"}),
    )
    proof = _push_review_proof_tag(
        client,
        repository=repository,
        binding=normalized,
        task_id=task_id,
        actor=actor,
        decision=OPERATOR_ACCEPT,
        message=message,
    )
    _dispatch_canonical_review_gate_workflow(
        client,
        repository=repository,
        binding=normalized,
        required=True,
    )
    result = {
        "repository": repository,
        "pr": normalized.pr,
        "head_sha": normalized.head_sha,
        "head_branch": normalized.head_branch,
        "base": normalized.base,
        "decision": OPERATOR_ACCEPT,
        "actor": actor,
        "mode": "operator_exact_head",
        "operator_acceptance_proof_ref": str(proof.get("ref") or "") or None,
        "pr_url": str(snapshot.get("url") or "") or None,
        "recorded_at": _utc_now(),
        "intent_nonce": nonce,
    }
    if current_admission is not None:
        frozen_base_sha = (
            str(binding.get("base_sha") or "").strip().lower()
            if isinstance(binding, Mapping)
            else ""
        )
        if not OID_RE.fullmatch(frozen_base_sha):
            raise GitHubReviewBridgeError(
                "operator acceptance requires frozen base evidence"
            )
        result.update(
            {
                "frozen_base_sha": frozen_base_sha,
                "current_base_sha": current_admission.base_sha,
            }
        )
    result = {key: value for key, value in result.items() if value not in (None, "")}
    validate_operator_acceptance_evidence(
        result,
        repository=repository,
        actor=actor,
        binding=binding,
        intent_nonce=nonce if nonce else None,
    )
    return result


def bridge_review_decision(
    *,
    repository: str,
    task_id: str,
    actor: str,
    decision: str,
    message: str,
    binding: Mapping[str, Any] | ReviewBinding,
    intent_nonce: str = "",
    runner: JsonRunner | None = None,
) -> BridgeResult:
    """Record one governed decision on the exact GitHub PR head."""

    repository = _require_repository_slug(repository)
    task_id = str(task_id or "").strip()
    actor = str(actor or "").strip()
    decision = str(decision or "").strip().lower()
    if not task_id:
        raise GitHubReviewBridgeError("GitHub review bridge requires a task id")
    if not actor:
        raise GitHubReviewBridgeError("GitHub review bridge requires an actor")
    if decision not in DECISIONS:
        raise GitHubReviewBridgeError(
            f"GitHub review bridge decision must be one of {sorted(DECISIONS)}"
        )
    intent_nonce = str(intent_nonce or "").strip().lower()
    if intent_nonce and not re.fullmatch(r"[0-9a-f]{32}", intent_nonce):
        raise GitHubReviewBridgeError("review intent nonce must be 32 lowercase hex")
    normalized_binding = (
        binding
        if isinstance(binding, ReviewBinding)
        else ReviewBinding.from_mapping(binding)
    )
    runner = runner or GhJsonRunner()
    pr = _pr_snapshot(
        runner,
        repository=repository,
        binding=normalized_binding,
        allowed_states=frozenset({"OPEN"}),
    )
    pr_url = str(pr.get("url") or "").strip()
    marker = _review_marker(
        task_id=task_id,
        actor=actor,
        decision=decision,
        head_sha=normalized_binding.head_sha,
        intent_nonce=intent_nonce,
    )
    body = _review_body(
        task_id=task_id,
        actor=actor,
        decision=decision,
        head_sha=normalized_binding.head_sha,
        message=message,
        intent_nonce=intent_nonce,
    )

    review: dict[str, Any] | None = None
    review_error = ""
    try:
        review = _submit_review(
            runner,
            repository=repository,
            binding=normalized_binding,
            body=body,
            marker=marker,
            decision=decision,
        )
    except GitHubReviewBridgeError as exc:
        review_error = str(exc)[:600]

    required_contexts: set[str] = set()
    context_error = ""
    try:
        required_contexts = _required_status_contexts(
            runner,
            repository=repository,
            base=normalized_binding.base,
        )
    except GitHubReviewBridgeError as exc:
        context_error = str(exc)[:600]

    status: dict[str, Any] | None = None
    context_required = CANONICAL_REVIEW_CONTEXT in required_contexts
    if context_required:
        status = _submit_required_status(
            runner,
            repository=repository,
            binding=normalized_binding,
            task_id=task_id,
            actor=actor,
            decision=decision,
            target_url=(
                f"{pr_url}#pantheon-review-intent-{intent_nonce}"
                if intent_nonce
                else pr_url
            ),
        )

    # Deliberately unchanged from the pre-tag contract: this still requires
    # a GitHub review or the required commit status, exactly as before the
    # proof tag existed. Loosening this to accept the tag alone would touch
    # scripts/ai_status.py's GITHUB_REVIEW_MODES / evidence-matching
    # validation, a separately audited integrity surface -- not worth
    # widening for a case ("no required context configured" + "self-review
    # blocked") that stops applying once the tag-based check is back in
    # dev's required contexts.
    if review is None and status is None:
        details = [item for item in (review_error, context_error) if item]
        if CANONICAL_REVIEW_CONTEXT not in required_contexts:
            details.append(
                f"base branch {normalized_binding.base!r} does not require "
                f"{CANONICAL_REVIEW_CONTEXT!r}"
            )
        raise GitHubReviewBridgeError(
            "Governed task decision was not recorded as a GitHub review or "
            "a branch-policy-recognized status"
            + (f": {'; '.join(details)}" if details else "")
        )

    # Only push the git-native proof tag once at least one legacy path has
    # confirmed the decision is real -- a call that was going to raise above
    # should not leave a dangling "approved" tag behind on GitHub.
    proof_ref = _push_review_proof_tag(
        runner,
        repository=repository,
        binding=normalized_binding,
        task_id=task_id,
        actor=actor,
        decision=decision,
        message=message,
    )
    review_proof_ref = str(proof_ref.get("ref") or "") or None

    if decision == APPROVE:
        _dispatch_canonical_review_gate_workflow(
            runner,
            repository=repository,
            binding=normalized_binding,
        )

    if review is not None and status is not None:
        mode = "pull_request_review_and_required_status"
    elif review is not None:
        mode = "pull_request_review"
    else:
        mode = "required_commit_status"

    result = BridgeResult(
        repository=repository,
        pr=normalized_binding.pr,
        head_sha=normalized_binding.head_sha,
        head_branch=normalized_binding.head_branch,
        base=normalized_binding.base,
        decision=decision,
        actor=actor,
        mode=mode,
        github_review_id=int(review.get("id")) if review and review.get("id") else None,
        status_id=int(status.get("id")) if status and status.get("id") else None,
        status_context=CANONICAL_REVIEW_CONTEXT if status is not None else None,
        status_state=STATUS_STATES[decision] if status is not None else None,
        review_proof_ref=review_proof_ref,
        pr_url=pr_url,
        recorded_at=_utc_now(),
        intent_nonce=intent_nonce,
        review_error=review_error if review is None else "",
    )
    validate_result_evidence(
        result.as_dict(),
        repository=repository,
        actor=actor,
        decision=decision,
        binding=normalized_binding,
        intent_nonce=intent_nonce if intent_nonce else None,
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bridge a governed task decision to an exact GitHub PR head."
    )
    parser.add_argument("--repository", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--decision", choices=sorted(DECISIONS), required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--head-branch", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--intent-nonce", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = bridge_review_decision(
        repository=args.repository,
        task_id=args.task_id,
        actor=args.actor,
        decision=args.decision,
        message=args.message,
        binding={
            "pr": args.pr,
            "head_sha": args.head_sha,
            "head_branch": args.head_branch,
            "base": args.base,
        },
        intent_nonce=args.intent_nonce,
    )
    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
