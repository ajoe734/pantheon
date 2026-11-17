#!/usr/bin/env python3
"""Run a driver against the code of an earlier git revision, so that state written
by the current release can be read by the previous release (RC1 WP1.5).

How it works
  1. ``git archive <revision> -- <paths>`` streams the revision's tree from the local
     object database into a fresh temp directory. There is no network access, and the
     working tree, index and HEAD of the caller are never touched.
  2. The driver runs in a subprocess (``python -I``) whose import path is that temp
     tree only (plus the interpreter's own site-packages for third-party libraries).
     Before the driver runs, every module named with ``--module`` is imported and its
     ``__file__`` is proved to live inside the temp tree; a module that resolves
     anywhere else is a hard failure, never a silent fallback to current code.
  3. The temp directory is removed when the run ends, including on failure.

A revision that is not available locally fails with a readable reason (exit 2); this
harness never skips silently. Branch CI obtains the revision from its existing
``actions/checkout`` steps with ``fetch-depth: 0`` (full history); a shallow checkout
makes ``git cat-file`` fail and the harness reports ``revision-unavailable``.

Driver contract: a python file executed with ``runpy`` as ``__main__`` after ``sys.argv``
is ``[driver, *driver_args]``. The environment variable ``PREVIOUS_RELEASE_TREE`` holds
the materialised tree; a driver prints its result (normally JSON) on stdout.

Example (another stateful service: the requirement_state reader of e7ec353d6)::

    python3 scripts/previous_release_reader.py --revision e7ec353d6 \\
        --path services/source_ingestion --module services.source_ingestion.requirement_state \\
        --driver my_driver.py -- /path/to/requirement_snapshots.jsonl
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = ("services",)

EXIT_UNAVAILABLE = 2
EXIT_DRIVER_FAILED = 3


class PreviousReleaseError(RuntimeError):
    """Base class; the message is always a readable reason."""


class RevisionUnavailable(PreviousReleaseError):
    pass


class PathUnavailable(PreviousReleaseError):
    pass


class DriverFailed(PreviousReleaseError):
    def __init__(self, message: str, *, returncode: int, stdout: str, stderr: str) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True)
class DriverResult:
    revision: str
    commit: str
    returncode: int
    stdout: str
    stderr: str

    def json(self):
        return json.loads(self.stdout)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env, check=False)


def resolve_revision(revision: str, *, repo: Path = REPO_ROOT) -> str:
    """Return the full commit sha, or raise RevisionUnavailable with the reason."""

    if not revision or revision.startswith("-"):
        raise RevisionUnavailable(f"revision-unavailable: invalid revision {revision!r}")
    proc = _git(repo, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}")
    if proc.returncode != 0:
        shallow = _git(repo, "rev-parse", "--is-shallow-repository").stdout.decode().strip() == "true"
        hint = (
            " (this is a shallow clone: fetch full history, e.g. actions/checkout fetch-depth: 0)"
            if shallow
            else " (not in the local object database; no network fetch is attempted)"
        )
        raise RevisionUnavailable(f"revision-unavailable: {revision}{hint}")
    return proc.stdout.decode().strip()


@contextlib.contextmanager
def materialised_tree(
    revision: str, paths: Sequence[str] = DEFAULT_PATHS, *, repo: Path = REPO_ROOT
) -> Iterator[Path]:
    """Extract ``paths`` of ``revision`` into a temp dir that is removed on exit."""

    commit = resolve_revision(revision, repo=repo)
    for path in paths:
        if Path(path).is_absolute() or ".." in Path(path).parts:
            raise PathUnavailable(f"path-unavailable: {path!r} must be repo-relative")
        if _git(repo, "cat-file", "-e", f"{commit}:{path}").returncode != 0:
            raise PathUnavailable(f"path-unavailable: {path} does not exist at {commit[:9]}")
    tmp = Path(tempfile.mkdtemp(prefix=f"previous-release-{commit[:9]}-"))
    try:
        archive = subprocess.Popen(
            ["git", "-C", str(repo), "archive", "--format=tar", commit, "--", *paths],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        extract = subprocess.run(
            ["tar", "-x", "-C", str(tmp)], stdin=archive.stdout, capture_output=True, check=False
        )
        assert archive.stdout is not None
        archive.stdout.close()
        archive_err = archive.stderr.read().decode(errors="replace") if archive.stderr else ""
        if archive.wait() != 0 or extract.returncode != 0:
            raise RevisionUnavailable(
                f"revision-unavailable: could not materialise {commit[:9]}: "
                f"{archive_err.strip() or extract.stderr.decode(errors='replace').strip()}"
            )
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_BOOTSTRAP = r"""
import importlib, os, runpy, sys
tree = os.path.realpath(os.environ["PREVIOUS_RELEASE_TREE"])
sys.path[:] = [tree] + [p for p in sys.path if p and os.path.realpath(p) != os.path.realpath(os.getcwd())]
for name in [m for m in os.environ.get("PREVIOUS_RELEASE_MODULES", "").split(",") if m]:
    module = importlib.import_module(name)
    origin = os.path.realpath(getattr(module, "__file__", "") or "")
    if not origin.startswith(tree + os.sep):
        sys.stderr.write(f"module-outside-tree: {name} loaded from {origin}, not {tree}\n")
        sys.exit(97)
driver = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(driver, run_name="__main__")
"""


def run_driver(
    revision: str,
    driver: str | Path,
    *,
    paths: Sequence[str] = DEFAULT_PATHS,
    modules: Sequence[str] = (),
    driver_args: Sequence[str] = (),
    env: dict[str, str] | None = None,
    timeout: float = 300.0,
    repo: Path = REPO_ROOT,
    check: bool = True,
) -> DriverResult:
    driver_path = Path(driver).resolve()
    if not driver_path.is_file():
        raise PathUnavailable(f"driver-unavailable: {driver} is not a file")
    with materialised_tree(revision, paths, repo=repo) as tree:
        commit = resolve_revision(revision, repo=repo)
        child_env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
        child_env.update(env or {})
        child_env["PREVIOUS_RELEASE_TREE"] = str(tree)
        child_env["PREVIOUS_RELEASE_MODULES"] = ",".join(modules)
        child_env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-c", _BOOTSTRAP, str(driver_path), *driver_args],
                cwd=tree,
                env=child_env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DriverFailed(
                f"driver-timeout: exceeded {timeout}s at {commit[:9]}", returncode=-1, stdout="", stderr=str(exc)
            ) from exc
    result = DriverResult(revision, commit, proc.returncode, proc.stdout, proc.stderr)
    if check and proc.returncode != 0:
        raise DriverFailed(
            f"driver-failed: exit {proc.returncode} at {commit[:9]}: {proc.stderr.strip()[-2000:]}",
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--revision", required=True, help="git revision whose code is loaded")
    parser.add_argument("--path", action="append", dest="paths", help="repo-relative path to materialise (repeatable; default: services)")
    parser.add_argument("--module", action="append", dest="modules", default=[], help="module that must import from the revision tree (repeatable)")
    parser.add_argument("--driver", required=True, help="python file run inside the revision's import path")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("driver_args", nargs="*", help="arguments after -- are passed to the driver")
    args = parser.parse_args(argv)
    try:
        result = run_driver(
            args.revision,
            args.driver,
            paths=args.paths or DEFAULT_PATHS,
            modules=args.modules,
            driver_args=args.driver_args,
            timeout=args.timeout,
        )
    except DriverFailed as exc:
        sys.stdout.write(exc.stdout)
        sys.stderr.write(f"{exc}\n")
        return EXIT_DRIVER_FAILED
    except PreviousReleaseError as exc:
        sys.stderr.write(f"{exc}\n")
        return EXIT_UNAVAILABLE
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
