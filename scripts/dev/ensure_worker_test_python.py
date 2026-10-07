#!/usr/bin/env python3
"""Build the shared test interpreter that auto-workers receive as PANTHEON_DEPENDENCY_PYTHON.

Each build lives in ``<parent>/<hash>``, where the hash covers
requirements.txt and services/control-plane/bff/requirements.txt. A build counts only once its
``.ready`` marker exists, and the marker is written after the imports are
proven. ``<parent>/current`` is switched atomically to the newest ready build,
so a worker never sees a half-installed venv. The newest few builds are kept
because checkout venvs made by provision_python_distribution.py point at their
site directories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

REQUIREMENTS = ("requirements.txt", "services/control-plane/bff/requirements.txt")
PROBE = "import pytest, fastapi, httpx, pydantic, yaml, cryptography, flask, jsonschema, psycopg"
READY = ".ready"
KEEP = 3


def requirements_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for name in REQUIREMENTS:
        digest.update(name.encode() + b"\0" + (root / name).read_bytes() + b"\0")
    return digest.hexdigest()[:16]


def _ready_builds(parent: Path) -> list[Path]:
    builds = [p for p in parent.iterdir() if p.is_dir() and not p.is_symlink() and (p / READY).is_file()]
    return sorted(builds, key=lambda p: (p / READY).stat().st_mtime, reverse=True)


def ensure(
    root: Path,
    parent: Path,
    *,
    python: str = sys.executable,
    run: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    parent.mkdir(parents=True, exist_ok=True)
    digest = requirements_hash(root)
    build = parent / digest
    interpreter = build / "bin" / "python3"
    reused = (build / READY).is_file()
    if not reused:
        shutil.rmtree(build, ignore_errors=True)  # leftover from an interrupted build
        run([python, "-m", "venv", str(build)], check=True)
        pip = [str(interpreter), "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
        req_args = [arg for name in REQUIREMENTS for arg in ("-r", str(root / name))]
        run([*pip, *req_args], check=True, cwd=root)
        run([str(interpreter), "-c", PROBE], check=True)
        (build / READY).write_text(digest + "\n")

    current = parent / "current"
    staged = parent / ".current.tmp"
    staged.unlink(missing_ok=True)
    staged.symlink_to(digest)
    os.replace(staged, current)

    for old in _ready_builds(parent)[KEEP:]:
        if old != build:
            shutil.rmtree(old, ignore_errors=True)
    return {"python": str(current / "bin" / "python3"), "build": str(build), "reused": reused}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--parent", required=True, help="e.g. <deploy-root>/runtime/worker-test-python")
    args = parser.parse_args(argv)
    print(json.dumps(ensure(Path(args.root), Path(args.parent))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
