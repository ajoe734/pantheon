#!/usr/bin/env python3
"""Download a fixed Actions artifact and verify its external ZIP seal.

Only non-secret release metadata is transported. Image archives stay on the VM.
The workflow supplies the upload action's ID and digest through job outputs or
resolves a named prior run attempt's artifact metadata from the Actions API;
neither an unverified filename nor a checksum inside the archive grants trust.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import select
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .capture_dev_artifact_baseline import (
        ARTIFACT_ROOT,
        IMPLEMENTATIONS,
        INSTALLER,
        VM_HOME,
        CaptureError,
        digest,
        encoded,
        matches,
        read_implementation,
        seal_result,
        unique_object,
    )
    from .dev_artifact_compensation_evidence import (
        ENV_PREFIX,
        PREFIX as READBACK_PREFIX,
        mixed_summary,
        private_bytes,
        private_write,
        validate_readback,
    )
    from .dev_candidate_receipt import private_directory, validate_receipt
    from .dev_release_artifacts import failure_record, remote_failure_record
except ImportError:
    from capture_dev_artifact_baseline import (
        ARTIFACT_ROOT,
        IMPLEMENTATIONS,
        INSTALLER,
        VM_HOME,
        CaptureError,
        digest,
        encoded,
        matches,
        read_implementation,
        seal_result,
        unique_object,
    )
    from dev_artifact_compensation_evidence import (
        ENV_PREFIX,
        PREFIX as READBACK_PREFIX,
        mixed_summary,
        private_bytes,
        private_write,
        validate_readback,
    )
    from dev_candidate_receipt import private_directory, validate_receipt
    from dev_release_artifacts import failure_record, remote_failure_record


REPOSITORY = "ajoe734/pantheon"
MAX_ARCHIVE = 2 * 1024 * 1024
FILES = {"baseline": {"artifact-baseline.json", "SHA256SUMS"},
         "candidate": {"candidate-receipt.json"}}


class RestoreExecutionError(CaptureError):
    """Whole-stack baseline restore failed on the remote VM."""

    def __init__(self, message: str, *, failure: dict | None = None,
                 mixed: str | None = None, exit_code: int = 75):
        super().__init__(message)
        self.failure = failure
        self.mixed = mixed
        self.exit_code = exit_code if type(exit_code) is int and 1 <= exit_code <= 255 else 75


def github(path: str) -> bytes:
    # gh owns authenticated API redirect handling. Never log its stderr (which
    # could include a signed download URL) or expose a token in command args.
    environment = dict(os.environ)
    if not environment.get("GITHUB_TOKEN"):
        raise CaptureError("authenticated Actions reader is unavailable")
    for name in ("GH_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        environment.pop(name, None)
    environment.update(GH_HOST="github.com", GH_PROMPT_DISABLED="1")
    with subprocess.Popen(["gh", "api", "--hostname", "github.com", "--method", "GET", path],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment) as process:
        data = bytearray()
        deadline = time.monotonic() + 60
        try:
            while True:
                if time.monotonic() >= deadline:
                    raise CaptureError("Actions evidence transport timed out")
                if not select.select([process.stdout], [], [], .1)[0]:
                    continue
                chunk = os.read(process.stdout.fileno(), min(65536, MAX_ARCHIVE + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_ARCHIVE:
                    raise CaptureError("Actions response exceeds the evidence size bound")
            if process.wait(timeout=max(.1, deadline - time.monotonic())):
                raise CaptureError("Actions evidence transport failed")
            return bytes(data)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def unpack(raw: bytes, *, expected_digest: str, kind: str) -> dict[str, bytes]:
    matches(expected_digest, r"[0-9a-f]{64}")
    if kind not in FILES or len(raw) > MAX_ARCHIVE or hashlib.sha256(raw).hexdigest() != expected_digest:
        raise CaptureError("Actions archive differs from the external upload seal")
    result = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) != len(FILES[kind]) or {item.filename for item in entries} != FILES[kind]:
            raise CaptureError("Actions archive has unexpected or duplicate entries")
        for item in entries:
            mode = item.external_attr >> 16
            limit = 1024 * 1024 if item.filename == "artifact-baseline.json" else 65536
            if (item.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG) or
                item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) or
                item.flag_bits & 1 or item.file_size <= 0 or item.file_size > limit):
                raise CaptureError("Actions archive entry is not bounded regular evidence")
            with archive.open(item) as stream:
                data = stream.read(limit + 1)
            if len(data) != item.file_size or len(data) > limit:
                raise CaptureError("Actions evidence entry size differs")
            result[item.filename] = data
    return result


def resolve_run_artifact(*, kind: str, run_id: str, attempt: str = "1", api=github) -> dict:
    for value in (run_id, attempt):
        matches(value, r"[1-9][0-9]{0,19}")
    if kind not in FILES:
        raise CaptureError("unknown evidence artifact kind")
    listing_raw = api(f"repos/{REPOSITORY}/actions/runs/{run_id}/artifacts")
    if len(listing_raw) > 65536:
        raise CaptureError("Actions run artifact listing exceeds its bound")
    listing = json.loads(listing_raw, object_pairs_hook=unique_object)
    if not isinstance(listing, dict) or not isinstance(listing.get("artifacts"), list):
        raise CaptureError("Actions run artifact listing is invalid")
    expected_name = f"pantheon-dev-artifact-{kind}-{run_id}-{attempt}"
    matched = [item for item in listing["artifacts"]
               if isinstance(item, dict) and item.get("name") == expected_name]
    if len(matched) != 1:
        raise CaptureError("Actions run does not have a unique matching attempt artifact")
    metadata = matched[0]
    workflow = metadata.get("workflow_run")
    raw_digest = metadata.get("digest")
    if (type(metadata.get("id")) is not int or metadata["id"] <= 0 or
        metadata.get("name") != expected_name or metadata.get("expired") is not False or
        not isinstance(raw_digest, str) or not raw_digest.startswith("sha256:") or
        type(metadata.get("size_in_bytes")) is not int or not 0 < metadata["size_in_bytes"] <= MAX_ARCHIVE or
        not isinstance(workflow, dict) or type(workflow.get("id")) is not int or workflow["id"] != int(run_id)):
        raise CaptureError("Actions artifact is not the exact prior-run attempt upload")
    matches(raw_digest.removeprefix("sha256:"), r"[0-9a-f]{64}")
    return metadata


def verify_baseline_files(files: dict[str, bytes], *, run_id: str, attempt: str) -> tuple[str, dict]:
    baseline_raw = files["artifact-baseline.json"]
    manifest_sha256 = hashlib.sha256(baseline_raw).hexdigest()
    sums_text = files["SHA256SUMS"].decode("utf-8")
    if sums_text != f"{manifest_sha256}  artifact-baseline.json\n":
        raise CaptureError("baseline SHA256SUMS does not match artifact-baseline.json")
    manifest = json.loads(baseline_raw, object_pairs_hook=unique_object)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("identity"), dict):
        raise CaptureError("baseline manifest identity is invalid")
    identity = manifest["identity"]
    if identity.get("run_id") != run_id or identity.get("attempt") != attempt:
        raise CaptureError("baseline manifest identity does not match requested run and attempt")
    folder = ARTIFACT_ROOT / f"baseline-{identity['run_id']}-{identity['attempt']}-{identity.get('candidate_id', '')}"
    sealed, _ = seal_result(encoded({
        "manifest_path": str(folder / "manifest.json"),
        "manifest_sha256": manifest_sha256,
        "manifest": manifest,
    }), identity)
    if sealed != baseline_raw:
        raise CaptureError("baseline bytes differ from sealed bytes")
    return manifest_sha256, manifest


def verify_candidate_files(files: dict[str, bytes], *, run_id: str, attempt: str,
                           baseline_manifest: dict | None = None,
                           baseline_manifest_sha256: str | None = None) -> tuple[str, dict]:
    receipt_raw = files["candidate-receipt.json"]
    receipt = json.loads(receipt_raw, object_pairs_hook=unique_object)
    if not isinstance(receipt, dict):
        raise CaptureError("candidate receipt is invalid")
    inner = receipt.get("candidate_image_manifest")
    identity = inner.get("identity") if isinstance(inner, dict) else receipt.get("identity")
    if not isinstance(identity, dict):
        raise CaptureError("candidate receipt identity is invalid")
    if identity.get("run_id") != run_id or identity.get("attempt") != attempt:
        raise CaptureError("candidate receipt identity does not match requested run and attempt")
    if encoded(receipt) != receipt_raw:
        raise CaptureError("candidate receipt is not canonical JSON")
    candidate_sha256 = matches(str(receipt.get("candidate_image_manifest_sha256") or ""), r"[0-9a-f]{64}")
    if baseline_manifest is not None and baseline_manifest_sha256 is not None:
        context = {
            "schema_version": "pantheon.dev-candidate-receipt-context.v1",
            "identity": baseline_manifest["identity"],
            "baseline_manifest_sha256": baseline_manifest_sha256,
            "guard_lease_id": baseline_manifest["capture_lease_id"],
        }
        if validate_receipt(receipt, context) != candidate_sha256:
            raise CaptureError("candidate receipt differs from its externally trusted seal")
    return candidate_sha256, receipt


def _write_files(output_dir: Path, files: dict[str, bytes]) -> None:
    if not output_dir.is_absolute() or output_dir.resolve() != output_dir:
        raise CaptureError("artifact destination is not canonical")
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_directory(output_dir)
    for name, data in files.items():
        fd = os.open(output_dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(output_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fetch(*, kind: str, artifact_id: str | None = None, expected_digest: str | None = None,
          run_id: str, attempt: str = "1", output_dir: Path, api=github) -> dict[str, str]:
    if (artifact_id is None) != (expected_digest is None):
        raise CaptureError("Actions artifact ID and digest must be provided together")
    for value in (run_id, attempt):
        matches(value, r"[1-9][0-9]{0,19}")
    if kind not in FILES:
        raise CaptureError("unknown evidence artifact kind")
    discovered = artifact_id is None and expected_digest is None
    if discovered:
        metadata = resolve_run_artifact(kind=kind, run_id=run_id, attempt=attempt, api=api)
        artifact_id = str(metadata["id"])
        expected_digest = metadata["digest"].removeprefix("sha256:")
        endpoint = f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}"
    else:
        assert artifact_id is not None and expected_digest is not None
        matches(artifact_id, r"[1-9][0-9]{0,19}")
        matches(expected_digest, r"[0-9a-f]{64}")
        endpoint = f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}"
        metadata_raw = api(endpoint)
        if len(metadata_raw) > 65536:
            raise CaptureError("Actions metadata exceeds its bound")
        metadata = json.loads(metadata_raw, object_pairs_hook=unique_object)
        if not isinstance(metadata, dict):
            raise CaptureError("Actions artifact metadata is invalid")
        workflow = metadata.get("workflow_run")
        expected_name = f"pantheon-dev-artifact-{kind}-{run_id}-{attempt}"
        if (type(metadata.get("id")) is not int or metadata["id"] != int(artifact_id) or
            metadata.get("name") != expected_name or metadata.get("expired") is not False or
            metadata.get("digest") != "sha256:" + expected_digest or
            type(metadata.get("size_in_bytes")) is not int or not 0 < metadata["size_in_bytes"] <= MAX_ARCHIVE or
            not isinstance(workflow, dict) or type(workflow.get("id")) is not int or workflow["id"] != int(run_id)):
            raise CaptureError("Actions artifact is not the exact same-run upload")
    raw = api(endpoint + "/zip")
    if len(raw) != metadata["size_in_bytes"]:
        raise CaptureError("Actions archive length differs from authenticated metadata")
    files = unpack(raw, expected_digest=expected_digest, kind=kind)
    extra: dict[str, str] = {}
    if discovered:
        if kind == "baseline":
            manifest_sha256, manifest = verify_baseline_files(files, run_id=run_id, attempt=attempt)
            extra = {
                "manifest_path": str(output_dir / "artifact-baseline.json"),
                "manifest_sha256": manifest_sha256,
                "previous_backend_sha": manifest["identity"]["previous_backend_sha"],
                "previous_frontend_sha": manifest["identity"]["previous_frontend_sha"],
            }
        else:
            candidate_sha256, _ = verify_candidate_files(files, run_id=run_id, attempt=attempt)
            extra = {
                "receipt_file": str(output_dir / "candidate-receipt.json"),
                "candidate_image_manifest_sha256": candidate_sha256,
            }
    _write_files(output_dir, files)
    return {"artifact_id": artifact_id, "artifact_sha256": expected_digest,
            "run_id": run_id, "attempt": attempt, "kind": kind, "output_dir": str(output_dir), **extra}


def _read_regular_file(path: Path, limit: int = 1024 * 1024) -> bytes:
    if not path.is_absolute() or path.resolve() != path or path.is_symlink() or not path.is_file():
        raise CaptureError("input file must be a canonical regular file")
    raw = path.read_bytes()
    if not raw or len(raw) > limit:
        raise CaptureError("input file size exceeds bound")
    return raw


def parse_accepted_deployment_pair(deployment_manifest: dict, *, expected_bff_url: str | None = None) -> tuple[str, str]:
    if not isinstance(deployment_manifest, dict):
        raise CaptureError("hosted deployment.json must be a JSON object")
    if (deployment_manifest.get("schemaVersion") != 1 or
        deployment_manifest.get("app") != "execute-plans" or
        deployment_manifest.get("repository") != "ajoe734/execute-plans" or
        deployment_manifest.get("sourceBranch") != "dev" or
        deployment_manifest.get("bffCommitEvidence") is not True or
        deployment_manifest.get("deploymentState") != "accepted"):
        raise CaptureError("hosted deployment.json is not an accepted Pantheon dev release")
    if expected_bff_url is not None:
        bff_section = deployment_manifest.get("bff")
        if not isinstance(bff_section, dict) or str(bff_section.get("baseUrl") or "").rstrip("/") != expected_bff_url.rstrip("/"):
            raise CaptureError("hosted deployment.json is not bound to the expected BFF endpoint")
    backend_claims = [deployment_manifest[key] for key in ("bffCommit", "bffSourceCommitSha") if key in deployment_manifest]
    if "bff" in deployment_manifest:
        if not isinstance(deployment_manifest["bff"], dict) or "sourceCommitSha" not in deployment_manifest["bff"]:
            raise CaptureError("hosted deployment.json has invalid nested bff release identity")
        backend_claims.append(deployment_manifest["bff"]["sourceCommitSha"])
    frontend_claims = [deployment_manifest[key] for key in ("frontendSha", "commit") if key in deployment_manifest]
    if "frontend" in deployment_manifest:
        if not isinstance(deployment_manifest["frontend"], dict) or "commitSha" not in deployment_manifest["frontend"]:
            raise CaptureError("hosted deployment.json has invalid nested frontend release identity")
        frontend_claims.append(deployment_manifest["frontend"]["commitSha"])
    if not backend_claims or len(set(backend_claims)) != 1 or not isinstance(backend_claims[0], str):
        raise CaptureError("hosted deployment.json lacks a unique backend release identity")
    if not frontend_claims or len(set(frontend_claims)) != 1 or not isinstance(frontend_claims[0], str):
        raise CaptureError("hosted deployment.json lacks a unique frontend release identity")
    backend_sha = matches(backend_claims[0], r"[0-9a-f]{40}")
    frontend_sha = matches(frontend_claims[0], r"[0-9a-f]{40}")
    return backend_sha, frontend_sha


def _git_is_ancestor(repo_root: Path, commit_sha: str, ref: str = "refs/remotes/origin/dev") -> bool:
    matches(commit_sha, r"[0-9a-f]{40}")
    if not repo_root.is_absolute() or repo_root.resolve() != repo_root or not repo_root.is_dir():
        raise CaptureError("controller checkout is not canonical")
    check_ref = ref
    probe = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--verify", "--quiet", check_ref],
                           capture_output=True, text=True, timeout=15)
    if probe.returncode != 0:
        check_ref = "HEAD"
    result = subprocess.run(["git", "-C", str(repo_root), "merge-base", "--is-ancestor", commit_sha, check_ref],
                            capture_output=True, text=True, timeout=15)
    return result.returncode == 0


def verify_restore_accepted_baseline_preconditions(
    *,
    baseline_manifest: dict,
    deployment_manifest: dict,
    bff_version: dict,
    repo_root: Path | None = None,
    expected_bff_url: str | None = None,
    is_ancestor=None,
) -> dict[str, str]:
    if not isinstance(baseline_manifest, dict) or not isinstance(baseline_manifest.get("identity"), dict):
        raise CaptureError("baseline manifest lacks identity")
    identity = baseline_manifest["identity"]
    previous_backend_sha = matches(str(identity.get("previous_backend_sha") or ""), r"[0-9a-f]{40}")
    previous_frontend_sha = matches(str(identity.get("previous_frontend_sha") or ""), r"[0-9a-f]{40}")
    frontend = baseline_manifest.get("frontend")
    if (not isinstance(frontend, dict) or
        frontend.get("backend_sha") != previous_backend_sha or
        frontend.get("frontend_sha") != previous_frontend_sha):
        raise CaptureError("baseline manifest frontend pair does not match previous identity")
    accepted_backend_sha, accepted_frontend_sha = parse_accepted_deployment_pair(
        deployment_manifest, expected_bff_url=expected_bff_url
    )
    if (previous_backend_sha, previous_frontend_sha) != (accepted_backend_sha, accepted_frontend_sha):
        raise CaptureError("baseline previous pair differs from hosted deployment.json accepted pair")
    if not isinstance(bff_version, dict):
        raise CaptureError("live /bff/version response must be a JSON object")
    live_backend_sha = bff_version.get("source_commit_sha")
    if not isinstance(live_backend_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", live_backend_sha):
        raise CaptureError("live /bff/version lacks an exact 40-hex source_commit_sha")
    if live_backend_sha == accepted_backend_sha:
        raise CaptureError("live /bff/version already matches the accepted backend; no drift to restore")
    checker = is_ancestor
    if checker is None:
        if repo_root is None:
            raise CaptureError("repo_root or is_ancestor is required to verify Pantheon dev containment")
        checker = lambda sha: _git_is_ancestor(repo_root, sha)
    if not checker(live_backend_sha):
        raise CaptureError("live /bff/version source_commit_sha is not contained in Pantheon dev")
    if not checker(accepted_backend_sha):
        raise CaptureError("accepted backend SHA is not contained in Pantheon dev")
    return {
        "accepted_backend_sha": accepted_backend_sha,
        "accepted_frontend_sha": accepted_frontend_sha,
        "drifted_backend_sha": live_backend_sha,
        "candidate_backend_sha": matches(str(identity.get("candidate_backend_sha") or ""), r"[0-9a-f]{40}"),
        "candidate_frontend_sha": matches(str(identity.get("candidate_frontend_sha") or ""), r"[0-9a-f]{40}"),
        "candidate_id": matches(str(identity.get("candidate_id") or ""), r"[0-9a-f]{64}"),
        "controller_sha": matches(str(identity.get("controller_sha") or ""), r"[0-9a-f]{40}"),
        "run_id": matches(str(identity.get("run_id") or ""), r"[1-9][0-9]{0,19}"),
        "attempt": matches(str(identity.get("attempt") or ""), r"[1-9][0-9]{0,19}"),
    }


def _extract_shell_function(script_text: str, name: str) -> str:
    marker = f"{name}() {{"
    start = script_text.index(marker)
    following = re.search(r"\n[A-Za-z_][A-Za-z_0-9]*\(\) \{", script_text[start:])
    if following is None:
        raise CaptureError(f"shell function {name} boundary not found")
    chunk = script_text[start:start + following.start()]
    return chunk[:chunk.rindex("\n}\n") + 3]


def build_restore_remote_script(
    *,
    repo_root: Path,
    current_controller_sha: str,
    implementations: dict[str, bytes],
    identity: dict[str, str],
    manifest_path: str,
    manifest_sha256: str,
    candidate_image_manifest_path: str,
    candidate_image_manifest_sha256: str,
    compose_file: str,
    env: dict[str, str],
    artifact_root: Path | None = None,
    vm_source_repo: Path | None = None,
) -> bytes:
    effective_artifact_root = ARTIFACT_ROOT if artifact_root is None else artifact_root
    effective_source_repo = (VM_HOME / "pantheon") if vm_source_repo is None else vm_source_repo
    deploy_script_path = repo_root / "scripts" / "deploy_nonprod_vm.sh"
    deploy_text = _read_regular_file(deploy_script_path, 512 * 1024).decode("utf-8")
    memory_fn = _extract_shell_function(deploy_text, "memory_llm_bridge_env")
    runtime_fn = _extract_shell_function(deploy_text, "with_dev_bff_runtime_env")
    run_driver_fn = _extract_shell_function(deploy_text, "run_dev_artifact_driver")
    # Authenticate the current controller directory while still passing the
    # baseline's original identity controller_sha to the driver CLI.
    run_driver_fn = run_driver_fn.replace(
        '"${PANTHEON_DEV_ARTIFACT_CONTROLLER_SHA}" <<\'ARTIFACT_PY\'',
        '"${PANTHEON_DEV_ARTIFACT_DRIVER_CONTROLLER_SHA}" <<\'ARTIFACT_PY\'',
        1,
    )
    driver_path = str(effective_artifact_root / "controllers" / current_controller_sha / IMPLEMENTATIONS[0])
    driver_sha256 = digest(implementations[IMPLEMENTATIONS[0]])
    library_sha256 = digest(implementations[IMPLEMENTATIONS[1]])
    install_payload = {
        "root": str(effective_artifact_root),
        "source": str(effective_source_repo),
        "controller_sha": current_controller_sha,
        "previous_backend_sha": identity["previous_backend_sha"],
        "files": {name: {"base64": base64.b64encode(raw).decode(), "sha256": digest(raw)}
                  for name, raw in implementations.items()},
    }
    install_failure = failure_record(subprocess.CalledProcessError(1, []), "install")
    install_failure["exit_code"] = 0
    failure_format = json.dumps(install_failure, sort_keys=True).replace('"exit_code": 0', '"exit_code": %d') + "\n"
    exports = {
        "PANTHEON_DEPLOY_ENV": "dev",
        "PANTHEON_DEPLOY_COMPONENT": "bff",
        "PANTHEON_DEPLOY_PROJECT_ID": env.get("GCP_DEPLOY_PROJECT_ID", "pantheon-dev-20260902"),
        "PANTHEON_DEPLOY_SHA": identity["previous_backend_sha"],
        "PANTHEON_DEV_ROLLBACK_BACKEND_SHA": identity["previous_backend_sha"],
        "PANTHEON_DEV_ARTIFACT_RESTORE": "true",
        "PANTHEON_DEV_ARTIFACT_SCOPE": "stack",
        "PANTHEON_DEV_ARTIFACT_TARGET": "baseline",
        "PANTHEON_DEV_ARTIFACT_DRIVER_CONTROLLER_SHA": current_controller_sha,
        "PANTHEON_DEV_ARTIFACT_CONTROLLER_SHA": identity["controller_sha"],
        "PANTHEON_DEV_ARTIFACT_DRIVER_PATH": driver_path,
        "PANTHEON_DEV_ARTIFACT_DRIVER_SHA256": driver_sha256,
        "PANTHEON_DEV_ARTIFACT_LIBRARY_SHA256": library_sha256,
        "PANTHEON_DEV_ARTIFACT_MANIFEST_PATH": manifest_path,
        "PANTHEON_DEV_ARTIFACT_MANIFEST_SHA256": manifest_sha256,
        "PANTHEON_DEV_ARTIFACT_COMPOSE_FILE": compose_file,
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_ID": identity["candidate_id"],
        "PANTHEON_DEV_ARTIFACT_RUN_ID": identity["run_id"],
        "PANTHEON_DEV_ARTIFACT_ATTEMPT": identity["attempt"],
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_BACKEND_SHA": identity["candidate_backend_sha"],
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_FRONTEND_SHA": identity["candidate_frontend_sha"],
        "PANTHEON_DEV_ARTIFACT_PREVIOUS_BACKEND_SHA": identity["previous_backend_sha"],
        "PANTHEON_DEV_ARTIFACT_PREVIOUS_FRONTEND_SHA": identity["previous_frontend_sha"],
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_PATH": candidate_image_manifest_path,
        "PANTHEON_DEV_ARTIFACT_CANDIDATE_IMAGE_MANIFEST_SHA256": candidate_image_manifest_sha256,
        "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": env.get("PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID", ""),
        "PANTHEON_DEV_BFF_PUBLIC_HOST": env.get("DEV_BFF_PUBLIC_HOST", "api.dev.mvl-cap.tw"),
        "PANTHEON_DEV_FE_PUBLIC_HOST": env.get("DEV_FE_PUBLIC_HOST", "app.dev.mvl-cap.tw"),
        "PANTHEON_DEV_BFF_AUTH_STUB": env.get("DEV_BFF_AUTH_STUB", "false"),
        "PANTHEON_DEV_BFF_AUTH_MODE": env.get("DEV_BFF_AUTH_MODE", "strict"),
        "PANTHEON_DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS": env.get("DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS", "900"),
        "PANTHEON_DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED": "false",
        "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": env.get("DEV_PAPER_PRINCIPALS_AUTHORIZED", "false"),
        "PANTHEON_DEV_CAPITAL_JWT_SECRET": env.get("DEV_BFF_JWT_SECRET", ""),
        "PANTHEON_DEV_BFF_CORS_ORIGINS": env.get("DEV_BFF_CORS_ORIGINS", "https://app.dev.mvl-cap.tw"),
        "PANTHEON_DEV_PAPER_EXPRESSION_LAYER": env.get("PANTHEON_DEV_PAPER_EXPRESSION_LAYER", env.get("DEV_PAPER_EXPRESSION_LAYER", "off")),
        "PANTHEON_DEV_EVOLUTION_AUTO_CONSUMER_ENABLED": env.get("DEV_EVOLUTION_AUTO_CONSUMER_ENABLED", "false"),
        "PANTHEON_DEV_L2_EXTRACTION_ENABLED": env.get("DEV_L2_EXTRACTION_ENABLED", "false"),
        "PANTHEON_DEV_L2_EXTRACTION_DAILY_CALL_CAP": env.get("DEV_L2_EXTRACTION_DAILY_CALL_CAP", "400"),
        "PANTHEON_DEV_MEMORY_LLM_BRIDGE_ENABLED": env.get("DEV_MEMORY_LLM_BRIDGE_ENABLED", "false"),
        "PANTHEON_DEV_MEMORY_LLM_BRIDGE_DAILY_BUDGET": env.get("DEV_MEMORY_LLM_BRIDGE_DAILY_BUDGET", "600"),
        "PANTHEON_DEV_PERSONA_EVALUATOR_PAPER_POOL_ID": env.get("DEV_PERSONA_EVALUATOR_PAPER_POOL_ID", "pool-paper-default"),
        "PANTHEON_DEV_PERSONA_RANKING_TOKEN_FILE": env.get("DEV_PERSONA_RANKING_TOKEN_FILE", "/run/pantheon-principals/PERSONA_RANKING_TOKEN"),
        "PANTHEON_DEV_PERSONA_ALLOCATION_TOKEN_FILE": env.get("DEV_PERSONA_ALLOCATION_TOKEN_FILE", "/run/pantheon-principals/PERSONA_ALLOCATION_TOKEN"),
        "PANTHEON_DEV_SHADOW_REPLAY_AGENT_ID": env.get("DEV_SHADOW_REPLAY_AGENT_ID", ""),
        "PANTHEON_DEV_SHADOW_REPLAY_POLICY_URL": env.get("DEV_SHADOW_REPLAY_POLICY_URL", ""),
        "PANTHEON_DEV_BFF_JWT_SECRET": env.get("DEV_BFF_JWT_SECRET", ""),
        "PANTHEON_DEV_BFF_JWT_ISSUER": env.get("DEV_BFF_JWT_ISSUER", ""),
        "PANTHEON_DEV_BFF_JWT_AUDIENCE": env.get("DEV_BFF_JWT_AUDIENCE", ""),
        "PANTHEON_DEV_BFF_JWKS_URI": env.get("DEV_BFF_JWKS_URI", ""),
        "PANTHEON_DEV_BFF_OIDC_DISCOVERY_URL": env.get("DEV_BFF_OIDC_DISCOVERY_URL", ""),
        "PANTHEON_DEV_BFF_OIDC_ISSUER": env.get("DEV_BFF_OIDC_ISSUER", ""),
        "PANTHEON_DEV_BFF_OIDC_AUDIENCE": env.get("DEV_BFF_OIDC_AUDIENCE", ""),
        "PANTHEON_DEV_BFF_OIDC_CLIENT_ID": env.get("DEV_BFF_OIDC_CLIENT_ID", ""),
        "PANTHEON_DEV_BFF_OIDC_CLIENT_SECRET": env.get("DEV_BFF_OIDC_CLIENT_SECRET", ""),
        "PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID": env.get("DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_ID", ""),
        "PANTHEON_DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET": env.get("DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET", ""),
        "PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_ID": env.get("DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_ID", ""),
        "PANTHEON_DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET": env.get("DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET", ""),
        "PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID": env.get("DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID", ""),
        "PANTHEON_DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET": env.get("DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET", ""),
        "PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID": env.get("DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID", ""),
        "PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET": env.get("DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET", ""),
        "PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID": env.get("DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_ID", ""),
        "PANTHEON_DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET": env.get("DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET", ""),
        "PANTHEON_DEV_BFF_DEV_LOGIN_TEST_ADMIN_CLIENT_ID": env.get("DEV_BFF_DEV_LOGIN_TEST_ADMIN_CLIENT_ID", ""),
        "PANTHEON_DEV_BFF_DEV_LOGIN_TEST_ADMIN_CLIENT_SECRET": env.get("DEV_BFF_DEV_LOGIN_TEST_ADMIN_CLIENT_SECRET", ""),
        "PANTHEON_DEV_BFF_MFA_REQUIRED": env.get("DEV_BFF_MFA_REQUIRED", "false"),
        "PANTHEON_DEV_BFF_MFA_CLAIMS": env.get("DEV_BFF_MFA_CLAIMS", "amr"),
        "PANTHEON_DEV_BFF_MFA_VALUES": env.get("DEV_BFF_MFA_VALUES", "mfa,otp,totp,webauthn,hwk,swk"),
        "PANTHEON_DEV_BFF_REQUIRE_EMAIL_VERIFIED": env.get("DEV_BFF_REQUIRE_EMAIL_VERIFIED", "true"),
        "PANTHEON_DEV_BFF_ROLE_CLAIMS": env.get("DEV_BFF_ROLE_CLAIMS", "roles,groups,pantheon_roles"),
        "PANTHEON_DEV_BFF_ROLE_MAP": env.get("DEV_BFF_ROLE_MAP", ""),
        "PANTHEON_DEV_BFF_ROLE_MAP_MODE": env.get("DEV_BFF_ROLE_MAP_MODE", "merge"),
        "PANTHEON_DEV_BFF_DEFAULT_ROLE": env.get("DEV_BFF_DEFAULT_ROLE", "viewer"),
        "PANTHEON_DEV_BFF_TENANT_ID": env.get("DEV_BFF_TENANT_ID", "tenant-dev"),
        "PANTHEON_DEV_BFF_ALLOWED_TENANTS": env.get("DEV_BFF_ALLOWED_TENANTS", "tenant-dev"),
        "PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN": env.get("DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN", ""),
        "PANTHEON_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED": env.get("DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED", "true"),
        "PANTHEON_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN": env.get("DEV_OPENCLAW_CLAUDE_CODE_OAUTH_TOKEN", ""),
        "PANTHEON_OPENCLAW_ANTHROPIC_API_KEY": env.get("DEV_OPENCLAW_ANTHROPIC_API_KEY", ""),
        "PANTHEON_OPENCLAW_ANTHROPIC_WORKSPACE_ID": env.get("DEV_OPENCLAW_ANTHROPIC_WORKSPACE_ID", ""),
        "PANTHEON_COLLECTOR_HOST": env.get("PANTHEON_COLLECTOR_HOST", ""),
        "PANTHEON_COLLECTOR_BUNDLE_TOKEN": env.get("PANTHEON_COLLECTOR_BUNDLE_TOKEN", ""),
        "PANTHEON_EXTERNAL_EGRESS": "allowlist" if env.get("PANTHEON_COLLECTOR_HOST") else "deny",
        "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS": env.get("PANTHEON_COLLECTOR_HOST", ""),
    }
    for key in (
        "MEMORY_INDEX_POSTGRES_PASSWORD",
        "HINDSIGHT_API_KEY",
        "HINDSIGHT_RETAIN_LLM_PROVIDER",
        "HINDSIGHT_RETAIN_LLM_MODEL",
        "HINDSIGHT_RETAIN_LLM_API_KEY",
        "HINDSIGHT_CONSOLIDATION_LLM_PROVIDER",
        "HINDSIGHT_CONSOLIDATION_LLM_MODEL",
        "HINDSIGHT_CONSOLIDATION_LLM_API_KEY",
        "HINDSIGHT_REFLECT_LLM_PROVIDER",
        "HINDSIGHT_REFLECT_LLM_MODEL",
        "HINDSIGHT_REFLECT_LLM_API_KEY",
        "HINDSIGHT_MENTAL_MODEL_REFRESH_LLM_PROVIDER",
        "HINDSIGHT_MENTAL_MODEL_REFRESH_LLM_MODEL",
        "HINDSIGHT_MENTAL_MODEL_REFRESH_LLM_API_KEY",
    ):
        if key in env:
            exports[key] = env[key]
    export_line = "export " + " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(exports.items()))
    install_b64 = base64.b64encode(encoded(install_payload)).decode()
    body = (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "umask 077\n"
        f"{export_line}\n"
        "info() { echo \"[remote-deploy] $*\"; }\n"
        "error() { echo \"[remote-deploy] ERROR: $*\" >&2; exit 1; }\n"
        f"if python3 -c {shlex.quote(INSTALLER)} 2>/dev/null <<'INSTALL_ARTIFACT_PAYLOAD'\n"
        f"{install_b64}\n"
        "INSTALL_ARTIFACT_PAYLOAD\n"
        "then :\n"
        "else\n"
        "  rc=$?\n"
        f"  printf {shlex.quote(failure_format)} \"$rc\" >&2\n"
        "  exit \"$rc\"\n"
        "fi\n"
        f"{memory_fn}\n"
        f"{runtime_fn}\n"
        f"{run_driver_fn}\n"
        "artifact_readback=\"$(run_dev_artifact_driver restore)\" || exit $?\n"
        "printf 'PANTHEON_ARTIFACT_READBACK_V1 %s\\n' \"${artifact_readback}\"\n"
    )
    return body.encode("utf-8")


def execute_restore_accepted_baseline(
    *,
    baseline_path: Path,
    expected_baseline_sha256: str,
    candidate_receipt_path: Path,
    deployment_manifest_path: Path,
    bff_version_path: Path,
    repo_root: Path,
    output_readback_path: Path,
    env: dict[str, str] | None = None,
    is_ancestor=None,
    guarded_runner=None,
) -> dict:
    environment = dict(os.environ if env is None else env)
    matches(expected_baseline_sha256, r"[0-9a-f]{64}")
    baseline_raw = _read_regular_file(baseline_path, 1024 * 1024)
    if hashlib.sha256(baseline_raw).hexdigest() != expected_baseline_sha256:
        raise CaptureError("baseline file digest differs from expected manifest_sha256")
    baseline_manifest = json.loads(baseline_raw, object_pairs_hook=unique_object)
    if not isinstance(baseline_manifest, dict) or not isinstance(baseline_manifest.get("identity"), dict):
        raise CaptureError("baseline manifest is invalid")
    identity = dict(baseline_manifest["identity"])
    folder = ARTIFACT_ROOT / f"baseline-{identity.get('run_id', '')}-{identity.get('attempt', '')}-{identity.get('candidate_id', '')}"
    sealed, outputs = seal_result(encoded({
        "manifest_path": str(folder / "manifest.json"),
        "manifest_sha256": expected_baseline_sha256,
        "manifest": baseline_manifest,
    }), identity)
    if sealed != baseline_raw:
        raise CaptureError("baseline bytes differ from sealed bytes")

    deployment_manifest = json.loads(_read_regular_file(deployment_manifest_path, 65536), object_pairs_hook=unique_object)
    bff_version = json.loads(_read_regular_file(bff_version_path, 65536), object_pairs_hook=unique_object)
    expected_bff_url = environment.get("DEV_BFF_URL")
    verify_restore_accepted_baseline_preconditions(
        baseline_manifest=baseline_manifest,
        deployment_manifest=deployment_manifest,
        bff_version=bff_version,
        repo_root=repo_root,
        expected_bff_url=expected_bff_url,
        is_ancestor=is_ancestor,
    )

    candidate_raw = _read_regular_file(candidate_receipt_path, 65536)
    candidate_sha256, candidate_receipt = verify_candidate_files(
        {"candidate-receipt.json": candidate_raw},
        run_id=identity["run_id"],
        attempt=identity["attempt"],
        baseline_manifest=baseline_manifest,
        baseline_manifest_sha256=expected_baseline_sha256,
    )

    if not repo_root.is_absolute() or repo_root.resolve() != repo_root or not repo_root.is_dir():
        raise CaptureError("controller checkout is not canonical")
    current_controller_sha = environment.get("PANTHEON_CURRENT_CONTROLLER_SHA", "").strip()
    if not current_controller_sha:
        current_controller_sha = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=15,
        ).stdout.strip()
    matches(current_controller_sha, r"[0-9a-f]{40}")
    implementations = {name: read_implementation(repo_root, name, current_controller_sha)
                       for name in IMPLEMENTATIONS}
    compose_raw = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"{identity['previous_backend_sha']}:docker-compose.yml"],
        check=True, capture_output=True, timeout=30,
    ).stdout
    if digest(compose_raw) != baseline_manifest["compose_sha256"]:
        raise CaptureError("prior Compose blob differs from baseline manifest compose_sha256")

    compose_file = str(ARTIFACT_ROOT / "compose" / identity["previous_backend_sha"] / "docker-compose.yml")
    driver_path = str(ARTIFACT_ROOT / "controllers" / current_controller_sha / IMPLEMENTATIONS[0])
    library_path = str(ARTIFACT_ROOT / "controllers" / current_controller_sha / IMPLEMENTATIONS[1])
    driver_sha256 = digest(implementations[IMPLEMENTATIONS[0]])
    library_sha256 = digest(implementations[IMPLEMENTATIONS[1]])
    remote_script = build_restore_remote_script(
        repo_root=repo_root,
        current_controller_sha=current_controller_sha,
        implementations=implementations,
        identity=identity,
        manifest_path=outputs["manifest_path"],
        manifest_sha256=expected_baseline_sha256,
        candidate_image_manifest_path=candidate_receipt["candidate_image_manifest_path"],
        candidate_image_manifest_sha256=candidate_sha256,
        compose_file=compose_file,
        env=environment,
    )

    if guarded_runner is None:
        ssh_helper = repo_root / "scripts" / "dev_vm_ssh.sh"
        guarded_exec = repo_root / "scripts" / "dev_remote_guarded_exec.py"

        def _default_guarded_runner(script_bytes: bytes) -> subprocess.CompletedProcess[bytes]:
            with tempfile.TemporaryDirectory(dir=environment.get("RUNNER_TEMP") or "/tmp",
                                            prefix="pantheon-restore-") as temp_dir:
                temp_path = Path(temp_dir)
                os.chmod(temp_path, 0o700)
                script_file = temp_path / "remote.sh"
                private_write(script_file, script_bytes)
                deadline = str(environment.get("DEV_DEPLOY_DEADLINE_SECONDS", "1500"))
                return subprocess.run(
                    [sys.executable, str(guarded_exec),
                     "--ssh-helper", str(ssh_helper),
                     "--script-file", str(script_file),
                     "--deadline-seconds", deadline],
                    env=environment,
                    capture_output=True,
                )

        runner_fn = _default_guarded_runner
    else:
        runner_fn = guarded_runner

    restore_proc = runner_fn(remote_script)
    combined_log = (restore_proc.stdout or b"") + b"\n" + (restore_proc.stderr or b"")
    if restore_proc.returncode != 0:
        with tempfile.TemporaryDirectory(prefix="pantheon-restore-fail-") as fail_dir:
            fail_path = Path(fail_dir)
            os.chmod(fail_path, 0o700)
            log_file = fail_path / "transport.log"
            private_write(log_file, combined_log)
            summary_env = environment.get("GITHUB_STEP_SUMMARY")
            summary_path = Path(summary_env) if summary_env else None
            mixed = mixed_summary(log_file, summary_path=summary_path)
        failure = remote_failure_record(restore_proc.stderr or b"") or remote_failure_record(restore_proc.stdout or b"")
        reason = None
        if isinstance(failure, dict):
            sc = failure.get("source_compatibility")
            if isinstance(sc, dict) and isinstance(sc.get("reason"), str):
                reason = sc["reason"]
            else:
                reason = f"{failure.get('failure_stage', 'restore')}:{failure.get('failure_kind', 'unknown')}@{failure.get('failure_location', 'unknown')}"
        msg = f"whole-stack baseline restore failed ({reason or f'exit {restore_proc.returncode}'})"
        if isinstance(failure, dict):
            print(json.dumps(failure, sort_keys=True), file=sys.stderr)
        raise RestoreExecutionError(
            msg,
            failure=failure,
            mixed=mixed,
            exit_code=failure["exit_code"] if isinstance(failure, dict) else restore_proc.returncode,
        )

    lines = (restore_proc.stdout or b"").splitlines()
    records = [line for line in lines if line.startswith(READBACK_PREFIX)]
    if len(records) != 1 or len(records[0]) > 65536:
        raise CaptureError("exactly one bounded typed artifact readback is required")
    if any(b"PANTHEON_ARTIFACT_READBACK_V1" in line and line not in records for line in lines):
        raise CaptureError("artifact readback framing is invalid")
    readback_value = json.loads(records[0][len(READBACK_PREFIX):], object_pairs_hook=unique_object)
    evidence = {
        "identity": identity,
        "baseline": baseline_manifest,
        "candidate": candidate_receipt,
        "target": "baseline",
        "exports": {
            ENV_PREFIX + "OPERATION": "restore",
            ENV_PREFIX + "SCOPE": "stack",
            ENV_PREFIX + "TARGET": "baseline",
            ENV_PREFIX + "MANIFEST_PATH": outputs["manifest_path"],
            ENV_PREFIX + "MANIFEST_SHA256": expected_baseline_sha256,
            ENV_PREFIX + "DRIVER_PATH": driver_path,
            ENV_PREFIX + "DRIVER_SHA256": driver_sha256,
            ENV_PREFIX + "LIBRARY_PATH": library_path,
            ENV_PREFIX + "LIBRARY_SHA256": library_sha256,
        },
    }
    validate_readback(readback_value, evidence, "restore")
    if (readback_value.get("stack", {}).get("scope") != "stack" or
        readback_value.get("public", {}).get("source_sha") != identity["previous_backend_sha"] or
        readback_value.get("frontend") != baseline_manifest["frontend"]):
        raise CaptureError("restored readback does not match accepted baseline pair and frontend manifest")
    output_readback_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_write(output_readback_path, encoded(readback_value))
    return readback_value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fetch", "verify-preconditions", "restore-accepted-baseline"), default="fetch")
    parser.add_argument("--kind", choices=tuple(FILES))
    parser.add_argument("--artifact-id")
    parser.add_argument("--artifact-sha256")
    parser.add_argument("--run-id")
    parser.add_argument("--attempt", default="1")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--baseline-file", type=Path)
    parser.add_argument("--expected-baseline-sha256")
    parser.add_argument("--candidate-receipt-file", type=Path)
    parser.add_argument("--deployment-json-file", type=Path)
    parser.add_argument("--bff-version-file", type=Path)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--output-readback", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.mode == "fetch":
            if not args.kind or not args.run_id or args.output_dir is None:
                raise CaptureError("fetch mode requires --kind, --run-id, and --output-dir")
            result = fetch(kind=args.kind, artifact_id=args.artifact_id, expected_digest=args.artifact_sha256,
                           run_id=args.run_id, attempt=args.attempt, output_dir=args.output_dir)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.mode == "verify-preconditions":
            if (args.baseline_file is None or args.deployment_json_file is None or
                args.bff_version_file is None or args.repo_root is None):
                raise CaptureError("verify-preconditions requires --baseline-file, --deployment-json-file, --bff-version-file, and --repo-root")
            baseline_manifest = json.loads(_read_regular_file(args.baseline_file, 1024 * 1024), object_pairs_hook=unique_object)
            deployment_manifest = json.loads(_read_regular_file(args.deployment_json_file, 65536), object_pairs_hook=unique_object)
            bff_version = json.loads(_read_regular_file(args.bff_version_file, 65536), object_pairs_hook=unique_object)
            summary = verify_restore_accepted_baseline_preconditions(
                baseline_manifest=baseline_manifest,
                deployment_manifest=deployment_manifest,
                bff_version=bff_version,
                repo_root=args.repo_root,
                expected_bff_url=os.environ.get("DEV_BFF_URL"),
            )
            print(json.dumps(summary, sort_keys=True))
            return 0
        if (args.baseline_file is None or not args.expected_baseline_sha256 or
            args.candidate_receipt_file is None or args.deployment_json_file is None or
            args.bff_version_file is None or args.repo_root is None or args.output_readback is None):
            raise CaptureError("restore-accepted-baseline requires all input and output file arguments")
        readback = execute_restore_accepted_baseline(
            baseline_path=args.baseline_file,
            expected_baseline_sha256=args.expected_baseline_sha256,
            candidate_receipt_path=args.candidate_receipt_file,
            deployment_manifest_path=args.deployment_json_file,
            bff_version_path=args.bff_version_file,
            repo_root=args.repo_root,
            output_readback_path=args.output_readback,
        )
        print(json.dumps(readback, sort_keys=True))
        return 0
    except RestoreExecutionError as error:
        print(f"[dev-artifact-restore] ERROR: {error}", file=sys.stderr)
        return error.exit_code
    except (CaptureError, OSError, ValueError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile, zlib.error) as error:
        if args.mode == "fetch":
            print("[dev-artifact-download] exact authenticated evidence download failed", file=sys.stderr)
        else:
            print(f"[dev-artifact-restore] ERROR: {error}", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
