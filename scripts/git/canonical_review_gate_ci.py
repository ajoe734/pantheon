#!/usr/bin/env python3
"""GitHub Action entry point that guarantees the `Pantheon canonical review
gate` required status check is posted for every PR into `dev`/`master`.

SUP-REVIEW-PIPELINE-INTEGRITY-20260804 first version of this script tried to
re-derive the review policy locally, by reading `ai-status.json` out of the
Action's own checkout. That is structurally wrong: the checkout is a fresh
clone on a GitHub-hosted runner, so it only ever sees whatever snapshot of
`ai-status.json` last happened to be committed -- never the live task board,
which lives entirely on the Pantheon host (an external, git-independent event
log). That version therefore reported `task_state_unavailable` for every
task, registered or not, and had to be pulled from branch protection the same
day it shipped.

SUP-REVIEW-GATE-GIT-NATIVE-PROOF-20260804 replaces the whole approach: rather
than trying to see live state from CI, the governed `approve` step
(`scripts/git/github_review_bridge.py::_push_review_proof_tag`) pushes a git
tag at the exact reviewed head SHA when it runs -- durably, on the host that
actually has the state, at decision time. A tag is part of the repository's
own object graph, so *any* clone or `gh api` call sees it, including this
runner. The check below therefore only ever asks one question, answerable
purely over the GitHub API with no local checkout required at all: does
`refs/tags/pantheon-review/approve/<head-sha>` exist? Existence is
sufficient proof, because the tag is only ever created by the trusted,
already-integrity-checked internal approve path (owner != reviewer, exact
head binding, etc.) -- CI does not need to re-derive any of that, only
confirm the artifact it produced is present for this exact head.

CLI:
  canonical_review_gate_ci.py --repo <owner/repo> --head-ref <branch> \
    --head-sha <sha> [--target-url <url>] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "git"))

from github_review_bridge import CANONICAL_REVIEW_CONTEXT, review_proof_tag_name  # noqa: E402

DEFAULT_TASK_BRANCH_PREFIX = "task/"
APPROVE_DECISION = "approve"

# GitHub's commit-status `description` field is truncated server-side at 140
# characters; truncate ourselves so the stored payload and the API's stored
# value never disagree.
_DESCRIPTION_LIMIT = 140

TagLookup = Callable[[str, str], Mapping[str, Any] | None]


def resolve_task_id(head_ref: str, *, prefix: str = DEFAULT_TASK_BRANCH_PREFIX) -> str | None:
    head_ref = (head_ref or "").strip()
    if not head_ref.startswith(prefix):
        return None
    task_id = head_ref[len(prefix):].strip()
    return task_id or None


def _run_gh_json(args: list[str]) -> Any:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return None
    text = (proc.stdout or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def default_tag_lookup(repository: str, ref: str) -> Mapping[str, Any] | None:
    # `ref` is a full ref path ("refs/tags/pantheon-review/approve/<sha>").
    # GitHub's git-refs lookup route wants `git/refs/tags/<name>` with
    # `refs/tags/` literal and only the tag's own internal slashes encoded --
    # encoding the whole ref (as the first version of this script did) 404s
    # even for a tag that exists; verified against the live API.
    prefix = "refs/tags/"
    assert ref.startswith(prefix), f"expected a refs/tags/ ref, got {ref!r}"
    tag_name = ref[len(prefix):]
    encoded_tag_name = quote(tag_name, safe="")
    result = _run_gh_json(["api", f"repos/{repository}/git/refs/tags/{encoded_tag_name}"])
    return result if isinstance(result, Mapping) else None


def review_proof_tag_exists(
    *, repository: str, head_sha: str, lookup: TagLookup = default_tag_lookup
) -> bool:
    ref = f"refs/tags/{review_proof_tag_name(decision=APPROVE_DECISION, head_sha=head_sha)}"
    found = lookup(repository, ref)
    return isinstance(found, Mapping) and found.get("ref") == ref


def build_status_payload(
    *,
    head_ref: str,
    repository: str,
    head_sha: str,
    task_branch_prefix: str = DEFAULT_TASK_BRANCH_PREFIX,
    target_url: str = "",
    lookup: TagLookup | None = None,
) -> dict[str, Any]:
    """Pure-ish decision function: the only network call is the single tag
    lookup, injectable via `lookup` for tests. Always returns a payload --
    the entire point of this module is that this function is never allowed
    to return "nothing to post".
    """
    task_id = resolve_task_id(head_ref, prefix=task_branch_prefix)
    if task_id is None:
        return {
            "state": "failure",
            "context": CANONICAL_REVIEW_CONTEXT,
            "description": (
                f"head branch {head_ref!r} does not match the "
                f"{task_branch_prefix!r} task-branch convention; the canonical "
                "review gate cannot evaluate an unregistered task branch"
            )[:_DESCRIPTION_LIMIT],
            "target_url": target_url,
        }

    # `lookup` defaults late (resolved here, not bound at def-time) so that
    # patching the module-level `default_tag_lookup` -- e.g. in tests --
    # is actually observed by callers, like main(), that don't pass one.
    active_lookup = lookup if lookup is not None else default_tag_lookup
    if review_proof_tag_exists(repository=repository, head_sha=head_sha, lookup=active_lookup):
        return {
            "state": "success",
            "context": CANONICAL_REVIEW_CONTEXT,
            "description": f"{task_id}: review-proof tag present at {head_sha[:12]}"[
                :_DESCRIPTION_LIMIT
            ],
            "target_url": target_url,
        }

    return {
        "state": "failure",
        "context": CANONICAL_REVIEW_CONTEXT,
        "description": (
            f"{task_id}: no review-proof tag "
            f"({review_proof_tag_name(decision=APPROVE_DECISION, head_sha=head_sha)}) "
            f"for head {head_sha[:12]} -- not yet independently approved at this head"
        )[:_DESCRIPTION_LIMIT],
        "target_url": target_url,
    }


def _post_status(*, repository: str, head_sha: str, payload: Mapping[str, Any]) -> None:
    subprocess.run(
        ["gh", "api", "--method", "POST", f"repos/{repository}/statuses/{head_sha}", "--input", "-"],
        input=json.dumps(dict(payload)),
        text=True,
        check=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--target-url", default="")
    parser.add_argument("--task-branch-prefix", default=DEFAULT_TASK_BRANCH_PREFIX)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the payload that would be posted; never call `gh api`",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_status_payload(
        head_ref=args.head_ref,
        repository=args.repo,
        head_sha=args.head_sha,
        task_branch_prefix=args.task_branch_prefix,
        target_url=args.target_url,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    if not args.dry_run:
        _post_status(repository=args.repo, head_sha=args.head_sha, payload=payload)
    return 0 if payload["state"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
