from __future__ import annotations

import errno
import json
import os
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import promote_supervisor_runtime as promotion

_REAL_VERIFY_PROMOTION_HEALTH = promotion.verify_promotion_health
_REAL_VERIFY_DRAIN_CAPABILITY = promotion.verify_incumbent_drain_capability
_REAL_VERIFY_WORKER_SANDBOX = promotion.verify_worker_sandbox


@pytest.fixture(autouse=True)
def _command_runtime_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Existing replacement fixtures use fake PIDs/minimal source trees. Health
    # and capability are exercised explicitly by the promotion-drain tests.
    monkeypatch.setattr(promotion, "verify_promotion_health", lambda *a, **k: {"verified": True})
    monkeypatch.setattr(promotion, "verify_incumbent_drain_capability", lambda *a, **k: None)
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


def _v2_incumbent_state() -> str:
    state = promotion.runtime_state.default_state()
    state["auto_commit_archive"]["pending_token"] = "old"
    return json.dumps(state)


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


def test_render_v2_config_accepts_a_candidate_with_required_dependencies(
    tmp_path: Path,
) -> None:
    candidate, status_root = _candidate(tmp_path)
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pytest\n", encoding="utf-8")

    rendered, _identity = promotion.render_v2_config(
        candidate,
        status_root=status_root,
        live_config_path=tmp_path / "runtime" / "live.json",
        python_executable=Path(sys.executable),
        requirements_path=requirements,
    )

    assert rendered["task_state_store"]["mode"] == "authoritative"


def test_render_v2_config_dependency_preflight_fails_closed(tmp_path: Path) -> None:
    candidate, status_root = _candidate(tmp_path)
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("definitely-not-a-real-package-xyz\n", encoding="utf-8")

    with pytest.raises(ValueError, match="python dependency preflight failed"):
        promotion.render_v2_config(
            candidate,
            status_root=status_root,
            live_config_path=tmp_path / "runtime" / "live.json",
            python_executable=Path(sys.executable),
            requirements_path=requirements,
        )


def test_replace_supervisor_preserves_incumbent_on_dependency_preflight_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed dependency preflight must leave the incumbent PID, live
    config, and launch path completely untouched -- it must fail before the
    stop/write/launch sequence even begins."""

    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("definitely-not-a-real-package-xyz\n", encoding="utf-8")
    stopped: list[bool] = []
    launched: list[bool] = []
    monkeypatch.setattr(
        promotion, "stop_existing_supervisor", lambda *_a, **_k: stopped.append(True)
    )
    monkeypatch.setattr(
        promotion, "launch_v2_supervisor", lambda *_a, **_k: launched.append(True)
    )

    with pytest.raises(ValueError, match="python dependency preflight failed"):
        promotion.replace_supervisor(
            candidate,
            status_root=status_root,
            live_config_path=live_config,
            python_executable=Path(sys.executable),
            termination_timeout=1,
            requirements_path=requirements,
        )

    assert stopped == []
    assert launched == []
    assert not live_config.exists()


def test_cli_promote_dependency_preflight_failure_never_stops_incumbent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("definitely-not-a-real-package-xyz\n", encoding="utf-8")
    stopped: list[bool] = []
    monkeypatch.setattr(
        promotion, "stop_existing_supervisor", lambda *_a, **_k: stopped.append(True)
    )

    code = promotion.main(
        [
            "--promote",
            "--repo",
            str(candidate),
            "--status-root",
            str(status_root),
            "--live-config",
            str(live_config),
            "--python",
            sys.executable,
            "--requirements",
            str(requirements),
        ]
    )

    assert code == 1
    assert stopped == []
    assert not live_config.exists()


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

    assert result["outcome"] == "launched", result.get("error", result)
    assert result["command_runtime_seal"]["outcome"] == "sealed"
    assert result["worker_sandbox_preflight"]["outcome"] == "available"
    assert result["stopped_pid"] == 41
    assert result["launched_pid"] == 42
    assert events == ["stop", "launch"]
    installed = json.loads(live_config.read_text(encoding="utf-8"))
    assert installed["task_state_store"]["mode"] == "authoritative"
    assert json.loads((status_root / ".orchestrator" / "worker-runtime" / "approval-queue.json").read_text(encoding="utf-8"))["version"] == 2
    assert not hasattr(promotion, "migrate_task_state_store_v2")
    assert not hasattr(promotion, "PromotionTransaction")


def test_replace_quiesces_incumbent_before_draining_its_writers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The incumbent cannot dispatch a new queue reservation after drain."""

    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    incumbent = {"paths": {"status_file": str(status_root / "ai-status.json"), "state_file": str(status_root / ".orchestrator/worker-runtime/state.json")}}
    live_config.parent.mkdir(parents=True)
    live_config.write_text(json.dumps(incumbent), encoding="utf-8")
    events: list[str] = []

    monkeypatch.setattr(
        promotion,
        "qualify_incumbent_identity",
        lambda *_args, **_kwargs: {"root": str(candidate), "head": "incumbent"},
    )
    monkeypatch.setattr(
        promotion,
        "stop_existing_supervisor",
        lambda *_args, **_kwargs: events.append("stop") or 41,
    )
    def drain(*_args: object, **kwargs: object) -> dict[str, object]:
        assert kwargs["recover_stopped_reservations"] is True
        events.append("drain")
        return {"drained": True}

    monkeypatch.setattr(promotion, "qualify_and_drain_incumbent_writers", drain)
    monkeypatch.setattr(
        promotion,
        "launch_v2_supervisor",
        lambda *_args, **_kwargs: events.append("launch") or 42,
    )

    result = promotion.replace_supervisor(
        candidate,
        status_root=status_root,
        live_config_path=live_config,
        python_executable=Path(sys.executable),
        termination_timeout=1,
        migrate_storage=True,
    )

    assert result["outcome"] == "launched", result.get("error", result)
    assert events == ["stop", "drain", "launch"]


def test_replace_uses_canonical_runtime_lock_during_reservation_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Promotion must use the lock API that supervisor recovery can re-enter."""

    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    incumbent = {"paths": {"status_file": str(status_root / "ai-status.json"), "state_file": str(status_root / ".orchestrator/worker-runtime/state.json")}}
    live_config.parent.mkdir(parents=True)
    live_config.write_text(json.dumps(incumbent), encoding="utf-8")
    lock_events: list[str] = []

    @contextmanager
    def runtime_admission_lock(_config: dict[str, object], **_kwargs):
        lock_events.append("entered")
        try:
            yield None
        finally:
            lock_events.append("exited")

    def recover(
        _config: dict[str, object],
        _phase: str,
        *,
        runtime_admission_locked: bool = False,
    ) -> bool:
        assert lock_events.count("entered") > lock_events.count("exited")
        assert runtime_admission_locked is True
        return True

    def drain(*_args: object, **kwargs: object) -> dict[str, object]:
        assert kwargs["recover_stopped_reservations"] is True
        promotion._recover_stopped_runtime_phase_reservations(
            incumbent, ["poll_workers_before_plan"]
        )
        return {"drained": True}

    monkeypatch.setattr(promotion.runtime_state, "runtime_state_lock", runtime_admission_lock)
    monkeypatch.setattr(
        promotion,
        "qualify_incumbent_identity",
        lambda *_args, **_kwargs: {"root": str(candidate), "head": "incumbent"},
    )
    monkeypatch.setattr(
        promotion, "stop_existing_supervisor", lambda *_args, **_kwargs: 41
    )
    monkeypatch.setattr(promotion, "qualify_and_drain_incumbent_writers", drain)
    monkeypatch.setattr(promotion.supervisor, "_recover_runtime_phase_reservation", recover)
    monkeypatch.setattr(promotion, "launch_v2_supervisor", lambda *_args, **_kwargs: 42)

    result = promotion.replace_supervisor(
        candidate,
        status_root=status_root,
        live_config_path=live_config,
        python_executable=Path(sys.executable),
        termination_timeout=1,
        migrate_storage=True,
    )

    assert result["outcome"] == "launched", result.get("error", result)
    assert lock_events.count("entered") == lock_events.count("exited")


def test_replace_restarts_untouched_incumbent_when_post_stop_drain_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    incumbent = {"paths": {"status_file": str(status_root / "ai-status.json"), "state_file": str(status_root / ".orchestrator/worker-runtime/state.json")}}
    live_config.parent.mkdir(parents=True)
    live_config.write_text(json.dumps(incumbent), encoding="utf-8")
    events: list[str] = []

    monkeypatch.setattr(
        promotion,
        "qualify_incumbent_identity",
        lambda *_args, **_kwargs: {"root": str(candidate), "head": "incumbent"},
    )
    monkeypatch.setattr(
        promotion,
        "stop_existing_supervisor",
        lambda *_args, **_kwargs: events.append("stop") or 41,
    )

    def fail_drain(*_args: object, **kwargs: object) -> dict[str, object]:
        assert kwargs["recover_stopped_reservations"] is True
        events.append("drain")
        raise RuntimeError("active supervisor reservations exist")

    monkeypatch.setattr(promotion, "qualify_and_drain_incumbent_writers", fail_drain)
    monkeypatch.setattr(
        promotion,
        "launch_v2_supervisor",
        lambda *_args, **_kwargs: events.append("restart") or 42,
    )

    result = promotion.replace_supervisor(
        candidate,
        status_root=status_root,
        live_config_path=live_config,
        python_executable=Path(sys.executable),
        termination_timeout=1,
        migrate_storage=True,
    )

    assert result["outcome"] == "failed"
    assert "active supervisor reservations exist" in result["error"]
    assert events == ["stop", "drain", "restart"]
    assert json.loads(live_config.read_text(encoding="utf-8")) == incumbent


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


def test_status_root_replacement_fails_before_changing_admission_authority(
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
    incumbent_supervisor_py = old_status_root / ".orchestrator" / "supervisor.py"
    incumbent_supervisor_py.touch()
    live_config.write_text(
        json.dumps({
            "paths": {"state_file": str(old_state)},
            "watchdog": {
                "supervisor_command": [
                    sys.executable, "-u", "-B", str(incumbent_supervisor_py),
                    "--config", str(live_config), "--verbose",
                ]
            },
            "identity": {
                "root": str(old_status_root),
                "head": "1111111111111111111111111111111111111111",
                "repository": "ajoe734/pantheon",
            },
        }),
        encoding="utf-8",
    )
    stopped: list[Path] = []
    real_validate = promotion.validated_immutable_command_root
    monkeypatch.setattr(
        promotion,
        "validated_immutable_command_root",
        lambda root, **kwargs: (
            {
                "root": str(root),
                "head": "1111111111111111111111111111111111111111",
                "repository": "ajoe734/pantheon",
            }
            if Path(root) == old_status_root
            else real_validate(root, **kwargs)
        ),
    )
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
        migrate_storage=True,
    )

    assert result["outcome"] == "failed"
    assert "cannot change canonical runtime admission root" in result["error"]
    assert stopped == []


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

    assert result["outcome"] == "launched", result.get("error", result)
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

    assert result["outcome"] == "launched", result.get("error", result)
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


def test_migrate_storage_paths_moves_task_state_and_worker_runtime_files(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    coord = tmp_path / "coord"
    runtime.mkdir(parents=True)
    coord.mkdir(parents=True)

    old_log = runtime / "events.jsonl"
    old_log.write_text("event data\n", encoding="utf-8")
    (runtime / "events.jsonl.head.json").write_text('{"seq": 1}\n', encoding="utf-8")
    (runtime / "events.jsonl.lock").touch()
    (runtime / "events.jsonl.legacy-anchor.json").write_text('{"anchor": 1}\n', encoding="utf-8")

    (coord / ".orchestrator").mkdir(parents=True)
    old_state = coord / ".orchestrator" / "state.json"
    old_state.write_text('{"workers": {}}\n', encoding="utf-8")
    old_queue = coord / ".orchestrator" / "approval-queue.json"
    old_queue.write_text('{"version": 2}\n', encoding="utf-8")

    new_log = runtime / "task-state" / "events.jsonl"
    new_state = coord / ".orchestrator" / "worker-runtime" / "state.json"
    new_queue = coord / ".orchestrator" / "worker-runtime" / "approval-queue.json"

    incumbent = {
        "task_state_store": {"mode": "authoritative", "event_log": str(old_log)},
        "paths": {"state_file": str(old_state), "approval_queue": str(old_queue)},
    }
    rendered = {
        "task_state_store": {"mode": "authoritative", "event_log": str(new_log)},
        "paths": {"state_file": str(new_state), "approval_queue": str(new_queue)},
    }

    record = promotion._migrate_storage_paths(incumbent, rendered)
    assert record["migrated"] is True
    assert not old_log.exists()
    assert new_log.exists()
    assert new_log.read_text(encoding="utf-8") == "event data\n"
    assert (runtime / "task-state" / "events.jsonl.head.json").read_text(encoding="utf-8") == '{"seq": 1}\n'
    assert (runtime / "task-state" / "events.jsonl.lock").exists()
    assert (runtime / "task-state" / "events.jsonl.legacy-anchor.json").exists()

    assert not old_state.is_file()
    assert old_state.is_fifo()
    assert new_state.exists()
    assert json.loads(new_state.read_text(encoding="utf-8")) == {"workers": {}}

    assert not old_queue.is_file()
    assert old_queue.is_fifo()
    assert new_queue.exists()
    assert json.loads(new_queue.read_text(encoding="utf-8")) == {"version": 2}


def test_replace_supervisor_rolls_back_storage_migration_on_launch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    live_config.parent.mkdir(parents=True, exist_ok=True)

    old_log = tmp_path / "runtime" / "task-state-events-v2.jsonl"
    old_log.write_text("old events\n", encoding="utf-8")
    (tmp_path / "runtime" / f"{old_log.name}.head.json").write_text('{"seq": 1}\n', encoding="utf-8")
    (tmp_path / "runtime" / f"{old_log.name}.lock").touch()

    old_state = status_root / ".orchestrator" / "state.json"
    old_state.write_text(_v2_incumbent_state(), encoding="utf-8")
    old_queue = status_root / ".orchestrator" / "approval-queue.json"
    old_queue.write_text('{"version": 2, "pending": [], "history": []}\n', encoding="utf-8")

    incumbent_root = tmp_path / "incumbent-runtime"
    incumbent_root.mkdir(parents=True, exist_ok=True)
    (incumbent_root / ".orchestrator").mkdir(parents=True, exist_ok=True)
    incumbent_supervisor_py = incumbent_root / ".orchestrator" / "supervisor.py"
    incumbent_supervisor_py.touch()

    incumbent = {
        "task_state_store": {"mode": "authoritative", "event_log": str(old_log)},
        "paths": {"state_file": str(old_state), "approval_queue": str(old_queue)},
        "watchdog": {
            "supervisor_command": [
                sys.executable, "-u", "-B", str(incumbent_supervisor_py),
                "--config", str(live_config), "--verbose",
            ]
        },
        "identity": {
            "root": str(incumbent_root),
            "head": "1111111111111111111111111111111111111111",
            "repository": "ajoe734/pantheon",
        },
    }
    live_config.write_text(json.dumps(incumbent), encoding="utf-8")

    monkeypatch.setattr(promotion, "stop_existing_supervisor", lambda *a, **k: 41)

    real_validate = promotion.validated_immutable_command_root

    def mock_validate(root, **kwargs):
        if Path(root) == incumbent_root:
            return {
                "root": str(incumbent_root),
                "head": "1111111111111111111111111111111111111111",
                "repository": "ajoe734/pantheon",
            }
        return real_validate(root, **kwargs)

    monkeypatch.setattr(promotion, "validated_immutable_command_root", mock_validate)

    launched_configs = []

    def maybe_failing_launch(cfg, *a, **k):
        launched_configs.append(cfg)
        if len(launched_configs) == 1:
            raise RuntimeError("simulated launch crash")
        return 999

    monkeypatch.setattr(promotion, "launch_v2_supervisor", maybe_failing_launch)

    result = promotion.replace_supervisor(
        candidate,
        status_root=status_root,
        live_config_path=live_config,
        python_executable=Path(sys.executable),
        termination_timeout=1,
        migrate_storage=True,
    )

    assert result["outcome"] == "failed"
    assert "simulated launch crash" in result["error"]
    # Files should be rolled back to their incumbent locations
    assert old_log.exists()
    assert old_log.read_text(encoding="utf-8") == "old events\n"
    assert (tmp_path / "runtime" / f"{old_log.name}.head.json").exists()
    assert old_state.exists()
    assert json.loads(old_state.read_text())["auto_commit_archive"]["pending_token"] == "old"
    assert old_queue.exists()
    assert json.loads(old_queue.read_text(encoding="utf-8")) == {"version": 2, "pending": [], "history": []}
    # Live config should be restored to incumbent
    restored = json.loads(live_config.read_text(encoding="utf-8"))
    assert restored["paths"]["state_file"] == str(old_state)


def test_migrate_storage_paths_fails_closed_when_store_lock_held(tmp_path: Path) -> None:
    import fcntl

    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True)
    old_log = runtime / "events.jsonl"
    old_log.write_text("event data\n", encoding="utf-8")
    (runtime / "events.jsonl.head.json").write_text('{"seq": 1}\n', encoding="utf-8")
    old_lock = runtime / "events.jsonl.lock"
    old_lock.touch()

    new_log = runtime / "task-state" / "events.jsonl"
    incumbent = {"task_state_store": {"mode": "authoritative", "event_log": str(old_log)}}
    rendered = {"task_state_store": {"mode": "authoritative", "event_log": str(new_log)}}

    # Hold the lock exclusively in the current process (simulating concurrent writer / supervisor)
    lock_fd = os.open(old_lock, os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="task-state store lock .* is held by another process"):
            promotion._migrate_storage_paths(incumbent, rendered)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    # Authority preserved at old location
    assert old_log.exists()
    assert not new_log.exists()


def test_migrate_storage_paths_preflight_rejects_symlinks(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True)
    symlink_src = runtime / "symlink_events.jsonl"
    target_src = runtime / "real_events.jsonl"
    target_src.write_text("event data\n", encoding="utf-8")
    symlink_src.symlink_to(target_src)

    new_log = runtime / "task-state" / "events.jsonl"
    incumbent = {"task_state_store": {"mode": "authoritative", "event_log": str(symlink_src)}}
    rendered = {"task_state_store": {"mode": "authoritative", "event_log": str(new_log)}}

    with pytest.raises(ValueError, match="contains symlink"):
        promotion._migrate_storage_paths(incumbent, rendered)


def test_migrate_storage_paths_preflight_rejects_target_collision(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True)
    old_log = runtime / "events.jsonl"
    old_log.write_text("event data\n", encoding="utf-8")
    (runtime / "events.jsonl.head.json").write_text('{"seq": 1}\n', encoding="utf-8")
    (runtime / "events.jsonl.lock").touch()

    new_log = runtime / "task-state" / "events.jsonl"
    new_log.parent.mkdir(parents=True, exist_ok=True)
    # Target file collision
    new_log.write_text("collision data\n", encoding="utf-8")

    incumbent = {"task_state_store": {"mode": "authoritative", "event_log": str(old_log)}}
    rendered = {"task_state_store": {"mode": "authoritative", "event_log": str(new_log)}}

    with pytest.raises(RuntimeError, match="collision: .* already exists"):
        promotion._migrate_storage_paths(incumbent, rendered)

    # Old log unchanged
    assert old_log.read_text(encoding="utf-8") == "event data\n"
    assert new_log.read_text(encoding="utf-8") == "collision data\n"


def test_migrate_storage_paths_atomic_rollback_on_rename_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True)
    old_log = runtime / "events.jsonl"
    old_log.write_text("event data\n", encoding="utf-8")
    (runtime / "events.jsonl.head.json").write_text('{"seq": 1}\n', encoding="utf-8")
    (runtime / "events.jsonl.lock").touch()

    new_log = runtime / "task-state" / "events.jsonl"
    incumbent = {"task_state_store": {"mode": "authoritative", "event_log": str(old_log)}}
    rendered = {"task_state_store": {"mode": "authoritative", "event_log": str(new_log)}}

    real_replace = os.replace

    def faulty_replace(src, dst):
        if "head.json" in str(src):
            raise OSError("simulated disk error on head rename")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", faulty_replace)

    with pytest.raises(OSError, match="simulated disk error"):
        promotion._migrate_storage_paths(incumbent, rendered)

    # All files rolled back to old path, preserving single authority
    assert old_log.exists()
    assert (runtime / "events.jsonl.head.json").exists()
    assert not new_log.exists()


def test_migrate_storage_paths_enforces_0700_permissions(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True)
    old_log = runtime / "events.jsonl"
    old_log.write_text("event data\n", encoding="utf-8")
    (runtime / "events.jsonl.head.json").write_text('{"seq": 1}\n', encoding="utf-8")
    (runtime / "events.jsonl.lock").touch()

    new_log = runtime / "task-state" / "events.jsonl"
    incumbent = {"task_state_store": {"mode": "authoritative", "event_log": str(old_log)}}
    rendered = {"task_state_store": {"mode": "authoritative", "event_log": str(new_log)}}

    promotion._migrate_storage_paths(incumbent, rendered)
    # Check directory permissions is 0o700
    dir_mode = stat.S_IMODE(new_log.parent.stat().st_mode)
    assert dir_mode == 0o700


def test_migrate_storage_paths_preflight_rejects_target_lock_collision(tmp_path: Path) -> None:
    import fcntl

    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True)
    old_log = runtime / "events.jsonl"
    old_log.write_text("event data\n", encoding="utf-8")
    (runtime / "events.jsonl.head.json").write_text('{"seq": 1}\n', encoding="utf-8")
    (runtime / "events.jsonl.lock").touch()

    new_log = runtime / "task-state" / "events.jsonl"
    new_log.parent.mkdir(parents=True, exist_ok=True)
    target_lock = new_log.with_name(f"{new_log.name}.lock")
    target_lock.touch()

    held = os.open(target_lock, os.O_RDWR)
    try:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held_inode = os.fstat(held).st_ino

        incumbent = {"task_state_store": {"mode": "authoritative", "event_log": str(old_log)}}
        rendered = {"task_state_store": {"mode": "authoritative", "event_log": str(new_log)}}

        with pytest.raises(RuntimeError, match="collision: .* already exists"):
            promotion._migrate_storage_paths(incumbent, rendered)

        # Held lock was not overwritten; same inode still held
        assert os.fstat(held).st_ino == held_inode
        assert old_log.exists()
        assert not new_log.exists()
    finally:
        os.close(held)


def test_replace_supervisor_restarts_incumbent_with_incumbent_identity_on_launch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    live_config.parent.mkdir(parents=True, exist_ok=True)

    old_log = tmp_path / "runtime" / "task-state-events-v2.jsonl"
    old_log.write_text("old events\n", encoding="utf-8")
    (tmp_path / "runtime" / f"{old_log.name}.head.json").write_text('{"seq": 1}\n', encoding="utf-8")
    (tmp_path / "runtime" / f"{old_log.name}.lock").touch()

    old_state = status_root / ".orchestrator" / "state.json"
    old_state.write_text(_v2_incumbent_state(), encoding="utf-8")
    old_queue = status_root / ".orchestrator" / "approval-queue.json"
    old_queue.write_text('{"version": 2, "pending": [], "history": []}\n', encoding="utf-8")

    incumbent_root = tmp_path / "incumbent-runtime"
    incumbent_root.mkdir(parents=True, exist_ok=True)
    (incumbent_root / ".orchestrator").mkdir(parents=True, exist_ok=True)
    incumbent_supervisor_py = incumbent_root / ".orchestrator" / "supervisor.py"
    incumbent_supervisor_py.touch()

    incumbent = {
        "task_state_store": {"mode": "authoritative", "event_log": str(old_log)},
        "paths": {"state_file": str(old_state), "approval_queue": str(old_queue)},
        "watchdog": {
            "supervisor_command": [
                sys.executable, "-u", "-B", str(incumbent_supervisor_py),
                "--config", str(live_config), "--verbose"
            ]
        },
        "identity": {
            "root": str(incumbent_root),
            "head": "1111111111111111111111111111111111111111",
            "repository": "ajoe734/pantheon",
        }
    }
    live_config.write_text(json.dumps(incumbent), encoding="utf-8")

    monkeypatch.setattr(promotion, "stop_existing_supervisor", lambda *a, **k: 41)

    real_validate = promotion.validated_immutable_command_root

    def mock_validate(root, **kwargs):
        if Path(root) == incumbent_root:
            return {
                "root": str(incumbent_root),
                "head": "1111111111111111111111111111111111111111",
                "repository": "ajoe734/pantheon",
            }
        return real_validate(root, **kwargs)

    monkeypatch.setattr(promotion, "validated_immutable_command_root", mock_validate)

    calls = []

    def mock_launch(rendered_conf, *, identity, status_root, authority_env_file=None):
        calls.append({"conf": rendered_conf, "identity": identity})
        if len(calls) == 1:
            raise RuntimeError("simulated candidate launch crash")
        return 777

    monkeypatch.setattr(promotion, "launch_v2_supervisor", mock_launch)

    result = promotion.replace_supervisor(
        candidate,
        status_root=status_root,
        live_config_path=live_config,
        python_executable=Path(sys.executable),
        termination_timeout=1,
        migrate_storage=True,
    )

    assert result["outcome"] == "failed"
    assert "simulated candidate launch crash" in result["error"]
    assert result.get("restarted_pid") == 777
    assert len(calls) == 2
    # Second launch was incumbent restart with incumbent identity
    assert calls[1]["identity"]["root"] == str(incumbent_root)
    assert calls[1]["identity"]["head"] == "1111111111111111111111111111111111111111"
    # Files rolled back
    assert old_log.exists()
    assert old_state.exists()
    assert old_queue.exists()


def test_replace_supervisor_reports_rollback_failure_on_restart_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    live_config.parent.mkdir(parents=True, exist_ok=True)

    old_log = tmp_path / "runtime" / "task-state-events-v2.jsonl"
    old_log.write_text("old events\n", encoding="utf-8")
    (tmp_path / "runtime" / f"{old_log.name}.head.json").write_text('{"seq": 1}\n', encoding="utf-8")
    (tmp_path / "runtime" / f"{old_log.name}.lock").touch()

    old_state = status_root / ".orchestrator" / "state.json"
    old_state.write_text(_v2_incumbent_state(), encoding="utf-8")
    old_queue = status_root / ".orchestrator" / "approval-queue.json"
    old_queue.write_text('{"version": 2, "pending": [], "history": []}\n', encoding="utf-8")

    incumbent_root = tmp_path / "incumbent-runtime"
    incumbent_root.mkdir(parents=True, exist_ok=True)
    (incumbent_root / ".orchestrator").mkdir(parents=True, exist_ok=True)
    incumbent_supervisor_py = incumbent_root / ".orchestrator" / "supervisor.py"
    incumbent_supervisor_py.touch()

    incumbent = {
        "task_state_store": {"mode": "authoritative", "event_log": str(old_log)},
        "paths": {"state_file": str(old_state), "approval_queue": str(old_queue)},
        "watchdog": {
            "supervisor_command": [
                sys.executable, "-u", "-B", str(incumbent_supervisor_py),
                "--config", str(live_config), "--verbose"
            ]
        },
        "identity": {
            "root": str(incumbent_root),
            "head": "1111111111111111111111111111111111111111",
            "repository": "ajoe734/pantheon",
        }
    }
    live_config.write_text(json.dumps(incumbent), encoding="utf-8")

    monkeypatch.setattr(promotion, "stop_existing_supervisor", lambda *a, **k: 41)

    real_validate = promotion.validated_immutable_command_root

    def mock_validate(root, **kwargs):
        if Path(root) == incumbent_root:
            return {
                "root": str(incumbent_root),
                "head": "1111111111111111111111111111111111111111",
                "repository": "ajoe734/pantheon",
            }
        return real_validate(root, **kwargs)

    monkeypatch.setattr(promotion, "validated_immutable_command_root", mock_validate)

    def failing_launch(*a, **k):
        raise RuntimeError("always failing launch")

    monkeypatch.setattr(promotion, "launch_v2_supervisor", failing_launch)

    result = promotion.replace_supervisor(
        candidate,
        status_root=status_root,
        live_config_path=live_config,
        python_executable=Path(sys.executable),
        termination_timeout=1,
        migrate_storage=True,
    )

    assert result["outcome"] == "failed"
    assert "rollback failures" in result["error"]
    assert "rollback_errors" in result
    assert any("incumbent restart failed" in err for err in result["rollback_errors"])


def test_fsync_dir_propagates_durability_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    test_dir = tmp_path / "sync_target"
    test_dir.mkdir()

    def failing_fsync(fd: int) -> None:
        raise OSError(errno.EIO, "injected directory fsync failure")

    monkeypatch.setattr(os, "fsync", failing_fsync)

    with pytest.raises(OSError, match="injected directory fsync failure"):
        promotion._fsync_dir(test_dir)


def test_migrate_storage_paths_fails_closed_on_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True)
    old_log = runtime / "events.jsonl"
    old_log.write_text("event data\n", encoding="utf-8")
    (runtime / "events.jsonl.head.json").write_text('{"seq": 1}\n', encoding="utf-8")
    (runtime / "events.jsonl.lock").touch()

    new_log = runtime / "task-state" / "events.jsonl"
    incumbent = {"task_state_store": {"mode": "authoritative", "event_log": str(old_log)}}
    rendered = {"task_state_store": {"mode": "authoritative", "event_log": str(new_log)}}

    def failing_fsync(fd: int) -> None:
        raise OSError(errno.EIO, "injected directory fsync failure")

    monkeypatch.setattr(os, "fsync", failing_fsync)

    with pytest.raises(promotion.StorageMigrationError) as exc_info:
        promotion._migrate_storage_paths(incumbent, rendered)

    err = exc_info.value
    assert "injected directory fsync failure" in str(err)
    assert err.forward_error is not None
    assert err.migration_record is not None
    assert str(new_log.parent) in err.migration_record["fsynced_directories"]


def test_migrate_storage_paths_partial_rollback_retains_lock_and_reports_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True)
    old_log = runtime / "events.jsonl"
    old_log.write_text("event data\n", encoding="utf-8")
    (runtime / "events.jsonl.head.json").write_text('{"seq": 1}\n', encoding="utf-8")
    (runtime / "events.jsonl.lock").touch()

    new_log = runtime / "task-state" / "events.jsonl"
    incumbent = {"task_state_store": {"mode": "authoritative", "event_log": str(old_log)}}
    rendered = {"task_state_store": {"mode": "authoritative", "event_log": str(new_log)}}

    original_replace = os.replace

    def fail_head_move_and_journal_rollback(src, dst):
        if str(src) == str(old_log) + ".head.json":
            raise OSError(errno.EIO, "injected forward head move failure")
        if Path(src) == new_log and Path(dst) == old_log:
            raise OSError(errno.EIO, "injected journal rollback failure")
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_head_move_and_journal_rollback)

    with pytest.raises(promotion.StorageMigrationError) as exc_info:
        promotion._migrate_storage_paths(incumbent, rendered, keep_lock=True)

    err = exc_info.value
    assert not err.restoration_verified
    assert err.lock_fd is not None
    assert any("injected journal rollback failure" in e for e in err.rollback_errors)
    try:
        os.close(err.lock_fd)
    except OSError:
        pass


def test_qualify_incumbent_identity_fails_closed_on_unqualified_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_identity = {
        "root": "/tmp/candidate",
        "head": "2" * 40,
        "repository": "ajoe734/pantheon",
    }
    # Case 1: Incumbent defines watchdog command, but validation fails closed
    incumbent_with_bad_root = {
        "watchdog": {
            "supervisor_command": ["python3", "/tmp/unqualified-runtime/.orchestrator/supervisor.py"]
        }
    }
    monkeypatch.setattr(
        promotion,
        "validated_immutable_command_root",
        mock.Mock(side_effect=ValueError("rejected unqualified runtime")),
    )
    with pytest.raises(ValueError, match="rejected unqualified runtime"):
        promotion.qualify_incumbent_identity(
            incumbent_with_bad_root, candidate_identity=candidate_identity
        )

    # Case 2: Incumbent specifies identity head mismatch
    monkeypatch.setattr(
        promotion,
        "validated_immutable_command_root",
        mock.Mock(return_value={"root": "/tmp/good-root", "head": "1" * 40, "repository": "ajoe734/pantheon"}),
    )
    incumbent_head_mismatch = {
        "command_root": "/tmp/good-root",
        "identity": {"root": "/tmp/good-root", "head": "3" * 40},
    }
    with pytest.raises(ValueError, match="incumbent identity head mismatch"):
        promotion.qualify_incumbent_identity(
            incumbent_head_mismatch, candidate_identity=candidate_identity
        )

    # Case 3: Incumbent with no command / identity returns None (never candidate identity)
    incumbent_empty = {"paths": {"state_file": "/tmp/state.json"}}
    result = promotion.qualify_incumbent_identity(
        incumbent_empty, candidate_identity=candidate_identity
    )
    assert result is None


def test_replace_supervisor_refuses_incumbent_restart_on_incomplete_restoration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    live_config.parent.mkdir(parents=True, exist_ok=True)

    old_log = tmp_path / "runtime" / "task-state-events-v2.jsonl"
    old_log.write_text("old events\n", encoding="utf-8")
    (tmp_path / "runtime" / f"{old_log.name}.head.json").write_text('{"seq": 1}\n', encoding="utf-8")
    (tmp_path / "runtime" / f"{old_log.name}.lock").touch()

    old_state = status_root / ".orchestrator" / "state.json"
    old_state.write_text(_v2_incumbent_state(), encoding="utf-8")
    old_queue = status_root / ".orchestrator" / "approval-queue.json"
    old_queue.write_text('{"version": 2, "pending": [], "history": []}\n', encoding="utf-8")

    incumbent_root = tmp_path / "incumbent-runtime"
    incumbent_root.mkdir(parents=True, exist_ok=True)
    (incumbent_root / ".orchestrator").mkdir(parents=True, exist_ok=True)
    incumbent_supervisor_py = incumbent_root / ".orchestrator" / "supervisor.py"
    incumbent_supervisor_py.touch()

    incumbent = {
        "task_state_store": {"mode": "authoritative", "event_log": str(old_log)},
        "paths": {"state_file": str(old_state), "approval_queue": str(old_queue)},
        "watchdog": {
            "supervisor_command": [
                sys.executable, "-u", "-B", str(incumbent_supervisor_py),
                "--config", str(live_config), "--verbose"
            ]
        },
        "identity": {
            "root": str(incumbent_root),
            "head": "1111111111111111111111111111111111111111",
            "repository": "ajoe734/pantheon",
        }
    }
    live_config.write_text(json.dumps(incumbent), encoding="utf-8")

    monkeypatch.setattr(promotion, "stop_existing_supervisor", lambda *a, **k: 41)

    real_validate = promotion.validated_immutable_command_root

    def mock_validate(root, **kwargs):
        if Path(root) == incumbent_root:
            return {
                "root": str(incumbent_root),
                "head": "1111111111111111111111111111111111111111",
                "repository": "ajoe734/pantheon",
            }
        return real_validate(root, **kwargs)

    monkeypatch.setattr(promotion, "validated_immutable_command_root", mock_validate)

    restarted = []
    monkeypatch.setattr(promotion, "launch_v2_supervisor", lambda *a, **k: restarted.append(True) or 999)

    def failing_migration(*a, **k):
        raise promotion.StorageMigrationError(
            "split storage simulated",
            migration_record={"migrated": True, "files": []},
            rollback_errors=["reverse rollback failed"],
            restoration_verified=False,
            lock_fd=None,
        )

    monkeypatch.setattr(promotion, "_migrate_storage_paths", failing_migration)

    result = promotion.replace_supervisor(
        candidate,
        status_root=status_root,
        live_config_path=live_config,
        python_executable=Path(sys.executable),
        termination_timeout=1,
        migrate_storage=True,
    )

    assert result["outcome"] == "failed"
    assert "refusing to restart incumbent against incomplete restoration / split storage" in result["error"]
    assert len(restarted) == 0


def test_replace_supervisor_qualifies_incumbent_before_stopping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    live_config.parent.mkdir(parents=True, exist_ok=True)

    incumbent_root = tmp_path / "unqualified-incumbent"
    incumbent_supervisor_py = incumbent_root / ".orchestrator" / "supervisor.py"
    incumbent = {
        "paths": {"state_file": str(status_root / ".orchestrator" / "state.json")},
        "watchdog": {
            "supervisor_command": [
                sys.executable, "-u", "-B", str(incumbent_supervisor_py),
                "--config", str(live_config)
            ]
        }
    }
    live_config.write_text(json.dumps(incumbent), encoding="utf-8")

    stopped = []
    monkeypatch.setattr(promotion, "stop_existing_supervisor", lambda *a, **k: stopped.append(True) or 41)

    real_validate = promotion.validated_immutable_command_root

    def mock_validate(root, **kwargs):
        if Path(root) == incumbent_root:
            raise ValueError("incumbent command root validation failed")
        return real_validate(root, **kwargs)

    monkeypatch.setattr(promotion, "validated_immutable_command_root", mock_validate)

    result = promotion.replace_supervisor(
        candidate,
        status_root=status_root,
        live_config_path=live_config,
        python_executable=Path(sys.executable),
        termination_timeout=1,
        migrate_storage=True,
    )

    assert result["outcome"] == "failed"
    assert "incumbent command root validation failed" in result["error"]
    assert len(stopped) == 0


def test_replace_supervisor_refuses_shutdown_when_incumbent_identity_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    live_config.parent.mkdir(parents=True, exist_ok=True)

    # Incumbent has no watchdog command or identity metadata
    incumbent = {
        "paths": {"state_file": str(status_root / ".orchestrator" / "state.json")},
    }
    live_config.write_text(json.dumps(incumbent), encoding="utf-8")

    stopped = []
    monkeypatch.setattr(
        promotion, "stop_existing_supervisor", lambda *a, **k: stopped.append(True) or 41
    )

    result = promotion.replace_supervisor(
        candidate,
        status_root=status_root,
        live_config_path=live_config,
        python_executable=Path(sys.executable),
        termination_timeout=1,
        migrate_storage=True,
    )

    assert result["outcome"] == "failed"
    assert "existing incumbent identity is not qualified for rollback" in result["error"]
    assert len(stopped) == 0


def test_post_rename_config_directory_fsync_failure_restores_and_verifies_incumbent_config_before_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, status_root = _candidate(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    live_config.parent.mkdir(parents=True, exist_ok=True)

    old_log = tmp_path / "runtime" / "task-state-events-v2.jsonl"
    old_log.write_text("old events\n", encoding="utf-8")
    (tmp_path / "runtime" / f"{old_log.name}.head.json").write_text('{"seq": 1}\n', encoding="utf-8")
    (tmp_path / "runtime" / f"{old_log.name}.lock").touch()

    old_state = status_root / ".orchestrator" / "state.json"
    old_state.write_text(_v2_incumbent_state(), encoding="utf-8")
    old_queue = status_root / ".orchestrator" / "approval-queue.json"
    old_queue.write_text('{"version": 2, "pending": [], "history": []}\n', encoding="utf-8")

    incumbent_root = tmp_path / "incumbent-runtime"
    incumbent_root.mkdir(parents=True, exist_ok=True)
    (incumbent_root / ".orchestrator").mkdir(parents=True, exist_ok=True)
    incumbent_supervisor_py = incumbent_root / ".orchestrator" / "supervisor.py"
    incumbent_supervisor_py.touch()

    incumbent = {
        "marker": "incumbent",
        "task_state_store": {"mode": "authoritative", "event_log": str(old_log)},
        "paths": {"state_file": str(old_state), "approval_queue": str(old_queue)},
        "watchdog": {
            "supervisor_command": [
                sys.executable, "-u", "-B", str(incumbent_supervisor_py),
                "--config", str(live_config), "--verbose",
            ]
        },
        "identity": {
            "root": str(incumbent_root),
            "head": "1111111111111111111111111111111111111111",
            "repository": "ajoe734/pantheon",
        },
    }
    live_config.write_text(json.dumps(incumbent), encoding="utf-8")

    stopped = []
    monkeypatch.setattr(
        promotion, "stop_existing_supervisor", lambda *a, **k: stopped.append(True) or 41
    )

    real_validate = promotion.validated_immutable_command_root

    def mock_validate(root, **kwargs):
        if Path(root) == incumbent_root:
            return {
                "root": str(incumbent_root),
                "head": "1111111111111111111111111111111111111111",
                "repository": "ajoe734/pantheon",
            }
        return real_validate(root, **kwargs)

    monkeypatch.setattr(promotion, "validated_immutable_command_root", mock_validate)

    restarted_configs = []

    def mock_launch(cfg, *a, **k):
        restarted_configs.append(cfg)
        return 999

    monkeypatch.setattr(promotion, "launch_v2_supervisor", mock_launch)

    real_fsync = os.fsync
    injected = []

    def fail_config_dir_fsync(fd):
        if (
            not injected
            and stat.S_ISDIR(os.fstat(fd).st_mode)
            and Path(os.readlink(f"/proc/self/fd/{fd}")) == live_config.parent
        ):
            injected.append(True)
            raise OSError(errno.EIO, "review injected config directory fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_config_dir_fsync)

    result = promotion.replace_supervisor(
        candidate,
        status_root=status_root,
        live_config_path=live_config,
        python_executable=Path(sys.executable),
        termination_timeout=1,
        migrate_storage=True,
    )

    assert bool(injected) is True
    assert result["outcome"] == "failed"
    assert "review injected config directory fsync failure" in result["error"]
    assert result["restoration_verified"] is True
    assert result["restarted_pid"] == 999

    on_disk_config = json.loads(live_config.read_text(encoding="utf-8"))
    assert on_disk_config["marker"] == "incumbent"

    assert old_log.exists()
    assert old_state.exists()
    assert old_queue.exists()

    assert len(restarted_configs) == 1
    assert restarted_configs[0]["marker"] == "incumbent"


def test_retained_immutable_writer_fails_closed_and_does_not_recreate_retired_state(
    tmp_path: Path,
) -> None:
    status = tmp_path / "status"
    orch = status / ".orchestrator"
    orch.mkdir(parents=True)
    old_state = orch / "state.json"
    old_queue = orch / "approval-queue.json"
    new_state = orch / "worker-runtime" / "state.json"
    new_queue = orch / "worker-runtime" / "approval-queue.json"

    import runtime_state
    state = runtime_state.default_state()
    state["auto_commit_archive"]["pending_token"] = "before-migration"
    old_state.write_text(json.dumps(state))
    old_queue.write_text('{"version": 2, "pending": [], "history": []}')

    old_cfg = {
        "paths": {
            "status_file": str(status / "ai-status.json"),
            "state_file": str(old_state),
            "approval_queue": str(old_queue),
        }
    }
    new_cfg = {
        "paths": dict(
            old_cfg["paths"],
            state_file=str(new_state),
            approval_queue=str(new_queue),
        )
    }

    record = promotion._migrate_storage_paths(old_cfg, new_cfg)
    assert record["migrated"] is True
    assert old_state.is_fifo()
    assert not old_state.is_file()

    old_root = Path(os.environ.get("PANTHEON_COMMAND_ROOT", Path.cwd()))
    program = """import sys, json
sys.path.insert(0, sys.argv[1])
import runtime_state
cfg = json.loads(sys.argv[2])
with runtime_state.runtime_state_update(cfg) as s:
    s["auto_commit_archive"]["pending_token"] = "old-runtime-after-cutover"
"""
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith(("PANTHEON_", "AI_"))}
    proc = subprocess.run(
        [sys.executable, "-c", program, str(old_root / ".orchestrator"), json.dumps(old_cfg)],
        env=clean_env,
        text=True,
        capture_output=True,
        timeout=15,
    )

    assert proc.returncode != 0
    assert old_state.is_fifo()
    assert not old_state.is_file()
    new_token = json.loads(new_state.read_text())["auto_commit_archive"]["pending_token"]
    assert new_token == "before-migration"


def test_qualify_and_drain_incumbent_writers_fails_closed_on_active_reservations(
    tmp_path: Path,
) -> None:
    incumbent_state = {
        "supervisor": {
            "runtime_phase_reservations": {
                "phase-1": {"status": "active"}
            }
        },
        "workers": {},
    }
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(incumbent_state))
    incumbent = {"paths": {"state_file": str(state_file)}}

    with pytest.raises(RuntimeError, match="active supervisor reservations exist"):
        promotion.qualify_and_drain_incumbent_writers(incumbent, timeout_seconds=1.0)


def test_stopped_reservation_recovery_uses_the_promotion_admission_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []

    def recover(
        _config: dict[str, object],
        phase_name: str,
        *,
        runtime_admission_locked: bool = False,
    ) -> None:
        calls.append((phase_name, runtime_admission_locked))

    monkeypatch.setattr(
        promotion.supervisor,
        "_recover_runtime_phase_reservation",
        recover,
    )

    assert promotion._recover_stopped_runtime_phase_reservations(
        {"paths": {}}, ["poll_workers_before_plan"]
    ) == ["poll_workers_before_plan"]
    assert calls == [("poll_workers_before_plan", True)]


def test_remove_retired_path_fence_restricted_to_verified_fences(tmp_path: Path) -> None:
    reg_file = tmp_path / "regular.json"
    reg_file.write_text("{}", encoding="utf-8")
    promotion._remove_retired_path_fence(reg_file)
    assert reg_file.exists()
    assert reg_file.is_file()

    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    symlink_file = tmp_path / "symlink.json"
    symlink_file.symlink_to(target)
    promotion._remove_retired_path_fence(symlink_file)
    assert symlink_file.is_symlink()

    empty_dir = tmp_path / "empty_dir_fence"
    empty_dir.mkdir(mode=0o700)
    promotion._remove_retired_path_fence(empty_dir)
    assert not empty_dir.exists()

    if hasattr(os, "mkfifo"):
        fifo_file = tmp_path / "fifo_fence"
        os.mkfifo(str(fifo_file), 0o600)
        assert fifo_file.is_fifo()
        promotion._remove_retired_path_fence(fifo_file)
        assert not fifo_file.exists()


def test_partial_rollback_preserves_already_restored_head_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, status = _candidate(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    old_log = runtime / "task-state-events-v2.jsonl"
    old_head = Path(str(old_log) + ".head.json")
    old_lock = Path(str(old_log) + ".lock")
    old_log.write_text("canonical journal bytes\n", encoding="utf-8")
    old_head.write_text("canonical head bytes\n", encoding="utf-8")
    old_lock.touch()
    state = status / ".orchestrator" / "state.json"
    queue = status / ".orchestrator" / "approval-queue.json"
    state.write_text(_v2_incumbent_state(), encoding="utf-8")
    queue.write_text('{"version":2,"pending":[],"history":[]}', encoding="utf-8")
    incumbent = {
        "command_root": str(candidate),
        "paths": {"state_file": str(state), "approval_queue": str(queue)},
        "task_state_store": {"mode": "authoritative", "event_log": str(old_log)},
    }
    live = runtime / "live.json"
    live.write_text(json.dumps(incumbent), encoding="utf-8")
    monkeypatch.setattr(promotion, "stop_existing_supervisor", lambda *a, **k: 41)
    monkeypatch.setattr(promotion, "launch_v2_supervisor", lambda *a, **k: 999)
    new_log = runtime / "task-state" / old_log.name
    new_head = Path(str(new_log) + ".head.json")
    real_replace = os.replace
    failed = []

    def injected(src, dst):
        if Path(src) == old_lock:
            raise OSError(errno.EIO, "review forward lock rename failure")
        if Path(src) == new_log and Path(dst) == old_log and not failed:
            failed.append(True)
            raise OSError(errno.EIO, "review first journal rollback failure")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", injected)
    result = promotion.replace_supervisor(
        candidate,
        status_root=status,
        live_config_path=live,
        python_executable=Path(sys.executable),
        termination_timeout=1,
        migrate_storage=True,
    )
    assert result["outcome"] == "failed"
    assert old_head.exists()
    assert old_head.read_text(encoding="utf-8") == "canonical head bytes\n"
    assert not new_head.exists()


def test_replace_supervisor_mixed_restored_unrestored_files_and_durability_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, status = _candidate(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    old_log = runtime / "task-state-events-v2.jsonl"
    old_head = Path(str(old_log) + ".head.json")
    old_lock = Path(str(old_log) + ".lock")
    old_log.write_text("canonical journal bytes\n", encoding="utf-8")
    old_head.write_text("canonical head bytes\n", encoding="utf-8")
    old_lock.touch()

    state = status / ".orchestrator" / "state.json"
    queue = status / ".orchestrator" / "approval-queue.json"
    state.write_text(_v2_incumbent_state(), encoding="utf-8")
    queue.write_text('{"version": 2, "pending": [], "history": []}', encoding="utf-8")

    incumbent = {
        "command_root": str(candidate),
        "paths": {"state_file": str(state), "approval_queue": str(queue)},
        "task_state_store": {"mode": "authoritative", "event_log": str(old_log)},
    }
    live = runtime / "live.json"
    live.write_text(json.dumps(incumbent), encoding="utf-8")

    stopped = []
    restarted = []
    monkeypatch.setattr(promotion, "stop_existing_supervisor", lambda *a, **k: stopped.append(True) or 41)
    monkeypatch.setattr(promotion, "launch_v2_supervisor", lambda *a, **k: restarted.append(True) or 999)

    new_log = runtime / "task-state" / old_log.name
    new_head = Path(str(new_log) + ".head.json")

    real_replace = os.replace
    def injected_replace(src, dst):
        if Path(src) == old_lock:
            raise OSError(errno.EIO, "forward lock rename failure")
        if Path(src) == new_log and Path(dst) == old_log:
            raise OSError(errno.EIO, "reverse journal rollback failure")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", injected_replace)

    def injected_fsync_dir(path: Path) -> None:
        raise OSError(errno.EIO, f"injected rollback directory durability fsync failure for {path}")

    monkeypatch.setattr(promotion, "_fsync_dir", injected_fsync_dir)

    result = promotion.replace_supervisor(
        candidate,
        status_root=status,
        live_config_path=live,
        python_executable=Path(sys.executable),
        termination_timeout=1,
        migrate_storage=True,
    )

    assert result["outcome"] == "failed"
    assert result["restoration_verified"] is False
    assert len(restarted) == 0
    assert "refusing to restart incumbent against incomplete restoration / split storage" in result["error"]
    assert any("reverse journal rollback failure" in err for err in result["rollback_errors"])
    assert any("injected rollback directory durability fsync failure" in err for err in result["rollback_errors"])

    # Restored head file is preserved and intact
    assert old_head.exists()
    assert old_head.read_text(encoding="utf-8") == "canonical head bytes\n"
    assert not new_head.exists()

    # Unrestored journal remains at new location
    assert not old_log.exists()
    assert new_log.exists()


def test_migration_fails_closed_and_rolls_back_when_fence_creation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = tmp_path / "status" / ".orchestrator"
    orch.mkdir(parents=True)
    old_state = orch / "state.json"
    old_queue = orch / "approval-queue.json"
    old_state.write_text('{"state": "original"}', encoding="utf-8")
    old_queue.write_text('{"queue": "original"}', encoding="utf-8")

    old_cfg = {"paths": {"state_file": str(old_state), "approval_queue": str(old_queue)}}
    new_cfg = {"paths": {k: str(orch / "worker-runtime" / Path(v).name) for k, v in old_cfg["paths"].items()}}

    def fail_fifo(*a, **k):
        raise OSError(errno.ENOSPC, "disk full: cannot create fifo fence")

    real_mkdir = Path.mkdir
    def fail_fallback(self, *a, **k):
        if self in (old_state, old_queue):
            raise OSError(errno.ENOSPC, "disk full: cannot create mkdir fence")
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(os, "mkfifo", fail_fifo)
    monkeypatch.setattr(Path, "mkdir", fail_fallback)

    with pytest.raises(promotion.StorageMigrationError) as exc_info:
        promotion._migrate_storage_paths(old_cfg, new_cfg)

    err = exc_info.value
    assert "cannot establish retired-path fence" in str(err)
    # Both paths rolled back and restored to regular files
    assert old_state.exists() and old_state.is_file()
    assert old_queue.exists() and old_queue.is_file()
    assert json.loads(old_state.read_text()) == {"state": "original"}
    assert json.loads(old_queue.read_text()) == {"queue": "original"}
    # Neither new path was left behind
    assert not (orch / "worker-runtime" / "state.json").exists()
    assert not (orch / "worker-runtime" / "approval-queue.json").exists()


def test_retained_immutable_writer_after_fence_creation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = tmp_path / "status" / ".orchestrator"
    orch.mkdir(parents=True)
    old_state = orch / "state.json"
    old_queue = orch / "approval-queue.json"
    import runtime_state
    state = runtime_state.default_state()
    state["auto_commit_archive"]["pending_token"] = "incumbent-valid-token"
    old_state.write_text(json.dumps(state), encoding="utf-8")
    old_queue.write_text('{"version": 2, "pending": [], "history": []}', encoding="utf-8")

    old_cfg = {
        "paths": {
            "status_file": str(tmp_path / "status" / "ai-status.json"),
            "state_file": str(old_state),
            "approval_queue": str(old_queue),
        }
    }
    new_cfg = {
        "paths": dict(
            old_cfg["paths"],
            state_file=str(orch / "worker-runtime" / "state.json"),
            approval_queue=str(orch / "worker-runtime" / "approval-queue.json"),
        )
    }

    def fail_fifo(*a, **k):
        raise OSError(errno.ENOSPC, "fifo fail")

    real_mkdir = Path.mkdir
    def fail_mkdir(self, *a, **k):
        if self in (old_state, old_queue):
            raise OSError(errno.ENOSPC, "mkdir fail")
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(os, "mkfifo", fail_fifo)
    monkeypatch.setattr(Path, "mkdir", fail_mkdir)

    with pytest.raises(promotion.StorageMigrationError):
        promotion._migrate_storage_paths(old_cfg, new_cfg)

    # Immutable writer with incumbent config still successfully reads/updates restored incumbent state
    with runtime_state.runtime_state_update(old_cfg) as s:
        assert s["auto_commit_archive"]["pending_token"] == "incumbent-valid-token"
        s["auto_commit_archive"]["pending_token"] = "incumbent-updated-token"

    updated = json.loads(old_state.read_text(encoding="utf-8"))
    assert updated["auto_commit_archive"]["pending_token"] == "incumbent-updated-token"


def test_drain_does_not_signal_terminal_or_completed_worker_pids(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "workers": {
            "run-1": {"status": "completed", "pid": 424242},
            "run-2": {"status": "failed", "pid": 424243},
            "run-3": {"status": "cancelled", "pid": 424244},
            "run-4": {"status": "superseded", "pid": 424245},
        }
    }))
    sent = []
    with mock.patch.object(promotion, "_pid_alive", return_value=True), \
         mock.patch.object(promotion.os, "kill", side_effect=lambda pid, sig: sent.append((pid, sig))):
        result = promotion.qualify_and_drain_incumbent_writers({"paths": {"state_file": str(state)}})
    assert sent == [], f"signalled terminal worker PIDs: {sent}"
    assert result["drained"] is True
    assert result["workers_drained"] == []


def test_drain_fails_closed_on_unverified_or_reused_active_worker_pid(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "workers": {
            "active-run": {
                "status": "running",
                "pid": 55555,
                "pid_start_ticks": 12345,
                "process_generation": "forged_generation",
                "task_id": "TASK-1",
                "queue_event_id": "Q-1",
            }
        }
    }))
    sent = []
    with mock.patch.object(promotion, "_pid_alive", return_value=True), \
         mock.patch.object(promotion, "_worker_pid_start_ticks", return_value=99999), \
         mock.patch.object(promotion.os, "kill", side_effect=lambda pid, sig: sent.append((pid, sig))):
        with pytest.raises(RuntimeError, match="unknown or reused process identity"):
            promotion.qualify_and_drain_incumbent_writers({"paths": {"state_file": str(state)}})
    assert sent == [], f"signalled unverified active worker PID: {sent}"


def _seed_bound_drain_worker(tmp_path):
    import common
    rs = promotion.runtime_state
    config = {"paths": {"state_file": str(tmp_path / "state.json")}}
    state = rs.default_state()
    worker = {"run_id": "run-1", "status": "running", "pid": 77777,
              "pid_start_ticks": 33333, "task_id": "TASK-1", "task_generation": 1,
              "queue_event_id": "Q-1", "lease_acquired_at": "now",
              "runner_status_path": str(tmp_path / "terminal.json"),
              "status_command_runtime": {"command_root": "/old", "source_sha": "a" * 40}}
    worker["process_generation"] = common.worker_process_generation_id(
        task_id="TASK-1", worker_run_id="run-1", queue_event_id="Q-1", pid=77777, pid_start_ticks=33333)
    state["workers"]["run-1"] = worker
    state["queue"]["events"]["Q-1"] = {"intent": {"event_id": "Q-1"}, "status": "started", "run_id": "run-1"}
    rs.begin_promotion(state, worker["status_command_runtime"], {"root": "/new", "head": "b" * 40})
    rs.save_runtime_state(config, state)
    promotion.write_json_atomic(Path(worker["runner_status_path"]), {
        "run_id": worker["run_id"], "pid": worker["pid"], "status": "running",
        "status_command_runtime": worker["status_command_runtime"],
    })
    return config, worker


@pytest.mark.parametrize("terminal_signal", [15, 9, None])
def test_drain_requires_bound_terminal_receipt_after_signal(tmp_path, terminal_signal):
    config, worker = _seed_bound_drain_worker(tmp_path)
    sent = []
    def signal_worker(pid, sig):
        state = promotion.runtime_state.load_runtime_state(config)
        receipt = state["promotion"]["receipts"]["run-1"]
        assert receipt["status"] == "prepared"  # fsynced before the signal
        sent.append((pid, sig))
        promotion.write_json_atomic(Path(worker["runner_status_path"]), {
            "run_id": "run-1", "pid": pid, "signal": terminal_signal,
            "exit_code": 143, "finished_at": "later",
            "status_command_runtime": worker["status_command_runtime"],
            "promotion_drain_digest": receipt["digest"],
        })
    alive = [True, True, False]
    with mock.patch.object(promotion, "_pid_alive", side_effect=lambda pid: alive.pop(0) if alive else False), mock.patch.object(promotion, "_worker_pid_start_ticks", return_value=33333), mock.patch.object(promotion.os, "kill", side_effect=signal_worker):
        if terminal_signal == 15:
            result = promotion.qualify_and_drain_incumbent_writers(config)
            assert result["drained_run_ids"] == ["run-1"]
            assert result["workers_drained"] == [77777]
        else:
            with pytest.raises(RuntimeError, match="matching planned SIGTERM"):
                promotion.qualify_and_drain_incumbent_writers(config)
            state = promotion.runtime_state.load_runtime_state(config)
            assert state["promotion"]["receipts"]["run-1"]["status"] == "prepared"
    assert sent == [(77777, signal.SIGTERM)]


def test_drain_rejects_inflight_event_without_drained_worker(tmp_path):
    config, worker = _seed_bound_drain_worker(tmp_path)
    with promotion.runtime_state.runtime_state_update(config) as state:
        state["workers"]["run-1"]["status"] = "completed"
    with pytest.raises(RuntimeError, match="in-flight queue events"):
        promotion.qualify_and_drain_incumbent_writers(config)


def test_drain_waits_for_active_task_state_store_lock_writer(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    event_log = runtime / "events.jsonl"
    event_log.touch()
    lock_file = runtime / "events.jsonl.lock"
    lock_file.touch()

    incumbent = {"task_state_store": {"mode": "authoritative", "event_log": str(event_log)}}

    result = promotion.qualify_and_drain_incumbent_writers(incumbent)
    assert result["drained"] is True


def test_current_writer_cannot_recreate_migrated_task_state(tmp_path: Path) -> None:
    from rewrite import task_state_store
    status = tmp_path / "status"
    status.mkdir()
    old_log = tmp_path / "runtime" / "events.jsonl"
    old_log.parent.mkdir()
    new_log = old_log.parent / "task-state" / old_log.name
    old_cfg = {
        "paths": {"status_file": str(status / "ai-status.json")},
        "task_state_store": {"mode": "authoritative", "event_log": str(old_log)},
    }
    new_cfg = {
        "paths": old_cfg["paths"],
        "task_state_store": {"mode": "authoritative", "event_log": str(new_log)},
    }
    task_state_store.append_state_commit(old_log, {"tasks": []}, source="isolated-test-seed")
    promotion._migrate_storage_paths(old_cfg, new_cfg)
    before = new_log.read_bytes()

    program = '''import json,sys
sys.path.insert(0, sys.argv[1])
import common
common.write_status(json.loads(sys.argv[2]), {"tasks": [], "marker": "retained-writer"}, source="isolated-test")
'''
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PANTHEON_", "AI_"))}
    result = subprocess.run(
        [sys.executable, "-c", program, str(Path(__file__).resolve().parents[1] / ".orchestrator"), json.dumps(old_cfg)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert new_log.read_bytes() == before
    assert result.returncode != 0
    assert not old_log.exists()


def test_health_requires_exact_pid_runtime_and_fresh_canonical_readback(tmp_path, monkeypatch):
    from copy import deepcopy
    rs = promotion.runtime_state
    config = {"paths": {"state_file": str(tmp_path / "state.json")}}
    state = rs.default_state()
    old = {"root": "/old", "head": "a" * 40}
    candidate = {"root": "/new", "head": "b" * 40}
    rs.begin_promotion(state, old, candidate)
    state["promotion"]["started_at"] = "2026-01-01T00:00:00Z"
    state["supervisor"].update({
        "pid": 123, "lifecycle": "running",
        "command_runtime_health": {"healthy": True, "runtime": rs.promotion_runtime(candidate),
                                   "checked_at": "2026-01-02T00:00:00Z"},
        "task_state_projection": {"ok": True, "caught_up": True, "last_checked_at": "2026-01-02T00:00:00Z"},
    })
    monkeypatch.setattr(promotion, "_pid_alive", lambda pid: True)
    rs.save_runtime_state(config, state)
    assert _REAL_VERIFY_PROMOTION_HEALTH(config, candidate, 123, timeout_seconds=.1)["pid"] == 123
    for field, key, value in (
        ("command_runtime_health", "healthy", False),
        ("command_runtime_health", "runtime", rs.promotion_runtime(old)),
        ("command_runtime_health", "checked_at", "2025-01-01T00:00:00Z"),
        ("task_state_projection", "ok", False),
        ("task_state_projection", "caught_up", False),
        ("task_state_projection", "last_checked_at", "2025-01-01T00:00:00Z"),
    ):
        changed = deepcopy(state)
        changed["supervisor"][field][key] = value
        rs.save_runtime_state(config, changed)
        with pytest.raises(RuntimeError, match="timed out"):
            _REAL_VERIFY_PROMOTION_HEALTH(config, candidate, 123, timeout_seconds=.01)
    rs.save_runtime_state(config, state)
    with pytest.raises(RuntimeError, match="timed out"):
        _REAL_VERIFY_PROMOTION_HEALTH(config, candidate, 456, timeout_seconds=.01)


@pytest.mark.parametrize("stop_fails", [False, True])
def test_failed_candidate_health_stops_before_rollback_and_restores_fence(tmp_path, monkeypatch, stop_fails):
    def unexpected_migration(*args, **kwargs):
        pytest.fail("ordinary source update entered storage migration")
    monkeypatch.setattr(promotion, "_migrate_storage_paths", unexpected_migration)
    candidate, status_root = _candidate(tmp_path)
    live = tmp_path / "runtime/live.json"
    incumbent, identity = promotion.render_v2_config(candidate, status_root=status_root,
        live_config_path=live, python_executable=Path(sys.executable))
    old_identity = {**identity, "head": "a" * 40}
    promotion.write_json_atomic(live, incumbent)
    monkeypatch.setattr(promotion, "qualify_incumbent_identity", lambda *a, **k: old_identity)
    events = []
    monkeypatch.setattr(promotion, "stop_existing_supervisor", lambda *a, **k: events.append("stop-old") or 41)
    def launch(config, *, identity, **kwargs):
        old = identity == old_identity
        events.append("restart-old" if old else "launch-new")
        return 43 if old else 42
    monkeypatch.setattr(promotion, "launch_v2_supervisor", launch)
    def health(config, identity, pid, **kwargs):
        assert kwargs["timeout_seconds"] == 123
        events.append("health-old" if pid == 43 else "health-new")
        if pid == 42:
            raise RuntimeError("candidate canonical readback failed")
        return {"pid": pid, "verified": True}
    monkeypatch.setattr(promotion, "verify_promotion_health", health)
    def stop_candidate(pid, *, timeout_seconds):
        assert timeout_seconds == 1
        events.append("stop-new")
        if stop_fails:
            raise RuntimeError("candidate stop timed out")
    monkeypatch.setattr(promotion, "stop_unaccepted_candidate", stop_candidate)
    result = promotion.replace_supervisor(candidate, status_root=status_root, live_config_path=live,
        python_executable=Path(sys.executable), termination_timeout=1, health_timeout=123)
    assert result["outcome"] == "failed"
    assert "canonical readback failed" in result["error"]
    assert result["launch_error"] == "RuntimeError: candidate canonical readback failed"
    assert result["health_timeout_seconds"] == 123
    assert result["termination_timeout_seconds"] == 1
    if stop_fails:
        assert events == ["stop-old", "launch-new", "health-new", "stop-new"]
        assert result["rollback_stop_error"] == "RuntimeError: candidate stop timed out"
        assert "candidate stop timed out" in result["error"]
        assert promotion.runtime_state.load_runtime_state(incumbent)["promotion"]["phase"] == "verifying"
        return
    assert events == ["stop-old", "launch-new", "health-new", "stop-new", "restart-old", "health-old"]
    state = promotion.runtime_state.load_runtime_state(incumbent)
    assert state["promotion"]["phase"] == "rolled_back"
    assert promotion.runtime_state.promotion_launch_allowed(state, old_identity)
    assert not promotion.runtime_state.promotion_launch_allowed(state, identity)
    assert json.loads(live.read_text()) == incumbent


@pytest.mark.parametrize("section,key", [
    ("task_state_store", "event_log"), ("paths", "state_file"),
    ("paths", "approval_queue"),
])
def test_ordinary_promotion_rejects_data_movement_before_stopping(tmp_path, monkeypatch, section, key):
    candidate, status_root = _candidate(tmp_path)
    live = tmp_path / "runtime/live.json"
    incumbent, _ = promotion.render_v2_config(candidate, status_root=status_root,
        live_config_path=live, python_executable=Path(sys.executable))
    incumbent[section][key] = str(tmp_path / "existing-data" / key)
    promotion.write_json_atomic(live, incumbent)
    with mock.patch.object(promotion, "stop_existing_supervisor") as stop:
        with pytest.raises(ValueError, match="explicitly select --migrate-storage"):
            promotion.replace_supervisor(candidate, status_root=status_root,
                live_config_path=live, python_executable=Path(sys.executable),
                termination_timeout=1)
    stop.assert_not_called()
    assert json.loads(live.read_text()) == incumbent


def test_storage_migration_is_explicit_cli_selection():
    assert not promotion.parse_args(["--status-root", "/tmp/status", "--promote"]).migrate_storage
    assert promotion.parse_args(["--status-root", "/tmp/status", "--promote", "--migrate-storage"]).migrate_storage


def test_health_and_termination_cli_budgets_are_independent():
    basic = ["--status-root", "/tmp/status", "--promote"]
    defaults = promotion.parse_args(basic)
    assert defaults.termination_timeout == 15
    assert defaults.health_timeout == promotion.DEFAULT_HEALTH_TIMEOUT_SECONDS == 600
    explicit = promotion.parse_args(basic + ["--health-timeout", "321", "--termination-timeout", "2"])
    assert explicit.health_timeout == 321
    assert explicit.termination_timeout == 2


def test_main_forwards_explicit_health_budget(tmp_path, monkeypatch):
    candidate, status_root = _candidate(tmp_path)
    with mock.patch.object(promotion, "replace_supervisor", return_value={
        "outcome": "launched", "exit_code": 0,
    }) as replace:
        assert promotion.main([
            "--repo", str(candidate), "--status-root", str(status_root),
            "--live-config", str(tmp_path / "live.json"), "--promote", "--json",
            "--python", sys.executable, "--health-timeout", "321", "--termination-timeout", "2",
        ]) == 0
    assert replace.call_args.kwargs["health_timeout"] == 321
    assert replace.call_args.kwargs["termination_timeout"] == 2


@pytest.mark.parametrize("budget", [0, -1, float("inf"), float("nan")])
def test_invalid_health_budget_fails_before_render_or_stop(tmp_path, monkeypatch, budget):
    with mock.patch.object(promotion, "render_v2_config") as render, mock.patch.object(
        promotion, "stop_existing_supervisor"
    ) as stop:
        with pytest.raises(ValueError, match="health timeout must be finite and positive"):
            promotion.replace_supervisor(tmp_path, status_root=tmp_path,
                live_config_path=tmp_path / "live.json", python_executable=Path(sys.executable),
                termination_timeout=1, health_timeout=budget)
    render.assert_not_called()
    stop.assert_not_called()


def test_pid_alive_reaps_an_actual_exited_direct_child():
    child = subprocess.Popen([sys.executable, "-B", "-c", "pass"])
    try:
        # Observe an unreaped child without Popen.poll()/wait() consuming it.
        deadline = time.monotonic() + 5
        while True:
            raw = Path(f"/proc/{child.pid}/stat").read_text()
            if raw[raw.rfind(")") + 2 :].split()[0] == "Z":
                break
            assert time.monotonic() < deadline, "fixture child did not exit"
            time.sleep(.01)
        os.kill(child.pid, 0)  # This succeeds for a zombie, the original defect.
        assert not promotion._pid_alive(child.pid)
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
    finally:
        child.wait(timeout=5)


@pytest.mark.parametrize("process_state", ["Z", "X"])
def test_pid_alive_rejects_terminal_nonchild(process_state, monkeypatch):
    with mock.patch.object(promotion.os, "waitpid", side_effect=ChildProcessError), mock.patch.object(
        promotion.os, "kill"
    ), mock.patch.object(promotion.Path, "read_text", return_value=f"123 (child) {process_state} 1"):
        assert not promotion._pid_alive(123)


def test_stop_unaccepted_candidate_reaps_actual_sigterm_child(tmp_path):
    script = tmp_path / "supervisor.py"
    script.write_text("import time\ntime.sleep(60)\n")
    child = subprocess.Popen([sys.executable, "-B", str(script)])
    try:
        assert promotion._pid_alive(child.pid)
        promotion.stop_unaccepted_candidate(child.pid, timeout_seconds=2)
        assert not promotion._pid_alive(child.pid)
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_health_timeout_remains_bounded_with_missing_projection(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(promotion.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(promotion.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(promotion, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(promotion.runtime_state, "load_runtime_state", lambda config: {})
    with pytest.raises(RuntimeError, match="health/canonical readback timed out"):
        _REAL_VERIFY_PROMOTION_HEALTH({}, {"root": "/candidate", "head": "a" * 40}, 123,
            timeout_seconds=.25)
    assert clock[0] == .25


def test_stop_failure_restores_prior_admission_without_signalling_workers(tmp_path, monkeypatch):
    candidate, status_root = _candidate(tmp_path)
    live = tmp_path / "runtime/live.json"
    config, identity = promotion.render_v2_config(candidate, status_root=status_root,
        live_config_path=live, python_executable=Path(sys.executable))
    promotion.write_json_atomic(live, config)
    monkeypatch.setattr(promotion, "qualify_incumbent_identity", lambda *a, **k: identity)
    def stop(*a, **k):
        assert promotion.runtime_state.load_runtime_state(config)["promotion"]["phase"] == "draining"
        raise RuntimeError("stop timed out")
    monkeypatch.setattr(promotion, "stop_existing_supervisor", stop)
    with mock.patch.object(promotion, "qualify_and_drain_incumbent_writers") as drain:
        result = promotion.replace_supervisor(candidate, status_root=status_root, live_config_path=live,
            python_executable=Path(sys.executable), termination_timeout=1)
    assert result["outcome"] == "failed"
    drain.assert_not_called()
    assert promotion.runtime_state.promotion_launch_allowed(promotion.runtime_state.load_runtime_state(config), identity)


def test_first_activation_requires_quiescent_legacy_workers_and_reservations(tmp_path):
    config, worker = _seed_bound_drain_worker(tmp_path)
    root = tmp_path / "legacy"
    (root / ".orchestrator").mkdir(parents=True)
    (root / ".orchestrator/worker_runner.py").write_text("# legacy runner\n")
    (root / ".orchestrator/supervisor.py").write_text("# legacy supervisor\n")
    identity = {"root": str(root), "head": "a" * 40}
    with pytest.raises(RuntimeError, match="lack promotion drain capability"):
        _REAL_VERIFY_DRAIN_CAPABILITY(config, identity)
    with promotion.runtime_state.runtime_state_update(config) as state:
        state["workers"] = {}
        state["supervisor"]["runtime_phase_reservations"] = {"process_queue": {"token": "reserved"}}
    with pytest.raises(RuntimeError, match="lack promotion drain capability"):
        _REAL_VERIFY_DRAIN_CAPABILITY(config, identity)
    with promotion.runtime_state.runtime_state_update(config) as state:
        state["supervisor"]["runtime_phase_reservations"] = {}
    _REAL_VERIFY_DRAIN_CAPABILITY(config, identity)
