#!/usr/bin/env python3
"""Read-only acceptance of a post-merge dev Taiwan refresh (requires authenticated gh).

Usage: python3 scripts/probe_tw_refresh_restore.py --run-id RUN --merged-commit SHA
No refresh, deployment, remote restore or state mutation is performed.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess

REPO = "ajoe734/pantheon"


def gh(*args: str, raw: bool = False):
    value = subprocess.check_output(["gh", *args], text=True, timeout=60)
    return value if raw else json.loads(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--merged-commit", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.merged_commit):
        parser.error("--merged-commit requires the full reviewed merge SHA")
    run = gh("api", f"repos/{REPO}/actions/runs/{args.run_id}")
    failures = []
    if run["head_branch"] != "dev" or run["path"] != ".github/workflows/dev-tw-market-refresh.yml":
        failures.append("run is not the dev Taiwan refresh workflow")
    if run["status"] != "completed":
        failures.append("refresh has not finished")
    comparison = gh("api", f"repos/{REPO}/compare/{args.merged_commit}...{run['head_sha']}")
    if comparison["status"] not in ("ahead", "identical"):
        failures.append("refresh head does not contain the supplied merge")
    jobs = gh("api", f"repos/{REPO}/actions/runs/{args.run_id}/jobs?per_page=100")["jobs"]
    refresh_jobs = [job for job in jobs if job["name"] == "Taiwan Market Daily Refresh"]
    if len(refresh_jobs) != 1:
        failures.append("expected exactly one refresh job")
    else:
        job = refresh_jobs[0]
        logs = gh("api", f"repos/{REPO}/actions/jobs/{job['id']}/logs", raw=True)
        # Match emitted log records, not echoed workflow shell source or diagnostic code.
        if re.search(r"\[remote-deploy\].*(?:bounded_runtime_stale_restore_failed|record .* kept: restore failed)", logs):
            failures.append("refresh encountered stale-record/restore failure")
        checks = [step for step in job["steps"] if step["name"] == "Verify and restore steady source-ingest posture"]
        if len(checks) != 1 or checks[0]["conclusion"] != "success":
            failures.append("hosted steady restore step did not pass")
        # Both successful paths remove the state directory before emitting this outcome.
        # restored_in_place additionally produces the existing capped receipt.
        healthy = re.search(r"\[remote-deploy\] steady posture check: status=(healthy|restored) reason=([^\r\n]*)", logs)
        if not healthy:
            failures.append("missing emitted healthy/restored steady posture outcome")
        retired = re.findall(r"\[remote-deploy\] bounded runtime record retired: [^\r\n]*reason=restored_in_place[^\r\n]*", logs)
    print(json.dumps({
        "passed": not failures,
        "run_id": args.run_id,
        "head_sha": run["head_sha"],
        "merged_commit": args.merged_commit,
        "restored_in_place_receipts": len(retired) if len(refresh_jobs) == 1 else 0,
        "record_disposition": "removed by successful hosted restore/retirement" if not failures else "unproven",
        "failures": failures,
    }, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
