from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TRANSPORT = ROOT / "scripts" / "dev_vm_ssh.sh"


def _generate_credentials(tmp_path: Path, host: str = "203.0.113.10") -> tuple[str, str]:
    source_key = tmp_path / "source-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(source_key)],
        check=True,
    )
    public_parts = source_key.with_suffix(".pub").read_text(encoding="utf-8").split()
    known_hosts = f"{host} {public_parts[0]} {public_parts[1]}"
    return source_key.read_text(encoding="utf-8"), known_hosts


def _prepare(tmp_path: Path, *, host: str = "203.0.113.10") -> dict[str, str]:
    private_key, known_hosts = _generate_credentials(tmp_path, host)
    credential_dir = tmp_path / "prepared"
    result = subprocess.run(
        [str(TRANSPORT), "prepare", str(credential_dir)],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "DEV_DEPLOY_SSH_PRIVATE_KEY": private_key,
            "DEV_DEPLOY_SSH_KNOWN_HOSTS": known_hosts,
            "DEV_DEPLOY_SSH_HOST": host,
        },
    )
    values = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert stat.S_IMODE(Path(values["DEV_DEPLOY_SSH_KEY_FILE"]).stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(values["DEV_DEPLOY_SSH_KNOWN_HOSTS_FILE"]).stat().st_mode) == 0o600
    return values


def _fake_command(path: Path, capture_prefix: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > '{capture_prefix}.args'\n"
        f"cat > '{capture_prefix}.stdin'\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_prepare_and_exec_use_pinned_direct_ssh_without_gcloud(tmp_path: Path) -> None:
    host = "203.0.113.10"
    prepared = _prepare(tmp_path, host=host)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "ssh-capture"
    _fake_command(fake_bin / "ssh", capture)
    gcloud_marker = tmp_path / "gcloud-called"
    (fake_bin / "gcloud").write_text(
        f"#!/bin/sh\ntouch '{gcloud_marker}'\nexit 99\n", encoding="utf-8"
    )
    (fake_bin / "gcloud").chmod(0o755)

    result = subprocess.run(
        [str(TRANSPORT), "exec", "printf remote-ok"],
        input="streamed-input\n",
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            **prepared,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "DEV_DEPLOY_SSH_HOST": host,
            "DEV_DEPLOY_SSH_USER": "pantheon-ci",
        },
    )

    assert result.returncode == 0, result.stderr
    args = Path(f"{capture}.args").read_text(encoding="utf-8").splitlines()
    assert "StrictHostKeyChecking=yes" in args
    assert f"UserKnownHostsFile={prepared['DEV_DEPLOY_SSH_KNOWN_HOSTS_FILE']}" in args
    assert "pantheon-ci@203.0.113.10" in args
    assert args[-1] == "printf remote-ok"
    assert Path(f"{capture}.stdin").read_text(encoding="utf-8") == "streamed-input\n"
    assert not gcloud_marker.exists()


def test_copy_from_uses_same_pinned_transport(tmp_path: Path) -> None:
    host = "203.0.113.11"
    prepared = _prepare(tmp_path, host=host)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "scp-capture"
    _fake_command(fake_bin / "scp", capture)

    result = subprocess.run(
        [str(TRANSPORT), "copy-from", "/tmp/evidence.json", str(tmp_path / "evidence.json")],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            **prepared,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "DEV_DEPLOY_SSH_HOST": host,
            "REMOTE_USER": "deploy-user",
        },
    )

    assert result.returncode == 0, result.stderr
    args = Path(f"{capture}.args").read_text(encoding="utf-8").splitlines()
    assert "deploy-user@203.0.113.11:/tmp/evidence.json" in args
    assert args[-1] == str(tmp_path / "evidence.json")


def test_exec_rejects_missing_or_permissive_private_key(tmp_path: Path, monkeypatch) -> None:
    # CI supplies both explicit paths and an implicit per-run credential path.
    # This case tests absent credentials, independently of both sources.
    monkeypatch.setenv("DEV_DEPLOY_SSH_KEY_FILE", str(tmp_path / "ambient-ci-key"))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "ambient-ci-runner"))
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    missing = subprocess.run(
        [str(TRANSPORT), "exec", "true"],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "DEV_DEPLOY_SSH_HOST": "203.0.113.12",
            "DEV_DEPLOY_SSH_USER": "deploy-user",
            "DEV_DEPLOY_SSH_KEY_FILE": "",
            "RUNNER_TEMP": "",
        },
    )
    assert missing.returncode == 2
    assert "DEV_DEPLOY_SSH_KEY_FILE is required" in missing.stderr

    prepared = _prepare(tmp_path, host="203.0.113.12")
    Path(prepared["DEV_DEPLOY_SSH_KEY_FILE"]).chmod(0o644)
    permissive = subprocess.run(
        [str(TRANSPORT), "exec", "true"],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            **prepared,
            "DEV_DEPLOY_SSH_HOST": "203.0.113.12",
            "DEV_DEPLOY_SSH_USER": "deploy-user",
        },
    )
    assert permissive.returncode == 2
    assert "must not be group/world accessible" in permissive.stderr


def test_prepare_rejects_known_hosts_for_a_different_host(tmp_path: Path) -> None:
    private_key, known_hosts = _generate_credentials(tmp_path, "203.0.113.13")
    result = subprocess.run(
        [str(TRANSPORT), "prepare", str(tmp_path / "prepared")],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "DEV_DEPLOY_SSH_PRIVATE_KEY": private_key,
            "DEV_DEPLOY_SSH_KNOWN_HOSTS": known_hosts,
            "DEV_DEPLOY_SSH_HOST": "203.0.113.14",
        },
    )
    assert result.returncode == 2
    assert "has no pinned entry for 203.0.113.14" in result.stderr


@pytest.mark.parametrize("command", ["exec", "copy-from"])
@pytest.mark.parametrize("missing", ["host", "user"])
def test_transport_never_falls_back_to_retired_host_or_user(
    tmp_path: Path, command: str, missing: str
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "transport-capture"
    _fake_command(fake_bin / "ssh", capture)
    _fake_command(fake_bin / "scp", capture)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("DEV_DEPLOY_SSH_") and key != "REMOTE_USER"}
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin"
    if missing == "host":
        env["DEV_DEPLOY_SSH_USER"] = "deploy-user"
        expected = "DEV_DEPLOY_SSH_HOST is required"
    else:
        env["DEV_DEPLOY_SSH_HOST"] = "203.0.113.10"
        expected = "DEV_DEPLOY_SSH_USER or REMOTE_USER is required"
    args = ["true"] if command == "exec" else ["/tmp/remote", str(tmp_path / "local")]
    result = subprocess.run(
        [str(TRANSPORT), command, *args],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert expected in result.stderr
    assert not Path(f"{capture}.args").exists()


def test_retired_vm_migration_entry_points_are_removed() -> None:
    for name in ("gcp_dev_vm_migrate.sh", "migrate_to_benjamin_cutover.sh"):
        assert not (ROOT / "scripts" / name).exists()
