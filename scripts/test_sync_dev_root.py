from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync-dev-root.sh"
CANONICAL_RUNTIME_PARENT = "/home/lupin/pantheon-ci-deploy/command-runtimes"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _seed_remote(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    remote.mkdir()
    seed.mkdir()
    _git(remote, "init", "--bare")
    _git(seed, "init", "-b", "dev")
    _git(seed, "config", "user.email", "test@example.invalid")
    _git(seed, "config", "user.name", "Pantheon Test")
    (seed / ".orchestrator").mkdir()
    (seed / "scripts").mkdir()
    (seed / ".orchestrator" / "supervisor.py").write_text("# V2\n", encoding="utf-8")
    (seed / "version.txt").write_text("one\n", encoding="utf-8")
    (seed / "scripts" / "provision_live_supervisor_config.py").write_text(
        "import sys\nsys.exit(0)\n", encoding="utf-8"
    )
    promotion = seed / "scripts" / "promote-supervisor-runtime.sh"
    promotion.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" >\"$SYNC_PROMOTION_ARGS_FILE\"\n",
        encoding="utf-8",
    )
    promotion.chmod(0o755)
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "runtime")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-u", "origin", "dev")
    return remote, seed


def _add_fake_watchdog_installer(seed: Path) -> None:
    installer = seed / "scripts" / "supervisor_watchdog_install.py"
    installer.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "path = os.environ.get('SYNC_WATCHDOG_ARGS_FILE')\n"
        "if path:\n"
        "    with open(path, 'w', encoding='utf-8') as handle:\n"
        "        handle.write('\\n'.join(sys.argv[1:]))\n"
        "sys.exit(int(os.environ.get('SYNC_WATCHDOG_EXIT_CODE', '0')))\n",
        encoding="utf-8",
    )
    installer.chmod(0o755)
    _git(seed, "add", "scripts/supervisor_watchdog_install.py")
    _git(seed, "commit", "-m", "add fake watchdog installer")
    _git(seed, "push", "origin", "dev")


def _advance(seed: Path) -> str:
    (seed / "version.txt").write_text("two\n", encoding="utf-8")
    _git(seed, "add", "version.txt")
    _git(seed, "commit", "-m", "advance")
    _git(seed, "push", "origin", "dev")
    return _git(seed, "rev-parse", "HEAD")


def _coordination_root(tmp_path: Path) -> Path:
    root = tmp_path / "coordination"
    root.mkdir()
    _git(root, "init", "-b", "dev")
    (root / ".orchestrator").mkdir()
    (root / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
    return root


def _patched_sync_script(tmp_path: Path, runtime_parent: Path) -> Path:
    script = tmp_path / "sync-dev-root-under-test.sh"
    source = SYNC_SCRIPT.read_text(encoding="utf-8").replace(
        f'COMMAND_RUNTIME_PARENT="{CANONICAL_RUNTIME_PARENT}"',
        f'COMMAND_RUNTIME_PARENT="{runtime_parent}"',
    )
    script.write_text(source, encoding="utf-8")
    script.chmod(0o755)
    return script


def _run_sync(
    script: Path,
    dev_root: Path,
    live_config: Path,
    coordination_root: Path,
    promotion_args: Path,
    *,
    authority_env_file: Path | None = None,
    watchdog_args_file: Path | None = None,
    watchdog_exit_code: int | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SYNC_PROMOTION_ARGS_FILE"] = str(promotion_args)
    # The fake Pantheon repository is enough to validate the wiring; the
    # production script's default remains the canonical execute-plans checkout.
    env["PANTHEON_EXECUTE_PLANS_SOURCE_ROOT"] = str(dev_root)
    args = ["bash", str(script), str(dev_root), str(live_config), str(coordination_root)]
    if authority_env_file is not None:
        args.append(str(authority_env_file))
    if watchdog_args_file is not None:
        env["SYNC_WATCHDOG_ARGS_FILE"] = str(watchdog_args_file)
    if watchdog_exit_code is not None:
        env["SYNC_WATCHDOG_EXIT_CODE"] = str(watchdog_exit_code)
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_sync_uses_explicit_coordination_root_and_never_inspects_live_cwd(tmp_path: Path) -> None:
    remote, seed = _seed_remote(tmp_path)
    dev_root = tmp_path / "dev-root"
    _git(tmp_path, "clone", "--branch", "dev", str(remote), str(dev_root))
    target = _advance(seed)
    runtime_parent = tmp_path / "command-runtimes"
    script = _patched_sync_script(tmp_path, runtime_parent)
    coordination = _coordination_root(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    promotion_args = tmp_path / "promotion-args.txt"

    result = _run_sync(script, dev_root, live_config, coordination, promotion_args)

    candidate = runtime_parent / target
    assert result.returncode == 0, result.stderr
    assert _git(dev_root, "rev-parse", "HEAD") == target
    assert _git(candidate, "rev-parse", "HEAD") == target
    assert promotion_args.read_text(encoding="utf-8").splitlines() == [
        "--promote",
        "--repo",
        str(candidate),
        "--status-root",
        str(coordination),
        "--live-config",
        str(live_config),
        "--repository-source-root",
        f"pantheon={dev_root}",
        "--repository-source-root",
        f"execute_plans={dev_root}",
    ]
    source = SYNC_SCRIPT.read_text(encoding="utf-8")
    assert "PID_FILE=" not in source
    assert "ACTIVE_ROOT" not in source
    assert "/proc/$pid/cwd" not in source


def test_sync_rejects_staging_as_its_own_coordination_root(tmp_path: Path) -> None:
    remote, _seed = _seed_remote(tmp_path)
    dev_root = tmp_path / "dev-root"
    _git(tmp_path, "clone", "--branch", "dev", str(remote), str(dev_root))
    script = _patched_sync_script(tmp_path, tmp_path / "command-runtimes")

    result = _run_sync(
        script,
        dev_root,
        tmp_path / "runtime" / "live.json",
        dev_root,
        tmp_path / "promotion-args.txt",
    )

    assert result.returncode == 1
    assert "must not also be the coordination root" in result.stdout


def test_sync_is_a_noop_when_installed_config_already_names_exact_candidate(tmp_path: Path) -> None:
    remote, seed = _seed_remote(tmp_path)
    dev_root = tmp_path / "dev-root"
    _git(tmp_path, "clone", "--branch", "dev", str(remote), str(dev_root))
    head = _git(seed, "rev-parse", "HEAD")
    runtime_parent = tmp_path / "command-runtimes"
    runtime_parent.mkdir()
    candidate = runtime_parent / head
    _git(runtime_parent, "clone", str(remote), str(candidate))
    _git(candidate, "checkout", "--detach", head)
    script = _patched_sync_script(tmp_path, runtime_parent)
    coordination = _coordination_root(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    live_config.parent.mkdir()
    live_config.write_text(
        json.dumps(
            {
                "watchdog": {
                    "supervisor_command": [
                        "python3",
                        str(candidate / ".orchestrator" / "supervisor.py"),
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    promotion_args = tmp_path / "promotion-args.txt"

    result = _run_sync(script, dev_root, live_config, coordination, promotion_args)

    assert result.returncode == 0, result.stderr
    assert "promotion=no-op-current-runtime" in result.stdout
    assert not promotion_args.exists()


def test_sync_repoints_the_watchdog_after_a_real_promotion(tmp_path: Path) -> None:
    remote, seed = _seed_remote(tmp_path)
    _add_fake_watchdog_installer(seed)
    dev_root = tmp_path / "dev-root"
    _git(tmp_path, "clone", "--branch", "dev", str(remote), str(dev_root))
    target = _advance(seed)
    runtime_parent = tmp_path / "command-runtimes"
    script = _patched_sync_script(tmp_path, runtime_parent)
    coordination = _coordination_root(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    promotion_args = tmp_path / "promotion-args.txt"
    watchdog_args = tmp_path / "watchdog-args.txt"
    authority_env_file = tmp_path / "authority.env"
    authority_env_file.write_text("# test authority env\n", encoding="utf-8")
    authority_env_file.chmod(0o600)

    result = _run_sync(
        script,
        dev_root,
        live_config,
        coordination,
        promotion_args,
        authority_env_file=authority_env_file,
        watchdog_args_file=watchdog_args,
    )

    candidate = runtime_parent / target
    assert result.returncode == 0, result.stderr
    assert "watchdog repointed" in result.stdout
    assert watchdog_args.read_text(encoding="utf-8").splitlines() == [
        "--repo",
        str(candidate),
        "--config",
        str(live_config),
        "--authority-env-file",
        str(authority_env_file),
        "--method",
        "auto",
        "--start-now",
    ]


def test_sync_skips_watchdog_repoint_without_failing_the_deploy(tmp_path: Path) -> None:
    remote, seed = _seed_remote(tmp_path)
    _add_fake_watchdog_installer(seed)
    dev_root = tmp_path / "dev-root"
    _git(tmp_path, "clone", "--branch", "dev", str(remote), str(dev_root))
    _advance(seed)
    runtime_parent = tmp_path / "command-runtimes"
    script = _patched_sync_script(tmp_path, runtime_parent)
    coordination = _coordination_root(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    promotion_args = tmp_path / "promotion-args.txt"
    watchdog_args = tmp_path / "watchdog-args.txt"
    missing_authority_env_file = tmp_path / "does-not-exist.env"

    result = _run_sync(
        script,
        dev_root,
        live_config,
        coordination,
        promotion_args,
        authority_env_file=missing_authority_env_file,
        watchdog_args_file=watchdog_args,
    )

    assert result.returncode == 0, result.stderr
    assert promotion_args.exists()
    assert "skipped watchdog repoint" in result.stdout
    assert not watchdog_args.exists()


def test_sync_survives_watchdog_repoint_failure(tmp_path: Path) -> None:
    remote, seed = _seed_remote(tmp_path)
    _add_fake_watchdog_installer(seed)
    dev_root = tmp_path / "dev-root"
    _git(tmp_path, "clone", "--branch", "dev", str(remote), str(dev_root))
    _advance(seed)
    runtime_parent = tmp_path / "command-runtimes"
    script = _patched_sync_script(tmp_path, runtime_parent)
    coordination = _coordination_root(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    promotion_args = tmp_path / "promotion-args.txt"
    authority_env_file = tmp_path / "authority.env"
    authority_env_file.write_text("# test authority env\n", encoding="utf-8")
    authority_env_file.chmod(0o600)

    result = _run_sync(
        script,
        dev_root,
        live_config,
        coordination,
        promotion_args,
        authority_env_file=authority_env_file,
        watchdog_exit_code=1,
    )

    assert result.returncode == 0, result.stderr
    assert promotion_args.exists()
    assert "WARNING: watchdog repoint failed" in result.stdout


def test_sync_prunes_old_command_runtimes_after_promotion(tmp_path: Path) -> None:
    remote, seed = _seed_remote(tmp_path)
    dev_root = tmp_path / "dev-root"
    _git(tmp_path, "clone", "--branch", "dev", str(remote), str(dev_root))
    _advance(seed)
    runtime_parent = tmp_path / "command-runtimes"
    script = _patched_sync_script(tmp_path, runtime_parent)
    coordination = _coordination_root(tmp_path)
    live_config = tmp_path / "runtime" / "live.json"
    promotion_args = tmp_path / "promotion-args.txt"
    prune_args = tmp_path / "prune-args.txt"

    prune_script = dev_root / "scripts" / "prune_command_runtimes.py"
    prune_script.write_text(
        "import sys, os\n"
        "with open(os.environ['SYNC_PRUNE_ARGS_FILE'], 'w') as fh:\n"
        "    fh.write('\\n'.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["SYNC_PROMOTION_ARGS_FILE"] = str(promotion_args)
    env["SYNC_PRUNE_ARGS_FILE"] = str(prune_args)
    # execute-plans multi-repo support (SUP-WORKTREE-BASE-SYNC-20260822) requires
    # a real git checkout at this root; the fake Pantheon repository is enough
    # to validate the prune wiring, so reuse it here too.
    env["PANTHEON_EXECUTE_PLANS_SOURCE_ROOT"] = str(dev_root)
    result = subprocess.run(
        ["bash", str(script), str(dev_root), str(live_config), str(coordination)],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert prune_args.read_text(encoding="utf-8").splitlines() == [
        "--parent",
        str(runtime_parent),
        "--live-config",
        str(live_config),
        "--status-root",
        str(coordination),
        "--keep",
        "5",
    ]
