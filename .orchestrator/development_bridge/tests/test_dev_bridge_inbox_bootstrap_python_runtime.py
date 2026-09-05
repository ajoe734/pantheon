from __future__ import annotations

import json
import os
import subprocess
import venv
from pathlib import Path

import pytest

from ..dev_bridge_signer import sign_packet
from .dev_bridge_test_support import authoritative_test_runtime_env
from .test_dev_bridge_inbox_cli import (
    DRAIN_SCRIPT,
    QUEUE_SCRIPT,
    REPO_ROOT,
    TEST_KEY,
    _assert_single_batch_materialization,
    _cli_env,
    _make_packet,
    _write_fake_repo,
)

# OPS-SUPERVISOR-PYTHON-RUNTIME-PREREQUISITE-001: the promotion chain proves a
# candidate interpreter with importlib.metadata + a real module import
# (provision_live_supervisor_config.validate_python_dependencies), but that is
# a proxy for the thing that actually matters: the exact signed dev-bridge
# packet intake this task exists to keep working must genuinely succeed under
# an interpreter built the same way bootstrap/sync build the supervisor's own
# venv (``python3 -m venv`` plus ``pip install -r
# .orchestrator/requirements.txt``), not merely report importable metadata.
REQUIREMENTS = REPO_ROOT / ".orchestrator" / "requirements.txt"


@pytest.fixture(scope="module")
def bootstrap_style_venv_python(tmp_path_factory: pytest.TempPathFactory) -> Path:
    venv_dir = tmp_path_factory.mktemp("supervisor-python-runtime-venv")
    venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
    python_path = venv_dir / "bin" / "python3"
    if not python_path.is_file():
        pytest.skip("python3 -m venv did not produce a usable interpreter on this host")

    install = subprocess.run(
        [
            str(python_path),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--disable-pip-version-check",
            "-r",
            str(REQUIREMENTS),
        ],
        capture_output=True,
        text=True,
    )
    if install.returncode != 0:
        pytest.skip(
            "could not install .orchestrator/requirements.txt into a fresh venv "
            f"(likely no package-index access in this environment): {install.stderr}"
        )
    return python_path


def test_bootstrap_style_venv_drains_a_real_signed_packet(
    bootstrap_style_venv_python: Path, tmp_path: Path
) -> None:
    """Genuinely queue and drain a real Ed25519-signed DevTaskPacket using
    the queue/drain CLIs run under an interpreter built exactly the way
    scripts/bootstrap-orchestrator-runtime.sh and scripts/sync-dev-root.sh
    build the supervisor's own venv -- not the pytest interpreter -- proving
    the bridge's pydantic parsing and cryptography signature verification
    actually work end to end under that interpreter, not merely that a
    dependency-metadata probe accepts it.
    """

    repo_root = _write_fake_repo(tmp_path)
    signed = sign_packet(
        _make_packet("pkt_bootstrap_python_runtime"),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(
        json.dumps({"taskPacket": signed.model_dump(mode="json", by_alias=True)}),
        encoding="utf-8",
    )
    env = _cli_env(repo_root)

    queue_result = subprocess.run(
        [
            str(bootstrap_style_venv_python),
            str(QUEUE_SCRIPT),
            "--packet-file",
            str(packet_path),
            "--repo-root",
            str(repo_root),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert queue_result.returncode == 0, queue_result.stderr

    drain_result = subprocess.run(
        [
            str(bootstrap_style_venv_python),
            str(DRAIN_SCRIPT),
            "--repo-root",
            str(repo_root),
            "--limit",
            "1",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )

    assert drain_result.returncode == 0, drain_result.stderr
    body = json.loads(drain_result.stdout)
    assert body["processedCount"] == 1
    assert body["packets"][0]["packetId"] == "pkt_bootstrap_python_runtime"
    _assert_single_batch_materialization(repo_root, packet_id="pkt_bootstrap_python_runtime")


def test_bare_venv_without_the_dependency_contract_fails_closed(tmp_path: Path) -> None:
    """The exact failure this task closes: a venv built the same way (``python3
    -m venv``) but never given ``.orchestrator/requirements.txt`` must fail
    the real drain loudly, instead of silently dropping the packet while a
    surrounding health check stays green. This is deliberately a separate,
    bare venv (never pip-installs pydantic/cryptography at all) rather than
    depending on whatever the pytest-running interpreter happens to have, so
    the failure is deterministic instead of environment-dependent."""

    venv_dir = tmp_path / "bare-venv"
    venv.EnvBuilder(with_pip=False, clear=True).create(venv_dir)
    bare_python = venv_dir / "bin" / "python3"
    if not bare_python.is_file():
        pytest.skip("python3 -m venv did not produce a usable interpreter on this host")

    repo_root = _write_fake_repo(tmp_path)
    env = authoritative_test_runtime_env(repo_root)
    drain_result = subprocess.run(
        [str(bare_python), str(DRAIN_SCRIPT), "--repo-root", str(repo_root), "--limit", "1"],
        cwd=str(REPO_ROOT),
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )

    assert drain_result.returncode != 0
    assert "pydantic" in (drain_result.stderr + drain_result.stdout)
