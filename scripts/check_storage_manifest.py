#!/usr/bin/env python3
"""Fail when persistent writers in the code and docs/operations/storage-manifest.json disagree.

RC3 WP3.2 of docs/operations/remediation-plan-20261117.md. The inventory is discovered
statically from non-test sources (exclude test_*.py, tests/**, conftest.py, fixtures/**,
reproduce_*.py):

  jsonl          names matching [A-Za-z0-9_.{}-]+\\.jsonl in services/**/*.py
  sqlite         names matching [A-Za-z0-9_.{}-]+\\.(sqlite3|sqlite|db) in services/**/*.py
  sqlite_connect files under services/ that call sqlite3.connect
  compose_path   /data/...(json|jsonl|sqlite3|sqlite|db) paths in docker-compose.yml
  table          CREATE TABLE targets in services/**/*.py, services/**/*.sql, scripts/db_migrate.sh
                 (a templated target is keyed ``<file>::<template>``)
  schema_table   schema.table literals on a line mentioning "table" in services/**/*.py

Rules (fail closed, exit 1):
  * a discovered key with no manifest entry (exact name, or ``pattern`` regex) and no waiver;
  * a manifest entry or waiver that matches nothing discovered;
  * an entry missing a required field, or with bound_status not in bounded/unbounded/unreviewed;
  * ``unbounded`` / ``unreviewed`` without register_class and task;
  * ``bounded`` without cap and a ``bound_proof`` {file, contains} whose text is found in code.
Output is JSON on stdout (or --output). No credential is read.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "docs" / "operations" / "storage-manifest.json"
SCHEMA_VERSION = "storage_manifest_check.v1"
KINDS = ("jsonl", "sqlite", "sqlite_connect", "compose_path", "table", "schema_table")
STATUSES = ("bounded", "unbounded", "unreviewed")
REQUIRED = ("kind", "owner", "growth_driver", "growth_per_day", "cap", "cleanup", "readers", "bound_status")
FILE_SUFFIX = re.compile(r"\.(py|json|jsonl|md|txt|yml|yaml|sql|db|sqlite3?)$")

JSONL_RE = re.compile(r"[A-Za-z0-9_.{}-]+\.jsonl")
SQLITE_RE = re.compile(r"[A-Za-z0-9_.{}-]+\.(?:sqlite3|sqlite|db)\b")
COMPOSE_RE = re.compile(r"/data/[^\s\"'`,;:)]*\.(?:json|jsonl|sqlite3|sqlite|db)\b")
CREATE_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_.\"{}$]+)", re.I)
SCHEMA_RE = re.compile(r"[\"']([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)[\"']")


def is_excluded(path: Path) -> bool:
    s = path.as_posix()
    n = path.name
    return (
        n.startswith("test_") or n == "conftest.py" or n.startswith("reproduce_")
        or "/tests/" in s or "/fixtures/" in s or "node_modules" in s or "__pycache__" in s
    )


def _sources(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    base = root / "services"
    out = []
    for suffix in suffixes:
        out.extend(p for p in base.rglob(f"*{suffix}") if p.is_file() and not is_excluded(p.relative_to(root)))
    return sorted(set(out))


def discover(root: Path = REPO_ROOT) -> dict[str, dict[str, set[str]]]:
    """Return {kind: {key: {files where found}}}."""
    found: dict[str, dict[str, set[str]]] = {k: {} for k in KINDS}

    def add(kind: str, key: str, path: Path) -> None:
        found[kind].setdefault(key, set()).add(path.relative_to(root).as_posix())

    for p in _sources(root, (".py",)):
        text = p.read_text(errors="ignore")
        for m in JSONL_RE.finditer(text):
            add("jsonl", m.group(0), p)
        for m in SQLITE_RE.finditer(text):
            add("sqlite", m.group(0), p)
        if "sqlite3.connect" in text:
            add("sqlite_connect", p.relative_to(root).as_posix(), p)
        for line in text.splitlines():
            if re.search("table", line, re.I):
                for m in SCHEMA_RE.finditer(line):
                    if not FILE_SUFFIX.search(m.group(1)):
                        add("schema_table", m.group(1), p)
    compose = root / "docker-compose.yml"
    if compose.is_file():
        for m in COMPOSE_RE.finditer(compose.read_text(errors="ignore")):
            add("compose_path", m.group(0), compose)
    table_files = _sources(root, (".py", ".sql"))
    migrate = root / "scripts" / "db_migrate.sh"
    if migrate.is_file():
        table_files.append(migrate)
    for p in table_files:
        rel = p.relative_to(root).as_posix()
        for m in CREATE_RE.finditer(p.read_text(errors="ignore")):
            name = m.group(1).replace('"', "")
            key = f"{rel}::{name}" if ("{" in name or "$" in name) else name
            add("table", key, p)
    return found


def _entry_matches(entry: dict, key: str) -> bool:
    if "name" in entry and entry["name"] == key:
        return True
    pattern = entry.get("pattern")
    return bool(pattern) and re.fullmatch(pattern, key) is not None


def validate_entry(entry: dict, root: Path) -> list[str]:
    errs = []
    label = f"{entry.get('kind')}:{entry.get('name') or entry.get('pattern')}"
    if entry.get("kind") not in KINDS:
        errs.append(f"{label}: unknown kind")
    if ("name" in entry) == ("pattern" in entry):
        errs.append(f"{label}: exactly one of name/pattern is required")
    for field in REQUIRED:
        if field not in entry or entry[field] in (None, "", []):
            errs.append(f"{label}: missing {field}")
    status = entry.get("bound_status")
    if status not in STATUSES:
        errs.append(f"{label}: bound_status must be one of {STATUSES}")
    elif status in ("unbounded", "unreviewed"):
        for field in ("register_class", "task"):
            if not entry.get(field):
                errs.append(f"{label}: {status} entry needs {field}")
    elif status == "bounded":
        proof = entry.get("bound_proof") or {}
        target = root / proof.get("file", "")
        needle = proof.get("contains", "")
        if not needle or not target.is_file() or needle not in target.read_text(errors="ignore"):
            errs.append(f"{label}: bound_proof text not found in {proof.get('file')!r}")
    return errs


def check(manifest: dict, root: Path = REPO_ROOT) -> dict:
    found = discover(root)
    entries = manifest.get("entries", [])
    waivers = manifest.get("waivers", [])
    failures: list[str] = []
    for entry in entries:
        failures.extend(validate_entry(entry, root))
    for w in waivers:
        if not w.get("reason") or w.get("kind") not in KINDS or not w.get("name"):
            failures.append(f"waiver {w!r}: needs kind, name and reason")
    undeclared, stale = [], []
    for kind in KINDS:
        k_entries = [e for e in entries if e.get("kind") == kind]
        k_waivers = [w for w in waivers if w.get("kind") == kind]
        for key in sorted(found[kind]):
            if not any(_entry_matches(e, key) for e in k_entries) and not any(w.get("name") == key for w in k_waivers):
                undeclared.append(f"{kind}:{key}")
        for e in k_entries:
            if not any(_entry_matches(e, key) for key in found[kind]):
                stale.append(f"{kind}:{e.get('name') or e.get('pattern')}")
        for w in k_waivers:
            if w.get("name") not in found[kind]:
                stale.append(f"waiver {kind}:{w.get('name')}")
    failures += [f"undeclared writer (add a manifest entry or waiver): {u}" for u in undeclared]
    failures += [f"manifest entry no longer found in code (remove it): {s}" for s in stale]
    counts = {k: len(found[k]) for k in KINDS}
    status_counts = {s: sum(1 for e in entries if e.get("bound_status") == s) for s in STATUSES}
    return {
        "schema_version": SCHEMA_VERSION,
        "passed": not failures,
        "discovered": counts,
        "entries": len(entries),
        "waivers": len(waivers),
        "bound_status": status_counts,
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    ap.add_argument("--root", type=Path, default=REPO_ROOT)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text())
    except (OSError, ValueError) as exc:
        report = {"schema_version": SCHEMA_VERSION, "passed": False, "failures": [f"manifest unreadable: {exc}"]}
    else:
        report = check(manifest, args.root.resolve())
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(text + "\n")
    print(text)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
