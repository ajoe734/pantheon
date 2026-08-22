from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import provision_live_supervisor_config as provision


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    command = tmp_path / "command"
    status = tmp_path / "status"
    command.mkdir()
    status.mkdir()
    (command / ".orchestrator").mkdir()
    (command / "scripts").mkdir()
    (status / ".git").mkdir()
    (status / ".orchestrator").mkdir()
    (status / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
    config_source = Path(__file__).resolve().parents[1] / ".orchestrator" / "config.json"
    (command / ".orchestrator" / "config.json").write_bytes(config_source.read_bytes())
    (command / ".orchestrator" / "supervisor.py").write_text("# V2\n", encoding="utf-8")
    for name in ("run-supervisor-watchdog.sh", "promote-supervisor-runtime.sh"):
        path = command / "scripts" / name
        path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        path.chmod(0o755)
    _git(command, "init", "-b", "dev")
    _git(command, "config", "user.email", "test@example.invalid")
    _git(command, "config", "user.name", "Pantheon Test")
    _git(command, "add", ".")
    _git(command, "commit", "-m", "v2")
    _git(command, "remote", "add", "origin", "https://github.com/ajoe734/pantheon.git")
    return command, status


@pytest.mark.parametrize("retired_key", ["account_group", "quota_group", "dispatch_group"])
def test_provider_account_aliases_are_rejected(retired_key: str) -> None:
    config = {"providers": {"codex": {retired_key: "old"}}}

    with pytest.raises(ValueError, match=f"providers.codex.{retired_key} is retired"):
        provision.validate_provider_accounts(config)


def test_provider_account_is_required() -> None:
    with pytest.raises(ValueError, match="providers.codex.account is required"):
        provision.validate_provider_accounts({"providers": {"codex": {}}})

    provision.validate_provider_accounts({"providers": {"codex": {"account": "codex"}}})


@pytest.mark.parametrize("mode", ["off", "shadow", "", "projection"])
def test_task_state_store_rejects_every_non_authoritative_mode(
    tmp_path: Path, mode: str
) -> None:
    repo = {"task_state_store": {"mode": mode, "event_log": "ignored.jsonl"}}

    with pytest.raises(ValueError, match="must be 'authoritative'"):
        provision.apply_task_state_store(
            repo,
            {},
            command_root=tmp_path / "command",
            status_root=tmp_path / "status",
            live_config_path=tmp_path / "runtime" / "live.json",
        )


def test_task_state_store_uses_external_runtime_journal(tmp_path: Path) -> None:
    command = tmp_path / "command"
    status = tmp_path / "status"
    runtime = tmp_path / "runtime"
    command.mkdir()
    status.mkdir()
    runtime.mkdir()
    rendered: dict[str, object] = {}

    provision.apply_task_state_store(
        {"task_state_store": {"mode": "authoritative", "event_log": "v2.jsonl"}},
        rendered,
        command_root=command,
        status_root=status,
        live_config_path=runtime / "live.json",
    )

    store = rendered["task_state_store"]
    assert isinstance(store, dict)
    assert store == {"mode": "authoritative", "event_log": str(runtime / "v2.jsonl")}


def test_validated_command_root_is_self_contained_and_clean(tmp_path: Path) -> None:
    command, _status = _roots(tmp_path)

    identity = provision.validated_immutable_command_root(command)

    assert identity["root"] == str(command.resolve())
    assert len(identity["head"]) == 40
    assert identity["repository"] == "https://github.com/ajoe734/pantheon.git"
    (command / "untracked.txt").write_text("no\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be clean"):
        provision.validated_immutable_command_root(command)


def test_build_live_config_ignores_live_overlay_and_renders_v2_paths(tmp_path: Path) -> None:
    command, status = _roots(tmp_path)
    repo_config = json.loads((command / ".orchestrator" / "config.json").read_text(encoding="utf-8"))
    live_path = tmp_path / "runtime" / "live.json"

    rendered = provision.build_live_config(
        repo_config,
        existing_live_config={"task_state_store": {"mode": "shadow"}, "extra": "stale"},
        command_root=command,
        status_root=status,
        live_config_path=live_path,
        python_executable=Path(sys.executable),
    )

    assert "extra" not in rendered
    assert rendered["task_state_store"]["mode"] == "authoritative"
    assert Path(rendered["task_state_store"]["event_log"]).parent == live_path.parent
    assert rendered["paths"]["status_file"] == str(status / "ai-status.json")


def test_build_live_config_projects_explicit_repository_source_roots(tmp_path: Path) -> None:
    command, status = _roots(tmp_path)
    execute_root = tmp_path / "execute-plans"
    execute_root.mkdir()
    _git(execute_root, "init", "-b", "dev")
    repo_config = json.loads(
        (command / ".orchestrator" / "config.json").read_text(encoding="utf-8")
    )

    rendered = provision.build_live_config(
        repo_config,
        existing_live_config=None,
        command_root=command,
        status_root=status,
        live_config_path=tmp_path / "runtime" / "live.json",
        python_executable=Path(sys.executable),
        repository_source_roots={
            "pantheon": command,
            "execute_plans": execute_root,
        },
    )

    repositories = rendered["coordination"]["repositories"]
    assert repositories["pantheon"]["local_path"] == str(command.resolve())
    assert repositories["execute_plans"]["local_path"] == str(execute_root.resolve())


def test_repository_source_root_requires_absolute_git_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        provision.parse_repository_source_roots(["pantheon=relative/root"])

    with pytest.raises(ValueError, match="not a Git checkout"):
        provision.apply_repository_source_roots(
            {"coordination": {"repositories": {}}},
            {"pantheon": tmp_path},
        )


def test_cli_creates_one_v2_config_without_merging_an_incumbent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    command, status = _roots(tmp_path)
    live_path = tmp_path / "runtime" / "live.json"

    code = provision.main(
        [
            "--repo-config",
            str(command / ".orchestrator" / "config.json"),
            "--live-config",
            str(live_path),
            "--command-root",
            str(command),
            "--status-root",
            str(status),
            "--python",
            sys.executable,
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["config_created"] is True
    installed = json.loads(live_path.read_text(encoding="utf-8"))
    assert installed["task_state_store"]["mode"] == "authoritative"
