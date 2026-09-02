#!/usr/bin/env python3
"""Verify that commit messages on a branch carry required Pantheon trailers.

Used by:
  - .githooks/commit-msg          (one commit, the staged message)
  - .github/workflows/branch-ci.yml (range of commits in a PR)

Reads required trailers from .orchestrator/config.json:
  branch_workflow.task_pr.require_commit_trailers   (preferred, post-2026-05-17)
  wave_workflow.wave_merge.require_commit_trailers  (legacy fallback)

Default required trailers: LLM-Agent, Task-ID, Reviewer.
(The legacy `Wave:` trailer was dropped in OPS-GIT-REDESIGN-001; it is
still tolerated when present but no longer required.)

CLI:
  check_commit_trailers.py --message-file <path>
  check_commit_trailers.py --range <base>..<head>
  check_commit_trailers.py --rev <sha>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / ".orchestrator" / "config.json"

DEFAULT_REQUIRED = ("LLM-Agent", "Task-ID", "Reviewer")
SUBJECT_PATTERN = re.compile(r"^[A-Z][A-Z0-9-]*[A-Z0-9]:\s+\S")

# Subjects that legitimately bypass the task-id prefix rule.
EXEMPT_SUBJECT_PREFIXES = (
    "Merge ",
    "Revert ",
    "wave-merge:",
    "wave-close:",
    "wave-open:",
    "promote:",
    "hotfix:",
    "publish:",
    "fixup!",
    "squash!",
    "Initial commit",
)


def load_settings() -> tuple[tuple[str, ...], bool]:
    if not CONFIG_FILE.exists():
        return DEFAULT_REQUIRED, True
    try:
        payload = json.loads(CONFIG_FILE.read_text())
    except json.JSONDecodeError:
        return DEFAULT_REQUIRED, True
    # Prefer the new branch_workflow location; fall back to the legacy
    # wave_workflow path so commits made during the migration window
    # do not blow up.
    bw = (payload.get("branch_workflow") or {}).get("task_pr") or {}
    legacy = (payload.get("wave_workflow") or {}).get("wave_merge") or {}
    trailers = bw.get("require_commit_trailers") or legacy.get("require_commit_trailers")
    if not isinstance(trailers, list) or not trailers:
        trailers = list(DEFAULT_REQUIRED)
    prefix_required = bw.get("subject_prefix_required", legacy.get("subject_prefix_required", True))
    return tuple(str(t) for t in trailers), bool(prefix_required)


def required_trailers_for_delivery(required: tuple[str, ...], delivery_class: str) -> tuple[str, ...]:
    if delivery_class == "tooling":
        return tuple(name for name in required if name != "Reviewer")
    return required


def parse_trailers(body: str) -> dict[str, str]:
    trailers: dict[str, str] = {}
    for line in body.splitlines():
        stripped = line.rstrip()
        m = re.match(r"^([A-Za-z][A-Za-z0-9-]*):\s+(.+)$", stripped)
        if m:
            trailers[m.group(1)] = m.group(2)
    return trailers


def is_exempt_subject(subject: str) -> bool:
    return any(subject.startswith(p) for p in EXEMPT_SUBJECT_PREFIXES)


def check_message(message: str, required: tuple[str, ...], prefix_required: bool) -> list[str]:
    lines = message.splitlines()
    subject = lines[0] if lines else ""
    body = "\n".join(lines[1:])
    problems: list[str] = []

    if not subject.strip():
        return ["empty commit subject"]

    if is_exempt_subject(subject):
        return []

    if prefix_required and not SUBJECT_PATTERN.match(subject):
        problems.append(
            f"subject must start with TASK-ID: '<TASK-ID>: <summary>'; got '{subject[:60]}'"
        )

    if len(subject) > 72:
        problems.append(f"subject exceeds 72 chars ({len(subject)})")

    trailers = parse_trailers(body)
    for name in required:
        if name not in trailers:
            problems.append(f"missing trailer: {name}")
        elif not trailers[name].strip():
            problems.append(f"empty trailer value: {name}")
    problems.extend(check_independent_review(trailers))
    return problems


def _normalize_actor(value: str) -> str:
    return " ".join(value.strip().casefold().split())


# Values that name the author rather than an independent second party.
_SELF_REVIEW_VALUES = frozenset(
    {"self", "self-review", "self review", "same", "same as author", "author", "n/a", "none"}
)


def check_independent_review(trailers: dict[str, str]) -> list[str]:
    """Reject a commit that reviews itself.

    A blocking or fail-closed change that nobody else looked at is how a gate
    ships asserting a field whose meaning it got wrong. That is not
    hypothetical: the deploy gate that auto-rolled-back four healthy releases
    (OPGAP-DEPLOY-PROVIDER-GATE-20260901) arrived as `LLM-Agent: Codex` with
    `Reviewer: Codex` on the same commit, so no second party ever asked whether
    the assertion meant what it claimed.
    """
    author = trailers.get("LLM-Agent", "")
    reviewer = trailers.get("Reviewer", "")
    if not author.strip() or not reviewer.strip():
        # Missing/empty trailers are already reported by the caller.
        return []

    normalized_reviewer = _normalize_actor(reviewer)
    if normalized_reviewer in _SELF_REVIEW_VALUES:
        return [
            f"Reviewer '{reviewer.strip()}' does not name an independent reviewer; "
            "a change must be reviewed by someone other than its author"
        ]
    if normalized_reviewer == _normalize_actor(author):
        return [
            f"self-review is not accepted: LLM-Agent and Reviewer are both "
            f"'{author.strip()}'; name a different agent or Human/Ops"
        ]
    return []


def collect_messages_from_range(rev_range: str) -> list[tuple[str, str]]:
    out = subprocess.run(
        ["git", "log", "--format=%H%x00%B%x1e", rev_range],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    ).stdout
    items: list[tuple[str, str]] = []
    for chunk in out.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        sha, _, body = chunk.partition("\x00")
        items.append((sha, body))
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--message-file", help="Path to a commit message file (commit-msg hook)")
    group.add_argument("--range", dest="rev_range", help="Git rev range (e.g. origin/dev..HEAD)")
    group.add_argument("--rev", help="Single commit sha to check")
    parser.add_argument(
        "--skip-merge",
        action="store_true",
        help="Ignore merge commits when checking a range",
    )
    parser.add_argument(
        "--delivery-class",
        choices=("product", "tooling"),
        default="product",
        help="Tooling delivery does not require the product-reviewer trailer.",
    )
    args = parser.parse_args()

    # Allow CI/cron jobs to bypass with explicit opt-out (used by automated merge bots).
    if os.environ.get("PANTHEON_TRAILER_CHECK_DISABLED") == "1":
        return 0

    required, prefix_required = load_settings()
    required = required_trailers_for_delivery(required, args.delivery_class)

    targets: list[tuple[str, str]]
    if args.message_file:
        text = Path(args.message_file).read_text()
        # Strip comment lines (git includes them in the commit message file).
        text = "\n".join(l for l in text.splitlines() if not l.startswith("#"))
        targets = [("<staged>", text)]
    elif args.rev:
        body = subprocess.run(
            ["git", "log", "-1", "--format=%B", args.rev],
            check=True,
            capture_output=True,
            text=True,
            cwd=ROOT,
        ).stdout
        targets = [(args.rev, body)]
    else:
        targets = collect_messages_from_range(args.rev_range)

    exit_code = 0
    for sha, msg in targets:
        if args.skip_merge:
            parents = subprocess.run(
                ["git", "rev-list", "--parents", "-n", "1", sha],
                check=True,
                capture_output=True,
                text=True,
                cwd=ROOT,
            ).stdout.split()
            if len(parents) > 2:  # merge commit
                continue
        problems = check_message(msg, required, prefix_required)
        if problems:
            exit_code = 1
            print(f"\n[trailers] {sha}:")
            for p in problems:
                print(f"  - {p}")
    if exit_code:
        print(
            "\nFix: amend the commit message to include the required trailers. "
            "See docs/conventions/GIT_WORKFLOW.md §7."
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
