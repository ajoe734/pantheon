#!/usr/bin/env python3
"""Reject executable shared-deploy disable/cancel instructions.

Worker-facing guides, skills, task briefs, and templates may explain the
fleet rule, but they must not contain a copy-pastable command that disables a
workflow or cancels an Actions run. Runtime CLI enforcement is separate; this
check prevents the unsafe pattern from being taught to future workers.
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = (
    ROOT / "AI_COLLABORATION_GUIDE.md",
    ROOT / ".orchestrator" / "skills",
    ROOT / ".orchestrator" / "task-briefs",
    ROOT / ".orchestrator" / "templates",
)

MUTATION_PATTERNS = (
    re.compile(r"\bgh\s+workflow\s+disable\b", re.IGNORECASE),
    re.compile(r"\bgh\s+run\s+(?:cancel\b|[^\s]+\s+force-cancel\b)", re.IGNORECASE),
    re.compile(
        r"actions/(?:workflows/[^\s/]+/disable|runs/[^\s/]+/(?:cancel|force-cancel))\b",
        re.IGNORECASE,
    ),
)
NEGATED_RULE = re.compile(
    r"\b(?:do not|don't|must not|may not|never|forbidden|prohibited|rejects?|blocks?|"
    r"no\s+(?:task|worker|agent)|remove every instance)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    path: Path
    line_number: int
    line: str


def _iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from sorted(candidate for candidate in path.rglob("*") if candidate.is_file())


def scan_paths(paths: Iterable[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in _iter_files(paths):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        in_fence = False
        for line_number, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = not in_fence
                continue
            if not any(pattern.search(line) for pattern in MUTATION_PATTERNS):
                continue
            if not in_fence and NEGATED_RULE.search(line):
                continue
            findings.append(Finding(path=path, line_number=line_number, line=line.strip()))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="Optional files or directories to scan.")
    args = parser.parse_args(argv)
    paths = tuple(path.resolve() for path in args.paths) or DEFAULT_PATHS
    findings = scan_paths(paths)
    for finding in findings:
        try:
            display_path = finding.path.resolve().relative_to(ROOT)
        except ValueError:
            display_path = finding.path
        print(f"{display_path}:{finding.line_number}: unsafe shared-deploy mutation instruction: {finding.line}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
