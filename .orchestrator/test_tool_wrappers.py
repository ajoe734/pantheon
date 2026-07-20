from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
GH_WRAPPER = ROOT / ".orchestrator" / "bin" / "gh"


def _wrapper_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{GH_WRAPPER.parent}:/usr/local/bin:/usr/bin:/bin"
    return env


def test_gh_wrapper_falls_back_to_path_without_recursing(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(GH_WRAPPER), "--version"],
        env=_wrapper_env(tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "gh version" in result.stdout


def test_gh_wrapper_preserves_newline_in_vendored_path(tmp_path: Path) -> None:
    vendor = (
        tmp_path
        / ".local"
        / "share"
        / "pantheon-orchestrator-tools"
        / "gh-2.45.0\n2.45.0"
        / "bin"
        / "gh"
    )
    vendor.parent.mkdir(parents=True)
    vendor.write_text("#!/usr/bin/env bash\nprintf 'vendored-newline-path-ok\\n'\n", encoding="utf-8")
    vendor.chmod(0o755)

    result = subprocess.run(
        [str(GH_WRAPPER), "--version"],
        env=_wrapper_env(tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "vendored-newline-path-ok\n"
