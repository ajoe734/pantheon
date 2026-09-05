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
import subprocess
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
PAIR_ID_RE = re.compile(r"^[0-9a-zA-Z._:-]{1,256}$")
RUN_ID_RE = re.compile(r"^[1-9][0-9]*$")
REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
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


def exact_pair_id(value: str, label: str = "pair ID") -> str:
    normalized = str(value or "").strip()
    if not PAIR_ID_RE.fullmatch(normalized):
        raise ControllerError(f"{label} must be a valid pair identifier")
    return normalized


def exact_run_id(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not RUN_ID_RE.fullmatch(normalized):
        raise ControllerError(f"{label} must be one positive integer run ID")
    return normalized


def exact_ref(value: str, label: str = "frontend ref") -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or not REF_RE.fullmatch(normalized)
        or normalized.startswith("/")
        or normalized.endswith("/")
        or ".." in normalized
        or "//" in normalized
        or normalized.startswith("-")
    ):
        raise ControllerError(f"{label} must be one exact branch ref")
    return normalized


def canonical_json_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded + b"\n").hexdigest()


def derive_pair_id(
    pantheon_sha: str | None = None,
    execute_plans_sha: str | None = None,
    *,
    manifest: dict[str, Any] | None = None,
) -> str | None:
    if manifest:
        raw = manifest.get("pairId") or manifest.get("pair_id")
        if raw:
            return exact_pair_id(raw, "served pair ID")
    return None


def create_candidate_record(
    *,
    pantheon_sha: str,
    execute_plans_sha: str,
    candidate_id: str | None = None,
    pair_id: str | None = None,
    profile: str = "read-only",
    expires_at: str | None = None,
    source_mode: str = "reconcile-only",
    ttl_seconds: int = 1800,
) -> dict[str, Any]:
    pantheon_sha = exact_sha(pantheon_sha, "backend SHA")
    execute_plans_sha = exact_sha(execute_plans_sha, "frontend SHA")
    norm_pair = exact_pair_id(pair_id, "pair ID") if pair_id is not None else None

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
        cid_payload: dict[str, Any] = {
            "execute_plans_sha": execute_plans_sha,
            "expires_at": expires_at,
            "pantheon_sha": pantheon_sha,
            "profile": profile,
            "source_mode": source_mode,
        }
        if norm_pair is not None:
            cid_payload["pair_id"] = norm_pair
        cid = canonical_json_sha256(cid_payload)

    return {
        "candidate_id": cid,
        "pantheon_sha": pantheon_sha,
        "execute_plans_sha": execute_plans_sha,
        "pair_id": norm_pair,
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
    if pair_id is not None and parent_candidate.get("pair_id") is not None:
        norm_pair = exact_pair_id(pair_id, "child pair ID")
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
    expected_fe_sha: str | None = None,
    expected_bff_manifest_sha: str | None = None,
    require_pair_id: bool = True,
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
    served_manifest = None
    if fe_base_url:
        if not (fe_base_url.startswith("https://") or fe_base_url.startswith("http://")):
            raise ControllerError("FE base URL must use HTTPS or HTTP")
        fe_deployment_url = f"{fe_base_url.rstrip('/')}/deployment.json"
        try:
            fe_data = fetcher(fe_deployment_url)
        except Exception as exc:
            raise ControllerError(f"served identity verification failed reaching frontend: {exc}") from exc

        served_manifest = fe_data
        observed_fe_sha = exact_sha(
            fe_data.get("frontendSha") or fe_data.get("commit") or (fe_data.get("frontend") or {}).get("commitSha") or "",
            "served frontend commit SHA",
        )
        target_fe_sha = expected_fe_sha if expected_fe_sha is not None else expected_candidate["execute_plans_sha"]
        if observed_fe_sha != target_fe_sha:
            raise ControllerError(
                f"served identity mismatch fails closed: FE served {observed_fe_sha} != candidate {target_fe_sha}"
            )

        manifest_bff_sha_raw = (
            fe_data.get("bffCommit")
            or fe_data.get("bffSourceCommitSha")
            or (fe_data.get("bff") or {}).get("sourceCommitSha")
        )
        if manifest_bff_sha_raw:
            observed_manifest_bff_sha = exact_sha(manifest_bff_sha_raw, "served frontend manifest BFF commit SHA")
            target_manifest_bff = expected_bff_manifest_sha if expected_bff_manifest_sha is not None else expected_candidate["pantheon_sha"]
            if observed_manifest_bff_sha != target_manifest_bff:
                raise ControllerError(
                    f"served identity mismatch fails closed: FE manifest BFF {observed_manifest_bff_sha} != candidate {target_manifest_bff}"
                )

        raw_pair_id = fe_data.get("pairId") or fe_data.get("pair_id") or ""
        if require_pair_id:
            if not raw_pair_id:
                raise ControllerError("served deployment manifest lacks pair ID")
            observed_pair_id = exact_pair_id(raw_pair_id, "served pair ID")
            if expected_candidate.get("pair_id") is not None and observed_pair_id != expected_candidate["pair_id"]:
                raise ControllerError(
                    f"served identity mismatch fails closed: served pair ID {observed_pair_id} != candidate {expected_candidate['pair_id']}"
                )
        elif raw_pair_id:
            observed_pair_id = exact_pair_id(raw_pair_id, "served pair ID")

    return {
        "status": "verified",
        "observed_bff_sha": observed_bff_sha,
        "observed_fe_sha": observed_fe_sha,
        "observed_pair_id": observed_pair_id,
        "served_manifest": served_manifest,
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
        fe_sha_raw = (
            served_manifest.get("frontendSha")
            or served_manifest.get("commit")
            or (served_manifest.get("frontend") or {}).get("commitSha")
        )
        fe_sha = exact_sha(fe_sha_raw or "", "served FE SHA")
        if fe_sha != restored["execute_plans_sha"]:
            raise ControllerError(
                f"read-only restoration verification mismatch: served FE={fe_sha} != candidate {restored['execute_plans_sha']}"
            )

        bff_sha_raw = (
            served_manifest.get("bffCommit")
            or served_manifest.get("bffSourceCommitSha")
            or (served_manifest.get("bff") or {}).get("sourceCommitSha")
        )
        if bff_sha_raw:
            bff_sha = exact_sha(bff_sha_raw, "served BFF SHA")
            if bff_sha != restored["pantheon_sha"]:
                raise ControllerError(
                    f"read-only restoration verification mismatch: served BFF={bff_sha} != candidate {restored['pantheon_sha']}"
                )

        raw_pair_id = (
            served_manifest.get("pairId")
            or served_manifest.get("pair_id")
        )
        if raw_pair_id:
            pair_id = exact_pair_id(raw_pair_id, "served pair ID")
            if restored.get("pair_id") is not None and pair_id != restored["pair_id"]:
                raise ControllerError(
                    f"read-only restoration verification mismatch: served pair ID {pair_id} != candidate {restored['pair_id']}"
                )
    return restored


def validate_bootstrap_identity(
    backend_sha: str,
    frontend_sha: str,
) -> tuple[str, str]:
    """Validate that bootstrap predecessor backend and frontend SHAs are exact 40-character lowercase hex commits."""
    norm_bff = exact_sha(backend_sha, "bootstrap predecessor backend SHA")
    norm_fe = exact_sha(frontend_sha, "bootstrap predecessor frontend SHA")
    return norm_bff, norm_fe


def check_ancestor_commits(
    backend_sha: str,
    frontend_sha: str,
    *,
    backend_dev_ref: str = "refs/remotes/origin/dev",
    frontend_dev_ref: str = "refs/remotes/origin/dev",
    backend_git_dir: str | Path | None = None,
    frontend_git_dir: str | Path | None = None,
    git_checker: Callable[[str, str, str | Path | None], bool] | None = None,
) -> None:
    """Verify that bootstrap predecessor SHAs are ancestors of their protected dev branch tips."""
    def _default_git_is_ancestor(commit: str, ref: str, git_dir: str | Path | None) -> bool:
        cmd = ["git"]
        if git_dir:
            cmd.extend(["-C", str(git_dir)])
        cmd.extend(["merge-base", "--is-ancestor", commit, ref])
        res = subprocess.run(cmd, capture_output=True)
        return res.returncode == 0

    checker = git_checker or _default_git_is_ancestor
    if not checker(backend_sha, backend_dev_ref, backend_git_dir):
        raise ControllerError(
            f"bootstrap predecessor backend commit {backend_sha} is not an ancestor of {backend_dev_ref}"
        )
    if not checker(frontend_sha, frontend_dev_ref, frontend_git_dir):
        raise ControllerError(
            f"bootstrap predecessor frontend commit {frontend_sha} is not an ancestor of {frontend_dev_ref}"
        )


def check_empty_host_prerequisite(
    fe_base_url: str,
    *,
    fetch_fn: Callable[[str], dict[str, Any]] | None = None,
) -> None:
    """Admit only an explicit HTTP 404 as the empty-host sentinel.

    Authentication failures, server errors, malformed JSON, TLS failures and
    timeouts are not evidence that a host is empty and therefore fail closed.
    """
    if not fe_base_url or not (fe_base_url.startswith("https://") or fe_base_url.startswith("http://")):
        raise ControllerError(
            f"empty-host bootstrap prerequisite failed closed: invalid fe_base_url: {fe_base_url!r}"
        )
    fetcher = fetch_fn or fetch_url_json
    fe_deployment_url = f"{fe_base_url.rstrip('/')}/deployment.json"
    try:
        fetcher(fe_deployment_url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return
        raise ControllerError(
            f"empty-host bootstrap prerequisite failed closed: deployment.json returned HTTP {exc.code}"
        ) from exc
    except Exception as exc:
        raise ControllerError(
            f"empty-host bootstrap prerequisite failed closed: deployment.json could not be verified: {exc}"
        ) from exc
    raise ControllerError(
        "repeated bootstrap is rejected: host already serves deployment.json"
    )


def verify_bootstrap_served_identity(
    *,
    bff_base_url: str,
    fe_base_url: str,
    expected_backend_sha: str,
    expected_frontend_sha: str,
    fetch_fn: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Verify that the bootstrapped predecessor pair has exact served BFF and FE identity readback in strict live read-only mode."""
    fetcher = fetch_fn or fetch_url_json
    # 1. Verify BFF version
    bff_version_url = f"{bff_base_url.rstrip('/')}/bff/version"
    try:
        bff_data = fetcher(bff_version_url)
    except Exception as exc:
        raise ControllerError(f"bootstrap served identity verification failed reaching BFF: {exc}") from exc

    observed_bff_sha = exact_sha(
        bff_data.get("source_commit_sha") or bff_data.get("commit") or "",
        "served BFF commit SHA",
    )
    if observed_bff_sha != expected_backend_sha:
        raise ControllerError(
            f"bootstrap served identity mismatch fails closed: BFF served {observed_bff_sha} != expected {expected_backend_sha}"
        )

    # 2. Verify FE deployment.json
    fe_deployment_url = f"{fe_base_url.rstrip('/')}/deployment.json"
    try:
        fe_data = fetcher(fe_deployment_url)
    except Exception as exc:
        raise ControllerError(f"bootstrap served identity verification failed reaching frontend: {exc}") from exc

    observed_fe_sha = exact_sha(
        fe_data.get("frontendSha") or fe_data.get("commit") or (fe_data.get("frontend") or {}).get("commitSha") or "",
        "served frontend commit SHA",
    )
    if observed_fe_sha != expected_frontend_sha:
        raise ControllerError(
            f"bootstrap served identity mismatch fails closed: FE served {observed_fe_sha} != expected {expected_frontend_sha}"
        )

    manifest_bff_sha_raw = (
        fe_data.get("bffCommit")
        or fe_data.get("bffSourceCommitSha")
        or (fe_data.get("bff") or {}).get("sourceCommitSha")
    )
    if manifest_bff_sha_raw:
        observed_manifest_bff_sha = exact_sha(manifest_bff_sha_raw, "served frontend manifest BFF commit SHA")
        if observed_manifest_bff_sha != expected_backend_sha:
            raise ControllerError(
                f"bootstrap served identity mismatch fails closed: FE manifest BFF {observed_manifest_bff_sha} != expected {expected_backend_sha}"
            )

    # Must be read-only profile
    profile = fe_data.get("profile") or fe_data.get("deploymentProfile")
    if profile != "read-only":
        raise ControllerError(
            f"bootstrap served identity must be in read-only profile, got {profile!r}"
        )

    # Must be strict live read-only buildMode if present
    build_mode = fe_data.get("buildMode")
    if isinstance(build_mode, dict):
        if (
            build_mode.get("VITE_BFF_MODE") != "live"
            or build_mode.get("VITE_BFF_FALLBACK") != "strict"
            or str(build_mode.get("VITE_BFF_REAL_WRITES", "")).lower() != "false"
            or str(build_mode.get("VITE_BFF_ALLOW_DEV_STUB_WRITES", "")).lower() != "false"
        ):
            raise ControllerError("bootstrap frontend must be deployed in strict live read-only mode")

    return {
        "status": "verified",
        "observed_bff_sha": observed_bff_sha,
        "observed_fe_sha": observed_fe_sha,
        "profile": "read-only",
        "verified_at": utc_now(),
        "manifest": fe_data,
    }


def admit_bootstrap_predecessor(
    *,
    backend_sha: str,
    frontend_sha: str,
    fe_base_url: str | None = None,
    backend_dev_ref: str = "refs/remotes/origin/dev",
    frontend_dev_ref: str = "refs/remotes/origin/dev",
    backend_git_dir: str | Path | None = None,
    frontend_git_dir: str | Path | None = None,
    fetch_fn: Callable[[str], dict[str, Any]] | None = None,
    git_checker: Callable[[str, str, str | Path | None], bool] | None = None,
    compat_checker: Callable[[str, str], bool] | None = None,
) -> dict[str, Any]:
    """Execute governed one-time bootstrap admission for an empty dev host."""
    # 1. Repeated bootstrap check: if host already has deployment.json, fail closed
    if fe_base_url:
        check_empty_host_prerequisite(fe_base_url, fetch_fn=fetch_fn)

    # 2. Malformed identity check
    norm_bff, norm_fe = validate_bootstrap_identity(backend_sha, frontend_sha)

    # 3. Ancestor commits check
    check_ancestor_commits(
        norm_bff,
        norm_fe,
        backend_dev_ref=backend_dev_ref,
        frontend_dev_ref=frontend_dev_ref,
        backend_git_dir=backend_git_dir,
        frontend_git_dir=frontend_git_dir,
        git_checker=git_checker,
    )

    # 4. Compatibility check
    if compat_checker and not compat_checker(norm_bff, norm_fe):
        raise ControllerError(
            f"bootstrap predecessor pair {norm_bff}+{norm_fe} failed compatibility admission"
        )

    # Generate bootstrap candidate record
    candidate = create_candidate_record(
        pantheon_sha=norm_bff,
        execute_plans_sha=norm_fe,
        profile="read-only",
    )

    return {
        "schema_version": "pantheon.dev-release-bootstrap-admission.v1",
        "bootstrap_admission_status": "admitted",
        "predecessor_backend_sha": norm_bff,
        "predecessor_frontend_sha": norm_fe,
        "candidate": candidate,
        "admitted_at": utc_now(),
    }


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
        if candidate.get("pair_id") is not None:
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
    head_branch: str = FRONTEND_BRANCH


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

    def list_runs(self, workflow: str, *, branch: str = FRONTEND_BRANCH) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode(
            {"branch": branch, "event": "workflow_dispatch", "per_page": 100}
        )
        payload = self.request(
            "GET",
            f"/repos/{self.repository}/actions/workflows/{workflow}/runs?{query}",
        )
        runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
        if not isinstance(runs, list):
            raise ControllerError(f"{workflow} run listing has no workflow_runs array")
        return [run for run in runs if isinstance(run, dict)]

    def dispatch(self, workflow: str, inputs: dict[str, str], *, ref: str = FRONTEND_BRANCH) -> None:
        self.request(
            "POST",
            f"/repos/{self.repository}/actions/workflows/{workflow}/dispatches",
            {"ref": ref, "inputs": inputs},
        )

    def get_run(self, run_id: int) -> dict[str, Any]:
        payload = self.request(
            "GET",
            f"/repos/{self.repository}/actions/runs/{run_id}",
        )
        if not isinstance(payload, dict):
            raise ControllerError(f"run {run_id} response is not an object")
        return payload

    def get_ref(self, ref: str) -> str:
        normalized = str(ref).removeprefix("refs/heads/").removeprefix("heads/")
        if not normalized:
            raise ControllerError("execute-plans controller ref is empty")
        payload = self.request(
            "GET",
            f"/repos/{self.repository}/git/ref/heads/{normalized}",
        )
        value = str((payload or {}).get("object", {}).get("sha") or "").lower()
        return exact_sha(value, "execute-plans controller SHA")


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
        and run.get("head_branch") == expected.head_branch
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
        run.get("id")
        for run in client.list_runs(expected.workflow, branch=FRONTEND_BRANCH)
        if run.get("id")
    }
    # Workflow definitions are dispatched from the trusted execute-plans/dev
    # controller.  ``frontend_ref`` is the candidate source ref carried as an
    # input and verified by the workflow; dispatching the candidate branch
    # itself would execute unmerged workflow code and recreate the deadlock.
    client.dispatch(expected.workflow, inputs, ref=FRONTEND_BRANCH)
    deadline = time.monotonic() + timeout_seconds
    selected: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        matches = [
            run
            for run in client.list_runs(expected.workflow, branch=FRONTEND_BRANCH)
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
    frontend_ref: str = FRONTEND_BRANCH,
    backend_sha: str,
    bff_base_url: str,
    release_candidate_id: str,
    compatibility_manifest_sha256: str,
    controller_run_id: str,
    gate_timeout_seconds: int,
    deploy_timeout_seconds: int,
    poll_seconds: float,
    pair_id: str | None = None,
    candidate_profile: str = "read-only",
    candidate_out: str | Path | None = None,
    fe_base_url: str | None = None,
    predecessor_fe_sha: str | None = None,
    predecessor_bff_sha: str | None = None,
    fetch_fn: Callable[[str], dict[str, Any]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    frontend_sha = exact_sha(frontend_sha, "frontend SHA")
    frontend_ref = exact_ref(frontend_ref)
    controller_sha = client.get_ref("heads/dev")
    candidate_ref_sha = client.get_ref(frontend_ref)
    if candidate_ref_sha != frontend_sha:
        raise ControllerError(
            f"frontend ref {frontend_ref} does not point to the exact frontend SHA"
        )
    backend_sha = exact_sha(backend_sha, "backend SHA")
    if predecessor_fe_sha is not None:
        predecessor_fe_sha = exact_sha(predecessor_fe_sha, "predecessor frontend SHA")
    if predecessor_bff_sha is not None:
        predecessor_bff_sha = exact_sha(predecessor_bff_sha, "predecessor backend SHA")
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

    pre_dispatch_verification: dict[str, Any] | None = None
    post_switch_verification: dict[str, Any] | None = None
    gate_run: dict[str, Any] | None = None
    deploy_run: dict[str, Any] | None = None
    started_at = utc_now()
    try:
        if candidate_out:
            cand_path = Path(candidate_out)
            cand_path.parent.mkdir(parents=True, exist_ok=True)
            cand_path.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        emit_github_outputs(candidate)

        # 1. Identity verification BEFORE any child dispatch: verify served BFF is live and matching
        pre_dispatch_verification = verify_served_identity(
            bff_base_url=bff_base_url,
            expected_candidate=candidate,
            fe_base_url=fe_base_url if predecessor_fe_sha else None,
            fetch_fn=fetch_fn,
            expected_fe_sha=predecessor_fe_sha,
            expected_bff_manifest_sha=predecessor_bff_sha,
            require_pair_id=False if predecessor_fe_sha else True,
        )
        state_machine.transition("IDENTITY_VERIFIED")

        gate_title = f"Release candidate {release_candidate_id}"

        gate = dispatch_and_wait(
            client,
            expected=ExpectedRun(
                workflow=GATE_WORKFLOW,
                title=gate_title,
                head_sha=controller_sha,
                head_branch=FRONTEND_BRANCH,
            ),
            inputs={
                "fe_sha": frontend_sha,
                "frontend_ref": frontend_ref,
                "bff_sha": backend_sha,
                "bff_base_url": bff_base_url,
                "release_candidate_id": release_candidate_id,
                "compatibility_manifest_sha256": compatibility_manifest_sha256,
                "release_controller_run_id": controller_run_id,
                "soft_fail": "false",
            },
            timeout_seconds=gate_timeout_seconds,
            poll_seconds=poll_seconds,
            sleep=sleep,
        )
        gate_run = gate
        gate_run_id = str(validate_run(
            gate,
            ExpectedRun(GATE_WORKFLOW, gate_title, controller_sha, FRONTEND_BRANCH),
        ))

        deploy_title = f"Deploy release candidate {release_candidate_id}"
        deploy_inputs = {
            "candidate_sha": frontend_sha,
            "frontend_ref": frontend_ref,
            "gate_run_id": gate_run_id,
            "release_candidate_id": release_candidate_id,
            "compatibility_manifest_sha256": compatibility_manifest_sha256,
            "release_controller_run_id": controller_run_id,
            "deployment_profile": "read-only",
            "proof_window_ack": "false",
            "emergency_override": "false",
            "rollback_drill": "false",
            "override_reason": "",
        }
        # Empty-host bootstrap is deliberately an explicit read-only exception:
        # the parent deploy job owns the transaction while its normal
        # coordinator job is not yet schedulable. Keep ordinary deployments'
        # exact input contract unchanged.
        if os.environ.get("PANTHEON_DEV_BOOTSTRAP_PREDECESSOR", "").strip().lower() == "true":
            deploy_inputs["bootstrap_predecessor"] = "true"

        deploy = dispatch_and_wait(
            client,
            expected=ExpectedRun(
                workflow=DEPLOY_WORKFLOW,
                title=deploy_title,
                head_sha=controller_sha,
                head_branch=FRONTEND_BRANCH,
            ),
            inputs=deploy_inputs,
            timeout_seconds=deploy_timeout_seconds,
            poll_seconds=poll_seconds,
            sleep=sleep,
        )
        deploy_run = deploy

        # 2. Post-switch verification: verify served FE, BFF, and pair identity after atomic deploy switch
        if fe_base_url:
            post_switch_verification = verify_served_identity(
                bff_base_url=bff_base_url,
                expected_candidate=candidate,
                fe_base_url=fe_base_url,
                fetch_fn=fetch_fn,
            )
            if post_switch_verification.get("observed_pair_id"):
                candidate["pair_id"] = post_switch_verification["observed_pair_id"]
    finally:
        # Restore read-only profile across success, failure, cancellation, expiry, probe error
        # Refetch the served manifest for restoration verification
        refetched_manifest = None
        if fe_base_url:
            try:
                refetched_manifest = (fetch_fn or fetch_url_json)(f"{fe_base_url.rstrip('/')}/deployment.json")
            except Exception:
                refetched_manifest = None

        if sys.exc_info()[0] is None and post_switch_verification is not None and refetched_manifest is not None:
            candidate = restore_read_only_profile(candidate, served_manifest=refetched_manifest)
        else:
            candidate = restore_read_only_profile(candidate)

        state_machine.transition("READ_ONLY_RESTORED")
        if candidate_out:
            cand_path = Path(candidate_out)
            cand_path.parent.mkdir(parents=True, exist_ok=True)
            cand_path.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        emit_github_outputs(candidate)

    return {
        "schema_version": "pantheon.cross-repo-release-controller-evidence.v1",
        "release_candidate_id": release_candidate_id,
        "candidate": candidate,
        "pre_dispatch_verification": pre_dispatch_verification,
        "served_verification": post_switch_verification or pre_dispatch_verification,
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
            "source_ref": frontend_ref,
            "commit": frontend_sha,
        },
        "integration_gate": {
            "run_id": str(gate_run["id"]) if gate_run else None,
            "url": gate_run.get("html_url") if gate_run else None,
            "conclusion": gate_run.get("conclusion") if gate_run else None,
        },
        "frontend_deploy": {
            "run_id": str(deploy_run["id"]) if deploy_run else None,
            "url": deploy_run.get("html_url") if deploy_run else None,
            "conclusion": deploy_run.get("conclusion") if deploy_run else None,
        },
        "started_at": started_at,
        "completed_at": utc_now(),
        "outcome": "accepted",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-sha", required=True)
    parser.add_argument("--frontend-ref", default=FRONTEND_BRANCH)
    parser.add_argument("--backend-sha", required=True)
    parser.add_argument("--bff-base-url", required=True)
    parser.add_argument("--fe-base-url", default=None)
    parser.add_argument("--release-candidate-id", required=True)
    parser.add_argument("--compatibility-manifest-sha256", required=True)
    parser.add_argument("--controller-run-id", required=True)
    parser.add_argument("--evidence-out", required=True)
    parser.add_argument("--candidate-out", default=None)
    parser.add_argument("--pair-id", default=None)
    parser.add_argument("--candidate-profile", default="read-only")
    parser.add_argument("--predecessor-fe-sha", default=None)
    parser.add_argument("--predecessor-bff-sha", default=None)
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
            frontend_ref=args.frontend_ref,
            backend_sha=args.backend_sha,
            bff_base_url=args.bff_base_url,
            fe_base_url=args.fe_base_url,
            predecessor_fe_sha=args.predecessor_fe_sha,
            predecessor_bff_sha=args.predecessor_bff_sha,
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
