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
