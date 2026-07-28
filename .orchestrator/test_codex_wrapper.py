from __future__ import annotations

import os
import subprocess
from pathlib import Path


WRAPPER = Path(__file__).parent / "bin" / "codex"


def _fake_codex(path: Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"#!/usr/bin/env bash\nprintf '{label}:%s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _run_wrapper(home: Path, *, codex_home: Path | None = None) -> str:
    env = dict(os.environ)
    env["HOME"] = str(home)
    if codex_home is None:
        env.pop("CODEX_HOME", None)
    else:
        env["CODEX_HOME"] = str(codex_home)
    result = subprocess.run(
        [str(WRAPPER), "--version"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip()


def test_default_codex_home_prefers_vscode_extension_binary(tmp_path: Path) -> None:
    _fake_codex(tmp_path / ".npm-global" / "bin" / "codex", "npm")
    _fake_codex(
        tmp_path
        / ".vscode-server"
        / "extensions"
        / "openai.chatgpt-0.146.0"
        / "bin"
        / "linux-x86_64"
        / "codex",
        "extension",
    )

    assert _run_wrapper(tmp_path) == "extension:--version"


def test_alternate_codex_home_keeps_npm_binary(tmp_path: Path) -> None:
    _fake_codex(tmp_path / ".npm-global" / "bin" / "codex", "npm")
    _fake_codex(
        tmp_path
        / ".vscode-server"
        / "extensions"
        / "openai.chatgpt-0.146.0"
        / "bin"
        / "linux-x86_64"
        / "codex",
        "extension",
    )

    assert _run_wrapper(tmp_path, codex_home=tmp_path / ".codex2") == "npm:--version"


def test_default_codex_home_falls_back_to_npm_without_extension(tmp_path: Path) -> None:
    _fake_codex(tmp_path / ".npm-global" / "bin" / "codex", "npm")

    assert _run_wrapper(tmp_path) == "npm:--version"
