#!/usr/bin/env python3
"""Resolve the PPL-ALLOC-009 acceptance harness to the accepted frontend SHA."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def resolve_harness_sha(
    *,
    expected_frontend_sha: str,
    requested_test_sha: str = "",
) -> str:
    expected = str(expected_frontend_sha or "").strip()
    requested = str(requested_test_sha or "").strip()
    if not FULL_SHA_RE.fullmatch(expected):
        raise ValueError("expected frontend SHA must be a full lowercase 40-character commit SHA")

    resolved = requested or expected
    if not FULL_SHA_RE.fullmatch(resolved):
        raise ValueError("acceptance harness SHA must be a full lowercase 40-character commit SHA")
    if resolved != expected:
        raise ValueError(
            "acceptance harness SHA must equal the exact accepted frontend SHA "
            f"({expected}); received {resolved}"
        )
    return resolved


def _append_output(path: Path, key: str, value: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-frontend-sha", required=True)
    parser.add_argument("--requested-test-sha", default="")
    parser.add_argument("--github-output", type=Path, required=True)
    parser.add_argument("--github-env", type=Path, required=True)
    args = parser.parse_args()

    try:
        resolved = resolve_harness_sha(
            expected_frontend_sha=args.expected_frontend_sha,
            requested_test_sha=args.requested_test_sha,
        )
    except ValueError as exc:
        parser.error(str(exc))

    _append_output(args.github_output, "test_sha", resolved)
    _append_output(args.github_env, "PPL_ALLOC_009_TEST_SHA", resolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
