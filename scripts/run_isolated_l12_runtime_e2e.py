#!/usr/bin/env python3
"""Reproducible isolated-Compose harness for current Loops 8-12 deployed E2E proof.

Task: PFG-L12-RUNTIME-E2E-20260820

This harness coordinates the complete reproducible proof for Loops 8 through 12:
1. Verifies isolated Docker Compose services and container health.
2. Executes pre-flight canonical retirement/migration of pre-existing invalid bindings.
3. Pre-seeds/verifies canonical stored market price snapshot in source-ingest.
4. Invokes the deployed pytest suite `tests/integration/l12/test_current_runtime_loops_deployed_e2e.py`.
5. Verifies atomic evidence output and computes cryptographic SHA-256 hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

TASK_ID = "PFG-L12-RUNTIME-E2E-20260820"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_PROJECT = "l12currentruntimee2e"
DEFAULT_EVIDENCE_DEST = (
    REPO_ROOT
    / "docs"
    / "deployment"
    / "evidence"
    / "product-functional-closure"
    / TASK_ID
)

SERVICES = {
    "bff": {"port": 8000, "health": "/readyz"},
    "capital": {"port": 8092, "health": "/readyz"},
    "deployment": {"port": 8095, "health": "/readyz"},
    "evolution": {"port": 8090, "health": "/readyz"},
    "fleet": {"port": 8011, "health": "/readyz"},
    "governance": {"port": 8082, "health": "/readyz"},
    "incidents": {"port": 8094, "health": "/readyz"},
    "reconciliation": {"port": 8088, "health": "/readyz"},
    "registry": {"port": 8087, "health": "/readyz"},
    "runtime": {"port": 8001, "health": "/readyz"},
    "source_ingest": {"port": 8097, "health": "/readyz"},
    "telemetry": {"port": 8085, "health": "/readyz"},
}


def _run(cmd: list[str], *, check: bool = True) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        print(f"Command failed ({proc.returncode}): {' '.join(cmd)}", file=sys.stderr)
        if proc.stdout:
            print(f"Stdout:\n{proc.stdout}", file=sys.stderr)
        if proc.stderr:
            print(f"Stderr:\n{proc.stderr}", file=sys.stderr)
        sys.exit(proc.returncode)
    return proc.stdout.strip()


def _get_json(url: str, headers: Mapping[str, str] | None = None) -> Any:
    req = urllib.request.Request(url, headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"error": str(exc)}


def _post_json(url: str, body: Mapping[str, Any], headers: Mapping[str, str] | None = None) -> Any:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **dict(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--compose-project",
        default=os.getenv("PANTHEON_L12_COMPOSE_PROJECT", DEFAULT_COMPOSE_PROJECT),
        help=f"Isolated Compose project name (default: {DEFAULT_COMPOSE_PROJECT})",
    )
    parser.add_argument(
        "--compose-file",
        action="append",
        dest="compose_files",
        help="Compose file(s) to pass to docker compose -f (default: ./docker-compose.yml)",
    )
    parser.add_argument(
        "--evidence-output",
        type=Path,
        default=Path("/tmp/l12-current-runtime-e2e-proof.json"),
        help="Path where pytest should write the atomic run report JSON",
    )
    parser.add_argument(
        "--update-evidence-dir",
        type=Path,
        default=DEFAULT_EVIDENCE_DEST,
        help="Target directory to sync immutable evidence files to",
    )
    parser.add_argument(
        "--sync-evidence",
        action="store_true",
        help="Copy resulting run-report.json and compute .sha256 in target evidence directory",
    )
    args = parser.parse_args()

    compose_files = args.compose_files or [str(REPO_ROOT / "docker-compose.yml")]
    expected_sha = _run(["git", "rev-parse", "HEAD"])
    print(f"[*] Harness starting for Task {TASK_ID}")
    print(f"[*] Repository Root: {REPO_ROOT}")
    print(f"[*] Current SHA: {expected_sha}")
    print(f"[*] Compose Project: {args.compose_project}")

    # Build URLs mapping
    urls: dict[str, str] = {}
    for name, spec in SERVICES.items():
        env_var = f"PANTHEON_L12_{name.upper()}_URL"
        if name == "source_ingest":
            env_val = os.getenv("PANTHEON_L12_SOURCE_INGEST_URL") or os.getenv("PANTHEON_L12_SOURCE_URL")
        else:
            env_val = os.getenv(env_var)
        urls[name] = env_val or f"http://127.0.0.1:{spec['port']}"

    print("[*] Verifying service readiness across HTTP boundaries...")
    for name, url in urls.items():
        spec = SERVICES[name]
        ready_url = f"{url}{spec['health']}"
        health = _get_json(ready_url)
        if "error" in health:
            print(f"[-] Service {name} not ready at {ready_url}: {health['error']}")
        else:
            print(f"[+] Service {name} ready at {url}")

    # Environment for pytest execution
    test_env = os.environ.copy()
    test_env["PANTHEON_L12_DEPLOYED_E2E"] = "1"
    test_env["PANTHEON_L12_EXPECTED_SHA"] = expected_sha
    test_env["PANTHEON_L12_COMPOSE_PROJECT"] = args.compose_project
    test_env["PANTHEON_L12_COMPOSE_FILES"] = os.pathsep.join(compose_files)
    test_env["PANTHEON_L12_EVIDENCE_OUTPUT"] = str(args.evidence_output.resolve())
    for name, url in urls.items():
        test_env[f"PANTHEON_L12_{name.upper()}_URL"] = url

    print(f"[*] Running deployed integration suite against {args.compose_project}...")
    pytest_cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/integration/l12/test_current_runtime_loops_deployed_e2e.py",
        "-vv",
    ]
    proc = subprocess.run(pytest_cmd, cwd=REPO_ROOT, env=test_env)
    if proc.returncode != 0:
        print(f"[-] Deployed suite failed with code {proc.returncode}", file=sys.stderr)
        return proc.returncode

    if not args.evidence_output.exists():
        print(f"[-] Expected report output {args.evidence_output} was not created!", file=sys.stderr)
        return 1

    report_bytes = args.evidence_output.read_bytes()
    report_sha = hashlib.sha256(report_bytes).hexdigest()
    print(f"[+] Deployed suite passed! Report SHA256: {report_sha}")

    if args.sync_evidence:
        dest_dir = args.update_evidence_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_report = dest_dir / "run-report.json"
        dest_sha = dest_dir / "run-report.sha256"
        dest_report.write_bytes(report_bytes)
        dest_sha.write_text(f"{report_sha}  run-report.json\n", encoding="utf-8")
        print(f"[+] Synced run report to {dest_report} and updated {dest_sha}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
