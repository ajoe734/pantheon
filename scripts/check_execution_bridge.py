#!/usr/bin/env python3
from __future__ import annotations

import argparse
import configparser
import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUBMODULE_PATH = "lean"
DEFAULT_REMOTE = "ajoe734/pantheon-lean.git"
DEFAULT_BASE_PATH = "Algorithm.Python/pantheon_algo/base.py"
DEFAULT_COMPOSE_FILE = "docker-compose.exec.yml"
DEFAULT_LEAN_CONFIG_PATH = "/workspace/lean/Launcher/config.json"


def normalize_remote_url(value: str) -> str:
    remote = value.strip().rstrip("/")
    if remote.startswith("git@github.com:"):
        remote = remote.split(":", 1)[1]
    elif remote.startswith("ssh://git@github.com/"):
        remote = remote.removeprefix("ssh://git@github.com/")
    elif remote.startswith("https://github.com/"):
        remote = remote.removeprefix("https://github.com/")
    elif remote.startswith("http://github.com/"):
        remote = remote.removeprefix("http://github.com/")
    elif remote.startswith("github.com/"):
        remote = remote.removeprefix("github.com/")
    return remote


def load_gitmodules(root: Path) -> list[dict[str, str]]:
    gitmodules_path = root / ".gitmodules"
    if not gitmodules_path.is_file():
        return []

    parser = configparser.ConfigParser()
    parser.read(gitmodules_path, encoding="utf-8")
    entries: list[dict[str, str]] = []
    for section in parser.sections():
        if not section.startswith("submodule "):
            continue
        entries.append(
            {
                "section": section,
                "path": parser.get(section, "path", fallback="").strip(),
                "url": parser.get(section, "url", fallback="").strip(),
            }
        )
    return entries


def read_bridge_commit(root: Path, submodule_path: str) -> str | None:
    bridge_dir = root / submodule_path
    if not bridge_dir.exists():
        return None
    result = subprocess.run(
        ["git", "-C", str(bridge_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None


def build_report(
    *,
    root: Path = ROOT,
    expected_submodule_path: str = DEFAULT_SUBMODULE_PATH,
    expected_remote: str = DEFAULT_REMOTE,
    base_path: str = DEFAULT_BASE_PATH,
    compose_file: str = DEFAULT_COMPOSE_FILE,
    lean_config_path: str = DEFAULT_LEAN_CONFIG_PATH,
    bridge_commit: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    violations: list[str] = []

    submodules = load_gitmodules(root)
    bridge = next((entry for entry in submodules if entry.get("path") == expected_submodule_path), None)
    if bridge is None:
        violations.append(f".gitmodules must contain submodule path = {expected_submodule_path}")
        bridge = {"section": "", "path": expected_submodule_path, "url": ""}

    actual_remote = bridge.get("url", "")
    normalized_actual_remote = normalize_remote_url(actual_remote)
    normalized_expected_remote = normalize_remote_url(expected_remote)
    remote_valid = normalized_actual_remote == normalized_expected_remote
    if not remote_valid:
        violations.append(
            f"{expected_submodule_path} remote must be {expected_remote}; found {actual_remote or '<missing>'}"
        )

    commit = bridge_commit if bridge_commit is not None else read_bridge_commit(root, expected_submodule_path)
    if not commit:
        violations.append(f"{expected_submodule_path} HEAD commit must be readable")

    base_file = root / expected_submodule_path / base_path
    base_text = ""
    if base_file.is_file():
        try:
            base_text = base_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            base_text = base_file.read_text(errors="ignore")
    pantheon_algo_base_present = base_file.is_file() and "class PantheonAlgoBase" in base_text
    if not pantheon_algo_base_present:
        violations.append(f"PantheonAlgoBase must exist at {expected_submodule_path}/{base_path}")

    compose_path = root / compose_file
    compose_text = compose_path.read_text(encoding="utf-8") if compose_path.is_file() else ""
    compose_exec_path_valid = lean_config_path in compose_text
    if not compose_exec_path_valid:
        violations.append(f"{compose_file} must reference {lean_config_path}")

    submodule_path_valid = bridge.get("path") == expected_submodule_path
    bridge_repo_path = f"pantheon/{expected_submodule_path}"
    return {
        "ok": not violations,
        "bridge_path": bridge_repo_path,
        "submodule_path": bridge.get("path") or expected_submodule_path,
        "bridge_remote": normalized_actual_remote or actual_remote,
        "bridge_url": actual_remote,
        "bridge_commit": commit,
        "pantheon_algo_base_path": f"{expected_submodule_path}/{base_path}",
        "pantheon_algo_base_present": pantheon_algo_base_present,
        "compose_file": compose_file,
        "compose_exec_path": lean_config_path,
        "compose_exec_path_valid": compose_exec_path_valid,
        "checks": {
            "submodule_path_valid": submodule_path_valid,
            "bridge_remote_valid": remote_valid,
            "bridge_commit_present": bool(commit),
            "pantheon_algo_base_present": pantheon_algo_base_present,
            "compose_exec_path_valid": compose_exec_path_valid,
        },
        "violations": violations,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify the official Pantheon LEAN bridge submodule.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--expected-submodule-path", default=DEFAULT_SUBMODULE_PATH)
    parser.add_argument("--expected-remote", default=DEFAULT_REMOTE)
    parser.add_argument("--base-path", default=DEFAULT_BASE_PATH)
    parser.add_argument("--compose-file", default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("--lean-config-path", default=DEFAULT_LEAN_CONFIG_PATH)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = build_report(
        root=args.root,
        expected_submodule_path=args.expected_submodule_path,
        expected_remote=args.expected_remote,
        base_path=args.base_path,
        compose_file=args.compose_file,
        lean_config_path=args.lean_config_path,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
