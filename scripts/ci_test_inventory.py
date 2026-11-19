#!/usr/bin/env python3
"""Fail when a test file is run by no required CI job and is not in the inventory.

Rule: every test file (test_*.py and *_test.py, tracked by git, outside vendored
code) must be collected by a required CI job, or be listed in
docs/operations/ci-test-inventory.json with a reason and an owner.

What a job collects is derived from the workflow and stage-0 matrix definitions
themselves: every `pytest` / `unittest` / `unittest discover` command found in
the sources below is parsed and its path arguments are expanded against the
checkout (a directory argument covers every test file below it). There is no
second hand-kept list of covered files.

Usage:
  python3 scripts/ci_test_inventory.py check      # CI guard (exit 1 on violation)
  python3 scripts/ci_test_inventory.py report     # JSON of uncovered files
  python3 scripts/ci_test_inventory.py run-shard --shard I --of N
      # the default sweep: every tracked test file that no other job command
      # names and that is not in the inventory, one pytest process per file,
      # split over N shards by a stable hash of the path

The sweep is what makes a new test file run in CI by default: it is selected by
subtraction (all tracked tests minus files other jobs name minus the inventory),
so nothing has to be added to a list when a test file is created. A new test
that is red fails the sweep and must be fixed or listed with a reason and owner.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INVENTORY_PATH = "docs/operations/ci-test-inventory.json"
# Required-CI definitions the guard reads. Add a file here when a new required
# workflow starts running tests.
WORKFLOW_SOURCES = (
    ".github/workflows/branch-ci.yml",
    ".github/workflows/stage-0-ci.yml",
    ".github/pantheon-stage0-matrix.json",
)
# Extra import roots that unittest commands run with (PYTHONPATH=.orchestrator).
IMPORT_ROOTS = (".", ".orchestrator")
VENDORED_PARTS = {"node_modules", "vendor", "vendored", "third_party", ".venv", "venv", "site-packages"}
TEST_GLOBS = ("test_*.py", "*_test.py")
# Shard runner: its TARGETS constant is read, not copied.
SHARD_RUNNER = "scripts/ci_source_ingestion_test_shards.py"


def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, g) for g in TEST_GLOBS)


def tracked_test_files(root: Path = REPO_ROOT) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"], cwd=root, check=True, capture_output=True
    ).stdout.decode()
    files = []
    for p in out.split("\0"):
        if not p or not is_test_file(p):
            continue
        if VENDORED_PARTS & set(p.split("/")):
            continue
        if (root / p).is_file():
            files.append(p)
    return sorted(files)


def _logical_lines(text: str) -> list[str]:
    text = text.replace("\\\n", " ")
    return text.splitlines()


def _command_strings(rel: str, root: Path) -> list[str]:
    text = (root / rel).read_text(encoding="utf-8")
    if rel.endswith(".json"):
        found: list[str] = []

        def walk(node):
            if isinstance(node, str):
                found.append(node)
            elif isinstance(node, list):
                for n in node:
                    walk(n)
            elif isinstance(node, dict):
                for n in node.values():
                    walk(n)

        walk(json.loads(text))
        return found
    return _logical_lines(text)


def _split_commands(line: str) -> list[list[str]]:
    """Return token lists, one per `-m pytest|unittest` invocation on the line."""
    if not re.search(r"-m\s+(pytest|unittest)\b", line):
        return []
    try:
        tokens = shlex.split(line, comments=True, posix=True)
    except ValueError:
        tokens = line.split()
    cmds: list[list[str]] = []
    for i, tok in enumerate(tokens):
        if tok == "-m" and i + 1 < len(tokens) and tokens[i + 1] in ("pytest", "unittest"):
            j = i + 1
            end = len(tokens)
            for k in range(j + 1, len(tokens)):
                if tokens[k] in ("&&", "||", ";", "|") or tokens[k].endswith(";"):
                    end = k
                    break
            cmds.append(tokens[j:end])
    return cmds


# Options that consume the next token as a value (not a test path).
_VALUE_OPTS = {
    "-k", "-m", "-p", "-c", "-o", "-n", "--maxfail", "--timeout", "--junitxml",
    "--rootdir", "--tb", "--durations", "--deselect", "--ignore", "--ignore-glob",
    "--confcutdir", "--basetemp", "-s", "-t", "--shard",
}


def _expand_dir(root: Path, rel: str, pattern_globs, ignores: list[str]) -> set[str]:
    base = root / rel
    out: set[str] = set()
    for p in base.rglob("*.py"):
        r = p.relative_to(root).as_posix()
        if not any(fnmatch.fnmatch(p.name, g) for g in pattern_globs):
            continue
        if VENDORED_PARTS & set(r.split("/")):
            continue
        if any(r == ig or r.startswith(ig.rstrip("/") + "/") for ig in ignores):
            continue
        out.add(r)
    return out


def _resolve_module(root: Path, dotted: str) -> str | None:
    rel = dotted.replace(".", "/") + ".py"
    for base in IMPORT_ROOTS:
        cand = rel if base == "." else (Path(base) / rel).as_posix()
        if (root / cand).is_file():
            return cand
    return None


def parse_command(tokens: list[str], root: Path) -> tuple[set[str], set[str]]:
    """tokens start at pytest|unittest. Returns (whole_files, partial_files)."""
    tool = tokens[0]
    args = tokens[1:]
    whole: set[str] = set()
    partial: set[str] = set()
    ignores: list[str] = []
    positional: list[str] = []
    discover = False
    start_dir: str | None = None
    pattern = "test*.py"
    i = 0
    while i < len(args):
        a = args[i]
        if tool == "unittest" and a == "discover":
            discover = True
            i += 1
            continue
        if a.startswith("--ignore=") or a.startswith("--ignore-glob="):
            if a.startswith("--ignore="):
                ignores.append(a.split("=", 1)[1])
            i += 1
            continue
        if a == "--ignore" and i + 1 < len(args):
            ignores.append(args[i + 1])
            i += 2
            continue
        if discover and a in ("-s", "--start-directory") and i + 1 < len(args):
            start_dir = args[i + 1]
            i += 2
            continue
        if discover and a in ("-p", "--pattern") and i + 1 < len(args):
            pattern = args[i + 1]
            i += 2
            continue
        if a in _VALUE_OPTS:
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        positional.append(a)
        i += 1
    if discover:
        rel = (start_dir or ".").rstrip("/")
        if (root / rel).is_dir():
            whole |= _expand_dir(root, rel, (pattern,), ignores)
        return whole, partial
    for pos in positional:
        path, sep, _node = pos.partition("::")
        path = path.removeprefix("./")
        if (root / path).is_dir():
            whole |= _expand_dir(root, path, TEST_GLOBS, ignores)
        elif path.endswith(".py") and (root / path).is_file():
            (partial if sep else whole).add(path)
        elif tool == "unittest" and re.fullmatch(r"[A-Za-z_][\w.]*", path):
            # dotted module (optionally ending in Class.test): longest module prefix
            parts = path.split(".")
            for n in range(len(parts), 0, -1):
                mod = _resolve_module(root, ".".join(parts[:n]))
                if mod:
                    (whole if n == len(parts) else partial).add(mod)
                    break
    return whole, partial


def shard_runner_targets(root: Path) -> set[str]:
    p = root / SHARD_RUNNER
    if not p.is_file():
        return set()
    m = re.search(r"^TARGETS\s*=\s*\(([^)]*)\)", p.read_text(encoding="utf-8"), re.M | re.S)
    if not m:
        return set()
    out: set[str] = set()
    for t in re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)):
        if any(ch in t for ch in "*?["):
            out |= {q.relative_to(root).as_posix() for q in root.glob(t)}
        elif (root / t).is_dir():
            out |= _expand_dir(root, t, TEST_GLOBS, [])
        elif (root / t).is_file():
            out.add(t)
    return out


SWEEP_COMMAND = "ci_test_inventory.py run-shard"


def _sweep_declared(root: Path, sources) -> tuple[bool, list[str]]:
    """Is the sweep wired into a required workflow with every shard present?

    Returns (ok, problems). The sweep counts as collecting files only when each
    shard index 0..N-1 of `--of N` appears in a matrix of the same workflow.
    """
    for rel in sources:
        p = root / rel
        if not (rel.endswith(".yml") and p.is_file()):
            continue
        text = p.read_text(encoding="utf-8")
        if SWEEP_COMMAND not in text:
            continue
        m = re.search(r"run-shard\b[^\n]*--of\s+(\d+)", text)
        if not m or "matrix.shard" not in text:
            return False, [f"{rel}: sweep command must pass `--shard ${{{{ matrix.shard }}}} --of N`"]
        n = int(m.group(1))
        mm = re.search(r"shard:\s*\[([^\]]*)\]", text)
        shards = sorted(int(x) for x in re.findall(r"\d+", mm.group(1))) if mm else []
        if shards != list(range(n)):
            return False, [f"{rel}: sweep matrix shard list {shards} must be exactly 0..{n - 1}"]
        return True, []
    return False, []


def covered_files(root: Path = REPO_ROOT, sources=WORKFLOW_SOURCES, with_sweep: bool = True, inventory_rel: str = INVENTORY_PATH) -> dict[str, str]:
    """Map test file -> 'whole' | 'partial' | 'sweep' for what required CI collects."""
    cov = _named_files(root, sources)
    if with_sweep and _sweep_declared(root, sources)[0]:
        inv, _ = load_inventory(root, inventory_rel)
        for t in tracked_test_files(root):
            if t not in cov and t not in inv:
                cov[t] = "sweep"
    return cov


def _named_files(root: Path, sources) -> dict[str, str]:
    cov: dict[str, str] = {}
    for rel in sources:
        if not (root / rel).is_file():
            continue
        for line in _command_strings(rel, root):
            for toks in _split_commands(line):
                whole, partial = parse_command(toks, root)
                for f in whole:
                    cov[f] = "whole"
                for f in partial:
                    cov.setdefault(f, "partial")
    for f in shard_runner_targets(root):
        cov[f] = "whole"
    return cov


def load_inventory(root: Path = REPO_ROOT, rel: str = INVENTORY_PATH) -> tuple[dict[str, dict], list[str]]:
    """Return ({path: entry}, errors)."""
    errors: list[str] = []
    p = root / rel
    if not p.is_file():
        return {}, [f"{rel} is missing"]
    data = json.loads(p.read_text(encoding="utf-8"))
    entries: dict[str, dict] = {}
    for n, e in enumerate(data.get("exemptions", [])):
        paths = e.get("paths") or ([e["path"]] if e.get("path") else [])
        if not paths:
            errors.append(f"exemption #{n} lists no path")
        if not str(e.get("reason", "")).strip():
            errors.append(f"exemption #{n} ({paths[:1]}) has no reason")
        if not str(e.get("owner", "")).strip():
            errors.append(f"exemption #{n} ({paths[:1]}) has no owning task or domain")
        for path in paths:
            if path in entries:
                errors.append(f"{path} is listed twice in the inventory")
            entries[path] = e
    return entries, errors


def check(root: Path = REPO_ROOT, sources=WORKFLOW_SOURCES, inventory_rel: str = INVENTORY_PATH) -> list[str]:
    tests = tracked_test_files(root)
    cov = covered_files(root, sources, inventory_rel=inventory_rel)
    inv, errors = load_inventory(root, inventory_rel)
    ok, problems = _sweep_declared(root, sources)
    errors += problems
    for t in tests:
        if t not in cov and t not in inv:
            errors.append(
                f"{t}: not run by any required CI job and not in {inventory_rel}; "
                "add it to a required job command or list it with a reason and an owner"
            )
    known = set(tests)
    for path in sorted(inv):
        if path not in known:
            errors.append(f"{path}: listed in {inventory_rel} but is not a tracked test file; remove the entry")
        elif path in cov and cov[path] != "sweep":
            errors.append(f"{path}: listed in {inventory_rel} but a required CI job now runs it; remove the entry")
    return errors


def sweep_files(root: Path = REPO_ROOT, sources=WORKFLOW_SOURCES, inventory_rel: str = INVENTORY_PATH) -> list[str]:
    named = _named_files(root, sources)
    inv, _ = load_inventory(root, inventory_rel)
    return [t for t in tracked_test_files(root) if t not in named and t not in inv]


def shard_of(path: str, shards: int) -> int:
    return zlib.crc32(path.encode()) % shards


def run_one(root: Path, path: str, timeout: float) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [".orchestrator", env.get("PYTHONPATH", "")]))
    start = time.time()
    try:
        p = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfE", "--tb=short", path],
            cwd=root, env=env, capture_output=True, text=True, timeout=timeout,
        )
        rc, out = p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        rc, out = -9, f"TIMEOUT after {timeout:.0f}s"
    lines = out.splitlines()
    failed = [l.split(" ", 1)[1].split(" - ")[0] for l in lines if l.startswith(("FAILED ", "ERROR "))]
    # exit 5 = file collected no tests (all skipped or helper module): not a failure
    return {"file": path, "rc": rc, "ok": rc in (0, 5), "secs": round(time.time() - start, 1),
            "summary": lines[-1] if lines else "", "failed": failed, "output": out[-4000:]}


def run_shard(shard: int, shards: int, jobs: int, timeout: float, results: str | None,
              only: list[str] | None = None) -> int:
    files = only if only is not None else [f for f in sweep_files() if shard_of(f, shards) == shard]
    print(f"shard {shard}/{shards}: {len(files)} test files", flush=True)
    t0 = time.time()
    res: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        for r in ex.map(lambda f: run_one(REPO_ROOT, f, timeout), files):
            res.append(r)
            print(f"{'ok  ' if r['ok'] else 'FAIL'} {r['secs']:6.1f}s {r['file']}  {r['summary']}", flush=True)
            if not r["ok"]:
                print(r["output"], flush=True)
    bad = [r for r in res if not r["ok"]]
    print(f"shard {shard}/{shards}: {len(res) - len(bad)} passed, {len(bad)} failed, wall {time.time() - t0:.0f}s")
    if results:
        Path(results).write_text(json.dumps(res, indent=1))
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("check", "report", "run-shard"))
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--of", dest="shards", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds per test file")
    ap.add_argument("--results", help="write per-file results JSON here")
    ap.add_argument("--files-from", help="classification only: run exactly the files listed in this file")
    args = ap.parse_args(argv)
    if args.mode == "run-shard":
        only = None
        if args.files_from:
            only = [l.strip() for l in Path(args.files_from).read_text().splitlines() if l.strip()]
        return run_shard(args.shard, args.shards, args.jobs, args.timeout, args.results, only)
    if args.mode == "report":
        tests = tracked_test_files()
        cov = covered_files()
        print(json.dumps({
            "test_files": len(tests),
            "covered": sum(1 for t in tests if t in cov),
            "uncovered": [t for t in tests if t not in cov],
            "partial_only": [t for t in tests if cov.get(t) == "partial"],
        }, indent=1))
        return 0
    errors = check()
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    if errors:
        print(f"{len(errors)} test inventory violation(s)", file=sys.stderr)
        return 1
    print("test inventory ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
