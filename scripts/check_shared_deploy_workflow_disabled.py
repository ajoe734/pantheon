#!/usr/bin/env python3
"""Detect a fleet-shared deploy workflow stuck in `disabled_manually`.

Nonprod deploy is fleet infrastructure: `nonprod-deploy.yml` (pantheon) and
its execute-plans counterpart serve every task, not just the one that last
touched them. A task that wants exclusivity over its own proof run must use
the workflow's `concurrency:` group or the dev environment lease
(`scripts/dev_environment_lease.py`); it must never call
`gh workflow disable` on the shared workflow itself. If that happens anyway
(a stray script, a manual mistake), the workflow silently stops accepting
any dispatch or push trigger for the whole fleet until a human notices and
re-enables it -- see docs/conventions/GIT_WORKFLOW.md "Shared Deploy Workflow
Ownership" for the incident this guards against.

This script only reports; it never disables anything. With `--enable` it can
also restore the watched workflows, for an operator who has already decided
that the disable is unwarranted.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (repo, workflow_id, human label) for every workflow that the whole fleet's
# deploy path depends on. Add a new row here, not a one-off script, when
# another shared deploy workflow is introduced.
WATCHED_WORKFLOWS = (
    ("ajoe734/pantheon", "269991390", "Pantheon Nonprod Deploy"),
    ("ajoe734/execute-plans", "292028803", "Pantheon Dev FE Deploy"),
)


def workflow_state(repo: str, workflow_id: str) -> str | None:
    """Return the workflow's `state` field, or None if it cannot be read."""
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{repo}/actions/workflows/{workflow_id}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        payload = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None
    state = payload.get("state")
    return state if isinstance(state, str) else None


def enable_workflow(repo: str, workflow_id: str) -> bool:
    try:
        out = subprocess.run(
            ["gh", "api", "--method", "PUT", f"repos/{repo}/actions/workflows/{workflow_id}/enable"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0


def check(workflows=WATCHED_WORKFLOWS) -> list[dict]:
    """Return one record per watched workflow currently `disabled_manually`."""
    findings = []
    for repo, workflow_id, label in workflows:
        state = workflow_state(repo, workflow_id)
        if state == "disabled_manually":
            findings.append({"repo": repo, "workflow_id": workflow_id, "label": label, "state": state})
    return findings


def log_line(message: str, *, log_path: Path) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    line = f"[{stamp}] {message}"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass
    print(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="Status root for logs.")
    parser.add_argument(
        "--enable", action="store_true",
        help="Also re-enable any watched workflow found disabled_manually.",
    )
    args = parser.parse_args(argv)

    log_path = args.root.resolve() / ".orchestrator" / "logs" / "disabled-shared-workflow-guard.log"
    findings = check()

    for finding in findings:
        outcome = "report-only"
        if args.enable:
            outcome = "re-enabled" if enable_workflow(finding["repo"], finding["workflow_id"]) else "re-enable-failed"
        log_line(
            f"DISABLED_MANUALLY repo={finding['repo']} workflow_id={finding['workflow_id']} "
            f"label=\"{finding['label']}\" -> {outcome}",
            log_path=log_path,
        )

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
