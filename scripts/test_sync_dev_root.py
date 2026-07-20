from __future__ import annotations

import os
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
