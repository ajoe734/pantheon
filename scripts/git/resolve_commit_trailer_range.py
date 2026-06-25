#!/usr/bin/env python3
"""Resolve the commit range used by the Branch CI trailer gate.

Push events can carry a ``before`` SHA that is no longer present after a
force-push or rebase. In that case, use the branch's merge-base with the
target branch so CI still checks the commits that belong to this PR.
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
    return _git(["cat-file", "-e", f"{rev}^{{commit}}"] ).returncode == 0


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


def fallback_base_refs(event: str, ref_name: str, pr_base_ref: str) -> list[str]:
    if event == "pull_request" and pr_base_ref:
        return [f"origin/{pr_base_ref}", pr_base_ref]
    if ref_name in {"dev", "master"}:
        return []
    if ref_name.startswith(("publish/", "promote/")):
        return ["origin/master", "master", "origin/dev", "dev"]
    return ["origin/dev", "dev", "origin/master", "master"]


def resolve_commit_range(
    *,
    event: str,
    base_sha: str,
    head_sha: str,
    ref_name: str,
    pr_base_ref: str,
    commit_exists=git_commit_exists,
    is_ancestor=git_is_ancestor,
    merge_base=git_merge_base,
) -> str:
    head = head_sha or "HEAD"
    if not commit_exists(head):
        raise ValueError(f"head commit is not available: {head}")

    base = base_sha.strip()
    if base and base != ZERO_SHA and commit_exists(base) and is_ancestor(base, head):
        return f"{base}..{head}"

    for ref in fallback_base_refs(event, ref_name, pr_base_ref):
        candidate = merge_base(ref, head)
        if candidate and candidate != head:
            return f"{candidate}..{head}"

    parent = f"{head}^"
    if commit_exists(parent):
        return f"{parent}..{head}"
    return head


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--base-sha", default="")
    parser.add_argument("--head-sha", default="HEAD")
    parser.add_argument("--ref-name", default="")
    parser.add_argument("--pr-base-ref", default="")
    args = parser.parse_args()

    try:
        rev_range = resolve_commit_range(
            event=args.event,
            base_sha=args.base_sha,
            head_sha=args.head_sha,
            ref_name=args.ref_name,
            pr_base_ref=args.pr_base_ref,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"range={rev_range}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
