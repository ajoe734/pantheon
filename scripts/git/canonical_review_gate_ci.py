#!/usr/bin/env python3
"""GitHub Action entry point that guarantees the `Pantheon canonical review
gate` required status check is posted for every PR into `dev`/`master`.

SUP-REVIEW-PIPELINE-INTEGRITY-20260804: before this script existed, the
context was posted exclusively by `github_review_bridge.py`, itself invoked
only from `scripts/ai_status.py`'s `approve`/`done` command handlers. A PR
whose task never reached that internal transaction -- an unregistered
branch, a task that stalled before approval -- could never receive the
check, even though branch protection lists it as required for every PR.
This script closes that gap by running on every `pull_request` event and
always posting a state (`success` or `failure`), reusing the already-tested
`task_review_merge_gate.py` policy engine rather than re-deriving it.

CLI:
  canonical_review_gate_ci.py --repo <owner/repo> --pr-number <n> \
    --head-ref <branch> --head-sha <sha> [--target-url <url>] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "git"))

from github_review_bridge import CANONICAL_REVIEW_CONTEXT  # noqa: E402
from task_review_merge_gate import TaskReviewGateError, gate_for_task  # noqa: E402

DEFAULT_TASK_BRANCH_PREFIX = "task/"

# GitHub's commit-status `description` field is truncated server-side at 140
# characters; truncate ourselves so the stored payload and the API's stored
# value never disagree.
_DESCRIPTION_LIMIT = 140

PR_VIEW_FIELDS = (
    "headRefOid,headRefName,baseRefName,isDraft,number,state,mergedAt,"
    "commits,autoMergeRequest"
)


def resolve_task_id(head_ref: str, *, prefix: str = DEFAULT_TASK_BRANCH_PREFIX) -> str | None:
    head_ref = (head_ref or "").strip()
    if not head_ref.startswith(prefix):
        return None
    task_id = head_ref[len(prefix):].strip()
    return task_id or None


def build_status_payload(
    *,
    head_ref: str,
    pr: Mapping[str, Any] | None,
    task_branch_prefix: str = DEFAULT_TASK_BRANCH_PREFIX,
    target_url: str = "",
    status_root: Path | str | None = None,
) -> dict[str, Any]:
    """Pure decision function: no network calls, no filesystem writes other
    than the read-only status-root lookup `gate_for_task` already performs.

    Returns the exact GitHub commit-status payload to post. Always returns
    a payload -- the entire point of this module is that this function is
    never allowed to return "nothing to post".
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

    if pr is None:
        return {
            "state": "failure",
            "context": CANONICAL_REVIEW_CONTEXT,
            "description": f"no PR payload was supplied for {task_id}"[:_DESCRIPTION_LIMIT],
            "target_url": target_url,
        }

    try:
        decision = gate_for_task(task_id, pr, status_root=status_root)
    except TaskReviewGateError as exc:
        return {
            "state": "failure",
            "context": CANONICAL_REVIEW_CONTEXT,
            "description": f"{task_id}: gate evaluation failed: {exc}"[:_DESCRIPTION_LIMIT],
            "target_url": target_url,
        }

    state = "success" if decision.allow_merge else "failure"
    description = f"{task_id} policy={decision.policy} reason={decision.reason}"
    return {
        "state": state,
        "context": CANONICAL_REVIEW_CONTEXT,
        "description": description[:_DESCRIPTION_LIMIT],
        "target_url": target_url,
    }


def _run_gh_json(args: list[str]) -> Any:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def _post_status(*, repo: str, head_sha: str, payload: Mapping[str, Any]) -> None:
    subprocess.run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{repo}/statuses/{head_sha}",
            "--input",
            "-",
        ],
        input=json.dumps(dict(payload)),
        text=True,
        check=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--pr-number", required=True)
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--target-url", default="")
    parser.add_argument("--task-branch-prefix", default=DEFAULT_TASK_BRANCH_PREFIX)
    parser.add_argument("--status-root", type=Path, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the payload that would be posted; never call `gh api`",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    pr: Mapping[str, Any] | None = None
    task_id = resolve_task_id(args.head_ref, prefix=args.task_branch_prefix)
    if task_id is not None:
        try:
            pr = _run_gh_json(
                [
                    "pr",
                    "view",
                    args.pr_number,
                    "--repo",
                    args.repo,
                    "--json",
                    PR_VIEW_FIELDS,
                ]
            )
        except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            payload = {
                "state": "failure",
                "context": CANONICAL_REVIEW_CONTEXT,
                "description": (
                    f"{task_id}: could not read PR #{args.pr_number}: {exc}"
                )[:_DESCRIPTION_LIMIT],
                "target_url": args.target_url,
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
            if not args.dry_run:
                _post_status(repo=args.repo, head_sha=args.head_sha, payload=payload)
            # A PR read failure is an infrastructure fault, not a gate
            # verdict; surface it as a workflow failure distinct from a
            # normal "blocked" gate result so it gets noticed and retried.
            return 2

    payload = build_status_payload(
        head_ref=args.head_ref,
        pr=pr,
        task_branch_prefix=args.task_branch_prefix,
        target_url=args.target_url,
        status_root=args.status_root,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    if not args.dry_run:
        _post_status(repo=args.repo, head_sha=args.head_sha, payload=payload)
    return 0 if payload["state"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
