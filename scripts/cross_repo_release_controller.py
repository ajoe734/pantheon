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
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable


FRONTEND_REPOSITORY = "ajoe734/execute-plans"
FRONTEND_BRANCH = "dev"
GATE_WORKFLOW = "pantheon-integration-gate.yml"
DEPLOY_WORKFLOW = "pantheon-dev-fe-deploy.yml"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[1-9][0-9]*$")
PROOF_PROFILES = ("write-proof", "read-only", "operator-live")
PROOF_STATES = (
    "CREATED",
    "IDENTITY_VERIFIED",
    "WRITE_PROOF_ACTIVE",
    "JOURNEYS_RUNNING",
    "PROOF_CAPTURED",
    "READ_ONLY_RESTORED",
    "COMPLETE",
)


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


def canonical_json_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded + b"\n").hexdigest()


def derive_pair_id(pantheon_sha: str, execute_plans_sha: str) -> str:
    return canonical_json_sha256({
        "execute_plans_sha": exact_sha(execute_plans_sha, "frontend SHA"),
        "pantheon_sha": exact_sha(pantheon_sha, "backend SHA"),
    })


def create_candidate_record(
    *,
    pantheon_sha: str,
    execute_plans_sha: str,
    candidate_id: str | None = None,
    pair_id: str | None = None,
    profile: str = "write-proof",
    expires_at: str | None = None,
    source_mode: str = "reconcile-only",
    ttl_seconds: int = 1800,
) -> dict[str, Any]:
    pantheon_sha = exact_sha(pantheon_sha, "backend SHA")
    execute_plans_sha = exact_sha(execute_plans_sha, "frontend SHA")
    canonical_pair = derive_pair_id(pantheon_sha, execute_plans_sha)
    if pair_id is not None:
        supplied_pair = exact_digest(pair_id, "pair ID")
        if supplied_pair != canonical_pair:
            raise ControllerError(
                f"supplied pair ID {supplied_pair} does not match canonically derived pair ID {canonical_pair}"
            )

    if profile not in PROOF_PROFILES:
        raise ControllerError(f"invalid profile: {profile}")
    if source_mode != "reconcile-only":
        raise ControllerError(f"source_mode must be 'reconcile-only', got {source_mode!r}")

    if expires_at is None:
        expiry_dt = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        expires_at = expiry_dt.isoformat().replace("+00:00", "Z")

    if candidate_id:
        cid = exact_digest(candidate_id, "candidate ID")
    else:
        cid = canonical_json_sha256({
            "execute_plans_sha": execute_plans_sha,
            "expires_at": expires_at,
            "pair_id": canonical_pair,
            "pantheon_sha": pantheon_sha,
            "profile": profile,
            "source_mode": source_mode,
        })

    return {
        "candidate_id": cid,
        "pantheon_sha": pantheon_sha,
        "execute_plans_sha": execute_plans_sha,
        "pair_id": canonical_pair,
        "profile": profile,
        "expires_at": expires_at,
        "source_mode": source_mode,
    }


def validate_candidate_override(
    parent_candidate: dict[str, Any],
    child_inputs: dict[str, Any],
) -> None:
    fe_sha = (
        child_inputs.get("frontend_sha")
        or child_inputs.get("fe_sha")
        or child_inputs.get("candidate_sha")
    )
    bff_sha = (
        child_inputs.get("backend_sha")
        or child_inputs.get("bff_sha")
        or child_inputs.get("pantheon_sha")
    )
    pair_id = child_inputs.get("pair_id") or child_inputs.get("pairId")
    candidate_id = child_inputs.get("candidate_id") or child_inputs.get("release_candidate_id")

    if fe_sha is not None:
        norm_fe = exact_sha(fe_sha, "child frontend SHA")
        if norm_fe != parent_candidate["execute_plans_sha"]:
            raise ControllerError(
                f"stale task pair or child inputs cannot override parent candidate: "
                f"frontend SHA {norm_fe} != parent {parent_candidate['execute_plans_sha']}"
            )
    if bff_sha is not None:
        norm_bff = exact_sha(bff_sha, "child backend SHA")
        if norm_bff != parent_candidate["pantheon_sha"]:
            raise ControllerError(
                f"stale task pair or child inputs cannot override parent candidate: "
                f"backend SHA {norm_bff} != parent {parent_candidate['pantheon_sha']}"
            )
    if pair_id is not None:
        norm_pair = exact_digest(pair_id, "child pair ID")
        if norm_pair != parent_candidate["pair_id"]:
            raise ControllerError(
                f"stale task pair or child inputs cannot override parent candidate: "
                f"pair ID {norm_pair} != parent {parent_candidate['pair_id']}"
            )
    if candidate_id is not None:
        norm_cid = exact_digest(candidate_id, "child candidate ID")
        if norm_cid != parent_candidate["candidate_id"]:
            raise ControllerError(
                f"stale task pair or child inputs cannot override parent candidate: "
                f"candidate ID {norm_cid} != parent {parent_candidate['candidate_id']}"
            )


def fetch_url_json(url: str, timeout_seconds: int = 30) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "pantheon-cross-repo-release-controller"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        return json.loads(resp.read().decode("utf-8"))


def verify_served_identity(
    *,
    bff_base_url: str,
    expected_candidate: dict[str, Any],
    fe_base_url: str | None = None,
    fetch_fn: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    fetcher = fetch_fn or fetch_url_json
    if not (bff_base_url.startswith("https://") or bff_base_url.startswith("http://")):
        raise ControllerError("BFF base URL must use HTTPS or HTTP")

    bff_version_url = f"{bff_base_url.rstrip('/')}/bff/version"
    try:
        bff_data = fetcher(bff_version_url)
    except Exception as exc:
        raise ControllerError(f"served identity verification failed reaching BFF: {exc}") from exc

    observed_bff_sha = exact_sha(
        bff_data.get("source_commit_sha") or bff_data.get("commit") or "",
        "served BFF commit SHA",
    )
    if observed_bff_sha != expected_candidate["pantheon_sha"]:
        raise ControllerError(
            f"served identity mismatch fails closed: BFF served {observed_bff_sha} != candidate {expected_candidate['pantheon_sha']}"
        )

    observed_fe_sha = None
    observed_pair_id = None
    if fe_base_url:
        if not (fe_base_url.startswith("https://") or fe_base_url.startswith("http://")):
            raise ControllerError("FE base URL must use HTTPS or HTTP")
        fe_deployment_url = f"{fe_base_url.rstrip('/')}/deployment.json"
        try:
            fe_data = fetcher(fe_deployment_url)
        except Exception as exc:
            raise ControllerError(f"served identity verification failed reaching frontend: {exc}") from exc

        observed_fe_sha = exact_sha(
            fe_data.get("frontendSha") or fe_data.get("commit") or "",
            "served frontend commit SHA",
        )
        if observed_fe_sha != expected_candidate["execute_plans_sha"]:
            raise ControllerError(
                f"served identity mismatch fails closed: FE served {observed_fe_sha} != candidate {expected_candidate['execute_plans_sha']}"
            )
        observed_pair_id = fe_data.get("pairId") or fe_data.get("pair_id")
        if observed_pair_id:
            observed_pair_id = exact_digest(observed_pair_id, "served pair ID")
            if observed_pair_id != expected_candidate["pair_id"]:
                raise ControllerError(
                    f"served identity mismatch fails closed: served pair ID {observed_pair_id} != candidate {expected_candidate['pair_id']}"
                )

    return {
        "status": "verified",
        "observed_bff_sha": observed_bff_sha,
        "observed_fe_sha": observed_fe_sha,
        "observed_pair_id": observed_pair_id,
        "verified_at": utc_now(),
    }


def restore_read_only_profile(
    candidate_record: dict[str, Any],
    *,
    served_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    restored = dict(candidate_record)
    restored["profile"] = "read-only"
    if "restored_at" not in restored:
        restored["restored_at"] = utc_now()
    if served_manifest:
        fe_sha = exact_sha(
            served_manifest.get("frontendSha") or served_manifest.get("commit") or "",
            "served FE SHA",
        )
        bff_sha = exact_sha(
            served_manifest.get("bffCommit") or served_manifest.get("bffSourceCommitSha") or "",
            "served BFF SHA",
        )
        if fe_sha != restored["execute_plans_sha"] or bff_sha != restored["pantheon_sha"]:
            raise ControllerError(
                f"read-only restoration verification mismatch: served FE={fe_sha}/BFF={bff_sha} != candidate FE={restored['execute_plans_sha']}/BFF={restored['pantheon_sha']}"
            )
    return restored


class ProofStateMachine:
    def __init__(self, initial_state: str = "CREATED") -> None:
        if initial_state not in PROOF_STATES:
            raise ControllerError(f"invalid initial proof state: {initial_state}")
        self.state = initial_state
        self.history: list[dict[str, str]] = [{"state": initial_state, "timestamp": utc_now()}]

    def transition(self, next_state: str) -> str:
        if next_state not in PROOF_STATES:
            raise ControllerError(f"invalid proof state: {next_state}")
        if next_state == self.state:
            return self.state
        curr_idx = PROOF_STATES.index(self.state)
        next_idx = PROOF_STATES.index(next_state)
        if next_state == "READ_ONLY_RESTORED" or next_idx == curr_idx + 1:
            self.state = next_state
            self.history.append({"state": next_state, "timestamp": utc_now()})
            return self.state
        raise ControllerError(f"invalid state transition from {self.state} to {next_state}")


def emit_github_outputs(candidate: dict[str, Any]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as fh:
        fh.write(f"candidate_id={candidate['candidate_id']}\n")
        fh.write(f"pair_id={candidate['pair_id']}\n")
        fh.write(f"pantheon_sha={candidate['pantheon_sha']}\n")
        fh.write(f"execute_plans_sha={candidate['execute_plans_sha']}\n")
        fh.write(f"profile={candidate['profile']}\n")
        fh.write(f"expires_at={candidate['expires_at']}\n")
        fh.write(f"source_mode={candidate['source_mode']}\n")


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
    pair_id: str | None = None,
    candidate_profile: str = "write-proof",
    candidate_out: str | Path | None = None,
    fe_base_url: str | None = None,
    fetch_fn: Callable[[str], dict[str, Any]] | None = None,
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
    if fe_base_url is not None and not (str(fe_base_url).startswith("https://") or str(fe_base_url).startswith("http://")):
        raise ControllerError("FE base URL must use HTTPS or HTTP")

    state_machine = ProofStateMachine("CREATED")

    candidate = create_candidate_record(
        pantheon_sha=backend_sha,
        execute_plans_sha=frontend_sha,
        candidate_id=release_candidate_id,
        pair_id=pair_id,
        profile=candidate_profile,
        source_mode="reconcile-only",
    )
    validate_candidate_override(
        candidate,
        {
            "frontend_sha": frontend_sha,
            "backend_sha": backend_sha,
            "release_candidate_id": release_candidate_id,
            "pair_id": pair_id,
        },
    )

    if candidate_out:
        cand_path = Path(candidate_out)
        cand_path.parent.mkdir(parents=True, exist_ok=True)
        cand_path.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    emit_github_outputs(candidate)

    # 1. Identity verification BEFORE any child dispatch
    state_machine.transition("IDENTITY_VERIFIED")
    served_verification = verify_served_identity(
        bff_base_url=bff_base_url,
        expected_candidate=candidate,
        fe_base_url=fe_base_url,
        fetch_fn=fetch_fn,
    )

    # 2. Write-proof active during proof / dispatch window
    state_machine.transition("WRITE_PROOF_ACTIVE")
    state_machine.transition("JOURNEYS_RUNNING")

    started_at = utc_now()
    gate_title = f"Release candidate {release_candidate_id}"

    try:
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
        state_machine.transition("PROOF_CAPTURED")
    finally:
        # Restore read-only profile across success, failure, cancellation, expiry
        candidate = restore_read_only_profile(candidate)
        state_machine.transition("READ_ONLY_RESTORED")
        if candidate_out:
            cand_path = Path(candidate_out)
            cand_path.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        emit_github_outputs(candidate)

    state_machine.transition("COMPLETE")

    return {
        "schema_version": "pantheon.cross-repo-release-controller-evidence.v1",
        "release_candidate_id": release_candidate_id,
        "candidate": candidate,
        "served_verification": served_verification,
        "proof_state": state_machine.state,
        "proof_history": state_machine.history,
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
    parser.add_argument("--fe-base-url", default=None)
    parser.add_argument("--release-candidate-id", required=True)
    parser.add_argument("--compatibility-manifest-sha256", required=True)
    parser.add_argument("--controller-run-id", required=True)
    parser.add_argument("--evidence-out", required=True)
    parser.add_argument("--candidate-out", default=None)
    parser.add_argument("--pair-id", default=None)
    parser.add_argument("--candidate-profile", default="write-proof")
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
            fe_base_url=args.fe_base_url,
            release_candidate_id=args.release_candidate_id,
            compatibility_manifest_sha256=args.compatibility_manifest_sha256,
            controller_run_id=args.controller_run_id,
            gate_timeout_seconds=args.gate_timeout_seconds,
            deploy_timeout_seconds=args.deploy_timeout_seconds,
            poll_seconds=args.poll_seconds,
            pair_id=args.pair_id,
            candidate_profile=args.candidate_profile,
            candidate_out=args.candidate_out,
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
