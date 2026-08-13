from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync-dev-root.sh"
CANONICAL_RUNTIME_PARENT = "/home/lupin/pantheon-ci-deploy/command-runtimes"


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _seed_remote(tmp_path: Path) -> tuple[Path, Path, str]:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    remote.mkdir()
    seed.mkdir()
    _git(remote, "init", "--bare")
    _git(seed, "init", "-b", "dev")
    _git(seed, "config", "user.email", "test@example.invalid")
    _git(seed, "config", "user.name", "Pantheon Test")
    (seed / "version.txt").write_text("one\n", encoding="utf-8")
    _git(seed, "add", "version.txt")
    _git(seed, "commit", "-m", "first")
    first = _git(seed, "rev-parse", "HEAD")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-u", "origin", "dev")
    return remote, seed, first


def _install_runtime_stubs(seed: Path) -> str:
    (seed / ".orchestrator").mkdir(exist_ok=True)
    (seed / "scripts").mkdir(exist_ok=True)
    (seed / ".orchestrator" / "supervisor.py").write_text("", encoding="utf-8")
    (seed / "scripts" / "provision_live_supervisor_config.py").write_text(
        "import sys\nsys.exit(0)\n",
        encoding="utf-8",
    )
    promotion = seed / "scripts" / "promote-supervisor-runtime.sh"
    promotion.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" >\"$SYNC_PROMOTION_ARGS_FILE\"\n"
        "exit \"${SYNC_PROMOTION_EXIT:-0}\"\n",
        encoding="utf-8",
    )
    promotion.chmod(0o755)
    _git(seed, "add", ".orchestrator/supervisor.py", "scripts")
    _git(seed, "commit", "-m", "add runtime handoff stubs")
    _git(seed, "push", "origin", "dev")
    return _git(seed, "rev-parse", "HEAD")


def _advance(seed: Path, value: str) -> str:
    (seed / "version.txt").write_text(f"{value}\n", encoding="utf-8")
    _git(seed, "add", "version.txt")
    _git(seed, "commit", "-m", f"advance {value}")
    _git(seed, "push", "origin", "dev")
    return _git(seed, "rev-parse", "HEAD")


def _patched_sync_script(tmp_path: Path, runtime_parent: Path) -> Path:
    script = tmp_path / "sync-dev-root-under-test.sh"
    source = SYNC_SCRIPT.read_text(encoding="utf-8").replace(
        f'COMMAND_RUNTIME_PARENT="{CANONICAL_RUNTIME_PARENT}"',
        f'COMMAND_RUNTIME_PARENT="{runtime_parent}"',
    )
    assert str(runtime_parent) in source
    script.write_text(source, encoding="utf-8")
    script.chmod(0o755)
    return script


def _run_sync(
    script: Path,
    dev_root: Path,
    live_config: Path,
    *,
    pid_file: Path,
    promotion_args: Path,
    promotion_exit: int = 0,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PANTHEON_SUPERVISOR_PID"] = str(pid_file)
    env["SYNC_PROMOTION_ARGS_FILE"] = str(promotion_args)
    env["SYNC_PROMOTION_EXIT"] = str(promotion_exit)
    return subprocess.run(
        ["bash", str(script), str(dev_root), str(live_config)],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_sync_updates_origin_dev_when_remote_fetch_config_tracks_only_master(
    tmp_path: Path,
) -> None:
    remote, seed, first = _seed_remote(tmp_path)
    dev_root = tmp_path / "dev-root"
    _git(tmp_path, "clone", "--branch", "dev", str(remote), str(dev_root))
    _git(
        dev_root,
        "config",
        "remote.origin.fetch",
        "+refs/heads/master:refs/remotes/origin/master",
    )
    second = _advance(seed, "two")
    assert _git(dev_root, "rev-parse", "origin/dev") == first

    env = os.environ.copy()
    env["SYNC_REF"] = "origin/dev"
    env["PANTHEON_SUPERVISOR_PID"] = str(tmp_path / "no-supervisor.pid")
    result = subprocess.run(
        ["bash", str(SYNC_SCRIPT), str(dev_root), str(tmp_path / "no-live-config.json")],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "behind origin/dev by 1" in result.stdout
    assert _git(dev_root, "rev-parse", "origin/dev") == second
    assert _git(dev_root, "rev-parse", "HEAD") == second


def test_sync_never_preprovisions_existing_config_without_incumbent(tmp_path: Path) -> None:
    remote, _seed, _first = _seed_remote(tmp_path)
    dev_root = tmp_path / "dev-root"
    live_config = tmp_path / "runtime" / "live.json"
    live_config.parent.mkdir()
    live_config.write_text('{"watchdog":{"supervisor_command":["preserve"]}}\n', encoding="utf-8")
    before = live_config.read_bytes()
    _git(tmp_path, "clone", "--branch", "dev", str(remote), str(dev_root))

    env = os.environ.copy()
    env["PANTHEON_SUPERVISOR_PID"] = str(tmp_path / "no-supervisor.pid")
    result = subprocess.run(
        ["bash", str(SYNC_SCRIPT), str(dev_root), str(live_config)],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "promotion=no-incumbent" in result.stdout
    assert live_config.read_bytes() == before


def test_current_mutable_root_is_not_reset_or_signalled_before_bootstrap_handoff(
    tmp_path: Path,
) -> None:
    remote, seed, _first = _seed_remote(tmp_path)
    incumbent = _install_runtime_stubs(seed)
    dev_root = tmp_path / "dev-root"
    runtime_parent = tmp_path / "command-runtimes"
    script = _patched_sync_script(tmp_path, runtime_parent)
    _git(tmp_path, "clone", "--branch", "dev", str(remote), str(dev_root))
    target = _advance(seed, "candidate")
    pid_file = tmp_path / "supervisor.pid"
    promotion_args = tmp_path / "promotion-args.txt"
    process = subprocess.Popen(["sleep", "60"], cwd=dev_root)
    pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    try:
        result = _run_sync(
            script,
            dev_root,
            tmp_path / "live.json",
            pid_file=pid_file,
            promotion_args=promotion_args,
        )
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)

    candidate = runtime_parent / target
    assert result.returncode == 0, result.stderr
    assert "ACTIVE_MUTABLE_ROOT_PROTECTED" in result.stdout
    assert _git(dev_root, "rev-parse", "HEAD") == incumbent
    assert _git(candidate, "rev-parse", "HEAD") == target
    assert promotion_args.read_text(encoding="utf-8").splitlines() == [
        "--promote",
        "--repo",
        str(candidate),
    ]


def test_split_immutable_root_is_untouched_and_uses_normal_promotion_handoff(
    tmp_path: Path,
) -> None:
    remote, seed, _first = _seed_remote(tmp_path)
    incumbent = _install_runtime_stubs(seed)
    dev_root = tmp_path / "dev-root"
    runtime_parent = tmp_path / "command-runtimes"
    active_root = runtime_parent / incumbent
    runtime_parent.mkdir()
    _git(tmp_path, "clone", "--branch", "dev", str(remote), str(dev_root))
    _git(runtime_parent, "clone", "--branch", "dev", str(remote), str(active_root))
    target = _advance(seed, "next")
    script = _patched_sync_script(tmp_path, runtime_parent)
    pid_file = tmp_path / "supervisor.pid"
    promotion_args = tmp_path / "promotion-args.txt"
    process = subprocess.Popen(["sleep", "60"], cwd=active_root)
    pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    try:
        result = _run_sync(
            script,
            dev_root,
            tmp_path / "live.json",
            pid_file=pid_file,
            promotion_args=promotion_args,
        )
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)

    candidate = runtime_parent / target
    assert result.returncode == 0, result.stderr
    assert "ACTIVE_ROOT_SPLIT_PROTECTED" in result.stdout
    assert _git(active_root, "rev-parse", "HEAD") == incumbent
    assert _git(candidate, "rev-parse", "HEAD") == target
    assert promotion_args.read_text(encoding="utf-8").splitlines() == [
        "--promote",
        "--repo",
        str(candidate),
    ]


def test_current_immutable_root_is_a_true_noop(tmp_path: Path) -> None:
    remote, seed, _first = _seed_remote(tmp_path)
    current = _install_runtime_stubs(seed)
    dev_root = tmp_path / "dev-root"
    runtime_parent = tmp_path / "command-runtimes"
    active_root = runtime_parent / current
    runtime_parent.mkdir()
    _git(tmp_path, "clone", "--branch", "dev", str(remote), str(dev_root))
    _git(runtime_parent, "clone", "--branch", "dev", str(remote), str(active_root))
    script = _patched_sync_script(tmp_path, runtime_parent)
    pid_file = tmp_path / "supervisor.pid"
    promotion_args = tmp_path / "promotion-args.txt"
    process = subprocess.Popen(["sleep", "60"], cwd=active_root)
    pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    try:
        result = _run_sync(
            script,
            dev_root,
            tmp_path / "live.json",
            pid_file=pid_file,
            promotion_args=promotion_args,
        )
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)

    assert result.returncode == 0, result.stderr
    assert "promotion=no-op-current-root" in result.stdout
    assert not promotion_args.exists()
    assert _git(active_root, "rev-parse", "HEAD") == current


def test_promotion_handoff_failure_leaves_split_incumbent_alive(tmp_path: Path) -> None:
    remote, seed, _first = _seed_remote(tmp_path)
    incumbent = _install_runtime_stubs(seed)
    dev_root = tmp_path / "dev-root"
    runtime_parent = tmp_path / "command-runtimes"
    active_root = runtime_parent / incumbent
    runtime_parent.mkdir()
    _git(tmp_path, "clone", "--branch", "dev", str(remote), str(dev_root))
    _git(runtime_parent, "clone", "--branch", "dev", str(remote), str(active_root))
    _advance(seed, "rejected")
    script = _patched_sync_script(tmp_path, runtime_parent)
    pid_file = tmp_path / "supervisor.pid"
    promotion_args = tmp_path / "promotion-args.txt"
    process = subprocess.Popen(["sleep", "60"], cwd=active_root)
    pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    try:
        result = _run_sync(
            script,
            dev_root,
            tmp_path / "live.json",
            pid_file=pid_file,
            promotion_args=promotion_args,
            promotion_exit=9,
        )
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)

    assert result.returncode == 1
    assert "promotion handoff failed" in result.stdout
    assert _git(active_root, "rev-parse", "HEAD") == incumbent


def test_config_drift_on_current_runtime_fails_without_mutation_or_handoff(
    tmp_path: Path,
) -> None:
    remote, seed, _first = _seed_remote(tmp_path)
    (seed / "scripts").mkdir(exist_ok=True)
    (seed / "scripts" / "check_config_drift.py").write_text(
        "import json, sys\nprint(json.dumps({'drift':[{'path':'watchdog.enabled'}]}))\nsys.exit(1)\n",
        encoding="utf-8",
    )
    _git(seed, "add", "scripts/check_config_drift.py")
    _git(seed, "commit", "-m", "add drift probe")
    _git(seed, "push", "origin", "dev")
    current = _install_runtime_stubs(seed)
    dev_root = tmp_path / "dev-root"
    runtime_parent = tmp_path / "command-runtimes"
    active_root = runtime_parent / current
    runtime_parent.mkdir()
    _git(tmp_path, "clone", "--branch", "dev", str(remote), str(dev_root))
    _git(runtime_parent, "clone", "--branch", "dev", str(remote), str(active_root))
    live_config = tmp_path / "live.json"
    live_config.write_text(json.dumps({"preserve": True}) + "\n", encoding="utf-8")
    before = live_config.read_bytes()
    script = _patched_sync_script(tmp_path, runtime_parent)
    pid_file = tmp_path / "supervisor.pid"
    promotion_args = tmp_path / "promotion-args.txt"
    process = subprocess.Popen(["sleep", "60"], cwd=active_root)
    pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    try:
        result = _run_sync(
            script,
            dev_root,
            live_config,
            pid_file=pid_file,
            promotion_args=promotion_args,
        )
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)

    assert result.returncode == 1
    assert "CONFIG_DRIFT_REQUIRES_PROMOTION" in result.stdout
    assert live_config.read_bytes() == before
    assert not promotion_args.exists()


def test_sync_cleans_untracked_and_ignored_residue_in_dev_root(
    tmp_path: Path,
) -> None:
    remote, seed, first = _seed_remote(tmp_path)
    dev_root = tmp_path / "dev-root"
    _git(tmp_path, "clone", "--branch", "dev", str(remote), str(dev_root))

    # Add untracked and ignored residue inside dev_root
    stale_dir = dev_root / ".orchestrator" / "task-briefs"
    stale_dir.mkdir(parents=True)
    (stale_dir / "stale_brief.md").write_text("obsolete\n", encoding="utf-8")
    (dev_root / ".orchestrator" / "supervisor.lock").write_text("lock\n", encoding="utf-8")

    second = _advance(seed, "two")

    env = os.environ.copy()
    env["SYNC_REF"] = "origin/dev"
    env["PANTHEON_SUPERVISOR_PID"] = str(tmp_path / "no-supervisor.pid")
    result = subprocess.run(
        ["bash", str(SYNC_SCRIPT), str(dev_root), str(tmp_path / "no-live-config.json")],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert _git(dev_root, "rev-parse", "HEAD") == second
    assert not (stale_dir / "stale_brief.md").exists()
    assert not (dev_root / ".orchestrator" / "supervisor.lock").exists()

