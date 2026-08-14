from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import promote_supervisor_runtime as promotion


@pytest.fixture(autouse=True)
def _command_runtime_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(promotion, "COMMAND_RUNTIME_PARENT", tmp_path / "command-runtimes")


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
    assert result["stopped_pid"] == 41
    assert result["launched_pid"] == 42
    assert events == ["stop", "launch"]
    installed = json.loads(live_config.read_text(encoding="utf-8"))
    assert installed["task_state_store"]["mode"] == "authoritative"
    assert json.loads((status_root / ".orchestrator" / "approval-queue.json").read_text(encoding="utf-8"))["version"] == 2
    assert not hasattr(promotion, "migrate_task_state_store_v2")
    assert not hasattr(promotion, "PromotionTransaction")


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
