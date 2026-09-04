from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import promote_supervisor_runtime as promotion

_REAL_VERIFY_WORKER_SANDBOX = promotion.verify_worker_sandbox


@pytest.fixture(autouse=True)
def _command_runtime_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runtime_parent = tmp_path / "command-runtimes"
    monkeypatch.setattr(promotion, "COMMAND_RUNTIME_PARENT", runtime_parent)
    monkeypatch.setattr(
        promotion,
        "verify_worker_sandbox",
        lambda root: {
            "outcome": "available",
            "binary": "/usr/bin/bwrap",
            "command_root": str(Path(root).resolve()),
        },
    )
    monkeypatch.setenv(
        "BRIDGE_SIGNING_PUBLIC_KEYS_JSON", '{"test-key":"public-test-key"}'
    )
    yield
    # Promotion deliberately makes command runtimes read-only. Restore owner
    # write/traverse permission so pytest can remove its temporary directory.
    if runtime_parent.exists():
        for current_root, dirnames, filenames in os.walk(
            runtime_parent, topdown=False, followlinks=False
        ):
            current = Path(current_root)
            for name in (*filenames, *dirnames):
                path = current / name
                if not path.is_symlink():
                    mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
                    os.chmod(path, mode | stat.S_IWUSR, follow_symlinks=False)
            mode = stat.S_IMODE(current.stat(follow_symlinks=False).st_mode)
            os.chmod(current, mode | stat.S_IWUSR | stat.S_IXUSR, follow_symlinks=False)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _candidate(tmp_path: Path) -> tuple[Path, Path]:
    candidate = tmp_path / "candidate-stage"
    status_root = tmp_path / "status"
    candidate.mkdir()
    status_root.mkdir()
    (candidate / ".orchestrator").mkdir()
    (candidate / "scripts").mkdir()
    (status_root / ".git").mkdir()
    (status_root / ".orchestrator").mkdir()
    (status_root / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
    config_source = Path(__file__).resolve().parents[1] / ".orchestrator" / "config.json"
    (candidate / ".orchestrator" / "config.json").write_bytes(config_source.read_bytes())
    (candidate / ".orchestrator" / "supervisor.py").write_text("# V2\n", encoding="utf-8")
    for name in ("run-supervisor-watchdog.sh", "promote-supervisor-runtime.sh"):
        path = candidate / "scripts" / name
        path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        path.chmod(0o755)
    _git(candidate, "init", "-b", "dev")
    _git(candidate, "config", "user.email", "test@example.invalid")
    _git(candidate, "config", "user.name", "Pantheon Test")
    _git(candidate, "add", ".")
    _git(candidate, "commit", "-m", "v2")
    _git(candidate, "remote", "add", "origin", "https://github.com/ajoe734/pantheon.git")
    head = _git(candidate, "rev-parse", "HEAD")
    runtime_parent = tmp_path / "command-runtimes"
    runtime_parent.mkdir()
    runtime = runtime_parent / head
    candidate.rename(runtime)
    return runtime, status_root


def test_render_v2_config_requires_one_clean_authoritative_source(tmp_path: Path) -> None:
    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"

    rendered, identity = promotion.render_v2_config(
        candidate,
        status_root=status_root,
        live_config_path=live_config,
        python_executable=Path(sys.executable),
    )

    assert identity["root"] == str(candidate.resolve())
    assert len(identity["head"]) == 40
    assert rendered["task_state_store"]["mode"] == "authoritative"
    assert Path(rendered["task_state_store"]["event_log"]).is_absolute()
    assert rendered["watchdog"]["supervisor_command"][-2:] == [
        str(live_config),
        "--verbose",
    ]


def test_render_v2_config_projects_deployment_repository_roots(tmp_path: Path) -> None:
    candidate, status_root = _candidate(tmp_path)
    execute_root = tmp_path / "execute-plans"
    execute_root.mkdir()
    _git(execute_root, "init", "-b", "dev")

    rendered, _identity = promotion.render_v2_config(
        candidate,
        status_root=status_root,
        live_config_path=tmp_path / "runtime" / "live.json",
        python_executable=Path(sys.executable),
        repository_source_roots={
            "pantheon": candidate,
            "execute_plans": execute_root,
        },
    )

    repositories = rendered["coordination"]["repositories"]
    assert repositories["pantheon"]["local_path"] == str(candidate.resolve())
    assert repositories["execute_plans"]["local_path"] == str(execute_root.resolve())


def test_seal_command_runtime_removes_write_bits_and_preserves_execute_bits(
    tmp_path: Path,
) -> None:
    candidate, _status_root = _candidate(tmp_path)
    executable = candidate / "scripts" / "promote-supervisor-runtime.sh"

    result = promotion.seal_command_runtime(candidate)

    assert result["outcome"] == "sealed"
    assert result["root"] == str(candidate.resolve())
    assert result["changed_paths"] > 0
    assert stat.S_IMODE(executable.stat().st_mode) & 0o111
    for current_root, dirnames, filenames in os.walk(candidate, followlinks=False):
        current = Path(current_root)
        for name in (*filenames, *dirnames):
            path = current / name
            if not path.is_symlink():
                assert stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) & 0o222 == 0
        assert stat.S_IMODE(current.stat(follow_symlinks=False).st_mode) & 0o222 == 0


def test_worker_sandbox_preflight_fails_closed_without_bwrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(promotion.shutil, "which", lambda _name: None)

    with pytest.raises(ValueError, match="bubblewrap"):
        _REAL_VERIFY_WORKER_SANDBOX(tmp_path)


def test_render_rejects_a_clean_staging_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    candidate, status_root = _candidate(tmp_path)
    monkeypatch.setattr(promotion, "COMMAND_RUNTIME_PARENT", tmp_path / "other-runtimes")

    with pytest.raises(ValueError, match="command-runtimes/<HEAD>"):
        promotion.render_v2_config(
            candidate,
            status_root=status_root,
            live_config_path=tmp_path / "runtime" / "live.json",
            python_executable=Path(sys.executable),
        )


def test_render_rejects_non_authoritative_candidate_before_stopping_runtime(
    tmp_path: Path,
) -> None:
    candidate, status_root = _candidate(tmp_path)
    config_path = candidate / ".orchestrator" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["task_state_store"]["mode"] = "shadow"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    _git(candidate, "add", ".orchestrator/config.json")
    _git(candidate, "commit", "-m", "invalid")
    updated_candidate = candidate.parent / _git(candidate, "rev-parse", "HEAD")
    candidate.rename(updated_candidate)
    candidate = updated_candidate

    with pytest.raises(ValueError, match="must be 'authoritative'"):
        promotion.render_v2_config(
            candidate,
            status_root=status_root,
            live_config_path=tmp_path / "runtime" / "live.json",
            python_executable=Path(sys.executable),
        )


def test_stop_existing_supervisor_only_signals_a_verified_supervisor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid_path = tmp_path / "supervisor.pid"
    pid_path.write_text("123\n", encoding="utf-8")
    alive = iter((True, True, False, False))
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(promotion, "_pid_alive", lambda _pid: next(alive))
    monkeypatch.setattr(promotion, "_process_is_supervisor", lambda _pid: True)
    monkeypatch.setattr(promotion.os, "kill", lambda pid, sig: signals.append((pid, sig)))

    assert promotion.stop_existing_supervisor(pid_path, timeout_seconds=1) == 123
    assert signals == [(123, promotion.signal.SIGTERM)]


def test_stop_refuses_a_stale_pid_file_for_an_unrelated_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid_path = tmp_path / "supervisor.pid"
    pid_path.write_text("123\n", encoding="utf-8")
    monkeypatch.setattr(promotion, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(promotion, "_process_is_supervisor", lambda _pid: False)

    with pytest.raises(ValueError, match="does not identify a supervisor"):
        promotion.stop_existing_supervisor(pid_path, timeout_seconds=1)


def test_launch_detaches_supervisor_output_from_the_calling_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def popen(argv: list[str], **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        output = kwargs["stdout"]
        assert hasattr(output, "write")
        output.write(b"launched\n")
        output.flush()
        return SimpleNamespace(pid=42)

    monkeypatch.setattr(promotion.subprocess, "Popen", popen)
    identity = {
        "root": str(tmp_path),
        "head": "a" * 40,
        "repository": "https://github.com/ajoe734/pantheon.git",
    }

    pid = promotion.launch_v2_supervisor(
        {"watchdog": {"supervisor_command": ["python3", "supervisor.py"]}},
        identity=identity,
        status_root=tmp_path,
    )

    log_path = tmp_path / ".orchestrator" / "logs" / "supervisor.log"
    assert pid == 42
    assert captured["stderr"] == subprocess.STDOUT
    assert log_path.read_bytes() == b"launched\n"
    assert log_path.stat().st_mode & 0o777 == 0o600


def test_launch_uses_public_authority_file_and_strips_private_signing_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    authority_env = tmp_path / "supervisor-authority-public.env"
    authority_env.write_text(
        "BRIDGE_SIGNING_PUBLIC_KEYS_JSON='{\"promoted\":\"public-key\"}'\n",
        encoding="utf-8",
    )
    authority_env.chmod(0o600)
    monkeypatch.setenv("BRIDGE_SIGNING_PRIVATE_KEY", "must-not-reach-supervisor")
    monkeypatch.setattr(
        promotion.subprocess,
        "Popen",
        lambda _argv, **kwargs: captured.update(kwargs) or SimpleNamespace(pid=42),
    )

    promotion.launch_v2_supervisor(
        {"watchdog": {"supervisor_command": ["python3", "supervisor.py"]}},
        identity={
            "root": str(tmp_path),
            "head": "a" * 40,
            "repository": "https://github.com/ajoe734/pantheon.git",
        },
        status_root=tmp_path,
        authority_env_file=authority_env,
    )

    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["BRIDGE_SIGNING_PUBLIC_KEYS_JSON"] == '{"promoted":"public-key"}'
    assert "BRIDGE_SIGNING_PRIVATE_KEY" not in environment


def test_launch_rejects_invalid_public_authority_file(tmp_path: Path) -> None:
    authority_env = tmp_path / "supervisor-authority-public.env"
    authority_env.write_text("BRIDGE_SIGNING_PRIVATE_KEY='no'\n", encoding="utf-8")
    authority_env.chmod(0o600)

    with pytest.raises(ValueError, match="invalid public supervisor authority entry"):
        promotion.supervisor_launch_environment({}, authority_env_file=authority_env)


def test_launch_rejects_a_missing_verifier_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRIDGE_SIGNING_PUBLIC_KEYS_JSON")

    with pytest.raises(ValueError, match="BRIDGE_SIGNING_PUBLIC_KEYS_JSON must be valid JSON"):
        promotion.supervisor_launch_environment({})


def test_replace_has_only_stop_install_launch_and_never_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    events: list[str] = []
    def stop(pid_path: Path, *, timeout_seconds: float) -> int:
        events.append("stop")
        return 41

    def launch(*args: object, **kwargs: object) -> int:
        events.append("launch")
        return 42

    monkeypatch.setattr(promotion, "stop_existing_supervisor", stop)
    monkeypatch.setattr(promotion, "launch_v2_supervisor", launch)
    result = promotion.replace_supervisor(
        candidate,
        status_root=status_root,
        live_config_path=live_config,
        python_executable=Path(sys.executable),
        termination_timeout=1,
    )

    assert result["outcome"] == "launched"
    assert result["command_runtime_seal"]["outcome"] == "sealed"
    assert result["worker_sandbox_preflight"]["outcome"] == "available"
    assert result["stopped_pid"] == 41
    assert result["launched_pid"] == 42
    assert events == ["stop", "launch"]
    installed = json.loads(live_config.read_text(encoding="utf-8"))
    assert installed["task_state_store"]["mode"] == "authoritative"
    assert json.loads((status_root / ".orchestrator" / "approval-queue.json").read_text(encoding="utf-8"))["version"] == 2
    assert not hasattr(promotion, "migrate_task_state_store_v2")
    assert not hasattr(promotion, "PromotionTransaction")


def test_promotion_locks_before_candidate_validation_or_config_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    validated = False

    def unexpected_render(*args: object, **kwargs: object):
        nonlocal validated
        validated = True
        raise AssertionError("validation ran during active integration")

    monkeypatch.setattr(promotion, "render_v2_config", unexpected_render)
    lock_path = status_root / promotion.auto_integrator.DEFAULT_LOCK
    with promotion.auto_integrator.lock_file(lock_path):
        with pytest.raises(promotion.auto_integrator.IntegrationLockHeld):
            promotion.replace_supervisor(
                candidate,
                status_root=status_root,
                live_config_path=live_config,
                python_executable=Path(sys.executable),
                termination_timeout=1,
            )

    assert validated is False
    assert not live_config.exists()


def test_replace_rejects_missing_verifier_before_stopping_incumbent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    stopped: list[bool] = []
    monkeypatch.delenv("BRIDGE_SIGNING_PUBLIC_KEYS_JSON")
    monkeypatch.setattr(
        promotion,
        "stop_existing_supervisor",
        lambda *_args, **_kwargs: stopped.append(True),
    )

    with pytest.raises(ValueError, match="BRIDGE_SIGNING_PUBLIC_KEYS_JSON must be valid JSON"):
        promotion.replace_supervisor(
            candidate,
            status_root=status_root,
            live_config_path=live_config,
            python_executable=Path(sys.executable),
            termination_timeout=1,
        )

    assert stopped == []
    assert not live_config.exists()


def test_status_root_replacement_stops_pid_from_installed_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, status_root = _candidate(tmp_path)
    old_status_root = tmp_path / "old-status"
    (old_status_root / ".git").mkdir(parents=True)
    (old_status_root / ".orchestrator").mkdir()
    (old_status_root / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
    old_state = old_status_root / ".orchestrator" / "state.json"
    old_state.write_text("{}\n", encoding="utf-8")
    old_pid = old_state.parent / "supervisor.pid"
    old_pid.write_text("73\n", encoding="utf-8")
    live_config = tmp_path / "runtime" / "live.json"
    live_config.parent.mkdir()
    live_config.write_text(json.dumps({"paths": {"state_file": str(old_state)}}), encoding="utf-8")
    stopped: list[Path] = []
    monkeypatch.setattr(
        promotion,
        "stop_existing_supervisor",
        lambda path, *, timeout_seconds: stopped.append(path) or 73,
    )
    monkeypatch.setattr(promotion, "launch_v2_supervisor", lambda *_args, **_kwargs: 74)

    result = promotion.replace_supervisor(
        candidate,
        status_root=status_root,
        live_config_path=live_config,
        python_executable=Path(sys.executable),
        termination_timeout=1,
    )

    assert result["outcome"] == "launched"
    assert stopped == [old_pid]


def test_launch_failure_is_reported_without_a_rollback_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    monkeypatch.setattr(promotion, "stop_existing_supervisor", lambda *_args, **_kwargs: 41)
    monkeypatch.setattr(
        promotion,
        "launch_v2_supervisor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("launch failed")),
    )

    result = promotion.replace_supervisor(
        candidate,
        status_root=status_root,
        live_config_path=live_config,
        python_executable=Path(sys.executable),
        termination_timeout=1,
    )

    assert result["outcome"] == "failed"
    assert "launch failed" in result["error"]
    assert live_config.is_file()
    assert "rollback" not in result


def test_discover_only_is_read_only_and_reports_v2_identity(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"

    code = promotion.main(
        [
            "--discover-only",
            "--json",
            "--repo",
            str(candidate),
            "--status-root",
            str(status_root),
            "--live-config",
            str(live_config),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["outcome"] == "ready"
    assert payload["task_state_store"]["mode"] == "authoritative"
    assert not live_config.exists()


def test_sync_coordination_root_code_preserves_dirty_shared_checkout(tmp_path: Path) -> None:
    candidate = tmp_path / "command-runtimes" / "candidate"
    status_root = tmp_path / "status"
    (candidate / "scripts").mkdir(parents=True)
    (candidate / ".orchestrator" / "rewrite").mkdir(parents=True)
    (candidate / ".orchestrator" / "development_bridge").mkdir()
    (candidate / "scripts" / "ai_status.py").write_text("# new version\n", encoding="utf-8")
    (candidate / ".orchestrator" / "common.py").write_text("# new common\n", encoding="utf-8")
    (candidate / ".orchestrator" / "rewrite" / "task_machine.py").write_text(
        "# new task_machine\n", encoding="utf-8"
    )
    (candidate / ".orchestrator" / "development_bridge" / "dev_bridge_models.py").write_text(
        "# new bridge model\n", encoding="utf-8"
    )

    (status_root / "scripts").mkdir(parents=True)
    (status_root / ".orchestrator" / "rewrite").mkdir(parents=True)
    (status_root / ".orchestrator" / "development_bridge").mkdir()
    (status_root / "scripts" / "ai_status.py").write_text("# stale version\n", encoding="utf-8")
    (status_root / ".orchestrator" / "common.py").write_text("# stale common\n", encoding="utf-8")
    (status_root / ".orchestrator" / "rewrite" / "task_machine.py").write_text(
        "# stale task_machine\n", encoding="utf-8"
    )
    (status_root / ".orchestrator" / "development_bridge" / "dev_bridge_models.py").write_text(
        "# stale bridge model\n", encoding="utf-8"
    )
    live_status = json.dumps({"tasks": [{"id": "REG-1", "status": "in_progress"}]})
    (status_root / "ai-status.json").write_text(live_status, encoding="utf-8")
    (status_root / ".orchestrator" / "state.json").write_text('{"live": true}\n', encoding="utf-8")

    promotion.seal_command_runtime(candidate)
    before = {path: path.read_bytes() for path in status_root.rglob("*") if path.is_file()}
    result = promotion.sync_coordination_root_code(candidate, status_root)

    assert result["outcome"] == "preserved"
    assert result["reason"] == "coordination_root_is_state_only"
    assert result["paths"] == []
    assert {path: path.read_bytes() for path in status_root.rglob("*") if path.is_file()} == before
    # Live data must be byte-for-byte untouched.
    assert (status_root / "ai-status.json").read_text(encoding="utf-8") == live_status
    assert (status_root / ".orchestrator" / "state.json").read_text(encoding="utf-8") == '{"live": true}\n'


def test_sync_coordination_root_code_never_removes_retired_files(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    status_root = tmp_path / "status"
    (candidate / "scripts").mkdir(parents=True)
    (candidate / ".orchestrator" / "rewrite").mkdir(parents=True)
    (candidate / ".orchestrator" / "development_bridge").mkdir()
    (candidate / "scripts" / "kept.py").write_text("# kept\n", encoding="utf-8")
    (candidate / ".orchestrator" / "development_bridge" / "kept.py").write_text(
        "# kept bridge\n", encoding="utf-8"
    )

    (status_root / "scripts").mkdir(parents=True)
    (status_root / ".orchestrator" / "rewrite").mkdir(parents=True)
    (status_root / ".orchestrator" / "development_bridge").mkdir()
    (status_root / "scripts" / "kept.py").write_text("# stale\n", encoding="utf-8")
    (status_root / "scripts" / "retired_script.py").write_text("# should be removed\n", encoding="utf-8")
    (status_root / ".orchestrator" / "retired_top_level.py").write_text("# gone\n", encoding="utf-8")
    (status_root / ".orchestrator" / "rewrite" / "retired_module.py").write_text(
        "# gone too\n", encoding="utf-8"
    )
    (status_root / ".orchestrator" / "development_bridge" / "retired_bridge.py").write_text(
        "# gone bridge\n", encoding="utf-8"
    )

    result = promotion.sync_coordination_root_code(candidate, status_root)

    assert result["outcome"] == "preserved"
    assert (status_root / "scripts" / "retired_script.py").exists()
    assert (status_root / ".orchestrator" / "retired_top_level.py").exists()
    assert (status_root / ".orchestrator" / "rewrite" / "retired_module.py").exists()
    assert (status_root / ".orchestrator" / "development_bridge" / "retired_bridge.py").exists()
    assert (status_root / "scripts" / "kept.py").read_text(encoding="utf-8") == "# stale\n"


def test_sync_coordination_root_code_never_touches_orchestrator_json_or_logs(tmp_path: Path) -> None:
    """The allowlist is *.py-at-top-level plus rewrite/ -- config.json, logs/,
    and any other .orchestrator content must be left exactly as they were."""

    candidate = tmp_path / "candidate"
    status_root = tmp_path / "status"
    (candidate / "scripts").mkdir(parents=True)
    (candidate / ".orchestrator" / "rewrite").mkdir(parents=True)
    (candidate / ".orchestrator" / "config.json").write_text('{"from": "candidate"}\n', encoding="utf-8")

    (status_root / "scripts").mkdir(parents=True)
    (status_root / ".orchestrator" / "rewrite").mkdir(parents=True)
    (status_root / ".orchestrator" / "config.json").write_text('{"from": "status_root"}\n', encoding="utf-8")
    (status_root / ".orchestrator" / "logs").mkdir(parents=True)
    (status_root / ".orchestrator" / "logs" / "supervisor.log").write_text("live log\n", encoding="utf-8")

    result = promotion.sync_coordination_root_code(candidate, status_root)

    assert result["outcome"] == "preserved"
    assert (status_root / ".orchestrator" / "config.json").read_text(
        encoding="utf-8"
    ) == '{"from": "status_root"}\n'
    assert (status_root / ".orchestrator" / "logs" / "supervisor.log").read_text(
        encoding="utf-8"
    ) == "live log\n"


def test_replace_supervisor_records_coordination_code_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, status_root = _candidate(tmp_path)
    (status_root / ".orchestrator" / "supervisor.py").write_text("# stale copy\n", encoding="utf-8")
    live_config = tmp_path / "runtime" / "live.json"
    monkeypatch.setattr(promotion, "stop_existing_supervisor", lambda *_a, **_k: 41)
    monkeypatch.setattr(promotion, "launch_v2_supervisor", lambda *_a, **_k: 42)

    result = promotion.replace_supervisor(
        candidate,
        status_root=status_root,
        live_config_path=live_config,
        python_executable=Path(sys.executable),
        termination_timeout=1,
    )

    assert result["outcome"] == "launched"
    assert result["coordination_code_sync"]["outcome"] == "preserved"
    assert (status_root / ".orchestrator" / "supervisor.py").read_text(
        encoding="utf-8"
    ) == "# stale copy\n"


def test_replace_supervisor_survives_coordination_code_sync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    monkeypatch.setattr(promotion, "stop_existing_supervisor", lambda *_a, **_k: 41)
    monkeypatch.setattr(promotion, "launch_v2_supervisor", lambda *_a, **_k: 42)
    monkeypatch.setattr(
        promotion,
        "sync_coordination_root_code",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk full")),
    )

    result = promotion.replace_supervisor(
        candidate,
        status_root=status_root,
        live_config_path=live_config,
        python_executable=Path(sys.executable),
        termination_timeout=1,
    )

    assert result["outcome"] == "launched"
    assert result["exit_code"] == 0
    assert result["stopped_pid"] == 41
    assert result["launched_pid"] == 42


def test_deploy_root_defaults_to_current_users_portable_path(tmp_path: Path) -> None:
    env = dict(os.environ)
    env.pop("PANTHEON_DEPLOY_ROOT", None)
    output = subprocess.run(
        [sys.executable, "-c", "import promote_supervisor_runtime as m; print(m.DEPLOY_ROOT)"],
        cwd=str(Path(__file__).resolve().parent),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert output.stdout.strip() == str(Path.home() / "pantheon-ci-deploy")


def test_deploy_root_honors_env_override_and_expands_user(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["PANTHEON_DEPLOY_ROOT"] = "~/custom-deploy-root"
    output = subprocess.run(
        [
            sys.executable,
            "-c",
            "import promote_supervisor_runtime as m; "
            "print(m.DEPLOY_ROOT); print(m.LIVE_SUPERVISOR_CONFIG_PATH); print(m.COMMAND_RUNTIME_PARENT)",
        ],
        cwd=str(Path(__file__).resolve().parent),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    lines = output.stdout.strip().splitlines()
    expected_root = str(Path("~/custom-deploy-root").expanduser())
    assert lines[0] == expected_root
    assert lines[1] == str(Path(expected_root) / "runtime" / "live-supervisor-mainroot-config.json")
    assert lines[2] == str(Path(expected_root) / "command-runtimes")
