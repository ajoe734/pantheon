#!/usr/bin/env python3
"""Resolve the commit range used by the Branch CI trailer gate.

The ``push`` and ``pull_request`` shapes are deliberately different.

``pull_request``
    ``github.sha`` is GitHub's *synthetic merge commit*: the PR head merged
    into the **current** base tip. Anchoring the range on it drags every base
    commit that landed after ``pull_request.base.sha`` into the scan, so a
    task PR is failed by somebody else's already-merged ``dev`` commit. Run
    30219467575 scanned ``03389c0..0942107`` for PR #4211 and rejected dev
    commit ``0410a89f``, which is not reachable from that PR's head at all;
    PR #4215 failed the same way. The PR range is therefore always
    ``<base branch>..<pull_request.head.sha>``. Two-dot exclusion against the
    base branch keeps integration-base history and the synthetic merge commit
    out of the scan no matter how far the base moves afterwards.

``push`` on ``task/**`` / ``hotfix/**``
    ``github.event.before`` is the previous branch tip, so any commit the
    worker pulled in when syncing the branch with its integration target is
    inside ``before..github.sha`` even though the branch does not own it. Run
    30219364096 rejected the same dev commit ``0410a89f`` on
    ``task/SUP-WORKER-TRUTH-RECONCILE-001`` for exactly that reason. Work
    branches are therefore measured against their integration target, which
    also survives force-push, rebase and a brand-new branch without special
    cases.

``push`` on ``dev`` / ``master`` / ``publish/**`` / ``promote/**``
    These are the integration branches themselves; keep measuring them from
    ``before``, falling back to the merge base with their own target when
    ``before`` is missing, all-zero, or was rewritten.

Both shapes fail closed. An unusable head or an unresolvable base is an
error; it never degrades into a silently narrower or wider range.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ZERO_SHA = "0" * 40


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def git_commit_exists(rev: str) -> bool:
    if not rev:
        return False
    return _git(["cat-file", "-e", f"{rev}^{{commit}}"]).returncode == 0


def git_is_ancestor(base: str, head: str) -> bool:
    return _git(["merge-base", "--is-ancestor", base, head]).returncode == 0


def git_merge_base(ref: str, head: str) -> str | None:
    if not git_commit_exists(ref):
        return None
    proc = _git(["merge-base", ref, head])
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None


def is_work_branch(ref_name: str) -> bool:
    """True for branches that carry task-owned commits and nothing else."""

    return ref_name.startswith(("task/", "hotfix/"))


def push_fallback_base_refs(ref_name: str) -> list[str]:
    """Integration targets a pushed branch may be measured against."""

    if ref_name in {"dev", "master"}:
        return []
    if ref_name.startswith(("publish/", "promote/")):
        return ["origin/master", "master", "origin/dev", "dev"]
    return ["origin/dev", "dev", "origin/master", "master"]


def pull_request_base_refs(pr_base_ref: str, pr_base_sha: str) -> list[str]:
    """PR base candidates, tightest first.

    The live base tip wins over the event's ``base.sha`` because ``base.sha``
    is frozen when the PR is opened: if the task branch later merged the base
    branch in, only the live tip excludes the base commits that arrived with
    that merge.
    """

    refs: list[str] = []
    if pr_base_ref:
        refs.append(f"origin/{pr_base_ref}")
        refs.append(pr_base_ref)
    sha = (pr_base_sha or "").strip()
    if sha and sha != ZERO_SHA:
        refs.append(sha)
    return refs


def resolve_pull_request_range(
    *,
    pr_head_sha: str,
    pr_base_ref: str,
    pr_base_sha: str,
    commit_exists=git_commit_exists,
) -> str:
    """Commits unique to the PR head, measured against the base branch."""

    head = (pr_head_sha or "").strip()
    if not head:
        raise ValueError(
            "pull_request needs --pr-head-sha (github.event.pull_request.head.sha); "
            "refusing to scan from the synthetic merge commit"
        )
    if not commit_exists(head):
        raise ValueError(f"pull_request head commit is not available: {head}")

    candidates = pull_request_base_refs(pr_base_ref, pr_base_sha)
    for ref in candidates:
        if commit_exists(ref):
            return f"{ref}..{head}"

    tried = ", ".join(candidates) if candidates else "<none>"
    raise ValueError(f"pull_request base is not available; tried: {tried}")


def resolve_push_range(
    *,
    base_sha: str,
    head_sha: str,
    ref_name: str,
    commit_exists=git_commit_exists,
    is_ancestor=git_is_ancestor,
    merge_base=git_merge_base,
) -> str:
    head = head_sha or "HEAD"
    if not commit_exists(head):
        raise ValueError(f"head commit is not available: {head}")

    # A work branch owns exactly the commits its integration target does not
    # already have. Measuring from `before` instead re-scans whatever the
    # worker merged in when it synced the branch with dev.
    if is_work_branch(ref_name):
        for ref in push_fallback_base_refs(ref_name):
            if commit_exists(ref):
                return f"{ref}..{head}"

    base = (base_sha or "").strip()
    if base and base != ZERO_SHA and commit_exists(base) and is_ancestor(base, head):
        return f"{base}..{head}"

    for ref in push_fallback_base_refs(ref_name):
        candidate = merge_base(ref, head)
        if candidate and candidate != head:
            return f"{candidate}..{head}"

    parent = f"{head}^"
    if commit_exists(parent):
        return f"{parent}..{head}"
    return head


def resolve_commit_range(
    *,
    event: str,
    base_sha: str,
    head_sha: str,
    ref_name: str,
    pr_base_ref: str,
    pr_head_sha: str = "",
    commit_exists=git_commit_exists,
    is_ancestor=git_is_ancestor,
    merge_base=git_merge_base,
) -> str:
    if event == "pull_request":
        return resolve_pull_request_range(
            pr_head_sha=pr_head_sha,
            pr_base_ref=pr_base_ref,
            pr_base_sha=base_sha,
            commit_exists=commit_exists,
        )
    return resolve_push_range(
        base_sha=base_sha,
        head_sha=head_sha,
        ref_name=ref_name,
        commit_exists=commit_exists,
        is_ancestor=is_ancestor,
        merge_base=merge_base,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--base-sha", default="")
    parser.add_argument("--head-sha", default="HEAD")
    parser.add_argument("--ref-name", default="")
    parser.add_argument("--pr-base-ref", default="")
    parser.add_argument(
        "--pr-head-sha",
        default="",
        help="github.event.pull_request.head.sha; required for pull_request events",
    )
    args = parser.parse_args()

    try:
        rev_range = resolve_commit_range(
            event=args.event,
            base_sha=args.base_sha,
            head_sha=args.head_sha,
            ref_name=args.ref_name,
            pr_base_ref=args.pr_base_ref,
            pr_head_sha=args.pr_head_sha,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"[range] event={args.event} resolved={rev_range}", file=sys.stderr)
    print(f"range={rev_range}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
