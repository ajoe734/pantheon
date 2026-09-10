"""Typed, non-secret candidate seal receiver for the guarded SSH transport.

An ACK is returned only AFTER immutable runner-local receipt bytes and their
directory are fsynced. The remote producer waits for that ACK before up. This
module does not acquire authority, discover current images, or run a command.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
import time
import uuid

# Sanitized workflow steps set PYTHONSAFEPATH=1. Trust only this checked-in
# script's sibling directory, never the caller's cwd or an ambient PYTHONPATH.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .capture_dev_artifact_baseline import ARTIFACT_ROOT, CaptureError, digest, encoded, exact_keys, matches, unique_object
except ImportError:
    from capture_dev_artifact_baseline import ARTIFACT_ROOT, CaptureError, digest, encoded, exact_keys, matches, unique_object


PREFIX = b"PANTHEON_ARTIFACT_CANDIDATE_SEAL_V1 "
SCHEMA = "pantheon.dev-candidate-image-admission.v1"
SERVICES = ("operator-bff", "agora-interaction-worker", "loop-run-projector-scheduler")


def private_directory(path: Path) -> None:
    if not path.is_absolute() or path.resolve() != path:
        raise CaptureError("receipt directory is not canonical")
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise CaptureError("receipt directory must be private and owned by this user")


def private_document(path: Path) -> dict:
    private_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise CaptureError("receipt input is not a private regular file")
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise CaptureError("receipt input exceeds its bound")
    return json.loads(raw, object_pairs_hook=unique_object)


def validate_context(context: dict) -> dict:
    exact_keys(context, ("schema_version", "identity", "baseline_manifest_sha256", "guard_lease_id"))
    if context["schema_version"] != "pantheon.dev-candidate-receipt-context.v1":
        raise CaptureError("candidate context schema mismatch")
    identity = context["identity"]
    exact_keys(identity, ("candidate_id", "run_id", "attempt", "controller_sha", "candidate_backend_sha",
                          "candidate_frontend_sha", "previous_backend_sha", "previous_frontend_sha"))
    for field, value in identity.items():
        pattern = r"[0-9a-f]{64}" if field == "candidate_id" else r"[0-9a-f]{40}"
        if field == "run_id": pattern = r"[1-9][0-9]{0,19}"
        if field == "attempt": pattern = r"[1-9][0-9]{0,9}"
        matches(value, pattern)
    matches(context["baseline_manifest_sha256"], r"[0-9a-f]{64}")
    if str(uuid.UUID(context["guard_lease_id"])) != context["guard_lease_id"]:
        raise CaptureError("candidate guard context mismatch")
    return identity


def validate_receipt(result: dict, context: dict) -> str:
    identity = validate_context(context)
    exact_keys(result, ("candidate_image_manifest_path", "candidate_image_manifest_sha256", "candidate_image_manifest",
                        "candidate_image_override_path", "candidate_image_override_sha256"))
    record = result["candidate_image_manifest"]
    exact_keys(record, ("schema_version", "environment", "project_id", "vm", "identity", "seal_lease_id",
                        "sealed_at", "baseline_manifest_sha256", "candidate_compose_sha256", "image_override_sha256", "services"))
    if (record["schema_version"] != SCHEMA or record["environment"] != "dev" or
        record["project_id"] != "pantheon-dev-20260902" or record["vm"] != "pantheon-dev-deploy" or
        record["identity"] != identity or record["seal_lease_id"] != context["guard_lease_id"] or
        record["baseline_manifest_sha256"] != context["baseline_manifest_sha256"]):
        raise CaptureError("candidate receipt differs from the guarded admission")
    matches(record["sealed_at"], r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
    try:
        time.strptime(record["sealed_at"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise CaptureError("candidate seal timestamp is invalid") from exc
    matches(record["candidate_compose_sha256"], r"[0-9a-f]{64}")
    exact_keys(record["services"], SERVICES)
    for service, row in record["services"].items():
        exact_keys(row, ("image_id", "oci_revision", "git_sha", "compose_image"))
        matches(row["image_id"], r"sha256:[0-9a-f]{64}")
        git_shas = (None, identity["candidate_backend_sha"]) if service == "loop-run-projector-scheduler" else (identity["candidate_backend_sha"],)
        if (row["oci_revision"] != identity["candidate_backend_sha"] or
            row["git_sha"] not in git_shas or row["compose_image"] != "pantheon-" + service):
            raise CaptureError("candidate service image/source ownership mismatch")
    override = {"services": {service: {"image": row["image_id"], "pull_policy": "never"}
                             for service, row in record["services"].items()}}
    if record["image_override_sha256"] != digest(encoded(override)):
        raise CaptureError("candidate immutable override digest mismatch")
    if result["candidate_image_override_sha256"] != record["image_override_sha256"]:
        raise CaptureError("candidate override wrapper mismatch")
    expected_hash = digest(encoded(record))
    if result["candidate_image_manifest_sha256"] != expected_hash:
        raise CaptureError("candidate manifest byte seal mismatch")
    folder = ARTIFACT_ROOT / f"baseline-{identity['run_id']}-{identity['attempt']}-{identity['candidate_id']}"
    if (result["candidate_image_manifest_path"] != str(folder / "candidate-images.json") or
        result["candidate_image_override_path"] != str(folder / "candidate-images.override.json")):
        raise CaptureError("candidate receipt is not in the fixed run directory")
    return expected_hash


class CandidateReceiptObserver:
    def __init__(self, *, context_path: Path, output_path: Path):
        self.context = private_document(context_path)
        validate_context(self.context)
        private_directory(output_path.parent)
        if output_path.exists() or output_path.is_symlink():
            raise CaptureError("candidate receipt output already exists")
        self.output = output_path
        self.received = False

    def __call__(self, line: bytes) -> str | None:
        if not line.startswith(PREFIX):
            return None
        if self.received or len(line) > 65536:
            raise CaptureError("duplicate or oversized candidate receipt")
        result = json.loads(line[len(PREFIX):], object_pairs_hook=unique_object)
        ack = validate_receipt(result, self.context)
        raw = encoded(result)
        fd = os.open(self.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        fd = os.open(self.output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
        self.received = True
        return ack


def finalize(context_path: Path, receipt_path: Path, output_path: Path) -> None:
    """Expose only a previously saved receipt; never produce or acknowledge one."""
    context = private_document(context_path)
    validate_context(context)
    result = private_document(receipt_path)
    sealed = validate_receipt(result, context)
    # Preserve the observer's canonical bytes. This is an output consumer, not
    # a second admission path that repairs or accepts a differently encoded file.
    if receipt_path.read_bytes() != encoded(result):
        raise CaptureError("retained receipt bytes are not canonical")
    outputs = {key: result[key] for key in ("candidate_image_manifest_path",
               "candidate_image_override_path", "candidate_image_override_sha256")}
    outputs.update(candidate_image_manifest_sha256=sealed, receipt_file=str(receipt_path))
    if any(not isinstance(value, str) or "\n" in value or "\r" in value for value in outputs.values()):
        raise CaptureError("invalid candidate workflow output")
    with output_path.open("a", encoding="utf-8") as stream:
        stream.write("".join(f"{key}={value}\n" for key, value in outputs.items()))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate a saved candidate receipt for evidence upload")
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        finalize(args.context, args.receipt, args.github_output)
        return 0
    except (CaptureError, OSError, ValueError, TypeError, AttributeError):
        print("[dev-candidate-receipt] no valid retained candidate seal", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
