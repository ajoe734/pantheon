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
from urllib.parse import quote


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


class GitHubReviewBridgeError(RuntimeError):
    """The governed decision could not be represented on GitHub."""


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
        }
        if self.review_error:
            payload["review_error"] = self.review_error
        return {key: value for key, value in payload.items() if value not in (None, "")}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_repository_slug(value: str) -> str:
    slug = str(value or "").strip()
    if not REPO_RE.fullmatch(slug):
        raise GitHubReviewBridgeError(
            f"GitHub repository must use owner/name form, got {slug!r}"
        )
    return slug


def _review_marker(
    *,
    task_id: str,
    actor: str,
    decision: str,
    head_sha: str,
) -> str:
    return (
        "<!-- pantheon-review-bridge "
        f"task={task_id} actor={actor} decision={decision} head={head_sha} -->"
    )


def _review_body(
    *,
    task_id: str,
    actor: str,
    decision: str,
    head_sha: str,
    message: str,
) -> str:
    verdict = "approved" if decision == APPROVE else "requested changes"
    return (
        f"Pantheon governed reviewer `{actor}` {verdict} for task `{task_id}` "
        f"at exact head `{head_sha}`.\n\n{message.strip()}\n\n"
        f"{_review_marker(task_id=task_id, actor=actor, decision=decision, head_sha=head_sha)}"
    ).strip()


def _pr_snapshot(
    runner: JsonRunner,
    *,
    repository: str,
    binding: ReviewBinding,
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
            "number,url,state,headRefName,headRefOid,baseRefName",
        ]
    )
    if not isinstance(payload, Mapping):
        raise GitHubReviewBridgeError(f"GitHub PR #{binding.pr} metadata is unavailable")
    if int(payload.get("number") or 0) != binding.pr:
        raise GitHubReviewBridgeError(f"GitHub returned the wrong PR for #{binding.pr}")
    if str(payload.get("state") or "").upper() != "OPEN":
        raise GitHubReviewBridgeError(f"GitHub PR #{binding.pr} is not open")
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
        raise GitHubReviewBridgeError(
            f"GitHub PR #{binding.pr} no longer matches reviewed identity: "
            + "; ".join(mismatches)
        )
    return dict(payload)


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
        return dict(existing)

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
    return dict(created_ref)


CANONICAL_REVIEW_GATE_WORKFLOW_FILE = "canonical-review-gate.yml"


def _dispatch_canonical_review_gate_workflow(
    runner: JsonRunner,
    *,
    repository: str,
    binding: ReviewBinding,
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

    Failure to dispatch is not fatal: the tag is the durable proof, and the
    next natural push to the PR (or a manual re-run) picks it up either way.
    """

    try:
        runner.run_json(
            [
                "gh",
                "api",
                "--method",
                "POST",
                f"repos/{repository}/actions/workflows/"
                f"{CANONICAL_REVIEW_GATE_WORKFLOW_FILE}/dispatches",
                "--input",
                "-",
            ],
            payload={
                "ref": binding.base,
                "inputs": {
                    "head_ref": binding.head_branch,
                    "head_sha": binding.head_sha,
                },
            },
        )
    except GitHubReviewBridgeError:
        pass


def bridge_review_decision(
    *,
    repository: str,
    task_id: str,
    actor: str,
    decision: str,
    message: str,
    binding: Mapping[str, Any] | ReviewBinding,
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
    )
    pr_url = str(pr.get("url") or "").strip()
    marker = _review_marker(
        task_id=task_id,
        actor=actor,
        decision=decision,
        head_sha=normalized_binding.head_sha,
    )
    body = _review_body(
        task_id=task_id,
        actor=actor,
        decision=decision,
        head_sha=normalized_binding.head_sha,
        message=message,
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
            target_url=pr_url,
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

    return BridgeResult(
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
        review_error=review_error if review is None else "",
    )


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
    )
    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
