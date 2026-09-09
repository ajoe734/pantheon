#!/usr/bin/env python3
"""Validate retained compensation inputs and post-operation dev readback.

This consumer neither acknowledges a candidate seal nor grants mutation
authority. The pinned lease guard and VM driver retain those responsibilities.
Actions downloads are authenticated by the separate exact-artifact fetcher;
the hashes supplied here must come from workflow outputs, never the documents.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys

# The accepted controller directory is the import boundary, even when the
# pinned guard removes Python's default script/cwd path with PYTHONSAFEPATH=1.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from . import capture_dev_artifact_baseline as capture
    from . import dev_candidate_receipt as candidate
except ImportError:
    import capture_dev_artifact_baseline as capture
    import dev_candidate_receipt as candidate

PREFIX = b"PANTHEON_ARTIFACT_READBACK_V1 "
ENV_PREFIX = "PANTHEON_DEV_ARTIFACT_"
PROVENANCES = ("runner-local", "actions-download")
OWNERS = ("governance", "registry", "deployment", "runtime-manager",
          "deployment-outbox-consumer", "capital", "dev-paper-principal-issuer")
OBSERVATIONS = {"available_matching_source", "unavailable_connection_refused",
                "unavailable_connection_reset", "unavailable_timeout", "unavailable_disconnect",
                "unavailable_http_502", "unavailable_http_503", "unavailable_http_504"}


def private_bytes(path: Path, *, limit: int = 1024 * 1024) -> bytes:
    candidate.private_directory(path.parent)
    if not path.is_absolute() or path.resolve() != path:
        raise capture.CaptureError("evidence path is not canonical")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                info.st_mode & 0o077 or info.st_nlink != 1):
            raise capture.CaptureError("evidence must be a private regular file")
        raw = stream.read(limit + 1)
    if not raw or len(raw) > limit:
        raise capture.CaptureError("evidence size is outside its bound")
    return raw


def private_write(path: Path, raw: bytes) -> None:
    candidate.private_directory(path.parent)
    if not path.is_absolute() or path.resolve() != path:
        raise capture.CaptureError("evidence output path is not canonical")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def document(path: Path, *, limit: int = 1024 * 1024) -> tuple[dict, bytes]:
    raw = private_bytes(path, limit=limit)
    value = json.loads(raw, object_pairs_hook=capture.unique_object)
    if raw != capture.encoded(value):
        raise capture.CaptureError("evidence bytes are not canonical")
    return value, raw


def artifact_metadata(env: dict[str, str], kind: str, *, required: bool) -> dict | None:
    artifact_id = env.get(ENV_PREFIX + kind + "_ACTIONS_ARTIFACT_ID", "")
    digest = env.get(ENV_PREFIX + kind + "_ACTIONS_ARTIFACT_SHA256", "")
    if not artifact_id and not digest and not required:
        return None
    capture.matches(artifact_id, r"[1-9][0-9]{0,19}")
    capture.matches(digest, r"[0-9a-f]{64}")
    return {"artifact_id": artifact_id, "artifact_sha256": digest}


def load_evidence(env: dict[str, str], provenance: str) -> dict:
    if provenance not in PROVENANCES:
        raise capture.CaptureError("compensation provenance must be explicit")
    expected_target = {"TARGET_ENV": "dev", "GCP_DEPLOY_PROJECT_ID": "pantheon-dev-20260902",
                       "DEV_VM": "pantheon-dev-deploy", "DEV_ZONE": "asia-east1-b"}
    if any(env.get(key) != value for key, value in expected_target.items()):
        raise capture.CaptureError("compensation requires the exact current dev target")
    identity = {
        "candidate_id": env.get("PANTHEON_RELEASE_CANDIDATE_ID", ""),
        "run_id": env.get("GITHUB_RUN_ID", ""), "attempt": env.get("GITHUB_RUN_ATTEMPT", ""),
        "controller_sha": env.get(ENV_PREFIX + "CONTROLLER_SHA", ""),
        "candidate_backend_sha": env.get("PANTHEON_FAILED_BACKEND_SHA", ""),
        "candidate_frontend_sha": env.get("PANTHEON_FAILED_FRONTEND_SHA", ""),
        "previous_backend_sha": env.get("PANTHEON_ROLLBACK_BACKEND_SHA", ""),
        "previous_frontend_sha": env.get("PANTHEON_ROLLBACK_FRONTEND_SHA", ""),
    }
    capture.identity_from_environment({ENV_PREFIX + key.upper(): value for key, value in identity.items()})
    manifest_hash = capture.matches(env.get(ENV_PREFIX + "MANIFEST_SHA256", ""), r"[0-9a-f]{64}")
    baseline_path = Path(env.get(ENV_PREFIX + "BASELINE_FILE", ""))
    receipt_file = env.get(ENV_PREFIX + "CANDIDATE_RECEIPT_FILE", "")
    receipt_hash = env.get(ENV_PREFIX + "CANDIDATE_IMAGE_MANIFEST_SHA256", "")
    candidate_artifact_fields = [env.get(ENV_PREFIX + "CANDIDATE_ACTIONS_ARTIFACT_" + key, "")
                                 for key in ("ID", "SHA256")]
    has_candidate = bool(receipt_file or receipt_hash or any(candidate_artifact_fields))
    if has_candidate and (not receipt_file or not receipt_hash):
        raise capture.CaptureError("candidate receipt and external image seal must be paired")
    baseline_artifact = artifact_metadata(env, "BASELINE", required=provenance == "actions-download")
    candidate_artifact = artifact_metadata(env, "CANDIDATE", required=has_candidate and provenance == "actions-download")
    if provenance == "runner-local":
        temporary = Path(env.get("RUNNER_TEMP", ""))
        if not temporary.is_absolute() or temporary.resolve() != temporary:
            raise capture.CaptureError("runner temporary directory is not canonical")
        retained = temporary / f"pantheon-dev-artifacts-{identity['run_id']}-{identity['attempt']}"
        if baseline_path != retained / "baseline/artifact-baseline.json":
            raise capture.CaptureError("runner baseline is not from the exact run and attempt")
        if has_candidate and Path(receipt_file) != retained / "candidate/candidate-receipt.json":
            raise capture.CaptureError("runner receipt is not from the exact run and attempt")
    baseline, baseline_raw = document(baseline_path)
    folder = capture.ARTIFACT_ROOT / f"baseline-{identity['run_id']}-{identity['attempt']}-{identity['candidate_id']}"
    # Reuse the producer's strict schema and archive validation, but anchor the
    # wrapper to the EXTERNAL seal and fixed remote path, never a self-hash.
    sealed, outputs = capture.seal_result(capture.encoded({
        "manifest_path": str(folder / "manifest.json"), "manifest_sha256": manifest_hash,
        "manifest": baseline}), identity)
    if sealed != baseline_raw:
        raise capture.CaptureError("baseline bytes differ from sealed bytes")
    receipt = None
    if has_candidate:
        capture.matches(receipt_hash, r"[0-9a-f]{64}")
        receipt, _ = document(Path(receipt_file), limit=65536)
        context = {"schema_version": "pantheon.dev-candidate-receipt-context.v1", "identity": identity,
                   "baseline_manifest_sha256": manifest_hash, "guard_lease_id": baseline["capture_lease_id"]}
        if candidate.validate_receipt(receipt, context) != receipt_hash:
            raise capture.CaptureError("candidate differs from its externally trusted seal")
    root = Path(env.get("PANTHEON_RELEASE_REPO_ROOT", ""))
    if not root.is_absolute() or root.resolve() != root or not root.is_dir():
        raise capture.CaptureError("controller checkout is not canonical")
    implementations = {name: capture.read_implementation(root, name, identity["controller_sha"])
                       for name in capture.IMPLEMENTATIONS}
    controller = capture.ARTIFACT_ROOT / "controllers" / identity["controller_sha"]
    exports = {ENV_PREFIX + key.upper(): value for key, value in identity.items()}
    exports.update({ENV_PREFIX + "OPERATION": "restore" if receipt is not None else "verify",
                    ENV_PREFIX + "EVIDENCE_PROVENANCE": provenance,
                    ENV_PREFIX + "DRIVER_PATH": str(controller / capture.IMPLEMENTATIONS[0]),
                    ENV_PREFIX + "DRIVER_SHA256": capture.digest(implementations[capture.IMPLEMENTATIONS[0]]),
                    ENV_PREFIX + "LIBRARY_PATH": str(controller / capture.IMPLEMENTATIONS[1]),
                    ENV_PREFIX + "LIBRARY_SHA256": capture.digest(implementations[capture.IMPLEMENTATIONS[1]]),
                    ENV_PREFIX + "COMPOSE_FILE": str(capture.ARTIFACT_ROOT / "compose" / identity["previous_backend_sha"] / "docker-compose.yml"),
                    ENV_PREFIX + "MANIFEST_PATH": outputs["manifest_path"],
                    ENV_PREFIX + "MANIFEST_SHA256": manifest_hash})
    for key in ("candidate_image_manifest_path", "candidate_image_manifest_sha256",
                "candidate_image_override_path", "candidate_image_override_sha256"):
        exports[ENV_PREFIX + key.upper()] = receipt[key] if receipt is not None else ""
    return {"identity": identity, "baseline": baseline, "candidate": receipt, "exports": exports,
            "provenance": provenance, "baseline_actions_artifact": baseline_artifact,
            "candidate_actions_artifact": candidate_artifact}


def prepare(env: dict[str, str], provenance: str, output: Path) -> dict:
    evidence = load_evidence(env, provenance)
    raw = "".join("export " + key + "=" + shlex.quote(value) + "\n"
                  for key, value in sorted(evidence["exports"].items())).encode()
    private_write(output, raw)
    return evidence


def validate_readback(value: dict, evidence: dict, operation: str) -> dict:
    baseline, identity = evidence["baseline"], evidence["identity"]
    if operation != evidence["exports"][ENV_PREFIX + "OPERATION"]:
        raise capture.CaptureError("readback operation differs from validated admission")
    capture.exact_keys(value, ("schema_version", "operation", "manifest_sha256", "identity",
                              "pre_restore_source_observations", "image_readback_verified", "images", "frontend",
                              "baseline_nonsecret_config_verified", "protected_owners_unchanged", "owners", "public"))
    if (value["schema_version"] != "pantheon.dev-artifact-readback.v1" or value["operation"] != operation or
            value["identity"] != identity or
            value["manifest_sha256"] != evidence["exports"][ENV_PREFIX + "MANIFEST_SHA256"]):
        raise capture.CaptureError("readback scope differs from admitted baseline")
    for key in ("image_readback_verified", "baseline_nonsecret_config_verified", "protected_owners_unchanged"):
        if value[key] is not True:
            raise capture.CaptureError("artifact readback verification is incomplete")
    expected_images = {service: row["image_id"] for service, row in baseline["image_bundle"]["services"].items()}
    if value["images"] != expected_images or value["frontend"] != baseline["frontend"]:
        raise capture.CaptureError("readback is not the exact baseline images and frontend")
    observations = value["pre_restore_source_observations"]
    if (not isinstance(observations, list) or any(not isinstance(item, str) or item not in OBSERVATIONS for item in observations) or
            observations != sorted(set(observations)) or
            (operation == "verify" and observations) or (operation == "restore" and not observations)):
        raise capture.CaptureError("readback source observations are invalid")
    capture.exact_keys(value["owners"], OWNERS)
    for service, row in value["owners"].items():
        if service == "dev-paper-principal-issuer" and row is None:
            continue
        capture.exact_keys(row, ("container_id", "image_id", "started_at", "restart_count"))
        capture.matches(row["container_id"], r"[0-9a-f]{64}")
        capture.matches(row["image_id"], r"sha256:[0-9a-f]{64}")
        capture.matches(row["started_at"], r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z")
        if type(row["restart_count"]) is not int or row["restart_count"] < 0:
            raise capture.CaptureError("protected owner restart count is invalid")
    expected_public = {"source_sha": identity["previous_backend_sha"], "fe_manifest_bytes_verified": True,
                       "strict_auth_denials_verified": True, "authenticated_viewer_readback_verified": True}
    capture.exact_keys(value["public"], expected_public)
    if (value["public"]["source_sha"] != expected_public["source_sha"] or
            any(value["public"][key] is not True for key in expected_public if key != "source_sha")):
        raise capture.CaptureError("public source or strict authenticated readback is incomplete")
    return value


def readback(env: dict[str, str], provenance: str, input_log: Path, output: Path, operation: str) -> dict:
    evidence = load_evidence(env, provenance)
    lines = private_bytes(input_log, limit=4 * 1024 * 1024).splitlines()
    records = [line for line in lines if line.startswith(PREFIX)]
    if len(records) != 1 or len(records[0]) > 65536:
        raise capture.CaptureError("exactly one bounded typed artifact readback is required")
    # Any malformed lookalike is a framing failure, even beside a valid record.
    if any(b"PANTHEON_ARTIFACT_READBACK_V1" in line and line not in records for line in lines):
        raise capture.CaptureError("artifact readback framing is invalid")
    value = json.loads(records[0][len(PREFIX):], object_pairs_hook=capture.unique_object)
    validate_readback(value, evidence, operation)
    private_write(output, capture.encoded(value))
    return value


def compensation_evidence(env: dict[str, str], provenance: str, readback_path: Path, failure_log: Path) -> dict:
    evidence = load_evidence(env, provenance)
    value, _ = document(readback_path, limit=65536)
    operation = evidence["exports"][ENV_PREFIX + "OPERATION"]
    validate_readback(value, evidence, operation)
    # The controller log may contain private diagnostics. Retain only its hash.
    failure_raw = private_bytes(failure_log, limit=32 * 1024 * 1024)
    identity = evidence["identity"]
    return {"schema_version": "pantheon.cross-repo-release-compensation.v2",
            "release_candidate_id": identity["candidate_id"], "outcome": "compensated", "operation": operation,
            "rejected_pair": {"backend_sha": identity["candidate_backend_sha"], "frontend_sha": identity["candidate_frontend_sha"]},
            "restored_pair": {"backend_sha": identity["previous_backend_sha"], "frontend_sha": identity["previous_frontend_sha"]},
            "controller_failure_log_sha256": capture.digest(failure_raw),
            "workflow": {"repository": env["GITHUB_REPOSITORY"], "run_id": identity["run_id"], "run_attempt": identity["attempt"]},
            "provenance": provenance, "baseline_actions_artifact": evidence["baseline_actions_artifact"],
            "candidate_actions_artifact": evidence["candidate_actions_artifact"],
            "baseline_manifest_sha256": evidence["exports"][ENV_PREFIX + "MANIFEST_SHA256"],
            "candidate_image_manifest_sha256": evidence["exports"][ENV_PREFIX + "CANDIDATE_IMAGE_MANIFEST_SHA256"] or None,
            "artifact_readback": value}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--provenance", choices=PROVENANCES, required=True)
    prep.add_argument("--output-env", type=Path, required=True)
    read = commands.add_parser("readback")
    read.add_argument("--provenance", choices=PROVENANCES)
    read.add_argument("--input-log", type=Path, required=True)
    read.add_argument("--output", type=Path, required=True)
    read.add_argument("--operation", choices=("verify", "restore"), required=True)
    args = parser.parse_args(argv)
    try:
        env = dict(os.environ)
        if args.command == "prepare":
            prepare(env, args.provenance, args.output_env)
        else:
            readback(env, args.provenance or env.get(ENV_PREFIX + "EVIDENCE_PROVENANCE", ""),
                     args.input_log, args.output, args.operation)
        return 0
    except (capture.CaptureError, OSError, ValueError, TypeError, AttributeError, KeyError, subprocess.SubprocessError):
        print("[dev-artifact-compensation] evidence rejected; no compensation accepted", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
