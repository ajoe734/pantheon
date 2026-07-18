#!/usr/bin/env python3
"""Resolve deploy-owned planning runtime paths without permitting path escape."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any


CANONICAL_POINTER_PATH = PurePosixPath(".orchestrator/planning-session-pointer.json")
CONSENSUS_ROOT = PurePosixPath("docs/02-architecture/consensus")
SESSION_FILE_NAME = "planning-session.json"
SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
LEGACY_PHASE_PATTERN = re.compile(r"phase[0-9]+")


class PlanningRuntimePathError(ValueError):
    """Raised when the runtime pointer cannot safely identify a session file."""


def _assert_path_components(
    root: Path, reference: PurePosixPath, label: str
) -> Path:
    """Resolve a repo-relative path only when no component is a symlink."""

    current = root
    for index, part in enumerate(reference.parts):
        current = current / part
        try:
            component_stat = current.lstat()
        except FileNotFoundError as exc:
            raise PlanningRuntimePathError(f"{label} does not exist") from exc
        if stat.S_ISLNK(component_stat.st_mode):
            raise PlanningRuntimePathError(f"{label} contains a symlink path component")
        if index < len(reference.parts) - 1 and not stat.S_ISDIR(component_stat.st_mode):
            raise PlanningRuntimePathError(f"{label} parent component is not a directory")
    return current


def _load_pointer(pointer_path: Path) -> dict[str, Any]:
    try:
        pointer_stat = pointer_path.lstat()
    except FileNotFoundError as exc:
        raise PlanningRuntimePathError("canonical planning pointer does not exist") from exc
    if stat.S_ISLNK(pointer_stat.st_mode) or not stat.S_ISREG(pointer_stat.st_mode):
        raise PlanningRuntimePathError(
            "canonical planning pointer must be a regular non-symlink file"
        )

    try:
        payload = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlanningRuntimePathError(
            "canonical planning pointer is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise PlanningRuntimePathError("canonical planning pointer must contain a JSON object")
    return payload


def _validate_session_reference(raw_value: Any) -> PurePosixPath:
    if not isinstance(raw_value, str) or not raw_value:
        raise PlanningRuntimePathError("planning pointer session_file must be a non-empty string")
    if any(character in raw_value for character in ("\x00", "\r", "\n", "\\")):
        raise PlanningRuntimePathError(
            "planning pointer session_file contains forbidden characters"
        )

    reference = PurePosixPath(raw_value)
    if reference.is_absolute() or raw_value != reference.as_posix():
        raise PlanningRuntimePathError(
            "planning pointer session_file must be a normalized repo-relative path"
        )
    if any(part in {"", ".", ".."} for part in reference.parts):
        raise PlanningRuntimePathError("planning pointer session_file may not traverse directories")

    consensus_parts = CONSENSUS_ROOT.parts
    if reference.parts[: len(consensus_parts)] != consensus_parts:
        raise PlanningRuntimePathError(
            "planning pointer session_file is outside the consensus root"
        )

    suffix = reference.parts[len(consensus_parts) :]
    is_session = (
        len(suffix) == 3
        and suffix[0] == "sessions"
        and SESSION_ID_PATTERN.fullmatch(suffix[1]) is not None
        and suffix[2] == SESSION_FILE_NAME
    )
    is_legacy_phase = (
        len(suffix) == 2
        and LEGACY_PHASE_PATTERN.fullmatch(suffix[0]) is not None
        and suffix[1] == SESSION_FILE_NAME
    )
    if not (is_session or is_legacy_phase):
        raise PlanningRuntimePathError(
            "planning pointer session_file must identify one canonical planning-session.json"
        )
    return reference


def resolve_planning_session_path(repo_root: Path, pointer_relative_path: str) -> str:
    """Return the validated repo-relative session path referenced by the pointer."""

    root = repo_root.resolve(strict=True)
    pointer_reference = PurePosixPath(pointer_relative_path)
    if pointer_reference != CANONICAL_POINTER_PATH:
        raise PlanningRuntimePathError("only the canonical planning pointer may be resolved")

    pointer_path = _assert_path_components(root, pointer_reference, "canonical planning pointer")
    payload = _load_pointer(pointer_path)
    session_reference = _validate_session_reference(payload.get("session_file"))

    session_suffix = session_reference.parts[len(CONSENSUS_ROOT.parts) :]
    if session_suffix[0] == "sessions" and payload.get("session_id") != session_suffix[1]:
        raise PlanningRuntimePathError("planning pointer session_id does not match session_file")

    planning_dir = payload.get("planning_dir")
    if planning_dir is not None and planning_dir != session_reference.parent.as_posix():
        raise PlanningRuntimePathError("planning pointer planning_dir does not match session_file")

    session_path = _assert_path_components(
        root, session_reference, "pointer-referenced planning session"
    )
    session_stat = session_path.lstat()
    if stat.S_ISLNK(session_stat.st_mode) or not stat.S_ISREG(session_stat.st_mode):
        raise PlanningRuntimePathError(
            "pointer-referenced planning session must be a regular non-symlink file"
        )

    consensus_path = _assert_path_components(root, CONSENSUS_ROOT, "consensus root")
    resolved_consensus = consensus_path.resolve(strict=True)
    resolved_session = session_path.resolve(strict=True)
    if os.path.commonpath((str(resolved_consensus), str(resolved_session))) != str(
        resolved_consensus
    ):
        raise PlanningRuntimePathError(
            "pointer-referenced planning session escapes the consensus root"
        )
    return session_reference.as_posix()


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "usage: deploy_planning_runtime_paths.py REPO_ROOT POINTER_RELATIVE_PATH",
            file=sys.stderr,
        )
        return 2
    try:
        print(resolve_planning_session_path(Path(argv[1]), argv[2]))
    except (OSError, PlanningRuntimePathError) as exc:
        print(f"invalid deploy planning runtime pointer: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
