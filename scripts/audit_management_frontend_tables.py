#!/usr/bin/env python3
"""Audit Management frontend dense-table UX guardrails.

Large Management tables must not hide the only horizontal scrollbar at the
bottom of a long vertical list. This static audit catches newly added table-like
surfaces that lack an explicit dense-table affordance: pinned/sticky horizontal
scroll, pagination, virtualization, or the shared dense-table marker.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_SOURCE_ROOTS = (
    "execute-plans/src/management",
    "apps/management/src",
)

TABLE_PATTERNS = (
    re.compile(r"<table\b", re.IGNORECASE),
    re.compile(r"role=[\"']table[\"']", re.IGNORECASE),
    re.compile(r"\bDataTable\b"),
    re.compile(r"\bgridTemplateColumns\b"),
)

WIDE_SCROLL_PATTERN = re.compile(r"overflow-x\s*[:=]\s*[\"']?(auto|scroll)", re.IGNORECASE)

AFFORDANCE_PATTERNS = (
    re.compile(r"data-management-dense-table"),
    re.compile(r"\bManagementDenseTable\b"),
    re.compile(r"\bsticky[-_]?scrollbar\b", re.IGNORECASE),
    re.compile(r"\bpinned[-_]?horizontal[-_]?scroll", re.IGNORECASE),
    re.compile(r"position\s*:\s*sticky", re.IGNORECASE),
    re.compile(r"\bpagination\b", re.IGNORECASE),
    re.compile(r"\bpage[_-]?size\b", re.IGNORECASE),
    re.compile(r"\bvirtuali[sz]e", re.IGNORECASE),
)

FRONTEND_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".html", ".css"}


@dataclass(frozen=True)
class Issue:
    severity: str
    category: str
    path: str
    line: int
    title: str
    evidence: str
    recommendation: str


def iter_source_files(repo_root: Path, source_roots: Sequence[str]) -> Iterable[Path]:
    for root_name in source_roots:
        root = repo_root / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in FRONTEND_SUFFIXES:
                yield path


def line_number(text: str, index: int) -> int:
    return text.count("\n", 0, max(index, 0)) + 1


def has_affordance(text: str) -> bool:
    return any(pattern.search(text) for pattern in AFFORDANCE_PATTERNS)


def table_evidence(text: str) -> tuple[str, int] | None:
    for pattern in TABLE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0), match.start()
    match = WIDE_SCROLL_PATTERN.search(text)
    if match:
        return match.group(0), match.start()
    return None


def audit_file(path: Path, repo_root: Path) -> list[Issue]:
    text = path.read_text(encoding="utf-8")
    evidence = table_evidence(text)
    if evidence is None or has_affordance(text):
        return []
    snippet, index = evidence
    rel = str(path.relative_to(repo_root))
    return [
        Issue(
            severity="high",
            category="dense_table_affordance",
            path=rel,
            line=line_number(text, index),
            title="Management table-like surface lacks a dense-table affordance",
            evidence=snippet,
            recommendation=(
                "Use a shared dense table with pinned/sticky horizontal scroll, "
                "or add server pagination/virtualization so users never have to "
                "scroll to the bottom just to move horizontally."
            ),
        )
    ]


def audit(repo_root: Path, source_roots: Sequence[str]) -> list[Issue]:
    issues: list[Issue] = []
    for path in iter_source_files(repo_root, source_roots):
        issues.extend(audit_file(path, repo_root))
    return issues


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", type=Path)
    parser.add_argument("--source-root", action="append", dest="source_roots")
    parser.add_argument("--format", choices={"summary", "json"}, default="summary")
    parser.add_argument("--fail-on-issues", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source_roots = tuple(args.source_roots or DEFAULT_SOURCE_ROOTS)
    repo_root = args.repo_root.resolve()
    issues = audit(repo_root, source_roots)
    payload = {
        "schema": "pantheon.management-frontend-table-audit.v1",
        "source_roots": source_roots,
        "issue_count": len(issues),
        "issues": [asdict(issue) for issue in issues],
    }
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            "source=frontend-static "
            f"roots={','.join(source_roots)} issues={len(issues)}"
        )
        for issue in issues:
            print(f"{issue.severity}\t{issue.category}\t{issue.path}:{issue.line}\t{issue.evidence}")
    return 1 if issues and args.fail_on_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
