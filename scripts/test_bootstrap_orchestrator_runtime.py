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
    """A minimal fake checkout materialized as the bootstrap script's command
    root worktree.

    Real bootstrap installs the exact candidate's own ``.orchestrator/
    requirements.txt`` into the deploy-root-owned supervisor venv before ever
    generating the dev-bridge keypair, so the fixture must carry a real
    dependency contract -- not a description of one -- for the real-run tests
    below to exercise the genuine ordering fix (venv/pip-install before
    keypair) instead of a stub.

    The real-run tests also exercise the read-only-validate-first supervisor
    Python provisioning path, which calls the real
    ``scripts/provision_live_supervisor_config.py --validate-python-
    dependencies-only`` against the exact command root -- and that call
    validates the whole immutable command root identity (Git remote, clean
    tree, and the exact launch entry points), not just the requirements file.
    So this fixture is a minimal but complete command root: a real dependency
    contract, a real preflight script, a committed origin remote, and stub
    launch entry points that only need to exist and be executable for this
    phase (phase 5's real promotion is never reached in these tests).
    """

    status_root = tmp_path / "checkout"
    status_root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=status_root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=status_root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=status_root, check=True)
    (status_root / "ai-status.json").write_text("{}\n", encoding="utf-8")
    orchestrator_dir = status_root / ".orchestrator"
    orchestrator_dir.mkdir()
    (orchestrator_dir / "requirements.txt").write_text(
        (ROOT / ".orchestrator" / "requirements.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (orchestrator_dir / "config.json").write_text("{}\n", encoding="utf-8")
    (orchestrator_dir / "supervisor.py").write_text("# fake supervisor\n", encoding="utf-8")
    scripts_dir = status_root / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "provision_live_supervisor_config.py").write_text(
        (ROOT / "scripts" / "provision_live_supervisor_config.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    for launch_entry_point in ("run-supervisor-watchdog.sh", "promote-supervisor-runtime.sh"):
        stub = scripts_dir / launch_entry_point
        stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    subprocess.run(["git", "add", "-A"], cwd=status_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=status_root, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.invalid/fake/pantheon.git"],
        cwd=status_root,
        check=True,
    )
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


def _run_stop_after_keypair(
    tmp_path: Path, status_root: Path, deploy_root: Path
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(
        [str(_stub_bin_dir(tmp_path, bwrap_exit=0)), env.get("PATH", "")]
    )
    env["PANTHEON_STATUS_ROOT"] = str(status_root)
    env["PANTHEON_DEPLOY_ROOT"] = str(deploy_root)
    env["BOOTSTRAP_ORCHESTRATOR_STOP_AFTER_KEYPAIR"] = "1"
    return subprocess.run([str(SCRIPT)], capture_output=True, text=True, check=False, env=env)


def test_real_run_mints_keypair_then_second_run_is_idempotent(tmp_path: Path) -> None:
    """A real (non-dry-run) invocation must mint the pair once; a second real
    run must recognize the existing pair and leave it untouched rather than
    minting again."""
    status_root = _make_status_root(tmp_path)
    deploy_root = tmp_path / "deploy"

    def run_stop_after_keypair() -> subprocess.CompletedProcess[str]:
        return _run_stop_after_keypair(tmp_path, status_root, deploy_root)

    authority_file = deploy_root / "runtime" / "supervisor-authority-public.env"
    signer_file = deploy_root / "runtime" / "dev-bridge-signing-private.env"

    first = run_stop_after_keypair()
    assert first.returncode == 0, first.stdout + first.stderr
    assert "generating Ed25519 keypair" in first.stdout
    assert authority_file.is_file()
    assert signer_file.is_file()
    first_authority_bytes = authority_file.read_bytes()
    first_signer_bytes = signer_file.read_bytes()

    second = run_stop_after_keypair()
    assert second.returncode == 0, second.stdout + second.stderr
    assert "keypair already present, keeping existing key" in second.stdout
    assert "generating Ed25519 keypair" not in second.stdout
    assert authority_file.read_bytes() == first_authority_bytes
    assert signer_file.read_bytes() == first_signer_bytes


def test_second_real_run_on_same_sha_reuses_verified_supervisor_python(
    tmp_path: Path,
) -> None:
    """Re-running bootstrap for the exact same command SHA (idempotent by
    design, for example after an operator re-invokes it following an earlier
    unrelated failure) must not re-install into the per-SHA supervisor Python
    directory once that exact directory is already verified healthy -- that
    directory can already be the one a currently running incumbent
    supervisor launched from. The first real run must provision it in
    isolation and publish it; a second real run against the same command SHA
    must reuse it in place and never repeat venv creation or pip install."""
    status_root = _make_status_root(tmp_path)
    deploy_root = tmp_path / "deploy"

    first = _run_stop_after_keypair(tmp_path, status_root, deploy_root)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "creating supervisor Python environment in isolation" in first.stdout
    assert "published verified supervisor Python environment" in first.stdout

    command_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=status_root, check=True, capture_output=True, text=True
    ).stdout.strip()
    supervisor_python_dir = deploy_root / "runtime" / "supervisor-python" / command_sha
    site_packages = next((supervisor_python_dir / "lib").glob("python*/site-packages"))
    first_installed_at = {
        path: path.stat().st_mtime for path in site_packages.rglob("*") if path.is_file()
    }

    second = _run_stop_after_keypair(tmp_path, status_root, deploy_root)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "reusing already-verified supervisor Python environment" in second.stdout
    assert "creating supervisor Python environment in isolation" not in second.stdout
    assert "installing supervisor Python dependencies" not in second.stdout
    second_installed_at = {
        path: path.stat().st_mtime for path in site_packages.rglob("*") if path.is_file()
    }
    assert second_installed_at == first_installed_at, (
        "same-SHA re-entry must not rewrite any file in the already-verified "
        "per-SHA supervisor Python environment"
    )


def test_partial_keypair_state_fails_closed(tmp_path: Path) -> None:
    """Simulate an interruption between the authority-file write and the
    signer-file write: only the authority file exists on disk, as it would
    after a prior run was killed mid-phase. A re-run must refuse to proceed
    (not silently treat the phase as done and continue with a permanently
    missing signer), and it must leave the mismatched state untouched so the
    operator can inspect or remove it."""
    status_root = _make_status_root(tmp_path)
    deploy_root = tmp_path / "deploy"
    runtime_dir = deploy_root / "runtime"
    runtime_dir.mkdir(parents=True)
    authority_file = runtime_dir / "supervisor-authority-public.env"
    signer_file = runtime_dir / "dev-bridge-signing-private.env"
    authority_file.write_text("BRIDGE_SIGNING_PUBLIC_KEYS_JSON='{}'\n", encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(
        [str(_stub_bin_dir(tmp_path, bwrap_exit=0)), env.get("PATH", "")]
    )
    env["PANTHEON_STATUS_ROOT"] = str(status_root)
    env["PANTHEON_DEPLOY_ROOT"] = str(deploy_root)
    env["BOOTSTRAP_ORCHESTRATOR_STOP_AFTER_KEYPAIR"] = "1"

    proc = subprocess.run([str(SCRIPT)], capture_output=True, text=True, check=False, env=env)

    assert proc.returncode == 1
    combined = proc.stdout + proc.stderr
    assert "mismatched dev-bridge keypair state" in combined
    assert "remove both files" in combined
    assert not signer_file.exists(), "must not mint a signer file over a mismatched pair"
    assert authority_file.read_text(encoding="utf-8") == "BRIDGE_SIGNING_PUBLIC_KEYS_JSON='{}'\n", (
        "must not touch the surviving half of a mismatched pair"
    )


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
