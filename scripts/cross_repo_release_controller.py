#!/usr/bin/env python3
"""Dispatch and verify one exact execute-plans gate/deploy transaction.

The Pantheon nonprod workflow calls this controller only after the exact BFF
candidate is live and healthy under the shared dev environment lease.  The
controller dispatches the frontend integration gate for the immutable
Pantheon/execute-plans ledger, waits for that exact run, then dispatches the
atomic frontend switch.  Ordinary frontend pushes never call this path.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


FRONTEND_REPOSITORY = "ajoe734/execute-plans"
FRONTEND_BRANCH = "dev"
GATE_WORKFLOW = "pantheon-integration-gate.yml"
DEPLOY_WORKFLOW = "pantheon-dev-fe-deploy.yml"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[1-9][0-9]*$")


class ControllerError(RuntimeError):
    """Raised when the exact cross-repository transaction cannot continue."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def exact_sha(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not SHA_RE.fullmatch(normalized):
        raise ControllerError(f"{label} must be one exact lowercase 40-character SHA")
    return normalized


def exact_digest(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not DIGEST_RE.fullmatch(normalized):
        raise ControllerError(f"{label} must be one exact lowercase SHA-256 digest")
    return normalized


def exact_run_id(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not RUN_ID_RE.fullmatch(normalized):
        raise ControllerError(f"{label} must be one positive integer run ID")
    return normalized


@dataclass(frozen=True)
class ExpectedRun:
    workflow: str
    title: str
    head_sha: str


class GitHubClient:
    def __init__(self, *, api_url: str, token: str, repository: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.repository = repository

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "pantheon-cross-repo-release-controller",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.api_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise ControllerError(
                f"GitHub API {method} {path} failed with HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ControllerError(
                f"GitHub API {method} {path} failed: {exc.reason}"
            ) from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ControllerError(
                f"GitHub API {method} {path} returned invalid JSON"
            ) from exc

    def list_runs(self, workflow: str) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode(
            {"branch": FRONTEND_BRANCH, "event": "workflow_dispatch", "per_page": 100}
        )
        payload = self.request(
            "GET",
            f"/repos/{self.repository}/actions/workflows/{workflow}/runs?{query}",
        )
        runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
        if not isinstance(runs, list):
            raise ControllerError(f"{workflow} run listing has no workflow_runs array")
        return [run for run in runs if isinstance(run, dict)]

    def dispatch(self, workflow: str, inputs: dict[str, str]) -> None:
        self.request(
            "POST",
            f"/repos/{self.repository}/actions/workflows/{workflow}/dispatches",
            {"ref": FRONTEND_BRANCH, "inputs": inputs},
        )

    def get_run(self, run_id: int) -> dict[str, Any]:
        payload = self.request(
            "GET",
            f"/repos/{self.repository}/actions/runs/{run_id}",
        )
        if not isinstance(payload, dict):
            raise ControllerError(f"run {run_id} response is not an object")
        return payload


def validate_run(run: dict[str, Any], expected: ExpectedRun) -> int:
    run_id = run.get("id")
    if not isinstance(run_id, int) or run_id <= 0:
        raise ControllerError(f"{expected.workflow} returned an invalid run ID")
    path = str(run.get("path") or "").split("@", 1)[0]
    repository = str(
        (run.get("head_repository") or {}).get("full_name")
        or (run.get("repository") or {}).get("full_name")
        or ""
    ).lower()
    valid = (
        path == f".github/workflows/{expected.workflow}"
        and repository == FRONTEND_REPOSITORY
        and run.get("event") == "workflow_dispatch"
        and run.get("head_branch") == FRONTEND_BRANCH
        and str(run.get("head_sha") or "").lower() == expected.head_sha
        and str(run.get("display_title") or "") == expected.title
    )
    if not valid:
        raise ControllerError(
            f"run {run_id} does not match exact {expected.workflow} identity"
        )
    return run_id


def dispatch_and_wait(
    client: GitHubClient,
    *,
    expected: ExpectedRun,
    inputs: dict[str, str],
    timeout_seconds: int,
    poll_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    existing_ids = {
        run.get("id") for run in client.list_runs(expected.workflow) if run.get("id")
    }
    client.dispatch(expected.workflow, inputs)
    deadline = time.monotonic() + timeout_seconds
    selected: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        matches = [
            run
            for run in client.list_runs(expected.workflow)
            if run.get("id") not in existing_ids
            and str(run.get("display_title") or "") == expected.title
        ]
        if len(matches) > 1:
            raise ControllerError(
                f"{expected.workflow} dispatch resolved to multiple candidate runs"
            )
        if matches:
            validate_run(matches[0], expected)
            selected = matches[0]
            break
        sleep(poll_seconds)
    if selected is None:
        raise ControllerError(f"timed out discovering {expected.workflow} dispatch")

    run_id = validate_run(selected, expected)
    while time.monotonic() < deadline:
        current = client.get_run(run_id)
        validate_run(current, expected)
        status = str(current.get("status") or "")
        if status == "completed":
            conclusion = str(current.get("conclusion") or "")
            if conclusion != "success":
                raise ControllerError(
                    f"{expected.workflow} run {run_id} concluded {conclusion or 'unknown'}"
                )
            return current
        if status not in {"queued", "in_progress", "pending", "waiting", "requested"}:
            raise ControllerError(
                f"{expected.workflow} run {run_id} has unexpected status {status!r}"
            )
        sleep(poll_seconds)
    raise ControllerError(f"timed out waiting for {expected.workflow} run {run_id}")


def coordinate_release(
    client: GitHubClient,
    *,
    frontend_sha: str,
    backend_sha: str,
    bff_base_url: str,
    release_candidate_id: str,
    compatibility_manifest_sha256: str,
    controller_run_id: str,
    gate_timeout_seconds: int,
    deploy_timeout_seconds: int,
    poll_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    frontend_sha = exact_sha(frontend_sha, "frontend SHA")
    backend_sha = exact_sha(backend_sha, "backend SHA")
    release_candidate_id = exact_digest(
        release_candidate_id, "release candidate ID"
    )
    compatibility_manifest_sha256 = exact_digest(
        compatibility_manifest_sha256, "compatibility manifest digest"
    )
    controller_run_id = exact_run_id(controller_run_id, "controller run ID")
    if not str(bff_base_url).startswith("https://"):
        raise ControllerError("BFF base URL must use HTTPS")
    started_at = utc_now()
    gate_title = f"Release candidate {release_candidate_id}"
    gate = dispatch_and_wait(
        client,
        expected=ExpectedRun(
            workflow=GATE_WORKFLOW,
            title=gate_title,
            head_sha=frontend_sha,
        ),
        inputs={
            "fe_sha": frontend_sha,
            "bff_sha": backend_sha,
            "bff_base_url": bff_base_url,
            "pantheon_contract_ref": backend_sha,
            "release_candidate_id": release_candidate_id,
            "compatibility_manifest_sha256": compatibility_manifest_sha256,
            "release_controller_run_id": controller_run_id,
            "soft_fail": "false",
        },
        timeout_seconds=gate_timeout_seconds,
        poll_seconds=poll_seconds,
        sleep=sleep,
    )
    gate_run_id = str(validate_run(
        gate,
        ExpectedRun(GATE_WORKFLOW, gate_title, frontend_sha),
    ))

    deploy_title = f"Deploy release candidate {release_candidate_id}"
    deploy = dispatch_and_wait(
        client,
        expected=ExpectedRun(
            workflow=DEPLOY_WORKFLOW,
            title=deploy_title,
            head_sha=frontend_sha,
        ),
        inputs={
            "candidate_sha": frontend_sha,
            "gate_run_id": gate_run_id,
            "release_candidate_id": release_candidate_id,
            "compatibility_manifest_sha256": compatibility_manifest_sha256,
            "release_controller_run_id": controller_run_id,
            "deployment_profile": "read-only",
            "proof_window_ack": "false",
            "emergency_override": "false",
            "rollback_drill": "false",
            "override_reason": "",
        },
        timeout_seconds=deploy_timeout_seconds,
        poll_seconds=poll_seconds,
        sleep=sleep,
    )
    return {
        "schema_version": "pantheon.cross-repo-release-controller-evidence.v1",
        "release_candidate_id": release_candidate_id,
        "compatibility_manifest_sha256": compatibility_manifest_sha256,
        "compatibility_status": "compatible",
        "controller": {
            "repository": "ajoe734/pantheon",
            "workflow": "nonprod-deploy.yml",
            "run_id": controller_run_id,
        },
        "backend": {
            "repository": "ajoe734/pantheon",
            "branch": "dev",
            "commit": backend_sha,
        },
        "frontend": {
            "repository": FRONTEND_REPOSITORY,
            "branch": FRONTEND_BRANCH,
            "commit": frontend_sha,
        },
        "integration_gate": {
            "run_id": str(gate["id"]),
            "url": gate.get("html_url"),
            "conclusion": gate.get("conclusion"),
        },
        "frontend_deploy": {
            "run_id": str(deploy["id"]),
            "url": deploy.get("html_url"),
            "conclusion": deploy.get("conclusion"),
        },
        "started_at": started_at,
        "completed_at": utc_now(),
        "outcome": "accepted",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-sha", required=True)
    parser.add_argument("--backend-sha", required=True)
    parser.add_argument("--bff-base-url", required=True)
    parser.add_argument("--release-candidate-id", required=True)
    parser.add_argument("--compatibility-manifest-sha256", required=True)
    parser.add_argument("--controller-run-id", required=True)
    parser.add_argument("--evidence-out", required=True)
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    parser.add_argument("--gate-timeout-seconds", type=int, default=10_800)
    parser.add_argument("--deploy-timeout-seconds", type=int, default=5_400)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = os.environ.get("CROSS_REPO_RELEASE_TOKEN", "")
    if not token:
        print("ERROR: CROSS_REPO_RELEASE_TOKEN is required", file=sys.stderr)
        return 2
    if args.gate_timeout_seconds <= 0 or args.deploy_timeout_seconds <= 0:
        print("ERROR: timeouts must be positive", file=sys.stderr)
        return 2
    if args.poll_seconds <= 0:
        print("ERROR: poll interval must be positive", file=sys.stderr)
        return 2
    try:
        evidence = coordinate_release(
            GitHubClient(
                api_url=args.api_url,
                token=token,
                repository=FRONTEND_REPOSITORY,
            ),
            frontend_sha=args.frontend_sha,
            backend_sha=args.backend_sha,
            bff_base_url=args.bff_base_url,
            release_candidate_id=args.release_candidate_id,
            compatibility_manifest_sha256=args.compatibility_manifest_sha256,
            controller_run_id=args.controller_run_id,
            gate_timeout_seconds=args.gate_timeout_seconds,
            deploy_timeout_seconds=args.deploy_timeout_seconds,
            poll_seconds=args.poll_seconds,
        )
    except ControllerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    output = Path(args.evidence_out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(f"accepted release candidate {evidence['release_candidate_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
