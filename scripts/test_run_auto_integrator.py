from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "scripts" / "run-auto-integrator.sh"

FAKE_INTEGRATOR_SOURCE = (
    "import json, os, sys\n"
    "from pathlib import Path\n"
    "Path(os.environ['AUTO_INTEGRATOR_ARGS_OUT']).write_text(\n"
    "    json.dumps({\n"
    "        'argv': sys.argv[1:],\n"
    "        'cwd': os.getcwd(),\n"
    "        'live_supervisor_config': os.environ.get('PANTHEON_LIVE_SUPERVISOR_CONFIG'),\n"
    "    }),\n"
    "    encoding='utf-8',\n"
    ")\n"
)


def _install_fake_integrator(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "command-runtime"
    scripts = repo / "scripts"
    git_scripts = scripts / "git"
    git_scripts.mkdir(parents=True)
    shutil.copy2(WRAPPER, scripts / WRAPPER.name)
    fake_integrator = git_scripts / "auto_integrator.py"
    fake_integrator.write_text(FAKE_INTEGRATOR_SOURCE, encoding="utf-8")
    return repo, scripts


def test_wrapper_passes_explicit_live_config_to_integrator(tmp_path: Path) -> None:
    repo, scripts = _install_fake_integrator(tmp_path)
    status_root = tmp_path / "coordination-root"
    status_root.mkdir()
    live_config = tmp_path / "runtime" / "live.json"
    live_config.parent.mkdir()
    live_config.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "args.json"
    env = os.environ.copy()
    env.update(
        {
            "PANTHEON_STATUS_ROOT": str(status_root),
            "PANTHEON_AUTO_INTEGRATOR_CONFIG": str(live_config),
            "AUTO_INTEGRATOR_DRY_RUN": "1",
            "AUTO_INTEGRATOR_MAX_TASKS": "3",
            "AUTO_INTEGRATOR_ARGS_OUT": str(output),
        }
    )

    result = subprocess.run(
        ["bash", str(scripts / WRAPPER.name), "--task-id", "TASK-123", "--json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload == {
        "argv": [
            "--max-tasks",
            "3",
            "--status-file",
            str(status_root / "ai-status.json"),
            "--config-file",
            str(live_config),
            "--task-id",
            "TASK-123",
            "--json",
        ],
        "cwd": str(repo),
        "live_supervisor_config": None,
    }


def test_execute_mode_forwards_explicit_live_config(tmp_path: Path) -> None:
    """Positive: an explicit PANTHEON_AUTO_INTEGRATOR_CONFIG still reaches
    the integrator as PANTHEON_LIVE_SUPERVISOR_CONFIG for --execute, and no
    --status-file/--config-file override is passed (live authority owns
    those under --execute)."""

    repo, scripts = _install_fake_integrator(tmp_path)
    status_root = tmp_path / "coordination-root"
    status_root.mkdir()
    live_config = tmp_path / "runtime" / "live.json"
    live_config.parent.mkdir()
    live_config.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "args.json"
    env = os.environ.copy()
    env.update(
        {
            "PANTHEON_STATUS_ROOT": str(status_root),
            "PANTHEON_AUTO_INTEGRATOR_CONFIG": str(live_config),
            "AUTO_INTEGRATOR_MAX_TASKS": "1",
            "AUTO_INTEGRATOR_ARGS_OUT": str(output),
        }
    )
    env.pop("AUTO_INTEGRATOR_DRY_RUN", None)

    result = subprocess.run(
        ["bash", str(scripts / WRAPPER.name)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["argv"] == ["--execute", "--max-tasks", "1"]
    assert payload["live_supervisor_config"] == str(live_config)


def test_execute_mode_without_override_leaves_live_config_unset(tmp_path: Path) -> None:
    """Negative: with no explicit PANTHEON_AUTO_INTEGRATOR_CONFIG, --execute
    must not export PANTHEON_LIVE_SUPERVISOR_CONFIG pointing at the
    repo-committed .orchestrator/config.json template -- that file has no
    per-repository integration_path and would silently merge against the
    shared dev-root checkout. Leaving the variable unset lets
    auto_integrator.py fall back to its own promoted DEFAULT_LIVE_CONFIG."""

    repo, scripts = _install_fake_integrator(tmp_path)
    status_root = tmp_path / "coordination-root"
    status_root.mkdir()
    output = tmp_path / "args.json"
    env = os.environ.copy()
    env.update(
        {
            "PANTHEON_STATUS_ROOT": str(status_root),
            "AUTO_INTEGRATOR_MAX_TASKS": "1",
            "AUTO_INTEGRATOR_ARGS_OUT": str(output),
        }
    )
    env.pop("AUTO_INTEGRATOR_DRY_RUN", None)
    env.pop("PANTHEON_AUTO_INTEGRATOR_CONFIG", None)
    env.pop("PANTHEON_LIVE_SUPERVISOR_CONFIG", None)

    result = subprocess.run(
        ["bash", str(scripts / WRAPPER.name)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["argv"] == ["--execute", "--max-tasks", "1"]
    assert payload["live_supervisor_config"] is None
