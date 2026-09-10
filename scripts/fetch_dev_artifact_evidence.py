#!/usr/bin/env python3
"""Download a fixed same-run Actions artifact and verify its external ZIP seal.

Only non-secret release metadata is transported. Image archives stay on the VM.
The workflow supplies the upload action's ID and digest through job outputs;
neither a filename nor a checksum inside the downloaded archive grants trust.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import select
import stat
import subprocess
import sys
import time
import zipfile
import zlib

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .capture_dev_artifact_baseline import CaptureError, matches, unique_object
    from .dev_candidate_receipt import private_directory
except ImportError:
    from capture_dev_artifact_baseline import CaptureError, matches, unique_object
    from dev_candidate_receipt import private_directory


REPOSITORY = "ajoe734/pantheon"
MAX_ARCHIVE = 2 * 1024 * 1024
FILES = {"baseline": {"artifact-baseline.json", "SHA256SUMS"},
         "candidate": {"candidate-receipt.json"}}


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


def fetch(*, kind: str, artifact_id: str, expected_digest: str, run_id: str,
          attempt: str, output_dir: Path, api=github) -> dict[str, str]:
    for value in (artifact_id, run_id, attempt):
        matches(value, r"[1-9][0-9]{0,19}")
    matches(expected_digest, r"[0-9a-f]{64}")
    if kind not in FILES:
        raise CaptureError("unknown evidence artifact kind")
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
    return {"artifact_id": artifact_id, "artifact_sha256": expected_digest,
            "run_id": run_id, "attempt": attempt, "kind": kind, "output_dir": str(output_dir)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=tuple(FILES), required=True)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--artifact-sha256", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = fetch(kind=args.kind, artifact_id=args.artifact_id, expected_digest=args.artifact_sha256,
                       run_id=args.run_id, attempt=args.attempt, output_dir=args.output_dir)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (CaptureError, OSError, ValueError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile, zlib.error):
        print("[dev-artifact-download] exact authenticated evidence download failed", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
