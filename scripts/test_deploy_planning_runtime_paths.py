from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "deploy_planning_runtime_paths.py"
SPEC = importlib.util.spec_from_file_location("deploy_planning_runtime_paths", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write_valid_runtime(repo: Path, session_id: str = "phase9-test-session") -> tuple[Path, Path]:
    relative_session = Path(
        "docs/02-architecture/consensus/sessions"
    ) / session_id / "planning-session.json"
    session = repo / relative_session
    session.parent.mkdir(parents=True)
    session.write_text('{"runtime":"exact"}\n', encoding="utf-8")

    pointer = repo / ".orchestrator/planning-session-pointer.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "planning_dir": relative_session.parent.as_posix(),
                "session_file": relative_session.as_posix(),
            }
        ),
        encoding="utf-8",
    )
    return pointer, session


def test_resolves_canonical_pointer_referenced_session(tmp_path: Path) -> None:
    _write_valid_runtime(tmp_path)

    assert MODULE.resolve_planning_session_path(
        tmp_path, ".orchestrator/planning-session-pointer.json"
    ) == (
        "docs/02-architecture/consensus/sessions/phase9-test-session/"
        "planning-session.json"
    )


def test_cli_emits_only_validated_repo_relative_session_path(tmp_path: Path) -> None:
    _write_valid_runtime(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            str(tmp_path),
            ".orchestrator/planning-session-pointer.json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip().endswith("phase9-test-session/planning-session.json")
    assert completed.stderr == ""


@pytest.mark.parametrize(
    "unsafe_reference",
    [
        "/etc/passwd",
        "docs/02-architecture/consensus/sessions/../../../../etc/passwd",
        "docs/02-architecture/consensus-other/sessions/phase9/planning-session.json",
        "docs/02-architecture/consensus/sessions/phase9/other.json",
        "docs/02-architecture/consensus/sessions/phase9/nested/planning-session.json",
        "docs\\02-architecture\\consensus\\sessions\\phase9\\planning-session.json",
    ],
)
def test_rejects_arbitrary_or_escaping_pointer_reference(
    tmp_path: Path, unsafe_reference: str
) -> None:
    pointer, _ = _write_valid_runtime(tmp_path)
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    payload["session_file"] = unsafe_reference
    payload.pop("planning_dir", None)
    pointer.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MODULE.PlanningRuntimePathError):
        MODULE.resolve_planning_session_path(
            tmp_path, ".orchestrator/planning-session-pointer.json"
        )


def test_rejects_symlinked_session_even_when_reference_is_canonical(tmp_path: Path) -> None:
    pointer, session = _write_valid_runtime(tmp_path)
    session.unlink()
    outside = tmp_path.parent / f"{tmp_path.name}-outside-session.json"
    outside.write_text('{"outside":true}\n', encoding="utf-8")
    session.symlink_to(outside)

    with pytest.raises(MODULE.PlanningRuntimePathError, match="symlink path component"):
        MODULE.resolve_planning_session_path(
            tmp_path, ".orchestrator/planning-session-pointer.json"
        )

    assert pointer.is_file()


def test_rejects_symlinked_session_parent_with_repo_internal_target(tmp_path: Path) -> None:
    _, session = _write_valid_runtime(tmp_path, session_id="phase9")
    session.unlink()
    session.parent.rmdir()
    arbitrary = tmp_path / "arbitrary"
    arbitrary.mkdir()
    (arbitrary / "planning-session.json").write_text(
        '{"arbitrary":true}\n', encoding="utf-8"
    )
    session.parent.symlink_to(arbitrary, target_is_directory=True)

    with pytest.raises(MODULE.PlanningRuntimePathError, match="symlink path component"):
        MODULE.resolve_planning_session_path(
            tmp_path, ".orchestrator/planning-session-pointer.json"
        )


def test_rejects_symlinked_canonical_pointer(tmp_path: Path) -> None:
    pointer, _ = _write_valid_runtime(tmp_path)
    target = tmp_path / "pointer-target.json"
    pointer.replace(target)
    pointer.symlink_to(target)

    with pytest.raises(MODULE.PlanningRuntimePathError, match="symlink path component"):
        MODULE.resolve_planning_session_path(
            tmp_path, ".orchestrator/planning-session-pointer.json"
        )


def test_rejects_symlinked_orchestrator_parent_with_repo_internal_target(
    tmp_path: Path,
) -> None:
    pointer, _ = _write_valid_runtime(tmp_path)
    pointer_payload = pointer.read_bytes()
    pointer.unlink()
    pointer.parent.rmdir()
    runtime_meta = tmp_path / "runtime-meta"
    runtime_meta.mkdir()
    (runtime_meta / "planning-session-pointer.json").write_bytes(pointer_payload)
    pointer.parent.symlink_to(runtime_meta, target_is_directory=True)

    with pytest.raises(MODULE.PlanningRuntimePathError, match="symlink path component"):
        MODULE.resolve_planning_session_path(
            tmp_path, ".orchestrator/planning-session-pointer.json"
        )


def test_rejects_mismatched_planning_dir(tmp_path: Path) -> None:
    pointer, _ = _write_valid_runtime(tmp_path)
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    payload["planning_dir"] = "docs/02-architecture/consensus/sessions/different"
    pointer.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MODULE.PlanningRuntimePathError, match="planning_dir"):
        MODULE.resolve_planning_session_path(
            tmp_path, ".orchestrator/planning-session-pointer.json"
        )


def test_rejects_mismatched_session_id(tmp_path: Path) -> None:
    pointer, _ = _write_valid_runtime(tmp_path)
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    payload["session_id"] = "different-session"
    pointer.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MODULE.PlanningRuntimePathError, match="session_id"):
        MODULE.resolve_planning_session_path(
            tmp_path, ".orchestrator/planning-session-pointer.json"
        )
