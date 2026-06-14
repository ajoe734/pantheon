#!/usr/bin/env python3
"""audit_canonical_docs.py — canonical document & contract integrity check.

Verifies the canonical-doc surface the whole system treats as authoritative is
internally consistent:
  1. every file in ai-status.json `canonical_files` exists at the repo root;
  2. every `*.md` referenced by CANONICAL_DOCUMENT_MAP.md exists;
Reports missing/broken references. Exit 1 if any are broken (so it can gate).

Usage: python3 scripts/audit_canonical_docs.py
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _exists(name: str) -> bool:
    # canonical_files are repo-root-relative (some are nested paths)
    return (ROOT / name).exists()


def audit_status_canonical_files() -> list[str]:
    p = ROOT / "ai-status.json"
    if not p.exists():
        return ["ai-status.json missing"]
    cf = json.loads(p.read_text()).get("canonical_files", [])
    if not isinstance(cf, list):
        return [f"canonical_files is {type(cf).__name__}, expected list"]
    return [f"canonical_files: missing {n}" for n in cf if not _exists(n)]


def audit_doc_map_refs() -> list[str]:
    p = ROOT / "CANONICAL_DOCUMENT_MAP.md"
    if not p.exists():
        return ["CANONICAL_DOCUMENT_MAP.md missing"]
    text = p.read_text()
    # referenced markdown filenames (uppercase canonical docs + path-like refs)
    refs = sorted(set(re.findall(r"`?([A-Za-z0-9_./-]+\.md)`?", text)))
    broken = []
    for r in refs:
        # skip obvious non-repo refs (urls handled by lack of scheme)
        if r.startswith(("http", "//")):
            continue
        if not (ROOT / r).exists() and not (ROOT / r.split("/")[-1]).exists():
            broken.append(f"DOC_MAP ref: missing {r}")
    return broken


def main() -> int:
    problems = audit_status_canonical_files() + audit_doc_map_refs()
    if not problems:
        print("OK: canonical_files + CANONICAL_DOCUMENT_MAP references all resolve.")
        return 0
    print(f"FOUND {len(problems)} broken canonical reference(s):")
    for p in problems:
        print("  -", p)
    return 1


if __name__ == "__main__":
    sys.exit(main())
