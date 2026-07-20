from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync-dev-root.sh"


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_sync_updates_origin_dev_when_remote_fetch_config_tracks_only_master(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    dev_root = tmp_path / "dev-root"
    remote.mkdir()
    seed.mkdir()
    _git(remote, "init", "--bare")
    _git(seed, "init", "-b", "dev")
    _git(seed, "config", "user.email", "test@example.invalid")
    _git(seed, "config", "user.name", "Pantheon Test")
    (seed / "version.txt").write_text("one\n", encoding="utf-8")
    _git(seed, "add", "version.txt")
    _git(seed, "commit", "-m", "first")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-u", "origin", "dev")

    _git(tmp_path, "clone", "--branch", "dev", str(remote), str(dev_root))
    first = _git(dev_root, "rev-parse", "HEAD")
    # Match the live dev-root topology: its configured fetch refspec does not
    # include dev, even though a stale origin/dev ref already exists.
    _git(
        dev_root,
        "config",
        "remote.origin.fetch",
        "+refs/heads/master:refs/remotes/origin/master",
    )

    (seed / "version.txt").write_text("two\n", encoding="utf-8")
    _git(seed, "add", "version.txt")
    _git(seed, "commit", "-m", "second")
    second = _git(seed, "rev-parse", "HEAD")
    _git(seed, "push", "origin", "dev")
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
    assert (dev_root / "version.txt").read_text(encoding="utf-8") == "two\n"


def test_sync_reprovisions_stale_split_root_account_config(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    dev_root = tmp_path / "dev-root"
    status_root = tmp_path / "canonical-root"
    live_config = tmp_path / "runtime" / "live.json"
    remote.mkdir()
    seed.mkdir()
    _git(remote, "init", "--bare")
    _git(seed, "init", "-b", "dev")
    _git(seed, "config", "user.email", "test@example.invalid")
    _git(seed, "config", "user.name", "Pantheon Test")
    (seed / "version.txt").write_text("one\n", encoding="utf-8")
    _git(seed, "add", "version.txt")
    _git(seed, "commit", "-m", "first")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-u", "origin", "dev")
    _git(tmp_path, "clone", "--branch", "dev", str(remote), str(dev_root))

    (dev_root / ".orchestrator").mkdir()
    (dev_root / "scripts").mkdir()
    (dev_root / ".orchestrator" / "supervisor.py").write_text("", encoding="utf-8")
    (dev_root / "scripts" / "run-supervisor-watchdog.sh").write_text("", encoding="utf-8")
    shutil.copy2(
        REPO_ROOT / "scripts" / "provision_live_supervisor_config.py",
        dev_root / "scripts" / "provision_live_supervisor_config.py",
    )
    (dev_root / ".orchestrator" / "config.json").write_text(
        json.dumps(
            {
                "paths": {
                    "status_file": "ai-status.json",
                    "state_file": ".orchestrator/state.json",
                    "approval_queue": ".orchestrator/approval-queue.json",
                },
                "ready_dispatcher": {
                    "require_explicit_provider_accounts": True,
                    "allow_legacy_provider_account_aliases": False,
                    "max_concurrent_per_account": {"shared": 1},
                },
                "providers": {"claude": {"account": "shared"}},
                "watchdog": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (status_root / ".git").mkdir(parents=True)
    (status_root / ".orchestrator").mkdir()
    (status_root / "ai-status.json").write_text('{"tasks":[]}\n', encoding="utf-8")
    live_config.parent.mkdir()
    live_config.write_text(
        json.dumps(
            {
                "paths": {"status_file": "/stale/ai-status.json"},
                "ready_dispatcher": {
                    "max_concurrent_per_quota_group": {"claude": 1}
                },
                "providers": {"claude": {"account_group": "shared"}},
                "watchdog": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PANTHEON_STATUS_ROOT"] = str(status_root)
    env["PANTHEON_SUPERVISOR_PID"] = str(tmp_path / "no-supervisor.pid")
    result = subprocess.run(
        ["bash", str(SYNC_SCRIPT), str(dev_root), str(live_config)],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    rendered = json.loads(live_config.read_text(encoding="utf-8"))
    assert "updated split-root live supervisor config" in result.stdout
    assert rendered["ready_dispatcher"]["max_concurrent_per_account"] == {"shared": 1}
    assert "max_concurrent_per_quota_group" not in rendered["ready_dispatcher"]
    assert rendered["providers"]["claude"]["account"] == "shared"
    assert "account_group" not in rendered["providers"]["claude"]
