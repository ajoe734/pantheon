from __future__ import annotations

import json
import os
import subprocess
import sys
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

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import provision_live_supervisor_config as provision  # noqa: E402

# OPS-SUPERVISOR-PYTHON-RUNTIME-PREREQUISITE-001: this must exercise the exact
# shared provisioning function bootstrap-orchestrator-runtime.sh and
# sync-dev-root.sh both call
# (provision_live_supervisor_config.ensure_supervisor_python_environment),
# not a hand-rolled venv/pip-install stand-in for it. The real proof this
# task cares about is that the exact signed dev-bridge packet intake this
# task exists to keep working genuinely succeeds under the final published
# ``python_executable`` that function hands back.
REQUIREMENTS = REPO_ROOT / ".orchestrator" / "requirements.txt"


def _provision_bootstrap_style_venv_python(
    python_parent: Path, sha: str, requirements_path: Path
) -> Path:
    # OPS-SUPERVISOR-PYTHON-RUNTIME-PREREQUISITE-001: a failed mandatory
    # provisioning step must fail this fixture, not skip it -- a silent skip
    # here would suppress the only positive real-intake proof this task has.
    # There is deliberately no broad except/skip around this call: an
    # explicit, pre-declared offline opt-out (checked before the helper runs)
    # is the only legitimate way to bypass it, and none is required in the
    # supported CI/dev-worker environment this test targets.
    result = provision.ensure_supervisor_python_environment(
        python_parent=python_parent,
        sha=sha,
        requirements_path=requirements_path,
    )
    python_path = Path(result["python_executable"])
    assert python_path.is_file(), (
        "ensure_supervisor_python_environment reported success but the "
        f"published python_executable does not exist: {python_path}"
    )
    return python_path


@pytest.fixture(scope="module")
def bootstrap_style_venv_python(tmp_path_factory: pytest.TempPathFactory) -> Path:
    python_parent = tmp_path_factory.mktemp("supervisor-python-runtime")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    return _provision_bootstrap_style_venv_python(python_parent, sha, REQUIREMENTS)


def test_bootstrap_style_venv_drains_a_real_signed_packet(
    bootstrap_style_venv_python: Path, tmp_path: Path
) -> None:
    """Genuinely queue and drain a real Ed25519-signed DevTaskPacket using
    the queue/drain CLIs run under the exact final published
    ``python_executable`` that
    ``provision_live_supervisor_config.ensure_supervisor_python_environment``
    hands back -- the same shared function
    scripts/bootstrap-orchestrator-runtime.sh and scripts/sync-dev-root.sh
    both call -- not the pytest interpreter, proving the bridge's pydantic
    parsing and cryptography signature verification actually work end to end
    under that interpreter, not merely that a dependency-metadata probe
    accepts it.
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


def test_forced_provisioning_failure_fails_rather_than_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the fixture flaw this task's review flagged: every
    ``ensure_supervisor_python_environment`` ``ValueError`` used to be caught
    and turned into ``pytest.skip``, which would let a genuinely broken
    mandatory provisioning step masquerade as an environment limitation
    instead of failing the only positive real-intake test."""

    def _boom(**_kwargs: object) -> dict[str, object]:
        raise ValueError("simulated mandatory provisioning failure")

    monkeypatch.setattr(provision, "ensure_supervisor_python_environment", _boom)

    with pytest.raises(ValueError, match="simulated mandatory provisioning failure"):
        _provision_bootstrap_style_venv_python(tmp_path, "0" * 40, REQUIREMENTS)


def test_missing_final_python_executable_fails_rather_than_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the second half of the same fixture flaw: a
    ``python_executable`` reported as published but not actually present on
    disk used to be silently skipped instead of failing the test."""

    def _fake_success(**_kwargs: object) -> dict[str, object]:
        return {
            "python_executable": str(tmp_path / "nonexistent-sha" / "bin" / "python3"),
            "reused": False,
            "python_dependencies": {},
        }

    monkeypatch.setattr(provision, "ensure_supervisor_python_environment", _fake_success)

    with pytest.raises(AssertionError):
        _provision_bootstrap_style_venv_python(tmp_path, "0" * 40, REQUIREMENTS)
