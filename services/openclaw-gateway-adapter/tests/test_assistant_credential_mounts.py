from __future__ import annotations

import os
from pathlib import Path
import sys


ADAPTER_DIR = Path(__file__).resolve().parents[1]
if str(ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTER_DIR))

from assistant_credential_mounts import AssistantCredentialMounts  # noqa: E402


def _env(**overrides: str) -> dict[str, str]:
    env = {
        "PANTHEON_ASSISTANT_CODEX_HOST_HOME": "/srv/pantheon-assistant/.codex",
        "PANTHEON_ASSISTANT_CODEX_CONTAINER_HOME": "/home/pantheon-assistant/.codex",
        "PANTHEON_ASSISTANT_CLAUDE_HOST_CONFIG_DIR": "/srv/pantheon-assistant/.claude",
        "PANTHEON_ASSISTANT_CLAUDE_CONTAINER_CONFIG_DIR": "/home/pantheon-assistant/.claude",
        "PANTHEON_ASSISTANT_CREDENTIAL_MOUNT_MODE": "rw",
    }
    env.update(overrides)
    return env


def test_forbidden_human_home_is_rejected_before_stat() -> None:
    def fail_if_codex_called(path: str) -> os.stat_result:
        if path.endswith(".codex"):
            raise AssertionError("human home paths must be rejected before stat")
        raise FileNotFoundError(path)

    mounts = AssistantCredentialMounts(
        _env(PANTHEON_ASSISTANT_CODEX_HOST_HOME="/home/alice/.codex"),
        stat_func=fail_if_codex_called,
        expected_uid=os.getuid(),
    )

    validation = mounts.validate_mounts()
    metadata = mounts.get_readiness_metadata()

    assert validation["codex"].ready is False
    assert validation["codex"].status == "forbidden_human_home"
    assert metadata["mounts"]["codex"]["host_source"] == "rejected"
    assert "/home/alice" not in repr(metadata)


def test_macos_human_home_is_rejected_before_stat() -> None:
    mounts = AssistantCredentialMounts(
        _env(PANTHEON_ASSISTANT_CLAUDE_HOST_CONFIG_DIR="/Users/bob/.claude"),
        stat_func=lambda _path: os.stat_result((0,) * 10),
        expected_uid=os.getuid(),
    )

    metadata = mounts.get_readiness_metadata()

    assert metadata["mounts"]["claude"]["ready"] is False
    assert metadata["mounts"]["claude"]["status"] == "forbidden_human_home"
    assert "/Users/bob" not in repr(metadata)


def test_missing_mount_reports_sanitized_degraded_status() -> None:
    missing = "/home/pantheon-assistant/.codex"

    def missing_stat(path: str) -> os.stat_result:
        raise FileNotFoundError(path)

    mounts = AssistantCredentialMounts(_env(), stat_func=missing_stat, expected_uid=os.getuid())

    metadata = mounts.get_readiness_metadata()

    assert metadata["ready"] is False
    assert metadata["status"] == "degraded"
    assert metadata["mounts"]["codex"]["status"] == "missing_host_mount"
    assert metadata["mounts"]["codex"]["container_target"] == "codex_home"
    assert missing not in repr(metadata)


def _populate_credentials(codex_home: Path, claude_home: Path) -> tuple[Path, Path]:
    codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    claude_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    codex_auth = codex_home / "auth.json"
    claude_creds = claude_home / ".credentials.json"
    codex_auth.write_text("{}", encoding="utf-8")
    claude_creds.write_text("{}", encoding="utf-8")
    codex_auth.chmod(0o600)
    claude_creds.chmod(0o600)
    return codex_auth, claude_creds


def _container_stat_func(codex_home: Path, claude_home: Path):
    codex_auth = codex_home / "auth.json"
    claude_creds = claude_home / ".credentials.json"

    def _stat(path: str) -> os.stat_result:
        if path == "/home/pantheon-assistant/.codex":
            return codex_home.stat()
        if path == "/home/pantheon-assistant/.codex/auth.json" and codex_auth.exists():
            return codex_auth.stat()
        if path == "/home/pantheon-assistant/.claude":
            return claude_home.stat()
        if path == "/home/pantheon-assistant/.claude/.credentials.json" and claude_creds.exists():
            return claude_creds.stat()
        raise FileNotFoundError(path)

    return _stat


def test_mount_stat_uses_container_target_after_host_policy_passes(tmp_path: Path) -> None:
    codex_home = tmp_path / "container" / ".codex"
    claude_home = tmp_path / "container" / ".claude"
    codex_auth, claude_creds = _populate_credentials(codex_home, claude_home)

    seen: list[str] = []

    def stat_container(path: str) -> os.stat_result:
        seen.append(path)
        if path == "/home/pantheon-assistant/.codex":
            return codex_home.stat()
        if path == "/home/pantheon-assistant/.codex/auth.json":
            return codex_auth.stat()
        if path == "/home/pantheon-assistant/.claude":
            return claude_home.stat()
        if path == "/home/pantheon-assistant/.claude/.credentials.json":
            return claude_creds.stat()
        raise FileNotFoundError(path)

    mounts = AssistantCredentialMounts(_env(), stat_func=stat_container, expected_uid=os.getuid())

    metadata = mounts.get_readiness_metadata()

    assert metadata["ready"] is True
    assert seen == [
        "/home/pantheon-assistant/.codex",
        "/home/pantheon-assistant/.codex/auth.json",
        "/home/pantheon-assistant/.claude",
        "/home/pantheon-assistant/.claude/.credentials.json",
    ]


def test_empty_mounts_without_auth_files_report_missing_credentials_regardless_of_dir_owner(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / ".codex"
    claude_home = tmp_path / ".claude"
    codex_home.mkdir(mode=0o700)
    claude_home.mkdir(mode=0o700)

    # Even if the empty directory is owned by the expected service user, missing
    # auth files must never be reported as ready.
    matched_owner_mounts = AssistantCredentialMounts(
        _env(),
        stat_func=_container_stat_func(codex_home, claude_home),
        expected_uid=os.getuid(),
    )
    matched_meta = matched_owner_mounts.get_readiness_metadata()
    assert matched_meta["ready"] is False
    assert matched_meta["status"] == "degraded"
    assert matched_meta["mounts"]["codex"]["ready"] is False
    assert matched_meta["mounts"]["codex"]["status"] == "missing_credentials"
    assert matched_meta["mounts"]["codex"]["owner_check"] == "not_checked"
    assert matched_meta["mounts"]["claude"]["ready"] is False
    assert matched_meta["mounts"]["claude"]["status"] == "missing_credentials"
    assert matched_meta["mounts"]["claude"]["owner_check"] == "not_checked"

    # When an empty directory is owned by root (e.g. auto-created Docker mount)
    # and auth files do not exist, report missing_credentials rather than
    # misleadingly claiming wrong_owner on an empty directory.
    mismatched_owner_mounts = AssistantCredentialMounts(
        _env(),
        stat_func=_container_stat_func(codex_home, claude_home),
        expected_uid=os.getuid() + 1,
    )
    mismatched_meta = mismatched_owner_mounts.get_readiness_metadata()
    assert mismatched_meta["ready"] is False
    assert mismatched_meta["mounts"]["codex"]["status"] == "missing_credentials"
    assert mismatched_meta["mounts"]["claude"]["status"] == "missing_credentials"


def test_metadata_never_exposes_provider_session_file_paths(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    claude_home = tmp_path / ".claude"
    _populate_credentials(codex_home, claude_home)

    mounts = AssistantCredentialMounts(
        _env(
            PANTHEON_ASSISTANT_CODEX_HOST_HOME="/srv/pantheon-assistant/.codex",
            PANTHEON_ASSISTANT_CODEX_CONTAINER_HOME="/home/pantheon-assistant/.codex",
            PANTHEON_ASSISTANT_CLAUDE_HOST_CONFIG_DIR="/srv/pantheon-assistant/.claude",
            PANTHEON_ASSISTANT_CLAUDE_CONTAINER_CONFIG_DIR="/home/pantheon-assistant/.claude",
        ),
        stat_func=_container_stat_func(codex_home, claude_home),
        expected_uid=os.getuid(),
    )

    metadata = mounts.get_readiness_metadata()
    rendered = repr(metadata)

    assert metadata["ready"] is True
    assert metadata["mounts"]["codex"]["container_target"] == "codex_home"
    assert metadata["mounts"]["claude"]["container_target"] == "claude_config"
    assert "/srv/pantheon-assistant/.codex" not in rendered
    assert "/home/pantheon-assistant/.codex" not in rendered
    assert "/srv/pantheon-assistant/.claude" not in rendered
    assert "/home/pantheon-assistant/.claude" not in rendered
    assert "auth.json" not in rendered
    assert ".credentials.json" not in rendered


def test_wrong_owner_reports_sanitized_mismatch(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    claude_home = tmp_path / ".claude"
    _populate_credentials(codex_home, claude_home)

    wrong_uid = os.getuid() + 1
    mounts = AssistantCredentialMounts(
        _env(
            PANTHEON_ASSISTANT_CODEX_HOST_HOME="/srv/pantheon-assistant/.codex",
            PANTHEON_ASSISTANT_CLAUDE_HOST_CONFIG_DIR="/srv/pantheon-assistant/.claude",
        ),
        stat_func=_container_stat_func(codex_home, claude_home),
        expected_uid=wrong_uid,
    )

    metadata = mounts.get_readiness_metadata()

    assert metadata["ready"] is False
    assert metadata["mounts"]["codex"]["status"] == "wrong_owner"
    assert metadata["mounts"]["codex"]["owner_check"] == "mismatch"
    assert str(tmp_path) not in repr(metadata)


def test_valid_mounts_return_ready_without_raw_paths(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    claude_home = tmp_path / ".claude"
    _populate_credentials(codex_home, claude_home)

    mounts = AssistantCredentialMounts(
        _env(PANTHEON_ASSISTANT_CREDENTIAL_MOUNT_MODE="ro"),
        stat_func=_container_stat_func(codex_home, claude_home),
        expected_uid=os.getuid(),
    )

    metadata = mounts.get_readiness_metadata()

    assert metadata["ready"] is True
    assert metadata["status"] == "ready"
    assert metadata["mounts"]["codex"]["status"] == "ready"
    assert metadata["mounts"]["codex"]["mount_mode"] == "ro"
    assert metadata["mounts"]["claude"]["owner_check"] == "matched"
    assert "/srv/pantheon-assistant" not in repr(metadata)
    assert "/home/pantheon-assistant" not in repr(metadata)


def test_mount_mode_reporting_rw(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    claude_home = tmp_path / ".claude"
    _populate_credentials(codex_home, claude_home)

    mounts = AssistantCredentialMounts(
        _env(PANTHEON_ASSISTANT_CREDENTIAL_MOUNT_MODE="rw"),
        stat_func=_container_stat_func(codex_home, claude_home),
        expected_uid=os.getuid(),
    )

    metadata = mounts.get_readiness_metadata()
    assert metadata["mounts"]["codex"]["mount_mode"] == "rw"
    assert metadata["mounts"]["claude"]["mount_mode"] == "rw"


def test_invalid_mode_and_container_path_are_rejected() -> None:
    invalid_mode = AssistantCredentialMounts(
        _env(PANTHEON_ASSISTANT_CREDENTIAL_MOUNT_MODE="delegated"),
        stat_func=lambda _path: os.stat_result((0,) * 10),
        expected_uid=os.getuid(),
    )
    assert invalid_mode.validate_mounts()["codex"].status == "invalid_mount_mode"

    invalid_container = AssistantCredentialMounts(
        _env(PANTHEON_ASSISTANT_CODEX_CONTAINER_HOME="/etc"),
        stat_func=lambda _path: os.stat_result((0,) * 10),
        expected_uid=os.getuid(),
    )
    assert invalid_container.validate_mounts()["codex"].status == "forbidden_container_path"
