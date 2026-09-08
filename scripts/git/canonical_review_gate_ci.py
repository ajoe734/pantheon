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
purely over the GitHub API with no local checkout required at all: does an
independent-review tag or a separately named Human/Ops acceptance tag exist
for this exact head? Each tag is produced only by its matching trusted,
exact-head path. CI does not re-derive that decision; it only confirms the
artifact it produced is present for this exact head.

Product PRs require the exact-head review-proof tag.  Development-tooling PRs
labelled `delivery:tooling` use the explicit Human/Ops delivery decision
instead: they do not pretend to be product tasks or manufacture a proof tag.

CLI:
  canonical_review_gate_ci.py --repo <owner/repo> --head-ref <branch> \
    --head-sha <sha> [--delivery-class <product|tooling>] [--target-url <url>] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "git"))

from github_review_bridge import (  # noqa: E402
    CANONICAL_REVIEW_CONTEXT,
    REOPEN,
    operator_acceptance_proof_tag_name,
    review_proof_tag_name,
)

DEFAULT_TASK_BRANCH_PREFIX = "task/"
APPROVE_DECISION = "approve"
REOPEN_DECISION = REOPEN
PRODUCT_DELIVERY_CLASS = "product"
TOOLING_DELIVERY_CLASS = "tooling"
_DELIVERY_CLASSES = frozenset({PRODUCT_DELIVERY_CLASS, TOOLING_DELIVERY_CLASS})
MAX_TAG_PEEL_DEPTH = 5
OID_RE = re.compile(r"^[0-9a-fA-F]{40}$")

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


def default_tag_lookup(repository: str, ref_or_sha: str) -> Mapping[str, Any] | None:
    prefix = "refs/tags/"
    if ref_or_sha.startswith(prefix):
        tag_name = ref_or_sha[len(prefix):]
        encoded_tag_name = quote(tag_name, safe="")
        result = _run_gh_json(["api", f"repos/{repository}/git/refs/tags/{encoded_tag_name}"])
        return result if isinstance(result, Mapping) else None
    result = _run_gh_json(["api", f"repos/{repository}/git/tags/{ref_or_sha}"])
    return result if isinstance(result, Mapping) else None


def resolve_proof_tag_target(
    *,
    repository: str,
    ref: str,
    lookup: TagLookup = default_tag_lookup,
    max_peel_depth: int = MAX_TAG_PEEL_DEPTH,
) -> str | None:
    """Resolve the exact commit object targeted by a git ref, peeling tags as needed.

    Returns the 40-hex lowercase commit SHA if the ref resolves to a commit
    object within bounded peel depth, or None if the ref is missing, malformed,
    points to a non-commit object, or lookup fails.
    """
    found = lookup(repository, ref)
    if not isinstance(found, Mapping):
        return None
    if str(found.get("ref") or "").strip() != ref:
        return None
    obj = found.get("object")
    if not isinstance(obj, Mapping):
        return None
    obj_type = str(obj.get("type") or "").strip().lower()
    obj_sha = str(obj.get("sha") or "").strip().lower()
    if not OID_RE.fullmatch(obj_sha):
        return None
    if obj_type == "commit":
        return obj_sha
    if obj_type != "tag":
        return None

    current_sha = obj_sha
    for _ in range(max(1, max_peel_depth)):
        tag_obj = lookup(repository, current_sha)
        if not isinstance(tag_obj, Mapping):
            return None
        target = tag_obj.get("object")
        if not isinstance(target, Mapping):
            return None
        target_type = str(target.get("type") or "").strip().lower()
        target_sha = str(target.get("sha") or "").strip().lower()
        if not OID_RE.fullmatch(target_sha):
            return None
        if target_type == "commit":
            return target_sha
        if target_type == "tag":
            current_sha = target_sha
            continue
        return None
    return None


def review_proof_tag_exists(
    *, repository: str, head_sha: str, lookup: TagLookup = default_tag_lookup
) -> bool:
    normalized_head = str(head_sha or "").strip().lower()
    if not OID_RE.fullmatch(normalized_head):
        return False
    ref = f"refs/tags/{review_proof_tag_name(decision=APPROVE_DECISION, head_sha=normalized_head)}"
    resolved = resolve_proof_tag_target(repository=repository, ref=ref, lookup=lookup)
    return resolved == normalized_head


def operator_acceptance_proof_tag_exists(
    *, repository: str, head_sha: str, lookup: TagLookup = default_tag_lookup
) -> bool:
    normalized_head = str(head_sha or "").strip().lower()
    if not OID_RE.fullmatch(normalized_head):
        return False
    ref = f"refs/tags/{operator_acceptance_proof_tag_name(head_sha=normalized_head)}"
    resolved = resolve_proof_tag_target(repository=repository, ref=ref, lookup=lookup)
    return resolved == normalized_head


def reopen_proof_tag_exists(
    *, repository: str, head_sha: str, lookup: TagLookup = default_tag_lookup
) -> bool:
    normalized_head = str(head_sha or "").strip().lower()
    if not OID_RE.fullmatch(normalized_head):
        return False
    ref = f"refs/tags/{review_proof_tag_name(decision=REOPEN_DECISION, head_sha=normalized_head)}"
    resolved = resolve_proof_tag_target(repository=repository, ref=ref, lookup=lookup)
    return resolved == normalized_head


def build_status_payload(
    *,
    head_ref: str,
    repository: str,
    head_sha: str,
    delivery_class: str = PRODUCT_DELIVERY_CLASS,
    task_branch_prefix: str = DEFAULT_TASK_BRANCH_PREFIX,
    target_url: str = "",
    lookup: TagLookup | None = None,
) -> dict[str, Any]:
    """Pure-ish decision function: product delivery makes one tag lookup;
    tooling delivery is explicitly classified by the GitHub workflow label.
    lookup, injectable via `lookup` for tests. Always returns a payload --
    the entire point of this module is that this function is never allowed
    to return "nothing to post".
    """
    if delivery_class not in _DELIVERY_CLASSES:
        return {
            "state": "failure",
            "context": CANONICAL_REVIEW_CONTEXT,
            "description": f"unknown delivery class {delivery_class!r}"[:_DESCRIPTION_LIMIT],
            "target_url": target_url,
        }

    if delivery_class == TOOLING_DELIVERY_CLASS:
        return {
            "state": "success",
            "context": CANONICAL_REVIEW_CONTEXT,
            "description": "development tooling: Human/Ops direct delivery path"[:_DESCRIPTION_LIMIT],
            "target_url": target_url,
        }

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
    has_reopen = reopen_proof_tag_exists(
        repository=repository, head_sha=head_sha, lookup=active_lookup
    )
    has_review = review_proof_tag_exists(
        repository=repository, head_sha=head_sha, lookup=active_lookup
    )
    has_operator = operator_acceptance_proof_tag_exists(
        repository=repository, head_sha=head_sha, lookup=active_lookup
    )

    if has_reopen:
        if has_review or has_operator:
            return {
                "state": "failure",
                "context": CANONICAL_REVIEW_CONTEXT,
                "description": (
                    f"{task_id}: conflicting review-proof tags at {head_sha[:12]} -- "
                    "reopen tag invalidates approval"
                )[:_DESCRIPTION_LIMIT],
                "target_url": target_url,
            }
        return {
            "state": "failure",
            "context": CANONICAL_REVIEW_CONTEXT,
            "description": (
                f"{task_id}: review changes requested / reopened for head {head_sha[:12]} -- not approved"
            )[:_DESCRIPTION_LIMIT],
            "target_url": target_url,
        }

    if has_review:
        return {
            "state": "success",
            "context": CANONICAL_REVIEW_CONTEXT,
            "description": f"{task_id}: review-proof tag present at {head_sha[:12]}"[
                :_DESCRIPTION_LIMIT
            ],
            "target_url": target_url,
        }

    if has_operator:
        return {
            "state": "success",
            "context": CANONICAL_REVIEW_CONTEXT,
            "description": f"{task_id}: Human/Ops exact-head acceptance present at {head_sha[:12]}"[
                :_DESCRIPTION_LIMIT
            ],
            "target_url": target_url,
        }

    return {
        "state": "failure",
        "context": CANONICAL_REVIEW_CONTEXT,
        "description": (
            f"{task_id}: no review-proof tag "
            f"({review_proof_tag_name(decision=APPROVE_DECISION, head_sha=head_sha)} or "
            f"{operator_acceptance_proof_tag_name(head_sha=head_sha)}) "
            f"for head {head_sha[:12]} -- not yet independently approved at this head"
        )[:_DESCRIPTION_LIMIT],
        "target_url": target_url,
    }


class StatusPostError(RuntimeError):
    """The `gh api` status POST never succeeded, even after retries.

    This must never be conflated with the script's normal `exit(1)`, which
    means the gate correctly posted a *failing* status (head not yet
    approved) -- that is a successful run of this script. This exception
    means no status was posted at all: the required check is left unset for
    this head, silently blocking merge until someone re-dispatches and gets
    lucky. `main()` maps this to a distinct exit code so the caller (the
    workflow's bash step) can tell the two apart instead of the earlier
    behaviour, where an uncaught exception from a failed POST also exited 1
    and was indistinguishable from the designed "not yet approved" outcome.
    """


def _post_status(
    *,
    repository: str,
    head_sha: str,
    payload: Mapping[str, Any],
    attempts: int = 4,
    backoff_seconds: float = 2.0,
) -> None:
    body = json.dumps(dict(payload))
    args = ["gh", "api", "--method", "POST", f"repos/{repository}/statuses/{head_sha}", "--input", "-"]
    last_output = ""
    for attempt in range(1, attempts + 1):
        proc = subprocess.run(args, input=body, text=True, capture_output=True)
        if proc.returncode == 0:
            return
        last_output = (proc.stderr or proc.stdout or "").strip()
        if attempt < attempts:
            print(
                f"gh api status POST failed (attempt {attempt}/{attempts}): "
                f"{last_output[:300]} -- retrying in {backoff_seconds:.0f}s",
                file=sys.stderr,
            )
            time.sleep(backoff_seconds)
            backoff_seconds *= 2
    raise StatusPostError(
        f"could not POST the {payload.get('context')!r} status for {head_sha} "
        f"after {attempts} attempts: {last_output[:300]}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument(
        "--delivery-class",
        choices=sorted(_DELIVERY_CLASSES),
        default=PRODUCT_DELIVERY_CLASS,
        help="product requires a review-proof tag; tooling is Human/Ops direct delivery",
    )
    parser.add_argument("--target-url", default="")
    parser.add_argument("--task-branch-prefix", default=DEFAULT_TASK_BRANCH_PREFIX)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the payload that would be posted; never call `gh api`",
    )
    return parser


EXIT_STATUS_POST_FAILED = 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_status_payload(
        head_ref=args.head_ref,
        repository=args.repo,
        head_sha=args.head_sha,
        delivery_class=args.delivery_class,
        task_branch_prefix=args.task_branch_prefix,
        target_url=args.target_url,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    if not args.dry_run:
        try:
            _post_status(repository=args.repo, head_sha=args.head_sha, payload=payload)
        except StatusPostError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_STATUS_POST_FAILED
    return 0 if payload["state"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
