#!/usr/bin/env python3
"""Run the source ingestion test step as deterministic parallel shards.

The Branch CI step used to run one serial pytest over every source ingestion
test and hit its 15 minute timeout.  This runner keeps the same test set but:

* collects the exact same targets, records the collected count and fails if the
  shards together do not run exactly that many tests (nothing silently dropped);
* assigns whole test files to shards with a deterministic greedy
  longest-processing-time rule over a checked-in weight table (unknown files get
  a default weight), so the split never depends on timing noise;
* gives every shard its own SOURCE_INGEST_DATA_DIR (no shared fixed paths);
* fails with a clear message when a single test exceeds --max-test-seconds, so
  the next slow test is caught in its own pull request.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGETS = ("services/source_ingestion/tests", "services/source_ingestion/test_*.py")
DEFAULT_SHARDS = 4
DEFAULT_MAX_TEST_SECONDS = 300.0
DEFAULT_WEIGHT = 2.0
# Approximate seconds per file (serial). Missing files use DEFAULT_WEIGHT.
FILE_WEIGHTS: dict[str, float] = {
    "services/source_ingestion/tests/test_scheduled_connector.py": 163.0,
    "services/source_ingestion/tests/test_collector_newest_session_progress.py": 140.0,
    "services/source_ingestion/tests/test_collector_newest_session_hosted_throughput.py": 92.0,
    "services/source_ingestion/tests/test_collector_bundle_connector.py": 58.0,
    "services/source_ingestion/tests/test_collector_market_early_fetch_pit.py": 29.0,
    "services/source_ingestion/tests/test_controller_worker.py": 28.0,
    "services/source_ingestion/test_service.py": 18.0,
    "services/source_ingestion/tests/test_distillation_queue_lifetime.py": 14.0,
    "services/source_ingestion/tests/test_collector_outcome_truth.py": 13.0,
    "services/source_ingestion/tests/test_l12_dist_001_transactional_distillation.py": 11.0,
    "services/source_ingestion/tests/test_collector_market_provenance_bound.py": 10.0,
    "services/source_ingestion/tests/test_latest_market_snapshot_compaction.py": 10.0,
}


def expand_targets() -> list[str]:
    out: list[str] = []
    for target in TARGETS:
        if any(ch in target for ch in "*?["):
            out.extend(sorted(glob.glob(str(REPO_ROOT / target))))
        else:
            out.append(str(REPO_ROOT / target))
    return [os.path.relpath(p, REPO_ROOT) for p in out]


def collect(python: str) -> dict[str, int]:
    """Return {test file: collected test count} for the exact step targets."""
    proc = subprocess.run(
        [python, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *expand_targets()],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode not in (0,):
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit(f"collection failed (exit {proc.returncode})")
    counts: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        if "::" in line and not line.startswith(" "):
            counts[line.split("::", 1)[0]] = counts.get(line.split("::", 1)[0], 0) + 1
    return counts


def plan_shards(files: list[str], shards: int, weights: dict[str, float] | None = None) -> list[list[str]]:
    """Deterministic greedy LPT assignment of files to shards."""
    weights = FILE_WEIGHTS if weights is None else weights
    ordered = sorted(files, key=lambda f: (-weights.get(f, DEFAULT_WEIGHT), f))
    bins: list[list[str]] = [[] for _ in range(shards)]
    loads = [0.0] * shards
    for f in ordered:
        i = min(range(shards), key=lambda k: (loads[k], k))
        bins[i].append(f)
        loads[i] += weights.get(f, DEFAULT_WEIGHT)
    return [sorted(b) for b in bins]


def slow_tests(junit_paths: list[str], limit: float) -> list[tuple[str, float]]:
    slow = []
    for path in junit_paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            t = float(case.get("time", "0"))
            if t > limit:
                slow.append((f'{case.get("classname")}::{case.get("name")}', t))
    return sorted(slow, key=lambda x: -x[1])


def junit_summary(path: str) -> tuple[int, int, int, int]:
    tests = fail = skip = 0
    for case in ET.parse(path).getroot().iter("testcase"):
        tests += 1
        if case.find("failure") is not None or case.find("error") is not None:
            fail += 1
        if case.find("skipped") is not None:
            skip += 1
    return tests, fail, skip, 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shards", type=int, default=DEFAULT_SHARDS)
    ap.add_argument("--max-test-seconds", type=float, default=DEFAULT_MAX_TEST_SECONDS)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--report", help="write a JSON report (counts, per-file and per-shard durations)")
    ap.add_argument("--print-plan", action="store_true")
    ap.add_argument("pytest_args", nargs="*", help="extra pytest args")
    args = ap.parse_args(argv)

    counts = collect(args.python)
    expected = sum(counts.values())
    plan = plan_shards(sorted(counts), args.shards)
    if args.print_plan:
        print(json.dumps(plan, indent=2))
    print(f"collected {expected} tests in {len(counts)} files; {args.shards} shards", flush=True)

    workdir = Path(tempfile.mkdtemp(prefix="source-ingest-shards-"))
    procs = []
    started = time.monotonic()
    for i, files in enumerate(plan):
        if not files:
            continue
        data_dir = workdir / f"data-{i}"
        data_dir.mkdir()
        env = dict(os.environ, SOURCE_INGEST_DATA_DIR=str(data_dir))
        junit = workdir / f"junit-{i}.xml"
        log = (workdir / f"shard-{i}.log").open("w")
        cmd = [args.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}", "-o", "junit_family=xunit1",
               "--durations=10", *args.pytest_args, *files]
        procs.append((i, subprocess.Popen(cmd, cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT),
                      junit, workdir / f"shard-{i}.log", time.monotonic()))
    failed = False
    ran = 0
    skipped = 0
    shard_info = []
    junits = []
    for i, proc, junit, logpath, t0 in procs:
        rc = proc.wait()
        proc.wait()
        suite = ET.parse(junit).getroot() if junit.exists() else None
        node = suite if suite is None or suite.tag == "testsuite" else suite.find("testsuite")
        dur = float(node.get("time", "0")) if node is not None else time.monotonic() - t0
        print(f"::group::shard {i} (exit {rc}, {dur:.0f}s)")
        print(logpath.read_text()[-6000:] if rc else logpath.read_text()[-1500:])
        print("::endgroup::")
        shard_info.append({"shard": i, "exit": rc, "seconds": round(dur, 1), "files": plan[i]})
        if rc != 0:
            failed = True
        if junit.exists():
            t, _, s, _ = junit_summary(str(junit))
            ran += t
            skipped += s
            junits.append(str(junit))
    total = time.monotonic() - started
    print(f"all shards finished in {total:.0f}s; ran {ran} (skipped {skipped}) of {expected} collected")
    if ran != expected:
        print(f"ERROR: collected {expected} tests but shards ran {ran}; a test was dropped or duplicated")
        failed = True
    slow = slow_tests(junits, args.max_test_seconds)
    for name, t in slow:
        print(f"ERROR: test took {t:.0f}s, over the {args.max_test_seconds:.0f}s per-test limit: {name}. "
              "Make it cheaper or shard/mark it deliberately in scripts/ci_source_ingestion_test_shards.py.")
    if slow:
        failed = True
    if args.report:
        per_file: dict[str, float] = {}
        for jp in junits:
            for case in ET.parse(jp).getroot().iter("testcase"):
                f = case.get("file") or case.get("classname", "")
                per_file[f] = round(per_file.get(f, 0.0) + float(case.get("time", "0")), 1)
        Path(args.report).write_text(json.dumps({
            "collected": expected, "ran": ran, "skipped": skipped, "total_seconds": round(total, 1),
            "shards": shard_info, "per_file_seconds": dict(sorted(per_file.items(), key=lambda x: -x[1])),
        }, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
