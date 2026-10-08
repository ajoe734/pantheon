#!/usr/bin/env python3
"""Net-size report for reviewers and CI.

Line counts are review evidence. The only enforced limit is the evidence-file
cap, through the exit code the required CI step uses. Standard library only:
CI runs it without extras.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

PRODUCTION = "production"
TEST = "test"
DOCS = "docs/evidence"


def classify(path: str) -> str:
    """Classify a repository-relative path as production, test or docs/evidence."""
    p = PurePosixPath(path)
    name = p.name
    parts = p.parts[:-1]
    if (
        "tests" in parts
        or "test" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
        or ".test." in name
        or ".spec." in name
    ):
        return TEST
    if (
        (p.parts and p.parts[0] in {"docs", "ai-task-archive", "support"})
        or "evidence" in parts
        or name.endswith(".md")
    ):
        return DOCS
    return PRODUCTION


def is_evidence(path: str) -> bool:
    return "evidence" in PurePosixPath(path).parts[:-1]


def settings(config: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = ((config or {}).get("branch_workflow") or {}).get("diff_budget") or {}
    return {
        "evidence_max_added_lines": int(raw.get("evidence_max_added_lines", 400)),
    }


def summarize(files: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Sum additions and deletions per class; fail closed on missing counts."""
    totals = {cls: {"additions": 0, "deletions": 0} for cls in (PRODUCTION, TEST, DOCS)}
    rows = []
    for entry in files:
        path = str(entry.get("filename") or "").strip()
        added, deleted = entry.get("additions"), entry.get("deletions")
        if not path or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (added, deleted)):
            raise SystemExit(f"diff budget: line counts unavailable for changed file {path or '?'!r}")
        cls = classify(path)
        totals[cls]["additions"] += added
        totals[cls]["deletions"] += deleted
        rows.append({"path": path, "class": cls, "additions": added, "deletions": deleted})
    for bucket in totals.values():
        bucket["net"] = bucket["additions"] - bucket["deletions"]
    return {"totals": totals, "files": rows}


def violations(
    summary: Mapping[str, Any],
    config: Mapping[str, Any] | None,
    *,
    label: str,
) -> list[str]:
    cfg = settings(config)
    problems: list[str] = []
    cap = cfg["evidence_max_added_lines"]
    for row in summary["files"]:
        if is_evidence(row["path"]) and row["additions"] > cap:
            problems.append(
                f"{label}: evidence file {row['path']!r} adds {row['additions']} lines (cap {cap}); "
                "commit a summary and reference bulky output by digest."
            )
    return problems


def git_numstat(base: str, head: str, cwd: Path | None = None) -> list[dict[str, Any]]:
    out = subprocess.run(
        ["git", "diff", "--numstat", "-z", "--no-renames", base, head],
        check=True,
        capture_output=True,
        cwd=cwd,
    ).stdout.decode("utf-8", "surrogateescape")
    files = []
    for record in out.split("\0"):
        if not record:
            continue
        added, deleted, path = record.split("\t", 2)
        if added == "-":  # binary
            continue
        files.append({"filename": path, "additions": int(added), "deletions": int(deleted)})
    return files


def render(summary: Mapping[str, Any]) -> str:
    lines = ["| class | added | deleted | net |", "|---|---|---|---|"]
    for cls, bucket in summary["totals"].items():
        lines.append(f"| {cls} | +{bucket['additions']} | -{bucket['deletions']} | {bucket['net']:+d} |")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[2] / ".orchestrator" / "config.json"))
    parser.add_argument("--summary", help="append the markdown table to this file (e.g. $GITHUB_STEP_SUMMARY)")
    args = parser.parse_args(argv)

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    summary = summarize(git_numstat(args.base, args.head))
    table = render(summary)
    print(table)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write("### Diff budget\n\n" + table + "\n")
    problems = violations(summary, config, label=f"{args.base[:12]}..{args.head[:12]}")
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
