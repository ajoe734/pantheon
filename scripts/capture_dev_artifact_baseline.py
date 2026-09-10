#!/usr/bin/env python3
"""Seal a dev-only artifact baseline beneath the existing pinned lease guard.

The GitHub job admits source identities first. This guarded child retains the
actual prior bytes before candidate mutation and returns their external seal.
It neither acquires a lease nor performs a restore. Archives remain private on
the VM; only the non-secret manifest belongs in the Actions evidence upload.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
VM_HOME = Path("/home/chloe_ong_dev_cctech_support_com")
ARTIFACT_ROOT = VM_HOME / "pantheon-ci-deploy/release-artifacts"
FIELDS = ("candidate_id", "run_id", "attempt", "controller_sha", "candidate_backend_sha",
          "candidate_frontend_sha", "previous_backend_sha", "previous_frontend_sha")
IMPLEMENTATIONS = ("dev_release_artifact_driver.py", "dev_release_artifacts.py")


class CaptureError(RuntimeError):
    pass


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CaptureError("duplicate JSON key")
        result[key] = value
    return result


def exact_keys(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise CaptureError("capture document fields are invalid")


def matches(value, pattern):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise CaptureError("capture document value is invalid")
    return value


def identity_from_environment(env: dict[str, str]) -> dict[str, str]:
    result = {name: env.get("PANTHEON_DEV_ARTIFACT_" + name.upper(), "") for name in FIELDS}
    for name, value in result.items():
        pattern = r"[0-9a-f]{64}" if name == "candidate_id" else r"[0-9a-f]{40}"
        if name in {"run_id", "attempt"}:
            pattern = r"[1-9][0-9]{0,19}" if name == "run_id" else r"[1-9][0-9]{0,9}"
        if not re.fullmatch(pattern, value):
            raise CaptureError(f"invalid artifact identity field: {name}")
    return result


def require_guarded_dev(env: dict[str, str]) -> str:
    expected = {"TARGET_ENV": "dev", "GCP_DEPLOY_PROJECT_ID": "pantheon-dev-20260902",
                "DEV_VM": "pantheon-dev-deploy", "DEV_ZONE": "asia-east1-b",
                "DEV_DEPLOY_SSH_HOST": "34.81.52.222",
                "DEV_DEPLOY_SSH_USER": VM_HOME.name}
    if any(env.get(key) != value for key, value in expected.items()):
        raise CaptureError("artifact capture requires the explicit current dev target")
    value = env.get("PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID", "")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except ValueError as exc:
        raise CaptureError("artifact capture requires the existing guard context") from exc
    state_path = Path(env.get("PANTHEON_DEV_ENVIRONMENT_LEASE_STATE_FILE", ""))
    if not state_path.is_absolute() or state_path.is_symlink() or not state_path.is_file():
        raise CaptureError("existing guard state file is unavailable")
    with state_path.open("rb") as handle:
        raw = handle.read(32769)
    if len(raw) > 32768:
        raise CaptureError("existing guard state exceeds bound")
    state = json.loads(raw, object_pairs_hook=unique_object)
    expected_state = {"schemaVersion": 1, "repository": "ajoe734/execute-plans",
                      "branch": "environment-coordination",
                      "path": ".pantheon/environment-leases/pantheon-dev-environment.json",
                      "resource": "pantheon-dev-environment", "mode": "deployment",
                      "leaseId": value,
                      "expectedBackendSha": env.get("PANTHEON_DEV_ARTIFACT_CANDIDATE_BACKEND_SHA")}
    if not isinstance(state, dict) or any(state.get(key) != item for key, item in expected_state.items()):
        raise CaptureError("guard state is not bound to this dev candidate")
    # Context only: the pinned parent validates ownership and controls this
    # process group. This UUID is not a transferable/reusable lease token.
    return value


def read_implementation(root: Path, name: str, controller_sha: str) -> bytes:
    path = root / "scripts" / name
    if path.is_symlink() or not path.is_file():
        raise CaptureError("implementation must be a regular checked-in file")
    raw = path.read_bytes()
    if not raw or len(raw) > 1024 * 1024:
        raise CaptureError("implementation exceeds size bound")
    recorded = subprocess.run(["git", "-C", str(root), "show", f"{controller_sha}:scripts/{name}"],
                              capture_output=True, check=True, timeout=30).stdout
    if raw != recorded:
        raise CaptureError("implementation differs from the admitted controller commit")
    return raw


# Executed only inside the independently tested runner/remote watchdog channel.
# It prepares new private controller/config paths, never checks out an existing
# owner-mounted source directory, never touches Docker, and never fetches a token.
INSTALLER = r'''
import base64, hashlib, json, os, pathlib, stat, subprocess, sys
p = json.loads(base64.b64decode(sys.argv[1], validate=True))
root = pathlib.Path(p["root"])
def private_directory(path):
    for part in (path, *path.parents):
        if part.is_symlink(): raise SystemExit("artifact path contains a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.stat()
    if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise SystemExit("artifact directory is not privately owned")
private_directory(root)
controller = root / "controllers" / p["controller_sha"]
private_directory(controller.parent)
private_directory(controller)
for name, entry in p["files"].items():
    raw = base64.b64decode(entry["base64"], validate=True)
    if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise SystemExit("controller payload digest mismatch")
    dest = controller / name
    if dest.exists() or dest.is_symlink():
        fd = os.open(dest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            st = os.fstat(handle.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
                raise SystemExit("retained controller is unsafe")
            if handle.read(1024 * 1024 + 1) != raw:
                raise SystemExit("retained controller identity differs; refusing overwrite")
    else:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
config_parent = root / "compose"
private_directory(config_parent)
config = config_parent / p["previous_backend_sha"]
if config.is_symlink(): raise SystemExit("baseline worktree is a symlink")
if not config.exists():
    subprocess.run(["git", "-C", p["source"], "cat-file", "-e",
                    p["previous_backend_sha"] + "^{commit}"], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", p["source"], "worktree", "add", "--detach", str(config),
                    p["previous_backend_sha"]], check=True, stdout=subprocess.DEVNULL)
observed = subprocess.run(["git", "-C", str(config), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()
if observed != p["previous_backend_sha"]:
    raise SystemExit("retained baseline worktree has drifted")
'''


def remote_script(identity: dict[str, str], implementations: dict[str, bytes],
                  env: dict[str, str], guard_id: str) -> str:
    controller = ARTIFACT_ROOT / "controllers" / identity["controller_sha"]
    compose = ARTIFACT_ROOT / "compose" / identity["previous_backend_sha"] / "docker-compose.yml"
    payload = {"root": str(ARTIFACT_ROOT), "source": str(VM_HOME / "pantheon"),
               "controller_sha": identity["controller_sha"],
               "previous_backend_sha": identity["previous_backend_sha"],
               "files": {name: {"base64": base64.b64encode(raw).decode(), "sha256": digest(raw)}
                         for name, raw in implementations.items()}}
    # Only the two dedicated read-only login fields are transported; no
    # Config.Env dump, generic GitHub token, or write principal is collected.
    exports = {"PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": guard_id}
    for suffix in ("CLIENT_ID", "CLIENT_SECRET"):
        source = "DEV_BFF_DEV_LOGIN_VIEWER_" + suffix
        value = env.get(source, "")
        if not value or len(value) > 16384 or "\0" in value:
            raise CaptureError("dedicated viewer credential is unavailable")
        exports["PANTHEON_BFF_DEV_LOGIN_VIEWER_" + suffix] = value
    args = ["python3", str(controller / IMPLEMENTATIONS[0]), "capture",
            "--artifact-root", str(ARTIFACT_ROOT), "--environment", "dev",
            "--project-id", "pantheon-dev-20260902", "--vm", "pantheon-dev-deploy",
            "--compose-file", str(compose), "--bff-url", "https://api.dev.mvl-cap.tw",
            "--fe-url", "https://app.dev.mvl-cap.tw", "--fe-release-store", "/var/www/pantheon-dev-fe-releases",
            "--fe-live-link", "/var/www/pantheon-dev-fe"]
    for name, value in identity.items():
        args.extend(("--" + name.replace("_", "-"), value))
    lines = ["set -euo pipefail", "umask 077",
             ': "${PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD:?remote watchdog channel required}"']
    lines.extend("export " + key + "=" + shlex.quote(value) for key, value in exports.items())
    lines.append("python3 - " + shlex.quote(base64.b64encode(encoded(payload)).decode()) + " <<'INSTALL_ARTIFACT_CONTROLLER'")
    lines.extend((INSTALLER, "INSTALL_ARTIFACT_CONTROLLER"))
    lines.append("exec " + shlex.join(args) + ' --guard-channel-fd "${PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD}"')
    return "\n".join(lines) + "\n"


def seal_result(raw: bytes, identity: dict[str, str], *, expected_lease_id=None) -> tuple[bytes, dict[str, str]]:
    if len(raw) > 1024 * 1024:
        raise CaptureError("capture result exceeds size bound")
    result = json.loads(raw, object_pairs_hook=unique_object)
    exact_keys(result, ("manifest_path", "manifest_sha256", "manifest"))
    manifest = result["manifest"]
    exact_keys(manifest, ("schema_version", "environment", "project_id", "vm", "identity",
                          "capture_lease_id", "captured_at", "image_bundle", "image_bundle_sha256",
                          "frontend", "compose_sha256", "baseline_nonsecret_config"))
    if (manifest.get("schema_version") != "pantheon.dev-release-artifact-baseline.v1" or
        manifest.get("environment") != "dev" or manifest.get("project_id") != "pantheon-dev-20260902" or
        manifest.get("vm") != "pantheon-dev-deploy" or manifest.get("identity") != identity):
        raise CaptureError("capture result does not match admitted dev candidate")
    if str(uuid.UUID(manifest["capture_lease_id"])) != manifest["capture_lease_id"]:
        raise CaptureError("invalid capture guard context")
    if expected_lease_id is not None and manifest["capture_lease_id"] != expected_lease_id:
        raise CaptureError("capture guard context differs from current guard")
    matches(manifest["captured_at"], r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
    try:
        time.strptime(manifest["captured_at"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise CaptureError("capture timestamp is invalid") from exc
    for name in ("compose_sha256", "image_bundle_sha256"):
        matches(manifest[name], r"[0-9a-f]{64}")
    config = manifest["baseline_nonsecret_config"]
    expected_config = {"PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN_FILE": "/run/pantheon-principals/PANTHEON_PERSONA_GOVERNANCE_SERVICE_TOKEN",
                       "PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID": "pantheon-dev-paper-provisioner"}
    exact_keys(config, expected_config)
    if any(config[key] not in (None, "", value) for key, value in expected_config.items()):
        raise CaptureError("capture includes unsupported configuration")
    frontend = manifest["frontend"]
    exact_keys(frontend, ("target", "dist_sha256", "manifest_sha256", "frontend_sha", "backend_sha"))
    for name in ("dist_sha256", "manifest_sha256"):
        matches(frontend[name], r"[0-9a-f]{64}")
    if (frontend["frontend_sha"] != identity["previous_frontend_sha"] or
        frontend["backend_sha"] != identity["previous_backend_sha"]):
        raise CaptureError("capture FE differs from admitted prior pair")
    target = Path(matches(frontend["target"], r"/var/www/pantheon-dev-fe-releases/[A-Za-z0-9._-]+"))
    if target.name in (".", ".."):
        raise CaptureError("capture FE target is unsafe")
    bundle = manifest["image_bundle"]
    exact_keys(bundle, ("schema_version", "source_sha", "services", "archives"))
    if (bundle["schema_version"] != "pantheon.dev-bff-image-bundle.v1" or
        bundle["source_sha"] != identity["previous_backend_sha"] or
        digest(encoded(bundle)) != manifest["image_bundle_sha256"]):
        raise CaptureError("capture image bundle differs from admitted prior")
    exact_keys(bundle["services"], ("operator-bff", "agora-interaction-worker", "loop-run-projector-scheduler"))
    image_ids = set()
    for row in bundle["services"].values():
        exact_keys(row, ("image_id", "oci_revision", "repo_digests"))
        image_ids.add(matches(row["image_id"], r"sha256:[0-9a-f]{64}"))
        if row["oci_revision"] not in (None, "", "unknown", identity["previous_backend_sha"]):
            raise CaptureError("capture image revision differs from prior")
        digests = row["repo_digests"]
        if digests is not None:
            if not isinstance(digests, list) or len(digests) > 32:
                raise CaptureError("capture registry metadata is invalid")
            for entry in digests:
                matches(entry, r"[a-zA-Z0-9._:/-]+@sha256:[0-9a-f]{64}")
    exact_keys(bundle["archives"], image_ids)
    for image_id, archive in bundle["archives"].items():
        exact_keys(archive, ("name", "sha256", "size"))
        matches(archive["sha256"], r"[0-9a-f]{64}")
        if (archive["name"] != image_id.removeprefix("sha256:") + "-" + archive["sha256"] + ".tar" or
            type(archive["size"]) is not int or archive["size"] <= 0):
            raise CaptureError("capture archive identity is invalid")
    manifest_raw = encoded(manifest)
    if digest(manifest_raw) != result["manifest_sha256"]:
        raise CaptureError("capture manifest seal mismatch")
    expected_path = ARTIFACT_ROOT / f"baseline-{identity['run_id']}-{identity['attempt']}-{identity['candidate_id']}" / "manifest.json"
    if result["manifest_path"] != str(expected_path):
        raise CaptureError("capture manifest is not the exact run/candidate path")
    path = expected_path
    return manifest_raw, {"manifest_path": str(path), "manifest_sha256": digest(manifest_raw)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        env = dict(os.environ)
        guard = require_guarded_dev(env)
        identity = identity_from_environment(env)
        implementations = {name: read_implementation(ROOT, name, identity["controller_sha"])
                           for name in IMPLEMENTATIONS}
        directory = args.evidence_dir
        if not directory.is_absolute() or directory.resolve() != directory:
            raise CaptureError("evidence directory must be canonical")
        # The run-level parent also holds rollback.env. mkdir(parents=True)
        # alone would create that intermediate directory with umask defaults.
        directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent_info = directory.parent.stat()
        if parent_info.st_uid != os.geteuid() or parent_info.st_mode & 0o077:
            raise CaptureError("artifact run directory must be privately owned")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = directory.stat()
        if st.st_uid != os.geteuid() or st.st_mode & 0o077:
            raise CaptureError("evidence directory must be privately owned")
        # The script contains the viewer credential and must never be uploaded.
        with tempfile.TemporaryDirectory(prefix="pantheon-artifact-capture-") as private:
            script = Path(private) / "capture.sh"
            with script.open("x", encoding="utf-8") as handle:
                os.chmod(script, 0o600)
                handle.write(remote_script(identity, implementations, env, guard))
            result = subprocess.run([sys.executable, str(ROOT / "scripts/dev_remote_guarded_exec.py"),
                                     "--ssh-helper", str(ROOT / "scripts/dev_vm_ssh.sh"),
                                     "--script-file", str(script), "--deadline-seconds", "1200"],
                                    capture_output=True, timeout=1230)
            if result.returncode:
                # Neither the private script nor remote raw diagnostics are evidence.
                raise CaptureError("guarded artifact capture failed")
        manifest_raw, outputs = seal_result(result.stdout, identity, expected_lease_id=guard)
        for name, data in (("artifact-baseline.json", manifest_raw),
                           ("SHA256SUMS", (outputs["manifest_sha256"] + "  artifact-baseline.json\n").encode())):
            destination = directory / name
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data); handle.flush(); os.fsync(handle.fileno())
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        outputs.update({"driver_path": str(ARTIFACT_ROOT / "controllers" / identity["controller_sha"] / IMPLEMENTATIONS[0]),
                        "driver_sha256": digest(implementations[IMPLEMENTATIONS[0]]),
                        "library_sha256": digest(implementations[IMPLEMENTATIONS[1]]),
                        "compose_file": str(ARTIFACT_ROOT / "compose" / identity["previous_backend_sha"] / "docker-compose.yml"),
                        "evidence_dir": str(directory)})
        output_file = env.get("GITHUB_OUTPUT")
        if output_file:
            with open(output_file, "a", encoding="utf-8") as handle:
                for key, value in outputs.items():
                    if "\n" in value or "\r" in value:
                        raise CaptureError("invalid workflow output")
                    handle.write(f"{key}={value}\n")
        print(json.dumps(outputs, sort_keys=True))
        return 0
    except (CaptureError, OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        print("[dev-artifact-capture] failed closed; no sealed baseline accepted", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
