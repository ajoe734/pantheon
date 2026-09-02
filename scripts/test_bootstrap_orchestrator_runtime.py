from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bootstrap-orchestrator-runtime.sh"


def _make_status_root(tmp_path: Path) -> Path:
    status_root = tmp_path / "checkout"
    status_root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=status_root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=status_root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=status_root, check=True)
    (status_root / "ai-status.json").write_text("{}\n", encoding="utf-8")
    (status_root / ".orchestrator").mkdir()
    subprocess.run(["git", "add", "-A"], cwd=status_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=status_root, check=True)
    return status_root


def _stub_bin_dir(tmp_path: Path, *, bwrap_exit: int | None) -> Path:
    """A PATH entry providing a controllable bwrap, ahead of the real one."""
    import uuid

    bin_dir = tmp_path / f"stub-bin-{uuid.uuid4().hex}"
    bin_dir.mkdir()
    if bwrap_exit is not None:
        bwrap = bin_dir / "bwrap"
        bwrap.write_text(f"#!/usr/bin/env bash\nexit {bwrap_exit}\n", encoding="utf-8")
        bwrap.chmod(bwrap.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _run(
    tmp_path: Path,
    *,
    status_root: Path,
    deploy_root: Path,
    args: list[str],
    bwrap_exit: int | None = 0,
    extra_path_dirs: list[Path] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    path_dirs = list(extra_path_dirs or [])
    if bwrap_exit is not None:
        path_dirs.insert(0, str(_stub_bin_dir(tmp_path, bwrap_exit=bwrap_exit)))
    env["PATH"] = os.pathsep.join([*(str(p) for p in path_dirs), env.get("PATH", "")])
    env["PANTHEON_STATUS_ROOT"] = str(status_root)
    env["PANTHEON_DEPLOY_ROOT"] = str(deploy_root)
    env.pop("HOME_OVERRIDDEN", None)
    return subprocess.run(
        [str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_script_syntax() -> None:
    proc = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_sandbox_preflight_fails_before_runtime_sealing_missing_bwrap(tmp_path: Path) -> None:
    status_root = _make_status_root(tmp_path)
    deploy_root = tmp_path / "deploy"

    # No stub bwrap on PATH at all, and hide any real one by using a minimal PATH.
    env = dict(os.environ)
    env["PATH"] = "/usr/bin:/bin"
    env["PANTHEON_STATUS_ROOT"] = str(status_root)
    env["PANTHEON_DEPLOY_ROOT"] = str(deploy_root)
    # If bwrap genuinely exists on /usr/bin or /bin on the test host, this
    # assertion is skipped in favor of the explicit-failure variant below.
    which = subprocess.run(["bash", "-lc", "command -v bwrap"], env=env, capture_output=True, text=True)
    if which.returncode == 0:
        pytest.skip("bwrap present on minimal PATH; covered by explicit-failure variant")

    proc = subprocess.run([str(SCRIPT)], capture_output=True, text=True, env=env, check=False)

    assert proc.returncode == 1
    assert "bubblewrap is required" in proc.stdout + proc.stderr
    assert not deploy_root.exists(), "preflight failure must not create the deployment layout"


def test_sandbox_preflight_fails_when_bwrap_cannot_create_userns(tmp_path: Path) -> None:
    status_root = _make_status_root(tmp_path)
    deploy_root = tmp_path / "deploy"

    proc = _run(tmp_path, status_root=status_root, deploy_root=deploy_root, args=[], bwrap_exit=1)

    assert proc.returncode == 1
    assert "cannot create a user namespace" in proc.stdout + proc.stderr
    assert not deploy_root.exists(), "preflight failure must not create the deployment layout"


def test_rejects_status_root_without_pantheon_markers(tmp_path: Path) -> None:
    status_root = tmp_path / "not-a-checkout"
    status_root.mkdir()
    deploy_root = tmp_path / "deploy"

    proc = _run(tmp_path, status_root=status_root, deploy_root=deploy_root, args=[])

    assert proc.returncode == 1
    assert "not a Pantheon checkout" in proc.stdout + proc.stderr
    assert not deploy_root.exists()


def test_rejects_dirty_status_root(tmp_path: Path) -> None:
    status_root = _make_status_root(tmp_path)
    (status_root / "ai-status.json").write_text('{"dirty": true}\n', encoding="utf-8")
    deploy_root = tmp_path / "deploy"

    proc = _run(tmp_path, status_root=status_root, deploy_root=deploy_root, args=[])

    assert proc.returncode == 1
    assert "uncommitted changes" in proc.stdout + proc.stderr
    assert not deploy_root.exists()


def test_dry_run_has_no_writes(tmp_path: Path) -> None:
    status_root = _make_status_root(tmp_path)
    deploy_root = tmp_path / "deploy"

    proc = _run(
        tmp_path, status_root=status_root, deploy_root=deploy_root, args=["--dry-run"], bwrap_exit=0
    )

    assert proc.returncode == 0, proc.stderr
    assert "would run" in proc.stdout
    assert not deploy_root.exists(), "dry-run must not create the deployment layout"
    assert not any(status_root.glob(".git/worktrees/*")), "dry-run must not add a worktree"


def test_dry_run_honors_custom_deploy_root(tmp_path: Path) -> None:
    status_root = _make_status_root(tmp_path)
    deploy_root = tmp_path / "custom-deploy-location"

    proc = _run(
        tmp_path, status_root=status_root, deploy_root=deploy_root, args=["--dry-run"], bwrap_exit=0
    )

    assert proc.returncode == 0, proc.stderr
    assert f"deployment root: {deploy_root}" in proc.stdout


def test_dry_run_is_idempotent(tmp_path: Path) -> None:
    """Running --dry-run twice must behave identically: still no writes, still
    a full plan, since dry-run never mutates the state that later runs would
    have skipped steps for."""
    status_root = _make_status_root(tmp_path)
    deploy_root = tmp_path / "deploy"

    first = _run(
        tmp_path, status_root=status_root, deploy_root=deploy_root, args=["--dry-run"], bwrap_exit=0
    )
    second = _run(
        tmp_path, status_root=status_root, deploy_root=deploy_root, args=["--dry-run"], bwrap_exit=0
    )

    assert first.returncode == 0 and second.returncode == 0
    assert not deploy_root.exists()
    assert "would generate Ed25519 keypair" in first.stdout
    assert "would generate Ed25519 keypair" in second.stdout


def test_rejects_unknown_argument(tmp_path: Path) -> None:
    status_root = _make_status_root(tmp_path)
    deploy_root = tmp_path / "deploy"

    proc = _run(
        tmp_path,
        status_root=status_root,
        deploy_root=deploy_root,
        args=["--not-a-real-flag"],
        bwrap_exit=0,
    )

    assert proc.returncode == 2
    assert "unknown argument" in proc.stdout + proc.stderr
