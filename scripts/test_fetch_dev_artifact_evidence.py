"""Offline exact Actions artifact downloads; no GitHub or VM calls occur."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import time
import warnings
import zipfile

import pytest

from scripts import fetch_dev_artifact_evidence as f


def archive(entries, *, compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w") as handle:
            for name, data, mode in entries:
                item = zipfile.ZipInfo(name)
                item.create_system = 3
                item.external_attr = mode << 16
                item.compress_type = compression
                handle.writestr(item, data)
    return output.getvalue()


def entries(kind):
    return [(name, b'{"synthetic_fixture":true}\n' if name.endswith(".json") else b"fixture-checksum\n",
             stat.S_IFREG | 0o600) for name in sorted(f.FILES[kind])]


@pytest.fixture(params=("baseline", "candidate"))
def case(request, tmp_path):
    kind = request.param
    raw = archive(entries(kind))
    digest = hashlib.sha256(raw).hexdigest()
    metadata = {"id": 54321, "name": f"pantheon-dev-artifact-{kind}-12345-2", "expired": False,
                "digest": "sha256:" + digest, "size_in_bytes": len(raw), "workflow_run": {"id": 12345}}
    calls = []
    def api(path):
        calls.append(path)
        if path == "repos/ajoe734/pantheon/actions/artifacts/54321":
            return json.dumps(metadata).encode()
        if path == "repos/ajoe734/pantheon/actions/artifacts/54321/zip":
            return raw
        raise AssertionError("unexpected or unscoped endpoint: " + path)
    args = dict(kind=kind, artifact_id="54321", expected_digest=digest, run_id="12345", attempt="2",
                output_dir=tmp_path / "download", api=api)
    return {"kind": kind, "raw": raw, "digest": digest, "metadata": metadata, "calls": calls, "args": args}


def test_exact_artifact_download_uses_two_fixed_endpoints_and_private_files(case):
    result = f.fetch(**case["args"])
    assert case["calls"] == ["repos/ajoe734/pantheon/actions/artifacts/54321",
                             "repos/ajoe734/pantheon/actions/artifacts/54321/zip"]
    assert result == {key: case["args"][key] for key in ("artifact_id", "run_id", "attempt", "kind")} | {
        "artifact_sha256": case["digest"], "output_dir": str(case["args"]["output_dir"])}
    output = case["args"]["output_dir"]
    assert {path.name for path in output.iterdir()} == f.FILES[case["kind"]]
    assert output.stat().st_mode & 0o777 == 0o700
    for name, data, _ in entries(case["kind"]):
        assert (output / name).read_bytes() == data
        assert (output / name).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("change", ["id", "id_boolean", "run", "run_boolean", "attempt", "kind", "name",
                                     "expired", "expired_integer", "no_workflow", "digest", "no_digest", "size_zero",
                                     "size_negative", "size_boolean", "size_oversize"])
def test_metadata_rejection_precedes_download_and_filesystem_changes(case, change):
    meta = case["metadata"]
    if change == "id": meta["id"] += 1
    elif change == "id_boolean": meta["id"] = True
    elif change == "run": meta["workflow_run"]["id"] += 1
    elif change == "run_boolean": meta["workflow_run"]["id"] = True
    elif change == "attempt": meta["name"] = meta["name"][:-1] + "3"
    elif change == "kind": meta["name"] = meta["name"].replace(case["kind"], "other")
    elif change == "name": meta["name"] = "pantheon-dev-artifact-baseline-latest"
    elif change == "expired": meta["expired"] = True
    elif change == "expired_integer": meta["expired"] = 0
    elif change == "no_workflow": meta["workflow_run"] = None
    elif change == "digest": meta["digest"] = "sha256:" + "0" * 64
    elif change == "no_digest": meta.pop("digest")
    elif change == "size_zero": meta["size_in_bytes"] = 0
    elif change == "size_negative": meta["size_in_bytes"] = -1
    elif change == "size_boolean": meta["size_in_bytes"] = True
    elif change == "size_oversize": meta["size_in_bytes"] = f.MAX_ARCHIVE + 1
    with pytest.raises(f.CaptureError): f.fetch(**case["args"])
    assert len(case["calls"]) == 1
    assert not case["args"]["output_dir"].exists()


@pytest.mark.parametrize("raw", [b"null", b"[]", b'{"id":54321,"id":54321}', b" " * 65537])
def test_metadata_is_bounded_unique_json(case, raw):
    case["args"]["api"] = lambda _path: raw
    with pytest.raises((f.CaptureError, ValueError)): f.fetch(**case["args"])
    assert not case["args"]["output_dir"].exists()


@pytest.mark.parametrize("field,value", [("artifact_id", "0"), ("artifact_id", "01"), ("artifact_id", "54321/zip"),
                                         ("artifact_id", "1" * 21), ("run_id", "other"), ("attempt", "0"),
                                         ("attempt", "2/../1"), ("expected_digest", "sha256:" + "a" * 64),
                                         ("expected_digest", "A" * 64), ("kind", "other")])
def test_invalid_external_identity_does_not_call_api(case, field, value):
    case["args"][field] = value
    with pytest.raises(f.CaptureError): f.fetch(**case["args"])
    assert case["calls"] == []


def test_archive_length_is_bound_to_authenticated_metadata(case):
    case["metadata"]["size_in_bytes"] += 1
    with pytest.raises(f.CaptureError): f.fetch(**case["args"])
    assert len(case["calls"]) == 2
    assert not case["args"]["output_dir"].exists()


def test_zip_bytes_must_match_external_seal_even_when_contents_are_equivalent(case):
    different = archive(entries(case["kind"]), compression=zipfile.ZIP_STORED)
    case["metadata"]["size_in_bytes"] = len(different)
    original = case["args"]["api"]
    case["args"]["api"] = lambda path: different if path.endswith("/zip") else original(path)
    with pytest.raises(f.CaptureError): f.fetch(**case["args"])
    assert not case["args"]["output_dir"].exists()


@pytest.mark.parametrize("kind", ["baseline", "candidate"])
@pytest.mark.parametrize("change", ["duplicate", "traversal", "absolute", "nested", "extra", "missing", "symlink",
                                     "directory", "fifo", "empty", "oversize", "bzip2", "lzma"])
def test_untrusted_zip_structure_never_becomes_evidence(kind, change):
    values = entries(kind)
    name, data, mode = values[0]
    compression = zipfile.ZIP_DEFLATED
    if change == "duplicate": values.append(values[0])
    elif change == "traversal": values[0] = ("../" + name, data, mode)
    elif change == "absolute": values[0] = ("/tmp/" + name, data, mode)
    elif change == "nested": values[0] = ("nested/" + name, data, mode)
    elif change == "extra": values.append(("unexpected.env", b"fixture-secret", mode))
    elif change == "missing": values.pop()
    elif change == "symlink": values[0] = (name, b"../../unsafe", stat.S_IFLNK | 0o777)
    elif change == "directory": values[0] = (name, data, stat.S_IFDIR | 0o700)
    elif change == "fifo": values[0] = (name, data, stat.S_IFIFO | 0o600)
    elif change == "empty": values[0] = (name, b"", mode)
    elif change == "oversize": values[0] = (name, b"x" * (1024 * 1024 + 1), mode)
    elif change == "bzip2": compression = zipfile.ZIP_BZIP2
    elif change == "lzma": compression = zipfile.ZIP_LZMA
    raw = archive(values, compression=compression)
    with pytest.raises(f.CaptureError):
        f.unpack(raw, expected_digest=hashlib.sha256(raw).hexdigest(), kind=kind)


@pytest.mark.parametrize("flag", ["encrypted", "unknown_compression"])
def test_archive_flags_are_checked_before_decompression(flag):
    raw = bytearray(archive(entries("candidate")))
    # Mutate both local and central headers, then provide the matching external
    # seal so rejection demonstrates the entry policy rather than a bad digest.
    local, central = raw.index(b"PK\x03\x04"), raw.index(b"PK\x01\x02")
    offset = (6, 8) if flag == "encrypted" else (8, 10)
    for position, delta in zip((local, central), offset):
        struct.pack_into("<H", raw, position + delta, 1 if flag == "encrypted" else 99)
    with pytest.raises(f.CaptureError):
        f.unpack(bytes(raw), expected_digest=hashlib.sha256(raw).hexdigest(), kind="candidate")


def test_actual_zip_size_is_bounded_even_with_matching_external_digest():
    raw = b"x" * (f.MAX_ARCHIVE + 1)
    with pytest.raises(f.CaptureError):
        f.unpack(raw, expected_digest=hashlib.sha256(raw).hexdigest(), kind="candidate")


@pytest.mark.parametrize("change", ["relative", "symlink", "parent_symlink", "public", "existing_file", "existing_symlink"])
def test_destinations_are_canonical_private_and_never_overwritten(case, tmp_path, change):
    output = case["args"]["output_dir"]
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"untouched fixture")
    if change == "relative": case["args"]["output_dir"] = Path("relative-download")
    elif change == "symlink":
        real = tmp_path / "real"; real.mkdir(mode=0o700); output.symlink_to(real)
    elif change == "parent_symlink":
        real = tmp_path / "real"; real.mkdir(mode=0o700)
        linked = tmp_path / "linked"; linked.symlink_to(real)
        case["args"]["output_dir"] = linked / "download"
    elif change == "public": output.mkdir(mode=0o755)
    else:
        output.mkdir(mode=0o700)
        first_name = sorted(f.FILES[case["kind"]])[0]
        if change == "existing_file": (output / first_name).write_bytes(sentinel.read_bytes())
        else: (output / first_name).symlink_to(sentinel)
    with pytest.raises((f.CaptureError, OSError)): f.fetch(**case["args"])
    assert sentinel.read_bytes() == b"untouched fixture"
    if change == "existing_file": assert (output / first_name).read_bytes() == sentinel.read_bytes()


def test_file_and_parent_are_fsynced_before_fetch_returns(case, monkeypatch):
    calls = []
    fsync = os.fsync
    def durable(fd):
        calls.append(stat.S_IFMT(os.fstat(fd).st_mode))
        fsync(fd)
    monkeypatch.setattr(f.os, "fsync", durable)
    f.fetch(**case["args"])
    assert calls == [stat.S_IFREG] * len(f.FILES[case["kind"]]) + [stat.S_IFDIR]


def fake_gh(monkeypatch, source):
    real_popen = subprocess.Popen
    observed = []
    def spawn(command, **kwargs):
        observed.append({"command": command, "kwargs": kwargs})
        process = real_popen([sys.executable, "-c", source], **kwargs)
        observed[-1]["process"] = process
        return process
    monkeypatch.setattr(f.subprocess, "Popen", spawn)
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-actions-reader")
    monkeypatch.setenv("GH_TOKEN", "fixture-unrelated-token")
    monkeypatch.setenv("GH_ENTERPRISE_TOKEN", "fixture-unrelated-token")
    monkeypatch.setenv("GITHUB_ENTERPRISE_TOKEN", "fixture-unrelated-token")
    monkeypatch.setenv("GH_HOST", "unapproved.invalid")
    return observed


def test_gh_transport_pins_github_host_and_removes_competing_credentials(monkeypatch):
    observed = fake_gh(monkeypatch, "import os; os.write(1, b'fixture-response')")
    assert f.github("repos/ajoe734/pantheon/actions/artifacts/54321") == b"fixture-response"
    call = observed[0]
    assert call["command"] == ["gh", "api", "--hostname", "github.com", "--method", "GET",
                                "repos/ajoe734/pantheon/actions/artifacts/54321"]
    assert "fixture-actions-reader" not in " ".join(call["command"])
    env = call["kwargs"]["env"]
    assert env["GITHUB_TOKEN"] == "fixture-actions-reader"
    assert env["GH_HOST"] == "github.com" and env["GH_PROMPT_DISABLED"] == "1"
    assert not any(key in env for key in ("GH_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"))
    assert call["kwargs"]["stderr"] == subprocess.DEVNULL


def test_transport_rejects_oversized_stream_and_reaps_child(monkeypatch):
    observed = fake_gh(monkeypatch, "import os,time; os.write(1, b'x' * (3 * 1024 * 1024)); time.sleep(60)")
    started = time.monotonic()
    with pytest.raises(f.CaptureError): f.github("fixture-no-network")
    assert time.monotonic() - started < 5
    assert observed[0]["process"].poll() is not None


def test_transport_failure_does_not_expose_signed_urls_or_tokens(monkeypatch, capfd):
    observed = fake_gh(monkeypatch, "import sys; print('fixture-secret-signed-url', file=sys.stderr); sys.exit(1)")
    with pytest.raises(f.CaptureError): f.github("fixture-no-network")
    captured = capfd.readouterr()
    assert "fixture-secret" not in captured.err + captured.out
    assert observed[0]["process"].poll() == 1


def test_transport_requires_explicit_actions_credential_before_process(monkeypatch):
    def forbidden(*_args, **_kwargs): raise AssertionError("must not start a process")
    monkeypatch.setattr(f.subprocess, "Popen", forbidden)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(f.CaptureError): f.github("fixture-no-network")


def test_cli_imports_only_its_checked_in_siblings_under_safe_python_path(tmp_path):
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONSAFEPATH"] = "1"
    result = subprocess.run(
        [sys.executable, str(Path(f.__file__).resolve()), "--help"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


# --- DEV-DEPLOY-RESTORE-ACCEPTED-BASELINE-AFTER-FAILED-COMPENSATION-20261116 ---

def _sealed_attempt_fixtures(run_id: str = "43718489118", attempt: str = "1"):
    from scripts.test_capture_dev_artifact_baseline import manifest as build_manifest
    from scripts import capture_dev_artifact_baseline as capture
    from scripts import dev_candidate_receipt as candidate

    identity = {
        "candidate_id": "56a3a01c6e3ad250c403daac505ca4270a5f108da737717c77478ee208e5f826",
        "run_id": run_id,
        "attempt": attempt,
        "controller_sha": "725836718295f445fd9923790b58ed29842d9869",
        "candidate_backend_sha": "725836718295f445fd9923790b58ed29842d9869",
        "candidate_frontend_sha": "87fc32ab860325573a205500ff381092c2012617",
        "previous_backend_sha": "94b73806ae004049f82f26d1d225f290086a4942",
        "previous_frontend_sha": "87fc32ab860325573a205500ff381092c2012617",
    }
    manifest = build_manifest(identity)
    manifest_raw = capture.encoded(manifest)
    manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    baseline_zip = archive([
        ("artifact-baseline.json", manifest_raw, stat.S_IFREG | 0o600),
        ("SHA256SUMS", f"{manifest_sha256}  artifact-baseline.json\n".encode(), stat.S_IFREG | 0o600),
    ])
    folder = capture.ARTIFACT_ROOT / f"baseline-{identity['run_id']}-{identity['attempt']}-{identity['candidate_id']}"
    services = {}
    for index, service in enumerate(("operator-bff", "agora-interaction-worker", "loop-run-projector-scheduler"), 1):
        services[service] = {
            "image_id": "sha256:" + str(index) * 64,
            "oci_revision": identity["candidate_backend_sha"],
            "git_sha": None if service == "loop-run-projector-scheduler" else identity["candidate_backend_sha"],
            "compose_image": "pantheon-" + service,
        }
    override = {"services": {service: {"image": row["image_id"], "pull_policy": "never"}
                             for service, row in services.items()}}
    override["services"]["operator-bff"]["environment"] = {
        "BFF_IMAGE_DIGEST": services["operator-bff"]["image_id"],
    }
    override_sha = hashlib.sha256(capture.encoded(override)).hexdigest()
    inner_receipt = {
        "schema_version": "pantheon.dev-candidate-image-admission.v2",
        "environment": "dev",
        "project_id": "pantheon-dev-20260902",
        "vm": "pantheon-dev-deploy",
        "identity": identity,
        "seal_lease_id": manifest["capture_lease_id"],
        "sealed_at": "2026-11-15T13:45:00Z",
        "baseline_manifest_sha256": manifest_sha256,
        "candidate_compose_sha256": "c" * 64,
        "candidate_bundle_sha256": "d" * 64,
        "image_override_sha256": override_sha,
        "services": services,
    }
    inner_sha = hashlib.sha256(capture.encoded(inner_receipt)).hexdigest()
    outer_receipt = {
        "candidate_image_manifest": inner_receipt,
        "candidate_image_manifest_path": str(folder / "candidate-images.json"),
        "candidate_image_manifest_sha256": inner_sha,
        "candidate_image_override_path": str(folder / "candidate-images.override.json"),
        "candidate_image_override_sha256": override_sha,
    }
    receipt_raw = capture.encoded(outer_receipt)
    candidate_zip = archive([
        ("candidate-receipt.json", receipt_raw, stat.S_IFREG | 0o600),
    ])
    return {
        "identity": identity,
        "manifest": manifest,
        "manifest_raw": manifest_raw,
        "manifest_sha256": manifest_sha256,
        "baseline_zip": baseline_zip,
        "baseline_zip_sha256": hashlib.sha256(baseline_zip).hexdigest(),
        "receipt": outer_receipt,
        "receipt_raw": receipt_raw,
        "candidate_sha256": inner_sha,
        "candidate_zip": candidate_zip,
        "candidate_zip_sha256": hashlib.sha256(candidate_zip).hexdigest(),
    }


def test_fetch_discovers_attempt_1_artifacts_when_run_is_on_later_attempt(tmp_path):
    fixtures = _sealed_attempt_fixtures(run_id="43718489118", attempt="1")
    listing = {
        "total_count": 4,
        "artifacts": [
            {
                "id": 999999,
                "name": "pantheon-dev-artifact-baseline-43718489118-2",
                "expired": False,
                "digest": "sha256:" + "9" * 64,
                "size_in_bytes": 123,
                "workflow_run": {"id": 43718489118, "run_attempt": 2},
            },
            {
                "id": 14163830735,
                "name": "pantheon-dev-artifact-baseline-43718489118-1",
                "expired": False,
                "digest": "sha256:" + fixtures["baseline_zip_sha256"],
                "size_in_bytes": len(fixtures["baseline_zip"]),
                "workflow_run": {"id": 43718489118, "run_attempt": 2},
            },
            {
                "id": 14164431785,
                "name": "pantheon-dev-artifact-candidate-43718489118-1",
                "expired": False,
                "digest": "sha256:" + fixtures["candidate_zip_sha256"],
                "size_in_bytes": len(fixtures["candidate_zip"]),
                "workflow_run": {"id": 43718489118, "run_attempt": 2},
            },
        ],
    }
    calls = []

    def api(path: str) -> bytes:
        calls.append(path)
        if path == "repos/ajoe734/pantheon/actions/runs/43718489118/artifacts":
            return json.dumps(listing).encode()
        if path == "repos/ajoe734/pantheon/actions/artifacts/14163830735/zip":
            return fixtures["baseline_zip"]
        if path == "repos/ajoe734/pantheon/actions/artifacts/14164431785/zip":
            return fixtures["candidate_zip"]
        raise AssertionError(path)

    baseline_out = f.fetch(
        kind="baseline",
        run_id="43718489118",
        attempt="1",
        output_dir=tmp_path / "baseline",
        api=api,
    )
    candidate_out = f.fetch(
        kind="candidate",
        run_id="43718489118",
        attempt="1",
        output_dir=tmp_path / "candidate",
        api=api,
    )
    assert baseline_out == {
        "artifact_id": "14163830735",
        "artifact_sha256": fixtures["baseline_zip_sha256"],
        "run_id": "43718489118",
        "attempt": "1",
        "kind": "baseline",
        "output_dir": str(tmp_path / "baseline"),
        "manifest_path": str(tmp_path / "baseline/artifact-baseline.json"),
        "manifest_sha256": fixtures["manifest_sha256"],
        "previous_backend_sha": "94b73806ae004049f82f26d1d225f290086a4942",
        "previous_frontend_sha": "87fc32ab860325573a205500ff381092c2012617",
    }
    assert candidate_out == {
        "artifact_id": "14164431785",
        "artifact_sha256": fixtures["candidate_zip_sha256"],
        "run_id": "43718489118",
        "attempt": "1",
        "kind": "candidate",
        "output_dir": str(tmp_path / "candidate"),
        "receipt_file": str(tmp_path / "candidate/candidate-receipt.json"),
        "candidate_image_manifest_sha256": fixtures["candidate_sha256"],
    }
    assert calls == [
        "repos/ajoe734/pantheon/actions/runs/43718489118/artifacts",
        "repos/ajoe734/pantheon/actions/artifacts/14163830735/zip",
        "repos/ajoe734/pantheon/actions/runs/43718489118/artifacts",
        "repos/ajoe734/pantheon/actions/artifacts/14164431785/zip",
    ]


@pytest.mark.parametrize("artifact_id,expected_digest", [("12345", None), (None, "a" * 64)])
def test_fetch_rejects_partial_artifact_id_or_digest(tmp_path, artifact_id, expected_digest):
    with pytest.raises(f.CaptureError, match="must be provided together"):
        f.fetch(
            kind="baseline",
            artifact_id=artifact_id,
            expected_digest=expected_digest,
            run_id="43718489118",
            attempt="1",
            output_dir=tmp_path / "out",
            api=lambda _p: b"{}",
        )


def test_fetch_discovery_rejects_missing_or_corrupt_inner_sums(tmp_path):
    fixtures = _sealed_attempt_fixtures()
    bad_zip = archive([
        ("artifact-baseline.json", fixtures["manifest_raw"], stat.S_IFREG | 0o600),
        ("SHA256SUMS", ("0" * 64 + "  artifact-baseline.json\n").encode(), stat.S_IFREG | 0o600),
    ])
    listing = {
        "artifacts": [{
            "id": 14163830735,
            "name": "pantheon-dev-artifact-baseline-43718489118-1",
            "expired": False,
            "digest": "sha256:" + hashlib.sha256(bad_zip).hexdigest(),
            "size_in_bytes": len(bad_zip),
            "workflow_run": {"id": 43718489118},
        }]
    }
    with pytest.raises(f.CaptureError, match="SHA256SUMS does not match"):
        f.fetch(
            kind="baseline",
            run_id="43718489118",
            attempt="1",
            output_dir=tmp_path / "out",
            api=lambda path: json.dumps(listing).encode() if path.endswith("/artifacts") else bad_zip,
        )
    assert not (tmp_path / "out").exists()


# --- DEV-DEPLOY-RESTORE-BASELINE-ENV-FIX-20261116 ---


def test_build_restore_remote_script_exports_all_shell_and_runtime_vars_and_executes_under_env_i(tmp_path):
    import re
    import shutil

    repo_root = Path(f.__file__).resolve().parents[1]
    deploy_text = (repo_root / "scripts" / "deploy_nonprod_vm.sh").read_text(encoding="utf-8")

    # 1. Static parity check: every uppercase variable expanded without `:-` in
    #    memory_llm_bridge_env, with_dev_bff_runtime_env, and run_dev_artifact_driver
    #    must be exported by build_restore_remote_script (except the runtime guard FD).
    referenced_vars: set[str] = set()
    for fn_name in ("memory_llm_bridge_env", "with_dev_bff_runtime_env", "run_dev_artifact_driver"):
        fn_text = f._extract_shell_function(deploy_text, fn_name)
        for match in re.finditer(r"\$\{([A-Z][A-Z0-9_]*)\}", fn_text):
            referenced_vars.add(match.group(1))

    artifact_root = tmp_path / "release-artifacts"
    vm_repo = tmp_path / "pantheon"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(mode=0o700)
    recorder = tmp_path / "driver_invocations.jsonl"

    stub_py = bin_dir / "python3"
    stub_py.write_text(
        f"""#!/bin/bash
set -euo pipefail
if [[ "${{1:-}}" == "-c" ]]; then
  exit 0
fi
if [[ "${{1:-}}" == "-" ]]; then
  cat >/dev/null
  exit 0
fi
{shutil.which("python3") or sys.executable} -c '
import json, os, pathlib, sys
rec = pathlib.Path(sys.argv[1])
args = sys.argv[2:]
env_keys = [
    "GIT_SHA",
    "PANTHEON_ENV",
    "LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS",
    "PANTHEON_BFF_AUTH_STUB",
    "PANTHEON_BFF_AUTH_MODE",
    "PANTHEON_BFF_MFA_CLAIMS",
    "PANTHEON_BFF_MFA_VALUES",
    "PANTHEON_BFF_ROLE_CLAIMS",
    "PANTHEON_BFF_ALLOWED_TENANTS",
    "PANTHEON_ASSISTANT_KERNEL_ENABLED",
    "PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH",
    "PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH",
    "PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS",
    "PANTHEON_BFF_STUB_CAPABILITIES",
    "MANAGEMENT_AI_STORE_BACKEND",
    "MANAGEMENT_AI_STORE_SCHEMA",
    "MANAGEMENT_AI_DATABASE_URL",
    "PANTHEON_MGMT_AI_ATTACH_BUCKET",
    "PANTHEON_MGMT_AI_ATTACH_LOCATION",
    "AGORA_PRIVATE_CONTENT_DEV_KEK",
    "FINMIND_API_TOKEN",
    "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED",
    "RECONCILIATION_DRIFT_STORE_BACKEND",
    "RECONCILIATION_DRIFT_STORE_DSN",
    "MEMORY_LLM_BRIDGE_ENABLED",
    "MEMORY_LLM_BRIDGE_DAILY_BUDGET",
]
rec.write_text(json.dumps({{"argv": args, "env": {{k: os.environ.get(k) for k in env_keys}}}}) + "\\n", encoding="utf-8")
print(json.dumps({{"schema_version": "pantheon.dev-artifact-readback.v1", "operation": "restore"}}))
' {str(recorder)!r} "$@"
""",
        encoding="utf-8",
    )
    stub_py.chmod(0o755)

    identity = {
        "candidate_id": "56a3a01c6e3ad250c403daac505ca4270a5f108da737717c77478ee208e5f826",
        "run_id": "43718489118",
        "attempt": "1",
        "controller_sha": "725836718295f445fd9923790b58ed29842d9869",
        "candidate_backend_sha": "725836718295f445fd9923790b58ed29842d9869",
        "candidate_frontend_sha": "87fc32ab860325573a205500ff381092c2012617",
        "previous_backend_sha": "94b73806ae004049f82f26d1d225f290086a4942",
        "previous_frontend_sha": "87fc32ab860325573a205500ff381092c2012617",
    }
    current_controller_sha = "d" * 40
    script_bytes = f.build_restore_remote_script(
        repo_root=repo_root,
        current_controller_sha=current_controller_sha,
        implementations={
            "dev_release_artifact_driver.py": b"# driver\n",
            "dev_release_artifacts.py": b"# lib\n",
        },
        identity=identity,
        manifest_path="/tmp/manifest.json",
        manifest_sha256="1" * 64,
        candidate_image_manifest_path="/tmp/candidate.json",
        candidate_image_manifest_sha256="2" * 64,
        compose_file="/tmp/docker-compose.yml",
        env={
            "PANTHEON_DEV_ENVIRONMENT_LEASE_GUARD_LEASE_ID": "11111111-1111-4111-8111-111111111111",
            "DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH": "sha256:test-passphrase-hash",
            "DEV_MANAGEMENT_AI_DB_USER": "custom_mgmt_user",
            "DEV_MANAGEMENT_AI_DB_PASSWORD": "custom_mgmt_password",
            "DEV_MANAGEMENT_AI_DATABASE_URL": "",
            "DEV_MANAGEMENT_AI_ATTACH_BUCKET": "pantheon-dev-mgmt-attach",
            "DEV_AGORA_PRIVATE_CONTENT_DEV_KEK": "test-agora-kek",
            "FINMIND_API_TOKEN": "test-finmind-token",
            "DEV_PAPER_PRINCIPALS_AUTHORIZED": "true",
        },
        artifact_root=artifact_root,
        vm_source_repo=vm_repo,
    )
    script_text = script_bytes.decode("utf-8")
    export_line = next(line for line in script_text.splitlines() if line.startswith("export "))
    exported_keys = set(re.findall(r"([A-Z][A-Z0-9_]*)=", export_line))
    missing = (referenced_vars - {"PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD"}) - exported_keys
    assert not missing, f"Unexported variables referenced in restore functions: {sorted(missing)}"

    # 2. Prove that stripping PANTHEON_ASSISTANT_KERNEL_ENABLED reproduces the exact
    #    bash -u unbound variable exit 1 failure before reaching the artifact driver.
    broken_script = re.sub(r"\bPANTHEON_ASSISTANT_KERNEL_ENABLED=[^\s]+", "", script_text, count=1).encode("utf-8")
    r_fd, w_fd = os.pipe()
    try:
        os.write(w_fd, b"pulse\n")
        broken_proc = subprocess.run(
            [
                "env",
                "-i",
                f"PATH={bin_dir}:/usr/bin:/bin",
                f"PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD={r_fd}",
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-euo",
                "pipefail",
                "-s",
            ],
            input=broken_script,
            pass_fds=(r_fd,),
            capture_output=True,
            timeout=15,
        )
    finally:
        os.close(r_fd)
        os.close(w_fd)
    assert broken_proc.returncode == 1
    assert b"PANTHEON_ASSISTANT_KERNEL_ENABLED: unbound variable" in broken_proc.stderr
    assert not recorder.exists()

    # 3. Prove the fixed script executes cleanly under `env -i bash --noprofile --norc -euo pipefail`,
    #    reaches the artifact driver, and emits PANTHEON_ARTIFACT_READBACK_V1 with exit 0.
    r_fd, w_fd = os.pipe()
    try:
        os.write(w_fd, b"pulse\n")
        ok_proc = subprocess.run(
            [
                "env",
                "-i",
                f"PATH={bin_dir}:/usr/bin:/bin",
                f"PANTHEON_DEV_ARTIFACT_GUARD_CHANNEL_FD={r_fd}",
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-euo",
                "pipefail",
                "-s",
            ],
            input=script_bytes,
            pass_fds=(r_fd,),
            capture_output=True,
            timeout=15,
        )
    finally:
        os.close(r_fd)
        os.close(w_fd)
    assert ok_proc.returncode == 0, ok_proc.stderr.decode("utf-8", errors="replace")
    assert ok_proc.stdout.startswith(b"PANTHEON_ARTIFACT_READBACK_V1 ")
    assert json.loads(ok_proc.stdout.removeprefix(b"PANTHEON_ARTIFACT_READBACK_V1 ").strip()) == {
        "schema_version": "pantheon.dev-artifact-readback.v1",
        "operation": "restore",
    }
    assert recorder.is_file()
    recorded = json.loads(recorder.read_text(encoding="utf-8").strip())
    assert recorded["argv"][1] == "restore"
    assert "--scope" in recorded["argv"] and recorded["argv"][recorded["argv"].index("--scope") + 1] == "stack"
    rec_env = recorded["env"]
    assert rec_env["GIT_SHA"] == identity["previous_backend_sha"]
    assert rec_env["PANTHEON_ENV"] == "dev"
    assert rec_env["LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS"] == "300"
    assert rec_env["PANTHEON_BFF_AUTH_STUB"] == "false"
    assert rec_env["PANTHEON_BFF_AUTH_MODE"] == "strict"
    assert rec_env["PANTHEON_BFF_MFA_CLAIMS"] == "amr,acr,mfa,mfa_verified,firebase.sign_in_second_factor"
    assert rec_env["PANTHEON_BFF_MFA_VALUES"] == "true,1,yes,mfa,otp,totp,webauthn"
    assert rec_env["PANTHEON_BFF_ROLE_CLAIMS"] == "roles,role"
    assert rec_env["PANTHEON_BFF_ALLOWED_TENANTS"] == "tenant-dev,pantheon-dev"
    assert rec_env["PANTHEON_ASSISTANT_KERNEL_ENABLED"] == "true"
    assert rec_env["PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH"] == "/data/bff/assistant-control-mode.json"
    assert rec_env["PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH"] == "sha256:test-passphrase-hash"
    assert rec_env["PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS"] == "300"
    assert rec_env["PANTHEON_BFF_STUB_CAPABILITIES"] == "assistant.kernel.debug,assistant.kernel.repair"
    assert rec_env["MANAGEMENT_AI_STORE_BACKEND"] == "postgres"
    assert rec_env["MANAGEMENT_AI_STORE_SCHEMA"] == "management_ai"
    assert rec_env["MANAGEMENT_AI_DATABASE_URL"] == "postgresql://custom_mgmt_user:custom_mgmt_password@postgres:5432/pantheon"
    assert rec_env["PANTHEON_MGMT_AI_ATTACH_BUCKET"] == "pantheon-dev-mgmt-attach"
    assert rec_env["PANTHEON_MGMT_AI_ATTACH_LOCATION"] == "asia-east1"
    assert rec_env["AGORA_PRIVATE_CONTENT_DEV_KEK"] == "test-agora-kek"
    assert rec_env["FINMIND_API_TOKEN"] == "test-finmind-token"
    assert rec_env["PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED"] == "true"
    assert rec_env["RECONCILIATION_DRIFT_STORE_BACKEND"] == "postgres"
    assert rec_env["RECONCILIATION_DRIFT_STORE_DSN"] == "postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon"
    assert rec_env["MEMORY_LLM_BRIDGE_ENABLED"] == "false"


