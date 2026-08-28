from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "scripts" / "run-auto-integrator.sh"


def test_wrapper_passes_explicit_live_config_to_integrator(tmp_path: Path) -> None:
    repo = tmp_path / "command-runtime"
    scripts = repo / "scripts"
    git_scripts = scripts / "git"
    git_scripts.mkdir(parents=True)
    shutil.copy2(WRAPPER, scripts / WRAPPER.name)
    fake_integrator = git_scripts / "auto_integrator.py"
    fake_integrator.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['AUTO_INTEGRATOR_ARGS_OUT']).write_text(\n"
        "    json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}),\n"
        "    encoding='utf-8',\n"
        ")\n",
        encoding="utf-8",
    )
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
    }
