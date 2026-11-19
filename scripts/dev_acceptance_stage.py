#!/usr/bin/env python3
"""Post-deploy acceptance stage for the dev pair (RC1 step 5).

The stage runs AFTER a deploy has been switched and verified.  It never rolls
anything back and never fails the run: every outcome, including an internal
error, becomes the ``acceptance`` verdict (pending / passed / failed) and the
process exits 0.  ``deploymentState`` of the hosted manifest is not touched.

Inputs
  * declared checks   ``acceptance/checks.json``  (id, owner, mutates, argv)
  * feature probes    executable files directly inside ``acceptance/probes/``
    (contract: ``acceptance/README.md``)
  * one built-in read-only check, ``deployed-pair-identity``

Growth bound: the verdict is ONE small JSON document that the caller replaces
(latest result per release).  Per-check output is truncated and the number of
checks is capped, so nothing here can grow without bound.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "pantheon.dev-acceptance-verdict.v1"
SELF_OWNER = "DEV-DEPLOY-ACCEPTANCE-STAGE-20261117"
SKIP_EXIT = 77
DETAIL_LIMIT = 2000
MAX_CHECKS = 200
DEFAULT_TIMEOUT = 300
OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
OWNER_HEADER_RE = re.compile(r"^\s*(?:#|//)\s*acceptance-owner:\s*(\S+)\s*$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HEADER_LINES = 20


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clip(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= DETAIL_LIMIT else text[-DETAIL_LIMIT:]


def read_owner(path: Path) -> str | None:
    """Owning task or chain id declared in the first lines of a probe file."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(HEADER_LINES):
                match = OWNER_HEADER_RE.match(handle.readline())
                if match and OWNER_RE.match(match.group(1)):
                    return match.group(1)
    except OSError:
        pass
    return None


def discover_probes(directory: Path) -> list[dict]:
    """Executable regular files directly inside ``directory`` (no recursion, no dotfiles)."""
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.name.startswith(".") or path.name.endswith((".md", ".json")):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        found.append({"id": f"probe:{path.name}", "kind": "probe", "path": path,
                      "owner": read_owner(path), "executable": os.access(path, os.X_OK)})
    return found


def load_checks(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    checks = []
    for item in document.get("checks", []):
        if not (isinstance(item.get("id"), str) and isinstance(item.get("argv"), list) and item["argv"]
                and all(isinstance(part, str) for part in item["argv"])
                and isinstance(item.get("owner"), str) and OWNER_RE.match(item["owner"])):
            raise ValueError(f"invalid acceptance check declaration: {item!r}")
        checks.append({"id": f"check:{item['id']}", "kind": "check", "owner": item["owner"],
                       "mutates": bool(item.get("mutates", False)), "argv": item["argv"],
                       "timeout": int(item.get("timeout", DEFAULT_TIMEOUT))})
    return checks


def plan(checks: list[dict], probes: list[dict]) -> dict:
    return {"checks": len(checks), "probes": len(probes),
            "mutating": any(check["mutates"] for check in checks)}


def _fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def identity_check(*, fe_url: str, bff_url: str, backend_sha: str, frontend_sha: str, fetch=_fetch_json) -> dict:
    """Read-only: the hosted pair still serves exactly the pair that was deployed."""
    manifest = fetch(f"{fe_url.rstrip('/')}/deployment.json")
    version = fetch(f"{bff_url.rstrip('/')}/bff/version")
    problems = []
    if manifest.get("deploymentState") not in {"accepted", "functional-accepted"}:
        problems.append(f"deploymentState={manifest.get('deploymentState')!r}")
    if manifest.get("bffCommit") != backend_sha:
        problems.append("hosted manifest backend SHA differs from the deployed pair")
    if manifest.get("frontendSha") != frontend_sha:
        problems.append("hosted manifest frontend SHA differs from the deployed pair")
    if version.get("source_commit_sha") != backend_sha:
        problems.append("/bff/version source_commit_sha differs from the deployed backend")
    return {"status": "failed" if problems else "passed", "detail": "; ".join(problems)}


def _run(argv: list[str], env: dict, timeout: int) -> tuple[int, str]:
    try:
        done = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except OSError as exc:
        return 126, f"could not execute: {exc}"
    return done.returncode, _clip((done.stdout or "") + (done.stderr or ""))


def _result(item: dict, status: str, *, exit_code: int | None = None, detail: str = "", seconds: float = 0.0) -> dict:
    return {"id": item["id"], "kind": item["kind"], "owner": item.get("owner") or "UNDECLARED",
            "status": status, "exit_code": exit_code, "detail": _clip(detail), "seconds": round(seconds, 2)}


def run_stage(*, checks: list[dict], probes: list[dict], identity: dict, base_env: dict,
              lease_held: bool, runner=_run, identity_fn=identity_check, clock=time.monotonic) -> dict:
    """Returns the ``checks`` list. Never raises."""
    results = []
    probe_env = {**base_env,
                 "ACCEPTANCE_BACKEND_SHA": identity["backend_sha"],
                 "ACCEPTANCE_FRONTEND_SHA": identity["frontend_sha"],
                 "ACCEPTANCE_FE_URL": identity["fe_url"], "ACCEPTANCE_BFF_URL": identity["bff_url"]}
    started = clock()
    try:
        outcome = identity_fn(fe_url=identity["fe_url"], bff_url=identity["bff_url"],
                              backend_sha=identity["backend_sha"], frontend_sha=identity["frontend_sha"])
    except Exception as exc:  # advisory stage: an unreachable host is a failed check, not a crash
        outcome = {"status": "failed", "detail": f"identity read failed: {exc}"}
    results.append(_result({"id": "check:deployed-pair-identity", "kind": "check", "owner": SELF_OWNER},
                           outcome["status"], detail=outcome["detail"], seconds=clock() - started))
    for check in checks:
        if len(results) >= MAX_CHECKS:
            break
        if check["mutates"] and not lease_held:
            results.append(_result(check, "skipped", detail="mutating check needs the qualification lease, which is not held"))
            results[-1]["lease_required"] = True
            continue
        started = clock()
        code, output = runner(check["argv"], probe_env, check["timeout"])
        results.append(_result(check, "passed" if code == 0 else "failed", exit_code=code, detail=output,
                               seconds=clock() - started))
    for probe in probes:
        if len(results) >= MAX_CHECKS:
            break
        if not probe["owner"]:
            results.append(_result(probe, "failed", detail="probe lacks an '# acceptance-owner: <id>' header"))
            continue
        if not probe["executable"]:
            results.append(_result(probe, "failed", detail="probe is not executable"))
            continue
        started = clock()
        code, output = runner([str(probe["path"])], probe_env, DEFAULT_TIMEOUT)
        status = "passed" if code == 0 else "skipped" if code == SKIP_EXIT else "failed"
        results.append(_result(probe, status, exit_code=code, detail=output, seconds=clock() - started))
    return results


def verdict_status(results: list[dict]) -> str:
    if any(item["status"] == "failed" for item in results):
        return "failed"
    if any(item.get("lease_required") for item in results):
        return "pending"  # a required mutating check did not run: not a pass
    return "passed"


FRONTEND_GATE_OWNER = "frontend"


def gate_failure_result(*, gate_run_id: str, failed_gates: list[str]) -> dict:
    """A frontend gate failure for an unchanged served frontend: the release is
    deployed (not rolled back) and not accepted, owned by the frontend chain."""
    names = ", ".join(failed_gates) or "unknown"
    return _result({"id": "gate:frontend-integration", "kind": "check", "owner": FRONTEND_GATE_OWNER},
                   "failed", detail=_clip(f"integration gate run {gate_run_id} failed: {names}"))


def build_verdict(*, status: str, results: list[dict], identity: dict, run: dict) -> dict:
    failed = [{"id": item["id"], "kind": item["kind"], "owner": item["owner"], "detail": item["detail"]}
              for item in results if item["status"] == "failed"]
    return {
        "schemaVersion": SCHEMA,
        "acceptance": status,
        "generatedAt": _now(),
        "deployment": {"backendSha": identity["backend_sha"], "frontendSha": identity["frontend_sha"]},
        "evidenceRun": run,
        "failed": failed,
        "checks": results,
    }


def _identity_from(args) -> dict:
    for label, value in (("backend", args.backend_sha), ("frontend", args.frontend_sha)):
        if not SHA_RE.match(value):
            raise SystemExit(f"--{label}-sha must be an exact 40-hex commit")
    return {"backend_sha": args.backend_sha, "frontend_sha": args.frontend_sha,
            "fe_url": args.fe_url, "bff_url": args.bff_url}


def _run_info() -> dict:
    repo, run_id = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_RUN_ID", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    return {"runId": run_id, "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            "url": f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else ""}


def _write(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)  # replace, never append: one verdict per release


def _output(name: str, value: str) -> None:
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def summary_markdown(verdict: dict, deployed: str) -> str:
    lines = ["## Dev release result", f"- deployed: `{deployed}`", f"- acceptance: `{verdict['acceptance']}`"]
    for item in verdict["failed"]:
        lines.append(f"  - FAILED `{item['id']}` (owner `{item['owner']}`)")
    skipped = [item["id"] for item in verdict["checks"] if item["status"] == "skipped"]
    if skipped:
        lines.append(f"- skipped: {', '.join(f'`{name}`' for name in skipped)}")
    lines.append("- a release is `accepted` only when acceptance is `passed`; acceptance never rolls back a deploy")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "pending", "run"))
    parser.add_argument("--checks", type=Path, default=Path("acceptance/checks.json"))
    parser.add_argument("--probes-dir", type=Path, default=Path("acceptance/probes"))
    parser.add_argument("--backend-sha", default="")
    parser.add_argument("--frontend-sha", default="")
    parser.add_argument("--fe-url", default="")
    parser.add_argument("--bff-url", default="")
    parser.add_argument("--verdict-out", type=Path, default=Path("acceptance-verdict.json"))
    parser.add_argument("--lease-held", action="store_true")
    parser.add_argument("--deployed", default="success")
    parser.add_argument("--release-outcome", default="")
    parser.add_argument("--gate-run-id", default="")
    parser.add_argument("--failed-gates", default="")
    args = parser.parse_args(argv)
    try:
        checks, probes = load_checks(args.checks), discover_probes(args.probes_dir)
    except Exception as exc:
        checks, probes, load_error = [], [], str(exc)
    else:
        load_error = ""
    if args.command == "plan":
        info = plan(checks, probes)
        print(json.dumps(info))
        _output("mutating", "true" if info["mutating"] else "false")
        return 0
    identity = _identity_from(args)
    if args.command == "pending":
        _write(args.verdict_out, build_verdict(status="pending", results=[], identity=identity, run=_run_info()))
        return 0
    try:
        if load_error:
            raise ValueError(load_error)
        results = run_stage(checks=checks, probes=probes, identity=identity,
                            base_env=dict(os.environ), lease_held=args.lease_held)
    except Exception as exc:
        results = [_result({"id": "check:stage-internal", "kind": "check", "owner": SELF_OWNER}, "failed",
                           detail=f"acceptance stage error: {exc}")]
    if args.release_outcome == "deployed_not_accepted":
        results = [gate_failure_result(gate_run_id=args.gate_run_id,
                                       failed_gates=[n for n in args.failed_gates.split(",") if n]), *results]
    verdict = build_verdict(status=verdict_status(results), results=results, identity=identity, run=_run_info())
    _write(args.verdict_out, verdict)
    _output("acceptance", verdict["acceptance"])
    markdown = summary_markdown(verdict, args.deployed)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(markdown)
    print(markdown)
    return 0  # advisory: the verdict, not the exit code, carries the result


if __name__ == "__main__":
    sys.exit(main())
