from __future__ import annotations

import json
import os
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


def _integration_clone(tmp_path: Path, repository_id: str) -> Path:
    remote = tmp_path / f"{repository_id}.git"
    source = tmp_path / f"{repository_id}-source"
    remote.mkdir()
    source.mkdir()
    _git(remote, "init", "--bare")
    _git(source, "init", "-b", "dev")
    _git(source, "config", "user.email", "test@example.invalid")
    _git(source, "config", "user.name", "Pantheon Test")
    (source / "README.md").write_text(repository_id + "\n", encoding="utf-8")
    _git(source, "add", "README.md")
    _git(source, "commit", "-m", "initial")
    _git(source, "remote", "add", "origin", str(remote))
    _git(source, "push", "-u", "origin", "dev")
    head = _git(source, "rev-parse", "HEAD")
    destination = tmp_path / "integration-runtimes" / repository_id / head
    destination.parent.mkdir(parents=True)
    _git(destination.parent, "clone", str(remote), str(destination))
    _git(destination, "checkout", "--detach", head)
    return destination


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


def test_build_live_config_pins_high_reasoning_antigravity_models(tmp_path: Path) -> None:
    command, status = _roots(tmp_path)
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
    )

    for provider_id in ("antigravity", "antigravity2"):
        provider = rendered["providers"][provider_id]
        assert provider["antigravity"]["model"] == "gemini-3.8-flash-high"
        assert provider["antigravity"]["output_format"] == "stream-json"
        assert provider["model_rotation"] == {
            "enabled": True,
            "primary": "gemini-3.8-flash-high",
            "fallback": "claude-sonnet-4-6",
            "cooldown_seconds": 900,
        }


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


def test_build_live_config_projects_clean_standalone_integration_roots(
    tmp_path: Path,
) -> None:
    command, status = _roots(tmp_path)
    pantheon_integration = _integration_clone(tmp_path, "pantheon")
    execute_integration = _integration_clone(tmp_path, "execute_plans")
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
        repository_source_roots={"pantheon": command},
        repository_integration_roots={
            "pantheon": pantheon_integration,
            "execute_plans": execute_integration,
        },
    )

    repositories = rendered["coordination"]["repositories"]
    assert repositories["pantheon"]["local_path"] == str(command.resolve())
    assert repositories["pantheon"]["integration_path"] == str(
        pantheon_integration.resolve()
    )
    assert repositories["execute_plans"]["integration_path"] == str(
        execute_integration.resolve()
    )
    for root in (pantheon_integration, execute_integration):
        assert _git(root, "status", "--porcelain", "--untracked-files=all") == ""
        assert _git(root, "rev-parse", "--git-common-dir") == ".git"


def test_repository_integration_root_rejects_dirty_or_unversioned_clone(
    tmp_path: Path,
) -> None:
    integration = _integration_clone(tmp_path, "pantheon")
    (integration / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    rendered = {"coordination": {"repositories": {"pantheon": {}}}}

    with pytest.raises(ValueError, match="must be clean"):
        provision.apply_repository_integration_roots(
            rendered,
            {"pantheon": integration},
        )


def test_repository_source_root_requires_absolute_git_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        provision.parse_repository_source_roots(["pantheon=relative/root"])

    with pytest.raises(ValueError, match="not a Git checkout"):
        provision.apply_repository_source_roots(
            {"coordination": {"repositories": {}}},
            {"pantheon": tmp_path},
        )


def test_validate_python_dependencies_reports_real_installed_versions(
    tmp_path: Path,
) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("# comment\npytest\n", encoding="utf-8")

    versions = provision.validate_python_dependencies(Path(sys.executable), requirements)

    assert set(versions) == {"pytest"}
    assert versions["pytest"]


def test_validate_python_dependencies_fails_closed_for_missing_package(
    tmp_path: Path,
) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("definitely-not-a-real-package-xyz\n", encoding="utf-8")

    with pytest.raises(ValueError, match="python dependency preflight failed"):
        provision.validate_python_dependencies(Path(sys.executable), requirements)


def test_validate_python_dependencies_rejects_a_de_virtualized_interpreter(
    tmp_path: Path,
) -> None:
    """Prove the failure mode this task closes: an interpreter that no longer
    has the venv's packages (e.g. the base interpreter behind a resolved venv
    symlink) is rejected instead of silently accepted."""

    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pytest\n", encoding="utf-8")
    base_interpreter = Path("/usr/bin/python3")
    if not base_interpreter.is_file():
        pytest.skip("no base system interpreter available on this host")

    with pytest.raises(ValueError, match="python dependency preflight failed"):
        provision.validate_python_dependencies(base_interpreter, requirements)


def test_validate_python_dependencies_enforces_the_version_specifier(
    tmp_path: Path,
) -> None:
    """Metadata-only comparisons were rejected because they never enforce
    the requirements file's version specifier at all. A satisfiable
    specifier must pass and an unsatisfiable one must fail closed with the
    real installed version named in the error."""

    installed_version = provision.validate_python_dependencies(
        Path(sys.executable), _write_requirements(tmp_path, "pytest\n")
    )["pytest"]

    satisfiable = _write_requirements(tmp_path, "pytest>=1\n")
    versions = provision.validate_python_dependencies(Path(sys.executable), satisfiable)
    assert versions["pytest"] == installed_version

    unsatisfiable = _write_requirements(tmp_path, "pytest>=9999,<10000\n")
    with pytest.raises(ValueError, match="does not satisfy"):
        provision.validate_python_dependencies(Path(sys.executable), unsatisfiable)


def _write_requirements(tmp_path: Path, content: str, *, name: str = "requirements.txt") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_validate_python_dependencies_actually_imports_not_just_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A distribution can report a version that satisfies every specifier
    while its module fails to import (a broken native extension, a
    half-removed package). Metadata-only preflight passed this silently;
    the real preflight must import the module and fail closed here."""

    site_dir = tmp_path / "fake-site"
    package_dir = site_dir / "broken_pkg"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text(
        "raise RuntimeError('this module cannot actually be imported')\n",
        encoding="utf-8",
    )
    dist_info = site_dir / "broken_pkg-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: broken-pkg\nVersion: 1.0\n", encoding="utf-8"
    )

    requirements = _write_requirements(tmp_path, "broken-pkg\n")
    existing_pythonpath = os.environ.get("PYTHONPATH")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(filter(None, [str(site_dir), existing_pythonpath])),
    )

    with pytest.raises(ValueError, match="python dependency preflight failed"):
        provision.validate_python_dependencies(Path(sys.executable), requirements)


def test_cli_preserves_a_venv_symlink_path_instead_of_resolving_it(
    tmp_path: Path,
) -> None:
    """A venv's bin/python is normally a symlink chain to the base
    interpreter. Fully resolving --python before storing it collapses that
    chain and launches the base interpreter directly, which never finds the
    venv's pyvenv.cfg and silently loses every package the venv provides."""

    command, status = _roots(tmp_path)
    live_path = tmp_path / "runtime" / "live.json"
    fake_venv_python = tmp_path / "fake-venv" / "bin" / "python3"
    fake_venv_python.parent.mkdir(parents=True)
    fake_venv_python.symlink_to(Path(sys.executable))

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
            str(fake_venv_python),
            "--json",
        ]
    )

    assert code == 0
    installed = json.loads(live_path.read_text(encoding="utf-8"))
    assert installed["watchdog"]["supervisor_command"][0] == str(fake_venv_python)
    assert installed["watchdog"]["supervisor_command"][0] != str(
        fake_venv_python.resolve()
    )


def test_cli_dependency_preflight_failure_leaves_no_config_behind(
    tmp_path: Path,
) -> None:
    """A failed dependency preflight must not create the live config at all,
    matching the "preserve incumbent" requirement for provisioning."""

    command, status = _roots(tmp_path)
    live_path = tmp_path / "runtime" / "live.json"
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("definitely-not-a-real-package-xyz\n", encoding="utf-8")

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
            "--requirements",
            str(requirements),
            "--json",
        ]
    )

    assert code == 2
    assert not live_path.exists()


def test_cli_validate_python_dependencies_only_never_touches_live_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The lightweight preflight-only CLI mode (used by bootstrap/sync before
    they ever mutate incumbent state) must run the real preflight and return
    without any of --repo-config/--live-config/--status-root."""

    command, _status = _roots(tmp_path)
    requirements = _write_requirements(tmp_path, "pytest\n")

    code = provision.main(
        [
            "--command-root",
            str(command),
            "--python",
            sys.executable,
            "--requirements",
            str(requirements),
            "--validate-python-dependencies-only",
            "--json",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["python_dependencies"]["pytest"]
    assert not (tmp_path / "runtime").exists()


def test_cli_validate_python_dependencies_only_fails_closed(tmp_path: Path) -> None:
    command, _status = _roots(tmp_path)
    requirements = _write_requirements(tmp_path, "definitely-not-a-real-package-xyz\n")

    code = provision.main(
        [
            "--command-root",
            str(command),
            "--python",
            sys.executable,
            "--requirements",
            str(requirements),
            "--validate-python-dependencies-only",
        ]
    )

    assert code == 2


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
