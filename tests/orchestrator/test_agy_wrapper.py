from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / ".orchestrator" / "bin" / "agy"
sys.path.insert(0, str(ROOT / ".orchestrator"))

from adapters.antigravity import _apply_provider_home  # noqa: E402


def test_wrapper_finds_cli_in_login_home_when_provider_home_is_isolated(tmp_path: Path) -> None:
    login_home = tmp_path / "login-home"
    provider_home = tmp_path / "agy2-home"
    binary = login_home / ".local" / "bin" / "agy"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$HOME\"\n", encoding="utf-8")
    binary.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PANTHEON_HOST_HOME": str(login_home),
            "HOME": str(provider_home),
            "ANTIGRAVITY_HOME": str(provider_home),
            "PATH": "/usr/bin:/bin",
        }
    )
    result = subprocess.run([str(WRAPPER)], env=env, text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(provider_home)


def test_adapter_preserves_login_home_before_isolating_provider_home(tmp_path: Path) -> None:
    login_home = tmp_path / "login-home"
    provider_home = tmp_path / "agy2-home"
    spawn_env: dict[str, str] = {}

    _apply_provider_home(spawn_env, provider_home, {"HOME": str(login_home)})

    assert spawn_env == {
        "PANTHEON_HOST_HOME": str(login_home),
        "ANTIGRAVITY_HOME": str(provider_home),
        "HOME": str(provider_home),
    }
